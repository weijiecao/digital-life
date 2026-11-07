# 记忆验证报告（2026-11-05，v1.0.16）

> **后续改动（11-06 11:32）**：大模型筛选触发逻辑已修复——背景包不再被当作本地命中；
> 本地命中不调用模型；加载中保留背景、不转远程；候选池扩展到已审核事件来源，
> 并按“时间线 + 事件线”取候选（旧事件来源不会因为不在最近 100 条里而丢失）；
> 另加最近 10 条用户消息的近因缓冲，供指代锚点和 judge 上下文使用。
> 删除闭环（V2）与媒体计数（V10）已修复；**v1.0.18 起 `/dlm` 不再注册为聊天命令，
> 用户聊天面静默；不新建正式事件；管理走 WebUI panel。** 当前 **359/359**。
> 本报告 §1–§10 仍是 352 时点的 V1–V12 证据，未把这次改动混入原结论。
>
> - 验证方案：[MEMORY_VERIFICATION_PLAN.md](MEMORY_VERIFICATION_PLAN.md)
- 代码状态：**v1.0.19（静默版 + 数据安全层）**；当前 `STATUS.md` 为 11-07 10:52 **363/363**；本报告执行时点为 11-05 16:13/352
- 执行时间：2026-11-05 15:20（重跑）；环境：Windows 11 / Python 3.12.10 / 替身 AstrBot + 受控编码器
- 范围：执行时 **7 项通过、2 项部分通过、2 项未通过、1 项阻塞**
- 修复更新（16:13）：**F1/F2 已修复**，V12 转为通过；全量回归 **352/352**，详见 §10
- 后续（11-06）：删除闭环、媒体计数、judge 触发/候选池/近因缓冲已改生产代码；当前 359/359
- 原则：本轮主体验证未改生产代码；§10 的 P0 与 11-06 的后续修复除外

> 一句话：**记忆的“可溯源、作用域隔离、抗注入、外发白名单、跨版本数据保全”都过了；
> 备份轮转 P0、删除/遗忘、媒体跳过都已修复；乱序到达仍会把旧原话标成“最近”。**

---

## 0. 结论先行

| 判断 | 内容 |
|---|---|
| **可以确认** | 注入可逐字溯源；A/B 作用域不串；20 条对抗记忆不越权；本地模式 0 外网请求；双进程写入不丢不重；1.0.3 数据可被 1.0.16 读写 |
| **必须修** | ~~备份“校验→轮转”会删掉全部真实备份（P0）~~ **已修复**；~~删除/遗忘无入口~~ **已修复**（V2 pass） |
| **应修** | 乱序到达时“最近原话”按 seq 而非 `recorded_at` 标注（P1）；~~媒体/混合消息静默跳过~~ **已修复**（V10 pass） |
| **仍阻塞** | 真实语料盲评（V4）：没有 300–500 条带标注的真实对话，无法证明真实 R@1/误注入/纠错 |
| **不能外推** | 本轮编码器是受控替身；所有“相关性”数字只证明链路与判据，不代表真实 BGE 准确率 |

**建议动作顺序**：~~先修备份轮转~~（已修）→ ~~删除闭环~~（已修）→ ~~媒体计数~~（已修）
→ 真实语料（外部 5–10 天）→ 乱序时间策略。

---

## 1. 执行矩阵

