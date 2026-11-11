# Digital Life Memory Engine 工程报告

**R0 候选评估与 NO-GO 结论** ｜ 版本 1.0 ｜ 2026-11-11

> 发布结论：**R0 / R1 NO-GO**。C01/C02/C03 已关闭；R0-D 同库正常重跑已通过，提交前/后终止与完整语义快照仍缺；R1 真实宿主 72h 未完成。

## 1. 执行摘要

本报告汇总 Memory Engine 从基线、一致性、数据质量、检索、上下文、规模可靠性到真实宿主接入的工程证据。所有数字均来自本工作区的测试、评测脚本与运行记录；合成数据与真实用户数据严格区分。

- 离线回归：539/539；Windows CI 在 Python 3.10 / 3.11 / 3.12 全绿；Linux 有 6 项可选 tiktoken 用例 skipped（533 通过）。
- R0 候选包：31 文件、55,833 bytes、SHA-256 91d52b7b…，整包与逐文件哈希均校验。
- 21 例真实模型重判：六次 19–20/21；来源污染 0；T02-S2 稳定错误。
- 340 合成 trace：词面 F1 约 0.66；BGE top20 F1 0.8740/0.8782；BGE top30 回落到 0.8533。
- 100k：读 p95 458.8ms；备份 100k；kill 续跑 100k；4×25k 多 scope 续跑 0 重复。
- 真实宿主：AstrBot 4.29.3 testbed 10 次 shadow，52 events / 13 scopes，0 失败。
- R0-D 同库二跑、提交前/后中断、10 场回放矩阵均通过；rejudge 8/8、并发 2/2。
- 独立 gold：holdout Top1 280/300、自然问句 140/140、摘要保真 100/100；paraphrase 20/40。
- 结论：R0/R1 NO-GO；不得接入 V1 生产或 Shadow。

| 维度 | 结论 | 证据强度 |
|---|---|---|
| 功能闭环 | ingest/retrieve/context/delete/更正/撤回/历史可用 | 强（单测+CLI） |
| 数据质量 | 合成数据可用；真实 gold 缺失 | 弱（合成） |
| 检索质量 | BGE 显著优于词面；延迟约 15× | 中（合成 340） |
| 规模可靠性 | 100k 读写/备份/恢复/续跑通过 | 中（简化图） |
| 宿主接入 | testbed shadow 10 次；无 72h | 中弱（mock） |
| 发布 | R0/R1 NO-GO | — |

## 2. 范围与边界

### 2.1 目标

验证并推进一个独立于 V1 的 Memory Engine：把私聊原文写成可追溯事件，按需检索并以未确认线索回注模型；所有决策 append-only、可回退、可审计。

### 2.2 明确不做

- 不修改 V1 生产插件、不改变 V1 的回复与注入路径。
- V1.5-B 作为独立 Sidecar 运行，不写 V1 数据库。
- 在 R0/R1 通过前，不把 Memory Engine 接入生产 Shadow。
- 不把合成 B4 或模型标注当作真实用户准确率。

### 2.3 证据等级

| 等级 | 含义 | 本项目例子 |
|---|---|---|
| A | 真实用户/真实宿主/可重复 | 无（尚未取得） |
| B | 真实模型+合成输入 | 21 例、340 BGE |
| C | 替身/模拟宿主 | M7 testbed shadow |
| D | 纯单测/静态 | B4 契约、缓存、存储 |

本报告的结论按最低证据等级表述；D 级通过不等于 B/A 级通过。

## 3. 系统架构

事件流水线：B1 候选检索 → B2 LLM 关系判断 → B3 约束 Resolver → B4 append-only decisions/evidence。模型只给提案；是否附着由 Resolver 与 ProvenanceGuard 决定。

| 层 | 职责 | 关键实现 |
|---|---|---|
| B1 | 候选事件召回 | LexicalCandidateIndex / HybridCandidateIndex（BGE，RRF） |
| B2 | 关系提案 | OpenAIProvider；超时/瞬时重试；严格 JSON |
| B3 | 约束解析 | ConstrainedResolver；cannot-link、同故事归一化 |
| B4 | 决策与证据 | decisions/evidence/receipts；scope lease/fencing |
| M3 | 引擎 API | ingest/retrieve/context/delete/revise/withdraw/history |
| M5 | 上下文 | ContextPacket、摘要、tiktoken、持久缓存、HostBudget |
| M6 | 规模/生命周期 | 备份、恢复、续跑、自动备份锁 |
| M7 | 宿主接入 | AstrBot shadow 插件、宿主预算、preflight |

