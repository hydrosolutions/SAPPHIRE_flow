# Plan 003 — Multi-Target predict() Chain

---
status: ARCHIVED
created: 2026-03-26
scope: protocols + services + store + tests + docs
depends_on: [007, 004]  # Phase 0 needs 007; Phase 2 needs 004 (SkillScore.parameter field)
---

## Problem Statement

Both `StationForecastModel.predict()` and `GroupForecastModel.predict_batch()` currently return a single `ForecastEnsemble` per station. As multi-output models (e.g. predicting both `discharge` and `water_level` simultaneously) are introduced, these methods will return `dict[str, ForecastEnsemble]` keyed by parameter name — one ensemble per predicted parameter.

The hindcast service, hindcast store, skill computation, and fetch protocol must all be updated to handle the resulting multiple `HindcastForecast` records (one per parameter) for each `(station_id, issue_time)` combination.

---

## Current State Inventory

### `protocols/forecast_model.py`

The spec (`docs/spec/types-and-protocols.md`) already defines multi-target return types:
- `StationForecastModel.predict()` → `tuple[dict[str, ForecastEnsemble], bytes | None]`
- `GroupForecastModel.predict_batch()` → `dict[StationId, tuple[dict[str, ForecastEnsemble], bytes | None]]`

The source code (`src/sapphire_flow/protocols/forecast_model.py`) still has single-ensemble returns:
- `StationForecastModel.predict()` → `tuple[ForecastEnsemble, bytes | None]`
- `GroupForecastModel.predict_batch()` → `dict[StationId, tuple[ForecastEnsemble, bytes | None]]`

After plan 007 lands, Protocol *attributes* (`data_requirements.past_dynamic_features`, `station_config.forecast_targets`) match the spec, but *return types* still need updating. Phase 0 of this plan closes that gap.

**Note:** Plan 007 does **not** update `predict_batch()`'s input type from `dict[StationId, ModelInputs]` to `GroupModelInputs`. That change is out of scope for both plan 007 and this plan — it will be addressed when `GroupModelInputs` is first consumed by a real model implementation. Phase 0A here updates only return types.

### `services/hindcast.py`

`run_station_hindcast` — calls `model.predict()`, receives one `(ensemble, _)`, constructs one `HindcastForecast`, calls `hindcast_store.store_hindcast()` once per issue time.

`run_group_hindcast` — calls `model.predict_batch()`, iterates `batch_results.items()` as `(sid, (ensemble, _))`, constructs one `HindcastForecast` per station per issue time.

Both are single-parameter only.

### `types/forecast.py`

`HindcastForecast` has no top-level `parameter` field — the parameter is carried inside `ensemble.parameter`. This is sufficient; no change needed to the dataclass itself.

### `protocols/stores.py` — `HindcastStore`

`fetch_hindcasts()` has no `parameter` filter. When multiple parameters exist for the same `(station_id, model_id, hindcast_step)`, callers (skill service, flow orchestration) need to request a single-parameter slice.

### `store/hindcast_store.py` — `PgHindcastStore`

`store_hindcast()` already writes `hindcast.ensemble.parameter` to the `parameter` column — no change needed.

`fetch_hindcasts()` does not filter on `parameter`. Must add the optional filter.

### `db/metadata.py` — `hindcast_forecasts` table

`parameter` column exists (`sa.Column("parameter", sa.Text, nullable=False)`).

The index `ix_hindcast_forecasts_station_model_step` covers `(station_id, model_id, hindcast_step)` but does not include `parameter`. With multi-parameter storage the selectivity of queries that filter on `parameter` additionally benefits from including it.

There is **no unique constraint** on `(station_id, model_id, hindcast_step, parameter)`. Each hindcast run generates fresh UUIDs so there is no collision risk in practice, but a natural-key uniqueness guarantee would be desirable. Whether to add it is a separate migration decision — this plan flags it but does not mandate it in Phase 1.

### `services/skill/service.py` — `compute_skill_for_station()`

Accepts `list[HindcastForecast]` without parameter awareness. `_build_strata` iterates `hindcast.ensemble.values` directly and looks up `obs_lookup` timestamps. If hindcasts for different parameters are mixed in, observations for `water_level` would be matched against `discharge` ensemble values (or vice versa), silently producing garbage metrics.

The function must either:
- Receive only pre-filtered hindcasts (caller responsibility), or
- Accept an explicit `parameter: str` argument and assert/filter internally.

