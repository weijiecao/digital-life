# dev/reports

这里只保留**可复跑、可引用的最终证据**；一次性 stdout、构建日志、控制台输出和中间轮次不入库。

## 保留

- `verification_v*.json`：V1–V12 的验证输出；
- `verification_real_db.json`：真实库审计；
- `real_env.json` / `real_env_installtest_isolated.json`：真实 AstrBot 链路；
- `integration_chain.json`、`scale_5000.json`、`m2_*.json`：链路、容量、召回评估；
- `install_test_*` / `verify_install_*`：安装与自检结果；
- `real_messages_judge_eval_40_filtered*.json`：40 基础探针，三轮稳定性；
- `real_messages_judge_eval_80_neg*.json`：80 正例 + 10 负例，三轮；
- `reply_quality_*v7/v7b/guard*.summary.json`：回复质量最终汇总（原始 JSONL 与旧版中间轮次不入库）；
- `status.json`：`scripts/report.py` 生成的机器可读状态。

## 不保留

- `*.log`、`*_stdout.txt`、`report_*.txt`、`build_*.txt`；
- 旧版中间轮次（如 `reply_quality_*v2–v6*`、10 条试点 `real_messages_judge_eval.json`）；
- `reply_quality_*.jsonl` 原始逐条对话（含测试原文，避免仓库体积与隐私面扩大）。

重新生成：

```powershell
cd dev
..\testbed\.venv\Scripts\python.exe scripts\report.py
```

最终数字以 `dev/STATUS.md` 与 `docs/MEMORY_VERIFICATION_REPORT_2026-11-05.md` 为准。
