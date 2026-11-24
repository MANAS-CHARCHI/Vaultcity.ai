"""
Deterministic Agent Tools for Smart Agentic RAG.

Provides 4 mathematically exact and factual Python tools for Agent 2 (The Investigator):
1. `tool_get_user_entity`: 0ms identity graph lookup (e.g. father's name, DOB, PAN).
2. `tool_search_vault`: Hybrid pgvector + keyword search with optional document filter.
3. `tool_filter_by_date`: Python datetime comparator for upcoming vs past journeys/tickets.
4. `tool_ask_user_choice`: Interactive clarification payload generator for multi-candidate choices.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.file import FileDocument
from app.models.user import User
from app.services.entity_extractor import get_user_entity_value
from app.services.ingestion_service import _embed_chunk_text
from app.services.vector_service import vector_service

logger = logging.getLogger("vaultcity.agent_tools")

# Common regex patterns to extract travel / ticket dates from document excerpts
_DATE_PATTERNS = [
    # 02-Dec-2026 or 02 Dec 2026 or 2 Dec 2026
    re.compile(r"\b([0-3]?[0-9])[\s\-\./](Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s\-\./](20[2-3][0-9])\b", re.IGNORECASE),
    # 2026-12-02 or 2026/12/02
    re.compile(r"\b(20[2-3][0-9])[\-\./](0?[1-9]|1[0-2])[\-\./](0?[1-9]|[12][0-9]|3[01])\b"),
    # 02-12-2026 or 02/12/2026
    re.compile(r"\b(0?[1-9]|[12][0-9]|3[01])[\-\./](0?[1-9]|1[0-2])[\-\./](20[2-3][0-9])\b"),
]

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
}


def parse_extracted_date(text: str) -> Optional[date]:
    """Extract and parse first valid calendar date found in text snippet."""
    if not text:
        return None

    # Check pattern 1: DD-Mon-YYYY
    m1 = _DATE_PATTERNS[0].search(text)
    if m1:
        try:
            day = int(m1.group(1))
            mon_str = m1.group(2).lower()[:3]
            month = _MONTH_MAP.get(mon_str, 1)
            year = int(m1.group(3))
            return date(year, month, day)
        except Exception:
            pass

    # Check pattern 2: YYYY-MM-DD
    m2 = _DATE_PATTERNS[1].search(text)
    if m2:
        try:
            year = int(m2.group(1))
            month = int(m2.group(2))
            day = int(m2.group(3))
            return date(year, month, day)
        except Exception:
            pass

    # Check pattern 3: DD-MM-YYYY
    m3 = _DATE_PATTERNS[2].search(text)
    if m3:
        try:
            day = int(m3.group(1))
            month = int(m3.group(2))
            year = int(m3.group(3))
            return date(year, month, day)
        except Exception:
            pass

    return None


async def tool_get_user_entity(
    user_id: uuid.UUID,
    entity_key: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Tool 1: Instant lookup of identity facts (father_name, mother_name, spouse_name, dob, pan).
    """
    clean_key = entity_key.strip().lower()
    val = await get_user_entity_value(db, user_id=user_id, entity_key=clean_key)
    if val:
        return {
            "found": True,
            "entity_key": clean_key,
            "value": val,
            "source": "user_entities_cache",
        }

    # Fallback for user's own identity from user table
    user_row = await db.get(User, user_id)
    if user_row:
        if clean_key in ("user_name", "my_name", "name", "self"):
            name = user_row.username or (user_row.email.split("@")[0] if user_row.email else "User")
            return {"found": True, "entity_key": clean_key, "value": name, "source": "user_profile"}
        if clean_key in ("email", "my_email"):
            return {"found": True, "entity_key": clean_key, "value": user_row.email, "source": "user_profile"}

    return {
        "found": False,
        "entity_key": clean_key,
        "value": None,
        "message": f"No identity record found for '{entity_key}'.",
    }