The caller approach is simpler and more composable.

### `services/forecast_qc.py` — `ForecastOutputQualityChecker`

Takes a single `ForecastEnsemble` directly. Not affected — `ForecastOutputQualityChecker` is implemented but not invoked in the hindcast flow (by design). The checker operates per-ensemble and requires no structural changes for multi-target support.

### `tests/fakes/fake_models.py`

`FakeStationForecastModel.predict()` and `FakeGroupForecastModel.predict_batch()` return single-ensemble forms. Phase 0B updates these to return the multi-target form. Because `hindcast.py` destructures `ensemble, _ = model.predict(...)`, the fake model update and hindcast service update **must land atomically** — otherwise `ensemble` silently becomes a `dict` instead of a `ForecastEnsemble`, and current tests do not catch this (no test asserts on `h.ensemble` type).

### `tests/fakes/fake_stores.py` — `FakeHindcastStore`

`fetch_hindcasts()` has no `parameter` filter. Must add it to match the Protocol change.

---

## Changes

### Phase 0 — Protocol Return Types + Hindcast Service + Fakes (atomic)

**Critical:** Phases 0A–0D must land in a single commit. Updating fake model return types (0B) without updating the hindcast service (0C/0D) causes silent data corruption: `ensemble, _ = model.predict(...)` in `hindcast.py` would assign a `dict` to `ensemble` instead of a `ForecastEnsemble`. Current tests do not catch this because no test asserts on `h.ensemble` type.

#### 0A. `protocols/forecast_model.py`

`StationForecastModel.predict()`:

```python
# BEFORE (source)
def predict(self, ...) -> tuple[ForecastEnsemble, bytes | None]: ...

# AFTER (matches spec)
def predict(self, ...) -> tuple[dict[str, ForecastEnsemble], bytes | None]: ...
```

`GroupForecastModel.predict_batch()`:

```python
# BEFORE (source)
def predict_batch(self, ...) -> dict[StationId, tuple[ForecastEnsemble, bytes | None]]: ...

# AFTER (matches spec)
def predict_batch(self, ...) -> dict[StationId, tuple[dict[str, ForecastEnsemble], bytes | None]]: ...
```

#### 0B. Update all fake models

`FakeStationForecastModel.predict()` → return `({self.parameter: ensemble}, state)`
`FakeGroupForecastModel.predict_batch()` → return `{sid: ({param: ensemble}, state) for ...}`

#### 0C. `services/hindcast.py` — `run_station_hindcast`

Replace the current single-ensemble storage block:

```python
# BEFORE
ensemble, _ = model.predict(artifact=artifact, inputs=inputs, rng=rng, prior_state=None)
hindcast = HindcastForecast(...)
hindcast_store.store_hindcast(hindcast)
results.append(HindcastStepResult(issue_time=issue_time, success=True))
```

With a multi-parameter loop:

```python
# AFTER
ensembles, _ = model.predict(artifact=artifact, inputs=inputs, rng=rng, prior_state=None)
for param_name, ensemble in ensembles.items():
    if ensemble.parameter != param_name:
        raise ValueError(
            f"Dict key '{param_name}' != ensemble.parameter '{ensemble.parameter}'"
        )
    hindcast = HindcastForecast(
        id=HindcastForecastId(uuid4()),
        station_id=station_id,
        model_id=model_id,
        model_artifact_id=artifact_id,
        hindcast_step=issue_time,
        forcing_type=ForcingType.REANALYSIS,
        representation=EnsembleRepresentation.MEMBERS,
        hindcast_run_id=hindcast_run_id,
        ensemble=ensemble,
        created_at=clock(),
    )
    hindcast_store.store_hindcast(hindcast)
results.append(HindcastStepResult(issue_time=issue_time, success=True))
```

The `HindcastStepResult` is appended once per issue time (not once per parameter) — the step either succeeds or fails atomically. If storing any individual parameter fails it will be caught by the outer `except` block and the whole step is marked failed.

**Partial-write risk:** If parameter 1 stores successfully but parameter 2 raises, one record exists in the DB for a step marked as failed. This is accepted for now — Open Item 2 / Phase 3C (unique constraint) will enable idempotent re-runs that clean up partial writes.

