# Contributing

本仓库同时维护数字记忆基座、引擎、统一 AstrBot 插件、构建工具与验收文档。
**本仓库是唯一源码来源**；分发仓库只能由 `tools/export_astrbot_plugin.py` 单向导出。

## 原则

1. 不自动确认事实；注入内容保持"未确认"语义；
2. 新能力默认关闭，不默认联网；
3. 无真实证据不写准确率；离线结果与真实宿主结果分开写；
4. 平台相关结论必须附 `dev/reports/` 或 `dist/` 下的原始输出；
5. 不提交 `.venv`、模型权重、AstrBot 实例数据、SQLite 运行库、凭据或私人会话。

## 本地开发

```powershell
python -m pip install -r dev/requirements-release.txt
python tools/sync_version.py --check
python tools/run_regression.py
python tools/run_package_regression.py --dist dist --suites completion,core,legacy
```

`dist/regression.json` 是数字的唯一来源；不要把它手抄到文档里。
只跑单个文件：`cd dev; python -m unittest -q tests.test_unified_plugin`。

## 改动位置

| 改动 | 位置 |
| --- | --- |
| 插件入口、应用服务、管理 UI | `dev/astrbot_plugin_digital_life/` |
| 输入、实时追加、历史对账 | `dev/astrbot_memory_input/` |
| 召回、准入、上下文、预算 | `dev/memory_engine/` |
| 输出协议与注入 | `dev/memory_output/` |
| 基座、Resolver、租约 | `dev/v1_5_consolidator/` |
| 控制台 | `dev/dlm_console/` |
| 构建、验包、导出 | `tools/` |
| 质量与恢复验收 | `dev/evaluation/` |
| 当前验收证据 | `dev/reports/` |
| 文档 | `README.md`、`docs/`、`dev/README.md`、模块 README |

活动代码不得 `import archive`，也不得把 `archive/` 加进 `sys.path`；
需要旧实现时把它迁回 `dev/` 并补测试。

## 打包与验包

```powershell
python tools/build_unified_plugin.py --out-dir dist --require-clean
python tools/verify_unified_plugin.py --dist dist --report dist/package_verification.json
python tools/verify_unified_plugin.py --dist dist --self-test
# 有 AstrBot 源码时再加两项真实宿主导入/归档校验
python tools/verify_unified_plugin.py --dist dist --host-path testbed/AstrBot-4.29.3
python tools/check_astrbot_install.py --dist dist --astrbot-root testbed/AstrBot-4.29.3
python tools/check_repository.py
```

版本只改仓库根 `VERSION`，然后执行 `python tools/sync_version.py`；CI 用 `--check` 拒绝漂移。
字节发生变化时必须使用新的候选标识，不得覆盖 `releases/` 里已有的交付物。

## 分发导出

```powershell
python tools/export_astrbot_plugin.py --output dist/plugin_export
python tools/export_astrbot_plugin.py --check dist/plugin_export
```

分发仓库里的修复必须回到本仓库完成，再重新导出。

## 提交

- 使用 `feat:` / `fix:` / `docs:` / `test:` / `chore:`；
- 一个 PR 做一件事；
- 改行为必须有测试，且跑过 `tools/run_regression.py`；
- 平台/真实环境结论必须附原始输出，不用口头说明代替。

## 不接受

- 把测试 skip 当通过，或用跳过让安装包检查变绿；
- 未验证的"生产可用"结论；
- 把模型权重或用户真实聊天入库；
- 直接修改冻结标签或在归档脚本里加路径绕回来让验收通过；
- 在文档里写当前代码不支持的入口。

License: MIT.
