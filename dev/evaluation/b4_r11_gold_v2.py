"""R11-7A gold v2: separate story membership from event identity."""

from __future__ import annotations

import json
from pathlib import Path

CASES = [
    {"case_id": "POS-S1", "category": "same_story_same_event",
     "story_text": "故事里他登上雪山，山顶风很大。",
     "candidate_text": "故事里他登上雪山。",
     "candidate_provenance": {"narrator": "user", "subject": "story_character",
                              "reality_status": "fictional", "story_id": "story-1"},
     "same_story": "YES", "same_event": "YES", "expected_relation": "SAME_EVENT",
     "gold_rationale": "同一故事、同一登山情节的补充细节。"},
    {"case_id": "POS-S2", "category": "same_story_different_event",
     "story_text": "小说主角下山后遇到暴风雪。",
     "candidate_text": "小说主角登上雪山。",
     "candidate_provenance": {"narrator": "user", "subject": "story_character",
                              "reality_status": "fictional", "story_id": "story-2"},
     "same_story": "YES", "same_event": "NO", "expected_relation": "RELATED_CONTEXT",
     "gold_rationale": "同一故事的前后不同情节；不能合并为同一事件。"},
    {"case_id": "POS-U1", "category": "user_real_same_event",
     "story_text": "我上周去了苏州博物馆。",
     "candidate_text": "我上周去了苏州博物馆，排队很久。",
     "candidate_provenance": {"narrator": "user", "subject": "user",
                              "reality_status": "real", "story_id": None},
     "same_story": "UNKNOWN", "same_event": "YES", "expected_relation": "SAME_EVENT",
     "gold_rationale": "用户真实自述，同一次参观。"},
    {"case_id": "POS-U2", "category": "user_real_same_event",
     "story_text": "我明年要搬去青岛。",
     "candidate_text": "我计划明年搬去青岛。",
     "candidate_provenance": {"narrator": "user", "subject": "user",
                              "reality_status": "real", "story_id": None},
     "same_story": "UNKNOWN", "same_event": "YES", "expected_relation": "SAME_EVENT",
     "gold_rationale": "用户真实计划，同一事件。"},
    {"case_id": "NEG-S1", "category": "different_story",
     "story_text": "另一个故事：主角在成都开公司。",
     "candidate_text": "故事里他登上雪山。",
     "candidate_provenance": {"narrator": "user", "subject": "story_character",
                              "reality_status": "fictional", "story_id": "story-1"},
     "same_story": "NO", "same_event": "NO", "expected_relation": "DIFFERENT_EVENT",
     "gold_rationale": "不同故事，地点与情节均不同。"},
    {"case_id": "NEG-S2", "category": "different_story",
     "story_text": "小说里的人物去了厦门。",
     "candidate_text": "小说主角登上雪山。",
     "candidate_provenance": {"narrator": "user", "subject": "story_character",
                              "reality_status": "fictional", "story_id": "story-2"},
     "same_story": "NO", "same_event": "NO", "expected_relation": "DIFFERENT_EVENT",
     "gold_rationale": "不同故事，角色与地点均不同。"},
]


def main() -> int:
    root = Path(__file__).resolve().parent / "b4_r11"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "gold_v2.jsonl"
    path.write_text("\n".join(json.dumps(case, ensure_ascii=False) for case in CASES) + "\n",
                    encoding="utf-8")
    print(json.dumps({"cases": len(CASES), "output": str(path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
