"""Loader for the B4 synthetic/adversarial fixture package.

The fixture is explicitly synthetic (`real_chat_records=0`). This loader only
reads the model-visible message files; gold files are used by evaluators and
must not be passed to retrievers/judges.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


@dataclass
class B4Fixture:
    root: Path
    messages: dict[str, dict[str, Any]]
    events: dict[str, dict[str, Any]]
    event_by_source: dict[str, str]
    pair_gold: dict[str, dict[str, Any]]
    retrieval_queries: list[dict[str, Any]]
    retrieval_gold: dict[str, dict[str, Any]]

    @classmethod
    def load(cls, root: str | Path) -> "B4Fixture":
        root = Path(root)
        messages = {row["source_id"]: row for row in read_jsonl(root / "messages_interleaved.jsonl")}
        events = {row["event_id"]: row for row in read_jsonl(root / "gold_events.jsonl")}
        event_by_source = {row["source_id"]: row["gold_event_id"]
                           for row in read_jsonl(root / "message_gold.jsonl")
                           if row.get("gold_event_id")}
        pair_gold = {row["pair_id"]: row for row in read_jsonl(root / "pair_gold.jsonl")}
        queries = read_jsonl(root / "retrieval_queries_input.jsonl")
        retrieval_gold = {row["query_id"]: row for row in read_jsonl(root / "retrieval_query_gold.jsonl")}
        return cls(root=root, messages=messages, events=events,
                   event_by_source=event_by_source, pair_gold=pair_gold,
                   retrieval_queries=queries, retrieval_gold=retrieval_gold)

    def event_sources_before(self, event_id: str, query_source_id: str,
                             max_sources: int = 4) -> list[dict[str, Any]]:
        event = self.events[event_id]
        query_seq = int(self.messages[query_source_id]["stream_seq"])
        sources = []
        for source_id in event["source_ids"]:
            msg = self.messages.get(source_id)
            if msg is None:
                continue
            if int(msg["stream_seq"]) < query_seq:
                sources.append(msg)
        sources.sort(key=lambda item: int(item["stream_seq"]))
        return sources[-max_sources:]