可靠性基线：unit_of_work 全有或全无；scope lease + fencing token；source receipt；manifest；连续 frontier；source_ready 队列；PendingRejudger 处理暂定/重评。

## 4. 阶段完成情况

| 阶段 | 状态 | 主要证据 | 缺口 |
|---|---|---|---|
| M0 基线 | ✅ | 539 测试、CI、manifest、R0 包 | — |
| M1 一致性 | ◐ | kill/续跑、pending 重评、provider 重试 | 真实模型 340 稳定性 |
| M2 数据 | ◐ | 21 例六次、340 多组、污染 0 | 独立真实 gold |
| M3 引擎 | ◐ | API/CLI、更正/撤回/历史 | 自然更正/时间状态/自动后台 |
| M4 检索 | ◐ | 词面/BGE、阈值、延迟 | 真实查询集 |
| M5 上下文 | ◐ | Packet/摘要/tiktoken/缓存/预算 | 摘要保真/完整依赖契约 |
| M6 规模 | ◐ | 100k 读/写/备份/恢复/续跑 | 复杂混合负载/断电另立 |
| M7 接入 | ◐ | testbed shadow 10 次、回退 | 真实 QQ 72h |

## 5. 测试与 CI

统一命令：python -m unittest discover -q -s tests -t .；报告脚本：python scripts/report.py。

| 项 | 结果 |
|---|---|
| 离线用例 | 539（Windows）；Linux 533 通过 + 6 skipped |
| 失败/错误 | 0 / 0 |
| Windows CI | 3.10 / 3.11 / 3.12 全绿 |
| 耗时 | 约 43 s（本机） |
| R0 包验证 | 哈希/字节/文件数/解压 ingest/retrieve 全过 |

- 测试覆盖存储、租约/fencing、Resolver、ProvenanceGuard、rejudge、worker、缓存、备份、渲染、token 预算、CLI。
- CI 曾出现 Windows integration flake；通过 warm-up + 20ms settle + 确定性 pending backlog 修复，未削弱断言。
- R0 包进入回归套件；曾发现遗漏 projection/summary/token 模块，解压导入失败，补齐后新增逐文件哈希校验。

## 6. 数据质量

### 6.1 21 例真实模型重判

| run | event correct | story | 污染 | guard false reject |
|---|---|---|---|---|
| 1 | 20/21 | 17/17 | 0 | 0 |
| 2 | 19/21 | 17/17 | 0 | 0 |
| 3 | 20/21 | 17/17 | 0 | 0 |
| 4 | 20/21 | 17/17 | 0 | 0 |
| 5 | 20/21 | 17/17 | 0 | 0 |
| 6 | 20/21 | 17/17 | 0 | 0 |

六次分布 19–20/21，均值 19.8；T02-S2 六次都错（同故事不同事件被判 DIFFERENT），其余稳定。Resolver 已把同 story 的 DIFFERENT 归一化为 RELATED_CONTEXT，但原始模型分数仍按 20/21 记录，不抬高指标。

### 6.2 340 合成 trace：词面 vs BGE

| 配置 | attached | clusters | mixed | TP | FP | FN | F1 |
|---|---|---|---|---|---|---|---|
| 词面 k5 | 117 | 203 | 0 | 220 | 0 | 260 | 0.6286 |
| 词面 k10 run1 | 123 | 197 | 0 | 234 | 0 | 246 | 0.6555 |
| 词面 k10 run2 | 124 | 196 | 0 | 237 | 0 | 243 | 0.6611 |
| BGE k10 run1 | 182 | 138 | 1 | 358 | 12 | 122 | 0.8424 |
| BGE k10 run2 | 187 | 133 | 1 | 366 | 8 | 114 | 0.8571 |
| BGE k20 run1 | 194 | 126 | 1 | 385 | 16 | 95 | 0.8740 |
| BGE k20 run2 | 196 | 125 | 1 | 382 | 8 | 98 | 0.8782 |
| BGE k30 | 192 | 128 | 2 | 378 | 28 | 102 | 0.8533 |

