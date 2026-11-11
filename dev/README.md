# dev — E5.0-F 下游主插件开发工作区

> 基线：`E5.0-F_0.15.116_工程包`（未改动原件，本目录是可写的开发副本）
> 上游：`_e50f_standalone`（已实机验证的 pilot 基线，与桌面 E50F 目录同源）
> 真源清单：`../MANIFEST.md`　进度看板：`STATUS.md`　指标口径：`docs/METRICS.md`
> 更新：2026-11-06（v1.0.19 静默版：移除 /dlm 聊天命令，不新建正式事件）

## 这个目录是什么

按《项目总结 2026-11-03》的结论，开发继续以 **E5.0-F 插件为主线**，不重写、不引入 V16 依赖。
本目录是这条主线的可写工作区，目标是把"能跑"变成"可验证地跑"。

```
dev/
  astrbot_plugin_digital_memory_store/   插件源码（含 store/ 包、commands.py、docs/）
  tests/                                  全部回归用例（538 项）
    test_store.py / test_plugin.py        主体回归（存储、插件钩子、命令面、帮助）
    test_baseline_alignment.py            6 项：上游基线继承
    test_preflight.py                     6 项：依赖/模型自检提示
    test_auto_jobs.py                     4 项：重试上限与死信不阻塞后续
    test_backup_regression.py             4 项：备份可删除/可恢复（P0 回归）
    test_prewarm.py                       5 项：模型预热不阻塞采集/整理（F1）
    test_model_judge.py                   17 项：候选筛选器（G3 窄路，默认关闭）
    test_soak.py                          11 项：L0 soak 采集/增量/汇总/报告
    test_delivery.py                      12 项：配置预设 + 安装/升级/回退 + 安装自检
    test_metrics.py                       3 项：每轮耗时/注入埋点
    test_real_corpus.py                   5 项：真实语料导入 + 探针评分
    test_integration_chain.py             12 项：钩子契约 + 端到端
    test_package.py                       3 项：zip 结构/校验和/可重复构建
    test_attribution.py / test_threshold.py / test_panel.py / test_background.py 等
  sim_astrbot.py                          可观测假 AstrBot（tests/ 从 dev 根导入）
  integration_probe.py                    端到端探针 → reports/integration_chain.json
  eval_baseline_retrieval.py              4 案例检索对齐（规则/语义双路径）
  _schema.py / _dbg_*.py                  一次性调试脚本（可删）
  evaluation/                             原有 15 项场景评估
  fixtures/                               上游冻结语料（8 条话术 + 3 条旧来源）
  scripts/report.py                       唯一数字来源 → STATUS.md / reports/status.json
  scripts/preflight.py                    离线自检 CLI
  scripts/install.py / upgrade.py / rollback.py   安装、升级、回退
  scripts/build_package.py                生成 dist/*.zip + SHA256
  scripts/verify_install.py               安装后自检
  docs/                                   指标口径、实机报告、评估集、上线清单等
  reports/                                评估/探针/状态输出（含 real_env.json / qq_real.json / bge_*.json）
```

`tests/` 里的模块用包名调用：`python -m unittest -q tests.test_store`（在 `dev/` 下执行），
或直接 `python scripts/report.py` 跑全量并刷新 `STATUS.md`。

## 本轮（M0 收尾 + M1 离线层）做了什么

| # | 改动 | 原因 |
|---|---|---|
| 1 | `Store.backup()` 清掉校验残留的 `-wal/-shm`，使备份目录只剩 `.sqlite3` | 10 次备份会产生 20 个旁文件，干扰轮转与人工清理 |
| 2 | `test_backup_regression` 4 项 | P0 修复此前**没有**回归锁：可删除、连续 10 次、可恢复、可删目录 |
| 3 | `requirements.txt` 版本区间 + `preflight.py` 自检 + 启动单条提示 | P1-3：依赖未声明，缺依赖时静默降级 |
| 4 | `sim_astrbot.py` + `test_integration_chain` + `integration_probe.py` | M1 L1：把①层"接口假设"变成可执行断言，并量出钩子耗时/重启行为 |
| 5 | 后台任务 `MAX_JOB_ATTEMPTS=8` + `dead` 状态 + panel 报数 | P2-7：无上限重试导致每 5 分钟空转 |
| 6 | `scripts/report.py` → `STATUS.md` | P1-2：用例数 177/142/225 三次漂移 |
| 7 | `MANIFEST.md`、`docs/METRICS.md`、`docs/baseline_E50F_real.json` | M0 真源清单、指标口径、实机证据记账 |

## 当前状态

**v1.0.19：538 项离线用例全绿；用户聊天面静默（无 /dlm），不新建正式事件；管理走 WebUI panel。**

