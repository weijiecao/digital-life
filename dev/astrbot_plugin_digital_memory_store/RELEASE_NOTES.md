# 发布说明 v1.0.10

继续拆结构，**行为不变**：上一版把 `storage.py` 拆成 `store/` 包后，`store/context.py` 仍有 945 行，
这版按职责分成三个 mixin。

## 拆成什么样

| 模块 | 行数 | 职责 |
|---|---|---|
| `store/context_sources.py` | 475 | 事件准备（`_prepare_context_event`）、语义分组、主题与向量同步、未关联语义 |
| `store/context_packet.py` | 367 | 待审上下文、多事件包、背景兜底、`context_packet`（预算拼装） |
| `store/context_followup.py` | 153 | 指代追问（`_auto_followup`）与 `context_turn` |
| `store/context.py` | 7 | `class ContextMixin(SourceMixin, PacketMixin, FollowupMixin)` 组合入口 |

`Store` 的 MRO 多了三层中间类，调用方式与行为完全不变。

## 顺带补上的工程设施

**GitHub Actions CI**（`.github/workflows/tests.yml`）：`windows-latest` + Python 3.12 跑同一套
**337 项**。测试全部用替身（stub 的 AstrBot、假编码器），**不需要 torch、不需要模型、不联网**，
所以能在干净 runner 上 3 分钟内跑完。

CI 第一次运行就抓到一个真问题：

> `testbed/harness/attribution.py` 里 `print()` 输出中文，在**英文 Windows**（控制台 cp1252）上
> 直接 `UnicodeEncodeError` 崩溃。这不是 CI 特有的——任何英文系统的用户跑这个归因工具都会撞上。

修复方式：新增 `emit()`，`print` 抛 `UnicodeEncodeError` 时退回 `sys.stdout.buffer` 写 UTF-8 字节。
本机用 `PYTHONIOENCODING=cp1252` 复现过崩溃、确认修复后不再复现。

## 验证

| 项 | 结果 |
|---|---|
| 离线回归 | **337/337**（本机） |
| GitHub Actions | **337/337 通过**（windows-latest / Python 3.12，3 分钟） |
| 真实 AstrBot 4.29.3 | 上一版已验证：加载 ✅、钩子 3 个 ✅、采集/注入正常 |
| 插件 zip | 通过 AstrBot 自带 `inspect_plugin_archive`（version=1.0.10） |

## 已知限制（不变）

- 只支持私聊纯文本；群聊/图片/语音/多设备不支持。
- 语义阈值基于自造语料，真实分布下可能下降。
- 指代追问默认接不住；开启候选筛选器会外发原话。
