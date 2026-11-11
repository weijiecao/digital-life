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

## 2.7 340 条第二次真实运行

| run | attached | created | P | R | F1 | FP | pure clusters | mixed | covered |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| run1 | 117 | 223 | 1.0000 | 0.45833 | 0.62857 | 0 | 203 | 0 | 203/320 |
| run2 | 123 | 217 | 1.0000 | 0.48542 | **0.65358** | 0 | 197 | 0 | 197/320 |

判读：

- 两次 FP 都为 0，混合簇都为 0；
- F1 在 0.629–0.654 间波动；
- attached/created 数量也变化；
- 这是模型波动 + unknown provenance 保守共同结果，不是稳定质量。

## 2.8 冻结输入

`m2_frozen_manifest.json` 固定：

- 340 trace run1 SHA-256 `0f839707...`；
- 340 trace run2 SHA-256 `f03cb18f...`；
- 21 run2 SHA-256 `e88ab12e...`；
- B4 message gold SHA-256 `8884a543...`；
- B4 manifest SHA-256 `752c1504...`。

这只固定输入，不把合成 gold 变成真实 gold。

## 2.9 340 来源污染

| 项 | 值 |
|---|---:|
| 有 provenance 的来源 | 311 |
| 附着 | 311 |
| fictional/reported → user-real | **0** |

说明：

- 在 strict replay DB 上逐来源读取有效 event；
- 比较 source provenance 与 event provenance；
- 没有发现叙事来源附着到 user-real 事件；
- 这是合成数据上的来源污染结果，不是真实语料。

## 2.10 21 例第三次真实运行

| run | event correct | story | contamination | guard false reject |
|---|---:|---:|---:|---:|
| run1 | 20/21 | 17/17 | 0 | 0 |
| run2 | 19/21 | 17/17 | 0 | 0 |
| run3 | 20/21 | 17/17 | 0 | 0 |

三次分布 19–20/21；T02-S2 三次都错，T04-N2 一次波动。

判读：

- 污染三次都为 0；
- Guard false reject 三次都为 0；
- 关系判断仍有稳定性问题；
- 不能把单次 20/21 当准确率。

## 2.11 340 第三次真实运行

| run | attached | created | invalid | P | R | F1 | FP | pure | mixed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| run1 | 117 | 223 | 54 | 1.0000 | 0.45833 | 0.62857 | 0 | 203 | 0 |
| run2 | 123 | 217 | 49 | 1.0000 | 0.48542 | 0.65358 | 0 | 197 | 0 |
| run3 | 123 | 217 | 51 | 1.0000 | 0.48750 | **0.65546** | 0 | 197 | 0 |

三次：

- FP 都为 0；
- mixed 都为 0；
- F1 首次 0.629，后两次约 0.654–0.655；
- attached 后两次稳定 123；
- strict Guard 的召回仍然低（约 0.46–0.49）。

## 2.12 21 例第四次真实运行

| run | event correct | story | contamination | guard false reject |
|---|---:|---:|---:|---:|
| run1 | 20/21 | 17/17 | 0 | 0 |
| run2 | 19/21 | 17/17 | 0 | 0 |
| run3 | 20/21 | 17/17 | 0 | 0 |
| run4 | 20/21 | 17/17 | 0 | 0 |

四次分布 19–20/21，均值 19.75；T02-S2 四次都错，其余稳定。

判读：

- 污染四次都为 0；
- Guard false reject 四次都为 0；
- T02-S2 是稳定系统性错误；
- 仍不是独立真实 gold。

## 2.13 同故事不同事件归一化

T02-S2 连续四次被模型判为 `DIFFERENT_EVENT`，但同一 `story_id` 的另一个事件应为 `RELATED_CONTEXT`。

Resolve 层新增：

- 当 source/candidate 都是 story_character 且 story_id 相同；
- 把 `DIFFERENT_EVENT` 归一化为 `RELATED_CONTEXT`；
- 不再创建 cannot-link。

边界：

- 这不改变模型的原始输出；
- 21 例脚本仍按原始预测计分，run5 仍是 20/21；
- 归一化用于实际 DB 决策；
- 需要单独评测归一化后的语义。

## 2.14 340 重放回归

同故事归一化后，340 trace 重放（batch 8）结果：

| 项 | 值 |
|---|---:|
| messages | 340 |
| attached | 117 |
| created | 223 |
| mapping_ambiguous | 0 |
| mapping_missing | 0 |
| scope_claim_failed | 0 |
| job_commit_failed | 0 |
| predicted_clusters | 203 |
| pure_clusters | 203 |
| mixed_clusters | 0 |
| gold_covered | 203 |

与归一化前的 cluster 指标一致（203/203 pure、mixed 0）。

pair F1 对比：

