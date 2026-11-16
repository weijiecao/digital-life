# 历史材料索引

这里保存不属于活动实现、但需要追溯的工程材料。运行时代码、CI 和测试都不读取本目录。

| 目录 | 内容 | 来源 |
| --- | --- | --- |
| `e5_0_f/` | E5.0-F 交接包、路线与进度规划、v0.15.116 工程进度核查报告、桌面工程包清单（`MANIFEST.md`）、桌面 `E5.0-F_0.15.116_工程包/`（只读交付基线） | 2026-11 桌面工作区 |
| `legacy_installers/` | 旧安装脚本 `configure_env_1.0.17～1.0.19.ps1`、旧 `INSTALL_*.md`、`_rc2_verify*.log` | 旧插件 1.0.x 线 |
| `unified_plugin_package/` | 上一版统一插件"工程包"的专项测试、离线闭环与浏览器脚本、旧构建脚本、Alpha 状态说明 | 0.7.0-dev.2 之前的工程包 |

## 使用规则

- 这些文件是**证据与对照**，不是可用入口；不要把它们加入 `sys.path`，也不要让活动脚本调用它们。
- `unified_plugin_package/` 里的测试已被 `dev/tests/test_unified_plugin.py` 重写，
  能力映射见 [`../../dev/reports/SPECIALIZED_COVERAGE.md`](../../dev/reports/SPECIALIZED_COVERAGE.md)。
- `e5_0_f/` 里的 `MANIFEST.md` 描述的是桌面工作区的多个工程包；其中的路径只在那台机器上成立。
- 需要重新启用其中某个实现时，按 `archive/README.md` 的规则迁回 `dev/`，补测试并更新文档。
