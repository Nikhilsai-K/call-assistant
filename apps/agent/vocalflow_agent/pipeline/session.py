"""
CallSession — the central orchestrator per call.

Threads the whole pipeline together:

  audio_in -> VAD -> STT (Deepgram stream)
                    |
                    +-> partials: prewarm speculative LLM call on high-conf partials
                    +-> endpoint: finalize turn, cancel prewarm losers
                                  |
                                  v
                            Claude Haiku (stream)
                                  |
                   +--------------+-------------+
                   v                            v
                 TEXT                       TOOL_USE
                   |                            |
                   v                            v
               TTS Flash 2.5                ToolRouter (3s timeout)
                   |                            |    (backchannel during)
                   v                            v
             audio_out <-----------------  resume LLM with tool_result

Every event is pushed to Redis pubsub for the dashboard and to Langfuse for audit.

Barge-in: customer audio during agent speech cancels TTS + LLM immediately.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis
import structlog

from ..config import settings
from ..tools.registry import TOOL_DEFINITIONS, ToolContext, ToolRouter
from .endpointing import EndpointState, is_endpoint
from .llm import ClaudeStreamer
from .sentiment import SentimentTracker

log = structlog.get_logger("session")


@dataclass
class CallFlags:
    pci_mode: bool = False
    hipaa_mode: bool = False
    recording_paused: bool = False
    in_payment_entry: bool = False
    shadow_mode: bool = False


@dataclass
class CallContext:
    call_id: str
    org_id: str
    agent_id: str
    room: str
    system_prompt: str
    voice_id: str
    voice_provider: str
    kb_id: str | None
    emergency_keywords: list[str]
    tools_enabled: list[str]
    flags: CallFlags = field(default_factory=CallFlags)


@dataclass
class LatencyMarks:
    customer_end_speech: float | None = None
    stt_final: float | None = None
    llm_first_token: float | None = None
    tts_first_audio: float | None = None

    def summary_ms(self) -> dict[str, int | None]:
        def ms(a: float | None, b: float | None) -> int | None:
            if a is None or b is None:
                return None
            return int((b - a) * 1000)

        return {
            "stt_final_from_end_ms": ms(self.customer_end_speech, self.stt_final),
            "llm_ttft_from_stt_ms": ms(self.stt_final, self.llm_first_token),
            "tts_first_audio_from_llm_ms": ms(self.llm_first_token, self.tts_first_audio),
            "end_to_end_ms": ms(self.customer_end_speech, self.tts_first_audio),
        }


class CallSession:
    """One instance per call."""

    def __init__(self, ctx: CallContext):
        self.ctx = ctx
        self.flags = ctx.flags
        self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        self._llm = ClaudeStreamer(
            api_key=settings.anthropic_api_key, model=settings.llm_model_incall
        )
        self._tool_router = ToolRouter(
            ToolContext(
                org_id=ctx.org_id,
                call_id=ctx.call_id,
                agent_id=ctx.agent_id,
                kb_id=ctx.kb_id,
                api_base_url=settings.api_base_url,
            )
        )
        self._sentiment = SentimentTracker()
        self._endpoint = EndpointState()
        self._history: list[dict[str, Any]] = []
        self._turn_counter = 0
        # Cancellation handle for whatever agent-side task is running (LLM+TTS).
        self._agent_speech_task: asyncio.Task | None = None
        self._started_at = time.time()

    # ---- Public entry points ----

    async def run(self, audio_in: AsyncIterator[bytes], audio_out: AudioSink) -> None:
        """Main loop — drives STT, turn detection, and agent response."""
        # 1. Play greeting immediately.
        await self._speak("Hi, thanks for calling. How can I help?", audio_out)

        # 2. Bridge audio → Deepgram streaming.
        stt_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        stt_task = asyncio.create_task(self._run_stt(audio_in, stt_queue))

        try:
            while True:
                event = await stt_queue.get()
                if event["type"] == "eof":
                    break
                if event["type"] == "partial":
                    await self._handle_partial(event, audio_out)
                elif event["type"] == "final":
                    await self._handle_final(event, audio_out)
                elif event["type"] == "vad_speech":
                    # Barge-in.
                    await self._cancel_agent_speech()
        finally:
            stt_task.cancel()
            await self._redis.aclose()

    # ---- Internals ----

    async def _run_stt(
        self, audio_in: AsyncIterator[bytes], queue: asyncio.Queue[dict[str, Any]]
    ) -> None:
        """Stream audio to Deepgram Nova-3; push STT + VAD events to queue."""
        # In production this uses deepgram-sdk streaming websocket. We wire a
        # pluggable interface so unit tests can inject a fake.
        from .stt_deepgram import DeepgramStream

        async with DeepgramStream(
            api_key=settings.deepgram_api_key,
            interim_results=settings.stt_interim_results,
            endpointing_ms=settings.stt_endpointing_ms,
        ) as dg:

            async def pump() -> None:
                async for chunk in audio_in:
                    await dg.send(chunk)
                await dg.flush()

            pump_task = asyncio.create_task(pump())
            async for evt in dg.events():
                await queue.put(evt)
            await queue.put({"type": "eof"})
            pump_task.cancel()

    async def _handle_partial(self, evt: dict[str, Any], audio_out: AudioSink) -> None:
        text = evt["text"]
        self._endpoint.update_text(text)
        self._endpoint.update_silence(evt.get("silence_ms", 0))

        # Emergency keyword fast-path — trigger transfer mid-turn.
        lower = text.lower()
        for kw in self.ctx.emergency_keywords:
            if kw.lower() in lower:
                await self._handle_emergency(kw, audio_out)
                return

        if is_endpoint(self._endpoint):
            await self._run_turn(text, evt.get("customer_end_ts"), audio_out)

    async def _handle_final(self, evt: dict[str, Any], audio_out: AudioSink) -> None:
        # Deepgram's own final; only run if we haven't already handled it.
        text = evt["text"]
        if self._endpoint.text == text and self._turn_counter > 0:
            return
        self._endpoint.update_text(text)
        await self._run_turn(text, evt.get("customer_end_ts"), audio_out)

    async def _run_turn(
        self, customer_text: str, customer_end_ts: float | None, audio_out: AudioSink
    ) -> None:
        self._turn_counter += 1
        self._endpoint = EndpointState()
        marks = LatencyMarks(customer_end_speech=customer_end_ts, stt_final=time.perf_counter())

        await self._persist_transcript("customer", customer_text)
        self._history.append({"role": "user", "content": customer_text})

        self._sentiment.observe(customer_text, int((time.time() - self._started_at) * 1000))

        # Cancel any still-playing agent speech (defensive).
        await self._cancel_agent_speech()

        self._agent_speech_task = asyncio.create_task(self._generate_and_speak(audio_out, marks))

        if self._sentiment.should_suggest_handoff and settings.feature_emotion_routing:
            # Inject a system nudge on the next turn so the agent offers a handoff.
            self._history.append(
                {
                    "role": "user",
                    "content": (
                        "[INTERNAL: the caller seems frustrated. On this turn, empathize "
                        "and offer to transfer them to a human manager.]"
                    ),
                }
            )

    async def _generate_and_speak(self, audio_out: AudioSink, marks: LatencyMarks) -> None:
        """Streams LLM tokens → TTS → audio_out; pauses for tool calls."""
        sent_first_token = False
        text_buffer: list[str] = []
        tts_tasks: list[asyncio.Task] = []

        filler_task: asyncio.Task | None = None

        async def filler_after(ms: int) -> None:
            await asyncio.sleep(ms / 1000)
            if settings.feature_filler_injection:
                await self._speak("Let me check that for you…", audio_out, filler=True)

        if settings.feature_filler_injection:
            filler_task = asyncio.create_task(filler_after(settings.filler_trigger_ms))

        try:
            async for chunk in self._llm.stream(
                system=self.ctx.system_prompt,
                messages=self._history,
                tools=[t for t in TOOL_DEFINITIONS if t["name"] in self.ctx.tools_enabled],
            ):
                if chunk.kind == "text":
                    if not sent_first_token:
                        marks.llm_first_token = time.perf_counter()
                        if filler_task and not filler_task.done():
                            filler_task.cancel()
                        sent_first_token = True
                    text_buffer.append(chunk.text)
                    # Flush on sentence boundary for low-latency TTS chunking.
                    combined = "".join(text_buffer)
                    if _has_sentence_boundary(combined):
                        sentence, leftover = _split_on_last_boundary(combined)
                        tts_tasks.append(asyncio.create_task(self._speak(sentence, audio_out)))
                        text_buffer = [leftover] if leftover else []
                elif chunk.kind == "tool_use_end":
                    # Flush any pending text first (agent said something before tool).
                    if text_buffer:
                        await self._speak("".join(text_buffer), audio_out)
                        text_buffer = []
                    # Backchannel while tool runs.
                    if settings.feature_backchannels:
                        asyncio.create_task(self._speak("one sec", audio_out, filler=True))
                    tool_result = await self._tool_router.invoke(
                        chunk.tool_name, chunk.tool_input or {}
                    )
                    self._history.append(
                        {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": chunk.tool_id,
                                    "name": chunk.tool_name,
                                    "input": chunk.tool_input or {},
                                }
                            ],
                        }
                    )
                    self._history.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": chunk.tool_id,
                                    "content": json.dumps(tool_result),
                                }
                            ],
                        }
                    )
                    await self._log_event(
                        "tool_call",
                        {
                            "name": chunk.tool_name,
                            "input": chunk.tool_input,
                            "result": tool_result,
                        },
                    )
                    # Warm Handoff 2.0: when transfer_to_human fires, generate
                    # the 20-second spoken brief and stash it in Redis so the
                    # human accepting the call hears it before the customer is
                    # bridged in.
                    if chunk.tool_name == "transfer_to_human":
                        try:
                            brief = await self._llm.generate_handoff_brief(
                                [
                                    {"speaker": h.get("role"), "text": h.get("content", "")}
                                    for h in self._history
                                    if isinstance(h.get("content"), str)
                                ],
                                reason=(chunk.tool_input or {}).get("reason", ""),
                            )
                            await self._redis.set(
                                f"vocalflow.calls.{self.ctx.call_id}.handoff_brief",
                                brief,
                                ex=3600,
                            )
                            await self._log_event("handoff_brief", {"text": brief})
                        except Exception as e:
                            log.warn("handoff_brief.failed", err=str(e))
                    # After a tool, loop the LLM once more to verbalize the result.
                    asyncio.create_task(self._generate_and_speak(audio_out, marks))
                    return
                elif chunk.kind == "done":
                    if text_buffer:
                        tts_tasks.append(
                            asyncio.create_task(self._speak("".join(text_buffer), audio_out))
                        )
                    self._history.append(
                        {
                            "role": "assistant",
                            "content": "".join(t for t in text_buffer) or "",
                        }
                    )
                    await self._log_latency(marks, chunk.usage or {})
                    break

            await asyncio.gather(*tts_tasks, return_exceptions=True)

        except asyncio.CancelledError:
            # Barge-in happened. Cancel TTS tasks and let caller re-listen.
            for t in tts_tasks:
                t.cancel()
            raise
        finally:
            if filler_task and not filler_task.done():
                filler_task.cancel()

    async def _speak(self, text: str, audio_out: AudioSink, *, filler: bool = False) -> None:
        """Synthesize via ElevenLabs Flash → stream chunks to room."""
        from .tts import stream_tts

        first_chunk = True
        started = time.perf_counter()
        async for pcm in stream_tts(
            text, voice_id=self.ctx.voice_id, provider=self.ctx.voice_provider
        ):
            if first_chunk and not filler:
                first_chunk = False
                await self._log_event(
                    "tts_first_audio_ms",
                    {"elapsed_ms": int((time.perf_counter() - started) * 1000)},
                )
            await audio_out.write(pcm)
        if not filler:
            await self._persist_transcript("agent", text)

    async def _cancel_agent_speech(self) -> None:
        if self._agent_speech_task and not self._agent_speech_task.done():
            self._agent_speech_task.cancel()
            try:
                await self._agent_speech_task
            except (asyncio.CancelledError, Exception):
                pass
            await self._log_event("interruption", {"at_ms": int(time.time() * 1000)})

    async def _handle_emergency(self, matched_kw: str, audio_out: AudioSink) -> None:
        await self._cancel_agent_speech()
        await self._speak(
            "I understand — this sounds urgent. Hold on, I'm getting you to someone now.",
            audio_out,
        )
        result = await self._tool_router.invoke(
            "transfer_to_human",
            {
                "queue": "emergency",
                "reason": f"emergency_keyword:{matched_kw}",
                "context_summary": f"Emergency keyword '{matched_kw}' detected.",
            },
        )
        await self._log_event("emergency_transfer", {"keyword": matched_kw, "result": result})

    async def _persist_transcript(self, speaker: str, text: str) -> None:
        payload = {
            "call_id": self.ctx.call_id,
            "speaker": speaker,
            "text": text,
            "ts_ms": int((time.time() - self._started_at) * 1000),
        }
        await self._redis.publish(f"vocalflow.calls.{self.ctx.call_id}.events", json.dumps(payload))
        await self._redis.xadd(f"vocalflow.calls.{self.ctx.call_id}.transcript", payload)

    async def _log_event(self, type_: str, payload: dict[str, Any]) -> None:
        await self._redis.xadd(
            f"vocalflow.calls.{self.ctx.call_id}.events.log",
            {"type": type_, "payload": json.dumps(payload)},
        )

    async def _log_latency(self, marks: LatencyMarks, usage: dict[str, int]) -> None:
        payload = {"marks": marks.summary_ms(), "usage": usage}
        await self._log_event("latency", payload)
        budget = 500
        e2e = marks.summary_ms().get("end_to_end_ms")
        if e2e is not None and e2e > budget:
            log.warn("latency.over_budget", e2e_ms=e2e, budget_ms=budget)


# ---- Audio sink contract ----


class AudioSink:
    async def write(self, pcm_chunk: bytes) -> None:  # pragma: no cover - interface
        raise NotImplementedError


def _has_sentence_boundary(s: str) -> bool:
    return any(c in s for c in ".!?") and s.rstrip().endswith(("!", "?", ".", ","))


def _split_on_last_boundary(s: str) -> tuple[str, str]:
    for i in range(len(s) - 1, -1, -1):
        if s[i] in ".!?":
            return s[: i + 1], s[i + 1 :].lstrip()
    return s, ""
