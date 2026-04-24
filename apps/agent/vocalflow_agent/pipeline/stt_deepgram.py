"""
Deepgram Nova-3 streaming websocket wrapper.

Exposes:
  async with DeepgramStream(...) as dg:
      await dg.send(pcm_chunk)
      async for evt in dg.events(): ...

Events are dicts shaped for the session loop:
  { "type": "partial" | "final" | "vad_speech" | "vad_silence",
    "text": str, "confidence": float, "silence_ms": int, ... }
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from typing import Any


class DeepgramStream:
    def __init__(
        self,
        *,
        api_key: str,
        interim_results: bool = True,
        endpointing_ms: int = 100,
        model: str = "nova-3",
        sample_rate: int = 16000,
    ):
        self._api_key = api_key
        self._model = model
        self._interim = interim_results
        self._endpointing_ms = endpointing_ms
        self._sample_rate = sample_rate
        self._ws: Any = None
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._listener: asyncio.Task | None = None
        self._last_speech_ts: float = time.perf_counter()

    async def __aenter__(self) -> DeepgramStream:
        # Use websockets via httpx WS if available; otherwise rely on deepgram-sdk.
        # Implementation is pluggable; in tests we override with a fake.
        try:
            import websockets
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("websockets package required for DeepgramStream") from e

        import websockets

        url = (
            f"wss://api.deepgram.com/v1/listen"
            f"?model={self._model}"
            f"&interim_results={'true' if self._interim else 'false'}"
            f"&endpointing={self._endpointing_ms}"
            f"&vad_events=true"
            f"&punctuate=true"
            f"&smart_format=true"
            f"&language=en-US"
            f"&encoding=linear16"
            f"&sample_rate={self._sample_rate}"
        )
        self._ws = await websockets.connect(
            url, extra_headers={"Authorization": f"Token {self._api_key}"}
        )
        self._listener = asyncio.create_task(self._listen())
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._listener:
            self._listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener
        if self._ws is not None:
            await self._ws.close()

    async def send(self, chunk: bytes) -> None:
        if self._ws is not None:
            await self._ws.send(chunk)

    async def flush(self) -> None:
        if self._ws is not None:
            await self._ws.send(json.dumps({"type": "CloseStream"}))

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            evt = await self._event_queue.get()
            if evt.get("_sentinel"):
                return
            yield evt

    async def _listen(self) -> None:
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                data = json.loads(raw)
                t = data.get("type")
                if t == "Results":
                    alt = data.get("channel", {}).get("alternatives", [{}])[0]
                    text = alt.get("transcript", "")
                    conf = alt.get("confidence", 0.0)
                    is_final = data.get("is_final", False)
                    silence_ms = int((time.perf_counter() - self._last_speech_ts) * 1000)
                    if text.strip():
                        self._last_speech_ts = time.perf_counter()
                    await self._event_queue.put(
                        {
                            "type": "final" if is_final else "partial",
                            "text": text,
                            "confidence": conf,
                            "silence_ms": silence_ms,
                            "customer_end_ts": time.perf_counter() if is_final else None,
                        }
                    )
                elif t == "SpeechStarted":
                    await self._event_queue.put({"type": "vad_speech"})
                elif t == "UtteranceEnd":
                    await self._event_queue.put({"type": "vad_silence", "silence_ms": 0})
        except Exception:
            pass
        finally:
            await self._event_queue.put({"_sentinel": True})
