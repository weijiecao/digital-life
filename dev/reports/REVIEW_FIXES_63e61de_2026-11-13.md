# 审查快照 63e61de 修复报告

> 审查对象：`63e61de412498ccc1c28bc72737c9ac166485838`（`feat: integrate memory console and native output module`）。
> 本报告记录该轮审查提出的 10 项问题（F01–F10）的修复内容、行为变化与验收结果。
> 修复只改 `dev/` 开发线；未重建历史发布包，也未声称完成真实 AstrBot + NapCatQQ 全链路。

## 1. 结论

10 项问题全部修复，并新增 27 项确定性回归（`dev/tests/test_review_63e61de_fixes.py`）。
`python -m unittest discover -q -s tests -t .` 在 `dev/` 下为 **742 项，741 通过、1 跳过、0 失败**
（跳过项仍是寻找旧 RC1 包的包测试）。

审查证据包中的复现脚本按原断言会失败，因为它们断言的是缺陷行为；把断言换成打印后，
同一批路径的结果已变为预期值：

| 复现 | 修复前 | 修复后 |
|---|---|---|
| F01 `SAME A + DIFFERENT B` | `cannot_link=1` 但 `persisted=false`；随后 `SAME B` 仍能附加 | `persisted=true`；随后 `SAME B` 得到 `conflicts=1`、`attached=0` |
| F02 会话人格 alice / bob | 只有 1 个 scope | 2 个 scope（按宿主人格区分） |
| F03 `_checkpoint` 历史 | `status=invalid`，整份快照被拒 | `status=staged`，用户原话入队，游标元数据保留 |
| F04 判别被阻塞时 `observe` | 阻塞直到判别结束 | 不阻塞（同轮 `elapsed_seconds≈0.2`，只含检索与本条记录写入） |
| F05 超大附加文本 | `output_status=INJECTED`，`host_memory_tokens=397` | `output_status=EMPTY`，`host_memory_tokens=0` |
| F06 205 条导入 | 3 轮后仍 5 条未投递 | 一轮后未投递 0 |
| F07 前 10 条持续失败 | 第 11 条三轮都未被尝试 | 第二轮尝试第 11 条并提交成功 |
| F08 源码构建包 | 隔离导入 `ModuleNotFoundError: astrbot_memory_input` | 隔离导入 0，控制台静态资源齐全 |
| F09 抽屉删除按钮 | 抽屉路径调用 0 次 | 抽屉路径调用 1 次（真实 `app.js` 绑定，Node 事件替身验证） |
| F10 只有 `seq` 列的 `dl_capture` | `ok=true` | `ok=false`，`missing_columns` |

## 2. 修复明细

### F01 · 排斥关系没有落盘（P1）

`ConstrainedResolver.resolve` 改为「先定归属、再算排斥」的两阶段，并包在同一事务里：

1. 先按判别器给出的顺序记录全部 proposal；
2. 处理 `SAME_EVENT`，得到本次运行的最终归属集合（已有归属 ∪ 本次新附加）；
3. 对每个 `DIFFERENT_EVENT`，与**最终归属集合**逐个写入 cannot-link。

因此 `SAME A + DIFFERENT B` 与 `DIFFERENT B + SAME A` 结果一致。`ResolutionSummary.cannot_link`
现在只统计**实际至少落盘一条排斥关系**的 proposal；没有可绑定归属时仍写审计决定，但不计成功。
`DIFFERENT_EVENT` 指向原话已经归属的事件时记为 `CONFLICT`（理由 `different_from_attached_event`），
不再静默“成功”。

位置：`dev/v1_5_consolidator/resolver.py`。

### F02 · 人格没有参与作用域映射（P1）

- 新增 `read_conversation_identity()` / `conversation_persona()`，人格取自宿主会话对象
  的 `persona_id`；采集与查询使用同一个来源。
