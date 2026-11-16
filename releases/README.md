# Releases

本目录保存**当前整体版本**的安装包、发布清单与校验值。完整交付集合（源码 ZIP、工程 ZIP）
在 `dist/release/` 生成后作为 Release 附件分发；旧版本一律移到 `archive/releases/`。

## 下载入口

用户下载固定在分发仓库的 Release：
[数字生命 0.8.0-dev.2](https://github.com/weijiecao/astrbot_plugin_digital_life/releases/tag/v0.8.0-dev.2)。
附件是完整的五件套（插件 ZIP、源码 ZIP、工程 ZIP、发布 JSON、`SHA256SUMS`）。
主仓库只保留同名 tag 与来源记录，不另做一份内容不同的公开 Release。

## 当前版本：0.8.0-dev.2

版本号来自仓库根 `VERSION`；`dev/astrbot_plugin_digital_life/metadata.yaml` 由
`python tools/sync_version.py` 派生，CI 校验两者一致。

`astrbot_plugin_digital_life-0.8.0-dev.2.zip` —— 统一插件开发版，**79 个文件，
223,273 bytes，SHA-256 `6706578c2e1c46ed00e856ef7d1b9bb1de1b723b1149bf44ae8b1eedd84981b3`**。
来源提交 `c42ceff`；源码回归 401 项、包级 47＋137＋20、DOM 7 条路径均通过，
并由“解压源码 ZIP 重建”检查确认源码附件能产出同一字节。

| 文件 | 说明 |
| --- | --- |
| `astrbot_plugin_digital_life-0.8.0-dev.2.zip` | 安装包（AstrBot WebUI 上传这个文件） |
| `astrbot_plugin_digital_life-0.8.0-dev.2.sha256` | 上述包的 SHA-256 |
| `digital-life-release-0.8.0-dev.2.json` | 发布清单：版本、来源提交、附件哈希与大小、验证边界 |
| `README.md` | 本文件 |

这是整体开发基线，不是 RC。安装或升级后应完整重启 AstrBot，热重载不能清除既有顶层模块。

### 与上一版的关系

功能与 `0.7.0-dev.3` / `0.8.0-dev.1` 相同；本版关闭 0.8.0-dev.1 验收报告中的 A01–A04：

- **A01**：源码 ZIP 归档强制 LF，打包器把 `text=auto` 规范化为 LF，发布检查新增
  “源码 ZIP → 用附件内构建器重建 → 与插件 ZIP 逐字节比较”；
- **A02**：正式验收记录要求提交/版本/包哈希逐项相等，并校验候选 ZIP 的实际字节；
- **A03**：`summary.json` 与 `index.json` 一次写入，`--check-evidence` 可复核；
- **A04**：托管路径上的符号链接、目录联接、硬链接一律拒绝，畸形清单直接拒绝。

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
