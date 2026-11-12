"""可观测的假 AstrBot 运行时：把 ① 平台层的接口假设变成可执行断言。

本机没有安装 AstrBot（离线），所以这里用最小替身复现插件**实际依赖**的五个接口：

1. 装饰器注册：`filter.event_message_type` / `filter.on_llm_request` / `filter.command`
2. 私聊事件字段：`message_str`、`message_obj.message_id`、`message_obj.message`（Plain 链）
3. 会话标识：`message_obj.sender.user_id`、`message_obj.group_id`、`unified_msg_origin`
4. 模型请求对象：`req.extra_user_content_parts`（list）+ `TextPart(text).mark_as_temp()`
5. 生命周期：`initialize()` / `terminate()`

`profile='v4293'` 是《工程进度核查报告》里描述的 AstrBot v4.29.3 契约；
`profile='legacy'` 故意不提供 `extra_user_content_parts`，用于验证"钩子不兼容时必须静默放行"。

**它不是 AstrBot 的实现**：替身只能证明"契约自洽、链路接通"，不能替代实机验收。
所有与真实平台有关的结论仍以 M1 实机结果为准（见 docs/baseline_E50F_real.json）。
"""

import asyncio
import importlib
import inspect
import sys
import time
import types
from pathlib import Path

PLUGIN_PACKAGE = 'astrbot_plugin_digital_memory_store'
INJECTION_PREFIX = '[DL_MEMORY_V040]'
STATE = {}


class FixtureEncoder:
    """受控编码器替身：只验证语义管线是否接通，不代表真实 BGE 的准确率。"""

    model_key = 'sim-astrbot-fixture-v1'

    def __init__(self, path):
        pass

    def encode_documents(self, texts):
        return [[1.0, 0.0] if '书' in text else [0.0, 1.0] for text in texts]

    def encode_queries(self, texts):
        return [[1.0, 0.0] if '书' in text else [0.0, 1.0] for text in texts]


class Registry:
    """记录插件在导入时通过装饰器注册了哪些钩子，以及用什么参数注册。"""

    def __init__(self):
        self.entries = {}

    def register(self, kind, options, fn):
        self.entries.setdefault(getattr(fn, '__name__', '?'), []).append(
            {'kind': kind, 'options': dict(options or {}), 'fn': fn})
        return fn

    def find(self, kind):
        return [(name, entry) for name, items in self.entries.items()
                for entry in items if entry['kind'] == kind]

    def kinds(self, name):
        return sorted(entry['kind'] for entry in self.entries.get(name, ()))


class Plain:
    def __init__(self, text):
        self.text = text


class Image:
    def __init__(self, url='https://example.invalid/image.png'):
        self.url = url


class Sender:
    def __init__(self, user_id):
        self.user_id = user_id


class MessageObject:
    def __init__(self, message_id, parts, sender, group_id, self_id):
        self.message_id = message_id
        self.message = list(parts)
        self.sender = Sender(sender)
        self.group_id = group_id
        self.self_id = self_id


class PrivateEvent:
    """私聊消息事件：字段集合按 AstrBot v4.29.3 的假设构造。"""

    def __init__(self, text, message_id, parts=None, sender='42',
                 origin='test:FriendMessage:42', group_id=None, self_id='bot'):
        self.message_str = text
        self.unified_msg_origin = origin
        self.message_obj = MessageObject(
            message_id, [Plain(text)] if parts is None else parts, sender, group_id, self_id)
        self.results = []

    def plain_result(self, text):
        self.results.append(text)
        return text


class TextPart:
    def __init__(self, text):
        self.text = text
        self.temporary = False
        self._no_save = False

    def mark_as_temp(self):
        self.temporary = True
        self._no_save = True
        return self


class LlmRequest:
    """模型请求对象。legacy profile 下完全没有 extra_user_content_parts。"""

    def __init__(self, profile='v4293'):
        if profile == 'v4293':
            self.extra_user_content_parts = []


