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

## Design decisions (proposed; confirm in grill-me)

- **D1 — writable `PREFECT_HOME` (fixes #2, simple).** Set
  `PREFECT_HOME=/tmp/prefect` in the environment of every Prefect-running service
  (`prefect-worker`, `prefect-worker-ingest`, `api` if it uses the Prefect client).
  `/tmp` is already a writable tmpfs, so no new mount. PREFECT_HOME holds only
  CLI/profile scratch (the durable server state is Postgres), so tmpfs (ephemeral)
  is correct. Confirm the exact set of services.
- **D2 — ship app logs to the Prefect run-log store (fixes #1). GRILL-ME on the
  mechanism:**
  - (a) **`PREFECT_LOGGING_EXTRA_LOGGERS="sapphire_flow"`** — ask Prefect to attach
    its `APILogHandler` to the `sapphire_flow` logger hierarchy. Least code, but
    depends on our structlog stdlib logger names being under `sapphire_flow.*`
    (verify — `add_logger_name` is in the processor chain) and on handler
    propagation to children.
  - (b) **Attach Prefect's `APILogHandler` in `logging.py`** when a run context is
    active, so all app records flow to the API store alongside stdout. More
    explicit/robust; a few lines in our own logging setup.
  - (c) **`get_run_logger()` in flows** — most faithful to Prefect but invasive
    (rewrites logging call sites); rejected unless (a)/(b) prove insufficient.
  - Recommend (a) first, fall back to (b). Decide.
- **D3 — logging level for shipped run logs.** `PREFECT_LOGGING_LEVEL=WARNING`
  currently suppresses the INFO events (`nwp.*`, `model_*`, `ingest.qc_complete`)
  that are exactly what we need in a run log. Decide the level for **API/run-log
  shipping** (want INFO) vs **console** (can stay quieter) — Prefect separates these
  (`PREFECT_LOGGING_LEVEL` vs handler-level config). Confirm we can get INFO into
  the run-log store without flooding container stdout.
- **D4 — verify no duplication / no perf hit.** Ensure app logs don't get
  double-emitted (once to stdout, once to Prefect is intended; twice to stdout is
  not), and that API-log shipping (batched by Prefect) doesn't add meaningful
  latency to the 5-min ingest cadence.

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

DRAFT until a grill-me settles D2 (extra-loggers vs explicit handler) and D3 (level
split). Then plan-review → implement. Code change (`docker-compose.yml` ×3 services,
`src/sapphire_flow/logging.py`, `docs/standards/logging.md`) → **hold-at-PR** with a
version bump.
