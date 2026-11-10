# M2 数据闭环进展（本地）

- 日期：2026-11-10
- 状态：**准备中；未通过**
- 本地基线：448/448

## 1. 已完成

### 21 例冻结 trace

```text
dev/reports/r11_s2_frozen_trace_21.json
schema_version: r11-s2-v1
run_id: r11-21-frozen-2026-11-10
cases: 21
gold_included: false
```

包含：

- 固定 source；
- source provenance；
- candidate provenance；
- model label；
- Guard action；
- post attach 状态。

限制：

- 单次模型运行；
- 未保存候选当时可见成员快照；
- 不能证明稳定性。

### 340 条 provenance trace

340 条 provenance trace 已重新启动，输出目标：

```text
dev/reports/v1_5b_b4_cluster_prov_trace.json
```

运行中；尚未生成结果，不能作为结果。

## 2. 未完成

| M2 项 | 状态 |
|---|---|
| 21 例独立 gold | ❌ |
| 21 例固定回放 | ◐ trace 已有 |
| 340 条 provenance trace | ◐ 运行中 |
| 340 条独立 gold | ❌ |
| 分叉回放 | ✅ 固定 fixture |
| 分层 P/R/F1、污染、碎片 | ❌ |
| 真实脱敏留出集 | ❌ |

## 2.1 21 例计分（非独立 gold）

`dev/evaluation/m2_score_21.py`：

| 指标 | 值 |
|---|---:|
| cases | 21 |
| raw event correct | **20/21** |
| Guard effective correct | **20/21** |
| contamination | **0** |

分类：same story same 4、same story different 5、different story 4、narrative→user 4、user real 4。

限制：gold 来自生成器标签，不是独立留出集；不能作为泛化准确率。

## 2.2 340 首轮失败归因

首次 340 运行：

```text
attached 0 / created 340 / invalid 72
```

归因：

- `ConstraintResolver` 已默认启用 ProvenanceGuard；
- 新建候选事件没有写入 provenance，全部为 unknown；
- 来源为 user-real 时 Guard ABSTAIN，因此所有 SAME 都不附着；
- 这不是模型没有判 SAME（trace 中有 33 个 SAME）。

修复：

- cluster 创建事件时写入 `judgment.provenance`；
- 单 scope 复测 attached 14 / created 20 / invalid 4。

340 已重新运行，结果待定。

## 2.3 340 条 provenance trace 结果

| 指标 | 值 |
|---|---:|
| messages | 340 |
| attached | 117 |
| created | 223 |
| invalid | 54 |
| pairwise precision | 1.00000 |
| pairwise recall | 0.45833 |
| pairwise F1 | **0.62857** |
| FP | 0 |
| FN | 260 |

与旧 trace F1 0.92857 相比，recall 明显下降。

归因：

- 前几条消息没有候选，创建的事件 provenance 为 unknown；
- 后续 user-real 来源与 unknown 候选比较时，ProvenanceGuard ABSTAIN；
- 因此 SAME 不附着，产生大量 FN；
- 这是保守 Guard 与“无候选来源没有 provenance”共同造成，不是单纯模型退化。

结论：

- 该 trace 可以用于“Guard 开启后的保守代价”分析；
- 不能直接作为质量提升证据；
- 需要为无候选来源补充 provenance 或显式 unknown 策略。

## 2.4 Guard unknown-candidate 策略消融

固定 340 trace、不调用模型：

| 策略 | P | R | F1 | FP | FN | false merge |
|---|---:|---:|---:|---:|---:|---:|
| strict ABSTAIN | 1.00000 | 0.45833 | 0.62857 | 0 | 260 | 0 |
| allow_user_real | 0.92233 | 0.59375 | **0.72243** | 24 | 195 | 0.00536 |

判读：

- 放宽 unknown candidate 能提高召回和 F1；
- 但引入 24 个 FP，false merge 0.00536；
- 当前没有独立 gold 证明这些 FP 可接受；
- 因此默认仍保持 strict，不因为 F1 好看就放宽。

这是显式策略开关，不是静默改默认。

## 2.5 340 条簇级质量

对 B4 fixture 独立 gold 做簇纯度检查（不是真实语料）：

| 策略 | predicted clusters | pure | mixed | mixed rate | gold events covered |
|---|---:|---:|---:|---:|---:|
| strict ABSTAIN | 203 | 203 | **0** | 0.0000 | 203/320 |
| allow_user_real | 153 | 151 | **2** | 0.0131 | 155/320 |

判读：

- strict 没有任何 gold 事件混合，但只覆盖 203/320 个 gold 事件；
- 放宽后覆盖 155/320，却出现 2 个混合簇；
- 与 pairwise 结果一致：放宽提高召回、增加误合并；
- 都不是可接受的质量结论，需要独立真实 gold 决策。

## 2.6 21 例两次真实运行

| run | event correct | story membership | contamination | guard false reject |
|---|---:|---:|---:|---:|
| frozen trace run | 20/21 | 17/17 | 0 | 0 |
| rerun 2026-11-10 | **19/21** | 17/17 | 0 | 0 |

两次共同错误：

- T02-S2：同故事不同情节判 DIFFERENT，应 RELATED；

波动错误：

- T04-N2：一次 DIFFERENT，一次 SAME；SAME 被 Guard REJECT，所以最终污染仍 0。

说明：

- 21 例不是稳定单一数字；
- Guard 能拦住波动产生的错误 SAME；
- 不能把 20/21 或 19/21 当成真实泛化准确率。

## 3. 结论

M2 仍为 **BLOCKED/PREPARING**：21 例 trace 可复放，但 340 条运行与独立 gold 未完成，不能报告质量指标。

旧 trace F1 0.92857 只能作为兼容基线，不能作为 Guard 或真实质量成绩。
