"""
OpenRouter Unified AI Client (Jev Decision Router & Qwen 2.5 Text/Reasoning).

Features:
1. Unified API Gateway: Calls Jev (typesafe/jev) for sub-second typed JSON decisions
   and Qwen 2.5 (72B & 7B) for high-fidelity RAG generation and fact distillation.
2. Graceful Offline / Fallback Mode: When OPENROUTER_API_KEY is not configured,
   smoothly falls back to deterministic local rule engines and heuristics with 0 crashes.
3. Detailed Token & Cost Tracking: Persists call metrics into token logs and metrics DB.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from app.core.config import settings

logger = logging.getLogger("vaultcity.openrouter")


def _get_headers() -> Dict[str, str]:
    api_key = (settings.OPENROUTER_API_KEY or "").strip()
    headers = {
        "Content-Type": "application/json",
        "HTTP-Referer": "https://vaultcity.ai",
        "X-Title": "Vault City AI",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


class OpenRouterClient:
    """Unified client for OpenRouter handling Jev decisions and Qwen 2.5 generation."""

    def __init__(self):
        self.base_url = (settings.OPENROUTER_BASE_URL or "https://openrouter.ai/api/v1").rstrip("/")

    @property
    def is_configured(self) -> bool:
        return bool(settings.OPENROUTER_API_KEY and settings.OPENROUTER_API_KEY.strip())

    async def call_chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        max_tokens: int = 1000,
        temperature: float = 0.2,
        user_id: Optional[str] = None,
        call_type: str = "chat",
        timeout_sec: float = 30.0,
        allow_free_tier: bool = True,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Invoke Qwen or other chat models via OpenRouter with cascading free-tier fallback.
        1. Tries OpenRouter Free Frontier models first ($0.00 cost) if allow_free_tier=True.
        2. Seamlessly cascades to paid Qwen 2.5 72B on rate limit (HTTP 429) or overload.
        3. Returns (reply_text, token_info_dict).
        """
        if not self.is_configured:
            fallback_model = model or settings.OPENROUTER_DEFAULT_MODEL or "qwen/qwen-2.5-72b-instruct"
            logger.info("OPENROUTER_API_KEY not set; using local fallback for %s.", call_type)
            return "", {"status": "no_api_key", "total_tokens": 0, "model": fallback_model}

        # Build candidate models cascade
        if model:
            candidate_models = [model]
        elif allow_free_tier and getattr(settings, "USE_FREE_MODELS_FIRST", True):
            free_models_raw = getattr(settings, "OPENROUTER_FREE_MODELS", "") or ""
            free_models = [m.strip() for m in free_models_raw.split(",") if m.strip()]
            paid_fallback = settings.OPENROUTER_DEFAULT_MODEL or "qwen/qwen-2.5-72b-instruct"
            candidate_models = free_models + [paid_fallback]
        else:
            candidate_models = [settings.OPENROUTER_DEFAULT_MODEL or "qwen/qwen-2.5-72b-instruct"]

        formatted_messages = []
        if system_prompt:
            formatted_messages.append({"role": "system", "content": system_prompt})
        formatted_messages.extend(messages)

        t0 = time.perf_counter()
        token_info: Dict[str, Any] = {
            "model": candidate_models[0],
            "call_type": call_type,
            "user_id": user_id,
            "status": "pending",
            "is_free_tier": False,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "duration_ms": 0.0,
        }

        try:
            async with httpx.AsyncClient(timeout=timeout_sec) as client:
                for cand_model in candidate_models:
                    payload = {
                        "model": cand_model,
                        "messages": formatted_messages,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    }
                    try:
                        resp = await client.post(
                            f"{self.base_url}/chat/completions",
                            headers=_get_headers(),
                            json=payload,
                        )
                        duration_ms = (time.perf_counter() - t0) * 1000.0
                        token_info["duration_ms"] = round(duration_ms, 1)

                        if resp.status_code == 200:
                            data = resp.json()
                            choices = data.get("choices", [])
                            reply_text = choices[0].get("message", {}).get("content", "").strip() if choices else ""
                            usage = data.get("usage", {})
                            token_info["model"] = cand_model
                            token_info["is_free_tier"] = (":free" in cand_model)
                            token_info["input_tokens"] = usage.get("prompt_tokens", 0)
                            token_info["output_tokens"] = usage.get("completion_tokens", 0)
                            token_info["total_tokens"] = usage.get("total_tokens", 0)
                            token_info["status"] = "success"

                            if ":free" in cand_model:
                                logger.info(
                                    "OpenRouter answered via Free Tier Model: %s ($0.00 cost, %d tokens).",
                                    cand_model,
                                    token_info["total_tokens"],
                                )
                            else:
                                logger.info(
                                    "OpenRouter answered via Paid Model: %s (%d tokens).",
                                    cand_model,
                                    token_info["total_tokens"],
                                )
                            return reply_text, token_info
                        elif resp.status_code in (429, 503, 504, 529):
                            logger.warning(
                                "OpenRouter model %s rate-limited/overloaded (HTTP %d); cascading to next model...",
                                cand_model,
                                resp.status_code,
                            )
                            token_info["status"] = f"http_{resp.status_code}_cascading"
                            continue
                        else:
                            logger.warning(
                                "OpenRouter model %s returned HTTP %d: %s. Cascading...",
                                cand_model,
                                resp.status_code,
                                resp.text[:150],
                            )
                            token_info["status"] = f"http_{resp.status_code}"
                            continue
                    except httpx.TimeoutException:
                        logger.warning("OpenRouter model %s timed out after %ss; cascading...", cand_model, timeout_sec)
                        continue
                    except Exception as model_exc:
                        logger.warning("OpenRouter exception on %s: %s; cascading...", cand_model, model_exc)
                        continue

            logger.warning("All OpenRouter candidate models exhausted for %s.", call_type)
            return "", token_info
        except Exception as exc:
            token_info["status"] = f"error: {exc}"
            logger.warning("OpenRouter client-level exception during %s: %s", call_type, exc)
            return "", token_info

    async def call_jev_decision(
        self,
        *,
        instruction: str,
        context: Dict[str, Any],
        choices: List[str],
        default_choice: str,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute sub-second typed decision evaluation using Jev (typesafe/jev).
        Returns a dict: {"choice": str, "confidence": float, "engine": "jev" | "local_heuristic"}
        """
        if not choices:
            return {"choice": default_choice, "confidence": 1.0, "engine": "default"}

        # ---------------------------------------------------------------------
        # 1. If OpenRouter Key is configured: Call Jev via OpenRouter
        # ---------------------------------------------------------------------
        if self.is_configured:
            jev_model = settings.OPENROUTER_JEV_MODEL or "typesafe/jev"
            prompt = (
                f"INSTRUCTION: {instruction}\n"
                f"CONTEXT: {json.dumps(context, ensure_ascii=False)}\n"
                f"CHOICES: {json.dumps(choices)}\n"
                "Return valid JSON strictly with keys 'choice' (must be exactly one of the CHOICES) and 'confidence' (0.0-1.0)."
            )
            payload = {
                "model": jev_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 100,
            }

            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers=_get_headers(),
                        json=payload,
                    )
                    if resp.status_code == 200:
                        raw = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                        clean = re.sub(r"```json|```", "", raw).strip()
                        parsed = json.loads(clean)
                        choice = parsed.get("choice")
                        if choice in choices:
                            return {
                                "choice": choice,
                                "confidence": float(parsed.get("confidence", 0.95)),
                                "engine": "openrouter_jev",
                            }
            except Exception as exc:
                logger.warning("Jev decision call failed (%s); falling back to local heuristic.", exc)

        # ---------------------------------------------------------------------
        # 2. Local Fallback Heuristic Gate (0 tokens, works completely offline)
        # ---------------------------------------------------------------------
        query = str(context.get("query", "")).lower().strip()
        memory_sections = context.get("known_memory_sections") or []
        profile_text = str(context.get("user_memory_profile", "")).lower()

        # Memory Cache Check: Verify if fact is actually recorded in user_memory.md
        if "read_memory_cache" in choices:
            if profile_text:
                if "pan" in query and "pan number:" in profile_text:
                    return {"choice": "read_memory_cache", "confidence": 0.99, "engine": "local_heuristic"}
                if "aadhaar" in query and "aadhaar number:" in profile_text:
                    return {"choice": "read_memory_cache", "confidence": 0.99, "engine": "local_heuristic"}
                if ("dob" in query or "birth" in query) and "date of birth:" in profile_text:
                    return {"choice": "read_memory_cache", "confidence": 0.99, "engine": "local_heuristic"}
                if "blood" in query and "blood group:" in profile_text:
                    return {"choice": "read_memory_cache", "confidence": 0.99, "engine": "local_heuristic"}
                if "father" in query and ("father" in profile_text or "father's pan" in profile_text):
                    return {"choice": "read_memory_cache", "confidence": 0.95, "engine": "local_heuristic"}
            else:
                if re.search(r"\b(my\s+pan|my\s+aadhaar|my\s+dob|my\s+blood|my\s+name|who am i)\b", query):
                    return {"choice": "read_memory_cache", "confidence": 0.99, "engine": "local_heuristic"}
                if any(sec.lower() in query for sec in memory_sections if len(sec) > 3):
                    return {"choice": "read_memory_cache", "confidence": 0.90, "engine": "local_heuristic"}

        # Deep Investigator Check: Temporal, comparisons, or multi-hop family lookups
        if "deep_investigator" in choices:
            if re.search(r"\b(pnr|ticket|train|flight|journey|upcoming|compare|difference|between|father|mother|spouse)\b", query):
                return {"choice": "deep_investigator", "confidence": 0.92, "engine": "local_heuristic"}

        # Digital Text vs Scanned Image Check (Ingestion Gate)
        if "clean_digital_text" in choices and "scanned_needs_vision" in choices:
            extracted_len = int(context.get("extracted_text_len", 0))
            if extracted_len > 40:
                return {"choice": "clean_digital_text", "confidence": 0.98, "engine": "local_heuristic"}
            else:
                return {"choice": "scanned_needs_vision", "confidence": 0.95, "engine": "local_heuristic"}

        return {"choice": default_choice, "confidence": 0.85, "engine": "local_heuristic"}


openrouter_client = OpenRouterClient()
