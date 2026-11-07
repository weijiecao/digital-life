# Digital Life V1.5-B / B4 合成对抗测试集（v1.0）

> **性质声明：全部为人工设计规则生成的合成数据；真实聊天记录数为 0。**
> 它用于 B1 候选召回、B2 关系判断、B3 Resolver、B4 端到端回归，不可替代真实对话上的发布验收，也不能把结果描述成“真实语料准确率”。

## 1. 与当前验证阶段的关系

当前报告（DL-V1.5-B-REP-005）指出：B3.2 已实现多个 SAME 归入 CONFLICT、cannot-link 持久、重启后幂等；全量 381/381、V1.5 18/18；B2 在合成数据上 precision 1.0、recall 约 0.64–0.68。下一步需要真实语料、batch=8/20/40 端到端、强杀进程后事务恢复和来源删除后的关系重建。

本数据包提供 **结构性回归与算法对抗样本**：

- 10 个模拟 `scope`，80 个金标准事件；
- 340 条用户消息（含 320 条可归属提及、20 条应拒判的含糊指代）；
- 相同内容、相同 source_id、相同事件标签的两种排列：交错与连续；
- 610 条带金标准的消息对：SAME_EVENT 200、RELATED 150、DIFFERENT_EVENT 200、UNRESOLVED 60；
- 180 个候选召回查询：160 个应指向既有事件、20 个应拒判；
- 12 个故障、幂等、删除与跨批次场景（**只有测试规范，不是已经通过的执行结果**）。

**重要：** 所有数据自动生成，在表达风格上具有模板化偏差。调算法时请同时保留此前的原始留出集，不要反复在同一批样本上调 Prompt；真实标注集必须单独冻结。

## 2. 文件说明

| 文件 | 用途 |
|---|---|
| `messages_interleaved.jsonl` | 10 个 scope 内事件逐次交错的消息流（主要回归） |
| `messages_contiguous.jsonl` | 同一批消息按事件连续排列（消融对照） |
| `message_gold.jsonl` | 消息归属的评分答案，与输入消息分开存储 |
| `gold_events.jsonl` | 80 个事件身份、类型、对应来源 ID 的金标准 |
| `gold_event_anchors_DO_NOT_FEED.jsonl` | 仅供评分与错误分析的金标准锚点，**禁止直接输入被测检索器** |
| `pair_input.jsonl` | B2 被测输入：只有 pair ID 和两条来源 ID，不包含标签 |
| `pair_gold.jsonl` | B2 关系分类和 false merge 的评分答案 |
| `retrieval_queries_input.jsonl` | B1 被测查询，不包含正确事件 ID |
| `retrieval_query_gold.jsonl` | B1 Top-K 候选检索问题的评分答案 |
| `reliability_scenarios.jsonl` | B3 幂等、cannot-link、删除、503、kill 等测试规范 |
| `real_annotation_template.jsonl` | 用户授权并脱敏的真实数据标注模板（仅占位符） |
| `generate_dataset.py` | 固定规则重建样本 |
| `validate_fixtures.py` | 数据集一致性验证 |
| `evaluate_pair_predictions.py` | 评估 B2 SAME precision/recall/F1、误合并、拒判 |
| `evaluate_retrieval_predictions.py` | 评估 B1 Recall@1/5/10 与跨 scope 污染 |
| `evaluate_cluster_predictions.py` | 评估 B3 端到端 pairwise F1、覆盖率、误合并 |
| `manifest.json` | 样本规模清单 |

## 3. 最快开始

要求 Python 3.10+，不需要第三方依赖。

```bash
python validate_fixtures.py
python generate_dataset.py
python validate_fixtures.py
```

使用 `pair_input.jsonl` 中的 pair_id 和两条 source ID 到消息输入文件中取文本，运行你的 B2 关系模型后，输出独立 JSONL，例如 `pair_predictions.jsonl`：

```json
{"pair_id":"P0001","relation":"SAME_EVENT"}
{"pair_id":"P0002","relation":"UNRESOLVED"}
```

执行：

```bash
python evaluate_pair_predictions.py --pred pair_predictions.jsonl
```

使用 `retrieval_queries_input.jsonl` 和 `messages_interleaved.jsonl` 运行 B1 候选召回后，输出 `retrieval_predictions.jsonl`（示例的哈希 ID 是格式占位，实际必须来自当前数据集）：

```json
{"query_id":"Q0001","candidate_source_ids":["S01-R0123456789abcd"],"decision":"ATTACH_EXISTING"}
```

执行：

```bash
python evaluate_retrieval_predictions.py --pred retrieval_predictions.jsonl
```

运行完整 Resolver 后，输出 `cluster_predictions.jsonl`，`predicted_event_id` 可使用你的实现产生的任意稳定事件 ID（示例中的来源 ID 仅作格式说明）：

```json
{"source_id":"S01-R0123456789abcd","predicted_event_id":"actual_event_a"}
{"source_id":"S01-Rfedcba98765432","predicted_event_id":null}
```

执行：

```bash
python evaluate_cluster_predictions.py --pred cluster_predictions.jsonl
```