- `SHADOW_PERSONA_ID` 只作为兜底；一旦显式设置，宿主人格不一致就拒绝采集与注入
  （`input_modes["persona_mismatch"]`），不再把两个不同人格写进同一 scope。

位置：`dev/memory_engine/native_host.py`、`dev/host_shadow/main.py`、`dev/host_shadow/README.md`。

### F03 · `_checkpoint` 导致整份历史被拒（P1）

输入包识别 AstrBot v4.29.3 的内部段 `role == "_checkpoint"`：跳过该段、不生成来源记录，
并保留 checkpoint id 到 `import_attempt.detail`（`internal_segments` / `checkpoint_ids`）。
原话编号按**保留后的消息顺序**计算，因此插入 checkpoint 不会改变已有原话的身份，
后续快照仍是有效前缀。`Inbox.accept` 明确拒绝把内部段当作来源。

位置：`dev/astrbot_memory_input/native.py`、`dev/astrbot_memory_input/journal.py`、
`dev/memory_engine/native_input.py`。

### F04 · 慢模型阻塞聊天钩子（P1）

判别拆成三段，跨线程调度：

1. **准备**（数据库线程）：选记录、算候选、构造判别请求并落审计（`RelationJudge.prepare`）；
2. **判别**（独立线程池）：只做模型等待（`RelationJudge` 的 provider 调用），
   纯解析在模型线程内完成（`RelationJudge.complete`）；
3. **提交**（数据库线程）：短事务复验 `state`/`scope_epoch` 后写入。

`NativeInputPump` 新增 `judge_executor`，宿主为它单独开一个 `dljudge` 线程池；
数据库线程只做短事务。`NativeInputService.run_once()` 保留同步语义供 CLI 与测试使用。

位置：`dev/v1_5_consolidator/relation_judge.py`、`dev/memory_engine/native_judge.py`、
`dev/memory_engine/native_input.py`、`dev/memory_engine/native_host.py`、`dev/host_shadow/main.py`。

### F05 · 宿主预算漏算当前请求（P1）

`HostBudget` 增加 `request_tokens`，`memory_tokens` 扣除：
当前 `prompt`、其他插件追加的 `extra_user_content_parts`（本插件上次注入的临时块除外）、
`image_urls`/`audio_urls`（按固定保守值）、`func_tool` 定义与 `tool_calls_result`。
提交注入前按当前请求再算一次预算，准备与提交之间被追加内容时会被拒绝。

位置：`dev/memory_engine/host_budget.py`、`dev/host_shadow/main.py`、`dev/memory_output/astrbot.py`。

### F06 · 后台不继续排空 journal（P2）

`NativeInputService` 每轮先 `journal.deliver` 再判别：`submit` 仍即时投递，
泵的每一轮与启动初始化都会继续投递积压；`health()` 暴露 journal 未投递数、inbox 状态与失败重试明细。

位置：`dev/memory_engine/native_host.py`。

### F07 · 持续失败的旧记录饿死新输入（P2）

- `native_input_attempt` 增加 `attempts` / `next_attempt_at`（启动时自动 `ALTER TABLE` 迁移）；
- 判别或提交失败时持久化次数、原因与下次尝试时间，按 `2**(n-1)` 秒退避（封顶 300 秒）；
- 选取顺序：新任务优先，其次到期的重试，最后是暂缓项；新任务与重试分开配额
  （默认重试只占本轮的 1/4，周期全量重试时把配额留给重试）。

位置：`dev/memory_engine/native_input.py`。

### F08 · 构建清单缺新增依赖与 UI（P2）

`source_files()` 改为显式目录清单（`v1_5_consolidator`、`memory_engine`、
`astrbot_memory_input`、`memory_output`、`dlm_console`、`host_shadow`）+ 递归静态资源
（`html/js/css/svg` 与 `views/console`），排除缓存目录。`build()` 写完 zip 后立即在
隔离目录里做导入与静态资源自检，缺包即构建失败；`verify_r0_bundle.py` 的 smoke 同步覆盖
`native_host`、`output`、`console` 导入与控制台资源。

