# Contributing

本仓库同时维护独立 数字记忆引擎、验收脚本和 AstrBot 插件源码。

## 原则

1. 不自动确认事实；注入内容保持“未确认”语义；
2. 新能力默认关闭，不默认联网；
3. 无真实证据不写准确率；
4. 平台相关结论必须附 `dev/reports/` 或测试输出；
5. 不提交 `.venv`、模型权重、AstrBot 实例数据或 SQLite 运行库。

## 本地开发

```powershell
cd dev
python -m pip install -r requirements-release.txt
python -m unittest discover -q -s tests -t .
```

当前基线：590 项；Windows/Ubuntu CI 覆盖 Python 3.10/3.11/3.12。

## 改动位置

| 改动 | 位置 |
|---|---|
| 引擎 | `dev/memory_engine/` |
| 事件/证据/Resolver | `dev/v1_5_consolidator/` |
| AstrBot 插件 | `dev/astrbot_plugin_digital_memory_store/` |
| 质量/恢复验收 | `dev/evaluation/` |
| 当前报告与证据 | `dev/reports/` |
| 打包/校验 | `dev/scripts/` |

历史报告已清理；需要时由脚本重生成或从 Git 历史取回。当前交付说明见
`dev/reports/Q1_INTEGRATION_2026-11-12.md`。

## 提交

- 使用 `feat:` / `fix:` / `docs:` / `test:` / `chore:`；
- 一个 PR 做一件事；
- 改行为必须有测试；
- 平台/真实环境结论必须附原始输出，不用口头说明代替。

## 打包

```powershell
python dev/scripts/build_r0_bundle.py --out releases/memory-engine-rc3-queryguard.zip
python dev/scripts/verify_r0_bundle.py --bundle releases/memory-engine-rc3-queryguard.zip --exact-budget
python dev/scripts/build_astrbot_plugin.py
```

## 不接受

- 把测试 skip 当通过；
- 未验证的“生产可用”结论；
- 把模型权重或用户真实聊天入库；
- 直接修改冻结标签来让验收通过。

License: MIT.
