# 第二轮复评 R01–R03 修复记录

日期：2026-11-13。基线：`29a1fe634a218a895fefc5706f4615b30f022b82`
（`fix: resolve review findings F01-F10 in memory engine`）。

本轮处理复评报告中的 1 项 P1 与 2 项 P2。原始 F01–F10 的既有修复与测试保留，
不回退。

## R01（P1）· 宿主预算跟随当前 provider

`dev/host_shadow/main.py`：

- 不再在初始化时取 `cmd_config.json` 中第一个 `max_context_tokens`；
- 每次请求从当前事件解析实际 provider（先显式 `selected_provider`，再
  `get_using_provider_async` / `get_using_provider`）；
- 从 provider 的 `provider_config.max_context_tokens` 取上限；
- 宿主没有 provider 解析能力时，按 `req.model` 与配置的 provider/model 唯一匹配；
- `SHADOW_HOST_TOTAL_TOKENS` 现在只作为上限（与 provider 值取较小者），不会被
  配置顺序或 provider 上限静默放大；
- 无匹配 provider 时保留保守默认 8192。

覆盖：provider 顺序互换、当前 provider 切换、显式小预算、未知 provider、
大附加内容下的小 provider 预算。

## R02（P2）· 引擎备份恢复必需来源链

`dev/dlm_console/core.py`：

- engine 备份必需表新增 `source_ref`、`source_receipt`、`scope_epoch`、
  `resolution_pending`、`event_relation`、`event_cannot_link`；
- 增加对应主键检查；
- 有效 `event_evidence` 必须能联到未删除的 `source_ref`，且 evidence 与
  event 的 `scope_id` 一致；
- 缺少来源表、来源行或来源被标记删除时，备份判为无效并给出可定位错误。

覆盖：完整引擎库通过；删整张 `source_ref` 表拒绝；删来源行拒绝；来源
`deleted_at` 非空拒绝。

## R03（P2）· 关闭流程不阻塞宿主事件循环

`dev/host_shadow/main.py`：

- 停止 pump 后，先把 `judge_pool.shutdown(wait=True)` 放到工作线程等待，
  让 judge 线程等待的宿主 `llm_generate` coroutine 仍能在事件循环执行；
- judge 线程退出后才关闭 store，避免关闭后的迟到提交；
- `pool.shutdown(wait=True)` 同样移出事件循环。

覆盖：provider 等待期间 `terminate()`，50 ms 心跳仍按时运行，provider 正常完成，
关闭后无遗留线程提交。

## 验证

```powershell
cd dev
python -m unittest tests.test_rereview_29a1fe6_fixes -v
python -m unittest tests.test_review_63e61de_fixes -v
python -m unittest discover -q -s tests -t .
```

结果：

- 新增 R01–R03 测试：11 / 11 通过；
- 既有复评测试：27 / 27 通过；
- 全量：753 项 OK（1 项跳过）；
- 构建/包验证：见本轮推送后的运行记录；未启动真实 AstrBot/NapCat 或模型 API。

复评证据包中的 `edge_reproduce.py` 断言的是**修复前缺陷存在**（例如
`host_total==1000000`、`terminate` 阻塞超过 0.8 秒），修复后按设计不再通过；
固定口径以本目录新增的正向回归为准。

## 边界

- R01 依赖宿主暴露 provider 解析；只有无法解析时才退回 model 唯一匹配/保守默认；
- R02 只验证恢复必需结构与证据来源，不替代真实用户备份的覆盖恢复演练；
- R03 的等待仍受 provider 超时约束；进程强制终止仍可能留下已启动的外部调用；
- 未改变 R0/R1 的 NO-GO，也未做真实宿主联调。
