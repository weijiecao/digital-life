"""Build a held-out synthetic V1.5 event-partition dataset.

The dataset is generated, not taken from the tuning examples. It is intended
to test generalization of the current prompt/validator, not to be a release
benchmark.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

DESTINATIONS = ["青岛", "苏州", "厦门", "成都", "西安", "杭州", "南京", "长沙", "武汉", "重庆"]
MUSEUMS = ["苏州博物馆", "南京博物院", "陕西历史博物馆", "上海科技馆", "浙江省博物馆",
          "湖北省博物馆", "湖南省博物馆", "成都博物馆", "厦门博物馆", "青岛啤酒博物馆"]
AREAS = ["市南区", "崂山区", "姑苏区", "思明区", "武侯区", "雁塔区", "西湖区", "鼓楼区",
         "岳麓区", "渝中区"]
HABITS = ["晨跑", "自己做饭", "早睡", "记账", "学日语", "练吉他", "喝温水", "写日记",
          "周末爬山", "睡前阅读"]
CODES = ["蓝鲸7391", "白鹭2048", "青柠1180", "北斗9032", "赤狐4471", "灰鲸6620",
         "银杉3319", "黄鹂7812", "黑曜5501", "绿洲8840"]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_scope(index: int, offset: int, layout: str = "interleaved") -> tuple[list[tuple[int, str, str]], list[dict]]:
    dest = DESTINATIONS[index % len(DESTINATIONS)]
    other = DESTINATIONS[(index + 3) % len(DESTINATIONS)]
    museum = MUSEUMS[index % len(MUSEUMS)]
    area = AREAS[index % len(AREAS)]
    habit = HABITS[index % len(HABITS)]
    code = CODES[index % len(CODES)]
    groups = [
        {"id": "trip_oct", "seqs": [], "texts": [
            "我计划十月去%s玩" % dest,
            "酒店想订在%s" % area,
            "预算大概三千",
        ]},
        {"id": "trip_dec", "seqs": [], "texts": [
            "十二月还想去%s" % dest,
            "这次想住%s附近" % area,
            "还没订票",
        ]},
        {"id": "museum", "seqs": [], "texts": [
            "上周去了%s" % museum,
            "排队等了四十分钟",
            "里面有个青铜器展",
        ]},
        {"id": "habit", "seqs": [], "texts": [
            "我最近开始%s" % habit,
            "每天坚持二十分钟",
            "感觉比之前好",
        ]},
    ]
    noise = [
        "今天有点冷",
        "哈哈",
        "你看这个",
        "我朋友说周末有空",
        "晚点再说",
        "外面下雨了",
        "刚吃完饭",
        "有点困",
    ]
    rows = []
    labels = []
    seq = offset + 1
    if layout == "contiguous":
        # Each event's three messages are adjacent, followed by two noise lines.
        for group_index, group in enumerate(groups):
            for text in group["texts"]:
                rows.append((seq, "synthetic-heldout-%02d" % index, text))
                group["seqs"].append(seq)
                seq += 1
            rows.append((seq, "synthetic-heldout-%02d" % index, noise[group_index * 2]))
            seq += 1
            rows.append((seq, "synthetic-heldout-%02d" % index, noise[group_index * 2 + 1]))
            seq += 1
    else:
        # Interleave one message from each group, then two noise messages per round.
        for round_index in range(3):
            for group in groups:
                text = group["texts"][round_index]
                rows.append((seq, "synthetic-heldout-%02d" % index, text))
                group["seqs"].append(seq)
                seq += 1
            rows.append((seq, "synthetic-heldout-%02d" % index, noise[round_index * 2]))
            seq += 1
            rows.append((seq, "synthetic-heldout-%02d" % index, noise[round_index * 2 + 1]))
            seq += 1
    for group in groups:
        labels.append({"id": group["id"], "seqs": group["seqs"]})
    return rows, labels


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--layout", choices=("interleaved", "contiguous"), default="interleaved")
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()
    root = Path(args.out_dir) if args.out_dir else Path(__file__).resolve().parent / "v1_5_heldout"
    if args.layout == "contiguous" and not args.out_dir:
        root = Path(__file__).resolve().parent / "v1_5_heldout_contiguous"
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "v1.sqlite3"
    if db_path.exists():
        db_path.unlink()
    db = sqlite3.connect(str(db_path))
    db.execute("""CREATE TABLE dl_capture(
        seq INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
        event_hash TEXT NOT NULL, content TEXT NOT NULL, content_sha TEXT NOT NULL,
        recorded_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending')""")
    scopes = []
    for index in range(10):
        rows, groups = make_scope(index, index * 20, args.layout)
        scope = "synthetic-heldout-%02d" % index
        for seq, _scope, text in rows:
            db.execute("""INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,
                recorded_at,state) VALUES(?,?,?,?,?,?,?)""",
                (seq, scope, "h%d" % seq, text, _sha(text),
                 "2026-11-%02dT10:00:00+00:00" % (index % 28 + 1), "pending"))
        excluded = sorted({seq for seq, _scope, _text in rows} -
                          {seq for group in groups for seq in group["seqs"]})
        scopes.append({"scope": scope, "groups": groups, "excluded": excluded})
    db.commit()
    db.close()
    labels = {"dataset": "v1.5-heldout-synthetic-%s" % args.layout, "scopes": scopes}
    (root / "labels.json").write_text(json.dumps(labels, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    (root / "README.md").write_text(
        "# V1.5 held-out synthetic dataset\n\n"
        "10 scopes × 20 messages × 4 event groups = 120 positive pairs.\\n"
        "Generated, not tuning examples; no real personal data.\\n", encoding="utf-8")
    print(json.dumps({"db": str(db_path), "scopes": len(scopes),
                      "messages": 200, "groups": 40, "positive_pairs": 120},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
