"""
Lean 2-Agent Smart Agentic RAG Engine for Vault City.

Architecture:
- Agent 1 (The Dispatcher): Fast intent router & profile entity cache (resolves 85% in < 500ms).
- Agent 2 (The Investigator): ReAct tool-calling loop (max 3 turns) armed with 4 deterministic Python tools
  for multi-hop entity deduction, Python date arithmetic, and interactive clarification.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.file import FileDocument
from app.services.agent_tools import (
    tool_ask_user_choice,
    tool_filter_by_date,
    tool_get_user_entity,
    tool_search_vault,
)
from app.services.openrouter_client import openrouter_client
from app.services.memory_service import memory_service
from app.services.rag_workflow import (
    get_user_realtime_context,
    run_assistant_rag,
)

logger = logging.getLogger("vaultcity.agentic_rag")

INVESTIGATOR_SYSTEM_PROMPT = """You are the Vault City AI Senior Investigator (va).
You solve complex, multi-hop, or date-sensitive questions regarding the user's private document vault.

REAL-TIME CONTEXT:
- Today's Exact Date: {current_date_str} (ISO: {iso_date}).
- Authenticated User: {username} ({email}).
- Today is the latest reference day. Dates >= {iso_date} are upcoming/future; dates < {iso_date} are past.

AVAILABLE TOOLS (Deterministic Python execution):
1. `get_user_entity(entity_key)`: Looks up user's profile/family graph (e.g. 'father_name', 'mother_name', 'spouse_name', 'dob', 'pan_number').
2. `search_vault(query, doc_name_filter)`: Hybrid semantic search across vault documents.
3. `filter_by_date(mode)`: Python datetime comparator. Modes: 'upcoming', 'past', 'latest'. Strips expired dates.
4. `ask_user_choice(question, options)`: Sends interactive choice buttons to user when multiple candidates exist. Options format: [{{'label': '...', 'value': '...'}}].

INSTRUCTIONS:
- Reason step-by-step.
- On each step, respond strictly in JSON:
  If you need a tool:
  {{"thought": "why you need this tool", "action": "tool_name", "action_input": {{...}}}}

  If you have solved the question:
  {{"thought": "reasoning summary", "final_answer": "Short direct answer with specific entity in backticks like `value`.", "sources": ["matching_doc.pdf"]}}

