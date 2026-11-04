# L0 自用验收记录（模板 + 判定规则）

> 目标：用**连续 3 天真实私聊**回答 L0 的六条验收线。工具已就绪：
> `testbed/harness/soak.py`（每 5 分钟记录采集/任务/连接/内存/注入比例）、
> `testbed/harness/real_chain.py`（钩子→provider 延迟探针）、`/dlm` 命令与备份恢复。

## 1. 六条验收线与测法

| # | 验收线 | 目标 | 测法 | 判定 |
|---|---|---|---|---|
| 1 | 采集成功率 | ≥99% | 每天从**主号**发固定 20 条纯文本到小号（内容用虚构句），记录发送条数；当天 soak 的 `captures_delta` ÷ 20 | ≥0.99 |
| 2 | 每轮供给 | ≥95% | soak 的 `hook_injected / hook_turns`（有历史后应接近 100%）；相关性用 M2/真实探针单独看 | ≥0.95 |
| 3 | 端到端延迟 | P95 < 1.5 s | soak 的 `hook_p95_max`（真实 QQ 每轮钩子耗时）；每天再用 `real_chain.py` 复核钩子→provider | 全部 <1.5 s |
| 4 | 备份 | 可生成/校验/恢复/删除 | 第 1 天：`/dlm 备份` → `/dlm 备份校验` → `recovery restore` 到新目录 → 删除备份文件；第 3 天重复一次 | 两次全过 |
| 5 | 重启 | 积压续跑、无重复注入 | 第 1、3 天各重启一次 testbed；soak 的 `restarts` +1，`pending` 回到 0，采集数不重复增长 | 两次全过 |
| 6 | 连续运行 | 3 天内无未处理错误 | soak 的 `serve_error_lines`；逐条看是否影响功能 | 无阻断性错误 |

> 采集成功率只能用"发送条数 vs 采集增量"算；机器人自己的回复、命令消息不计入。
> 供给率的分母是"走钩子的轮次"；v1.0.1 起每轮都会给有界上下文（无匹配时给背景），
> 所以该比例应接近 100%——但"给了上下文"不等于"给了相关证据"，相关性必须另看探针。

## 2. 三天流程（建议）

| 天 | 动作 | 产出 |
|---|---|---|
| D0 | 确认 testbed 在跑（`boot_check.py`）、soak 已启动、白名单含主号 | 环境就绪 |
| D1 | 发 20 条虚构纯文本 + 1 次备份恢复演练 + 1 次重启 | 当日 soak 曲线 |
| D2 | 发 20 条（可混合追问）+ 1 次延迟探针 | 当日曲线 |
| D3 | 发 20 条 + 第 2 次备份恢复 + 第 2 次重启 + 跑 `real_chain.py` | 汇总 |
| D4 | 读 `soak_summary.json`，逐条填下表 | 验收结论 |

## 3. 记录表（填写）

| 项 | 目标 | D1 | D2 | D3 | 结论 |
|---|---|---|---|---|---|
| 发送/采集条数 | 20 / ≥20 | | | | |
| 采集成功率 | ≥99% | | | | |
| 注入比（有记忆轮次） | ≥95% | | | | |
| 钩子→provider P95 | <1.5 s | | | | |
| 备份+恢复+删除 | 2 次全过 | | | | |
| 重启续跑/无重复 | 2 次全过 | | | | |
| serve 错误行 | 0 阻断 | | | | |
| RSS 峰值 | 观察 | | | | |

### 已完成的演练（2026-11-04）

| 项 | 结果 | 证据 |
|---|---|---|
| 备份 → 校验 → 恢复到新目录 → 删除（真实数据副本） | **通过**：9 条原话，校验/恢复后仍 9 条，删除成功，0.07 s | `dev/reports/l0_drills.json` |
| 重启 testbed | **通过**：7.2 s 重连，采集 9→9 无重复，任务 done 9 | 同上；soak 会在下个 tick 记 `restarts=1` |

## 4. 判定

- 六条全过 → **L0 通过**，可以进入"个人自用"状态，并把结论写进 `STATUS.md`/`READINESS.md`。
- 任一条不过 → 先修该条，不启动新功能；记录失败样本与时间点。
- 3 天后运行汇总：

```powershell
cd testbed
.\.venv\Scripts\python.exe harness\soak_report.py --require-hours 72 --sent 11-04:20,11-05:20,11-06:20
```

`--sent` 填每天实际发送条数；它会输出六条线的值与“通过/未通过”，缺数据的项会标“需人工”。

## 5. 启动 soak（当前环境）

**已注册为 Windows 计划任务，并已在运行：**

```powershell
cd testbed
powershell -NoProfile -ExecutionPolicy Bypass -File harness\schedule_soak.ps1 -Action status
powershell -NoProfile -ExecutionPolicy Bypass -File harness\schedule_soak.ps1 -Action stop      # 需要时暂停
powershell -NoProfile -ExecutionPolicy Bypass -File harness\schedule_soak.ps1 -Action unregister # 验收后删除
```

- 任务名 `DLM-L0-Soak`：当前用户登录时启动，执行 `harness\run_soak_task.cmd`（`--interval 300 --hours 72`），
  80 小时硬超时；输出追加到 `testbed/logs/soak_task.log`。
- 手工前台运行（调试）：`.\.venv\Scripts\python.exe harness\soak.py --interval 300 --hours 72`
- 产物：`testbed/logs/soak.jsonl`（每行一次采样）、`soak_summary.json`（汇总，实时覆盖）。
- 注意：任务随登录会话运行；注销/重启后会在下次登录时重新启动一个 72 小时周期。

## 6. 已知限制

- 插件已把每轮钩子耗时与是否注入写入 `plugin_data/.../latency.jsonl`，soak 汇总为
  `hook_turns / hook_injected / hook_p95_max`，QQ 真实 P95 不再依赖探针；样本少时可与 `real_chain.py` 交叉验证。
- 供给率的分母是“所有走钩子的轮次”；背景包也算供给，因此相关性必须另看探针命中，不能只看这个比例。
- soak 不采集“平台侧实际发送条数”，第 1 条需人工记录。