| 项 | 结果 | 关键数字 | 证据 |
|---|---|---|---|
| V1 溯源审计 | **通过** | 1,000 packet / 5,000 条“原话” / 6,000 个序号，0 违反；真实库 27 条、3 scope、0 error | `reports/verification_v1_traceability.json`、`verification_real_db.json` |
| V2 删除/遗忘 | **通过（修复后）** | `/dlm 删除` / `/dlm 清空记忆 确认`；V2 `checks` 全 true：删除 1 条、0 孤儿、删除前已无明文 | `reports/verification_v2_delete_absence.json` |
| V3 对抗记忆 | **通过** | 20/20；固定头/未确认/转义/逐字保留/0 状态变更 | `reports/verification_v3_adversarial.json` |
| V4 真实语料 | **阻塞** | 无 300–500 条带标注真实语料；现有 27 条真实采集无 ground truth | — |
| V5 跨作用域 | **通过** | 50 scope × 10 条 × 20 请求 = 1,000 packet；跨 scope 命中 **0**；本 scope canary 命中 1,000 | `reports/verification_v5_scope_diff.json` |
| V6 时间/边界 | **部分通过（9/10）** | 399/400 注入、401 不注入；179/180 成组、181 不成组；未来/当前排除；**乱序“最近”标错** | `reports/verification_v6_boundaries.json` |
| V7 外发白名单 | **通过** | 100 轮本地 0 外部连接/0 DNS；窄路 1 次调用，只含问题/候选，无 scope 哈希/路径/全库 | `reports/verification_v7_egress.json` |
| V8 跨版本数据 | **通过** | 1.0.3 建 5 条 → 1.0.16 读 5 写 1 → 旧版重开并追加 1 条后 7 条 → 1.0.16 最终 7 条；完整性 ok | `reports/verification_v8_upgrade.json` |
| V9 双进程/崩溃 | **通过** | 2 进程各 200 条 → 400 行 / 400 hash / 400 job；完整性 ok、0 重复；强杀恢复 rc=0 | `reports/verification_v9_concurrency.json` |
| V10 缺口可见性 | **通过（修复后）** | 正常 1 条、超长记 1 个 gap；媒体/混合 2 条计入 `capture_unsupported`，0 gap | `reports/verification_v10_gaps.json` |
| V11 长度/预算 | **通过（并入 V6）** | 149→450；150→150；2000→2000；2001→450；399/400/401 行为符合定义 | `reports/verification_v6_boundaries.json` |
| V12 写时备份 | **通过（修复后）** | 10 次备份均校验通过；最终备份恢复 300 条；校验 **0 旁文件**；轮转保留 2 个真实备份 | `reports/verification_v12_backup.json` |

> V1/V5/V7/V9 的数字来自替身编码器与假平台，只能证明“判据成立、链路不串”，不能当真实准确率。

---

## 2. 缺陷清单

### F1（P0，已修复）备份“校验 → 轮转”会删掉全部真实备份

**现象**：3 个备份全部校验后执行 `rotate(keep=2)`，结果是 **0 个 `.sqlite3`**，只剩一对 `-wal/-shm`；
`remaining=0`。

**复现**：

```powershell
cd dev
..\testbed\.venv\Scripts\python.exe verification\v12_backup.py
```

**证据**（`reports/verification_v12_backup.json`）：

```json
"rotation": {
  "real_backups": [],
  "sidecar": ["...sqlite3-shm", "...sqlite3-wal"],
  "rotate": {"kept": 2, "removed": ["...sqlite3", "...sqlite3-shm", "...sqlite3-wal", ...],
             "remaining": 0}
}
```

**根因**（两处叠加）：

1. `store/backup.py:27-33` 的 `verify_stored_backup()` 调 `verify_backup()` 后不清理旁文件；
   `recovery.verify_backup()` 以 `mode=ro` 打开 WAL 库，会生成 `-wal/-shm`（实测每个备份 2 个）。
2. `panel.py:134-147` 的 `rotate_backups()` 用 `folder.glob('*')` 统计**所有文件**，旁文件也占 `keep` 名额，
   于是真实 `.sqlite3` 被当成“旧文件”删掉。

**影响**：用户按面板提示“校验备份”后再“轮转”，可能把可用备份全部删掉；只剩旁文件，
`list_backups()` 返回 0。这是**备份恢复链的 P0 缺陷**，不是显示问题。

**建议**：见 §4 P0-1；修复前不要在面板上连续执行“校验 + 轮转”。
**修复后**：3 个备份全部校验 → `rotate(keep=2)` 保留 2 个真实 `.sqlite3`、0 旁文件（§10）。

### F2（P1，已修复）备份校验不是幂等/无副作用的

