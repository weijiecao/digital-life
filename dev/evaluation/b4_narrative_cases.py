"""Synthetic R11 A-F narrative cases for offline attribution probing.

These are adversarial templates, not real user data. Each case has a candidate
that represents a *user real event*; the query is narrative/third-party text
that must not be attached to that user event.
"""

from __future__ import annotations

import json
from pathlib import Path

TEMPLATES = {
    "R11-A": [
        ("我给你讲个故事：主人公去年去了雪山，还在山顶露营。", "我去年去了雪山，还在山顶露营。"),
        ("这个故事里，主角在青岛海边开了一家书店。", "我在青岛海边开了一家书店。"),
        ("小说里的人物搬到了杭州西湖区。", "我搬到了杭州西湖区。"),
        ("我编了一个故事，主角在成都吃了火锅。", "我在成都吃了火锅。"),
        ("故事的主人公养了一只叫团子的猫。", "我养了一只叫团子的猫。"),
    ],
    "R11-B": [
        ("小说用第一人称写：我推开门，走进雪山。", "我推开门，走进雪山。"),
        ("故事里的‘我’最后离开了杭州。", "我最后离开了杭州。"),
        ("第一人称虚构：我三年前在苏州工作。", "我三年前在苏州工作。"),
        ("这本书的‘我’其实是个虚构角色，住在厦门。", "我住在厦门。"),
        ("剧本第一人称：‘我’在成都开了一家公司。", "我在成都开了一家公司。"),
    ],
    "R11-C": [
        ("朋友上周去了四川，爬了四姑娘山。", "我上周去了四川，爬了四姑娘山。"),
        ("同事说他明年要搬去青岛。", "我明年要搬去青岛。"),
        ("我妈昨天去看了苏州博物馆。", "我昨天去看了苏州博物馆。"),
        ("朋友在杭州开了一家咖啡店。", "我在杭州开了一家咖啡店。"),
        ("同学今年去了日本留学。", "我今年去了日本留学。"),
    ],
    "R11-D": [
        ("朋友给我讲了个故事，故事里他登上了雪山。", "我登上了雪山。"),
        ("他说：‘她告诉我，她在青岛买了房。’", "我在青岛买了房。"),
        ("老王转述小李的话：小李明年要辞职。", "我明年要辞职。"),
        ("故事中的角色又在讲另一个故事：主角去了厦门。", "我去了厦门。"),
        ("朋友说，他朋友在成都开了一家公司。", "我在成都开了一家公司。"),
    ],
    "R11-E": [
        ("刚才那段是编的，不是真实经历。", "我去年在雪山露营。"),
        ("上面那个故事是我瞎编的，别当真。", "我在青岛买了房。"),
        ("那不是我，是小说里的角色。", "我搬到了杭州。"),
        ("我收回刚才的话，那是虚构的。", "我明年要辞职。"),
        ("这不是我的经历，是我朋友的故事。", "我在成都吃了火锅。"),
    ],
    "R11-F": [
        ("故事里他登上雪山，山顶风很大。", "故事里他登上雪山。"),
        ("小说主角下山后遇到暴风雪。", "小说主角登上雪山。"),
        ("故事中的‘我’在青岛开书店，后来搬走了。", "故事中的‘我’在青岛开书店。"),
        ("朋友讲的故事：主角在成都开公司，后来倒闭。", "朋友讲的故事：主角在成都开公司。"),
        ("这本小说里，主人公去了厦门，又回到杭州。", "这本小说里，主人公去了厦门。"),
    ],
}


def main() -> int:
    root = Path(__file__).resolve().parent / "b4_r11"
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    index = 1
    for category, cases in TEMPLATES.items():
        for story, user_event in cases:
            rows.append({"case_id": "R11-%03d" % index, "category": category,
                         "story_text": story, "user_event_text": user_event,
                         "expected_relation": "NOT_SAME_EVENT",
                         "is_synthetic": True})
            index += 1
    (root / "cases.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8")
    print(json.dumps({"cases": len(rows), "output": str(root / "cases.jsonl")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
