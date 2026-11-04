"""5,000 条规模基准：真实 BGE 下测写入、整理、首次查询与 P50/P95/P99 延迟。

语料为合成数据（唯一实体 + 固定句式），只用于测规模与延迟，不代表召回质量。
输出 reports/scale_5000.json。

用法：
    python evaluation/scale_bench.py --model-dir ../testbed/models/bge-small-zh-v1.5 --count 5000
"""

import argparse
import json
import random
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from m2_runner import _factory, configure  # noqa: E402
from astrbot_plugin_digital_memory_store.auto_organize import process_jobs  # noqa: E402
from astrbot_plugin_digital_memory_store.storage import Store  # noqa: E402

ADJECTIVES = ['青柠', '云杉', '白桦', '赤松', '紫藤', '金桂', '银杉', '墨竹', '碧桃', '红枫',
              '翠柏', '青松', '白杨', '绿柳', '黄杏', '蓝莓', '橙柚', '灰鸽', '粉樱', '黑檀',
              '暖阳', '冷月', '清晨', '傍晚', '春分', '夏至', '秋分', '冬至', '南风', '北雪',
              '东海', '西山', '长河', '小溪', '高岭', '深谷', '平湖', '远帆', '近岸', '孤岛',
              '繁星', '明月', '薄雾', '浓云', '细雨', '轻雷', '微光', '静水', '暗香', '疏影']
NOUNS = ['计划', '报告', '方案', '清单', '预算', '行程', '合同', '图纸', '样本', '提案',
         '复盘', '排期', '流程', '手册', '看板', '报表', '项目', '任务', '活动', '聚会',
         '旅行', '课程', '考试', '面试', '体检', '搬家', '装修', '采购', '报销', '还款',
         '缴费', '预约', '挂号', '复诊', '健身', '训练', '比赛', '演出', '展览', '电影',
         '聚餐', '露营', '钓鱼', '爬山', '骑行', '游泳', '滑雪', '摄影', '读书', '写作',
         '翻译', '编程', '设计', '测试', '上线', '发布', '培训', '会议', '出差', '值班',
         '排班', '盘点', '审计', '结算', '对账', '开票', '寄件', '收件', '退货', '换货',
         '维修', '保养', '年检', '上牌', '过户', '看房', '租房', '买房', '卖房', '签约',
         '续约', '解约', '投保', '理赔', '年审', '申报', '登记', '备案', '审批', '招标',
         '投标', '验收', '交付', '总结', '汇报', '路演', '沙龙', '团建', '年会', '生日']
SCOPES = 20


def entity_pool():
    """50 × 100 = 5,000 个互不共享四字片段的两两组合。"""
    return [adjective + noun for adjective in ADJECTIVES for noun in NOUNS]


def build_corpus(count):
    pool = entity_pool()
    rows = []
    per_scope = count // SCOPES
    for index in range(count):
        scope_index = index // per_scope
        entity = pool[index % len(pool)]
        text = '关于%s的安排定在周五下午三点' % entity
        stamp = '2025-03-%02dT%02d:%02d:00+00:00' % (
            1 + (index // 40) % 28, (index // 2) % 24, (index * 7) % 60)
        rows.append((scope_index, '%d-%d' % (scope_index, index), text, stamp))
    return rows


def rss_mb():
    try:
        import psutil
        return round(psutil.Process().memory_info().rss / 1024 / 1024, 1)
    except ImportError:
        return None


def percentile(values, pct):
    ordered = sorted(values)
    if not ordered:
        return None
    position = min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1))))
    return round(ordered[position], 2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-dir', default='')
    parser.add_argument('--count', type=int, default=5000)
    parser.add_argument('--queries', type=int, default=50)
    parser.add_argument('--out', default=str(ROOT / 'reports' / 'scale_5000.json'))
    args = parser.parse_args()

    rows = build_corpus(args.count)
    per_scope = args.count // SCOPES
    report = {'suite': 'scale-%d' % args.count, 'count': args.count,
              'scopes': SCOPES, 'model': bool(args.model_dir)}
    with tempfile.TemporaryDirectory(prefix='scale-') as d:
        store = Store(d, args.model_dir, _factory(args.model_dir))
        configure(store, args.model_dir)
        # 合成语料模板高度相似，会自动触发主题/跨事件分支的保守阻断；
        # 这里只测候选与召回管线本身，因此关闭自动主题分支（不影响 pending/语义候选）。
        store.automatic_memory_enabled = False
        started = time.perf_counter()
        for scope_index, message_id, text, stamp in rows:
            scope = store.scope(str(scope_index), 'scale')
            store.capture(scope, message_id, text, recorded_at=stamp)
        report['seed_s'] = round(time.perf_counter() - started, 2)
        report['rss_after_seed_mb'] = rss_mb()

        started = time.perf_counter()
        for scope_index in range(SCOPES):
            process_jobs(store.flow.db)
        report['jobs_s'] = round(time.perf_counter() - started, 2)
        report['rss_after_jobs_mb'] = rss_mb()

        scopes = [store.scope(str(i), 'scale') for i in range(SCOPES)]
        # 先显式加载索引，把“冷加载”和“热查询”分开计时（产品里由预热线程承担）。
        started = time.perf_counter()
        store._get_embedder()
        report['index_load_ms'] = round((time.perf_counter() - started) * 1000, 1)
        random.seed(7)
        samples = random.sample(range(args.count), args.queries)
        latencies = []
        hits = 0
        first_query_ms = None
        details = []
        for order, index in enumerate(samples):
            scope_index = index // per_scope
            scope = scopes[scope_index]
            entity = rows[index][2].split('关于', 1)[1].split('的安排', 1)[0]
            query = '%s那个安排怎么样了？' % entity
            started = time.perf_counter()
            packet = store.context_packet(scope, query, budget=450,
                                          current_message_id='q-%d' % order)
            elapsed = (time.perf_counter() - started) * 1000
            if order == 0:
                first_query_ms = round(elapsed, 2)
            latencies.append(elapsed)
            if entity in packet:
                hits += 1
            if len(details) < 5:
                details.append({'index': index, 'entity': entity,
                                'packet_chars': len(packet),
                                'contains': entity in packet})

        db_files = [Path(d) / name for name in ('manual_test_memory.sqlite3',
                                                'manual_test_memory.sqlite3-wal',
                                                'manual_test_memory.sqlite3-shm')]
        report['db_mb'] = round(sum(path.stat().st_size for path in db_files if path.exists())
                                / 1024 / 1024, 1)
        report['rss_mb'] = rss_mb()
        report['queries'] = args.queries
        report['details'] = details
        report['target_hits'] = hits
        report['latency_ms'] = {
            'first': first_query_ms,
            'p50': percentile(latencies, 50),
            'p95': percentile(latencies, 95),
            'p99': percentile(latencies, 99),
            'max': round(max(latencies), 2),
        }
        report['target'] = {'p95_under_ms': 2000,
                            'passed': report['latency_ms']['p95'] < 2000}
        store.close()

    out = Path(args.out)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['target']['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
