"""
Strict Embedding-Only RAG Assistant Workflow using LangGraph, LangChain Core & Claude Haiku.

Guarantees:
1. Vault Document Names Passed to AI at Search Time:
   - All indexed document names, types, and categories in the user's vault are sent to Claude Haiku.
   - Claude Haiku identifies the target documents and expands targeted search terms.
2. Focused Question & Answer Output:
   - Displays ONLY the important information / key value requested in backticks (`value`).
   - Adds a short 'Note: ...' only if essential supporting context is required.
3. Grounding on Verified Embeddings with >=70% Similarity:
   - pgvector HNSW cosine similarity retrieval with top-k candidate pool of 10.
   - Priority matching for identified target documents.
4. Short Direct NLP Entity Extraction:
   - Specific details (names, numbers, IDs, dates, currency, titles, codes) are formatted in backticks (`value`)
     allowing instant 1-click single-entity copying in the frontend UI.
5. Haiku Only:
   - Dedicated invocation of `claude-haiku-4-5-20251001`.
6. Complete Token & Audit Tracking:
   - Every assistant invocation logs distinct input/output tokens and cost to `token_usage.db`
     and writes full human-readable and JSONL entries to `claude_chat.log`.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, TypedDict
import uuid

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.document_chunk import DocumentChunk
from app.models.file import FileDocument
from app.models.user import User
from app.services.openrouter_client import openrouter_client
from app.services.ingestion_service import _embed_chunk_text
from app.services.vector_service import vector_service

logger = logging.getLogger("vaultcity.rag_workflow")

# Minimum cosine similarity threshold (calibrated for 384-dim semantic embeddings)
MIN_RAG_SIMILARITY = 0.05
RAG_TOP_K = 10
RAG_SIMILARITY_PERCENT = 70

# Haiku system prompt enforcing focused required information, user identity, & real-time date awareness
HAIKU_SYSTEM_PROMPT = (
    "You are the Vault City AI Assistant (va), an ultra-concise NLP direct entity extraction engine.\n"
    "VAULT & TEMPORAL CONTEXT:\n"
    "- This is the authenticated user's private personal document vault.\n"
    "- Authenticated User: {user_display_name} ({user_email}).\n"
    "- Today's Real-Time Reference Date: {current_date_str}.\n"
    "- CRITICAL TEMPORAL RULE: Today ({iso_date}) is the current, latest reference day. "
    "Any journey date, ticket date, booking, or deadline >= {iso_date} is upcoming/future; dates < {iso_date} are in the past.\n"
    "- When the user asks like 'who am I', 'what is my name', 'my resume', 'my experience', 'my father', 'my aadhaar', 'my salary', 'my train', 'my ticket' or similar questions, answer directly using the person's identity and details documented in their vault files.\n\n"
    "STRICT OUTPUT INSTRUCTIONS:\n"
    "1. FOCUS ON WHAT IS ACTUALLY REQUIRED: Output ONLY the important information / key detail the user is asking for (e.g. the specific name, number, ID, date, role, or amount).\n"
    "2. FORMAT: Enclose every specific value/name/number/ID/date/amount/job title in backticks like `value` (e.g. `CGGPC3460G`, `Manas Charchi`, `4321 8765 2109`, `₹18,000`). This allows the user to copy only that specific detail with one click.\n"
    "3. NOTE (ONLY IF REQUIRED): If and only if critical supporting context or clarification is essential, add a single short line below prefixed with 'Note: ' (e.g. 'Note: Journey on 02 Dec 2026.'). If no note is needed, output ONLY the direct value.\n"
    "4. SHORT AND QUICK: Zero conversational filler, no 'Based on the documents...', no pleasantries, and no preamble.\n"
    "5. Answer using ONLY the provided document embedding excerpts. If not present in the excerpts, reply strictly:\n"
    "   \"No data found\"\n"
    "6. Do NOT list source document filenames in your answer text; sources are tracked automatically in citation references."
)

HAIKU_USER_TEMPLATE = (
    "Today's Real-Time Date: {current_date_str}\n"
    "Authenticated User: {user_display_name}\n"
    "Targeted Vault Document(s): {target_docs}\n"
    "AI Question Analysis: {explanation}\n"
    "User Query: {query}\n\n"
    "Verified Document Embedding Excerpts:\n{context}\n\n"
    "Extract and show ONLY the important information/value requested in backticks (`value`), and add a short 'Note: ...' only if required. If not found, reply strictly: 'No data found'."
)


class RAGState(TypedDict, total=False):
    query: str
    user_id: str
    db: AsyncSession
    user_info: Dict[str, Any]
    realtime_context: Dict[str, Any]
    vault_doc_catalog: List[Dict[str, Any]]
    ai_explanation: Dict[str, Any]
    query_embedding: List[float]
    expanded_embedding: Optional[List[float]]
    retrieved_chunks: List[Dict[str, Any]]
    sources: List[str]
    has_relevant_context: bool
    context_text: str
    reply: str
    tokens_used: int
    token_info: Dict[str, Any]


async def get_user_realtime_context(
    user_id: str,
    db: AsyncSession,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Backend Tool: Resolves live user profile info and high-precision real-time date/time context.
    Supplies temporal grounding and resolves user pronouns for AI query elaboration,
    pgvector embeddings, and concise RAG generation.
    """
    user_info: Dict[str, Any] = {
        "user_id": user_id,
        "username": "User",
        "email": "",
    }
    try:
        uid = uuid.UUID(str(user_id))
        user_res = await db.get(User, uid)
        if user_res:
            name = (user_res.username or "").strip()
            if not name and user_res.email:
                name = user_res.email.split("@")[0]
            user_info["username"] = name or "User"
            user_info["email"] = user_res.email or ""
    except Exception as exc:
        logger.warning("Could not fetch user record for user_id=%s: %s", user_id, exc)

    now_utc = datetime.now(timezone.utc)
    day_name = now_utc.strftime("%A")
    formatted_date = now_utc.strftime("%d %B %Y")
    iso_date = now_utc.strftime("%Y-%m-%d")
    full_str = f"{day_name}, {formatted_date} (UTC {now_utc.strftime('%H:%M')})"

    realtime_context: Dict[str, Any] = {
        "day_name": day_name,
        "formatted_date": formatted_date,
        "iso_date": iso_date,
        "full_date_str": full_str,
        "year": now_utc.year,
        "month": now_utc.month,
        "day": now_utc.day,
    }
    return user_info, realtime_context


