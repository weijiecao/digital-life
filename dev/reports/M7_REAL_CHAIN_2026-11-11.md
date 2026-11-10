# M7 真实 AstrBot 消息链探针

- 日期：2026-11-11
- 宿主：AstrBot 4.29.3 / Python 3.12.10
- 命令：`testbed\.venv\Scripts\python.exe harness\real_chain.py`
- 状态：**真实宿主事件链可用；验证的是 V1 插件 1.0.8，不是 Memory Engine Shadow**

## 1. 观察

| 项 | 值 |
|---|---|
| plugin loaded | true |
| handlers | add_memory_context(OnLLMRequestEvent), capture_private, dlm |
| capture after seed | 1 条 |
| cold query memory prefix | false |
| injection attempt 1 | found=true |
| contains_memory_prefix | **true** |
| contains_seed_text | **true** |
| contains_query_text | **true** |
| request chars | 10,721 |
| message roles | system,user,assistant,user,assistant,user |
| BGE | 512 维、load 9.96s、3.5ms/item |

## 2. 判读

- 真实 AstrBot 能加载插件并调用 `OnLLMRequestEvent`；
- WebChat 消息进入真实事件总线；
- mock provider 收到包含记忆前缀的请求体；
- 这是 V1 现有插件链路的真实验证；
- 不是 Memory Engine Shadow。

## 3. 边界

- 插件版本 1.0.8，不是本次 Memory Engine；
- 单会话、合成文本；
- 没有 72h；
- 没有回退演练；
- 不能作为 M7 真实 Shadow 通过。

## 4. 结论

宿主事件链和注入位置有真实证据；下一步应把 Memory Engine 作为独立 Shadow 接入同一事件链，并保持 V1 回复不被修改。