**现象**：`backup()` 本身会清旁文件，但每调用一次 `verify_stored_backup(name)` 就重新生成
`name-wal` / `name-shm`；10 次备份 + 最终备份各校验一次后，目录里共 22 个旁文件
（`reports/verification_v12_backup.json`）。
**根因**：同 F1 第 1 点。
**影响**：污染备份目录、干扰人工清理；与 F1 叠加会造成丢备份。
**建议**：校验用 `?mode=ro&immutable=1`，或在 `finally` 中删除本次产生的旁文件；校验函数返回前保证目录无旁文件。
**修复后**：`immutable=1` + `verify_stored_backup` 的 `finally` 清理；V12 `no_sidecar=true`（§10）。

### F3（P1）删除/遗忘无实现；手删会留孤儿与原话残片（**11-06 已修复**）

**现象**：

- 生产代码无 `DELETE FROM dl_capture`，无 `/dlm 删除`、无面板删除入口；
- 直接 `DELETE FROM dl_capture` 后，`dl_auto_text_index` 被触发器清掉 1 条，但
  `dl_auto_jobs`、`dl_auto_localization`、`dl_pending_dirty` 各留 1 个孤儿；
- 删除标记原话在 VACUUM 前仍能在主库文件中按明文找到；VACUUM 后才消失。

**证据**：`reports/verification_v2_delete_absence.json`；审计器报
`orphan_jobs=1`、`orphan_derived: dl_auto_jobs/dl_auto_localization/dl_pending_dirty`。

**影响**：用户无法撤回私密原话；若按“直接 SQL 删除”实现，检索/任务/审计会看到指向不存在原话的残留；
备份/归档仍保留旧内容。

**建议**：见 §4 P0-2 与 §5-B；不要在现有触发器上“补一个 DELETE”就上线。

### F4（P1）乱序到达时，“最近原话”按 seq 而不是时间标注

**现象**：甲先到但记录时间更晚（seq 小、time 新），乙后到但记录时间更早（seq 大、time 旧）；
背景包 `最近原话` 的最后一项是**乙（旧）**，实际最新是**甲**。

**证据**（`reports/verification_v6_boundaries.json`）：

```json
"out_of_order": {
  "labelled_latest": "2026-11-05T06:20:01+00:00",
  "actual_latest":   "2026-11-05T07:10:01+00:00",
  "pass": false
}
```

**根因**：`store/context_packet.py:203-235` 的 `_background_context()` 用 `ORDER BY seq` 取
`candidates[:2]` 与 `candidates[-1]`，把 seq 最大者当“最近”；`_possible_update_peer()`（同文件 29-43）
也按“下一 seq”找相邻更新，而不是按时间最近。

**影响**：历史导入、延迟到达、跨设备补录时，用户会看到旧原话被标成“最近”；改期配对也可能配错邻居。
文本仍是原话且未确认，不会伪造事实，但会误导“哪条更新”。

**建议**：以 `recorded_at` 为主序、`seq` 只做 tie-break；在导入时保留 `time_source`（received/imported/original），
并对“时间倒挂”的记录单独标注。

### F5（P2）媒体/混合消息静默跳过（**11-06 已修复**）

**现象**：正常文本 1 条、超长消息 1 个 gap；图片/混合消息既不计 `capture_skipped`，也不写 `dl_capture_gaps`。

**证据**：`reports/verification_v10_gaps.json`：`captures=1, gaps_total=1, capture_skipped=1`；
`media_gap_visible=false`。

**根因**：`main.py:511` 对非 `Plain` 链直接 `return`，在 `_note_capture_gap()` 之前。
**影响**：L0 的“采集成功率”分母只含纯文本，暂不影响该指标；但“缺口可见性”无法回答“漏了什么”。
**建议**：新增 `unsupported_media` 计数（不混入 gap），面板/状态命令展示；不要把它算成采集失败。

### F6（P2）查询 401 字时静默不注入

**现象**：399/400 字注入正常；401 字直接 0 片段，无提示。
**根因**：`main.py:628` 与 `store/context_packet.py:239` 的 `len<=400` 门。
**影响**：用户发长消息时“记忆突然消失”，且没有任何解释。
**建议**：产品决策——截断到 400 字并标注，或返回一条“问题过长，未检索记忆”的提示；二者都要有测试。