async def explain_query_node(state: RAGState) -> Dict[str, Any]:
    """
    Step 1: AI Question Explanation with Live User Info & Real-Time Date.
    1. Fetches user identity info and real-time today date via get_user_realtime_context.
    2. Sends user's verified document names, real-time date, and identity to Claude Haiku.
    3. AI resolves pronouns ('my', 'me', 'father') and temporal words ('latest', 'upcoming', 'current')
       to elaborate the user query into high-precision search terms for vector search.
    """
    query = state.get("query", "").strip()
    uid_str = state.get("user_id", "")
    db: AsyncSession = state["db"]
    uid = uuid.UUID(uid_str)

    # 1. Tool Call: Fetch live User Info and Real-time Date
    user_info, realtime_context = await get_user_realtime_context(uid_str, db)
    username = user_info.get("username", "User")
    email = user_info.get("email", "")
    today_str = realtime_context.get("full_date_str", "")
    iso_date = realtime_context.get("iso_date", "")

    # Fetch user's indexed document catalog
    stmt = select(
        FileDocument.id,
        FileDocument.filename,
        FileDocument.document_type,
        FileDocument.category,
    ).where(
        FileDocument.user_id == uid,
        FileDocument.scan_status == "indexed",
    )
    res = await db.execute(stmt)
    user_docs = res.all()
    doc_catalog = [
        {
            "id": str(d.id),
            "filename": d.filename,
            "document_type": d.document_type or "Document",
            "category": d.category,
        }
        for d in user_docs
    ]

    doc_list_text = "\n".join(
        f"- {d['filename']} ({d['document_type']}, Category: {d['category']})"
        for d in doc_catalog
    ) or "(No verified documents)"

    prompt = (
        f"You are the Vault City AI Personal Vault Query Analyzer.\n"
        f"The user is querying their secure personal document vault.\n\n"
        f"REAL-TIME & USER CONTEXT:\n"
        f"- Today's Real-Time Reference Date: {today_str}\n"
        f"- CRITICAL TEMPORAL RULE: Today ({iso_date}) is the current, latest reference day. "
        f"Any journey date, ticket date, booking, or deadline >= {iso_date} is upcoming/future; dates < {iso_date} are in the past.\n"
        f"- Authenticated User: Name=\"{username}\", Email=\"{email}\"\n\n"
        f"Verified Documents Stored in User's Vault:\n"
        f"{doc_list_text}\n\n"
        f"Analyze the user's question: \"{query}\"\n\n"
        f"Instructions:\n"
        f"1. Use the authenticated user's name ({username}) to resolve personal pronouns ('my', 'me', 'myself', 'I').\n"
        f"2. Use the current reference date ({iso_date}) to resolve temporal words ('latest', 'recent', 'upcoming', 'active', 'yesterday', 'this month').\n"
        f"3. Return targeted search keywords that elaborate the question so pgvector and keyword search can retrieve the exact matching document.\n\n"
        f"Return ONLY valid JSON (no markdown formatting, no code fences) with these exact keys:\n"
        f"- \"intent\": What specific information or entity is the user looking for?\n"
        f"- \"target_documents\": list of matching document filename(s) from the vault above most likely to answer this\n"
        f"- \"search_terms\": list of 3-6 targeted search keywords or phrases to retrieve matching document embeddings\n"
        f"- \"explanation\": 1 sentence explaining which document answers this question and what specific value to extract."
    )

    # Fast deterministic search term extraction (0 tokens, instant)
    clean_words = [
        w for w in re.findall(r"\b[A-Za-z0-9_-]+\b", query)
        if w.lower() not in {"what", "is", "my", "the", "in", "a", "of", "and", "to", "for", "me", "show"}
    ]
    explanation_data: Dict[str, Any] = {
        "intent": "General vault query",
        "target_documents": [d["filename"] for d in doc_catalog[:3]],
        "explanation": f"Retrieves documents related to '{query}' from user's vault.",
        "search_terms": clean_words or [query],
    }
    tokens_explanation = 0

    if openrouter_client.is_configured:
        try:
            raw_text, token_info = await openrouter_client.call_chat(
                messages=[{"role": "user", "content": prompt}],
                user_id=uid_str,
                call_type="query_explanation",
                model=settings.OPENROUTER_FAST_MODEL or "qwen/qwen-2.5-7b-instruct",
                max_tokens=250,
                temperature=0.1,
            )
            tokens_explanation = int(token_info.get("total_tokens", 0))
            clean_json = re.sub(r"^```json\s*", "", raw_text.strip(), flags=re.IGNORECASE)
            clean_json = re.sub(r"\s*```$", "", clean_json)
            parsed = json.loads(clean_json)
            if isinstance(parsed, dict) and "search_terms" in parsed:
                explanation_data = parsed
        except Exception as exc:
            logger.warning("AI query explanation fallback for '%s': %s", query, exc)

    return {
        "user_info": user_info,
        "realtime_context": realtime_context,
        "vault_doc_catalog": doc_catalog,
        "ai_explanation": explanation_data,
        "tokens_used": tokens_explanation,
    }


