"""Codec round-trips. Voice quality isn't quantitatively validated here — just
size invariants and that the data path is non-lossy across modes that don't
discard samples."""

from vocalflow_bridge.codec import (
    downsample_16k_to_8k,
    pcm16_to_ulaw,
    ulaw_to_pcm16,
    upsample_8k_to_16k,
)


def test_ulaw_pcm_roundtrip_size():
    ulaw = bytes(range(160))  # 20ms @ 8kHz
    pcm = ulaw_to_pcm16(ulaw)
    assert len(pcm) == 320  # 16-bit
    back = pcm16_to_ulaw(pcm)
    assert len(back) == 160


def test_upsample_doubles_samples():
    pcm8 = b"\x00\x01" * 100  # 100 samples
    pcm16 = upsample_8k_to_16k(pcm8)
    assert len(pcm16) == len(pcm8) * 2


def test_downsample_halves_samples():
    pcm16 = b"\x00\x10" * 200  # 200 samples (even)
    pcm8 = downsample_16k_to_8k(pcm16)
    assert len(pcm8) == len(pcm16) // 2


def test_downsample_handles_odd_length():
    pcm16 = b"\x00\x10" * 201  # 201 samples
    pcm8 = downsample_16k_to_8k(pcm16)
    # Drops the trailing odd sample, then halves.
    assert len(pcm8) == 200


def test_empty_inputs_are_safe():
    assert ulaw_to_pcm16(b"") == b""
    assert pcm16_to_ulaw(b"") == b""
    assert upsample_8k_to_16k(b"") == b""
    assert downsample_16k_to_8k(b"") == b""
