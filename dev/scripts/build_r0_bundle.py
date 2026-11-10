"""Build the R0 release-candidate evidence bundle (NO-GO label)."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

DEV = Path(__file__).resolve().parents[1]
OUT = DEV.parent / "_publish" / "digital-life" / "releases" / "memory-engine-r0-rc1.zip"
FILES = [
    "v1_5_consolidator/store.py",
    "v1_5_consolidator/contracts.py",
    "v1_5_consolidator/candidate_retriever.py",
    "v1_5_consolidator/resolver.py",
    "v1_5_consolidator/provenance_guard.py",
    "v1_5_consolidator/gateway.py",
    "v1_5_consolidator/relation_judge.py",
    "memory_engine/__init__.py",
    "memory_engine/engine.py",
    "memory_engine/rejudge.py",
    "memory_engine/worker.py",
    "memory_engine/cache.py",
    "memory_engine/backup.py",
    "memory_engine/render.py",
    "memory_engine/host_budget.py",
    "memory_engine/projection.py",
    "memory_engine/summary.py",
    "memory_engine/summary_cache.py",
    "memory_engine/token_budget.py",
    "memory_engine/host_adapter.py",
    "memory_engine/shadow.py",
    "host_shadow/main.py",
    "host_shadow/metadata.yaml",
    "host_shadow/README.md",
    "host_shadow/soak.py",
    "reports/MEMORY_ENGINE_ACCEPTANCE_2026-11-10.md",
    "reports/MEMORY_ENGINE_STATUS_2026-11-10.md",
    "reports/M2_DATA_PROGRESS_2026-11-10.md",
    "reports/M6_SCALE_SMOKE_2026-11-10.md",
    "reports/M7_HOST_BUDGET_REAL_2026-11-11.md",
]


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as bundle:
        for rel in sorted(FILES):
            info = zipfile.ZipInfo(rel, date_time=(2026, 11, 11, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, (DEV / rel).read_bytes())
    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    manifest = {"artifact": OUT.name, "sha256": digest,
                "bytes": OUT.stat().st_size, "files": len(FILES),
                "status": "R0 release candidate; NO-GO", "tests": 526}
    (OUT.parent / "memory-engine-r0-rc1.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
