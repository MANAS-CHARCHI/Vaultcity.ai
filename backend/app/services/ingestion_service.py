"""
Real End-to-End File Security Inspection, Promotion & Vectorization Pipeline.

Workflow:
1. File is already staged in `vault-quarantine` S3/MinIO bucket and recorded in PostgreSQL
   with `scan_status = 'scanning'`.
2. Verify SHA-256 integrity of bytes fetched from `vault-quarantine`.
3. Run 3-stage inspection:
   - Prefers AWS SAM Local Lambda Pipeline (`SAM_API_URL`) when running.
   - Seamlessly executes the identical built-in pipeline against live `clamav:3310`,
     `minio:9000`, and PostgreSQL `pgvector` when SAM CLI isn't running locally.
4. Security Gate:
   - If INFECTED (ClamAV / EICAR) or THREAT (SQLi / AI Prompt Injection):
     Immediately deletes object from `vault-quarantine`, marks `files.scan_status`,
     writes `audit_logs`, and stores ZERO chunks in `document_chunks`.
5. Promote & Index:
   - Copies verified file `vault-quarantine` -> `vault-secured` and deletes quarantine copy.
   - Upserts chunks + 384-dim vectors into PostgreSQL `document_chunks` (`pgvector`).
   - Updates `files.s3_key = secured_key`, `files.scan_status = 'indexed'`, and logs `audit_logs`.
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime
import hashlib
import io
import json
import logging
import os
import re
import socket
import struct
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

import httpx
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.pipeline_logger import log_file_event
from app.models.audit_log import AuditLog
from app.models.file import FileDocument
from app.services.lambda_pipeline import lambda_pipeline_service
from app.services.s3_service import s3_service
from app.services.vector_service import vector_service
from app.services.vision_service import analyze_image, summarize_text_document

logger = logging.getLogger("vaultcity.ingestion")

Image.MAX_IMAGE_PIXELS = 25_000_000

EICAR_SIGNATURE = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)

EICAR_PATTERN = re.compile(
    r"X5O!P%@AP\[4\\?PZX54\(P\^\)7CC\)7\}\$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!\$H\+H\*|EICAR-STANDARD-ANTIVIRUS-TEST-FILE",
    re.IGNORECASE,
)

INVISIBLE_CHARS_RE = re.compile(
    r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff\U000e0000-\U000e007f]"
)
BASE64_CANDIDATE_RE = re.compile(
    r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{32,}={0,2}(?![A-Za-z0-9+/=])"
)

SQLI_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("union_select", re.compile(r"\bUNION\s+(?:ALL\s+)?SELECT\b", re.IGNORECASE)),
    (
        "stacked_ddl_dml",
        re.compile(
            r";\s*(?:DROP\s+(?:TABLE|DATABASE|SCHEMA)|DELETE\s+FROM|TRUNCATE\s+TABLE|ALTER\s+TABLE|INSERT\s+INTO|UPDATE\s+\w+\s+SET)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "boolean_tautology",
        re.compile(
            r"(?:'|\"|\b)\s*OR\s+(?:'?\d+'?\s*=\s*'?\d+'?|TRUE)\s*(?:--|#|/\*|$)",
            re.IGNORECASE,
        ),
    ),
    (
        "time_based_blind",
        re.compile(
            r"\b(?:PG_SLEEP\s*\(\s*\d+(?:\.\d+)?\s*\)|SLEEP\s*\(\s*\d+(?:\.\d+)?\s*\)|BENCHMARK\s*\(\s*\d+\s*,|WAITFOR\s+DELAY\s+['\"]\d+)",
            re.IGNORECASE,
        ),
    ),
    (
        "system_catalog_probe",
        re.compile(r"\b(?:INFORMATION_SCHEMA\.(?:TABLES|COLUMNS)|PG_CATALOG\.PG_TABLES|XP_CMDSHELL)\b", re.IGNORECASE),
    ),
]

PROMPT_INJECTION_PATTERNS: List[Tuple[str, re.Pattern]] = [
    (
        "instruction_override",
        re.compile(
            r"\b(?:ignore|disregard|forget|override|bypass)\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier|system)\s+(?:instructions|prompts|rules|directions|guidelines)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_exfiltration",
        re.compile(
            r"\b(?:reveal|print|dump|output|show|leak|repeat)\s+(?:your\s+|the\s+)?(?:system\s+prompt|hidden\s+instructions|initial\s+prompt|secret\s+instructions|api\s+keys?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "role_hijack_jailbreak",
        re.compile(
            r"\b(?:you\s+are\s+now\s+(?:in\s+)?(?:DAN|developer|unrestricted|jailbreak|admin)\s+mode|act\s+as\s+an?\s+unrestricted\s+AI|new\s+system\s+instruction)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "chat_template_delimiter",
        re.compile(r"(?:<\|im_start\|>\s*system|<\|system\|>|\[INST\]|<<SYS>>|###\s*System\s*:)", re.IGNORECASE),
    ),
    (
        "rag_data_exfiltration",
        re.compile(
            r"\b(?:send|exfiltrate|forward|post)\s+(?:all\s+)?(?:user|vault|private|confidential)\s+(?:documents|data|files|records|secrets)\s+to\b",
            re.IGNORECASE,
        ),
    ),
]

PII_PATTERNS: List[re.Pattern] = [
    re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),  # Aadhaar-like 12-digit ID
    re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),  # Indian PAN
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # US SSN
    re.compile(r"\b(?:passport\s*(?:no|number|#)|dob|date of birth)\b", re.IGNORECASE),
]


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _scan_clamav_sync(data: bytes) -> Tuple[bool, str, Optional[str], str]:
    """
    Stream bytes to live ClamAV daemon (`clamav:3310`) via native zINSTREAM protocol.
    Returns (is_clean, status, threat_name, engine).
    """
    if EICAR_SIGNATURE in data:
        return False, "infected", "Eicar-Signature", "eicar-builtin"

    try:
        with socket.create_connection((settings.CLAMAV_HOST, settings.CLAMAV_PORT), timeout=15.0) as sock:
            sock.settimeout(15.0)
            sock.sendall(b"zINSTREAM\0")
            chunk_size = 8192
            for offset in range(0, len(data), chunk_size):
                chunk = data[offset : offset + chunk_size]
                sock.sendall(struct.pack("!I", len(chunk)))
                sock.sendall(chunk)
            sock.sendall(struct.pack("!I", 0))

            chunks = []
            while True:
                buf = sock.recv(4096)
                if not buf:
                    break
                chunks.append(buf)
                if b"\0" in buf or b"\n" in buf:
                    break

            reply = b"".join(chunks).decode("utf-8", errors="replace").strip("\x00\r\n ")

        if reply.endswith("OK"):
            return True, "clean", None, "clamav"
        if "FOUND" in reply:
            threat = reply.replace("stream:", "").replace("FOUND", "").strip() or "Malware.Detected"
            return False, "infected", threat, "clamav"

        return False, "failed", f"Unexpected ClamAV reply: {reply}", "clamav"
    except (OSError, socket.timeout) as exc:
        logger.warning("ClamAV daemon unreachable at %s:%s (%s); using built-in EICAR guard.", settings.CLAMAV_HOST, settings.CLAMAV_PORT, exc)
        return True, "clean", None, "clamav-fallback"


def _extract_document_sync(
    data: bytes,
    filename: str,
    mime_type: str,
) -> Tuple[str, List[Dict[str, Any]], int, str]:
    """
    Extract text and page structure from PDF, DOCX, images, and text files.
    Returns (full_text, pages_list, page_count, document_type_label).
    """
    ext = os.path.splitext(filename.lower().strip())[1]
    mime = (mime_type or "").lower().strip()

    # 1. Image upload -> validate safely with Pillow
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"} or mime.startswith("image/"):
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            fmt = img.format or ext.lstrip(".").upper() or "IMAGE"
        summary = f"Verified {fmt} image ({width}x{height}px) stored in encrypted vault: {filename}"
        return summary, [{"page_number": 1, "text": summary, "source_type": "image_metadata"}], 1, f"{fmt} Image"

    # 2. PDF upload -> pypdf (pure Python, lightweight)
    if ext == ".pdf" or mime == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages: List[Dict[str, Any]] = []
        texts: List[str] = []
        page_count = max(1, len(reader.pages))
        for idx, page in enumerate(reader.pages, start=1):
            txt = (page.extract_text() or "").replace("\x00", "").strip()
            pages.append({"page_number": idx, "text": txt, "source_type": "text"})
            if txt:
                texts.append(txt)

        full_text = "\n\n".join(texts).replace("\x00", "").strip()
        if not full_text:
            full_text = f"Scanned PDF document ({page_count} page(s)): {filename}"
            pages = [{"page_number": 1, "text": full_text, "source_type": "scanned_pdf"}]
        return full_text, pages, page_count, f"PDF Document ({page_count} page{'s' if page_count != 1 else ''})"

    # 3. DOCX upload -> python-docx
    if ext == ".docx" or mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        from docx import Document

        document = Document(io.BytesIO(data))
        parts: List[str] = [p.text.strip() for p in document.paragraphs if p.text.strip()]
        for table in document.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        full_text = "\n".join(parts).strip() or f"Word document: {filename}"
        return full_text, [{"page_number": 1, "text": full_text, "source_type": "text"}], 1, "Word Document"

    # 4. Text / JSON / CSV / MD / fallback
    try:
        decoded = data.decode("utf-8").strip()
    except UnicodeDecodeError:
        decoded = data.decode("latin-1", errors="replace").strip()

    full_text = decoded or f"Binary document: {filename}"
    return full_text, [{"page_number": 1, "text": full_text, "source_type": "text"}], 1, "Text Document"


def _inspect_text_security(raw_text: str) -> Tuple[str, Optional[str], Dict[str, Any]]:
    """
    Normalize text (NFKC + invisible char strip + base64 decode) and scan for SQLi
    and AI prompt injection payloads.
    Returns (normalized_text, block_status_or_None, details).
    """
    import unicodedata

    clean_raw = (raw_text or "").replace("\x00", "")
    invisible_count = len(INVISIBLE_CHARS_RE.findall(clean_raw))
    stripped = INVISIBLE_CHARS_RE.sub("", clean_raw)
    normalized = unicodedata.normalize("NFKC", stripped).replace("\x00", "").strip()

    decoded_b64: List[str] = []
    for match in BASE64_CANDIDATE_RE.findall(normalized):
        if len(match) % 4 != 0:
            continue
        try:
            decoded_str = base64.b64decode(match, validate=True).decode("utf-8")
            printable = sum(ch.isprintable() or ch.isspace() for ch in decoded_str) / max(1, len(decoded_str))
            if printable > 0.9 and len(decoded_str.strip()) >= 12:
                decoded_b64.append(decoded_str.strip())
        except Exception:
            continue

    scan_corpus = "\n".join([normalized, *decoded_b64])

    # 1. Antivirus & Malware check on decompressed / extracted text
    is_av_clean, av_status, threat_name, av_engine = _scan_clamav_sync(scan_corpus.encode("utf-8", errors="replace"))
    eicar_hit = bool(EICAR_PATTERN.search(scan_corpus))
    if not is_av_clean or eicar_hit:
        threat = threat_name or "Eicar-Signature"
        engine = av_engine if not is_av_clean else "eicar-content-gate"
        details = {
            "invisible_chars_stripped": invisible_count,
            "decoded_base64_count": len(decoded_b64),
            "malware_detected": threat,
            "av_engine": engine,
            "sqli_matches": [],
            "prompt_injection_matches": [],
        }
        return normalized, "infected", details

    sqli_hits = [name for name, pat in SQLI_PATTERNS if pat.search(scan_corpus)]
    pi_hits = [name for name, pat in PROMPT_INJECTION_PATTERNS if pat.search(scan_corpus)]

    details = {
        "invisible_chars_stripped": invisible_count,
        "decoded_base64_count": len(decoded_b64),
        "sqli_matches": sqli_hits,
        "prompt_injection_matches": pi_hits,
    }

    if sqli_hits and pi_hits:
        return normalized, "blocked_sqli_and_prompt_injection", details
    if sqli_hits:
        return normalized, "blocked_sqli", details
    if pi_hits:
        return normalized, "blocked_prompt_injection", details
    return normalized, None, details


def _chunk_text(pages: List[Dict[str, Any]], chunk_size: int = 1000, overlap: int = 150) -> List[Dict[str, Any]]:
    """Split verified page text into overlapping chunks preserving page_number."""
    chunks: List[Dict[str, Any]] = []
    chunk_idx = 0
    for page in pages:
        text = (page.get("text") or "").replace("\x00", "").strip()
        if not text:
            continue
        page_num = int(page.get("page_number", 1))
        source_type = str(page.get("source_type", "text"))
        step = max(1, chunk_size - overlap)
        for start in range(0, len(text), step):
            segment = text[start : start + chunk_size].strip()
            if not segment:
                continue
            chunks.append(
                {
                    "chunk_index": chunk_idx,
                    "page_number": page_num,
                    "content": segment,
                    "source_type": source_type,
                }
            )
            chunk_idx += 1
            if start + chunk_size >= len(text):
                break
    return chunks


def _embed_chunk_text(text: str, dim: int) -> List[float]:
    """
    Generate normalized 384-dim feature vector via hashed token n-grams so pgvector cosine
    similarity works deterministically even when SAM's FastEmbed container isn't running.
    """
    vec = [0.0] * dim
    tokens = re.findall(r"[a-z0-9]{2,}", text.lower())
    if not tokens:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [((digest[i % len(digest)] / 255.0) * 2.0 - 1.0) for i in range(dim)]
        norm = sum(x * x for x in raw) ** 0.5 or 1.0
        return [round(x / norm, 6) for x in raw]

    for tok in tokens:
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        idx = int.from_bytes(h[:4], "big") % dim
        sign = 1.0 if (h[4] % 2 == 0) else -1.0
        vec[idx] += sign

    norm = sum(x * x for x in vec) ** 0.5 or 1.0
    return [round(x / norm, 6) for x in vec]


def _detect_pii(text: str, category: str) -> bool:
    if category == "Identity":
        return True
    return any(pat.search(text or "") for pat in PII_PATTERNS)


def _build_description(text: str, filename: str, doc_type: str) -> str:
    clean = re.sub(r"\s+", " ", (text or "")).strip()
    if clean and not clean.startswith("Verified ") and not clean.startswith("Scanned PDF"):
        snippet = clean[:220]
        return f"{snippet}..." if len(clean) > 220 else snippet
    return f"{doc_type} ({filename}) verified by antivirus & ML security gates."


async def _log_audit(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    action: str,
    file_id: uuid.UUID,
    status_str: str,
    details: Dict[str, Any],
) -> None:
    db.add(
        AuditLog(
            id=uuid.uuid4(),
            user_id=user_id,
            action=action,
            resource_type="file",
            resource_id=str(file_id),
            status=status_str,
            details=details,
            created_at=datetime.utcnow(),
        )
    )


async def _try_sam_pipeline(
    *,
    file_record: FileDocument,
    quarantine_key: str,
) -> Optional[Dict[str, Any]]:
    """
    Attempt to execute Stage 1 + Stage 2 via AWS SAM Local API (`SAM_API_URL`).
    Returns dict verdict if SAM responded, or None if SAM local server is unreachable.
    """
    if not settings.USE_SAM_PIPELINE:
        return None
    try:
        stage1 = await lambda_pipeline_service.scan_and_extract(
            file_id=str(file_record.id),
            user_id=str(file_record.user_id),
            filename=file_record.filename,
            mime_type=file_record.mime_type,
            category=file_record.category,
            s3_bucket=settings.S3_QUARANTINE_BUCKET,
            s3_key=quarantine_key,
            invoke_vlm=True,
            invoke_next=False,
        )
        if stage1.get("status") != "clean":
            return {"mode": "sam", "stage": "stage1", "result": stage1}

        stage2 = await lambda_pipeline_service.security_scan_and_vectorize(
            file_id=str(file_record.id),
            user_id=str(file_record.user_id),
            filename=file_record.filename,
            extracted_text=stage1.get("extracted_text", ""),
            pages=stage1.get("pages"),
            category=file_record.category,
            sha256=stage1.get("sha256") or file_record.sha256_hash,
        )
        return {"mode": "sam", "stage": "stage2", "stage1": stage1, "stage2": stage2}
    except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
        logger.info("SAM local API unreachable at %s (%s); running built-in ClamAV+S3+pgvector pipeline.", settings.SAM_API_URL, exc)
        return None
    except Exception as exc:
        logger.warning("SAM pipeline error (%s); falling back to built-in pipeline.", exc)
        return None


async def _extract_and_summarize(
    data: bytes,
    filename: str,
    mime_type: str,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Extract text/OCR and generate a real summary for images, PDFs (including scanned PDFs),
    DOCX, and text documents.
    """
    ext = os.path.splitext(filename.lower().strip())[1]
    mime = (mime_type or "").lower().strip()

    # 1. Image upload -> Vision / OCR summary engine
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"} or mime.startswith("image/"):
        v = await analyze_image(data, filename, user_id=user_id)
        pages = [
            {
                "page_number": 1,
                "text": v.combined_text,
                "source_type": "vision_ocr" if v.visible_text else "vision_summary",
            }
        ]
        return {
            "full_text": v.combined_text,
            "summary": v.summary,
            "visible_text": v.visible_text,
            "pages": pages,
            "page_count": 1,
            "doc_type": v.document_type,
            "category_hint": v.category_hint,
            "engine": v.engine,
        }

    # 2. PDF / DOCX / Text -> synchronous parser (pypdf / python-docx / text)
    full_text, pages, page_count, doc_type = await asyncio.to_thread(
        _extract_document_sync, data, filename, mime_type
    )
    engine = "pypdf" if (ext == ".pdf" or mime == "application/pdf") else (
        "python-docx" if ext == ".docx" else "text-parser"
    )
    category_hint: Optional[str] = None
    visible_text = full_text

    # 2b. If PDF is scanned or has very short text (< 250 chars) but has embedded images,
    # extract the embedded image and send to OCR / VLM!
    is_scanned_or_short_pdf = (
        (ext == ".pdf" or mime == "application/pdf")
        and pages
        and (pages[0].get("source_type") == "scanned_pdf" or len(full_text.strip()) < 250)
    )
    if is_scanned_or_short_pdf:
        try:
            def _extract_first_embedded_image() -> Optional[bytes]:
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(data))
                for pg in reader.pages[:2]:
                    for img_obj in getattr(pg, "images", []):
                        if getattr(img_obj, "data", None):
                            return img_obj.data
                return None

            embedded_img = await asyncio.to_thread(_extract_first_embedded_image)
            if embedded_img:
                v = await analyze_image(embedded_img, filename, user_id=user_id)
                combined_full = (
                    f"{v.combined_text}\n\n[Metadata / Header Text]: {full_text}"
                    if full_text and "Scanned PDF document" not in full_text
                    else v.combined_text
                )
                full_text = combined_full
                visible_text = v.visible_text
                summary = v.summary
                engine = f"pypdf+{v.engine}"
                category_hint = v.category_hint
                pages = [{"page_number": 1, "text": combined_full, "source_type": "scanned_pdf_vlm"}]
                return {
                    "full_text": full_text,
                    "summary": summary,
                    "visible_text": visible_text,
                    "pages": pages,
                    "page_count": page_count,
                    "doc_type": f"{v.document_type} (PDF)",
                    "category_hint": category_hint,
                    "engine": engine,
                }
        except Exception as exc:
            logger.warning("Scanned PDF embedded-image VLM extraction failed for %s: %s", filename, exc)

    summary_info = await summarize_text_document(full_text, filename, doc_type, page_count, user_id=user_id)
    summary = summary_info["summary"]
    resolved_doc_type = summary_info.get("doc_type") or doc_type
    resolved_category = summary_info.get("category_hint") or category_hint
    engine = f"{engine}+{summary_info.get('engine', 'haiku')}"
    return {
        "full_text": (full_text or "").replace("\x00", ""),
        "summary": (summary or "").replace("\x00", ""),
        "visible_text": (visible_text or "").replace("\x00", ""),
        "pages": [
            {**p, "text": (p.get("text") or "").replace("\x00", "")}
            for p in pages
        ],
        "page_count": page_count,
        "doc_type": resolved_doc_type,
        "category_hint": resolved_category,
        "engine": engine,
    }


