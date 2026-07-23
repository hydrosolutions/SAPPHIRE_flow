---
status: DRAFT
created: 2026-07-23
plan: 142
title: Persist Prefect flow-run logs (app events reach `prefect flow-run logs` + the UI)
scope: Make our structlog app events (`nwp.*`, `model_*`, `ingest.qc_complete`) reachable via Prefect's run-log store, so a flow run is debuggable after the fact without raw container stdout. Split out of Plan 103 (which now owns only the trivial PREFECT_HOME fix). Load-bearing — it changes deployment registration; treat carefully. Infra/observability.
depends_on: [103]
blocks: []
supersedes: []
---

# Plan 142 — Persist Prefect flow-run logs

## Status

**DRAFT — no rush, careful.** Carved out of Plan 103 (owner decision 2026-07-23) because it turned out to
be a **load-bearing change to deployment registration**, not the "env var" the PREFECT_HOME fix (now Plan
103's whole scope) is. This plan preserves the design work from Plan 103's `/plan` rounds (2026-07-22/23,
worktree `feat/plan-103`); it needs its own `/plan` + `/implement` in isolation, and a full deploy
verification, before READY. Depends on Plan 103 (PREFECT_HOME must be writable first, or the in-container
Prefect CLI can't even read the logs).

## Problem — flow-run logs are empty

`prefect flow-run logs <id>` (and the Prefect UI) return **nothing** for our flows — the `nwp.*`/model/QC
events that explain a run exist only in the container's stdout (lost on container recreation). This is what
most slowed the 2026-07-03 mini incident AND the 2026-07-22 NWP-outage diagnosis this session (both fell
back to grepping raw `docker compose logs`). Two compounding causes, both found during Plan 103's `/plan`:

1. **`configure_prefect_logging()` has ZERO callers on the flow-run path** (`logging.py:82`; grep-confirmed;
   also independently noted in Plan 084 finding D7). So structlog never even gets configured in the flow-run
   subprocess — app events reach stdout via structlog's fallback printer but never a stdlib handler, so never
   Prefect's `APILogHandler`.
2. **Deployments register *file-path* entrypoints**, which Prefect's process worker resolves via
   `load_script_as_module` — it never imports the `sapphire_flow.flows` *package* (so no `__init__.py` hook
   can run) and names loggers by bare filename, not `sapphire_flow.*`.

## Design (settled in shape via Plan 103's grill-me + `/plan`; verify in this plan's own `/plan`)

- **Part A0 — deployments must use MODULE-PATH entrypoints (DOT-separated, ⚠️ NO colon).**
  `register_deployments.py:139` calls `flow_fn.afrom_source(source="/app", entrypoint=…)`. Today it builds a
  file-path entrypoint. Change it to `entrypoint = f"{spec.flow_module}.{spec.flow_attr}"` — **all dots**,
  e.g. `sapphire_flow.flows.run_forecast_cycle.run_forecast_cycle_flow`.
  **⚠️ BLOCKER the `/plan` verified (do NOT use a colon):** Prefect 3.6.23's `Flow.afrom_source`
  (`.venv/.../prefect/flows.py:1273-1279`) branches on the mere presence of **any `:`** in the entrypoint —
  a colon form (`…run_forecast_cycle:run_forecast_cycle_flow`) is treated as a file path, prefixed with
  `/app/`, and `import_object` then tries `importlib.import_module('/app/sapphire_flow…')` →
  `ModuleNotFoundError`, **crashing registration for all 12 specs at `init` startup** (reproduced by the
  reviewer). The dot form has no colon → `afrom_source` takes the module-path branch → `import_module` runs
  the package (so `flows/__init__.py` fires) and names the flow `sapphire_flow.flows.run_forecast_cycle`.
  **Test through `afrom_source`, not `import_object` directly** — a direct `import_object` test bypasses the
  path-mangling and would pass even against the broken colon form. Also keep a live registered-deployment
  run gate.
- **Part A — a guarded `flows/__init__.py` hook.** With module-path entrypoints, the engine subprocess
  imports the `sapphire_flow.flows` package → runs `flows/__init__.py` (currently empty). Put an
  **idempotent** call to `configure_prefect_logging()` there, **guarded on being inside a flow-run
  subprocess** (e.g. `os.environ.get("PREFECT__FLOW_RUN_ID")` set). The guard is required: operational
  scripts (`scripts/backfill_meteoswiss_history.py:58,174`, `scripts/onboard.py:54,309`,
  `scripts/validate_forcing_reference.py:62`) call `configure_api_logging()` then import flow modules — an
  unconditional hook would clobber their logging + attach an API handler with no run to ship to.
- **Part B — attach `APILogHandler` in `configure_prefect_logging`, option (b) from the grill-me.** Add
  Prefect's `APILogHandler` to the **root** logger with **our `ProcessorFormatter`**, so records render
  correctly AND ship to Prefect. Chosen over `PREFECT_LOGGING_EXTRA_LOGGERS` because (a) misses the 11
  `structlog.get_logger()`-without-name sites and ships the structlog-wrapped-record `repr()` (Prefect's
  handler lacks our ProcessorFormatter). Handle the **missing-run-context** case (out-of-run records must be
  a silent no-op on the API handler, still hitting stdout — set the "log-to-API-when-missing-flow" behaviour
  to *ignore*, or gate the attach).
- **D3 — level:** `APILogHandler` ships at **INFO**; console/stdout **unchanged** (preserve the raw-logs
  fallback both incidents relied on). *(`PREFECT_LOGGING_LEVEL=WARNING` does NOT gate our app stdout logs —
  observed live carrying INFO/DEBUG `nwp.*`; it gates Prefect's own loggers.)*
- **D4 — verify:** no double-emission to stdout; no perf hit on the 5-min ingest cadence; `PREFECT_LOGGING_LEVEL`
  doesn't cap API ingestion of our INFO records.

## Non-goals
- Central log aggregation (ELK/Loki). The PREFECT_HOME fix (Plan 103). Console retuning.

## Verification (deploy)
`prefect flow-run logs <id>` (no manual PREFECT_HOME) returns a run's `nwp.*` / model / `ingest.qc_complete`
events; the deployment-registration change registers all 12 specs cleanly (module-path, no colon).

## References
- `src/sapphire_flow/logging.py` (`configure_prefect_logging` `:82`; `ProcessorFormatter` on root `:34,51`).
- `src/sapphire_flow/cli/register_deployments.py:139` (`afrom_source` call); `.venv/.../prefect/flows.py:1273-1279`
  (colon-branching), `1183` (module-path docstring). Prefect pinned 3.6.23 (`uv.lock`).
- `src/sapphire_flow/flows/__init__.py` (empty — the Part A hook site).
- Plan 103 (the PREFECT_HOME half, its dependency); Plan 084 finding D7 (independent note of the unconfigured
  structlog). The full round-by-round `/plan` design lives in git history on branch `feat/plan-103`.
