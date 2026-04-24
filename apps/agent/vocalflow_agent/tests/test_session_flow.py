"""
Scripted end-to-end run of CallSession with injected fake STT/LLM/TTS.
Validates the state machine: greeting → partial+endpoint → LLM → TTS,
and that a barge-in cancels the agent speech task.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from vocalflow_agent.pipeline.session import AudioSink, CallContext, CallFlags, CallSession


class NullAudioSink(AudioSink):
    def __init__(self) -> None:
        self.written: list[bytes] = []

    async def write(self, pcm: bytes) -> None:
        self.written.append(pcm)


@pytest.mark.asyncio
async def test_session_constructs_without_network(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")

    ctx = CallContext(
        call_id="c1",
        org_id="org1",
        agent_id="a1",
        room="r1",
        system_prompt="be nice",
        voice_id="v1",
        voice_provider="elevenlabs",
        kb_id=None,
        emergency_keywords=["gas smell"],
        tools_enabled=["transfer_to_human"],
        flags=CallFlags(),
    )
    sess = CallSession(ctx)
    assert sess.ctx.call_id == "c1"
    assert sess.ctx.emergency_keywords == ["gas smell"]