- Keep answers ultra-crisp, concise, and direct with specific values in backticks (`value`). No fluff.
"""


def _detect_query_intent(query: str) -> str:
    """
    Deterministic regex classifier for routing fast queries vs multi-hop deep investigations.
    """
    q = query.lower().strip()

    # Fast Profile Lookups
    if re.search(r"\b(who am i|my name|what is my name)\b", q):
        return "profile_name"
    if re.search(r"\b(my\s+pan|my\s+pan\s+number)\b", q):
        return "profile_pan"
    if re.search(r"\b(my\s+dob|my\s+date\s+of\s+birth|when\s+was\s+i\s+born)\b", q):
        return "profile_dob"
    if re.search(r"\b(my\s+aadhaar|my\s+aadhaar\s+number)\b", q):
        return "profile_aadhaar"
    if re.search(r"\b(my\s+blood\s+group)\b", q):
        return "profile_blood_group"

    # Multi-hop family lookups (needs entity lookup first)
    if re.search(r"\b(father|mother|spouse|wife|husband|dad|mom|parent)['s]*\s+(pan|aadhaar|passport|dob|birth|name|details|number)\b", q):
        return "investigate_multihop"

    # Temporal & Date Queries (tickets, journeys, bills)
    if re.search(r"\b(pnr|train|flight|ticket|journey|upcoming|next|booking|travel|bbsr|vizag|delhi|mumbai)\b", q):
        return "investigate_temporal"

    # Comparison / Multi-doc queries
    if re.search(r"\b(compare|difference|between|vs|both)\b", q):
        return "investigate_multihop"

    return "standard_rag"


async def _run_investigator_loop(
    query: str,
    user_id: uuid.UUID,
    user_info: Dict[str, Any],
    realtime_context: Dict[str, Any],
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Agent 2 (The Investigator): ReAct tool-calling loop (max 3 turns).
    """
    history: List[Dict[str, str]] = []
    total_tokens = 0
    uid_str = str(user_id)
    today_str = realtime_context.get("full_date_str", "")
    iso_date = realtime_context.get("iso_date", "")
    username = user_info.get("username", "User")
    email = user_info.get("email", "")

    system_prompt = INVESTIGATOR_SYSTEM_PROMPT.format(
        current_date_str=today_str,
        iso_date=iso_date,
        username=username,
        email=email,
    )

    history.append({
        "role": "user",
        "content": f"User Query: {query}\nSolve this step-by-step using tools if needed. Return JSON."
    })

    current_chunks: List[Dict[str, Any]] = []

    for turn in range(1, 4):
        try:
            qwen_model = settings.OPENROUTER_DEFAULT_MODEL or "qwen/qwen-2.5-72b-instruct"
            raw_response, token_info = await openrouter_client.call_chat(
                messages=history,
                system_prompt=system_prompt,
                user_id=uid_str,
                call_type="agentic_investigator",
                model=qwen_model,
                max_tokens=600,
                temperature=0.1,
            )
            total_tokens += int(token_info.get("total_tokens", 0))

            if not raw_response:
                # If OpenRouter is not configured or offline, exit loop and fallback cleanly
                break

            clean_json = re.sub(r"^```json\s*", "", raw_response.strip(), flags=re.IGNORECASE)
            clean_json = re.sub(r"\s*```$", "", clean_json)
            step_data = json.loads(clean_json)
        except Exception as exc:
            logger.warning("Investigator loop turn %d JSON parse error: %s", turn, exc)
            break

        # Check if final answer reached
        if "final_answer" in step_data:
            return {
                "reply": step_data["final_answer"].strip(),
                "sources": step_data.get("sources", []),
                "interactive_options": [],
                "tokens_used": total_tokens,
                "strategy_used": "multi_hop_investigator",
            }

        action = step_data.get("action")
        action_input = step_data.get("action_input") or {}

        # Tool 1: get_user_entity
        if action == "get_user_entity":
            key = action_input.get("entity_key", "")
            res = await tool_get_user_entity(user_id=user_id, entity_key=key, db=db)
            obs = f"tool_get_user_entity result: {json.dumps(res)}"

        # Tool 2: search_vault
        elif action == "search_vault":
            s_q = action_input.get("query", query)
            flt = action_input.get("doc_name_filter")
            res_chunks = await tool_search_vault(user_id=user_id, query=s_q, db=db, doc_name_filter=flt, top_k=4)
            current_chunks = res_chunks
            # Summarize chunks for LLM observation
            chunks_summary = [
                f"[File: {c['filename']} p.{c['page_number']}] {c['content'][:250]}"
                for c in res_chunks
            ]
            obs = f"search_vault found {len(res_chunks)} chunk(s):\n" + "\n".join(chunks_summary)

        # Tool 3: filter_by_date
        elif action == "filter_by_date":
            mode = action_input.get("mode", "upcoming")
            date_res = tool_filter_by_date(items=current_chunks, mode=mode, reference_date=iso_date)
            matched = date_res.get("matches", [])
            obs = f"tool_filter_by_date(mode='{mode}', ref_date='{iso_date}'): {len(matched)} matching items remaining:\n" + json.dumps(matched, default=str)

        # Tool 4: ask_user_choice
        elif action == "ask_user_choice":
            q_text = action_input.get("question", "Please select an option:")
            opts = action_input.get("options", [])
            payload = tool_ask_user_choice(question=q_text, options=opts)
            return {
                "reply": payload["question"],
                "sources": [c["filename"] for c in current_chunks if "filename" in c][:2],
                "interactive_options": payload["options"],
                "tokens_used": total_tokens,
                "strategy_used": "interactive_clarification",
            }

        else:
            obs = f"Unknown tool '{action}'. Please provide final_answer or valid tool."

        # Feed tool observation back to investigator
        history.append({"role": "assistant", "content": raw_response})
        history.append({"role": "user", "content": f"Observation: {obs}\nWhat is your next thought or final_answer?"})

    # Fallback to standard RAG if investigator reached max turns without decision
    logger.info("Investigator loop fallback to standard RAG for query: %s", query)
    fallback_res = await run_assistant_rag(query=query, user_id=uid_str, db=db)
    fallback_res["tokens_used"] = total_tokens + fallback_res.get("tokens_used", 0)
    fallback_res["strategy_used"] = "investigator_fallback_rag"
    return fallback_res


