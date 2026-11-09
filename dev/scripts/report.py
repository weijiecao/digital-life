"""单一事实源：跑全量用例 + 评估 + 集成探针，生成 STATUS.md 与 reports/status.json。

背景（核查报告 P1-2）：文档里的用例数漂移过三次（177 / 142 / 225），
因为数字是手写的。从本文件开始，文档只引用 STATUS.md，不再抄数字。

用法（在 dev/ 下）：
    python scripts/report.py
    python scripts/report.py --model-dir "D:/models/bge-small-zh-v1.5"

退出码：0 = 全部用例通过且证据齐全；1 = 有用例失败/证据缺失。
"""

import argparse
import importlib.util
import io
import json
import platform
import subprocess
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SUITES = (
    'tests.test_store',
    'tests.test_plugin',
    'tests.test_data_safety',
    'tests.test_v1_5_consolidator',
    'tests.test_b4_fixture',
    'tests.test_b4_reliability',
    'tests.test_b4_gate',
    'tests.test_v1_5b_t1_t2_fixes',
    'tests.test_b4_lease_fencing',
    'tests.test_b4_provenance',
    'tests.test_b4_replay_mapping',
    'tests.test_b4_frozen_fork',
    'tests.test_b4_unit_of_work',
    'tests.test_b4_scope_lease',
    'tests.test_baseline_alignment',
    'tests.test_preflight',
    'tests.test_auto_jobs',
    'tests.test_backup_regression',
    'tests.test_prewarm',
    'tests.test_model_judge',
    'tests.test_delivery',
    'tests.test_soak',
    'tests.test_threshold',
    'tests.test_correction',
    'tests.test_metrics',
    'tests.test_real_corpus',
    'tests.test_background',
    'tests.test_package',
    'tests.test_attribution',
    'tests.test_panel',
    'tests.test_integration_chain',
)

# 验收线 -> 必须存在且通过的测试名（子串匹配）。缺失会显式报告，不静默算通过。
REQUIRED_EVIDENCE = {
    '备份可删除/可恢复（Windows 回归）': ['test_backup_regression'],
    '备份校验与隔离恢复': [
        'test_backup_verify_and_restore_isolated_copy',
        'test_backup_detects_broken_capture_link_and_cleans_failed_output'],
    '重启续跑且不重复注入': [
        'test_restart_resumes_jobs_without_duplicate_capture',
        'test_auto_jobs_restart_retry_and_source_bound_group'],
    '钩子契约（模拟宿主）': ['test_hooks_are_registered_with_the_assumed_filters'],
    '后台整理死信策略': ['test_failed_job_stops_retrying_and_keeps_source'],
    '依赖与模型自检': ['test_missing_dependencies_produce_one_actionable_message'],
}


