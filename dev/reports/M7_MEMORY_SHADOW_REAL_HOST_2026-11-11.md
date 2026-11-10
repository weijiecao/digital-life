# M7 真实 AstrBot 中的 Memory Engine Shadow

- 日期：2026-11-11
- 宿主：AstrBot 4.29.3 / Python 3.12.10
- 插件：testbed `astrbot_plugin_memory_shadow` 0.1.0
- 状态：**真实宿主 shadow 观察可用；不是 72h/回退验收**

## 1. 接入方式

独立 testbed 插件：

- `@filter.event_message_type(PRIVATE_MESSAGE)`：把私聊文本写入独立 MemoryEngine DB；
- `@filter.on_llm_request()`：构建 context 并写 `shadow_events.jsonl`；
- **不修改 req，不注入 V1 回复**。

## 2. 观察

```text
plugins: [
  'astrbot_plugin_digital_memory_store',
  'astrbot_plugin_memory_shadow',
  'astrbot',
  'builtin_commands'
]
```

Shadow DB：

| 项 | 值 |
|---|---:|
| events | 4 |
| valid evidence | 4 |
| scopes | 1 |
| sources | 4 |

Shadow 观察日志：

| query | items |
|---|---:|
| 我喜欢吃苹果 | 1 |
| 你之前喜欢吃苹果吗 | 1 |
| 你之前喜欢吃苹果吗（重复） | 2 |

## 3. V1 回复未受影响

同一次真实链路中：

- V1 插件版本 1.0.8；
- injection attempts found=true；
- contains_memory_prefix=true；
- contains_seed_text=true；
- reply 正常返回。

Shadow 只写自己的 DB/log，没有改 V1 请求。

## 4. 边界

- testbed 合成用户与对话；
- 单会话、数分钟；
- 没有 72h；
- 没有回退演练；
- 插件在 testbed，不在生产包；
- 不是 R1 验收。

## 5. 结论

Memory Engine 已作为独立影子接入真实 AstrBot 私聊事件和 LLM 请求钩子，且未改变 V1 回复。下一步是 72h 观察、回退演练与真实脱敏语料。