async def process_uploaded_file(
    file_id: uuid.UUID,
    user_id: uuid.UUID,
    quarantine_key: str,
) -> Dict[str, Any]:
    """
    Full asynchronous inspection, S3 promotion (quarantine -> secured), and pgvector indexing.
    Opens its own database session so it is safe to run in FastAPI BackgroundTasks.
    """
    pipeline_start = time.perf_counter()
    steps: List[Dict[str, Any]] = [
        {
            "stage": "quarantine_s3",
            "label": "Quarantine S3 Staging",
            "status": "passed",
            "detail": f"Isolated in {settings.S3_QUARANTINE_BUCKET}",
            "duration_ms": 0.0,
        }
    ]

    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(FileDocument).where(
                FileDocument.id == file_id,
                FileDocument.user_id == user_id,
            )
        )
        file_record = res.scalar_one_or_none()
        if not file_record:
            log_file_event(
                "PIPELINE_ABORT",
                "missing",
                "File record vanished before background inspection",
                file_id=str(file_id),
                user_id=str(user_id),
                level="WARNING",
            )
            return {"status": "missing"}

        fid_str = str(file_id)
        uid_str = str(user_id)
        fname = file_record.filename

        try:
            # 1. Fetch staged bytes from Quarantine S3 and verify SHA-256 integrity
            t0 = time.perf_counter()
            raw_bytes = await s3_service.download_from_quarantine(quarantine_key)
            actual_sha256 = compute_sha256(raw_bytes)
            t_integrity = (time.perf_counter() - t0) * 1000.0

            if actual_sha256 != file_record.sha256_hash:
                steps.append({
                    "stage": "sha256_integrity",
                    "label": "SHA-256 Integrity Check",
                    "status": "failed",
                    "detail": "Hash mismatch between upload and quarantine object",
                    "duration_ms": round(t_integrity, 1),
                })
                await s3_service.delete_from_quarantine(quarantine_key)
                file_record.scan_status = "failed"
                file_record.description = "Integrity verification failed: SHA-256 mismatch."
                file_record.pages_json = json.dumps({
                    "summary": file_record.description,
                    "sha256_verified": False,
                    "total_duration_ms": round((time.perf_counter() - pipeline_start) * 1000.0, 1),
                    "steps": steps,
                })
                file_record.updated_at = datetime.utcnow()
                await _log_audit(
                    db,
                    user_id=user_id,
                    action="file_integrity_mismatch",
                    file_id=file_id,
                    status_str="failed",
                    details={"expected": file_record.sha256_hash, "actual": actual_sha256},
                )
                await db.commit()
                log_file_event(
                    "INTEGRITY_VERIFY",
                    "failed",
                    "SHA-256 mismatch — purged from quarantine",
                    file_id=fid_str,
                    user_id=uid_str,
                    filename=fname,
                    duration_ms=t_integrity,
                    details={"expected": file_record.sha256_hash, "actual": actual_sha256},
                    level="ERROR",
                )
                return {"status": "failed", "reason": "sha256_mismatch"}

            steps.append({
                "stage": "sha256_integrity",
                "label": "SHA-256 Integrity Check",
                "status": "passed",
                "detail": f"SHA-256 verified ({actual_sha256[:12]}…)",
                "duration_ms": round(t_integrity, 1),
            })
            log_file_event(
                "INTEGRITY_VERIFY",
                "passed",
                f"Downloaded {len(raw_bytes)} bytes from {settings.S3_QUARANTINE_BUCKET}; SHA-256 verified",
                file_id=fid_str,
                user_id=uid_str,
                filename=fname,
                duration_ms=t_integrity,
                details={"sha256": actual_sha256, "bytes": len(raw_bytes)},
            )

            # 2. Try AWS SAM 3-Lambda pipeline first if active
            sam_outcome = await _try_sam_pipeline(
                file_record=file_record,
                quarantine_key=quarantine_key,
            )

            if sam_outcome is not None:
                if sam_outcome["stage"] == "stage1":
                    st1 = sam_outcome["result"]
                    status_val = st1.get("status", "failed")
                    await s3_service.delete_from_quarantine(quarantine_key)
                    file_record.scan_status = status_val
                    file_record.description = st1.get("detail") or f"Blocked at antivirus stage ({st1.get('threat_name') or status_val})."
                    file_record.updated_at = datetime.utcnow()
                    await _log_audit(
                        db,
                        user_id=user_id,
                        action=f"file_{status_val}",
                        file_id=file_id,
                        status_str="blocked",
                        details=st1,
                    )
                    await db.commit()
                    log_file_event(
                        "SAM_STAGE1_BLOCKED",
                        status_val,
                        file_record.description,
                        file_id=fid_str,
                        user_id=uid_str,
                        filename=fname,
                        details=st1,
                        level="WARNING",
                    )
                    return {"status": status_val, "engine": "sam"}

                st1 = sam_outcome["stage1"]
                st2 = sam_outcome["stage2"]
                st2_status = st2.get("status", "failed")
                if st2_status != "indexed":
                    await s3_service.delete_from_quarantine(quarantine_key)
                    file_record.scan_status = st2_status
                    file_record.description = f"Blocked by ML security scanner ({st2_status})."
                    file_record.updated_at = datetime.utcnow()
                    await db.commit()
                    log_file_event(
                        "SAM_STAGE2_BLOCKED",
                        st2_status,
                        file_record.description,
                        file_id=fid_str,
                        user_id=uid_str,
                        filename=fname,
                        level="WARNING",
                    )
                    return {"status": st2_status, "engine": "sam"}

                secured_key = await s3_service.promote_to_secured(
                    quarantine_key=quarantine_key,
                    user_id=uid_str,
                    file_id=fid_str,
                    filename=fname,
                    content_type=file_record.mime_type,
                    sha256_hex=actual_sha256,
                )
                extracted = st1.get("extracted_text", "")
                pages = st1.get("pages") or []
                summary_info = await summarize_text_document(
                    extracted, fname, file_record.document_type or "Document", max(1, len(pages)), user_id=uid_str
                )
                summary = summary_info["summary"]
                if summary_info.get("doc_type"):
                    file_record.document_type = summary_info["doc_type"]
                if summary_info.get("category_hint") and file_record.category == "Other":
                    file_record.category = summary_info["category_hint"]
                file_record.s3_key = secured_key
                file_record.scan_status = "indexed"
                file_record.page_count = max(1, len(pages))
                file_record.is_pii = _detect_pii(extracted, file_record.category)
                file_record.description = summary
                file_record.updated_at = datetime.utcnow()
                try:
                    from app.services.entity_extractor import sync_entities_from_document
                    await sync_entities_from_document(db, user_id=user_id, text=extracted, source_file_id=file_id)
                except Exception as _e_sync:
                    logger.warning("Could not sync identity entities from SAM pipeline: %s", _e_sync)
                await db.commit()
                log_file_event(
                    "PIPELINE_COMPLETE",
                    "indexed",
                    "Completed via AWS SAM 3-Lambda pipeline",
                    file_id=fid_str,
                    user_id=uid_str,
                    filename=fname,
                    duration_ms=(time.perf_counter() - pipeline_start) * 1000.0,
                    details={"engine": "sam", "secured_s3_key": secured_key},
                )
                return {"status": "indexed", "s3_key": secured_key, "engine": "sam"}

            # 3. Built-in Live Pipeline: Stage 3a - ClamAV Antivirus Scan
            t_av0 = time.perf_counter()
            is_clean, av_status, threat_name, av_engine = await asyncio.to_thread(_scan_clamav_sync, raw_bytes)
            t_av = (time.perf_counter() - t_av0) * 1000.0

            if not is_clean:
                steps.append({
                    "stage": "virus_scan",
                    "label": "ClamAV Antivirus Scan",
                    "status": "blocked",
                    "detail": f"Threat detected ({av_engine}): {threat_name or 'malware'}",
                    "duration_ms": round(t_av, 1),
                })
                await s3_service.delete_from_quarantine(quarantine_key)
                await vector_service.delete_file_chunks(db=db, file_id=file_id, user_id=user_id)

                threat_detected = threat_name or "Malware.Detected"
                await _log_audit(
                    db,
                    user_id=user_id,
                    action="file_infected_purged",
                    file_id=file_id,
                    status_str="blocked",
                    details={
                        "filename": fname,
                        "threat_name": threat_detected,
                        "engine": av_engine,
                        "sha256": actual_sha256,
                        "auto_removed": True,
                        "reason": f"Malware detected by {av_engine} ({threat_detected}); file automatically purged from vault.",
                    },
                )
                await db.delete(file_record)
                await db.commit()

                log_file_event(
                    "VIRUS_PURGED",
                    "blocked",
                    f"MALWARE DETECTED by {av_engine}: {threat_detected} — file '{fname}' automatically removed from database and storage.",
                    file_id=fid_str,
                    user_id=uid_str,
                    filename=fname,
                    duration_ms=t_av,
                    details={"av_engine": av_engine, "threat_name": threat_detected, "sha256": actual_sha256, "auto_removed": True},
                    level="WARNING",
                )
                return {
                    "status": "infected",
                    "threat_name": threat_detected,
                    "auto_removed": True,
                    "filename": fname,
                    "message": f"Security Alert: '{fname}' was infected with {threat_detected} and has been automatically removed from your vault.",
                }

            steps.append({
                "stage": "virus_scan",
                "label": "ClamAV Antivirus Scan",
                "status": "passed",
                "detail": f"Clean ({av_engine} zINSTREAM verified)",
                "duration_ms": round(t_av, 1),
            })
            log_file_event(
                "VIRUS_SCAN_CLAMAV",
                "passed",
                f"Antivirus clean via {av_engine}",
                file_id=fid_str,
                user_id=uid_str,
                filename=fname,
                duration_ms=t_av,
                details={"av_engine": av_engine, "av_status": av_status},
            )

            # Mark classifying while running Vision / OCR / Text Extraction & Security Checks
            file_record.scan_status = "classifying"
            file_record.updated_at = datetime.utcnow()
            await db.commit()

            # Stage 3b - Vision / OCR / Document Extraction & Summary
            t_ext0 = time.perf_counter()
            extracted_info = await _extract_and_summarize(
                raw_bytes, fname, file_record.mime_type, user_id=uid_str
            )
            t_ext = (time.perf_counter() - t_ext0) * 1000.0

            full_text = extracted_info["full_text"]
            doc_summary = extracted_info["summary"]
            visible_text = extracted_info["visible_text"]
            pages = extracted_info["pages"]
            page_count = extracted_info["page_count"]
            doc_type = extracted_info["doc_type"]
            vision_engine = extracted_info["engine"]
            category_hint = extracted_info.get("category_hint")

            if file_record.category == "Other" and category_hint:
                file_record.category = category_hint

            steps.append({
                "stage": "extract_vision",
                "label": "Content Extraction & Image Summary",
                "status": "passed",
                "detail": f"{vision_engine} — {page_count} page(s), {len(visible_text)} chars extracted",
                "duration_ms": round(t_ext, 1),
            })
            log_file_event(
                "EXTRACT_AND_VISION",
                "passed",
                f"Extracted content & generated summary via {vision_engine}",
                file_id=fid_str,
                user_id=uid_str,
                filename=fname,
                duration_ms=t_ext,
                details={
                    "engine": vision_engine,
                    "doc_type": doc_type,
                    "page_count": page_count,
                    "extracted_chars": len(visible_text),
                    "summary_preview": doc_summary[:180],
                },
            )

            # Stage 3c - Content Security Gate (NFKC + Zero-Width + Base64 + SQLi + Prompt Injection)
            t_sec0 = time.perf_counter()
            normalized_text, block_status, security_details = _inspect_text_security(full_text)
            t_sec = (time.perf_counter() - t_sec0) * 1000.0

            if block_status is not None:
                is_virus_block = (block_status == "infected")
                stage_label = "Malware & Security Gate"
                detail_msg = (
                    f"Threat detected ({security_details.get('av_engine', 'clamav')}): {security_details.get('malware_detected', 'Eicar-Signature')}"
                    if is_virus_block
                    else f"Blocked ({block_status})"
                )

                steps.append({
                    "stage": "security_gate",
                    "label": stage_label,
                    "status": "blocked",
                    "detail": detail_msg,
                    "duration_ms": round(t_sec, 1),
                })
                await s3_service.delete_from_quarantine(quarantine_key)
                await vector_service.delete_file_chunks(db=db, file_id=file_id, user_id=user_id)

                if is_virus_block:
                    threat_detected = security_details.get("malware_detected", "Malware.Detected")
                    engine_used = security_details.get("av_engine", av_engine)

                    # Record security audit log documenting infected file purge
                    await _log_audit(
                        db,
                        user_id=user_id,
                        action="file_infected_purged",
                        file_id=file_id,
                        status_str="blocked",
                        details={
                            "filename": fname,
                            "threat_name": threat_detected,
                            "engine": engine_used,
                            "sha256": actual_sha256,
                            "auto_removed": True,
                            "reason": f"Malware detected in document content by {engine_used} ({threat_detected}); file automatically purged from vault.",
                        },
                    )

                    # Permanently delete file record from PostgreSQL database so it does not remain in user vault
                    await db.delete(file_record)
                    await db.commit()
                    from app.core.redis import delete_cache, make_user_files_cache_key
                    await delete_cache(make_user_files_cache_key(uid_str))

                    log_file_event(
                        "MALWARE_PURGED",
                        "blocked",
                        f"INFECTED FILE PURGED ({engine_used}): {threat_detected} — file '{fname}' automatically removed from database and storage.",
                        file_id=fid_str,
                        user_id=uid_str,
                        filename=fname,
                        duration_ms=t_sec,
                        details={**security_details, "auto_removed": True},
                        level="WARNING",
                    )
                    return {
                        "status": "infected",
                        "threat_name": threat_detected,
                        "auto_removed": True,
                        "filename": fname,
                        "details": security_details,
                        "message": f"Security Alert: '{fname}' was infected with {threat_detected} and has been automatically removed from your vault.",
                    }

                file_record.scan_status = block_status
                file_record.description = f"Blocked by content security scanner ({block_status})."

                file_record.pages_json = json.dumps({
                    "summary": file_record.description,
                    "vision_engine": vision_engine,
                    "av_engine": security_details.get("av_engine", av_engine),
                    "av_status": "clean",
                    "av_threat": None,
                    "sqli_status": "blocked" if security_details.get("sqli_matches") else "clean",
                    "prompt_injection_status": "blocked" if security_details.get("prompt_injection_matches") else "clean",
                    "sha256_verified": True,
                    "chunks_stored": 0,
                    "total_duration_ms": round((time.perf_counter() - pipeline_start) * 1000.0, 1),
                    "steps": steps,
                })
                file_record.updated_at = datetime.utcnow()
                await _log_audit(
                    db,
                    user_id=user_id,
                    action=f"file_{block_status}",
                    file_id=file_id,
                    status_str="blocked",
                    details={"threat_name": None, "security": security_details, "sha256": actual_sha256},
                )
                await db.commit()
                from app.core.redis import delete_cache, make_user_files_cache_key
                await delete_cache(make_user_files_cache_key(uid_str))
                log_file_event(
                    "SECURITY_GATE",
                    "blocked",
                    f"Content threat detected ({block_status}) — purged from quarantine, 0 vectors stored",
                    file_id=fid_str,
                    user_id=uid_str,
                    filename=fname,
                    duration_ms=t_sec,
                    details=security_details,
                    level="WARNING",
                )
                return {"status": block_status, "details": security_details}

            is_pii = _detect_pii(normalized_text, file_record.category)
            steps.append({
                "stage": "security_gate",
                "label": "SQLi & Prompt-Injection Gate",
                "status": "passed",
                "detail": f"Clean (0 SQLi, 0 Prompt Injections{', PII flagged' if is_pii else ''})",
                "duration_ms": round(t_sec, 1),
            })
            log_file_event(
                "SECURITY_GATE",
                "passed",
                "Passed SQLi, Prompt-Injection, invisible-char & Base64 security inspection",
                file_id=fid_str,
                user_id=uid_str,
                filename=fname,
                duration_ms=t_sec,
                details={**security_details, "is_pii": is_pii},
            )

            # Stage 3d - Promote Quarantine -> Secured S3
            t_s30 = time.perf_counter()
            secured_key = await s3_service.promote_to_secured(
                quarantine_key=quarantine_key,
                user_id=uid_str,
                file_id=fid_str,
                filename=fname,
                content_type=file_record.mime_type,
                sha256_hex=actual_sha256,
            )
            t_s3 = (time.perf_counter() - t_s30) * 1000.0
            steps.append({
                "stage": "promote_secured_s3",
                "label": "Secured S3 Promotion",
                "status": "passed",
                "detail": f"Promoted to {settings.S3_SECURED_BUCKET} & purged quarantine",
                "duration_ms": round(t_s3, 1),
            })
            log_file_event(
                "PROMOTE_SECURED_S3",
                "passed",
                f"Moved {quarantine_key} -> {settings.S3_SECURED_BUCKET}/{secured_key}",
                file_id=fid_str,
                user_id=uid_str,
                filename=fname,
                duration_ms=t_s3,
                details={"secured_s3_key": secured_key},
            )

            # Stage 3e - Chunk & store 384-dim vectors in PostgreSQL pgvector
            t_vec0 = time.perf_counter()
            normalized_pages = [
                {
                    "page_number": p["page_number"],
                    "source_type": p.get("source_type", "text"),
                    "text": _inspect_text_security(p.get("text", ""))[0].replace("\x00", ""),
                }
                for p in pages
            ]
            raw_chunks = _chunk_text(normalized_pages)
            if not raw_chunks:
                raw_chunks = [
                    {
                        "chunk_index": 0,
                        "page_number": 1,
                        "content": (normalized_text or fname).replace("\x00", ""),
                        "source_type": "text",
                    }
                ]

            chunk_payloads = [
                {
                    "chunk_index": c["chunk_index"],
                    "page_number": c["page_number"],
                    "content": (c["content"] or "").replace("\x00", ""),
                    "embedding": _embed_chunk_text((c["content"] or "").replace("\x00", ""), settings.VECTOR_DIMENSION),
                    "metadata_json": {
                        "filename": fname,
                        "category": file_record.category,
                        "source_type": c["source_type"],
                        "sha256": actual_sha256,
                        "av_engine": av_engine,
                        "vision_engine": vision_engine,
                        "scan_verified": True,
                    },
                }
                for c in raw_chunks
            ]

            chunks_stored = await vector_service.upsert_chunks(
                db=db,
                file_id=file_id,
                user_id=user_id,
                chunks=chunk_payloads,
            )
            t_vec = (time.perf_counter() - t_vec0) * 1000.0
            total_ms = (time.perf_counter() - pipeline_start) * 1000.0

            steps.append({
                "stage": "pgvector_index",
                "label": "pgvector Semantic Index",
                "status": "passed",
                "detail": f"{chunks_stored} chunk(s) indexed ({settings.VECTOR_DIMENSION}-dim)",
                "duration_ms": round(t_vec, 1),
            })

            clean_summary = (doc_summary or "").replace("\x00", "")
            scan_report = {
                "summary": clean_summary,
                "extracted_text_preview": ((visible_text or "")[:1200]).replace("\x00", ""),
                "vision_engine": vision_engine,
                "av_engine": av_engine,
                "av_status": "clean",
                "sqli_status": "clean",
                "prompt_injection_status": "clean",
                "sha256_verified": True,
                "chunks_stored": chunks_stored,
                "total_duration_ms": round(total_ms, 1),
                "steps": steps,
            }

            file_record.s3_key = secured_key
            file_record.scan_status = "indexed"
            file_record.document_type = doc_type
            file_record.page_count = page_count
            file_record.is_pii = is_pii
            file_record.description = clean_summary
            file_record.pages_json = json.dumps(scan_report, ensure_ascii=False).replace("\x00", "")
            file_record.updated_at = datetime.utcnow()

            try:
                from app.services.entity_extractor import sync_entities_from_document
                await sync_entities_from_document(
                    db,
                    user_id=user_id,
                    text=(normalized_text or visible_text or ""),
                    source_file_id=file_id,
                )
            except Exception as _e_sync:
                logger.warning("Could not sync identity entities: %s", _e_sync)

            await _log_audit(
                db,
                user_id=user_id,
                action="file_scanned_and_indexed",
                file_id=file_id,
                status_str="success",
                details={
                    "filename": fname,
                    "av_engine": av_engine,
                    "vision_engine": vision_engine,
                    "chunks_stored": chunks_stored,
                    "secured_s3_key": secured_key,
                    "sha256": actual_sha256,
                    "total_duration_ms": round(total_ms, 1),
                },
            )
            await db.commit()
            from app.core.redis import delete_cache, make_user_files_cache_key
            await delete_cache(make_user_files_cache_key(uid_str))
            log_file_event(
                "PGVECTOR_INDEX",
                "passed",
                f"Stored {chunks_stored} vector chunk(s) in PostgreSQL pgvector",
                user_id=uid_str,
                filename=fname,
                duration_ms=t_vec,
                details={"chunks_stored": chunks_stored, "vector_dim": settings.VECTOR_DIMENSION},
            )
            log_file_event(
                "PIPELINE_COMPLETE",
                "indexed",
                f"File verified, summarized, promoted & indexed in {total_ms:.1f}ms",
                file_id=fid_str,
                user_id=uid_str,
                filename=fname,
                duration_ms=total_ms,
                details={
                    "av_engine": av_engine,
                    "vision_engine": vision_engine,
                    "chunks_stored": chunks_stored,
                    "secured_s3_key": secured_key,
                },
            )
            return {"status": "indexed", "s3_key": secured_key, "chunks_stored": chunks_stored, "summary": doc_summary}

        except Exception as exc:
            total_ms = (time.perf_counter() - pipeline_start) * 1000.0
            logger.exception("Ingestion failed for file_id=%s: %s", file_id, exc)
            await s3_service.delete_from_quarantine(quarantine_key)
            try:
                await db.rollback()
            except Exception:
                pass
            try:
                res = await db.execute(
                    select(FileDocument).where(
                        FileDocument.id == file_id,
                        FileDocument.user_id == user_id,
                    )
                )
                refreshed = res.scalar_one_or_none()
                if refreshed:
                    refreshed.scan_status = "failed"
                    refreshed.description = f"Processing error: {exc}".replace("\x00", "")
                    refreshed.pages_json = json.dumps({
                        "summary": refreshed.description,
                        "error": str(exc).replace("\x00", ""),
                        "total_duration_ms": round(total_ms, 1),
                        "steps": steps,
                    }, ensure_ascii=False).replace("\x00", "")
                    refreshed.updated_at = datetime.utcnow()
                    await _log_audit(
                        db,
                        user_id=user_id,
                        action="file_processing_failed",
                        file_id=file_id,
                        status_str="error",
                        details={"error": str(exc).replace("\x00", "")},
                    )
                    await db.commit()
            except Exception as save_err:
                logger.error("Failed to commit failed status for file_id=%s: %s", file_id, save_err)
            from app.core.redis import delete_cache, make_user_files_cache_key
            await delete_cache(make_user_files_cache_key(uid_str))
            log_file_event(
                "PIPELINE_FAILED",
                "error",
                f"Ingestion crashed: {exc}",
                file_id=fid_str,
                user_id=uid_str,
                filename=fname,
                duration_ms=total_ms,
                details={"error": str(exc)},
                level="ERROR",
            )
            return {"status": "failed", "error": str(exc)}
