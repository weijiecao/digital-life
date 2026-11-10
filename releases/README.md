# Memory Engine R0 Release Candidate 1

**Status: NO-GO.** This bundle is for offline engineering review only. It is not
a production release and must not be connected to V1 or a live chat platform.

## Contents

- `v1_5_consolidator/` — store, resolver, provenance guard, candidate index;
- `memory_engine/` — ingest/retrieve/context, rejudge, worker, cache, backup,
  render, host budget;
- `host_shadow/` — independent AstrBot shadow plugin and soak sampler;
- selected reports under `reports/`.

## Quick start

```powershell
# extract
Expand-Archive .\memory-engine-r0-rc1.zip -DestinationPath .\r0
cd .\r0

# run one ingest/search
python -m memory_engine.cli --db .\r0.sqlite3 ingest --scope s --seq 1 --text "我喜欢乌龙茶"
python -m memory_engine.cli --db .\r0.sqlite3 search --scope s --query "乌龙茶"
python -m memory_engine.cli --db .\r0.sqlite3 context --scope s --query "乌龙茶"
```

Python 3.12 is recommended. `tiktoken` is optional; without it token budgets are
estimated rather than exact.

## Verification

From the repository checkout:

```powershell
python dev\scripts\verify_r0_bundle.py
```

Expected: SHA-256, bytes, file count, unpacked ingest/retrieve and integrity all
pass.

## Known gaps

- no independent real gold;
- no 72h real-host soak;
- no power-loss/fsync evidence;
- no real QQ/NapCat;
- M4 thresholds are synthetic.

See `reports/MEMORY_ENGINE_ACCEPTANCE_2026-11-10.md` for the full matrix.
