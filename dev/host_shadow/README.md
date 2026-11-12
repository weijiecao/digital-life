# Memory Engine Shadow Host Plugin

Independent AstrBot plugin that feeds private conversations into the offline
Memory Engine through the native input pipeline. By default it only observes
LLM requests; with `SHADOW_OUTPUT_MODE=inject` it also prepares evidence-backed
memory and adds it as a **temporary** provider-facing content block. It never
changes the reply, `prompt`, `system_prompt`, or persisted history.

## What it does

| Step | Behaviour |
|---|---|
| Collect | On each private message, read the current AstrBot conversation history (`conversation_manager`) and submit the whole snapshot. |
| Validate | Roles, content and identity are validated by `astrbot_memory_input`; only non-empty `user` text is queued, other roles are archived. |
| Dedupe | Repeated snapshots add nothing; rewritten/truncated snapshots are rejected as a batch and recorded as a conflict. |
| Judge | Opt-in: a relation judge (`memory_engine/native_judge.py`) over an AstrBot provider or an OpenAI-compatible endpoint. |
| Store | Events, evidence, provenance and receipts stay in the Memory Engine DB. |
| Output | Opt-in: `memory_output` + `memory_engine/output.py` build a budgeted evidence block, re-check the current DB, and attach it as a temp `TextPart`. |
| Console | The plugin registers a `views/console` page and `/astrbot_plugin_memory_shadow/console/*` APIs, reusing `dlm_console` for logs, backups and maintenance. |

Without `SHADOW_JUDGE_PROVIDER` the plugin only queues history. It creates **no
events**: "no model configured" must not become "the model said this is a new
event".

## Install

```powershell
$env:DIGITAL_LIFE_DEV='C:\path\to\digital-life\dev'
$env:ASTRBOT_ROOT='C:\path\to\AstrBot\instance'
Copy-Item -Recurse .\host_shadow "$env:ASTRBOT_ROOT\data\plugins\astrbot_plugin_memory_shadow"
```