**Doc update:** Update `docs/architecture-context.md` Flow 7 H.6 to note that multi-target models produce N `HindcastForecast` records per `(station_id, hindcast_step)` — one per forecast target parameter. Consumers (Flow 8 skill computation) must filter by parameter when fetching.

#### 0D. `services/hindcast.py` — `run_group_hindcast`

Replace the current storage block inside `for sid, (ensemble, _) in batch_results.items()`:

```python
# BEFORE
for sid, (ensemble, _) in batch_results.items():
    hindcast = HindcastForecast(...)
    hindcast_store.store_hindcast(hindcast)
    per_station[sid].append(HindcastStepResult(issue_time=issue_time, success=True))
```

With:

```python
# AFTER
for sid, (ensembles, _) in batch_results.items():
    param_name: str | None = None
    try:
        for param_name, ensemble in ensembles.items():
            if ensemble.parameter != param_name:
                raise ValueError(
                    f"Dict key '{param_name}' != ensemble.parameter '{ensemble.parameter}'"
                )
            hindcast = HindcastForecast(
                id=HindcastForecastId(uuid4()),
                station_id=sid,
                model_id=model_id,
                model_artifact_id=artifact_id,
                hindcast_step=issue_time,
                forcing_type=ForcingType.REANALYSIS,
                representation=EnsembleRepresentation.MEMBERS,
                hindcast_run_id=hindcast_run_id,
                ensemble=ensemble,
                created_at=clock(),
            )
            hindcast_store.store_hindcast(hindcast)
        per_station[sid].append(HindcastStepResult(issue_time=issue_time, success=True))
    except Exception as exc:
        log.error("hindcast.store_failed", station_id=str(sid),
                  issue_time=str(issue_time), parameter=param_name or "<unknown>",
                  exc_info=exc)
        per_station[sid].append(HindcastStepResult(..., success=False, error=str(exc)))
```

**Note:** The `ensembles` dict keys reflect the model's training targets (what the model predicts), not necessarily the station's `forecast_target`. This is architecturally sound — storage is parameter-agnostic — but callers must be aware that a multi-target model may store parameters the station is not explicitly configured to forecast.

**Dependencies:** Plan 007 complete. Phase 1 **must land with Phase 0** — Phase 2's `_fetch_hindcasts` task needs the `parameter=` filter from Phase 1A/1B to fetch parameter-scoped hindcasts for skill computation.

---

### Phase 1 — Protocol and Store Layer

#### 1A. `protocols/stores.py`

Add optional `parameter: str | None = None` to `HindcastStore.fetch_hindcasts()`.

```python
def fetch_hindcasts(
    self,
    station_id: StationId,
    model_id: ModelId,
    start: UtcDatetime,
    end: UtcDatetime,
    forcing_type: ForcingType | None = None,
    hindcast_run_id: UUID | None = None,
    parameter: str | None = None,       # NEW
) -> list[HindcastForecast]:
```

The default `None` means "all parameters" — existing callers that do not pass it continue to receive all parameters, which is backward-compatible.

**Dependencies:** None.

#### 1B. `store/hindcast_store.py` — `PgHindcastStore`

Add `parameter: str | None = None` to `fetch_hindcasts()`. When not `None`, append a WHERE clause:

```python
if parameter is not None:
    q = q.where(hindcast_forecasts.c.parameter == parameter)
```

No other changes to this file.

**Dependencies:** 1A must be done first so the signature is consistent with the Protocol.

#### 1C. `tests/fakes/fake_stores.py` — `FakeHindcastStore`

Add `parameter: str | None = None` to `fetch_hindcasts()`. Add filter predicate:

```python
and (parameter is None or h.ensemble.parameter == parameter)
```

**Dependencies:** 1A.

---

### Phase 2 — Skill Computation: Parameter Awareness

**Gate:** Phase 2 code changes can land immediately, but **skill computation must only be invoked with `parameter="discharge"` until plan 004 adds `SkillScore.parameter`**. A runtime guard in `compute_skills_flow` (Phase 2B) enforces this. Running for non-discharge parameters before 004 lands produces skill scores that are indistinguishable by parameter in the DB — inconsistent with WMO verification practice (WMO-1364).

#### 2A. `services/skill/service.py` — `compute_skill_for_station()`

Add `parameter: str` as a **required** argument. Add a mismatch check at entry:

