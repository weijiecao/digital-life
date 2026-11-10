# M4 B4 检索质量（词面）

- 日期：2026-11-10
- 状态：**单次词面检索评测，不是混合检索验收**
- 数据：B4 合成 fixture，160 attach 查询，20 个 expected-abstain
- 命令：`python evaluation/v1_5b_b4_retrieval.py --root ...`

## 1. 结果

| 指标 | 值 |
|---|---:|
| method | lexical |
| scopes | 10 |
| attach queries | 160 |
| expected abstain | 20 |
| Recall@1 | **0.7812** |
| Recall@5 | **1.0000** |
| Recall@10 | **1.0000** |
| MRR | **0.8891** |

## 2. 判读

- 词面检索在合成 B4 查询上 R@5 全中；
- R@1 约 0.78，仍有排序改进空间；
- 未评估 expected-abstain 的误召回率；
- 未使用 BGE/混合索引；
- 不是真实查询集，不是 10 万规模检索。

## 3. 边界

- 合成数据，不能代表真实聊天；
- 与 M4-01 索引更新修复分开；
- 不能作为 M4 完成或 R0 检索验收。
