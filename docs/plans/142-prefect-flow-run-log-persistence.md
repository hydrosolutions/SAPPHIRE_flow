---
status: DRAFT
created: 2026-07-23
plan: 142
title: Persist Prefect flow-run logs (app events reach `prefect flow-run logs` + the UI)
scope: Make our structlog app events (`nwp.*`, `model_*`, `ingest.qc_complete`) reachable via Prefect's run-log store, so a flow run is debuggable after the fact without raw container stdout. Split out of Plan 103 (which now owns only the trivial PREFECT_HOME fix). The fix is a logging-config change wired via Prefect's native `on_running` flow-state hook plus one deploy-env var — it does NOT change deployment registration (see the A0 reversal below). Infra/observability.
depends_on: [103]
blocks: []
supersedes: []
---

# Plan 142 — Persist Prefect flow-run logs

## Status

**DRAFT — no rush, careful.** Carved out of Plan 103 (owner decision 2026-07-23). This plan preserves the
design work from Plan 103's `/plan` rounds (2026-07-22/23, worktree `feat/plan-103`) but **reverses that
work's central premise** after an independent Codex pass (2026-07-23): the module-path-entrypoint switch
(old "Part A0") is neither necessary nor safe, and is dropped from the critical path (see §"Design decision:
drop A0"). Needs its own confirming `/plan` + `/implement` before READY, plus a full deploy verification.
Depends on Plan 103 (PREFECT_HOME must be writable first, or the in-container Prefect CLI can't even read the
logs).

**`/plan` run 2026-07-23 (4 rounds, stalled) — findings folded by hand.** The loop produced the big win (A0
reversal — 142 no longer touches deployment registration) but stalled on a **verified blocker** the planner
couldn't self-resolve: the `on_running` hook fires for *any* direct `<flow>(...)` engine call (not just
worker-dispatched runs), so the plan's "`.fn()`-isolation, no guard" argument was wrong — our own
`test_run_forecast_cycle.py` (~58 direct calls) would trigger a real Prefect API client + root
reconfiguration inside hermetic unit tests. **Fix folded: guard the hook on `flow_run.deployment_id is None:
return`** (None for direct/`.fn()`/ad-hoc/test calls; set only for deployment-dispatched production runs) —
which also subsumes the "clobbers a script's own `configure_api_logging()`" major. Also folded: T6 now
verifies **two** runs (forecast-cycle for `nwp.*`/model, ingest-observations for `ingest.qc_complete`) with a
**non-vacuous** native-dedup check (unique `get_run_logger().warning(<uuid>)` marker asserted exactly once,
since `PREFECT_LOGGING_LEVEL=WARNING` makes a plain "no dup lines" count vacuous); D4 perf claim bounded;
`cicd.md:231` added to T4; test-file path pinned to `tests/unit/test_logging.py`; References de-duplicated.

