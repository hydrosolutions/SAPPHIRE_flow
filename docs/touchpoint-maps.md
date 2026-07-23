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
invariant. Symbol names only — no line numbers, and no file paths *except* where the
path itself is the routing target (Dockerfile, `docker-compose*.yml`, launchd plists,
standards docs), as in the infra map. Verify a map against the code (an independent
code-grounded pass, e.g. `codex exec -s read-only`) whenever it is added or touched.

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
- **Prefect / Docker / deployment** — deployment registration, work-pool topology,
  Docker build / compose / Caddy topology, entrypoint, DB migration sequencing,
  the VERSION deploy convention, the Mac-mini launchd host.
- **Training / hindcast / skill** — the offline model lifecycle: training-data
  assembly, model training + artifact creation / registration / promotion, hindcast
  generation, skill computation, retraining / recomputation.
- **Alerting / alert-state** — ensemble/observation threshold checking, the
  danger-level model, the Alert lifecycle (raise / acknowledge / resolve), dedup /
  auto-resolve semantics, and the (unimplemented) notification boundary.

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
- weather-source binding lookup goes through the role-scoped `StationStore` accessors
  (Plan 115a) — `fetch_forecast_binding` (exactly one FORECAST binding, else
  `ConfigurationError`) and `fetch_reanalysis_bindings` (0..n REANALYSIS bindings, no
  `status` filter); `fetch_weather_sources` (all bindings, unfiltered by role) is
  display-only, not for routing