BGE 把召回从约 0.49 提到 0.80，F1 从 0.66 提到 0.876；top30 因 FP/mixed 增加回落，在本次少量合成实验里 20 优于 10 和 30，但不能外推为通用最优 k。所有数字仍是合成 B4，不是真实用户准确率。

### 6.3 FN 归因

| 原因 | 对数 |
|---|---|
| B1 单边未召回 | 124 |
| B3/暂定事件碎片化 | 136 |
| 合计 | 260 |

340 的瓶颈约一半在 B1 候选召回，另一半在 B3/暂定分片；继续调分类阈值不会消掉这些 FN。

## 7. 检索与延迟

| 项 | 结果 |
|---|---|
| 词面 R@1 / R@5 / MRR | 0.7812 / 1.0 / 0.8891 |
| BGE R@1 / R@5 / MRR | 0.7937 / 1.0 / 0.8938 |
| 阈值 1.139（dev/holdout） | dev 69/69；holdout 91/91；注入 0 |
| 1000 anchors 词面 p95 | 2.80 ms |
| 1000 anchors BGE p95 | 41.45 ms（约 14.8×） |
| BGE build | 6,424 ms / 1000 anchors |

Engine 支持 MemoryEngine(store, encoder=...)；CLI 支持 --bge-model。真实 CLI 探针能命中，但每次进程都重新加载 BGE，不适合逐命令生产调用。

BGE 是明确的“质量换延迟”：生产需要常驻索引、持久化向量或分层检索，而不是无条件全量。

## 8. 上下文、缓存与预算

- ContextPacket 稳定包含 scope、query、items、budget、degraded；渲染器输出最终模型 block。
- 摘要为抽取式，保留否定与不确定；tiktoken 预算为精确计数，缺库时保守估算。
- PersistentContextCache 支持跨实例、四进程共享、epoch 失效、prune、TTL 与 CacheJanitor（状态+锁+stale 清理）。
- HostBudget：memory_tokens = max(0, total - system - history - output)。
- testbed 真实请求：host_memory_tokens 5,684；rendered 197；items 2；V1 注入不变。

## 9. 规模与可靠性

| 场景 | 结果 |
|---|---|
| 100k 查询 p95 | 458.756 ms |
| 100k 并发读 p95 | 0.766 ms |
| 100k 单写 | 598.3 s |
| 100k 多写 | 631.2 s |
| 100k 备份 | 100,000 events；integrity ok |
| 100k kill 续跑 | 693 → 100,000；0 重复 |
| 4×25k 多 scope 续跑 | 100,000 receipts/evidence；0 重复 |
| 100k 恢复查询 | 10 hits；151.87 ms；context 10 |
| 100k 恢复后继续写 | 新增 source 可检索；receipt 1；integrity ok |
| 并发写入时备份 | 20 个快照；计数 0→2,579 单调；0 错误 |

写锁缺陷：多进程 BEGIN IMMEDIATE/release_scope 出现 database is locked；通过 busy_timeout=5000 + 退避重试修复，100k 多 scope 续跑通过。时间均为本机测量，不是生产 SLA。

## 10. 宿主接入

真实宿主：AstrBot 4.29.3 testbed，mock provider。M7 shadow 插件只观察、不修改请求/回复。

| 项 | 结果 |
|---|---|
| 成功运行 | 10 次 |
| 失败 | 0 |
| shadow events / sources | 52 / 52 |
| scopes / observations | 13 / 39 |
| 每次 V1 memory prefix | true |
| 宿主预算 | 5,684 memory tokens；197 rendered |
| preflight | 8 项全 true |
| 回退 | 移除 shadow 后 V1 正常加载 |

部署陷阱：未设置 DIGITAL_LIFE_DEV 时，V1 仍正常，但 shadow 导入失败、DB 不增长，进程仍 exit 0。新增 preflight，未通过则 exit 1。

真实 QQ：V1 侧状态记录 NapCat 小号私聊 2 条；这不等于 Memory Engine M7 的 72h 证据。M7 的 10 次运行仍是 testbed；没有真实平台 72h 连续观察。

## 11. 缺陷与修复

