"""Freeze the exact inputs used by the M2 340/21 evaluations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    ROOT / "reports" / "v1_5b_b4_cluster_prov_trace.json",
    ROOT / "reports" / "v1_5b_b4_cluster_prov_trace_run2.json",
    ROOT / "reports" / "v1_5b_r11_series21_run2.json",
    ROOT / "evaluation" / "b4_testdata" / "Digital_Life_B4_TestData_v1"
    / "message_gold.jsonl",
    ROOT / "evaluation" / "b4_testdata" / "Digital_Life_B4_TestData_v1"
    / "manifest.json",
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    payload = {"schema": "m2-frozen-inputs-v1", "date": "2026-11-10",
               "files": {}}
    for path in FILES:
        if path.exists():
            payload["files"][str(path.relative_to(ROOT))] = {
                "sha256": digest(path), "bytes": path.stat().st_size}
    out = ROOT / "reports" / "m2_frozen_manifest.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