```python
def compute_skill_for_station(
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
    hindcasts: list[HindcastForecast],
    observations: list[Observation],
    thresholds: list[StationThreshold],
    flow_regime_config: FlowRegimeConfig | None,
    seasons: list[SeasonDefinition],
    skill_source: SkillSource,
    forcing_type: ForcingType | None,
    clock: Callable[[], UtcDatetime],
    uuid_factory: Callable[[], UUID],
    parameter: str,                        # NEW — required, not optional
) -> tuple[list[SkillScore], list[SkillDiagram]]:
```

At entry, after the empty-list guard, add:

```python
mismatched = [hc for hc in hindcasts if hc.ensemble.parameter != parameter]
if mismatched:
    raise ValueError(
        f"compute_skill_for_station received hindcasts with parameters other than "
        f"'{parameter}': {sorted({hc.ensemble.parameter for hc in mismatched})}"
    )
```

The calling flow is responsible for grouping hindcasts by `ensemble.parameter` and fetching observations for the matching parameter before calling `compute_skill_for_station`. The mismatch assertion is a defensive safety net.

`SkillScore` and `SkillDiagram` do not yet carry `parameter` — flagged in Open Item 1 below. Plan 004 adds the field; until it lands, the flow-level guard in Phase 2B blocks non-discharge invocations.

**Dependencies:** None (independent of Phase 1 and 2 except that it is meaningless to run until multi-parameter hindcasts are being stored).

#### 2B. `flows/compute_skills.py`

This flow has **two hardcoded `"discharge"` references** and passes results to `compute_skill_for_station()` without a `parameter` argument.

The two hardcoded strings:
1. `parameter="discharge"` in `_fetch_observations` (line 42)
2. `"discharge"` in `flow_regime_store.fetch_latest(station_id, "discharge")` (line 103)

Update the flow to:
- Add `parameter: str` as a **required** argument to `compute_skills_flow`. At flow entry, add a temporary guard: `if parameter != "discharge": raise NotImplementedError("Non-discharge skill computation requires SkillScore.parameter field (plan 004) to comply with WMO-1364 verification standards")`. Remove when plan 004 lands.
- Add `parameter: str` to `_fetch_observations` task signature; replace the hardcoded `parameter="discharge"` (line 42) with `parameter=parameter`
- Add `parameter: str` to `_fetch_hindcasts` task signature; pass `parameter=parameter` to `hindcast_store.fetch_hindcasts()`
- Replace `flow_regime_store.fetch_latest(station_id, "discharge")` (line 103) with `flow_regime_store.fetch_latest(station_id, parameter)`
- Pass `parameter=parameter` to `compute_skill_for_station()` (new argument, not replacing a hardcoded string)

**Why all five changes are required:** Without updating `_fetch_observations`, running skill computation for `water_level` would silently fetch discharge observations and match them against water_level hindcasts — producing garbage metrics with no error.

**Caller impact:** `train_models_flow` in `flows/train_models.py` (lines 302–314) is currently the only caller. It must add `parameter="discharge"` to its `compute_skills_flow()` invocation. Any future flow or subflow that invokes `compute_skills_flow` must also pass `parameter`.

**Multi-parameter orchestration:** When a training flow runs for a multi-target model, the caller is responsible for iterating over the model's target parameters and invoking `compute_skills_flow(parameter=p)` once per parameter. Whether this loop runs sequentially or fans out as parallel Prefect task submissions is a decision for the training flow's plan — this plan does not prescribe it.

**Dependencies:** 2A.

---

### Phase 3 — DB Schema: Index and Unique Constraint

#### 3A. Add `parameter` to the compound index (new Alembic migration)

The existing `ix_hindcast_forecasts_station_model_step` covers `(station_id, model_id, hindcast_step)`. Skill computation and fetch calls will now filter on `parameter` in addition, so the index should be extended.

New migration (`0015_hindcast_parameter_index.py`) — number is correct assuming plan 007's migration `0014` lands first (hard dependency):

```python
def upgrade() -> None:
    # Create the wider index. The old 3-column index is a leading-column
    # subset of this 4-column index, so PostgreSQL can still serve old
    # queries via leftmost-prefix matching. We keep the old index in
    # this migration for strict additive-only compliance (cicd.md §Migrations).
    op.create_index(
        "ix_hindcast_forecasts_station_model_step_param",
        "hindcast_forecasts",
        ["station_id", "model_id", "hindcast_step", "parameter"],
    )

def downgrade() -> None:
    op.drop_index("ix_hindcast_forecasts_station_model_step_param",
                  table_name="hindcast_forecasts")
```

