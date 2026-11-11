# Memory Engine 阶段验收矩阵（2026-11-11）

- 状态：**NO-GO（R0/R1 均未通过）**
- 全量测试：529/529
- 远端提交：`998662c`
- CI：Windows 3.10 / 3.11 / 3.12 全绿
- 真实聊天语料：0
- 真实宿主：AstrBot 4.29.3 testbed

## 1. M0 基线

| 验收项 | 证据 | 状态 |
|---|---|---|
| 版本/提交冻结 | manifest、本地/远端提交 | ✅ |
| 全量回归 | 529/529 | ✅ |
| 关键源文件 SHA | manifest | ✅ |
| 契约/CI | Windows CI 三版本 | ✅ |

## 2. M1 一致性

| 验收项 | 证据 | 状态 |
|---|---|---|
| 原子领取/token | lease、双进程 | ✅ |
| 双进程同批竞争 | 最终 DB | ✅ |
| 不同来源 handoff | 子进程 | ✅ |
| 提交前/后 kill | 子进程 os._exit | ✅ |
| pending 重评 | 注入 + 真实模型单条 | ✅ |
| 真实模型 21 例 | 三次 20/19/20 | ◐ |
| 340 trace | 三次 F1 0.629–0.655 | ◐ |
| 部分批次失败保留 | 第 2 条 READY 重试 | ✅ |

## 3. M2 数据

| 验收项 | 证据 | 状态 |
|---|---|---|
| 21 例三次 | 20/19/20 | ◐ |
| 340 三次 | F1 0.62857/0.65358/0.65546，FP0 | ◐ |
| 簇级 | 0 mixed | ✅ |
| 来源污染 | 0/311 | ✅ |
| 冻结输入 SHA | m2_frozen_manifest | ✅ |
| 独立真实 gold | 无 | ❌ |

## 4. M3 引擎

| 验收项 | 证据 | 状态 |
|---|---|---|
| ingest/retrieve/context/delete | API/CLI | ✅ |
| 更正/撤回 | 单步/多步 | ✅ |
| 历史链 | history API/CLI | ✅ |
| 真实宿主 shadow 采集 | AstrBot testbed | ✅ |
| 生产宿主采集 | 无 | ❌ |

## 5. M4 检索

| 验收项 | 证据 | 状态 |
|---|---|---|
| 词面/BGE | R@1 0.7812/0.7937 | ◐ |
| abstain | 默认 1.0；min_score 1.0 → 0 | ◐ |
| 阈值 dev/holdout | holdout 91/91、0 注入 | ◐ |
| 分 scope | 10/10 稳定 | ◐ |
| 真实查询集 | 无 | ❌ |

## 6. M5 上下文

| 验收项 | 证据 | 状态 |
|---|---|---|
| ContextPacket | ✅ | ✅ |
| 摘要 | 抽取式、保留否定 | ✅ |
| 精确 tokenizer | tiktoken | ✅ |
| 宿主总预算 | HostBudget | ✅ |
| final renderer | fit_packet | ✅ |
| 持久缓存 | 四进程共享 | ✅ |
| prune/TTL/janitor | 状态+锁+stale | ✅ |
| 宿主总预算 | 无 | ❌ |

## 7. M6 规模/生命周期

| 验收项 | 证据 | 状态 |
|---|---|---|
| 100k 读/写 | p95/100k | ✅ |
| 100k 备份 | 100k/integrity | ✅ |
| kill 续跑 | 20k/100k | ✅ |
| 多 scope kill 续跑 | 4×25k，0 重复 | ✅ |
| 写锁缺陷 | busy_timeout + BEGIN/release 重试 | ✅ |
| 自动备份 | interval/retention/state/lock | ✅ |
| 断电 fsync | 无 | ❌ |

## 8. M7 接入

| 验收项 | 证据 | 状态 |
|---|---|---|
| 真实 AstrBot 启动 | 4.29.3 | ✅ |
| 真实消息链 | OnLLMRequest + 注入 | ✅ |
| Memory Engine shadow | 5 次连续，0 失败 | ✅ |
| 回退 | 移出 shadow 后 V1 正常 | ✅ |
| 72h / 真实 QQ | 无 | ❌ |

## 9. 发布结论

| 层级 | 结论 |
|---|---|
| R0 独立离线文本引擎 | **NO-GO**：真实 gold/宿主总预算/断电未完成 |
| R1 目标宿主 | **NO-GO**：无真实 QQ/72h |
| Shadow/生产 | **禁止** |

## 10. 主要阻塞

1. 真实脱敏语料与独立 gold 缺失；
2. M4 阈值来自合成 B4；
3. M6 无断电级 fsync；
4. M7 无真实 QQ/NapCat 72h；
5. host 总预算未冻结。
