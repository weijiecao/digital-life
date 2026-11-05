# 变更记录

## v1.0.16（2026-11-05）

迁移包：把“换机器”从手工对文件变成一条命令打包 + 一条命令校验。

### 新增

- `migration.py`：先 `PRAGMA wal_checkpoint(TRUNCATE)` 再拷贝库，保证快照自洽；zip 内含
  `memory.sqlite3`、`plugin_config.json`（若有）、`scope_overrides.json`（若有）、
  `manifest.json`（逐文件 sha256 + 原话/作用域/事件计数 + 插件版本）、`RESTORE.md`（恢复步骤）；
- `/dlm 迁移包`：生成到 `exports/`，回执给出大小与计数；
- `/dlm 迁移检查 <文件>.zip`：从 `exports/` 或 `imports/` 读取，逐文件比对 sha256、
  对库跑 `PRAGMA integrity_check`、核对计数；未通过时列出原因（最多 8 条）。

### 安全与边界

- 只接受本目录下的 `.zip` 文件名，拒绝路径；
- 包内每个非 manifest 文件都必须登记，未登记文件会被报告；
- 校验失败时明确提示“不要用这个包恢复”；
- **不做在线恢复**：恢复必须停服后手工替换，`RESTORE.md` 里写了步骤与 `model_dir` 注意事项。

### 测试

新增 `test_plugin.MigrationPack` 3 项：打包结构/manifest 计数/自检通过；路径与缺失拒绝；
篡改一个文件后校验必须报“校验和不匹配”。全量 **350/350**。

## v1.0.15（2026-11-05）

补上迁移的最后一环：**换环境后 origin 变了，怎么让旧库继续可见**。

### 新增

- `scope_alias.py`：发送者 → 旧 origin 的绑定表（`<数据目录>/scope_overrides.json`），原子写入；
- `/dlm 会话认领 <旧 origin>`：校验三段式私聊 origin → 确认该 origin 下确有数据 → 写入绑定并立即生效；
- `/dlm 取消认领`：解除绑定回到本机 origin；
- `/dlm 会话认领`（无参数）与 `/dlm 会话信息` 都显示当前认领状态；
- 入口 `_identity` 在算作用域前应用绑定：**不重写 61 张表**，旧库直接可用。

### 安全约束

- 只接受 `platform:FriendMessage:session` 形式，群聊 origin 一律拒绝；
- 目标 origin 下没有数据时拒绝写入（防止抄错把新消息写进不存在的会话）；
- 绑定只影响本机、只影响该发送者；`/dlm 取消认领` 或删除 json 即恢复；
- 认领后新消息会继续写入旧作用域（保持连续），这是有意为之，文档已说明。

### 测试

新增 `test_plugin.ScopeClaim` 2 项：格式/群聊/无数据三类拒绝；认领后计数可见、重启后仍生效、
取消后回到本机 origin。全量 **347/347**。

## v1.0.14（2026-11-05）

补上导出与“迁移前必备信息”，让记忆能带走。

### 新增

- `/dlm 导出历史 [天数]`：把**当前会话**原话导出到 `exports/`，一次生成三个文件——
  JSONL（四字段，与 `/dlm 导入历史` 完全兼容）、Markdown（人读全文）、meta（条数、时间范围、
  两个文件的 sha256、插件版本）；
- `/dlm 会话信息`：输出 `origin`、作用域指纹与各类计数。插件用「发送者 + origin」算作用域，
  换环境后 origin 变化会导致旧库“查不到”，迁移前必须记录这一行。

### 边界（写进文档，避免误解）

- 只导出**原话**，不含已确认的事件、关系、摘要、别名、优先级（这些可在新机器重建）；
- 超过 400 字的消息**只写进 Markdown**（导入端拒绝超长单条），回执里报告条数；
- 消息 ID 为合成值：同一导出文件重复导入会被去重，但不等同于平台原始 ID。

### 测试

新增 `test_plugin.HistoryExportCommand` 4 项：正常导出（JSONL 可被 `parse_jsonl` 接受、Markdown
含全文、meta 计数正确）、空会话不生成文件、超长消息只进 Markdown、会话信息含 origin 且发送者打码。
全量 **345/345**。

