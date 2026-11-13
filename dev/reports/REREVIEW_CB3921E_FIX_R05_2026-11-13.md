# 第四轮复审 R05 修复记录

日期：2026-11-13。基线：`91d7bcc5fd1b9eeab62f7cd2deff05769c1575ce`
（`fix: close rereview R04 lifecycle race`）。

本轮处理复审新增的 **R05（P2）**：共享清理任务被调用方取消、或清理步骤失败时，
实例被错误标记为 `CLOSED`，导致后续 `terminate()` 无法补完清理。R04 的生命周期
保护与之前各轮修复保留。

## 修复

`dev/host_shadow/main.py`：

- `terminate()` 用 `asyncio.shield()` 等待共享清理任务；一个等待者取消不会取消其他
  等待者共享的清理；
- 生命周期只在必需资源全部清理完成后才从 `CLOSING` 变为 `CLOSED`；
- 每个阶段有独立完成标记：pump / judge / store+service / console / pool；
- 清理异常不再被静默吞掉：保存 `terminate_error`，任务失败，生命周期保持
  `CLOSING`，再次调用 `terminate()` 会从失败阶段继续重试；
- store/service 未关闭时不会先关闭负责清理的线程池；
- 保留 R03 的异步线程等待顺序，以及 R04 的关闭中禁止重启保护。

`dev/memory_engine/native_host.py`：

- `NativeInputService.close()` 改为 journal 成功关闭后才标记 `closed`，使失败可以重试。

`dev/scripts/build_r0_bundle.py`：

- 排除 `build/`、`dist/`、`*.egg-info`，避免把本机构建残渣算进发布包。
  这也解释了上一轮报告中“75 文件”与复审从提交构建出的“71 文件”的差异；
  现在源码提交的构建结果稳定为 71 文件。

## 验证

```powershell
cd dev
python -m unittest tests.test_rereview_29a1fe6_fixes -v
python -m unittest discover -q -s tests -t .
```

结果：

- 生命周期/预算/备份回归：16 / 16 通过；
- 全量：**758 项 OK（1 项跳过）**；
- 当前源码运行包：71 文件，SHA-256
  `1991748c39ebdc0b3090ca2fc89035ec61ec4cc7498cbb7c489b69f85eca8717`；
- 隔离导入、精确预算、备份恢复、输入/输出/控制台导入、UI 资源与 CLI 全部通过；
- 未启动真实 AstrBot/NapCat/模型 API。

## 边界

- 生命周期是进程内保护；强制杀进程仍可能留下已发出的外部调用；
- 取消/失败后的重试依赖进程和事件循环仍存活；
- 未做真实宿主卸载/热重载和长期累计压力测试；
- 105 秒为本次 Windows 本地全量耗时，不作为性能承诺。
