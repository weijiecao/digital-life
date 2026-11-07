# V1.5 定时事件候选归纳服务（Alpha）

> 状态：**实现中/离线 Alpha**，独立于 V1 插件；只读 V1 原话，只写独立 SQLite。
> 不创建 V1 正式事件，不向聊天用户暴露。

## 1. 定位

V1.0.19 只有“原话存档 + 混合召回”，没有事件归纳。V1.5 新增：

```text
V1 SQLite（只读）
  → 按 scope 读游标后的原话
  → ContextGateway 预算/审计
  → LLM 输出事件划分候选
  → 确定性 Validator
  → V1.5 SQLite 候选事件/修订/证据
  → 影子查询
```

V1.5 **不是** V2 的 claim/双时态实现；它只做事件候选、事件修订和来源证据。

## 2. 目录

```text
dev/v1_5_consolidator/
├── contracts.py       SourceEnvelope / ProposalOperation
├── adapter.py         V1 只读 Adapter
├── store.py           V1.5 SQLite、inventory/tombstone、job/cursor
├── gateway.py         ContextGateway、Mock/OpenAI provider
├── engine.py          Validator + Consolidator
├── cli.py             init / run-once / run-loop / status
├── v1_5_run.ps1       Windows 任务计划包装
└── README.md          用法与边界
```

## 3. 当前实现

- `source_ref` / `source_inventory`：来源映射、删除对账；
- `consolidation_job` / `consolidation_cursor`：幂等批次与游标；
- `event_candidate` / `event_revision` / `event_evidence`：候选事件、追加修订、来源证据；
- `context_audit` / `provider_usage` / `validation_issue`：审计、Token、校验失败原因；
- `ContextGateway`：字符预算、默认 `deny_external`、请求审计；
- `ProposalValidator`：action/type/modality/status、source_seq、quote 子串、候选 ID、scope；
- 删除 V1 原话：inventory 对账标记 tombstone，evidence 失效，事件转 `ambiguous`。

## 4. 命令

```powershell
cd dev
..\testbed\.venv\Scripts\python.exe -m v1_5_consolidator.cli `
  --v1-db "...\manual_test_memory.sqlite3" `
  --v15-db "...\v1_5_events.sqlite3" `
  init

..\testbed\.venv\Scripts\python.exe -m v1_5_consolidator.cli `
  --v1-db "...\manual_test_memory.sqlite3" `
  --v15-db "...\v1_5_events.sqlite3" `
  run-once --force --min-messages 1
```

真实 LLM 需显式 `--provider openai --allow-external --api-key-file ...`；默认 mock，不外发。

## 5. 已验证

- 单元测试：`tests.test_v1_5_consolidator` 5 项；
- 真实 testbed 库（3 scope / 27 条）mock 冒烟：3 个事件、27 条证据；
- 真实 DeepSeek 冒烟（20 条单 scope）：7–8 个事件、18–19 条证据；
- **小规模事件划分评测**（4 个标注组 / 14 条消息 / 26 个正对，3 轮；seq 4 为日期上下文，已排除）：

| 指标 | 均值 |
|---|---:|
| pairwise precision | 1.0000 |
| pairwise recall | 1.0000 |
| pairwise F1 | 1.0000 |
| false merge rate | 0.0000 |
| source coverage | 1.0000 |
| events created | 7–8 |

  **判读**：加入“追问/回顾句归入同批事件”的确定性合并后，`seq 16` 被归入搬家事件，
  26 个正对全中。但这只是 **4 个标注组、单 scope、单模型** 的结果，且标签与提示词都经过一轮修正，
  不能当发布准确率；必须扩到 100–200 个正对后再判断。

- 早期 33 正对标签把“日期上下文”和“饮食计划”混在一起，recall 只有 0.70；
  修正标签、加入追问合并后提升到 1.0。这个提升包含**标签口径修正 + 确定性后处理**，
  不是纯模型提升，不能直接对外宣传。

- 首次真实运行曾因 `max_tokens=1200` 截断 JSON；已提高到 4000。

## 6. 已知限制

- LLM 事件划分可能过分割/误合并；当前 7 个事件是单次冒烟，不足以定论；
- 没有 claim、valid time、双时态、实体消歧；
- 真实 LLM 输出仍需 100+ 条标注评测；
- `run-loop` 只是简单循环，生产建议用 Windows 任务计划；
- 删除/备份仍由 V1.0.19 负责；V1.5 自己也要备份；
- 不接入 V1 回复，只做影子。