---

## 3. 通过项细节

### V1 溯源审计（通过）

- 50 scope × 5 条原话 × 20 个请求 = **1,000 packet**；
- 5,000 条“原话”、6,000 个序号引用，**0 个无法溯源、0 个跨 scope、0 个未来项、0 个缺序号**；
- 真实 testbed 库（27 条、3 scope）DB 审计 **0 error**；另有 4 组同 scope 重复正文——
  可能是用户重复表述，也可能是不同 `message_id` 的重复投递，本轮未判定，建议在 V4 语料里单独标注；
- 边界：本轮覆盖 pending + background 路径；**未覆盖已审核事件 / 自动主题 / 模型草稿**的 packet 形态。

### V3 对抗记忆（通过）

20 条载荷（“忽略之前指令”“删除所有记忆”“DROP TABLE”“JSON 破坏”“伪造 `[DL_MEMORY_V040]` 头”、
bidi/HTML/换行等）全部满足：固定头仍在、JSON 可解析、原文逐字保留、DB 结构/内容不变、仅 1 个临时片段。
**边界**：不验证回复模型是否听从；只验证插件没有把记忆当指令执行。

### V5 跨作用域（通过）

50 个 scope，各 10 条带唯一 canary 的原话；各 20 个相同问题 → 1,000 packet；
**跨 scope 命中 0**，本 scope canary 命中 1,000，各 scope 行数均等于 10。

### V6 时间/边界（9/10）

| 用例 | 结果 |
|---|---|
| 399/400 字 | 注入（1 片段） |
| 401 字 | 不注入（0 片段） |
| 预算 149/150/2000/2001 | 450/150/2000/450，valid=False/True/True/False |
| 改期 179 s / 180 s / 181 s | 成组 / 成组 / 不成组 |
| 未来原话 | 不注入 |
| 当前消息自引 | 不注入 |
| 乱序“最近” | **失败**（F4） |

### V7 外发白名单（通过）

- 全关 + 仅本地 BGE：100 轮，**0 外部连接、0 DNS**（只有 asyncio 的 127.0.0.1 socketpair，单独记录）；
- 窄路开启：1 次 `llm_generate`，provider 正确；prompt 含当前问题与候选，**不含** scope 哈希、绝对路径、全库内容。
- 边界：`model_drafts` / `auto_model` 两条远程路径未单独触发；预设默认关闭，但不能替代出口白名单测试。

### V8 跨版本数据（通过）

旧版 1.0.3 建 5 条 → 1.0.16 读 5 条、写 1 条 → 旧版 1.0.3 重新打开、追加 1 条后为 7 条 → 1.0.16 最终 7 条；
`PRAGMA integrity_check=ok`，审计 0 error。
**边界**：只验证 1.0.3↔1.0.16 的基础表/数据；不验证旧版命令/UI，也不验证所有中间版本。

### V9 双进程/崩溃（通过）

两个独立进程各写 200 条：400 行、400 个不同 `event_hash`、400 个 job，0 重复、完整性 ok、两进程 rc=0；
既有强杀演练 rc=0。
**边界**：验证的是“数据不丢不重”；两个实例同时跑自动整理/模型调用时的**语义重复**未测（见 §6 风险）。

### V12 写时备份（修复后通过）

10 次写时备份（4→71 条）全部 `verify` 通过、行数单调；最终 300 条，恢复副本 300 条。
修复前校验留下 22 个旁文件、轮转删光真实备份（F1/F2）；修复后 **0 旁文件、轮转保留 2 个真实备份**，
`reports/verification_v12_backup.json` 全部 `checks=true`。

---

## 4. 工程建议

### P0（发布/自用前必须）

**P0-1 修复备份校验与轮转（已修复，见 §10）**

- `panel.rotate_backups()` 只处理 `memory_*.sqlite3`；旁文件随对应备份一起删除；
- `Store.verify_stored_backup()` 在 `finally` 清理本次产生的 `-wal/-shm`（或校验时用 `immutable=1`）；
- 实际新增回归（2 项）：
  1. 校验后备份目录只剩 1 个 `.sqlite3`、0 旁文件；
  2. 3 个备份全部 `verify_stored_backup()` → `rotate(keep=2)` → 仍有 **2 个真实 `.sqlite3`**、0 旁文件。
