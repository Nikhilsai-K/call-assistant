"""
Streaming LLM adapter for Anthropic Claude.

Design:
 - Every in-call message uses prompt caching (system prompt + tools + KB).
 - Tokens stream out. The first-token latency is the critical metric.
 - Tool use: the stream yields tool_use events; we return partial text so TTS
   can start playback immediately. Tool execution happens on the caller side.
 - Cancellation: the caller cancels the task on barge-in; this cleanly flushes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from anthropic import AsyncAnthropic


@dataclass
class TokenChunk:
    kind: str  # "text" | "tool_use_start" | "tool_use_input" | "tool_use_end" | "done"
    text: str = ""
    tool_name: str = ""
    tool_id: str = ""
    tool_input: dict[str, Any] | None = None
    usage: dict[str, int] | None = None


class ClaudeStreamer:
    def __init__(self, api_key: str, model: str):
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def stream(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 600,
        temperature: float = 0.3,
        cached_system: bool = True,
    ) -> AsyncIterator[TokenChunk]:
        """
        Yields TokenChunk events as the model generates.

        Uses explicit prompt caching on system + tools to slash per-turn tokens.
        """
        system_blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": system,
                **({"cache_control": {"type": "ephemeral"}} if cached_system else {}),
            }
        ]
        # Cache the tool schemas block too (identical across turns).
        cached_tools = tools
        if cached_system and tools:
            cached_tools = [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}]

        stream = self._client.messages.stream(
            model=self._model,
            system=system_blocks,
            tools=cached_tools,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        async with stream as s:
            current_tool_id: str | None = None
            current_tool_name: str | None = None
            current_tool_input_buf: list[str] = []
            async for event in s:
                t = getattr(event, "type", None)
                if t == "content_block_start":
                    block = getattr(event, "content_block", None)
                    if block is not None and getattr(block, "type", "") == "tool_use":
                        current_tool_id = getattr(block, "id", "")
                        current_tool_name = getattr(block, "name", "")
                        current_tool_input_buf = []
                        yield TokenChunk(
                            kind="tool_use_start",
                            tool_name=current_tool_name or "",
                            tool_id=current_tool_id or "",
                        )
                elif t == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    dtype = getattr(delta, "type", "")
                    if dtype == "text_delta":
                        yield TokenChunk(kind="text", text=getattr(delta, "text", ""))
                    elif dtype == "input_json_delta":
                        frag = getattr(delta, "partial_json", "")
                        current_tool_input_buf.append(frag)
                        yield TokenChunk(kind="tool_use_input", text=frag)
                elif t == "content_block_stop" and current_tool_id:
                    import json as _json

                    try:
                        input_obj = (
                            _json.loads("".join(current_tool_input_buf))
                            if current_tool_input_buf
                            else {}
                        )
                    except _json.JSONDecodeError:
                        input_obj = {}
                    yield TokenChunk(
                        kind="tool_use_end",
                        tool_name=current_tool_name or "",
                        tool_id=current_tool_id or "",
                        tool_input=input_obj,
                    )
                    current_tool_id = None
                    current_tool_name = None
                    current_tool_input_buf = []
                elif t == "message_stop":
                    final = await s.get_final_message()
                    usage = None
                    if final and final.usage:
                        usage = {
                            "input_tokens": final.usage.input_tokens,
                            "output_tokens": final.usage.output_tokens,
                            "cache_read_input_tokens": getattr(
                                final.usage, "cache_read_input_tokens", 0
                            ),
                            "cache_creation_input_tokens": getattr(
                                final.usage, "cache_creation_input_tokens", 0
                            ),
                        }
                    yield TokenChunk(kind="done", usage=usage)
                    return

    async def generate_handoff_brief(self, transcript: list[dict], reason: str) -> str:
        """Warm Handoff 2.0: 20-second spoken brief that plays to the human."""
        convo = "\n".join(f"{t['speaker']}: {t['text']}" for t in transcript[-20:])
        msg = await self._client.messages.create(
            model=self._model,
            max_tokens=220,
            system=(
                "You write 15-20 second call briefs for a human taking over a call. "
                "Format: customer identity, their issue, what the agent tried, what they want next. "
                "Spoken style, under 60 words, no pleasantries, no 'the agent'."
            ),
            messages=[{"role": "user", "content": f"Reason: {reason}\n\nConversation:\n{convo}"}],
        )
        return msg.content[0].text if msg.content else ""
