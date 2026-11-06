"""真实消息上的“有无大模型介入”检索准确率对比。

数据：testbed 真实采集库的只读副本（默认取 27 条真实私聊所在的 scope）。
查询：从真实消息内容人工构造的指代/追问（不是改写原话）。
对比：
  baseline = Store.context_packet（本地字面/待审/背景，不调用 LLM）
  judge    = baseline 未命中目标时，用真实 DeepSeek 从候选里选，再看选中的原话是否含目标

用法：
  python evaluation/real_messages_judge_eval.py \
    --db ../testbed/instance/data/plugin_data/astrbot_plugin_digital_memory_store/manual_test_memory.sqlite3 \
    --api-key-file ../testbed/instance/data/.deepseek_key \
    --out ../dev/reports/real_messages_judge_eval.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEV = Path(__file__).resolve().parent.parent
if str(DEV) not in sys.path:
    sys.path.insert(0, str(DEV))

from astrbot_plugin_digital_memory_store.storage import Store  # noqa: E402
from astrbot_plugin_digital_memory_store.model_judge import (  # noqa: E402
    build_prompt, filter_selection, parse_reply)

# 目标来自真实消息原文；只写最小可判定片段，不复制整段聊天。
SCENARIOS = [
    {'id': 'weekday', 'query': '今天星期几？', 'targets': ['星期三']},
    {'id': 'thursday_plan', 'query': '明天疯狂星期四要不要吃麦当劳？', 'targets': ['麦当劳']},
    {'id': 'chocolate_pork', 'query': '明天吃什么锅包肉？', 'targets': ['巧克力锅包肉']},
    {'id': 'delivery_or_store', 'query': '明天点外卖还是到店吃？', 'targets': ['外卖', '到店']},
    {'id': 'mixue_buy', 'query': '明天去蜜雪冰城买什么？', 'targets': ['星冰乐']},
    {'id': 'tomorrow_eat', 'query': '明天吃什么？', 'targets': ['内牛五方']},
    {'id': 'niuwu', 'query': '麦当劳的内牛五方还是别的？', 'targets': ['内牛五方']},
    {'id': 'blue_code', 'query': '蓝鲸的暗号是什么？', 'targets': ['蓝鲸7391']},
    {'id': 'code_short', 'query': '暗号是多少？', 'targets': ['7391']},
    {'id': 'fruit', 'query': '我喜欢吃什么水果？', 'targets': ['苹果']},
    {'id': 'apple_yes', 'query': '我喜欢吃苹果吗？', 'targets': ['苹果']},
    {'id': 'move_when', 'query': '我什么时候搬家？', 'targets': ['下个月十五号']},
    {'id': 'move_where', 'query': '我搬到哪里？', 'targets': ['西湖区', '滨江区']},
    {'id': 'move_binjiang', 'query': '搬家改到哪个区？', 'targets': ['滨江区']},
    {'id': 'move_old_thing', 'query': '之前搬到杭州西湖区那件事怎么样？', 'targets': ['西湖区', '滨江区']},
    {'id': 'move_final', 'query': '搬家最后定在哪？', 'targets': ['滨江区']},
    {'id': 'later_mix', 'query': '后来呢？', 'targets': ['滨江区', '7391', '内牛五方']},
    {'id': 'that_thing', 'query': '那件事后来怎么样了？', 'targets': ['滨江区', '西湖区', '内牛五方']},
    {'id': 'that_thing_2', 'query': '之前那件事呢？', 'targets': ['滨江区', '西湖区', '内牛五方']},
    {'id': 'pizza', 'query': '我更喜欢吃蜜雪冰城的什么？', 'targets': ['披萨']},
    {'id': 'mixue_or_mcd', 'query': '我喜欢蜜雪冰城还是麦当劳？', 'targets': ['蜜雪冰城', '麦当劳']},
    {'id': 'lime_code', 'query': '青柠计划的门禁暗号是什么？', 'targets': ['7391']},
    {'id': 'lime_code_short', 'query': '青柠计划的暗号是多少？', 'targets': ['7391']},
    {'id': 'code_what', 'query': '门禁暗号是多少？', 'targets': ['7391']},
    {'id': 'thursday_eat', 'query': '疯狂星期四吃什么？', 'targets': ['麦当劳']},
    {'id': 'tomorrow_drink', 'query': '明天喝什么？', 'targets': ['星冰乐']},
    {'id': 'pork_flavor', 'query': '锅包肉是什么口味？', 'targets': ['巧克力']},
    {'id': 'fifteenth_plan', 'query': '我下个月十五号做什么？', 'targets': ['搬家']},
    {'id': 'blue_what', 'query': '蓝鲸7391是什么？', 'targets': ['暗号', '7391']},
    {'id': 'apple_past', 'query': '我之前说喜欢吃什么？', 'targets': ['苹果']},
    {'id': 'pizza_shop', 'query': '我更喜欢的披萨是哪家的？', 'targets': ['蜜雪冰城']},
    {'id': 'niuwu_shop', 'query': '内牛五方是哪家店的？', 'targets': ['麦当劳']},
    {'id': 'move_hangzhou', 'query': '我搬家到杭州哪个区？', 'targets': ['西湖区']},
    {'id': 'move_binjiang_yes', 'query': '我搬家到滨江区了吗？', 'targets': ['滨江区']},
    {'id': 'buy_first', 'query': '明天先买什么？', 'targets': ['星冰乐']},
    {'id': 'delivery_again', 'query': '到店吃还是点外卖？', 'targets': ['外卖', '到店']},
    {'id': 'blue_short', 'query': '暗号蓝鲸是多少？', 'targets': ['7391', '蓝鲸7391']},
    {'id': 'code_before', 'query': '之前说的门禁暗号？', 'targets': ['7391']},
    {'id': 'move_old_where', 'query': '之前搬家到哪个区？', 'targets': ['西湖区', '滨江区']},
    {'id': 'food_tomorrow', 'query': '我明天到底吃什么？', 'targets': ['内牛五方', '麦当劳']},
]


NEGATIVES = [
    {'id': 'neg_cat', 'query': '我养的是什么猫？'},
    {'id': 'neg_dog', 'query': '我家狗叫什么名字？'},
    {'id': 'neg_japan', 'query': '我上次去日本住在哪？'},
    {'id': 'neg_car', 'query': '我的车是什么颜色？'},
    {'id': 'neg_birthday', 'query': '我生日是哪天？'},
    {'id': 'neg_phone', 'query': '我电话号码是多少？'},
    {'id': 'neg_company', 'query': '我公司在哪？'},
    {'id': 'neg_school', 'query': '我大学在哪读的？'},
    {'id': 'neg_movie', 'query': '我最喜欢哪部电影？'},
    {'id': 'neg_yesterday', 'query': '我昨天买了什么？'},
]


def build_probes():
    """40 个基础场景 + 每个场景一个“你还记得吗？”前缀探针（共 80 个探针）。

    注意：前缀探针不是独立真实问题，只用于扩大探针量、观察问法变化；
    汇总会同时给出 base 40 和全部 80 的结果。
    """
    probes = []
    for scenario in SCENARIOS:
        probes.append(dict(scenario, variant='base'))
        probes.append(dict(scenario, id=scenario['id'] + '_recall',
                           query='你还记得吗？' + scenario['query'], variant='prefix'))
    return probes


def hit(text, targets):
    return any(target in (text or '') for target in targets)


def call_model(base_url, model, key, prompt, timeout=90):
    body = json.dumps({
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0,
        'max_tokens': 120,
        'stream': False,
    }).encode('utf-8')
    request = urllib.request.Request(
        base_url.rstrip('/') + '/chat/completions',
        data=body,
        headers={'Content-Type': 'application/json',
                 'Authorization': 'Bearer ' + key},
        method='POST')
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode('utf-8'))
    return payload['choices'][0]['message']['content'], payload.get('usage')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--api-key-file', required=True)
    parser.add_argument('--base-url', default='https://api.deepseek.com/v1')
    parser.add_argument('--model', default='deepseek-chat')
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)

    key = Path(args.api_key_file).read_text(encoding='utf-8').strip()
    src = Path(args.db)
    with tempfile.TemporaryDirectory(prefix='real-judge-') as tmp:
        tmp = Path(tmp)
        target_db = tmp / 'manual_test_memory.sqlite3'
        shutil.copy2(src, target_db)
        conn = sqlite3.connect('file:%s?mode=ro&immutable=1' % target_db.as_posix(), uri=True)
        try:
            conn.row_factory = sqlite3.Row
            scope = conn.execute(
                'SELECT scope,COUNT(*) c FROM dl_capture GROUP BY scope ORDER BY c DESC LIMIT 1'
            ).fetchone()['scope']
            recent = [row['content'] for row in conn.execute(
                'SELECT content FROM dl_capture WHERE scope=? ORDER BY seq DESC LIMIT 8',
                (scope,)).fetchall()]
        finally:
            conn.close()
        recent.reverse()
        store = Store(str(tmp), '')
        store.pending_context_enabled = True
        store.always_context_enabled = True
        store.weak_association_enabled = False
        records = []
        probes = build_probes()
        try:
            for index, scenario in enumerate(probes, 1):
                started = time.perf_counter()
                packet = store.context_packet(
                    scope, scenario['query'], budget=450,
                    current_message_id='real-q-%d' % index,
                    as_of=datetime.now(timezone.utc))
                baseline_hit = hit(packet, scenario['targets'])
                called = False
                judge_hit = baseline_hit
                selected = []
                usage = None
                reply = ''
                if not baseline_hit:
                    candidates = store.judge_candidates(scope, scenario['query'], limit=5)
                    if len(candidates) >= 2:
                        called = True
                        prompt = build_prompt(scenario['query'], recent[-8:], candidates)
                        reply, usage = call_model(
                            args.base_url, args.model, key, prompt)
                        parsed = parse_reply(reply, len(candidates))
                        if parsed and parsed['choices']:
                            chosen = [candidates[i - 1] for i in parsed['choices']]
                            chosen = filter_selection(scenario['query'], chosen)
                            if chosen:
                                selected = [item['content'] for item in chosen]
                                judge_hit = hit('\n'.join(selected), scenario['targets'])
                records.append({
                    'id': scenario['id'], 'variant': scenario.get('variant', 'base'),
                    'query': scenario['query'],
                    'targets': scenario['targets'],
                    'baseline_hit': baseline_hit,
                    'judge_hit': judge_hit,
                    'judge_called': called,
                    'selected': selected,
                    'model_reply': reply,
                    'usage': usage,
                    'elapsed_s': round(time.perf_counter() - started, 3),
                })
                print('%-12s baseline=%-5s judge=%-5s called=%-5s' % (
                    scenario['id'], baseline_hit, judge_hit, called))
                time.sleep(0.2)
            for index, scenario in enumerate(NEGATIVES, 1):
                started = time.perf_counter()
                packet = store.context_packet(
                    scope, scenario['query'], budget=450,
                    current_message_id='real-neg-%d' % index,
                    as_of=datetime.now(timezone.utc))
                candidates = store.judge_candidates(scope, scenario['query'], limit=5)
                called = False
                selected = []
                reply = ''
                usage = None
                if len(candidates) >= 2:
                    called = True
                    prompt = build_prompt(scenario['query'], recent[-8:], candidates)
                    reply, usage = call_model(
                        args.base_url, args.model, key, prompt)
                    parsed = parse_reply(reply, len(candidates))
                    if parsed and parsed['choices']:
                        chosen = [candidates[i - 1] for i in parsed['choices']]
                        chosen = filter_selection(scenario['query'], chosen)
                        selected = [item['content'] for item in chosen]
                records.append({
                    'id': scenario['id'], 'variant': 'negative', 'negative': True,
                    'query': scenario['query'], 'targets': [],
                    'baseline_hit': False,
                    'judge_hit': not selected,
                    'judge_called': called,
                    'selected': selected,
                    'model_reply': reply,
                    'usage': usage,
                    'elapsed_s': round(time.perf_counter() - started, 3),
                })
                print('%-12s negative selected=%-5s called=%-5s' % (
                    scenario['id'], bool(selected), called))
                time.sleep(0.2)
        finally:
            store.close()

    positives = [r for r in records if not r.get('negative')]
    negatives = [r for r in records if r.get('negative')]
    both = sum(1 for r in positives if r['baseline_hit'])
    judged = sum(1 for r in positives if r['judge_hit'])
    called = sum(1 for r in positives if r['judge_called'])
    missed = [r for r in positives if not r['baseline_hit']]
    recovered = [r for r in missed if r['judge_hit']]
    base_records = [r for r in positives if r['variant'] == 'base']
    base_both = sum(1 for r in base_records if r['baseline_hit'])
    base_judged = sum(1 for r in base_records if r['judge_hit'])
    neg_false = sum(1 for r in negatives if r['selected'])
    neg_called = sum(1 for r in negatives if r['judge_called'])
    payload = {
        'suite': 'real-messages-judge-eval-v3',
        'source_db': str(src),
        'scope': scope,
        'model': args.model,
        'scenarios': len(base_records),
        'probes': len(positives),
        'baseline_accuracy': '%d/%d' % (both, len(positives)),
        'judge_accuracy': '%d/%d' % (judged, len(positives)),
        'base_baseline_accuracy': '%d/%d' % (base_both, len(base_records)),
        'base_judge_accuracy': '%d/%d' % (base_judged, len(base_records)),
        'judge_calls': called,
        'baseline_misses': len(missed),
        'recovered': len(recovered),
        'negative_probes': len(negatives),
        'negative_calls': neg_called,
        'negative_false_selections': neg_false,
        'records': records,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: payload[k] for k in (
        'scenarios', 'probes', 'baseline_accuracy', 'judge_accuracy',
        'base_baseline_accuracy', 'base_judge_accuracy',
        'judge_calls', 'baseline_misses', 'recovered',
        'negative_probes', 'negative_calls', 'negative_false_selections')},
        ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
