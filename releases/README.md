# Releases

本目录保存**当前整体版本**的安装包、发布清单与校验值。完整交付集合（源码 ZIP、工程 ZIP）
在 `dist/release/` 生成后作为 Release 附件分发；旧版本一律移到 `archive/releases/`。

## 当前版本：0.8.0-dev.1

版本号来自仓库根 `VERSION`；`dev/astrbot_plugin_digital_life/metadata.yaml` 由
`python tools/sync_version.py` 派生，CI 校验两者一致。

`astrbot_plugin_digital_life-0.8.0-dev.1.zip` —— 统一插件开发版，**79 个文件，
223,273 bytes，SHA-256 `05ba15e9d98792220c7ddf1615bad8448c178175626a6ebbc529c57a9ec90cff`**。

| 文件 | 说明 |
| --- | --- |
| `astrbot_plugin_digital_life-0.8.0-dev.1.zip` | 安装包（AstrBot WebUI 上传这个文件） |
| `astrbot_plugin_digital_life-0.8.0-dev.1.sha256` | 上述包的 SHA-256 |
| `digital-life-release-0.8.0-dev.1.json` | 发布清单：版本、来源提交、附件哈希与大小、验证边界 |
| `README.md` | 本文件 |

这是整体开发基线，不是 RC。安装或升级后应完整重启 AstrBot，热重载不能清除既有顶层模块。

### 与上一版的关系

功能与 `0.7.0-dev.3` / `0.7.0-dev.2` 相同；本版的变化是工程契约：

- 整体版本从 `0.7.0-dev.3` 升到 `0.8.0-dev.1`，唯一版本源改为仓库根 `VERSION`；
- 插件、基座、引擎、输入、输出、应用服务与 UI 共用一个对外版本号；
- 新增包级验收入口（47＋137＋20）与完整交付集合（源码 ZIP、工程 ZIP、发布 JSON、校验值）；
- 导出工具补齐清单校验与覆盖保护，验收记录绑定同一提交与同一包哈希。

**没有数据库 schema、记忆组织、模型策略或输出 v2 语义的改动。**

## 从源码重建

```powershell
python tools/sync_version.py --check
python tools/build_unified_plugin.py --out-dir dist --require-clean
python tools/verify_unified_plugin.py --dist dist --report dist/package_verification.json
python tools/run_package_regression.py --dist dist --suites completion,core,legacy
python tools/build_engineering_bundle.py --dist dist --out-dir dist/release
python tools/check_release_set.py --release-dir dist/release
```

同一份源码连续构建两次、以及在干净副本中构建，插件包哈希一致。

## 完整交付集合

`dist/release/` 中的文件（由 `tools/check_release_set.py` 校验，全部同名同版本）：

| 文件 | 内容 |
| --- | --- |
| `astrbot_plugin_digital_life-<版本>.zip` | 可安装插件与全部运行依赖 |
| `digital-life-source-<版本>.zip` | 该提交的完整可构建源码 |
| `digital-life-engineering-<版本>.zip` | 源码 ZIP + 插件 ZIP + 说明 + 验收摘要 |
| `digital-life-release-<版本>.json` | 版本、来源提交、各附件大小与 SHA-256、验证边界 |
| `SHA256SUMS-<版本>.txt` | 上述附件的校验值 |

集合里任一件混入其他版本、缺件或哈希不符，检查都会失败。

## 历史交付

`archive/releases/` 保留原字节，仅作追溯：

- `astrbot_plugin_digital_life-0.7.0-dev.3.*`
- `astrbot_plugin_digital_life-0.7.0-dev.2.*`
- `astrbot_plugin_digital_memory_store-1.0.19.*`
- `memory-engine-rc3-queryguard.*`、`memory-engine-r0-rc3.*`

`python tools/check_repository.py` 会核对归档清单里的 SHA-256 是否仍与 ZIP 一致。

## 边界

这些包只用于隔离验收，不得接入 V1 生产或生产 Shadow。真实 AstrBot/NapCat 实例、
Windows 实机安装、浏览器页面与 72 小时长跑仍未验收；下载入口与验收级别见
`docs/CURRENT_STATUS.md`。
