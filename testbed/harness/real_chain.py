"""真实 AstrBot 4.29.3 链路探针：不用替身，跑实机代码。

结构：真实 AstrBot CoreLifecycle
  → 真实平台适配器（WebChat，等价于"收到一条私聊"）
  → 真实事件总线 / 插件管理器（加载 data/plugins 里的本插件）
  → 真实 on_llm_request 钩子
  → 真实 openai 兼容 provider（指向本地 mock，记录完整请求体）

它回答 M1 的三个问题：
  1. 插件能否被 AstrBot 4.29.3 正常加载、钩子是否真的被调用；
  2. 注入的记忆片段是否真的进入发给模型的请求体（不是"以为注入了"）；
  3. 真实 BGE 的维度与编码耗时（另由 bge_probe.py 产出）。

用法（在 testbed 下）：
    python harness/mock_openai.py --port 9101          # 另一个进程
    python harness/real_chain.py --out ../dev/reports/real_env.json
"""

import argparse
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent
INSTANCE = TESTBED / 'instance'
SOURCE = TESTBED / 'AstrBot-4.29.3'
PLUGIN_DATA = INSTANCE / 'data/plugin_data/astrbot_plugin_digital_memory_store'
MOCK_LOG = TESTBED / 'logs/llm_requests.jsonl'
MODEL_DIR = TESTBED / 'models/bge-small-zh-v1.5'
# 每次用独立会话，避免 AstrBot 自身的历史记录掩盖本次链路证据。
CONV = 'dlm-real-' + time.strftime('%H%M%S')
USER = 'tester'
SEED_TEXT = '我喜欢吃苹果'
QUERY_TEXT = '你之前喜欢吃苹果吗'

os.environ['ASTRBOT_ROOT'] = str(INSTANCE)
os.chdir(INSTANCE)
# AstrBot 以 `data.plugins.<name>.main` 形式导入插件，实例根目录必须在 sys.path 上。
sys.path.insert(0, str(INSTANCE))
sys.path.insert(0, str(SOURCE))

import runtime_bootstrap  # noqa: E402

runtime_bootstrap.initialize_runtime_bootstrap()

from astrbot.core import LogBroker, db_helper  # noqa: E402
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle  # noqa: E402
from astrbot.core.star.star_handler import EventType, star_handlers_registry  # noqa: E402
from astrbot.core.platform.sources.webchat.webchat_queue_mgr import webchat_queue_mgr  # noqa: E402

PLUGIN = 'astrbot_plugin_digital_memory_store'


