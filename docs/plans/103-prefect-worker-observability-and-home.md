# Plan 103 — Prefect worker observability & home (persist flow-run logs + writable PREFECT_HOME)

**Status**: DRAFT
**Priority**: high — during the 2026-07-03 mini incident, `prefect flow-run logs
<id>` returned **empty** for every run, so per-run debugging was impossible; we
could only read container stdout. That single gap is what most slowed the
diagnosis. Surfaced again on the local stack 2026-07-06.
**Phase**: v0b — orchestration / observability
**Parent**: the operational reliability track (Plans 098/100); the mini + local
debugging effort
**Related**:
- `docker-compose.yml` (`prefect-worker` `:67-120`: `read_only: true`, `tmpfs:
  [/tmp]`, `PREFECT_LOGGING_LEVEL: WARNING`, **no `PREFECT_HOME`**), plus
  `prefect-worker-ingest` and `api` (same base image, same gap)
- `src/sapphire_flow/logging.py` (`structlog.configure` → `stdlib.LoggerFactory` +
  `ProcessorFormatter` attached to the **root** logger `:51` → stdout only; no
  Prefect `APILogHandler`)
- `src/sapphire_flow/flows/*.py` (`@flow(..., log_prints=False)`)
- `docs/standards/logging.md` (structlog config — must be updated)
**Created**: 2026-07-06

---

## Problem

Two defects in the worker's Prefect runtime, both observed live:

1. **Flow-run logs are not persisted to Prefect.** The app logs via structlog
   through the **root stdlib logger** to stdout (`logging.py:51`). Prefect only
   ships to its API-backed run-log store the records emitted by the `prefect`
   logger hierarchy or loggers named in `PREFECT_LOGGING_EXTRA_LOGGERS`; the app's
   `sapphire_flow.*` loggers are **not** among them, and `PREFECT_LOGGING_LEVEL=
   WARNING` would drop INFO even if they were. Result: `prefect flow-run logs <id>`
   and the Prefect UI show **nothing** for our flows — the NWP/model/QC events that
   explain a run exist only in the container's stdout (lost when the container is
   recreated). This is exactly why the mini's "completed-but-empty" forecast-cycle
   could not be diagnosed from Prefect.

2. **`PREFECT_HOME` is on a read-only path.** The worker runs `read_only: true`
   with no `PREFECT_HOME` set, so Prefect defaults to `/home/app/.prefect` and logs
   `UserWarning: Failed to create the Prefect home directory`. The worker still
   runs, but any Prefect CLI in the container (e.g. `prefect flow-run logs`) fails
   until you manually pass `-e PREFECT_HOME=/tmp/prefect`, and it is latent
   fragility (any Prefect feature that writes to home breaks silently).

## Goal

- **Per-run logs are retrievable via Prefect** (CLI `prefect flow-run logs` + UI)
  for all our flows, at a useful level — so a run can be debugged after the fact
  without the container's stdout.
- **No `PREFECT_HOME` warning; the in-container Prefect CLI works with no manual
  env override.**

## Design decisions (SETTLED via grill-me 2026-07-22)

> This is now the single owning plan for the Prefect `PREFECT_HOME`/log-persistence work
> — **Plans 062 and 141 are `SUPERSEDED by 103`** (141 was a redundant re-draft of D1;
> 062 the older subsumed draft). All four decisions below are settled; only D4 remains
> as verification tasks for `/implement`.

- **D1 — writable `PREFECT_HOME` (SETTLED).** Set `PREFECT_HOME=/tmp/prefect` on the
  **three Prefect-client services: `prefect-worker`, `prefect-worker-ingest`, and
  `init`** (init uses the Prefect client to register deployments). **NOT `api`** — it is
  an HTTP-only Prefect touchpoint (no Prefect client import; verified). `/tmp` is already
  a writable tmpfs on each (`docker-compose.yml`), so **no new mount, no volume, no
  chown**. PREFECT_HOME holds only CLI/profile scratch (durable server state is Postgres),
  so tmpfs (ephemeral) is correct.

