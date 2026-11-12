# dev — Memory Engine 开发树

- 状态：R0/R1 NO-GO
- 全量回归：`python -m unittest discover -q -s tests -t .`（590 项）
- 当前交付说明：`reports/Q1_INTEGRATION_2026-11-12.md`
- 工程报告：`reports/ENGINEERING_REPORT_2026-11-11.pdf`

## 目录

- `memory_engine/`：独立引擎与 CLI
- `v1_5_consolidator/`：事件/证据/Resolver/租约
- `host_shadow/`：AstrBot shadow 与 soak
- `tests/`：590 项回归
- `evaluation/`：当前质量与恢复验收脚本、冻结数据
- `reports/`：当前报告与证据索引
- `scripts/`：构建、校验、生命周期、AstrBot 打包
- `astrbot_plugin_digital_memory_store/`：AstrBot 插件源码

历史实验日志和旧报告已移除；需要时由脚本重新生成，或从 Git 历史取回。
