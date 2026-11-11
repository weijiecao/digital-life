"""Freeze the independent RC3 quality groups before any engine run.

The groups are authored scenario families, not real chats.  Dev and holdout
are disjoint; the manifest fixes the split SHA-256 and the pass criteria before
the engine is evaluated.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "quality_v2"
USER = {"narrator": "user", "subject": "user", "reality_status": "real", "story_id": None}
FRIEND = {"narrator": "user", "subject": "friend", "reality_status": "real", "story_id": None}
FAMILY = {"narrator": "user", "subject": "family", "reality_status": "real", "story_id": None}
FICTION = {"narrator": "user", "subject": "story_character",
           "reality_status": "fictional", "story_id": "story-1"}


def group(name, split, initial, current, deleted, withdrawn, other, provenance, queries):
    return {"group": name, "split": split, "messages": {
        "initial": initial, "current": current, "deleted": deleted,
        "withdrawn": withdrawn, "other_scope": other},
        "initial_provenance": provenance,
        "queries": [{"case": f"{name}:{category}:{i}", "category": category,
                     "query": query, "expected": expected}
                    for i, (category, query, expected) in enumerate(queries, 1)]}


def data():
    dev = [
        group("trip", "dev",
              "周日去木桥市集，暂定南入口。",
              "周六去木桥市集，最终确认西入口。",
              "撤回便签木桥市集，临时方案北入口。",
              "已废弃选项木桥市集，备份方案东入口。",
              "隔壁组专属木桥市集，集合点西入口。", USER,
              [("subject_self", "我周六去木桥市集走哪个入口", "current"),
               ("subject_other", "我朋友周六去木桥市集走哪个入口", None),
               ("story_wrong", "故事里的角色周六去木桥市集走哪个入口", None),
               ("time_current", "现在木桥市集定在周六吗", "current"),
               ("time_old", "原来木桥市集定在周日吗", None),
               ("correction", "木桥市集改成哪个入口了", "current"),
               ("withdrawal", "撤回便签木桥市集", None),
               ("no_answer", "木桥市集的付款凭证", None),
               ("paraphrase", "木拱桥边的周末聚会走哪个入口", "current")]),
        group("friend", "dev",
              "朋友周六去医院复查，上午九点。",
              "朋友周六去医院复查，最终确认上午十点。",
              "撤回便签朋友复查，临时方案下午。",
              "已废弃选项朋友复查，备份方案下午三点。",
              "隔壁组专属朋友复查，上午八点。", FRIEND,
              [("subject_friend", "朋友周六什么时候去医院复查", "current"),
               ("subject_self", "我周六什么时候去医院复查", None),
               ("story_wrong", "故事里的角色周六什么时候去医院复查", None),
               ("correction", "朋友复查改成了几点", "current"),
               ("no_answer", "朋友复查的付款凭证", None),
               ("paraphrase", "好友周末去诊所复诊的时间", "current")]),
        group("fiction", "dev",
              "故事里的角色林夏周六去木桥市集，暂定南入口。",
              "故事里的角色林夏周六去木桥市集，最终确认西入口。",
              "撤回便签故事角色，临时方案北入口。",
              "已废弃选项故事角色，备份方案东入口。",
              "隔壁组专属故事角色，集合点西入口。", FICTION,
              [("story", "故事里的角色林夏周六去木桥市集走哪个入口", "current"),
               ("reality_wrong", "真实的我周六去木桥市集走哪个入口", None),
               ("subject_wrong", "我周六去木桥市集走哪个入口", None),
               ("no_answer", "故事角色的付款凭证", None),
               ("paraphrase", "虚构故事角色周末去集市的入口", "current")]),
        group("meeting", "dev",
              "会议改到周二下午三点。",
              "会议改到周四下午四点。",
              "撤回便签会议，临时方案周五。",
              "已废弃选项会议，备份方案周一。",
              "隔壁组专属会议，周二上午。", USER,
              [("time_current", "现在会议在周四几点", "current"),
               ("time_old", "原来会议在周二几点", None),
               ("correction", "会议改成了周四吗", "current"),
               ("withdrawal", "已废弃选项会议", None),
               ("no_answer", "会议纪要编号", None),
               ("paraphrase", "开会时间改到周四下午", "current")]),
    ]
    holdout = [
        group("family", "holdout",
              "妈妈周日去银行，上午十点。",
              "妈妈周日去银行，最终确认上午十一点。",
              "撤回便签妈妈银行，临时方案下午。",
              "已废弃选项妈妈银行，备份方案周一。",
              "隔壁组专属妈妈银行，上午九点。", FAMILY,
              [("subject_family", "妈妈周日去银行几点", "current"),
               ("subject_wrong", "我周日去银行几点", None),
               ("time_current", "现在妈妈去银行是周日上午十一点吗", "current"),
               ("time_old", "原来妈妈去银行是周日上午十点吗", None),
               ("correction", "妈妈去银行改成了几点", "current"),
               ("withdrawal", "撤回便签妈妈银行", None),
               ("no_answer", "妈妈银行的付款凭证", None),
               ("paraphrase", "母亲周末去金融机构的时间", "current")]),
        group("repair", "holdout",
              "单车检修约在周三，暂定东门。",
              "单车检修约在周五，最终确认西门。",
              "撤回便签单车检修，临时方案北门。",
              "已废弃选项单车检修，备份方案南门。",
              "隔壁组专属单车检修，东门。", USER,
              [("time_current", "现在单车检修在周五吗", "current"),
               ("time_old", "原来单车检修在周三吗", None),
               ("correction", "单车检修改到哪个门了", "current"),
               ("withdrawal", "备份方案单车检修", None),
               ("no_answer", "单车检修的付款凭证", None),
               ("paraphrase", "自行车送修的最终地点", "current")]),
        group("story2", "holdout",
              "故事里的角色阿岚周四去海边，暂定北岸。",
              "故事里的角色阿岚周四去海边，最终确认南岸。",
              "撤回便签故事角色阿岚，临时方案东岸。",
              "已废弃选项故事角色阿岚，备份方案西岸。",
              "隔壁组专属故事角色阿岚，北岸。", FICTION,
              [("story", "故事里的角色阿岚周四去海边哪里", "current"),
               ("reality_wrong", "真实的我周四去海边哪里", None),
               ("subject_wrong", "我周四去海边哪里", None),
               ("no_answer", "故事角色的付款凭证", None),
               ("paraphrase", "虚构人物周末去海边的地点", "current")]),
        group("move", "holdout",
              "搬家改到周六上午，暂定三楼。",
              "搬家改到周日上午，最终确认五楼。",
              "撤回便签搬家，临时方案一楼。",
              "已废弃选项搬家，备份方案二楼。",
              "隔壁组专属搬家，三楼。", USER,
              [("time_current", "现在搬家在周日上午吗", "current"),
               ("time_old", "原来搬家在周六上午吗", None),
               ("correction", "搬家改到几楼了", "current"),
               ("withdrawal", "已废弃选项搬家", None),
               ("no_answer", "搬家的联系人号码", None),
               ("paraphrase", "搬迁最终安排在几楼", "current")]),
    ]
    return dev, holdout


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    dev, holdout = data()
    paths = {"dev": OUT / "dev.json", "holdout": OUT / "holdout.json"}
    for split, groups in (("dev", dev), ("holdout", holdout)):
        paths[split].write_text(json.dumps(groups, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
    manifest = {
        "version": "quality-v2-rc3",
        "frozen_at": "2026-11-12",
        "source": "single-author authored scenario families; not real chats or blind human labels",
        "split_rule": "disjoint scenario families; one group is the statistical unit",
        "criteria": {
            "positive_top1_min": 0.95,
            "positive_top5_min": 0.95,
            "negative_context_injection_max": 0,
            "invalid_context_injection_max": 0,
            "safety_categories_zero_injection": True,
            "paraphrase_top1_min": 0.80,
            "no_answer_injection_max": 0,
        },
        "splits": {split: {
            "groups": len(groups),
            "sha256": hashlib.sha256(paths[split].read_bytes()).hexdigest(),
            "queries": sum(len(g["queries"]) for g in groups),
            "negative": sum(q["expected"] is None for g in groups for q in g["queries"]),
        } for split, groups in (("dev", dev), ("holdout", holdout))},
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
