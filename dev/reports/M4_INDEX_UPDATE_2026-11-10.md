# M4 索引同 ID 更新修复

- 日期：2026-11-10
- 状态：**M4 单点修复，不是检索验收**
- 代码：`dev/v1_5_consolidator/candidate_retriever.py`

## 1. 问题

同一 event_id 更新标题：

```text
v1: 熊猫
v2: 火箭
```

基线只更新 anchor，不刷新词面 postings：

- 旧词“熊猫”仍能召回；
- 新词“火箭”无结果。

## 2. 修复

`LexicalCandidateIndex.add`：

- 移除旧 term 的 doc_freq；
- 重建当前 searchable_text 的 terms/length；
- 写回新 anchor。

`EmbeddingCandidateIndex.add`：

- 同 event_id 且 version 变化时重新编码向量；
- 不沿用旧向量。

## 3. 测试

| 测试 | 结果 |
|---|---|
| 词面 v1 熊猫命中 | ✅ |
| v2 火箭后旧词不命中 | ✅ |
| 新词命中 | ✅ |
| 向量同 ID 更新后旧查询为 0 | ✅ |
| 新查询命中 | ✅ |

全量：**468/468**。

## 4. 边界

- 只修复同 ID 更新的索引一致性；
- 不是混合检索质量报告；
- 不是 10 万规模性能；
- 不是 AstrBot 检索接线。
