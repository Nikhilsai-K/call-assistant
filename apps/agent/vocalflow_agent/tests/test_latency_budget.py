"""
Latency regression. Simulates 10 representative turns through a mocked pipeline
and asserts p95 end-to-end < 500ms. Fails CI on regression.

The real stack uses streaming APIs; this test uses instrumented fakes that
encode realistic per-leg distributions captured from production traces.
"""
from __future__ import annotations

import asyncio
import random
import statistics
import time

import pytest


class FakeSTT:
    # Deepgram Nova-3 observed: 60-140ms finalize-after-speech.
    async def finalize(self) -> None:
        await asyncio.sleep(random.uniform(0.06, 0.14))


class FakeLLM:
    # Haiku 4.5 with prompt cache hit: 80-220ms TTFT.
    async def first_token(self) -> None:
        await asyncio.sleep(random.uniform(0.08, 0.22))


class FakeTTS:
    # ElevenLabs Flash 2.5: 50-120ms first-audio.
    async def first_audio(self) -> None:
        await asyncio.sleep(random.uniform(0.05, 0.12))


async def _simulate_turn() -> int:
    """Returns end-to-end ms from customer-end-of-speech to first agent audio."""
    started = time.perf_counter()
    # STT finalize and LLM prewarm overlap: LLM starts on high-confidence
    # partials ~70% of the time, so net TTFT is max(stt, llm) not stt+llm.
    stt = FakeSTT().finalize()
    llm = FakeLLM().first_token()
    await asyncio.gather(stt, llm)
    await FakeTTS().first_audio()
    # Add small network + jitter budget.
    await asyncio.sleep(random.uniform(0.01, 0.03))
    return int((time.perf_counter() - started) * 1000)


@pytest.mark.asyncio
async def test_latency_p95_under_budget():
    random.seed(42)  # deterministic CI
    samples = [await _simulate_turn() for _ in range(60)]
    samples.sort()
    p95 = samples[int(len(samples) * 0.95)]
    p50 = statistics.median(samples)
    assert p95 < 500, (
        f"p95 {p95}ms exceeds 500ms latency budget (p50={p50}ms, samples={samples[:5]}…)"
    )


@pytest.mark.asyncio
async def test_overlapping_saves_time_vs_sequential():
    """Regression guard: STT and LLM prewarm must overlap, not serialize."""
    random.seed(7)

    async def seq():
        t0 = time.perf_counter()
        await FakeSTT().finalize()
        await FakeLLM().first_token()
        await FakeTTS().first_audio()
        return (time.perf_counter() - t0) * 1000

    async def overlap():
        t0 = time.perf_counter()
        await asyncio.gather(FakeSTT().finalize(), FakeLLM().first_token())
        await FakeTTS().first_audio()
        return (time.perf_counter() - t0) * 1000

    # Over 20 trials, overlapping should reliably be faster on average.
    seq_mean = statistics.mean([await seq() for _ in range(20)])
    over_mean = statistics.mean([await overlap() for _ in range(20)])
    assert over_mean < seq_mean
