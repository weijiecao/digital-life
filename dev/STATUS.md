# E5.0-F 工程状态（自动生成，勿手改）

> 由 `python scripts/report.py` 生成于 2026-11-10T22:12:06+08:00；插件版本 `1.0.19`。
> 文档中的用例数、通过率、评估分一律引用本文件，不再手写。

## 一句话

**离线用例 499/499 通过**；场景评估 14/15；集成探针注入 8/8。

**真实环境**：AstrBot 4.29.3 上插件加载成功，1 轮真实请求中 1 轮看到记忆片段（平台 WebChat、provider 为本地 mock）。

**真实 QQ**：NapCat 小号私聊已采集 2 条；最近一次请求体含记忆片段，引用待审序号 3「我喜欢吃苹果」。

仍未做：**主链路真实模型服务**（当前 provider 是本地 mock，回复为固定文本；真实 DeepSeek 仅用于 G3 窄路 A/B）、**用户真实语料**（管线已就绪，见 `docs/REAL_CORPUS.md`）、**L0 三天 soak 数据**。标「替身」的数字不能当作 L0/L1 达标证据；标「real」的见真实环境与真实 QQ 两节。

## 环境事实

| 项 | 值 |
|---|---|
| 操作系统 | Windows-11-10.0.26200-SP0 |
| 离线回归解释器 | Python 3.12.10（C:\Users\205\Desktop\数字生命\testbed\.venv\Scripts\python.exe） |
| 离线环境 torch / sentence-transformers | 已安装 / 已安装 |
| 离线环境 AstrBot | 未安装（用例用替身） |
| 真实环境 | AstrBot 4.29.3 / Python 3.12.10 / torch 2.15.0+cpu / sentence-transformers 5.7.0 |
| 真实模型 | bge-small-zh-v1.5（512 维） |
| model_dir（自检用） | 未配置 |
| 语义检索自检（离线解释器） | 不可用 |

## 测试套件

| 套件 | 用例 | 失败/错误 | 跳过 | 耗时 |
|---|---|---|---|---|
| `tests.test_store` | 162 | 0 | 0 | 15.1s |
| `tests.test_plugin` | 78 | 0 | 0 | 5.8s |
| `tests.test_data_safety` | 4 | 0 | 0 | 0.1s |
| `tests.test_v1_5_consolidator` | 18 | 0 | 0 | 0.3s |
| `tests.test_b4_fixture` | 3 | 0 | 0 | 0.0s |
| `tests.test_b4_reliability` | 11 | 0 | 0 | 0.8s |
| `tests.test_b4_gate` | 6 | 0 | 0 | 0.0s |
| `tests.test_v1_5b_t1_t2_fixes` | 10 | 0 | 0 | 0.0s |
| `tests.test_b4_lease_fencing` | 7 | 0 | 0 | 0.2s |
| `tests.test_b4_provenance` | 8 | 0 | 0 | 0.1s |
| `tests.test_b4_replay_mapping` | 6 | 0 | 0 | 0.1s |
| `tests.test_b4_frozen_fork` | 2 | 0 | 0 | 0.1s |
| `tests.test_b4_unit_of_work` | 11 | 0 | 0 | 0.3s |
| `tests.test_b4_scope_lease` | 13 | 0 | 0 | 1.3s |
| `tests.test_memory_engine_worker` | 3 | 0 | 0 | 0.1s |
| `tests.test_b4_crash_unit` | 2 | 0 | 0 | 0.3s |
| `tests.test_memory_engine_rejudge` | 4 | 0 | 0 | 0.1s |
| `tests.test_memory_engine_engine` | 13 | 0 | 0 | 0.4s |
| `tests.test_memory_engine_cli` | 2 | 0 | 0 | 0.1s |
| `tests.test_memory_index_update` | 3 | 0 | 0 | 0.0s |
| `tests.test_memory_store_backup` | 3 | 0 | 0 | 0.1s |
| `tests.test_memory_engine_host` | 2 | 0 | 0 | 0.1s |
| `tests.test_memory_engine_summary` | 3 | 0 | 0 | 0.1s |
| `tests.test_memory_engine_token_budget` | 2 | 0 | 0 | 0.2s |
| `tests.test_memory_engine_shadow` | 2 | 0 | 0 | 0.1s |
| `tests.test_memory_engine_render` | 2 | 0 | 0 | 0.1s |
| `tests.test_baseline_alignment` | 6 | 0 | 0 | 0.1s |
| `tests.test_preflight` | 6 | 0 | 0 | 0.0s |
| `tests.test_auto_jobs` | 4 | 0 | 0 | 0.2s |
| `tests.test_backup_regression` | 6 | 0 | 0 | 0.6s |
| `tests.test_prewarm` | 5 | 0 | 0 | 1.1s |
| `tests.test_model_judge` | 23 | 0 | 0 | 1.8s |
| `tests.test_delivery` | 13 | 0 | 0 | 1.0s |
| `tests.test_soak` | 11 | 0 | 0 | 9.7s |
| `tests.test_threshold` | 3 | 0 | 0 | 0.3s |
| `tests.test_correction` | 3 | 0 | 0 | 0.1s |
| `tests.test_metrics` | 3 | 0 | 0 | 0.2s |
| `tests.test_real_corpus` | 5 | 0 | 0 | 0.1s |
| `tests.test_background` | 6 | 0 | 0 | 0.3s |
| `tests.test_package` | 3 | 0 | 0 | 0.5s |
| `tests.test_attribution` | 4 | 0 | 0 | 0.0s |
| `tests.test_panel` | 6 | 0 | 0 | 0.3s |
| `tests.test_integration_chain` | 12 | 0 | 0 | 0.4s |
| **合计** | **499** | **0** | | |

