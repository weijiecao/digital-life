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
| abstain false injection@1 | **1.0000** |
| abstain false injection@5 | **1.0000** |
| Recall@1 | **0.7812** |
| Recall@5 | **1.0000** |
| Recall@10 | **1.0000** |
| MRR | **0.8891** |

## 2. 判读

- 词面检索在合成 B4 查询上 R@5 全中；
- R@1 约 0.78，仍有排序改进空间；
- expected-abstain 的误召回率为 **1.0**：20/20 都返回了候选；
- 未使用 BGE/混合索引；
- 不是真实查询集，不是 10 万规模检索。

## 2.0 混合检索（BGE）

| method | R@1 | R@5 | MRR | inj@1 | inj@5 |
|---|---:|---:|---:|---:|---:|
| lexical | 0.7812 | 1.0000 | 0.8891 | 1.0000 | 1.0000 |
| bm25+bge-small-zh | **0.7937** | **1.0000** | **0.8938** | **1.0000** | **1.0000** |

判读：

- BGE 只带来 R@1 +0.0125、MRR +0.0047；
- expected-abstain 仍全部返回候选；
- RRF 分数上限约 0.033，阈值 0.034 会同时清空正常查询；
- 因此 RRF 的 min_score 不能替代 abstain gate。

## 2.05 阈值 dev/holdout 切分

按 query_id 哈希奇偶切成 dev 79 / holdout 101：

| 集 | attach | hit | recall | abstain | injected |
|---|---:|---:|---:|---:|---:|
| dev | 69 | 69 | 1.0000 | 10 | 0 |
| holdout | 91 | 91 | 1.0000 | 10 | 0 |

选中阈值 **1.139003**。

判读：

- dev 选阈值后 holdout 仍 0 误注入、attach recall 1.0；
- 这是同一合成查询集的切分，不是独立真实语料；
- 不能据此确定真实阈值。

## 2.06 分 scope 稳定性

阈值 1.139003 下 10 个 scope：

| scope | attach | hit | abstain | injected |
|---|---:|---:|---:|---:|
| S01–S10 | 各 16 | 各 16 | 各 2 | 各 0 |

所有 scope attach recall 1.0、误注入 0；仍是同一合成 fixture，不代表真实跨域泛化。

## 2.1 min_score 阈值消融

| min_score | R@1 | R@5 | MRR | inj@1 | inj@5 |
|---:|---:|---:|---:|---:|---:|
| 0 | 0.7812 | 1.0000 | 0.8891 | 1.0000 | 1.0000 |
| **1.0** | **0.7812** | **1.0000** | **0.8891** | **0.0000** | **0.0000** |
| 2.0 | 0.7063 | 0.8688 | 0.7875 | 0.0000 | 0.0000 |
| 3.0 | 0.4938 | 0.5000 | 0.4969 | 0.0000 | 0.0000 |
| 4.0 | 0.2750 | 0.2750 | 0.2750 | 0.0000 | 0.0000 |

在本合成集上 `min_score=1.0` 保持 R/MRR 不变并把误注入降到 0；这是合成集阈值，不是真实数据标定。默认仍为 0，未静默启用。

## 2.2 候选检索延迟（1000 anchors/100 queries, CPU）

| 索引 | build | p50 | p95 | max |
|---|---:|---:|---:|---:|
| 词面 | 12.89 ms | 2.45 ms | **2.80 ms** | 4.22 ms |
| BGE hybrid | 6,424 ms | 39.10 ms | **41.45 ms** | 46.57 ms |

BGE 查询 p95 约为词面的 14.8 倍；build 需要 6.4 s/1000。

## 2.3 引擎混合入口

`MemoryEngine(store, encoder=...)` 现在可选构建 `HybridCandidateIndex`：

- 默认 `encoder=None` 仍走词面 SQL；
- 传入 encoder 后按 scope 建索引并 RRF 融合；
- ingest/delete/withdraw 会使索引失效并重建；
- 测试用确定性 fake encoder，检索语义匹配成功。

边界：索引在进程内，按 scope 懒加载；不是持久化向量库。

## 2.4 CLI 入口

```powershell
python -m memory_engine.cli --db x.sqlite3 --bge-model <path> search --scope s --query q
```

`--bge-model` 可选；不传时仍走词面 SQL。测试用 fake SentenceTransformer 验证 ingest/search 可走 encoder 路径。

真实 BGE CLI 探针：

- 写入「我喜欢乌龙茶」「今天下雨」；
- 查询「乌龙茶」；
- 第一条命中乌龙茶；
- 但每次 CLI 进程都重新加载 BGE（日志出现 3 次 Loading weights），不适合逐命令生产调用。

## 3. 边界

- 合成数据，不能代表真实聊天；
- 与 M4-01 索引更新修复分开；
- 不能作为 M4 完成或 R0 检索验收；需要 abstain 阈值或 gate。
