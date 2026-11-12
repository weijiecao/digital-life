# 记忆基座匹配输出模块 v0.1

本模块把数字生命记忆基座的检索证据转成标准上下文输出，再适配到 AstrBot 的本轮请求。输入模块负责接入原生历史；输出模块负责将记忆基座已经组织好的内容交还宿主。事件、证据与真实性判断仍由引擎统一管理。

## 分层与交付

| 部分 | 路径 | 职责 |
|---|---|---|
| 原生输入 | astrbot_memory_input | 原文、身份、重复导入与可靠投递 |
| 记忆基座 | v1_5_consolidator / memory_engine | 事件组织、来源归属、检索证据准入 |
| 输出服务 | memory_engine/output.py | 读取准入结果、复验当前证据、组装输出、最终注入检查 |
| 独立输出包 | memory_output | 不可变协议、预算排版、JSON 导出、AstrBot 临时内容适配 |

`digital-life-memory-output` 可独立安装，运行依赖仅 Python 标准库。不导入 AstrBot、不打开数据库、不调用模型。默认 AstrBot 适配器只在实际注入时延迟导入 TextPart。配套输出服务依赖当前记忆引擎；使用现有 TokenCounter 时还需要 tiktoken。

## 标准输出协议

- OutputRequest：request_id、scope_id、query、budget_tokens。宿主应自行提供可信范围和每次请求的唯一 ID。
- ContextOutput：协议版本 memory-output-v1、请求绑定、scope_epoch、items、text、used_tokens、budget_tokens、tokenizer、reasons；persist 恒为 false。
- MemoryItem：event_id、revision、snapshot_hash、provenance、证据片段及准入证书。
- SourceSpan：source_seq、source_hash、start、end、text、recorded_at；可追踪回记忆基座原始证据。
- `output.as_dict()` / `output.to_json()`：脱离内部对象的结构化导出，供其他宿主读取。JSON 导出本身不是可跨进程执行的授权凭证；当前 AstrBot 交付要求与引擎同进程复验。

输出正文保留事件、来源编号、叙述主体、现实/虚构属性及证据。源哈希、版本与偏移留在结构化协议中，避免重复占用模型正文。未知属性继续显示 unknown；不生成情绪、时间或人物关系的补全结论，也不重新扩写已有摘要。

证据中的 source_recorded_at 保留来源记录时间，缺失时为 null；它不是事件实际发生时间，不会直接将“明天”等相对表达换算为日期。

## 接入示例

从源码 dev 目录运行，或把 dev 加入 PYTHONPATH：

```python
from uuid import uuid4
from memory_engine.output import MemoryOutputService
from memory_engine.token_budget import TokenCounter
from memory_output import OutputRequest

# engine 是既有 MemoryEngine，req 是当前 AstrBot ProviderRequest。
service = MemoryOutputService(engine, TokenCounter('cl100k_base'))
request = OutputRequest(
    request_id=uuid4().hex,
    scope_id=origin.scope_id,  # 与原生输入时的可信 Origin 完全一致
    query=req.prompt,
    budget_tokens=1024,
)
output = service.prepare(request)
receipt = service.inject(req, output, request)
```

这段调用可放入已有插件的 on_llm_request 处理器。必须在最终请求文本确定后调用；prepare 和 inject 之间不应发生异步模型等待。v0.1 要求 req.prompt 与 query 完全相同，文本改变则拒绝旧输出。纯图片请求、查询改写、主动聊天自行构造查询等路径需要明确的宿主接线，不会在这里猜测。

输入模块与输出模块必须共享同一记忆存储及 scope 映射。不同用户、机器人和人格的范围应来自宿主身份信息，不能取自模型生成文本。当前输出包不负责自动初始化引擎或注册插件生命周期。

独立安装包（`dev/releases/digital_life_memory_output-0.1.0-py3-none-any.whl`）：

```sh
python -m pip install --no-index --no-deps digital_life_memory_output-0.1.0-py3-none-any.whl
```

## 在 host_shadow 中接线

`dev/host_shadow/main.py` 已提供可选的 AstrBot 注入路径，默认关闭：

```powershell
$env:SHADOW_OUTPUT_MODE = 'inject'
$env:SHADOW_MEMORY_BUDGET = '512'
```

