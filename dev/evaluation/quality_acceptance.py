"""Candidate recall + evidence admission acceptance harness.

Runs the same grouped query set through the real MemoryEngine.  Supply
``--model-dir`` to exercise the actual local BGE encoder; without it the
lexical path is measured.  The harness never writes to the source database.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from collections import defaultdict
from pathlib import Path

from memory_engine.engine import MemoryEngine
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store


def _source(scope: str, seq: int, text: str) -> SourceEnvelope:
    import hashlib
    return SourceEnvelope(scope, seq, hashlib.sha256(text.encode()).hexdigest(),
                          "t", text)


def _encoder(model_dir: str | None):
    if not model_dir:
        return None
    from astrbot_plugin_digital_memory_store.engine.e32.cpu_model import CPUSentenceEmbedder
    return CPUSentenceEmbedder(model_dir, cpu_threads=4, batch_size=8)


def evaluate(groups: list[dict], encoder=None) -> dict:
    rows = []
    for group in groups:
        scope = group["group"]
        with tempfile.TemporaryDirectory(prefix="quality-") as directory:
            store = V15Store(Path(directory) / "quality.sqlite3")
            try:
                engine = MemoryEngine(store, encoder=encoder)
                messages = group["messages"]
                old = engine.ingest(
                    scope, _source(scope, 1, messages["initial"]),
                    provenance=group.get("initial_provenance")).event_id
                current = engine.revise_source(
                    scope, 1, _source(scope, 2, messages["current"]))
                deleted = engine.ingest(
                    scope, _source(scope, 3, messages["deleted"]),
                    provenance=group.get("deleted_provenance")).event_id
                engine.delete_source(scope, 3)
                withdrawn = engine.ingest(
                    scope, _source(scope, 4, messages["withdrawn"]),
                    provenance=group.get("withdrawn_provenance")).event_id
                engine.withdraw_event(scope, withdrawn)
                other = engine.ingest(
                    "other", _source("other", 1, messages["other_scope"])).event_id
                labels = {old: "initial", current: "current", deleted: "deleted",
                          withdrawn: "withdrawn", other: "other_scope"}
                for query in group["queries"]:
                    retrieved = engine.retrieve(scope, query["query"], limit=10)
                    packet = engine.build_context(scope, query["query"])
                    rank_labels = [labels.get(item["event_id"], "unknown")
                                   for item in retrieved]
                    context_labels = [labels.get(item["event_id"], "unknown")
                                      for item in packet["items"]]
                    expected = query["expected"]
                    rows.append({
                        **query,
                        "group": scope,
                        "rank": (rank_labels.index(expected) + 1
                                 if expected in rank_labels else 0),
                        "context_contains_gold": expected in context_labels,
                        "context_count": len(context_labels),
                        "invalid_context": any(label != "current" for label in context_labels),
                        "returned": rank_labels,
                    })
            finally:
                store.close()
    return summarize(rows)


def summarize(rows: list[dict]) -> dict:
    def stats(items):
        pos = [r for r in items if r["expected"] is not None]
        neg = [r for r in items if r["expected"] is None]
        return {
            "positive": len(pos), "negative": len(neg),
            "top1_hits": sum(r["rank"] == 1 for r in pos),
            "top5_hits": sum(0 < r["rank"] <= 5 for r in pos),
            "positive_context_hits": sum(r["context_contains_gold"] for r in pos),
            "negative_context_injections": sum(bool(r["context_count"]) for r in neg),
            "invalid_context_injections": sum(r["invalid_context"] for r in items),
        }
    categories = defaultdict(list)
    for row in rows:
        categories[row["category"]].append(row)
    return {"overall": stats(rows),
            "by_category": {name: stats(items) for name, items in sorted(categories.items())}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", required=True)
    parser.add_argument("--model-dir", default="")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    groups = json.loads(Path(args.gold).read_text(encoding="utf-8"))
    result = evaluate(groups, _encoder(args.model_dir))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
