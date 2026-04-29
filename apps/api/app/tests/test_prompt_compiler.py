import pytest

from app.schemas.agents import Personality
from app.services.prompt_compiler import _template_fallback, compile_prompt


def test_template_fallback_includes_core_directives():
    out = _template_fallback("Be a dental front desk", Personality())
    sys = out["system_prompt"].lower()
    assert "backchannel" in sys
    assert "one sec" in sys
    assert "emergency" in sys or "gas smell" in sys
    assert "transfer_to_human" in out["tools_enabled"]
    assert set(out["emergency_keywords"])  # nonempty


@pytest.mark.asyncio
async def test_compile_prompt_falls_back_without_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    out = await compile_prompt("HVAC receptionist", Personality())
    assert "system_prompt" in out
    assert len(out["system_prompt"]) > 50
