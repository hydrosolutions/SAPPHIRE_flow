---
status: DRAFT
created: 2026-03-26
scope: types + protocols + services + fakes + tests
depends_on: [003]  # Multi-target predict_batch return type must be in place
---

# 008 — Align `predict_batch()` with `GroupModelInputs` spec

## Problem

`GroupForecastModel.predict_batch()` accepts `dict[StationId, ModelInputs]` in the source
code, but the spec (`docs/spec/types-and-protocols.md` lines 1003–1024, 1410–1443) defines
`GroupModelInputs` — a frozen dataclass with stacked Polars DataFrames and a `for_station()`
slice method.

This is not just a type wrapper. `GroupModelInputs` stacks all stations' data into single
DataFrames with a `station_id` column prepended, enabling ML models (LSTM, transformer) to
operate on the full group batch natively. The current `dict[StationId, ModelInputs]` forces
models to iterate per-station, which is correct for conceptual models but suboptimal for ML.

**Urgency:** Medium. No group model exists in v0a (station-scoped only). This must land before
the first ML group model is implemented (v0b).

---

## Current State Inventory

### `docs/spec/types-and-protocols.md`

Defines three types not yet in code:

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class StationInputData:
    past_targets: pl.DataFrame       # timestamp + target columns (lookback window)
    past_dynamic: pl.DataFrame       # timestamp + dynamic forcing columns (lookback window)
    future_dynamic: pl.DataFrame     # timestamp + dynamic forcing columns (forecast horizon)
    static: pl.DataFrame | None      # single-row scalar catchment attributes; None if not needed

@dataclass(frozen=True, kw_only=True, slots=True)
class GroupModelInputs:
    group_id: StationGroupId
    station_ids: tuple[StationId, ...]
    past_targets: pl.DataFrame       # stacked: station_id + timestamp + target columns
    past_dynamic: pl.DataFrame       # stacked: station_id + timestamp + dynamic columns
    future_dynamic: pl.DataFrame     # stacked: station_id + timestamp + dynamic columns
    static: pl.DataFrame | None      # stacked: station_id + attribute columns; None if not needed
    issue_time: UtcDatetime
    forecast_horizon_steps: int
    time_step: timedelta

    def for_station(self, station_id: StationId) -> StationInputData:
        """Slice stacked DataFrames for one station."""
        ...