AstrBot loads `astrbot_plugin_memory_shadow`; set the same env vars for the
AstrBot process (systemd/Windows service or launcher).

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DIGITAL_LIFE_DEV` | — | Path to the `dev` tree (required). |
| `ASTRBOT_ROOT` | `.` | AstrBot instance root; data goes to `data/plugin_data/astrbot_plugin_memory_shadow`. |
| `SHADOW_PERSONA_ID` | `default` | Persona id used in the origin key. Set it to the persona you actually run; it is not inferred. |
| `SHADOW_JUDGE_PROVIDER` | `none` | `none` / `mock` / `astrbot` / `openai`. Only `none` is silent; the others call a model. |
| `SHADOW_JUDGE_PROVIDER_ID` | — | AstrBot chat provider id (required for `astrbot`). |
| `SHADOW_JUDGE_MODEL` / `SHADOW_JUDGE_BASE_URL` / `SHADOW_JUDGE_API_KEY` | — | OpenAI-compatible endpoint (required for `openai`). |
| `SHADOW_JUDGE_POLICY` | `native-input-0.2` | Policy version; changing it re-judges deferred items. |
| `SHADOW_JUDGE_BUDGET_CHARS` | `6000` | Prompt character cap; larger inputs are deferred, not truncated. |
| `SHADOW_JUDGE_TIMEOUT` | `20` | Seconds per model call (thread-bridged, does not block the loop). |
| `SHADOW_TICK_SECONDS` | `5` | Worker interval. |
| `SHADOW_TICK_LIMIT` | `10` | Records judged per tick. |
| `SHADOW_RETRY_EVERY` | `12` | Every N ticks, retry all deferred items. |
| `SHADOW_HOST_TOTAL_TOKENS` / `SHADOW_OUTPUT_TOKENS` | `8192` / `800` | Host context envelope; the second value is the reply reserve, not the memory budget. |
| `SHADOW_OUTPUT_MODE` | `off` | `off` only observes; `inject` prepares memory and attaches a temporary `TextPart` to the provider request. |
| `SHADOW_MEMORY_BUDGET` | `512` | Upper bound for the injected memory block, further clamped by the host budget. Requires `tiktoken` via `TokenCounter`. |
| `SHADOW_CONSOLE_READONLY` | `0` | `1` makes the embedded console reject backup/restore/delete/VACUUM; viewing and integrity checks remain. |

External providers (`astrbot`, `openai`) send the user's original text off the
process and may cost money. Choose them explicitly; nothing is enabled by
default. Output injection is also off by default because it changes what the
model sees.

## Preflight

```powershell
python dev\host_shadow\preflight.py --dev $env:DIGITAL_LIFE_DEV --root $env:ASTRBOT_ROOT
```

Exit 1 means the shadow plugin is not installed or env is incomplete; do not
start a soak.

## Console page

After the plugin is installed and activated, open AstrBot WebUI → Plugins →
**Memory Engine Shadow** → 视图 **数字记忆控制台**.

- The page is served by AstrBot itself; no extra port, token or browser origin.
- It reuses `dev/dlm_console` through `/astrbot_plugin_memory_shadow/console/*`
  and reads both the store plugin DB and the shadow DB.
- Writes (backup/restore/delete/VACUUM) are available to the WebUI admin, same
  as the store plugin panel; set `SHADOW_CONSOLE_READONLY=1` to disable them.
- The standalone `python -m dlm_console` app remains available for offline or
  cross-process inspection; both read the same files.

The page exposes the same six sections: overview, memory store, shadow engine,
observation log (including `output_status`), backup maintenance and environment.

## Files

- `shadow.sqlite3` — Memory Engine shadow DB (`source_ref`, `event_candidate`, `native_input_inbox`, …);
- `native_input_journal.sqlite3` — durable import journal/outbox;
- `shadow_events.jsonl` — one record per LLM request. In `inject` mode each record
  also carries `output_status` (`INJECTED` / `UNCHANGED` / `EMPTY` / `REJECTED` /
  `UNAVAILABLE`), `output_reason`, `budget_tokens`, and `output_reasons`.

## Upgrade note

v0.4 adds the embedded AstrBot console page (`views/console` +
`/console/*` APIs) and keeps the output injection path from v0.3. Both are
additive: no data migration, and output injection stays off by default.

v0.3 adds the optional `memory_output` injection path (`SHADOW_OUTPUT_MODE=inject`).
It defaults to off and does not migrate or rewrite existing shadow data.

v0.2 reads conversation history through the native input contract and uses the
`native-*` scope family. Data written by v0.1 (captured one message at a time
under `shadow-*` scopes) is not read by v0.2. Keep the old DB aside for
comparison or start a fresh one; the new pipeline does not migrate it.

## Guarantees

- default `SHADOW_OUTPUT_MODE=off`: no request or reply mutation;
- in `inject` mode only `extra_user_content_parts` is touched: `prompt`,
  `system_prompt`, `contexts`, and persisted history are unchanged;
- the injected part is marked temporary and is not saved into conversation history;
- output is bound to scope, query, request id, event revision and evidence hash;
  deleted/withdrawn/revised/changed-scope evidence is rejected at install time;
- command messages (`/`, `!`) are ignored;
- only private text is captured; group messages and unknown senders are skipped;
- no event is created without an explicitly configured judge;
- non-prefix history snapshots are rejected as a whole, never partially written.

## Not guaranteed

- 72h stability, real QQ/NapCat, production rollback;
- that AstrBot history contains every turn: the pair is written only after a
  successful reply, so a failed turn may never appear, and the first turn of a
  new conversation becomes visible on the next message;
- that the model's relation labels are correct — the judge only proposes, the
  engine's resolver/guard still decides what is written;
- that the injected memory improves replies; the evidence block is reference
  material, not an instruction, and prompt-injection immunity is not claimed;
- the 0.1 output contract was verified against AstrBot 4.29.3 source, not a live
  QQ/model deployment; token accounting depends on the configured tokenizer.
