# 发布说明 v1.0.16

迁移包：把“换机器”从手工对文件，变成**一条命令打包 + 一条命令校验**。

## 用法

```text
旧机器：/dlm 迁移包
新机器：/dlm 迁移检查 migrate-20261105-1530-1.0.16.zip
```

包会生成在 `data\plugin_data\astrbot_plugin_digital_memory_store\exports\`，内含：

| 文件 | 作用 |
|---|---|
| `memory.sqlite3` | 记忆库快照（打包前先做 WAL checkpoint，保证自洽） |
| `plugin_config.json` | 插件配置（存在才带） |
| `scope_overrides.json` | 会话认领表（存在才带） |
| `manifest.json` | 逐文件 sha256、原话/作用域/事件计数、插件版本、创建时间 |
| `RESTORE.md` | 恢复步骤（含 `model_dir` 必须改成新机器路径的提醒） |

## 校验到底查什么

`/dlm 迁移检查 <文件>.zip` 做三件事，任一不符就判失败：

1. **结构**：manifest 存在、格式为 `digital-life-migration/1`；包内每个非 manifest 文件都必须登记；
2. **完整性**：逐文件比对 sha256；对 `memory.sqlite3` 跑 `PRAGMA integrity_check`；
3. **一致性**：库里的原话数、作用域数与 manifest 记录的数字相等。

失败时会列出原因（最多 8 条）并明确写“**不要用这个包恢复**”。

## 为什么不做“一键恢复”

恢复要替换正在被占用的 SQLite 文件。在线替换的失败模式是“数据半新半旧”，比手工停服危险得多。
所以这版只做**打包 + 校验**，恢复按 `RESTORE.md` 停服操作；等以后有停服编排再考虑自动化。

## 测试

新增 `test_plugin.MigrationPack` 3 项：

1. 打包：zip 内含 manifest/memory.sqlite3/RESTORE.md，manifest 计数 ≥1，随后自检通过；
2. 拒绝：`../x.zip` 带路径、找不到的包、无参数；
3. 篡改：把库文件末尾 4 字节改掉后重新打包，校验必须报“校验和不匹配”。

全量 **350/350**，CI 在 3.10 / 3.11 / 3.12 三版本通过。

## 迁移功能现在的全貌

| 路线 | 命令 | 适用 |
|---|---|---|
| 迁移包 | `/dlm 迁移包` → `/dlm 迁移检查` | 想按步骤走、要校验完整性 |
| 整目录拷贝 + 认领 | 拷目录 → `/dlm 会话认领 <旧 origin>` | origin 变了但想保留原库 |
| 导出/导入 | `/dlm 导出历史` → `/dlm 导入历史` | 只搬原话、跨环境不方便搬库 |

三者互补：迁移包保真但要求停服；导出/导入轻量但只搬原话。
