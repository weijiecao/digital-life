# dev — 数字记忆工程开发树

- 状态：`0.8.0-dev.2` 开发基线，R0/R1 NO-GO；能力与证据见
  [`../docs/CURRENT_STATUS.md`](../docs/CURRENT_STATUS.md)。
- 源码回归：在仓库根目录执行 `python tools/run_regression.py`（401 项，结果写入 `dist/regression.json`）。
- 包级用例：`python tools/run_package_regression.py --dist dist --suites completion,core,legacy`
  （47＋137＋20，针对同一个候选 ZIP；见 `verification/README.md`）。
- 当前验收记录：[`reports/current_acceptance.json`](reports/current_acceptance.json)。
- 专项覆盖映射：[`reports/SPECIALIZED_COVERAGE.md`](reports/SPECIALIZED_COVERAGE.md)。

## 目录

| 路径 | 职责 |
| --- | --- |
| `astrbot_plugin_digital_life/` | 统一插件：入口、应用服务、迁移/恢复、管理 UI |
| `astrbot_memory_input/` | 原生输入契约、实时追加、历史对账（独立包，仅标准库） |
| `memory_engine/` | 召回/准入/重评/上下文/预算/渲染/CLI |
| `memory_output/` | 输出协议 v2 与 AstrBot 临时注入适配（独立包，仅标准库） |
| `v1_5_consolidator/` | 基座：事件/证据/Resolver/租约/回执/水位 |
| `dlm_console/` | 独立本地 Web 控制台（也作为插件页复用） |
| `tests/` | 活动回归（`unittest`，从 `dev` 作为 top-level 运行） |
| `evaluation/` | 质量/恢复验收脚本与冻结数据（需 BGE 目录，不进离线回归） |
| `fixtures/` | 固定样本 |
| `examples/` | 原生输入与输出注入的最小示例 |
| `docs/` | 模块级架构与基座说明 |
| `reports/` | 当前验收证据、历史快照、专项覆盖映射 |
| `scripts/` | 仍受支持的开发辅助脚本 |

旧插件源码、旧发布包、旧测试与旧构建脚本在仓库根 `archive/`，不参与活动回归，
也不允许被这里导入。历史实验日志已移除；需要时由脚本重新生成或从 Git 历史取回。

## 运行

```powershell
# 仓库根目录
python tools/run_regression.py

# 只跑某一个文件
cd dev
python -m unittest -q tests.test_unified_plugin
```

质量验收需要本机 BGE 目录：

```powershell
cd dev
python evaluation/query_admission_challenge.py --model-dir <BGE目录> --out challenge.json
python -m evaluation.quality_v3_accept --model-dir <BGE目录> --out quality-v3.json
```

控制台：根目录 `启动数字记忆控制台.cmd`，或在 `dev` 下 `python -m dlm_console`。
