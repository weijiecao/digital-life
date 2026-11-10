"""Host-level token budget envelope (system + history + output reserve)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HostBudget:
    total_tokens: int
    system_tokens: int = 0
    history_tokens: int = 0
    output_tokens: int = 0

    @property
    def memory_tokens(self) -> int:
        return max(0, self.total_tokens - self.system_tokens
                   - self.history_tokens - self.output_tokens)


def memory_budget(total_tokens: int, system_tokens: int = 0,
                  history_tokens: int = 0, output_tokens: int = 0) -> int:
    return HostBudget(total_tokens, system_tokens, history_tokens,
                      output_tokens).memory_tokens