- 新增 26 项用例（6 自检 + 4 死信 + 4 备份回归 + 12 集成），原 231 项不受影响；
- 集成探针：注入 8/8、同一请求不重复注入、重启续跑无重复采集、不兼容宿主静默放行；
- **真实环境（`testbed/`）**：AstrBot 4.29.3 + Python 3.12 + 真实 BGE（512 维）；
  插件加载成功、钩子以 `OnLLMRequestEvent` 注册、注入片段出现在真实 provider 请求体里
  （钩子→provider 0.13 s）；v1.0.19 起 `/dlm` 不再注册，管理看 panel。详见 `docs/REAL_ENV_REPORT.md`；
- **真实 QQ 私聊**（2026-11-04 08:14）：小号 `3266206281` 经 NapCat 接入，主号 `1508236662` 的两条私聊
  被采集（独立作用域），第二条请求体引用待审序号 3「我喜欢吃苹果」——QQ 段注入可见。
  provider 仍是本地 mock，回复为固定文本；
- 固定场景评估仍是 14/15，`paraphrase_book`（自然改写）未找回，保留不自欺。

真实环境暴露的关键问题（已修复）：**BGE 冷启动约 11–13 s 会占满单线程池**。
现在改为 `Store.prewarm_embedder()` 独立线程预热，加载期间语义路径降级、采集/整理与回复不受阻；
真实环境复测：首条回复 TTFB 8.0 s → **0.017 s**，加载中查询回复 12 ms，加载后首轮注入 89 ms。
`test_prewarm` 5 项锁定该行为。局限：加载窗口内语义检索不可用（详见 REAL_ENV_REPORT F1）。

## 与上游的继承契约（测试锁定）

| 契约 | 断言位置 |
|---|---|
| 语义索引默认 `min_cosine = 0.55` | `test_inherited_thresholds_and_limits` |
| 单条采集上限 4000 字 | 同上 |
| 8 条话术 = 4 案例（3/2/2/1） | `test_frozen_fixture_structure_and_hashes` |
| 旧来源必须先于新消息 | `test_old_fixture_predates_new_messages` |
| held-out 细节不得出现在新线索 | `test_held_out_details_never_appear_in_new_clues` |
| 单会话、连续 seq、同事务任务 | `test_capture_accepts_baseline_script_in_one_scope` |

## 基线检索对齐结果

`python eval_baseline_retrieval.py`，同一批 3 条旧来源 + 4 个案例：

| 模式 | 召回 | 误召回 | held-out 泄漏 |
|---|---|---|---|
| plain / plain_followup / weak / weak_followup（规则） | 0/2 | 0/2 | 0 |
| semantic_fixture（受控 4 维替身） | 2/2 | 0/2 | 0 |

**结论**：
1. 规则路径 0/2 是**结构性的**——上游线索刻意不提事件名（"之前那件事"），字面匹配无从下手；
2. 语义管线接线正确：换受控编码器后 2/2，负例保持空，held-out 不泄漏；
3. 因此**真实 BGE 是硬依赖，不是增强项**：没有模型，下游无法复现上游基线。

> 受控替身只证明"管线通"，不证明"认得准"；准确率仍需真实 BGE。

## 还没做的（按顺序）

1. **真实模型服务**：G3 窄路已实现并做了真实 DeepSeek A/B（指代追问 15/15、负例 20/20 拒绝），
   默认关闭；下一步是在 200–500 条**真实**语料上复核，防止闸门参数过拟合自造集。
2. **真实语料评估集**（M2 后续）：现有 120 条是自造样本，需 200–500 条脱敏真实语料做外部效度。
3. **BGE 阈值重标**（M3）：0.75 原话阈值过严（正确原话 0.60–0.66），候选 0.55–0.60；
   须在评估集上同时看 R@1 与误注入率。
4. **交付硬化**（M5）：安装/升级/回退脚本、29 个开关收敛成 3 个预设。
5. **规模与恢复**（M4）：5,000 条压测、进程 kill 后的崩溃恢复、NapCat 断线重连。
6. **加载窗口优化**：目前是"首条消息触发预热"，可改为"插件加载即预热"，进一步缩短无记忆窗口。

## 运行

```powershell
cd dev
python scripts/report.py            # 跑全量 + 生成 STATUS.md（约 30 s）
python -m unittest -q tests.test_store tests.test_plugin tests.test_baseline_alignment tests.test_preflight tests.test_auto_jobs tests.test_backup_regression tests.test_prewarm tests.test_model_judge tests.test_integration_chain
python scripts/preflight.py --model-dir "<本机模型目录>"

# 真实环境（先按 testbed/README.md 启动 mock provider）
cd ../testbed
.\.venv\Scripts\python.exe harness\real_chain.py --out "..\dev\reports\real_env.json"
```

> 说明：`reports/*.json` 都是可重生成的产物；`testbed/` 可整目录删除后按 README 重建。