```

### `src/sapphire_flow/types/model.py`

Has `ModelInputs` (per-station, uses `forcing: pl.DataFrame | xr.Dataset` and
`observations: pl.DataFrame`), `TrainingData`, `GroupTrainingData`. Does **not** have
`StationInputData` or `GroupModelInputs`.

Note: `ModelInputs` uses different field names (`forcing`, `observations`) than
`StationInputData` (`past_targets`, `past_dynamic`, `future_dynamic`). The spec's
`StationInputData` is a more granular decomposition — targets separated from dynamic
forcing, past separated from future. `ModelInputs` conflates these. This plan does NOT
refactor `ModelInputs` to match `StationInputData` — that is a larger change that affects
`StationForecastModel.predict()` and all station-scoped code. This plan only adds
`GroupModelInputs` for the group path.

### `src/sapphire_flow/protocols/forecast_model.py`

```python
# Current (line 57–62)
def predict_batch(
    self,
    artifact: ModelArtifact,
    inputs: dict[StationId, ModelInputs],  # ← spec says GroupModelInputs
    rng: random.Random,
) -> dict[StationId, tuple[dict[str, ForecastEnsemble], bytes | None]]: ...
```

### `src/sapphire_flow/services/hindcast.py`

`run_group_hindcast()` (lines 300–349) builds `dict[StationId, ModelInputs]` by looping
over stations and calling `_assemble_hindcast_inputs()` per-station. This is the **only**
call site for `predict_batch()`.

### `tests/fakes/fake_models.py`

`FakeGroupForecastModel` and `FakeMultiTargetGroupForecastModel` both accept
`dict[StationId, ModelInputs]` and iterate `inputs.items()`.

### Column contracts (from spec)

Stacked DataFrames in `GroupModelInputs` must have:
- First column: `station_id` (Utf8)
- Second column: `timestamp` (Datetime UTC) — except for `static` which has no timestamp
- Remaining columns: one per canonical parameter name
- For each parameter column `{param}`, a companion `{param}_provenance` column (Polars Enum)
- `static`: one column per attribute name, values Float64, one row per station

Helper functions already exist in `types/model.py`: `parameter_columns()`,
`forcing_provenance_columns()`, `validate_forcing_provenance()`.

---

## Design Decisions

### D1: `GroupModelInputs` constructed from per-station `ModelInputs`

The stacking step converts `dict[StationId, ModelInputs]` → `GroupModelInputs`. This keeps
the per-station assembly logic (`_assemble_hindcast_inputs`) unchanged — it already works and
is well-tested. The new code only stacks the results.

**Alternative rejected:** Assemble stacked DataFrames directly (bypass per-station assembly).
This would require rewriting the input preparation pipeline — high risk, low value for v0.

### D2: Stacking is a pure function in `types/model.py`

The stacking logic (`stack_model_inputs()`) belongs in `types/model.py` alongside the type
definitions and existing DataFrame helpers. It is a data transformation, not a service — no
I/O, no store access, no side effects.

**Alternative rejected:** `services/data_stacking.py` — a new module for one function is
unnecessary. The function operates on the types defined in the same file.

### D3: `for_station()` filters and drops `station_id` column

`GroupModelInputs.for_station(station_id)`:
1. Filters each DataFrame to rows where `station_id` column matches
2. Drops the `station_id` column from the result
3. Returns `StationInputData`

For `static` (no timestamp): filters to the row for that station, drops `station_id`.

### D4: `ModelInputs` field mapping to `StationInputData` fields

`ModelInputs` has `forcing` and `observations`; `StationInputData` has `past_targets`,
`past_dynamic`, `future_dynamic`. The stacking function must map between these:

- `past_targets` ← `observations` (target parameter columns from the observation DataFrame)
- `past_dynamic` ← `forcing` (the lookback portion — rows where timestamp ≤ issue_time)
- `future_dynamic` ← `forcing` (the forecast portion — rows where timestamp > issue_time)
- `static` ← `static_attributes`

This mapping happens inside `stack_model_inputs()`. The split between past/future forcing
uses `issue_time` as the boundary.

**Note:** `ModelInputs.forcing` may be a `pl.DataFrame | xr.Dataset`. For group stacking,
only `pl.DataFrame` is supported (xr.Dataset is for elevation-band models which have their
own spatial structure). A `TypeError` is raised if `xr.Dataset` is passed.

### D5: Fake models use `for_station()` internally

Updated fake group models accept `GroupModelInputs` and call `inputs.for_station(sid)` to
get per-station data. This validates the slice method in every test that uses fakes.

---

## Changes

### Phase 1 — Type Definitions

#### 1A. Add `StationInputData` to `types/model.py`

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class StationInputData:
    past_targets: pl.DataFrame
    past_dynamic: pl.DataFrame
    future_dynamic: pl.DataFrame
    static: pl.DataFrame | None
```

No validation in `__post_init__` — the DataFrames are already validated during assembly.

**Dependencies:** None.

#### 1B. Add `GroupModelInputs` to `types/model.py`

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class GroupModelInputs:
    group_id: StationGroupId
    station_ids: tuple[StationId, ...]
    past_targets: pl.DataFrame
    past_dynamic: pl.DataFrame
    future_dynamic: pl.DataFrame
    static: pl.DataFrame | None
    issue_time: UtcDatetime
    forecast_horizon_steps: int
    time_step: timedelta

    def for_station(self, station_id: StationId) -> StationInputData:
        sid_str = str(station_id)
        def _filter(df: pl.DataFrame) -> pl.DataFrame:
            return df.filter(pl.col("station_id") == sid_str).drop("station_id")

        return StationInputData(
            past_targets=_filter(self.past_targets),
            past_dynamic=_filter(self.past_dynamic),
            future_dynamic=_filter(self.future_dynamic),
            static=_filter(self.static) if self.static is not None else None,
        )
