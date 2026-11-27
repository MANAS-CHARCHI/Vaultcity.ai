"""
User Profile & Dynamic Knowledge Memory Service (user_memory.md).

Architecture:
1. Primary Source of Truth: Stored securely in MinIO/R2 at:
   `s3://vault-secured/vault/{user_id}/profile/user_memory.md`
2. Level-1 Performance Cache: Hot-cached in Redis at:
   `vault:memory:{user_id}` (1 millisecond retrieval latency).
3. Post-Chat Asynchronous Distillation: After a chat response is sent to the user,
   this service distills any newly discovered or confirmed personal facts
   and appends/updates the user's markdown memory file without adding latency.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import uuid

from app.core.redis import get_text_cache, set_text_cache, delete_cache
from app.services.s3_service import s3_service
from app.services.openrouter_client import openrouter_client

logger = logging.getLogger("vaultcity.memory")

_MEMORY_KEY_PREFIX = "vault:memory:"
_DEFAULT_TTL_SECONDS = 3600 * 24  # 24 hours in Redis
_local_memory_cache: Dict[str, str] = {}


def _get_s3_key(user_id: str | uuid.UUID) -> str:
    return f"vault/{str(user_id)}/profile/user_memory.md"


def _get_redis_key(user_id: str | uuid.UUID) -> str:
    return f"{_MEMORY_KEY_PREFIX}{str(user_id)}"


def _build_initial_memory_template(user_id: str | uuid.UUID) -> str:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""# Personal Memory Vault
- **User ID:** `{str(user_id)}`
- **Last Updated:** {now_iso}

## 1. Identity & Government Records
*(No verified identity records saved yet)*

## 2. Family & Dependents
*(No family details recorded yet)*

## 3. Financial & Accounts
*(No financial accounts recorded yet)*

## 4. Utilities, Travel & Bookings
*(No utility records saved yet)*

## 5. Notes & Key Highlights
*(No personal notes recorded yet)*
"""


