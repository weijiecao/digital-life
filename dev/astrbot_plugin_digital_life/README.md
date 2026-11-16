# 数字生命：统一 AstrBot 插件

开发版 `0.7.0-dev.2`，基于 `abbfceae5b2a0ef4659310a2c1d7791946d48f3c`（alpha.6）。新增迁移、实时追加、恢复切换、偏好排序、短指代和主动请求记忆入口。当前交付是可安装的开发版；真实 AstrBot/NapCat/Windows 及长期运行验收仍需完成，未发布 RC。

## dev.2 修复

基于远端 `4c07d5d53f79ea3187ab6aefd6177a66fa805f6a`（与 dev.1 源码树一致）。备份、校验、迁移和测试夹具的短期 SQLite 连接现在明确关闭，同时保留提交/回滚语义。新增测试持有连接强引用，检查成功、异常和后续文件清理均不依赖 GC；这不代替 Windows 实机结果。

## 安装和模式

构建与验包（在仓库根目录执行）：

```powershell
python tools/build_unified_plugin.py --out-dir dist
python tools/verify_unified_plugin.py --dist dist --report dist/package_verification.json
```

安装：AstrBot WebUI → 插件 → 上传 `dist/astrbot_plugin_digital_life-0.7.0-dev.2.zip`。
安装包包含全部共享模块，不需要设置 `DIGITAL_LIFE_DEV`。

从 alpha.6 升级后请完整重启 AstrBot 服务；仅热重载不能可靠清除既有顶层共享模块的 Python 导入缓存。

停用旧 `astrbot_plugin_memory_shadow` 后使用统一插件。同一目录只允许一个统一实例拥有写权限；旧插件活动时显示 `blocked-legacy`。同进程检测不能阻止另一个进程中的不合作旧程序，操作同一数据集前应停用它。

| 模式 | 行为 |
|---|---|
| OFF | 不采集、不注入；管理员可管理已有数据 |
| COLLECT（默认） | 实时采集、同步历史、后台整理，不注入 |
| ASSIST | 在 COLLECT 基础上，按证据和 Token 预算注入临时记忆块 |

`allowed_sender_ids` 必须填写明确的私聊用户 ID，留空不采集任何用户。`judge_provider_id` 填 AstrBot provider ID；`auto` 可以读取兼容环境变量，显式空字符串关闭整理模型。无模型时原文保存在队列中，不假装已整理。精确预算依赖 `tiktoken`；缺失时不注入。

保留 `memory_budget_tokens`、`judge_timeout_seconds`、`judge_budget_chars`、`tick_seconds`、`tick_limit`、`retry_every` 和 `console_read_only`。旧 SHADOW_* 配置优先级仍为：明确插件配置 > 兼容环境变量 > 默认值。

新增配置：

- `preference_rank_enabled=true`：已通过证据准入的候选中，偏好内容优先。
- `followup_enabled=true`：同用户、人格、会话的近期话题辅助解析“那后来呢”等短指代；五分钟过期，最多保存 1024 个会话、每会话十条短文本，只在内存中保存。
- `proactive_enabled=false`：允许其他宿主代码调用 `inject_proactive`。本插件不创建定时任务，不自行发消息。

## 控制台操作

WebUI → 插件 → 数字生命 → 数字生命控制台 → **统一管理**。

统一管理显示运行时队列、所有权、数据代次、待定位条数、迁移任务、备份和错误。既有数据浏览页继续提供只读诊断，旧维护页的写操作被禁用；迁移、备份、恢复和撤回通过统一应用服务串行执行。设置 `console_read_only=true` 同时禁用统一管理写操作。

### 旧基座迁移

1. 输入旧基座 SQLite 文件路径，点击“创建旧基座迁移”，生成包含 WAL 已提交内容的一致冻结快照。原文件保持不变。
2. 根据显示的旧 scope 清单填写身份映射，例如：

```json
{"旧scope":{"platform":"aiocqhttp","bot":"机器人ID","user":"用户ID","persona":"人格ID","conversation":"legacy"}}
```

`conversation` 使用独立、稳定的旧来源命名空间。身份不得从故事正文猜测。未映射记录为 `QUARANTINED`；补全映射后可继续，已使用的映射不能换成其他用户。

3. 点击“继续迁移”，每批最多 200 条，界面可在批次间暂停。刷新页面、重启插件后水位仍保留。失败任务保留错误和最后已提交水位，可重试。
4. 确认整理模型、每轮条数及预算配置后点击“允许后台整理”。此前用户原文处于 `MIGRATION_HELD`，包括仅 journal 已写入就中断的情况。assistant、被忽略/撤回内容及旧摘要不会提升为用户事实。

支持含 `dl_capture` 的旧基座及未关联 capture 的 `messages`。旧分组、摘要、关系等完整保存在冻结数据库中，供追溯，不直接伪装为新引擎已验证事件。哈希错误为 `INVALID_HASH`；同一原始身份内容变化为 `IDENTITY_CONFLICT`，保留原文且不覆盖已入库事实。相同来源的后续快照只补充新身份，已有记录为 `DUPLICATE`。

**来源标识必须保持稳定。** 首次不填时从旧文件绝对路径生成并显示。旧库搬到另一目录、机器或改变路径表示后，请填写先前的 `source_id`，否则会被视为另一个来源。相同内容不能证明是同一旧库。

