# verification — 包级与 UI 验收

本目录保存**针对已构建安装包**运行的验收资产。它们不进入插件运行 ZIP，也不被活动
`dev/tests` 自动执行；统一入口是仓库根目录的 `tools/run_package_regression.py`。

| 路径 | 内容 | 用例数 | 运行对象 |
| --- | --- | --- | --- |
| `plugin/completion_suite.py` | 迁移、双入口、恢复、撤回与兼容输出的集成验收 | 47 | 解压后的候选 ZIP |
| `plugin/test_legacy_plugin_contracts.py` | 上一版插件契约（配置优先级、运行权、数据目录、状态接口） | 20 | 解压后的候选 ZIP |
| `run_core_package.py` | 复用 `dev/tests/` 的 13 个核心测试源 | 137 | 解压后的候选 ZIP |
| `plugin/console_dom_check.cjs` | DOM → 真实 `ConsoleApi`/`ApplicationService` 的 7 条管理路径 | — | 解压后的候选 ZIP（需 jsdom） |
| `plugin/console_browser_check.py` | 可选的真实 Chromium 页面验收 | — | 需 aiohttp + Playwright + Chromium |
| `plugin/console_rpc_fixture.py` | DOM 测试的 RPC 子进程夹具 | — | 同上 |

## 运行

```powershell
python tools/build_unified_plugin.py --out-dir dist --require-clean
python tools/run_package_regression.py --dist dist --suites completion,core,legacy `
  --report dist/package_regression.json --log dist/package_regression.log
```

运行器会：核对 ZIP 与构建清单哈希 → 校验成员路径 → 解压到独立临时目录 → 每个套件在
全新子进程里运行（清除 `PYTHONPATH`）→ 校验所有已加载业务模块都来自解压包 → 汇总
用例数、失败、错误、跳过。缺套件、零用例、超时或非零退出码都算失败。

单套件调试：

```powershell
# 需要先解压候选包
python verification/plugin/completion_suite.py <解压后的插件目录>
$env:DIGITAL_LIFE_CANDIDATE = "<候选 ZIP>"; python -m unittest verification/plugin/test_legacy_plugin_contracts.py
python verification/run_core_package.py --package <解压后的插件目录>
```

## UI / DOM 验证

DOM 套件需要 Node 与 jsdom，依赖固定在 `plugin/package.json` 与
`plugin/package-lock.json`：

```powershell
cd verification/plugin
npm ci
node console_dom_check.cjs <python> <解压后的插件目录>
```

**当前状态（2026-11-16）：DOM 接线通过，浏览器未运行。** 环境为 Node v20.18.1、
npm 10.8.2、jsdom 26.1.0；对象是 0.8.0-dev.2 候选包，7 条管理路径
（迁移创建/批次/放行、备份、校验、恢复、撤回）全部通过，`visual_browser_validation: false`。
结果见 `dev/reports/DOM_WIRING_2026-11-16.md`。

DOM 通过只说明页面事件到真实 `ConsoleApi`/`ApplicationService` 的接线与状态反馈正确，
**不等于**浏览器验收：`console_browser_check.py` 还需要 aiohttp、Playwright 与 Chromium，
本轮未安装、未运行，单独记录。

## 与当前回归的关系

- `dev/tests/`：407 项（源码回归，`python tools/run_regression.py`）。
- 本目录：47 + 137 + 20 = 204 项**包级**用例，针对同一个候选 ZIP。
- 数字不重复计为独立覆盖：131 个核心文件与 `dev/tests/` 同源，47/20 项沿用历史工程包。
- 旧的 20/47 项已适配到当前布局与构建接口，不再调用旧构建器；历史原始文件保留在
  `archive/history/unified_plugin_package/`。
