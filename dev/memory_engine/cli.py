"""CLI for the offline memory engine slice."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memory_engine.engine import MemoryEngine
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memory-engine")
    parser.add_argument("--db", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest")
    ingest.add_argument("--scope", required=True)
    ingest.add_argument("--seq", type=int, required=True)
    ingest.add_argument("--text", required=True)
    ingest.add_argument("--hash", default="cli")

    search = sub.add_parser("search")
    search.add_argument("--scope", required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--min-score", type=float, default=0.0)

    context = sub.add_parser("context")
    context.add_argument("--scope", required=True)
    context.add_argument("--query", required=True)
    context.add_argument("--budget", type=int, default=2000)
    context.add_argument("--budget-tokens", type=int, default=None)
    context.add_argument("--rendered", action="store_true")
    context.add_argument("--tokenizer", default="cl100k_base")
    context.add_argument("--min-score", type=float, default=0.0)

    health = sub.add_parser("health")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    store = V15Store(args.db)
    try:
        engine = MemoryEngine(store)
        if args.command == "ingest":
            receipt = engine.ingest(
                args.scope, SourceEnvelope(args.scope, args.seq, args.hash,
                                           "cli", args.text))
            print(json.dumps({"event_id": receipt.event_id,
                              "receipt_ok": receipt.receipt_ok}, ensure_ascii=False))
        elif args.command == "search":
            print(json.dumps(engine.retrieve(args.scope, args.query, args.limit,
                                             args.min_score),
                             ensure_ascii=False, indent=2))
        elif args.command == "context":
            if args.rendered:
                from memory_engine.token_budget import TokenCounter
                counter = TokenCounter(args.tokenizer)
                packet = engine.build_rendered_context(
                    args.scope, args.query, args.budget_tokens or args.budget,
                    counter, args.min_score)
            else:
                packet = engine.build_context(args.scope, args.query, args.budget,
                                              min_score=args.min_score)
            print(json.dumps(packet, ensure_ascii=False, indent=2))
        elif args.command == "health":
            print(json.dumps(engine.health(), ensure_ascii=False))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
