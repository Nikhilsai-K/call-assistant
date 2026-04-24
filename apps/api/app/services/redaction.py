"""
PII / PHI / PCI redaction.

PCI mode diverts card entry to DTMF; this module redacts *post-hoc* anything
that leaked (e.g. a caller says their card number). Used both on live transcripts
before persistence and in the post-call pipeline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Conservative regexes — Presidio handles the heavy lifting in post-call.
_CC = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE = re.compile(r"\b\+?1?[ .-]?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b")
_CVV = re.compile(r"\b\d{3,4}\b")  # applied only inside payment-entry window


@dataclass
class RedactionResult:
    text: str
    had_pii: bool
    categories: list[str]


def redact_inline(text: str, *, payment_window: bool = False) -> RedactionResult:
    categories: list[str] = []
    original = text

    def sub(pattern: re.Pattern[str], label: str, s: str) -> str:
        nonlocal categories
        if pattern.search(s):
            categories.append(label)
            return pattern.sub(f"[REDACTED_{label}]", s)
        return s

    text = sub(_CC, "CC", text)
    text = sub(_SSN, "SSN", text)
    text = sub(_EMAIL, "EMAIL", text)
    # Don't redact phone in general transcripts — callers give their own number.
    if payment_window:
        text = sub(_PHONE, "PHONE", text)
        text = sub(_CVV, "CVV", text)
    return RedactionResult(text=text, had_pii=text != original, categories=categories)
