# 数字记忆工程 · Digital Memory Engineering

数字记忆工程由两个部分组成：

- **数字记忆基座**：事件、证据、来源、Resolver、租约/回执/连续水位，以及 AstrBot 接入插件；
- **数字记忆引擎**：候选召回、证据准入、重评、上下文与渲染。

两者都独立于 V1 生产插件；当前只用于隔离验收。

**当前状态：R0 / R1 NO-GO。** 运行包与插件包只用于隔离验收；不要接入 V1
生产或生产 Shadow。

## 组件与目录

```text
dev/
  memory_engine/         数字记忆引擎：召回/准入/重评/上下文
  v1_5_consolidator/     数字记忆基座：事件/证据/Resolver/租约/回执/水位
  host_shadow/           AstrBot shadow 与长期采样工具
  tests/                 590 项回归
  evaluation/            当前质量/恢复验收脚本与冻结数据
  reports/               当前交付报告与证据
  scripts/               构建、校验、安装生命周期、AstrBot 打包
  astrbot_plugin_digital_memory_store/  数字记忆基座的 AstrBot 插件源码
releases/                AstrBot 插件包与 Q1 运行包
testbed/                 AstrBot/NapCat 测试床说明与 harness
env/                     环境模板与锁文件
```

插件源码也独立维护在
[`weijiecao/astrbot_plugin_digital_memory_store`](https://github.com/weijiecao/astrbot_plugin_digital_memory_store)。

## 快速验证

```powershell
cd dev
python -m pip install -r requirements-release.txt
python -m unittest discover -q -s tests -t .
```

当前结果：**590/590**（Windows 本地；Linux 3.10 有一项
`sqlite3.Connection.setlimit` 依赖跳过）。

质量验收需要本机 BGE 目录：

```powershell
python evaluation/query_admission_challenge.py --model-dir <BGE目录> --out challenge.json
python -m evaluation.quality_v3_accept --model-dir <BGE目录> --out quality-v3.json
```

## 交付物

| 文件 | 说明 |
|---|---|
| `releases/astrbot_plugin_digital_memory_store-1.0.19.zip` | 数字记忆基座 AstrBot 插件包（72 文件，238,565 bytes，SHA-256 `52f3e641…`） |
| `releases/memory-engine-rc3-queryguard.zip` | Q1 独立运行包（39 文件，68,022 bytes，SHA-256 `82874bf2…`） |
| `releases/memory-engine-r0-rc3.zip` | 上一版，仅用于升级/回退复验 |
| `dev/reports/Q1_INTEGRATION_2026-11-12.md` | Q1 集成、质量、包和 GO/NO-GO |
| `dev/reports/ENGINEERING_REPORT_2026-11-11.pdf` | 当前工程报告 |

## 已知边界

- 质量集是合成/开发反例，不是独立真实聊天或人工盲评；
- quality-v2 的 1 条矛盾标签保留为 FAIL，quality-v3 修正后单独冻结；
- 无真实 AstrBot/NapCat 72h、用户效果、生产错误/回退记录；
- 断电/fsync 未纳入；
- 本轮不修改 V1 生产插件、不部署、不接入生产 Shadow。

## License

MIT，见 `LICENSE`。插件详细安装/配置见插件目录的 `README.md` 与 `INSTALL.md`。
