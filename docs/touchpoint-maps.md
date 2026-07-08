# SAPPHIRE Flow — Touchpoint Maps

Reusable per-subsystem **routing checklists**. When a task touches a subsystem, the
Multi-Model Review **context packet** (see `docs/workflow.md` § Multi-Model Review →
Context packet) points into the relevant map below: it names the touchpoints to
inspect, the contracts that must not change silently, and the verification to run —
without re-deriving the subsystem.

These are **routing signposts, not architecture docs**, governed by the right-sizing
fitness test in `docs/workflow.md` § Multi-Model Review → Right-sizing: every bullet
names a symbol/subsystem to go read; no bullet teaches how the code works; a “must not
change silently” contract covers only a surprising, high-consequence, cross-cutting
invariant. Symbol names only — no line numbers or file paths (they rot). Verify a map
against the code (an independent code-grounded pass, e.g. `codex exec -s read-only`)
whenever it is added or touched.

## Maps

- **ForecastInterface / model execution** — model boundary, adapters, data
  requirements, operational input assembly + time-series preprocessing,
  ModelFailure / ModelOutputError.
- **Forecast cycle / assignment selection** — cycle phase sequence, assignment
  resolution / priority / fallback, STATION vs GROUP dispatch, combination modes,
  alerting / persistence attach points.
- **Persistence / API write path** — store write methods, transaction / commit
  scoping, optimistic locking, idempotency, the JSONB / PostGIS boundary,
  StoreError classification, the single API mutation.

---

### Touchpoint map: ForecastInterface / model execution

Use this map when a task touches ForecastInterface behavior, model adapters,
model data requirements, operational input assembly, time-series preprocessing,
prediction input assembly, model execution, or ModelFailure semantics. For
forecast-cycle control flow — phase sequence, assignment resolution, STATION/GROUP
dispatch — see the **Forecast cycle / assignment selection** map below.

Before planning or implementation, inspect the relevant touchpoints below and
include them in the task context packet.

**Common touch triggers:**

- ForecastInterface Protocol or adapter behavior
- model `data_requirements` (SAP3 `ModelDataRequirements` / FI `InputRequirement`)
- `ModelFailure` / `ModelOutputError` behavior
- prediction input assembly
- operational input assembly / source fetch
- time-series preprocessing (resampling / aggregation / windowing)
- requirement-superset construction
- NWP coverage / input-quality gating
- model discovery / registry wrapping (`adapt_if_fi`)
- model assignment / selection
- forecast cycle orchestration
- output shape or persistence behavior
- tests that exercise model execution or forecast cycle behavior

**Upstream inputs to inspect:**

- model assignment and priority selection
- station / forecast-cycle configuration
- weather / hydrological input availability
- data-requirement construction and overrides
- persisted model artifacts and model metadata

**Core implementation touchpoints:**

- ForecastInterface definition and adapters
- model discovery / registry wrapping — FI entry-point models wrapped via
  `adapt_if_fi()` in `discover_models()` so all callers get SAP3-compatible models
- operational input assembly
- forecast cycle orchestration
- model execution call sites
- error/failure handling around prediction
- output normalization before persistence

**Downstream consumers to inspect when behavior changes:**

- forecast persistence / API write path (write-side contracts: see the
  **Persistence / API write path** map)
- dashboard or API readers if output schema changes
- logs / operational observability
- alerting or quality gates that depend on model success/failure
- tests and fixtures that assume current output shape or failure behavior

**Operational inputs / time-series preprocessing**

How raw source data becomes prediction inputs, *before* the model boundary above.
Inspect on tasks touching source fetch, input assembly, resampling / aggregation,
windowing, requirement-superset construction, or the NWP coverage / input-quality
gates.

- input assembly: `assemble_station_operational_inputs` /
  `assemble_group_operational_inputs` build four channels — past_targets,
  past_dynamic (reanalysis), future_dynamic (NWP), static — plus warm-up state
- hindcast reimplements assembly independently (`_assemble_hindcast_inputs`): uses
  neither `assemble_*_operational_inputs` nor `resample_to_time_step`, derives from
  one model's `data_requirements` (not `build_superset_requirements`), and has its
  own issue-time conventions — diff it separately on any assembly / issue-time /
  requirements change
- sources: observation store, reanalysis (`HybridForcingSource`), NWP store +
  `GridExtractor` (basin-average, runs at flow level), basin store, model-state store
