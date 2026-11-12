# 记忆基座匹配输出模块 v0.1 验证报告

日期：2026-11-12。实现提交：fdb7ca4；基线为输入模块交付 a5f3d3b。分支 codex/memory-base-output。当前实现已本地提交，未推送、发布或接管线上 AstrBot。

## 本次结果

新增独立 memory_output 包与引擎侧 MemoryOutputService，完成原生输入、记忆基座事件组织、检索证据、预算输出、临时宿主注入的离线闭环。输出模块不写入记忆事实，不改变原有事件组织逻辑。

标准输出包含请求和范围绑定、事件 ID/版本、证据片段/源哈希/偏移、来源记录时间、叙述主体、现实或虚构属性、最终正文、计数与降级原因。未知字段保持未知，来源记录时间不冒充事件发生时间。

输出前使用引擎既有查询准入；安装到宿主请求前再检查当前数据库中的证据。过期、被删除、被撤回、被修订、跨范围或问题已改变的输出均拒绝。最终正文包含说明和 JSON 封装后统一计数，超预算时只舍弃完整条目。

## 验证证据

| 检查 | 结果 |
|---|---|
| 输出模块针对性测试 | 37 / 37 通过 |
| 全量 unittest 回归 | 655 / 655 通过，19.021 秒 |
| 环境 | Linux Python 3.12；未执行 Windows 和 Python 3.10/3.11 验证 |
| 独立 wheel 构建和新环境安装 | 通过；该环境无 AstrBot、memory_engine、tiktoken |
| 真实 tokenizer | cl100k_base，最终文本包含所有外围说明与结构后计数 |
| 原生输入→引擎→输出→模拟宿主 | 通过；重复注入仅一个块；删除来源后拒绝并移除旧块 |
| 解压运行包后隔离执行新闭环示例 | 通过 |
| 运行包摘要、旧功能与精确预算冒烟验证 | 51 个文件；全部通过 |
| AstrBot v4.29.3 纯文本源代码协议验证 | 通过；模型能见记忆，持久化历史不含记忆，保留其他内容 |

37 项针对性测试覆盖结构化来源、角色与真实性保留、时间戳变更、JSON 副本隔离、来源删除、事件撤回/修订、直接改写证据、并发时序中的过期输出、伪造正文/来源、请求范围/问题绑定、预算边界、完整条目裁剪、计数器缺失或异常、其他插件内容保留、重复注入、空结果清理、临时标记能力缺失、工厂异常及输出不写记忆数据库。

AST 协议验证使用两份官方源文件：message.py 原样加载；ProviderRequest 类从 entities.py 原样抽取，避开未使用的媒体/模型 SDK 导入。执行其 assemble_context、消息模型转换和 dump_messages_with_checkpoints，验证临时内容的传递与保存过滤。**没有启动完整 AstrBot**，也未加载全部 provider、事件流水线或 QQ 连接。源文件摘要及详细结果在同名 JSON 报告中。

## 未完成与实际限制

- 尚未安装到用户 AstrBot，未注册完整宿主生命周期，未接入真实判别器、主动聊天及 QQ 实际请求。
- 旧有语义检索质量门槛不变。本次测试中，“故事青岛海边散步”无候选，而“故事里的小鱼去青岛海边散步”可召回；已记录为现有词面召回限制。来源属性保持测试使用可召回查询，不将此变化宣称为语义修复。
- Token 计数精确性取决于选用的 tokenizer。此处严格约束记忆块的分配预算，宿主仍需提供系统提示、当前输入、工具、其他内容和输出预留的预算，模型原生 Token 计费和多模态开销未验证。
- JSON 转义与资料用途说明用于结构隔离，并不证明模型完全免疫提示注入。
- 最终复验覆盖请求块安装时刻；安装完成到网络发送之间仍由宿主控制。需要在最终 on_llm_request 路径靠近发送处接线。
- 暂未做跨进程输出授权协议；to_json 是数据导出，不是可直接信任的注入凭证。

## 复现方式

从完整源码的 dev 目录执行：

```sh
python -m unittest tests.test_memory_output -q
python -m unittest discover -q -s tests -t .
python -m examples.memory_output.demo
```

安装引擎依赖使用 requirements-release.txt。示例的固定判别器和模拟宿主仅验证线路，不能替代真实模型判断。独立包及宿主接口用法见 memory_output/README.md。

官方协议参考：

- https://docs.astrbot.app/dev/star/guides/listen-message-event.html
- https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/agent/message.py
- https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/provider/entities.py
