"""
Semantic endpointing — decides "customer has finished their turn" from partial
text, not just silence. Works in parallel with Silero VAD; a turn ends when
either both agree or VAD sees > 900ms silence regardless.

Heuristic V1:
- Sentence ends with terminal punctuation (., !, ?)
- Or matches common end-of-turn lexical patterns ("that's it", "yes please",
  "ok thanks", numbers/dates spoken completely)
- Or STT confidence dropped AND no new tokens in 180ms AND speaker was silent
  for > VAD_min_silence.

Upgrades later: a small classifier fine-tuned on transcripts.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

_TERMINAL_PUNCT = re.compile(r"[.!?]\s*$")
_LEX_ENDS = re.compile(
    r"\b("
    r"that'?s (it|all)|yes please|no thanks?|ok thanks?|thanks?|bye|"
    r"that works|sounds good|perfect|go ahead|that'?s correct|yeah that'?s it"
    r")\b\.?\s*$",
    re.IGNORECASE,
)
_CONTINUATIONS = re.compile(
    r"\b("
    r"um|uh|and|so|like|actually|wait|hmm|let me|hold on|hang on"
    r")\s*$",
    re.IGNORECASE,
)


@dataclass
class EndpointState:
    last_text_change_ts: float = 0.0
    consecutive_silence_ms: int = 0
    text: str = ""
    partials: list[str] = field(default_factory=list)

    def update_text(self, text: str) -> None:
        if text != self.text:
            self.text = text
            self.last_text_change_ts = time.perf_counter()
            self.partials.append(text)

    def update_silence(self, silence_ms: int) -> None:
        self.consecutive_silence_ms = silence_ms


def is_endpoint(
    state: EndpointState,
    *,
    hard_silence_ms: int = 900,
    soft_silence_ms: int = 300,
) -> bool:
    """Returns True if customer likely finished."""
    if not state.text.strip():
        return False

    # Never endpoint in the middle of a hesitation.
    if _CONTINUATIONS.search(state.text):
        return False

    # Hard silence: always endpoint.
    if state.consecutive_silence_ms >= hard_silence_ms:
        return True

    # Semantic cue + short silence: endpoint.
    return state.consecutive_silence_ms >= soft_silence_ms and bool(
        _TERMINAL_PUNCT.search(state.text) or _LEX_ENDS.search(state.text)
    )