- preprocessing: `resample_to_time_step` (precip SUM, temp/discharge MEAN), NWP
  hourly→daily + issue-time filter + horizon cap, lookback wide-pivot, `ensure_utc`
- the cycle assembles a **superset** (`build_superset_requirements`); each model
  slices it
- gates: `assess_future_coverage` (horizon truncation), `assess_input_quality`
  (degraded / partial input flags)

**Contracts that must not change silently:**

- FI model anticipated failures return `ModelFailure` (never raised from inside
  the model); the SAP3 adapter surfaces the pre-`predict` `max_nan` gate and total
  FI failure as `ModelOutputError` at the adapter/orchestration boundary
- data requirements must match what input assembly actually provides
- output shape and station / issue-time identity remain stable
- assignment priority and fallback semantics remain explicit
- **no imputation** — missing operational-input values are gated (`max_nan`), never
  imputed / interpolated / filled
- `resample_to_time_step` is shared with the **training** path (hindcast uses
  neither) — a change there hits operational *and* training preprocessing
- `HybridForcingSource` `priority` order decides which source's forcing wins per
  `(station, valid_time, parameter)` — reordering it silently changes model inputs
- repo-specific Task Exit Gate still applies before PR approval

**Suggested verification:**

- focused tests around the changed adapter or input-assembly path
- forecast-cycle test covering assignment → input assembly → model execution
- regression test for `ModelFailure` behavior when expected data is missing
- regression test that missing operational data is *gated, not filled* (assert
  `max_nan`, not imputation)
- `assess_input_quality` coverage (`test_input_quality.py`) when changing staleness /
  degraded-input thresholds or `OperationalInputMetadata` fields
- log/observability assertion if changing operational warnings
- full Task Exit Gate for implementation PRs

**Context packet reminder:**

When this map applies, the context packet should name:

- which touch trigger applies
- which upstream inputs were inspected
- which downstream consumers are affected or explicitly unaffected
- which contracts are at risk
- which focused tests will prove the change

### Touchpoint map: Forecast cycle / assignment selection

Use this map when a task touches forecast-cycle control flow — the phase sequence, model-assignment resolution (priority / fallback / status filtering), STATION vs GROUP dispatch, fan-out / parallelisation, combination-mode selection, or where alerting and persistence attach. For the model boundary itself, `data_requirements`, operational input assembly, and time-series preprocessing, use the **ForecastInterface / model execution** map above — do not re-derive that detail here.

Before planning or implementation, inspect the relevant touchpoints below and include them in the task context packet.

**Common touch triggers:**

- forecast-cycle phase ordering (`run_forecast_cycle_flow`)
- model-assignment fetch / status filtering / priority sort
- STATION vs GROUP dispatch, and the per-model fallback behavior
- fan-out / `.submit` / `task.map` parallelisation
- combination-mode selection (`ModelCombinationStrategy`)
- where alerting (`check_station_alerts`) attaches to the cycle
- where forecast / model-state persistence attaches
- `clock` / `rng` / `config` / `qc_rules` injection at the flow boundary
- cycle health / result assembly (`ForecastCycleResult`)
- tests that exercise cycle sequencing or assignment resolution

**Upstream inputs to inspect:**

- operational station selection (`StationKind.RIVER` + `StationStatus.OPERATIONAL`)
- station-level assignments (`fetch_model_assignments`) vs group-level
  assignments (`fetch_groups_for_model`, `fetch_group_model_assignments`)
- `ModelAssignment.status` / `ModelAssignmentStatus`, `priority`
- `discover_models()` registry; `DeploymentConfig`
  (`forecast_combination_strategy`, `enable_forecast_alerts`)
- injected `clock` / `rng` / `config` / `qc_rules`
- NWP cycle availability (`NwpCycleSource`) — extraction/coverage detail lives
  in the FI map

**Core implementation touchpoints:**

- flow body / phase sequence: `run_forecast_cycle_flow` (setup → Phase A NWP
  fetch → Phase B stations → Phase B2 groups → alert-eligibility partition →
  Phase C alerting → result assembly)
- STATION dispatch: `run_all_station_forecasts` (executor) with
  `run_station_forecast` (PRIMARY selector) over `_run_single_model`
- GROUP dispatch: `discover_group_runs` / `run_group_forecast`, dedup via
  `group_produced_pairs`
- combination (STATION / Phase B only — GROUP dispatch never combines):
  `build_combined_forecasts`, `combine_ensembles_pooled`, `combine_ensembles_bma`
  — `CONSENSUS` is unimplemented and BMA is not operationally wired (the flow
  passes no weights)
