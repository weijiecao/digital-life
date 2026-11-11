"""Deterministic extractive summary for the M5 slice.

No model call. It keeps the leading sentence(s) and never invents content; the
caller still has the original evidence in the packet.
"""

from __future__ import annotations

import re

_SENTENCE = re.compile(r"[^\u3002\uff01\uff1f\uff1b]+[\u3002\uff01\uff1f\uff1b]?")
_SIGNAL = re.compile("\u6ca1\u6709|\u8fd8\u6ca1|\u672a|\u53d6\u6d88|\u865a\u6784|\u670b\u53cb|\u672c\u4eba|\u5e76\u975e|\u5f85\u5b9a|\u53ef\u80fd|\u4e0d\u786e\u5b9a|\u4ed8\u6b3e")


def summarize(text: str, max_chars: int = 120) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= max_chars:
        return clean
    sentences = [part.strip() for part in _SENTENCE.findall(clean) if part.strip()]
    if not sentences:
        return clean[:max_chars]
    signals = [idx for idx, sentence in enumerate(sentences)
               if _SIGNAL.search(sentence) and len(sentence) <= max_chars]
    selected = [0]
    for index in signals:
        if index == 0:
            continue
        if sum(len(sentences[i]) for i in selected) + len(sentences[index]) <= max_chars:
            selected.append(index)
    if len(selected) == 1 and signals:
        selected = [signals[-1]]
    result = "".join(sentences[i] for i in selected).strip()
    if len(result) > max_chars:
        result = result[:max_chars]
    return result or clean[:max_chars]
