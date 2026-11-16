# 历史归档

本目录保存已经停止维护的实现、工具和材料，只作查阅与追溯，**不被活动代码、CI
或测试导入**。活动实现只有 `dev/`，交付包只有 `releases/` 的当前候选。

```text
archive/
  plugins/                            旧插件源码（只读对照）
    astrbot_plugin_digital_memory_store/
    astrbot_plugin_memory_shadow/
  releases/                           旧插件/引擎发布包及其清单、校验和
  tests/                              旧插件相关测试（不纳入活动回归）
  scripts/                            旧构建、安装、校验脚本（入口已失效）
  docs/                               停止维护的安装与验收文档
  history/                            工程以外的历史材料（见下）
```

## 各目录说明

| 路径 | 内容 | 状态 |
| --- | --- | --- |
| `plugins/astrbot_plugin_digital_memory_store/` | 数字记忆基座旧插件（v1.0.19 线） | 已停止维护；被 `dev/astrbot_plugin_digital_life/` 取代 |
| `plugins/astrbot_plugin_memory_shadow/` | 旧 shadow 插件 | 已停止维护；不得与统一插件同时启用 |
| `releases/` | `astrbot_plugin_digital_life-0.7.0-dev.2`、`astrbot_plugin_digital_memory_store-1.0.19`、`memory-engine-rc3-queryguard`、`memory-engine-r0-rc3` 及其清单 | 保留原字节，用于历史追溯，不再是当前交付物 |
| `tests/` | 旧插件、shadow、旧运行包相关测试 | 保留原样；不保证在当前树上可运行 |
| `scripts/` | `build_astrbot_plugin.py`、`build_package.py`、`build_r0_bundle.py`、`install.py`、`rollback.py`、`sync_console_view.py`、`verify_install.py`、`verify_r0_bundle.py`、`package_lifecycle.py`、`upgrade.py` | 只针对归档插件或 RC3 运行包；**活动工具不得用路径绕回这些脚本** |
| `docs/` | 1.0.19 安装与本地安装测试、`METRICS`/`MEMORY_VERIFICATION_PLAN` 计划、RC 说明 | 与当前 0.7.0-dev.2 无关 |
| `history/e5_0_f/` | E5.0-F 交接包、路线规划、核查报告与桌面材料清单 | 项目历史证据 |
| `history/legacy_installers/` | `configure_env_1.0.17～1.0.19.ps1`、旧 `INSTALL_*.md`、`_rc2_verify*.log` | 旧安装脚本与一次性日志 |
| `history/unified_plugin_package/` | 上一版统一插件工程包的专项测试与构建脚本 | 功能已归集到 `dev/tests/test_unified_plugin.py` 与 `tools/`，原文件仅作对照 |

## 归档与活动的关系

- 活动代码不得 `import archive`，不得把 `archive/` 加入 `sys.path`；
  `python tools/check_repository.py` 会检查这一点。
- 归档测试与归档脚本不参与 `dev/tests` 回归，也不由 CI 执行。
- 归档发布包只作为字节记录：`python tools/check_repository.py` 会校验
  `archive/releases/*.json` 里的 SHA-256 与字节数是否仍与 ZIP 一致。
- 需要重新启用某个归档实现时，应另开功能任务把它迁回 `dev/`，补测试并更新文档，
  而不是在活动脚本里跨目录引用。

## 历史快照

- `history/unified_plugin_package/test_unified_plugin_v0_7_0.py`：上一版工程包的统一插件测试，
  面向当时的 `shared/` 目录布局，已在当前树上重写为 `dev/tests/test_unified_plugin.py`。
- `history/unified_plugin_package/build_unified_plugin_v0_7_0.py`：上一版构建脚本，
  硬编码 `shared/` 布局与 ZIP 时间戳；当前实现见 `tools/build_unified_plugin.py`。
- `history/unified_plugin_package/completion_suite.py`、`console_dom_check.cjs`、
  `console_browser_check.py`、`console_rpc_fixture.py`：离线闭环与浏览器验收资产。
  当前仓库未纳入这些浏览器依赖；真实页面验收仍列为未完成项。
