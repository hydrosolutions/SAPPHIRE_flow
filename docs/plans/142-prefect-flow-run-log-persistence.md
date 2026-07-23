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
  at `:768-786` sync / `:1131,1369` async) at the Running-state transition (`begin_run` sets `Running()` at
  `:506-507`) — i.e. **strictly before `call_flow_fn()` runs the flow body** (`:1003`). Wire a module-level
  hook, `configure_flow_run_logging_hook(flow, flow_run, state)` (the `FlowStateHook` signature), that
  **first guards on `flow_run.deployment_id is None: return`** and only then calls
  `configure_prefect_logging()`, and pass `on_running=[configure_flow_run_logging_hook]` on each of the
  **12 deployed `@flow(...)` decorators enumerated in `register_deployments.py`** (two of them —
  `compute_skills_flow` and `compute_combined_skills_flow` — live in `flows/compute_skills.py`; the count is
  12, not 13, `register_deployments.py:91-95`). Same one-line-per-flow footprint as the earlier first-statement
  proposal, but strictly better:
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
    `prefect/client/schemas/objects.py:1132`). Worker-dispatched deployment runs (all 12 production flows, run
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
    Running transition): `_apply_structlog_config` already does `root.handlers.clear()` before re-adding, so
    handlers must not accumulate — the `APILogHandler` attach in Part B must sit *inside* that same
    clear/re-add path.

  *(Note: the hook fires from `begin_run` before `setup_run_context()` is entered for the body, but the attach
  step needs no run context — `APILogHandler` resolves the flow-run id at emit time, during body execution,
  when the context is live. The hook only attaches the handler.)*