| 指标 | 归一化前 | 归一化后 |
|---|---:|---:|
| TP | 220 | 220 |
| FP | 0 | 0 |
| FN | 260 | 260 |
| precision | 1.0 | 1.0 |
| recall | 0.4583 | 0.4583 |
| F1 | 0.6286 | 0.6286 |

原始 event_id 每次重放都会重新生成，不能直接按 ID 比较；应按 gold 簇和 pair 指标比较。

## 2.15 缺失 candidate 防护

归一化读取 candidate provenance 时，如果 candidate 不存在会 `NarrativeProvenance(**None)` 崩溃。

修复：

- `candidate_data` 为空时不归一化；
- 不抛异常；
- 仍走原有决策路径。

新增测试：missing candidate + same story → 不崩溃、产生 1 条 decision。

## 2.16 340 FN 归因

| 原因 | 对数 |
|---|---:|
| B1 单边未召回 | 124 |
| B3/暂定事件碎片化 | 136 |
| 合计 | 260 |

判读：

- 召回损失约一半来自 B1 候选检索；
- 另一半来自 B3/暂定事件分片；
- 不是单纯分类阈值问题；
- 继续调阈值不会消掉这 260 个 FN。

## 2.17 21 例第五次

| run | event correct | story | contamination |
|---|---:|---:|---:|
| run1 | 20/21 | 17/17 | 0 |
| run2 | 19/21 | 17/17 | 0 |
| run3 | 20/21 | 17/17 | 0 |
| run4 | 20/21 | 17/17 | 0 |
| run5 | 20/21 | 17/17 | 0 |
| run6 | 20/21 | 17/17 | 0 |

五次分布 19–20/21，均值 19.8；T02-S2 仍稳定错误，其余稳定。

## 2.18 B1 top-k 对 340 的影响

| top-k run | attached | created | invalid | clusters | pure | mixed | TP | FP | FN | F1 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 5 | 117 | 223 | — | 203 | 203 | 0 | 220 | 0 | 260 | 0.6286 |
| 10 run1 | 123 | 217 | 51 | 197 | 197 | 0 | 234 | 0 | 246 | 0.6555 |
| 10 run2 | 124 | 216 | 53 | 196 | 196 | 0 | 237 | 0 | 243 | 0.6611 |
| 20 run1 | 135 | 205 | 44 | 185 | 185 | 0 | 259 | 0 | 221 | 0.7009 |
| 20 run2 | 121 | 219 | 53 | 199 | 199 | 0 | 230 | 0 | 250 | 0.6479 |
| 30 | 122 | 218 | 51 | 198 | 198 | 0 | 232 | 0 | 248 | 0.6517 |

判读：

- top-k 从 5 到 10：F1 0.6286 → 0.6555/0.6611，均值 0.6583；
- top20 两次：0.7009 / 0.6479，均值 0.6744，方差明显；
- top30：0.6517，不比 top20 高；
- FP 在所有实验都为 0，簇全 pure；
- 因此 top-k 不是单调杠杆，单次 0.7009 不能当成稳定提升；
- 生产默认 k=10，B4 基线 k=5；
- k10 两次更稳（0.6555/0.6611），k20 均值更高但方差大；
- 风险调整后维持默认 k=10；
- 需要延迟成本后再决定是否提高。

## 2.19 BGE 混合检索（top10）

| 项 | 词面 top10 run1 | 词面 top10 run2 | **BGE top10** |
|---|---:|---:|---:|
| attached | 123 | 124 | **182** |
| created | 217 | 216 | **158** |
| invalid | 51 | 53 | **45** |
| clusters | 197 | 196 | **138** |
| pure | 197 | 196 | **137** |
| mixed | 0 | 0 | **1** |
| TP | 234 | 237 | **358** |
| FP | 0 | 0 | **12** |
| FN | 246 | 243 | **122** |
| precision | 1.0 | 1.0 | **0.9676** |
| recall | 0.4875 | 0.4938 | **0.7458** |
| F1 | 0.6555 | 0.6611 | **0.8424** |

判读：

- BGE top10 两次均值 0.8498；BGE top20 两次均值 **0.8761**，更稳也更高；
- recall 从 0.49 提到 **0.80**；
- 代价是 8–16 个 FP、1 个 mixed cluster（约 0.8%）；
- 这是 B1 召回瓶颈的直接证据；
- 仍是合成 B4，不是真实语料；
- engine hybrid 使用 k=max(limit,20)，与该配置一致。
- 运行成本明显更高（BGE 编码 + 340 次调用）。

## 3. 结论

M2 仍为 **BLOCKED/PREPARING**：21 例 trace 可复放，但 340 条运行与独立 gold 未完成，不能报告质量指标。

旧 trace F1 0.92857 只能作为兼容基线，不能作为 Guard 或真实质量成绩。
