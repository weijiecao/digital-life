# M1 Provider 超时烟测

- 日期：2026-11-11
- 状态：**本地慢 HTTP 服务触发 provider 超时**
- 测试：519/519

## 1. 场景

- 本地 HTTP server 延迟 2s；
- `OpenAIProvider(timeout=0.2)`；
- 调用 `generate`；
- 断言抛异常。

## 2. 结果

| 项 | 值 |
|---|---|
| provider timeout | 0.2s |
| server delay | 2s |
| 结果 | 超时异常 |
| 测试 | ✅ |

## 2.1 超时后 pending

测试：

- 慢 provider 超时；
- source 无 evidence；
- pending 仍 READY；
- 快速 judge 重试后附着。

## 2.2 瞬时网络重试

`OpenAIProvider` 新增 `retries=2`、`retry_backoff=0.2`：

- `URLError` / `TimeoutError` / `ConnectionError` / `IncompleteRead` 可重试；
- `HTTPError` 直接抛出，不重试 4xx/5xx；
- 单测：第一次 URLError、第二次成功，body 正常解析。

## 3. 边界

- 本地 HTTP，不是 DeepSeek 真实网络；
- 只验证超时抛出，不验证重试策略；
- 不是模型质量测试。
