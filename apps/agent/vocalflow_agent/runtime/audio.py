"""
Thin wrappers around LiveKit audio I/O. The CallSession speaks to these
interfaces; tests substitute fakes.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from ..pipeline.session import AudioSink


class LiveKitAudioSink(AudioSink):
    """Publishes 16kHz PCM frames into a LiveKit room as an agent track."""

    def __init__(self, room: "livekit.rtc.Room", sample_rate: int = 16000):  # noqa: F821
        self._room = room
        self._sr = sample_rate
        self._source = None
        self._track = None

    async def start(self) -> None:
        from livekit import rtc

        self._source = rtc.AudioSource(self._sr, 1)
        self._track = rtc.LocalAudioTrack.create_audio_track("agent", self._source)
        await self._room.local_participant.publish_track(self._track)

    async def write(self, pcm_chunk: bytes) -> None:
        from livekit import rtc

        # Deliver 20ms frames for low jitter.
        frame_samples = self._sr // 50
        frame_bytes = frame_samples * 2
        view = memoryview(pcm_chunk)
        for i in range(0, len(view), frame_bytes):
            frame = rtc.AudioFrame(
                data=bytes(view[i : i + frame_bytes]),
                sample_rate=self._sr,
                num_channels=1,
                samples_per_channel=frame_samples,
            )
            await self._source.capture_frame(frame)


async def iter_participant_audio(track: "livekit.rtc.RemoteAudioTrack") -> AsyncIterator[bytes]:  # noqa: F821
    from livekit import rtc

    stream = rtc.AudioStream(track)
    async for frame_evt in stream:
        yield bytes(frame_evt.frame.data)
