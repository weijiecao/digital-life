# AstrBot 原生记忆接入模块 v0.2

本模块给数字生命记忆引擎提供一个独立的原生历史入口。输入兼容 AstrBot 对话 history 的 role/content 消息列表；进入引擎后，事件、证据、归属和检索仍由数字生命记忆基座组织。

v0.2 在 v0.1 的输入契约不变的前提下，补上了两处此前缺失的接线：**AstrBot 运行时自动读取会话历史**（`memory_engine/native_host.py`、`host_shadow/`）与**真实判别器**（`memory_engine/native_judge.py` 接引擎既有 `RelationJudge` 与宿主 provider）。包内的去重、前缀校验、冲突拒绝语义未放宽。

## 模块边界

| 层 | 位置 | 责任 |
|---|---|---|
| 原生输入 | astrbot_memory_input | 格式校验、原文保留、来源标识、重复检测、持久化投递 |
| 引擎桥接 | memory_engine/native_input.py | 接收回执、隔离角色、构建候选、调用判别器、事务提交 |
| 宿主接入 | memory_engine/native_host.py | 来源映射、会话历史读取、串行服务、周期泵、宿主 LLM 端口 |
| 判别器 | memory_engine/native_judge.py | 把引擎既有关系判别接成 judge 协议，预算与失败冷却 |
| 记忆基座 | v1_5_consolidator + memory_engine | 事件与证据、归属约束、故事来源检查、检索及上下文输出 |

输入包只使用 Python 标准库，不导入 AstrBot、记忆引擎或模型 SDK，可以单独安装。引擎桥接与宿主接入依赖输入包及当前数字生命引擎。此版本仍未把整个记忆引擎的存储层重新拆包。


## 安装和导入

Python 声明支持 >=3.10；本次实测为 Windows 11 + Python 3.12.10，v0.1 另在 Linux Python 3.12 验证。

安装交付包内的 wheel：

```sh
python -m pip install --no-index --no-deps digital_life_astrbot_input-0.2.0-py3-none-any.whl
python -m astrbot_memory_input --input history.json --journal native-input.db --platform qq --bot demo-bot --user demo-user --persona demo-persona --conversation demo-conversation
```

CLI 只完成持久化入队，输出新增数和重复数。它不连接正在运行的 AstrBot，也不调用模型。可接受的文件内容：

```json
[
  {"role": "user", "content": "周六去青岛海边散步。"},
  {"role": "assistant", "content": "记得看看天气。"}
]
```

也接受 JSON 编码的上述列表，或 `{"history": [...]}` / `{"history": "[...]"}`。包装对象中若包含 cid/persona_id，必须与调用者提供的来源一致；包装对象的其他顶层字段不归档，消息对象的全部 JSON 字段会保留。

来源必须由宿主提供 platform、bot、user、persona、conversation。缺失的时间、主体和现实/虚构属性不会由适配器补猜。当前隔离键由前四项组成，会话标识用于区分输入流；不同会话可进入同一个人的记忆范围。群聊需由宿主提供可靠的成员归属，不能把整群历史冒充某一个用户。

## 引擎侧连接

在完整源码的 dev 目录中运行，或将 dev 加入 PYTHONPATH：

```python
from astrbot_memory_input import ImportJournal
from memory_engine.native_input import NativeInputInbox, NativeInputWorker
from v1_5_consolidator.store import V15Store

store = V15Store('memory.db')
journal = ImportJournal('native-input.db')
inbox = NativeInputInbox(store)
try:
    print(journal.deliver(inbox))
    # 应用提供 relation_judge；此处不内置任何模型或服务账号。
    worker = NativeInputWorker(inbox, relation_judge, judge_revision='your-policy-v1')
    print(worker.run_once())
finally:
    journal.close()
    store.close()
```

`relation_judge(source, candidates, record, context)` 应返回已有协议 `JudgmentResult`：

- source 为 SourceEnvelope；candidates 为同 scope 的事件候选，最多 20 个，带标题、原文证据与 provenance。
- record 是含原始消息及来源的 Record。context 最多 12 条同输入流的先前记录，保留角色；它不是事实列表，也不是系统指令。
- 必须覆盖返回的所有候选，声明 NEW_EVENT / ATTACH_EXISTING / DEFER，并返回来源判断。ATTACH_EXISTING 的 target_event_id 必须匹配唯一 SAME_EVENT。
- narrator 必须是 user；subject 和 reality_status 由判别器提供，允许 unknown。unknown 不会被自动当作 real。
- 新事件用记忆基座的建档协议创建；已有事件归属通过 ConstrainedResolver 和 ProvenanceGuard；不同事件关系落入 cannot-link。空候选只有在判别器明确 NEW_EVENT 时才建档。
- 判别器应将原始内容当作待分析资料，使用现有模型网关和预算策略。

暂缓记录在 scope 发生变化或 judge_revision 更新后可自动复判；调用方需周期调用 run_once。retry_deferred=True 可由内部任务进行定时重试，无需把人工确认暴露给聊天用户。模型异常保持记录可重试，同轮继续处理后续条目。

## 真实判别器接线（v0.2）

`memory_engine/native_judge.py` 把引擎已有的 `RelationJudge`（`ContextGateway` 提示词 + 严格 JSON 解析 + 候选覆盖校验）接成上面的 judge 协议，不复制一套新的判定逻辑：

