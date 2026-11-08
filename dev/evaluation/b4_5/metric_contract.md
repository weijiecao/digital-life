# B4.5 指标契约（metric_contract）

## 1. 分母

| 口径 | 定义 | 当前 B4 synthetic |
|---|---|---:|
| raw pairs | `pair_gold.jsonl` 全部 | 610 |
| b2_evaluated | 同 scope 且右侧有候选；跨 scope 被 B2 排除 | 560 |
| pairwise_scored | 两侧都有 gold_event；排除 UNRESOLVED | 500 |
| cluster pairs | 事件回放后 scope 内可比较消息对 | 4960 |

**任何报告必须写清使用的是哪个分母。**

## 2. 指标

- `pairwise_precision = TP / (TP + FP)`
- `pairwise_recall = TP / (TP + FN)`
- `pairwise_f1 = 2PR / (P + R)`
- `false_merge_per_gold_negative_pair = FP / (FP + TN)`
- `source_coverage = 有归属来源 / 有 gold_event 来源`
- `ambiguous_forced_attach = gold_event=None 且被归属的来源数`
- `abstention_rate`：按 gold SAME / DIFFERENT / UNRESOLVED 分组报告
- `candidate_recall@K`：gold 事件是否在 Top-K；单独报告 lexical / BGE / RRF
- `cluster_contamination`：包含 ≥2 个 gold 事件的 predicted cluster 数 / predicted cluster 总数

## 3. 当前口径

- T1/T2 后 B4 cluster：P=1.0，R=0.86667，F1=0.92857，FP=0，FN=64，ambiguous=20；
- 这是合成 B4，不是真实语料；
- Gate C 组 F1=0.88631；固定 trace 配对显示 Gate 只拦截正确 SAME，无 FP 可拦；
- A/B/C 并非全部配对，不构成严格因果消融。

## 4. 禁止

- 不把 560 的 B2 F1 写成 610 全量；
- 不把 4960 cluster pairs 与 610 B2 pairs 混用；
- 不把 synthetic 指标写成真实记忆准确率；
- 不因 0/50 叙事探测宣称 R11 通过；
- 不在真实语料/故障恢复完成前进入 Shadow。
