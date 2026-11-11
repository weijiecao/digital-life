"""Verify the R0 RC zip hash and a minimal import/ingest/retrieve."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = [
    ROOT.parent / "releases" / "memory-engine-r0-rc1.zip",
    ROOT.parent / "_publish" / "digital-life" / "releases" / "memory-engine-r0-rc1.zip",
]


def locate(bundle: str | None) -> tuple[Path, Path]:
    if bundle:
        zip_path = Path(bundle).resolve()
        return zip_path, zip_path.with_suffix(".json")
    for candidate in CANDIDATES:
        if candidate.is_file():
            return candidate, candidate.with_suffix(".json")
    raise FileNotFoundError(
        "R0 bundle not found; pass --bundle /path/to/memory-engine-r0-rc1.zip")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", default="")
    args = parser.parse_args(argv)
    try:
        zip_path, manifest_path = locate(args.bundle)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    expected = json.loads(manifest_path.read_text(encoding="utf-8"))
    checks = {"sha256": digest == expected["sha256"],
              "bytes": zip_path.stat().st_size == expected["bytes"]}
    with tempfile.TemporaryDirectory(prefix="r0-verify-") as d:
        with zipfile.ZipFile(zip_path) as bundle:
            bundle.extractall(d)
            checks["files"] = len(bundle.namelist()) == expected["files"]
        sys.path.insert(0, d)
        from memory_engine.engine import MemoryEngine
        from v1_5_consolidator.contracts import SourceEnvelope
        from v1_5_consolidator.store import V15Store
        store = V15Store(str(Path(d) / "v15.sqlite3"))
        engine = MemoryEngine(store)
        engine.ingest("s", SourceEnvelope("s", 1, "sha", "t", "tea"))
        checks["retrieve"] = len(engine.retrieve("s", "tea")) == 1
        checks["integrity"] = store.integrity_check() == "ok"
        store.close()
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
