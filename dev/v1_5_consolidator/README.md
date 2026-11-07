# V1.5 定时事件候选归纳服务

独立于 AstrBot/V1 插件的候选事件归纳器。**只读 V1 原话，只写自己的 SQLite；
不创建 V1 正式事件，不向用户暴露。**

## 工作方式

```text
V1 manual_test_memory.sqlite3（只读）
  → 按 scope 读取游标后的原话
  → ContextGateway 预算/审计
  → LLM（或 mock）生成事件划分候选
  → 确定性 Validator：来源、quote、scope、候选 ID
  → V1.5 SQLite：event_candidate / event_revision / event_evidence
  → 影子查询
```

## 快速使用

```powershell
cd dev
..\testbed\.venv\Scripts\python.exe -m v1_5_consolidator.cli `
  --v1-db "C:\AstrBot\data\plugin_data\astrbot_plugin_digital_memory_store\manual_test_memory.sqlite3" `
  --v15-db "C:\AstrBot\data\plugin_data\astrbot_plugin_digital_memory_store\v1_5_events.sqlite3" `
  init

# 先 dry-run：mock provider 不调用外部模型
..\testbed\.venv\Scripts\python.exe -m v1_5_consolidator.cli `
  --v1-db "...\manual_test_memory.sqlite3" --v15-db "...\v1_5_events.sqlite3" `
  run-once --force --min-messages 1

..\testbed\.venv\Scripts\python.exe -m v1_5_consolidator.cli `
  --v1-db "...\manual_test_memory.sqlite3" --v15-db "...\v1_5_events.sqlite3" `
  status
```

真实 LLM：

```powershell
..\testbed\.venv\Scripts\python.exe -m v1_5_consolidator.cli `
  --v1-db "...\manual_test_memory.sqlite3" --v15-db "...\v1_5_events.sqlite3" `
  --provider openai --allow-external `
  --api-key-file "C:\AstrBot\data\.deepseek_key" `
  run-once --force
```

`--allow-external` 必须显式给出；没有它，openai provider 会被拒绝。

## 定时

`run-loop` 是简单的进程内循环，适合开发/自用：

```powershell
... run-loop --interval 3600
```

生产建议用 Windows 任务计划程序每天调用一次 `run-once`，避免常驻进程：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\v1_5_run.ps1
```

## 安全边界

- V1 以 `mode=ro` 打开；不写 `dl_capture` / `events`；
- 只发送当前批次原话 + 少量已有候选标题；
- 模型输出只作为候选，Validator 校验来源/quote/scope/候选 ID；
- 没有有效证据的 operation 不落库；
- 删除 V1 原话后，`source_inventory` 对账会把对应 evidence 标记失效、事件转 `ambiguous`；
- 事件合并/更新必须 append revision，不覆盖历史；
- 不向用户展示事件、审核或调试信息。

## 已知限制

- 当前只支持 `plan / experience / state_change / narrative` 四类；
- 没有完整 claim/双时态，只做事件候选与 revision；
- 真实 LLM 的误合并/误归因仍需评测；
- `run-loop` 不是生产级调度器；生产用外部定时任务；
- 备份、删除、V1 数据安全仍由 V1.0.19 负责。

## 测试

```powershell
cd dev
..\testbed\.venv\Scripts\python.exe -m unittest -q tests.test_v1_5_consolidator
```