## v1.0.13（2026-11-05）

补上悬空的历史导入入口：`history_import` 模块一直有实现和测试，但没有 `/dlm` 命令。

### 新增

- `/dlm 导入历史`：显示 `imports/` 目录位置与文件格式；
- `/dlm 导入历史 <文件名>.jsonl`：只接受 `imports/` 目录下的 `.jsonl` 文件名（拒绝路径穿越），
  严格校验后**单事务**写 `dl_capture` + `dl_import_provenance`，只导入为**待审原话**；
- 帮助文本新增“迁移”一行；README 命令表、`docs/QUICKSTART.md`（备份与迁移）、
  `docs/CODE_ARCHITECTURE.md`（文件职责）同步说明。

### 安全约束（沿用模块原有校验）

- 同一会话里已有实时采集时拒绝导入，避免混源；重复 `message_id` 跳过；
- 导入顺序必须晚于已有导入记录；单文件 ≤10 MB / ≤1 万条，`content` ≤400 字，
  `recorded_at` 必须带时区且不能是未来。

### 测试

- 新增 `test_plugin.HistoryImportCommand` 两项：目录/格式提示与路径穿越拒绝；
  导入后出现在 `/dlm 待审`、`/dlm 导入来源 <序号>` 能查到来源标签。

## v1.0.12（2026-11-05）

只加文档：`docs/CODE_ARCHITECTURE.md`（代码架构梳理），并逐项对照代码核对过。

### 新增

- 架构数据流图、消息写入链、模型请求与记忆读取链（mermaid，GitHub 可直接渲染）；
- 数据表按“信任等级”分组（原始采集 / 已审核 / 自动候选 / 索引 / 状态 / 草稿）；
- 文件职责表与重构后的代码规模；
- 静态审查结论：6 条需要重点验证的边界 + 1 条已消项（`storage.py` 拆分已完成）。

### 核对结果（对照代码，非照抄）

- 表名逐条核对 `store/`、`lexical_index.py`、`pending_index.py`、`engine/e419/dynamic_index.py` 的
  `CREATE TABLE`；
- 参数核对：`asyncio.Queue(maxsize=100)`、`ThreadPoolExecutor(max_workers=1)`、检索 `1.5` 秒超时、
  自动整理 `2` 秒周期、`TextPart(...).mark_as_temp()` 注入；
- 修正原文过时处：版本 1.0.7 → 1.0.12、`storage.py` 2800 行的描述 → 5 行兼容层 + `store/` 15 个模块、
  `main.py` 职责中的“命令” → 已移至 `commands.py`；删掉指向不存在图片的引用。

## v1.0.11（2026-11-05）

命令分发拆分，行为不变：`commands.py` 里 487 行的 `_work` if 链按命令族拆成 7 个方法。

### 重构

- `_work` 变成 9 行分发器：按顺序调用 `_cmd_state`/`_cmd_pending`/`_cmd_groups`/`_cmd_capture`/
  `_cmd_briefs`/`_cmd_events`/`_cmd_help`，不匹配返回 `None`，全部不中才回“未知命令”；
- 每个 if 分支原样搬移，没有改写任何判断条件或返回文本。

### 测试

- 新增 `test_plugin.CommandSurface` 两项：11 条只读命令必须都有非空回复；
  `/dlm`、空白参数、乱写参数、缺参数的 `备份校验` 不得抛异常。

## v1.0.10（2026-11-05）

继续拆结构，行为不变：`store/context.py`（945 行）按职责分成三个 mixin。

### 重构

- `store/context.py` 945 行 → 7 行组合入口，拆成
  `context_sources.py`（事件准备、语义分组、主题与向量同步，475 行）、
  `context_packet.py`（待审/多事件/背景拼装与预算，367 行）、
  `context_followup.py`（追问与上一轮衔接，153 行）；
- `ContextMixin` 由三者组合，`Store` 的 MRO 与调用方式不变。

### 工程

