# Releases

Status: **R0/R1 NO-GO**. Only isolated evaluation artifacts.

| File | Purpose |
|---|---|
| `astrbot_plugin_digital_memory_store-1.0.19.zip` | AstrBot plugin package (72 files, 238,565 bytes) |
| `memory-engine-rc3-queryguard.zip` | Q1 isolated engine package (39 files, 68,022 bytes) |
| `memory-engine-r0-rc3.zip` | Previous RC3 package, kept only for upgrade/rollback validation |

Each ZIP has an adjacent `.json` manifest and (for the plugin) `.sha256`.

## Verify

```powershell
python dev\scripts\verify_r0_bundle.py --bundle releases\memory-engine-rc3-queryguard.zip --exact-budget
python dev\scripts\package_lifecycle.py releases\memory-engine-rc3-queryguard.zip --previous-bundle releases\memory-engine-r0-rc3.zip
python dev\scripts\build_astrbot_plugin.py
```

Do not connect these packages to V1 production or a production Shadow until the
remaining real-host, independent-quality and 72h gates are satisfied.
