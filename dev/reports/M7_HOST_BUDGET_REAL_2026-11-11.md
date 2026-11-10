# M7 真实宿主 Shadow 接入宿主预算

- 日期：2026-11-11
- 宿主：AstrBot 4.29.3 testbed
- 状态：**Memory Engine shadow 使用真实 system/history/output 预留**
- 模型调用：0（mock provider）

## 1. 接入

`dev/host_shadow/main.py`：

- `HostBudget(total, system, history, output)`；
- total 默认 `SHADOW_HOST_TOTAL_TOKENS=8192`；
- output 默认 `SHADOW_OUTPUT_TOKENS=800`；
- system 从 `req.system_prompt` 计数；
- history 从 `req.contexts` JSON 计数；
- 用 `memory_tokens` 调 `build_rendered_context`。

## 2. 真实运行

```text
host_memory_tokens = 5791 / 5684
rendered_tokens    = 123 / 199
items              = 1 / 2
V1 injection       = true
```

## 3. 判读

- Shadow 不再使用固定 1200 字符预算；
- system/history/output 从真实 `ProviderRequest` 读取；
- 最终 rendered block 远低于 memory budget；
- V1 回复与注入不受影响。

## 4. 边界

- 宿主 total 是环境变量，不是 AstrBot 真实配置；
- mock provider；
- 单次运行；
- 不是 72h；
- 不是 R1 验收。
