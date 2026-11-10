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

## 2. 边界

- 10 万只是单进程词面烟测，不是完整主档验收；
- 没有并发读者/写者；
- 没有更新/删除压力；
- 没有向量编码；
- 没有真实查询集；
- 不是 M6 规模验收。
