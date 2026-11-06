# 本地安装测试结果（2026-11-05）

- 执行对象：`dist-test-install-20261105_170126`（当前工作树构建，含备份 P0 修复）
- 包版本：**1.0.16**；SHA256 `AF19032D4F250B3FB3F0CC3701C74DFB401DE82545009B1B907C4CF98110644A`
- 结论：**通过**（A1–A5、B1–B7 全过）
- 机器：Windows 11 / Python 3.12.10 / AstrBot 4.29.3 / BGE `bge-small-zh-v1.5`

> 本轮 B 段用 `testbed/instance` 的**隔离副本** `instance_installtest`：
> 当时 9101/6201 已被既有 testbed 服务占用。副本保留真实 AstrBot 4.29.3、真实 BGE、
> WebChat 事件总线和本地 mock provider，只把 provider 指向 9102、关掉 NapCat 平台。
> 测试后已删除副本；真实 testbed 已恢复到测试前状态。

---

## 1. 测试 A：干净安装到一次性目录

| 检查 | 结果 | 证据 |
|---|---|---|
| `install.ps1` `self` 预设 | 成功 | `install_test_A_summary_20261105.json` |
| `verify_install` | `ok=true`，version 1.0.16，schema_keys 36，problems=[] | `install_test_A_verify_self_20261105.json` |
| 配置 | `enabled=true`、`automatic=true`、`ids=123456`、budget=450、threshold=60 | 同上 |
| `off` 预设 | `ok=true`，`enabled=false`、`always_context=false` | `install_test_A_verify_off_20261105.json` |
| 升级 | 旧插件进入 `plugins_backup` | summary `backups` |
| 回退 | 回退后 `verify_install.ok=true`，problems=[] | `install_test_A_verify_rollback_20261105.json` |

测试 A 临时目录：`%TEMP%\DLM-InstallTest-20261105_170132`（证据已复制到 `dev/reports/`，临时目录已清理）。

---

## 2. 测试 B：真实 AstrBot 链路

### 安装

- 用同一个 zip 的 `install.ps1` 安装到 `testbed/instance_installtest`；
- `verify_install`：`ok=true`、version 1.0.16、model_dir=true、problems=[]；
- 安装后的 `recovery.py` 含 `immutable=1`，`panel.py` 含 `BACKUP_NAME`，确认不是 15:07 的旧包。

### 真实链路（`real_chain_installtest.py`）

| 项 | 结果 |
|---|---|
| AstrBot | 4.29.3 启动成功 |
| 插件加载 | `plugin.loaded=true`，版本 1.0.16 |
| 钩子 | `add_memory_context(OnLLMRequestEvent)`、`capture_private(AdapterMessageEvent)`、`dlm(AdapterMessageEvent)` |
| 采集 | 种子后 `dl_capture=1`，最终 3 条 |
| 注入 | **1/1 命中**；`contains_memory_prefix=true`；hook→provider **0.343 s** |
| 命令 | `/dlm 记忆状态` 返回真实统计文本 |
| BGE | 512 维；加载 11.74 s；4.1 ms/条 |
| 只读审计 | 3 条原话、0 error |
| 备份 P0 | 3 次备份+校验后 `rotate(keep=2)` → **2 个真实备份、0 旁文件** |

证据：

- `dev/reports/real_env_installtest_isolated.json`
- `dev/reports/real_chain_iso_stdout.log`（成功那轮；另有 `real_chain_installtest_isolated.log` 为被 9101 旧代理干扰的尝试）
- `dev/reports/install_test_audit_20261105.json`
- `dev/reports/install_test_backup_20261105.json`
- `dev/reports/install_test_summary_20261105.json`
- `dev/reports/install_test_restored_verify_20261105.json`（testbed 恢复后自检：1.0.8 / 27 条）

---

## 3. 与流程的差异

1. **B 段使用隔离副本**：现有 testbed 的 `serve.py`/`mock_openai.py` 占用 6201/9101，
   且会与 `real_chain` 争用同一实例；因此复制 `instance` → `instance_installtest`，
   provider 改 9102、移除 NapCat 平台。真实 AstrBot/BGE/WebChat/mock 均未替换为替身。
2. **真实 testbed 已恢复**：测试前插件是 **1.0.8**，`plugin_data` 27 条；测试后已从
   `plugins_backup/preinstall_20261105_170144`、`plugin_data_backup_20261105_170144`、
   config `.bak-20261105_170144` 恢复，`verify_install ok=true`、27 条、integrity ok。
3. **testbed 服务**：测试期间为避开 9101/6201 冲突停掉了 `serve.py`/`mock_openai.py`；
   测试后已恢复 testbed 文件，DSH 后台任务又自动拉起了 `serve.py`/`mock_openai.py`
   （9101/6201 重新监听，插件仍是恢复后的 1.0.8）。soak 进程未动。
4. **测试 A 的升级/回退用 dev 源码**：验证的是安装脚本与备份/恢复逻辑，不是 zip 覆盖升级；
   zip 覆盖升级可用 `install.ps1` 对同一 root 再跑一次验证。

---

## 4. 判定

**本地安装测试通过。** 当前工作树构建的 1.0.16 测试包可以：

- 干净安装、配置生成、`off` 预设、升级备份、回退；
- 被真实 AstrBot 4.29.3 加载，注册 3 个钩子；
- 采集原话、注入 `[DL_MEMORY_V040]` 到真实 provider 请求体；
- `/dlm 记忆状态` 返回真实统计；
- 使用真实 BGE 512 维编码；
- 备份校验不产生旁文件，轮转保留真实备份。

**未覆盖**：真实 QQ/NapCat、真实回复模型质量、3 天 soak、真实语料准确率。
