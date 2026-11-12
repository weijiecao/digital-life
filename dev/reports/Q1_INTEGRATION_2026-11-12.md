# Q1 查询准入补丁集成与插件交付（2026-11-12）

- 补丁：`from_9565524.patch`（15 文件，21 项专项）
- 基线：`9565524`
- 测试：**590/590**
- 结论：**R0/R1 NO-GO；AstrBot 插件包可安装验收**

## 1. 补丁集成

已应用 Q1 查询准入补丁，未叠加旧 RC2 补丁。新增：

- `memory_engine/admission.py`：结构化准入，SUPPORTED / RELATED_ONLY / INSUFFICIENT / CONFLICT；
- 语义与词面双路候选，保留各自排名与分数；
- 时间、金额、时长、地点、楼层、联系人、号码、编号等事实只从当前有效原文读取；
- 主体（本人/朋友/家人）、现实性/故事、周末/星期冲突；
- 引用 span 校验、并发失效复核、失败降级；
- `retrieve_with_audit()` 与 CLI `search --explain`。

## 2. 验证

| 检查 | 结果 |
|---|---:|
| Windows 本地全量 | **590/590** |
| Q1 开发反例 | 正例 11/11，额外注入 0；负例 0/12 |
| 闭环（自动/重试/重启/缓存/预算/200 次查询） | 11/11 |
| RC3 → Q1 包 → RC3 升级/回退 | 6/6 |
| 冻结 quality-v2 | dev 12/12；holdout **11/12（FAIL）** |
| 冻结 quality-v3（修正一处矛盾标签） | dev 12/12；holdout **12/12** |

quality-v2 的失败没有被改写：`story2` 的“虚构人物周末去海边”与“周四去海边”是周末冲突，原标签错误地期望 current；v3 将这一条改为 `time_old` 负例并重新冻结。v2 证据保留。

## 3. 包

### QueryGuard 运行包

- 文件：`releases/memory-engine-rc3-queryguard.zip`
- 39 文件、68,022 bytes
- SHA-256：`82874bf227ce0f5cdd13009119c6e96466e6cd89cdc911c087399b616fa285da`
- 隔离 smoke：7/7
- exact budget：PASS
- 结论：`PACKAGE_VERIFIED_NOT_RELEASE_GO`

### AstrBot 插件包

- 文件：`releases/astrbot_plugin_digital_memory_store-1.0.19.zip`
- 72 文件、238,552 bytes
- SHA-256：`977cb6a143fe8c915f270fb2d646bb1f6746ad168e7fd780b9ae3e3d7dbfddb6`
- 顶层目录：`astrbot_plugin_digital_memory_store/`
- 已校验：metadata、必需文件、无 `.git`/`__pycache__`、52 个 Python 文件语法编译通过
- 已生成同名 `.json` 与 `.sha256`

## 4. GO / NO-GO

- **AstrBot 插件包**：可交给目标 AstrBot 做安装/升级/回退验收。
- **生产 GO**：仍为 **NO-GO**。
  - 质量集仍是合成/开发反例，不是独立真实聊天或人工盲评；
  - 本补丁的 Windows/3.10/3.11 CI 尚未执行；本轮只验证了 Windows 3.12 本地与 590 项回归；
  - 真实 AstrBot/NapCat 72h、用户效果、错误/回退记录缺失；
  - 断电/fsync 未纳入。

## 5. 证据

- `reports/q1_results/challenge.json`
- `reports/q1_results/quality_v2_known_fail.json`
- `reports/q1_results/quality_v3.json`
- `reports/q1_results/closed_loop.json`
- `reports/q1_results/lifecycle.json`
- `reports/q1_results/unittest.log`
- `reports/q1_results/plugin_package.json`
