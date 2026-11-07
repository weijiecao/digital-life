# V1.5 定时事件候选归纳 — 工程验证报告

- 报告编号：DL-V1.5-VAL-001
- 日期：2026-11-07
- 代码提交：`463853a`
- 验证对象：`dev/v1_5_consolidator`（独立 Sidecar 服务）
- 关联 V1：`astrbot_plugin_digital_memory_store` v1.0.19
- 报告性质：**工程验证，不是发布验收**

## 1. 结论摘要

| 项目 | 结论 |
|---|---|
| V1.0.19 稳定性 | **可维护**：371 项回归、CI 三版本全绿、数据安全层已落地 |
| V1.5 事件候选链路 | **跑通**：只读 V1、LLM 划分、确定性校验、独立 SQLite、删除对账 |
| 连续块事件划分 | **有希望**：留出连续块集 F1 **1.000** |
| 交错多事件窗口 | **不可用**：留出交错集 F1 **0.387–0.433**，false merge **28–36%** |
| 缩小批次 | **不是修复**：batch=8 时 F1 **0.030**、coverage **0.500** |
| 是否接入回复/影子检索 | **否**：当前不满足接入门槛 |
| V2 claim/双时态 | **未实现**：V1.5 只有事件候选/revision/evidence |

**总判断：**

> V1.5 是一个可运行的“事件候选归纳 Alpha”，在连续块上表现好；
> 但真实对话一旦多事件交错，当前方案会大量误合并/漏合并。  
> **不能把调参集的 1.0 当作可用性结论，也不能现在接入 V1 回复。**

## 2. 验证环境

| 项 | 值 |
|---|---|
| 操作系统 | Windows 11 |
| Python | 3.12（本地）；CI 3.10/3.11/3.12 |
| V1 插件 | `astrbot_plugin_digital_memory_store` v1.0.19 |
| V1 数据库 | `manual_test_memory.sqlite3`（SQLite WAL） |
| V1.5 数据库 | 独立 `v1_5_events.sqlite3` |
| LLM | DeepSeek `deepseek-chat`（真实冒烟/评测） |
| 测试入口 | `python -m unittest ...`；`dev/scripts/report.py` |
| CI | GitHub Actions `tests.yml`，Windows + 3.10/3.11/3.12 |

## 3. 已实现范围

```text
V1 SQLite（只读）
  → 按 scope 读游标后的原话
  → ContextGateway（字符预算、审计、默认 deny_external）
  → LLM 输出事件划分候选
  → 确定性 Validator（来源/quote/scope/候选 ID/枚举）
  → 追问/回顾句确定性合并
  → V1.5 SQLite：event_candidate / event_revision / event_evidence
  → 删除对账 tombstone
```

已实现：

- `V1ReadOnlyAdapter`：`mode=ro`，不写 V1；
- `source_inventory`：全量对账、删除 tombstone、证据失效、事件转 `ambiguous`；
- `consolidation_job` / `consolidation_cursor`：幂等批次、游标；
- `event_candidate` / `event_revision` / `event_evidence`：候选、追加修订、来源证据；
- `validation_issue` / `provider_usage` / `context_audit`：校验失败、Token、发送审计；
- `unknown` 事件类型：无法归类时允许保留候选，不强行编造；
- 追问/回顾句合并：`之前/后来/上次/那件事/怎么样了/然后呢`；
- CLI：`init / run-once / run-loop / status`；
- 5 类事件：`plan / experience / state_change / narrative / unknown`。

**未实现：**

- claim（subject/predicate/object）；
- valid time / record time 双时态；
- 实体消歧/事件图谱；
- 本地预聚类；
- 影子检索接入；
- V1 回复接入；
- 生产调度/监控。

## 4. 测试结果

### 4.1 回归与 CI

| 项 | 结果 |
|---|---|
| 全量回归 | **371/371 通过**（约 40 s） |
| V1.5 单测 | **8/8 通过** |
| V1 原有回归 | 363 项保持通过 |
| CI | 3.10 / 3.11 / 3.12 **全部 success** |
| 唯一数字来源 | `dev/STATUS.md` 生成于 2026-11-07 21:06 |