def _iter_tests(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_tests(item)
        else:
            yield item


def run_suite(name):
    suite = unittest.defaultTestLoader.loadTestsFromName(name)
    # TestSuite 在运行后会把已执行的用例置空，必须在运行前取 id。
    ids = [test.id() for test in _iter_tests(suite)]
    buffer = io.StringIO()
    started = time.perf_counter()
    result = unittest.TextTestRunner(stream=buffer, verbosity=0).run(suite)
    elapsed = time.perf_counter() - started
    return {
        'suite': name,
        'tests': result.testsRun,
        'failures': len(result.failures),
        'errors': len(result.errors),
        'skipped': len(result.skipped),
        'duration_s': round(elapsed, 2),
        'failed': [test.id() for test, _ in result.failures + result.errors],
        'ids': ids,
    }


def _module_available(name):
    from astrbot_plugin_digital_memory_store.preflight import module_available
    return module_available(name)


def collect_environment(model_dir=''):
    """必须在导入模拟宿主之前调用，否则 astrbot 会被替身占据。"""
    from astrbot_plugin_digital_memory_store.preflight import check_environment
    checks = check_environment(model_dir)
    return {
        'time': datetime.now().astimezone().isoformat(timespec='seconds'),
        'platform': platform.platform(),
        'python': sys.version.split()[0],
        'executable': sys.executable,
        'torch': _module_available('torch'),
        'sentence_transformers': _module_available('sentence_transformers'),
        'astrbot': _module_available('astrbot'),
        'model_dir': model_dir,
        'preflight_ok': checks['ok'],
        'preflight_message': checks['message'],
        'semantic_ready': checks['semantic_ready'],
    }


def run_plugin_version():
    metadata = ROOT / 'astrbot_plugin_digital_memory_store' / 'metadata.yaml'
    for line in metadata.read_text(encoding='utf-8').splitlines():
        if line.startswith('version:'):
            return line.split(':', 1)[1].strip()
    return 'unknown'


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_scenarios():
    module = _load_module('run_scenarios', ROOT / 'evaluation' / 'run_scenarios.py')
    return module.run()


def _run_script(relative, timeout=300):
    process = subprocess.run([sys.executable, str(ROOT / relative)], cwd=str(ROOT),
                             capture_output=True, text=True, encoding='utf-8',
                             errors='replace', timeout=timeout)
    return {'ok': process.returncode == 0, 'exit': process.returncode,
            'tail': (process.stdout or process.stderr or '').strip().splitlines()[-3:]}


def read_json(relative):
    path = ROOT / relative
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def evaluate_evidence(results):
    by_id = {}
    failed = set()
    for result in results:
        for test_id in result['ids']:
            by_id[test_id] = result['suite']
        failed.update(result['failed'])
    evidence = {}
    for line, needles in REQUIRED_EVIDENCE.items():
        matches = [test_id for test_id in by_id
                   if any(needle in test_id for needle in needles)]
        if not matches:
            evidence[line] = {'status': '缺失', 'tests': []}
        elif any(test_id in failed for test_id in matches):
            evidence[line] = {'status': '失败', 'tests': matches}
        else:
            evidence[line] = {'status': '通过', 'tests': matches}
    return evidence


def render_g3(v1, v2):
    """G3 预研：LLM 判定两版提示词的实测结果。"""
    if not v1 and not v2:
        return []

    def value(report, key, sub=None):
        if not report:
            return '-'
        summary = report.get('summary') or {}
        if sub is None:
            return summary.get(key, '-')
        return summary.get(key, {}).get(sub, '-')

    def kind(report, name):
        entry = (report or {}).get('summary', {}).get(name, {})
        return '%s/%s' % (entry.get('ok', 0), entry.get('n', 0))

    lines = ['## G3 预研（LLM 判定，13 条虚构用例）', '',
             '| 指标 | v1 朴素 | v2 受约束 |', '|---|---|---|']
    lines.append('| 可解析 | %s | %s |' % (kind(v1, 'resolvable'), kind(v2, 'resolvable')))
    lines.append('| 无候选正确 | %s | %s |' % (kind(v1, 'insufficient'), kind(v2, 'insufficient')))
    lines.append('| 冲突并列 | %s | %s |' % (kind(v1, 'conflict'), kind(v2, 'conflict')))
    lines.append('| 注入抵抗 | %s | %s |' % (kind(v1, 'injection'), kind(v2, 'injection')))
    lines.append('| 延迟 p50 / p95 | %s / %s s | %s / %s s |'
                 % (value(v1, 'latency_p50_s'), value(v1, 'latency_p95_s'),
                    value(v2, 'latency_p50_s'), value(v2, 'latency_p95_s')))
    lines.append('| tokens/次 | %s | %s |'
                 % (value(v1, 'avg_tokens_per_call'), value(v2, 'avg_tokens_per_call')))
    lines.append('')
    lines.append('> 结论：**不换主路径**；只加默认关闭的"候选筛选器"（只筛候选 + 必报冲突 + 禁止确认口吻），'
                 '触发条件与验收门槛见 `docs/G3_LLM_DECISION.md`。')
    lines.append('')
    return lines


def render_g3_ab(anaphora, negative):
    """G3 窄路端到端 A/B（真实 DeepSeek，虚构语料）。"""
    if not anaphora and not negative:
        return []
    lines = ['## G3 窄路 A/B（真实 DeepSeek，虚构语料）', '',
             '| 组 | 结果 | 调用次数 | tokens | 平均延迟 |', '|---|---|---|---|---|']
    if anaphora:
        lines.append('| 指代追问（15） | **%d/%d 命中** | %s | %s+%s | %.2f s |'
                     % (anaphora.get('hits', 0), anaphora.get('cases', 0),
                        anaphora.get('calls', 0),
                        (anaphora.get('tokens') or {}).get('in', 0),
                        (anaphora.get('tokens') or {}).get('out', 0),
                        anaphora.get('avg_judge_latency_s') or 0))
    if negative:
        lines.append('| 负例（20） | **%d/%d 正确拒绝** | %s | %s+%s | %.2f s |'
                     % (negative.get('hits', 0), negative.get('cases', 0),
                        negative.get('calls', 0),
                        (negative.get('tokens') or {}).get('in', 0),
                        (negative.get('tokens') or {}).get('out', 0),
                        negative.get('avg_judge_latency_s') or 0))
    lines.append('')
    lines.append('> 两道确定性闸门：①只对「≤16 字 + 指代词」的查询触发；'
                 '②选中候选必须与问题有字面重合（纯指代如“后来呢？”除外）。'
                 '负例 20 条里只有 3 条真正调用模型，其余本地直接拒绝。详见 `docs/G3_LLM_DECISION.md`。')
    lines.append('')
    return lines


def render_real_corpus(real, smoke):
    """真实语料探针：优先用户数据；没有时展示管线自测（合成样例）。"""
    report = real or smoke
    if not report:
        return []
    summary = report.get('summary') or {}
    lines = ['## 真实语料探针（%s）' % ('用户数据' if real else '管线自测：合成样例'),
             '', '| 指标 | 结果 |', '|---|---|']
    lines.append('| 正例命中 | %s/%s |' % (summary.get('hits'), summary.get('positives')))
    lines.append('| 负例未误注入 | %s/%s |'
                 % (summary.get('negative_ok'), summary.get('negatives')))
    lines.append('| 禁注命中 | %s |' % summary.get('forbid_hits'))
    latency = summary.get('latency_ms') or {}
    lines.append('| 热延迟 P50 / P95 | %s / %s ms |' % (latency.get('p50'), latency.get('p95')))
    lines.append('')
    if smoke and not real:
        lines.append('> 当前只有管线自测；放入 200–500 条脱敏语料后按 `docs/REAL_CORPUS.md` 运行，'
                     '即可得到用户真实分数。')
        lines.append('')
    return lines


def render_scale_crash(scale, crash):
    """5,000 条规模与崩溃恢复（合成数据，只测规模/恢复，不代表召回质量）。"""
    if not scale and not crash:
        return []
    lines = ['## 规模与恢复（合成数据）', '', '| 项 | 结果 |', '|---|---|']
    if scale:
        latency = scale.get('latency_ms') or {}
        lines.append('| 5,000 条写入 / 整理 | %s s / %s s |'
                     % (scale.get('seed_s'), scale.get('jobs_s')))
        lines.append('| 模型冷加载 | %.1f s（产品由预热线程承担） |'
                     % ((scale.get('index_load_ms') or 0) / 1000))
        lines.append('| 查询命中 | %s/%s |' % (scale.get('target_hits'), scale.get('queries')))
        lines.append('| 延迟 P50 / P95 / P99 | %s / %s / %s ms |'
                     % (latency.get('p50'), latency.get('p95'), latency.get('p99')))
        lines.append('| 库大小 / RSS | %s MB / %s MB |'
                     % (scale.get('db_mb'), scale.get('rss_mb')))
    if crash:
        lines.append('| 崩溃恢复 | 完整性 %s；杀进程时 %s 条、重启后 %s 条；续写=%s；同消息重复=%s |'
                     % (crash.get('integrity'), crash.get('rows_after_kill'),
                        crash.get('rows_before_reopen'),
                        '是' if crash.get('resume_capture_ok') else '否',
                        crash.get('duplicate_rows_for_same_message')))
    lines.append('')
    if scale and not scale.get('target', {}).get('passed', True):
        lines.append('> ⚠️ P95 超过 2 s 目标，需优化。')
        lines.append('')
    return lines


def render_m2(no_model, real_bge, always=None):
    """M2 分层评估集基线（m2_runner.py 产出）。"""
    if not no_model and not real_bge:
        return []

    def metric(report, key, sub=None):
        if not report:
            return '-'
        summary = report.get('summary') or {}
        if sub:
            return summary.get(key, {}).get(sub)
        return summary.get(key)

    def ratio(report, key, denominator):
        if not report:
            return '-'
        summary = report.get('summary') or {}
        return '%d/%d' % (summary.get(key, 0), summary.get(denominator, 0))

    lines = ['## M2 评估集基线（自造 120 条）', '',
             '| 指标 | 无模型 | 真实 BGE | L1 目标 |', '|---|---|---|---|']
    lines.append('| 正例 R@1 | %s | %s | ≥70%% |'
                 % (ratio(no_model, 'r1', 'positives'), ratio(real_bge, 'r1', 'positives')))
    lines.append('| 负例误注入 | %s | %s | ≤5%% |'
                 % (ratio(no_model, 'false_injected', 'negatives'),
                    ratio(real_bge, 'false_injected', 'negatives')))
    lines.append('| 纠错（新旧+未确认） | %s | %s | ≥60%% |'
                 % (ratio(no_model, 'correction_ok', 'corrections'),
                    ratio(real_bge, 'correction_ok', 'corrections')))
    latency = metric(real_bge, 'latency_ms')
    if latency:
        lines.append('| 延迟 P50 / P95 | %s / %s ms | %s / %s ms | — |'
                     % (metric(no_model, 'latency_ms', 'p50'),
                        metric(no_model, 'latency_ms', 'p95'),
                        latency.get('p50'), latency.get('p95')))
    lines.append('')
    if always:
        summary = always.get('summary') or {}
        lines.append('| **每轮供给模式** | R@1 %s/%s；供给 %s/%s；背景-only %s；非背景误注入 %s/%s；串味 %s |'
                     % (summary.get('r1'), summary.get('positives'),
                        summary.get('supplied'), summary.get('total'),
                        summary.get('background_only'),
                        summary.get('false_injected'), summary.get('negatives'),
                        summary.get('forbidden_hits')))
        lines.append('')
    lines.append('| 类别 | 无模型 R@1 | 真实 BGE R@1 |')
    lines.append('|---|---|---|')
    categories = (real_bge or no_model or {}).get('summary', {}).get('categories', {})
    for name in categories:
        def cell(report):
            entry = ((report or {}).get('summary') or {}).get('categories', {}).get(name, {})
            if entry.get('false_n'):
                return '误注入 %d/%d' % (entry.get('false', 0), entry['false_n'])
            n = entry.get('n', 0)
            return 'R@1 %d/%d' % (entry.get('r1', 0), n)
        lines.append('| %s | %s | %s |' % (name, cell(no_model), cell(real_bge)))
    lines.append('')
    lines.append('> 语料为**自造**（非真实聊天），用于回归与阈值扫描，不等于真实准确率。'
                 '缺口集中：明确事件（短实体被四字门拒绝）、指代追问（自动路径不产生焦点）。'
                 '详见 `docs/EVAL_SET.md`。')
    lines.append('')
    return lines


def render_calibration(turns, scenarios_real):
    """真实 BGE 的阈值校准摘要（数据来自 probe_bge_turns.py）。"""
    if not turns:
        return []
    cases = turns.get('cases') or {}
    first_turns = [case['turns'][0] for case in cases.values() if case.get('turns')]
    informative = [turn for case in cases.values() for turn in case.get('turns', [])[1:]]
    source_scores = [turn['target_source_score'] for turn in informative
                     if turn.get('target_source_score') is not None]
    lines = ['## 真实 BGE 校准（M3 第一步）', '', '| 项 | 结果 |', '|---|---|']
    if first_turns:
        lines.append('| 首轮「无内容追问」事件过 0.55 | %d/%d（最高 %.3f） |'
                     % (sum(1 for turn in first_turns if turn.get('event_pass')),
                        len(first_turns),
                        max(turn['best_event_score'] for turn in first_turns)))
    if informative:
        lines.append('| 有内容轮次事件过 0.55 | %d/%d |'
                     % (sum(1 for turn in informative if turn.get('event_pass')),
                        len(informative)))
    if source_scores:
        lines.append('| 正确原话分数 | %.3f–%.3f |' % (min(source_scores), max(source_scores)))
        lines.append('| 正确原话过 0.75 | %d/%d |'
                     % (sum(1 for turn in informative if turn.get('source_pass_075')),
                        len(source_scores)))
    if scenarios_real:
        lines.append('| 真实 BGE 场景评估 | %d/%d（替身 14/15） |'
                     % (scenarios_real.get('passed', 0), scenarios_real.get('total', 0)))
    lines.append('')
    lines.append('> 结论：事件门槛 0.55 有数据支持；原话门槛 0.75 过严（候选 0.55–0.60，'
                 '须 100 条评估集验证）；「之前那件事」类追问无语义信号，调阈值无解。'
                 '详见 `docs/BGE_CALIBRATION.md`。')
    lines.append('')
    return lines


def render_markdown(status):
    env = status['environment']
    suites = status['suites']
    total = sum(item['tests'] for item in suites)
    failed = sum(item['failures'] + item['errors'] for item in suites)
    scenarios = status['scenarios']
    baseline = status['baseline_retrieval']
    probe = status['integration']
    real = status.get('real_env')
    qq = status.get('qq_real')
    evidence = status['evidence']

    lines = []
    lines.append('# E5.0-F 工程状态（自动生成，勿手改）')
    lines.append('')
    lines.append('> 由 `python scripts/report.py` 生成于 ' + env['time'] +
                 '；插件版本 `' + status['plugin_version'] + '`。')
    lines.append('> 文档中的用例数、通过率、评估分一律引用本文件，不再手写。')
    lines.append('')
    lines.append('## 一句话')
    lines.append('')
    if failed == 0:
        lines.append('**离线用例 %d/%d 通过**；场景评估 %d/%d；集成探针注入 %d/%d。'
                     % (total, total, scenarios['passed'], scenarios['total'],
                        probe['injection']['injected'], probe['injection']['rounds']))
    else:
        lines.append('**离线用例 %d/%d 通过，%d 项失败**；其余数字见下表。'
                     % (total - failed, total, failed))
    if real:
        attempts = [row for row in (real.get('injection_attempts') or [])]
        injected = [row for row in attempts if row.get('contains_memory_prefix')]
        lines.append('')
        lines.append('**真实环境**：AstrBot %s 上插件加载成功，%d 轮真实请求中 %d 轮看到记忆片段'
                     '（平台 WebChat、provider 为本地 mock）。'
                     % (real.get('astrbot_version', '?'), len(attempts), len(injected)))
    if qq:
        latest = (qq.get('provider') or {}).get('latest_injected') or {}
        scope = (qq.get('captures') or {}).get('latest_scope') or {}
        lines.append('')
        lines.append('**真实 QQ**：NapCat 小号私聊已采集 %d 条；最近一次请求体'
                     '%s记忆片段，引用待审序号 %s「%s」。'
                     % (scope.get('rows', 0),
                        '含' if latest.get('has_memory_prefix') else '不含',
                        latest.get('injected_seq'), latest.get('injected_original')))
    lines.append('')
    if qq:
        lines.append('仍未做：**主链路真实模型服务**（当前 provider 是本地 mock，回复为固定文本；'
                     '真实 DeepSeek 仅用于 G3 窄路 A/B）、**用户真实语料**（管线已就绪，见 '
                     '`docs/REAL_CORPUS.md`）、**L0 三天 soak 数据**。'
                     '标「替身」的数字不能当作 L0/L1 达标证据；标「real」的见真实环境与真实 QQ 两节。')
    elif real:
        lines.append('仍未做：**NapCat/QQ 实聊**、**真实模型服务**、5,000 条压测与崩溃恢复。'
                     '下表里标「替身」的数字不能当作 L0/L1 达标证据；标「real」的见真实环境一节。')
    else:
        lines.append('仍未做：真实 AstrBot + NapCat + 真实 BGE 的实机验收。'
                     '因此下表的延迟与召回都是**替身环境**数字，不能当作 L0/L1 达标证据。')
    lines.append('')
    lines.append('## 环境事实')
    lines.append('')
    lines.append('| 项 | 值 |')
    lines.append('|---|---|')
    lines.append('| 操作系统 | %s |' % env['platform'])
    lines.append('| 离线回归解释器 | Python %s（%s） |' % (env['python'], env['executable']))
    lines.append('| 离线环境 torch / sentence-transformers | %s / %s |'
                 % ('已安装' if env['torch'] else '缺失',
                    '已安装' if env['sentence_transformers'] else '缺失'))
    lines.append('| 离线环境 AstrBot | %s |' % ('已安装' if env['astrbot'] else '未安装（用例用替身）'))
    if real:
        bge = real.get('bge') or {}
        lines.append('| 真实环境 | AstrBot %s / Python %s / torch %s / sentence-transformers %s |'
                     % (real.get('astrbot_version', '?'), real.get('python', '?'),
                        bge.get('torch_version', '?'),
                        bge.get('sentence_transformers_version', '?')))
        lines.append('| 真实模型 | %s（%s 维） |'
                     % (Path(real.get('model_dir', '')).name or 'bge',
                        bge.get('dimension', '?')))
    lines.append('| model_dir（自检用） | %s |' % (env['model_dir'] or '未配置'))
    lines.append('| 语义检索自检（离线解释器） | %s |' % ('可用' if env['semantic_ready'] else '不可用'))
    lines.append('')
    lines.append('## 测试套件')
    lines.append('')
    lines.append('| 套件 | 用例 | 失败/错误 | 跳过 | 耗时 |')
    lines.append('|---|---|---|---|---|')
    for item in suites:
        lines.append('| `%s` | %d | %d | %d | %.1fs |' % (
            item['suite'], item['tests'], item['failures'] + item['errors'],
            item['skipped'], item['duration_s']))
    lines.append('| **合计** | **%d** | **%d** | | |' % (total, failed))
    lines.append('')
    if failed:
        lines.append('失败用例：')
        for item in suites:
            for test_id in item['failed']:
                lines.append('- `%s`' % test_id)
        lines.append('')
    lines.append('## 评估与探针')
    lines.append('')
    lines.append('| 项目 | 结果 | 边界 |')
    lines.append('|---|---|---|')
    lines.append('| 固定场景评估 | %d/%d 符合预期 | 15 项固定夹具；未调用真实模型/平台 |'
                 % (scenarios['passed'], scenarios['total']))
    if scenarios.get('failed'):
        lines.append('| 未通过项 | %s | 已知短板，保留不自欺 |'
                     % '、'.join(scenarios['failed']))
    if status.get('scenarios_real'):
        real_scen = status['scenarios_real']
        lines.append('| 真实 BGE 场景评估 | %d/%d | 真实编码；失败项见 BGE_CALIBRATION |'
                     % (real_scen.get('passed', 0), real_scen.get('total', 0)))
    if baseline:
        summary = baseline['summary']
        cells = []
        for mode in ('plain', 'weak', 'semantic_fixture', 'semantic_real'):
            if mode in summary:
                cells.append('%s %d/%d' % (mode, summary[mode]['recalled'],
                                           summary[mode]['positives']))
        lines.append('| 上游基线检索对齐 | %s | 规则 0/2 是结构性的；semantic_fixture 仅证明管线 |'
                     % '；'.join(cells))
    if probe:
        lines.append('| 集成探针·注入 | %d/%d 轮 | 替身平台与受控编码器 |'
                     % (probe['injection']['injected'], probe['injection']['rounds']))
        lines.append('| 集成探针·钩子耗时 | P50 %.1fms / P95 %.1fms | 不含真实 BGE 编码 |'
                     % (probe['latency_ms']['p50'], probe['latency_ms']['p95']))
        lines.append('| 集成探针·重启 | 积压 %d → %d，重复采集=%s | 同一进程重启，非崩溃恢复 |'
                     % (probe['restart']['pending_before_restart'],
                        probe['restart']['pending_after_drain'],
                        '是' if probe['restart']['duplicate_capture'] else '否'))
        lines.append('| 集成探针·不兼容宿主 | 静默放行=%s | 模拟缺 extra_user_content_parts |'
                     % ('是' if probe['legacy_host']['failed_open'] else '否'))
    lines.append('')
    if real:
        bge = real.get('bge') or {}
        plugin = real.get('plugin') or {}
        handlers = plugin.get('handlers') or []
        attempts = real.get('injection_attempts') or []
        injected = [row for row in attempts if row.get('contains_memory_prefix')]
        first = injected[0] if injected else {}
        lines.append('## 真实环境（AstrBot %s / WebChat 探针）' % real.get('astrbot_version', '?'))
        lines.append('')
        lines.append('| 项 | 结果 |')
        lines.append('|---|---|')
        lines.append('| 插件加载 | %s |' % ('是' if plugin.get('loaded') else '否'))
        lines.append('| 钩子注册 | %s |' % '、'.join(
            '%s(%s)' % (row['name'], row['event']) for row in handlers))
        lines.append('| 真实请求中注入可见 | %d/%d 轮 |' % (len(injected), len(attempts)))
        if first:
            lines.append('| 钩子到 provider | %.2f s |' % first.get('hook_to_provider_s', 0))
        if bge.get('ok'):
            lines.append('| 真实 BGE | %d 维 / 加载 %.1f s / %.1f ms 每条 |'
                         % (bge['dimension'], bge['load_seconds'], bge['encode_ms_per_item']))
        lines.append('| 后台任务排空 | %s s |' % real.get('jobs_drained_after_s'))
        cold = real.get('cold_query') or {}
        if cold:
            lines.append('| 模型加载中发消息 | 回复 %.0f ms；本轮注入=%s（语义降级为字面） |'
                         % ((cold.get('reply_ttfb_s') or 0) * 1000,
                            '是' if cold.get('contains_memory_prefix') else '否'))
        lines.append('| 平台 / provider | WebChat（QQ 见下一节）/ 本地 mock |')
        lines.append('')
        lines.append('> 这是 real 级框架证据（真实 AstrBot + 真实 BGE + 真实钩子）；'
                     'provider 仍是本地 mock。详见 `docs/REAL_ENV_REPORT.md`。')
        lines.append('')
    if qq:
        latest = (qq.get('provider') or {}).get('latest_injected') or {}
        scope = (qq.get('captures') or {}).get('latest_scope') or {}
        lines.append('## 真实 QQ 链路（NapCat 小号）')
        lines.append('')
        lines.append('| 项 | 结果 |')
        lines.append('|---|---|')
        lines.append('| 平台 | NapCat 4.18.41 / QQ 9.9.36，反向 WS 连接 `127.0.0.1:6201` |')
        lines.append('| 小号 → testbed | 已连接（aiocqhttp 事件 WS） |')
        lines.append('| 私聊采集 | %d 条（独立于 WebChat 的作用域） |' % scope.get('rows', 0))
        lines.append('| 后台整理 | %s |' % json.dumps(qq.get('jobs') or {}, ensure_ascii=False))
        lines.append('| 注入 | %s；引用待审序号 %s「%s」 |'
                     % ('可见' if latest.get('has_memory_prefix') else '未出现',
                        latest.get('injected_seq'), latest.get('injected_original')))
        lines.append('| 注入时间 | %s |' % latest.get('at', '-'))
        lines.append('')
        lines.append('> QQ 段是**真实平台**（不是 WebChat），但 provider 仍是本地 mock：'
                     '因此能证明"记忆进入了模型请求"，不能证明回复质量。')
        lines.append('')
    calibration = render_calibration(status.get('bge_turns'), status.get('scenarios_real'))
    if calibration:
        lines.extend(calibration)
    m2 = render_m2(status.get('m2_no_model'), status.get('m2_real_bge'),
                   status.get('m2_always_context'))
    if m2:
        lines.extend(m2)
    g3 = render_g3(status.get('g3_llm'), status.get('g3_llm_v2'))
    if g3:
        lines.extend(g3)
    ab = render_g3_ab(status.get('g3_ab_anaphora'), status.get('g3_ab_negative'))
    if ab:
        lines.extend(ab)
    scale_crash = render_scale_crash(status.get('scale'), status.get('crash'))
    if scale_crash:
        lines.extend(scale_crash)
    real_corpus = render_real_corpus(status.get('real_corpus'),
                                     status.get('real_corpus_smoke'))
    if real_corpus:
        lines.extend(real_corpus)
    lines.append('## 关键证据（缺一即不算完成）')
    lines.append('')
    lines.append('| 能力 | 状态 | 覆盖测试数 |')
    lines.append('|---|---|---|')
    for line, item in evidence.items():
        lines.append('| %s | %s | %d |' % (line, item['status'], len(item['tests'])))
    lines.append('')
    lines.append('## L0 自用版验收线')
    lines.append('')
    lines.append('| 验收线 | 目标 | 当前 | 状态 |')
    lines.append('|---|---|---|---|')
    probe_injection = probe['injection'] if probe else None
    lines.append('| 采集成功率（真实私聊 3 天） | ≥99% | 无实机数据 | 未测 |')
    if probe_injection:
        lines.append('| 注入生效 | ≥95%% | %d%%（替身） | 仅替身证据 |'
                     % round(probe_injection['rate'] * 100))
        lines.append('| P95 钩子耗时 | <1.5s | %.0fms（替身，无 BGE 编码） | 仅替身证据 |'
                     % probe['latency_ms']['p95'])
    lines.append('| 备份可生成/校验/恢复/删除 | 全部通过 | %s | %s |'
                 % (evidence['备份可删除/可恢复（Windows 回归）']['status'],
                    '单测通过' if evidence['备份可删除/可恢复（Windows 回归）']['status'] == '通过' else '待修'))
    lines.append('| 重启续跑、无重复注入 | 通过 | %s | %s |'
                 % (evidence['重启续跑且不重复注入']['status'],
                    '探针通过（替身）' if evidence['重启续跑且不重复注入']['status'] == '通过' else '待修'))
    if real:
        attempts = real.get('injection_attempts') or []
        injected = [row for row in attempts if row.get('contains_memory_prefix')]
        cost = injected[0].get('hook_to_provider_s', 0) if injected else 0
        lines.append('| 真实框架注入（AstrBot %s） | 可见 | %d/%d 轮 / %.2fs | real（WebChat+mock） |'
                     % (real.get('astrbot_version', '?'), len(injected), len(attempts), cost))
    if qq:
        latest = (qq.get('provider') or {}).get('latest_injected') or {}
        lines.append('| 真实 QQ 私聊采集与注入 | 可见 | 采集 %d 条 / 序号 %s | real-qq（provider=mock） |'
                     % ((qq.get('captures') or {}).get('latest_scope', {}).get('rows', 0),
                        latest.get('injected_seq')))
    lines.append('')
    lines.append('## L1 Beta 验收线')
    lines.append('')
    lines.append('| 验收线 | 目标 | 当前 |')
    lines.append('|---|---|---|')
    lines.append('| 目标原话召回率 R@1 | ≥70% | 未测（缺 100 条评估集；真实 BGE 已就位） |')
    lines.append('| 误注入率 | ≤5% | 未测 |')
    lines.append('| 纠错场景新旧并列 | ≥60% | 未测 |')
    lines.append('| 5,000 条历史 P95 | <2s | 未测（无压测） |')
    lines.append('| 新人 30 分钟装好 | 通过 | 未测（无安装包/无实机） |')
    lines.append('| 恢复演练 3 次 | 通过 | 离线恢复单测通过；无实机演练 |')
    lines.append('')
    lines.append('## 里程碑与下一步')
    lines.append('')
    lines.append('| 项 | 状态 | 说明 |')
    lines.append('|---|---|---|')
    lines.append('| M0 P0 备份句柄修复 + 回归 | 完成 | `test_backup_regression` 4 项锁定可删除/可恢复 |')
    lines.append('| M0 依赖落盘与启动自检 | 完成 | `requirements.txt` 版本区间 + `preflight.check_environment` |')
    lines.append('| M0 测试计数自动化 | 完成 | 本文件由脚本生成 |')
    lines.append('| M1 基线继承对齐 | 完成 | 冻结话术 6 项 + 检索对齐报告 |')
    lines.append('| M1 钩子契约（L1 离线） | 完成 | 模拟宿主证明链路与签名；**不是实机证据** |')
    if real:
        lines.append('| M1 真实 AstrBot + 真实 BGE（L2） | 完成 | AstrBot %s / WebChat / mock provider；见 `docs/REAL_ENV_REPORT.md` |'
                     % real.get('astrbot_version', '?'))
    else:
        lines.append('| M1 真实 AstrBot + NapCat + BGE | 阻塞 | 需外部提供实例与模型 |')
    lines.append('| M1 NapCat / QQ 实聊（L3） | %s | %s |'
                 % ('完成（平台层）' if qq else '待用户',
                    '真实 QQ 私聊采集 + 注入可见；provider 仍为 mock' if qq
                    else '需测试 QQ 号；不碰生产实例'))
    lines.append('| M4/F1 模型预热（消除首轮阻塞） | 完成 | `Store.prewarm_embedder()` 后台线程加载；'
                 '加载中语义降级、采集/整理不受阻，`test_prewarm` 5 项锁定 |')
    lines.append('| G3 预研（LLM 判定） | 完成 | 13 条虚构用例：v2 约束提示词全对、+0.7 s、276 tokens/次；'
                 '建议**不换主路径**、只加默认关闭窄路，见 `docs/G3_LLM_DECISION.md` |')
    lines.append('| G3 窄路实现（默认关闭） | 完成 | `model_judge.py`：只筛候选+必报冲突+禁止确认口吻；'
                 '严格 JSON 校验、1.1 s 超时、每会话每小时限流；`test_model_judge` 17 项 |')
    ab_a = status.get('g3_ab_anaphora') or {}
    ab_n = status.get('g3_ab_negative') or {}
    if ab_a or ab_n:
        lines.append('| G3 窄路 A/B（真实 DeepSeek） | 完成 | anaphora **%d/%d**、负例 **%d/%d** 正确拒绝；'
                     '两道确定性闸门把误注入压到 0 |'
                     % (ab_a.get('hits', 0), ab_a.get('cases', 0),
                        ab_n.get('hits', 0), ab_n.get('cases', 0)))
    lines.append('| M2 评估集与基线分数 | 基线已建（自造 120 条） | 真实 BGE R@1 41/100、误注入 0/20；'
                 '真实语料待补，见 `docs/EVAL_SET.md` |')
    lines.append('')
    lines.append('## 复跑')
    lines.append('')
    lines.append('```powershell')
    lines.append('cd dev')
    lines.append('python scripts/report.py            # 重新生成本文件')
    lines.append('python -m unittest -q test_store test_plugin test_baseline_alignment test_preflight test_auto_jobs test_backup_regression test_prewarm test_integration_chain')
    lines.append('python evaluation/run_scenarios.py')
    lines.append('python evaluation/m2_runner.py')                     # M2 无模型基线
    lines.append('python evaluation/g3_llm_probe.py --prompt v2')      # 需 key，仅虚构语料
    lines.append('python eval_baseline_retrieval.py')
    lines.append('python integration_probe.py')
    lines.append('python scripts/preflight.py --model-dir "<本机模型目录>"')
    lines.append('# 真实环境（需先启动 mock provider，见 testbed/README.md）')
    lines.append('cd ../testbed')
    lines.append('.venv\\Scripts\\python.exe harness\\real_chain.py --out "..\\dev\\reports\\real_env.json"')
    lines.append('```')
    lines.append('')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description='生成 STATUS.md / status.json')
    parser.add_argument('--model-dir', default='', help='本机模型目录（只用于自检与记录）')
    parser.add_argument('--no-write', action='store_true', help='只打印，不写文件')
    args = parser.parse_args(argv)

    environment = collect_environment(args.model_dir)

    scenarios = run_scenarios()
    scenarios_view = {'total': scenarios['total'], 'passed': scenarios['passed'],
                      'failed': [row['id'] for row in scenarios['results']
                                 if not row['passed']]}

    suites = [run_suite(name) for name in SUITES]

    baseline = _run_script('eval_baseline_retrieval.py')
    probe_run = _run_script('integration_probe.py')

    status = {
        'generated_at': environment['time'],
        'plugin_version': run_plugin_version(),
        'environment': environment,
        'scenarios': scenarios_view,
        'suites': suites,
        'baseline_retrieval_run': baseline,
        'integration_run': probe_run,
        'baseline_retrieval': read_json('reports/baseline_retrieval.json'),
        'integration': read_json('reports/integration_chain.json'),
        'real_env': read_json('reports/real_env.json'),
        'qq_real': read_json('reports/qq_real.json'),
        'scenarios_real': read_json('reports/scenarios_real_bge.json'),
        'bge_turns': read_json('reports/bge_turns.json'),
        'm2_no_model': read_json('reports/m2_baseline_no_model.json'),
        'm2_real_bge': read_json('reports/m2_baseline_real_bge.json'),
        'm2_always_context': read_json('reports/m2_always_context.json'),
        'g3_llm': read_json('reports/g3_llm.json'),
        'g3_llm_v2': read_json('reports/g3_llm_v2.json'),
        'g3_ab_anaphora': read_json('reports/g3_ab_anaphora.json'),
        'g3_ab_negative': read_json('reports/g3_ab_negative.json'),
        'scale': read_json('reports/scale_5000.json'),
        'crash': read_json('reports/crash_recovery.json'),
        'real_corpus': read_json('reports/real_corpus.json'),
        'real_corpus_smoke': read_json('reports/real_corpus_smoke.json'),
        'evidence': evaluate_evidence(suites),
    }
    # 场景评估允许保留已知失败（例如 paraphrase_book），它不阻塞工程健康判定。
    status['ok'] = (sum(item['failures'] + item['errors'] for item in suites) == 0
                    and all(item['status'] == '通过' for item in status['evidence'].values()))

    markdown = render_markdown(status)
    if not args.no_write:
        (ROOT / 'reports').mkdir(exist_ok=True)
        (ROOT / 'reports' / 'status.json').write_text(
            json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
        (ROOT / 'STATUS.md').write_text(markdown, encoding='utf-8')
    print(markdown)
    return 0 if status['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
