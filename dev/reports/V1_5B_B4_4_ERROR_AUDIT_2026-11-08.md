# V1.5-B B4.4-1 错误归因报告

- 报告编号：DL-V1.5-B-REP-011
- 日期：2026-11-08
- 输入：`v1_5b_b4_cluster_batch8.jsonl`（R12 增量回放）
- 数据：Synthetic B4 v1（real_chat_records=0）
- 状态：**最终簇级归因完成；缺少逐消息 B1/B2/B3 trace**

## 1. 结果

| 项 | 数量 |
|---|---:|
| FP pair | **12** |
| FN pair | **73** |
| ambiguous 被强制归属 | **20** |
| FP 原因 | `cross_event_state_change` ×12 |
| FN 原因 | `split_across_clusters` ×73 |

## 2. 12 个 FP

全部集中在 **同一个 scope（S09）**：

```text
gold S09-E05 (state_change, 4 sources)
gold S09-E06 (state_change, 4 sources)
  → predicted 同一个 evt_3d3f... 
```

12 个 FP 是 E05 与 E06 的跨事件组合（4×4=16 个跨对，其中 12 个被计入 FP；同事件内部 4 个对仍正确）。

判读：

- 不是多个 scope 的普遍误合并；
- 是**单次链式合并**把两个 state_change 事件串成一个簇；
- 现有 Resolver 的 cannot-link 没有阻止它，因为 B2 很可能把桥接消息判成了 SAME；
- 这是“关系误判 + 链式合并”的高风险样本，应作为 EvidenceGate 首要目标。

## 3. 73 个 FN

最终簇级原因全部是：

```text
split_across_clusters
```

即 gold 同事件的消息被分到不同 predicted 事件。  
这不是“B1 没召回”的证明；最终簇级数据无法区分：

- B1 候选缺失；
- B2 判成 RELATED/DIFF；
- B3 拒判；
- provisional 事件创建过早；
- 批次状态未共享。

需要在回放中保存每消息的候选、B2 label、B3 decision 才能细分。

## 4. 20 个 ambiguous 强制归属

10 个 scope 各有约 2 个 `gold_event_id=None` 的来源被赋了 predicted event。  
这与 B2 在 B4 pair 上的 7 个 `UNRESOLVED → SAME_EVENT` 方向一致：  
**模型对歧义指代过度自信，B3 又没有“证据不足”门控。**

## 5. 对 B4.4 的意义

- FP 不是分散噪声，而是**一个可定位的链式合并案例**，适合先做 Gate 对照实验；
- FN 是广泛碎片化，但根因未分层；
- 20 个 ambiguous 是明确安全缺陷；
- 不能只靠总 F1 0.905 判断系统可靠。

## 6. 下一步

1. 给 R12 回放增加逐消息 trace（候选/关系/决策）；
2. 用 12 个 FP 和 20 个 ambiguous 做 EvidenceGate 的固定对照集；
3. 对 73 FN 分层统计 B1/B2/B3；
4. 真实语料验收前不接入生产。