- V12 复验：10 次写时备份、0 旁文件、轮转保留 2 个真实备份、最终 300 条可恢复。
- 验收：`verification_v12_backup.json` 的 `no_sidecar` 与 `rotation_keeps_real_backups` 均 true。

**P0-2 删除/遗忘闭环（架构变更，见 §5-B）——11-06 已实现**

- 不建议在现有触发器上直接加 SQL DELETE；实测会留 `dl_auto_jobs`/`dl_auto_localization`/`dl_pending_dirty` 孤儿；
- 必须先定义“逻辑删除 / 物理擦除 / 备份保留”三种语义，再做入口与清理；
- 最小验收：删除后检索/注入/导出/统计均不可见；派生表 0 孤儿；DB/WAL 明文扫描 0 命中；并发删除不复活。

**P0-3 真实语料（外部依赖）**

- 300–500 条脱敏真实对话，分层至少覆盖：无内容追问 / 有内容改写 / 改期取消 / 相似干扰 / 跨日 / 负例；
- 双人盲评 κ≥0.6；按 `§8.3` 的样本量判 R@1、误注入、纠错、保真；
- 这是 V4 的唯一解锁条件，不能用自造 120 条替代。

### P1（Beta 前）

**P1-1 时间主序**：`_background_context`/`_possible_update_peer` 以 `recorded_at` 为主序，`seq` 只做 tie-break；
新增“乱序导入”回归，断言 `最近原话` 最后一项 = 最大 `recorded_at`。

**P1-2 溯源产品化**：packet 中每个“原话”增加 `来源`（seq/sha/recorded_at），并写入 `packet_id`；
`/dlm 记忆体检` 调用 `memory_audit.py`，每次报告/发布都跑一遍。这样 V1 才能从“事后审计”变成“在线自检”。

**P1-3 外发网关**：把所有 `context.llm_generate` 收敛到一个 `ModelGateway`，按用途白名单
（judge/draft/auto_model）、按次记录 provider/用途/字节数/scope 哈希；关闭时 0 调用。
理由：当前 V7 只覆盖 judge；drafts/auto_model 仍可能绕开同一套出口约束。

**P1-4 单实例租约**：同一 data dir 被两个 AstrBot 实例打开时，数据不丢（V9 已证），
但两边都会跑自动整理/模型调用，可能重复计费与重复注入。建议加 `.instance.lock`（PID+心跳），
第二实例拒绝启动或只读；若不做，至少在文档里写“禁止双开”。

**P1-5 媒体/混合消息计数——11-06 已实现**：新增 `capture_unsupported` 计数器（不算 gap），
`/dlm 采集状态` 展示该计数。

### P2（可排期）

- **P2-1 长查询提示**：401 字时给“问题过长，未检索记忆”或截断到 400；二者都要测试。
- **P2-2 审计接入**：`scripts/report.py` 末尾调用 `memory_audit.py`，把 error 数写进 `STATUS.md`。
- **P2-3 备份清单**：`backups/index.json` 记录 name/sha256/rows/created_at，人工清理与迁移包共用。

---

## 5. 架构修改方案

> 只列“由本轮证据触发”的变更；不做无证据的重构。

### A. BackupManager：把“备份、校验、轮转、恢复”收成一个有清单的子系统（必要）

**触发**：F1/F2；当前 `backup()`、`verify_stored_backup()`、`panel.rotate_backups()` 各自处理文件，
旁文件语义没有唯一 owner。

**目标结构**：

```
store/backup.py        create / verify / list
backups/index.json     [{name, sha256, rows, schema_version, created_at}]
panel.rotate_backups   只消费 index 条目；删除时连带 sidecar
```

- 校验使用 `immutable=1` 或 `finally` 清理；
- 轮转只按 `index.json` 排序，不 glob 目录；
- 迁移：首次启动扫描现有 `memory_*.sqlite3` 建索引；不改库、不删文件；
- 验收：§4 P0-1 的三条回归。