- fan-out: Phase A `_fetch_nwp_task.submit` + Step 1.6
  `_fetch_obs_timestamps_task.submit` (the only concurrency in the flow)
- drift guard: `_check_fallback_priority_drift`
- health: `_forecast_cycle_health` → `ForecastCycleResult`

**Downstream consumers to inspect when behavior changes:**

- forecast persistence (`store_forecast`) and model-state persistence
  (`store_state`) — inline per-record inside the Phase B / B2 loops (write-side
  contracts: see the **Persistence / API write path** map)
- alerting (`check_station_alerts`), gated on the
  `AlertEligibility.SKILL_FORECAST` partition
- `ForecastCycleResult` readers and cycle observability logs
- API / dashboard readers if dispatch or combination changes which forecasts
  are emitted
- tests / fixtures asserting phase order, assignment resolution, or
  combination output shape

**Contracts that must not change silently:**

- STATION assignment resolution does **not** filter on `ModelAssignmentStatus`,
  while GROUP resolution filters ACTIVE at both discovery and selection. Because
  the STATION superset (`build_superset_requirements`) is built from the
  *unfiltered* list, an INACTIVE station assignment still feeds both dispatch
  and input assembly — the two paths have asymmetric status semantics.
- STATION dispatch is **not** a short-circuiting fallback chain:
  `run_all_station_forecasts` executes EVERY priority-sorted assignment each
  cycle (no early exit). `run_station_forecast` (PRIMARY, the config default) is
  a selector that persists only the highest-priority succeeded result;
  lower-priority models still run and cost compute every cycle. GROUP dispatch
  runs each discovered ACTIVE `(group, model)` assignment — a group may carry
  several (schema key `(group_id, model_id)`), so it is not "one model per group".
- Store/state failure handling is **not uniform** — it differs by call
  (`store_forecast` vs `store_state`) and by path. STATION `store_forecast`
  degrades (appends to `errors`); STATION `store_state` logs only. GROUP
  re-raises `StoreError` (direct, plus connection-fatal errors promoted via
  `_raise_store_error_if_connection_fatal`), aborting the whole cycle; GROUP
  `store_state` non-`StoreError`s log only. Do not assume one store call's
  failure semantics match another's — diff the specific branch before changing it.
- Phase A NWP fetch has two opposite-consequence failure modes:
  `NoCycleAvailableError` (`nwp_unavailable`) degrades to runoff-only for the
  cycle, whereas any other Phase A failure (`_fetch_nwp_task` → `None`) aborts
  the WHOLE cycle with `stations_attempted=0`, before Phase B/B2/C run.
- Phase C alerting has a single outer guard around the whole `check_station_alerts`
  call, which itself loops stations internally. A mid-loop exception **stops the
  remaining stations' alert processing** and leaves `alerts_checked=False`, but
  alerts already written for earlier stations are **not rolled back** (it is not
  all-or-nothing); the exception is caught, so it does not abort the cycle.
- `stations_failed` counts **STATION-loop (Phase B) failures only**;
  `ForecastCycleResult.health` folds in those plus the `alert_suppressed` /
  `nwp_grid_stale` / `fallback_priority_drift` flags. **GROUP-loop (Phase B2)
  non-fatal failures never affect `stations_failed` or `health`** — a monitor
  that assumes `health` covers GROUP failures will be wrong.
- STATION and GROUP results must land in the shared accumulators so the
  alert-eligibility partition and Phase C treat both dispatch paths identically.
- repo-specific Task Exit Gate still applies before PR approval

**Suggested verification:**

- forecast-cycle test covering phase order and STATION → GROUP → alert
  sequencing
- assignment-resolution test: priority sort + all-models execution + primary
  selection
- STATION vs GROUP status-filter regression (INACTIVE-assignment behavior on
  each path)
- store-failure regression proving STATION degrades and GROUP `StoreError`
  aborts (plus the NWP unavailable-vs-failed split)
- combination-mode test per reachable `ModelCombinationStrategy` branch
- `_check_fallback_priority_drift` coverage when changing priority semantics
- full Task Exit Gate for implementation PRs

**Context packet reminder:**

When this map applies, the context packet should name:

- which touch trigger applies
- which upstream inputs (assignment source, config flags) were inspected
- which downstream consumers (persistence, alerting, health) are affected or
  explicitly unaffected
- which contracts (status-filter asymmetry, fallback breadth, store-failure
  asymmetry, NWP failure split, alert guard scope, health scope) are at risk