- **D2 — ship app logs to the Prefect run-log store → OPTION (b) (SETTLED).** Attach
  Prefect's **`APILogHandler`** to the **root** logger, with **our existing
  `ProcessorFormatter`**, inside **`configure_prefect_logging`** (`logging.py:82` — the
  config the flow-running services use; NOT `configure_api_logging`, since `api` runs no
  flows). Rationale, grounded in the code:
  - **Option (a) `PREFECT_LOGGING_EXTRA_LOGGERS="sapphire_flow"` was rejected** — two real
    defects: (1) **11 call sites use `structlog.get_logger()` with no name** (vs 45 with
    `__name__`), so their stdlib logger is not under `sapphire_flow.*` and (a) would
    silently miss them; (2) our records go through structlog's `ProcessorFormatter`
    (`wrap_for_formatter`, `logging.py:34`) attached only to the root handler — Prefect's
    `APILogHandler` uses its **own** formatter, so under (a) it would ship the
    structlog-**wrapped record's `repr()`** (broken/ugly), not the rendered event.
  - **Option (b)** puts the `APILogHandler` on the root with our `ProcessorFormatter`, so
    it (i) catches **all** app loggers (named + the 11 unnamed) via root propagation and
    (ii) ships the **correctly-rendered** event. Option (c) `get_run_logger()` in flows
    stays rejected (invasive).
  - **Missing-run-context handling (implementation-critical):** `APILogHandler` has no run
    to attach to outside a flow/task (worker startup, between runs), where it warns/errors
    by default. The implementation MUST make out-of-run records a **silent no-op on the API
    handler** (still emitted to stdout via the StreamHandler) — set Prefect's
    "log-to-API-when-missing-flow" behaviour to **ignore** (confirm the exact setting name,
    e.g. `PREFECT_LOGGING_TO_API_WHEN_MISSING_FLOW=ignore`), or gate the handler so it only
    emits inside a run. Locked with a test.

- **D3 — run-log level = INFO; console UNCHANGED (SETTLED).** The `APILogHandler` ships at
  **INFO** (captures `nwp.*` / `model_*` / `ingest.qc_complete`; DEBUG would flood run logs
  with per-file `nwp.file_downloaded`). The console/stdout `StreamHandler` is **left at its
  current effective level** — no console retuning in this plan, preserving the raw
  `docker compose logs` fallback that both the 2026-07-03 incident and the 2026-07-22
  NWP-outage diagnosis relied on. *(Premise correction: `PREFECT_LOGGING_LEVEL=WARNING`
  does NOT gate our app logs on stdout — observed live 2026-07-22, stdout carried INFO and
  even DEBUG `nwp.*` events; it gates Prefect's own loggers. So the level concern is only
  about what the new APILogHandler ships, which D2(b) controls directly.)*

- **D4 — verification (tasks for `/implement`).** Ensure app logs are **not**
  double-emitted to stdout (once to stdout + once to Prefect is intended; twice to stdout
  is not); that Prefect's batched API-log shipping adds no meaningful latency to the 5-min
  ingest cadence; and that `PREFECT_LOGGING_LEVEL=WARNING` does not cap API-log ingestion
  of our INFO records (if it does, scope the fix — e.g. a per-handler level — here).

## Non-goals

- Central log aggregation (ELK/Loki) — out of scope; this is about making Prefect's
  own run-log store usable.
- Changing the structlog stdout format or the JSON/console renderer split.
- The Flow-4 pipeline-monitoring watchdog (separate).

## Verification (local stack is up)

1. Set the changes, rebuild, restart. Trigger a `forecast-cycle`.
2. `prefect flow-run logs <id>` (no manual `PREFECT_HOME`) returns the run's
   `nwp.cycle_resolved` / `nwp.fetch_started` / model-outcome / `ingest.qc_complete`
   events — i.e. the events that were invisible during the mini incident.
3. Worker startup shows **no** `Failed to create the Prefect home directory`
   warning.

## Process

**Grill-me DONE (2026-07-22)** — D2 settled to option (b), D3 settled to INFO-run-logs /
console-unchanged (see Design decisions above); D1 was already settled. Plans 062 + 141
folded in as `SUPERSEDED by 103`. **Next: `/plan` → `/implement` → deploy.** Code change
(`docker-compose.yml` — `PREFECT_HOME` env on the 3 client services; `src/sapphire_flow/logging.py`
— the D2(b) `APILogHandler` attach + missing-context handling; `docs/standards/logging.md`)
→ **hold-at-PR** with a version bump. Deploy (mac-mini, overlay stack) is the final step,
verifying `prefect flow-run logs <id>` is non-empty and no EROFS warning.
