"""Verify the R0 RC zip hash and a minimal import/ingest/retrieve."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLISH = ROOT.parent / "_publish" / "digital-life"
ZIP = PUBLISH / "releases" / "memory-engine-r0-rc1.zip"
MANIFEST = PUBLISH / "releases" / "memory-engine-r0-rc1.json"


def main() -> int:
    digest = hashlib.sha256(ZIP.read_bytes()).hexdigest()
    expected = json.loads(MANIFEST.read_text(encoding="utf-8"))
    checks = {"sha256": digest == expected["sha256"],
              "bytes": ZIP.stat().st_size == expected["bytes"]}
    with tempfile.TemporaryDirectory(prefix="r0-verify-") as d:
        with zipfile.ZipFile(ZIP) as bundle:
            bundle.extractall(d)
            checks["files"] = len(bundle.namelist()) == expected["files"]
        sys.path.insert(0, d)
        from memory_engine.engine import MemoryEngine
        from v1_5_consolidator.contracts import SourceEnvelope
        from v1_5_consolidator.store import V15Store
        store = V15Store(str(Path(d) / "v15.sqlite3"))
        engine = MemoryEngine(store)
        engine.ingest("s", SourceEnvelope("s", 1, "sha", "t", "茶"))
        checks["retrieve"] = len(engine.retrieve("s", "茶")) == 1
        checks["integrity"] = store.integrity_check() == "ok"
        store.close()
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
