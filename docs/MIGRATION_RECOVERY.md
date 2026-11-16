# 迁移、备份、恢复与撤回

本文只描述当前 `0.7.0-dev.3` 的行为。所有操作都通过统一应用服务串行执行，
入口是宿主鉴权后的管理 API：`/astrbot_plugin_digital_life/console/runtime/`。

| 操作 | 方法与路径 | 正文 |
| --- | --- | --- |
| 创建迁移 | `POST …/runtime/migration_create` | `{"source": "<旧库路径>", "mapping": {...}, "source_id": "可选"}` |
| 继续迁移 | `POST …/runtime/migration_batch` | `{"id": "<run>", "limit": 200}` |
| 补映射 | `POST …/runtime/migration_mapping` | `{"id": "<run>", "mapping": {...}}` |
| 允许整理 | `POST …/runtime/migration_release` | `{"id": "<run>"}` |
| 导入旧 shadow | `POST …/runtime/shadow_import` | `{"source": "<目录>", "source_stopped": true}` |
| 备份 | `POST …/runtime/backup` | `{}` |
| 校验备份 | `POST …/runtime/verify_backup` | `{"id": "<backup>"}` |
| 恢复 | `POST …/runtime/restore` | `{"id": "<backup>"}` |
| 撤回 | `POST …/runtime/withdraw` | `{"scope": "<scope>", "source_seq": 12}` |
| 查询状态 | `GET …/runtime/status` | — |

## 1. 旧基座迁移

### 前提

- 旧库包含 `dl_capture` 表；未关联 capture 的 `messages` 也会被读取。
- 迁移只读取旧库的**一致快照**（`sqlite3` backup，包含 WAL 已提交内容），源文件不变。
- 身份映射必须由人给出，不得从正文猜测；`conversation` 用独立、稳定的旧来源命名空间。
- 整理模型可选；没有模型时原文留在队列，不会被当成已整理事实。

### 步骤

1. `migration_create`：冻结快照，返回 `source_scopes`（每个旧 scope 的条数）与 `id`。
   状态为 `SNAPSHOTTED`。
2. 按 `source_scopes` 填 `mapping`，例如：
   `{"旧scope":{"platform":"aiocqhttp","bot":"…","user":"…","persona":"…","conversation":"legacy"}}`
3. `migration_batch`：每批最多 200 条（服务端还会限制到 1000），水位持久化。
   刷新页面或重启插件后从水位继续。
4. 剩余条目若为 `QUARANTINED`，用 `migration_mapping` 补映射；映射一旦被使用不可改成其他用户。
5. 全部批次完成、状态为 `READY` 后调用 `migration_release`，把 `MIGRATION_HELD` 的原文
   放入整理队列，返回 `released` 条数。

### 结果与失败处理

| 条目状态 | 含义 | 处理 |
| --- | --- | --- |
| `IMPORTED` | 已进入业务收件箱 | 等待 `release` 后参与整理 |
| `ARCHIVED` | assistant / 被忽略 / 撤回内容 | 只作历史，不升格为用户事实 |
| `QUARANTINED` | 缺少身份映射 | 补映射后重跑该批 |
| `INVALID_HASH` | 旧 `content_sha` 与正文不符 | 修数据源，不能强行导入 |
| `IDENTITY_CONFLICT` | 同一原始身份内容变化 | 保留原文，不覆盖已入库事实 |
| `DUPLICATE` | 后续快照中的已有记录 | 跳过，不重复写入 |

- 异常会把 run 置为 `FAILED` 并记录 `error`，已提交水位保留；再次调用 `migration_batch` 可续跑。
- 快照文件哈希在每批开始时校验，变化即报 `migration snapshot hash changed`。
- 同一来源的后续快照只补充新身份；**来源标识必须稳定**。旧库换目录/机器后，
  必须显式传回先前的 `source_id`，否则会被当成另一个来源。

## 2. 备份

`runtime/backup` 在维护闸门内执行：

1. 停止 pump、等待在途模型线程退出、排空控制台执行器与数据库工作；
2. 快照 `shadow.sqlite3` 与 `native_input_journal.sqlite3`（含 WAL 已提交内容）；
3. 复制 `commit_state.json` 与 `migrations/`（迁移冻结源属于备份集）；
4. 写 `manifest.json`（格式 `digital-life-backup-v1`、代次、逐文件 SHA-256、配置）；
5. 立刻调用 `validate_backup`，失败即报错，不留下"看起来成功"的备份。

`runtime/verify_backup` 的检查项：清单格式与路径白名单、逐文件哈希、两个库的
`integrity_check`/外键/必需表与 `schema_version`、journal 与业务库的**双向**投递回执
与原文载荷、迁移快照是否齐全。

## 3. 恢复切换

`runtime/restore` 的顺序：

```text
校验备份 → 备份当前数据 → 暂存到 generations/<32hex> → 关闭活动库
        → 原子切换 CURRENT.json → 重开 → 校验新库 → 标记 COMMITTED
```

- 打开失败或校验失败：关闭新库、把指针**整组回退**到旧代次（代次号继续增加）、重开旧库，
  错误写入 `status.maintenance.error`。
- 进程在 `PREPARED` / `SWITCHED` 阶段退出：下次启动读 `restore_operation.json`，
  未到 `COMMITTED`/`ROLLED_BACK` 就整组回退，候选目录保留供诊断。
- 恢复成功后 `store_generation` 增加，旧 `ContextOutput` 会被拒绝，短指代与派生缓存清空。
- 旧代次目录与恢复前备份都保留；不得绕过 `CURRENT.json` 直接使用根目录旧文件。
- 维护期间未采集的消息只记缺口计数；后续完整历史快照会尝试补录，宿主已丢失的正文无法重建。

## 4. 撤回

1. 先写 journal 重放屏障（`tombstone`），保证同一 key 不会再次投递；
2. 在同一事务里让 `delete_source` 失效派生证据，并把收件箱状态改为 `WITHDRAWN`；
3. 清空短指代缓存，返回 `physical_delete: false`。

中断发生在屏障之后时，重启会补齐业务失效；撤回后原文不再参与回忆，但**不是物理擦除**：
审计原文、旧代次目录与历史备份仍然保留。若需要真正删除，应另开数据清理任务，
不能把撤回当作擦除承诺。

## 5. 与旧 shadow 的关系

- 旧 shadow 目录必须**先停用**，再用 `shadow_import` 整组导入为可校验备份，
  然后通过 `restore` 切换；不会重建已完成记录的事件。
- 旧 `astrbot_plugin_memory_shadow` 仍在运行时，统一插件显示 `blocked-legacy`，
  此时不采集、不注入。同进程检测无法阻止另一个进程中的旧程序。

## 6. 未验证部分

上表描述的是**代码路径与离线测试**覆盖的行为。真实 AstrBot/NapCat、Windows 实机文件句柄、
断电耐久性与浏览器页面操作仍未验收，见
[`CURRENT_STATUS.md`](CURRENT_STATUS.md) 与
[`dev/reports/SPECIALIZED_COVERAGE.md`](../dev/reports/SPECIALIZED_COVERAGE.md)。
