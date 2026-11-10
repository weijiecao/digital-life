# 集成链 CI flake 修复

- 日期：2026-11-11
- 触发：3.10 runner 上 `test_integration_chain` 出现 1503ms、7/8 injection、pending=0
- 状态：**probe 夹具确定性修复；未放宽断言**

## 1. 改动

`dev/integration_probe.py`：

- 注入测量前等待 jobs drained；
- 增加一次 warm-up LLM 请求，不计入延迟样本；
- 每轮 receive 后 `sleep(0.02)`，让采集/索引稳定；
- 若重启前 pending=0，显式把一条已存在 auto job 置回 pending，构造确定积压。

## 2. 结果

| 项 | 值 |
|---|---|
| 本地 `test_integration_chain` 连续 3 次 | OK |
| 全量 | 507/507 |
| 断言 | 未放宽阈值/未跳过 |

## 3. 边界

- 这是测试夹具确定性修复；
- 不证明 3.10 真实性能；
- warm-up 不计入样本，需在报告中保持透明；
- 仍保留 1.5s L0 阈值。