**Confirming `/plan` run 2026-07-23 (2 rounds, stalled) — BLOCKER CLEARED (the `deployment_id` fold held: 0
blockers), 5 majors folded by hand.** The loop converged on a cleaner, security-correct v2:
1. **Scoped logger, not root+filter (security — reviewer #1/#5).** Root+filter would ship third-party
   `httpx`/`urllib3` INFO records to Prefect's run-log store, and the recap client puts its **`api_key` in the
   request URL** → a credential leak forbidden by `logging.md:394-395`. Fix folded: attach `APILogHandler` to a
   **scoped `sapphire_flow` logger** + rename the 12 flow-module top-level loggers to explicit
   `sapphire_flow.flows.<module>` names (T2b). No filter, no third-party noise, no leak.
2. **Shared `@flow` wrapper, not 12 hand-wired decorators (#6)** — makes the hook structural for future flows.
3. **Test-mechanics corrections (#2/#3/#4)** — see the "Test-mechanics corrections" block below the tasks.
The A0 reversal and the `deployment_id` guard both stand. Still DRAFT; deploy gated on Plan 103 (done) + a
live rebuild.

## Problem — flow-run logs are empty

`prefect flow-run logs <id>` (and the Prefect UI) return **nothing** for our flows — the `nwp.*`/model/QC
events that explain a run exist only in the container's stdout (lost on container recreation). This is what
most slowed the 2026-07-03 mini incident AND the 2026-07-22 NWP-outage diagnosis this session (both fell
back to grepping raw `docker compose logs`).

**Root cause — `configure_prefect_logging()` has ZERO callers on the flow-run path** (`logging.py:82`;
grep-confirmed; also independently noted in Plan 084 finding D7). structlog is never configured in the
flow-run subprocess, so no stdlib handler — and therefore no Prefect `APILogHandler` — is ever attached.
App events reach stdout only via structlog's fallback printer; nothing ships them to Prefect's run-log API.
This single cause is **sufficient** to explain and fix the empty-log symptom.

> **What is NOT the cause (corrected 2026-07-23).** An earlier draft treated a second cause — the bare-name
> flow module under file-path `load_script_as_module` — as co-load-bearing. It is not: stdlib `logging`
> propagates every record up to the **root** logger regardless of the emitting logger's name (nothing in our
> chain sets `propagate=False`), so once a handler is attached at root, app events reach it whatever the
> entrypoint format. The bare-name effect touches only the **display name** of the flow module's own top-level
> logger; the service/adapter modules where most `nwp.*` / `model_*` / `ingest.qc_complete` events originate
> already get a correct `sapphire_flow.*` dotted name via ordinary `import`. This drives one design choice
> (attach at **root**, Part B); it is not a second root cause. (The full "why A0 was dropped" reasoning lives
> once in §"Design decision: drop A0" below, not restated here.)

## Design decision: drop A0 (module-path entrypoints)

The old "Part A0" rewrote all 12 deployment specs from file-path to module-path (dot-separated) entrypoints
so the engine subprocess would import the `sapphire_flow.flows` *package* and fire a `flows/__init__.py` hook.
**A0 is dropped — because it is unnecessary.** Attaching a handler on the flow-run path fixes the empty-log
symptom regardless of entrypoint format, since records propagate to root (see the Problem footnote). The
earlier "module-path switch would crash registration for all 12 specs" claim was *not* the reason to drop A0
and is in any case likely wrong (`register_deployments.py:135` already imports the identical dotted path via
the editable `sapphire_flow.pth` today); it is set aside, not relied on.

**Decision: keep the existing file-path entrypoints unchanged**, and wire logging via Prefect's native
`on_running` flow-state hook (Part A) instead of a package-import side-effect. This gives durable flow-run
logs without touching deployment registration and without the guarded-package-import mechanism the old draft
needed. (Module-path entrypoints, if ever wanted for unrelated reasons, are a separate change — and would
first need a real spike on the colon/dot `Flow.afrom_source` branch, not this doc's assumption.) The
round-by-round history of the A0 reversal is on branch `feat/plan-103`; it is not reproduced further here.

## Design

- **Part A — configure logging via Prefect's native `on_running` flow-state hook (no in-body call, no
  `__init__.py` hook, no entrypoint change).** Prefect's `@flow(...)` decorator already accepts
  `on_running: list[FlowStateHook]` (`.venv/.../prefect/flows.py:222`, stored at `:393`), and those hooks are
  invoked **exclusively from the engine's `call_hooks()`** (`.venv/.../prefect/flow_engine.py:527`, dispatch
  at `:768-786` sync / the async `for hook in hooks or []:` dispatch loop at `:1374`, whose branch selecting
  `flow.on_running_hooks` is at `:1369`) at the Running-state transition (`begin_run` sets `Running()` at
  `:506-507`) — i.e. **strictly before `call_flow_fn()` runs the flow body** (`:1003`). Wire a module-level
  hook, `configure_flow_run_logging_hook(flow, flow_run, state)` (the `FlowStateHook` signature), that
  **first guards on `flow_run.deployment_id is None: return`** and only then calls
  `configure_prefect_logging()`. **Wire it via a single shared decorator, not 12 hand-edited call sites
  (reviewer major #6).** Hand-adding `on_running=[…]` to each of the 12 `@flow(...)` decorators reproduces the
  exact defect class this plan exists to fix — required wiring that is trivial to omit on the next new flow
  (mirroring how `configure_prefect_logging()` silently had zero callers). Instead define one wrapper in a
  shared module — `sapphire_flow_flow = functools.partial(prefect.flow, on_running=[configure_flow_run_logging_hook])`
  (a thin `def` wrapper is fine if a preset needs merging with a flow that supplies its own `on_running`;
  none do today) — and have every flow module import and use `@sapphire_flow_flow(...)` in place of
  `@prefect.flow(...)`. The hook is then **structural for all current and future deployed flows**, and the
  regression test collapses from "enumerate all 12" to a single wrapper test (plus a cheap "every deployed
  flow object carries the hook" sweep). The 12 deployed flows are enumerated in `register_deployments.py:91-95`
  (`compute_skills_flow` + `compute_combined_skills_flow` share `flows/compute_skills.py`; count is 12, not 13).
  Strictly better than the earlier first-statement proposal:
  - **The guard fires the config ONLY for deployment-dispatched runs — `flow_run.deployment_id is None: return`
    (blocker fix).** A `<flow>.fn(...)` call bypasses the engine (raw undecorated function, `flows.py:313`), so
    the hook never fires there — BUT a *direct* `<flow>(...)` call (NOT `.fn()`) **does** go through the engine
    and **does** fire `on_running` (Prefect runs the decorated flow through the engine on every direct call).
    Our own suite is full of these — `tests/unit/flows/test_run_forecast_cycle.py` calls
    `run_forecast_cycle_flow(...)` directly ~58 times, `test_compute_skills.py` mixes direct and `.fn()` calls.
    An unguarded hook would therefore reconfigure root logging **and instantiate a real Prefect API client +
    background `APILogWorker` thread inside those hermetic Fake-store unit tests** on every direct call — the
    plan's earlier "`.fn()`-isolation is structural, no guard needed" claim was wrong (it conflated "bypasses
    the engine via `.fn()`" with "any direct call"). The fix is one line at the top of the hook:
    `if flow_run.deployment_id is None: return` (`FlowRun.deployment_id: Optional[UUID]`,
    `prefect/client/schemas/objects.py:582` — the `FlowRun` class starts at `:570`; note `:1132` is a
    *different* model, `DeploymentSchedule.deployment_id`). Worker-dispatched deployment runs (all 12 production flows, run
    on schedule) carry a `deployment_id`; direct/local/`.fn()`/ad-hoc/test calls carry `None`, so the config
    fires in production and **nowhere else**. This also subsumes the old "clobbers a script's own
    `configure_api_logging()`" risk (Part B2 / major): a script that calls a flow directly gets
    `deployment_id=None` → the hook returns before touching logging. Idempotence (below) still covers the
    subflow/retry re-fire case.
  - **Import-safe by construction** — the hook is passed as a decorator argument but only *called* by the
    engine at run time, so merely importing a flow module (`scripts/backfill_meteoswiss_history.py:58,174`,
    `scripts/onboard.py:54,318`, `scripts/validate_forcing_reference.py:62,212`) attaches nothing.
  - **Safer failure mode** — `call_hooks()` wraps each hook in `try/except Exception: self.logger.error(...)`
    (`flow_engine.py:768-786`) rather than propagating, so a bug in the logging configurator logs an error and
    the flow still runs — strictly safer than a first-statement call inside the body that would abort the run.
  - **No fresh-subprocess subflow to hedge for:** a grep of `flows/` finds no `task_runner` override and no
    `run_deployment(...)`; the only subflow calls (`flows/onboard_model.py`, `flows/train_models.py` invoke the
    hindcast subflow) are in-process Python calls that inherit the parent's already-configured root logger.
    Such in-process subflows carry **no `deployment_id`**, so their own `on_running` re-fire hits the guard and
    returns — harmless, because the parent deployment run already configured root (idempotent). A genuine
    fresh-subprocess subflow, if ever added, is a new case to handle then.
  - The hook must be **idempotent** across repeated invocation (subflows, retries — hooks re-fire on each
    Running transition), and it must **not** leak the `APILogHandler` into the shared helper that every
    `configure_*` function calls. Concretely: `configure_prefect_logging()` calls the shared
    `_apply_structlog_config()` **unchanged** (`src/sapphire_flow/logging.py:51-56` — it clears root and
    re-adds the root `StreamHandler` exactly as today), and **then**, as new code living **only inside
    `configure_prefect_logging()`** (not the shared helper), attaches the `APILogHandler` to the scoped
    `logging.getLogger("sapphire_flow")` logger (Part B). Because `_apply_structlog_config()`'s
    `root.handlers.clear()` does **not** touch the `sapphire_flow` logger, idempotence is **explicit**: before
    adding, remove any `APILogHandler` already present on that logger (or skip if present) — re-firing then
    leaves exactly one. **Do NOT put the `APILogHandler` attach inside `_apply_structlog_config()`** — that
    helper is shared by `configure_api_logging` / `configure_cli_logging` / `configure_test_logging` (called
    from `scripts/onboard.py:54`, `scripts/backfill_meteoswiss_history.py:58`, `src/sapphire_flow/ops/watchdog.py`,
    `src/sapphire_flow/cli/register_deployments.py`, etc.), none of which run in a flow-run context; leaking
    the handler there would instantiate a Prefect API client / background `APILogWorker` on every ad-hoc CLI
    invocation and reintroduce exactly the pollution the `deployment_id` guard exists to prevent.

  *(Note: the hook fires from `begin_run` before `setup_run_context()` is entered for the body, but the attach
  step needs no run context — `APILogHandler` resolves the flow-run id at emit time, during body execution,
  when the context is live. The hook only attaches the handler.)*

- **Part B — attach `APILogHandler` on a SCOPED `sapphire_flow` logger (not root), and give the 12 flow
  modules explicit dotted logger names.** Extend **only `configure_prefect_logging()`** (never
  `configure_api_logging` / `configure_cli_logging`, which have no flow-run to ship to) to add Prefect's
  `APILogHandler` — formatted with **our `ProcessorFormatter`**, `setLevel(logging.INFO)` — to
  `logging.getLogger("sapphire_flow")`, **not** the root logger. Records from `sapphire_flow.*` loggers are
  handled by that scoped `APILogHandler` **and** propagate up to root's existing `StreamHandler` (console
  unchanged) — so each app event ships once to the API and prints once to console, with `propagate` left
  `True` and no filter.
  - **Scoped, not root+filter (security fix — reviewer major #1/#5).** Attaching at root would sweep in every
    third-party record that propagates to root — `httpx`, `urllib3`, `asyncpg`, … — at INFO+. That is a
    **logging-security-contract violation**: `docs/standards/logging.md:394-395` prohibits logging secrets /
    tokens / raw external request data, and the recap client sends its **`api_key` in the request URL query
    string** (`adapters/recap_gateway.py`), which `httpx`'s INFO request logs would then persist into Prefect's
    run-log store. A scoped `sapphire_flow` handler never sees those records at all — no filter, no accepted
    "third-party noise" trade-off, no credential-leak surface. It also drops the `prefect.*`-reject filter that
    the root design needed (Prefect's `prefect.flow_runs`/`task_runs` loggers are not under `sapphire_flow`, so
    they never reach our handler; Prefect's own `api` handler still ships them once).
  - **Why the flow loggers must be renamed (the reason root was ever considered).** Most `nwp.*` / `model_*` /
    `ingest.qc_complete` events come from **service/adapter** modules already named `sapphire_flow.*` by
    ordinary import — those are caught by the scoped logger for free. The gap is the **flow modules' own
    top-level loggers**: each does `log = structlog.get_logger(__name__)`
    (`flows/run_forecast_cycle.py:92`, `flows/ingest_observations.py:38`, `flows/train_models.py:42`, …), and
    under file-path `load_script_as_module` `__name__` is a **bare** name (`run_forecast_cycle`) that would
    escape a `sapphire_flow`-scoped handler. **Fix: rename those 12 flow-module top-level loggers to an
    explicit dotted string** — `structlog.get_logger("sapphire_flow.flows.run_forecast_cycle")` etc. — a
    one-line-per-file change, the **same footprint** as the `on_running` wiring already touching all 12 flows,
    and fully orthogonal to the dropped A0 entrypoint rewrite. (This is the flow-body events cited in the
    Problem section: `run_forecast_cycle.py` `log.error("nwp.grid_stale", …)` / `log.warning("nwp.no_cycle_available", …)`;
    `ingest_observations.py`'s own `ingest.qc_complete`.) Task T2b covers the rename + a test that each flow
    logger name starts with `sapphire_flow.`.
  - **Why our own `APILogHandler` instance, not `PREFECT_LOGGING_EXTRA_LOGGERS`:** EXTRA_LOGGERS attaches
    *Prefect's* formatter, which ships the structlog-wrapped-record `repr()` rather than our
    ProcessorFormatter-rendered message. Our own instance carries our formatter.
  - **Idempotence (scoped logger).** The handler attaches to `logging.getLogger("sapphire_flow")`, which
    `_apply_structlog_config()`'s `root.handlers.clear()` does **not** touch — so the attach must guard against
    accumulation itself: before adding, remove any existing `APILogHandler` already on the `sapphire_flow`
    logger (or skip if present). Re-firing on subflows/retries then leaves exactly one. The attach stays
    **inside `configure_prefect_logging()`**, never in the shared `_apply_structlog_config()` (which the
    CLI/script `configure_*` paths call — leaking an API handler there would spin up an `APILogWorker` on ad-hoc
    invocations).

- **Part B2 — missing-run-context must be a silent no-op (name the setting).** With `APILogHandler`
  attached, any record shipped while no flow-run context is resolvable (subprocess bootstrap before the run
  context is established, an errant worker-thread emit) hits
  `APILogHandler.handleError` (`.venv/.../prefect/logging/handlers.py:176-195`), which branches on
  `PREFECT_LOGGING_TO_API_WHEN_MISSING_FLOW`. Its **default is `"warn"`**
  (`.venv/.../prefect/settings/models/logging.py:65-66`), i.e. `warnings.warn(...)` to stderr — NOT a silent
  drop. **Set `PREFECT_LOGGING_TO_API_WHEN_MISSING_FLOW=ignore`** declaratively in the container environment
  of the flow-run executors (`prefect-worker`, `prefect-worker-ingest` — the exact compose service names,
  `docker-compose.yml:80,144`) in `docker-compose.yml`, rather than mutating
  `os.environ` at runtime. This env only matters where `APILogHandler` is attached (the flow-run path), so
  the FastAPI/CLI paths are unaffected. Verified in D4.

- **D3 — level: the `APILogHandler` MUST carry an explicit `setLevel(logging.INFO)`.** Do not rely on the
  root logger's INFO level to cap what the API handler ships. `logging.py:56` sets root to INFO, **but**
  `logging.py:58-63` lets `SAPPHIRE_LOG_<module>=DEBUG` lower an individual `sapphire_flow.*` child logger to
  DEBUG; a DEBUG record accepted at that child then propagates to root and is offered to every root handler
  regardless of root's own level (a handler at default `NOTSET` accepts it). So the attach in T1 must do
  `api_handler.setLevel(logging.INFO)` explicitly, giving INFO-and-above shipping to the API sink independent
  of any per-module DEBUG override. Console/stdout `StreamHandler` is **unchanged** (stays at root level, so
  the raw-logs fallback both incidents relied on still carries the DEBUG override output). *(`PREFECT_LOGGING_LEVEL=WARNING`
  does NOT gate our app stdout logs — observed live carrying INFO/DEBUG `nwp.*`; it gates Prefect's own
  loggers.)*

- **D4 — verify:** exactly-once API shipping for a unique application marker **and** for a unique native
  Prefect WARNING marker (`get_run_logger().warning(<uuid>)`, per T6 — not a vacuous "no dup lines" count);
  no double-emission to stdout; INFO records are shipped (not capped by `PREFECT_LOGGING_LEVEL`); an out-of-run
  emission produces **no** `warnings.warn` on stderr. **Ingest-cadence sanity (bounded, not an SLO):** record
  one ingest run's wall-clock before/after and confirm it stays comfortably under the 5-minute schedule
  interval — flag only an obvious regression; a real sustained regression belongs in Flow-4 monitoring, not
  this plan. (`APILogHandler` ships via an async `BatchedQueueService`, so added cost is expected near-zero.)

## Non-goals

- Central log aggregation (ELK/Loki). The PREFECT_HOME fix (Plan 103). Console retuning. Switching deployment
  entrypoints to module-path form (explicitly dropped — see §"Design decision: drop A0").

## Implementation tasks

Each task lists scope, non-goals, and exact `uv run …` gates. Red-first: T1's API-sink tests are authored to
fail against `main` (no handler on the flow-run path) before the config change lands.

**Test placement (T3 folded in).** The former "T3" behavioral-test phase has been **merged into T1 and T2**
to remove a redundant phase node and its dependency edge — every T3 assertion either duplicated a T1/T2
property or belongs at the same layer. API-sink behavior (once-only shipping, filter dedup, bare-name
catch-all, per-module DEBUG, missing-context, idempotence, stdout-preserved, third-party trade-off) is a
property of `configure_prefect_logging()` and lives in **T1**; flow-wiring behavior (`on_running` present on
each flow, the `deployment_id` guard, import-safety, the direct-call-suite regression) lives in **T2**.

- **T1 — logging config + its API-sink behavioral tests (Part B + B2 mechanism, plus the hook helper).** In
  `src/sapphire_flow/logging.py`, extend `configure_prefect_logging()` (and only it) so that — **after** its
  existing unchanged call to the shared `_apply_structlog_config()` (which clears root + re-adds the root
  `StreamHandler`) — it attaches an `APILogHandler` to the **scoped `logging.getLogger("sapphire_flow")`
  logger, NOT root** (Part B — security fix). This attach lives **inside `configure_prefect_logging()` itself,
  NOT inside the shared `_apply_structlog_config()` helper** (see the Part A idempotence bullet — leaking it
  into the shared helper would pollute the CLI/script `configure_*` paths). The handler is formatted with our
  `ProcessorFormatter`, needs **no filter** (a scoped logger never sees `prefect.*` or third-party records),
  and gets an explicit **`api_handler.setLevel(logging.INFO)`** (D3 — must not depend on logger level, since
  `logging.py:58-63` can lower a child to DEBUG). Idempotence is **explicit** (the `sapphire_flow` logger is
  outside `_apply_structlog_config()`'s `root.handlers.clear()`): remove any existing `APILogHandler` on the
  `sapphire_flow` logger before adding, so re-firing leaves exactly one. Also add the
  module-level `FlowStateHook` — `configure_flow_run_logging_hook(flow, flow_run, state)` (Part A), which
  **guards `if flow_run.deployment_id is None: return`** and then calls `configure_prefect_logging()` — here or
  in a sibling module, so T2 has one importable hook to pass. The `deployment_id` guard (not a context-probe /
  env guard) is what keeps the hook inert for direct/`.fn()`/ad-hoc/test calls — see Part A. Do not touch
  `configure_api_logging` / `configure_cli_logging` / `configure_test_logging`.
  - **Non-goals:** no decorator wiring yet (T2); no docker-compose change (T5).
  - **Test capture mechanism (major fix — the sink is behind `prepare()`).** `APILogHandler.emit()` calls
    `prepare()` **before** `emit_api_log()`, and `prepare()` (`.venv/.../prefect/logging/handlers.py:200-233`)
    resolves a `flow_run_id` from `record.flow_run_id` **or** the current run context, and **drops the record
    if neither exists**. So a record emitted with no `flow_run_id` and no run context never reaches the sink —
    merely spying on `emit_api_log` outside a run context would capture **nothing** (or trip the
    missing-context path). Therefore, for every "reaches the sink" assertion (1-6, 8), the test **either**
    emits records that carry a known `flow_run_id` attribute (`logging.LogRecord` with `flow_run_id=<uuid>`, or
    a `structlog`/stdlib log call binding `flow_run_id`) **or** runs inside a controlled Prefect run context.
    Capture the shipped logs with **`prefect.logging.handlers.set_api_log_sink(<collector>)`**
    (`handlers.py:55-65` — the sanctioned interception point; `emit_api_log` calls the installed sink instead
    of the real `APILogWorker`), registered/torn down via a fixture. Only assertion (7),
    the missing-context/`ignore` test, uses a genuinely context-free record with no `flow_run_id`. Assert,
    all against `configure_prefect_logging()` directly (no live worker-dispatched flow needed):
    1. an application event (`sapphire_flow.*`, carrying a known `flow_run_id`) is shipped **exactly once**;
    2. a **native Prefect run event** (name `prefect.flow_runs`, carrying a `flow_run_id`) is **NOT** shipped
       by our scoped handler — `prefect.*` is not under `sapphire_flow`, so it never reaches our handler (no
       filter needed; Prefect's own `api` handler ships it once in production);
    3. a **renamed flow-module record** (name `sapphire_flow.flows.run_forecast_cycle`, carrying a
       `flow_run_id`) **is** shipped by the scoped handler (proves the T2b rename lands flow-body events under
       `sapphire_flow.*`);
    4. **an unrelated third-party record (name `httpx`, carrying a `flow_run_id`) is NOT shipped** — the
       security lock (reviewer #1): a scoped `sapphire_flow` handler must never persist third-party
       request-logs (which can carry the recap `api_key` in a URL) to Prefect. **Negative test**, red-first
       against a root-attached variant;
    5. INFO records (carrying a `flow_run_id`) are shipped (not gated to WARNING);
    6. **D3 per-module DEBUG override** — a `sapphire_flow.*` child logger lowered to DEBUG (mimicking
       `SAPPHIRE_LOG_*`) emits a DEBUG and an INFO record (both carrying a `flow_run_id`); **only the INFO
       reaches the API sink** while the console stream sees both (proves `api_handler.setLevel(INFO)`
       is explicit, not inherited);
    7. an **out-of-run** emission — a genuinely context-free record with **no `flow_run_id`** and no run
       context — with `PREFECT_LOGGING_TO_API_WHEN_MISSING_FLOW=ignore` produces **no** `warnings.warn` on
       stderr (and is dropped by `prepare()`, never reaching the sink);
    8. **console emission unchanged** — capture the root `StreamHandler`'s **configured stream (stderr — it is
       `logging.StreamHandler()` with no stream arg, `logging.py:53`), not literal stdout**; each app event
       still prints exactly once (raw container-log fallback preserved; Docker aggregates stdout+stderr);
    9. **idempotence** — configuring twice leaves exactly one root `StreamHandler` and exactly one
       `APILogHandler` on the `sapphire_flow` logger.
  - **Gate:** `uv run pytest tests/unit/test_logging.py -q` · `uv run ruff check src/sapphire_flow/logging.py`
    · `uv run ruff format --check src/sapphire_flow/logging.py`.

- **T2 — wire the hook via a shared `@flow` wrapper (Part A, reviewer #6) + flow-wiring tests.** Define the
  wrapper `sapphire_flow_flow = functools.partial(prefect.flow, on_running=[configure_flow_run_logging_hook])`
  in a shared module, and change each of the **12 deployed flows** (enumerated in `register_deployments.py`;
  `compute_skills.py` hosts two — `compute_skills_flow`, `compute_combined_skills_flow`; count 12, not 13) to
  decorate with `@sapphire_flow_flow(...)` instead of `@prefect.flow(...)`. No in-body call, no per-site
  `on_running` arg.
  - **Non-goals:** no change to `register_deployments.py` entrypoints (A0 dropped); no change to
    `flows/__init__.py` (stays empty); no `configure_prefect_logging()` body change (T1).
  - **Tests (add a dedicated `tests/unit/flows/test_flow_logging_entry.py`):**
    - (a) **wrapper test (collapses the 12-enumeration).** Assert `sapphire_flow_flow` produces a `Flow` whose
      `on_running_hooks` contains `configure_flow_run_logging_hook`; plus a cheap sweep asserting each of the 12
      deployed flow objects carries the hook (catches a flow that bypassed the wrapper).
    - (b) **`deployment_id` guard — the blocker's lock (corrected assertion, reviewer #2).** Call the hook
      helper directly with a fake `flow_run` whose `deployment_id is None` and assert `configure_prefect_logging`
      is **not called** (spy on it) and **no `APILogHandler` is left on the `sapphire_flow` logger**; call again
      with a non-None `deployment_id` and assert `configure_prefect_logging` **is** called and the handler is
      attached. Do **not** assert "no Prefect client instantiated" — `APILogHandler.__init__` creates no client
      (`handlers.py:114-121`); a client only appears on emit, and direct engine calls use orchestration clients
      anyway, so a global client-construction spy cannot isolate this hook. If worker creation must be pinned,
      spy specifically on `APILogWorker.instance()` / `emit_api_log`. Author red-first against the unguarded hook.
    - (b2) **existing direct-call suites stay green under the wrapper.** Re-run
      `tests/unit/flows/test_run_forecast_cycle.py` (~58 direct `run_forecast_cycle_flow(...)` calls) and
      `tests/unit/flows/test_compute_skills.py` (mixed direct + `.fn()`) with the wrapper in place, and assert
      **no `APILogHandler` ends up on the `sapphire_flow` logger** during them (those ad-hoc calls carry
      `deployment_id=None`). This is the regression proving the guard prevents test-suite pollution.
    - (c) importing a flow module attaches nothing (the hook is only *passed*, never *called*, at import).
    - *(Cut: an earlier draft carried a sub-test (d), an "`afrom_source` regression pin", exercising the real
      `Flow.afrom_source` over a `tempfile.TemporaryDirectory()`. It is **dropped** — it locked down the
      colon-vs-dot entrypoint behavior of `register_deployments.py`, which this plan explicitly **does not
      touch** (A0 dropped; entrypoints unchanged) and whose correctness Part A/B does not depend on. It was
      leftover due-diligence from the discarded A0 investigation, and materially heavier than anything else
      here. If a permanent colon-entrypoint regression pin is ever wanted, it belongs in the next plan that
      touches `register_deployments.py` entrypoints, not this one.)*
  - **Gate:** run the **whole flows suite** — `uv run pytest tests/unit/flows/ -q` — plus the new logging-entry
    test and the deployment-registration test:
    `uv run pytest tests/unit/flows/test_flow_logging_entry.py tests/unit/cli/test_register_deployments.py -q`.
    Running all of `tests/unit/flows/` is deliberate (major fix): every one of the 12 deployed decorators
    changes, and many suites — `test_run_forecast_cycle.py`, `test_compute_skills.py`,
    `test_ingest_observations.py`, `test_train_models.py`, `test_onboard_flow.py`, … — call the decorated flows
    **directly**, so this is the suite that proves the `deployment_id` guard prevents scoped-logger/API-client
    pollution across the whole flow layer (the b2 regression). Then `uv run ruff check src/sapphire_flow/flows` ·
    `uv run ruff format --check src/sapphire_flow/flows`.

- **T2b — rename the 12 flow-module top-level loggers to explicit dotted names (Part B).** In each deployed
  flow module, change the module-scope `log = structlog.get_logger(__name__)` to an explicit
  `structlog.get_logger("sapphire_flow.flows.<module>")` (e.g. `"sapphire_flow.flows.run_forecast_cycle"`),
  so flow-body events land under `sapphire_flow.*` and are caught by the scoped `APILogHandler` even under
  file-path `load_script_as_module` (which otherwise makes `__name__` a bare name). One line per file; same
  set of 12 files as T2. Service/adapter modules already get `sapphire_flow.*` names via ordinary import and
  need no change.
  - **Test:** assert every deployed flow module's top-level `structlog` logger name starts with
    `sapphire_flow.` (a small enumerated sweep, in `test_flow_logging_entry.py`).
  - **Gate:** covered by the T2 flows-suite gate.

- **T4 — documentation.** Update:
  - `docs/standards/logging.md:40-47` and `:118-150` — correct the `configure_prefect_logging()` description:
    it is now invoked **via a Prefect `on_running` flow-state hook at each flow-run start** (not "once at
    worker startup"); document the **scoped `sapphire_flow`-logger** `APILogHandler` + `ProcessorFormatter`
    topology (no root attach, no filter — the deliberate choice that keeps third-party/credential-bearing
    records out of the run-log store), the flow-logger dotted-name requirement (T2b), the INFO ship level, the
    missing-context → `ignore` behavior, and the idempotence contract.
  - `docs/standards/cicd.md:231` — currently mentions only `PREFECT_LOGGING_LEVEL` for the workers; add
    `PREFECT_LOGGING_TO_API_WHEN_MISSING_FLOW=ignore` (set on both workers in T5), with the rationale delegated
    to `logging.md` (one line here, the full topology there).
  - `docs/plans/README.md` — fix **both** adjacent bullets, which are now stale after the A0 reversal:
    - the **142** bullet (`:41-43`) — replace the "module-path deployment entrypoints (dot, ⚠️ no colon) +
      guarded `flows/__init__.py` hook + `APILogHandler` on a `sapphire_flow`-scoped logger" summary with the
      ratified design (file-path entrypoints unchanged; configure via `on_running` flow-state hook; root
      `APILogHandler` + `prefect.*`-reject filter). Resolves the root-vs-scoped contradiction the reviewers
      flagged.
    - the **103** bullet (`:37-39`) — its trailing sentence "The flow-run-log-persistence half was split to
      Plan 142 (2026-07-23) — it needed a **load-bearing deployment-entrypoint change**" is false post-reversal
      (Plan 142 explicitly does not touch deployment entrypoints). Reword to "…split to Plan 142 — a
      flow-run-start logging-config change plus one worker env var (no entrypoint change)."
  - **Non-goals:** no code.
  - **Gate:** `uv run pre-commit run --files docs/standards/logging.md docs/standards/cicd.md docs/plans/README.md`
    (markdown hygiene hooks — includes `cicd.md`, which this task edits); manual read-through.

- **T5 — deploy env (Part B2) + a focused compose assertion.** Add
  `PREFECT_LOGGING_TO_API_WHEN_MISSING_FLOW=ignore` to the `prefect-worker` (`docker-compose.yml:80`) and
  `prefect-worker-ingest` (`:144`) service environments in `docker-compose.yml`.
  - **Non-goals:** no change to `init` (registration doesn't attach the API handler); no other env churn.
  - **Tests (major fix — the YAML change must be asserted, not just parsed).** A `docker compose config`
    dry-parse only proves the YAML is valid, not that the var is on the *right* services with the *right*
    value. Add a focused test — extend the existing `tests/unit/deploy/test_compose_prefect_home.py` (which
    already loads the compose file and exposes a `_service_env(compose, service)` helper at
    `tests/unit/deploy/test_compose_prefect_home.py:33-81`) with a new test class asserting:
    - `PREFECT_LOGGING_TO_API_WHEN_MISSING_FLOW == "ignore"` on **exactly** `prefect-worker` **and**
      `prefect-worker-ingest`;
    - the var is **absent** on unrelated services (e.g. `init`, `api`, `postgres`) — so a mis-targeted edit
      fails the test.
  - **Gate:** `uv run pytest tests/unit/deploy/test_compose_prefect_home.py -q` (names the file explicitly,
    exercising the new assertion) plus `docker compose config` dry-parse (read-only).

- **T6 — deploy verification (after Plan 103).** On the mini, no manual `PREFECT_HOME` override anywhere.
  The promised event classes live in **different deployments**, so a single run cannot prove them all
  (`ingest.qc_complete` is emitted by observation-ingest, `flows/ingest_observations.py:557`; `nwp.*` / model
  events by the forecast cycle, `flows/run_forecast_cycle.py`). Verify **two** runs independently:
  1. **forecast-cycle run** — `prefect flow-run logs <fc-id>` contains that run's `nwp.*` and model events
     (and none of them appear more than once).
  2. **ingest-observations run** — `prefect flow-run logs <ingest-id>` contains `ingest.qc_complete`.
  - **Non-vacuous native-dedup check (major fix) — with a concrete injection mechanism.** A plain "no
    duplicate native lines" check is vacuous under `PREFECT_LOGGING_LEVEL=WARNING` (native `prefect.flow_runs`
    INFO lifecycle lines may not ship at all, so zero copies falsely passes). The dedup property must be proven
    with a **unique native Prefect WARNING** emitted via `get_run_logger().warning("<uuid-marker>")` asserted
    **exactly once**. **No deployed flow calls `get_run_logger()` today** (grep-confirmed — zero
    `get_run_logger` occurrences in `src/`), so this marker has no home in the production flows and must NOT be
    left injected into them. Use a **throwaway diagnostic deployment**, created and torn down as part of T6, so
    nothing verification-only leaks into shipped code:
    1. On the mini, write a tiny diagnostic flow file (e.g. `/tmp/diag_log_verify.py`) inside the running
       `prefect-worker` container defining a `@flow(on_running=[configure_flow_run_logging_hook])`-decorated
       flow whose body emits **both** markers: `get_run_logger().warning("<native-uuid>")` (native Prefect
       WARNING) and a structlog app-logger call carrying `"<app-uuid>"`. It wires the **same** hook the 12
       production flows use, so it exercises the real root-`APILogHandler` + `prefect.*`-reject path — and,
       being deployment-dispatched, it carries a real `deployment_id` so the guard passes.
    2. Register it as a one-off deployment against the same work pool the workers already poll (the mechanism
       `src/sapphire_flow/cli/register_deployments.py:131-141` uses — `Flow.from_source(source=<dir>,
       entrypoint="diag_log_verify.py:<flow>")`), then trigger a single run
       (`prefect deployment run <name>`) and capture its `<diag-id>`.
    3. Assert `prefect flow-run logs <diag-id>` contains `<native-uuid>` **exactly once** (proves the
       `prefect.*`-reject filter drops our root-handler copy while Prefect's own `api` handler ships it once)
       and `<app-uuid>` **exactly once** (proves our handler ships app events and root propagation doesn't
       double them).
    4. **Clean up:** `prefect deployment delete <name>` and remove the temp flow file. Record the two UUIDs
       and the assertion output in the plan's implement notes.
  - No stderr `PREFECT_LOGGING_TO_API_WHEN_MISSING_FLOW` warnings in `docker compose logs prefect-worker`.
  - **Gate:** manual, captured in the plan's implement notes (the two UUIDs, the `flow-run logs` counts, and the
    cleanup confirmation).

### Phase graph

```json
{
  "phases": [
    { "id": "T1", "title": "logging config: scoped sapphire_flow APILogHandler (no root, no filter) + hook helper + API-sink tests", "depends_on": [] },
    { "id": "T2", "title": "wire hook via shared @flow wrapper onto 12 flows + flow-wiring tests + full flows-suite regression gate", "depends_on": ["T1"] },
    { "id": "T2b", "title": "rename 12 flow-module loggers to explicit sapphire_flow.flows.<module>", "depends_on": [] },
    { "id": "T4", "title": "docs: standards/logging.md + cicd.md + plans/README.md", "depends_on": ["T1", "T2", "T2b"] },
    { "id": "T5", "title": "docker-compose: PREFECT_LOGGING_TO_API_WHEN_MISSING_FLOW=ignore on workers", "depends_on": [] },
    { "id": "T6", "title": "deploy verification on mini (after Plan 103)", "depends_on": ["T2", "T2b", "T4", "T5", "103"] }
  ]
}
```

## References

- `src/sapphire_flow/logging.py` — `configure_prefect_logging` `:82` (the one to extend — attach the
  `APILogHandler` here, *after* the shared-helper call, NOT inside the helper); `_apply_structlog_config`
  `:22-56`, the **shared** helper called by all four `configure_*` functions, whose `root.handlers.clear()`
  at `:51-52` gives the attach its idempotence for free; `ProcessorFormatter` `:34,43`.
- `src/sapphire_flow/flows/*.py` — the 12 `@flow(...)` decorators to add `on_running=[…]` to (all use the
  parenthesised form, e.g. `run_forecast_cycle.py:1222`, `ingest_observations.py:396`, `train_models.py:212`,
  `compute_skills.py:261,325`); flow-module loggers `structlog.get_logger(__name__)`
  (`run_forecast_cycle.py:92`, `ingest_observations.py:38`, `train_models.py:42`, …) that become bare names
  under file-path loading.
- `src/sapphire_flow/cli/register_deployments.py:138` — file-path entrypoint (`f"src/{…}.py:{attr}"`),
  **kept unchanged**; `:135` — `importlib.import_module(spec.flow_module)` already imports the dotted path
  successfully today (the fact that defuses the old "A0 crashes registration" claim); `:91-95` — the
  `compute_combined_skills_flow` spec, one of the 12 (not a 13th flow).
- **Prefect internals** — every Prefect-3.6.23 (`uv.lock`) `file:line` citation this plan depends on already
  lives once, with its rationale, **inline in the Design section** (`on_running` accept/store + `call_hooks`
  dispatch in Part A; `.fn()` bypass in Part A; `FlowRun.deployment_id` guard in Part A; the `logging.yml`
  propagation / `prefect.*`-reject rationale in Part B; the `handleError` / settings default in Part B2; the
  `afrom_source` colon-branch in §"drop A0"). The former duplicate index here is **removed** — it restated
  those facts a second time and carried a stale `objects.py:1132` anchor, and no task instructs adding
  call-site comments, so it served no purpose. These line numbers rot on the next `uv sync`; re-verify at
  implement time, don't trust the anchors blindly.
- `docs/standards/logging.md` (T4 target); `docs/plans/README.md:41-43` (T4 target, resolves the design
  contradiction).
- Plan 103 (the PREFECT_HOME half, its dependency); Plan 084 finding D7 (independent note of the unconfigured
  structlog). The full round-by-round `/plan` design lives in git history on branch `feat/plan-103`.
