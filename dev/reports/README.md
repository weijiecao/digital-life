# Reports index

This directory keeps only current delivery evidence and the frozen B4 trace
required by tests.

- `Q1_INTEGRATION_2026-11-12.md` — Q1 patch, 590 tests, quality-v3, packages.
- `NATIVE_INPUT_0_2_2026-11-12.md` — native input v0.2, host runtime hookup, 642 tests.
- `MEMORY_BASE_OUTPUT_0_1_2026-11-12.md` — output module handoff: protocol, evidence checks,
  temporary AstrBot injection, 37 targeted tests.
- `MEMORY_BASE_OUTPUT_0_1_INTEGRATION_2026-11-12.md` — current dev integration: host wiring,
  711-test regression, AstrBot source contract PASS.
- `REVIEW_FIXES_63e61de_2026-11-13.md` — review snapshot `63e61de` fixes F01–F10,
  behavior changes and the 742-test regression.
- `native_input_0_2_{targeted,full,demo}.log` — evidence for the above.
- `ENGINEERING_REPORT_2026-11-11.{md,docx,pdf}` — current engineering report.
- `MEMORY_ENGINE_ACCEPTANCE_2026-11-10.md` — acceptance matrix.
- `M0_BASELINE_MANIFEST.json` — source/test baseline.
- `FINAL_HANDOFF_2026-11-11.md` — delivery handoff.
- `q1_results/` — challenge, quality-v2 known fail, quality-v3, lifecycle.
- `rc3_results/` — RC3 quality/closed-loop evidence.
- `v1_5b_b4_cluster_prov_trace.json` — frozen trace used by R0-D tests.

Historical experiment logs and traces were removed to keep the repository
source-first. Releases live in `releases/`.