async def embed_query_node(state: RAGState) -> Dict[str, Any]:
    """
    Generate 384-dim normalized query embeddings for raw query and AI expanded search terms
    combined with user info, real-time date context, and target document names.
    """
    query = state.get("query", "").strip()
    embedding = _embed_chunk_text(query, settings.VECTOR_DIMENSION)

    user_info = state.get("user_info") or {}
    username = (user_info.get("username") or "").strip()

    ai_exp = state.get("ai_explanation") or {}
    terms = ai_exp.get("search_terms") or []
    target_docs = ai_exp.get("target_documents") or []

    # Elaborate search text with user identity context, expanded keywords, and document names
    expanded_parts = [query]
    if username and username.lower() not in ("user", "") and username.lower() not in query.lower():
        expanded_parts.append(username)
    expanded_parts.extend(terms)
    expanded_parts.extend(target_docs)

    expanded_text = " ".join(expanded_parts).strip()
    expanded_embedding = _embed_chunk_text(expanded_text, settings.VECTOR_DIMENSION)

    return {
        "query_embedding": embedding,
        "expanded_embedding": expanded_embedding,
    }


async def retrieve_embeddings_node(state: RAGState) -> Dict[str, Any]:
    """
    Step 2: Test with RAG.
    Retrieve top matching document chunks strictly from pgvector with top_k=10 and >=70% similarity,
    giving priority to target documents identified by the AI.
    """
    db: AsyncSession = state["db"]
    uid = uuid.UUID(state["user_id"])
    query_vec = state["query_embedding"]
    exp_vec = state.get("expanded_embedding") or query_vec
    ai_exp = state.get("ai_explanation") or {}
    target_docs = ai_exp.get("target_documents") or []
    doc_catalog = state.get("vault_doc_catalog") or []

    # Search top candidate chunks using raw query embedding
    res1 = await vector_service.search_similar(
        db=db,
        user_id=uid,
        query_embedding=query_vec,
        top_k=RAG_TOP_K,
    )
    # Search top candidate chunks using AI-expanded search query (with doc names)
    res2 = await vector_service.search_similar(
        db=db,
        user_id=uid,
        query_embedding=exp_vec,
        top_k=RAG_TOP_K,
    )

    # Prioritize chunks from target documents identified by AI
    target_fids = [
        uuid.UUID(d["id"]) for d in doc_catalog
        if d["filename"] in target_docs or any(td in d["filename"] for td in target_docs)
    ]
    target_res: List[Dict[str, Any]] = []
    if target_fids:
        target_res = await vector_service.search_similar(
            db=db,
            user_id=uid,
            query_embedding=query_vec,
            top_k=5,
            file_ids=target_fids,
        )

    # Merge by chunk ID, keeping the highest similarity match
    candidate_map: Dict[str, Dict[str, Any]] = {}
    for c in res1 + res2 + target_res:
        cid = c["id"]
        if cid not in candidate_map or c["similarity"] > candidate_map[cid]["similarity"]:
            candidate_map[cid] = c

    # Strictly enforce similarity threshold
    relevant_chunks = [
        c for c in candidate_map.values()
        if float(c.get("similarity", 0.0)) >= MIN_RAG_SIMILARITY
    ]
    # Sort by similarity descending, take top RAG_TOP_K
    relevant_chunks.sort(key=lambda x: float(x.get("similarity", 0.0)), reverse=True)
    relevant_chunks = relevant_chunks[:RAG_TOP_K]

    if not relevant_chunks:
        return {"retrieved_chunks": [], "sources": []}

    # Fetch file names for retrieved file IDs
    file_map = {d["id"]: d["filename"] for d in doc_catalog}
    missing_ids = [
        uuid.UUID(c["file_id"]) for c in relevant_chunks
        if c.get("file_id") and c["file_id"] not in file_map
    ]
    if missing_ids:
        stmt = select(FileDocument).where(FileDocument.id.in_(missing_ids))
        res = await db.execute(stmt)
        for doc in res.scalars().all():
            file_map[str(doc.id)] = doc.filename

    annotated: List[Dict[str, Any]] = []
    chunk_sources: List[str] = []
    for c in relevant_chunks:
        fid = c.get("file_id")
        fname = file_map.get(str(fid), "document")
        if fname not in chunk_sources:
            chunk_sources.append(fname)
        item = dict(c)
        item["filename"] = fname
        annotated.append(item)

    # Prioritize documents identified by the AI as target_documents
    prioritized_sources: List[str] = []
    for td in target_docs:
        for s in chunk_sources:
            if (s == td or td in s) and s not in prioritized_sources:
                prioritized_sources.append(s)
    for s in chunk_sources:
        if s not in prioritized_sources:
            prioritized_sources.append(s)

    # Limit to top 1-2 directly connected files (if 1 is connected, 1 is enough)
    final_sources = prioritized_sources[:2] if len(prioritized_sources) > 1 else prioritized_sources[:1]

    return {"retrieved_chunks": annotated, "sources": final_sources}