位置：`dev/scripts/build_r0_bundle.py`、`dev/scripts/verify_r0_bundle.py`。

### F09 · 抽屉删除按钮没有事件委托（P2）

把 `[data-act]` 点击处理抽成 `handleActionClick`，同时挂到 `#view` 与 `#drawer-body`；
两份控制台静态资源用 `scripts/sync_console_view.py` 保持字节一致。

位置：`dev/dlm_console/web/app.js`、`dev/host_shadow/views/console/app.js`。

### F10 · 结构不完整的备份被判为有效（P2）

备份校验新增显式 schema 约定：

- 关键表 + 关键列（store 的 `dl_capture`、engine 的三张事件表、journal 的两张表）；
- 关键主键（`dl_capture.seq`、`event_candidate.event_id`、`native_record.key`）；
- schema 版本：engine 必须有 `meta.schema_version`；store 旧库缺 `dl_meta` 只记 warning，
  版本存在但非法时记 error；
- 必要扫描失败、缺列、`foreign_key_check` 失败都记 error，不再等价于“扫描通过且零异常”。

位置：`dev/dlm_console/core.py`。

## 3. 行为与契约变化

| 项 | 变化 |
|---|---|
| `ResolutionSummary.cannot_link` | 从“处理过的 DIFFERENT proposal 数”变为“实际落盘至少一条排斥的 proposal 数”。 |
| `DIFFERENT_EVENT` 指向已归属事件 | 记为 `CONFLICT`，不再静默成功。 |
| 人格 | 宿主 `persona_id` 优先；`SHADOW_PERSONA_ID` 显式设置后成为强制校验。 |
| checkpoint | 接受并跳过，元数据写入 `import_attempt.detail`，不进入原话。 |
| 判别调度 | 泵改为准备/判别/提交三段；`run_once()` 仍同步执行，接口不变。 |
| `native_input_attempt` | 新增两列，旧库自动迁移；控制台仍按存在的列降级读取。 |
| 失败重试 | 新增指数退避与重试配额；提交阶段错误不设退避，但仍受配额限制。 |
| `HostBudget` | 新增 `request_tokens`；位置参数前四项含义不变。 |
| 发布包 | 内容集合扩大（新增三个包与控制台资源），旧发布包保持原样。 |
| 备份校验 | 更严格；不可用结构不再返回 `ok=true`，`restore_backup` 因此也会拒绝。 |

## 4. 验收

```powershell
cd dev
python -m pip install -r requirements-release.txt
python -m unittest discover -q -s tests -t .
```

- 742 项：741 通过、1 跳过、0 失败（`skipped` 仍是旧 RC1 包测试）。
- 新增回归覆盖：F01 两种 proposal 顺序与重评冲突、F02 双人格与固定人格失配、
  F03 checkpoint 插入/幂等、F04 阻塞判别下的 `observe`、F05 超大附加文本与工具/媒体计数、
  F06 journal 补投、F07 退避与公平重试、F08 隔离导入与静态资源、F09 抽屉委托与双份一致、
  F10 缺列/缺主键/缺 schema 版本。
- 控制台前端另用 Node 事件替身执行真实 `app.js` 绑定：`drawer_action_calls=1`、
  `table_action_calls=1`、两份 `app.js` 字节一致。

## 5. 未验证边界

- 未在真实 AstrBot 4.29.3 + NapCatQQ 上跑全链路；F03 只用持久化协议的最小样本验证。
- 未做浏览器像素/真实点击验收；F09 是事件绑定与 HTML 结构验证。
- 未重跑 BGE 质量评估，也未测真实多模态预算，媒体 token 是固定保守估计。
- 百万级记忆规模下的检索与判别延迟未测量；本轮不把推测的复杂度风险写成实测结论。

按审查建议，当前状态仍应表述为：**模块结构完整、基础回归通过、完整宿主链路仍需真实接线验收，
继续保持隔离验收状态。**
