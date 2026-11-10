# M6 自动备份调度

- 日期：2026-11-10
- 状态：**interval + retention 切片；不是生产调度器**
- 测试：504/504

## 1. 实现

`BackupScheduler`：

- `maybe_backup(now)`：按 interval 决定是否备份；
- `backup_to` 一致性备份；
- 只保留最近 `keep` 个 `backup-*.sqlite3`；
- `backups()` 列出。

## 2. 测试

| 测试 | 结果 |
|---|---|
| interval 内不重复备份 | ✅ |
| 超过 interval 备份 | ✅ |
| keep=2 只保留 2 个 | ✅ |
| 重启后 interval 仍生效 | ✅ |
| 锁文件阻止并行备份 | ✅ |
| stale lock 自动回收 | ✅ |
| 恢复备份后 retrieve/context 可用 | ✅ |

## 3. 边界

- 内存 `last_backup_at`，重启后不持久；
- 没有分布式锁；
- 没有备份失败重试；
- 不是生产级定时任务。
