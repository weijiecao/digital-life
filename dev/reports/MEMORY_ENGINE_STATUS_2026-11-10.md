# Memory Engine 当前状态与 Go/No-Go（2026-11-11）

- 状态：**NO-GO for R0/R1；M0 完成，M1–M7 有可复现切片，真实 gold/72h 未完成**
- 全量测试：**508/508**
- 真实聊天语料：0
- 真实宿主：AstrBot 4.29.3 本地 testbed
- 远端最后成功提交：`77cd1dc`
- CI：Windows 3.10 / 3.11 / 3.12 全绿

## 1. 节点状态

| 节点 | 状态 | 已有证据 | 主要缺口 |
|---|---|---|---|
| M0 基线 | ✅ | 508 测试、manifest、关键 SHA、远端 CI | 真实平台包验收 |
| M1 一致性 | ◐ | 双进程竞争/handoff、提交前后 kill、pending 重评、真实模型单条、READY 代际 CAS | 21/340 模型稳定性门槛 |
| M2 数据 | ◐ | 21 例三次 20/19/20；340 三次 F1 0.629–0.655、FP0、0 mixed；冻结 SHA；污染 0/311 | 独立真实 gold、真实留出 |
| M3 引擎 | ◐ | ingest/retrieve/context/delete、CLI、类型、更正/撤回、真实 shadow 采集 | 完整类型体系、生产宿主采集 |
| M4 检索 | ◐ | 词面/BGE、abstain、阈值 dev/holdout、分 scope、引擎 min_score | 真实查询集、约束过滤统一 |
| M5 上下文 | ◐ | 摘要、tiktoken、final renderer、持久缓存、多进程、prune | 宿主总预算、高并发击穿 |
| M6 规模 | ◐ | 100k 读/写、备份、kill 续跑、多 scope、自动备份锁 | 断电 fsync、多 scope 100k 中断 |
| M7 接入 | ◐ | 真实 AstrBot 启动/消息链、Memory Engine shadow、回退 | 72h、真实平台消息、版本回退 |

## 2. 关键风险

1. 真实脱敏语料仍为 0，不能报告真实误归属/漏归属。
2. unknown provenance 使 strict Guard 召回低；放宽会增加 FP。
3. 21 例三次 19–20/21，仍有模型波动。
4. M4 阈值来自合成 B4，不是真实标定。
5. M6 断电级故障与多 scope 100k 中断未做。
6. M7 只有 testbed shadow，没有 72h/真实 QQ 平台。

## 3. 发布结论

- R0 独立离线文本引擎：**未达成**；
- R1 目标宿主：**未达成**；
- Shadow/生产：**禁止**；
- V1 保持正常，V1.5-B 保持离线。

## 4. 下一阶段优先项

1. 接入真实脱敏语料与独立 gold；
2. 冻结 renderer/宿主总预算；
3. 断电级 fsync 与 100k 多 scope 中断恢复；
4. 真实 AstrBot/NapCat 72h shadow 与回退演练；
5. 再讨论 R0/R1。
