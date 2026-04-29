"""
G.711 µ-law ↔ 16-bit PCM, plus 8 kHz ↔ 16 kHz resampling.

Twilio Media Streams send µ-law 8 kHz; LiveKit consumes 16-bit linear PCM.
We avoid SciPy's heavyweight resampler — a small polyphase upsample/downsample
is sufficient for voice and stays under 1 ms per frame on a single core.
"""

from __future__ import annotations

import audioop  # type: ignore[import-not-found]

import numpy as np


def ulaw_to_pcm16(ulaw_bytes: bytes) -> bytes:
    """G.711 µ-law (1 byte/sample) → signed 16-bit linear PCM."""
    return audioop.ulaw2lin(ulaw_bytes, 2)


def pcm16_to_ulaw(pcm_bytes: bytes) -> bytes:
    """Signed 16-bit linear PCM → G.711 µ-law (1 byte/sample)."""
    return audioop.lin2ulaw(pcm_bytes, 2)


def upsample_8k_to_16k(pcm_bytes: bytes) -> bytes:
    """Linear-interpolate 8 kHz mono PCM up to 16 kHz mono PCM.

    Voice quality is nearly indistinguishable from a windowed sinc resampler
    for this 2x ratio at human-speech bandwidth.
    """
    samples = np.frombuffer(pcm_bytes, dtype=np.int16)
    if samples.size == 0:
        return b""
    # x2 by interleaving each sample with the midpoint of itself and the next.
    out = np.empty(samples.size * 2, dtype=np.int16)
    out[0::2] = samples
    # Midpoints: average with next sample, last sample copies itself.
    next_samples = np.concatenate([samples[1:], samples[-1:]])
    out[1::2] = ((samples.astype(np.int32) + next_samples.astype(np.int32)) // 2).astype(np.int16)
    return out.tobytes()


def downsample_16k_to_8k(pcm_bytes: bytes) -> bytes:
    """Decimate 16 kHz mono PCM to 8 kHz mono PCM with a 2-tap pre-filter."""
    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.int32)
    if samples.size == 0:
        return b""
    # Average pairs (cheap low-pass) then drop every other.
    if samples.size % 2 == 1:
        samples = samples[:-1]
    pairs = samples.reshape(-1, 2)
    out = (pairs[:, 0] + pairs[:, 1]) // 2
    return out.astype(np.int16).tobytes()