async def tool_search_vault(
    user_id: uuid.UUID,
    query: str,
    db: AsyncSession,
    doc_name_filter: Optional[str] = None,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    Tool 2: Hybrid vector & keyword search with optional document name/category filter.
    """
    query_clean = query.strip()
    if not query_clean:
        return []

    target_file_ids: Optional[List[uuid.UUID]] = None
    if doc_name_filter:
        clean_flt = doc_name_filter.strip().lower()
        stmt = select(FileDocument.id).where(
            FileDocument.user_id == user_id,
            FileDocument.scan_status == "indexed",
        )
        res = await db.execute(stmt)
        all_docs = res.scalars().all()
        # Filter files by name or category match
        stmt2 = select(FileDocument.id).where(
            FileDocument.user_id == user_id,
            FileDocument.scan_status == "indexed",
            (FileDocument.filename.ilike(f"%{clean_flt}%") | FileDocument.category.ilike(f"%{clean_flt}%")),
        )
        res2 = await db.execute(stmt2)
        matched_ids = res2.scalars().all()
        if matched_ids:
            target_file_ids = matched_ids

    # 384-dim semantic query embedding
    q_vec = _embed_chunk_text(query_clean, settings.VECTOR_DIMENSION)

    results = await vector_service.search_similar(
        db=db,
        user_id=user_id,
        query_embedding=q_vec,
        top_k=top_k,
        file_ids=target_file_ids,
    )

    # Fetch filenames for chunks
    fids = [uuid.UUID(c["file_id"]) for c in results if c.get("file_id")]
    file_map: Dict[str, str] = {}
    if fids:
        f_stmt = select(FileDocument.id, FileDocument.filename).where(FileDocument.id.in_(fids))
        f_res = await db.execute(f_stmt)
        file_map = {str(fid): fname for fid, fname in f_res.all()}

    annotated: List[Dict[str, Any]] = []
    for c in results:
        fid_str = str(c.get("file_id", ""))
        annotated.append({
            "chunk_id": str(c.get("id")),
            "file_id": fid_str,
            "filename": file_map.get(fid_str, "document"),
            "page_number": c.get("page_number", 1),
            "similarity": float(c.get("similarity", 0.0)),
            "content": c.get("content", "").strip(),
        })

    return annotated


def tool_filter_by_date(
    items: List[Dict[str, Any]],
    mode: str = "upcoming",
    reference_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Tool 3: Pure Python date comparator.
    Strips expired tickets / records and sorts chronologically.
    Never relies on LLM date arithmetic.
    """
    if reference_date:
        try:
            ref_dt = datetime.strptime(reference_date, "%Y-%m-%d").date()
        except Exception:
            ref_dt = datetime.now(timezone.utc).date()
    else:
        ref_dt = datetime.now(timezone.utc).date()

    annotated_items: List[Dict[str, Any]] = []
    for it in items:
        text_content = it.get("content", "") + " " + it.get("filename", "")
        detected_date = parse_extracted_date(text_content)
        item_copy = dict(it)
        item_copy["parsed_journey_date"] = str(detected_date) if detected_date else None
        item_copy["_date_obj"] = detected_date
        annotated_items.append(item_copy)

    mode_lower = (mode or "upcoming").strip().lower()
    matches: List[Dict[str, Any]] = []

    if mode_lower == "upcoming":
        # Keep items with date >= reference_date
        for it in annotated_items:
            d = it.get("_date_obj")
            if d and d >= ref_dt:
                matches.append(it)
        # Sort ascending (nearest upcoming journey first)
        matches.sort(key=lambda x: x["_date_obj"])

    elif mode_lower == "past":
        # Keep items with date < reference_date
        for it in annotated_items:
            d = it.get("_date_obj")
            if d and d < ref_dt:
                matches.append(it)
        # Sort descending (most recent past journey first)
        matches.sort(key=lambda x: x["_date_obj"], reverse=True)

    elif mode_lower == "latest":
        # Return single most recent item
        dated = [it for it in annotated_items if it.get("_date_obj")]
        if dated:
            dated.sort(key=lambda x: x["_date_obj"], reverse=True)
            matches = [dated[0]]

    # Clean internal _date_obj before returning
    for m in matches:
        m.pop("_date_obj", None)

    return {
        "reference_date": str(ref_dt),
        "filter_mode": mode_lower,
        "total_analyzed": len(items),
        "matched_count": len(matches),
        "matches": matches,
    }


def tool_ask_user_choice(
    question: str,
    options: List[Dict[str, str]],
) -> Dict[str, Any]:
    """
    Tool 4: Generates interactive choice prompt payload for the frontend.
    Allows 1-click continuation without user typing.
    """
    clean_options: List[Dict[str, str]] = []
    for opt in options:
        lbl = opt.get("label", "").strip()
        val = opt.get("value", "").strip() or lbl
        prompt_payload = opt.get("prompt_payload", "").strip() or f"Provide details for {lbl}"
        clean_options.append({
            "id": f"opt-{uuid.uuid4().hex[:8]}",
            "label": lbl,
            "value": val,
            "prompt_payload": prompt_payload,
        })

    return {
        "status": "needs_clarification",
        "question": question.strip(),
        "options": clean_options,
    }
