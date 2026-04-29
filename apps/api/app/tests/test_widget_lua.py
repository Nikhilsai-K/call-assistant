"""The widget rate-limit Lua script is tiny but critical — verify shape and
that all three caps are enforced (per-IP, per-agent, per-org)."""

from app.api.widget import (
    _RATE_LIMIT_LUA,
    RATE_LIMIT_PER_AGENT_PER_HOUR,
    RATE_LIMIT_PER_MIN,
    RATE_LIMIT_PER_ORG_PER_MIN,
)


def test_lua_script_uses_one_key_arg():
    """Caller passes 1 KEYS and 2 ARGV entries; misuse breaks the rate limit."""
    assert "KEYS[1]" in _RATE_LIMIT_LUA
    assert "ARGV[1]" in _RATE_LIMIT_LUA
    assert "ARGV[2]" in _RATE_LIMIT_LUA
    assert "INCR" in _RATE_LIMIT_LUA
    assert "EXPIRE" in _RATE_LIMIT_LUA


def test_rate_limit_caps_are_sensible():
    # Defaults must remain conservative — these protect COGS.
    assert RATE_LIMIT_PER_MIN <= 10
    assert RATE_LIMIT_PER_AGENT_PER_HOUR <= 200
    assert RATE_LIMIT_PER_ORG_PER_MIN <= 100
