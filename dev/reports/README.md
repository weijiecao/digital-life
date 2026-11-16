# Reports index

本目录保存**当前验收证据**、**历史报告快照**，以及仍被测试使用的冻结数据。
数字只以 `current_acceptance.json` 及其引用日志为准；历史报告里的数字是当时快照，
不代表当前状态。

## 当前验收

| 文件 | 内容 |
| --- | --- |
| `current_acceptance.json` | 当前验收记录（schema 2）：被测提交、整体版本、包哈希、各套件（源码/包级）、host 层级、一致性问题 |
| `acceptance/<提交>/` | 持久证据：`summary.json`、`index.json` 与小型报告副本（回归、包级用例、构建清单、验包、负例、导出清单） |
| `SPECIALIZED_COVERAGE.md` | 能力 → 测试文件 → 用例 → 运行环境的映射，以及明确的缺口 |
| `M0_BASELINE_MANIFEST.json` | 2026-11-11 的 M0 基线（旧版本、旧哈希），只作历史对照 |
| `REPOSITORY_ORGANIZATION_2026-11-15.md` | 本轮仓库整理记录：改动、证据、偏差与计划 §8 清单状态 |
| `DISTRIBUTION_SYNC_2026-11-16.md` | 分发仓库逐文件比较结果、三种导出布局与首次导出方案 |

活动日志由命令生成在 `dist/`（Git 忽略）：`dist/regression.json`、`dist/regression.log`、
`dist/build_manifest.json`、`dist/package_verification.json`、`dist/selftest.json`、
`dist/astrbot_install_check.json`、`dist/package_regression.json`、`dist/release_set.json`、
`dist/plugin_export/`、`dist/release/`。`current_acceptance.json` 由 `python tools/record_acceptance.py` 从这些文件生成，
并交叉核对“同一提交、同一包哈希、每个套件都有结果”。缺失报告或哈希不一致会
写入 `problems` 且 `ok=false`，不要手改这个文件。

## 当前工程报告

| 文件 | 内容 |
| --- | --- |
| `ENGINEERING_REPORT_2026-11-11.{md,docx,pdf}` | 2026-11-11 的工程报告（含当时 590 项回归与旧插件结论） |
| `FINAL_HANDOFF_2026-11-11.md` | 当时的交付交接 |
| `Q1_INTEGRATION_2026-11-12.md` | Q1 集成、质量、包与 GO/NO-GO |
| `MEMORY_ENGINE_ACCEPTANCE_2026-11-10.md` | 当时的验收矩阵 |
| `MEMORY_BASE_OUTPUT_0_1_2026-11-12.md` | 输出模块协议、证据复验与注入验证 |
| `MEMORY_BASE_OUTPUT_0_1_INTEGRATION_2026-11-12.md` | 输出模块并入 dev 与宿主接线 |
| `NATIVE_INPUT_0_2_2026-11-12.md` | 原生输入 v0.2 与宿主接线 |
| `REVIEW_FIXES_63e61de_2026-11-13.md`、`REREVIEW_29A1FE6_FIX_2026-11-13.md`、`REREVIEW_CB3921E_FIX_2026-11-13.md`、`REREVIEW_CB3921E_FIX_R05_2026-11-13.md` | 各轮复审修复记录（含当时的用例数） |
| `0.7.0-dev.2/SQLITE_FIX.md` | dev.2 的 SQLite 连接释放修复说明 |

上述报告描述的是当时提交下的行为；其中引用的 `dev/scripts/build_r0_bundle.py`、
`verify_r0_bundle.py`、`tests/…` 等路径现在位于 `archive/` 或已被替代。**报告正文不改写**，
因为它记录的是当时的证据；当前命令以仓库根 `README.md` 与 `docs/MAINTAINER_FLOW.md` 为准。

## 历史快照

| 文件 | 说明 |
| --- | --- |
| `history/STATUS_2026-11-11.md` | 旧的自动生成状态文件（590 项、旧插件 1.0.19）。原 `dev/STATUS.md` 位置改为指向本文件与当前状态 |
| `q1_results/` | Q1 的 challenge、quality-v2 known fail、quality-v3、lifecycle 结果 |
| `rc3_results/` | RC3 的质量与闭环证据 |
| `v1_5b_b4_cluster_prov_trace.json` | 仍被 R0-D 测试使用的冻结 trace（**运行依赖，勿删除**） |

## 仍被测试使用的数据

| 路径 | 使用方 |
| --- | --- |
| `dev/reports/v1_5b_b4_cluster_prov_trace.json` | `dev/tests/test_r0d_replay.py`、`test_r0d_crash.py` |
| `dev/fixtures/*.json` | `dev/tests/test_b4_*`、`test_evidence_admission.py` |
| `dev/evaluation/quality_v2`、`quality_v3`、`b4_testdata` | 质量与恢复验收脚本（需 BGE 目录，不进入离线回归） |

移动这些文件必须先登记路径与内容哈希，并同步修改引用；不能只移动文件。