class UserMemoryService:
    """Manages the lifecycle of user_memory.md across Redis and MinIO."""

    async def load_user_memory(self, user_id: str | uuid.UUID) -> str:
        """
        Retrieve the user's markdown memory sheet.
        Checks Redis (<1ms) -> In-Memory fallback -> MinIO -> creates default if missing.
        """
        uid_str = str(user_id)
        redis_key = _get_redis_key(uid_str)

        # 1. Hot Cache check in Redis
        cached = await get_text_cache(redis_key)
        if cached:
            _local_memory_cache[uid_str] = cached
            return cached

        # 2. Local process memory fallback (when Redis/MinIO is temporarily offline or in unit tests)
        if uid_str in _local_memory_cache:
            return _local_memory_cache[uid_str]

        # 3. Storage fetch from MinIO
        s3_key = _get_s3_key(uid_str)
        try:
            raw_bytes, _ = await s3_service.download_from_secured(s3_key)
            content = raw_bytes.decode("utf-8", errors="replace")
            # Populate Redis hot cache
            await set_text_cache(redis_key, content, ttl_seconds=_DEFAULT_TTL_SECONDS)
            _local_memory_cache[uid_str] = content
            return content
        except Exception:
            # File does not exist yet -> initialize fresh template
            fresh_template = _build_initial_memory_template(uid_str)
            _local_memory_cache[uid_str] = fresh_template
            await self.save_user_memory(uid_str, fresh_template)
            return fresh_template

    async def save_user_memory(self, user_id: str | uuid.UUID, content: str) -> None:
        """Persist markdown memory sheet to MinIO and refresh Redis cache."""
        uid_str = str(user_id)
        s3_key = _get_s3_key(uid_str)
        redis_key = _get_redis_key(uid_str)

        # Store in local process cache immediately
        _local_memory_cache[uid_str] = content

        raw_bytes = content.encode("utf-8")
        try:
            await s3_service.save_secured_file(
                secured_key=s3_key,
                data=raw_bytes,
                content_type="text/markdown; charset=utf-8",
                metadata={"user_id": uid_str, "type": "user_memory_md"},
            )
        except Exception as exc:
            logger.warning("Failed saving user_memory.md to MinIO for %s: %s", uid_str, exc)

        # Update Redis cache
        await set_text_cache(redis_key, content, ttl_seconds=_DEFAULT_TTL_SECONDS)

    async def get_memory_topics(self, user_id: str | uuid.UUID) -> List[str]:
        """
        Extract major sections and key bullet points from memory markdown for Jev context.
        """
        content = await self.load_user_memory(user_id)
        topics: List[str] = []
        for line in content.splitlines():
            line_str = line.strip()
            if line_str.startswith("## "):
                topics.append(line_str.replace("## ", "").strip())
            elif line_str.startswith("- **") and ":" in line_str:
                label = line_str.split(":")[0].replace("- **", "").replace("**", "").strip()
                if label:
                    topics.append(label)
        return topics[:15]

    async def distill_and_update_memory(
        self,
        user_id: str | uuid.UUID,
        query: str,
        reply: str,
    ) -> bool:
        """
        Background task executed after user receives their answer.
        Distills verified personal facts into the user's markdown memory file.
        """
        uid_str = str(user_id)
        if not reply or len(reply.strip()) < 10:
            return False

        # Ignore generic denials or questions asking the user for clarification
        reply_lower = reply.lower()
        if any(w in reply_lower for w in ["i cannot find", "no document", "not mentioned", "please provide", "sorry"]):
            return False

        # ---------------------------------------------------------------------
        # 1. Deterministic Regex Fact Extractor (0 tokens, instant)
        # ---------------------------------------------------------------------
        extracted_facts: List[Tuple[str, str, str]] = []  # (Section, Key, Value)

        # Identity matches
        pan_match = re.search(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b", reply)
        if pan_match and "pan" in query.lower():
            label = "Father's PAN" if "father" in query.lower() else "PAN Number"
            extracted_facts.append(("1. Identity & Government Records", label, pan_match.group(1)))

        aadhaar_match = re.search(r"\b(\d{4}\s\d{4}\s\d{4}|\d{12})\b", reply)
        if aadhaar_match and "aadhaar" in query.lower():
            label = "Mother's Aadhaar" if "mother" in query.lower() else "Aadhaar Number"
            extracted_facts.append(("1. Identity & Government Records", label, aadhaar_match.group(1)))

        dob_match = re.search(r"\b(\d{4}-\d{2}-\d{2}|\d{2}[/-]\d{2}[/-]\d{4})\b", reply)
        if dob_match and ("dob" in query.lower() or "birth" in query.lower()):
            extracted_facts.append(("1. Identity & Government Records", "Date of Birth", dob_match.group(1)))

        blood_match = re.search(r"\b(A|B|AB|O)[+-]\b", reply)
        if blood_match and "blood" in query.lower():
            extracted_facts.append(("1. Identity & Government Records", "Blood Group", blood_match.group(0)))

        # Travel / Tickets
        pnr_match = re.search(r"\b(\d{10})\b", reply)
        if pnr_match and "pnr" in query.lower():
            extracted_facts.append(("4. Utilities, Travel & Bookings", "Recent PNR Booking", pnr_match.group(1)))

        # If no regex facts found, we can check if Qwen fast model is configured
        if not extracted_facts and openrouter_client.is_configured:
            prompt = (
                f"Analyze this Q&A turn between user and personal document assistant:\n"
                f"User Question: {query}\n"
                f"Assistant Answer: {reply}\n\n"
                "If the assistant confirmed an exact personal fact, ID, policy number, or entity value, "
                "extract it. Return strictly JSON: {\"found\": true/false, \"section\": \"1|2|3|4|5\", \"key\": \"...\", \"value\": \"...\"}"
            )
            try:
                raw_json, _ = await openrouter_client.call_chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=openrouter_client.base_url,
                    max_tokens=150,
                    temperature=0.0,
                    call_type="memory_distill",
                )
                clean = re.sub(r"```json|```", "", raw_json).strip()
                data = json.loads(clean)
                if data.get("found") and data.get("key") and data.get("value"):
                    sec_map = {
                        "1": "1. Identity & Government Records",
                        "2": "2. Family & Dependents",
                        "3": "3. Financial & Accounts",
                        "4": "4. Utilities, Travel & Bookings",
                        "5": "5. Notes & Key Highlights",
                    }
                    target_sec = sec_map.get(str(data.get("section")), "5. Notes & Key Highlights")
                    extracted_facts.append((target_sec, data["key"], data["value"]))
            except Exception:
                pass

        if not extracted_facts:
            return False

        # ---------------------------------------------------------------------
        # 2. Apply updates to the Markdown document
        # ---------------------------------------------------------------------
        current_md = await self.load_user_memory(uid_str)
        updated_md = current_md

        for section, key, val in extracted_facts:
            new_bullet = f"- **{key}:** `{val}` *(Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d')})*"
            
            # Check if this exact key already exists in the document
            key_pattern = rf"- \*\*{re.escape(key)}:\*\* .*"
            if re.search(key_pattern, updated_md):
                # Update existing entry
                updated_md = re.sub(key_pattern, new_bullet, updated_md)
            else:
                # Append under target section
                sec_header = f"## {section}"
                if sec_header in updated_md:
                    parts = updated_md.split(sec_header, 1)
                    updated_md = f"{parts[0]}{sec_header}\n{new_bullet}{parts[1]}"
                else:
                    updated_md += f"\n\n## {section}\n{new_bullet}"

        # Clean placeholder lines
        updated_md = re.sub(r"\*\(No [^\)]+\)\*\n?", "", updated_md)

        await self.save_user_memory(uid_str, updated_md)
        logger.info("Successfully updated user_memory.md for user=%s with %d facts.", uid_str, len(extracted_facts))
        return True

    async def extract_and_sync_profile_from_document(
        self,
        user_id: str | uuid.UUID,
        filename: str,
        doc_type: str,
        summary: str,
        text: str,
    ) -> int:
        """
        User Profile Intelligence Agent (Ingestion Time).
        Scans uploaded document content and summary to discover personal info,
        identity numbers, family details, and account records, then writes them
        directly to user_memory.md in MinIO & Redis BEFORE any chat takes place.
        """
        uid_str = str(user_id)
        sample_text = f"{filename}\n{summary}\n{text[:4000]}"
        facts: List[Tuple[str, str, str]] = []

        # 1. Deterministic Extraction (Always works, $0 cost, offline)
        # PAN
        pan_match = re.search(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b", sample_text)
        if pan_match:
            label = "Father's PAN" if any(w in sample_text.lower() for w in ["father", "s/o", "d/o"]) and "father" in filename.lower() else "PAN Number"
            facts.append(("1. Identity & Government Records", label, pan_match.group(1)))

        # Aadhaar
        aadhaar_match = re.search(r"\b(\d{4}\s\d{4}\s\d{4}|\d{12})\b", sample_text)
        if aadhaar_match and any(w in sample_text.lower() for w in ["aadhaar", "uidai", "government of india", "mera aadhaar"]):
            facts.append(("1. Identity & Government Records", "Aadhaar Number", aadhaar_match.group(1)))

        # Passport
        passport_match = re.search(r"\b([A-Z][0-9]{7})\b", sample_text)
        if passport_match and "passport" in sample_text.lower():
            facts.append(("1. Identity & Government Records", "Passport Number", passport_match.group(1)))

        # Date of Birth
        dob_match = re.search(r"\b(?:DOB|Date of Birth|Birth Date)[:\s]+([0-9]{2}[/-][0-9]{2}[/-][0-9]{4}|[0-9]{4}-[0-9]{2}-[0-9]{2})\b", sample_text, re.IGNORECASE)
        if dob_match:
            facts.append(("1. Identity & Government Records", "Date of Birth", dob_match.group(1)))

        # Father's / Spouse name
        rel_match = re.search(r"\b(?:Father(?:'s)? Name|Father|S/O|D/O|W/O)[:\s]+([A-Za-z\s]{3,30})\b", sample_text, re.IGNORECASE)
        if rel_match:
            rel_name = rel_match.group(1).strip()
            if len(rel_name) > 2 and "\n" not in rel_name:
                facts.append(("2. Family & Dependents", "Father / Guardian", rel_name))

        # Utilities / Consumer ID
        util_match = re.search(r"\b(?:Consumer\s*(?:No|ID|Number)|Account\s*ID|Meter\s*(?:No|Number))[:\s]*([0-9A-Z-]+)\b", sample_text, re.IGNORECASE)
        if util_match:
            facts.append(("4. Utilities, Travel & Bookings", f"{doc_type} ID", util_match.group(1)))

        # 2. AI Intelligence Agent Extraction (via OpenRouter if configured)
        if openrouter_client.is_configured and not facts:
            prompt = (
                f"You are the User Profile Intelligence Agent for a personal vault.\n"
                f"Analyze this newly uploaded document:\n"
                f"Filename: {filename}\nType: {doc_type}\nSummary: {summary}\n\n"
                f"Content Sample:\n{text[:2500]}\n\n"
                "Extract any verified personal facts belonging to the user or their family. "
                "Return strictly JSON: {\"has_info\": true/false, \"facts\": [{\"section\": \"1|2|3|4\", \"key\": \"...\", \"value\": \"...\"}]}"
            )
            try:
                raw_json, _ = await openrouter_client.call_chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=openrouter_client.base_url,
                    max_tokens=200,
                    temperature=0.0,
                    call_type="ingestion_profile_agent",
                )
                clean = re.sub(r"```json|```", "", raw_json).strip()
                data = json.loads(clean)
                if data.get("has_info"):
                    sec_map = {
                        "1": "1. Identity & Government Records",
                        "2": "2. Family & Dependents",
                        "3": "3. Financial & Accounts",
                        "4": "4. Utilities, Travel & Bookings",
                    }
                    for item in data.get("facts", []):
                        sec_name = sec_map.get(str(item.get("section")), "1. Identity & Government Records")
                        if item.get("key") and item.get("value"):
                            facts.append((sec_name, item["key"], item["value"]))
            except Exception:
                pass

        if not facts:
            return 0

        # Apply updates to user_memory.md
        current_md = await self.load_user_memory(uid_str)
        updated_md = current_md
        now_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        for section, key, val in facts:
            new_bullet = f"- **{key}:** `{val}` *(Verified from {filename} on {now_date})*"
            key_pattern = rf"- \*\*{re.escape(key)}:\*\* .*"
            if re.search(key_pattern, updated_md):
                updated_md = re.sub(key_pattern, new_bullet, updated_md)
            else:
                sec_header = f"## {section}"
                if sec_header in updated_md:
                    parts = updated_md.split(sec_header, 1)
                    updated_md = f"{parts[0]}{sec_header}\n{new_bullet}{parts[1]}"
                else:
                    updated_md += f"\n\n## {section}\n{new_bullet}"

        updated_md = re.sub(r"\*\(No [^\)]+\)\*\n?", "", updated_md)
        await self.save_user_memory(uid_str, updated_md)
        logger.info(
            "Profile Intelligence Agent populated %d facts from '%s' into user_memory.md for user=%s",
            len(facts),
            filename,
            uid_str,
        )
        return len(facts)


memory_service = UserMemoryService()