- which focused tests will prove the change

### Touchpoint map: Persistence / API write path

Use this map when a task touches the store-write layer — how domain objects are persisted or mutated: the `Pg*Store` write methods, transaction / commit scoping, optimistic locking / `ConflictError`, idempotency / `ON CONFLICT`, the JSONB↔domain (de)serialization boundary, PostGIS geometry (de)serialization, `ensure_utc` at the write edge, `StoreError` classification, or the one API mutation endpoint. For *what triggers* a write during a forecast run — cycle phases, where `store_forecast` / `store_state` attach — use the **Forecast cycle / assignment selection** map; for output normalization *before* persistence, use the **ForecastInterface / model execution** map.

**The API is read-mostly.** Every route is `GET` except one write endpoint (`POST /api/v1/alerts/{alert_id}/acknowledge` → `PgAlertStore.acknowledge_alert`). The real write path is **Prefect flows via stores** (ingest, QC, forecast cycle, group forecast, training/skill flows). Route write-behavior questions there, not to the API.

Before planning or implementation, inspect the relevant touchpoints below and include them in the task context packet.

**Common touch triggers:**

- a `Pg*Store` write method (`store_*`, `upsert_*`, `update_*`, `transition_*`, `mark_*`, `append_*`, `delete_*`, `register_model`, `archive`)
- transaction / commit scoping or connection lifecycle (`get_connection_rw`, `make_pg_stores`, `setup_production_stores`)
- optimistic locking / version columns / `ConflictError`
- idempotency: `ON CONFLICT` clauses, natural-key unique constraints, dedup on re-run
- JSONB column read/write ((de)serialization of `QcFlag`, id arrays, `band_geometries`)
- PostGIS `geometry` column read/write (`from_shape` / `to_shape`, geoalchemy2 — distinct from the JSONB `band_geometries` on the same `basins` row)
- `ensure_utc` / `UtcDatetime` normalization at the write edge
- `StoreError` / exception classification / SQLAlchemy exception surfacing
- a new store Protocol or a new `Pg*` implementation
- the API acknowledge endpoint or any newly-added API mutation
- schema/DDL changes in `metadata.py` that a write path depends on
- tests exercising store writes, upsert semantics, or the acknowledge route

**Upstream inputs to inspect:**

- who constructs the domain object being written (parse-at-boundary is expected to have already run — most stores do **not** re-validate; `store_raw_observations` is a limited exception that does)
- the injected `sa.Connection` and its transaction mode (API vs flow path — see contracts)
- for cycle-driven writes, the caller in the **Forecast cycle** map (Phase B/B2 inline persistence)
- the relevant table's constraints / indexes in `metadata.py` (unique keys, partial-index predicates, version columns)

**Core implementation touchpoints:**

- store Protocols (`protocols/stores.py`) and their one-to-one `Pg*` implementations under `store/`; every SQL store takes `sa.Connection` by constructor injection and manages no transaction of its own
- connection factories: `get_connection_rw`, `make_pg_stores`, `setup_production_stores`
- version-gated mutation: `PgForecastStore.transition_status`
- upsert / idempotent writers (`store_observations` / `store_raw_observations`, `store_weather_forecasts`, `store_forcing`, `PgAlertStore.upsert_alert`, `store_baselines`, station/group upserts, `register_model`)
- plain-insert / append-only writers (`store_forecast`, `store_hindcast`, `store_state`, `store_config`, `append_health_record`, `store_basin`)
- filesystem-plus-DB writers with separate failure domains: `PgModelArtifactStore.store_artifact`, `ZarrNwpGridStore.archive`
- JSONB (de)serialization helpers (`_serialize_flags` / `_deserialize_flags` and the per-store id-array builders)
- PostGIS geometry (de)serialization for `basins.geometry` (`from_shape` / `to_shape`)
- read-side UTC normalization (`utc_from_row` / `utc_or_none` in `store/_helpers.py`)
- the single API write route (`api_alerts` acknowledge handler) and its error mapping (`errors.py`)

**Downstream consumers to inspect when behavior changes:**

- Prefect flow callers that assume a write is atomic, idempotent, or fail-loud (forecast cycle, ingest, hindcast, training)
- flow-side readers of already-written rows (`compute_skills`, `services/onboarding`) that consume `hindcast_store` / `observation_store` output — check these, not just write-atomicity callers, when a JSONB shape or table schema changes
- API / dashboard readers if a written schema or JSONB shape changes
- the acknowledge route if `AlertStore` write semantics or `Alert` status states change
- retry / re-run logic in callers that catches on `SapphireError` (raw SQLAlchemy exceptions leak past the store — see contracts)
- tests / fixtures asserting upsert-vs-duplicate behavior, version conflict, or serialized JSONB shape

