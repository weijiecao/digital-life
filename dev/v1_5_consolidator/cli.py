"""CLI for the V1.5 consolidation service."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .adapter import V1ReadOnlyAdapter
from .engine import Consolidator
from .gateway import ContextGateway, MockProvider, OpenAIProvider
from .store import V15Store


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="V1.5 定时事件候选归纳服务")
    parser.add_argument("--v1-db", required=True, help="V1 manual_test_memory.sqlite3（只读）")
    parser.add_argument("--v15-db", required=True, help="V1.5 独立 SQLite 路径")
    parser.add_argument("--provider", choices=("mock", "openai"), default="mock")
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--base-url", default="https://api.deepseek.com/v1")
    parser.add_argument("--api-key-file", default="")
    parser.add_argument("--allow-external", action="store_true")
    parser.add_argument("--scope", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--min-messages", type=int, default=20)
    parser.add_argument("--batch-limit", type=int, default=40)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    run_parser = sub.add_parser("run-once")
    run_parser.add_argument("--force", action="store_true", default=argparse.SUPPRESS)
    run_parser.add_argument("--min-messages", type=int, default=argparse.SUPPRESS)
    run_parser.add_argument("--batch-limit", type=int, default=argparse.SUPPRESS)
    loop_parser = sub.add_parser("run-loop")
    loop_parser.add_argument("--interval", type=int, default=3600)
    loop_parser.add_argument("--force", action="store_true", default=argparse.SUPPRESS)
    loop_parser.add_argument("--min-messages", type=int, default=argparse.SUPPRESS)
    loop_parser.add_argument("--batch-limit", type=int, default=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.command == "init":
        store = V15Store(args.v15_db)
        store.close()
        print(json.dumps({"ok": True, "v15_db": args.v15_db, "schema_version": 1},
                         ensure_ascii=False, indent=2))
        return 0
    if args.command == "status":
        store = V15Store(args.v15_db)
        try:
            print(json.dumps(store.status(), ensure_ascii=False, indent=2))
        finally:
            store.close()
        return 0

    if args.provider == "openai":
        if not args.allow_external:
            print("拒绝：openai provider 需要显式 --allow-external", file=sys.stderr)
            return 2
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        if args.api_key_file:
            key = Path(args.api_key_file).read_text(encoding="utf-8").strip()
        if not key:
            print("缺少 API key：--api-key-file 或 DEEPSEEK_API_KEY", file=sys.stderr)
            return 2
        provider = OpenAIProvider(args.base_url, args.model, key)
        store = V15Store(args.v15_db)
        gateway = ContextGateway(store, deny_external=False)
        consolidator = Consolidator(args.v1_db, args.v15_db, provider, gateway=gateway,
                                    min_messages=args.min_messages,
                                    batch_limit=args.batch_limit, store=store)
    else:
        provider = MockProvider()
        consolidator = Consolidator(args.v1_db, args.v15_db, provider,
                                    min_messages=args.min_messages,
                                    batch_limit=args.batch_limit)
    try:
        if args.command == "run-loop":
            while True:
                summary = consolidator.run_once(scope=args.scope or None, force=args.force)
                print(json.dumps(summary.__dict__, ensure_ascii=False, indent=2), flush=True)
                time.sleep(max(60, args.interval))
        summary = consolidator.run_once(scope=args.scope or None, force=args.force)
        print(json.dumps(summary.__dict__, ensure_ascii=False, indent=2))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2),
              file=sys.stderr)
        return 1
    finally:
        consolidator.close()


if __name__ == "__main__":
    raise SystemExit(main())
