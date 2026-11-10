"""Deterministic extractive summary for the M5 slice.

No model call. It keeps the leading sentence(s) and never invents content; the
caller still has the original evidence in the packet.
"""

from __future__ import annotations

import re

_SENTENCE = re.compile(r"[^。！？!?]+[。！？!?]?")


def summarize(text: str, max_chars: int = 120) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= max_chars:
        return clean
    sentences = [part.strip() for part in _SENTENCE.findall(clean) if part.strip()]
    result = ""
    for sentence in sentences:
        candidate = (result + sentence).strip()
        if len(candidate) > max_chars:
            break
        result = candidate
    if not result:
        result = clean[:max_chars]
    return result