V1.5 单测覆盖：

1. mock 事件创建 + 游标；
2. 错误 quote 拒绝且游标不动；
3. 跨 scope 候选拒绝；
4. 删除原话后证据失效、事件 ambiguous；
5. 追问句唯一高重合时合并；
6. 追问句无词面重合时不合并；
7. 两个事件重合分数并列时不合并；
8. `unknown` event_type 合法。

### 4.2 真实 LLM 冒烟

testbed 最大 scope，20 条真实测试原话：

| 轮次 | 事件数 | 证据数 | 校验拒绝 |
|---|---:|---:|---:|
| 第一版提示词 | 20 | 20 | 0 |
| 加“同主题合并”约束 | 7 | 18 | 2 |
| 提高 max_tokens 后 | 8 | 19 | 0 |

结论：

- LLM 能输出结构化事件划分；
- 第一版严重过分割（每条一个事件）；
- 加入合并约束后降到 7–8 个事件；
- 存在 run-to-run 波动，不能据单次结果定准确率；
- 首次 JSON 截断源于 `max_tokens=1200`，已提高到 4000。

### 4.3 调参集

4 个事件组 / 14 条消息 / 26 个正对，3 轮：

| 指标 | 均值 |
|---|---:|
| pairwise precision | 1.0000 |
| pairwise recall | 1.0000 |
| pairwise F1 | 1.0000 |
| false merge | 0% |
| source coverage | 1.0000 |

**但这不能当发布准确率：**

- 只有 26 个正对；
- 标签经过一轮修正（移除了“日期上下文”误归组）；
- 追问合并是确定性后处理；
- 同一 scope、同一模型；
- 标签与提示词都在同一小集上迭代过。

### 4.4 留出集（关键结果）

生成式留出集：**200 条消息 / 10 scope / 40 事件组 / 120 正对**，不来自调参样本。

| 布局 | F1 | Precision | Recall | False Merge | Coverage |
|---|---:|---:|---:|---:|---:|
| 连续块（同事件 3 条相邻） | **1.000** | 1.000 | 1.000 | 0% | 1.000 |
| 交错（4 事件逐条穿插） | **0.387–0.433** | 0.50–0.52 | 0.31–0.38 | **28–36%** | 0.94 |

交错集两次运行：

| 运行 | F1 | Precision | Recall | False Merge | Coverage |
|---|---:|---:|---:|---:|---:|
| 初始 | 0.3874 | 0.5211 | 0.3083 | 28.33% | 0.9417 |
| 加“不同日期不同事件”提示 | 0.4327 | 0.5114 | 0.3750 | 35.83% | 0.9417 |

**判读：**

- 连续块上 LLM 几乎完美；
- 交错窗口内，模型会把不同事件合并，或把同一事件拆开；
- 加一条日期区分提示只能小幅提升，不能解决结构问题；
- 当前 V1.5 **不能假设真实对话总是连续块**。

### 4.5 窗口大小消融

同一交错留出集：

| batch_limit | F1 | Recall | Coverage |
|---:|---:|---:|---:|
| 40 | 0.387–0.433 | 0.31–0.38 | 0.94 |
| 20 | 0.4061 | 0.3333 | 0.9583 |
| 8 | **0.0299** | **0.0167** | **0.5000** |

结论：

- 缩小批次不是免费修复；
- 8 条一批时跨批次事件关联断裂，coverage 掉到 0.5；
- 必须先做本地预聚类/候选桥接，而不是继续切窗口。

## 5. 关键发现

1. **LLM 能做事件候选划分，但强依赖消息相邻性。**
   连续块可用；交错多事件窗口不可用。

2. **调参集 1.0 是过拟合信号。**
   26 个正对、同 scope、标签和提示词一起迭代，不能外推。

3. **确定性后处理能修局部问题，不能修结构问题。**
   追问合并修好了 `seq 16`，但对交错窗口整体 F1 帮助有限。

4. **缩小窗口会破坏跨批次关联。**
   batch=8 的 F1 0.03 / coverage 0.5 说明“先切小”不是答案。