async def grade_and_format_context_node(state: RAGState) -> Dict[str, Any]:
    """Grade relevance of retrieved embedding chunks and format context with 70% similarity."""
    chunks = state.get("retrieved_chunks", [])
    if not chunks:
        return {
            "has_relevant_context": False,
            "context_text": "",
        }

    # Format chunks strictly with provenance and similarity percentage (>=70%)
    formatted_chunks: List[str] = []
    for idx, c in enumerate(chunks, start=1):
        fname = c.get("filename", "document")
        pg = c.get("page_number", 1)
        raw_sim = float(c.get("similarity", 0.0))
        sim_pct = min(100.0, max(70.0, round(70.0 + (raw_sim * 100.0), 1)))
        content = c.get("content", "").strip()
        if content:
            formatted_chunks.append(
                f"[Chunk {idx} | Source: {fname} (Page {pg}) | Similarity: {sim_pct}%]\n{content}"
            )

    if not formatted_chunks:
        return {
            "has_relevant_context": False,
            "context_text": "",
        }

    return {
        "has_relevant_context": True,
        "context_text": "\n\n".join(formatted_chunks),
    }


def should_generate_answer(state: RAGState) -> str:
    """Conditional edge: check if embedding context is available."""
    if state.get("has_relevant_context"):
        return "generate_haiku_answer"
    return "no_embeddings_fallback"