旧 shadow 数据使用“导入已停用的 shadow 数据组”：输入含 `shadow.sqlite3` 与 `native_input_journal.sqlite3` 的目录，确认来源实例已停用。校验后出现在备份列表，点击恢复切换；保持原有 source、事件 ID 和回执，不重新判别已完成记录。

### 实时追加和历史对账

当前事件中的稳定 `message_id` 独立追加到持久 journal，不依赖宿主先把本轮写进历史。同一 ID 幂等，相同文本不同 ID 分别保留。历史继续接受完整快照；截断、编辑、重排会报冲突，不把局部列表冒充完整历史。

有稳定 ID 的历史与实时记录直接对齐。缺少 ID 的历史若与实时记录可能重叠，会保留原始别名并进入待定位状态，不按文本相等强行合并。可信宿主适配器可通过 `UnifiedJournal.stage(history, origin, links={"0":"平台消息ID"})` 提供位置映射，验证文本和身份后对齐。当前通用 AstrBot 钩子没有假定宿主一定提供这种映射；无法证明的重叠可能长期待定位。日常聊天无需用户手工整理。

### 备份、恢复和撤回

完整备份包含业务库、输入日志、迁移账本与映射、冻结旧库、提交时间文件及文件哈希。恢复前核对数据库版本、完整性、外键、双向投递回执和原文载荷，再自动备份当前数据。

恢复会停止采集/注入和 pump、等待在途模型退出、排空数据库工作，复制到独立代次目录，通过 `CURRENT.json` 原子切换活动路径，重开并验证后恢复运行。失败回到旧目录；在 PREPARED/SWITCHED 阶段进程退出，下次启动整组回退。旧目录和恢复前备份保留。配置仍使用当前 AstrBot 插件配置，备份中的摘要不自动覆盖配置。

恢复后 `store_generation` 增加，旧 ContextOutput 被拒绝，短指代与派生缓存清空。等待目录锁的实例取得所有权时重新加载活动代次。

维护期间未采集的事件记录缺口计数，后续完整历史快照会尝试补录。若宿主已截断或丢失这些消息，计数不能重建正文；这类宿主需要停聊维护或提供可靠历史回放。

“撤回来源”使原文不再参与回忆，失效派生证据并阻止 journal 重放；中断发生在 journal 屏障之后时，重启补齐业务失效。这不是物理擦除：审计原文、旧目录和历史备份仍保留。

## 主动请求接入

```python
result = await plugin.inject_proactive(
    trusted_event, provider_request,
    request_id="scheduler:task-id:occurrence-id",
    trigger_kind="scheduled",  # 或 manual
)
```

目标身份来自可信宿主事件与当前会话，人格/用户隔离、允许用户范围和预算检查与普通输出共用。不会把主动生成文本写成用户经历。相同 scope/request_id 再次调用返回 `DUPLICATE`；同 ID 换查询返回 `CONFLICT`。调度器应处理这些返回值并负责实际消息发送及其幂等。本入口不是送达回执，不包含某一第三方主动聊天插件的专用连接器。

输出协议升为 `memory-output-v2`，新增 `store_generation` 和可选 `retrieval_query`。后者只扩展检索意图，不修改原始 prompt、人设和聊天历史；证据证书仍绑定实际检索查询。

## 数据与协议

默认根目录为 `<AstrBot>/data/plugin_data/astrbot_plugin_digital_life/`。若新目录不存在且旧 shadow 目录存在，沿用旧目录；这是整组接续。首次恢复前活动库位于根目录，之后位于 `generations/<id>/`。不得绕过 `CURRENT.json` 直接把根目录旧文件当作当前数据。

管理 API 均在宿主鉴权保护下：`/astrbot_plugin_digital_life/console/runtime/`。`GET status`；POST `migration_create`、`migration_mapping`、`migration_batch`、`migration_release`、`shadow_import`、`backup`、`verify_backup`、`restore`、`withdraw`。
接口行为、前提与失败处理见 [`docs/MIGRATION_RECOVERY.md`](../../docs/MIGRATION_RECOVERY.md)，
路由与状态返回由 `dev/tests/test_unified_plugin.py::ConsoleApiTests` 覆盖。

本版没有做全套旧命令兼容，也没有把旧插件另一个独立注入器搬进来。共享模块沿用仓库既有的顶层导入布局；不宣称已完成所有私有命名空间隔离改造。

## 验证

仓库回归（在仓库根目录执行）：`python tools/run_regression.py`。当前活动套件包含本插件的
专项用例 `dev/tests/test_unified_plugin.py`：迁移预览、备份一致性、恢复切换与崩溃回退、
实时追加与撤回、运行权、输出 v2、管理 API 路由与 SQLite 句柄释放。

包级检查：`python tools/verify_unified_plugin.py --dist dist --report dist/package_verification.json`，
外加 `--self-test` 证明缺文件、篡改、路径穿越和重复成员会被拒绝。无 AstrBot 时插件模块
用标注的宿主 stub 导入，报告写 `host: stub`，只算离线通过。

前端 DOM 与浏览器脚本（`completion_suite.py`、`console_dom_check.cjs`、
`console_browser_check.py`）属于上一版工程包，现保存在
`archive/history/unified_plugin_package/`，不在本仓库运行。

真实宿主、真实模型语义质量、Windows 文件句柄行为、第三方主动聊天连接器和 72 小时长跑仍待验收。进程退出回退测试不能替代断电耐久性验证。能力映射见
`dev/reports/SPECIALIZED_COVERAGE.md`。
