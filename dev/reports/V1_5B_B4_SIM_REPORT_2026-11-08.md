# V1.5-B B4 模拟基线报告（Synthetic B4 v1）

- 报告编号：DL-V1.5-B-REP-006
- 日期：2026-11-08
- 数据包：`Digital_Life_B4_TestData_v1.zip`
- 数据性质：**合成/人工规则生成；真实聊天记录 0 条**
- 状态：**模拟基线与分层错误诊断完成；真实 B4 验收仍未完成**

## 1. 结论

| 项 | 结果 |
|---|---|
| 数据校验 | 340 条消息 / 10 scope / 80 事件 / 610 对 / 180 查询 |
| B1 词面召回 | R@1 0.7812 / R@5 **1.000** / R@10 1.000 |
| B1 BGE 召回 | R@1 0.2750 / R@5 **0.9938** / R@10 1.000 |
| B2 全量 pair 基线 | P **0.9662** / R **1.0000** / F1 **0.9828** |
| B2 误合并 | 7 / 610，全部是 `UNRESOLVED` 歧义对 |
| B3 cluster | **未执行**；12 个 reliability 场景仍未跑 |
| 真实语料验收 | **未完成**，0 条真实聊天 |

**总判断：**

> 这组数据说明当前链路在**合成对抗样本**上可以做到“候选召回不瓶颈、关系判别高精度、几乎不误合并”。  
> 但它不是真实语料，不能据此关闭 B4，也不能据此上线。

## 2. 数据包校验

运行数据包自带：

```text
validate_fixtures.py
VALID: 340 messages × 2 layouts, 80 events, 610 labeled pairs, 180 queries, 12 reliability scenarios.
```

分布：

| 项 | 值 |
|---|---|
| SAME_EVENT | 200 |
| RELATED | 150 |
| DIFFERENT_EVENT | 200 |
| UNRESOLVED | 60 |
| 事件类型 | plan 20 / experience 20 / state_change 30 / narrative 10 |
| 数据性质 | synthetic；real_chat_records=0 |
| 输入防泄漏 | 消息输入不含 gold_event_id / gold_action；source_id 不透明 |

## 3. B1 候选召回（模拟）

对象：180 个 retrieval query，其中 160 个 `ATTACH_EXISTING`、20 个 `ABSTAIN`。  
只用 query 之前的来源构造事件锚点；未使用 `gold_event_anchors_DO_NOT_FEED`。

| 方法 | R@1 | R@5 | R@10 | MRR |
|---|---:|---:|---:|---:|
| 词面 BM25-like | **0.7812** | **1.0000** | 1.0000 | **0.8891** |
| BGE + 词面混合 | 0.2750 | 0.9938 | 1.0000 | 0.5352 |

判读：

- 在这组合成数据上，**词面召回已经足够把正确事件放进 Top-5**；
- BGE 混合反而把 R@1 拉低（0.78 → 0.275），说明当前“词面分数 + BGE 分数”的融合方式不适配这组模板化文本；
- 候选召回在 **R@5/R@10 不是瓶颈**；
- 不能由此断言真实对话里词面足够；此前交错留出集里纯词面 R@10 为 0。

## 4. B2 pair 关系基线（全量 610）

对象：`pair_input` + `pair_gold`；每个 pair 给一个 gold candidate event，隔离 B1。

| 指标 | 值 |
|---|---:|
| Pairs | 610 |
| TP | 200 |
| FP | **7** |
| FN | **0** |
| TN | 353 |
| Precision | **0.9662** |
| Recall | **1.0000** |
| F1 | **0.9828** |

标签分布：

```text
SAME_EVENT       207
DIFFERENT_EVENT  300
UNCERTAIN         53
```

### 错误归因

7 个 FP 全部是：

```text
gold = UNRESOLVED
difficulty = ambiguous_reference
predicted = SAME_EVENT
```

没有把 `RELATED` 或 `DIFFERENT_EVENT` 误判为 SAME。  
错误集中在“指代不明”样本，模型把“可能同一事件”过度确认为 SAME。

这与前一轮 B2 合成留出集的结论方向不同：

- 前一轮：precision 1.0，recall 约 0.65（过度保守）；
- 本轮 B4 模拟：recall 1.0，precision 0.966（对歧义过度自信）。

**说明当前关系判别对数据分布高度敏感，不能用一个合成集的结果替代另一个。**

## 5. 分层错误诊断

| 层 | 发现 |
|---|---|
| B1 召回 | R@5≈1.0；正确事件基本都能进候选；R@1 排序不稳 |
| B2 关系 | 同事件无漏判；7 个误合并全部来自 `UNRESOLVED` 歧义 |
| B3 约束 | 未在本数据包跑完整 cluster；现有 Resolver 只能拒绝 cannot-link/多 SAME，不能识别“歧义被模型误判为 SAME” |
| 安全 | 这次模拟没有出现相关/不同事件误合并，但真实误合并率仍未测 |

## 6. 与真实 B4 的差距

这组数据是**人工规则生成的模板化语料**，与真实聊天至少有三点差异：

1. 用词更规整，事件线索更明确；
2. 负例是设计出来的，不是真实对话中的自然歧义；
3. `real_chat_records=0`，不能替代真实脱敏语料。

因此：

- 不能把本报告写成“真实语料准确率”；
- 不能据此关闭 B4；
- 不能据此接入 Shadow/回复。

## 7. 测试

| 项 | 结果 |
|---|---|
| 全量 | **383/383 通过** |
| V1.5 单测 | **18/18 通过** |
| B4 fixture 校验测试 | **2/2 通过** |
| CI | 提交后跑 3.10/3.11/3.12 |

## 8. 结论与下一步

### 已完成

- 数据包一致性校验；
- B1 词面/BGE 模拟召回基线；
- B2 610 pair 全量模拟基线；
- 错误归因：7 个 FP 全为 `UNRESOLVED` 歧义。

### 未完成

- B3 cluster 端到端；
- 12 个 reliability 场景（幂等/cannot-link/删除/503/kill）；
- batch=8/20/40；
- 100–200 条真实脱敏语料；
- 真实误合并率。

### 下一步

1. 用当前 B2 预测生成 B3 cluster predictions，跑 `evaluate_cluster_predictions.py`；
2. 跑 12 个 reliability 场景；
3. 用真实语料替换/补充合成集，冻结 holdout；
4. 在真实集上测 false merge 与 recall；
5. 只有真实门槛通过，才讨论 Shadow。

**当前仍维持：不接入生产。**