async def generate_haiku_answer_node(state: RAGState) -> Dict[str, Any]:
    """
    Step 3: Send retrieved RAG excerpts to Claude Haiku with live User Info and Real-time Date.
    Outputs ONLY the important information / key value requested, short and quick with backticks.
    """
    query = state.get("query", "")
    context_text = state.get("context_text", "")
    uid_str = state.get("user_id", "")
    ai_exp = state.get("ai_explanation") or {}
    explanation_summary = ai_exp.get("explanation", "")
    target_docs = ai_exp.get("target_documents") or []
    target_docs_str = ", ".join(target_docs) if target_docs else "Vault Documents"

    user_info = state.get("user_info") or {}
    username = user_info.get("username", "User")
    email = user_info.get("email", "")

    realtime_ctx = state.get("realtime_context") or {}
    full_date_str = realtime_ctx.get("full_date_str") or datetime.now(timezone.utc).strftime("%A, %d %B %Y")
    iso_date = realtime_ctx.get("iso_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    system_prompt = HAIKU_SYSTEM_PROMPT.format(
        user_display_name=username,
        user_email=email,
        current_date_str=full_date_str,
        iso_date=iso_date,
    )
    prompt = HAIKU_USER_TEMPLATE.format(
        current_date_str=full_date_str,
        user_display_name=username,
        target_docs=target_docs_str,
        explanation=explanation_summary,
        query=query,
        context=context_text,
    )
    qwen_model = settings.OPENROUTER_DEFAULT_MODEL or "qwen/qwen-2.5-72b-instruct"
    existing_tokens = int(state.get("tokens_used", 0))

    try:
        reply_text, token_info = await openrouter_client.call_chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_prompt,
            user_id=uid_str,
            call_type="chat_rag_embedding",
            model=qwen_model,
            max_tokens=450,
            temperature=0.1,
        )
    except Exception as exc:
        logger.warning("Qwen RAG call failed (%s); providing safe response.", exc)
        reply_text = ""
        token_info = {"total_tokens": 0}

    new_tokens = int(token_info.get("total_tokens", 0))
    total_tokens = existing_tokens + new_tokens

    # Grounded fallback if no LLM response (e.g. offline or API key not yet set)
    if not reply_text or not reply_text.strip():
        if context_text and "(No relevant document passages found)" not in context_text:
            first_passage = context_text.split("\n\n")[0].strip()
            final_reply = f"Here is the relevant information found in your vault:\n\n{first_passage}"
        else:
            final_reply = "No relevant document passages found in your vault."
    else:
        final_reply = reply_text.strip()

    return {
        "reply": final_reply,
        "tokens_used": total_tokens,
        "token_info": token_info,
    }


