# M6 多进程写并发修复

- 日期：2026-11-11
- 触发：4 scope × 25k kill 后续跑出现 `database is locked`
- 状态：**修复后 100k 多 scope 续跑完成**

## 1. 失败路径

修复前：

- `BEGIN IMMEDIATE` 直接抛 `database is locked`；
- `release_scope` 也会抛 locked；
- 子进程 exit 1，最终只完成 31k–54k 条。

## 2. 修复

`dev/v1_5_consolidator/store.py`：

- `PRAGMA busy_timeout=5000`；
- `release_scope` 对 locked 退避重试；
- `unit_of_work` 的 `BEGIN IMMEDIATE` 对 locked 退避重试。

## 3. 结果

| 项 | 值 |
|---|---:|
| scopes | 4 |
| per scope | 25,000 |
| kill 后已提交 | 549 / 410 / 0 / 0 |
| resume exits | 0/0/0/0 |
| receipts | 100,000 |
| evidence | 100,000 |
| duplicates | 0 |
| integrity | ok |

## 4. 边界

- 单机 SQLite WAL；
- 进程 terminate，不是断电；
- 4 scope，不是高基数；
- 修复不改变单写者吞吐上限。
