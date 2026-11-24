"""
Pydantic schemas for RAG conversations and chat messages.
"""
import uuid
from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class ChatMessageCreate(BaseModel):
    """Schema for user chat message creation."""
    content: str = Field(..., min_length=1, description="Message text content")
    conversation_id: Optional[uuid.UUID] = Field(None, description="Optional existing conversation ID")


class InteractiveOption(BaseModel):
    """Clickable 1-click option pill for interactive clarification."""
    id: str
    label: str
    value: str
    prompt_payload: str


class ChatMessageResponse(BaseModel):
    """Chat message response schema."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    sender: str = Field(..., max_length=32)
    content: str
    sources: Optional[List[Any]] = None
    interactive_options: Optional[List[InteractiveOption]] = None
    strategy_used: Optional[str] = None
    tokens_used: int = 0
    created_at: datetime


class ConversationCreate(BaseModel):
    """Schema for initializing a new conversation thread."""
    title: Optional[str] = Field("New Chat", max_length=255)


class ConversationResponse(BaseModel):
    """Conversation thread metadata response."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime
    messages: Optional[List[ChatMessageResponse]] = None
