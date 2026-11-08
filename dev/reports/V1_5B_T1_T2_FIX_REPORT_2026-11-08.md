# V1.5-B T1/T2 修复合并报告

- 报告编号：DL-V1.5-B-REP-013
- 日期：2026-11-08
- 补丁包：`DigitalLife_V15B_T1_T2_Fix_Package.zip`
- 状态：**T1/T2 已合并并通过完整回归；仍未进行真实语料验收**

## 1. 合并方式

`git apply --check` 失败：当前离线 `candidate_retriever.py` 已经包含后续 B4 改动，与补丁采集基线不一致。

按补丁说明，**没有强制覆盖**；人工对照 `changed_files` 应用等价修改：

- `HybridCandidateIndex.search` 改为 RRF；
- `ConstrainedResolver.resolve` 增加 fail-closed 入口检查；
- 新增 `tests/test_v1_5b_t1_t2_fixes.py`。

## 2. T1：候选融合

### 修改

旧实现把词面原始分数和 BGE 原始分数直接相加/倒数：

```text
lexical: 1 / (1 + score)
embedding: 1 + score
```

新实现：

```text
RRF(d) = Σ 1 / (60 + rank)
```

- 排名 1 贡献最大；
- 词面与向量排名打平时，优先词面排名；
- 同分最后按 event_id 稳定排序。

### B4 检索对照

同一 B4 合成集，160 个 `ATTACH_EXISTING` 查询：

| 指标 | 修复前 | 修复后 |
|---|---:|---:|
| R@1 | 0.2750 | **0.7937** |
| R@5 | 0.9938 | **1.0000** |
| R@10 | 1.0000 | 1.0000 |
| MRR | 0.5352 | **0.8938** |

判读：

- RRF 修复了“词面高分反而被惩罚”的问题；
- 在该合成集上，R@1/MRR 明显提升；
- 但仍不能宣称真实 BGE 效果提升；需要真实语料/冻结输入对照；
- 词面基线 R@1 为 0.7812、MRR 0.8891，RRF 后接近词面单独效果。

## 3. T2：Resolver fail-closed

### 修改

`resolve` 入口现在检查：

- `judgment.ok == False`；
- `judgment.source_seq != source.source_seq`；
- 无有效 proposal。

无效时：

- 返回 `DEFERRED`；
- 记录 `reason=invalid_judgment`；
- **不调用 Store 任何写方法**；
- 不产生 ATTACHED / CANNOT_LINK / evidence。

### 验证

补丁包 10 项测试全部通过：

- 词面得分方向；
- 排名冲突 RRF 稳定；
- 合法 SAME 归属；
- 重复归属幂等；
- 无效 judgment 禁止 attach；
- 无效 judgment 禁止 cannot-link；
- source_seq 不一致拒绝；
- 空 proposal 不写。

另补 1 项**真实 V15Store SQLite**测试：

```text
无效 judgment → 0 proposal / 0 decision / 0 evidence
```

## 4. 完整回归

| 项 | 结果 |
|---|---|
| 全量 | **411/411 通过** |
| V1.5 单测 | **18/18** |
| B4 fixture | **3/3** |
| B4 reliability | **11/11** |
| B4 gate | **6/6** |
| T1/T2 补丁测试 | **10/10** |
| CI | 待提交后跑 3.10/3.11/3.12 |

## 5. 边界

- T1 的 R@1 提升来自合成 B4，不是真实语料；
- 没有重新跑 340 条完整 B3 cluster（T1 只影响候选排序，T2 只影响无效输入）；
- 没有真实 DeepSeek/BGE 全量对照；
- T2 无效诊断目前只在 `ResolutionSummary.decisions`，没有持久审计表；
- EvidenceGate 仍默认关闭；
- 不接生产/Shadow/回复。

## 6. 结论

**T1/T2 可以合并到离线开发分支。**

- T2 是明确安全修复，建议优先保留；
- T1 是排序修复，B4 合成指标改善，但需要真实冻结输入复核；
- 继续 B4 离线验证，不进入生产。