Update `db/metadata.py` to add the new `sa.Index(...)` definition (keeping the old index definition as-is).

#### 3B. Drop redundant old index (follow-up migration, next release)

After plan 003's code has been deployed and the old application image is no longer running, a follow-up migration drops the now-redundant 3-column index:

```python
def upgrade() -> None:
    op.drop_index("ix_hindcast_forecasts_station_model_step",
                  table_name="hindcast_forecasts")

def downgrade() -> None:
    op.create_index(
        "ix_hindcast_forecasts_station_model_step",
        "hindcast_forecasts",
        ["station_id", "model_id", "hindcast_step"],
    )
```

This is a separate migration in a later release, not part of plan 003's implementation scope.

#### 3C. Unique constraint (deferred, flagged)

A unique constraint on `(station_id, model_id, hindcast_run_id, hindcast_step, parameter)` would prevent duplicate storage on re-runs. Not required for correctness because each call generates a fresh UUID PK, but the partial-write risk (see Phase 0C/0D) means re-runs can accumulate duplicate records for failed steps. Add as a follow-up migration when idempotent re-run support becomes a requirement.

---

### Phase 4 — Test Changes

#### 4A. Tests that break without changes

| Test file | Why it breaks | Fix |
|---|---|---|
| `tests/unit/services/skill/test_service.py` — all tests in `TestComputeSkillBasic`, `TestSeasonStratification`, `TestNoMatchingObservations` | `compute_skill_for_station()` gains a required `parameter` argument | Add `parameter="discharge"` to every call |
| `tests/unit/services/test_hindcast.py` — all tests using `FakeStationForecastModel` or `FakeGroupForecastModel` | fake models return `dict[str, ForecastEnsemble]` after Phase 0B, but hindcast service is also updated in 0C/0D (atomic) | No changes needed — service destructures `ensembles.values()` correctly. **Add `assert isinstance(h.ensemble, ForecastEnsemble)` to all existing hindcast storage assertions** in `TestBasicHindcast`, `TestStepFailureContinues`, `TestHindcastStored`, and any other test class that stores hindcasts, to guard against dict-vs-ensemble regression. |

#### 4B. `FakeHindcastStore` — `fetch_hindcasts` signature change

All existing test files that call `hindcast_store.fetch_hindcasts(...)` without `parameter=` continue to work because the new argument defaults to `None`. No existing tests break.

#### 4C. New tests needed

**`tests/unit/services/test_hindcast.py`**

1. `TestMultiParameterStation` — `test_two_parameters_stored_per_step`:
   - Use `FakeMultiTargetStationForecastModel` returning two keys: `"discharge"` and `"water_level"`.
   - Assert `FakeHindcastStore` contains `2 * n_steps` records.
   - Assert filtering by `parameter="discharge"` returns only discharge records.
   - Assert filtering by `parameter="water_level"` returns only water_level records.
   - **Assert each stored `h.ensemble` is a `ForecastEnsemble` instance** (guards against the silent dict-corruption regression).

2. `TestMultiParameterGroup` — `test_two_parameters_stored_for_group`:
   - Same shape as above but using `run_group_hindcast`, `FakeMultiTargetGroupForecastModel`, and two stations.
   - Assert total stored = `2_params * 2_stations * n_steps`.

**`tests/unit/services/skill/test_service.py`**

3. `TestParameterMismatch` — `test_mismatched_parameter_raises`:
   - Build hindcasts with `parameter="water_level"`, call `compute_skill_for_station` with `parameter="discharge"`.
   - Assert `ValueError` is raised with a message matching `"parameters other than"`.

4. `TestParameterFiltering` — `test_skill_computed_only_for_named_parameter`:
   - Mix hindcasts of `"discharge"` and `"water_level"` in a list.
   - Pre-filter before calling: confirm skill computed on discharge-only slice produces correct sample sizes.

**`tests/unit/flows/test_compute_skills.py`** (or inline in existing test file):

5. `TestNonDischargeGuard` — `test_non_discharge_raises_not_implemented`:
   - Call `compute_skills_flow(parameter="water_level", ...)`.
   - Assert `NotImplementedError` is raised with message matching `"SkillScore.parameter"`.
   - Temporary test — remove when plan 004 lands and the guard is removed.

**`tests/fakes/fake_models.py`** — add helpers (not a test class, but test-support code):

