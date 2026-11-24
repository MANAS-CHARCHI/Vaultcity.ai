"""
Vault Assistant Chat API (RAG with Claude Haiku & pgvector).

Retrieves verified document context from PostgreSQL pgvector and sends grounded prompts
to Anthropic Claude Haiku.
Every single conversation call is:
1. Logged to `<FILE_LOG_DIR>/claude_chat.log` & `claude_chat.jsonl` with full details.
2. Tracked in dedicated SQLite metrics database `<FILE_LOG_DIR>/token_usage.db`
   with separate input and output token counts per user and per model.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.redis import delete_cache, make_user_tokens_cache_key
from app.models.conversation import ChatMessageModel, Conversation
from app.models.user import User
from app.schemas.conversation import ChatMessageCreate, ChatMessageResponse, ConversationResponse
from app.services.rag_workflow import run_assistant_rag

logger = logging.getLogger("vaultcity.chat")

router = APIRouter(prefix="/chat", tags=["Chat & Vault Assistant"])


@router.post("/message", response_model=ChatMessageResponse)
async def send_chat_message(
    payload: ChatMessageCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Send a message to the Vault Assistant.
    Retrieves semantic document context from pgvector, calls Claude Haiku,
    logs every detail to claude_chat.log and updates the dedicated token metrics database.
    """
    query_text = (payload.content or "").strip()
    if not query_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message content cannot be empty.",
        )

    # 1. Resolve or create active Conversation
    conv = None
    if payload.conversation_id:
        conv = await db.get(Conversation, payload.conversation_id)
        if conv and conv.user_id != current_user.id:
            conv = None

    if not conv:
        stmt = (
            select(Conversation)
            .where(Conversation.user_id == current_user.id)
            .order_by(desc(Conversation.updated_at))
            .limit(1)
        )
        res = await db.execute(stmt)
        conv = res.scalar_one_or_none()

    if not conv:
        conv = Conversation(
            id=uuid.uuid4(),
            user_id=current_user.id,
            title="Vault Assistant",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(conv)
        await db.flush()

    # 2. Execute Smart Agentic RAG (Fast Profile Cache -> Single-Shot RAG -> Investigator ReAct Loop)
    from app.services.agentic_rag import run_smart_agentic_rag
    rag_result = await run_smart_agentic_rag(
        query=query_text,
        user_id=str(current_user.id),
        db=db,
    )

    reply_text = rag_result["reply"]
    sources = rag_result.get("sources", [])
    tokens_used = rag_result.get("tokens_used", 0)
    interactive_opts = rag_result.get("interactive_options") or []
    strategy_used = rag_result.get("strategy_used")

    # 3. Save messages in chat_messages table
    user_msg_id = uuid.uuid4()
    asst_msg_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    user_msg = ChatMessageModel(
        id=user_msg_id,
        conversation_id=conv.id,
        sender="user",
        content=query_text,
        sources=None,
        tokens_used=0,
        created_at=now,
    )
    db.add(user_msg)

    asst_msg = ChatMessageModel(
        id=asst_msg_id,
        conversation_id=conv.id,
        sender="assistant",
        content=reply_text,
        sources=sources[:5],
        tokens_used=tokens_used,
        created_at=now,
    )
    db.add(asst_msg)

    conv.updated_at = now
    await db.commit()

    # Invalidate cached user AI token metrics so settings reflects fresh usage immediately
    await delete_cache(make_user_tokens_cache_key(str(current_user.id)))

    return ChatMessageResponse(
        id=asst_msg_id,
        conversation_id=conv.id,
        sender="assistant",
        content=reply_text,
        sources=sources[:5],
        interactive_options=interactive_opts,
        strategy_used=strategy_used,
        tokens_used=tokens_used,
        created_at=now,
    )


@router.get("/history", response_model=List[ChatMessageResponse])
async def get_chat_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve chat message history for the current user."""
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == current_user.id)
        .order_by(desc(Conversation.updated_at))
        .limit(1)
    )
    res = await db.execute(stmt)
    conv = res.scalar_one_or_none()
    if not conv:
        return []

    msg_stmt = (
        select(ChatMessageModel)
        .where(ChatMessageModel.conversation_id == conv.id)
        .order_by(ChatMessageModel.created_at.asc())
        .limit(100)
    )
    msg_res = await db.execute(msg_stmt)
    return msg_res.scalars().all()


@router.delete("/history", status_code=status.HTTP_204_NO_CONTENT)
async def clear_chat_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Clear chat messages and conversations for the current user."""
    stmt = select(Conversation).where(Conversation.user_id == current_user.id)
    res = await db.execute(stmt)
    convs = res.scalars().all()
    for c in convs:
        await db.delete(c)
    await db.commit()
    return None