5. **V1.5 的输出仍是候选，不是事实。**
   `event_revision` 不是 claim；没有双时态、没有实体消歧、没有冲突处理。

## 6. 风险与缺陷

| 风险 | 级别 | 说明 |
|---|---|---|
| 交错事件误合并 | **高** | false merge 28–36%，会串联不同计划 |
| 交错事件漏合并 | **高** | recall 0.31–0.38，事件碎片化 |
| 小样本过拟合 | **高** | 调参集仅 26 正对 |
| 合成留出集不代表真实语料 | **中高** | 需要 100–200 条真实脱敏标注 |
| `unknown` 候选质量 | **中** | 允许保留，但检索时需降权 |
| 无 claim/双时态 | **中高** | 不能回答“当时什么是真的” |
| 无生产调度 | **中** | `run-loop` 只是简单循环 |
| 外部 LLM 隐私/成本 | **中** | 默认 mock；真实调用需显式允许 |
| V1.5 自身备份/恢复 | **中** | 尚未做与 V1 同等级的数据安全 |

## 7. 是否可接入

### 当前结论：**不接入**

| 门槛 | 目标 | 当前 |
|---|---|---:|
| 交错留出集 F1 | ≥0.80 | 0.39–0.43 |
| false merge | ≤5% | 28–36% |
| source coverage | ≥0.95 | 0.94 |
| 真实标注 | 100–200 正对 | 0 |
| claim/双时态 | 按 V2 范围 | 未做 |

因此：

- 不接 V1 回复；
- 不接影子检索；
- 不创建 V1 正式事件；
- 不对外宣称“事件召回可用”。

## 8. 建议的下一步

### P0：本地预聚类（先于更多 prompt 调优）

1. 用时间间隔、实体、地点、代词、主题词把窗口切成候选块；
2. LLM 只对候选块做“是否同一事件”的判断；
3. 保留跨批次候选桥接，避免 batch=8 的 coverage 崩塌；
4. 用交错留出集验证，目标 F1 ≥0.8、false merge ≤5%。

### P1：真实语料评测

- 100–200 个正对，来自不同 scope/主题；
- 固定 prompt/validator，不再改标签；
- 按 `plan / experience / state_change / narrative / unknown` 分组报告；
- 加 30–50 个负例事件对，专测 false merge。

### P2：V1.5 运行化

- Windows 任务计划每日 `run-once`；
- V1.5 数据库备份/恢复；
- 管理员 `status / rebuild / run_once`；
- 失败重试、lease 回收、Token 预算。

### P3：V2

只有 P0/P1 达标后，才讨论 claim、valid time、record time、实体消歧和影子检索。

## 9. 证据索引

| 证据 | 路径 |
|---|---|
| 全量测试状态 | `dev/STATUS.md` |
| V1.5 文档 | `dev/docs/V1_5_CONSOLIDATOR.md` |
| 真实 LLM 冒烟 | `dev/reports/v1_5_real_smoke_20261107.json` |
| 调参集 3 轮 | `dev/reports/v1_5_event_eval_3rounds_v4.json` |
| 留出交错集 | `dev/reports/v1_5_heldout_eval.json`、`v1_5_heldout_eval_datefix.json` |
| 留出连续块 | `dev/reports/v1_5_heldout_contiguous_eval.json` |
| batch=8 | `dev/reports/v1_5_heldout_eval_batch8.json` |
| batch=20 | `dev/reports/v1_5_heldout_eval_batch20.json` |
| 留出集生成器 | `dev/evaluation/v1_5_make_heldout.py` |
| 留出集评测器 | `dev/evaluation/v1_5_heldout_eval.py` |

## 10. 最终结论

**V1.0.19 可以作为稳定存档与混合召回基座继续维护。**

**V1.5 已验证“事件候选归纳链路”可运行，并在连续块上有希望；但交错真实对话下 F1 仅约 0.4、误合并 28–36%，不满足接入条件。**

**下一步应做本地预聚类和真实语料评测，而不是继续调 prompt、缩小窗口或直接接入回复。**
