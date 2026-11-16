# 专项回归映射（能力 → 测试文件 → 用例 → 运行环境）

本文件回答一个问题：文档里承诺的能力，究竟由哪个测试、在哪个环境验证。
旧工程包（`archive/history/unified_plugin_package/`）的专项测试不在此运行，
但同一批场景已在活动套件中重写或对照覆盖。

运行环境缩写：
- **offline**：`python tools/run_regression.py`（Windows / Ubuntu，Python 3.10–3.12，无 AstrBot、无模型）
- **stub**：`python tools/verify_unified_plugin.py` 用标注清楚的宿主 stub 导入插件模块，结果只算离线通过
- **real**：真实 AstrBot/NapCat/Windows 实机，**本轮未执行**

| 能力 | 测试文件 | 关键用例 | 环境 | 结果 |
| --- | --- | --- | --- | --- |
| 旧数据迁移（预览与安全边界） | `dev/tests/test_unified_plugin.py` | `MigrationPreviewTests.test_inventory_counts_unlinked_messages`、`test_rows_report_identity_and_withdrawal`、`test_rows_paginate_by_watermark`、`test_non_legacy_database_is_rejected` | offline | 通过 |
| 旧数据迁移（首次导入/续跑/幂等落库） | `dev/tests/test_unified_plugin.py` | `MigrationWritePathTests.test_full_migration_imports_holds_and_releases`、`test_batch_resumes_after_interruption`、`test_repeat_batch_does_not_duplicate_receipts`、`test_source_database_is_unchanged`、`test_mapping_cannot_be_rebound_after_use`、`test_unmapped_scopes_never_reach_the_queue` | offline | 通过 |
| 实时追加 | `dev/tests/test_unified_plugin.py` | `RealtimeJournalTests.test_same_identity_appends_once`、`test_changed_content_for_the_same_identity_conflicts`、`test_append_requires_a_stable_message_id`、`test_history_without_identity_waits_when_realtime_overlaps` | offline | 通过 |
| 历史对账 | `dev/tests/test_astrbot_memory_input.py`、`dev/tests/test_native_input_host.py` | 前缀/身份对账、冲突与待定位（原套件） | offline | 通过 |
| 恢复切换 | `dev/tests/test_unified_plugin.py` | `GenerationTests.test_restore_switches_generation_and_commits`、`test_crash_after_switch_rolls_back_on_startup`、`test_generation_paths_cannot_escape` | offline | 通过 |
| 备份一致性 | `dev/tests/test_unified_plugin.py` | `GenerationTests.test_backup_is_self_describing_and_verified`、`test_backup_rejects_tampered_bytes`、`test_backup_rejects_an_unknown_id` | offline | 通过 |
| 撤回 | `dev/tests/test_unified_plugin.py` | `RealtimeJournalTests.test_withdrawn_source_is_not_delivered` | offline | 通过 |
| 运行权（同目录单实例） | `dev/tests/test_unified_plugin.py` | `OwnershipTests.test_lock_is_exclusive_inside_one_process`、`test_registry_reports_an_active_legacy_star`、`test_registry_answer_wins_over_loaded_module`、`test_manager_fallback_detects_loaded_plugin`、`test_module_fallback_is_the_last_resort` | offline | 通过 |
| 输出 v2 协议 | `dev/tests/test_unified_plugin.py`、`dev/tests/test_memory_output.py` | `OutputContractTests.test_schema_version_is_v2`、`test_generation_and_retrieval_query_are_validated`；渲染/预算由原套件覆盖 | offline | 通过 |
| SQLite 生命周期 | `dev/tests/test_unified_plugin.py`、`dev/tests/test_memory_store_backup.py` | `GenerationTests.test_managed_database_closes_on_success_and_failure`（成功与异常都释放句柄，Windows 下可重命名） | offline（Windows 本地） | 通过 |
| 管理 UI / API 面 | `dev/tests/test_unified_plugin.py` | `ConsoleApiTests.test_routes_cover_management_and_migration`、`test_status_handler_reports_the_runtime_state` | stub | 通过 |
| 包级结构（metadata、schema、静态资源、导入） | `tools/verify_unified_plugin.py` | 16 项检查：成员安全、逐文件哈希、必需文件、`metadata`、`_conf_schema`、UI 资源、编译、共享包导入、插件导入 | stub / astrbot-source | 通过 |
| 真实 AstrBot 源码导入 | `tools/verify_unified_plugin.py --host-path` | 同一份 16 项检查，插件模块由 AstrBot 4.29.3 真实源码导入（`host: astrbot-source`） | real（源码） | 通过 |
| 宿主上传前归档校验 | `tools/check_astrbot_install.py` | AstrBot `_PluginUpdater.inspect_plugin_archive` 识别 metadata 入口与 name/version | real（源码） | 通过 |
| 包级负例 | `tools/verify_unified_plugin.py --self-test` | 缺文件、篡改内容、路径穿越、重复成员都会被拒绝 | offline | 通过 |
| 浏览器页面实际操作 | 无 | 依赖 jsdom/Playwright，本仓库未纳入；资产见 `archive/history/unified_plugin_package/` | — | **未运行** |
| 真实 AstrBot / NapCat / Windows 实机 / 72h | 无 | `testbed/` 有 harness，但没有本轮结果 | `real` | **未运行** |

## 明确的缺口

1. **真实旧库迁移演练**：写路径已用合成旧库夹具覆盖，但还没有在真实旧库（含真实分组、摘要、
   撤回记录）上演练过一次。下一轮应把脱敏旧库样本放进 `dev/fixtures/` 并重跑同一组用例。
2. **管理 UI 的浏览器验收**：只验证了路由与状态返回，未验证点击、错误提示与页面加载。
3. **真实宿主实例**：已完成 AstrBot 4.29.3 源码导入与归档校验，但未启动实例；所有权、注入、
   模型调用与 72h 长跑仍需按 `testbed/README.md` 单独记录。

## 数字来源

所有"通过"都来自 `dist/regression.json`（由 `tools/run_regression.py` 写出）和
`dist/package_verification.json`（由 `tools/verify_unified_plugin.py` 写出）；
汇总见 `dev/reports/current_acceptance.json`。旧工程包的 333/204 等数字不再作为当前基线。