| 编号/问题 | 修复 | 验证 |
|---|---|---|
| P01 自动迁移绕过 Guard | relocate_pending 重跑 ProvenanceGuard | 回归测试 |
| P02 UNCERTAIN 被标 resolved | 以 Resolver 结果为准 | 回归测试 |
| P03 pending 绕过检索 | SQL NOT EXISTS PENDING | 回归测试 |
| P04 缓存跨连接陈旧 | data_version + scope_epoch | 双连接测试 |
| P05 重复 revision | revision_receipt 幂等 | 重复更正测试 |
| P06 旧 generation 消费新任务 | expected_generation CAS | worker 测试 |
| B01 cannot-link 绕过 | cannot_link_exists 检查 | 回归测试 |
| B02 陈旧 generation 提交证据 | 事务内预检查 | 回归测试 |
| B03 同连接缓存失效 | scope_epoch | 回归测试 |
| B04 哈希冲突 | revision identity 校验 | 回归测试 |
| M6 多进程 database is locked | busy_timeout + BEGIN/release 重试 | 100k 续跑 |
| R0 包缺模块 | 补齐 projection/summary/token，新增包测试 | 解压导入 |
| shadow 环境变量缺失静默 | preflight 8 项检查 | testbed 实测 |
| provider 瞬时网络错误 | retries/backoff；HTTPError 不重试 | 单测 |
| C01 hybrid 索引外部失效 | scope epoch + 返回前 current-row 再核验 | 双连接测试 |
| C02 零预算/裁剪/无 tokenizer | 空块、重建 refs/used、strict 默认、CLI 0 | 六类边界测试 |
| C03 原判被归一化覆盖 | raw_label/raw_reasons 独立保存；explicit [] 原样保留 | 审计字段测试 |

## 12. 验收与结论

| 层级 | 结论 | 原因 |
|---|---|---|
| R0 独立离线引擎 | NO-GO | C01/C02/C03 已关闭；R0-D 同库/中断/矩阵/rejudge/gold 已复核；paraphrase 20/40、单作者合成未关闭 |
| R1 目标宿主 | NO-GO | 无真实平台 72h/用户效果 |
| Shadow/生产 | 禁止 | R0/R1 未通过 |

当前工程切片完整、可审计、可交接；但“能跑通”不等于“可发布”。R0 包是候选包，不是生产发布包。

## 13. 建议

- 先取得真实脱敏语料与独立人工 gold，重做 21/340，不以合成分数代替。
- 在真实 QQ/NapCat 上做 72h shadow，保留采样、错误与回退记录。
- 断电/磁盘损坏另立耐久性实验；R0 先完成真实重放、完整语义快照、独立数据分割和可移植包验证。
- 用真实查询集校准 M4 阈值；BGE 先做常驻索引/持久向量，避免逐命令冷加载。
- 在 R0/R1 通过前，不接 V1 生产，也不把 shadow 结果当作用户效果。

## 14. 附录

### 14.1 产物

| 产物 | 值 |
|---|---|
| R0 包 | releases/memory-engine-r0-rc1.zip |
| 大小/文件数 | 55,833 bytes / 31 |
| SHA-256 | 91d52b7b255520a504b04f23a2d4348faec30014ffa692a651bbdda9ca6340e2 |
| 代码提交 | 01c484d |
| 测试 | 539/539（Windows；Linux 533 通过 + 6 skipped） |

### 14.2 证据索引

| 文档 | 内容 |
|---|---|
| MEMORY_ENGINE_ACCEPTANCE_2026-11-10.md | 验收矩阵 |
| M2_DATA_PROGRESS_2026-11-10.md | 21/340/top-k/BGE/FN |
| M4_RETRIEVAL_QUALITY_2026-11-10.md | 检索质量/延迟/CLI |
| M5_HOST_BUDGET_2026-11-11.md | 宿主预算 |
| M6_SCALE_SMOKE_2026-11-10.md | 100k/备份/恢复/续跑 |
| M7_SHADOW_SOAK_2026-11-11.md | shadow 10 次/preflight |
| R0_RC_BUNDLE_2026-11-11.md | R0 包/hash |
| FINAL_HANDOFF_2026-11-11.md | 移交说明 |

### 14.3 复现命令

python -m unittest discover -q -s tests -t .

python scripts/report.py

python scripts/verify_r0_bundle.py

本报告由工作区报告、测试输出与运行记录汇总；未使用工作区外的真实用户数据。
