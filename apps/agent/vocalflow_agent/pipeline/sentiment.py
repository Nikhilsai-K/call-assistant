"""
Cheap per-turn sentiment classifier. Two-tier:
 1. Lexical fast-path (< 1ms) catches obvious anger/frustration markers.
 2. If ambiguous, queue to a small LLM call async — the result arrives on the
    NEXT turn (never blocks the current turn).

Trigger: if sentiment trends negative for 2+ consecutive turns, the main loop
proactively surfaces transfer_to_human.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_NEGATIVE = re.compile(
    r"\b("
    r"angry|frustrated|ridiculous|unacceptable|terrible|horrible|awful|"
    r"worst|hate|sick of|fed up|stop|cancel|refund|lawsuit|manager|supervisor|"
    r"this is insane|what the|are you kidding"
    r")\b",
    re.IGNORECASE,
)
_POSITIVE = re.compile(
    r"\b(thank you|thanks|great|perfect|awesome|love|happy|appreciate)\b",
    re.IGNORECASE,
)


@dataclass
class SentimentTracker:
    timeline: list[dict] = field(default_factory=list)
    consecutive_negative: int = 0

    def score(self, text: str) -> float:
        """-1 (very negative) .. +1 (very positive)."""
        neg = len(_NEGATIVE.findall(text))
        pos = len(_POSITIVE.findall(text))
        total = neg + pos
        if total == 0:
            return 0.0
        return (pos - neg) / max(total, 1)

    def observe(self, text: str, ts_ms: int) -> float:
        s = self.score(text)
        self.timeline.append({"ts_ms": ts_ms, "score": s, "text_len": len(text)})
        if s < -0.2:
            self.consecutive_negative += 1
        else:
            self.consecutive_negative = 0
        return s

    @property
    def should_suggest_handoff(self) -> bool:
        return self.consecutive_negative >= 2