### B. LifecycleManager：删除/遗忘的一等公民（必要）

**触发**：F3；现有 `AFTER DELETE` 触发器只覆盖 text/topic/vector/chunks 四类，
jobs/localization/pending 仍留孤儿；且没有入口与物理擦除语义。

**目标结构**：

```
dl_deletion_log(scope, seq, mode, requested_at, purged_at, reason)
dl_capture.state: pending | linked | deleted
LifecycleMixin.delete(scope, seq|all, mode='logical'|'purge')
```

- 一个事务内：置 tombstone → 按**显式 cascade 注册表**清派生（jobs/localization/groups/chains/
  pending index/terms/dirty/vectors/topic members/suggestions）→ 提交；
- 物理清理：`PRAGMA secure_delete=ON` → checkpoint → `VACUUM`；备份保留策略单独定义（建议 7 天轮转）；
- 命令：`/dlm 删除 <序号>`、`/dlm 删除会话`（二次确认 + 确认词）；面板同接口；
- 验收：V2 全项 + 并发删除/检索不复活 + 备份保留有文档。

### C. Provenance/Audit Layer：让“每个注入项可追溯”成为结构保证（必要）

**触发**：V1 目前只能做事后文本匹配；packet 没有统一来源 ID，摘要/标题/原话混在一起。

**目标结构**：

```json
{"packet_id":"...", "来源":[{"seq":1,"sha":"...","recorded_at":"..."}], "最近原话":[...]}
```

- `latency.jsonl` 增加 `packet_id` 与 `trace_ok`；
- `/dlm 记忆体检` 调用 `memory_audit.py`；`report.py` 汇总 error 数；
- 兼容：新增键，不改旧键；旧客户端忽略；
- 验收：V1 的 0 违反在 **event/auto-topic/summary** 路径同样成立。

### D. ModelGateway：把“外发”收成一个可审计入口（建议）

**触发**：V7 只覆盖 judge；`model_drafts`/`auto_model` 仍直接调用 provider。

**目标**：`ModelGateway.call(purpose, provider, payload)`，用途白名单 + 每次审计
（provider、purpose、payload 字节数、scope 哈希、是否含原话）；关闭时抛 `EgressDisabled`。
验收：V7 扩展到三条路径，全关时 0 调用。

### E. TimePolicy：时间语义显式化（建议）

**触发**：F4；`recorded_at` 是接收时间，导入保留 `original_timestamp`，但排序仍用 seq。

**目标**：统一的 `time_key(row)`（`recorded_at` 主序、seq tie-break）；
新增 `time_source`（received/imported/original）；对“时间倒挂”的导入行在状态命令中标注。
验收：V6 乱序用例 + 导入乱序回归。

---

## 6. 未覆盖与风险

| 项 | 状态 | 说明 |
|---|---|---|
| 真实语料 R@1/误注入/纠错 | **未测** | V4 阻塞；现有 27 条真实采集无标注 |
| 真实回复模型 | 未测 | provider 仍是 mock；V3/V7 不能证明模型侧安全/质量 |
| 已审核事件/自动主题 packet | 未覆盖 | V1 只覆盖 pending/background；需补 event/auto-topic 路径 |
| 3 天 soak / P95 | 未测 | 单轮/短时；V6/V9 不产生真实 P95 |
| 双实例语义重复 | 未测 | V9 只证数据不丢；两实例的自动整理/模型调用可能重复 |
| 群聊/多模态 | 不在范围 | 设计 Non-goals；媒体仅作为“跳过可见性”验证 |
| 真实 BGE 阈值 | 未复测 | 本轮编码器是 2 维替身；65%/70% 结论仍引用既有 `BGE_CALIBRATION` |

---

## 7. 对验证方案的修订

