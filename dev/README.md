# dev — 数字记忆工程开发树

- 状态：R0/R1 NO-GO
- 全量回归：`python -m unittest discover -q -s tests -t .`（756 项）
- 当前交付说明：`reports/Q1_INTEGRATION_2026-11-12.md`、`reports/NATIVE_INPUT_0_2_2026-11-12.md`、`reports/MEMORY_BASE_OUTPUT_0_1_INTEGRATION_2026-11-12.md`
- 工程报告：`reports/ENGINEERING_REPORT_2026-11-11.pdf`

## 目录

- `memory_engine/`：数字记忆引擎（召回/准入/重评/上下文/输出/CLI）
- `memory_output/`：记忆输出协议与 AstrBot 临时注入适配（独立包，仅标准库）
- `v1_5_consolidator/`：数字记忆基座（事件/证据/Resolver/租约/回执）
- `astrbot_memory_input/`：原生历史输入契约（独立包，仅标准库）
- `examples/astrbot_native/`：原生输入线路演示
- `examples/memory_output/`：输入→基座→预算输出→临时注入演示
- `host_shadow/`：AstrBot shadow、soak、原生输入装配、可选输出注入与插件页控制台
- `dlm_console/`：数字记忆系统独立本地 Web 控制台（仅标准库）
- `tests/`：756 项回归
- `evaluation/`：当前质量与恢复验收脚本、冻结数据
- `reports/`：当前报告与证据索引
- `scripts/`：构建、校验、生命周期、AstrBot 打包
- `astrbot_plugin_digital_memory_store/`：数字记忆基座的 AstrBot 插件源码

历史实验日志和旧报告已移除；需要时由脚本重新生成，或从 Git 历史取回。
