"""Unified AstrBot plugin for the digital-life memory system.

One Star owns native input, background judging, retrieval/output and the
console. The published zip is self-contained; source-tree development may set
DIGITAL_LIFE_DEV to ``shared/``. Modes are OFF / COLLECT / ASSIST; only ASSIST
attaches a temporary memory block to the provider request, and only explicitly
allowed private senders are captured.

The judge is opt-in: without a judge provider the plugin only queues history and
creates no events, so "no model" never becomes "the model said it was a new
event". Output injection is separately opt-in and defaults to off.
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

_DEV_ENV = os.environ.get("DIGITAL_LIFE_DEV", "").strip()
DEV = (Path(_DEV_ENV).resolve() if _DEV_ENV
       else Path(__file__).resolve().parent)
if not (DEV / "memory_engine").is_dir():
    raise RuntimeError(
        "DIGITAL_LIFE_DEV must point to the dev tree (or the bundled plugin "
        "must contain memory_engine)")
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

try:
    from .config import plugin_version as _plugin_version
    from .config import resolve as resolve_config
except Exception:
    from config import plugin_version as _plugin_version
    from config import resolve as resolve_config

try:
    from .runtime.ownership import (DataDirectoryLock, OwnershipError,
                                    legacy_plugin_active)
except Exception:
    from runtime.ownership import (DataDirectoryLock, OwnershipError,
                                   legacy_plugin_active)


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


def _load_json_file(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


try:
    from .application.storage import GenerationStore
    from .application.service import ApplicationService
    from .application.migration import LegacyMigration
    from .application.compatibility import FollowupContext
except ImportError:
    from application.storage import GenerationStore
    from application.service import ApplicationService
    from application.migration import LegacyMigration
    from application.compatibility import FollowupContext
from astrbot_memory_input.unified import UnifiedJournal


class DigitalLifePlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        root = Path(os.environ.get("ASTRBOT_ROOT", ".")).resolve()
        self.base = (root / "data" / "plugin_data"
                     / "astrbot_plugin_digital_life")
        legacy = (root / "data" / "plugin_data"
                  / "astrbot_plugin_memory_shadow")
        self.legacy_data_dir = False
        if not self.base.exists() and legacy.exists():
            # v0.5.1 shadow data is schema-compatible; continue using it rather
            # than silently starting with an empty database.
            self.base = legacy
            self.legacy_data_dir = True
        self.base.mkdir(parents=True, exist_ok=True)
        self.log = self.base / "shadow_events.jsonl"
        self.commit_state_path = self.base / "commit_state.json"
        self.ownership_status = "unknown"
        self.ownership_error = None
        self.ownership_blocked = False
        self._ownership = None
        if legacy_plugin_active(context):
            # Do not write the legacy database while the old Star is loaded.
            self.ownership_status = "blocked-legacy"
            self.ownership_error = "astrbot_plugin_memory_shadow is active"
            self.ownership_blocked = True
        else:
            lock = DataDirectoryLock(self.base)
            try:
                lock.acquire()
                self._ownership = lock
                self.ownership_status = "owned"
            except OwnershipError as exc:
                self.ownership_status = "blocked-locked"
                self.ownership_error = str(exc)
                self.ownership_blocked = True
        try:
            self.generations = GenerationStore(self.base, recover=not self.ownership_blocked)
        except Exception:
            if self._ownership is not None:
                self._ownership.release()
            raise
        self.data_dir = self.generations.active
        self.commit_state_path = self.data_dir / 'commit_state.json'
        self.log = self.data_dir / 'shadow_events.jsonl'
        options = config if isinstance(config, dict) else {}
        self.preference_rank_enabled = options.get('preference_rank_enabled', True) is True
        self.followup = FollowupContext(options.get('followup_enabled', True) is True)
        self.proactive_enabled = options.get('proactive_enabled', False) is True
        self.application = ApplicationService(self)
        self.migration = None
        self.config_error = None
        try:
            self.cfg = resolve_config(config if isinstance(config, dict) else {},
                                      os.environ)
        except ValueError as exc:
            self.config_error = str(exc)
            self.cfg = resolve_config({"mode": "COLLECT",
                                       "allowed_sender_ids": ""}, os.environ)
        self.mode = self.cfg.mode
        self.allowed = set(self.cfg.allowed_senders)
        # 操作者显式给出的来源字段；没有就跳过，不由插件猜。
        # 人格以宿主会话的 persona_id 为准；显式配置只在宿主没有给出时兜底，
        # 一旦显式固定，宿主人格不匹配就拒绝采集与注入，避免跨人格串记忆。
        self.persona = (os.environ.get("SHADOW_PERSONA_ID", "default").strip()
                        or "default")
        self.persona_pinned = bool(os.environ.get("SHADOW_PERSONA_ID", "").strip())
        self.judge_kind = self.cfg.judge_kind
        self.judge_provider_id = self.cfg.judge_provider_id
        self.judge_source = self.cfg.judge_source
        self.judge_policy = self.cfg.judge_policy
        self.judge_budget = self.cfg.judge_budget_chars
        self.judge_timeout = self.cfg.judge_timeout_seconds
        self.tick_seconds = float(self.cfg.tick_seconds)
        self.retry_every = self.cfg.retry_every
        self.tick_limit = self.cfg.tick_limit
        # mode 是唯一注入开关；不读取隐藏的 output_mode 覆盖字段。
        self.output_mode = self.cfg.output_mode
        self.memory_budget = self.cfg.memory_budget_tokens
        self.console_read_only = self.cfg.console_read_only
        self.config_generation = 1
        self.store_generation = self.generations.current["epoch"]
        self.last_capture_at = None
        self.last_commit_at = self._load_commit_time()
        self.last_commit_observed_at = None
        self.last_injection_at = None
        self.last_error = None
        self._inbox_snapshot = {}
        self._inbox_error = None
        self._last_commit_marker = 0
        self.output_service = None
        self.output_error = None
        self.console_api = None
        self.console_error = None
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dlshadow")
        # 模型等待走独立线程池：聊天路径的数据库线程只做短事务。
        self.judge_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dljudge")
        # NEW -> RUNNING -> CLOSING -> CLOSED. Callbacks must not revive work
        # after CLOSING starts, even if they entered while RUNNING.
        self.lifecycle = "NEW"
        self._terminate_task = None
        self._ownership_task = None
        self._ownership_interval = 2.0
        self._pump_stop_scheduled = False
        self._pump_stopped = False
        self._judge_shutdown = False
        self._store_closed = False
        self._console_closed = False
        self._pool_shutdown = False
        self._cleanup_errors = []
        self.terminate_error = None
        self.loop = None
        self.store = None
        self.engine = None
        self.service = None
        self.pump = None
        self.error = None
        self.input_modes = {"conversation": 0, "event": 0, "skipped": 0,
                            "persona_mismatch": 0, "not_allowed": 0,
                            "mode_off": 0, "errors": 0}
        # 精确分词器在宿主线程池里懒加载，避免首次构造阻塞事件循环。
        self.counter = None
        self.counter_failed = False
        self.host_total = self.cfg.host_total_tokens
        self.host_total_explicit = self.cfg.host_total_explicit
        # Provider budgets are resolved from the active request, never from the
        # first entry in cmd_config.json: config order must not change safety.
        self.provider_budgets = []
        config_path = root / "data" / "cmd_config.json"
        if config_path.exists():
            try:
                provider_config = json.loads(config_path.read_text(encoding="utf-8"))
                for entry in provider_config.get("provider", []):
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
        api = None
        try:
            api = ConsoleApi(DEV, root, read_only=(self.console_read_only
                                                   or self.ownership_blocked),
                             plugin_name="astrbot_plugin_digital_life",
                             data_dir=self.data_dir,
                             status_provider=self.status_async, application=self.application)
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

    def _sender_allowed(self, event) -> bool:
        """Only explicitly configured private users are captured."""
        if not self.allowed:
            return False
        obj = getattr(event, "message_obj", None)
        sender = getattr(getattr(obj, "sender", None), "user_id", None)
        if sender is None:
            getter = getattr(event, "get_sender_id", None)
            sender = getter() if callable(getter) else None
        return sender is not None and str(sender) in self.allowed

    def status(self) -> dict:
        """Return a secret-free snapshot; never touches SQLite from the loop."""
        return {
            "schema_version": 1,
            "plugin_version": _plugin_version(),
            "config_generation": self.config_generation,
            "store_generation": self.store_generation,
            "mode": self.mode,
            "output_mode": self.output_mode,
            "lifecycle": self.lifecycle,
            "ownership": getattr(self, "ownership_status", "unknown"),
            "ownership_error": getattr(self, "ownership_error", None),
            "provider_status": "configured" if self.judge_kind != "none" else "missing",
            "judge_provider": self.judge_kind,
            "judge_source": self.judge_source,
            "tokenizer_status": ("ready" if self.counter is not None
                                 else "unavailable" if self.counter_failed
                                 else "lazy"),
            "allowed_senders": len(self.allowed),
            "legacy_data_dir": self.legacy_data_dir,
            "data_dir": str(self.data_dir),
            "reconciliation_pending": getattr(self,"_reconciliation_pending",0),
            "input_modes": dict(self.input_modes),
            "inbox": dict(self._inbox_snapshot),
            "inbox_error": self._inbox_error,
            "config_error": self.config_error,
            "error": self.error,
            "output_error": self.output_error,
            "console_error": self.console_error,
            "cleanup_error": self.terminate_error,
            "last_capture_at": self.last_capture_at,
            "last_commit_at": self.last_commit_at,
            "last_commit_observed_at": self.last_commit_observed_at,
            "last_injection_at": self.last_injection_at,
            "last_error": self.last_error,
        }

    def _status_work(self) -> dict:
        """Runs on the database thread; only this thread may query the inbox."""
        inbox = {}
        error = None
        marker = self._last_commit_marker
        if self.service is not None:
            try:
                inbox = dict(self.service.inbox.counts())
                journal = getattr(self.service, 'journal', None)
                self._reconciliation_pending = journal.pending() if isinstance(journal, UnifiedJournal) else 0
                stats = getattr(self.service, "stats", {}) or {}
                marker = int(stats.get("created", 0)) + int(stats.get("attached", 0))
            except Exception as exc:
                error = "%s: %s" % (type(exc).__name__, exc)
        return {"inbox": inbox, "error": error, "marker": marker}

    async def status_async(self) -> dict:
        """Console status: query DB state on the owning thread, not the loop."""
        if (self.lifecycle in ("CLOSING", "CLOSED")
                or getattr(self.pool, "_shutdown", False)):
            return self.status()
        loop = self.loop
        if loop is None or loop.is_closed():
            loop = asyncio.get_running_loop()
        try:
            snapshot = await loop.run_in_executor(self.pool, self._status_work)
            self._inbox_snapshot = snapshot["inbox"]
            self._inbox_error = snapshot["error"]
            if snapshot["marker"] > self._last_commit_marker:
                self._last_commit_marker = snapshot["marker"]
                self.last_commit_observed_at = time.time()
        except Exception as exc:
            self._inbox_error = "%s: %s" % (type(exc).__name__, exc)
        return self.status()

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

    def _load_commit_time(self):
        value = _load_json_file(self.commit_state_path).get("last_commit_at")
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _record_commit(self, result) -> None:
        """Called on the database thread after a successful commit."""
        if not (result.get("created") or result.get("attached")):
            return
        now = time.time()
        self.last_commit_at = now
        try:
            temp = self.commit_state_path.with_suffix(".json.tmp")
            temp.write_text(json.dumps({"last_commit_at": now}) + "\n",
                            encoding="utf-8")
            os.replace(temp, self.commit_state_path)
        except OSError:
            pass

    def _ensure(self):
        """在宿主线程池线程里打开引擎与输入服务（sqlite 连接不跨线程）。"""
        if self.ownership_blocked:
            return False
        if self.application.busy and not self.application.opening:
            return False
        if self.lifecycle in ("CLOSING", "CLOSED"):
            return False
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
            store = V15Store(str(self.data_dir / "shadow.sqlite3"))
            judge = self._build_judge(store)
            service = NativeInputService(
                store, journal_path_for(self.data_dir), judge=judge,
                judge_revision=revision_for(judge, "none"),
                on_commit=self._record_commit, journal_factory=UnifiedJournal)
        except Exception as exc:
            self.error = type(exc).__name__ + ": " + str(exc)
            return False
        self.store = store
        self.engine = MemoryEngine(store)
        self.engine.preference_rank_enabled = self.preference_rank_enabled
        self.service = service
        self.migration = LegacyMigration(store, service, self.data_dir)
        store.db.execute('CREATE TABLE IF NOT EXISTS proactive_receipts(scope TEXT,request_id TEXT,query TEXT,PRIMARY KEY(scope,request_id))')
        store.db.commit()
        # journal 先写的撤回屏障在启动时补齐业务失效，避免中断重放复活。
        for (key,) in service.journal.db.execute('SELECT key FROM input_tombstone').fetchall():
            row=store.db.execute('SELECT scope_id,source_seq FROM native_input_inbox WHERE key=?',(key,)).fetchone()
            if row:
                with store.unit_of_work():
                    self.engine.delete_source(row[0],row[1])
                    store.db.execute("UPDATE native_input_inbox SET state='WITHDRAWN' WHERE key=?",(key,))
        if self.output_mode == "inject":
            if MemoryOutputService is None or OutputRequest is None:
                self.output_error = "output module unavailable"
            else:
                try:
                    self.output_service = MemoryOutputService(self.engine, self.counter, generation=self.store_generation)
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
        if not self._commit_allowed():
            return
        if not self._ensure():
            return
        result = self.service.submit(history, origin)
        with self.lock:
            if result.get("status") == "staged":
                self.input_modes[mode] = self.input_modes.get(mode, 0) + 1
            else:
                self.input_modes["skipped"] += 1

    def _append(self, message, origin):
        if not self._commit_allowed() or not self._ensure():
            if self.application.busy:self.application.note_gap(origin)
            return False
        self.service.submit_message(message, origin)
        self.followup.remember(origin, message)
        return True

    def _observe_work(self, scope, query, budget):
        if not self._commit_allowed() or not self._ensure():
            return None
        packet = self.engine.build_rendered_context(
            scope, query, budget.memory_tokens, self.counter, host_budget=budget)
        return {"items": len(packet["items"]),
                "event_ids": [item["event_id"] for item in packet["items"]],
                "rendered_tokens": packet.get("rendered_tokens"),
                "host_memory_tokens": budget.memory_tokens}

    def _output_work(self, req, scope, query, provider=None, origin=None, request_id=None):
        """在宿主线程池里完成检索、证据复验和临时内容块安装。"""
        if (self.mode != "ASSIST" or not self._commit_allowed()
                or not self._ensure()):
            return None
        if self.output_service is None or OutputRequest is None:
            return {"items": 0, "event_ids": [], "rendered_tokens": 0,
                    "output_status": "UNAVAILABLE",
                    "output_reason": self.output_error or "output module unavailable",
                    "budget_tokens": 0, "output_reasons": [],
                    "host_memory_tokens": 0}
        try:
            budget = self.host_budget_for(req, provider)
            retrieval = self.followup.resolve(origin, query) if origin is not None else query
            request = OutputRequest(request_id or uuid.uuid4().hex, scope, query,
                                    self.memory_budget, self.store_generation,
                                    retrieval if retrieval != query else None)
            output = self.output_service.prepare(request, host_budget=budget)
            # 提交前按当前请求复验预算：其他插件可能在准备期间追加内容。
            final = self.host_budget_for(req, provider)
            if not self._commit_allowed():
                self.output_service.adapter.clear(req)
                return None
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

    async def _reconcile_ownership(self) -> bool:
        """Re-check the old Star and the data-directory lock before work."""
        if self.lifecycle != "RUNNING" or self.application.busy:
            return False
        loop = self.loop
        if loop is None or loop.is_closed():
            loop = asyncio.get_running_loop()
            self.loop = loop
        try:
            legacy = legacy_plugin_active(self.context)
        except Exception:
            legacy = False
        if legacy:
            if not self.ownership_blocked:
                self.ownership_blocked = True
                self.ownership_status = "blocked-legacy"
                self.ownership_error = "astrbot_plugin_memory_shadow is active"
            if self.pump is not None:
                await self.pump.stop()
                self.pump = None
            if self.store is not None:
                try:
                    await loop.run_in_executor(self.pool, self._close)
                except Exception:
                    pass
                self._inbox_snapshot = {}
            if self.console_api is not None:
                self.console_api.read_only = True
                if self.console_api.console is not None:
                    self.console_api.console.read_only = True
            return False
        if not self.ownership_blocked:
            return True
        if self._ownership is None:
            lock = DataDirectoryLock(self.base)
            try:
                lock.acquire()
            except OwnershipError as exc:
                self.ownership_status = "blocked-locked"
                self.ownership_error = str(exc)
                return False
            self._ownership = lock
            # 等待占用期间，前一个实例可能已恢复到新代次，不能打开旧缓存路径。
            self.generations = GenerationStore(self.base)
            self.application._paths()
        self.ownership_blocked = False
        self.ownership_status = "owned"
        self.ownership_error = None
        try:
            await loop.run_in_executor(self.pool, self._ensure)
            if self.service is not None:
                await loop.run_in_executor(self.pool,
                                           self.service.deliver_pending)
            self._start_pump()
        except Exception as exc:
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
        if self.console_api is not None:
            self.console_api.read_only = self.console_read_only
            if self.console_api.console is not None:
                self.console_api.console.read_only = True
        return True

    async def _ownership_loop(self):
        while self.lifecycle == "RUNNING":
            try:
                await self._reconcile_ownership()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = "%s: %s" % (type(exc).__name__, exc)
            await asyncio.sleep(self._ownership_interval)

    def _start_ownership_monitor(self):
        if self._ownership_task is None or self._ownership_task.done():
            self._ownership_task = asyncio.create_task(self._ownership_loop())

    def _commit_allowed(self) -> bool:
        """Synchronous fence checked on the database thread before commit."""
        if self.lifecycle != 'RUNNING' or self.ownership_blocked or self.application.busy:
            return False
        # 数据库队列可能晚于模型后的检查：这里必须重新读宿主的当前活动状态。
        try:
            active = legacy_plugin_active(self.context)
        except Exception:
            return False
        if active:
            self.ownership_blocked = True
            self.ownership_status = 'blocked-legacy'
            self.ownership_error = 'astrbot_plugin_memory_shadow is active'
            return False
        return True

    async def _pump_guard(self) -> bool:
        """Last check immediately before a background tick can write."""
        if self.lifecycle != "RUNNING" or self.application.busy:
            return False
        try:
            legacy = legacy_plugin_active(self.context)
        except Exception:
            legacy = False
        if legacy:
            if not self.ownership_blocked:
                self.ownership_blocked = True
                self.ownership_status = "blocked-legacy"
                self.ownership_error = "astrbot_plugin_memory_shadow is active"
            if self.pump is not None and not self._pump_stop_scheduled:
                self._pump_stop_scheduled = True
                asyncio.create_task(self._stop_conflict_pump())
            return False
        return not self.ownership_blocked

    async def _stop_conflict_pump(self):
        try:
            await asyncio.sleep(0)
            pump, self.pump = self.pump, None
            if pump is not None:
                await pump.stop()
        finally:
            self._pump_stop_scheduled = False

    def _start_pump(self):
        if (self.application.busy or self.ownership_blocked or self.mode == "OFF"
                or self.lifecycle != "RUNNING"
                or self.pump is not None or self.service is None):
            return
        self.pump = NativeInputPump(self.service, executor=self.pool,
                                    judge_executor=self.judge_pool,
                                    interval=self.tick_seconds,
                                    retry_every=self.retry_every,
                                    limit=self.tick_limit,
                                    guard=self._pump_guard,
                                    commit_guard=self._commit_allowed)
        self.pump.start()

    async def initialize(self):
        if self.lifecycle != "NEW":
            return
        self.lifecycle = "RUNNING"
        self.loop = asyncio.get_running_loop()
        self._start_ownership_monitor()
        if self.ownership_blocked:
            return
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
        if self.application.busy:
            self.application.note_gap(None)
            return
        if self.lifecycle != "RUNNING":
            return
        if not await self._reconcile_ownership():
            return
        if self.mode == "OFF":
            with self.lock:
                self.input_modes["mode_off"] += 1
            return
        if not self._sender_allowed(event):
            with self.lock:
                self.input_modes["not_allowed"] += 1
            return
        text = (getattr(event, "message_str", "") or "").strip()
        if not text or text.startswith(("/", "!")):
            return
        if self.loop is None:
            self.loop = asyncio.get_running_loop()
        conversation_id, history, host_persona, mode = await self._snapshot(event)
        if self.lifecycle != "RUNNING":
            return
        if history is None and event_message(event) is None:
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
        if self.lifecycle != "RUNNING":
            return
        try:
            # 实时消息先独立落 journal；历史仍走完整快照校验，不能伪装为追加。
            current = event_message(event)
            if current:
                await self.loop.run_in_executor(self.pool, self._append, current[0], origin)
            if history is not None and mode == 'conversation':
                await self.loop.run_in_executor(self.pool, self._submit, history, origin, mode)
            self.last_capture_at = time.time()
        except Exception as exc:
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            with self.lock:
                self.input_modes["errors"] += 1
        self._start_pump()

    @filter.on_llm_request()
    async def observe(self, event, req):
        if self.lifecycle != "RUNNING":
            return
        if not await self._reconcile_ownership():
            return
        if self.mode == "OFF":
            with self.lock:
                self.input_modes["mode_off"] += 1
            return
        if not self._sender_allowed(event):
            with self.lock:
                self.input_modes["not_allowed"] += 1
            return
        text = (getattr(event, "message_str", "") or "").strip()
        prompt = getattr(req, "prompt", None)
        # 输出模块要求 query 与 ProviderRequest.prompt 完全一致；观察也记录真正发送的文本。
        query = prompt if isinstance(prompt, str) and prompt.strip() else text
        if not query:
            return
        if self.loop is None:
            self.loop = asyncio.get_running_loop()
        conversation_id, host_persona = await self._conversation_identity(event)
        if self.lifecycle != "RUNNING" or not conversation_id:
            return
        persona = self._persona_for(host_persona)
        if persona is None:
            # 人格不匹配时不检索、不注入，避免跨人格串记忆。
            return
        origin = build_origin(event, persona=persona, conversation=conversation_id)
        if origin is None:
            return
        current = event_message(event)
        if current and not text.startswith(('/', '!')):
            try:
                await self.loop.run_in_executor(self.pool, self._append, current[0], origin)
            except Exception as exc:
                self.last_error = type(exc).__name__ + ': ' + str(exc)
        provider = await self._active_provider(event)
        if self.lifecycle != "RUNNING":
            return
        budget = self.host_budget_for(req, provider)
        try:
            if self.mode == "ASSIST" and self.output_mode == "inject":
                observed = await self.loop.run_in_executor(
                    self.pool, self._output_work, req, origin.scope_id, query,
                    provider, origin)
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
        if self.mode == "ASSIST" and self.output_mode == "inject":
            record.update({
                "output_status": observed.get("output_status"),
                "output_reason": observed.get("output_reason"),
                "budget_tokens": observed.get("budget_tokens"),
                "tokenizer": observed.get("tokenizer"),
                "output_reasons": observed.get("output_reasons", []),
            })
            if observed.get("output_status") == "INJECTED":
                self.last_injection_at = time.time()
        with self.lock:
            with self.log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    async def terminate(self):
        """Stop accepting work, drain judges, then close storage (retryable)."""
        if self.lifecycle == "CLOSED":
            return
        self.lifecycle = "CLOSING"
        if self._terminate_task is None or self._terminate_task.done():
            self._terminate_task = asyncio.create_task(self._terminate())
        task = self._terminate_task
        try:
            # A caller cancelling its wait must not cancel the shared cleanup.
            await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise RuntimeError(
                self.terminate_error or "cleanup incomplete") from exc
        if self.lifecycle != "CLOSED":
            raise RuntimeError(self.terminate_error or "cleanup incomplete")

    async def inject_proactive(self, event, request, request_id, trigger_kind='scheduled'):
        if not self.proactive_enabled or not isinstance(request_id,str) or not request_id.strip():
            return {'status':'DISABLED'}
        if trigger_kind not in ('scheduled','manual') or not self._sender_allowed(event):
            return {'status':'REJECTED'}
        if not await self._reconcile_ownership():
            return {'status':'BLOCKED'}
        cid, persona = await self._conversation_identity(event)
        persona = self._persona_for(persona)
        if not cid or not persona:
            return {'status':'REJECTED'}
        origin = build_origin(event, persona=persona, conversation=cid)
        query = getattr(request,'prompt',None)
        if origin is None or not isinstance(query,str) or not query.strip():
            return {'status':'REJECTED'}
        provider = await self._active_provider(event)
        def work():
            if not self._commit_allowed() or not self._ensure():
                return {'status':'BLOCKED'}
            prior=self.store.db.execute('SELECT query FROM proactive_receipts WHERE scope=? AND request_id=?',
                                        (origin.scope_id,request_id)).fetchone()
            if prior:
                return {'status':'DUPLICATE' if prior[0]==query else 'CONFLICT'}
            result=self._output_work(request,origin.scope_id,query,provider,origin,request_id)
            if result and result.get('output_status') in ('INJECTED','EMPTY','UNCHANGED'):
                with self.store.unit_of_work():
                    self.store.db.execute('INSERT INTO proactive_receipts VALUES(?,?,?)',
                                          (origin.scope_id,request_id,query))
            return result or {'status':'BLOCKED'}
        return await asyncio.get_running_loop().run_in_executor(self.pool,work)

    async def _terminate(self):
        self.terminate_error = None
        self._cleanup_errors = []
        try:
            await self.application.wait_idle()
            if self._ownership_task is not None:
                self._ownership_task.cancel()
                try:
                    await self._ownership_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
                self._ownership_task = None
            if not self._pump_stopped:
                if self.pump is not None:
                    await self.pump.stop()
                self.pump = None
                self._pump_stopped = True
            loop = self.loop
            if loop is None or loop.is_closed():
                loop = asyncio.get_running_loop()
            # A judge thread may be waiting on run_coroutine_threadsafe() into this
            # loop, so shutdown must not run synchronously on the loop. Off-load the
            # wait, then close the store only after no judge can commit.
            if not self._judge_shutdown:
                await asyncio.to_thread(self.judge_pool.shutdown, True)
                self._judge_shutdown = True
            if not self._store_closed:
                await loop.run_in_executor(self.pool, self._close)
                self._store_closed = True
            if not self._console_closed:
                if self.console_api is not None:
                    self.console_api.close()
                    self.console_api = None
                self._console_closed = True
            if not self._pool_shutdown:
                # Only close the executor that owns the store after the store is
                # actually closed; otherwise a retry has no thread to run on.
                await asyncio.to_thread(self.pool.shutdown, True)
                self._pool_shutdown = True
            self.lifecycle = "CLOSED"
            if self._ownership is not None:
                self._ownership.release()
                self._ownership = None
                self.ownership_status = "released"
        except asyncio.CancelledError:
            self.terminate_error = "cleanup cancelled"
            self._cleanup_errors.append(self.terminate_error)
            raise
        except Exception as exc:
            self.terminate_error = "%s: %s" % (type(exc).__name__, exc)
            self._cleanup_errors.append(self.terminate_error)
            raise