- **Part B — attach `APILogHandler` on the flow-run path, at ROOT, with a `prefect.*`-reject filter.** Extend
  **only `configure_prefect_logging()`** (never `configure_api_logging` / `configure_cli_logging`, which have
  no flow-run to ship to) to add Prefect's `APILogHandler` to the **root** logger, formatted with **our
  `ProcessorFormatter`** so records render correctly AND ship to Prefect.
  - **Why root, not a `sapphire_flow`-scoped logger:** the flow module's own `log =
    structlog.get_logger(__name__)` (`flows/run_forecast_cycle.py:92`, `flows/ingest_observations.py:38`,
    `flows/train_models.py:42`, …) resolves to a **bare** name (`run_forecast_cycle`, or
    `__prefect_loader_<id>__`) under file-path `load_script_as_module`, so those flow-body events would NOT
    propagate through a `sapphire_flow`-scoped handler and would be silently missed. A root handler catches
    them regardless of name. (This is why we choose root even though it forces the filter below — it is the
    coherent alternative to the risky A0 entrypoint rewrite.)
  - **Why a `prefect.*`-reject filter is mandatory (duplication guard):** Prefect's own
    `prefect.flow_runs` / `prefect.task_runs` loggers already carry `handlers: [api]` and do **not** set
    `propagate: false` (`.venv/.../prefect/logging/logging.yml:88-95`), so their records propagate to root.
    A bare root `APILogHandler` would ship every native Prefect run/task log **twice**. The added handler
    MUST carry a `logging.Filter` that **rejects** records whose `name == "prefect"` or starts with
    `"prefect."`. Application records (`sapphire_flow.*`, bare flow-module names, `__main__`) pass; native
    Prefect records are dropped by our handler (still shipped once by Prefect's own `api` handler).
  - **Why our own `APILogHandler` instance, not `PREFECT_LOGGING_EXTRA_LOGGERS`:** EXTRA_LOGGERS attaches
    *Prefect's* formatter, which ships the structlog-wrapped-record `repr()` rather than our
    ProcessorFormatter-rendered message. Our own instance carries our formatter.
  - **Accepted trade-off (documented, not silent):** a root handler + `prefect.*`-reject filter will also
    ship third-party records that propagate to root (e.g. `httpx`, `urllib3`) into the flow-run log —
    **INFO+ by default**, not just WARNING+. The handler is pinned to INFO (D3 below), but that only sets the
    floor; whether a given third-party record reaches root at all is governed by *that library's own
    effective logger level*. This is low-volume and arguably useful run context. **Accepted for now; if noisy
    in practice, tighten the filter in a follow-up change** (its design belongs there, not here). Noted so the
    choice is explicit.

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
each flow, `.fn()` bypass, import-safety, the `afrom_source` regression pin) lives in **T2**.

- **T1 — logging config + its API-sink behavioral tests (Part B + B2 mechanism, plus the hook helper).** In
  `src/sapphire_flow/logging.py`, extend `configure_prefect_logging()` (and only it) to attach an
  `APILogHandler` to root inside the existing clear/re-add path, formatted with our `ProcessorFormatter`,
  carrying a `prefect.*`-reject `logging.Filter`, and with an explicit **`api_handler.setLevel(logging.INFO)`**
  (D3 — must not depend on the root level, since `logging.py:58-63` can lower a child to DEBUG). Also add the
  module-level `FlowStateHook` — `configure_flow_run_logging_hook(flow, flow_run, state)` (Part A), which
  **guards `if flow_run.deployment_id is None: return`** and then calls `configure_prefect_logging()` — here or
  in a sibling module, so T2 has one importable hook to pass. The `deployment_id` guard (not a context-probe /
  env guard) is what keeps the hook inert for direct/`.fn()`/ad-hoc/test calls — see Part A. Do not touch
  `configure_api_logging` / `configure_cli_logging` / `configure_test_logging`.
  - **Non-goals:** no decorator wiring yet (T2); no docker-compose change (T5).
  - **Tests:** create a **new** `tests/unit/test_logging.py` (does not exist today — only
    `tests/unit/test_logging_override.py` does; this plan uses the new file, matching the T1 gate below).
    Capture Prefect's API sink (monkeypatch/spy on `emit_api_log` or the client `create_logs`
    path) and assert, all against `configure_prefect_logging()` directly (no flow needed):
    1. an application event (`sapphire_flow.*`) is shipped **exactly once**;
    2. a **native Prefect run event** (name `prefect.flow_runs`) is shipped **exactly once** by the system —
       our root handler's `prefect.*`-reject filter drops the propagated copy, so no duplication;
    3. a **bare-named** flow-module record (name `run_forecast_cycle`) is shipped (root handler catches it
       despite the non-`sapphire_flow` name);
    4. an **unrelated third-party** record (name `httpx`) is shipped — the documented trade-off, pinned so a
       future filter tightening is a conscious change;
    5. INFO records are shipped (not gated to WARNING);
    6. **D3 per-module DEBUG override** — a `sapphire_flow.*` child logger lowered to DEBUG (mimicking
       `SAPPHIRE_LOG_*`) emits a DEBUG and an INFO record; **only the INFO reaches the API sink** while the
       console `StreamHandler` sees both (proves `api_handler.setLevel(INFO)` is explicit, not inherited);
    7. an **out-of-run** emission (no resolvable flow-run context) with
       `PREFECT_LOGGING_TO_API_WHEN_MISSING_FLOW=ignore` produces **no** `warnings.warn` on stderr;
    8. stdout emission continues unchanged (raw-logs fallback preserved);
    9. **idempotence** — configuring twice leaves exactly one `StreamHandler` and one `APILogHandler` on root.
  - **Gate:** `uv run pytest tests/unit/test_logging.py -q` · `uv run ruff check src/sapphire_flow/logging.py`
    · `uv run ruff format --check src/sapphire_flow/logging.py`.

- **T2 — wire the `on_running` hook onto each flow (Part A) + flow-wiring tests.** Add
  `on_running=[configure_flow_run_logging_hook]` to each of the **12 deployed `@flow(...)` decorators
  enumerated in `register_deployments.py`** (there are 12, not 13; `compute_skills.py` hosts two of them —
  `compute_skills_flow` and `compute_combined_skills_flow`). No in-body call, no guard.
  - **Non-goals:** no change to `register_deployments.py` entrypoints (A0 dropped); no change to
    `flows/__init__.py` (stays empty); no `configure_prefect_logging()` body change (T1).
  - **Tests (add a dedicated `tests/unit/flows/test_flow_logging_entry.py`):**
    - (a) assert each of the 12 `Flow` objects carries the hook — `configure_flow_run_logging_hook in
      <flow>.on_running_hooks` (`flows.py:393` stores the list) — an explicit, enumerated set, not a `-k`
      expression that can silently match nothing.
    - (b) **`deployment_id` guard — the blocker's lock.** Call the hook helper directly with a fake `flow_run`
      whose `deployment_id is None` and assert it **returns without** attaching an `APILogHandler` to root (and
      without instantiating a Prefect client); call it again with a non-None `deployment_id` and assert the
      handler **is** attached. This is the exact production-vs-local discriminator. Author this test **red-first
      against the unguarded hook** (which would attach in the `None` case) to prove the guard is doing the work.
    - (b2) **existing direct-call suites stay green under the wired hook.** Re-run
      `tests/unit/flows/test_run_forecast_cycle.py` (~58 direct `run_forecast_cycle_flow(...)` calls) and
      `tests/unit/flows/test_compute_skills.py` (mixed direct + `.fn()` calls) with `on_running` now wired, and
      assert **no** `APILogHandler` ends up on root and no Prefect API client is instantiated during them —
      because those ad-hoc direct calls produce `deployment_id=None`. This is the regression that proves the
      guard prevents test-suite pollution, not the (wrong) `.fn()`-only argument.
    - (c) importing a flow module attaches nothing (the hook is only *passed*, never *called*, at import).
    - (d) **`afrom_source` regression pin (A0-not-needed).** Load the target flow with the real, unmocked
      `Flow.afrom_source(source, entrypoint)` using the **existing file-path (colon) entrypoint form**, over a
      **portable `tempfile.TemporaryDirectory()`** as `source` with the flow file copied in (so
      `LocalStorage.pull_code()` and the colon-branch in `flows.py:1273` actually run). Do **not** use the
      container-only `FLOW_SOURCE_ROOT = "/app"` (`register_deployments.py:22`) as `source` — it is absent on
      dev/CI filesystems and would make the test fail for a reason unrelated to entrypoint form. Add this to
      `tests/unit/cli/test_register_deployments.py` alongside `TestRegisterOne`, or to this file — either is
      fine, but exercise the *real* `afrom_source`, not the mocked one at `test_register_deployments.py:190-209`.
  - **Gate:** `uv run pytest tests/unit/flows/test_flow_logging_entry.py tests/unit/cli/test_register_deployments.py -q`
    · `uv run ruff check src/sapphire_flow/flows` · `uv run ruff format --check src/sapphire_flow/flows`.

- **T4 — documentation.** Update:
  - `docs/standards/logging.md:40-47` and `:118-150` — correct the `configure_prefect_logging()` description:
    it is now invoked **via a Prefect `on_running` flow-state hook at each flow-run start** (not "once at
    worker startup"); document the root `APILogHandler` + `ProcessorFormatter` + `prefect.*`-reject filter
    topology, the INFO ship level, the missing-context → `ignore` behavior, and the idempotence contract.
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
  - **Gate:** `uv run pre-commit run --files docs/standards/logging.md docs/plans/README.md` (markdown
    hygiene hooks); manual read-through.

- **T5 — deploy env (Part B2).** Add `PREFECT_LOGGING_TO_API_WHEN_MISSING_FLOW=ignore` to the
  `prefect-worker` (`docker-compose.yml:80`) and `prefect-worker-ingest` (`:144`) service environments in
  `docker-compose.yml`.
  - **Non-goals:** no change to `init` (registration doesn't attach the API handler); no other env churn.
  - **Gate:** `uv run pytest tests/ -q -k compose` if a compose-lint test exists; else `docker compose config`
    dry-parse (read-only).

- **T6 — deploy verification (after Plan 103).** On the mini, no manual `PREFECT_HOME` override anywhere.
  The promised event classes live in **different deployments**, so a single run cannot prove them all
  (`ingest.qc_complete` is emitted by observation-ingest, `flows/ingest_observations.py:557`; `nwp.*` / model
  events by the forecast cycle, `flows/run_forecast_cycle.py`). Verify **two** runs independently:
  1. **forecast-cycle run** — `prefect flow-run logs <fc-id>` contains that run's `nwp.*` and model events
     (and none of them appear more than once).
  2. **ingest-observations run** — `prefect flow-run logs <ingest-id>` contains `ingest.qc_complete`.
  - **Non-vacuous native-dedup check (major fix).** A plain "no duplicate native lines" check is vacuous under
    `PREFECT_LOGGING_LEVEL=WARNING` (native `prefect.flow_runs` INFO lifecycle lines may not ship at all, so
    zero copies falsely passes). Instead, inside one representative run emit a **unique native Prefect WARNING**
    via `get_run_logger().warning("<uuid-marker>")` and assert that marker appears **exactly once** in
    `prefect flow-run logs` (proves the `prefect.*`-reject filter drops our root-handler copy while Prefect's
    own `api` handler ships it once). Separately emit a unique **structlog** app marker and assert it too
    appears exactly once (proves our handler ships it and root propagation doesn't double it).
  - No stderr `PREFECT_LOGGING_TO_API_WHEN_MISSING_FLOW` warnings in `docker compose logs prefect-worker`.
  - **Gate:** manual, captured in the plan's implement notes.

### Phase graph

```json
{
  "phases": [
    { "id": "T1", "title": "logging config: root APILogHandler + prefect.* filter + hook helper + API-sink tests", "depends_on": [] },
    { "id": "T2", "title": "wire on_running hook onto 12 flows (drop A0) + flow-wiring/afrom_source tests", "depends_on": ["T1"] },
    { "id": "T4", "title": "docs: standards/logging.md + plans/README.md", "depends_on": ["T1", "T2"] },
    { "id": "T5", "title": "docker-compose: PREFECT_LOGGING_TO_API_WHEN_MISSING_FLOW=ignore on workers", "depends_on": [] },
    { "id": "T6", "title": "deploy verification on mini (after Plan 103)", "depends_on": ["T2", "T4", "T5", "103"] }
  ]
}
```

## References

- `src/sapphire_flow/logging.py` — `configure_prefect_logging` `:82` (the one to extend); `_apply_structlog_config`
  clear/re-add of the root `StreamHandler` `:51-56` (the idempotent path Part B must join); `ProcessorFormatter`
  `:34,43`.
- `src/sapphire_flow/flows/*.py` — the 12 `@flow(...)` decorators to add `on_running=[…]` to (all use the
  parenthesised form, e.g. `run_forecast_cycle.py:1222`, `ingest_observations.py:396`, `train_models.py:212`,
  `compute_skills.py:261,325`); flow-module loggers `structlog.get_logger(__name__)`
  (`run_forecast_cycle.py:92`, `ingest_observations.py:38`, `train_models.py:42`, …) that become bare names
  under file-path loading.
- `src/sapphire_flow/cli/register_deployments.py:138` — file-path entrypoint (`f"src/{…}.py:{attr}"`),
  **kept unchanged**; `:135` — `importlib.import_module(spec.flow_module)` already imports the dotted path
  successfully today (the fact that defuses the old "A0 crashes registration" claim); `:91-95` — the
  `compute_combined_skills_flow` spec, one of the 12 (not a 13th flow).
- **Prefect internals (facts to RE-VERIFY, not durable anchors).** Line numbers are Prefect 3.6.23 (`uv.lock`)
  and rot on the next `uv sync`; the rationale for each already lives once in the Design section, so this is a
  bare citation index (the durable form is a one-line comment at each call site — `logging.py` by the
  filter/`setLevel`, the flow decorator by `on_running`, the hook by the `deployment_id` guard):
  - `on_running: list[FlowStateHook]` accepted/stored — `prefect/flows.py:222,393`; invoked only from
    `call_hooks()` at the Running transition, before the body — `prefect/flow_engine.py:527,768-786,1003,506-507`.
  - `<flow>.fn` = raw undecorated function (bypasses engine) — `prefect/flows.py:313`.
  - `flow_run.deployment_id: Optional[UUID]` (the guard discriminator) — `prefect/client/schemas/objects.py:1132`.
  - `prefect.flow_runs`/`prefect.task_runs` have `handlers: [api]`, no `propagate: false` → reach root (why the
    `prefect.*`-reject filter is mandatory) — `prefect/logging/logging.yml`.
  - `APILogHandler.handleError` branches on `PREFECT_LOGGING_TO_API_WHEN_MISSING_FLOW` (default `"warn"` →
    `warnings.warn`; set `ignore`) — `prefect/logging/handlers.py`, `prefect/settings/models/logging.py`.
  - `load_script_as_module` names a file-path-loaded module by bare filename (why we attach at root) —
    `prefect/utilities/importtools.py`.
  - (A0 background only) `Flow.afrom_source` treats any `:` as file-path — `prefect/flows.py:1273`.
- `docs/standards/logging.md` (T4 target); `docs/plans/README.md:41-43` (T4 target, resolves the design
  contradiction).
- Plan 103 (the PREFECT_HOME half, its dependency); Plan 084 finding D7 (independent note of the unconfigured
  structlog). The full round-by-round `/plan` design lives in git history on branch `feat/plan-103`.