5. `FakeMultiTargetStationForecastModel` — returns `tuple[dict[str, ForecastEnsemble], bytes | None]` with two parameters. Used only by new hindcast tests.

6. `FakeMultiTargetGroupForecastModel` — returns `dict[StationId, tuple[dict[str, ForecastEnsemble], bytes | None]]` with two parameters per station. Used only by new group hindcast tests.

**`tests/unit/services/test_hindcast.py`** — backward-compatibility & edge cases:

7. `TestSingleParameterBackwardCompat` — `test_single_param_model_stores_one_record_per_step`:
   - Use the updated `FakeStationForecastModel` (returns `{"discharge": ensemble}` — a single-entry dict).
   - Assert one `HindcastForecast` stored per step, `isinstance(h.ensemble, ForecastEnsemble)`.
   - Guards against regressions where single-param models break after the Phase 0 refactor.

8. `TestEmptyEnsembleDict` — `test_empty_ensemble_dict_stores_nothing`:
   - Model returns `({}, state)` with no parameters.
   - Assert zero hindcasts stored. Assert step is still marked `success=True` (no exception).
   - Documents the accepted behavior for this edge case.

**`tests/fakes/fake_stores.py`** — filter correctness:

9. `TestFakeHindcastStoreParameterFilter`:
   - `test_parameter_none_returns_all` — store discharge + water_level hindcasts; fetch with `parameter=None`; assert all returned.
   - `test_parameter_filters_exact_match` — fetch with `parameter="discharge"`; assert only discharge returned.

**Integration tests:** `tests/integration/store/test_hindcast_store.py` has 8 existing calls to `fetch_hindcasts()`. Add integration test cases that store multi-parameter hindcasts with the same `(station_id, model_id, hindcast_step)` and verify that the SQL `WHERE parameter = ?` clause in `PgHindcastStore` returns the correct subset.

Note: normaliser shim tests are not needed — the shim was removed in favour of a direct Protocol update in Phase 0.

---

## File-Level Change Summary

| File | Change type | Phase |
|---|---|---|
| `src/sapphire_flow/protocols/forecast_model.py` | Update `predict()` and `predict_batch()` return types to multi-target form | 0A |
| `tests/fakes/fake_models.py` | Update `FakeStationForecastModel` and `FakeGroupForecastModel` to return `dict[str, ForecastEnsemble]`; add `FakeMultiTargetStationForecastModel`, `FakeMultiTargetGroupForecastModel` | 0B, 4C |
| `src/sapphire_flow/services/hindcast.py` | Update `run_station_hindcast` and `run_group_hindcast` to iterate per-parameter | 0C, 0D |
| `src/sapphire_flow/protocols/stores.py` | Add `parameter: str \| None = None` to `HindcastStore.fetch_hindcasts` | 1A |
| `src/sapphire_flow/store/hindcast_store.py` | Add `parameter` filter to `PgHindcastStore.fetch_hindcasts` | 1B |
| `tests/fakes/fake_stores.py` | Add `parameter` filter to `FakeHindcastStore.fetch_hindcasts` | 1C |
| `src/sapphire_flow/services/skill/service.py` | Add required `parameter: str` to `compute_skill_for_station`; add mismatch assertion | 2A |
| `src/sapphire_flow/flows/compute_skills.py` | Add `parameter` to flow, `_fetch_observations`, `_fetch_hindcasts`; replace two hardcoded `"discharge"` strings; add `parameter=` to `compute_skill_for_station()` call | 2B |
| `src/sapphire_flow/flows/train_models.py` | Add `parameter="discharge"` to `compute_skills_flow()` invocation (lines 302–314) | 2B |
| `src/sapphire_flow/db/metadata.py` | Add new index definition including `parameter` (keep old index definition) | 3A |
| `alembic/versions/0015_hindcast_parameter_index.py` | New migration: create new composite index (additive only; old index dropped in follow-up release) | 3A |
| `tests/unit/services/test_hindcast.py` | Add `TestMultiParameterStation`, `TestMultiParameterGroup`, `TestSingleParameterBackwardCompat`, `TestEmptyEnsembleDict` | 4C |
| `tests/unit/services/skill/test_service.py` | Add `parameter="discharge"` to all existing calls; add `TestParameterMismatch`, `TestParameterFiltering` | 4A, 4C |
| `tests/fakes/fake_stores.py` (tests) | Add `TestFakeHindcastStoreParameterFilter` | 4C |
| `docs/spec/types-and-protocols.md` | Add `parameter: str \| None = None` to `HindcastStore.fetch_hindcasts` | 1A |
| `docs/design/v0-flow678-training-pipeline.md` | Update `compute_skill_for_station` and `compute_skills_flow` signatures; note multi-parameter per-step storage | 2A, 2B |
| `docs/architecture-context.md` | Add note at Flow 7 H.6 (multi-param records), Flow 8 S.2/S.4 (parameter-scoped fetch/metrics) | 0C, 2A |