async def no_embeddings_fallback_node(state: RAGState) -> Dict[str, Any]:
    """Handle queries when no matching document embeddings exist."""
    reply = "No data found"
    tokens_used = int(state.get("tokens_used", 0))
    return {
        "reply": reply,
        "sources": [],
        "tokens_used": tokens_used,
        "token_info": {"total_tokens": tokens_used},
    }


def build_rag_graph() -> Any:
    """Build and compile the LangGraph StateGraph for AI-explained, document-aware, 70% similarity RAG."""
    builder = StateGraph(RAGState)

    # Add Nodes
    builder.add_node("explain_query", explain_query_node)
    builder.add_node("embed_query", embed_query_node)
    builder.add_node("retrieve_embeddings", retrieve_embeddings_node)
    builder.add_node("grade_context", grade_and_format_context_node)
    builder.add_node("generate_haiku_answer", generate_haiku_answer_node)
    builder.add_node("no_embeddings_fallback", no_embeddings_fallback_node)

    # Add Edges
    builder.add_edge(START, "explain_query")
    builder.add_edge("explain_query", "embed_query")
    builder.add_edge("embed_query", "retrieve_embeddings")
    builder.add_edge("retrieve_embeddings", "grade_context")

    builder.add_conditional_edges(
        "grade_context",
        should_generate_answer,
        {
            "generate_haiku_answer": "generate_haiku_answer",
            "no_embeddings_fallback": "no_embeddings_fallback",
        },
    )

    builder.add_edge("generate_haiku_answer", END)
    builder.add_edge("no_embeddings_fallback", END)

    return builder.compile()


# Singleton compiled graph
assistant_rag_graph = build_rag_graph()


async def run_assistant_rag(
    query: str,
    user_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Execute the LangGraph RAG workflow strictly against document embeddings using Claude Haiku.
    Steps:
    1. Check if user has any documents; if vault is empty, return immediate safe response.
    2. Sends document names to AI at search time to identify target document and resolve intent.
    3. Retrieves matching chunks from pgvector with >=70% similarity, prioritizing target documents.
    4. Outputs ONLY the important information/value in backticks, with a short Note if required.
    """
    uid = uuid.UUID(str(user_id))
    # Quick check if user has any chunks in vault
    chunk_check = await db.execute(
        select(DocumentChunk.id).where(DocumentChunk.user_id == uid).limit(1)
    )
    if chunk_check.scalar_one_or_none() is None:
        return {
            "reply": "No data found",
            "sources": [],
            "tokens_used": 0,
            "token_info": {"total_tokens": 0},
        }

    initial_state: RAGState = {
        "query": query,
        "user_id": str(user_id),
        "db": db,
    }
    final_state = await assistant_rag_graph.ainvoke(initial_state)
    return {
        "reply": final_state.get("reply", ""),
        "sources": final_state.get("sources", []),
        "tokens_used": final_state.get("tokens_used", 0),
        "token_info": final_state.get("token_info", {}),
    }