class Injection:
    def __init__(self, elapsed, request):
        self.elapsed = elapsed
        self.request = request
        parts = getattr(request, 'extra_user_content_parts', None)
        self.parts = list(parts) if isinstance(parts, list) else []

    @property
    def count(self):
        return len(self.parts)

    @property
    def text(self):
        return '\n'.join(str(getattr(part, 'text', '')) for part in self.parts)

    @property
    def is_memory(self):
        return bool(self.parts) and INJECTION_PREFIX in self.text

    @property
    def temporary(self):
        return bool(self.parts) and all(getattr(p, 'temporary', False) for p in self.parts)

    def body(self):
        """去掉固定提示后的载荷，用于断言字符预算。"""
        text = self.text
        return text.split('\n', 1)[1] if '\n' in text else ''


def _build_stubs(registry, settings, profile):
    event_module = types.ModuleType('astrbot.api.event')

    class Filter:
        class EventMessageType:
            PRIVATE_MESSAGE = 'PRIVATE_MESSAGE'
            GROUP_MESSAGE = 'GROUP_MESSAGE'

        @staticmethod
        def command(name, *args, **kwargs):
            return lambda fn: registry.register('command', {'name': name}, fn)

        @staticmethod
        def event_message_type(kind, *args, **kwargs):
            return lambda fn: registry.register('event_message_type', {'type': kind}, fn)

        @staticmethod
        def on_llm_request(*args, **kwargs):
            return lambda fn: registry.register('on_llm_request', {}, fn)

    event_module.filter = Filter
    event_module.AstrMessageEvent = object

    star_module = types.ModuleType('astrbot.api.star')

    class Star:
        def __init__(self, context):
            self.context = context

    star_module.Context = object
    star_module.Star = Star

    path_module = types.ModuleType('astrbot.core.utils.astrbot_path')
    path_module.get_astrbot_data_path = lambda: settings['data_path']

    message_module = types.ModuleType('astrbot.core.agent.message')
    message_module.TextPart = TextPart

    web_module = types.ModuleType('astrbot.api.web')

    class _WebRequest:
        query = {}

        async def json(self, default=None):
            return default

    web_module.request = _WebRequest()
    web_module.json_response = lambda data=None, **kwargs: data
    web_module.error_response = lambda message, **kwargs: {'error': message}

    modules = {
        'astrbot': types.ModuleType('astrbot'),
        'astrbot.api': types.ModuleType('astrbot.api'),
        'astrbot.api.event': event_module,
        'astrbot.api.star': star_module,
        'astrbot.api.web': web_module,
        'astrbot.core': types.ModuleType('astrbot.core'),
        'astrbot.core.utils': types.ModuleType('astrbot.core.utils'),
        'astrbot.core.utils.astrbot_path': path_module,
        'astrbot.core.agent': types.ModuleType('astrbot.core.agent'),
        'astrbot.core.agent.message': message_module,
    }
    return modules


def load(profile='v4293'):
    """在独立替身环境里导入插件主模块，返回 (module, registry, settings)。"""
    if profile in STATE:
        return STATE[profile]
    registry = Registry()
    settings = {'data_path': '.'}
    sys.modules.update(_build_stubs(registry, settings, profile))
    sys.modules.pop(PLUGIN_PACKAGE + '.main', None)
    module = importlib.import_module(PLUGIN_PACKAGE + '.main')
    base = module.Store
    module.Store = lambda root, model: base(root, model, FixtureEncoder)
    STATE[profile] = (module, registry, settings)
    return STATE[profile]


