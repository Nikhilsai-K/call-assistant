import asyncio

import pytest

from vocalflow_agent.tools.registry import ToolContext, ToolRouter


@pytest.mark.asyncio
async def test_unknown_tool_returns_error():
    r = ToolRouter(ToolContext(org_id="x", call_id="c", agent_id="a", kb_id=None, api_base_url=""))
    out = await r.invoke("not_a_tool", {})
    assert out["error"].startswith("unknown")


@pytest.mark.asyncio
async def test_timeout_produces_backchannel(monkeypatch):
    r = ToolRouter(ToolContext(org_id="x", call_id="c", agent_id="a", kb_id=None, api_base_url=""))

    async def slow(_: dict) -> dict:
        await asyncio.sleep(5)
        return {"ok": True}

    r._handlers["book_appointment"] = slow
    out = await r.invoke("book_appointment", {"service": "x"}, timeout=0.05)
    assert out["error"] == "tool_timeout"
    assert "backchannel" in "".join(out.keys())


@pytest.mark.asyncio
async def test_successful_handler_marks_elapsed():
    r = ToolRouter(ToolContext(org_id="x", call_id="c", agent_id="a", kb_id=None, api_base_url=""))

    async def ok(_: dict) -> dict:
        return {"slots": [{"start": "2026-04-25T15:00Z"}]}

    r._handlers["check_calendar_availability"] = ok
    out = await r.invoke("check_calendar_availability", {})
    assert out["slots"][0]["start"] == "2026-04-25T15:00Z"
    assert "_elapsed_ms" in out