- 新增 GitHub Actions `tests.yml`（windows-latest + Python 3.12）跑同一套 337 项，
  不需要 torch/模型、不联网；
- CI 首跑发现并修复：`testbed/harness/attribution.py` 在英文 Windows 控制台（cp1252）
  打印中文会 `UnicodeEncodeError`；新增 `emit()` 在 print 失败时改写 UTF-8 字节。

## v1.0.9（2026-11-05）

纯结构重构，**行为不变**：把两个巨型单文件按领域拆开。

### 重构

- `storage.py`（2934 行 / 114 个方法）→ `store/` 包：
  `base`（常量与写锁）、`embedding`、`capture`、`drafts`、`events`、`organize`、`relations`、
  `briefs`、`context`、`backup`、`core`（组合入口）；`storage.py` 保留为 5 行兼容 shim，
  旧导入路径（`from .storage import Store, MAX_CAPTURE_CHARS, _UPDATE_CUES`）全部照旧可用。
- `main.py`（1520 行）→ 721 行；命令分发 `_work`、6 个 `_generate_*`、6 个 `_render_*`
  移入 `commands.py` 的 `CommandMixin`，`DigitalMemoryStore(CommandMixin, Star)` 组合。
  装饰过的入口（`capture_private`/`add_memory_context`/`dlm`）仍留在 main.py，AstrBot 注册不受影响。

### 验证

- 离线回归 **337/337**（含 `test_plugin` 的钩子与命令用例）；
- 真实 AstrBot 4.29.3 链路复跑：插件加载 ✅、3 个钩子注册 ✅、采集 24 条 ✅、注入 2 次 ✅、
  真实 BGE 512 维 / 冷加载 12.3 s；
- `engine/` 与 `store/` 的调用关系、边界写在 `engine/README.md`。

## v1.0.8（2026-11-05）

代码整理：补上文档承诺却不存在的东西，修正文档里“查无此命令”的部分。

### 新增

- `/dlm 帮助`（也接受 `help`、`?`）：按“状态/线索/查看/记录/维护/整理”分组的命令总览；
  未知命令不再只返回一行用法，而是提示去 `/dlm 帮助`；
- `engine/README.md`：解释 `e31/e32/e417/e418/e419/lifemem` 是实验编号而非新旧版本，
  给出真实调用链与每个模块的边界，避免被当成死代码误删。

### 修复

- README / QUICKSTART 的命令表：删掉**从未实现**的 `/dlm 导入历史`、`/dlm 恢复说明`，
  修正 `/dlm 备份校验` 必须带备份文件名（原来写成无参数，实际只会回一条用法）；
- 新增回归 `test_plugin.CommandHelp`，锁住“帮助里必须列出的命令”与“未知命令指向帮助”。

## v1.0.7（2026-11-05）

只改 README，不动代码：加徽章与兼容性说明。

- 顶部徽章：release 版本、许可证、AstrBot 4.29.3、Python 3.12；
- 兼容性块：明确写成「4.29.3 实测通过；4.x 其他版本未验证」，并指向 QUICKSTART 的 4 个检查点，
  避免用户误以为所有 4.x 都能直接用。

## v1.0.6（2026-11-05）

只补文档，不动代码：把“下载之后怎么用”的流程写清楚。

### 新增

- `docs/QUICKSTART.md`：用户流程（安装三选一 → **4 个验证检查点** → 日常命令 → 更新 → 停用/卸载 → 备份迁移）；
- `docs/TROUBLESHOOTING.md`：症状 → 原因 → 处理（含第 0 节三条诊断命令、提 issue 需要带什么）；
- README 增加「快速开始」与「文档」索引，`INSTALL.md` 保持为参数级参考。

## v1.0.5（2026-11-05）

面向“已经在用 AstrBot 的人”：把安装收敛成一条命令。

### 新增

- `install.ps1`（随包分发）：自动定位 AstrBot 根 → 复制插件（旧版先备份）→ 按 `self` 预设写配置并打开
  `enabled`/`automatic_memory_enabled` → 追加白名单 → 可选安装 torch(CPU)+sentence-transformers 到
  AstrBot 自己的 `.venv` → 可选从 ModelScope 下载 BGE（约 92 MB）并回填 `model_dir`；
  支持 `-WithDeps/-SkipDeps/-WithModel/-SkipModel/-ModelDir/-Preset/-Force` 全自动模式；
