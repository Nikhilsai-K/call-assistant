"""
Plain-English → structured system prompt compiler.

Designer workflow: user types "you're Mike's HVAC receptionist, book jobs into
Jobber, emergencies page Mike directly" and Claude Sonnet compiles it to a
structured system prompt + tool config.

Falls back to a deterministic template when Anthropic is unavailable (tests, CI).
"""
from __future__ import annotations

import json
from typing import Any

from anthropic import AsyncAnthropic

from app.core.config import get_settings
from app.schemas.agents import Personality

COMPILER_SYSTEM = """You are a prompt engineer for VocalFlow voice agents.
Given a plain-English business description, compile a structured system prompt
that runs on Claude Haiku 4.5 during live phone calls.

HARD REQUIREMENTS of the compiled prompt:
- Concise (under 400 words). Every token runs on every turn.
- Explicit persona, objectives, tone, and voice style.
- Crystal-clear tool-use rules: when to call each, what to say while waiting.
- Backchannel policy ("mm-hmm", "one sec") during silence > 700ms.
- Emergency keyword handling routes to transfer_to_human immediately.
- Post-hold recovery: if customer says "hold on", stop talking, resume naturally.
- Barge-in: customer speech instantly cancels TTS.
- Never make up calendar/customer data — always use tools.
- When unsure, answer_faq against the KB; if still unsure, offer transfer.
- No "I am an AI" disclosures unless explicitly asked.

Return JSON with fields:
  system_prompt (string)
  tools_enabled (array of tool names from the allowlist)
  personality (object: formality, pace, warmth, verbosity — each 0..1)
  emergency_keywords (array of strings)
  greeting (one-sentence opening line)
"""

ALLOWED_TOOLS = [
    "check_calendar_availability",
    "book_appointment",
    "lookup_customer",
    "create_ticket",
    "transfer_to_human",
    "send_sms",
    "send_email",
    "collect_payment",
    "escalate",
    "schedule_callback",
    "answer_faq",
]


def _template_fallback(plain: str, personality: Personality | None) -> dict[str, Any]:
    p = personality or Personality()
    system = (
        "You are a friendly, efficient voice receptionist. "
        f"{plain.strip()} "
        "Keep replies under 25 words. Speak conversationally. "
        "Use backchannels ('mm-hmm', 'got it') during pauses. "
        "While a tool is running, say 'one sec'. "
        "On customer speech, stop talking and listen. "
        "Use tools for any calendar or customer lookup — never invent data. "
        "If the caller mentions 'emergency', 'gas smell', 'no heat', "
        "'burst pipe', or 'flooding', transfer to a human immediately."
    )
    return {
        "system_prompt": system,
        "tools_enabled": [
            "check_calendar_availability",
            "book_appointment",
            "transfer_to_human",
            "answer_faq",
            "send_sms",
        ],
        "personality": p.model_dump(),
        "emergency_keywords": [
            "emergency", "gas smell", "no heat", "burst pipe", "flooding", "fire",
        ],
        "greeting": "Hi, thanks for calling — how can I help today?",
    }


async def compile_prompt(
    plain: str,
    personality: Personality | None = None,
) -> dict[str, Any]:
    s = get_settings()
    if not s.anthropic_api_key:
        return _template_fallback(plain, personality)

    client = AsyncAnthropic(api_key=s.anthropic_api_key)
    msg = await client.messages.create(
        model=s.llm_model_postcall,
        max_tokens=1500,
        system=COMPILER_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Compile this: {plain}\n\n"
                    f"Personality: {(personality or Personality()).model_dump()}\n\n"
                    f"Allowed tools: {ALLOWED_TOOLS}\n"
                    "Return JSON only, no prose."
                ),
            }
        ],
    )
    raw = msg.content[0].text if msg.content else ""
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        compiled: dict[str, Any] = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return _template_fallback(plain, personality)

    compiled["tools_enabled"] = [
        t for t in compiled.get("tools_enabled", []) if t in ALLOWED_TOOLS
    ]
    return compiled