```

**Dependencies:** 1A.

#### 1C. Add `stack_model_inputs()` to `types/model.py`

Pure function that converts `dict[StationId, ModelInputs]` → `GroupModelInputs`:

```python
def stack_model_inputs(
    group_id: StationGroupId,
    inputs: dict[StationId, ModelInputs],
    issue_time: UtcDatetime,
) -> GroupModelInputs:
    """Stack per-station ModelInputs into a single GroupModelInputs.

    Adds a ``station_id`` (Utf8) column as the first column of each DataFrame.
    Splits ``ModelInputs.forcing`` into past_dynamic (≤ issue_time) and
    future_dynamic (> issue_time) based on the timestamp column.
    Maps ``ModelInputs.observations`` → past_targets.
    """
    if not inputs:
        raise ValueError("Cannot stack empty inputs dict")

    station_ids = tuple(inputs.keys())
    first = next(iter(inputs.values()))

    past_targets_parts: list[pl.DataFrame] = []
    past_dynamic_parts: list[pl.DataFrame] = []
    future_dynamic_parts: list[pl.DataFrame] = []
    static_parts: list[pl.DataFrame] = []

    for sid, inp in inputs.items():
        if isinstance(inp.forcing, pl.DataFrame):
            forcing = inp.forcing
        else:
            raise TypeError(
                f"GroupModelInputs stacking requires pl.DataFrame forcing, "
                f"got {type(inp.forcing).__name__} for station {sid}"
            )

        sid_col = pl.lit(str(sid)).alias("station_id")

        # Split forcing into past/future on issue_time boundary
        past = forcing.filter(pl.col("timestamp") <= issue_time)
        future = forcing.filter(pl.col("timestamp") > issue_time)

        past_dynamic_parts.append(past.with_columns(sid_col))
        future_dynamic_parts.append(future.with_columns(sid_col))

        # Observations → past_targets
        past_targets_parts.append(inp.observations.with_columns(sid_col))

        # Static attributes
        if inp.static_attributes is not None:
            static_parts.append(inp.static_attributes.with_columns(sid_col))

    def _reorder_station_id_first(df: pl.DataFrame) -> pl.DataFrame:
        cols = ["station_id"] + [c for c in df.columns if c != "station_id"]
        return df.select(cols)

    return GroupModelInputs(
        group_id=group_id,
        station_ids=station_ids,
        past_targets=_reorder_station_id_first(pl.concat(past_targets_parts)),
        past_dynamic=_reorder_station_id_first(pl.concat(past_dynamic_parts)),
        future_dynamic=_reorder_station_id_first(pl.concat(future_dynamic_parts)),
        static=_reorder_station_id_first(pl.concat(static_parts)) if static_parts else None,
        issue_time=first.issue_time,
        forecast_horizon_steps=first.forecast_horizon_steps,
        time_step=first.time_step,
    )
```

This is the only piece of new data logic in the plan. It is a pure function with no I/O.

**Dependencies:** 1A, 1B.

---

### Phase 2 — Protocol Update

#### 2A. Update `GroupForecastModel.predict_batch` signature

```python
# BEFORE
def predict_batch(
    self,
    artifact: ModelArtifact,
    inputs: dict[StationId, ModelInputs],
    rng: random.Random,
) -> dict[StationId, tuple[dict[str, ForecastEnsemble], bytes | None]]: ...

# AFTER
def predict_batch(
    self,
    artifact: ModelArtifact,
    inputs: GroupModelInputs,
    rng: random.Random,
) -> dict[StationId, tuple[dict[str, ForecastEnsemble], bytes | None]]: ...
```

Add `from sapphire_flow.types.model import GroupModelInputs` to imports (or TYPE_CHECKING).

**Dependencies:** 1B.

---

### Phase 3 — Service Layer

#### 3A. Update `run_group_hindcast()` in `services/hindcast.py`

After the existing per-station assembly loop (which builds `inputs_batch: dict[StationId, ModelInputs]`),
add the stacking call before `predict_batch()`:

```python
# BEFORE (line ~345)
batch_results = model.predict_batch(
    artifact=artifact,
    inputs=inputs_batch,
    rng=rng,
)

# AFTER
from sapphire_flow.types.model import stack_model_inputs

