"""Positive/negative narrative continuity cases for ProvenanceGuard."""

from __future__ import annotations

import json
from pathlib import Path

CASES = [
    # Same story, different segments: should stay connected within the story.
    {"case_id": "POS-S1", "category": "same_story",
     "story_text": "故事里他登上雪山，山顶风很大。",
     "candidate_text": "故事里他登上雪山。",
     "candidate_provenance": {"narrator": "user", "subject": "story_character",
                              "reality_status": "fictional", "story_id": "story-1"},
     "expected": "SAME_EVENT"},
    {"case_id": "POS-S2", "category": "same_story",
     "story_text": "小说主角下山后遇到暴风雪。",
     "candidate_text": "小说主角登上雪山。",
     "candidate_provenance": {"narrator": "user", "subject": "story_character",
                              "reality_status": "fictional", "story_id": "story-2"},
     "expected": "SAME_EVENT"},
    # User real event, same event: should be allowed.
    {"case_id": "POS-U1", "category": "user_real",
     "story_text": "我上周去了苏州博物馆。",
     "candidate_text": "我上周去了苏州博物馆，排队很久。",
     "candidate_provenance": {"narrator": "user", "subject": "user",
                              "reality_status": "real", "story_id": None},
     "expected": "SAME_EVENT"},
    {"case_id": "POS-U2", "category": "user_real",
     "story_text": "我明年要搬去青岛。",
     "candidate_text": "我计划明年搬去青岛。",
     "candidate_provenance": {"narrator": "user", "subject": "user",
                              "reality_status": "real", "story_id": None},
     "expected": "SAME_EVENT"},
    # Different stories: must not merge.
    {"case_id": "NEG-S1", "category": "different_story",
     "story_text": "另一个故事：主角在成都开公司。",
     "candidate_text": "故事里他登上雪山。",
     "candidate_provenance": {"narrator": "user", "subject": "story_character",
                              "reality_status": "fictional", "story_id": "story-1"},
     "expected": "NOT_SAME_EVENT"},
    {"case_id": "NEG-S2", "category": "different_story",
     "story_text": "小说里的人物去了厦门。",
     "candidate_text": "小说主角登上雪山。",
     "candidate_provenance": {"narrator": "user", "subject": "story_character",
                              "reality_status": "fictional", "story_id": "story-2"},
     "expected": "NOT_SAME_EVENT"},
]


def main() -> int:
    root = Path(__file__).resolve().parent / "b4_r11"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "continuity_cases.jsonl"
    path.write_text("\n".join(json.dumps(case, ensure_ascii=False) for case in CASES) + "\n",
                    encoding="utf-8")
    print(json.dumps({"cases": len(CASES), "output": str(path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
