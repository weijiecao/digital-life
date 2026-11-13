"""AstrBot 运行时接入层：把宿主会话历史送进原生输入，再交给引擎基座。

本模块不导入 AstrBot、不读配置、不抓事件；它只接收调用方给的**宿主对象**
（事件、会话管理器、provider 上下文），因此可以在没有安装 AstrBot 的环境里
用替身验证契约。AstrBot 专用的注册与生命周期在 ``host_shadow/main.py``。

边界：
- 只读取宿主已经存在、且能明确归属到单个用户的历史；群聊、缺少发送者或
  缺少机器人标识时返回 ``None``，不猜测。
- 历史按宿主给出的顺序原样提交；去重、前缀校验、冲突拒绝由输入包负责。
  AstrBot 持久化历史里的 ``_checkpoint`` 内部段由输入包识别并跳过，只保留
  游标元数据，既不当作用户事实，也不会让整份快照失效。
- 人格身份以宿主会话的 ``persona_id`` 为准；显式配置只作为兜底，不能把
  同实例的不同人格混进同一个作用域。
- 没有判别器时只入队、不建档；不能让「没有模型」变成「模型说这是新事件」。
- 判别（模型等待）在独立线程里执行，聊天路径的数据库线程只做短事务准备与提交。
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

from astrbot_memory_input import ImportConflict, ImportJournal, Origin
from astrbot_memory_input.native import InputError
from memory_engine.native_input import (NativeInputInbox, NativeInputWorker,
                                        empty_stats)
from v1_5_consolidator.gateway import ModelResult

_MAX_FIELD = 512


class HostInputError(ValueError):
    """宿主给的历史不能按原生输入契约使用。"""


def _field(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text or len(text) > _MAX_FIELD or "\x00" in text:
        return None
    return text


def build_origin(event, *, persona: str, bot: str | None = None,
                 conversation: str | None = None) -> Origin | None:
    """从宿主事件推导来源隔离键；无法无歧义归属时返回 ``None``。

    只使用宿主已提供且稳定的字段：``unified_msg_origin``（平台与会话）、
    消息对象的发送者与机器人 ID、调用方显式给出的 persona 和会话 ID。
    群聊消息不会被当作某一个用户的历史。

    Args:
        event: 宿主事件对象（鸭子类型）：``unified_msg_origin``、``message_obj``
            （``sender.user_id`` / ``group_id`` / ``self_id``）或 ``get_sender_id()``。
        persona: 当前人格标识，由宿主会话确认（配置值只作兜底）。
        bot: 机器人标识的兜底值；事件里已有 ``self_id`` 时以事件为准。
        conversation: 会话/对话标识，用于区分输入流。

    Returns:
        可用的 ``Origin``，或 ``None``（群聊、缺字段、字段非法）。
    """
    origin = _field(getattr(event, "unified_msg_origin", None))
    if origin is None or ":" not in origin:
        return None
    platform = _field(origin.split(":", 1)[0])
    if platform is None:
        return None
    obj = getattr(event, "message_obj", None)
    user = getattr(getattr(obj, "sender", None), "user_id", None)
    if user is None:
        getter = getattr(event, "get_sender_id", None)
        user = getter() if callable(getter) else None
    user = _field(user)
    if user is None:
        return None
    group = getattr(obj, "group_id", None)
    if group not in (None, "", 0, "0"):
        return None
    self_id = _field(getattr(obj, "self_id", None)) or _field(bot)
    persona_id = _field(persona)
    stream = _field(conversation)
    if self_id is None or persona_id is None or stream is None:
        return None
    if self_id == user:
        return None
    try:
        return Origin(platform, self_id, user, persona_id, stream)
    except InputError:
        return None


def history_from_conversation(conversation) -> list:
    """取出宿主会话里的消息列表。

    AstrBot 4.29.3 的 ``Conversation.history`` 是 JSON 字符串，内容为
    ``{"role": ..., "content": ...}`` 列表；这里也接受已经解析好的列表或
    直接传入列表。内容本身的角色、内容类型和多模态校验交给输入包。

    内部段（``role == "_checkpoint"``）保持原样交给输入包规范化：它被跳过并
    只保留游标元数据，不参与原话编号，因此插入 checkpoint 不会改变已有原话
    的身份，也不会让整份历史被拒收。

    Args:
        conversation: 带 ``history`` 的会话对象，或消息列表本身。

    Returns:
        消息列表（原样，不裁剪、不改写）。

    Raises:
        HostInputError: ``history`` 不是列表或不是合法 JSON。
    """
    raw = getattr(conversation, "history", conversation)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise HostInputError("conversation history is not JSON") from exc
    if not isinstance(raw, list):
        raise HostInputError("conversation history must be a list")
    return raw


def conversation_persona(conversation) -> str | None:
    """宿主会话当前人格；没有可确认的人格时返回 ``None``。"""
    return _field(getattr(conversation, "persona_id", None))


async def read_conversation_identity(conversation_manager, unified_msg_origin: str):
    """读取宿主当前对话的（会话 ID，消息列表，人格 ID）。

    不可用时返回 ``(None, None, None)``。调用方据此决定是否回退到单条消息。
    人格取自会话对象本身，采集与查询因此使用同一个宿主确认过的人格。
    """
    get_id = getattr(conversation_manager, "get_curr_conversation_id", None)
    get_conv = getattr(conversation_manager, "get_conversation", None)
    if not callable(get_id) or not callable(get_conv):
        return None, None, None
    conversation_id = _field(await get_id(unified_msg_origin))
    if conversation_id is None:
        return None, None, None
    conversation = await get_conv(unified_msg_origin, conversation_id)
    if conversation is None:
        return None, None, None
    return (conversation_id, history_from_conversation(conversation),
            conversation_persona(conversation))


async def read_conversation(conversation_manager, unified_msg_origin: str):
    """读取宿主当前对话的（会话 ID，消息列表）；不可用时返回 ``(None, None)``。

    调用方据此决定是否回退到单条消息。宿主没有 ``conversation_manager``
    或还没有建立对话（首轮请求之前）时都返回空，不补造历史。

    Args:
        conversation_manager: 宿主会话管理器（鸭子类型）。
        unified_msg_origin: 宿主会话标识。

    Returns:
        ``(conversation_id, messages)`` 或 ``(None, None)``。
    """
    conversation_id, messages, _ = await read_conversation_identity(
        conversation_manager, unified_msg_origin)
    return conversation_id, messages


def event_message(event) -> list | None:
    """单条消息的保守回退；没有稳定消息 ID 或纯文本时返回 ``None``。"""
    obj = getattr(event, "message_obj", None)
    message_id = _field(getattr(obj, "message_id", None))
    text = getattr(event, "message_str", None)
    if message_id is None or not isinstance(text, str) or not text.strip():
        return None
    return [{"role": "user", "content": text, "message_id": message_id}]


class AstrBotProvider:
    """把宿主的 ``context.llm_generate`` 接成关系判别 provider 端口。

    判别在独立线程里执行，本端口用 ``run_coroutine_threadsafe`` 回到宿主事件循环，
    因此不会在宿主循环里做网络等待，也不会阻塞消息处理。

    ``external = True``：原话会离开本进程进入模型服务，调用方必须显式启用。
    """

    external = True

    def __init__(self, context, provider_id: str, loop, *, timeout: float = 20.0,
                 model: str = ""):
        if not callable(getattr(context, "llm_generate", None)):
            raise ValueError("host context has no llm_generate")
        self.context = context
        self.provider_id = str(provider_id)
        self.loop = loop
        self.timeout = max(1.0, float(timeout))
        self.model = str(model or provider_id)

    def generate(self, prepared) -> ModelResult:
        future = asyncio.run_coroutine_threadsafe(
            self.context.llm_generate(chat_provider_id=self.provider_id,
                                      prompt=prepared.prompt),
            self.loop)
        try:
            response = future.result(timeout=self.timeout)
        except Exception:
            future.cancel()
            raise
        text = getattr(response, "completion_text", None)
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("host provider returned no text")
        return ModelResult(text=text, model=self.model)


class NativeInputService:
    """串行化的原生输入服务：入队、投递、判别。

    所有方法都不切换线程；宿主必须保证 ``store`` 与 ``journal`` 在**同一个**
    工作线程里创建和使用（sqlite 连接默认不跨线程）。数据库相关方法用一把锁
    串行；模型等待由 :meth:`execute` 在**另一个**线程里完成，因此聊天路径
    （``observe``/``capture``）不必等待一轮判别结束。

    一轮判别的调用顺序是 ``start_tick`` → ``prepare_one`` → ``execute`` →
    ``commit_one``，每一步之间都可以插入其他数据库任务。
    """

    def __init__(self, store, journal_path, *, judge=None, encoder=None,
                 judge_revision: str = "v1", deliver_limit: int = 200):
        self.store = store
        self.journal = ImportJournal(journal_path)
        self.inbox = NativeInputInbox(store)
        self.worker = (NativeInputWorker(self.inbox, judge, encoder=encoder,
                                         judge_revision=judge_revision)
                       if judge is not None else None)
        self.deliver_limit = max(1, int(deliver_limit))
        self.lock = threading.Lock()
        self.closed = False
        self.last_delivery = {"acknowledged": 0, "remaining": 0}
        self.last_delivery_error = None
        self.stats = {"submitted": 0, "new": 0, "duplicates": 0, "conflicts": 0,
                      "invalid": 0, "created": 0, "attached": 0, "deferred": 0,
                      "stale": 0, "judge_errors": 0, "delivery_errors": 0}
        self.last_conflict: dict | None = None

    @property
    def has_judge(self) -> bool:
        return self.worker is not None

    def _deliver_locked(self) -> dict:
        """把 journal 中尚未投递的记录继续送进 inbox；失败不终止泵。"""
        try:
            self.last_delivery = self.journal.deliver(self.inbox, limit=self.deliver_limit)
            self.last_delivery_error = None
        except Exception as exc:
            self.stats["delivery_errors"] += 1
            self.last_delivery_error = type(exc).__name__ + ": " + str(exc)
        return dict(self.last_delivery)

    def submit(self, history, origin: Origin) -> dict:
        """校验并入队一份历史快照，然后投递给引擎收件箱。

        Returns:
            ``{"status": "staged", "new": n, "duplicates": m, "delivered": {...}}``
            或 ``{"status": "conflict", "reason": ...}`` /
            ``{"status": "invalid", "reason": ...}``。
        """
        if self.closed:
            raise RuntimeError("native input service is closed")
        with self.lock:
            try:
                staged = self.journal.stage(history, origin)
            except ImportConflict as exc:
                self.stats["conflicts"] += 1
                self.last_conflict = {"stream_id": origin.stream_id, "reason": str(exc)}
                return {"status": "conflict", "reason": str(exc)}
            except InputError as exc:
                self.stats["invalid"] += 1
                return {"status": "invalid", "reason": str(exc)}
            delivered = self._deliver_locked()
            self.stats["submitted"] += 1
            self.stats["new"] += staged["new"]
            self.stats["duplicates"] += staged["duplicates"]
            return {"status": "staged", "new": staged["new"],
                    "duplicates": staged["duplicates"], "delivered": delivered}

    def deliver_pending(self, limit: int | None = None) -> dict:
        """继续投递 journal 积压；返回本次确认数与剩余未投递数。"""
        if self.closed:
            raise RuntimeError("native input service is closed")
        with self.lock:
            if limit is None:
                return self._deliver_locked()
            try:
                self.last_delivery = self.journal.deliver(
                    self.inbox, limit=max(0, int(limit)))
                self.last_delivery_error = None
            except Exception as exc:
                self.stats["delivery_errors"] += 1
                self.last_delivery_error = type(exc).__name__ + ": " + str(exc)
            return dict(self.last_delivery)

    def start_tick(self, limit: int = 100, retry_deferred: bool = False):
        """数据库线程：先补投 journal，再开始一轮判别；没有判别器时返回 ``None``。"""
        if self.closed:
            raise RuntimeError("native input service is closed")
        with self.lock:
            self._deliver_locked()
            if self.worker is None:
                return None
            return self.worker.start_tick(limit=limit, retry_deferred=retry_deferred)

    def prepare_one(self, tick):
        """数据库线程：取出下一条可处理记录并准备判别输入；不等待模型。"""
        with self.lock:
            if self.worker is None or tick is None:
                return None
            return self.worker.prepare_one(tick)

    def execute(self, task):
        """模型线程：执行判别调用；不接触 sqlite，也不持有数据库锁。"""
        if self.worker is not None and task is not None:
            self.worker.execute(task)
        return task

    def commit_one(self, task) -> dict:
        """数据库线程：短事务复验并写入结果。"""
        with self.lock:
            result = self.worker.commit_one(task) if self.worker is not None else empty_stats()
        self._merge_stats(result)
        return result

    def _merge_stats(self, result: dict) -> None:
        for key, target in (("created", "created"), ("attached", "attached"),
                            ("deferred", "deferred"), ("stale", "stale"),
                            ("errors", "judge_errors")):
            self.stats[target] += result.get(key, 0)

    def run_once(self, limit: int = 100, retry_deferred: bool = False) -> dict:
        """同步跑一轮判别；没有判别器时也会补投 journal，然后什么都不做。"""
        if self.closed:
            raise RuntimeError("native input service is closed")
        with self.lock:
            self._deliver_locked()
            if self.worker is None:
                return {"skipped": "no_judge"}
            result = self.worker.run_once(limit=limit, retry_deferred=retry_deferred)
        self._merge_stats(result)
        return result

    def counts(self) -> dict:
        """各状态条数（含已归档与暂缓），供宿主观察积压。"""
        with self.lock:
            return self.inbox.counts()

    def health(self) -> dict:
        """宿主健康状态：journal 积压、inbox 队列与重试明细。"""
        with self.lock:
            return {"judge": self.worker is not None,
                    "delivery": dict(self.last_delivery),
                    "delivery_error": self.last_delivery_error,
                    "inbox": self.inbox.counts(),
                    "backlog": self.inbox.backlog(),
                    "stats": dict(self.stats)}

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            # Mark closed only after the journal closes so a failed attempt can
            # be retried by the host lifecycle.
            self.journal.close()
            self.closed = True


class NativeInputPump:
    """周期性驱动 ``run_once``；定期整批重试暂缓项。

    判别调用可能很慢（模型网络），所以放到 ``judge_executor`` 里执行，与聊天
    路径共用的数据库线程（``executor``）分开；单轮异常不会终止循环。
    """

    def __init__(self, service: NativeInputService, *, executor=None,
                 judge_executor=None, interval: float = 5.0, retry_every: int = 12,
                 limit: int = 10):
        self.service = service
        self.executor = executor
        self.judge_executor = judge_executor
        self.interval = max(0.1, float(interval))
        self.retry_every = max(0, int(retry_every))
        self.limit = max(1, int(limit))
        self.task = None
        self.stopping = False

    def start(self) -> None:
        if self.task is not None and not self.task.done():
            return
        self.stopping = False
        self.task = asyncio.get_running_loop().create_task(self._loop())

    async def _loop(self) -> None:
        tick = 0
        while not self.stopping:
            await asyncio.sleep(self.interval)
            if self.stopping:
                return
            retry = bool(self.retry_every and tick and tick % self.retry_every == 0)
            try:
                await self.tick(retry)
            except asyncio.CancelledError:
                return
            except Exception:
                # 数据库临时不可用或判别异常都不终止泵；下一轮继续。
                pass
            tick += 1

    async def _db(self, func, *args):
        loop = asyncio.get_running_loop()
        if self.executor is not None:
            return await loop.run_in_executor(self.executor, func, *args)
        return await asyncio.to_thread(func, *args)

    async def _model(self, func, *args):
        loop = asyncio.get_running_loop()
        if self.judge_executor is not None:
            return await loop.run_in_executor(self.judge_executor, func, *args)
        return await asyncio.to_thread(func, *args)

    async def tick(self, retry_deferred: bool = False) -> dict:
        """准备（数据库线程）→ 判别（模型线程）→ 提交（数据库线程）。"""
        scope = await self._db(self.service.start_tick, self.limit, retry_deferred)
        if scope is None:
            return {"skipped": "no_judge"}
        stats = empty_stats()
        for _ in range(self.limit):
            task = await self._db(self.service.prepare_one, scope)
            if task is None:
                break
            await self._model(self.service.execute, task)
            result = await self._db(self.service.commit_one, task)
            for name, value in result.items():
                stats[name] += value
        return stats

    async def stop(self) -> None:
        self.stopping = True
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def journal_path_for(base: Path | str) -> Path:
    """原生输入日志与引擎库放在同一目录，便于一起备份与迁移。"""
    root = Path(base)
    root.mkdir(parents=True, exist_ok=True)
    return root / "native_input_journal.sqlite3"


def revision_for(judge, default: str = "v1") -> str:
    """判别策略版本；用于让暂缓项在策略变化后复判。"""
    revision = getattr(judge, "revision", None)
    return revision if isinstance(revision, str) and revision else default
