# Releases

## 0.7.0-dev.2 统一插件

| File | Purpose |
|---|---|
| `astrbot_plugin_digital_life-0.7.0-dev.2.zip` | 单插件开发版（78 files, 222,031 bytes） |
| `astrbot_plugin_digital_life-0.7.0-dev.2.sha256` | SHA-256 `06756a5f…` |
| `astrbot_plugin_digital_life-0.7.0-dev.2.json` | Build manifest |

开发版，不是 RC；安装后完整重启 AstrBot。

历史发布包已统一移入 `archive/releases/`：

- `astrbot_plugin_digital_memory_store-1.0.19.*`
- `memory-engine-rc3-queryguard.*`
- `memory-engine-r0-rc3.*`

当前目录只保留最新插件 `astrbot_plugin_digital_life-0.7.0-dev.2.*`。

## Verify

```powershell
python dev\scripts\verify_r0_bundle.py --bundle releases\memory-engine-rc3-queryguard.zip --exact-budget
python dev\scripts\package_lifecycle.py releases\memory-engine-rc3-queryguard.zip --previous-bundle releases\memory-engine-r0-rc3.zip
python dev\scripts\build_astrbot_plugin.py
```

Do not connect these packages to V1 production or a production Shadow until the
remaining real-host, independent-quality and 72h gates are satisfied.
