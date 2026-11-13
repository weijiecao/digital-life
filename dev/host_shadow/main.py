"""AstrBot shadow plugin for the offline Memory Engine.

Install this directory under AstrBot's data/plugins and set:
  DIGITAL_LIFE_DEV=/path/to/digital-life/dev
  ASTRBOT_ROOT=/path/to/AstrBot/instance
It reads each private conversation's stored history, feeds it through the native
input pipeline (validate -> dedupe -> engine inbox -> relation judge), and
observes LLM requests.

By default it never modifies the request or the reply. With
SHADOW_OUTPUT_MODE=inject it also prepares evidence-backed memory and adds a
temporary provider-facing content block; prompt, system prompt, contexts and
persisted history stay unchanged.

The judge is opt-in: without SHADOW_JUDGE_PROVIDER the plugin only queues
history and creates no events, so "no model" never becomes "the model said it
was a new event". Output injection is separately opt-in and defaults to off.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from astrbot.api.event import filter
from astrbot.api.star import Context, Star

DEV = Path(os.environ.get("DIGITAL_LIFE_DEV", "")).resolve()
if not DEV.exists():
    raise RuntimeError("DIGITAL_LIFE_DEV must point to the digital-life/dev directory")
sys.path.insert(0, str(DEV))

from memory_engine.engine import MemoryEngine
from memory_engine.host_budget import HostBudget
from memory_engine.native_host import (AstrBotProvider, HostInputError,
                                       NativeInputPump, NativeInputService,
                                       build_origin, conversation_persona,
                                       event_message, journal_path_for,
                                       read_conversation_identity, revision_for)
from memory_engine.native_judge import NativeRelationJudge, build_provider
from v1_5_consolidator.store import V15Store

try:
    from memory_engine.token_budget import TokenCounter
except Exception:
    TokenCounter = None

try:
    from memory_engine.output import MemoryOutputService
    from memory_output import OutputRequest, own_output_part
except Exception:  # 输出模块是可选件；缺失时只观察、不注入。
    MemoryOutputService = None
    OutputRequest = None
    own_output_part = None

try:
    from .console_api import ConsoleApi
except Exception:
    try:
        from console_api import ConsoleApi
    except Exception:  # 插件页控制台是可选件，不影响主链路。
        ConsoleApi = None


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default
    return value if low <= value <= high else default


def _positive_int(value) -> int | None:
    """Parse a positive integer limit, rejecting bools and junk."""
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


# 图片/音频等无法精确分词的部分，按固定保守值预留。
_MEDIA_TOKENS = 1024


def _json_dump(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


class MemoryShadow(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        root = Path(os.environ.get("ASTRBOT_ROOT", ".")).resolve()
        self.base = root / "data" / "plugin_data" / "astrbot_plugin_memory_shadow"
        self.base.mkdir(parents=True, exist_ok=True)
        self.log = self.base / "shadow_events.jsonl"
        # 操作者显式给出的来源字段；没有就跳过，不由插件猜。
        # 人格以宿主会话的 persona_id 为准；显式配置只在宿主没有给出时兜底，
        # 一旦显式固定，宿主人格不匹配就拒绝采集与注入，避免跨人格串记忆。
        self.persona = (os.environ.get("SHADOW_PERSONA_ID", "default").strip()
                        or "default")
        self.persona_pinned = bool(os.environ.get("SHADOW_PERSONA_ID", "").strip())
        self.judge_kind = (os.environ.get("SHADOW_JUDGE_PROVIDER", "none")
                           .strip().lower() or "none")
        self.judge_provider_id = os.environ.get("SHADOW_JUDGE_PROVIDER_ID", "").strip()
        self.judge_policy = (os.environ.get("SHADOW_JUDGE_POLICY", "native-input-0.2")
                             .strip() or "native-input-0.2")
        self.judge_budget = _env_int("SHADOW_JUDGE_BUDGET_CHARS", 6000, 500, 20000)
        self.judge_timeout = _env_int("SHADOW_JUDGE_TIMEOUT", 20, 1, 120)
        self.tick_seconds = float(_env_int("SHADOW_TICK_SECONDS", 5, 1, 3600))
        self.retry_every = _env_int("SHADOW_RETRY_EVERY", 12, 0, 1000)
        self.tick_limit = _env_int("SHADOW_TICK_LIMIT", 10, 1, 100)
        # 输出/注入默认关闭：显式 SHADOW_OUTPUT_MODE=inject 才会改模型输入。
        self.output_mode = (os.environ.get("SHADOW_OUTPUT_MODE", "off")
                            .strip().lower() or "off")
        if self.output_mode in ("on", "true", "1", "yes"):
            self.output_mode = "inject"
        if self.output_mode not in ("inject", "off"):
            self.output_mode = "off"
        self.memory_budget = _env_int("SHADOW_MEMORY_BUDGET", 512, 0, 8192)
        self.output_service = None
        self.output_error = None
        self.console_api = None
        self.console_error = None
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dlshadow")
        # 模型等待走独立线程池：聊天路径的数据库线程只做短事务。
        self.judge_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dljudge")
        self.loop = None
        self.store = None
        self.engine = None
        self.service = None
        self.pump = None
        self.error = None
        self.input_modes = {"conversation": 0, "event": 0, "skipped": 0,
                            "persona_mismatch": 0, "errors": 0}
        # 精确分词器在宿主线程池里懒加载，避免首次构造阻塞事件循环。
        self.counter = None
        self.counter_failed = False
        self.host_total = _env_int("SHADOW_HOST_TOTAL_TOKENS", 8192, 512, 2000000)
        self.host_total_explicit = bool(
            os.environ.get("SHADOW_HOST_TOTAL_TOKENS", "").strip())
        # Provider budgets are resolved from the active request, never from the
        # first entry in cmd_config.json: config order must not change safety.
        self.provider_budgets = []
        config_path = root / "data" / "cmd_config.json"
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
                for entry in config.get("provider", []):
                    limit = _positive_int(entry.get("max_context_tokens"))
                    if limit is None:
                        continue
                    models = entry.get("model")
                    if isinstance(models, str):
                        models = [models]
                    elif not isinstance(models, list):
                        models = []
                    names = {str(item) for item in models if item}
                    pid = str(entry.get("id", "") or "")
                    if pid:
                        names.add(pid)
                    self.provider_budgets.append((pid, frozenset(names), limit))
            except (OSError, ValueError, TypeError):
                self.provider_budgets = []
        self._register_console_apis(root)

    def _register_console_apis(self, root):
        """把控制台注册成插件页 Web API；失败不影响输入/输出主链路。"""
        register = getattr(getattr(self, "context", None), "register_web_api", None)
        if ConsoleApi is None or not callable(register):
            return
        read_only = (os.environ.get("SHADOW_CONSOLE_READONLY", "0")
                     .strip().lower() in ("1", "true", "yes", "on"))
        api = None
        try:
            api = ConsoleApi(DEV, root, read_only=read_only)
            count = api.register(register)
            if not count:
                raise RuntimeError("AstrBot web API helpers unavailable")
            self.console_api = api
        except Exception as exc:
            self.console_error = type(exc).__name__ + ": " + str(exc)
            if api is not None:
                api.close()

    def _tokens(self, text):
        if self.counter is not None:
            return self.counter.count(text or "")
        return (len(text or "") + 3) // 4

    def _persona_for(self, host_persona):
        """本次采集/查询应使用的人格；显式固定且与宿主不一致时返回 ``None``。"""
        host = (host_persona or "").strip() or None
        if self.persona_pinned:
            if host is not None and host != self.persona:
                return None
            return self.persona
        return host or self.persona

    def _part_tokens(self, part):
        """一个额外内容块占用的 token；无法精确计数的部分按保守值预留。"""
        if isinstance(part, dict):
            text, thinking = part.get("text"), part.get("think")
        else:
            text, thinking = getattr(part, "text", None), getattr(part, "think", None)
        if isinstance(text, str):
            return self._tokens(text)
        if isinstance(thinking, str):
            return self._tokens(thinking)
        return _MEDIA_TOKENS

    def _tool_tokens(self, toolset):
        """工具定义的开销；宿主没有暴露结构时按保守值预留。"""
        if toolset is None:
            return 0
        empty = getattr(toolset, "empty", None)
        if callable(empty):
            try:
                if empty():
                    return 0
            except Exception:
                pass
        tools = getattr(toolset, "tools", None)
        if isinstance(tools, list):
            if not tools:
                return 0
            payload = [{"name": getattr(item, "name", ""),
                        "description": getattr(item, "description", ""),
                        "parameters": getattr(item, "parameters", None)}
                       for item in tools]
            return self._tokens(_json_dump(payload))
        return _MEDIA_TOKENS

    def _request_tokens(self, req):
        """当前请求中除 system/contexts 外已占用的 token。

        包括当前 ``prompt``、其他插件追加的 ``extra_user_content_parts``、图片/音频、
        工具定义与上次工具调用结果；本插件上一次注入的临时块会被排除，避免重复计数。
        """
        tokens = self._tokens(getattr(req, "prompt", None) or "")
        own = own_output_part(req) if own_output_part is not None else None
        for part in getattr(req, "extra_user_content_parts", None) or []:
            if own is not None and part is own:
                continue
            tokens += self._part_tokens(part)
        for name in ("image_urls", "audio_urls"):
            tokens += _MEDIA_TOKENS * len(getattr(req, name, None) or [])
        tokens += self._tool_tokens(getattr(req, "func_tool", None))
        calls = getattr(req, "tool_calls_result", None)
        if calls:
            tokens += self._tokens(_json_dump(calls))
        return tokens

    async def _active_provider(self, event):
        """Resolve the provider actually used for this request.

        The hook only receives the request, so the event is the only place the
        host can expose an explicitly selected provider. If the host exposes a
        provider resolver, prefer it; otherwise fall back to the configured
        model match in ``_provider_limit``.
        """
        context = self.context
        selected = None
        get_extra = getattr(event, "get_extra", None)
        if callable(get_extra):
            try:
                selected = get_extra("selected_provider")
            except Exception:
                selected = None
        if isinstance(selected, str) and selected:
            getter = getattr(context, "get_provider_by_id", None)
            if callable(getter):
                try:
                    provider = getter(selected)
                except Exception:
                    provider = None
                if provider is not None:
                    return provider
        getter = getattr(context, "get_using_provider_async", None)
        if callable(getter):
            try:
                return await getter(umo=event.unified_msg_origin)
            except TypeError:
                try:
                    return await getter(event.unified_msg_origin)
                except Exception:
                    return None
            except Exception:
                return None
        getter = getattr(context, "get_using_provider", None)
        if callable(getter):
            try:
                return getter(event.unified_msg_origin)
            except TypeError:
                try:
                    return getter()
                except Exception:
                    return None
            except Exception:
                return None
        return None

    def _provider_limit(self, provider, req) -> int | None:
        """Read a provider's context limit, falling back to a unique model match."""
        if provider is not None:
            config = getattr(provider, "provider_config", None)
            if isinstance(config, dict):
                limit = _positive_int(config.get("max_context_tokens"))
                if limit is not None:
                    return limit
            limit = _positive_int(getattr(provider, "max_context_tokens", None))
            if limit is not None:
                return limit
        model = getattr(req, "model", None)
        if isinstance(model, str) and model:
            limits = {limit for _pid, names, limit in self.provider_budgets
                      if model in names}
            if len(limits) == 1:
                return limits.pop()
        return None

    def host_budget_for(self, req, provider=None) -> HostBudget:
        """按当前实际 provider 计算可分配给记忆的 token；不假设配置顺序。"""
        limit = self._provider_limit(provider, req)
        if limit is None:
            total = self.host_total
        elif self.host_total_explicit:
            total = min(self.host_total, limit)
        else:
            total = limit
        reserve = _env_int("SHADOW_OUTPUT_TOKENS", 800, 1, 200000)
        history = _json_dump(getattr(req, "contexts", None) or [])
        return HostBudget(total,
                          self._tokens(getattr(req, "system_prompt", None) or ""),
                          self._tokens(history), reserve,
                          self._request_tokens(req))

    def _build_judge(self, store):
        """按显式配置构造判别器；没有配置时返回 None（只入队、不建档）。"""
        if self.judge_kind in ("", "none", "off"):
            return None
        if self.judge_kind == "astrbot":
            if not self.judge_provider_id:
                raise ValueError("SHADOW_JUDGE_PROVIDER_ID is required for astrbot")
            provider = AstrBotProvider(self.context, self.judge_provider_id,
                                       self.loop or asyncio.get_event_loop(),
                                       timeout=self.judge_timeout)
        else:
            provider = build_provider(
                self.judge_kind,
                model=os.environ.get("SHADOW_JUDGE_MODEL", ""),
                api_key=os.environ.get("SHADOW_JUDGE_API_KEY", ""),
                base_url=os.environ.get("SHADOW_JUDGE_BASE_URL", ""),
                timeout=self.judge_timeout)
        if provider is None:
            return None
        return NativeRelationJudge(store, provider, policy_version=self.judge_policy,
                                   max_chars=self.judge_budget)

    def _ensure(self):
        """在宿主线程池线程里打开引擎与输入服务（sqlite 连接不跨线程）。"""
        if self.store is not None:
            return True
        if self.error is not None:
            return False
        if self.counter is None and not self.counter_failed and TokenCounter is not None:
            try:
                self.counter = TokenCounter()
            except Exception:
                # 没有精确分词器时不冒充预算：观察记录里 items 会为空。
                self.counter_failed = True
        try:
            store = V15Store(str(self.base / "shadow.sqlite3"))
            judge = self._build_judge(store)
            service = NativeInputService(
                store, journal_path_for(self.base), judge=judge,
                judge_revision=revision_for(judge, "none"))
        except Exception as exc:
            self.error = type(exc).__name__ + ": " + str(exc)
            return False
        self.store = store
        self.engine = MemoryEngine(store)
        self.service = service
        if self.output_mode == "inject":
            if MemoryOutputService is None or OutputRequest is None:
                self.output_error = "output module unavailable"
            else:
                try:
                    self.output_service = MemoryOutputService(self.engine, self.counter)
                except Exception as exc:
                    self.output_error = type(exc).__name__ + ": " + str(exc)
        return True

    async def _snapshot(self, event):
        """(会话标识, 历史, 宿主确认的人格, 模式)；不可用时返回 ``(None, None, None, 原因)``。"""
        umo = getattr(event, "unified_msg_origin", None)
        manager = getattr(self.context, "conversation_manager", None)
        if isinstance(umo, str) and callable(
                getattr(manager, "get_curr_conversation_id", None)):
            try:
                conversation_id, history, host_persona = await read_conversation_identity(
                    manager, umo)
            except HostInputError:
                return None, None, None, "invalid"
            except Exception:
                return None, None, None, "host_error"
            if conversation_id is None or history is None:
                return None, None, None, "no_conversation"
            return conversation_id, history, host_persona, "conversation"
        # 宿主没有会话管理器：只回退到当前这一条带稳定 ID 的消息。
        if isinstance(umo, str):
            return "event:" + umo, event_message(event), None, "event"
        return None, None, None, "no_origin"

    async def _conversation_identity(self, event):
        """只取（会话 ID，宿主人格），让观察钩子与采集钩子落在同一个作用域。

        这里不读取历史正文，避免观察路径多解析一份 JSON；人格仍来自宿主会话。
        """
        umo = getattr(event, "unified_msg_origin", None)
        manager = getattr(self.context, "conversation_manager", None)
        if isinstance(umo, str) and callable(
                getattr(manager, "get_curr_conversation_id", None)):
            try:
                conversation_id = await manager.get_curr_conversation_id(umo)
                if not conversation_id:
                    return None, None
                get_conv = getattr(manager, "get_conversation", None)
                conversation = (await get_conv(umo, conversation_id)
                                if callable(get_conv) else None)
                if conversation is None:
                    return conversation_id, None
                return conversation_id, conversation_persona(conversation)
            except Exception:
                return None, None
        return (("event:" + umo) if isinstance(umo, str) else None), None

    def _submit(self, history, origin, mode):
        if not self._ensure():
            return
        result = self.service.submit(history, origin)
        with self.lock:
            if result.get("status") == "staged":
                self.input_modes[mode] = self.input_modes.get(mode, 0) + 1
            else:
                self.input_modes["skipped"] += 1

    def _observe_work(self, scope, query, budget):
        if not self._ensure():
            return None
        packet = self.engine.build_rendered_context(
            scope, query, budget.memory_tokens, self.counter, host_budget=budget)
        return {"items": len(packet["items"]),
                "event_ids": [item["event_id"] for item in packet["items"]],
                "rendered_tokens": packet.get("rendered_tokens"),
                "host_memory_tokens": budget.memory_tokens}

    def _output_work(self, req, scope, query, provider=None):
        """在宿主线程池里完成检索、证据复验和临时内容块安装。"""
        if not self._ensure():
            return None
        if self.output_service is None or OutputRequest is None:
            return {"items": 0, "event_ids": [], "rendered_tokens": 0,
                    "output_status": "UNAVAILABLE",
                    "output_reason": self.output_error or "output module unavailable",
                    "budget_tokens": 0, "output_reasons": [],
                    "host_memory_tokens": 0}
        try:
            budget = self.host_budget_for(req, provider)
            request = OutputRequest(uuid.uuid4().hex, scope, query,
                                    self.memory_budget)
            output = self.output_service.prepare(request, host_budget=budget)
            # 提交前按当前请求复验预算：其他插件可能在准备期间追加内容。
            final = self.host_budget_for(req, provider)
            receipt = self.output_service.inject(req, output, request,
                                                 host_budget=final)
            return {"items": len(output.items),
                    "event_ids": [item.event_id for item in output.items],
                    "rendered_tokens": output.used_tokens,
                    "output_status": receipt.status,
                    "output_reason": receipt.reason,
                    "budget_tokens": output.budget_tokens,
                    "tokenizer": output.tokenizer,
                    "output_reasons": list(output.reasons),
                    "host_memory_tokens": final.memory_tokens}
        except Exception as exc:
            return {"items": 0, "event_ids": [], "rendered_tokens": 0,
                    "output_status": "REJECTED",
                    "output_reason": type(exc).__name__ + ": " + str(exc),
                    "budget_tokens": 0, "output_reasons": [],
                    "host_memory_tokens": 0}

    def _close(self):
        if self.service is not None:
            self.service.close()
            self.service = None
        if self.store is not None:
            self.store.close()
            self.store = None

    def _start_pump(self):
        if self.pump is not None or self.service is None:
            return
        self.pump = NativeInputPump(self.service, executor=self.pool,
                                    judge_executor=self.judge_pool,
                                    interval=self.tick_seconds,
                                    retry_every=self.retry_every,
                                    limit=self.tick_limit)
        self.pump.start()

    async def initialize(self):
        self.loop = asyncio.get_running_loop()
        await self.loop.run_in_executor(self.pool, self._ensure)
        if self.service is not None:
            # 启动恢复：先把 journal 里上次未投递的记录补投，再开始判别。
            try:
                await self.loop.run_in_executor(self.pool,
                                                self.service.deliver_pending)
            except Exception:
                pass
        self._start_pump()

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def capture(self, event):
        text = (getattr(event, "message_str", "") or "").strip()
        if not text or text.startswith(("/", "!")):
            return
        if self.loop is None:
            self.loop = asyncio.get_running_loop()
        conversation_id, history, host_persona, mode = await self._snapshot(event)
        if history is None:
            with self.lock:
                self.input_modes["skipped"] += 1
            return
        persona = self._persona_for(host_persona)
        if persona is None:
            # 显式固定的人格与宿主会话不一致：宁可跳过，也不写进别的作用域。
            with self.lock:
                self.input_modes["persona_mismatch"] += 1
            return
        origin = build_origin(event, persona=persona, conversation=conversation_id)
        if origin is None:
            with self.lock:
                self.input_modes["skipped"] += 1
            return
        try:
            await self.loop.run_in_executor(self.pool, self._submit, history, origin, mode)
        except Exception:
            with self.lock:
                self.input_modes["errors"] += 1
        self._start_pump()

    @filter.on_llm_request()
    async def observe(self, event, req):
        text = (getattr(event, "message_str", "") or "").strip()
        prompt = getattr(req, "prompt", None)
        # 输出模块要求 query 与 ProviderRequest.prompt 完全一致；观察也记录真正发送的文本。
        query = prompt if isinstance(prompt, str) and prompt.strip() else text
        if not query:
            return
        if self.loop is None:
            self.loop = asyncio.get_running_loop()
        conversation_id, host_persona = await self._conversation_identity(event)
        if not conversation_id:
            return
        persona = self._persona_for(host_persona)
        if persona is None:
            # 人格不匹配时不检索、不注入，避免跨人格串记忆。
            return
        origin = build_origin(event, persona=persona, conversation=conversation_id)
        if origin is None:
            return
        provider = await self._active_provider(event)
        budget = self.host_budget_for(req, provider)
        try:
            if self.output_mode == "inject":
                observed = await self.loop.run_in_executor(
                    self.pool, self._output_work, req, origin.scope_id, query,
                    provider)
            else:
                observed = await self.loop.run_in_executor(
                    self.pool, self._observe_work, origin.scope_id, query, budget)
        except Exception:
            return
        if observed is None:
            return
        record = {"ts": time.time(), "scope": origin.scope_id, "query": query,
                  "items": observed["items"], "event_ids": observed["event_ids"],
                  "host_memory_tokens": observed.get("host_memory_tokens",
                                                     budget.memory_tokens),
                  "rendered_tokens": observed["rendered_tokens"]}
        if self.output_mode == "inject":
            record.update({
                "output_status": observed.get("output_status"),
                "output_reason": observed.get("output_reason"),
                "budget_tokens": observed.get("budget_tokens"),
                "tokenizer": observed.get("tokenizer"),
                "output_reasons": observed.get("output_reasons", []),
            })
        with self.lock:
            with self.log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    async def terminate(self):
        if self.pump is not None:
            await self.pump.stop()
            self.pump = None
        loop = self.loop or asyncio.get_running_loop()
        # A judge thread may be waiting on run_coroutine_threadsafe() into this
        # loop, so shutdown must not run synchronously on the loop. Off-load the
        # wait, then close the store only after no judge can commit.
        try:
            await asyncio.to_thread(self.judge_pool.shutdown, True)
        except Exception:
            pass
        try:
            await loop.run_in_executor(self.pool, self._close)
        except Exception:
            pass
        if self.console_api is not None:
            self.console_api.close()
            self.console_api = None
        try:
            await asyncio.to_thread(self.pool.shutdown, True)
        except Exception:
            pass
