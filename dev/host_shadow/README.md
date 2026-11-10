# Memory Engine Shadow Host Plugin

Independent AstrBot plugin that observes private text and LLM requests, writes
its own Memory Engine DB/log, and never modifies the request or reply.

## Install

```powershell
$env:DIGITAL_LIFE_DEV='C:\path\to\digital-life\dev'
$env:ASTRBOT_ROOT='C:\path\to\AstrBot\instance'
Copy-Item -Recurse .\host_shadow "$env:ASTRBOT_ROOT\data\plugins\astrbot_plugin_memory_shadow"
```

AstrBot loads `astrbot_plugin_memory_shadow`; set the same env vars for the
AstrBot process (systemd/Windows service or launcher).

## Files

- `shadow.sqlite3` — Memory Engine shadow DB;
- `shadow_events.jsonl` — one observation per LLM request.

## Guarantees

- no request mutation;
- no reply mutation;
- command messages (`/`, `!`) are ignored;
- only private text is captured.

## Not guaranteed

- 72h stability;
- real QQ/NapCat;
- production rollback;
- cache/host budget integration.