- `metadata.yaml` 增加 `short_desc` 与 `repo`（AstrBot 4.29.3 的 `StarMetadata` 会读取），便于面板展示与仓库来源。

### 文档

- `INSTALL.md` 改为三条路：一键脚本（推荐）/ WebUI 上传 zip / 手工解压；补升级=重跑脚本、常见问题；
- 明确“AstrBot 自动装依赖会拉 CUDA 版 torch”，省空间请用 `install.ps1`。

## v1.0.4（2026-11-05）

只改文档，不动代码：让“下载 zip → 装进 AstrBot”这条路真的能走通。

### 修复

- `INSTALL.md` 原来让人跑 `python scripts/install.py`，但发包 zip 里没有 `scripts/`（脚本在完整工程
  仓库 `dev/scripts/`），照着做会直接失败——已改写为“手工安装为主、脚本指向完整工程仓库”；
- 明确默认状态是“装好但停用”，并给出 3 个必填开关（`enabled` / `automatic_memory_enabled` /
  `allowed_sender_ids`），补上 ModelScope 拉模型的完整命令；
- 升级/回退改为可手工执行的备份/恢复步骤；常见问题新增“装完完全没反应”。

## v1.0.3（2026-11-04）

把外围运维能力集成进 AstrBot 插件页面的可视化视图。

### 新增

- `views/panel/`：WebUI 控制面板（存储统计、容量预估、备份、轮转、checkpoint、
  完整性检查、归档导出、VACUUM）；
- 后端 Web API：`/panel/stats|backup|rotate|checkpoint|integrity|archive|vacuum`
  （通过 `context.register_web_api`，仅插件页可见）；
- 统计包含：库/WAL 大小、原话/待审/事件数、7 日均量、每条字节、5 年容量预估、
  磁盘剩余、钩子 P95、备份列表与阈值告警。

### 说明

- 归档只导出、不删除主库原话；VACUUM 需要临时空间，页面会二次确认。

## v1.0.2（2026-11-04）

修复“每轮供给”在指代分支上的漏口，并补充现场归因证据。

### 修复

- `context_turn`（“后来呢？”类追问）没接住时返回空，绕过背景兜底；
  现在回退到 `context_packet`，该轮也会给有界上下文（`test_followup_without_focus_still_gets_context`）。

### 现场验证（真实 QQ + 真实 DeepSeek）

- 向记忆库播种 30 天前的旧原话“青柠计划的门禁暗号是7391”（**不经过聊天历史**）；
- 提问后请求体里该整句**只出现在插件片段**，普通历史为 0；
- 真实模型复述了 7391，并主动标注“未确认、可能是记错/过时/玩笑”，符合“只给线索、不确认事实”。

## v1.0.1（2026-11-04）

修正产品定位：**每轮都给模型提供有界上下文**，而不是只在字面/语义命中时注入。

### 新增

- `always_context_enabled`（schema 默认开，`self`/`debug` 预设开，`off` 关）：
  级联检索无结果时，注入“背景上下文”包（较早原话 2 条 + 最近 1 条，均标未确认，≤320 字）；
- M2 双口径：命中口径与供给口径分开报告（供给 105/120、R@1 84/100、串味 11）。

## v1.0.0（2026-11-04）

首个可交付版本：把 0.16.x 的实机闭环、纠错成组、可观测性、交付工具收敛为可分发安装包。

### 包含

- 私聊纯文本采集、本地 BGE 语义召回、未确认标注、备份/校验/恢复；
- 自动整理：字面分组、短句变化成组（纠错 27/35 自造语料）、可选本地语义；
- 默认安全：默认关闭、白名单、不确认事实、候选筛选器默认关闭；
- 交付：安装/升级/回退脚本、安装后自检、配置三档预设、zip + SHA256；
- 可观测性：`latency.jsonl` 与 L0 soak 汇总；
- 规模与恢复：5,000 条 P95 293 ms（合成数据）、受控强杀恢复通过。

