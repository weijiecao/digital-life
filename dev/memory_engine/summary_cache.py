"""Pure text-summary cache; no dependency invalidation needed."""

from __future__ import annotations

from memory_engine.summary import summarize


class SummaryCache:
    def __init__(self):
        self._data: dict[str, str] = {}

    def get(self, text: str, max_chars: int = 120) -> str:
        key = "%d:%s" % (max_chars, text)
        value = self._data.get(key)
        if value is None:
            value = summarize(text, max_chars)
            self._data[key] = value
        return value

    def size(self) -> int:
        return len(self._data)
