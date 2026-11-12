# QQ 实测运行手册（小号扫码登录 NapCat → testbed AstrBot）

> 目标：让真实 QQ 私聊走完 ①→⑤，证据落到 `dev/reports/`。
> 红线：**不碰生产实例**（`C:\ProgramData\NapCatQQ Desktop\...`，正连着你的主号）。
> 本机已就绪：AstrBot 4.29.3 实例在反向 WS `127.0.0.1:6201` 监听；插件已加载。

## 角色分工

| 角色 | 用哪个号 | 需要做什么 |
|---|---|---|
| 机器人 | **小号** | 登录便携 NapCat（扫码一次），之后由 AstrBot 驱动 |
| 聊天用户 | 你的主号（手机） | 给小号发私聊，按脚本逐条发送冻结话术 |

## 第 1 步：启动便携 NapCat（小号）

```bat
cd /d C:\Users\205\Desktop\NapCat.Shell
launcher-user.bat
```

- 不传 `-q` 时是**扫码登录**；弹出 QQ 登录窗口后，用手机 QQ 扫**小号**的码。
- 如果提示"QQ 已在运行"：说明主号 QQ 客户端占着实例。不要在主号客户端里切换账号
  （会把生产机器人顶下线）。可选做法：
  1. 用 `launcher-user.bat -q <小号QQ>` 试快速登录；
  2. 或先确认生产机器人可以短暂离线，再关闭主号 QQ 后启动；
  3. 或换一台机器/另一个 Windows 用户会话专门跑小号。
- 登录成功后，`C:\Users\205\Desktop\NapCat.Shell\config\onebot11_<小号QQ>.json` 会生成，
  控制台会打印 WebUI 地址（一般 `http://127.0.0.1:6099`）与 token。

**已完成**：小号 `3266206281` 已登录（`onebot11_3266206281.json` 已生成），
`napcat_connect.py` 已写入反向 WS 客户端（`ws://127.0.0.1:6201/ws`），
NapCat 重启后已连接；主号 `1508236662` 的两条私聊已产生采集与注入证据
（`dev/reports/qq_real.json`、`dev/docs/REAL_ENV_REPORT.md` 第 3.5 节）。

## 第 2 步：把 NapCat 接到 testbed（我来做）

```powershell
cd "C:\Users\205\Desktop\数字生命\testbed"
.\.venv\Scripts\python.exe harness\napcat_connect.py --qq <小号QQ> --dry-run   # 先看
.\.venv\Scripts\python.exe harness\napcat_connect.py --qq <小号QQ>           # 写入
```

写入内容是一个反向 WS 客户端：`ws://127.0.0.1:6201/ws`、token `dlm-testbed-2026`。
NapCat 多数版本会热加载；没有的话重启 NapCat（QQ 会快速登录，通常不用重新扫码）。

## 第 3 步：确认链路（我跑）

```powershell
cd testbed
.\.venv\Scripts\python.exe harness\boot_check.py     # 6201 应为 open，插件应在 plugins 列表
```

然后从**你的主号**给小号发一条：`我喜欢吃苹果`，再发一条：`你之前喜欢吃苹果吗`。
我会读插件数据库与 mock provider 记录，确认：

- `dl_capture` 有两条原话（作用域 = 你的 QQ + 私聊）；
- 第二轮的请求体里出现 `[DL_MEMORY_V040]` 与第一条原话；
- 记录 `钩子→provider` 耗时。

## 第 4 步：8 条冻结话术（可选，对齐上游基线）

按《远程执行单》逐字发送（标点也要一致），我在收到 `shadow_observations` 或插件库后
填 `dev/docs/baseline_E50F_real.json` 的待填字段。

```
A 书店：之前那件事有什么安排？ / 我记得找编程资料，跟周末行程有关。 / 是十月去书店找资料那次。
B 足球：之前约的那次呢？ / 后来想起是踢足球，和周末有关。
C 负例-无关：上次去火星租飞船的事情呢？ / 飞船码头的停车规则是什么？
D 负例-近似：之前去买书那次有没有订飞船？
```

## 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 6201 端口 closed | testbed 没起 | 先 `boot_check.py`；aiocqhttp 平台配置见 `instance/data/cmd_config.json` |
| NapCat 控制台无"已连接" | 客户端未生效 | 确认 `onebot11_<QQ>.json` 的 `network.websocketClients` 有 `astrbot-testbed`；重启 NapCat |
| 有消息但插件没采集 | 白名单不匹配 | 插件 `allowed_sender_ids` 必须包含**发消息的 QQ**（不是小号） |
| 回复是"好的，这是测试回复。" | 正常 | provider 是本地 mock，只为验证请求体，不是真模型 |
| 第一轮没有记忆 | 已知 | BGE 冷启动占满单线程池，见 `dev/docs/REAL_ENV_REPORT.md` 的 F1 |