- MeteoSwiss REANALYSIS bindings (Plan 115b2) are written by TWO paths that must
  stay in agreement: `bind_meteoswiss_reanalysis_fleet` (one-shot, existing fleet —
  `scripts/backfill_meteoswiss_history.py`) and station onboarding's Step 4c
  (`services/onboarding.py`, new stations). Eligibility for the MeteoSwiss
  binding is `eligible_meteoswiss_configs` (§3D — valid basin polygon only); a
  binding write with no matching backfill rows leaves a station forcing-less (the
  bug class Plan 115b2 exists to end) — see the onboarding Step 8 hold-out gate.
  Step 4b (`services/onboarding.py`) still stores CAMELS-CH `historical_forcing`
  rows (validation reference + audit trail, Plan 115b3) but — since Plan 115b5
  retired the `camels-ch`/POINT weather binding (migration `0033`) — MUST NOT
  write a `camels-ch` `station_weather_sources` row; only the non-weather
  `icon_ch2_eps`/BASIN_AVERAGE forecast binding is written alongside it
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
- plain-insert / append-only writers (`store_forecast`, `store_hindcast`, `store_state`, `store_config`, `append_health_record`)
- atomic two-table CTE writer: `PgBasinStore.store_basin` (v1 — Plan 120 Task 0A) writes the `basins` projection row AND its paired `version=1` `basin_versions` row in ONE data-modifying CTE, so the pair is atomic even on an AUTOCOMMIT connection — NOT a plain single-table insert; touch this when changing basin write/version semantics
- correction (upsert) writer: `PgBasinStore.update_basin_from_package` (Plan 120 Task 2C, fixer round) — the SEPARATE path for a NEW `package_id` over an EXISTING `(network, code)`; stamps the prior current `basin_versions` row's `superseded_at` BEFORE appending the new `version+1` row (order load-bearing for `uq_basin_versions_one_current_per_basin`), then refreshes the `basins` projection — as of the fixer round this triple runs as ONE chained-CTE statement (like `store_basin`'s new-basin insert), NOT three sequential `execute()` calls, so it is atomic even on an AUTOCOMMIT connection
- package-level write orchestration: `store/basin_importer.py::import_basin_package` (Plan 120 Task 2A/2C, the Task 2B package-driven population) — the canonical write pipeline (package provenance row first, then per-basin new-insert-via-`store_basin` or correction-via-`update_basin_from_package`, then the accepted station's `stations.basin_id` binding, then the §5a `basin_average` replace via `RecapGatewayPolygonStore.store_binding` LAST); package-level idempotency (`_package_import_decision`) is a no-op on identical checksums and raises `BasinPackageRejectedError` on a reused `package_id` with different checksums. **Transaction contract (fixer round, blocker):** `import_basin_package` refuses to run — before writing anything — unless `conn` is genuinely inside a non-AUTOCOMMIT, already-open transaction (`_require_real_transaction`); a production AUTOCOMMIT connection (`flows/_db.py::setup_production_stores`) does NOT become safe just by calling `conn.begin()` on it (verified empirically — statements still commit independently). A future caller (Task 3A) must acquire a connection via `engine.connect()` + `conn.begin()`, or `engine.begin()`, never the shared production AUTOCOMMIT connection.
- station operational-identity binding: `PgStationStore.assign_basin` + `store/basin_importer.py::_assign_station_basin` (fixer round, major) — every accepted basin import/correction binds the matched station's `stations.basin_id` to the imported basin; a station already bound to a DIFFERENT basin raises `BasinPackageRejectedError` rather than silently remapping. Touch this when changing station↔basin identity semantics — `services/training_data.py::assemble_station_training_data` and `store/model_artifact_lineage.py::record_artifact_basin_lineage` both resolve a basin exclusively via `stations.basin_id`, never via the package.
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

### Touchpoint map: Prefect / Docker / deployment

Use this map when a task touches the **infra/ops** layer that *runs* the flows (not the flow logic): Prefect deployment registration / scheduling / work-pools / concurrency, the Docker build, the `docker-compose` topology + overlays, the Caddy edge, the entrypoint, DB migrations, the `VERSION` / `.env` deploy convention, or the Mac-mini launchd host. For *what a flow does* once it runs, use the **Forecast cycle / assignment selection** and **Persistence / API write path** maps. Authoritative detail lives in `docs/standards/orchestration.md`, `docs/standards/cicd.md`, `docs/standards/security.md` — this map routes into them, it does not restate them. **Aspirational-vs-real is this layer's core hazard:** several standards-doc items are not implemented (and a few implemented flows are undocumented) — flagged below; verify before depending on one. File paths are named directly here because they *are* the routing targets.

Before planning or implementation, inspect the relevant touchpoints below and include them in the task context packet.

**Common touch triggers:**

- Prefect deployment registration, schedules, work-pools, concurrency limits
- Dockerfile change (builder/runtime stage, base-image pin, non-root user, apt deps) — CI CVE-gate + wheel-guard live in `.github/workflows/ci.yml`
- `docker-compose.yml` service / volume / network / secret / capability / `mem_limit` change, or any overlay file
- public routing / domain / TLS / security headers — `caddy` service + `Caddyfile`
- container entrypoint (`docker/entrypoint.sh`) or DB init (`docker/init-db.sh`)
- Alembic migration sequencing at deploy (the one-shot `init` service)
- the `VERSION` / `.env` convention or the build-then-`up -d` flow
- the NWP on/off toggle — `SAPPHIRE_REQUIRE_NWP` (compose-overlay env) **and** `[adapters.weather_forecast].enabled` in the `SAPPHIRE_CONFIG_OVERLAY` TOML (e.g. `config/overlays/mac-mini.toml`) — two distinct layers
- Mac-mini host startup / watchdog (`scripts/launchd/*`, `scripts/bootstrap-mac-mini.sh`)
- a new flow that must be registered / scheduled / pool-routed

**Upstream inputs to inspect:**

- `docs/standards/orchestration.md` (pools, scheduling, concurrency — its v0-vs-v1 caveats decide what is real), `docs/standards/cicd.md` (compose, volumes, migrations, tagging, upgrade/rollback, per-pool limits), `docs/standards/security.md` (non-root, capabilities, secrets)
- `docs/deployment/mac-mini-staging.md` — the live-host runbook
- `.env` — the `VERSION` operators pin (minted per CLAUDE.md § Version Bumping)

**Core implementation touchpoints:**

- **Deployment registration**: `register_deployments` (`src/sapphire_flow/cli/`) — a hand-rolled registrar (**no `prefect.yaml`**; uses `afrom_source().adeploy()`) that registers flows + creates pools + sets schedules/concurrency, run idempotently as the compose `init` service. It, not the standards tables, is the source of truth for what is deployed.
- **Work pools**: `default` and `ingest` only. The `ops`/`training`/`hindcast` split in `orchestration.md` is **aspirational**; conversely `ingest_weather_history_flow` is implemented + scheduled but **absent from the orchestration tables** — drift runs both ways.
- **Schedules / concurrency**: cron + env overrides + `concurrency_limit` in `register_deployments`; the only implemented named-resource slot is `model_training:{model_id}`. The `db_bulk_write` / `observation_write` slots, `retries=`, and `ThreadPoolTaskRunner(max_workers=)` in `orchestration.md` are **aspirational** — Prefect 3 defaults apply.
- **Docker build**: two-stage `Dockerfile` (builder + slim non-root runtime; rationale in `cicd.md`). Net-new facts: **`git` is required in the builder** for the git-pinned `forecastinterface`; the actual base image is **`python:3.14.6-slim`** while `cicd.md` / `security.md` (and even the Dockerfile's own comments) are **stale**.
- **Entrypoint**: `docker/entrypoint.sh` drops to non-root via `gosu` (rationale in `security.md`) and splices `DB_PASSWORD` from `/run/secrets/db_password` into the DB URLs. `docker/init-db.sh` creates the separate `prefect` DB.
- **Compose topology**: `docker-compose.yml` — services (`postgres`, `prefect-server`, `prefect-worker`, `prefect-worker-ingest`, `api`, `caddy`, one-shot `init`), named volumes, `backend`/`frontend` nets, `cap_drop:[ALL]`, file-based `db_password` secret, and `image: sapphire-flow:${VERSION:?…}` on every built service. **All built services are `read_only: true`** — a new write path needs an explicit `tmpfs:` / volume or the container fails at start.
- **Caddy edge**: `caddy` + `Caddyfile` — 80/443, `SAPPHIRE_DOMAIN`-gated TLS, CSP + security headers (no HSTS). The Prefect UI is **not** proxied (SSH-tunnel only); new public routes go here.
- **Overlays**: `docker-compose.dev.yml`, `docker-compose.staging.yml`, `docker-compose.macmini.yml` — **not auto-merged**; the exact `-f` set is chosen per invocation.
- **DB migrations**: `alembic/versions/` + `alembic.ini`, run as `alembic upgrade head` in the `init` service before registration.
- **Host startup (Mac mini)**: `scripts/launchd/*.plist` → `start-sapphire.sh` (the sole `docker compose … up -d` per reboot), `install-launchd.sh`, `bootstrap-mac-mini.sh`; the watchdog surface = `src/sapphire_flow/ops/watchdog.py` + `scripts/launchd/watchdog.sh` + an operator-created host secret `secrets/slack_webhook_url` + a manually-installed `newsyslog` log-rotation conf. The `cicd.md` systemd unit is an **illustration, not shipped**.

**Downstream consumers to inspect when behavior changes:**

- the host-restart entry points (`start-sapphire.sh`, `bootstrap-mac-mini.sh`) if you add/rename any overlay or `-f` flag — they must stay in lockstep
- the `init` service if a flow / pool / schedule / slot is added (must be registered *and* have a worker on its pool)
- `.github/workflows/ci.yml` if the FI git-pin or build deps change
- the affected standard / runbook doc (docs are an explicit consumer — a code change must update affected docs)
- runtime NWP behavior in the forecast-cycle flow (it reads the toggle this layer sets — see the **Forecast cycle** map)

**Contracts that must not change silently:**

- **Every host-restart path must bring up the identical overlay set.** `start-sapphire.sh` and `bootstrap-mac-mini.sh` must use the same `-f` set — an overlay in one but not the other silently diverges on the next reboot (the Plan-100 "restart dropped the NWP overlay → NWP silently off → feed dark while flows stayed green" incident). Grep both whenever an overlay changes.
- **No registry: `docker compose up -d` without `--build` reuses the existing `sapphire-flow:${VERSION}` image** — a code change deployed without `--build` is a **no-op that looks successful**. See `cicd.md` for the publish-gap + rollback procedure.
- **`VERSION`-unset behavior diverges**: `docker-compose.yml`'s `${VERSION:?…}` hard-fails, while `bootstrap-mac-mini.sh` defaults to `latest`. Change one, check the other.
- **Non-root by contract** — root exists only during `entrypoint.sh` (drops via `gosu`); `prefect-server` is the single documented root exception. Don't add a `user:` override or widen `cap_add` outside `security.md`.
- **`mem_limit` is a tuned invariant**: `prefect-worker`'s `8g` bounds the NWP-tmpfs SIGKILL blast radius (Plan 086); `prefect-worker-ingest`'s `512m` must keep tmpfs headroom (the Plan-098 dead-feed mode). Re-check the tmpfs sizing before changing either.
- **The builder needs `git`** for the git-pinned `forecastinterface` (with the Plan-079 CI wheel-guard) — a temporary arrangement to delete once FI ships as a wheel (Plan 080).
- repo-specific Task Exit Gate still applies before PR approval

**Suggested verification:**

- `register_deployments` idempotent-re-registration test when adding/altering a deployment / schedule / pool / concurrency slot
- local `docker compose build` + `up -d` on the changed service: comes up non-root, health passes, any new write path has a `tmpfs` / volume, and (for a code change) the image was actually rebuilt
- migration dry-run through `init` (`alembic upgrade head`) with workers quiesced, if schema-adjacent
- overlay-parity grep of both restart scripts, and a `VERSION`-unset check on both, when touching the deploy convention
- doc-sync: update the affected standard / runbook in the same change, and correct any stale claim you touch
- full Task Exit Gate for implementation PRs

**Context packet reminder:**

When this map applies, name: which touch trigger (Prefect-layer, image/compose-layer, or host/deploy-layer); which standards doc(s) + sibling map(s) were consulted and which items are implemented vs aspirational; which downstream consumers (restart scripts, `init`, CI, affected docs) are impacted; which contracts (overlay-parity, stale-image, `VERSION`-unset, non-root, `mem_limit`, builder-`git`) are at risk; which build / migration / registration checks will prove the change.

### Touchpoint map: Training / hindcast / skill

Use this map when a task touches the **offline model lifecycle** — training-data assembly, model training + artifact creation / registration / promotion, hindcast generation, skill computation, or retraining / recomputation. For the model boundary (`train` / `serialize_artifact` / `predict`, `ModelDataRequirements`) and for `_assemble_hindcast_inputs` + `resample_to_time_step`, use the **ForecastInterface / model execution** map — this map does not re-derive them. For the *write semantics* of `store_artifact` / `store_hindcast` / `register_model`, use the **Persistence / API write path** map. Verification-metric definitions are normative in `docs/standards/wmo.md` — cite it, do not restate it. **Aspirational-vs-real is a core hazard here** (several lifecycle automations are manual-trigger-only or DRAFT) — flagged below; verify before depending on one.

**Common touch triggers:**

- training-data assembly (`assemble_station_training_data` / `assemble_group_training_data`)
- training / serialization (`train_station_model` / `train_group_model`, `model.train` / `serialize_artifact`, `ModelParams` passthrough)
- artifact creation / integrity / promotion (`store_and_promote_artifact`, `promote_artifact`, SHA-256 verify, `ArtifactIntegrityError`)
- STATION vs GROUP artifact scope + assignment-priority ordering
- hindcast generation (`run_hindcast_flow`, `run_station_hindcast` / `run_group_hindcast`)
- skill computation (`compute_skills_task`, `compute_skill_for_station`, `compute_combined_skills_flow`)
- onboarding skill gate (`evaluate_skill_gate`) vs retrain (no gate)
- `model_training:{model_id}` concurrency, `clock` / `rng` injection
- retraining / recomputation triggers, staleness (`SkillStore.mark_stale`)
- tests exercising any lifecycle flow

**Upstream inputs to inspect:**

- scope resolution: `determine_training_scope` (retrain) / `determine_onboarding_scope` (onboarding) → `TrainingUnit` per station (STATION) or per group (GROUP); `ArtifactScope`
- observation / forcing sources feeding assembly (same sources as operational input — see the FI map)
- `DeploymentConfig.model_priorities` (operator-set assignment priority)
- persisted hindcast rows + observations that skill computation reads back
- injected `clock` / `rng` at each flow signature

**Core implementation touchpoints:**

- entry flows: `train_models_flow` (retrain/refresh, sequential per-unit loop) and `onboard_model_flow` (first-time onboarding: `adapt_if_fi` → register → per-unit compat → smoke → train → hindcast → skill-gate → promote → assignment). A *separate* Flow-5 flow, `onboard_stations_flow` / `onboard_from_camelsch`, also runs its own hindcast + skill wiring — it is not the only hindcast/skill producer
- `onboard_stations_flow` (Plan 115b2) ALSO builds a `reanalysis_adapter` — but ONLY on the production DB-auto-setup path (`basin_store is None` at flow entry), never when a caller injects its own stores (tests/replay) — this is what makes the §2C promotion hold-out gate live for the real deployed flow; a test-injected-stores caller gets the binding write (§2B) but not the gate
- train/serialize service: `train_station_model` / `train_group_model`
- artifact store + promotion: `store_and_promote_artifact` (retrain), store-as-TRAINING then `promote_artifact` on passed gate (onboarding) — write semantics in the Persistence map
- `register_models` / `build_registry_entry` → `register_model` (model-class catalog row, distinct from artifacts)
- hindcast services (`run_station_hindcast` / `run_group_hindcast`) and the legacy `_to_legacy_model_inputs` GROUP shim
- skill service: `compute_skill_for_station` (strata by lead-time / season / flow-regime; `SkillScore` + `SkillDiagram`), combined/BMA skill in `combined_skill`
- skill gate: `evaluate_skill_gate` / `_evaluate_skill_gate_task` — an **automated threshold** compare against `config.skill_gate_thresholds` (no human step); onboarding-only

**Downstream consumers to inspect when behavior changes:**

- forecast cycle: an ACTIVE artifact + assignment is what the operational cycle loads (see the **Forecast cycle** map)
- skill computation consumes **persisted hindcast rows** — a hindcast schema / dedup change propagates here, not just to write-atomicity callers
- API / dashboard readers of skill scores, diagrams, artifact status
- `SkillStore.mark_stale` — **unwired today** (defined on the store, zero production callers; only tests exercise it). Its data-recovery / rating-curve consumers are unimplemented v1 designs — a design gap, not code to trace
- tests / fixtures asserting artifact status transitions, skill metrics, or scope

**Contracts that must not change silently:**

- **STATION trains one artifact per station; GROUP trains ONE artifact shared across the whole group** (`assemble_group_training_data` concatenates all group stations, tagged by `station_id`). Do not assume a per-station artifact for GROUP.
- **Assignment priority is config-driven for ordinary models but code-enforced for fallbacks.** Ordering among non-fallback models is a pure `model_priorities` convention an operator sets (the shipped default actually runs NWP/weather models *before* linear, not the reverse). The two fallbacks (`FALLBACK_MODEL_IDS`) DO have a code floor: `_assert_assignment_priority_invariant` raises `ConfigurationError` if their priority drops below `FALLBACK_PRIORITY_THRESHOLD` at assignment creation. Don't conflate the two.
- **Train/serve/hindcast are NOT preprocessing-parity.** `resample_to_time_step` is shared training↔operational (owned by the FI map); hindcast's independent assembly (`_assemble_hindcast_inputs`, uses neither) is also owned there. This map only flags the fallout: a skill/hindcast change must not assume the hindcast leg matches train/serve preprocessing.
- **Skill depends on hindcast rows already being persisted** — `compute_skills` is fan-out over `(station_id, parameter)` that reads stored hindcasts + observations. Because `store_hindcast` has no natural-key dedup (Persistence map), a re-run is additive, not idempotent — skill callers must key off `hindcast_run_id`.
- **Three lifecycle flows re-verify SHA-256 after the store already did** (store raises `ArtifactIntegrityError` on read — Persistence map): `onboard_model`, `train_models`, and `run_hindcast` each re-hash and raise a plain `ValueError` instead — an exception-type inconsistency; do not standardize one without the rest.
- **The training call itself is a scoped `except Exception` carve-out (Plan 130), not the flow's general exception policy.** `train_models_flow`'s `_train_model_task` (T.3) and `onboard_model_flow`'s `_train_onboarding_model_task` (M.3) each wrap ONLY that call — any raise (including a model's bare `TypeError`/`ValueError` for an anticipated missing-input condition, e.g. `nwp_regression`'s reanalysis-tail gap) is recorded as a failed unit (`TrainingResult.error` / `FAILED_TRAINING`) and the run continues. The SHA-256 re-verify immediately after (previous bullet) and every other phase (hindcast, skill, assignment, smoke test) are UNGUARDED here and still abort the run on raise — do not widen the try/except beyond the training call without re-checking `docs/conventions.md`'s carve-out note.
- **Retrain promotes without a skill gate; onboarding gates first.** `store_and_promote_artifact` moves a retrained artifact straight to ACTIVE, whereas onboarding stores as TRAINING and promotes only on `evaluate_skill_gate(...).passed`. A bad retrain has no skill floor — confirm this asymmetry is intended.
- **`ModelParams` is always `{}` at both call sites** — hyperparameter passthrough is aspirational; do not assume tuned params flow into `train`.
- **`clock` / `rng` are injectable on the core lifecycle flows** (train / onboard-model / hindcast) for determinism, but they **default to live `datetime.now(UTC)` / `random.Random()`**, and *station* onboarding is an exception (no `rng` param; hard-codes `Random(42)` + a `datetime.now` skill callback). Don't assume determinism is enforced everywhere.
- **Concurrency gap**: the `model_training:{model_id}` slot is acquired **only in onboarding**. Deployment-level `concurrency_limit=1` serializes repeated runs of the *same* deployment (Prefect/Docker map), so two retrains queue — but a retrain and a concurrent onboarding for the same `model_id` (two deployments, independent scopes) are NOT mutually exclusive.
- **Lifecycle automation is largely manual-trigger.** `run-hindcast` runs as a subflow and `compute-skills` as a `compute_skills_task.map()` task fan-out, from `train_models_flow` / `onboard_model_flow`; `compute-combined-skills` and `train_models_flow` are cron-less registered deployments with **no automated caller** (trigger-only). **Automated skill-decay-triggered retraining does not exist** (deferred). The hindcast window cap (Plan 094) is **DRAFT** — the wide 1980–2030 default lives in *station onboarding* (`onboard_from_camelsch`), not `onboard_model_flow` (which already bounds to `now.year - 2`); apply the fix to the right module.
- repo-specific Task Exit Gate still applies before PR approval

**Suggested verification:**

- training test per scope: STATION one-artifact-per-station and GROUP one-shared-artifact (concatenation tagged by `station_id`)
- artifact integrity regression: corrupt bytes → `ArtifactIntegrityError` on read; fallback below `FALLBACK_PRIORITY_THRESHOLD` → `ConfigurationError`
- promotion regression: onboarding gates on skill (TRAINING→ACTIVE only on pass) vs retrain promotes unconditionally
- hindcast → skill round-trip proving skill reads persisted rows, plus a re-run-duplication check keyed on `hindcast_run_id`
- skill-metric test against `wmo.md` definitions
- determinism test injecting a seeded `rng` + fake `clock` on a core flow
- full Task Exit Gate for implementation PRs

**Context packet reminder:**

When this map applies, the context packet should name: which touch trigger and lifecycle stage apply; which upstream inputs (scope resolution, sources, `model_priorities`, persisted hindcasts) were inspected; which downstream consumers (forecast cycle, skill-from-hindcast, API readers) are affected or explicitly not; which contracts are at risk and which are aspirational vs real; and which focused tests will prove the change.

### Touchpoint map: Alerting / alert-state

Use this map when a task touches alert **evaluation, state, or delivery** — ensemble/observation threshold checking, the danger-level model, the `Alert` lifecycle (`RAISED` → `ACKNOWLEDGED` → `RESOLVED`), dedup/resolve semantics, or the notification boundary. For **where** alert evaluation attaches to a forecast run (Phase C, the `AlertEligibility` partition, the single all-or-nothing guard), use the **Forecast cycle / assignment selection** map. For `PgAlertStore.upsert_alert` / `acknowledge_alert` write semantics and the `POST /alerts/{id}/acknowledge` route (auth + RESOLVED-guard race), use the **Persistence / API write path** map. Danger-level / severity definitions are normative in `docs/standards/wmo.md` (WMO-1150 impact-based warnings) — cite it, do not restate. **Aspirational-vs-real is a core hazard here** — much of the notification and state surface is enum/type-only; flagged below.

**Common touch triggers:**

- ensemble threshold checking (`check_station_alerts`, `alert_checker`, `_compute_exceedance` in `alert_strategy`)
- observation threshold checking (`check_observation_alerts`, `observation_alert_checker`)
- danger-level config (`DangerLevelDefinition`, `DeploymentConfig.get_danger_level_definitions`)
- multi-model alert combination (`ModelCombinationStrategy` enum + the `ModelAlertStrategy` Protocol — `PrimaryModelStrategy` / `PooledEnsembleStrategy`)
- ensemble-adequacy / representation gates (`_ensemble_size_adequate`, MEMBERS vs QUANTILES)
- the `Alert` lifecycle / `AlertStatus` / `AlertSource`
- notification delivery (`NotificationChannel`, `NotificationAdapter`, `Alert.notified_at`)
- tests exercising alert raise / resolve / acknowledge

**Upstream inputs to inspect:**

- the alert-eligibility partition that gates which ensembles reach the checker (owned by the **Forecast cycle** map — models declared `CURRENT_OBS_PROXY` / `NO_EVENT_INFORMATION` never raise)
- forecast path: `config.enable_forecast_alerts`, `config.threshold_check_mode`, `config.alert_model_strategy` (the alert combination-strategy knob — **not** `config.forecast_combination_strategy`, which is the unrelated forecast-cycle output-combination field owned by the **Forecast cycle** map), ensemble-size floors
- observation path: `config.enable_observation_alerts` (its on/off gate), plus the latest `QC_PASSED` observation within lookback
- `DangerLevelDefinition`s (name, `trigger_probability`, `direction`) from deployment config

**Core implementation touchpoints:**

- forecast path: `check_station_alerts` → per-danger-level exceedance vs `trigger_probability`; `_compute_exceedance` reduces across lead times
- observation path: `check_observation_alerts` — a **separate, fully-wired** point-threshold checker (latest value vs threshold, no probability, no direction), invoked from the observation-ingest flow
- combination: `ModelCombinationStrategy` selection in `alert_checker` + `alert_strategy`
- state writes: `PgAlertStore.upsert_alert` (write semantics owned by the **Persistence** map); `resolve_alert` is a trivial in-place status flip to `RESOLVED`
- acknowledge: `POST /alerts/{id}/acknowledge` → `acknowledge_alert` (owned by the **Persistence** map)
- delivery boundary: `NotificationChannel` / `NotificationAdapter` Protocol (**no concrete implementation exists**)

**Downstream consumers to inspect when behavior changes:**

- the acknowledge route + any `AlertStatus` reader if the state set or transitions change (**Persistence** map)
- API / dashboard alert readers if `Alert` shape, `alert_level` string domain, or `AlertSource` changes
- pipeline monitoring — `DATA_UNAVAILABLE` and `AlertSource.PIPELINE` belong to Flow 4, **not** this subsystem (see contracts)
- tests / fixtures asserting raise/resolve/dedup or acknowledge branches

**Contracts that must not change silently:**

- **The threshold statistic is reduced by MAX across lead times** ("alert if any lead time exceeds") — not mean/single-quantile. Changing the reduction silently shifts trigger sensitivity across the whole subsystem. See `_compute_exceedance`'s docstring for the MEMBERS/QUANTILES computation.
- **Dedup is by active rows only: a `RESOLVED` row never blocks a fresh raise for the same station/level/source** (resolution is final per row). The active-row unique index is owned by the **Persistence** map.
- **Danger levels are independent, concurrently-active rows — there is no supersede/auto-resolve between them.** One station can simultaneously hold `RAISED` at two levels; each clears only when its own probability drops. `DangerLevelDefinition.display_order` is display ordering, **not** precedence.
- **`Alert.first_detected_at` is NOT preserved across re-raises** — `upsert_alert`'s ON CONFLICT resets it to the new trigger time every cycle. Do not build duration-based logic on the assumption it is stable.
- **Auto-resolve is completeness-gated and never fires on missing data.** A level resolves only when no longer exceeded **and** every configured parameter was evaluated this cycle; on missing sensors/models active alerts **persist silently** (deferred, Plan 039). There is **no hysteresis**: `resolve_probability`, `min_trigger_duration`, `min_resolve_duration` on `DangerLevelDefinition` (the `*_hours` names are the deployment-config boundary equivalents) are validated but **never read** — aspirational.
- **`BELOW`-direction is aspirational in the forecast path only.** `check_station_alerts` evaluates only `ThresholdDirection.ABOVE`; the observation path has no direction concept (`StationThreshold` has no `direction` field, always compares `>=`). A non-`raw` `threshold_check_mode` is **rejected — the check skips and logs `alert.check_mode_rejected`** (it does not fall back to raw). `BMA` / `CONSENSUS` are not implemented: **multi-model input degrades to POOLED / PRIMARY with an `alert.strategy_degraded` warning**, while single-model input resolves straight to PRIMARY. Only PRIMARY and POOLED are real.
- **Alert delivery is not implemented — "webhook-only" is convention, not enforcement.** `NotificationChannel` lists `EMAIL`/`SMS`/`WEBHOOK` and `NotificationAdapter` is a Protocol, but **no concrete adapter exists** and `Alert.notified_at` is hard-coded `None`. Nothing sends, retries, or enforces webhook-exclusivity. (The Slack poster in `ops.watchdog` is pipeline-health, **not** flood-alert delivery.)
- **The flood-alert state model excludes `DATA_UNAVAILABLE`** (only `RAISED`/`ACKNOWLEDGED`/`RESOLVED`). `AlertSource.PIPELINE` exists but no `PIPELINE`-sourced `Alert` is produced — station-dark detection writes a `PipelineHealthRecord`. Pipeline alerting is Flow-4 deferred (Plan 039).
- **`alert_level` is a free-form string keyed to deployment `DangerLevelDefinition`s** — not a fixed enum, not yet WMO tri-color-locked (a v1 item per `wmo.md`).
- **Acknowledge-route atomicity (the RESOLVED-guard TOCTOU race) is a Persistence-map contract** — do not re-derive or duplicate the fix here.
- repo-specific Task Exit Gate still applies before PR approval

**Suggested verification:**

- exceedance-reduction test proving MAX-across-lead-time on both MEMBERS and QUANTILES paths
- combination-strategy test per reachable branch (PRIMARY, POOLED, and BMA/CONSENSUS → documented fallback)
- resolve regression: exceeded→cleared resolves only when all parameters evaluated; missing-data leaves the alert active (Plan 039)
- dedup regression: re-raise merges the active row; a `RESOLVED` row does not block a fresh raise
- observation-path test (point threshold, latest `QC_PASSED` value)
- full Task Exit Gate for implementation PRs

**Context packet reminder:**

When this map applies, the context packet should name: which touch trigger applies (evaluation, state, or delivery); which upstream inputs (eligibility partition, config flags, danger-level defs) were inspected; which downstream consumers (acknowledge route, API/dashboard readers, pipeline monitoring) are affected or explicitly not; which contracts are at risk and which are aspirational vs real; and which focused tests will prove the change.
