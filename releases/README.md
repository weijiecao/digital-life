# Releases

本目录只保存**当前交付**的插件包及其清单。所有命令都在仓库根目录执行。

## 当前交付：0.7.0-dev.3

`astrbot_plugin_digital_life-0.7.0-dev.3.zip` —— 统一插件开发版，**79 个文件，
223,274 bytes，SHA-256 `631e114a9040b6c2c36e11bda3cc3351d2fb0e925048ce7b57c7b409f6f2e5a6`**。

| 文件 | 说明 |
| --- | --- |
| `astrbot_plugin_digital_life-0.7.0-dev.3.zip` | 安装包（AstrBot WebUI 上传这个文件） |
| `astrbot_plugin_digital_life-0.7.0-dev.3.sha256` | 上述包的 SHA-256 |
| `astrbot_plugin_digital_life-0.7.0-dev.3.json` | 发布清单：版本、哈希、字节数、文件数、来源提交 |
| `README.md` | 本文件 |

这是开发版，不是 RC。安装或升级后应完整重启 AstrBot，热重载不能清除既有顶层模块。

功能与 `0.7.0-dev.2` **相同**（迁移、实时追加与历史对账、恢复切换、撤回、运行权、
输出 v2、统一管理页）。dev.3 是仓库整理后的重新发布，变化只在交付形态：
版本号改为只从 `metadata.yaml` 读取、补齐构建/验包/导出入口、模块文档与回归同步、
文本换行按 `.gitattributes` 规范化。**没有运行行为改动。**

## 从当前源码重建

```powershell
python tools/build_unified_plugin.py --out-dir dist --require-clean
python tools/verify_unified_plugin.py --dist dist --report dist/package_verification.json
```

产物写在 `dist/`（Git 忽略）：

| 产物 | 内容 |
| --- | --- |
| `dist/astrbot_plugin_digital_life-<version>.zip` | 由 `tools/plugin_package.json` 白名单决定内容 |
| `dist/astrbot_plugin_digital_life-<version>.sha256` | 包校验和 |
| `dist/build_manifest.json` | 来源提交、版本、逐文件哈希、包哈希、构建规则版本、构建环境 |
| `dist/package_verification.json` | 解压后逐项检查结果（含是否使用宿主 stub / 真实源码） |

同一份源码连续构建两次、以及在干净副本中构建，得到的包哈希一致。

## 上一版与历史交付

`v0.7.0-dev.2`（78 文件 / 222,031 bytes / `06756a5f…`）已移到 `archive/releases/`，
保留原字节用于追溯，不再是当前交付。它与 dev.3 的差异就是 0.7.0-dev.3 的发布说明里列出的
版本号、版本单点读取、模块 README 与换行规范化。

更早的旧插件与旧引擎包同样在 `archive/releases/`：

- `astrbot_plugin_digital_memory_store-1.0.19.*`
- `memory-engine-rc3-queryguard.*`
- `memory-engine-r0-rc3.*`

`python tools/check_repository.py` 会核对归档清单里的 SHA-256 是否仍与 ZIP 一致。

## 边界

这些包只用于隔离验收，不得接入 V1 生产或生产 Shadow。真实 AstrBot/NapCat 实例、
Windows 实机安装、浏览器页面与 72 小时长跑仍未验收；详见 `docs/CURRENT_STATUS.md`。