**禁止把 `message_gold.jsonl`、`gold_events.jsonl`、`pair_gold.jsonl`、`retrieval_query_gold.jsonl` 或 `gold_event_anchors_DO_NOT_FEED.jsonl` 传入被测模型。** 消息 ID 采用不含可读事件编号的不透明哈希，输入消息文件不含 `gold_event_id`、`gold_action` 等标签。

## 4. 正确接入当前 V1.5 Sidecar

本包是 **通用 JSONL 测试夹具，不代表已经适配 `dev/v1_5_consolidator` 的内部数据表**。建议增加一个测试专用的输入转换器：将 `messages_interleaved.jsonl` 转成现有只读适配器接受的标准 SourceEnvelope，或者写入完全隔离的 fixture 数据库。

- 测试用独立 SQLite；绝不能把金标准事件写进 V1 生产库；
- 每个 `scope_id` 单独重放，或者确保跨 scope 的隔离逻辑被显式测试；
- 相同数据跑 batch=8、20、40，并重建干净数据库；
- 固定 LLM 模型、Prompt、温度/采样参数、validator 版本；每组至少重复 3 次；
- 记录来源覆盖、候选召回、SAME precision/recall、false merge、拒判、延迟和 Token；
- 对每个失误分配 `retrieval_miss` / `judge_error` / `resolver_reject` / `invalid_source` 等原因；
- 金标准锚点只供评测对照；被测系统应从流中自行构建候选事件。任何用于检索的来源必须在当前 query 之前出现；不能读取未来消息或预加载金标准事件；
- `messages_contiguous` 为排列对照，除事件顺序与时间戳外消息与标签相同；不能和交错版重复算作互相独立的真实样本。
- 消息与查询输入不含标准答案，评分答案文件应只在评估进程中打开，严禁作为事件引擎的初始化数据。

## 5. 标签定义

`SAME_EVENT`：两条来源提及同一个事件身份。

`RELATED`：有可描述关系但事件 ID 不相同，例如同项目下不同任务、同地点不同日期的出行、同类但独立的登山计划。对 B3 Resolver **不能 attach**。

`DIFFERENT_EVENT`：事件不同，尤其包含故事情节与现实计划、跨 scope 等对抗负例。

`UNRESOLVED`：仅凭当前来源不能确定事件身份，不得强行归属。评测时应单独统计拒判率。

注意：`RELATED` 与 `DIFFERENT_EVENT` 对二元“是否 SAME”指标均为负例，不能直接误认为 `RELATED` 可以 attach。

## 6. 评估指标与报告边界

- **B1 Recall@10**：预测 `candidate_source_ids` 的前 10 个是否含有任一条同事件的**更早来源**。同时检查跨 scope 或使用未来来源的泄漏，对无 gold_event_id 的来源测试拒判。
- **B2 SAME Precision/Recall/F1**：从 `pair_gold.jsonl` 计算；false merge = 预测 SAME 且真值属于 RELATED/DIFFERENT 的对数 ÷ 全部 gold 负例对数。
- **B3 Pairwise F1**：从每个 scope 下所有确定归属的消息两两计算；额外报告 `source_coverage` 和给含糊消息误 attach 的数量。
- **B3 cannot-link / restart / deletion / kill**：必须使用可靠性测试脚本或注入真实进程故障执行。`reliability_scenarios.jsonl` 只是测试规约，**不是自动化通过证明**。
- **禁止跨 scope 关联**：任何真实服务都必须强制校验，不能只依赖 Prompt。

建议沿用此前门槛：候选 Recall@10≥0.95、交错 Pairwise F1≥0.80、false merge≤0.05、source coverage≥0.95；**仅作为合成集研发门槛**。真正发布还需要独立的真实标注和可靠性报告。

## 7. 如何构建真实标注数据（B4 发布验收不可省略）

1. 在明确授权前提下，从已有对话记录中抽取不同时间、不同 scope 的片段，在本地进行脱敏处理；不要上传包含敏感身份信息的未经处理原文。
2. 对原始消息赋予不可复用的来源 ID，记录来源时间、说话者和 scope（可为匿名标识）。
3. 人工建立标注：某几段消息是否同事件、是否同主题但非同事件、是否无法判断。
4. 优先标注 **100–200 个真实正对**、**50+ 困难负对**；若要精确判断误合并率，应扩展负例并报告置信区间。
5. 按完整事件/会话/时间段拆分开发集与冻结留出集，不能先看模型错误再重写留出标签。
6. 脱敏数据与原文的映射只放在本地受保护位置，评测文件不得混入生产记忆库。

真实标注数据生成后，可沿用本包的字段与评测脚本；对于用户没有确认的事件，不要因为模型输出就将它作为真实标签。

## 8. 已知局限

- 模板化文本相对容易，不能代表自然闲聊、隐含代词和长篇故事的复杂度；
- 同一事件每组固定四条，实际事件长度可能极不均衡；
- 部分强区分标志（不同日期、不同线路）会使模型更容易识别，不能借此推断真实召回率；
- 事件锚点和来源 ID 是评分/训练夹具，必须遵守可见时间，防止未来知识泄漏；
- `UNRESOLVED` 来源是明确设计为无唯一指代的句子，用于测试拒判，不等于所有真实上下文下仍不可解析。

**结论：本包适合立即开展离线结构回归；真实世界准确率必须另行验证。**