**Contracts that must not change silently:**

- **Transaction scope differs by caller and is not symmetric.** API writes run inside `engine.begin()` (one commit/rollback per request); flows run on an AUTOCOMMIT connection, so **each statement commits on its own** and multi-statement writes are **not atomic as a unit** — `store_forecast` (header + values), `store_hindcast`, `store_group` (group + members), and `store_artifact` (filesystem then DB row) can partial-write on a crash. Diff `_db.py` before assuming a change relies on atomicity.
- **Optimistic locking exists only on `forecasts.version`** (`transition_status`, the sole `ConflictError` caller). `transition_artifact_status` and other status flips have **no CAS guard** — do not assume a `transition_*` name implies conflict detection; diff the specific method.
- **`store_forecast` is a plain insert against a table carrying a partial unique index** (`uq_forecasts_station_model_issued_param`), with no `ON CONFLICT` and no store-boundary exception translation — a duplicate-cycle re-run raises an **unwrapped SQLAlchemy `IntegrityError`**, not a domain error. Confirm this is intended before assuming a naive retry-on-`SapphireError` caller covers it.
- **Idempotency is uneven.** Some writers upsert on real natural-key constraints; others (`store_hindcast`, `store_state`) have **no natural-key dedup** and silently duplicate rows on re-run. Verify the target table's constraint in `metadata.py` before assuming a re-run is safe.
- **No Pg SQL store wraps SQLAlchemy exceptions** — raw `sqlalchemy.exc.*` propagates out of the store layer. `StoreError` is raised by `ZarrNwpGridStore` and by the **caller / service layer** (e.g. group-forecast, hindcast), **not** by the Pg stores, and there is no transient-vs-fatal classification inside them. Any such classification lives in the caller (see the store-failure asymmetry in the **Forecast cycle** map), not here.
- **`ensure_utc` is applied on read, never re-asserted on write.** Correctness depends on `UtcDatetime` being normalized upstream (parse-at-boundary); there is no defense-in-depth at the write edge. Flag any write path that could receive a non-boundary-constructed datetime.
- **JSONB (de)serialization is hand-rolled and unguarded on read** (`_deserialize_flags` assumes fixed keys). Changing a JSONB shape is a silent cross-version compatibility hazard for existing rows.
- **The API acknowledge endpoint has no auth and is not atomic across its two connections** (RO existence/status check, then a separate RW `PgAlertStore` write) — the RESOLVED-guard→update sequence has a narrow race. `acknowledged_by` is a caller-supplied UUID that is **format-validated by the route** (400 on non-UUID) but checked against **no authenticated principal** — any syntactically valid UUID is accepted as the acknowledger.
- Declared Protocols `ForeignForecastStore`, `RatingCurveStore`, `ForecastAdjustmentStore` have **no `Pg*` implementation** — confirm deferred-vs-missing before depending on them.
- repo-specific Task Exit Gate still applies before PR approval

**Suggested verification:**

- store unit test for the changed write method: happy-path insert + the re-run case (upsert-dedup vs. duplicate vs. raised driver exception — whichever the table actually guarantees)
- optimistic-lock regression on `transition_status` (concurrent version mismatch → `ConflictError`) when touching version semantics
- round-trip test for any changed JSONB shape (serialize → deserialize → domain equality), including a legacy/malformed-row read if shape changed
- atomicity-intent test or explicit note for any new multi-statement flow-path write
- acknowledge-route test (400 / 404 / 409 branches) if touching that endpoint or `AlertStore` write behavior
- forecast-cycle integration test if the write is cycle-driven (cross-reference the **Forecast cycle** map's store-failure regressions)
- full Task Exit Gate for implementation PRs

**Context packet reminder:**

When this map applies, the context packet should name:

- which touch trigger applies, and whether the write is API-path or flow-path (transaction mode)
- which upstream constructor is trusted to have parsed/normalized the domain object
- which downstream consumers (flow callers, flow-side readers, API/dashboard readers, retry logic) are affected or explicitly unaffected
- which contracts (transaction asymmetry, version-guard scope, idempotency guarantee, exception surfacing, UTC-on-write, JSONB shape) are at risk
- which focused tests will prove the change
