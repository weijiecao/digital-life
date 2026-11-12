# 输出模块整合记录（output v0.1 → 当前 dev）

日期：2026-11-12。来源交接包：`MEMORY_BASE_NATIVE_IO_0_1_HANDOFF_fdb7ca4.zip`
（实现提交 `fdb7ca4`，源码快照 `dfdafe3`，基线 `a5f3d3b`）。

## 本轮做了什么

把交接包中的**记忆输出/注入模块**并入当前 `dev`，并接到 `host_shadow` 的
`on_llm_request` 路径；不覆盖当前已有的原生输入 v0.2、Q1 准入和工程改动。

新增/接线的文件：

- `dev/memory_output/`：不可变输出协议、预算排版、AstrBot 临时内容适配；
- `dev/memory_engine/output.py`：检索、证据复验、预算组装、注入前复验；
- `dev/tests/test_memory_output.py`：交接包 37 项输出模块测试；
- `dev/tests/test_host_shadow_output.py`：宿主注入接线 2 项测试；
- `dev/examples/memory_output/demo.py`：输入→基座→输出→临时注入演示；
- `dev/scripts/verify_native_output_contract.py`：AstrBot 源文件契约验证；
- `dev/host_shadow/main.py`：`SHADOW_OUTPUT_MODE` / `SHADOW_MEMORY_BUDGET` 接线；
- `dev/releases/digital_life_memory_output-0.1.0-py3-none-any.whl` + `.sha256`。

## 接线方式

默认 `SHADOW_OUTPUT_MODE=off`，只观察、不改请求。显式开启：

```powershell
$env:SHADOW_OUTPUT_MODE = 'inject'
$env:SHADOW_MEMORY_BUDGET = '512'
```

开启后仅修改 `ProviderRequest.extra_user_content_parts`，记忆块使用
`TextPart.mark_as_temp()`；`prompt`、`system_prompt`、`contexts`、持久化历史与回复不变。
查询必须与 `req.prompt` 完全一致。证据删除/撤回/修订、scope 变化、问题改变、
预算缩水或输出被篡改时，旧块被拒绝并移除。

`shadow_events.jsonl` 新增 `output_status`（INJECTED / UNCHANGED / EMPTY / REJECTED /
UNAVAILABLE）、`output_reason`、`budget_tokens`、`output_reasons`；控制台“观察日志”
显示输出状态，“环境”页显示输出模块可用性与接线方式。

## 插件页控制台

`host_shadow` 同时把 `dev/dlm_console` 内嵌为 AstrBot 插件页：

- `dev/host_shadow/console_api.py`：把 `Console` 的查询/维护方法注册到
  `/astrbot_plugin_memory_shadow/console/*`（24 条路由）；
- `dev/host_shadow/views/console/`：插件页前端（与 `dev/dlm_console/web/` 字节一致）；
- `metadata.yaml` 声明 `views.console`；
- `SHADOW_CONSOLE_READONLY=1` 禁用备份/恢复/删除/VACUUM；
- 前端通过 `AstrBotPluginPage` bridge 调用，独立模式仍用 token + `fetch`。

未启动真实 AstrBot WebUI；本轮验证的是路由注册、资源一致性、复用 `Console` 数据
和独立模式不回归，不能替代浏览器内实际点击验收。

## 验证

| 检查 | 结果 |
|---|---|
| 交接包输出模块测试 | 37 / 37 通过 |
| 宿主注入接线测试 | 2 / 2 通过 |
| 插件页控制台接线测试 | 4 / 4 通过 |
| 全量 unittest | 715 / 715 OK（1 项跳过） |
| AstrBot v4.29.3 源文件契约 | PASS；模型可见记忆、持久化历史不含记忆、其他内容保留、重复去重 |
| 独立 wheel 安装 | PASS；临时目录中仅安装 wheel 后可导入 `memory_output` |
| 默认关闭 | PASS；off 模式下 `extra_user_content_parts` 不变 |

契约验证源文件哈希：

- `message.py`：`aaa62b00606dad148a4ece617f2024cc882012d7a4da07439539e9015516b22f`
- `entities.py`：`1d2f1c416f8e5ffd847a2e5424641ddfa2dc6dacdda02453434dc0e3f57b68b3`

## 未完成与边界

- 未在真实 AstrBot/QQ 长跑，未验证真实模型收益；
- 注入默认关闭，控制台不能替 AstrBot 进程开启；
- Token 预算依赖调用方 `TokenCounter`（需要 `tiktoken`），不等于供应商计费；
- JSON 转义和资料说明不构成提示注入免疫；
- 证据安装完成到宿主发送之间有窗口，宿主需在靠近发送处调用；
- 未改变 R0/R1 的 NO-GO 状态，也未接入生产或生产 Shadow。
