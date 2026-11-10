# M6 规模烟测：10k / 100k 派生事件

- 日期：2026-11-10
- 状态：**烟测，不是 10 万主验收**
- 脚本：`dev/evaluation/m6_scale_smoke.py`

## 1. 结果

| 项 | 值 |
|---|---:|
| events | 10,000 |
| queries | 200 |
| lexical index build | 89.42 ms |
| p50 query | 37.204 ms |
| p95 query | 49.257 ms |
| max query | 76.21 ms |
| integrity_check | ok |

环境：本地 Windows，Python 3.12.10，SQLite 3.49.1；单进程、单 scope、词面索引。

## 1.1 100k 结果

| 项 | 值 |
|---|---:|
| events | 100,000 |
| queries | 200 |
| lexical index build | 828.18 ms |
| p50 query | 405.284 ms |
| p95 query | 458.756 ms |
| max query | 495.264 ms |
| integrity_check | ok |

100k 词面单进程 p95 约 459ms，低于 1s 目标；但这仍不是完整 10 万验收。

## 1.2 100k 并发读者

| 项 | 值 |
|---|---:|
| events | 100,000 |
| readers | 4 |
| queries | 200 |
| wall | 52.49 ms |
| p50 | 0.551 ms |
| p95 | 0.766 ms |
| max | 1.856 ms |
| errors | 0 |
| integrity | ok |

这是简单 LIKE 读查询的并发烟测，不是写并发、复杂检索或真实查询。

## 1.3 写并发（单 scope 租约）

| sources | wall | p95 | errors | evidence | receipts | integrity |
|---:|---:|---:|---:|---:|---:|---|
| 20 | 78.36 ms | 2.181 ms | 0 | 20 | 20 | ok |
| 10,000 | 42.8 s | 6.797 ms | 0 | 10,000 | 10,000 | ok |
| **100,000** | **598.3 s** | **7.73 ms** | **0** | **100,000** | **100,000** | **ok** |

多线程各自连接，通过 scope lease 串行写；同一 scope 无并发写冲突。不是多 scope 写扩展测试。

## 1.4 多 scope 写并发

| sources | scopes | per scope | wall | p95 | errors | evidence | receipts | integrity |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 10,000 | 4 | 2,500 | 16.98 s | 2.215 ms | 0 | 10,000 | 10,000 | ok |
| **100,000** | **4** | **25,000** | **631.2 s** | **8.463 ms** | **0** | **100,000** | **100,000** | **ok** |

多 scope 可并行申请不同租约；10k 比单 scope 42.8s 快，但 100k 仍受 SQLite 单写者限制（631s vs 单 scope 598s）。

## 1.5 写中途 kill

| 项 | 值 |
|---|---:|
| kill 时已提交 evidence | 1,064 |
| receipts | 1,064 |
| duplicate sources | 0 |
| integrity | ok |
| 子进程 exit | 1 |

说明：

- 父进程 2s 后 terminate；
- 子进程在 `unit_of_work` 内写；
- 重开后 evidence=receipts，无重复来源，integrity ok；
- 这是进程终止烟测，不是断电/fsync 故障注入。

## 1.6 kill 后续跑

| 项 | 值 |
|---|---:|
| kill 后已提交 | 608 |
| 续跑到 | 5,000 |
| receipts | 5,000 |
| evidence | 5,000 |
| source_refs | 5,000 |
| duplicates | 0 |
| integrity | ok |

kill 后从 max(source_seq)+1 续跑，最终每来源 1 条 receipt/evidence，无重复。

## 1.7 多 scope kill 后续跑

| 项 | 值 |
|---|---:|
| scopes | 4 |
| per scope | 2,000 |
| kill 后已提交 | s0 493 / s1 292 / s2 123 / s3 60 |
| resume exits | 0/0/0/0 |
| receipts | **8,000** |
| evidence | **8,000** |
| duplicates | **0** |
| integrity | **ok** |

四个 scope 同时被终止，再各自从 max+1 续跑；最终每个来源 1 条 receipt/evidence。

## 1.8 100k 备份校验

| 项 | 值 |
|---|---:|
| events | 100,000 |
| backup_events | **100,000** |
| backup_integrity | **ok** |

说明：100k 主档生成后 `backup_to`，重新打开备份库计数并 integrity_check；不包含恢复后 query/context。

## 2. 边界

- 10 万只是单进程词面烟测，不是完整主档验收；
- 没有并发读者/写者；
- 没有更新/删除压力；
- 没有向量编码；
- 没有真实查询集；
- 不是 M6 规模验收。