- 只有显式 `inject` 才把记忆块附加到 `ProviderRequest.extra_user_content_parts`；
- 输出块使用 `TextPart.mark_as_temp()`，不写入持久化会话历史；
- `prompt`、`system_prompt`、`contexts` 与回复均不被修改；
- 注入失败、证据过期或预算缩水时撤掉本插件上一次写入的块；
- `shadow_events.jsonl` 会记录 `output_status` / `output_reason` / `budget_tokens`，控制台“观察日志”可直接查看。

输出模块缺失或 `tiktoken` 不可用时，注入状态为 `UNAVAILABLE` 或 `EMPTY`，不会静默伪造记忆。

## 预算与降级

最终正文（包括说明、JSON、来源标记和外围标签）统一计数。按检索顺序选择完整事件；某项过大时跳过，继续尝试后续项，不截断事实半句。零预算、无有效证据、无计数器或计数器报错时输出空串，不输出空壳提示。

可将现有 HostBudget 传给 prepare 和 inject，模块预算取两者较小值。system_tokens、history_tokens 等调用方计数须覆盖系统提示、当前输入、历史、工具定义及其他插件内容；替换本模块旧块时应先从宿主占用计算中扣除它。输出预留由 output_tokens 提供。注入时预算缩水则拒绝旧输出。

“精确计数”指相对于调用方提供的 tokenizer；cl100k_base 不保证与所有供应商实际计费一致。本模块严格控制分配给记忆块的文本预算，未自动测量多模态负载或模型聊天封装开销，不能据此宣称整次请求一定不超供应商上下文窗口。

## 可靠性与隔离

- 仅使用通过现有查询证据准入的片段，不回退到未经验证的标题、摘要或原始相似搜索结果。
- 准备输出及注入前检查 scope、query、request_id、事件版本、证据哈希和来源片段；删除、撤回、修订、范围变化或数据版本变化均使旧输出失效。
- 注入复验与本地请求更新在一个短数据库事务内完成，没有网络等待；事务结束后到宿主实际发送之间的修改不在本模块控制范围内，因此应靠近发送时调用。
- AstrBot 使用 extra_user_content_parts + TextPart.mark_as_temp()。不修改 prompt、system_prompt 或 contexts。重复注入替换本模块自己的对象，保留其他插件内容；空输出或失败会移除本模块上一次留下的旧块。
- 输出层不写事件、证据或聊天历史，不把模型回答当作新记忆来源。真实宿主必须继续采用受验证的临时内容保存路径。
- 记忆正文按资料编码为 JSON，尖括号等转义，并附加资料用途说明。该措施避免结构标签逃逸，不等于证明模型完全免疫提示注入；不得把历史资料升格为系统指令。

DeliveryReceipt.status 为 INJECTED / UNCHANGED / EMPTY / REJECTED，reason 用于内部诊断。无需将这些状态变为聊天用户的人工确认步骤。

## 验证与限制

随附 demo 使用固定输入判别器与模拟请求块，演示“原生输入→基座事件→检索→预算输出→临时注入→删除后失效”。固定判别器只验证线路，不用于实际语义判断。运行方式：从 dev 目录执行 `python -m examples.memory_output.demo`。

`tests/test_memory_output.py` 验证协议、预算、隔离、失效和重复注入；全量测试命令为 `python -m unittest discover -q -s tests -t .`。详细数字见验证报告。

另提供 `scripts/verify_native_output_contract.py`：指定 AstrBot v4.29.3 的 message.py 和 entities.py，原样加载消息模型，并从 AST 抽取 ProviderRequest 类验证纯文本装配及历史保存路径。此项不启动完整 AstrBot，不连接 QQ、模型服务或真实聊天。

已知引擎限制仍存在：例如示例“故事青岛海边散步”无候选，而更接近原文的“故事里的小鱼去青岛海边散步”有候选。输出层不会通过绕开准入来掩盖召回缺失。本次未提升原有语义质量发布状态；真实判别器、宿主生命周期、主动聊天路径与长期运行仍待联调。

参考协议：
- https://docs.astrbot.app/dev/star/guides/listen-message-event.html
- https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/agent/message.py
- https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/provider/entities.py
