# M7 真实 AstrBot 宿主启动检查

- 日期：2026-11-11
- 宿主：AstrBot 4.29.3
- 命令：`testbed\.venv\Scripts\python.exe harness\boot_check.py`
- 状态：**真实宿主可启动并加载插件；不是 Memory Engine Shadow 验收**

## 1. 观察

```text
AstrBot v4.29.3
Loaded 1 personas
Loading plugin astrbot_plugin_digital_memory_store ...
Plugin astrbot_plugin_digital_memory_store (1.0.8)
Loading IM platform adapter webchat
Loading IM platform adapter aiocqhttp(napcat-testbed)
AstrBot started
plugins: ['astrbot_plugin_digital_memory_store', 'astrbot', 'builtin_commands']
reverse-ws 127.0.0.1:6201: open
webui 6199: closed
```

## 2. 判读

- 真实 AstrBot 4.29.3 可启动；
- 目标插件被加载；
- webchat 与 aiocqhttp 适配器存在；
- reverse-ws 端口可用；
- 未发送任何 QQ/WebChat 消息；
- 未调用 Memory Engine Shadow。

## 3. 边界

- 加载的是 testbed 中插件副本 1.0.8，不是本次 Memory Engine；
- 没有真实用户消息；
- 没有 72h 观察；
- 没有回退演练；
- 不是 M7 真实 Shadow 通过。

## 4. 结论

M7 的宿主可用性有真实启动证据；但“Memory Engine 真实 Shadow”仍未开始。下一步需要把 shadow runner 接到真实 AstrBot 事件链，而不是只做离线对照。
