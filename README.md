# 数字记忆工程 · Digital Memory Engineering

**愿在时间的长河中，我们可以再次相遇。  
而记忆，是我们留给未来的坐标。**

数字记忆工程由两个部分组成：

- **数字记忆基座**：事件、证据、来源、Resolver、租约/回执/连续水位，以及 AstrBot 接入插件；
- **数字记忆引擎**：候选召回、证据准入、重评、上下文与渲染，以及可选的证据输出和 AstrBot 临时注入。

两者都独立于 V1 生产插件；当前只用于隔离验收。

**当前状态：R0 / R1 NO-GO。** 运行包与插件包只用于隔离验收；不要接入 V1
生产或生产 Shadow。

## 统一插件开发版 0.7.0-dev.2

完整单插件源码位于 `dev/astrbot_plugin_digital_life/`，安装包位于
`releases/astrbot_plugin_digital_life-0.7.0-dev.2.zip`。

- 版本：`0.7.0-dev.2`
- SHA-256：`06756a5f79f172e70e6be596b45047b64047a12d72f53d6d25b1ac9e0f02a929`
- 已包含：旧基座迁移、实时追加、恢复切换、偏好排序/短指代/主动请求记忆入口、统一管理页
- 仍是开发版：真实 AstrBot/NapCat、Windows 和长跑验收未完成

## 组件与目录

```text
dev/
  memory_engine/         数字记忆引擎：召回/准入/重评/上下文/输出
  memory_output/         记忆输出协议与 AstrBot 临时注入适配（独立包）
  v1_5_consolidator/     数字记忆基座：事件/证据/Resolver/租约/回执/水位
  astrbot_plugin_digital_life/  最新单插件（0.7.0-dev.2）
  dlm_console/           独立本地 Web 控制台（基座/引擎/输入/观察/维护）
  tests/                 当前活动回归
  evaluation/            当前质量/恢复验收脚本与冻结数据
  reports/               当前交付报告与证据
  scripts/               当前构建、校验、安装生命周期
archive/                 旧插件源码、旧发布包、旧测试与旧脚本
releases/                最新插件包（旧包已移入 archive/releases）
testbed/                 AstrBot/NapCat 测试床说明与 harness
env/                     环境模板与锁文件
```

最新插件源码在本仓库 `dev/astrbot_plugin_digital_life/`；对应的独立发布仓库为
[`weijiecao/astrbot_plugin_digital_life`](https://github.com/weijiecao/astrbot_plugin_digital_life)。

## 快速验证

```powershell
cd dev
python -m pip install -r requirements-release.txt
python -m unittest discover -q -s tests -t .
```

当前活动 `dev/tests` 结果：**333 项 OK（1 项跳过）**（Windows 本地）。旧插件相关
测试已随源码移入 `archive/tests/`，不计入活动回归。

质量验收需要本机 BGE 目录：

```powershell
python evaluation/query_admission_challenge.py --model-dir <BGE目录> --out challenge.json
python -m evaluation.quality_v3_accept --model-dir <BGE目录> --out quality-v3.json
```

## 本机控制台

双击根目录的 `启动数字记忆控制台.cmd`，或在 `dev` 目录运行：

```powershell
python -m dlm_console
```

控制台默认只绑定 `127.0.0.1`、每次启动生成令牌，统一查看数字记忆基座、影子引擎、
原生输入队列与观察日志，并提供备份/轮转/checkpoint/完整性/VACUUM/归档。
观察日志显示每次请求的输出/注入状态，环境页显示输出模块是否可用。

它也可以直接作为 AstrBot 插件页运行：WebUI → 插件 → **Memory Engine Shadow** →
**数字记忆控制台**，无需额外端口或令牌。独立启动方式保留给离线场景。
详见 `dev/dlm_console/README.md` 与 `dev/astrbot_plugin_digital_life/README.md`。

## 输出与注入

`memory_output` 把引擎已准入的证据组装成预算内、可复验的临时上下文块。
宿主注入由 `dev/astrbot_plugin_digital_life` 统一接线，默认 `COLLECT` 不注入；
只有显式切换 `ASSIST` 才注入。

开启后只修改 `ProviderRequest.extra_user_content_parts`：记忆块标记为临时、
不写入持久化历史，`prompt` / `system_prompt` / `contexts` 与回复保持不变。
证据删除、撤回、修订、scope 变化或预算缩水时，旧块会被拒绝并移除。
详见 `dev/memory_output/README.md` 与 `dev/astrbot_plugin_digital_life/README.md`。

## 交付物

| 文件 | 说明 |
|---|---|
| `releases/astrbot_plugin_digital_life-0.7.0-dev.2.zip` | 最新单插件开发版（78 文件，222,031 bytes，SHA-256 `06756a5f…`） |
| `archive/releases/` | 旧插件与旧引擎发布包（仅历史查阅） |
| `dev/reports/Q1_INTEGRATION_2026-11-12.md` | Q1 集成、质量、包和 GO/NO-GO |
| `dev/reports/MEMORY_BASE_OUTPUT_0_1_2026-11-12.md` | 输出模块协议、证据复验与注入验证 |
| `dev/reports/MEMORY_BASE_OUTPUT_0_1_INTEGRATION_2026-11-12.md` | 输出模块并入当前 dev、宿主接线与 758 项回归 |
| `dev/reports/ENGINEERING_REPORT_2026-11-11.pdf` | 当前工程报告 |

## 已知边界

- 质量集是合成/开发反例，不是独立真实聊天或人工盲评；
- quality-v2 的 1 条矛盾标签保留为 FAIL，quality-v3 修正后单独冻结；
- 无真实 AstrBot/NapCat 72h、用户效果、生产错误/回退记录；
- 输出注入默认关闭，尚未在真实 AstrBot/QQ 长跑，也未评估真实模型收益或提示注入免疫；
- 输出预算依赖调用方 tokenizer，不等于供应商实际计费；
- 断电/fsync 未纳入；
- 本轮不修改 V1 生产插件、不部署、不接入生产 Shadow。

## License

MIT，见 `LICENSE`。插件详细安装/配置见插件目录的 `README.md` 与 `INSTALL.md`。
