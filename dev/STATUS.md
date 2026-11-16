# dev/STATUS.md — 已迁移

这个文件过去是 **自动生成的状态快照**（旧插件 `1.0.19`、590 项回归、2026-11-11），
生成它的 `scripts/report.py` 已不在本仓库，继续留在这里会与当前数字冲突。

现在分两处：

| 需要什么 | 看哪里 |
| --- | --- |
| 当前状态、能力与未完成验收 | [`../docs/CURRENT_STATUS.md`](../docs/CURRENT_STATUS.md) |
| 当前验收记录（唯一可引用数字） | [`reports/current_acceptance.json`](reports/current_acceptance.json) |
| 包级用例（47＋137＋20） | [`../verification/README.md`](../verification/README.md) |
| 旧快照原文（590 项、1.0.19） | [`reports/history/STATUS_2026-11-11.md`](reports/history/STATUS_2026-11-11.md) |
| 旧报告里的其它数字 | [`reports/README.md`](reports/README.md) |

## 数字从哪来

```powershell
# 仓库根目录
python tools/sync_version.py --check  # VERSION 与派生字段一致
python tools/run_regression.py        # dist/regression.json + dist/regression.log
python tools/build_unified_plugin.py --out-dir dist
python tools/verify_unified_plugin.py --dist dist --report dist/package_verification.json
```

旧快照里的 `590/590`、`715`、`758` 等数字属于不同提交与不同包装层，不能相加，
也不能当作当前基线；当前活动套件是 `dev/tests/` 下的全部用例。
