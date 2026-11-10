# M7 Shadow 回退演练

- 日期：2026-11-11
- 宿主：AstrBot 4.29.3
- 动作：移出 `astrbot_plugin_memory_shadow`，启动宿主，再恢复插件
- 状态：**回退演练通过；不是 72h 观察**

## 1. 回退结果

移出 shadow 插件后启动：

```text
AstrBot started
plugins: ['astrbot_plugin_digital_memory_store', 'astrbot', 'builtin_commands']
```

- 目标 V1 插件仍加载；
- shadow 插件不在列表；
- 宿主正常启动；
- 随后把 shadow 插件移回，未修改生产插件。

## 2. 判读

- Shadow 是独立插件，可整体移出；
- 回退不依赖 Memory Engine；
- V1 回复链保持可用；
- Shadow DB 保留，未删除用户数据。

## 3. 边界

- 本地 testbed，单次启动；
- 没有 72h；
- 没有真实 QQ/NapCat 消息；
- 没有版本升级/降级；
- 不是 R1 回退验收。

## 4. 结论

Shadow 具备可拆卸的最小回退路径；下一步是在真实宿主上做 72h 观察、故障注入与版本回退演练。