## 评估与探针

| 项目 | 结果 | 边界 |
|---|---|---|
| 固定场景评估 | 14/15 符合预期 | 15 项固定夹具；未调用真实模型/平台 |
| 未通过项 | paraphrase_book | 已知短板，保留不自欺 |
| 真实 BGE 场景评估 | 13/15 | 真实编码；失败项见 BGE_CALIBRATION |
| 上游基线检索对齐 | plain 0/2；weak 0/2；semantic_fixture 2/2 | 规则 0/2 是结构性的；semantic_fixture 仅证明管线 |
| 集成探针·注入 | 8/8 轮 | 替身平台与受控编码器 |
| 集成探针·钩子耗时 | P50 1.8ms / P95 5.8ms | 不含真实 BGE 编码 |
| 集成探针·重启 | 积压 8 → 0，重复采集=否 | 同一进程重启，非崩溃恢复 |
| 集成探针·不兼容宿主 | 静默放行=是 | 模拟缺 extra_user_content_parts |

## 真实环境（AstrBot 4.29.3 / WebChat 探针）

| 项 | 结果 |
|---|---|
| 插件加载 | 是 |
| 钩子注册 | add_memory_context(OnLLMRequestEvent)、capture_private(AdapterMessageEvent)、dlm(AdapterMessageEvent) |
| 真实请求中注入可见 | 1/1 轮 |
| 钩子到 provider | 0.09 s |
| 真实 BGE | 512 维 / 加载 10.7 s / 3.3 ms 每条 |
| 后台任务排空 | 0.0 s |
| 模型加载中发消息 | 回复 12 ms；本轮注入=否（语义降级为字面） |
| 平台 / provider | WebChat（QQ 见下一节）/ 本地 mock |

> 这是 real 级框架证据（真实 AstrBot + 真实 BGE + 真实钩子）；provider 仍是本地 mock。详见 `docs/REAL_ENV_REPORT.md`。

## 真实 QQ 链路（NapCat 小号）

| 项 | 结果 |
|---|---|
| 平台 | NapCat 4.18.41 / QQ 9.9.36，反向 WS 连接 `127.0.0.1:6201` |
| 小号 → testbed | 已连接（aiocqhttp 事件 WS） |
| 私聊采集 | 2 条（独立于 WebChat 的作用域） |
| 后台整理 | {"done": 4} |
| 注入 | 可见；引用待审序号 3「我喜欢吃苹果」 |
| 注入时间 | 2026-11-04 08:14:13 |

> QQ 段是**真实平台**（不是 WebChat），但 provider 仍是本地 mock：因此能证明"记忆进入了模型请求"，不能证明回复质量。

## 真实 BGE 校准（M3 第一步）

| 项 | 结果 |
|---|---|
| 首轮「无内容追问」事件过 0.55 | 0/4（最高 0.488） |
| 有内容轮次事件过 0.55 | 3/4 |
| 正确原话分数 | 0.595–0.656 |
| 正确原话过 0.75 | 0/3 |
| 真实 BGE 场景评估 | 13/15（替身 14/15） |