### 未验证（交付前必读）

- 真实聊天 R@1 / 误注入 / 纠错未测（缺脱敏语料）；
- L0 连续 72 小时未跑满；
- 真人 30 分钟安装走查未做。

## v0.16.1（2026-11-04）

可观测性与一个同秒召回修复。

### 新增

- **每轮耗时可观测**：`latency.jsonl` 记录每轮钩子耗时与是否注入（本机、2 MB 自动裁剪）；
  配置项 `latency_metrics_enabled`（默认开）；soak 会汇总 `hook_turns / hook_injected / p95`，
  L0 的“真实 QQ 注入率 / P95”不再依赖探针。

### 修复

- **同秒内的原话无法召回**：`cutoff` 被截断到秒，导致与请求同一秒采集的原话被当成“未来”排除。
  改为保留微秒后，同一秒内先采集再提问可以正常命中（`test_metrics` 覆盖）。

## v0.16.0（2026-11-04）

M1 实机闭环 + 交付最小闭环。**仍为自用/测试版，默认关闭。**

### 新增

- **模型预热**：`Store.prewarm_embedder()` 在独立线程加载 BGE；加载期间语义路径降级为字面，
  采集/整理与回复不再被 11–13 s 的冷加载阻塞（实测首条回复 TTFB 8.0 s → 0.017 s）。
- **候选筛选器（G3 窄路，默认关闭）**：本地无结果时，只对"≤16 字且含指代词"的追问，
  让模型从 ≤5 条候选里筛原话；严格 JSON 校验、1.1 s 超时、每会话每小时限流；
  被选候选必须与问题有字面重合（纯指代除外）。自造语料 A/B：指代 15/15、负例 20/20 拒绝。
- **配置预设**：`presets/off|self|debug.json`；安装脚本按 schema 默认值 + 预设生成配置。
- **安装/升级/回退**：`scripts/install.py`、`upgrade.py`、`rollback.py`（备份到 `data/plugins_backup`）。
- **启动自检**：缺依赖或模型目录无效时只打印一条明确提示，其余功能可用。
- **备份产物清理**：备份目录只保留单个 `.sqlite3`，校验残留的 `-wal/-shm` 会被删除。
- **后台任务死信**：连续失败 8 次后停止重试并标记 `dead`，不阻塞同会话后续任务，`/dlm 记忆状态` 会报数。
- **文档**：`INSTALL.md`、`presets/README.md`、`docs/` 下的实机、基线、校准、就绪度评估。

### 变更

- **纠错成组**：语义来源选中旧原话后，若 180 秒内下一条含变化词，会作为"后续可能更新"一起给出；
  待审链式匹配也从"整句以变化词开头"放宽为"短句内出现变化词"。M2 自造语料纠错 4/35 → **27/35（77%）**。
- **原话相似度默认阈值 0.75 → 0.60**：M2 自造语料实测 R@1 41/100 → 65/100，负例误注入仍 0/20；
  新增配置项 `source_similarity_percent`（50–95），0.55 可达 72/100 但作为高风险实验值保留。
- **变化词表扩充**：补充 `挪到/挪至/改去/改铺/改期/改签/换到/调到/降到/延期/延后/不做了/不办了/去不了`
  等自然说法（三处正则同步），修正只从 3 升到 4——瓶颈在自动链路未把新旧两条绑成一组。
- 移除未接入主流程的研究代码 `engine/e50b`、`e50g`、`e421`（约 530 行）；
  `engine/lifemem` 仍被 e418 使用，保留。
- 元数据改为"自用版"表述，版本号从 0.15.116 升至 0.16.0。

### 已知问题

- 纠错仍只有 4/35；相似事件串味 3 条（阈值下调的代价）。
- 自动路径下"之后呢？"类追问默认接不住（需开启候选筛选器）。
- 未做 5,000 条压测、未做真实进程崩溃恢复演练。
- 真实对话准确率未知；现有数字均来自自造/固定语料。