async def send_message(text, message_id, timeout=30.0):
    """通过真实 WebChat 队列投递一条私聊，并收集机器人回复文本。"""
    back = webchat_queue_mgr.get_or_create_back_queue(message_id, CONV)
    queue = webchat_queue_mgr.get_or_create_queue(CONV)
    started = time.perf_counter()
    await queue.put((USER, CONV, {
        'message': [{'type': 'plain', 'text': text}],
        'message_id': message_id,
        'flags': {},
    }))
    chunks, first_chunk_at = [], None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            item = await asyncio.wait_for(back.get(), timeout=max(0.1, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            break
        if first_chunk_at is None:
            first_chunk_at = time.perf_counter()
        if isinstance(item, dict) and item.get('type') == 'end':
            break
        if isinstance(item, dict) and isinstance(item.get('data'), str) and item['data']:
            text = item['data']
            if not text.startswith('{"token_usage"'):  # AstrBot 附带的用量元数据，不算回复
                chunks.append(text)
    return {'text': ''.join(chunks),
            'ttfb_s': round((first_chunk_at - started), 3) if first_chunk_at else None,
            'total_s': round(time.perf_counter() - started, 3)}


def mock_requests():
    if not MOCK_LOG.is_file():
        return []
    rows = []
    for line in MOCK_LOG.read_text(encoding='utf-8').splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


async def wait_for_provider_call(after_count, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = mock_requests()
        if len(rows) > after_count:
            return rows[-1]
        await asyncio.sleep(0.05)
    return None


def read_captures():
    path = PLUGIN_DATA / 'manual_test_memory.sqlite3'
    if not path.is_file():
        return {'rows': 0, 'contents': []}
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute('SELECT seq,content,recorded_at FROM dl_capture ORDER BY seq').fetchall()
    finally:
        conn.close()
    return {'rows': len(rows), 'contents': [row[1] for row in rows],
            'seqs': [row[0] for row in rows]}


def vector_count():
    path = PLUGIN_DATA / 'manual_test_memory.sqlite3'
    if not path.is_file():
        return 0
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute('SELECT COUNT(*) FROM dl_auto_source_vectors').fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


async def wait_for_vectors(timeout=180.0):
    """等真实 BGE 加载并完成首批编码：池子是单线程的，加载期间注入会超时。"""
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        if vector_count() > 0:
            return round(time.perf_counter() - started, 2)
        await asyncio.sleep(0.5)
    return None


def job_counts():
    path = PLUGIN_DATA / 'manual_test_memory.sqlite3'
    if not path.is_file():
        return {}
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute('SELECT status,COUNT(*) FROM dl_auto_jobs GROUP BY status').fetchall()
        return {row[0]: row[1] for row in rows}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


async def wait_for_jobs(timeout=240.0):
    """等后台整理任务排空：有积压时插件会保守地不给自动线索。"""
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        counts = job_counts()
        if counts.get('pending', 0) == 0 and counts.get('done', 0) > 0:
            return round(time.perf_counter() - started, 2)
        await asyncio.sleep(0.5)
    return None


async def wait_for_context_idle(lifecycle, timeout=120.0):
    """插件用 single-flight 保护上下文查询；上一次查询未结束时新请求会被跳过。"""
    plugin = find_plugin(lifecycle)
    if plugin is None:
        return None
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        future = getattr(plugin, 'pending_context', None)
        if future is None or future.done():
            return round(time.perf_counter() - started, 2)
        await asyncio.sleep(0.2)
    return None


def find_plugin(lifecycle):
    """get_all_stars() 返回的是 StarMetadata；插件实例在 star_cls 上。"""
    for metadata in lifecycle.plugin_manager.context.get_all_stars():
        if getattr(metadata, 'name', '') == PLUGIN and metadata.star_cls is not None:
            return metadata.star_cls
    return None


def retrieval_diag(plugin, umo):
    """在插件线程池里逐条检查 `_pending_context` 的前置条件。"""
    store = plugin.store
    db = store.flow.db
    scope = store.scope(USER, umo)
    out = {
        'scope': scope,
        'pending_enabled': store.pending_context_enabled,
        'model_dir_set': bool(plugin.model_dir),
        'pairs': len(store._character_pairs(QUERY_TEXT)),
        'index_rows': db.execute('SELECT COUNT(*) FROM dl_pending_index WHERE scope=?', (scope,)).fetchone()[0],
        'term_rows': db.execute('SELECT COUNT(*) FROM dl_pending_terms WHERE scope=?', (scope,)).fetchone()[0],
        'dirty_rows': db.execute('SELECT COUNT(*) FROM dl_pending_dirty WHERE scope=?', (scope,)).fetchone()[0],
        'captures': [list(row) for row in db.execute(
            'SELECT seq,state,recorded_at FROM dl_capture WHERE scope=? ORDER BY seq', (scope,))],
    }
    out['candidates'] = store.pending_index.candidates(scope, store._character_pairs(QUERY_TEXT))
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    try:
        out['pending_direct'] = len(store._pending_context(
            scope, QUERY_TEXT, now, 450, 'diag-1', set()))
    except Exception as exc:  # noqa: BLE001
        out['pending_direct'] = 'ERR ' + repr(exc)
    try:
        out['jobs'] = {row[0]: row[1] for row in db.execute(
            'SELECT status,COUNT(*) FROM dl_auto_jobs GROUP BY status')}
    except Exception as exc:  # noqa: BLE001
        out['jobs'] = 'ERR ' + repr(exc)
    try:
        from astrbot_plugin_digital_memory_store.auto_cross_update import cross_update_context
        blocked, paired = cross_update_context(
            db, store._index().model_key, store._index().embedder,
            scope, QUERY_TEXT, now, 450, 'diag-1', set())
        out['cross_update'] = {'blocked': blocked, 'chars': len(paired)}
    except Exception as exc:  # noqa: BLE001
        out['cross_update'] = 'ERR ' + repr(exc)
    try:
        out['packet_chars'] = len(store.context_packet(
            scope, QUERY_TEXT, budget=450, current_message_id='diag-1'))
    except Exception as exc:  # noqa: BLE001
        out['packet_chars'] = 'ERR ' + repr(exc)
    try:
        turn = store.context_turn(scope, QUERY_TEXT, None, budget=450,
                                  current_message_id='diag-1')
        out['turn_chars'] = len(turn[0])
    except Exception as exc:  # noqa: BLE001
        out['turn_chars'] = 'ERR ' + repr(exc)
    try:
        out['conflict'] = {seq: store._auto_source_conflict(scope, seq)
                           for seq in range(1, len(out['captures']) + 1)}
    except Exception as exc:  # noqa: BLE001
        out['conflict'] = 'ERR ' + repr(exc)
    try:
        out['topic_members'] = [list(row) for row in db.execute(
            'SELECT seq,topic_id FROM dl_auto_topic_members WHERE scope=?', (scope,))]
        out['topics'] = db.execute('SELECT COUNT(*) FROM dl_auto_topics WHERE scope=?', (scope,)).fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        out['topic_members'] = 'ERR ' + repr(exc)
    return out


def instrument_hook(records):
    """包住真实的 on_llm_request 处理器，记录"它为什么返回/注入了什么"。"""
    wrapped = 0
    for handler in star_handlers_registry:
        if PLUGIN not in (getattr(handler, 'handler_module_path', '') or ''):
            continue
        if handler.handler_name != 'add_memory_context':
            continue
        original = handler.handler
        plugin = getattr(original, '__self__', None)

        async def wrapper(event, req, _orig=original, _plugin=plugin):
            record = {
                'event_id': getattr(event, 'message_obj', None) and
                            getattr(event.message_obj, 'message_id', None),
                'text': getattr(event, 'message_str', None),
                'parts_is_list': isinstance(
                    getattr(req, 'extra_user_content_parts', None), list),
                'before_parts': len(getattr(req, 'extra_user_content_parts', []) or []),
                'context_enabled': getattr(_plugin, 'context_enabled', None),
                'capture_pending': dict(getattr(_plugin, 'capture_pending_by_scope', {}) or {}),
                'capture_gaps': [list(x) for x in getattr(_plugin, 'capture_gaps', set())],
            }
            try:
                await _orig(event, req)
            except BaseException as exc:
                record['error'] = repr(exc)
                raise
            finally:
                record['after_parts'] = len(getattr(req, 'extra_user_content_parts', []) or [])
                record['injected'] = '[DL_MEMORY_V040]' in json.dumps(
                    [(getattr(p, 'text', '') or '') for p in
                     (getattr(req, 'extra_user_content_parts', []) or [])], ensure_ascii=False)
                records.append(record)
        handler.handler = wrapper
        wrapped += 1
    return wrapped


def bge_probe():
    script = HERE / 'bge_probe.py'
    process = subprocess.run(
        [sys.executable, str(script), '--model-dir', str(MODEL_DIR)],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=900)
    if process.returncode != 0:
        return {'ok': False, 'stderr': (process.stderr or '')[-1500:]}
    try:
        return json.loads(process.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {'ok': False, 'stdout': process.stdout[-1500:]}


async def main(output_path):
    facts = {'suite': 'real-astrbot-chain-v1', 'astrbot_root': str(INSTANCE),
             'model_dir': str(MODEL_DIR), 'user': USER, 'conversation': CONV}
    import astrbot
    facts['astrbot_version'] = getattr(astrbot, '__version__', '?')
    facts['python'] = sys.version.split()[0]

    lifecycle = AstrBotCoreLifecycle(LogBroker(), db_helper)
    # 每次探针从干净插件数据开始，证据可复现；只删本测试实例的数据。
    import shutil
    shutil.rmtree(PLUGIN_DATA, ignore_errors=True)
    await lifecycle.initialize()
    task = asyncio.create_task(lifecycle.start())
    await asyncio.sleep(1.0)
    try:
        # 1) 插件是否被真实 AstrBot 加载，钩子以什么事件类型注册
        handlers = []
        for handler in star_handlers_registry:
            if PLUGIN in (getattr(handler, 'handler_module_path', '') or ''):
                handlers.append({'name': handler.handler_name,
                                 'event': handler.event_type.name})
        stars = [star.name for star in lifecycle.plugin_manager.context.get_all_stars()]
        facts['plugin'] = {'loaded': PLUGIN in stars, 'stars': stars,
                           'handlers': sorted(handlers, key=lambda row: row['name'])}
        hook_records = []
        facts['instrumented_hooks'] = instrument_hook(hook_records)
        plugin = find_plugin(lifecycle)
        facts['plugin_state'] = {
            key: getattr(plugin, key, None) for key in
            ('enabled', 'capture_enabled', 'auto_organize_enabled', 'context_enabled',
             'followup_enabled', 'automatic_memory_enabled', 'model_dir',
             'context_budget_chars', 'auto_gap_recovery_enabled')}

        # 2) 先聊一句，让插件走"采集 → 排队 → 落库 → 后台整理"
        seed = await send_message(SEED_TEXT, 'real-seed-1')
        facts['seed'] = seed
        facts['capture_after_seed'] = read_captures()
        # 2.5) 模型仍在后台加载时发一条查询：回复不应被阻塞，语义本轮跳过。
        cold_before = len(mock_requests())
        cold_started = time.perf_counter()
        cold_reply = await send_message(QUERY_TEXT, 'real-cold-query-1')
        cold_request = await wait_for_provider_call(cold_before)
        cold_body = json.dumps((cold_request or {}).get('payload'), ensure_ascii=False)
        facts['cold_query'] = {
            'reply_ttfb_s': cold_reply['ttfb_s'],
            'hook_to_provider_s': round(time.perf_counter() - cold_started, 3),
            'embedder_state': (plugin.store.embedder_state
                               if plugin is not None and plugin.store is not None else None),
            'contains_memory_prefix': '[DL_MEMORY_V040]' in cold_body,
            'found': bool(cold_request),
        }
        # 首次编码要加载 BGE（池子单线程）；等索引出现再提问，否则注入会撞上 1.5s 超时。
        facts['bge_index_ready_after_s'] = await wait_for_vectors()
        facts['jobs_drained_after_s'] = await wait_for_jobs()
        facts['context_idle_after_s'] = await wait_for_context_idle(lifecycle)
        await asyncio.sleep(0.5)

        # 诊断：直接调用插件的上下文检索，判断"没有内容"还是"钩子提前返回"
        umo = 'webchat:FriendMessage:webchat!tester!' + CONV
        if plugin is not None:
            try:
                loop = asyncio.get_running_loop()
                packet, _next_focus, _ids = await loop.run_in_executor(
                    plugin.pool, plugin._context_work, USER, umo, QUERY_TEXT,
                    None, set(), 'diag-1', None)
                facts['direct_context'] = {'chars': len(packet), 'head': packet[:300]}
            except Exception as exc:  # noqa: BLE001 - 诊断失败也要记录
                facts['direct_context'] = {'error': repr(exc)}
            try:
                facts['retrieval_diag'] = await loop.run_in_executor(
                    plugin.pool, retrieval_diag, plugin, umo)
            except Exception as exc:  # noqa: BLE001
                facts['retrieval_diag'] = {'error': repr(exc)}

        # 3) 再问一句：钩子注入后，mock provider 会收到完整请求体。
        #    冷启动时首次查询会占住 single-flight 槽位并把 BGE 加载塞进单线程池，
        #    因此允许重试一次；两次都记录，不掩饰"第一次没注入"。
        attempts = []
        for index in range(1, 3):
            await wait_for_context_idle(lifecycle)
            before = len(mock_requests())
            started = time.perf_counter()
            reply = await send_message(QUERY_TEXT, 'real-query-%d' % index)
            request = await wait_for_provider_call(before)
            elapsed = time.perf_counter() - started
            payload = (request or {}).get('payload') or {}
            body = json.dumps(payload, ensure_ascii=False)
            attempt = {
                'attempt': index,
                'reply': reply,
                'hook_to_provider_s': round(elapsed, 3),
                'found': bool(request),
                'contains_memory_prefix': '[DL_MEMORY_V040]' in body,
                'contains_seed_text': SEED_TEXT in body,
                'contains_query_text': QUERY_TEXT in body,
                'request_chars': len(body),
                'message_roles': [m.get('role') for m in (payload.get('messages') or [])
                                  if isinstance(m, dict)],
            }
            attempts.append(attempt)
            if attempt['contains_memory_prefix']:
                break

        facts['injection_attempts'] = attempts
        facts['provider_request'] = attempts[-1]
        facts['hook_to_provider_s'] = attempts[-1]['hook_to_provider_s']
        facts['hook_records'] = hook_records

        # 4) 命令路径：/dlm 记忆状态 必须走真实命令过滤器并返回插件状态
        status = await send_message('/dlm 记忆状态', 'real-cmd-1')
        facts['command'] = status
        facts['capture_final'] = read_captures()

        # 5) 真实 BGE：维度、加载耗时、单条编码耗时（独立进程，避免污染本进程）
        facts['bge'] = bge_probe()
    finally:
        try:
            await lifecycle.stop()
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding='utf-8')

    print('AstrBot %s / Python %s' % (facts['astrbot_version'], facts['python']))
    print('插件加载=%s，注册钩子=%s' % (
        facts['plugin']['loaded'],
        ', '.join('%s(%s)' % (h['name'], h['event']) for h in facts['plugin']['handlers'])))
    print('采集：%d 条 %s' % (facts['capture_after_seed']['rows'],
                            facts['capture_after_seed']['contents']))
    print('注入：尝试 %d 次；命中前缀=%s；最后一次耗时=%.3fs' % (
        len(facts['injection_attempts']),
        any(row['contains_memory_prefix'] for row in facts['injection_attempts']),
        facts['hook_to_provider_s']))
    print('命令回复：%s' % (facts['command']['text'][:120].replace('\n', ' ')))
    print('BGE：%s' % json.dumps({k: v for k, v in (facts['bge'] or {}).items()
                                  if k in ('ok', 'dimension', 'load_seconds', 'encode_ms_per_item')},
                                 ensure_ascii=False))
    return 0 if (facts['plugin']['loaded'] and facts['provider_request']['found']) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default=str(TESTBED.parent / 'dev/reports/real_env.json'))
    args = parser.parse_args()
    target = Path(args.out)
    if not target.is_absolute():
        target = TESTBED.parent / target
    raise SystemExit(asyncio.run(main(target)))
