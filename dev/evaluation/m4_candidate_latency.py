"""M4 BGE/lexical candidate search latency (CPU)."""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v1_5_consolidator.candidate_retriever import (
    EventAnchor, HybridCandidateIndex, LexicalCandidateIndex)

MODEL = Path(__file__).resolve().parents[2] / "testbed" / "models" / "bge-small-zh-v1.5"
N = 1000
Q = 100


def run(index, label: str) -> dict:
    started = time.perf_counter()
    for i in range(N):
        text = "记忆%d 主题%d %s" % (i, i % 50, "茶" * (i % 7 + 1))
        index.add(EventAnchor("evt_%05d" % i, "s", "unknown", text, text,
                              version=1))
    build_ms = (time.perf_counter() - started) * 1000
    times = []
    for i in range(Q):
        t0 = time.perf_counter()
        index.search("s", "主题%d 茶" % (i % 50), k=10)
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    return {"label": label, "build_ms": round(build_ms, 2),
            "p50_ms": round(statistics.median(times), 3),
            "p95_ms": round(times[int(len(times) * 0.95) - 1], 3),
            "max_ms": round(max(times), 3)}


def main() -> int:
    lexical = LexicalCandidateIndex()
    result = {"n": N, "queries": Q, "lexical": run(lexical, "lexical")}
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(str(MODEL), device="cpu")
    hybrid = HybridCandidateIndex(model)
    result["bge"] = run(hybrid, "bge")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    Path(__file__).resolve().parent.parent.joinpath(
        "reports", "m4_candidate_latency.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
