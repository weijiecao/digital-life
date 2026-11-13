# 第三轮复审 R04 修复记录

日期：2026-11-13。基线：`cb3921ec57545cddbd01d658bbd68863d00fb700`
（`fix: close rereview R01-R03 findings`）。

本轮只处理复审新增的 **R04（P2）**：关闭期间到达的采集/观察回调可以重新启动
后台 pump，导致 terminate 完成后仍有任务存活。R01–R03 的既有修复与测试保留。

## 修复

`dev/host_shadow/main.py` 增加显式生命周期：

```text
NEW -> RUNNING -> CLOSING -> CLOSED
```

- `terminate()` 在第一次 `await` 前切到 `CLOSING`，并复用同一个关闭任务，重复调用幂等；
- `capture()` 入口与 `_snapshot()` 之后都检查状态，进入关闭后不再提交；
- `observe()` 在进入、读取会话身份后、解析 provider 后检查状态，关闭后不再检索/注入；
- `_ensure()`、`_submit()`、`_observe_work()`、`_output_work()`、`_start_pump()`
  都拒绝在 `CLOSING/CLOSED` 下创建新工作；
- 保留 R03 的异步线程等待顺序：先让 judge 线程退出，再关闭 store，最后关闭线程池；
- `terminate()` 完成后状态为 `CLOSED`，之后到达的回调直接返回，不会重开数据库或 pump。

## 验证

```powershell
cd dev
python -m unittest tests.test_rereview_29a1fe6_fixes -v
python -m unittest discover -q -s tests -t .
```

结果：

- R01–R03 回归：11 / 11 通过；
- 新增 R04 回归：3 / 3 通过（关闭中 capture、关闭中 observe、重复 terminate 与关闭后回调）；
- 全量：**756 项 OK（1 项跳过）**；
- 当前源码运行包：75 文件，SHA-256
  `10e33dee5d026708efcdb7c75f7aa04f6f42cd9b55a8de84d730a7c01c03b8ab`，
  隔离导入、精确预算、备份恢复、native_host/output/console 导入与 CLI 均通过；
  （后续 R05 修复了 `build/`/`egg-info` 未排除的问题；从源码提交复现为 71 文件，
  见 `REREVIEW_CB3921E_FIX_R05_2026-11-13.md`。）
- 未启动真实 AstrBot/NapCat/模型 API。

复审证据包中的 `shutdown_race.py` 断言的是修复前竞态存在（pump 被重建、关闭后任务存活）；
修复后这些条件不再成立，固定口径以上述新增正向回归为准。

## 边界

- 生命周期状态是进程内保护，不跨进程；强制杀进程仍可能留下已发出的外部调用；
- 关闭等待仍受 provider 超时约束；
- 未做真实宿主卸载/热重载和长期累计压力测试。
