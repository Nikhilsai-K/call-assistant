"""
TTS streaming. Primary: ElevenLabs Flash 2.5. Fallback: Cartesia Sonic.
Emergency: AWS Polly (we don't implement the emergency path here — it is
triggered from a higher supervisor when both primary and fallback fail twice
within 30 seconds; see ops runbook).

Streams PCM chunks as they arrive; first-chunk target < 100ms.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from ..config import settings


async def stream_tts(
    text: str, *, voice_id: str, provider: str = "elevenlabs"
) -> AsyncIterator[bytes]:
    if provider == "elevenlabs":
        try:
            async for chunk in _elevenlabs(text, voice_id):
                yield chunk
            return
        except Exception:
            pass
    async for chunk in _cartesia(text, voice_id):
        yield chunk


async def _elevenlabs(text: str, voice_id: str) -> AsyncIterator[bytes]:
    url = (
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
        f"?optimize_streaming_latency=3&output_format=pcm_16000"
    )
    headers = {
        "xi-api-key": settings.elevenlabs_api_key,
        "Content-Type": "application/json",
        "Accept": "audio/pcm",
    }
    payload = {
        "text": text,
        "model_id": "eleven_flash_v2_5",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    async with httpx.AsyncClient(timeout=10.0) as c:
        async with c.stream("POST", url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(chunk_size=4096):
                if chunk:
                    yield chunk


async def _cartesia(text: str, voice_id: str) -> AsyncIterator[bytes]:
    url = "https://api.cartesia.ai/tts/sse"
    headers = {
        "X-API-Key": settings.cartesia_api_key,
        "Cartesia-Version": "2024-06-10",
        "Content-Type": "application/json",
    }
    payload = {
        "model_id": "sonic-english",
        "transcript": text,
        "voice": {"mode": "id", "id": voice_id},
        "output_format": {"container": "raw", "encoding": "pcm_s16le", "sample_rate": 16000},
    }
    async with httpx.AsyncClient(timeout=10.0) as c:
        async with c.stream("POST", url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                obj = json.loads(data)
                import base64 as _b64

                audio_b64 = obj.get("data", "")
                if audio_b64:
                    yield _b64.b64decode(audio_b64)
