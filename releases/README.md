# Releases

本目录只保存**已交付或待交付的插件包**及其清单。所有命令都在仓库根目录执行。

## 当前交付

`astrbot_plugin_digital_life-0.7.0-dev.2.zip` —— 统一插件开发版，78 个文件，
222,031 bytes，SHA-256 `06756a5f79f172e70e6be596b45047b64047a12d72f53d6d25b1ac9e0f02a929`。

| 文件 | 说明 |
| --- | --- |
| `astrbot_plugin_digital_life-0.7.0-dev.2.zip` | 已交付的开发版安装包 |
| `astrbot_plugin_digital_life-0.7.0-dev.2.sha256` | 上述包的 SHA-256 |
| `astrbot_plugin_digital_life-0.7.0-dev.2.json` | 上述包的构建清单（`files`/`bytes`/`sha256`） |

这是开发版，不是 RC。安装或升级后应完整重启 AstrBot，热重载不能清除既有顶层模块。

## 从当前源码重新构建

```powershell
python tools/build_unified_plugin.py --out-dir dist --require-clean
python tools/verify_unified_plugin.py --dist dist --report dist/package_verification.json
```

产物写在 `dist/`（Git 忽略），不在 `releases/`：

| 产物 | 内容 |
| --- | --- |
| `dist/astrbot_plugin_digital_life-<version>.zip` | 由 `tools/plugin_package.json` 白名单决定内容 |
| `dist/astrbot_plugin_digital_life-<version>.sha256` | 包校验和 |
| `dist/build_manifest.json` | 来源提交、版本、逐文件哈希、包哈希、构建规则版本、构建环境 |
| `dist/package_verification.json` | 解压后逐项检查结果（含是否使用宿主 stub） |

### 与已交付包的关系

当前源码构建出的包是 **79 文件 / 222,925 bytes / SHA-256 `08c90bbd…`**，与已交付的
78 文件 / 222,031 bytes / `06756a5f…` 不同。差异已逐文件核对：

- 新增 `memory_engine/README.md`（模块职责与公开接口说明，随包分发）；
- `README.md`、`astrbot_memory_input/README.md`、`dlm_console/README.md`、
  `memory_output/README.md` 的文档内容更新（删除失效命令、改写旧宿主接线描述）；
- `main.py`、`metadata.yaml`、`requirements.txt` 与插件 `README.md` 的换行由 CRLF
  规范化为 LF（与 `.gitattributes` 一致），字节数因此变化。

- 已交付包保留原字节，不再用于验证"能否从源码重建"；
- 换行规范化后的构建是可复现的（同一规范连续构建两次哈希一致）；
- 版本号没有变化，因此新字节不覆盖 `releases/` 里的旧交付物。下一次正式交付应使用
  新的候选标识（例如 `0.7.0-dev.3`），而不是把新字节命名为同一版本。

## 历史交付

旧插件与旧引擎包在 `archive/releases/`，仅作追溯：

- `astrbot_plugin_digital_memory_store-1.0.19.*`
- `memory-engine-rc3-queryguard.*`
- `memory-engine-r0-rc3.*`

它们对应的构建与校验脚本也已归档，`python tools/check_repository.py` 会核对归档清单里的
SHA-256 是否仍与 ZIP 一致。

## 边界

这些包只用于隔离验收，不得接入 V1 生产或生产 Shadow。真实 AstrBot/NapCat 安装、
Windows 实机、浏览器页面与 72 小时长跑仍未验收；详见 `docs/CURRENT_STATUS.md`。
