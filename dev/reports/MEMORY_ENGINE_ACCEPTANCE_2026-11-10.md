# Memory Engine 阶段验收矩阵（2026-11-10）

- 状态：**NO-GO（R0/R1 均未通过）**
- 全量测试：487/487
- CI：Windows 3.10 / 3.11 / 3.12 全绿
- 真实聊天语料：0
- 340 provenance trace：2 次
- 21 例：2 次

## 1. M0 基线

| 验收项 | 证据 | 状态 |
|---|---|---|
| 版本/提交冻结 | manifest、本地/远端提交 | ✅ |
| 全量回归 | 487/487 | ✅ |
| 关键源文件 SHA | manifest | ✅ |
| 契约冻结 | 部分：provenance/映射/receipt | ◐ |

## 2. M1 一致性

| 验收项 | 证据 | 状态 |
|---|---|---|
| 原子领取/token | lease 测试、双进程 | ✅ |
| 双进程同批竞争 | 最终 DB 对比 | ✅ |
| 不同来源 handoff | 子进程测试 | ✅ |
| 提交前 kill | 子进程 os._exit | ✅ |
| 提交后 cursor 前 kill | 子进程 os._exit | ✅ |
| scope fencing | unit/job commit | ✅ |
| receipt/manifest | 方法+回放 | ✅ |
| pending 重评 | 注入 provider + 真实模型单条 | ◐ |
| 真实模型 21/340 稳定性 | 两次运行 | ◐ 波动 |
| Windows CI | GitHub Windows 全绿 | ✅ |

## 3. M2 数据

| 验收项 | 证据 | 状态 |
|---|---|---|
| 21 例两次 | 20/21、19/21 | ◐ |
| 340 trace 两次 | F1 0.62857、0.65358；FP0 | ◐ |
| 簇级 | strict 203/197 pure，0 mixed | ✅ |
| Guard 策略消融 | strict vs allow_user_real | ✅ |
| 独立真实 gold | 无 | ❌ |
| 真实脱敏留出集 | 无 | ❌ |

## 4. M3 独立引擎

| 验收项 | 证据 | 状态 |
|---|---|---|
| ingest/retrieve/context/delete | API 测试 | ✅ |
| CLI | CLI 测试 | ✅ |
| 类型投影 | plan/preference/experience/story | ◐ |
| 更正/撤回 | revise/withdraw | ✅ |
| 自动采集宿主 | 无 | ❌ |

## 5. M4 检索

| 验收项 | 证据 | 状态 |
|---|---|---|
| 同 ID 索引更新 | 词面/向量测试 | ✅ |
| 词面质量 | R@1 0.7812、R@5 1.0、MRR 0.8891 | ◐ |
| expected-abstain | 默认 1.0；min_score 1.0 → 0 | ◐ |
| 混合检索 | 未运行 BGE | ❌ |
| 真实查询集 | 无 | ❌ |

## 6. M5 上下文

| 验收项 | 证据 | 状态 |
|---|---|---|
| ContextPacket | API | ✅ |
| 缓存失效 | ingest/delete | ✅ |
| 保守 Token 预算 | 4 chars/token | ✅ |
| 精确 tokenizer | tiktoken | ✅ |
| 摘要 | 抽取式、保留否定 | ✅ |
| 摘要缓存 | 同文本复用 | ✅ |
| 跨进程/持久缓存 | 无 | ❌ |

## 7. M6 规模/生命周期

| 验收项 | 证据 | 状态 |
|---|---|---|
| 一致性备份 | backup API + integrity | ✅ |
| 删除清单重放 | tombstone | ✅ |
| 100k 词面 | p95 459ms | ◐ |
| 100k 并发读 | p95 0.766ms | ◐ |
| 写入/删除并发 | 无 | ❌ |
| auto backup | 无 | ❌ |

## 8. M7 接入

| 验收项 | 证据 | 状态 |
|---|---|---|
| 模拟宿主 | SimHostAdapter | ✅ |
| 重复消息幂等 | 测试 | ✅ |
| 离线 shadow | ShadowRunner | ✅ |
| 真实 AstrBot/NapCat | 无 | ❌ |
| 72h 观察/回退 | 无 | ❌ |

## 9. 发布结论

| 层级 | 结论 |
|---|---|
| R0 独立离线文本引擎 | **NO-GO**：M5/M6/M7 未完整，真实 gold 缺失 |
| R1 目标宿主 | **NO-GO**：未接入真实宿主 |
| 生产/Shadow | **禁止** |

## 10. 主要阻塞

1. 真实脱敏语料与独立 gold 缺失，无法决定 unknown provenance 策略；
2. M1 真实模型 21/340 仍有波动，未达稳定门槛；
3. M4 无混合检索与真实查询集；
4. M6 无写入/删除并发、自动备份；
5. M7 无真实宿主影子与 72h 观察。

## 11. 现有资产的正确用法

- 可作为离线研究/回归基线；
- 不能作为生产准确率或 Shadow 许可；
- 不能把 487 测试等同于引擎验收。
