# M4 统一检索门面：min_score 接入引擎

- 日期：2026-11-10
- 状态：**引擎 retrieve/context/CLI 支持 min_score；默认 0 保留旧行为**
- 测试：502/502

## 1. 改动

- `MemoryEngine.retrieve(..., min_score=0.0)`；
- `build_context(..., min_score=...)`，缓存 key 包含 min_score；
- `build_rendered_context(..., min_score=...)`；
- CLI `search/context --min-score`；
- min_score>0 时用 `LexicalCandidateIndex` 对候选重排并过滤；0 时保留 SQL 候选池。

## 2. 测试

| 测试 | 结果 |
|---|---|
| min_score=0.1 返回 1 条 | ✅ |
| min_score=1000 返回空 | ✅ |
| 旧 retrieve/context 行为 | ✅ |

## 3. 边界

- 默认仍不过滤；
- 阈值 1.0 来自 B4 合成集，不是真实数据标定；
- 混合检索的 RRF 不能直接复用该阈值；
- 不是完整 M4 门面（未统一约束过滤/候选来源）。
