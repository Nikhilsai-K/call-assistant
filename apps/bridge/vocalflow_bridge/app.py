"""
Bridge process. Twilio's <Stream> WebSocket connects here for every PSTN call;
we join the corresponding LiveKit room as the "caller" participant, translate
audio in both directions, and exit cleanly when either side hangs up.

URL pattern:
  /twilio-bridge/{room}    Twilio connects here from TwiML.

The agent worker is already in the room (subscribed to "caller" and publishing
its own "agent" track), so handing off audio is just LiveKit's job from there.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from typing import Any

import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from livekit import api, rtc

from .codec import (
    downsample_16k_to_8k,
    pcm16_to_ulaw,
    ulaw_to_pcm16,
    upsample_8k_to_16k,
)
from .config import settings

log = structlog.get_logger("bridge")

app = FastAPI(title="VocalFlow Bridge", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/twilio-bridge/{room_name}")
async def twilio_bridge(ws: WebSocket, room_name: str) -> None:
    await ws.accept()
    log.info("bridge.connected", room=room_name)

    # Join LiveKit as the "caller" participant.
    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(f"caller-{room_name}")
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
            )
        )
        .to_jwt()
    )

    room = rtc.Room()
    await room.connect(settings.livekit_url, token)

    # Publish caller audio source (16 kHz mono).
    source = rtc.AudioSource(settings.livekit_sample_rate, 1)
    track = rtc.LocalAudioTrack.create_audio_track("caller", source)
    await room.local_participant.publish_track(track)

    # Subscribe to the agent's audio track to send back to Twilio.
    agent_audio_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)

    async def consume_agent_track(track: rtc.RemoteAudioTrack) -> None:
        stream = rtc.AudioStream(track)
        async for frame_evt in stream:
            # frame.data is signed 16-bit PCM at 16 kHz. Twilio wants 8 kHz µ-law.
            pcm16 = bytes(frame_evt.frame.data)
            pcm8 = downsample_16k_to_8k(pcm16)
            ulaw = pcm16_to_ulaw(pcm8)
            try:
                agent_audio_q.put_nowait(ulaw)
            except asyncio.QueueFull:
                pass  # drop on overflow rather than introduce latency.

    @room.on("track_subscribed")
    def _on_subscribe(track, _publication, participant):  # type: ignore[no-untyped-def]
        if track.kind == rtc.TrackKind.KIND_AUDIO and participant.identity != f"caller-{room_name}":
            asyncio.create_task(consume_agent_track(track))

    stream_sid: str | None = None

    async def send_to_twilio() -> None:
        """Pull agent µ-law frames and forward as base64 media events."""
        while True:
            ulaw = await agent_audio_q.get()
            if stream_sid is None:
                continue
            await ws.send_text(
                json.dumps(
                    {
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": base64.b64encode(ulaw).decode("ascii")},
                    }
                )
            )

    sender_task = asyncio.create_task(send_to_twilio())

    try:
        while True:
            raw = await ws.receive_text()
            evt: dict[str, Any] = json.loads(raw)
            event = evt.get("event")
            if event == "connected":
                continue
            if event == "start":
                stream_sid = evt.get("start", {}).get("streamSid") or evt.get("streamSid")
                log.info("bridge.start", room=room_name, stream_sid=stream_sid)
            elif event == "media":
                payload_b64 = evt.get("media", {}).get("payload", "")
                if not payload_b64:
                    continue
                ulaw = base64.b64decode(payload_b64)
                pcm8 = ulaw_to_pcm16(ulaw)
                pcm16 = upsample_8k_to_16k(pcm8)
                # Twilio frames are 20ms (160 samples @ 8 kHz → 320 @ 16 kHz).
                samples = len(pcm16) // 2
                frame = rtc.AudioFrame(
                    data=pcm16,
                    sample_rate=settings.livekit_sample_rate,
                    num_channels=1,
                    samples_per_channel=samples,
                )
                await source.capture_frame(frame)
            elif event == "dtmf":
                # Forward DTMF digits to the agent worker (used in PCI payment flow).
                digit = evt.get("dtmf", {}).get("digit")
                if digit:
                    import redis.asyncio as aioredis

                    r = aioredis.from_url(
                        getattr(settings, "redis_url", "redis://redis:6379/0"),
                        decode_responses=True,
                    )
                    await r.publish(f"vocalflow.rooms.{room_name}.dtmf", digit)
                    await r.aclose()
            elif event == "mark":
                # Twilio acks a `mark` we sent — confirms TTS chunk reached the caller.
                log.debug("bridge.mark", room=room_name, name=evt.get("mark", {}).get("name"))
            elif event == "stop":
                log.info("bridge.stop", room=room_name)
                break
    except WebSocketDisconnect:
        log.info("bridge.ws_disconnect", room=room_name)
    except Exception as e:
        log.exception("bridge.error", room=room_name, err=str(e))
    finally:
        sender_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender_task
        await room.disconnect()
        log.info("bridge.disconnected", room=room_name)
