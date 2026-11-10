# M7 Shadow 宿主插件源码

- 日期：2026-11-11
- 状态：**可安装的独立 AstrBot shadow 插件源码**
- 位置：`dev/host_shadow/`

## 1. 内容

- `main.py`：PRIVATE_MESSAGE 采集 + on_llm_request 观察；
- `metadata.yaml`：AstrBot 插件元数据；
- `README.md`：安装与环境变量。

## 2. 保证

- 不修改 req；
- 不修改回复；
- 忽略 `/`、`!` 命令；
- 只采集私聊文本；
- 独立 DB/log。

## 3. 安装

```powershell
$env:DIGITAL_LIFE_DEV='...\dev'
$env:ASTRBOT_ROOT='...\instance'
Copy-Item -Recurse dev\host_shadow "$env:ASTRBOT_ROOT\data\plugins\astrbot_plugin_memory_shadow"
```

## 4. 边界

- 只在 testbed 验证过；
- 没有 72h；
- 没有真实 QQ/NapCat；
- 未接入 host budget；
- 不是 R1 验收。