> 结论：事件门槛 0.55 有数据支持；原话门槛 0.75 过严（候选 0.55–0.60，须 100 条评估集验证）；「之前那件事」类追问无语义信号，调阈值无解。详见 `docs/BGE_CALIBRATION.md`。

## M2 评估集基线（自造 120 条）

| 指标 | 无模型 | 真实 BGE | L1 目标 |
|---|---|---|---|
| 正例 R@1 | 29/100 | 65/100 | ≥70% |
| 负例误注入 | 0/20 | 0/20 | ≤5% |
| 纠错（新旧+未确认） | 2/35 | 27/35 | ≥60% |
| 延迟 P50 / P95 | 1.3 / 2.37 ms | 21.96 / 27.97 ms | — |

| **每轮供给模式** | R@1 84/100；供给 105/120；背景-only 39；非背景误注入 0/20；串味 11 |

| 类别 | 无模型 R@1 | 真实 BGE R@1 |
|---|---|---|
| named_event | R@1 1/20 | R@1 10/20 |
| reschedule | R@1 10/20 | R@1 18/20 |
| cancel | R@1 9/15 | R@1 15/15 |
| similar_event | R@1 0/15 | R@1 7/15 |
| cross_day | R@1 9/15 | R@1 15/15 |
| anaphora | R@1 0/15 | R@1 0/15 |
| negative | 误注入 0/20 | 误注入 0/20 |

> 语料为**自造**（非真实聊天），用于回归与阈值扫描，不等于真实准确率。缺口集中：明确事件（短实体被四字门拒绝）、指代追问（自动路径不产生焦点）。详见 `docs/EVAL_SET.md`。

## G3 预研（LLM 判定，13 条虚构用例）

| 指标 | v1 朴素 | v2 受约束 |
|---|---|---|
| 可解析 | 5/6 | 6/6 |
| 无候选正确 | 3/3 | 3/3 |
| 冲突并列 | 0/2 | 2/2 |
| 注入抵抗 | 2/2 | 2/2 |
| 延迟 p50 / p95 | 0.574 / 0.881 s | 0.685 / 1.123 s |
| tokens/次 | 258.5 | 276.0 |

> 结论：**不换主路径**；只加默认关闭的"候选筛选器"（只筛候选 + 必报冲突 + 禁止确认口吻），触发条件与验收门槛见 `docs/G3_LLM_DECISION.md`。

## G3 窄路 A/B（真实 DeepSeek，虚构语料）

| 组 | 结果 | 调用次数 | tokens | 平均延迟 |
|---|---|---|---|---|
| 指代追问（15） | **15/15 命中** | 15 | 3304+462 | 0.78 s |
| 负例（20） | **20/20 正确拒绝** | 3 | 739+80 | 0.63 s |

> 两道确定性闸门：①只对「≤16 字 + 指代词」的查询触发；②选中候选必须与问题有字面重合（纯指代如“后来呢？”除外）。负例 20 条里只有 3 条真正调用模型，其余本地直接拒绝。详见 `docs/G3_LLM_DECISION.md`。

## 规模与恢复（合成数据）

| 项 | 结果 |
|---|---|
| 5,000 条写入 / 整理 | 3.83 s / 1.94 s |
| 模型冷加载 | 11.9 s（产品由预热线程承担） |
| 查询命中 | 50/50 |
| 延迟 P50 / P95 / P99 | 3.99 / 292.58 / 313.59 ms |
| 库大小 / RSS | 31.2 MB / 526.5 MB |
| 崩溃恢复 | 完整性 ok；杀进程时 20 条、重启后 20 条；续写=是；同消息重复=1 |

## 真实语料探针（管线自测：合成样例）

| 指标 | 结果 |
|---|---|
| 正例命中 | 2/2 |
| 负例未误注入 | 1/1 |
| 禁注命中 | 0 |
| 热延迟 P50 / P95 | 0.52 / 2.31 ms |

> 当前只有管线自测；放入 200–500 条脱敏语料后按 `docs/REAL_CORPUS.md` 运行，即可得到用户真实分数。

## 关键证据（缺一即不算完成）

| 能力 | 状态 | 覆盖测试数 |
|---|---|---|
| 备份可删除/可恢复（Windows 回归） | 通过 | 6 |
| 备份校验与隔离恢复 | 通过 | 2 |
| 重启续跑且不重复注入 | 通过 | 2 |
| 钩子契约（模拟宿主） | 通过 | 1 |
| 后台整理死信策略 | 通过 | 1 |
| 依赖与模型自检 | 通过 | 1 |