---

## Dependency Graph

```
Plan 007 (Protocol attributes aligned)
  └─ 0A (Protocol return types)
       └─ 0B (fake models)
            └─ 0C, 0D (hindcast service) ← ATOMIC with 0A+0B

1A (HindcastStore Protocol — independent, parallel with Phase 0)
  └─ 1B (PgHindcastStore)
  └─ 1C (FakeHindcastStore)

2A (compute_skill_for_station — independent of 0/1 for code correctness,
    but logically after 0 is done so multi-param hindcasts exist)
  └─ 2B (flows/compute_skills.py — two hardcoded "discharge" strings + new parameter arg)

3A (DB migration + metadata.py — independent, can run in parallel with 2)

4A (fix existing skill tests — depends on 2A)
4B (FakeHindcastStore tests — depends on 1C, no breakage)
4C (new tests — depends on 0C, 0D, 2A, 0B)
```

**Commit boundaries:**
- Phase 0 (0A+0B+0C+0D) must be a **single commit** — fake model and hindcast service changes are not independently safe.
- Phases 1, 2, 3 can be started in parallel after Phase 0.
- Phase 4 depends on 0 and 2.

**Cross-plan dependency:** Plan 004 (`SkillScore.parameter` field) must land before Phase 2 runs for any non-discharge parameter (see Open Item 1).

---

## Guardrails

- Run `uv run pytest` before starting and after each phase to catch regressions
- Run `uv run pyright` after Phase 0 to verify no type errors from the Protocol change (strict mode is enabled)
- After Phase 0 (atomic commit): all existing tests must still pass — verify that stored `HindcastForecast.ensemble` values are `ForecastEnsemble` instances, not dicts
- After Phase 1: verify `isinstance(FakeHindcastStore(), HindcastStore)` — Protocol conformance
- After Phase 3: run `alembic upgrade head` against test DB to verify migration; run `alembic downgrade -1` to verify rollback
- Version bump: `uv run bump-my-version bump patch` before committing (per CLAUDE.md)
- Do **not** run skill computation for non-discharge parameters until plan 004 lands

---

## Open Items / Follow-up Decisions

1. **`SkillScore.parameter` field** (**BLOCKER for multi-parameter skill computation**) — `SkillScore` does not carry a `parameter` field. Without it, skill scores for `discharge` and `water_level` stored with the same `(station_id, model_id, lead_time, season, metric)` are indistinguishable in the DB. Unambiguous parameter attribution is expected by WMO verification practice (WMO-1364). **Plan 004 must land before Phase 2 (skill computation) runs for any non-discharge parameter.** Plan 004 must also update `docs/spec/types-and-protocols.md` to add `parameter: str` to both `SkillScore` and `SkillDiagram` (spec is authoritative for type definitions). Until plan 004 lands, skill computation should only be invoked with `parameter="discharge"` — enforced by a temporary `NotImplementedError` guard in `compute_skills_flow` (Phase 2B).

2. **Unique constraint on hindcast natural key** — `(station_id, model_id, hindcast_run_id, hindcast_step, parameter)` uniqueness is not enforced. Should be added when idempotent re-run support becomes a requirement.

3. **Operational forecast service** — `services/forecast.py` does not yet exist. When implemented, it must consume `dict[str, ForecastEnsemble]` from the start. Covered by plan 005.

4. **`predict_batch()` input type** — Source still uses `dict[StationId, ModelInputs]` but the spec defines `GroupModelInputs`. Neither plan 007 nor this plan updates it. Deferred until a real group model implementation consumes `GroupModelInputs`.

5. **Old index cleanup** — After plan 003's code deploys and the old image is retired, a follow-up migration should drop the redundant `ix_hindcast_forecasts_station_model_step` index (see Phase 3B). Track as a post-release chore.