class SimRuntime:
    """一个可观测插件实例：装配、私聊事件派发、模型请求注入、生命周期。"""

    def __init__(self, data_root, config, *, profile='v4293'):
        self.module, self.registry, self.settings = load(profile)
        self.profile = profile
        self.settings['data_path'] = str(data_root)
        self.config = dict(config)
        self.plugin = self.module.DigitalMemoryStore(None, self.config)
        self.injections = []

    async def start(self):
        await self.plugin.initialize()
        return self

    async def receive(self, text, *, message_id=None, parts=None, sender='42',
                      origin='test:FriendMessage:42', group_id=None, self_id='bot'):
        """派发一条私聊消息，走与真实框架相同的钩子入口。"""
        event = PrivateEvent(text, text if message_id is None else message_id,
                             parts=parts, sender=sender,
                             origin=origin, group_id=group_id, self_id=self_id)
        command_names = {name for name, _ in self.registry.find('command')}
        for name, entry in self.registry.find('event_message_type'):
            # 真实框架里 command 钩子只在消息以命令前缀开头时触发。
            if name in command_names and not text.lstrip().startswith(('/', '!', '！')):
                continue
            await self._call(entry['fn'], event)
        return event

    async def _call(self, fn, *args):
        bound = fn.__get__(self.plugin, type(self.plugin))
        if inspect.isasyncgenfunction(bound):
            return [item async for item in bound(*args)]
        return await bound(*args)

    async def llm_request(self, event, request=None):
        """派发一次模型请求，返回注入结果与耗时（P95 指标的采集点）。

        传入同一个 `request` 可复现"同一请求被钩子重复处理"的真实场景。
        """
        request = request if request is not None else LlmRequest(self.profile)
        started = time.perf_counter()
        for _, entry in self.registry.find('on_llm_request'):
            await self._call(entry['fn'], event, request)
        elapsed = time.perf_counter() - started
        injection = Injection(elapsed, request)
        self.injections.append(injection)
        return injection

    async def command(self, text, **kwargs):
        event = PrivateEvent(text, text, **kwargs)
        for _, entry in self.registry.find('command'):
            event.results.extend(await self._call(entry['fn'], event))
        return event

    async def drain_auto(self):
        """同步等待一次后台整理（等价于 _auto_loop 的一次 tick，但不必真睡 2 秒）。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.plugin.pool, self.plugin._auto_work)

    async def query(self, sql, params=()):
        """数据库连接绑定在插件线程池线程上，所有读取都必须回到该线程。"""
        loop = asyncio.get_running_loop()

        def read():
            # Store 在该线程创建；重启后尚未有消息时数据库可能还没打开。
            if self.plugin.store is None:
                self.plugin._ensure_store()
            return self.plugin.store.flow.db.execute(sql, params).fetchall()

        return await loop.run_in_executor(self.plugin.pool, read)

    async def scalar(self, sql, params=()):
        rows = await self.query(sql, params)
        return rows[0][0] if rows else None

    async def execute(self, sql, params=(), commit=True):
        """在插件线程池里写入测试数据（例如把采集时间改早，模拟"先聊、后问"）。"""
        loop = asyncio.get_running_loop()

        def write():
            if self.plugin.store is None:
                self.plugin._ensure_store()
            database = self.plugin.store.flow.db
            result = database.execute(sql, params)
            if commit:
                database.commit()
            return result

        return await loop.run_in_executor(self.plugin.pool, write)

    async def jobs(self, status=None):
        rows = await self.query('SELECT status,COUNT(*) FROM dl_auto_jobs GROUP BY status')
        counts = {row[0]: row[1] for row in rows}
        return counts.get(status, 0) if status else counts

    async def stop(self):
        await self.plugin.terminate()


def contract_summary(registry=None):
    """导出"钩子契约表"，供报告引用：哪个方法用哪种过滤器注册、签名是什么。"""
    module, loaded, _ = load()
    registry = registry or loaded
    cls = module.DigitalMemoryStore
    rows = []
    for name, entries in registry.entries.items():
        fn = getattr(cls, name, None)
        if fn is None:
            continue
        bound = inspect.signature(fn)
        rows.append({
            'method': name,
            'kinds': sorted(entry['kind'] for entry in entries),
            'options': [entry['options'] for entry in entries],
            'parameters': list(bound.parameters),
            'async': inspect.iscoroutinefunction(fn),
            'async_generator': inspect.isasyncgenfunction(fn),
        })
    return sorted(rows, key=lambda row: row['method'])


def report_contract():
    return {'suite': 'astrbot-hook-contract-v1', 'profile': 'v4293-assumed',
            'verified_on': 'simulated runtime only (no AstrBot installed)',
            'hooks': contract_summary()}