group_inputs = stack_model_inputs(
    group_id=group.id,
    inputs=inputs_batch,
    issue_time=issue_time,
)
batch_results = model.predict_batch(
    artifact=artifact,
    inputs=group_inputs,
    rng=rng,
)
```

The per-station `_assemble_hindcast_inputs()` loop is unchanged. Only the final
`predict_batch()` call changes its input.

**Dependencies:** 1C, 2A.

---

### Phase 4 — Fake Models

#### 4A. Update `FakeGroupForecastModel`

Change signature to accept `GroupModelInputs`. Use `for_station()` internally:

```python
def predict_batch(
    self,
    artifact: ModelArtifact,
    inputs: GroupModelInputs,              # ← changed
    rng: random.Random,
) -> dict[StationId, tuple[dict[str, ForecastEnsemble], bytes | None]]:
    result = {}
    for sid in inputs.station_ids:         # ← iterate station_ids
        station_data = inputs.for_station(sid)  # ← use for_station()
        rows = []
        for step in range(inputs.forecast_horizon_steps):
            # ... create ensemble values ...
        result[sid] = ({self.parameter: ens}, None)
    return result
```

#### 4B. Update `FakeMultiTargetGroupForecastModel`

Same pattern — accept `GroupModelInputs`, use `for_station()`.

**Dependencies:** 1B, 2A.

---

### Phase 5 — Tests

#### 5A. Tests that break without changes

| Test file | Why it breaks | Fix |
|---|---|---|
| `tests/unit/services/test_hindcast.py` — group hindcast tests | `predict_batch` now receives `GroupModelInputs` instead of dict | Fakes updated in Phase 4; no test code changes needed if fakes are correct |

#### 5B. New tests

**`tests/unit/types/test_model.py`** (new file — does not exist yet):

1. `TestStationInputData` — `test_construction`:
   - Build a `StationInputData` with sample DataFrames. Assert fields accessible.

2. `TestGroupModelInputs`:
   - `test_for_station_returns_correct_slice`:
     Build `GroupModelInputs` with 2 stations. Call `for_station(sid1)`. Assert returned
     `StationInputData` contains only sid1's rows. Assert `station_id` column is dropped.

   - `test_for_station_static_none`:
     Build `GroupModelInputs` with `static=None`. Assert `for_station()` returns
     `StationInputData(static=None)`.

3. `TestStackModelInputs`:
   - `test_stack_two_stations`:
     Build 2 `ModelInputs` objects with known DataFrames. Call `stack_model_inputs()`.
     Assert stacked DataFrames have `station_id` as first column, correct row counts,
     correct past/future split on issue_time.

   - `test_stack_preserves_provenance_columns`:
     Build `ModelInputs` with provenance columns. Stack. Assert provenance columns
     survive stacking. Call `validate_forcing_provenance()` on result — assert no error.

   - `test_stack_empty_dict_raises`:
     Call `stack_model_inputs({})`. Assert `ValueError`.

   - `test_stack_xr_dataset_raises`:
     Build `ModelInputs` with `forcing=xr.Dataset(...)`. Call `stack_model_inputs()`.
     Assert `TypeError`.

   - `test_roundtrip_stack_then_slice`:
     Stack 3 stations. For each station, call `for_station()`. Assert the sliced
     DataFrames match the original per-station input DataFrames (modulo column order
     and the station_id column).

**`tests/unit/services/test_hindcast.py`**:

4. `TestGroupHindcastUsesGroupModelInputs`:
   - `test_predict_batch_receives_group_model_inputs`:
     Run `run_group_hindcast` with a spy fake that asserts `isinstance(inputs, GroupModelInputs)`.
     Assert the assertion holds.

**Dependencies:** Phase 1 (types), Phase 3 (service), Phase 4 (fakes).

---

## File-Level Change Summary

| File | Change type | Phase |
|---|---|---|
| `src/sapphire_flow/types/model.py` | Add `StationInputData`, `GroupModelInputs`, `stack_model_inputs()` | 1A, 1B, 1C |
| `src/sapphire_flow/protocols/forecast_model.py` | Update `predict_batch` input type | 2A |
| `src/sapphire_flow/services/hindcast.py` | Call `stack_model_inputs()` before `predict_batch()` | 3A |
| `tests/fakes/fake_models.py` | Update `FakeGroupForecastModel`, `FakeMultiTargetGroupForecastModel` | 4A, 4B |
| `tests/unit/types/test_model.py` | New tests for `StationInputData`, `GroupModelInputs`, `stack_model_inputs()` | 5B |
| `tests/unit/services/test_hindcast.py` | Add `TestGroupHindcastUsesGroupModelInputs` | 5B |
| `docs/spec/types-and-protocols.md` | No changes needed — spec already correct | — |

---

## Dependency Graph

```
1A (StationInputData type)
  └─ 1B (GroupModelInputs type + for_station())
       └─ 1C (stack_model_inputs())
       └─ 2A (Protocol signature)
            └─ 3A (hindcast service)
            └─ 4A, 4B (fake models)