```python
from memory_engine.native_judge import NativeRelationJudge, build_provider

provider = build_provider('openai', model='...', api_key='...', base_url='...')
judge = NativeRelationJudge(store, provider, policy_version='native-input-0.2')
worker = NativeInputWorker(inbox, judge, judge_revision=judge.revision)
```

- `build_provider` 支持 `none` / `mock` / `openai`；显式配置才产生 provider，缺参数直接报错而不是静默降级。
- 关系判别提示词超字符预算时返回带 `prompt:` issue 的结果，由工作者暂缓；不截断原话或候选。
- 连续失败达到阈值后进入冷却，冷却期不再外发请求；冷却结束仍可重试。
- 外部 provider（原话会离开进程）必须由调用方显式选择；`ContextGateway` 的禁止外发开关按 provider 的 `external` 属性设置。
- 判别器返回的候选覆盖、标签、narrator/subject/reality 仍由 `NativeInputWorker` 复核；复核失败即暂缓，不建档。

## 宿主接线与生命周期（v0.2）

`memory_engine/native_host.py` 是 AstrBot 无关的接入层，接收宿主的鸭子类型对象：

- `build_origin(event, persona=..., conversation=...)`：从 `unified_msg_origin`、消息发送者/机器人 ID 和调用方显式给出的 persona、会话 ID 构造 `Origin`。群聊、缺发送者、缺机器人与缺会话一律返回 `None`，不猜测。
- `read_conversation(conversation_manager, umo)`：调用 AstrBot 4.29.3 的 `get_curr_conversation_id` / `get_conversation`，取回 `(会话 ID, 消息列表)`。历史上没有稳定消息 ID，因此按「完整、只追加快照」模式导入。
- `NativeInputService`：串行完成 stage → deliver → run_once，记录冲突、无效输入与判别统计。没有判别器时 `run_once` 直接跳过，**不建任何事件**。
- `NativeInputPump`：周期性在宿主线程池里跑一轮判别，定期整批重试暂缓项；单轮异常不终止循环。
- `AstrBotProvider`：把宿主 `context.llm_generate` 接成 provider 端口（`run_coroutine_threadsafe` 回到宿主事件循环，不阻塞消息处理）。

`host_shadow/main.py` 是这一层的 AstrBot 装配：私聊消息到达时读取当前对话历史并提交，`initialize` 启动周期泵，`terminate` 停止泵并关闭输入与引擎。没有 provider 配置时插件只入队、不建档。

AstrBot 的会话历史是窗口（可能被会话切换、人工删除改写）。当前策略是：非前缀快照整批拒绝并记录冲突，不部分写入；改写后的历史需要新会话或后续版本的修订协议。


## 身份、重复与恢复

| 输入条件 | v0.2 行为（与 v0.1 相同） |
|---|---|
| 全部消息具有稳定 id/message_id | 可输入增量；按输入流+消息 ID 去重；上下文按首次接收顺序排列 |
| 全部消息无稳定 ID | 每次必须输入完整、只追加的历史快照；以位置+原文哈希验证已接收前缀 |
| 同批混合有 ID / 无 ID | 拒绝，避免混用身份规则 |
| 同 ID 内容改变 / 无 ID 历史裁剪、改写、重排 | 整批冲突并保留冲突记录，不部分写入 |
| 接收端成功、投递端确认前崩溃 | 下次重复投递；接收端通过身份+哈希幂等确认 |
| 提交事件时出现错误 | 事件、证据、回执、处理状态同事务回滚 |
| 判断期间另一工作者已提交或 scope 已改变 | 丢弃过期判断，后续重新处理 |

幂等性依赖保留 journal 和引擎 inbox。不要丢弃 journal 后把同一无 ID 会话的截断窗口当作完整历史输入。现有同 ID 修改需要后续版本的修订协议；此版本只检测，不自动覆盖。迟到的稳定 ID 消息按首次接收顺序处理，不宣称还原真实时间顺序。

## 角色与多模态

所有受支持角色的消息原文都归档。只有非空 user 文本进入事件处理队列。assistant、system、developer、tool、function 及空文本归档后仅可作为带角色上下文使用。明确提供 `source_kind != "message"` 或 `is_summary: true` 的消息只归档。这两个标记是本模块的保守扩展，不声称是 AstrBot 原生字段；未标记摘要不能可靠自动识别。

多模态 content 中的 text 段参与处理，图片/音频等描述保留在原始 JSON 中；不下载附件，不做视觉或音频理解。未知消息字段保留。原文保留可能包含敏感内容，应沿用应用原有的访问控制。

## 验证与范围

v0.2 的验证结果见 `dev/reports/NATIVE_INPUT_0_2_2026-11-12.md`：输入与桥接 28 项、宿主接入 20 项、shadow 装配 4 项，全量回归 642 项。线路上用替身会话管理器与脚本化 provider 验证，**不**等同真实模型的归并准确率，也未在真实 AstrBot/NapCat 上长时间运行。

尚未完成：返回请求的临时记忆注入（本模块只负责输入，插件注入仍是既有链路）、源消息编辑/删除同步、真实模型判别质量评估、真实聊天与长期稳定性验证。独立入口不会改变现有引擎的质量门槛与发布状态。

适配参考锁定 AstrBot v4.29.3 的 conversation history 与 ProviderRequest.contexts 形式：
- https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/conversation_mgr.py
- https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/provider/entities.py
- https://docs.astrbot.app/dev/star/guides/ai.html
