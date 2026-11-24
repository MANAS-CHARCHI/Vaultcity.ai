"""
Identity Entity Extractor & User Profile Graph Service.

Extracts high-priority personal entities (e.g. father's name, mother's name, DOB, PAN, Aadhaar)
from indexed document text and upserts them into `user_entities` table.
Enables instant (0ms) multi-hop deductions for the smart agent.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_entity import UserEntity

logger = logging.getLogger("vaultcity.entity_extractor")

# Strict regex patterns for deterministic identity entity extraction
_PATTERNS = {
    "father_name": {
        "regex": re.compile(
            r"(?:father(?:'s)?\s*(?:name)?|s/o|d/o|son\s+of|daughter\s+of)\s*[:\-]\s*([A-Za-z\s\.]{3,35})",
            re.IGNORECASE,
        ),
        "label": "Father's Full Name",
    },
    "mother_name": {
        "regex": re.compile(
            r"(?:mother(?:'s)?\s*(?:name)?)\s*[:\-]\s*([A-Za-z\s\.]{3,35})",
            re.IGNORECASE,
        ),
        "label": "Mother's Full Name",
    },
    "spouse_name": {
        "regex": re.compile(
            r"(?:spouse(?:'s)?\s*(?:name)?|w/o|wife\s+of|husband\s+of)\s*[:\-]\s*([A-Za-z\s\.]{3,35})",
            re.IGNORECASE,
        ),
        "label": "Spouse's Full Name",
    },
    "dob": {
        "regex": re.compile(
            r"(?:date\s+of\s+birth|d\.?o\.?b\.?|born\s+on)\s*[:\-]?\s*([0-9]{1,2}[/\-\.][0-9]{1,2}[/\-\.][0-9]{2,4}|[0-9]{4}[/\-\.][0-9]{1,2}[/\-\.][0-9]{1,2})",
            re.IGNORECASE,
        ),
        "label": "Date of Birth",
    },
    "pan_number": {
        "regex": re.compile(
            r"\b([A-Z]{5}[0-9]{4}[A-Z]{1})\b",
        ),
        "label": "Income Tax PAN",
    },
    "aadhaar_number": {
        "regex": re.compile(
            r"\b([2-9]{1}[0-9]{3}\s?[0-9]{4}\s?[0-9]{4})\b",
        ),
        "label": "Aadhaar Number",
    },
    "passport_number": {
        "regex": re.compile(
            r"\b([A-PR-WYa-pr-wy][1-9][0-9]{6})\b",
        ),
        "label": "Passport Number",
    },
    "blood_group": {
        "regex": re.compile(
            r"\b(A|B|AB|O)[+-]\s*(?:ve)?\b",
            re.IGNORECASE,
        ),
        "label": "Blood Group",
    },
}


def extract_identity_entities(text: str) -> Dict[str, Dict[str, Any]]:
    """
    Scan normalized text for known personal identity attributes.
    Returns dictionary mapping entity_key to value and label.
    """
    found: Dict[str, Dict[str, Any]] = {}
    if not text:
        return found

    for key, spec in _PATTERNS.items():
        match = spec["regex"].search(text)
        if match:
            raw_val = match.group(1).strip()
            # Clean common trailing punctuation or artifacts
            cleaned_val = re.sub(r"[\n\r,;]+", " ", raw_val).strip()
            if len(cleaned_val) >= 2:
                # Basic validation: ensure father_name isn't just a label like "Name" or "Address"
                if key.endswith("_name") and cleaned_val.lower() in ("name", "father", "mother", "guardian", "unknown"):
                    continue
                found[key] = {
                    "value": cleaned_val,
                    "label": spec["label"],
                }

    return found


async def upsert_user_entity(
    db: AsyncSession,
    user_id: uuid.UUID,
    entity_key: str,
    entity_value: str,
    entity_label: Optional[str] = None,
    source_file_id: Optional[uuid.UUID] = None,
    confidence: float = 1.0,
) -> UserEntity:
    """
    Atomic PostgreSQL upsert of a single user identity entity.
    """
    clean_val = entity_value.strip()[:255]
    stmt = (
        insert(UserEntity)
        .values(
            user_id=user_id,
            entity_key=entity_key,
            entity_value=clean_val,
            entity_label=entity_label,
            source_file_id=source_file_id,
            confidence=confidence,
            updated_at=datetime.now(timezone.utc),
        )
        .on_conflict_do_update(
            constraint="uq_user_entity_key",
            set_={
                "entity_value": clean_val,
                "entity_label": entity_label,
                "source_file_id": source_file_id,
                "confidence": confidence,
                "updated_at": datetime.now(timezone.utc),
            },
        )
        .returning(UserEntity)
    )
    res = await db.execute(stmt)
    row = res.scalar_one()
    await db.flush()
    return row


async def sync_entities_from_document(
    db: AsyncSession,
    user_id: uuid.UUID,
    text: str,
    source_file_id: Optional[uuid.UUID] = None,
) -> List[Dict[str, Any]]:
    """
    Extract and persist all discovered identity entities from document text.
    """
    extracted = extract_identity_entities(text)
    saved: List[Dict[str, Any]] = []

    for key, spec in extracted.items():
        try:
            entity = await upsert_user_entity(
                db=db,
                user_id=user_id,
                entity_key=key,
                entity_value=spec["value"],
                entity_label=spec["label"],
                source_file_id=source_file_id,
            )
            saved.append({
                "key": entity.entity_key,
                "value": entity.entity_value,
                "label": entity.entity_label,
            })
            logger.info(
                "Persisted user identity entity key=%s value=%s for user_id=%s",
                key, spec["value"], user_id
            )
        except Exception as exc:
            logger.warning("Failed to upsert entity key=%s for user_id=%s: %s", key, user_id, exc)

    return saved


async def get_user_entity_value(
    db: AsyncSession,
    user_id: uuid.UUID,
    entity_key: str,
) -> Optional[str]:
    """Retrieve single entity value by key (e.g. 'father_name')."""
    stmt = (
        select(UserEntity.entity_value)
        .where(
            UserEntity.user_id == user_id,
            UserEntity.entity_key == entity_key,
        )
        .limit(1)
    )
    res = await db.execute(stmt)
    return res.scalar_one_or_none()


async def get_all_user_entities(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> Dict[str, str]:
    """Retrieve dictionary of all stored identity key-values for user."""
    stmt = select(UserEntity.entity_key, UserEntity.entity_value).where(
        UserEntity.user_id == user_id
    )
    res = await db.execute(stmt)
    return {k: v for k, v in res.all()}