5B (tests — depends on 1C, 3A, 4A/4B)
```

All phases are sequential — each depends on the previous.

**Commit boundaries:**
- Phases 1A+1B+1C can be one commit (pure types, no behavioral change).
- Phases 2A+3A+4A+4B must be a **single atomic commit** — changing the Protocol signature
  without updating the service and fakes simultaneously would break `predict_batch()` calls.
- Phase 5B can follow in a separate commit.

---

## Guardrails

- Run `uv run pytest` before starting and after each phase
- Run `uv run pyright` after Phase 2A to verify Protocol conformance
- After Phase 3A: verify `run_group_hindcast` tests still pass with the new input type
- After Phase 4: verify `isinstance(FakeGroupForecastModel(), GroupForecastModel)` — Protocol conformance
- Version bump: `uv run bump-my-version bump patch` before committing (per CLAUDE.md)

---

## What This Plan Does NOT Do

1. **Does not refactor `ModelInputs`** — `StationForecastModel.predict()` continues to use
   `ModelInputs` with its current `forcing`/`observations` field names. Aligning the
   station-scoped path with `StationInputData`'s `past_targets`/`past_dynamic`/`future_dynamic`
   decomposition is a separate, larger change.

2. **Does not refactor `GroupTrainingData`** — Training continues to use
   `GroupTrainingData(station_data=dict[StationId, TrainingData])`. Stacking training data
   is a separate concern for when training needs batch-native input.

3. **Does not change the operational forecast path (Flow 1)** — Flow 1 step 1.7/1.8
   does not yet exist. When plan 005 (operational forecast service) is implemented, it
   should construct `GroupModelInputs` using `stack_model_inputs()` from the start.

4. **Does not add `__post_init__` validation** — The stacked DataFrames are assembled by
   `stack_model_inputs()` which produces correct output by construction. Adding column
   validation in `__post_init__` would be redundant and slow for large DataFrames.

---

## Open Items

1. **`ModelInputs` → `StationInputData` alignment** — The station-scoped path still uses
   `ModelInputs` with `forcing: pl.DataFrame | xr.Dataset` and `observations: pl.DataFrame`.
   The spec's `StationInputData` decomposes these into `past_targets`, `past_dynamic`,
   `future_dynamic`. Aligning these is a broader refactor that touches `StationForecastModel`,
   `run_station_hindcast`, and all station-scoped tests. Recommend a separate plan when
   station-scoped models need the decomposed input.

2. **Elevation-band group models** — `stack_model_inputs()` raises `TypeError` for
   `xr.Dataset` forcing. Elevation-band group models may need a different stacking strategy
   (e.g., stacking along a band dimension). Deferred until an elevation-band group model exists.

3. **Performance** — `pl.concat()` creates a new DataFrame by copying. For very large groups
   (100+ stations × 365-day lookback), this could be expensive. Polars lazy frames or
   chunked iteration could help. Deferred until profiling shows it matters.

4. **`StationModelInputs` wrapper** — The spec also defines `StationModelInputs` (a wrapper
   with `station_id`, `data: StationInputData`, and metadata fields like `issue_time`,
   `forecast_horizon_steps`, `time_step`). This plan adds `StationInputData` (the data
   payload) but not the wrapper. The wrapper is only needed when
   `StationForecastModel.predict()` is refactored to use the decomposed input — deferred
   to the `ModelInputs` alignment plan (Open Item 1).
