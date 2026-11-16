# DOM 接线验证（2026-11-16）

对象：候选安装包 `astrbot_plugin_digital_life-0.8.0-dev.1.zip`
（`05ba15e9d98792220c7ddf1615bad8448c178175626a6ebbc529c57a9ec90cff`，79 文件 / 223,273 B）。

## 结果：通过（仅 DOM 接线，不是浏览器验收）

```text
environment: jsdom + actual packaged ConsoleApi/ApplicationService; AstrBot stub
operations: migration_create, migration_batch, migration_release,
            backup, verify_backup, restore, withdraw
inbox: { ARCHIVED: 2, WITHDRAWN: 1 }
generation: 2
dom_errors: []
visual_browser_validation: false
```

7 条管理路径全部通过：创建迁移 → 执行批次 → 放行整理 → 备份 → 校验备份 → 恢复 → 撤回。
页面事件调用的是**解压包内**的 `ConsoleApi` 与 `ApplicationService`，不是源码树。

## 环境

| 项 | 值 |
| --- | --- |
| Node | v20.18.1（便携版，`.tools/node`） |
| npm | 10.8.2 |
| jsdom | 26.1.0（`verification/plugin/package-lock.json`，`npm ci` 可复现） |
| Python | 3.12.10（RPC 子进程使用 `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`） |
| 运行对象 | 解压后的候选 ZIP（ASCII 临时路径，避免 Windows 中文路径编码问题） |

命令：

```powershell
cd verification/plugin
npm ci
node console_dom_check.cjs <python> <解压后的插件目录>
```

## 边界

- **没有**运行真实浏览器（Playwright/Chromium 未安装），因此不能把本结果写成
  “浏览器验收通过”；`console_browser_check.py` 仍是未运行的可选入口。
- 不覆盖真实 AstrBot 登录态、页面布局、CSS 或跨浏览器行为。
- 不覆盖真实宿主实例：RPC 侧的 AstrBot 是离线替身（`completion_suite` 的 stub）。

## 本轮修复

DOM 首次运行失败于 RPC 子进程的编码：Windows 下 Node 以 UTF-8 写管道，而子进程 stdin/stdout
使用本地代码页，中文参数经往返后变成孤立代理字符。已在 `console_dom_check.cjs` 中为子进程
显式设置 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`，无需调用方预先配置环境。

## 与验收记录的关系

`dev/reports/current_acceptance.json` 是 0.8.0-dev.1 **发布时**的记录，其中 `ui_dom: not-run`；
本文件记录发布之后补跑的结果，不改变已发布的包字节，也不能替代真实浏览器或真实宿主验收。
