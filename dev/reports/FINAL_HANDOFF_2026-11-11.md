# Memory Engine 移交说明（2026-11-11）

- 代码：`dev/`
- R0 包：`_publish/digital-life/releases/memory-engine-r0-rc3.zip`（SHA-256 afcca11a…，38 文件）
- 全量测试：569/569
- 代码提交：`b5f9846`
- 最新文档：`pending`
- CI：Windows/Ubuntu 3.10 / 3.11 / 3.12 全绿

## 1. 已具备

| 模块 | 能力 |
|---|---|
| M0 | 版本、SHA、测试、CI 基线 |
| M1 | 原子领取、kill 恢复、pending 重评、provider 超时/重试 |
| M2 | 21 例六次 19–20/21；340 F1 0.66（词面）/0.85–0.88（BGE） |
| M3 | ingest/retrieve/context/delete/revise/withdraw/history |
| M4 | 词面/BGE、min_score、阈值、分 scope、BGE 延迟 |
| M5 | ContextPacket、摘要、tiktoken、持久缓存、TTL/janitor、HostBudget；C02 零预算/裁剪 |
| M6 | 100k 读/写/备份/恢复/续跑、并发备份、写锁修复 |
| M7 | 真实 AstrBot shadow 10 次、宿主预算、preflight、回退 |
| C01–C03 | hybrid 索引失效、渲染合同、原判审计（含 explicit []）已修复 |

## 2. 明确没有

- 独立真实脱敏语料与人工 gold；
- 真实 QQ/NapCat 72h；
- 断电级 fsync；
- 真实查询集与真实阈值校准；
- 生产接入批准。

## 3. 关键证据

- `reports/MEMORY_ENGINE_ACCEPTANCE_2026-11-10.md`
- `reports/M2_DATA_PROGRESS_2026-11-10.md`
- `reports/M4_RETRIEVAL_QUALITY_2026-11-10.md`
- `reports/M5_HOST_BUDGET_2026-11-11.md`
- `reports/M6_SCALE_SMOKE_2026-11-10.md`
- `reports/M7_SHADOW_SOAK_2026-11-11.md`
- `reports/R0_RC_BUNDLE_2026-11-11.md`

## 4. 结论

R0/R1：**NO-GO**。

不要在真实语料/72h/断电证据补齐前接入 V1 生产或 Shadow。
