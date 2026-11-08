# V1.5-B B4.5-0/1/2 基线、复测与错误归因

- 报告编号：DL-V1.5-B-REP-014
- 日期：2026-11-08
- 基线提交：`3f4ebf3`
- 数据：Synthetic B4 v1（real_chat_records=0）
- 状态：**B4.5-0/1/2 完成；真实语料与真正 batch 未完成**

## 1. B4.5-0 冻结

产物：

- `dev/evaluation/b4_5/baseline_manifest.json`
- `dev/evaluation/b4_5/metric_contract.md`

冻结：

| 项 | 值 |
|---|---|
| commit | `3f4ebf3` |
| tests | 411 |
| T1 | RRF，rrf_k=60 |
| T2 | fail-closed |
| CI | 3.10/3.11/3.12 success |
| 数据 | Synthetic B4 v1；real=0 |
| 分母 | 610 raw / 560 B2 / 500 pairwise |

## 2. B4.5-1 Cluster 复测

| 实验 | 排序/安全 | P | R | F1 | FP | FN | ambiguous |
|---|---|---:|---:|---:|---:|---:|---:|
| A 历史 | 旧融合/旧逻辑 | 0.97136 | 0.84792 | 0.90545 | 12 | 73 | 20 |
| B 修复 | RRF/fail-closed | **1.00000** | **0.86667** | **0.92857** | **0** | 64 | 20 |
| C 探索 | RRF + Gate | 1.00000 | 0.79583 | 0.88631 | 0 | 98 | 20 |

判读：

- T1/T2 后 B 组 F1 0.905 → **0.92857**；
- FP 12 → **0**；
- FN 73 → 64；
- ambiguous 仍为 **20**；
- A/B 是不同模型调用回放，不是逐对配对；C 是另一组调用；
- 不能把全部差异归因于 T1/T2。

## 3. B4.5-2 错误归因（T1 后）

对 T1 后 trace 的最终簇级审计：

| 项 | 数量 |
|---|---:|
| FP | **0** |
| FN | **64** |
| ambiguous 强制归属 | **20** |
| FN 原因 | `split_across_clusters` ×64 |

结论：

- 12 个旧 FP 在 T1 后消失；
- 误差从“误合并”转为“过度拆分”；
- 64 FN 仍无法区分 B1/B2/B3/provisional，需要逐消息 trace（当前已有 trace 字段，但根因分类尚未自动化）；
- 20 ambiguous 仍未解决。

## 4. Gate 配对结论

固定 T1 trace 的 227 个 SAME proposal：

| 结果 | 数量 |
|---|---:|
| ALLOW + 正确 | 201 |
| ABSTAIN + 正确 | 16 |
| REJECT + 正确 | 10 |
| 拦截 + 错误 | **0** |

Gate 只损失 26 个正确 SAME，没有阻止任何错误目标；**默认关闭**。

## 5. 测试

| 项 | 结果 |
|---|---|
| 全量 | **411/411** |
| CI | 3.10/3.11/3.12 success |
| B4 fixture | 3/3 |
| B4 reliability | 11/11 |
| B4 gate | 6/6 |
| T1/T2 | 10/10 |

## 6. 未完成

- 真正的 batch=8/20/40 状态机；
- R11 第一人称/转述/嵌套/纠错；
- 真实脱敏 holdout；
- B1/B2/B3 逐消息根因自动分类；
- Shadow/生产。

## 7. 结论

**B4.5-0/1/2 已完成。**

T1/T2 修复在合成 B4 上改善了 F1 和 FP；但当前主要错误已转为 64 个 FN 和 20 个 ambiguous，不能靠 Gate 解决。下一步应做真正的 batch 状态机，并把 FN 逐消息归因，而不是继续加规则。
