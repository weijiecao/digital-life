# 上线检查清单（Go / No-Go）

> 用法：上线前逐项打勾；任何 **P0 未过** 即 No-Go。证据以 `STATUS.md` 与 `reports/` 为准，
> 不引用聊天记录或记忆。版本锚点：`1.0.3`。

## 1. L0 自用（近期闸门）

| # | 项 | 目标 | 证据 | 状态 |
|---|---|---|---|---|
| 1 | 采集成功率 | ≥99% | `soak_report.py --sent 日:条` | 待 3 天数据 |
| 2 | 注入率（真实 QQ） | ≥95% | soak `hook_injection_rate` | 待真实对话 |
| 3 | 钩子 P95 | <1.5 s | soak `hook_p95_max` | 待真实对话 |
| 4 | 备份/校验/恢复/删除 | 2 次全过 | `l0_drills.json` | ✅ 已演练 1 次 |
| 5 | 重启续跑/不重复 | 2 次全过 | soak `restarts` + 采集数 | ✅ 已演练 |
| 6 | 连续运行 | 3 天无阻断错误 | soak `serve_error_lines` | 待 3 天 |

## 2. L1-lite（下一里程碑）

| # | 项 | 目标 | 证据 | 状态 |
|---|---|---|---|---|
| 7 | 离线回归 | 全绿 | `STATUS.md` 用例表 | ✅ 336/336 |
| 8 | 安装/升级/回退 | 可复现 | `scripts/install.py`、`verify_install.py`、`test_delivery` | ✅ 脚本实测 |
| 8b | 可分发安装包 | zip + SHA256 | `scripts/build_package.py` → `dist/...-1.0.3.zip` | ✅ 42 文件 / 0.15 MB |
| 9 | 新人按文档安装 | ≤30 分钟 | 人工走查 | ❌ 待真人 |
| 10 | 真实语料 R@1 | ≥70% | `real_corpus_eval.py` | ❌ 待语料 |
| 11 | 真实语料误注入 | ≤5% | 同上 | ❌ 待语料 |
| 12 | 真实语料纠错 | ≥60% | 同上 | ❌ 待语料 |
| 13 | 5,000 条 P95 | <2 s | `scale_5000.json` | ✅ 293 ms |
| 14 | 崩溃恢复 | 完整性/续写/去重 | `crash_recovery.json` | ✅ 通过 |
| 15 | 隐私告知 | 外发内容写清 | `INSTALL.md`、`G3_LLM_DECISION.md` | ✅ |
| 16 | 默认安全 | 默认关闭/白名单/不确认事实 | 配置默认值、`presets/off.json` | ✅ |

## 3. 上线前 10 分钟操作

```powershell
cd dev
python scripts/report.py                     # 全量回归 + STATUS
python evaluation/crash_recovery.py          # 恢复演练（可选，约 2 s）
..\testbed\.venv\Scripts\python.exe evaluation\scale_bench.py --model-dir ..\testbed\models\bge-small-zh-v1.5
python scripts/preflight.py --model-dir "<模型目录>"
python scripts/verify_install.py --astrbot-root "<AstrBotRoot>" --json reports/verify_install.json
```

- 部署：`install.py`（新装）或 `upgrade.py`（升级，自动备份）；
- 回退：`rollback.py`，备份在 `data/plugins_backup/`；
- 升级后：重启 AstrBot，确认日志出现插件加载，发 `/dlm 上下文状态`。

## 4. 已知限制（必须在发布说明里写）

- 只处理**私聊纯文本**；群聊/图片/语音/多设备不同步。
- 语义阈值 0.60 来自自造语料；真实语料未验前不要承诺“记得住”。
- 指代追问（“后来呢？”）默认接不住，需显式开启候选筛选器（会外发原话）。
- 纠错是“新旧并列 + 未确认”，不是自动改事实。
- 自动主题/跨事件在合成模板语料下会保守阻断；真实对话行为待验。

## 5. 回滚判定

出现以下任一情况立即回退到上一备份，并保留 `latency.jsonl` / `serve.log`：

- 插件加载失败或启动报错；
- 注入内容与 `未确认` 语义不符（把候选说成事实）；
- 采集数异常增长或重复；
- 钩子 P95 连续 3 个 tick 超过 1.5 s；
- 任何隐私外发超出 `INSTALL.md` 所述范围。
