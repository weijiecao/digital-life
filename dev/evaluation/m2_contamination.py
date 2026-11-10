"""M2 provenance contamination on the 340 replay."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation.b4_frozen_replay import run
from v1_5_consolidator.contracts import NarrativeProvenance
from v1_5_consolidator.store import V15Store

ROOT = Path(__file__).resolve().parents[1]
TRACE = ROOT / "reports" / "v1_5b_b4_cluster_prov_trace.json"
FIXTURE = ROOT / "evaluation" / "b4_testdata" / "Digital_Life_B4_TestData_v1"


def main() -> int:
    trace = json.loads(TRACE.read_text(encoding="utf-8"))["trace"]
    source_prov = {row["source_id"]: NarrativeProvenance(**row["source_provenance"])
                   for row in trace if row.get("source_provenance")}
    with tempfile.TemporaryDirectory(prefix="m2-contam-") as d:
        db = str(Path(d) / "v15.sqlite3")
        run(str(FIXTURE), str(TRACE), 20, db)
        store = V15Store(db)
        try:
            attachments = 0
            contamination = []
            for source_id, provenance in source_prov.items():
                scope = source_id.split("-")[0]
                seq = next((int(row["source_seq"]) for row in trace
                            if row["source_id"] == source_id), None)
                if seq is None:
                    continue
                for event_id in store.events_for_source(scope, seq):
                    attachments += 1
                    event_prov = NarrativeProvenance(**store.event_provenance(event_id))
                    if (provenance.reality_status in ("fictional", "reported")
                            and event_prov.subject == "user"
                            and event_prov.reality_status == "real"):
                        contamination.append({"source_id": source_id,
                                              "event_id": event_id,
                                              "source": provenance.as_dict(),
                                              "event": event_prov.as_dict()})
            payload = {"suite": "m2-contamination-340", "attachments": attachments,
                       "contamination": len(contamination), "examples": contamination[:5]}
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            (ROOT / "reports" / "m2_contamination_340.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return 0 if not contamination else 1
        finally:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
