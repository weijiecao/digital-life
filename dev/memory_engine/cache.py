"""Small generation-keyed context cache for the M5 slice."""

from __future__ import annotations

from typing import Any


class ContextCache:
    def __init__(self):
        self._data: dict[tuple, tuple[int, Any]] = {}

    def get(self, key: tuple, generation: int):
        row = self._data.get(key)
        if row is None or row[0] != generation:
            return None
        return row[1]

    def put(self, key: tuple, generation: int, value: Any) -> None:
        self._data[key] = (generation, value)

    def invalidate_scope(self, scope_id: str) -> None:
        for key in list(self._data):
            if key and key[0] == scope_id:
                del self._data[key]

    def size(self) -> int:
        return len(self._data)
