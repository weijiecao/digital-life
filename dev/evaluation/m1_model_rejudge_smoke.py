"""One-source real-model pending rejudge smoke (M1).

Calls the configured model through RelationJudge, then the actual Resolver.
Synthetic input only; one request; not a stability claim.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory_engine.rejudge import PendingRejudger
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.gateway import ContextGateway, OpenAIProvider
from v1_5_consolidator.relation_judge import RelationJudge
from v1_5_consolidator.store import V15Store


def main() -> int:
    key = Path(r"C:\Users\205\Desktop\数字生命\testbed\instance\data\.deepseek_key")
    provider = OpenAIProvider("https://api.deepseek.com/v1", "deepseek-chat",
                              key.read_text(encoding="utf-8").strip())
    with tempfile.TemporaryDirectory() as d:
        store = V15Store(str(Path(d) / "v15.sqlite3"))
        seed = SourceEnvelope("s", 99, "sha", "t", "周末去爬山")
        event_id = store.create_event_from_source(
            "s", seed, event_type="unknown", title="周末去爬山",
            provenance={"narrator": "user", "subject": "user",
                        "reality_status": "real", "story_id": None})
        source = SourceEnvelope("s", 1, "sha", "t", "周末和朋友去爬山")
        store.save_pending("s", 1, [{"candidate_event_id": event_id,
                                     "label": "SAME_EVENT"}],
                           {event_id: [event_id]}, "initial", source=source)
        store.enqueue_ready("s", 1, "model-recheck")
        judge = RelationJudge(ContextGateway(store, deny_external=False), provider)

        def evaluate(scope, src):
            candidate = {"event_id": event_id, "event_type": "unknown",
                         "title": "周末去爬山", "anchor_text": "周末去爬山"}
            return judge.judge(scope, src, [candidate])

        result = PendingRejudger(store, evaluate).run_once("s")
        payload = {"suite": "m1-model-rejudge-smoke", "result": result,
                   "attached": store.events_for_source("s", 1),
                   "pending": store.pending_for_source("s", 1)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        Path(r"C:\Users\205\Desktop\数字生命\dev\reports\m1_model_rejudge_smoke.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
