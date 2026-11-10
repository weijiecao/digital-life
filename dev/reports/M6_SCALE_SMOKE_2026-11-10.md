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

| 项 | 值 |
|---|---:|
| scopes | 4 |
| per scope | 2,500 |
| sources | 10,000 |
| workers | 4 |
| wall | 16.98 s |
| p95 | 2.215 ms |
| errors | 0 |
| evidence | 10,000 |
| receipts | 10,000 |
| integrity | ok |

多 scope 可并行申请不同租约；SQLite 仍是单写者，但比单 scope 串行 10k（42.8 s）快。

## 2. 边界

- 10 万只是单进程词面烟测，不是完整主档验收；
- 没有并发读者/写者；
- 没有更新/删除压力；
- 没有向量编码；
- 没有真实查询集；
- 不是 M6 规模验收。
