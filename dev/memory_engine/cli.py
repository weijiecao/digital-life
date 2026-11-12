"""CLI for the offline memory engine slice."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from memory_engine.engine import MemoryEngine
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memory-engine")
    parser.add_argument("--db", required=True)
    parser.add_argument("--bge-model", default="",
                        help="optional sentence-transformers model for hybrid retrieval")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest")
    ingest.add_argument("--scope", required=True)
    ingest.add_argument("--seq", type=int, required=True)
    ingest.add_argument("--text", required=True)
    ingest.add_argument("--hash", default=None, help="defaults to SHA-256 of text")

    search = sub.add_parser("search")
    search.add_argument("--scope", required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--explain", action="store_true", help="return candidate ranks and source-bound admission decisions")
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
    history = sub.add_parser("history")
    history.add_argument("--scope", required=True)
    history.add_argument("--event-id", required=True)

    revise = sub.add_parser("revise")
    revise.add_argument("--scope", required=True)
    revise.add_argument("--old-seq", type=int, required=True)
    revise.add_argument("--new-seq", type=int, required=True)
    revise.add_argument("--text", required=True)
    revise.add_argument("--hash", default=None, help="defaults to SHA-256 of text")

    withdraw = sub.add_parser("withdraw")
    withdraw.add_argument("--scope", required=True)
    withdraw.add_argument("--event-id", required=True)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    store = V15Store(args.db)
    try:
        encoder = None
        if args.bge_model:
            from sentence_transformers import SentenceTransformer
            encoder = SentenceTransformer(args.bge_model, device="cpu")
        engine = MemoryEngine(store, encoder=encoder)
        if args.command == "ingest":
            receipt = engine.ingest(
                args.scope, SourceEnvelope(args.scope, args.seq,
                                           args.hash or hashlib.sha256(args.text.encode("utf-8")).hexdigest(),
                                           "cli", args.text))
            print(json.dumps({"event_id": receipt.event_id,
                              "receipt_ok": receipt.receipt_ok}, ensure_ascii=False))
            if not receipt.receipt_ok:
                return 1
        elif args.command == "search":
            search_fn = engine.retrieve_with_audit if args.explain else engine.retrieve
            print(json.dumps(search_fn(args.scope, args.query, args.limit,
                                             args.min_score),
                             ensure_ascii=False, indent=2))
        elif args.command == "context":
            if args.rendered:
                from memory_engine.token_budget import TokenCounter
                counter = TokenCounter(args.tokenizer)
                budget = (args.budget_tokens if args.budget_tokens is not None
                          else args.budget)
                packet = engine.build_rendered_context(
                    args.scope, args.query, budget, counter, args.min_score)
            else:
                packet = engine.build_context(args.scope, args.query, args.budget,
                                              min_score=args.min_score)
            print(json.dumps(packet, ensure_ascii=False, indent=2))
        elif args.command == "health":
            print(json.dumps(engine.health(), ensure_ascii=False))
        elif args.command == "history":
            print(json.dumps(engine.history(args.scope, args.event_id),
                             ensure_ascii=False, indent=2))
        elif args.command == "revise":
            event_id = engine.revise_source(
                args.scope, args.old_seq,
                SourceEnvelope(args.scope, args.new_seq,
                               args.hash or hashlib.sha256(args.text.encode("utf-8")).hexdigest(),
                               "cli", args.text))
            print(json.dumps({"event_id": event_id}, ensure_ascii=False))
        elif args.command == "withdraw":
            print(json.dumps({"withdrawn": engine.withdraw_event(
                args.scope, args.event_id)}, ensure_ascii=False))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
