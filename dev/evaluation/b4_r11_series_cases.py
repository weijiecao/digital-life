"""R11-7B 20-case story-continuity series (synthetic, non-placeholder IDs)."""

from __future__ import annotations

import json
from pathlib import Path

CASES = [
    # T01: same story, same event
    ("T01-S1", "same_story_same_event", "故事里他登上雪山，山顶风很大。",
     "故事里他登上雪山。", "fictional", "story-1", "SAME_EVENT", "YES"),
    ("T01-S2", "same_story_same_event", "小说主角在青岛开书店，后来搬走了。",
     "小说主角在青岛开书店。", "fictional", "story-2", "SAME_EVENT", "YES"),
    ("T01-S3", "same_story_same_event", "这个故事里，女孩在厦门海边捡到一只猫。",
     "这个故事里，女孩在厦门海边捡到一只猫，猫是橘色的。", "fictional", "story-3",
     "SAME_EVENT", "YES"),
    ("T01-S4", "same_story_same_event", "剧中的‘我’去了杭州，住了三天。",
     "剧中的‘我’去了杭州。", "fictional", "story-4", "SAME_EVENT", "YES"),
    # T02: same story, different event
    ("T02-S1", "same_story_different_event", "小说主角下山后遇到暴风雪。",
     "小说主角登上雪山。", "fictional", "story-1", "RELATED_CONTEXT", "YES"),
    ("T02-S2", "same_story_different_event", "故事里他后来去了成都。",
     "故事里他登上雪山。", "fictional", "story-1", "RELATED_CONTEXT", "YES"),
    ("T02-S3", "same_story_different_event", "同一个故事：主角后来去了成都。",
     "同一个故事：主角在青岛开了书店。", "fictional", "story-2",
     "RELATED_CONTEXT", "YES"),
    ("T02-S4", "same_story_different_event", "这个故事第二段：女孩离开厦门。",
     "这个故事第一段：女孩在厦门捡猫。", "fictional", "story-3",
     "RELATED_CONTEXT", "YES"),
    ("T02-S5", "same_story_same_event_update", "同一个故事：主角在青岛开了书店，后来搬走。",
     "同一个故事：主角在青岛开了书店。", "fictional", "story-2",
     "SAME_EVENT", "YES"),
    # T03: different stories
    ("T03-S1", "different_story", "另一个故事：主角在成都开公司。",
     "故事里他登上雪山。", "fictional", "story-9", "DIFFERENT_EVENT", "NO"),
    ("T03-S2", "different_story", "新故事：女孩在厦门。",
     "小说主角在青岛开书店。", "fictional", "story-8", "DIFFERENT_EVENT", "NO"),
    ("T03-S3", "different_story", "另一个故事：他去了西藏。",
     "剧中的‘我’去了杭州。", "fictional", "story-7", "DIFFERENT_EVENT", "NO"),
    ("T03-S4", "different_story", "另一个故事：她在成都开公司。",
     "故事里他登上雪山。", "fictional", "story-6", "DIFFERENT_EVENT", "NO"),
    # T04: narrative -> user real event (must not attach)
    ("T04-N1", "narrative_to_user", "我给你讲个故事：主人公去年去了雪山。",
     "我去年去了雪山。", "real", None, "DIFFERENT_EVENT", "NO"),
    ("T04-N2", "narrative_to_user", "小说用第一人称写：我推开门，走进雪山。",
     "我推开门，走进雪山。", "real", None, "DIFFERENT_EVENT", "NO"),
    ("T04-N3", "narrative_to_user", "朋友上周去了四川，爬了四姑娘山。",
     "我上周去了四川，爬了四姑娘山。", "real", None, "DIFFERENT_EVENT", "NO"),
    ("T04-N4", "narrative_to_user", "朋友给我讲了个故事，故事里他登上了雪山。",
     "我登上了雪山。", "real", None, "DIFFERENT_EVENT", "NO"),
    # T05: user real same event
    ("T05-U1", "user_real_same_event", "我上周去了苏州博物馆。",
     "我上周去了苏州博物馆，排队很久。", "real", None, "SAME_EVENT", "UNKNOWN"),
    ("T05-U2", "user_real_same_event", "我明年要搬去青岛。",
     "我计划明年搬去青岛。", "real", None, "SAME_EVENT", "UNKNOWN"),
    ("T05-U3", "user_real_same_event", "我昨天买了去成都的票。",
     "我昨天买了去成都的票，准备下周出发。", "real", None, "SAME_EVENT", "UNKNOWN"),
    ("T05-U4", "user_real_same_event", "我开始晨跑了。",
     "我最近开始晨跑。", "real", None, "SAME_EVENT", "UNKNOWN"),
]


def main() -> int:
    root = Path(__file__).resolve().parent / "b4_r11"
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for cid, category, story, candidate, reality, story_id, relation, same_story in CASES:
        rows.append({
            "case_id": cid, "category": category, "story_text": story,
            "candidate_text": candidate,
            "candidate_provenance": {
                "narrator": "user", "subject": "user" if reality == "real" else "story_character",
                "reality_status": reality, "story_id": story_id,
            },
            "expected_relation": relation, "same_story": same_story,
            "is_synthetic": True,
        })
    path = root / "series20.jsonl"
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                    encoding="utf-8")
    print(json.dumps({"cases": len(rows), "output": str(path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