## L0 自用版验收线

| 验收线 | 目标 | 当前 | 状态 |
|---|---|---|---|
| 采集成功率（真实私聊 3 天） | ≥99% | 无实机数据 | 未测 |
| 注入生效 | ≥95% | 100%（替身） | 仅替身证据 |
| P95 钩子耗时 | <1.5s | 6ms（替身，无 BGE 编码） | 仅替身证据 |
| 备份可生成/校验/恢复/删除 | 全部通过 | 通过 | 单测通过 |
| 重启续跑、无重复注入 | 通过 | 通过 | 探针通过（替身） |
| 真实框架注入（AstrBot 4.29.3） | 可见 | 1/1 轮 / 0.09s | real（WebChat+mock） |
| 真实 QQ 私聊采集与注入 | 可见 | 采集 2 条 / 序号 3 | real-qq（provider=mock） |

## L1 Beta 验收线

| 验收线 | 目标 | 当前 |
|---|---|---|
| 目标原话召回率 R@1 | ≥70% | 未测（缺 100 条评估集；真实 BGE 已就位） |
| 误注入率 | ≤5% | 未测 |
| 纠错场景新旧并列 | ≥60% | 未测 |
| 5,000 条历史 P95 | <2s | 未测（无压测） |
| 新人 30 分钟装好 | 通过 | 未测（无安装包/无实机） |
| 恢复演练 3 次 | 通过 | 离线恢复单测通过；无实机演练 |

## 里程碑与下一步

| 项 | 状态 | 说明 |
|---|---|---|
| M0 P0 备份句柄修复 + 回归 | 完成 | `test_backup_regression` 4 项锁定可删除/可恢复 |
| M0 依赖落盘与启动自检 | 完成 | `requirements.txt` 版本区间 + `preflight.check_environment` |
| M0 测试计数自动化 | 完成 | 本文件由脚本生成 |
| M1 基线继承对齐 | 完成 | 冻结话术 6 项 + 检索对齐报告 |
| M1 钩子契约（L1 离线） | 完成 | 模拟宿主证明链路与签名；**不是实机证据** |
| M1 真实 AstrBot + 真实 BGE（L2） | 完成 | AstrBot 4.29.3 / WebChat / mock provider；见 `docs/REAL_ENV_REPORT.md` |
| M1 NapCat / QQ 实聊（L3） | 完成（平台层） | 真实 QQ 私聊采集 + 注入可见；provider 仍为 mock |
| M4/F1 模型预热（消除首轮阻塞） | 完成 | `Store.prewarm_embedder()` 后台线程加载；加载中语义降级、采集/整理不受阻，`test_prewarm` 5 项锁定 |
| G3 预研（LLM 判定） | 完成 | 13 条虚构用例：v2 约束提示词全对、+0.7 s、276 tokens/次；建议**不换主路径**、只加默认关闭窄路，见 `docs/G3_LLM_DECISION.md` |
| G3 窄路实现（默认关闭） | 完成 | `model_judge.py`：只筛候选+必报冲突+禁止确认口吻；严格 JSON 校验、1.1 s 超时、每会话每小时限流；`test_model_judge` 17 项 |
| G3 窄路 A/B（真实 DeepSeek） | 完成 | anaphora **15/15**、负例 **20/20** 正确拒绝；两道确定性闸门把误注入压到 0 |
| M2 评估集与基线分数 | 基线已建（自造 120 条） | 真实 BGE R@1 41/100、误注入 0/20；真实语料待补，见 `docs/EVAL_SET.md` |

## 复跑

```powershell
cd dev
python scripts/report.py            # 重新生成本文件
python -m unittest -q test_store test_plugin test_baseline_alignment test_preflight test_auto_jobs test_backup_regression test_prewarm test_integration_chain
python evaluation/run_scenarios.py
python evaluation/m2_runner.py
python evaluation/g3_llm_probe.py --prompt v2
python eval_baseline_retrieval.py
python integration_probe.py
python scripts/preflight.py --model-dir "<本机模型目录>"
# 真实环境（需先启动 mock provider，见 testbed/README.md）
cd ../testbed
.venv\Scripts\python.exe harness\real_chain.py --out "..\dev\reports\real_env.json"
```
