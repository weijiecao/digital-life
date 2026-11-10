"""Optional exact tokenizer adapter (tiktoken when installed)."""

from __future__ import annotations


class TokenCounter:
    def __init__(self, encoding_name: str = "cl100k_base"):
        import tiktoken

        self.encoding_name = encoding_name
        self._encoding = tiktoken.get_encoding(encoding_name)

    def count(self, text: str) -> int:
        return len(self._encoding.encode(text or ""))
