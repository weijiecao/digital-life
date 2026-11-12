# 数字记忆 · 原生输入模块 v0.2 与宿主接线（2026-11-12）

- 基线：交接包 `ASTRBOT_NATIVE_INPUT_0_1_HANDOFF_5878a5b`（实现提交 5878a5b，基于 00a730d）
- 当前开发树：`dev/`（Q1 查询准入之后，全量 618 项）
- 本版结果：**642/642 通过**（618 + 新增 24），wheel 可独立安装
- 结论：输入模块在引擎侧与 AstrBot 宿主侧均已接通；**仍不是生产 GO**，见第 6 节

## 1. 本版做了什么

v0.1 交付的是可独立安装的输入包与引擎桥接，明确未完成「运行时自动读取与生命周期挂接」和「真实判别器接线」。本版补上这两层，并保持输入契约不变：

| 新增 | 内容 |
|---|---|
| `memory_engine/native_host.py` | `build_origin`（事件→来源，群聊/缺字段拒绝）、`history_from_conversation` / `read_conversation`（AstrBot 会话历史）、`NativeInputService`（stage→deliver→run_once，冲突与统计）、`NativeInputPump`（周期判别与暂缓重试）、`AstrBotProvider`（宿主 `llm_generate` 端口） |
| `memory_engine/native_judge.py` | `NativeRelationJudge`：把引擎既有 `RelationJudge`/`ContextGateway`/provider 接成 judge 协议；提示词超预算暂缓、连续失败冷却；`build_provider` 显式构造 `none/mock/openai` |
| `host_shadow/main.py`（重写装配） | 私聊消息到达 → 读取当前对话历史 → 原生输入 → 周期判别；观察钩子按同一作用域取上下文；`initialize` 启动、`terminate` 停止；判别器默认不配置 |
| `tests/test_native_input_host.py` | 20 项：来源映射、历史解析、服务、判别器、provider、泵 |
| `tests/test_host_shadow_native.py` | 4 项：AstrBot 替身里的采集→判别→检索、观察记录、无会话管理器回退、群聊跳过 |
| `examples/astrbot_native/host_demo.py` | 离线可跑的宿主接线演示（自动把 `dev` 加入 `sys.path`） |
| 文档 | `astrbot_memory_input/README.md` 升到 v0.2、`host_shadow/README.md` 重写、`docs/ARCHITECTURE.md` 补输入层 |

对既有文件的改动只限于：

- `memory_engine/native_input.py`：候选补 `event_type`/`anchor_text`/`provenance`（判别器可见证据与来源），`NativeInputInbox.counts()`；
- `astrbot_memory_input/pyproject.toml`：0.1.0 → 0.2.0；
- `host_shadow/metadata.yaml`：0.1.0 → 0.2.0；
- `dev/README.md`、`dev/docs/ARCHITECTURE.md`：目录与层说明。

## 2. 实际验证

| 检查 | 结果 |
|---|---|
| v0.1 原生输入 + 桥接（`tests.test_astrbot_memory_input`） | 28/28 |
| 新增宿主接入（`tests.test_native_input_host`） | 20/20 |
| 新增 shadow 装配（`tests.test_host_shadow_native`） | 4/4 |
| 全量 unittest（Windows 11 / Python 3.12.10） | **642/642**，1 项跳过，约 54 秒 |
| wheel 构建 `digital_life_astrbot_input-0.2.0-py3-none-any.whl` | 通过，11,284 bytes，`0407f1a7…cbc14e41` |
| 隔离安装（`--no-index --no-deps --target`） | 通过，无 AstrBot / memory_engine 依赖 |
| 隔离 CLI 导入示例 history | `{"new": 3, "duplicates": 0}` |
| `examples/astrbot_native/host_demo.py` | 3 条历史 → 2 条用户原话入队、1 建档 1 归并、检索到同一事件的两条证据 |

覆盖点：

- 来源只从宿主字段推导；群聊、缺发送者、缺机器人、缺会话、自己发的消息都跳过，不猜；
- AstrBot `history` 的 JSON 字符串/列表都能读；非法 JSON 归为宿主输入错误；
- 同一历史重复提交不新增；裁剪/改写整批拒绝并记冲突；
- 历史按「完整只追加快照」导入，稳定 ID 单条回退只在没有会话管理器时使用；
- 没有判别器时只入队、不建档；
- 判别器经真实 `RelationJudge` 解析：候选覆盖不全 → 暂缓；提示词超预算 → 暂缓不截断；连续失败 → 冷却；
- 判别器异常不阻断同轮其它条目；停机时先停泵再关库。

## 3. 环境

| 项 | 值 |
|---|---|
| 操作系统 | Windows 11 (10.0.26200) |
| Python | 3.12.10（`testbed/.venv`） |
| tiktoken | 0.14.0（宿主观察用精确分词器） |
| AstrBot | 未安装；宿主用 `sim_astrbot` 与自建替身验证契约 |

v0.1 的 Linux 3.12 结果保持不变；本版未在 3.10/3.11 或 Linux 重跑。

## 4. 复现

```powershell
cd dev
python -m unittest tests.test_astrbot_memory_input tests.test_native_input_host tests.test_host_shadow_native -v
python -m unittest discover -q -s tests -t .
python examples\astrbot_native\host_demo.py
python -m pip wheel .\astrbot_memory_input --no-deps --no-build-isolation -w .\releases
```

## 5. 证据

- `reports/native_input_0_2_targeted.log` — 三个模块的逐项结果
- `reports/native_input_0_2_full.log` — 全量回归
- `reports/native_input_0_2_demo.log` — 示例输出
- `releases/digital_life_astrbot_input-0.2.0-py3-none-any.whl` 与 `.sha256`

## 6. 边界与未完成

1. **判别质量未验证。** 用例用脚本化 provider 与 `MockProvider`，只证明「提示词→解析→校验→事务」线路；真实模型的事件归并、故事识别与检索语义仍需独立评估，未改变既有质量门槛与 GO/NO-GO。
2. **未在真实 AstrBot/NapCat 上运行。** shadow 插件只在替身运行时里装配验证；真实平台的身份字段、会话历史完整性与生命周期顺序仍需实机确认。
3. **历史窗口限制。** AstrBot 只在回复成功后写入消息对；失败轮次可能永不出现在历史中。历史被人工改写时本版整批拒绝（保留冲突记录），不自动修订、删除旧证据。
4. **一次判别可能较慢。** 工作线程与引擎库同线程（sqlite 连接不跨线程），每轮最多 10 条、单次模型超时默认 20 秒；高峰时观察钩子可能排队。未做长稳压测。
5. **没有请求侧注入。** 本模块只负责输入；AstrBot 请求注入仍是插件既有链路，原生输入的事件目前只进入引擎库与 shadow 观察日志。
6. **升级不迁移旧 shadow 数据。** v0.1 的 `shadow-*` 作用域不会被 v0.2 读取；旧库保留作对照。