1. **V1** 增加 event/auto-topic/summary 三类 packet；否则“溯源通过”只覆盖自动路径的一半。
2. **V12** 拆成“写时一致性”与“校验→轮转”两段；后者是本轮 P0 的来源，必须独立回归。
3. **V2** 从“验证能力”改为“实现后验证”；当前结论是能力缺失，不是测试失败。
4. **V6** 正式加入“乱序到达”用例；本轮已证明它不是理论风险。
5. **V7** 扩展到 `model_drafts`/`auto_model`；否则“外发白名单”只覆盖一条路。
6. **V4** 保持阻塞；在真实语料到位前，不把自造集分数写进 L1 结论。

---

## 8. 复跑

```powershell
cd dev
$py = '..\testbed\.venv\Scripts\python.exe'

& $py verification\v1_traceability.py --scopes 50 --messages 5 --queries 20
& $py verification\v2_delete_absence.py
& $py verification\v3_adversarial.py
& $py verification\v5_scope_diff.py
& $py verification\v6_boundaries.py
& $py verification\v7_egress.py
& $py verification\v8_upgrade.py
& $py verification\v9_concurrency.py
& $py verification\v10_gaps.py
& $py verification\v12_backup.py

# 真实库只读审计
& $py scripts\memory_audit.py --db "..\testbed\instance\data\plugin_data\astrbot_plugin_digital_memory_store\manual_test_memory.sqlite3" --json

# 全量回归（352 项）
& $py -m unittest discover -q -s tests -t .
```

预期退出码：V6 为 1（乱序缺陷）；V2/V10/V12 已转 0；其余为 0；报告在 `reports/verification_*.json`。

**本轮新增文件**（未改生产代码）：

- `dev/scripts/memory_audit.py`（只读审计器，V1）
- `dev/verification/common.py`
- `dev/verification/v1_traceability.py`、`v2_delete_absence.py`、`v3_adversarial.py`、
  `v5_scope_diff.py`、`v6_boundaries.py`、`v7_egress.py`、`v8_upgrade.py`、
  `v9_concurrency.py`、`v10_gaps.py`、`v12_backup.py`
- `dev/reports/verification_*.json`（证据）

`STATUS.md` 已用当前 1.0.16 重新生成（352/352），未手改。

---

## 9. 判定

**当前状态：备份 P0 已修复；删除/遗忘与真实语料仍是主要缺口。**

- 个人自用：备份轮转已可安全使用；删除/遗忘需按 §5-B 补，不能直接 SQL 删除；
- 进入 L1 Beta：F3 必须关闭，V4 必须完成，F4/F5 至少明确豁免；
- 任何“记忆验证通过”的对外表述，必须同时说明：真实语料未测、模型侧未测、本轮编码器是替身。

---

## 10. P0 修复记录（2026-11-05 16:13）

**修复目标**：F1（轮转删光真实备份）与 F2（校验产生旁文件）。

| 文件 | 改动 |
|---|---|
| `store/recovery.py` | `_open_readonly` 改用 `?mode=ro&immutable=1`；新增 `cleanup_sidecars()`；`restore_to_new_directory` 与 CLI `verify` 在 `finally` 清理源备份旁文件 |
| `store/backup.py` | `verify_stored_backup()` 在 `finally` 清理旁文件；`backup()` 复用清理函数 |
| `panel.py` | `rotate_backups()` 只把 `memory_*.sqlite3` 算进 `keep`；删除/保留项都清旁文件；`remaining` 只数真实备份 |
| `tests/test_backup_regression.py` | 新增 2 项：校验后 0 旁文件；3 次“备份+校验”后 `rotate(keep=2)` 保留 2 个真实备份 |

**修复后验证**：

- `python verification\v12_backup.py`：`no_sidecar=true`、`rotation_keeps_real_backups=true`，
  10 次备份、最终 300 条、恢复 300 条，**全部 checks 通过**；
- `python -m unittest discover -q -s tests -t .`：**352/352 通过**（31.5 s）；
- `STATUS.md` 已重新生成：v1.0.16 / 352 项 / 0 失败。

**边界**：本轮只修备份路径，未 bump 版本、未重新打 `dist` 包；发版前建议 bump 到 1.0.17
并跑 `scripts/build_package.py`。删除/遗忘（F3）、乱序时间（F4）、媒体计数（F5）不在本次修复范围。
