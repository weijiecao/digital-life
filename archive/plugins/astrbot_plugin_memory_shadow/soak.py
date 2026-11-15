"""Sample the Memory Engine shadow DB/log for a long-running soak.

Usage:
  python soak.py --once
  python soak.py --interval 300 --hours 72 --out soak.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path


def sample(root: Path) -> dict:
    base = root / "data" / "plugin_data" / "astrbot_plugin_memory_shadow"
    db = base / "shadow.sqlite3"
    log = base / "shadow_events.jsonl"
    result = {"ts": time.time(), "db_exists": db.is_file(),
              "log_exists": log.is_file()}
    if db.is_file():
        try:
            conn = sqlite3.connect(str(db))
            try:
                result["events"] = conn.execute(
                    "SELECT COUNT(*) FROM event_candidate").fetchone()[0]
                result["sources"] = conn.execute(
                    "SELECT COUNT(*) FROM source_ref").fetchone()[0]
                result["scopes"] = conn.execute(
                    "SELECT COUNT(DISTINCT scope_id) FROM source_ref").fetchone()[0]
                result["valid_evidence"] = conn.execute(
                    "SELECT COUNT(*) FROM event_evidence WHERE valid=1").fetchone()[0]
            finally:
                conn.close()
        except sqlite3.Error as exc:
            result["db_error"] = repr(exc)
    if log.is_file():
        result["observations"] = len(
            log.read_text(encoding="utf-8").splitlines())
        result["bytes"] = log.stat().st_size
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=os.environ.get("ASTRBOT_ROOT", "."))
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--hours", type=float, default=0)
    parser.add_argument("--out", default="shadow_soak.jsonl")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    out = Path(args.out)
    start = time.time()
    while True:
        record = sample(root)
        with out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps(record, ensure_ascii=False), flush=True)
        if args.once:
            return 0
        if args.hours and time.time() - start >= args.hours * 3600:
            return 0
        time.sleep(max(1, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
