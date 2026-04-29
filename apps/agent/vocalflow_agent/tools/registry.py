"""
Tool definitions (Anthropic tool-use JSON schemas) and router.

Every tool execution is:
  - wrapped in a 3-second timeout
  - traced to Langfuse
  - logged to call_events
  - on error, surfaces a "one sec — my system's a little slow" backchannel,
    never silence. The caller then decides whether to retry, fall back, or
    transfer_to_human.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

# --- Anthropic tool-use schemas (claude.messages tools=...) ---

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "check_calendar_availability",
        "description": "Check real calendar slots for a service within a date range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_range": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "string", "format": "date-time"},
                        "end": {"type": "string", "format": "date-time"},
                    },
                    "required": ["start", "end"],
                },
                "service_type": {"type": "string"},
            },
            "required": ["date_range", "service_type"],
        },
    },
    {
        "name": "book_appointment",
        "description": "Book a confirmed appointment; writes through to the connected calendar/FSM.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "start_at": {"type": "string", "format": "date-time"},
                "duration_min": {"type": "integer", "minimum": 5, "maximum": 480},
                "customer": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "phone": {"type": "string"},
                        "email": {"type": "string"},
                        "address": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "required": ["name", "phone"],
                },
            },
            "required": ["service", "start_at", "duration_min", "customer"],
        },
    },
    {
        "name": "lookup_customer",
        "description": "Look up a returning customer by phone, email, or name+dob.",
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {"type": "string"},
                "email": {"type": "string"},
                "name": {"type": "string"},
                "dob": {"type": "string"},
            },
        },
    },
    {
        "name": "create_ticket",
        "description": "Create a support ticket.",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                "category": {"type": "string"},
            },
            "required": ["description", "priority", "category"],
        },
    },
    {
        "name": "transfer_to_human",
        "description": (
            "Hand off to a human. Triggers Warm Handoff 2.0: a 20-second AI brief plays "
            "to the human first, then bridges the customer in."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "queue": {"type": "string"},
                "reason": {"type": "string"},
                "context_summary": {"type": "string"},
            },
            "required": ["queue", "reason", "context_summary"],
        },
    },
    {
        "name": "send_sms",
        "description": "Send an SMS from a template.",
        "input_schema": {
            "type": "object",
            "properties": {
                "template": {"type": "string"},
                "variables": {"type": "object"},
            },
            "required": ["template", "variables"],
        },
    },
    {
        "name": "send_email",
        "description": "Send an email from a template.",
        "input_schema": {
            "type": "object",
            "properties": {
                "template": {"type": "string"},
                "variables": {"type": "object"},
            },
            "required": ["template", "variables"],
        },
    },
    {
        "name": "collect_payment",
        "description": (
            "Generate a Stripe payment link + SMS it. Does NOT accept card digits over voice; "
            "the caller pays on their phone."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "amount_cents": {"type": "integer", "minimum": 50},
                "description": {"type": "string"},
            },
            "required": ["amount_cents", "description"],
        },
    },
    {
        "name": "escalate",
        "description": "Flag for manager review.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
    {
        "name": "schedule_callback",
        "description": "Schedule a proactive outbound callback.",
        "input_schema": {
            "type": "object",
            "properties": {
                "contact": {"type": "string"},
                "reason": {"type": "string"},
                "when": {"type": "string", "format": "date-time"},
            },
            "required": ["contact", "reason", "when"],
        },
    },
    {
        "name": "answer_faq",
        "description": "Explicit KB query. Returns top-k chunks with citations.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]


@dataclass
class ToolContext:
    org_id: str
    call_id: str
    agent_id: str
    kb_id: str | None
    api_base_url: str
    # Set True during payment entry; PCI mode diverts to DTMF and pauses recording.
    in_payment_entry: bool = False


class ToolRouter:
    """Executes a named tool with bounded latency and structured errors."""

    def __init__(self, ctx: ToolContext):
        self.ctx = ctx
        self._handlers: dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]] = {
            "check_calendar_availability": self._calendar_availability,
            "book_appointment": self._book_appointment,
            "lookup_customer": self._lookup_customer,
            "create_ticket": self._create_ticket,
            "transfer_to_human": self._transfer_to_human,
            "send_sms": self._send_sms,
            "send_email": self._send_email,
            "collect_payment": self._collect_payment,
            "escalate": self._escalate,
            "schedule_callback": self._schedule_callback,
            "answer_faq": self._answer_faq,
        }

    async def invoke(
        self, name: str, args: dict[str, Any], *, timeout: float = 3.0
    ) -> dict[str, Any]:
        handler = self._handlers.get(name)
        if handler is None:
            return {"error": f"unknown tool {name}"}
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(handler(args), timeout=timeout)
            result["_elapsed_ms"] = int((time.perf_counter() - started) * 1000)
            return result
        except TimeoutError:
            return {
                "error": "tool_timeout",
                "_elapsed_ms": int(timeout * 1000),
                "_backchannel": "one sec — my system's a little slow",
            }
        except Exception as e:
            return {
                "error": f"tool_error: {e}",
                "_elapsed_ms": int((time.perf_counter() - started) * 1000),
            }

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.ctx.api_base_url}{path}"
        async with httpx.AsyncClient(timeout=2.5) as c:
            resp = await c.post(url, json=payload, headers={"X-Dev-Org": self.ctx.org_id})
            resp.raise_for_status()
            return resp.json()

    # ---- Handlers ----
    # Every tool gets the active call_id so the API can audit-log to call_events
    # and so payments/handoff can attribute back to the call.

    async def _calendar_availability(self, args: dict[str, Any]) -> dict[str, Any]:
        args["call_id"] = self.ctx.call_id
        return await self._post("/v1/tools/calendar/availability", args)

    async def _book_appointment(self, args: dict[str, Any]) -> dict[str, Any]:
        args["call_id"] = self.ctx.call_id
        return await self._post("/v1/tools/calendar/book", args)

    async def _lookup_customer(self, args: dict[str, Any]) -> dict[str, Any]:
        args["call_id"] = self.ctx.call_id
        return await self._post("/v1/tools/crm/lookup", args)

    async def _create_ticket(self, args: dict[str, Any]) -> dict[str, Any]:
        args["call_id"] = self.ctx.call_id
        return await self._post("/v1/tools/tickets", args)

    async def _transfer_to_human(self, args: dict[str, Any]) -> dict[str, Any]:
        args["call_id"] = self.ctx.call_id
        return await self._post("/v1/tools/handoff", args)

    async def _send_sms(self, args: dict[str, Any]) -> dict[str, Any]:
        args["call_id"] = self.ctx.call_id
        return await self._post("/v1/tools/sms", args)

    async def _send_email(self, args: dict[str, Any]) -> dict[str, Any]:
        args["call_id"] = self.ctx.call_id
        return await self._post("/v1/tools/email", args)

    async def _collect_payment(self, args: dict[str, Any]) -> dict[str, Any]:
        # We NEVER read card digits over voice. The tool returns a link + SMS.
        args["call_id"] = self.ctx.call_id
        return await self._post("/v1/tools/payments/link", args)

    async def _escalate(self, args: dict[str, Any]) -> dict[str, Any]:
        args["call_id"] = self.ctx.call_id
        return await self._post("/v1/tools/escalate", args)

    async def _schedule_callback(self, args: dict[str, Any]) -> dict[str, Any]:
        args["call_id"] = self.ctx.call_id
        return await self._post("/v1/tools/callback", args)

    async def _answer_faq(self, args: dict[str, Any]) -> dict[str, Any]:
        args["kb_id"] = self.ctx.kb_id
        args["call_id"] = self.ctx.call_id
        return await self._post("/v1/tools/kb/query", args)
