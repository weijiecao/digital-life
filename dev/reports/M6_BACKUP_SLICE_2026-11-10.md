# M6 备份一致性切片

- 日期：2026-11-10
- 状态：**M6 起步，不是规模/运维验收**
- 代码：`dev/v1_5_consolidator/store.py`

## 1. 已实现

`V15Store.backup_to(path)`：

- 使用 sqlite3 backup API；
- 生成一致快照；
- 对目标库执行 `PRAGMA integrity_check`；
- 返回 `ok` 或错误。

`integrity_check()`：

- 当前库完整性检查。

## 2. 测试

| 测试 | 结果 |
|---|---|
| 备份包含已提交事件/evidence | ✅ |
| 备份 integrity_check = ok | ✅ |
| 备份后新增事件不进入旧快照 | ✅ |

全量：**471/471**。

## 3. 边界

- 未实现自动备份调度；
- 未实现恢复后删除清单重放；
- 未实现跨 V1/V1.5 对账；
- 未做过 10 万/100 万规模；
- 未在 Windows 目标机验证备份目录权限。