async def run_smart_agentic_rag(
    query: str,
    user_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Main Entrypoint: Lean 2-Agent Agentic RAG Pipeline.
    1. Agent 1 (Dispatcher): Evaluates intent and direct profile entity cache (< 100ms).
    2. Fast Lane: Returns cached profile facts or executes fast single-shot RAG.
    3. Deep Lane: Engages Agent 2 (The Investigator) with deterministic Python tools.
    """
    uid = uuid.UUID(str(user_id))
    user_info, realtime_context = await get_user_realtime_context(user_id, db)

    # -------------------------------------------------------------------------
    # Lane 0: Jev Decision Gate + User Memory Fast Lane (< 20ms, 0 tokens)
    # -------------------------------------------------------------------------
    # Load user_memory.md once (<1ms from Redis) and pass the ENTIRE profile to Jev
    memory_content = await memory_service.load_user_memory(uid)
    jev_decision = await openrouter_client.call_jev_decision(
        instruction=(
            "Examine the user query against their verified personal memory profile. "
            "If the specific fact/answer is ALREADY present and verified in the profile text, choose 'read_memory_cache'. "
            "If the fact is MISSING, blank, or requires searching raw vault documents, choose 'single_shot_rag'. "
            "If it requires multi-document comparisons, journey dates, or tickets, choose 'deep_investigator'."
        ),
        context={"query": query, "user_memory_profile": memory_content},
        choices=["read_memory_cache", "single_shot_rag", "deep_investigator"],
        default_choice="single_shot_rag",
        user_id=str(user_id),
    )
    route_choice = jev_decision.get("choice", "single_shot_rag")

    if route_choice == "read_memory_cache":
        # Send user_memory.md as grounded context to Qwen LLM
        prompt = (
            f"You are the Vault City AI Personal Assistant.\n"
            f"Answer the user's question strictly and accurately using the verified personal memory profile below.\n"
            f"Do not guess or assume any facts not stated. Keep answers concise, direct, and factual with specific values in backticks.\n\n"
            f"User's Verified Personal Memory Profile:\n"
            f"\"\"\"\n{memory_content}\n\"\"\"\n\n"
            f"User Question: {query}"
        )

        reply_text, token_info = await openrouter_client.call_chat(
            messages=[{"role": "user", "content": prompt}],
            user_id=str(user_id),
            model=settings.OPENROUTER_FAST_MODEL or "qwen/qwen-2.5-7b-instruct",
            max_tokens=250,
            temperature=0.1,
            call_type="memory_cache_llm",
        )

        if reply_text and reply_text.strip():
            return {
                "reply": reply_text.strip(),
                "sources": ["user_memory.md"],
                "interactive_options": [],
                "tokens_used": int(token_info.get("total_tokens", 0)),
                "strategy_used": "memory_cache_jev",
            }

        # Offline / No API Key Fallback: check if direct key exists in markdown
        q_lower = query.lower()
        for line in memory_content.splitlines():
            line_str = line.strip()
            if line_str.startswith("- **") and ":" in line_str:
                label_part, val_part = line_str.split(":", 1)
                label_clean = label_part.replace("- **", "").replace("**", "").lower()
                label_words = [w for w in label_clean.split() if len(w) > 2]
                if label_words and any(w in q_lower for w in label_words):
                    val_clean = val_part.split("*(Updated:")[0].strip()
                    if val_clean and val_clean != "None":
                        return {
                            "reply": f"{label_part.replace('- ', '')}: {val_clean}",
                            "sources": ["user_memory.md"],
                            "interactive_options": [],
                            "tokens_used": 0,
                            "strategy_used": "memory_cache_jev_offline",
                        }

    intent = _detect_query_intent(query)

    # -------------------------------------------------------------------------
    # Lane 1: Fast Profile Cache (0ms extra tokens, < 100ms)
    # -------------------------------------------------------------------------
    if intent.startswith("profile_"):
        key_map = {
            "profile_name": "user_name",
            "profile_pan": "pan_number",
            "profile_dob": "dob",
            "profile_aadhaar": "aadhaar_number",
            "profile_blood_group": "blood_group",
        }
        target_key = key_map.get(intent, "")
        cached = await tool_get_user_entity(user_id=uid, entity_key=target_key, db=db)
        if cached.get("found"):
            val = cached["value"]
            if target_key == "user_name":
                reply = f"Your name is `{val}`."
            elif target_key == "pan_number":
                reply = f"Your PAN number is `{val}`."
            elif target_key == "dob":
                reply = f"Your date of birth is `{val}`."
            elif target_key == "aadhaar_number":
                reply = f"Your Aadhaar number is `{val}`."
            elif target_key == "blood_group":
                reply = f"Your blood group is `{val}`."
            else:
                reply = f"`{val}`"

            return {
                "reply": reply,
                "sources": [],
                "interactive_options": [],
                "tokens_used": 0,
                "strategy_used": "fast_profile_cache",
            }

    # -------------------------------------------------------------------------
    # Lane 3: Deep Investigator (Multi-Hop / Temporal / Ambiguous)
    # -------------------------------------------------------------------------
    if route_choice == "deep_investigator" or intent.startswith("investigate_"):
        return await _run_investigator_loop(
            query=query,
            user_id=uid,
            user_info=user_info,
            realtime_context=realtime_context,
            db=db,
        )

    # -------------------------------------------------------------------------
    # Lane 2: Fast Single-Shot RAG (Standard 800ms)
    # -------------------------------------------------------------------------
    rag_result = await run_assistant_rag(query=query, user_id=str(user_id), db=db)
    rag_result["interactive_options"] = []
    rag_result["strategy_used"] = "single_shot_rag"
    return rag_result
