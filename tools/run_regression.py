"""Run the active regression suite and record a machine-readable result.

The same command is used locally and by CI, so the numbers quoted in the
documentation always come from a log that was written by this script::

    python tools/run_regression.py              # human output + dist/regression.*
    python tools/run_regression.py --json-out dist/regression.json
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEV = REPO_ROOT / "dev"

sys.path.insert(0, str(REPO_ROOT))

from tools.repo_packaging import git_state  # noqa: E402


class _Tee(io.TextIOBase):
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)
        return len(text)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", default="dist/regression.json")
    parser.add_argument("--log-out", default="dist/regression.log")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    def _resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else REPO_ROOT / path

    json_out = _resolve(args.json_out)
    log_out = _resolve(args.log_out)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    log_out.parent.mkdir(parents=True, exist_ok=True)

    if str(DEV) not in sys.path:
        sys.path.insert(0, str(DEV))
    suite = unittest.TestLoader().discover(str(DEV / "tests"),
                                           top_level_dir=str(DEV))
    started = _now()
    clock = time.perf_counter()
    state = git_state(REPO_ROOT)
    with log_out.open("w", encoding="utf-8", newline="\n") as handle:
        stream = handle if args.quiet else _Tee(sys.stdout, handle)
        runner = unittest.TextTestRunner(
            stream=stream, verbosity=1 if args.quiet else 2)
        result = runner.run(suite)
    elapsed = time.perf_counter() - clock

    skipped = [{"test": test.id(), "reason": str(reason)}
               for test, reason in result.skipped]
    total = result.testsRun
    failed = len(result.failures)
    errors = len(result.errors)
    counts = {
        "total": total,
        "passed": total - failed - errors - len(skipped)
                  - len(result.unexpectedSuccesses),
        "failed": failed,
        "errors": errors,
        "skipped": len(skipped),
        "skip_reasons": skipped,
    }
    record = {
        "schema": "digital-life-regression/1",
        "suite": "source-regression",
        "target": "source",
        "ok": result.wasSuccessful(),
        "tested_commit": state["commit"],
        "dirty": state["dirty"],
        "command": "python tools/run_regression.py",
        "working_directory": "dev (top-level)",
        "started_at": started,
        "finished_at": _now(),
        "duration_seconds": round(elapsed, 3),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        **counts,
        "log_reference": (log_out.relative_to(REPO_ROOT).as_posix()
                          if log_out.is_relative_to(REPO_ROOT) else log_out.as_posix()),
    }
    json_out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
    if not args.quiet:
        print(json.dumps({key: record[key] for key in
                          ("ok", "total", "passed", "failed", "errors", "skipped",
                           "duration_seconds")}, ensure_ascii=False))
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
