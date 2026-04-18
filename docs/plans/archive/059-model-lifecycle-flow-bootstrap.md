# Plan 059 — Deployment bootstrap for model-lifecycle flows

**Status**: READY
**Revision**: 2 — fold in three-agent critical review findings (2026-04-18): (a) `forcing_source` correctly classified as adapter (not store) — after code-path inspection, pass-through as `None` rather than fail-fast; the `for unit in units:` loop at `onboard_model.py:572` only dereferences it when scope is non-empty, so empty-scope register-only runs (A3 step 1's use case) succeed; deployment-triggerable training via adapter-registry becomes future scope; (b) `deployment_config` is REQUIRED at runtime (confirmed at `compute_skills.py:128` via `.get_season_definitions()` and `onboard_model.py:218` via `.available_nwp_parameters`), must load from `SAPPHIRE_CONFIG`; (c) add defensive None-check block after bootstrap, mirroring `run_forecast_cycle.py:360-379`; (d) T2/T3 store lists written out explicitly (subagent implements, does not re-derive); (e) minor concurrency + AUTOCOMMIT notes added to Risks / Deferred.
**Date**: 2026-04-18
**Depends on**: Plan 044 (DONE, archived — shipped the ops-flow bootstrap pattern that this plan replicates)
**Blocks**: Plan 046 Stream A — specifically A3 from step 1 onwards
**Scope**: Add the `setup_production_stores(...)`-bootstrap block to the four Prefect deployments that Plan 044 left without one, so each can be invoked by `prefect deployment run <name>` with default (`None`) store params and resolve stores from the production DB via env-var `DATABASE_URL`. Strictly pattern replication — no new abstraction, no behaviour change beyond "can now be triggered by the Prefect worker."

---

## Context

### Why now

Plan 046 A3 (5-station dress rehearsal) attempted `prefect deployment run onboard-model --param model_id=linear_regression_daily` and the flow failed immediately with:

```
AttributeError: 'NoneType' object has no attribute 'register_model'
```

Root cause: `onboard_model_flow`'s signature defaults every store parameter to `None`. Downstream code then dereferences `model_store.register_model(...)` without a bootstrap. Plan 044's "deployment readiness" added the bootstrap pattern to the four operational flows (`ingest-observations`, `onboard-stations`, `run-forecast-cycle`, `run-hindcast`) but never wired it into the model-lifecycle flows because the A3 dress rehearsal hadn't run yet. This is a Plan 044 completeness gap surfaced by Plan 046 A3.

### Inputs (verified)

- `src/sapphire_flow/flows/_db.py:62-70` already provides `setup_production_stores(database_url) → (conn, stores)` returning a dict of 15 stores (model_store, station_store, obs_store, forecast_store, hindcast_store, skill_store, artifact_store, basin_store, group_store, flow_regime_store, baseline_store, model_state_store, weather_forecast_store, forcing_store, alert_store). No new wiring needed there.
- `src/sapphire_flow/flows/run_forecast_cycle.py:310-325` is the canonical bootstrap pattern. All four ops flows use the same shape.
- `DATABASE_URL` env var is set in every sapphire-flow container by `docker/entrypoint.sh` via the `DATABASE_URL_TEMPLATE` + `db_password` secret. Already available at flow runtime.
- `backup_database_flow` takes no store params and reads `DATABASE_URL` directly inside `dump_database_task`. **Already deployment-ready — out of scope for this plan but verified in T5.**

### Problem statement

Four Prefect deployments cannot be invoked in production:

| Deployment | Flow entry point | Failure symptom |
|---|---|---|
| `onboard-model` | `sapphire_flow.flows.onboard_model:onboard_model_flow` | `AttributeError: 'NoneType' object has no attribute 'register_model'` at `model_registry.register_models(..., model_store=None, ...)` |
| `train-models` | `sapphire_flow.flows.train_models:train_models_flow` | `None`-dereferencing on first store access (unverified; symptom class identical) |
| `compute-skills` | `sapphire_flow.flows.compute_skills:compute_skills_flow` | same |
| `compute-combined-skills` | `sapphire_flow.flows.compute_skills:compute_combined_skills_flow` | same module, second flow |

A3 cannot proceed past step 1 (onboard-model) until these land.

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Replicate the `run_forecast_cycle_flow` bootstrap pattern verbatim in each affected flow.** Block sits at the top of the flow body, pulls `DATABASE_URL` from `os.environ`, calls `setup_production_stores`, pulls only the stores that flow needs from the returned dict. | Lowest-risk; same shape as the four working ops flows; no new abstraction. |
| D2 | **`_conn` held on a local variable to prevent GC; no explicit close.** Matches `run_forecast_cycle_flow` behaviour at `run_forecast_cycle.py:310`. | Flow is a short-lived process — connection released on worker task exit. Not worth the async-context-manager complexity here. |
| D3 | **Bootstrap only fires when `station_store is None` (or an equivalent "injection not provided" sentinel).** When tests inject stores directly, the bootstrap must stay out of the way. | Keeps the existing unit-test patterns working. |
| D4 | **Per-flow bootstrap-path tests**: one test per flow that monkeypatches `setup_production_stores` and asserts (a) the flow calls it when stores are `None`, and (b) the returned stores are wired into the first downstream service call. | Catches bootstrap-wiring drift in the future. |
| D5 | **Do NOT refactor the four working ops flows' bootstrap blocks.** Temptation to DRY them into a shared helper is explicitly rejected for this plan. If a shared helper wants to happen, spawn a separate refactor plan later. | Contain scope. Plan 059 is "unblock A3", not "refactor bootstrap pattern." |
| D6 | **`backup-database` is out of scope.** Already reads `DATABASE_URL` directly via `dump_database_task`. T5 verifies it still works — if it does, no change. | Don't touch working code. |

---

## Phases

### T1 — Bootstrap `onboard_model_flow`

File: `src/sapphire_flow/flows/onboard_model.py`, function `onboard_model_flow` starting at line 399.

**Stores the flow needs (9, verified exhaustive)** — evidence for each at `onboard_model.py` line:
`model_store` (line 474), `station_store` (484, 507, 575, 634, 675, 758), `group_store` (485, 508, 576, 635, 758), `obs_store` (573, 632, 673), `basin_store` (509, 574, 636), `artifact_store` (597, 603, 743), `hindcast_store` (633, 672), `skill_store` (674, 686), `flow_regime_store` (676).

**Not a store — `forcing_source`** (signature line 415) is a live `WeatherReanalysisSource` adapter. `setup_production_stores` does NOT return one; `stores["forcing_store"]` is `PgHistoricalForcingStore` (DB time-series store), completely different.

**Handling**: pass-through as `None` at bootstrap; do NOT fail-fast. `forcing_source` is used only inside the `for unit in units:` loop at `onboard_model.py:572`. When `_determine_onboarding_scope_task` returns an empty scope (the common case for A3 step 1: "register the model class before any stations exist"), the loop never iterates and `forcing_source` is never dereferenced. For non-empty scope, the existing code passes `forcing_source=None` into `_assemble_onboarding_data_task` and the failure there is the existing code path (pre-059), not a Plan 059 regression. If a future plan wires a deployment-triggerable adapter-registry, training-via-deployment becomes possible; for now the deployment-trigger path supports register-only runs (empty scope) and actual training runs require a Python-side injected adapter.

**Required config — `deployment_config`** (signature line 416) IS required at runtime (`onboard_model.py:218` dereferences `.available_nwp_parameters`). Load from `SAPPHIRE_CONFIG` env var, mirroring `run_forecast_cycle.py:330-339`.

Insert at the top of the flow body (place it BEFORE the `clock is None` block AND before `register_models(..., model_store, ...)` at line 474):

```python
_conn: object = None
if station_store is None:
    import os

    from sapphire_flow.flows._db import setup_production_stores

    database_url = os.environ["DATABASE_URL"]
    _conn, stores = setup_production_stores(database_url)
    model_store = stores["model_store"]
    station_store = stores["station_store"]
    group_store = stores["group_store"]
    obs_store = stores["obs_store"]
    basin_store = stores["basin_store"]
    artifact_store = stores["artifact_store"]
    hindcast_store = stores["hindcast_store"]
    skill_store = stores["skill_store"]
    flow_regime_store = stores["flow_regime_store"]

if deployment_config is None:
    import os

    config_path = os.environ.get("SAPPHIRE_CONFIG")
    if config_path is not None:
        from sapphire_flow.config.deployment import load_config

        deployment_config = load_config(config_path)
    else:
        from sapphire_flow.config.deployment import DeploymentConfig

        deployment_config = DeploymentConfig(max_retention_days=600)

# forcing_source is passed through as-is. None is allowed for empty-scope (register-only)
# runs; the for-unit loop at line 572 only dereferences it when the scope is non-empty.
# If a future plan wires deployment-triggerable adapter injection, revisit.

# Defensive None-checks — catches partial-injection bugs like {station_store=Mock(), model_store=None}
if model_store is None:
    raise ConfigurationError("model_store is required but was not provided")
if group_store is None:
    raise ConfigurationError("group_store is required but was not provided")
if obs_store is None:
    raise ConfigurationError("obs_store is required but was not provided")
if basin_store is None:
    raise ConfigurationError("basin_store is required but was not provided")
if artifact_store is None:
    raise ConfigurationError("artifact_store is required but was not provided")
if hindcast_store is None:
    raise ConfigurationError("hindcast_store is required but was not provided")
if skill_store is None:
    raise ConfigurationError("skill_store is required but was not provided")
if flow_regime_store is None:
    raise ConfigurationError("flow_regime_store is required but was not provided")
```

**Imports to add at module top** (both `onboard_model.py` and `train_models.py` — neither currently imports them; `compute_skills.py` same):
```python
import os  # if not already present (onboard_model.py does not currently have it)

from sapphire_flow.exceptions import ConfigurationError
```
Do NOT leave `import os` and `ConfigurationError` inside the `if` branches — hoist to module top so the module-level defensive-check block always has the name available. (The sketch above shows `import os` inside the `if` for concise reading; actual implementation should have the real imports up top, matching `run_forecast_cycle.py:1-20`.)

The `_conn` local is held purely for GC-prevention so the underlying psycopg connection stays open for the lifetime of the flow. Add a trailing comment on the variable declaration to signal this to future ruff / reviewers (e.g. `_conn: object = None  # noqa: F841 — GC anchor for bootstrapped DB connection`).

### T2 — Bootstrap `train_models_flow`

File: `src/sapphire_flow/flows/train_models.py`.

**Stores needed (9, identical set to T1)** — evidence at `train_models.py`:
`model_store` (239, 252), `station_store` (253, 280, 340, 381), `group_store` (254, 281, 341), `obs_store` (278, 338, 379), `basin_store` (279, 342), `artifact_store` (306, 311), `hindcast_store` (339, 378), `skill_store` (380), `flow_regime_store` (382).

**Also required**: `deployment_config` (line 383, passed to `compute_skills_task`). Load from `SAPPHIRE_CONFIG` (same pattern as T1).

**`forcing_source`** (adapter, not store): pass-through as `None` — same handling as T1. `train_models_flow` uses `forcing_source` in the training path; empty-scope invocations (no stations) short-circuit without dereferencing it. Non-empty scope via deployment trigger requires a separate adapter-injection mechanism (deferred).

Replicate the T1 block (stores bootstrap + `deployment_config` load + defensive None-checks). Guard sentinel: `station_store is None`.

### T3 — Bootstrap `compute_skills_flow` + `compute_combined_skills_flow`

File: `src/sapphire_flow/flows/compute_skills.py`. Two `@flow` decorated functions in the same module; each gets its own bootstrap block.

**Stores needed (5, same set for both flows)** — evidence at `compute_skills.py`:
`hindcast_store` (99), `obs_store` (116), `skill_store` (146), `station_store` (119), `flow_regime_store` (121).

**Also required**: `deployment_config` (line 128 — `.get_season_definitions()`). Load from `SAPPHIRE_CONFIG`.

**NOT required**: `forcing_source`, `group_store`, `basin_store`, `artifact_store`, `model_store`, `obs_store` (for `compute_skills_task`) — verified by reading the task bodies at lines 99-150 and 189-233.

Smaller block than T1/T2 — no `forcing_source` handling needed (not in signature):

```python
_conn: object = None
if station_store is None:
    import os

    from sapphire_flow.flows._db import setup_production_stores

    database_url = os.environ["DATABASE_URL"]
    _conn, stores = setup_production_stores(database_url)
    station_store = stores["station_store"]
    hindcast_store = stores["hindcast_store"]
    obs_store = stores["obs_store"]
    skill_store = stores["skill_store"]
    flow_regime_store = stores["flow_regime_store"]

if deployment_config is None:
    import os

    config_path = os.environ.get("SAPPHIRE_CONFIG")
    if config_path is not None:
        from sapphire_flow.config.deployment import load_config

        deployment_config = load_config(config_path)
    else:
        from sapphire_flow.config.deployment import DeploymentConfig

        deployment_config = DeploymentConfig(max_retention_days=600)

if hindcast_store is None:
    raise ConfigurationError("hindcast_store is required but was not provided")
if obs_store is None:
    raise ConfigurationError("obs_store is required but was not provided")
if skill_store is None:
    raise ConfigurationError("skill_store is required but was not provided")
if flow_regime_store is None:
    raise ConfigurationError("flow_regime_store is required but was not provided")
```

Apply this block to BOTH `compute_skills_flow` and `compute_combined_skills_flow` (the two `@flow` wrappers). The `@task` inner functions (`compute_skills_task`, `compute_combined_skills_task`) are NOT themselves bootstrapped — they are invoked from the `@flow` wrappers which now guarantee stores are present. Direct callers of the tasks must still inject stores; in-scope only for deployment-triggered flow runs.

### T4 — Per-flow bootstrap-path unit tests

Check each target test file first (`tests/unit/flows/test_onboard_model.py`, `test_train_models.py`, `test_compute_skills.py` — all three likely exist; verify before editing). Extend the existing file with one new test class/method per flow; do not create a separate file per flow. Create only if the test file is genuinely absent. Each test:

1. Sets `DATABASE_URL` via `monkeypatch.setenv`.
2. Patches `sapphire_flow.flows._db.setup_production_stores` to return `(MagicMock(), {<required stores as MagicMocks>})`.
3. Calls the flow with all store params left as default (`None`).
4. Asserts `setup_production_stores` was called once with the expected DB URL.
5. Asserts the flow's first downstream store call landed on the mocked store (proving the wiring is right).

Keep each test under ~30 lines. Use `AsyncMock` where needed.

### T5 — End-to-end validation on the running compose stack

The orchestrator has a running `staging-5-stations` compose stack at `http://localhost:8010` and Prefect at `http://localhost:4200`. After T1-T4 land and the sapphire-flow image is rebuilt:

Trigger each of the 4 affected deployments via `PREFECT_API_URL=http://localhost:4200/api uv run python -c "..."` (same pattern the orchestrator used in A3 step 1). For each, record the final state + a one-line error message if any. Expected: all 4 reach `COMPLETED`.

Also trigger `backup-database` once (no params) to confirm it still works — that is the D6 sanity check.

If a flow fails for a **non-bootstrap reason** (e.g. missing data, missing station records), that is a Plan 046 A3 finding to carry back, not a Plan 059 regression — note it clearly in the T5 report and let the orchestrator decide whether to keep going.

### T6 — Commit + bump + tag

- Run `uv run ruff format` + `uv run ruff check --fix` on all touched files.
- **Gate 1 (fast, local)**: `uv run pytest tests/unit/flows/ -q` — must be green. All flow-module unit tests pass, including the new bootstrap-path tests.
- **Gate 2 (slow, safety)**: `uv run pytest tests/ -q` — **full suite must match or exceed the pre-059 count of 1160**, plus the new bootstrap-path tests (expect +4, one per flow; +5 if both `compute_skills_flow` and `compute_combined_skills_flow` get separate tests). If any existing test fails, stop and report — do NOT commit.
- `uv run bump-my-version bump patch` (0.1.313 → 0.1.314).
- `uv sync` to catch the lock.
- Stage edits + tests + version files.
- Commit on `main` with conventional message:

```
feat(plan-059): wire deployment bootstrap for model-lifecycle flows

Plan 046 A3 surfaced that onboard-model, train-models, compute-skills,
and compute-combined-skills fail immediately when triggered as Prefect
deployments (AttributeError on None.register_model / None.<method>).
Plan 044 wired the bootstrap pattern only into the four operational
flows; this plan completes the job by replicating setup_production_stores
across the four model-lifecycle flows.

Identical pattern to run_forecast_cycle_flow:310-325 — no new
abstraction, no behaviour change beyond deployment runnability.

Unblocks Plan 046 A3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

- `git tag v$(uv run bump-my-version show current_version)`.
- Do NOT push.

### T7 — Archive plan

Move this file to `docs/plans/archive/059-model-lifecycle-flow-bootstrap.md` in a follow-up `docs(plan-059): archive completed plan` commit + its own patch bump.

---

## Files to modify

| Path | Task | Change |
|---|---|---|
| `src/sapphire_flow/flows/onboard_model.py` | T1 | Add bootstrap block at top of flow body; ~15 lines |
| `src/sapphire_flow/flows/train_models.py` | T2 | Add bootstrap block; size TBD by subagent |
| `src/sapphire_flow/flows/compute_skills.py` | T3 | Two bootstrap blocks (one per flow); ~30 lines total |
| `tests/unit/flows/test_onboard_model.py` | T4 | New or extended bootstrap-path test |
| `tests/unit/flows/test_train_models.py` | T4 | Same |
| `tests/unit/flows/test_compute_skills.py` | T4 | Two new tests (one per flow) |

No files deleted. No config changes. No compose changes.

---

## Exit gates

1. All four affected Prefect deployments reach `COMPLETED` when triggered via the Prefect API with minimal params.
2. `backup-database` still works (D6 sanity — no regression).
3. `uv run pytest tests/ -q` remains green at pre-059 count (1160+ plus the new bootstrap-path tests — expect +4 to +6).
4. Commit + tag landed on `main`.
5. Orchestrator can resume Plan 046 A3 step 1.

---

## Dependency graph

```json
{
  "plan-059": {
    "tasks": ["T1", "T2", "T3", "T4", "T5", "T6", "T7"],
    "parallel": {"T1-T3 can be parallelised but T4 depends on T1-T3, T5 depends on T4, T6 depends on T5, T7 after T6 commit"},
    "depends_on": []
  }
}
```

A careful subagent can do T1-T4 as one coordinated pass (it's four near-identical pattern applications), then T5 as a validation, then T6 commit. T7 is a separate archive commit.

---

## Risks

| Risk | Mitigation |
|---|---|
| Each flow needs a *different* store subset; pulling the wrong subset causes a runtime error at the first call site. | T4 tests exercise each flow's happy path; T5 triggers the real deployment. |
| Adding the bootstrap block breaks existing unit tests that injected stores directly. | The `if station_store is None:` guard short-circuits the bootstrap when stores are injected. All ~30 existing unit tests for these flows should keep passing unchanged. |
| `compute_skills_flow` / `compute_combined_skills_flow` dereference `deployment_config.get_season_definitions()` at `compute_skills.py:128`; `onboard_model_flow` dereferences `deployment_config.available_nwp_parameters` at `onboard_model.py:218`. Crash with `AttributeError` if left `None`. | T1/T2/T3 MUST load `SAPPHIRE_CONFIG` via `load_config()` when `deployment_config is None`, mirroring `run_forecast_cycle.py:330-339`. Mandatory, not optional. |
| `forcing_source` is a live `WeatherReanalysisSource` **adapter** (not a store) — `setup_production_stores` cannot return one. Onboarding/training need real historical forcing for training data assembly (but only when scope is non-empty). | T1/T2 pass `forcing_source=None` through untouched. Empty-scope register-only runs (A3 step 1) succeed; non-empty scope via deployment trigger would crash at the first `_assemble_onboarding_data_task` call — by design, since no deployment-triggerable adapter-injection mechanism exists yet. A future plan can wire a deployment-level adapter registry. Do NOT map `forcing_source` to `stores["forcing_store"]` — that is a DB time-series store, semantically distinct. |
| Bootstrap + `concurrency("model_training:{model_id}", occupy=1)` at `onboard_model.py:476`: two concurrent triggers for the same `model_id` both open a DB connection before either enters the guard. | Minor connection waste (2× instead of 1×), not a correctness bug. Second connection released when second invocation's flow body exits. Acceptable for v0 single-worker setup. |
| `setup_production_stores` hard-codes `isolation_level="AUTOCOMMIT"` at `_db.py:68`. Model-onboarding writes (`register_models`, `store_artifact`, `store_hindcast`, `store_skill_scores`, `promote_artifact`, `store_model_assignment`) are not transactionally linked. Partial failure leaves orphan artifacts/hindcasts. | Pre-existing behaviour inherited from the four working ops flows. Acceptable for v0; noted in Deferred. |
| T5 surfaces a **different** A3 bug (e.g. missing stations, period-start validation) unrelated to bootstrap. | T5 brief explicitly says: any non-bootstrap failure is a Plan 046 finding, not a 059 regression. Report and return. |
| The four working ops flows diverge from the model-lifecycle bootstrap pattern we're adding (e.g. slightly different `DATABASE_URL` handling). | D5 forbids refactoring the working ops flows. New blocks match the canonical `run_forecast_cycle_flow:310-325` pattern exactly. |

---

## Deferred to follow-up plans

- **Shared bootstrap helper** to DRY the block across all eight flows that now have it. Deliberately not done here; candidate follow-up once the pattern is stable in all eight places. After Plan 059 archives, the pattern is in all eight places and the consolidation is effectively blocked on nothing but an orchestrator decision — recommend spawning the follow-up plan immediately.
- **Connection lifecycle hygiene** (explicit `conn.close()` in a `try/finally` or async context manager). Not worth the risk for this unblock; matches the existing four ops flows' behaviour.
- **Onboarding transactional hygiene** — wrap the artifact + hindcast + assignment writes in a single SQL transaction so partial failure doesn't leave orphans. Requires reworking `setup_production_stores` to drop the hard-coded AUTOCOMMIT. Substantial scope; not a blocker for v0.
- **Bootstrap observability** — emit a `bootstrap.completed` structured event with `duration_ms` and `store_count` in a future pass that covers all 8 flows at once. Inconsistent to add to 4 new sites without backfilling the 4 existing ones, which D5 forbids in this plan.
- **`DATABASE_URL` / `SAPPHIRE_CONFIG` / `PREFECT_API_URL` env-var inventory in `cicd.md`** — the canonical runtime-env contract is not currently documented as a standard; worth capturing once eight flows share the same bootstrap.
- **Any A3 finding surfaced in T5** that isn't a bootstrap bug rolls back to Plan 046.

---

## Open questions

Resolved by Rev 2 critical review (both questions answered):

1. ~~Does `onboard_model_flow`'s `forcing_source` param need a `forcing_store`-like wiring or a live adapter?~~ **Answered: adapter (not a store); pass-through as `None` at bootstrap. Only dereferenced inside the per-unit loop at `onboard_model.py:572`, so empty-scope register-only deployment invocations succeed. Non-empty-scope training via deployment trigger remains unsupported — deferred to a future adapter-registry plan.** See Risks table + T1 sketch.
2. ~~Does any of the four flows require `deployment_config` loaded from env?~~ **Answered: yes, all four — mandated in T1/T2/T3.**

Still open, non-blocking:

3. After Plan 059 is DONE, the Plan 046 A5 dress-rehearsal report should flag that A3 revealed this Plan 044 completeness gap. Agreed — add to the "what we fixed" section when A5 drafts.
4. T5 validation uses the staging-5-stations compose stack. `onboard-model` + `train-models` triggered via `prefect deployment run` with empty scope (no pre-existing stations/groups) succeed — bootstrap fires, stores resolve, and `_determine_onboarding_scope_task` returns empty units, so `forcing_source=None` is never dereferenced. Triggers after `onboard-stations` has populated stations will attempt to iterate units, then crash at `_assemble_onboarding_data_task` (`onboard_model.py:569-577`) because `forcing_source` is still `None` — that is a distinct finding (no deployment-triggerable adapter-injection mechanism) that rolls up to a future plan, not Plan 059 scope. T5 report: validate that (a) empty-scope register-only triggers succeed post-bootstrap, (b) non-empty-scope triggers fail at the expected line (confirms the bootstrap block is correctly placed and the downstream crash is the pre-existing code path, not a 059 regression).
5. T6 version bump cadence: subagent reads current `pyproject.toml` version at execution time — the `0.1.313 → 0.1.314` nominal may be ahead by the time this runs.
