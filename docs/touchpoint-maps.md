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
  ⚠️ **Adding a NEW NWP/forcing source? Read `docs/plans/187-forcing-canonicalisation-seam.md`
  (DRAFT) first.** A 2026-08-18 review found there is **no shared canonicalisation layer**: variable
  renaming, unit conversion and precipitation **de-accumulation** are adapter-private if-chains, and
  `types/forcing_schema.py` declares the canonical contract with **zero consumers**. A
  rate-vs-accumulation mismatch therefore produces plausible **wrong** forcing rather than an error.
  ⚠️ **Plan-number collision, RESOLVED 2026-08-18**: the canonicalisation-seam DRAFT was
  renumbered to **187**; `183-era5-land-swiss-forcing.md` (COMPLETE, merged as PR #184) keeps 183 —
  do not conflate them. `Era5LandReanalysisAdapter` (Plan 183 era5-land) is exactly the "second adapter"
  F1 warns about: it follows the SAME pre-existing adapter-private mapping-table pattern
  `meteoswiss_open_data_reanalysis.py` uses (not the not-yet-built seam), so it is consistent with
  today's convention but does not reduce F1's risk — worth the owner's attention before a THIRD
  source (e.g. IMERG/M-A5b) is added.
  Also: `exact_extract_grid_extractor.py:98` **overwrites** the CRS (`.rio.write_crs`) instead of
  reading it, so a non-4326 grid is silently mis-georeferenced; and `adapters/__init__.py` is empty —
  there is no registry to extend.
- **Forecast cycle / assignment selection** — cycle phase sequence, assignment
  resolution / priority / fallback, STATION vs GROUP dispatch, combination modes,
  alerting / persistence attach points.
- **Persistence / API write path** — store write methods, transaction / commit
  scoping, optimistic locking, idempotency, the JSONB / PostGIS boundary,
  StoreError classification, access-token auth enforcement (Plan 147 Slice C) +
  the removed (501) API mutation.
- **Prefect / Docker / deployment** — deployment registration, work-pool topology,
  Docker build / compose / Caddy topology, entrypoint, DB migration sequencing,
  the VERSION deploy convention, the Mac-mini launchd host.
- **Training / hindcast / skill** — the offline model lifecycle: training-data
  assembly, model training + artifact creation / registration / promotion, hindcast
  generation, skill computation, retraining / recomputation.
- **Alerting / alert-state** — ensemble/observation threshold checking, the
  danger-level model, the Alert lifecycle (raise / acknowledge / resolve), dedup /
  auto-resolve semantics, and the (unimplemented) notification boundary.
- **LINDAS / BAFU observation ingest** — the shared rate limiter, the two LINDAS
  caller adapters, per-station fetch-failure reporting, and the two LINDAS-caller
  cron schedules.

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
- Plan 156: `_project_requirements` rejects an `InputRequirement` declaring
  non-empty `future_known` in more than one `time_step` branch
  (`UnsupportedModelRequirementError`, deliberately not `ConfigurationError` —
  `discover_models()` skips just that entry point rather than darkening the
  whole registry); a past-only second branch stays constructible (Plan 151's
  shape). `supported_time_steps` is projected from the future-forced
  branch(es) only, never a past-only one
- Plan 151 T2 (D4, `adapters/forecast_interface.py::_assert_single_future_known_product`,
  called from `_project_requirements` per branch): a SEPARATE, per-branch
  construction-time guard — exactly ONE non-empty `future_known` product per
  branch, and exactly ONE `ensemble_mode` across that product's variables —
  same `UnsupportedModelRequirementError` / registry-skip treatment as the
  Plan 156 guard above. `past_known` products are NOT counted. Two new public
  per-time-step accessors on `ForecastInterfaceAdapter`,
  `future_feature_horizons(time_step)` / `future_feature_modes(time_step)`,
  expose the SELECTED branch's per-feature future horizons/modes without the
  `_project_requirements` max-collapse — consumed by
  `services/track_projection.py` (Plan 151 T3), with no other caller yet
  (T8 wires the per-track path in a later run)
- Plan 156 (follow-up): the accepted one-future-forced-plus-past-only shape is
  only constructible, not deliverable — `_station_inputs_from_frames`
  (predict/train time) raises `UnsupportedModelRequirementError` via
  `_assert_single_deliverable_dynamic_branch()` if the `InputRequirement`
  declares more than one `time_step` branch, rather than silently omitting
  the non-active branch's variables from `ModelInputs`. Every
  `next(iter(supported_time_steps))` call site (native + FI-adapted models)
  goes through `resolve_single_supported_time_step()`
  (`services/model_onboarding.py`), which requires exactly one element and
  raises `ConfigurationError` naming the ambiguous set otherwise
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
- hindcast reimplements assembly independently (`_assemble_hindcast_inputs`): never
  calls `assemble_*_operational_inputs`, derives from one model's
  `data_requirements` (not `build_superset_requirements`), and has its own
  issue-time conventions — diff it separately on any assembly / issue-time /
  requirements change. **Plan 228 (P1) fix**: it DOES now call
  `resample_to_time_step` (+ `validate_time_step_cadence`, a hard backstop) on
  `past_targets` before delivery — a model's `lookback_steps` count of rows must
  span its declared `time_step`, exactly as the operational path already
  delivered. `past_dynamic`/`future_dynamic` remain hindcast's own
  independent, unresampled assembly — this parity is scoped to `past_targets`
  only (see the D1 note below for why a blanket check would misfire on
  `past_dynamic`). **Plan 228 D4**: the fetch bounds feeding that resample are
  `aligned_lookback_bounds` (`services/training_data.py`) — ALIGNED to
  UTC-calendar `time_step` boundaries and EXTENDED so `lookback_steps` buckets
  are all COMPLETE, never a naive `issue_time - lookback_steps * time_step`
  window (which has a partial bucket at both ends whenever `issue_time` isn't
  itself a boundary, e.g. a 06:00 UTC cycle). A short-lived intermediate
  version instead phase-aligned buckets to `issue_time` itself (an `anchor`
  parameter on `resample_to_time_step`); D4 retracted that — it made hindcast
  consume rolling windows while training consumes UTC calendar days, and it
  aligned to `valid_time`, which Plan 226 exists to correct. `resample_to_
  time_step` has no `anchor` parameter any more; every path buckets onto the
  same UTC-calendar grid, full stop. Validation is additionally scoped to the
  MODEL's own declared `lookback_steps` (`declared_lookback_steps`), trimmed
  from the tail — not the runner's much larger fetch window (`lookback_steps`,
  default 720) — so a stale bucket far outside what a model's `.tail(n)`
  actually reads no longer suppresses the hindcast step (Plan 228 per-run
  scope, ALSO FIX #1)
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
- ERA5-Land REANALYSIS bindings (Plan 183, sloth-dynamic store) mirror the same
  bind-then-backfill shape via a SEPARATE one-shot entrypoint —
  `bind_era5_land_reanalysis_fleet` / `run_era5_land_backfill`
  (`services/era5_land_backfill.py`), driven by
  `scripts/backfill_era5_land_history.py` (fixer round, Plan 183: the T4
  `reanalysis_source="era5_land"` reader mode has no effect until this backfill
  has populated `historical_forcing` under `ForcingSource.ERA5_LAND` — selecting
  the mode alone does not write anything). Deliberately NOT wired into station
  onboarding's Step 4c — see `services/era5_land_backfill.py`'s module docstring.
- preprocessing: `resample_to_time_step` (precip SUM, temp/discharge MEAN,
  `swe`/`snow_depth` MEAN, `snowmelt` SUM — Plan 145 D2 canonical snow keys in
  `_V0_AGGREGATION_FALLBACK`), NWP hourly→daily + issue-time filter + horizon
  cap, lookback wide-pivot, `ensure_utc`
- the cycle assembles a **superset** (`build_superset_requirements`); each model
  slices it
- gates: `assess_future_coverage` (horizon truncation), `assess_input_quality`
  (degraded / partial input flags)
- Plan 145 D3.2d: an empty future-NWP read (`weather_forecast_store.fetch_weather_forecasts`
  returns nothing while `reqs.future_dynamic_features` is non-empty) does
  **NOT** return `None` from `assemble_station_operational_inputs` — it logs
  `operational_inputs.no_nwp` and continues with an empty `future_dynamic`
  frame. This is a GENERAL fix (not snow-specific): it also repairs the
  identical IFS-absent station-wide skip. The per-model `assess_future_coverage`
  gate (run downstream, per-model, on the MODEL's own `future_dynamic_features`
  — never this station-superset `reqs`) is what suppresses the NWP-fed model;
  a non-NWP fallback assigned to the same station still forecasts.

**Contracts that must not change silently:**

- FI model anticipated failures return `ModelFailure` (never raised from inside
  the model); the SAP3 adapter surfaces the pre-`predict` `max_nan` gate and total
  FI failure as `ModelOutputError` at the adapter/orchestration boundary
- **one unsupported model must never darken the registry** (Plan 156): a
  multi-FUTURE-FORCED-resolution `InputRequirement` raises
  `UnsupportedModelRequirementError`, NOT `ConfigurationError` —
  `discover_models()` re-raises `ConfigurationError` for every entry point,
  so a future FI-boundary check that reuses `ConfigurationError` for a
  per-model-rejectable condition reintroduces a registry-wide blackout
- data requirements must match what input assembly actually provides
- output shape and station / issue-time identity remain stable
- assignment priority and fallback semantics remain explicit
- **no imputation** — missing operational-input values are gated (`max_nan`), never
  imputed / interpolated / filled
- `resample_to_time_step` is shared across **operational, training, AND
  hindcast** `past_targets` assembly (Plan 228 P1 — hindcast used to build
  `past_targets` from raw, unresampled rows; fixed) — a change there hits all
  three. Hindcast's `past_dynamic`/`future_dynamic` assembly stays its own,
  unresampled, independent code path (see the preprocessing bullet above)
- `HybridForcingSource` `priority` order decides which source's forcing wins per
  `(station, valid_time, parameter)` — reordering it silently changes model inputs
- Plan 146 D4: `swe`/`snow_depth`/`snowmelt` are wired into `_PRIORITY_CHAINS`
  **and** `DEFAULT_PARAMETERS` in `hybrid_reanalysis_factories.py` — both are
  required (not just the priority-chain entry) because every real
  construction-time caller (training/hindcast/live + 6 more) relies on the
  `DEFAULT_PARAMETERS` fallback to decide which `PerSourceStoreReader`s get
  built at all. A stored `recap_snow_reanalysis` row (Plan 146's dedicated
  `ingest-recap-reanalysis` flow, not this map's ingest section) is otherwise
  never selected even though it exists in the store.
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
- snow-forecast (JSNOW) scoping/fetch/degradation (Plan 145 — Recap Gateway only,
  capability-gated via `SnowForecastSource`)
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
  `run_station_forecast` (PRIMARY selector) over `_run_single_model` — this is
  the LEGACY route: MeteoSwiss, group-CONTROL-overlap stations (D30-overlap),
  and any legacy (non-candidate-aware) adapter.
- **Per-track dispatch (Plan 151 T8b, D6/D12/D30) — a SEPARATE route, not a
  variant of the one above.** Gated by `per_track_eligible_stations(adapter,
  station_ids, group_store)`: a station is eligible iff its adapter satisfies
  `CandidateAwareForecastSource` (`isinstance` check, `@runtime_checkable`) AND
  it belongs to NO station group. Eligible stations are excluded from Phase A's
  legacy fetch (D7 — "Phase A must not persist partial candidates for migrated
  stations") and instead go through, once per resolved-cycle: track projection
  + dedup (`services/track_projection.py`: `project_forcing_requirement` +
  `resolve_tracks`) → per-track walk-back resolution
  (`services/track_resolution.py`: `resolve_candidate` + `commit_track`,
  wrapped by the flow's `_resolve_per_track_run_inputs`) → per-assignment
  assembly (`services/track_assembly.py::assemble_assignment_inputs`) → run
  via `run_all_station_forecasts_per_track` (the per-track counterpart of
  `run_all_station_forecasts` above — NOT a drop-in replacement; it takes a
  per-assignment `dict[ModelId, AssignmentRunInput]`, not one shared
  `inputs`/`nwp_cycle_reference_time`). The cross-cycle combination preflight
  (`resolve_combined_forcing_cycle`, D11) runs ONCE per per-track station,
  before EITHER the single-model or the pooled-combination persist branch — a
  mismatch across combinable results skips ALL writes for that station this
  cycle. A fatal typed error during resolution (`RecapAuthError` /
  `RecapConfigurationError` / `RecapPayloadIntegrityError` / `StoreError`)
  propagates out of the whole flow via `emit_freshness_on_fatal_exit` after
  emitting one forced-CRITICAL `FORECAST_FRESHNESS` record (Plan 116). See
  `docs/design/forecast-cycle-redesign.md` build-sequence item 3 and
  `docs/operations/recap-gateway-runbook.md` § Shared-forcing-track freshness.
  **Station-level exception containment (T8b fixer round, major):** the
  ENTIRE per-track branch body — from `run_all_station_forecasts_per_track`
  through both persist arms — is wrapped in the SAME `try/except Exception`
  pattern as the legacy branch below it (`forecast_cycle.station_forecast_failed`
  logged, appended to `errors`, `stations_failed` incremented, loop
  `continue`s). An unanticipated bug anywhere in that call chain (including
  the unguarded `build_combined_forecasts` call) degrades to one failed
  station, never crashes the whole flow run for every station in the cycle —
  this is DISTINCT from the fatal-typed-error re-raise above, which is a
  deliberate all-or-nothing contract for a narrow, *anticipated* taxonomy,
  not a substitute for unanticipated-bug containment.
- per-assignment warm-up state (Plan 148, READ side): `_run_single_model`
  loads THIS assignment's own state — `load_warm_up_state(model_state_store,
  station_id, assignment.model_id, clock)` — uniformly, after the
  model/coverage/artifact eligibility gates and before the reject-guards,
  building an assignment-keyed `ModelRunContext` (service-local,
  `services/operational_inputs.py`; shape in `types-and-protocols.md`).
  `assemble_station_operational_inputs` still loads warm-up for its single
  *representative* `model_id` only (unchanged behaviour, refactored through
  the same `load_warm_up_state` helper) — `OperationalInputMetadata` no
  longer carries `prior_state` (removed), only `warm_up_source`/age
  provenance (still read by the GROUP path). The WRITE side (state
  persistence) is still primary-only — see the store/state failure bullet
  below and `forecast-cycle-redesign.md` build sequence item 1.
- GROUP dispatch: `discover_group_runs` / `run_group_forecast`, dedup via
  `group_produced_pairs`
- combination (STATION / Phase B only — GROUP dispatch never combines):
  `build_combined_forecasts`, `combine_ensembles_pooled`, `combine_ensembles_bma`
  — `CONSENSUS` is unimplemented and BMA is not operationally wired (the flow
  passes no weights). Plan 222 (D2/D6): `combine_ensembles_pooled` pools a
  parameter only over the **intersection** of `valid_time`s across >=2
  `MEMBERS`-representation contributors (never the union — two model
  families can sit on different grids), and `build_combined_forecasts`
  additionally drops any resulting single-timestamp ensemble before
  persisting (the store would otherwise fabricate a 1-hour `time_step` for
  it). Plan 222 fixer round: completeness at each `valid_time` is counted
  BOTH ways — row count AND `n_unique(member_id)` must each equal the
  contributor's total member count, so a duplicate `(valid_time,
  member_id)` row cannot pass on `n_unique` alone — and the persistence
  floor also rejects a non-uniformly spaced intersection (an INTERIOR drop,
  not just a short one), since the store derives `native_step_seconds` from
  the first pair on readback regardless of what the rest of the grid looks
  like. `combine_ensembles_bma` is UNCHANGED (still unions; latent, not
  deployed).
- fan-out: Phase A `_fetch_nwp_task.submit` + Step 1.6
  `_fetch_obs_timestamps_task.submit` (the only concurrency in the flow)
- drift guard: `_check_fallback_priority_drift`
- health: `_forecast_cycle_health` → `ForecastCycleResult`
- freshness heartbeat (Plan 116): `_emit_forecast_freshness_record` — writes
  `PipelineCheckType.FORECAST_FRESHNESS` (CRITICAL when `forecasts_stored ==
  0` at every return point, including the no-operational-stations and
  hard-NWP-abort early returns; OK otherwise). A SEPARATE contract from
  `ForecastCycleHealth` — never keys off `health`/`_forecast_cycle_health`.
  Suppressed entirely (no record at all) when the flow's `cycle_time`
  parameter is explicitly set (backfill/replay), since
  `PipelineHealthStore.fetch_recent` orders by `checked_at` not
  `cycle_time` and would otherwise let a backfill masquerade as "latest".
  Probed by the watchdog's THIRD freshness block (see the **Infra / ops
  deployment** map). Fixer round (take 3, blocker): the group
  forecast-store loop's `store_forecast` call catches **any** `Exception`
  (not just `StoreError` — `PgForecastStore` never translates SQLAlchemy
  failures into `StoreError`, so a real `OperationalError`/`IntegrityError`
  would otherwise fall through a `StoreError`-only branch into a
  swallow-and-continue), emits this record (forced CRITICAL —
  `force_critical=True`, since a mid-cycle crash is never "OK" merely
  because an earlier forecast in the same cycle happened to store first)
  using `forecasts_stored` as it stands at that moment, and then
  RE-RAISES — a total (or partial, mid-cycle) forecast-store outage in a
  group-only deployment gets BOTH the watchdog record AND the fatal
  signal propagating out of the flow (`docs/conventions.md:256` — "log,
  raise to caller"). The re-raised exception must also clear the
  ENCLOSING per-group `try`/`except Exception` a few lines down (which
  otherwise isolates non-`StoreError` failures, e.g. a model bug in
  `run_group_forecast`, to a single group) — a local
  `fatal_group_store_failure` flag, set immediately before the inner
  `raise` and checked first in the outer handler, makes that one
  exception propagate while every other group-processing exception stays
  isolated. Unlike `_raise_store_error_if_connection_fatal`
  (`services/run_group_forecast.py`, used for `artifact_fetch`/
  `predict_batch`), this call site does NOT narrow to
  connection-fatal-only: any `store_forecast` failure means that specific
  forecast was NOT persisted, which is exactly the silent-failure mode
  this feature exists to detect. A prior (take 2) version caught only
  `StoreError`, and an earlier one still (take 1) used a broad
  `except Exception` contain-and-continue for everything — both let
  `forecasts_stored > 0` from an earlier success mask a later fatal
  failure with a false OK record and a "successful" flow return.
  **Plan 223** adds an OPTIONAL `reason` key to `detail` (present only on
  the NWP-abort path, never `"reason": null` on any other path) — see the
  `forecast_freshness` detail-shape entry in `architecture-context.md` for
  the full contract. Sourced from `_NwpFetchOutcome.fetch_failure_reason`
  (new field): the generic `except Exception` clause at the bottom of
  `_fetch_nwp_task`'s adapter-call try block is the ONLY boundary that
  still holds the raised exception, so it constructs a bounded, sanitised
  cause there (`_nwp_fetch_failure_reason` — never `str(exc)`, since
  MeteoSwiss download hrefs are presigned URLs carrying
  `AWSAccessKeyId`/`Signature`) and returns a non-`None` outcome carrying
  it instead of bare `None`. Every consumer that tested `nwp_outcome is
  None` for "fetch failed" now ALSO tests
  `nwp_outcome.fetch_failure_reason is not None` — the pruning gate
  (`:2637`, success-only pruning stays success-only), the fatal predicate
  (`:2653`, both conditions now abort identically), and
  `scripts/nepal_forcing_run.py`'s own fatal check (a CROSS-FILE consumer
  of `_fetch_nwp_task.fn` outside this flow). `BudgetExceededError`
  (`exceptions.py`) gained `kind`/`observed`/`limit` fields, set at both
  `meteoswiss_nwp.py` raise sites (byte cap, file-count cap), so the
  reason names WHICH cap tripped with real numbers
  (`"nwp_file_count_exceeded: 501 > 500"`) instead of a generic code.
- snow-forecast wiring (Plan 145): `_compute_required_snow` (pre-Phase-A,
  per-station required future-snow variables from ACTIVE assignments' resolved
  models' `future_dynamic_features` ∩ `SNOW_CANONICAL_PARAMETERS`) →
  `_fetch_nwp_task` capability-checks `isinstance(adapter, SnowForecastSource)`
  → `RecapGatewayForecastAdapter.fetch_snow_forecast` under the SAME resolved
  IFS cycle → stored via `weather_forecast_store.store_weather_forecasts` →
  broadcast by the existing `_broadcast_deterministic_features_to_members`
  (Plan 082 2H-snow) in `operational_inputs.py`. A `(hru, variable)` gap folds
  into `_NwpFetchOutcome.snow_unavailable` → `_forecast_cycle_health`'s
  `snow_unavailable` parameter (DEGRADED, never `nwp_unavailable`).
  `required_snow` is threaded all the way into `fetch_snow_forecast` (not just
  used to pick which stations reach the call) — the adapter scopes each HRU's
  Gateway calls to the UNION of variables its resolved stations actually need,
  so a swe-only station never triggers an hs/rof call. A required-snow station
  with NO matching forecast binding in the batch (config gap) also sets
  `snow_unavailable=True` rather than staying silently `HEALTHY`.
  `_reconcile_snow_coverage` (review fold-in, major) then compares the
  RESULT against `required_snow` per bound station — a required station the
  resolver silently skipped (while resolving others), or a required parameter
  that came back with zero rows on an otherwise-successful fetch (no
  exception, empty `unavailable`), both fold into `snow_unavailable=True` too.
  Neither `_require_some_resolved` (all-unmappable-only) nor
  `bool(snow_result.unavailable)` (raised-and-contained errors only) can see
  either gap on its own.

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
  `store_forecast` re-raises on **any** exception (fixer round, take 3 —
  see the freshness-heartbeat entry above), aborting the whole cycle;
  GROUP `store_state` still re-raises `StoreError` only and logs
  everything else (`_raise_store_error_if_connection_fatal` is a separate,
  narrower promotion used only inside `run_group_forecast.py` for
  `artifact_fetch`/`predict_batch` failures — it does not touch either
  `store_forecast` or `store_state`). Do not assume one store call's
  failure semantics match another's — diff the specific branch before
  changing it.
  **State WRITE stays primary-only** (PRIMARY mode persists only the selected
  result's `new_state`; combination mode persists only `mid ==
  primary_model_id`'s) — Plan 148 fixed the READ side only; a non-primary
  stateful assignment's state is never written back (deferred).
- Per-assignment warm-up-state READ failures and the stateful-ensemble
  reject-guards are **assignment-local, never a station-abort** (Plan 148).
  A `load_warm_up_state` store-read exception inside `_run_single_model`
  (event `run_station_forecast.warm_up_load_failed`) and both
  `reject_prior_state_for_fanout` (input-side) /
  `reject_stateful_ensemble_states` (output-side) raises (event
  `run_station_forecast.unsupported_stateful_ensemble`, catching
  `ModelOutputError` ONLY, never widened to span `predict`) are caught inside
  `_run_single_model` and recorded in `failed_models` — the same channel as
  `model_not_found`/`no_active_artifact`/`predict_failed`. A lower-priority
  assignment tripping either failure mode does **not** discard an
  already-succeeded higher-priority primary (this is a deliberate correction
  of the pre-148 behaviour, not a regression — see Plan 148 "State-load
  failure semantics"). The **assembler's** representative-model read is
  unchanged: it still aborts the whole station via
  `forecast_cycle.input_assembly_failed` if it raises.
- Phase A NWP fetch has two opposite-consequence failure modes:
  `NoCycleAvailableError` (`nwp_unavailable`) degrades to runoff-only for the
  cycle, whereas any other Phase A failure (`_fetch_nwp_task` → `None`) aborts
  the WHOLE cycle with `stations_attempted=0`, before Phase B/B2/C run.
- Snow (Plan 145) is a THIRD, snow-scoped failure mode, distinct from both of
  the above: an uncontained snow error sets `snow_unavailable=True` (folded into
  `health=DEGRADED`) and never the cycle-wide `nwp_unavailable` flag, and never
  aborts Phase A/B/B2/C. A per-`(hru, variable)` gap is contained INSIDE
  `fetch_snow_forecast` itself and never reaches `_fetch_nwp_task` as an
  exception at all. The `operational_inputs.assemble_station_operational_inputs`
  `return None` guard on an empty future-NWP read is RELAXED (Plan 145 D3.2d) —
  it now logs and continues with an empty `future_dynamic` frame for BOTH the
  snow-absent and the identical IFS-absent case, relying on the per-model
  `assess_future_coverage` gate (not this guard, and not a station-wide skip) to
  suppress only the NWP-fed model.
- Per-HRU IFS delivery gaps (Plan 154) are a FOURTH failure mode, distinct from
  all three above: `RecapGatewayForecastAdapter.fetch_forecasts` contains a
  station-scoped `RecapDataUnavailableError` (one HRU's control fetch missing)
  PER HRU — a discarded HRU contributes nothing, but every other HRU is still
  attempted, staged, and committed (all-or-nothing per HRU, never a subset —
  station-level partiality within an HRU is unrepresentable at this boundary
  until Plan 151's per-track path lands). The adapter still re-raises
  `RecapDataUnavailableError` when NO HRU committed a row (total loss,
  unchanged today-behaviour), so `nwp_unavailable`/runoff-only is untouched.
  When the adapter returns a non-empty PROPER SUBSET of the requested stations
  (`0 < returned < requested`), `_fetch_nwp_task` reconciles requested-vs-
  returned station ids and sets `_NwpFetchOutcome.nwp_delivery_partial=True`
  (folded into `health=DEGRADED`, never `nwp_unavailable`) plus a CRITICAL
  `PipelineCheckType.NWP_DELIVERY` `pipeline_health` record naming the missing
  stations (`recap.hru_unavailable_contained` at the adapter, `nwp.delivery_
  partial` at the flow — see `docs/standards/logging.md`). An EMPTY mapping
  (`returned == 0`) is excluded — that is still today's legitimate no-op-NWP
  success, not divergence. A committed HRU's complete-variable-set invariant is
  enforced INSIDE that same per-HRU staging step (fixer-round fold-in): if one
  required IFS variable's control fetch is a well-formed EMPTY response while
  another variable's is populated for the SAME HRU, `fetch_forecasts` raises an
  uncontained `AdapterError` (never `RecapDataUnavailableError`) rather than
  shipping partial-variable forcing — the same uncontained-malformed-response
  treatment as the pre-existing missing-polygon-column raise.
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
- store-failure regression proving STATION degrades and GROUP
  `store_forecast` aborts on any exception (`StoreError` or a raw
  SQLAlchemy failure alike) (plus the NWP unavailable-vs-failed split)
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

**The API is read-only as of v1.0 (Plan 147 Slice C, G4).** `POST /api/v1/alerts/{alert_id}/acknowledge` — the former sole write endpoint — now unconditionally returns `501` and reaches `PgAlertStore.acknowledge_alert` through no route. The real write path is **Prefect flows via stores** (ingest, QC, forecast cycle, group forecast, training/skill flows) plus the `access_tokens` CLI (`cli/access_tokens.py`, a write path of its own, gated by shell access not a bearer token). Route write-behavior questions to flows/CLI, not to the API.

**Every route except `GET /api/v1/health` now requires a bearer access token** (Plan 147 Slice C). `api/security.py::require_principal` (any valid token) / `require_admin` (role=admin) are FastAPI dependencies wired at `include_router(..., dependencies=[Depends(...)])` in `api/__init__.py` — router-level for the legacy HTML/`.json`/`admin`-only surface (`tables`, `dashboard`, `stations`, `forecasts`, `models`), per-route for `/health/detail`, and BOTH router-level AND a redundant per-route `Depends(require_principal)` parameter on the modern `/api/v1/stations|forecasts|alerts|forecast-lab` JSON API (the per-route parameter is load-bearing for station-scope filtering via `ensure_station_in_scope`, not merely defense-in-depth). A `consumer` token is filtered to its scope — resolved in one of two `scope_mode`s (Plan 215 D2.1/D2.2): `'stations'` (the default) reads the `access_token_stations` join; `'tenant'` derives the scope from `stations.tenant_id` at load time instead, unfiltered by network/kind — either way `out-of-scope → 404, empty scope → nothing`; an `admin` token is unscoped. `Principal.station_in_scope` and every call site are unchanged by which mode produced the scope. Touch `api/security.py` and re-run `tests/unit/api/test_security.py` + `tests/integration/api/test_access_token_auth.py` when changing role semantics, the pepper lifecycle, or which routers/routes are gated; touch `cli/access_tokens.py` + `store/access_token_store.py` and re-run `tests/unit/cli/test_access_tokens.py` + `tests/integration/cli/test_access_tokens_cli.py` + `tests/integration/store/test_access_token_store.py` when changing scope lifecycle (`grant`/`revoke-station`/`show`/`set-scope-mode`, Plan 215 T1/T2/T6).

**`GET /api/v1/forecast-lab/snapshot` (Plan 198) is read-only and outside this map's write-path scope, but shares its auth surface and its route-matrix lock.** It assembles a versioned cross-source document (BAFU observations + archived BAFU forecasts + SAPPHIRE forecasts) via `services/forecast_lab/snapshot.py::build_snapshot()` — the single producer both this route and `cli/export_forecast_lab.py` call (D1) — reading the quarantined BAFU forecast archive through a **read-only** volume mount on the `api` service (`docker-compose.yml`, Plan 111's quarantine widened but never breached: no write, no `ModelId` minted). Station eligibility (`network=bafu`, `kind=river`, `status=operational`) is resolved BEFORE scope on both the list-all and by-code paths (`services/forecast_lab/db_sources.py`), and the existence check runs independently of `principal.is_admin` — do not "simplify" that ordering (`api/security.py::Principal.station_in_scope` returns `True` for an admin before it even tests for `None`, so admin+None would silently pass a naive scope-first check). A poisoned `SQLAlchemyError` is deliberately NOT caught inside `build_snapshot()` — see the **Forecast Lab snapshot** spec (`docs/spec/forecast-lab-snapshot.md`) for the full partial-vs-500 contract. Update `tests/unit/api/test_security.py`'s route-auth matrix (`TestRouteAuthMatrixExhaustive`) when this route's path or classification changes. Plan 222's fixer round moved the T7 publication-cycle marker OFF the `FORECAST_FRESHNESS` heartbeat and removed `pipeline_health_store` from `ForecastLabStores` entirely (nothing in `db_sources.py`/`snapshot.py` ever read it): `build_snapshot()` resolves the current cycle via `ForecastStore.fetch_latest_uncombined_issued_at()` — `MAX(issued_at) WHERE combination_strategy IS NULL`, snapshot-wide, bounded by `data_cutoff_at` — never the heartbeat, whose append is best-effort and can silently fail to advance. This is resolved ONCE per call and reused to pin every station's `combined_forecast` fetch to that one publication cycle — see the spec's "Combined forecast" section for the read contract.

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
- tenant-scoped station/group writes (Plan 147 Slice A): `stations.tenant_id`/`station_groups.tenant_id` are canonical and `NOT NULL`; `station_group_members.tenant_id` is bound by TWO composite FKs (`(station_id, tenant_id) -> stations(id, tenant_id)`, `(group_id, tenant_id) -> station_groups(id, tenant_id)`) so `PgStationGroupStore.store_group`/`add_station_to_group` structurally reject a cross-tenant membership (`IntegrityError`, not a Python check) — touch this when changing group membership writers or the `tenants` table; `fetch_group_by_name` now takes a `tenant_id` (name is unique per tenant, not globally)
- plain-insert / append-only writers (`store_forecast`, `store_hindcast`, `store_state`, `store_config`, `append_health_record`)
- append-only audit writer: `PgAuditLogStore.append_entry` (Plan 147 Slice B) — ONLY an insert, no update/delete method on the class or the `AuditLogStore` Protocol; append-only is additionally enforced at the DB by a role-independent `BEFORE UPDATE OR DELETE` trigger (migration `0046`) that RAISEs for every role including the table owner, so it holds even before the scoped DB roles exist (Slice D). Deliberately takes NO `transaction_factory` and opens no transaction of its own — it executes on the SAME `sa.Connection` the caller passes in, so a caller sharing one externally-owned `conn.begin()` across a domain mutation store AND this store gets atomicity "for free" (a failed audit INSERT rolls back the paired domain mutation, since both share one SQLAlchemy transaction) — no repo-wide connection refactor. A REJECTED write instead persists its rejection event (attempted `event_type` + `detail.outcome="rejected"`) in a SEPARATE, independently-committed transaction after the domain rollback. Slice C wired the first call sites (`cli/access_tokens.py::create_token`/`revoke_token`/the `create-admin` bootstrap — token insert + `API_KEY_CREATED`/`API_KEY_REVOKED` audit row in ONE `engine.begin()` transaction, CLI-side, no `PgAuditLogStore` needed for `list_tokens` which is read-only); Slice E's onboard/promote/assign + rejection call sites are still unwired — touch this when adding a new audited mutation or changing the `audit_log` schema/enums (`types/enums.py::AuditEventType`/`AuditActorType`, `types/auth.py::AuditEntry`)
- atomic two-table CTE writer: `PgBasinStore.store_basin` (v1 — Plan 120 Task 0A) writes the `basins` projection row AND its paired `version=1` `basin_versions` row in ONE data-modifying CTE, so the pair is atomic even on an AUTOCOMMIT connection — NOT a plain single-table insert; touch this when changing basin write/version semantics
- correction (upsert) writer: `PgBasinStore.update_basin_from_package` (Plan 120 Task 2C, fixer round) — the SEPARATE path for a NEW `package_id` over an EXISTING `(network, code)`; stamps the prior current `basin_versions` row's `superseded_at` BEFORE appending the new `version+1` row (order load-bearing for `uq_basin_versions_one_current_per_basin`), then refreshes the `basins` projection — as of the fixer round this triple runs as ONE chained-CTE statement (like `store_basin`'s new-basin insert), NOT three sequential `execute()` calls, so it is atomic even on an AUTOCOMMIT connection
- additive attribute-only writer: `PgBasinStore.merge_namespaced_attributes` (Plan 155 T1b) — a THIRD basin-write path, deliberately separate from `store_basin`/`update_basin_from_package`: a JSONB `||` merge into `basins.attributes` only, no new `basin_versions` row, no `material_change`, no affected-artifact set. Structurally guarded to reject any key not carrying the hardcoded `"caravan:"` prefix (D15) — NOT a caller-suppliable parameter (round-2 fixer minor: an exposed `prefix` argument previously let `prefix=""` defeat the guard entirely) — so it cannot touch an existing non-namespaced attribute even by mistake; this is what makes skipping the supersede/flag machinery sound. **Reads the basin's current attributes via `SELECT ... FOR UPDATE` before comparing (fixer round 4 MAJOR fix)** — the prior plain `SELECT` was a check/update race: two concurrent imports could both observe "no existing key" and the second's write would silently win over the first's; the row lock forces the second caller to block until the first commits, then re-compare against the NOW-current row, so the conflict this method promises to raise is actually observed. Only closes the race for callers running inside a real transaction (`store/_helpers.py::require_real_transaction`) — meaningless on AUTOCOMMIT, where the lock releases the instant the `SELECT` completes. Orchestrated end-to-end by `store/caravan_import.py::import_caravan_attributes` (the flexible, internal, still-ungated building block — joins Caravan's `caravan_camels_ch_<code>` parquet rows onto `("bafu", code)` stations, network hardcoded rather than caller-suppliable, fixer round 4 minor fix; refuses to run at all — via the shared `store/_helpers.py::require_real_transaction` — unless `basin_store.connection` is inside a real, non-AUTOCOMMIT transaction; its `required_static_names` exit gate is scoped to the caller-supplied `expected_codes` MANIFEST, never the source parquet — the two must be supplied together or the call raises immediately, round-2 review BLOCKER fix). **The only PRODUCTION entrypoint is `store/caravan_import.py::run_operational_caravan_import`** (fixer round 4 BLOCKER fix) — `expected_codes` and `required_static_names` are mandatory, no-default, and additionally checked non-empty at runtime, closing the "both omitted (or both empty) silently writes with no validation" bypass the review found in `import_caravan_attributes`'s own lenient defaults; do not call `import_caravan_attributes` directly from a flow. Touch this when changing additive-vs-correction write semantics for basin attributes.
- package-level write orchestration: `store/basin_importer.py::import_basin_package` (Plan 120 Task 2A/2C, the Task 2B package-driven population) — the canonical write pipeline (package provenance row first — but only on the run that actually writes a basin, see below — then per-basin new-insert-via-`store_basin` or correction-via-`update_basin_from_package`, then the accepted station's `stations.basin_id` binding, then the §5a `basin_average` replace via `RecapGatewayPolygonStore.store_binding` LAST); idempotency is **basin-aware, not package-aware** (fixer round, blocker, 2026-07-23 — see `_basin_needs_import`): a basin is skipped only when its own current `basins.package_id` already equals this run's `package_id`, so a basin held on an earlier run over the identical package is still imported once it becomes accepted, even though `_resolve_package_provenance` (the renamed, now PURELY-immutability `_package_import_decision`) already finds a matching provenance row for this `package_id`+fingerprint. `_resolve_package_provenance` still raises `BasinPackageRejectedError` on a reused `package_id` with a differing fingerprint (including a manifest-only mutation). **Transaction contract (fixer round, blocker):** `import_basin_package` refuses to run — before writing anything — unless `conn` is genuinely inside a non-AUTOCOMMIT, already-open transaction (`store/_helpers.py::require_real_transaction` — extracted to a shared helper in Plan 155's round-2 fixer round so `store/caravan_import.py::import_caravan_attributes` enforces the identical guard); a production AUTOCOMMIT connection (`flows/_db.py::setup_production_stores`) does NOT become safe just by calling `conn.begin()` on it (verified empirically — statements still commit independently). The caller satisfying this contract is `services/basin_importer.py` (Task 3A, below) — it always acquires its own connection via `engine.begin()`, never the shared production AUTOCOMMIT connection.
- station operational-identity binding: `PgStationStore.assign_basin` + `store/basin_importer.py::_assign_station_basin` (fixer round, major) — every accepted basin import/correction binds the matched station's `stations.basin_id` to the imported basin; a station already bound to a DIFFERENT basin raises `BasinPackageRejectedError` rather than silently remapping. Touch this when changing station↔basin identity semantics — `services/training_data.py::assemble_station_training_data` and `store/model_artifact_lineage.py::record_artifact_basin_lineage` both resolve a basin exclusively via `stations.basin_id`, never via the package.
- top-level import orchestration + acceptance report: `services/basin_importer.py::import_loaded_basin_package` / `import_basin_package_from_directory` (Plan 120 Task 3A) — the ONLY caller of `store/basin_importer.py::import_basin_package` outside its own tests. `import_loaded_basin_package` runs the write pipeline inside a SAVEPOINT (`conn.begin_nested()`) so a caught `BasinPackageRejectedError` never leaves the caller's outer transaction in Postgres's aborted state; `import_basin_package_from_directory` (the `python -m sapphire_flow.cli.import_basin_package` entrypoint) opens that outer transaction via `engine.begin()`. Touch this when changing the accepted/onboarding_held/rejected report shape (`types/basin_package.py::BasinPackageImportReport`) or the CLI's station-resolution wiring (`PgStationStore.fetch_station_by_code`, network-scoped).
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
- **The API acknowledge endpoint is removed from the v1.0 surface (Plan 147 Slice C, G4) — `POST /alerts/{id}/acknowledge` now unconditionally returns 501**, closing the previously-noted no-auth/self-asserted-`acknowledged_by`/race hazard by removing the endpoint rather than re-authoring it. It returns with the v1.x session-token/Flow-3 dashboard stack. The underlying `PgAlertStore.acknowledge_alert` store method and its non-atomic two-connection RO-then-RW pattern are UNCHANGED and still exist for that future reactivation — re-verify atomicity when the endpoint is reinstated.
- Declared Protocols `ForeignForecastStore`, `RatingCurveStore`, `ForecastAdjustmentStore` have **no `Pg*` implementation** — confirm deferred-vs-missing before depending on them.
- **`merge_namespaced_attributes`'s additive-only contract is deliberate, not an oversight.** A caller reaching for the correction branch (`update_basin_from_package`) to add Caravan attributes would wholesale-replace `attributes`/geometry/area and flag every incumbent artifact as `material_change` — exactly what Plan 155 T1 exists to avoid (incumbent CAMELS-CH-trained artifacts must see unchanged inputs). Do not "simplify" the two write paths into one.
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
- **Work pools**: `default` and `ingest` only. The `ops`/`training`/`hindcast` split in `orchestration.md` is **aspirational**; conversely `ingest_weather_history_flow` and (Plan 146) `ingest_recap_reanalysis_flow` are implemented + scheduled but **absent from the main Flow-to-Prefect mapping table** — both are documented in their own `orchestration.md` § "Rolling-window ingest flows" subsection instead — drift runs both ways; verify which table you're reading before depending on it.
- **Schedules / concurrency**: cron + env overrides + `concurrency_limit` in `register_deployments`; the only implemented named-resource slot is `model_training:{model_id}`. The `db_bulk_write` / `observation_write` slots, `retries=`, and `ThreadPoolTaskRunner(max_workers=)` in `orchestration.md` are **aspirational** — Prefect 3 defaults apply.
- **Docker build**: two-stage `Dockerfile` (builder + slim non-root runtime; rationale in `cicd.md`). Net-new facts: **`git` is required in the builder** for the git-pinned `forecastinterface`; the actual base image is **`python:3.14.6-slim`** while `cicd.md` / `security.md` (and even the Dockerfile's own comments) are **stale**. **Plan 218**: the runtime stage also ships a CURATED set of operator scripts at `/app/scripts` (`import_caravan_attributes.py`, `onboard.py`, `backfill_meteoswiss_history.py`, `backfill_era5_land_history.py`, `validate_forcing_reference.py`) — **not** all of `scripts/`; adding a new operator script to that set is a deliberate Dockerfile edit, not automatic (`tests/unit/deploy/test_dockerfile_operator_scripts.py` locks the curated list).
- **Entrypoint**: `docker/entrypoint.sh` drops to non-root via `gosu` (rationale in `security.md`) and splices a password into the DB URLs. **Plan 147 Slice D**: the secret file it reads is now NAMED (`$DB_PASSWORD_SECRET`, default `/run/secrets/db_password`) — each service's compose entry points it at its OWN credential (owner / `sapphire_api` / `sapphire_worker`), so no app container can reconstruct another service's password. `docker/init-db.sh` still only creates the separate `prefect` DB (fresh-volume-only, `docker-entrypoint-initdb.d`) — it does NOT create the scoped app roles; that is `docker/bootstrap-roles.sh` (below), run by `init` on EVERY deploy, not just first boot.
- **DB role bootstrap** (Plan 147 Slice D): `docker/bootstrap-roles.sh` + `docker/bootstrap-roles.sql`, run by the `init` service AFTER `alembic upgrade head`, idempotently create/update the scoped `sapphire_api`/`sapphire_worker` roles + their per-table grants (`conventions.md` § Service users has the matrix; `cicd.md` § DB role bootstrap has the runbook). `sapphire_prefect` is UNCHANGED — `prefect-server` still connects with the owner credential.
- **Compose topology**: `docker-compose.yml` — services (`postgres`, `prefect-server`, `prefect-worker`, `prefect-worker-ingest`, `api`, `caddy`, one-shot `init`), named volumes, `backend`/`frontend` nets, `cap_drop:[ALL]`, and `image: sapphire-flow:${VERSION:?…}` on every built service. **Three file-based DB password secrets** (Plan 147 Slice D): `db_password` (owner — mounted only into `postgres`/`prefect-server`/`init`), `sapphire_api_db_password` (mounted into `api` + `init`), `sapphire_worker_db_password` (mounted into both workers + `init`) — `api`/the workers mount ONLY their own role's secret. **All built services are `read_only: true`** — a new write path needs an explicit `tmpfs:` / volume or the container fails at start.
- **Caddy edge**: `caddy` + `Caddyfile` — 80/443, `SAPPHIRE_DOMAIN`-gated TLS, CSP + security headers (no HSTS). The Prefect UI is **not** proxied (SSH-tunnel only); new public routes go here.
- **Overlays**: `docker-compose.dev.yml`, `docker-compose.staging.yml`, `docker-compose.macmini.yml` — **not auto-merged**; the exact `-f` set is chosen per invocation.
- **DB migrations**: `alembic/versions/` + `alembic.ini`, run as `alembic upgrade head` in the `init` service, as the DB OWNER role, before the role bootstrap and registration. `sapphire_api`/`sapphire_worker` have no DDL privilege — a migration run under a scoped role fails closed.
- **Host startup (Mac mini)**: `scripts/launchd/*.plist` → `start-sapphire.sh` (the sole `docker compose … up -d` per reboot), `install-launchd.sh`, `bootstrap-mac-mini.sh`; the watchdog surface = `src/sapphire_flow/ops/watchdog.py` + `scripts/launchd/watchdog.sh` + operator-created host secrets `secrets/slack_webhook_url` and (Plan 163) `secrets/deadman_url` + a manually-installed `newsyslog` log-rotation conf. The `cicd.md` systemd unit is an **illustration, not shipped**. Plan 163 adds an off-box dead-man's-switch heartbeat (Healthchecks.io) posted after every tick that completes and persists — feature-off when `secrets/deadman_url` is absent (dev/CI) — and hardens all four outbound HTTP call sites (health probe, BAFU-detail probe, Slack POST, dead-man POST) against `httpx.InvalidURL`/`OSError`/`UnicodeError`, none of which `httpx.HTTPError` alone covers; the guarded boundary also covers owned-`httpx.Client` cleanup (`finally`-block `close()`), not just the request. **Plan 116** adds a THIRD freshness block (`forecast_freshness_probe`, `consecutive_forecast_freshness_failures` hysteresis counter) probing `PipelineCheckType.FORECAST_FRESHNESS` — the forecast cycle (`flows/run_forecast_cycle.py`) emits that record itself (CRITICAL when a run persists zero forecasts, OK otherwise; a run invoked with an explicit `cycle_time`, i.e. backfill/replay, emits none; a group-path `StoreError` — total or mid-cycle-partial — emits a forced-CRITICAL record using `forecasts_stored` as it stands at that moment and then RE-RAISES, so the watchdog gets the record AND the fatal signal still propagates instead of the flow returning a false "successful" result). Deliberately a SEPARATE contract from `ForecastCycleHealth` — a DEGRADED-but-forecasts-stored cycle must not trip this alarm. Fixer round: uses a DEDICATED probe/result (`probe_forecast_freshness`/`ForecastFreshnessResult`), not `probe_bafu_freshness`/`BafuFreshnessResult` — it ages by the record's `cycle_time` (forecast production time), not `checked_at` (write time), so a delayed/long-running cycle can't refresh the heartbeat with a stale cycle. The HTTP-to-alert boundary itself is locked end-to-end via the real `probe_forecast_freshness` fed a `MockTransport` critical payload into `run_once` — not only via the hand-built `_forecast_freshness_critical_probe` fake. **Plan 223** adds an OPTIONAL `reason: str | None` field to `ForecastFreshnessResult`, parsed by `probe_forecast_freshness` from `detail.reason` when present (never invented when absent — a legacy record with no `reason` key parses to `None`), and `_format_forecast_freshness_critical_alert` appends `, cause: {reason}` to the Slack alert only when set — a healthy cycle's alert (there isn't one) and a legacy/reason-less CRITICAL alert both render byte-identical to before. **Plan 194** adds the backup-target DEVICE predicate (`backup_target_verified`/`default_backup_target_verifier` in `watchdog.py`, duplicated as a matching TRIO of shell functions — `_backup_target_device_id`, `_backup_mount_root_verified`, `backup_target_verified` — in both `bootstrap-mac-mini.sh` and `start-sapphire.sh` — not factored into a shared file; the plan's own exit gates shellcheck only those two scripts) at all three call sites: `bootstrap-mac-mini.sh` fails closed (interactive), `start-sapphire.sh` checks/warns-to-the-log/proceeds (D3; the marker file it originally wrote was dropped in PR #201 — nothing ever read it) — never trades a backup outage for a forecasting one), and the watchdog raises a FOURTH, DISTINCT freshness-independent condition (`consecutive_backup_device_unverified_ticks`/`backup_device_notification_pending`, evaluated BEFORE staleness) that alerts on TRANSITION only, never every tick — a permanently-diskless host would otherwise alert forever. The predicate compares `stat` device ids on the backup directory ITSELF (not its parent — fixer round 2: a parent-only check let a nested bind-mount, e.g. `pg_dumps/` itself bind-mounted back onto the boot disk, or a `pg_dumps` symlink resolving onto the data device, satisfy verification while still landing dumps on the wrong device; the Python predicate already checked `backup_dir` correctly — only the two shell copies had drifted to the parent) vs. the data path (`Path.home()` on the watchdog, `REPO_ROOT` in the shell scripts — never `/`) AND requires the backup path's parent be a real mount (`Path.is_mount()` / `mount` grep) — a plain directory Docker silently creates for a missing bind-mount host path satisfies neither. `_backup_mount_root_verified` (shell-only) is a NARROWER, separate helper that checks only the mount root, deliberately tolerating a not-yet-created backup directory — it exists solely to gate `bootstrap-mac-mini.sh`'s Step 6 `mkdir -p` of `pg_dumps/` on a freshly initialised volume (never unconditional — that would recreate the exact boot-disk-directory bug on an absent disk); it is never itself the verification predicate. Fixer round (Codex): `start-sapphire.sh`'s marker read/write is BEST-EFFORT under `set -e` (a directory collision or unwritable parent warns, never blocks `exec docker compose`); and the mac-mini-staging.md USB-disk-recovery runbook force-recreates `prefect-worker-backup` (the container the backup bind actually lives on, Plan 162 D2), not `prefect-worker`. Fixer round 2: the device-notification pending-retry/condition-reversal state machine (mirroring the staleness block's) is locked by `TestRunOnceBackupDeviceNotificationStateMachine`. **Plan 199** (salvaging two never-merged pieces of the abandoned Plan 158 branch) adds two more: T1 is a FIFTH, DISTINCT condition — free space on `config.disk_path` (default `/`), alerting below **5% of that volume's OWN total capacity** (`DISK_FREE_THRESHOLD_PCT`, D1 — NOT the source branch's absolute 20 GiB, which was 0.55% of the mac mini's 3.6 TB volume and would fire too late to matter), same transition-latched shape as the Plan 194 device check (`_disk_notification_kind`/`consecutive_disk_low_ticks`/`disk_notification_pending`, evaluated last, before `state.dump()`); `probe_disk_free`/`DiskSpaceResult` take `free`/`total` from ONE `shutil.disk_usage()` call so the ratio is never split across two instants. T2 adds `scripts/launchd/docker-endpoint.sh` — a SOURCED (not executed) contract defining `DOCKER_BIN`/`DOCKER_HOST`, consumed by all four launchd wrappers (`start-sapphire.sh`, `prune-docker.sh`, `run-recap-probe.sh`, `run-nepal-forcing.sh` — each resolves `DOCKER="${DOCKER_CMD:-${DOCKER_BIN}}"`, preserving the pre-existing `DOCKER_CMD` test-injection seam); both shellcheck gates (pre-commit + CI) now run `-x` so the `source` line doesn't trip SC1091, and CI's script list gained `prune-docker.sh`/`run-nepal-forcing.sh`/`docker-endpoint.sh`, previously omitted. Explicitly NOT salvaged: `install-launchd.sh`'s 382-line transactional expansion (unowned — Plan 164 deliberately rejected it) and `bootstrap-mac-mini.sh`'s branch-side fixes (deferred — Plan 194 already rewrote that script for a different concern). **Plan 195** adds a SIXTH condition, checking the launchd agents themselves rather than anything they produce: `probe_launchd_agents`/`parse_launchctl_list_output` read `launchctl list` (never `print`, whose output format Apple explicitly disclaims) for the installer-managed labels in `MONITORED_LAUNCHD_LABELS` (`install-launchd.sh`'s `PLISTS` minus the watchdog's own label, kept honest by a parity test), latched PER LABEL as the set of currently-failing labels (`failing_launchd_labels`/`launchd_notification_pending`) — FAILING (non-zero last exit status, including negative) and ABSENT (unloaded entirely) both count as failing, OK conflates never-run with succeeded deliberately. The probe being unreadable at all (timeout/missing executable/unparseable output, `LAUNCHD_PROBE_TIMEOUT_S = 5.0`) is a SEVENTH, separately-latched condition (`launchd_probe_unreadable_ticks`/`launchd_probe_notification_pending`) so "the monitor stopped monitoring" can never present as "no agents failing"; per-label state is held unchanged for the duration. Same injectable-probe/pending-retry/transition-latch shape as the backup-device block above (`launchd_probe` parameter on `run_once`, defaulting to the real `probe_launchd_agents`).

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
- **The backup-target device predicate (Plan 194) is not a config surface.** `bootstrap-mac-mini.sh` / `start-sapphire.sh` derive the data path from `REPO_ROOT` (never `/`); the watchdog derives it from `Path.home()` and the mount root from `backup_dir.parent` — none of these are CLI flags or config fields, deliberately (the watchdog is a launchd HOST process reading CLI args only; `SAPPHIRE_CONFIG_OVERLAY` never reaches it — see `watchdog.py`'s `main()`). Don't reintroduce a `backup_mount_root` field/flag; it would duplicate a pure function of an existing value. The old sentinel file (`.sapphire-backup-volume`) is a human label only — no code path treats its presence as proof.
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
- Caravan-declared static resolution (Plan 155 T2, G8; gating = D16): `services/caravan_statics.py` (`resolve_caravan_static_key` / `available_declared_static_keys` / `project_declared_static_attributes` / `declared_static_naming` / `verify_static_coverage` / `resolve_shared_static_frame`) — a Caravan-trained model (e.g. `cmal_pool_PT`) declares statics under Caravan's own names, which live `caravan:`-namespaced in `Basin.attributes` (D15, no bare-name fallback). **Whether a model gets this resolution AT ALL is gated by `declared_static_naming(model)` reading a `static_naming` class attribute (`types/enums.py::StaticNaming`, `NATIVE` default vs `CARAVAN` opt-in) — every call site below branches on it BEFORE calling `available_declared_static_keys`/`project_declared_static_attributes`, and must NOT union their result with the model's raw bare key set for a `CARAVAN` model** (post-implementation-review BLOCKER fix: the original wiring unioned unconditionally, which silently re-admitted a stale CAMELS-CH `area` whenever `caravan:area` was absent). Wired into EVERY compatibility-and-frame boundary that used to read `basin.attributes` directly: `flows/onboard_model.py::_validate_compatibility_task`, `services/model_onboarding.py::onboard_model`'s own compatibility step, `services/training_data.py::assemble_station_training_data`'s missing-static gate + static-frame build, `services/hindcast.py::_load_static_attributes` (now takes `static_naming` too), `services/operational_inputs.py::assemble_station_operational_inputs`. Needs no change to `adapters/forecast_interface.py::_static_inputs` — the projection always resolves into the model's own declared BARE column name. T2's collision guard (`project_declared_static_attributes`) checks BOTH an aliased name's raw-HydroATLAS-code key and its bare Caravan-name key when both are present in a package — a single-valued resolver cannot observe that case; `verify_static_coverage` implements T1's exit gate (non-null AND `math.isfinite`) as a reusable, testable function over a `{station_code: attributes}` manifest, resolving through `project_declared_static_attributes` itself so it is collision-aware.
- **`static_naming` must survive `ForecastInterfaceAdapter` (Plan 155 fixer round 2 BLOCKER)** — `adapters/forecast_interface.py::ForecastInterfaceAdapter` has no `__getattr__` passthrough, so `declared_static_naming(model)` at every call site above would silently see the ADAPTED instance's un-forwarded default (`NATIVE`) for a real FI model, never the RAW model's own declaration. `services/model_registry.py::_assert_model_classification_declared` — already the place `model_tier`/`alert_eligibility` are copied from raw to adapted at discovery time — copies `static_naming` identically. Check this function when adding any new FI-boundary passthrough field.
- **Shared static frames across co-assigned models must resolve PER model, not per representative (Plan 155 fixer round 2 MAJOR)** — `services/operational_inputs.py::assemble_station_operational_inputs` can be asked to assemble ONE static frame shared across every model in a station's fallback chain (`run_forecast_cycle.py`'s `superset_reqs` unions `static_features` across `assigned_models`, threaded through as `static_naming_models`). `resolve_shared_static_frame(attributes, models, ...)` scopes Caravan resolution to only the names a `CARAVAN`-declaring co-assigned model itself asks for, leaves `NATIVE`-only names untouched, and raises `ConfigurationError` if the SAME bare name is declared under differing regimes by two co-assigned models — never gate this on a single representative model (e.g. `first_model`) while resolving against the cross-model superset; that silently hands one model's declared name the other's derivation.
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
- **Caravan static resolution scopes to a model's OWN declared names, never a blanket projection.** `project_declared_static_attributes` only overrides a bare attribute for names the calling model's `data_requirements.static_features` actually declares — an unconditional projection of every `caravan:` key would silently shadow an INCUMBENT (non-Caravan) model's same-named bare attribute (e.g. its own `area`) even though that model never asked for Caravan's derivation. Any refactor that hoists this outside the per-model call sites must preserve that scoping.
- **Caravan resolution is additionally gated by the model's `StaticNaming` declaration (D16) — never call it, nor union its output with the raw bare key set, for a model that has not opted into `StaticNaming.CARAVAN`.** The default is `NATIVE`: an incumbent model with no declaration must see `basin.attributes` completely unprojected, exactly as before Plan 155. A refactor that drops this branch (e.g. "simplify" the five call sites back to always resolving) reintroduces the exact BLOCKER the post-implementation review found: a bare-fallback leak for the seven colliding CAMELS-CH/Caravan names (`area` is the dangerous one — it drives the `m³/s ↔ mm/day` conversion).
- **A PRESENT-but-malformed `static_naming` declaration raises, it does not silently default to `NATIVE` (Plan 155 fixer round 2).** `declared_static_naming` only defaults when the attribute is genuinely ABSENT; a present value that is not a `StaticNaming` member (including the plausible near-miss of the bare string `"caravan"`) raises `ConfigurationError`. Do not reintroduce an `isinstance(...) else NATIVE` fallback — that silently downgrades a broken declaration into the strictest-looking-safe default, which is the opposite of what a misconfiguration should do.
- **`merge_namespaced_attributes` (`store/basin_store.py`) refuses a changed value under an already-merged `caravan:`-namespaced key.** It fetches current attributes and raises `ConfigurationError` BEFORE issuing any write if an existing key's value differs from the incoming one; an identical-value replay is still a no-op success. The `prefix` parameter was removed (hardcoded to `"caravan:"`) — do not reintroduce a caller-suppliable prefix, which previously let `prefix=""` defeat the structural guard entirely.
- **`replace_namespaced_attributes` (`store/basin_store.py`, Plan 188 T3/D4) is the ONE recovery primitive `merge_namespaced_attributes` deliberately lacks** — a targeted correction for a genuinely-changed `caravan:` value (a re-delivered, corrected parquet), never `update_basin_from_package`'s wholesale replace-and-flag-artifacts branch. Same hardcoded `"caravan:"`-prefix guard and `SELECT ... FOR UPDATE` row lock as `merge_namespaced_attributes`, but it REPLACES instead of refusing on a differing value, so two concurrent callers serialise (B blocks until A commits, then applies its own replacement) rather than one of them raising — the property proven is ORDERING (B's write lands on A's already-committed row), not "not last-write-wins" (unachievable once replacement is permitted at all). Unlike `merge_namespaced_attributes`, it calls `require_real_transaction` ITSELF (there is no `run_operational_...` wrapper in front of a direct recovery-primitive caller to enforce it upstream) — do not drop that call if refactoring.
- **The operator CLI, `scripts/import_caravan_attributes.py` (Plan 188 T1), is the only production caller of `run_operational_caravan_import`.** It derives the T0a manifest LIVE (`derive_swiss_caravan_manifest` — network `"bafu"`, `StationKind.RIVER`, the `sapphire` tenant (`DEFAULT_TENANT_ID`), `"discharge"` in both `forecast_targets` and `measured_parameters`, minus the one owner-dropped `2446` Gampelen-Zihlbrücke) and cross-checks it by SET IDENTITY (not cardinality) against a pinned literal, `SWISS_CARAVAN_MANIFEST_CODES` — **populated with the reviewed 148-code T0a roster (fixer round, 2026-08-27)**, derived from `config.toml`'s 169 `[onboarding] basin_ids` minus Plan 155's 20 published lake codes and the one dropped `2446` canal; a module-load `assert len(...) == 148` fails fast on a typo'd literal. Required statics are read from the DISCOVERED `cmal_pool_pt` adapter (`resolve_required_static_names`, keyed by the `AQUACAST_CMAL_POOL_PT_MODEL_ID` registry constant, never a hand-typed literal) — a missing `aquacast` extra fails the preflight naming the model id, not a bare `KeyError`. `--dry-run` runs the full gate inside `with engine.begin():` and rolls back structurally (a private sentinel exception raised inside the `with` block), never a flag the code must remember to honour.
- **`available_declared_static_keys` and `project_declared_static_attributes` (`services/caravan_statics.py`) share ONE collision-aware resolver (`_resolve_declared_value`), by design (Plan 155 round-2 review MAJOR fix).** Before the fix, compatibility checked only the primary (raw-HydroATLAS-code) key while the frame accepted the secondary (bare-Caravan-name) key too, so a station could pass compatibility and then raise at frame build, or the reverse. Do not reintroduce a second, divergent lookup for either boundary — extend `_resolve_declared_value` instead, so both call sites (and `verify_static_coverage`, which routes through `project_declared_static_attributes`) can never disagree again. `available_declared_static_keys` also takes an optional `station_code` (fixer round 4 minor fix), forwarded from `resolve_available_static_keys_for_stations`'s per-station loop, so a compatibility-time collision names the real station instead of `<unknown>` — do not drop that kwarg when calling it.
- **`_resolve_declared_value`'s collision guard distinguishes key PRESENCE from value USABILITY (Plan 155 fixer round 4 MAJOR fix) — presence is `key in attributes`, checked BEFORE any null/finite filtering.** The prior implementation filtered out a null/NaN candidate before checking whether both keys existed, so a delivered-but-null primary value alongside a valid secondary value (or vice versa) silently resolved to the non-null one instead of raising — the exact silent pick T2's collision guard exists to forbid. `_values_agree` additionally requires BOTH operands to pass `_is_finite_numeric` (numeric, non-boolean, finite) before comparing equality — two equal strings, two equal booleans, or a `True == 1` bool/int pair no longer silently "agree". Do not reintroduce a presence check that filters on value usability first.
- **`import_caravan_attributes`'s `required_static_names` exit gate is scoped to the caller-supplied `expected_codes` MANIFEST, never the raw parquet (Plan 155 round-2 review BLOCKER fix).** A real Caravan delivery legitimately carries hundreds of out-of-scope codes beyond our configured stations; gating on the parquet made the exit-gate either always raise (real data) or validate nothing (synthetic single-station tests). `required_static_names` without `expected_codes` now raises `ConfigurationError` immediately, before any read or write — do not decouple the two parameters. The function also refuses to run at all unless `basin_store.connection` is inside a real, non-AUTOCOMMIT transaction (`store/_helpers.py::require_real_transaction`). **`import_caravan_attributes` itself stays deliberately lenient (both parameters remain optional there, for its own reporting-only tests) — the mandatory, non-empty-enforced production entrypoint is `run_operational_caravan_import` (fixer round 4 BLOCKER fix); do not call `import_caravan_attributes` directly from a flow, and do not add defaults to `run_operational_caravan_import`'s two gate parameters.**
- **Train/serve/hindcast `past_dynamic`/`future_dynamic` assembly is NOT preprocessing-parity** — hindcast's independent assembly (`_assemble_hindcast_inputs`, owned by the FI map) still never calls `assemble_*_operational_inputs`; this map only flags the fallout: a skill/hindcast change must not assume the hindcast leg's forcing matches train/serve preprocessing. **`past_targets` IS parity as of Plan 228 (P1 fix)**: `resample_to_time_step` is now shared by training, operational, AND hindcast `past_targets` assembly — before the fix, hindcast delivered a model's declared `lookback_steps` count of RAW (e.g. ~10-minute cadence) rows against a declared daily `time_step`, a 144x lookback shortfall. Hindcast's fetch bounds are additionally ALIGNED and EXTENDED (`aligned_lookback_bounds`, Plan 228 D4) so a non-midnight `issue_time` still gets `lookback_steps` full COMPLETE UTC-calendar buckets, not a partial one at the boundary nearest `issue_time` — a short-lived intermediate version instead phase-aligned buckets to `issue_time`'s own phase (an `anchor` parameter on `resample_to_time_step`), which D4 retracted (rolling windows vs. training's calendar days, and alignment to `valid_time`, which Plan 226 exists to correct — no path aligns to a caller's own timestamp any more). `validate_time_step_cadence` (`services/training_data.py`) is a hard backstop shared by the three **operational/hindcast-facing** `past_targets` assemblers (`hindcast.py`, `operational_inputs.py`, `track_assembly.py`) — **NOT** `training_data.py`'s own `assemble_station_training_data`, which resamples `past_targets` but deliberately does not additionally validate cadence: a real multi-year historical training window legitimately contains gaps a hard fail would need to reject rather than train around. It now checks **every** adjacent gap, not the median — a median-of-N check let an isolated missing bucket (e.g. days 1,2,3,5,6 — one 2-day gap among three 1-day gaps) pass silently, exactly the shape a model reading `.tail(n)`/`values[-n:]` positionally would misread as consecutive steps. **Every `skill_scores` row predating Plan 228 (2026-09-01) is invalid**; only the `hindcast_forecasts` rows from `linear_regression_daily`/`nwp_regression` (the two models with a `past_targets` lookback) predate the P1 fix and are invalid on that count — pending being marked superseded once the mac-mini's in-flight onboarding/hindcasting test run finishes (D3; do not mark or recompute against a live system before then).
- **Skill scoring compares a forecast step-mean against an observation resampled to the SAME step (Plan 228 P2 fix)** — `compute_skill_for_station` (`services/skill/service.py`) reads the ONE `time_step` the computation covers directly off the hindcast data itself (`validate_homogeneous_time_step_and_phase`, Plan 228 ALSO FIX #3 — reads `ForecastEnsemble.time_step`, a mandatory field every model sets at construction and migration 0050 persists losslessly, so this cannot fail to resolve once `hindcasts` is non-empty; RAISES `ConfigurationError` on a mixed `time_step` OR a mixed `valid_time` phase within one computation, rather than the earlier `min(...)`-based `_infer_forecast_time_step`'s silent coercion) and resamples observations to it (`_resample_observations_to_forecast_step`, reusing `resample_to_time_step` + `aggregation_method_for` — the SAME aggregation the model trains against, e.g. MEAN for discharge) before the `_build_strata` join. Before the fix, `_build_strata` looked a forecast's `valid_time` up in a dict keyed by RAW observation timestamps — comparing a daily-mean forecast against whichever single instantaneous reading happened to land exactly on `valid_time` (measured live: median 6.4%, p95 48.6%, max 78.8% error, present in every skill score regardless of forecast quality). Buckets are UTC-calendar-aligned (Plan 228 D4), never phase-aligned to the forecast's own `valid_time` — a short-lived intermediate version did the opposite (an `anchor` parameter pinning buckets to a `valid_time` drawn from the hindcasts themselves), which D4 retracted: a forecast whose `valid_time` is not itself calendar-aligned (a non-midnight daily cycle) legitimately produces NO score until Plan 226 fixes `valid_time` itself, rather than this function silently shifting the grid to paper over the mismatch. `compute_skills_task`/`compute_combined_skills_task`/onboarding's skill callback (Plan 228 ALSO FIX #2) derive their OBSERVATION FETCH bounds from `observation_fetch_bounds` (the ensembles' own `valid_time`s + step) — never from `hindcast_step` (the issue time), whose `min == max` for a single hindcast used to collapse to an EMPTY fetch range and silently score nothing. **Both flow-level tasks partition BEFORE calling `observation_fetch_bounds` (Plan 228 review fixer round, major)** — they fetch a station/model's ENTIRE unpartitioned history (no `hindcast_run_id` filter, 1970-2100 bounds), so a single differently configured hindcast run (a retraining, or Plan 226's future per-cycle anchoring) used to trip the mixed-`time_step`/phase `ConfigurationError` above uncaught, with no try/except like the three assembly paths have — permanently halting that station/model's skill scoring on every subsequent run. `partition_by_time_step_and_phase` (`services/skill/service.py`) splits the fetched hindcasts into homogeneous cohorts first; each task computes and stores skill once per cohort, and `compute_combined_skills_task` (the sharper case, since it used to union across every combined model before validating) only combines a cohort where >= 2 models are present.
- **A model's per-variable FI-declared aggregation now survives into SAP3 and wins over the name-keyed fallback (Plan 228 review fixer round, blocker).** `ModelDataRequirements.declared_aggregations` (new field, `types/model.py`) is populated by the FI adapter (`adapters/forecast_interface.py`, from `PastKnownVariable.aggregation`/`FutureKnownVariable.aggregation`, translated via `fi_aggregation_to_canonical` — a SEPARATE enum from SAP3's own `AggregationMethod`, never assumed interchangeable by value). `services/training_data.py::resolved_aggregation_methods` layers it over `_V0_AGGREGATION_FALLBACK` (declared wins) and feeds `resample_to_time_step` in `hindcast.py`, `operational_inputs.py`, and `track_assembly.py` — before this, all three matched purely on parameter NAME and would silently train/hindcast a model that legally declares e.g. `discharge: MAX` on the v0 MEAN fallback instead. Skill scoring's own `_resample_observations_to_forecast_step` still calls the simpler, per-parameter `aggregation_method_for` directly (a single-parameter join has no `ModelDataRequirements` in scope there) — do not conflate the two entry points when extending aggregation logic.
- **`skill_scores`/`skill_diagrams` recompute at a new `computation_version` was silently a no-op since migration 0016 (Plan 228 review fixer round, BLOCKER) — unconditionally, not specific to Plan 228.** Migration 0016 ("Add parameter column...") dropped and recreated both `uq_skill_scores_natural_key` and `uq_skill_diagrams_natural_key` to add `parameter`, and in the process silently DROPPED `computation_version` from both live indexes (present in 0001/0008, absent from 0016 on) — `db/metadata.py`'s Python `sa.Index` objects were never updated to match and have been WRONG about the live schema ever since. Since `store_skill_scores`/`store_skill_diagrams` insert with `ON CONFLICT DO NOTHING`, ANY second write for an existing stratum — a version bump, or a routine re-score — collided with the first write on every column the live index actually checked and was silently dropped, regardless of a differing `computation_version`. Migration 0051 restores `computation_version` to both live indexes, matching what `db/metadata.py` already (and, until now, incorrectly) declared. `services/skill/service.py::_COMPUTATION_VERSION` is bumped 1→2 alongside it — necessary (a recompute needs a different version to even be a distinct key) but was NOT, by itself, sufficient before 0051. Proved by an integration test (`tests/integration/store/test_skill_store.py`) that stores a corrupted v1 score, marks it stale, recomputes via the real `compute_skill_for_station`, and confirms the corrected v2 score — not the stale one — is what `fetch_latest_scores` returns. `SkillStore.mark_stale` remains unwired into any production caller (`compute_skills_task` never calls it) — a SEPARATE, pre-existing design gap this fixer round does not close: a routine re-score at the SAME `computation_version` (no code change) still collides with its own prior run's rows and is silently dropped, now for a different reason (identical key including version, not a missing column) than before 0051.
- **`skill_scores`/`skill_diagrams` natural keys now include `(time_step_seconds, phase_offset_seconds)` (Plan 228 per-run scope, migration 0052, BLOCKER fix).** `partition_by_time_step_and_phase` (previous bullet) computes and stores skill once PER `(time_step, phase)` cohort — deliberately, so a heterogeneous history degrades to "fewer cohorts scored" rather than raising and scoring nothing. But migration 0051's natural keys still omitted both dimensions, so two cohorts producing an identical score row on every OTHER natural-key column (e.g. a daily cohort's 24h lead and an hourly cohort's 24h lead) collided under `ON CONFLICT DO NOTHING` and the second was silently dropped — latent under today's single-cadence config, live the moment heterogeneity appears (a retraining, or Plan 226's planned per-cycle anchoring). `SkillScore`/`SkillDiagram` (`types/skill.py`) gained matching fields, defaulted to `(86400, None)` so unrelated call sites need no changes; `compute_skill_for_station` threads the REAL resolved `(time_step, phase)` into every constructed row. `phase_offset_seconds` uses the same NULL-safe `COALESCE` index pattern migration 0051 already uses for `season`/`flow_regime`/`forcing_type`. Proved by a real-Postgres integration test (`tests/integration/store/test_skill_store.py::test_natural_key_disambiguates_cohorts_by_time_step_and_phase`) — `ON CONFLICT` cannot be exercised against an in-memory fake store.
- **`_valid_time_phase_us` now validates EVERY distinct `valid_time` within one ensemble, not just the first (Plan 228 per-run scope, major fix).** A previous version classified a WHOLE hindcast's phase from `vts[0]` alone — a single multi-step hindcast whose own `valid_time`s internally mix phase (daily leads at both midnight and 06:00 within one ensemble) was silently misclassified by whichever phase happened to be first, passed `validate_homogeneous_time_step_and_phase`'s homogeneity check, and then had its off-phase leads silently vanish downstream (they never land on a UTC-calendar bucket, so `_resample_observations_to_forecast_step`'s exact-key lookup never matches them). Now raises `ConfigurationError` on internal disagreement; `partition_by_time_step_and_phase` catches that per-hindcast and excludes (with a `structlog` warning) just the offending hindcast, matching the graceful-degradation principle the rest of that function already follows — one malformed ensemble degrades scoring by one hindcast, not the whole partitioning pass.
- **`uq_skill_scores_natural_key`/`uq_skill_diagrams_natural_key` now include `model_id` and a NULL-safe `model_artifact_id` (fixer round, BLOCKER fix, migration 0052 amended in place).** Migration 0052 (previous two bullets) widened both natural keys to include `(time_step_seconds, phase_offset_seconds)` but still indexed `model_artifact_id` RAW (no `COALESCE`) and never indexed `model_id` at all. `services/skill/combined_skill.py::compute_combined_skill` always computes pooled/BMA scores with `model_artifact_id=None` — PostgreSQL treats every `NULL` as distinct from every other `NULL`, so (a) two repeated pooled/BMA computations for the identical stratum never collided under `ON CONFLICT DO NOTHING` and inserted a duplicate row on every re-run, defeating idempotency entirely, and (b) without `model_id` in the key, `POOLED_MODEL_ID` and `BMA_MODEL_ID` scores would have collided with each other the moment `model_artifact_id` was made NULL-safe. `model_id` is now part of both natural keys and `model_artifact_id` is compared via `COALESCE(model_artifact_id::text, '')`; the migration also defensively deduplicates (keeps the newest by `created_at`) under the new key before creating the tightened indexes, in case it ever runs against a database that already accumulated duplicates. Proved by two real-Postgres integration tests (`tests/integration/store/test_skill_store.py`): `test_repeated_pooled_computation_with_null_artifact_is_idempotent` and `test_pooled_and_bma_null_artifact_scores_do_not_collide` — both proven RED against the pre-fix schema (the first via unbounded duplicate inserts, the second specifically against a COALESCE-but-no-`model_id` half-fix).
- **Operational-input freshness (`observation_staleness_hours`) is now computed independently of the aligned `past_targets` window (fixer round, major fix).** `assemble_station_operational_inputs` and `assemble_assignment_inputs` (`operational_inputs.py`/`track_assembly.py`) both truncate `past_targets`'s fetch to `aligned_lookback_bounds`, which deliberately EXCLUDES the current, still-forming UTC-calendar bucket (Plan 228 D4) — correct for the data the model consumes. Before this fix, `latest_obs_ts`/`observation_staleness_hours` was derived from that SAME truncated `all_observations` collection, so a 06Z cycle with a 10-minute-old reading reported ~6.2h staleness (crossing the default warning threshold for no real reason), and 12Z/18Z cycles were worse. Both functions now fetch the trailing gap `[past_targets_end, issue_time)` separately, UNRESAMPLED, purely to find the latest raw observation — never mixed into `past_targets` itself. Locked by `TestOperationalInputsPastTargetsAreCompleteUtcCalendarBuckets::test_freshness_reflects_the_partial_bucket_not_the_aligned_window` and the equivalent `test_track_assembly.py` test, both proven RED (measured exactly 6.1667h, matching the D4 arithmetic) against the pre-fix code.
- **`_resample_observations_to_forecast_step` now excludes any bucket whose end has not elapsed as of `clock()` (fixer round, major fix).** `observation_fetch_bounds` fetches observations through `valid_time + time_step`, which does NOT guarantee those rows exist yet — a still-forming bucket (e.g. today's daily mean, built from whatever hours happen to have landed so far) would otherwise exact-match a forecast's `valid_time` in `_build_strata`'s lookup and silently score a partial mean as a complete one. The function now takes an explicit `now: UtcDatetime` (`compute_skill_for_station` passes `clock()`) and drops any resampled bucket where `bucket_start + time_step > now`. Locked by `TestIncompleteCurrentBucketExcludedFromScoring` (`tests/unit/services/skill/test_service.py`), proven RED (score 75.0, exactly the average of a 0-error completed pair and a 150-error partial-bucket pair) against the pre-fix code. **Two pre-existing stratification tests** (`TestSeasonStratification`, `TestFlowRegimeStratification`) used hindcast dates AFTER the fixture `clock`'s `_EPOCH` — an unrealistic fixture shape (you cannot hindcast a future date) that this correct filter now catches; their dates were moved to before `_EPOCH` rather than the filter being weakened.
- **`evaluate_skill_gate` now considers only CURRENT rows at the highest `computation_version` (fixer round, major fix).** Migrations 0051/0052 and `_COMPUTATION_VERSION = 2` deliberately let a stale (pre-Plan-228) score and its corrected recompute coexist — `mark_stale` flips `freshness` without deleting the row. `fetch_skill_scores` returns every version/freshness combination, so before this fix a re-evaluation could let a stale v1 value decide the worst score and wrongly reject or promote an artifact whose CURRENT v2 scores said otherwise. `evaluate_skill_gate` (`services/model_onboarding.py`) now filters to `freshness == CURRENT`, then to `computation_version == max(...)` among those, before the sample-size/threshold logic runs. Locked by `TestEvaluateSkillGate::test_stale_v1_score_never_overrides_current_v2` (opposing stale-v1/current-v2 rows, proven RED against the pre-fix code) and `::test_current_v1_score_still_used_when_no_v2_exists` (guards against over-restricting when no recompute has happened yet).
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

Use this map when a task touches alert **evaluation, state, or delivery** — ensemble/observation threshold checking, the danger-level model, the `Alert` lifecycle (`RAISED` → `ACKNOWLEDGED` → `RESOLVED`), dedup/resolve semantics, or the notification boundary. For **where** alert evaluation attaches to a forecast run (Phase C, the `AlertEligibility` partition, the single all-or-nothing guard), use the **Forecast cycle / assignment selection** map. For `PgAlertStore.upsert_alert` / `acknowledge_alert` write semantics and the (v1.0-removed, 501) `POST /alerts/{id}/acknowledge` route, use the **Persistence / API write path** map. Danger-level / severity definitions are normative in `docs/standards/wmo.md` (WMO-1150 impact-based warnings) — cite it, do not restate. **Aspirational-vs-real is a core hazard here** — much of the notification and state surface is enum/type-only; flagged below.

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

---

### Touchpoint map: LINDAS / BAFU observation ingest

Use this map when a task touches anything that calls `lindas.admin.ch` — the shared
rate limiter, either LINDAS-caller adapter, the operational ingest's per-station
fetch-failure reporting, or either LINDAS-caller cron schedule. See
`docs/decisions/bafu-lindas-rate-limit.md` (Plan 175) for the measured rate-limit
contract this subsystem exists to respect, and `docs/decisions/bafu-lindas-monday-window.md`
for the separate Monday-publish transient this subsystem must not be confused with.

**Common touch triggers:**

- the shared limiter (`adapters/lindas_rate_limiter.py` — `TokenBucketLindasLimiter`,
  `LindasLimiterConfig`, the `LindasRateLimiter` Protocol)
- either LINDAS-caller adapter (`adapters/bafu_observation.py`,
  `adapters/hydro_scraper.py`)
- the shared query layer (`adapters/lindas_hydro_query.py`, Plan 186 T1/D2) — SPARQL
  query text/constants (`build_whole_graph_query`, `QUERY_LIMIT`, the dimension/graph
  URIs) and the non-raising `parse_subject_key` helper. Both callers import from here;
  grouping, validation and failure policy stay adapter-local (see D2 below)
- `HydroScraperAdapter.fetch_observations_batch` / `StationFetchOutcome` /
  `FetchOutcomeCause` — the ingest flow's per-station failure reporting. Since Plan
  186, this issues ONE whole-graph fetch per call (not one request per station) and
  filters locally to the passed `station_configs`
- `ingest_observations_flow`'s `PipelineCheckType.OBSERVATION_INGEST_FETCH` health
  record
- either LINDAS-caller cron schedule (`SCHEDULE_INGEST_OBSERVATIONS`,
  `SCHEDULE_COLLECT_BAFU_OBSERVATIONS`) in `docker-compose.yml` or
  `cli/register_deployments.py`
- `tools/record_fixtures.py`'s BAFU path, `tests/integration/live/test_lindas_live_schema.py`,
  `tests/integration/live/test_lindas_publish_lag.py` (Plan 176 T7 — extended publish-lag
  measurement; `live_lindas_lag` marker, NOT swept by the `live_lindas` weekly workflow)
- `cli/bafu_observation_audit.py` (Plan 176 T8) reads the archive only — it is
  NOT a LINDAS caller and carries no limiter

**Upstream inputs to inspect:**

- the measured rate-limit contract (burst 3, ~1 slot refill per 3-4 s, no
  `Retry-After`) — do not re-derive it from a fresh probe without updating
  `bafu-lindas-rate-limit.md`
- `config.toml` / overlay `[adapters.river_stations].endpoint` and
  `[adapters.bafu_observation].archive_base_path` (the Plan 136 collector's
  quarantine gate — unrelated to this subsystem's limiter, but shares the
  endpoint)
- the onboarded station roster feeding `ingest_observations_flow` (D5's scaling
  ceiling on the OLD per-station design; Plan 186 made ingest cost flat in station
  count, so this is no longer a scaling risk — see below)

**Core implementation touchpoints:**

- `TokenBucketLindasLimiter.call` — pacing (token-bucket acquire, once per HTTP
  attempt, including retries — round 2) + bounded 429/5xx/transport retry (attempt
  cap AND wall-clock deadline, independently). `send` runs on the calling
  thread; the deadline gates when a new attempt or retry sleep may START, and
  each attempt is bounded by the HTTP client's own phase timeouts
- `BafuObservationAdapter._post_with_retries` / `HydroScraperAdapter.
  fetch_observations_batch` — both translate `LindasRateLimitExhausted` into their
  own error shape (`AdapterError` for the collector; `StationFetchOutcome.
  failure_cause` for ingest, broadcast to every eligible station — D4)
- `HydroScraperAdapter._group_by_requested_subject` / `._extract_one` (Plan 186 T2)
  — the ingest-local grouping/extraction split D2 requires: bindings are grouped by
  *usable subject only* (non-raising `parse_subject_key`, dropped silently if
  unattributable or not one of the requested `(code, station_kind)` keys), then each
  requested station's OWN group is validated independently via `_parse_bindings_typed`
  — a malformed binding for a station not in this batch is never looked at, and one
  station's malformed/absent data cannot fail another (subject-local, D4)
- `protocols/adapters.py`'s `BatchStationDataSource` — narrow capability Protocol
  for `fetch_observations_batch`; `HydroScraperAdapter` and `ReplayStationAdapter`
  both satisfy it (round 2 — `ingest_observations_flow`'s `adapter=` parameter is
  typed against this, not the concrete `HydroScraperAdapter`)
- `ingest_observations_flow` — fetch → `_append_fetch_health_record` (BEFORE
  store/QC) → store → QC → result union of fetch + QC failures
- Plan 217 (M-G1): the fetch now also pulls `StationKind.WEATHER` (joining
  RIVER/LAKE, D1). Weather stations gate on `station_status` alone — the
  `GaugingStatus.GAUGED` filter is RIVER/LAKE-only (D2), since `gauging_status`
  *defaults* to GAUGED and is a discharge (rating-curve) concept, not a weather
  one. The `fetch_latest_timestamp` cursor parameter comes from an explicit
  `_cursor_parameter_for_kind` mapping (D3, WEATHER → `"precipitation"`) that
  raises `ConfigurationError` on an unhandled `StationKind` rather than
  defaulting; calculated-station derivation stays RIVER/LAKE-only regardless of
  a WEATHER station's `gauging_status` (D4). No adapter maps `WEATHER` yet
  (`HydroScraperAdapter` drops it, D5) — weather stations are eligible and
  cursor-correct but unserved until M-G2, and no QC rule matches
  `"precipitation"` yet (M-I4). See
  `docs/design/dhm-precipitation-milestones.md` § M-G1 and
  `docs/plans/217-weather-station-observation-ingest.md`.
- the two cron defaults, each living in TWO places (compose init env + the Python
  fallback) — see the Prefect/Docker/deployment map for the general pattern
- Plan 176 D2/D3: `collect_bafu_observations_flow`'s `cycle_at` is now DATA-derived
  (the response's modal `measurement_time`, truncated to the 10-minute grid) —
  the flow FETCHES BEFORE it can dedup (the key is unknowable from the clock
  alone). A dedup skip still appends a freshness heartbeat using `run_at`; only
  the archive WRITES are skipped (D3 — the trap inside D2)
- Plan 176 D5: `collect-bafu-observations` now shares the `ingest` pool with
  `ingest-observations` — this does **not** create implicit rate-limit
  coordination between them (see "Pacing is process-local" below); it is
  insurance against worker-side poll-cycle starvation, unrelated to the
  schedule-separation contract this map otherwise documents

**Downstream consumers to inspect when behavior changes:**

- the watchdog (`ops/watchdog.py`) if `BAFU_OBSERVATION_FRESHNESS` semantics change
  (it queries that check type directly; `OBSERVATION_INGEST_FETCH` has **no** probe
  wired yet — Plan 175 § Residual forks #1)
- `/api/v1/health/detail` and the dashboard health page, the only current visibility
  for `OBSERVATION_INGEST_FETCH`
- `tools/record_fixtures.py` constructs `HydroScraperAdapter`/`BafuObservationAdapter`
  directly and gets the default limiter by construction; changing the default
  limiter config changes its behavior too. The live LINDAS schema test
  (`test_lindas_live_schema.py`) instead shares ONE `TokenBucketLindasLimiter`
  instance (a module-scoped fixture) across every adapter it builds (round 2) — a
  fresh-per-class limiter let one test's real upstream consumption go unseen by
  the next, producing a predictable 429 the zero-429 assertion could not survive

**Contracts that must not change silently:**

- **All in-process production LINDAS callers go through the shared limiter.** The
  verified caller set (Plan 175 D6): `adapters/bafu_observation.py`,
  `adapters/hydro_scraper.py` (**every** public method, including
  `verify_gauge_reachable` — fixer-round hardening closed a gap where that probe
  POSTed directly), `tools/record_fixtures.py` (constructs `HydroScraperAdapter`
  and calls `fetch_observations`), `tests/integration/live/test_lindas_live_schema.py`,
  and `tests/integration/live/test_lindas_publish_lag.py` (Plan 176 T7). A new
  LINDAS caller that builds its own `httpx.Client` POST instead of going
  through one of these two adapters silently re-opens the collision this
  subsystem exists to close.
- **Pacing is process-local, not cluster-wide.** A token bucket inside one process
  cannot enforce a shared budget across two different Prefect work-pool processes —
  cross-process safety rests on schedule separation (the two cron minutes), not on
  the limiter. **Still true after Plan 176 D5 moved both LINDAS-calling
  deployments onto the SAME `ingest` pool**: `ProcessWorker` submits each flow
  run as its own OS subprocess, so co-location on one pool does not give the
  two deployments a shared limiter instance either — do not assume the pool
  move bought any pacing coordination it did not have before. Do not treat
  the limiter as a substitute for schedule separation.
- **`Retry-After` is untrusted input.** Any change to the limiter's parsing must keep
  the clamp (`LINDAS_MAX_DELAY_S`), the floor (`LINDAS_RETRY_FLOOR_S` — a
  `Retry-After` value BELOW the floor is clamped UP, not honoured verbatim), and the
  total wall-clock deadline (`LINDAS_TOTAL_DEADLINE_S`) — both delay bounds
  independent of the attempt-count bound. This includes a numeric value large
  enough to overflow `float()` or exceed CPython's int-string-conversion digit
  limit (round 2) — both must clamp to `LINDAS_MAX_DELAY_S`, never raise past
  `_parse_retry_after`.
- **The 120 s deadline covers bucket wait too, and bounds when work STARTS.**
  `TokenBucketLindasLimiter.call` starts its clock BEFORE token acquisition, not
  after, and re-checks the remaining budget before every attempt. The guarantee
  is: **no new attempt and no retry sleep starts past the deadline.** It does
  NOT abort an in-flight request — each attempt is bounded by the HTTP client's
  own configured connect/read/write/pool timeouts. An earlier version ran `send`
  on a daemon thread joined with a hard timeout to get a stricter bound; that
  bounded the caller's wait but abandoned the thread and its live request, so
  repeated timeouts leaked both while reporting the call exhausted. The thread
  was removed in favour of the weaker, honest bound.
- **The fetch health record is written IMMEDIATELY after fetch reconciliation, before
  store/QC.** Moving it later would let a downstream storage/QC failure suppress the
  one signal this subsystem exists to guarantee.
- **`StationDataSource.fetch_observations` keeps its locked list-only shape.** The
  typed per-station outcome (`fetch_observations_batch`, captured by the narrow
  `BatchStationDataSource` Protocol) is an ADDITION any `StationDataSource`
  implementation may also offer — `HydroScraperAdapter` and (round 2)
  `ReplayStationAdapter` both do — not a `StationDataSource` Protocol change;
  widening that Protocol itself was the explicit rejected alternative (see
  `docs/spec/types-and-protocols.md` § StationDataSource).
- **`ReplayStationAdapter` requires a UNIQUE `station_code` across a requested
  batch's `station_configs` (Plan 217 fixer round).** The fixture keys rows by
  `station_code` alone (`docs/plans/archive/020-phase3-replay-recording.md`),
  but `code` is only unique per `(network, code)` in the DB
  (`uq_stations_network_code`) — Plan 217 made this reachable for the first
  time in practice by adding WEATHER to the same fetch as RIVER/LAKE, so a
  weather station and a river station on different networks can now
  legitimately share a bare code in one replay batch. `fetch_observations`/
  `fetch_observations_batch` raise `ConfigurationError` on any such collision
  rather than silently routing a matching row (e.g. discharge) to whichever
  config happened to be last in the list. Do not "fix" this by reverting to
  last-write-wins; if fixture identity ever needs to be more than
  `station_code`, that is a fixture-schema change (adds a `network` column),
  not a lookup-order fix.
- **The per-station fan-out was replaced by a whole-graph fetch (D5, RESOLVED —
  Plan 186).** `HydroScraperAdapter.fetch_observations_batch` issues ONE SPARQL
  request per call regardless of station count, indexed by `(gauge_code,
  lindas_kind)` and filtered locally — see `bafu-lindas-rate-limit.md` for the
  before/after arithmetic. Do not reintroduce a per-station request loop; the
  `flat_in_station_count` test in `tests/unit/adapters/test_hydro_scraper.py`
  locks this with `max_retries=0`.
- **Whole-batch vs subject-local failure semantics (D4) — do not blur these.** A
  transport/HTTP fault on the one request (429/5xx/connect error) fails EVERY
  eligible station in the batch with the same cause — a deliberate change in kind
  from the old per-station isolation (Q1, accepted). A malformed binding or an
  absent/incomplete subject stays SUBJECT-LOCAL — it must never touch another
  station's outcome. A mutant that broadcasts a subject-local fault, or that
  emits a single outcome instead of one-per-eligible-station on a whole-batch
  fault, is the one most likely to survive a thin test suite — see the "Subject-
  local isolation" / "Whole-batch causes" test classes for the lock.
- **An unmatched/invalid station code is now a lookup MISS (`NO_DATA`), not a
  pre-request `MALFORMED_RESPONSE` (Q2).** Ingest no longer interpolates
  `site_code` into SPARQL at all, so `_SITE_CODE_RE`'s injection guard has no
  surface left in this path and does not fire here. The guard survives ONLY in
  `_build_sparql_query` / `verify_gauge_reachable` (the onboarding-time probe,
  still one-request-per-gauge). Do not "fix" a NO_DATA-on-bad-code result back to
  MALFORMED_RESPONSE by reflex — that would be reintroducing a guard this design
  deliberately removed from the ingest path.
- **The quarantine boundary is query-sharing only, not adapter-sharing (D2).**
  `adapters/lindas_hydro_query.py` shares SPARQL text/constants and the
  non-raising `parse_subject_key` key helper — nothing else. `BafuObservationAdapter`
  keeps its own raising, fail-the-whole-fetch grouping (`_SparqlTriple`,
  correct for an archive snapshot); `HydroScraperAdapter` keeps its own
  non-raising, subject-local grouping. Do not lift `BafuObservationAdapter`'s
  grouping loop into the shared module and call it from ingest — it raises on a
  malformed binding anywhere in the graph, which would fail every station in one
  batch and destroy the D4 isolation guarantee above. This is a WEAKER structural
  guarantee than Plan 136's separate-adapter quarantine (DC-2) — a separate
  mapper, not a separate adapter — flagged deliberately in Plan 186, not an
  oversight.
- **A cron default lives in TWO places; the compose fallback is the one that
  deploys.** See the Prefect/Docker/deployment map's general version of this
  contract; `tests/unit/test_compose_schedule_default.py` locks both
  LINDAS-relevant schedules.

**Suggested verification:**

- limiter unit tests with a fake clock + recording sleeper (never real time, never
  the network) — retry-after clamp, total-deadline abort, bucket-pacing, exhaustion
- exhaustion test asserting the EXACT HTTP attempt count (a success-path retry test
  cannot detect a retained/duplicated outer retry loop)
- a real-adapter-over-`httpx.MockTransport` regression test for the incident itself
  (transient 429 → 200 → OK heartbeat, not CRITICAL)
- `stations_failed` / `OBSERVATION_INGEST_FETCH` detail-key tests: mixed
  success/failure, all-failure (must not read as success), and a storage failure
  AFTER fetch (the health record must still have persisted)
- the compose + registration cron-schedule lock tests (non-divisible-by-5, disjoint
  from the other LINDAS/BAFU crons)
- full Task Exit Gate for implementation PRs

**Context packet reminder:**

When this map applies, the context packet should name: which LINDAS caller(s) are
touched; whether the change affects pacing, retry, or schedule separation (or more
than one); which downstream consumers (watchdog, health API, fixture recorder, live
test) are affected or explicitly not; which contracts are at risk (especially the
process-local-pacing and D5-scaling-ceiling caveats); and which focused tests will
prove the change.
