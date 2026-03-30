---
status: DONE
created: 2026-03-26
scope: types + protocols + services + fakes + tests
depends_on: [003]  # completed — multi-target return type already in place
phases: 1a (types/protocols), 1b (fakes), 4 (services)  # all prerequisites met via plan 003
---

# 008 — Align `predict_batch()` with `GroupModelInputs` spec

## Problem

`GroupForecastModel.predict_batch()` accepts `dict[StationId, ModelInputs]` in the source
code, but the spec (`docs/spec/types-and-protocols.md`) defines `StationInputData` (lines
1004–1010), `GroupModelInputs` (lines 1021–1042), and `GroupForecastModel.predict_batch()`
(lines 1434–1439, within the `GroupForecastModel` Protocol at lines 1427–1441) — a frozen dataclass with stacked Polars DataFrames and a `for_station()`
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
# Current (lines 57–63)
def predict_batch(
    self,
    artifact: ModelArtifact,
    inputs: dict[StationId, ModelInputs],  # ← spec says GroupModelInputs
    rng: random.Random,
) -> dict[StationId, tuple[dict[str, ForecastEnsemble], bytes | None]]: ...
```

### `src/sapphire_flow/services/hindcast.py`

`run_group_hindcast()` (starts at line 254; inner loop at lines 300–349) builds
`dict[StationId, ModelInputs]` by looping over stations and calling
`_assemble_hindcast_inputs()` per-station. This is the **only** call site for
`predict_batch()`.

### `tests/fakes/fake_models.py`

`FakeGroupForecastModel` and `FakeMultiTargetGroupForecastModel` both accept
`dict[StationId, ModelInputs]` and iterate `inputs.items()`.

### Column contracts

The spec's formal column contracts (per-station DataFrames) define `timestamp` as the first
column, followed by parameter columns with companion `{param}_provenance` columns. For stacked
`GroupModelInputs` DataFrames, `station_id` (Utf8) is prepended as the first column — this is
implied by the class docstring and inline field comments (e.g. "stacked: station_id + timestamp
+ target columns"), not by the formal column contracts section.

Stacked DataFrames in `GroupModelInputs` have:
- First column: `station_id` (Utf8) — prepended during stacking
- Second column: `timestamp` (Datetime UTC) — except for `static` which has no timestamp
- Remaining columns: one per canonical parameter name
- For each parameter column `{param}`, a companion `{param}_provenance` column (Polars Enum)
- `static`: one column per attribute name, values Float64, one row per station

Helper functions already exist in `types/model.py`: `parameter_columns()`,
`forcing_provenance_columns()`, `validate_forcing_provenance()`.

**`parameter_columns()` fix required:** The existing `parameter_columns(forcing)` helper
excludes `timestamp` and provenance columns but does **not** exclude `station_id`. If called
on a stacked `GroupModelInputs` DataFrame, it would incorrectly include `station_id` as a
parameter column. Phase 1C must update `parameter_columns()` to also exclude `"station_id"`.
This is a one-line change — add `and c != "station_id"` to the filter predicate.

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

**Note:** `stack_model_inputs()` is a plan-008 addition — it is not in the spec's
`types/model.py` inventory (spec line 2385). The spec should be updated to include it
(see File-Level Change Summary).

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

**Prerequisite (Phase 0):** `ModelInputs.forcing` must contain both lookback and
forecast-horizon rows for this split to produce non-empty `future_dynamic`. Phase 0 of this
plan extends the forcing fetch in `_assemble_hindcast_inputs()` to cover the full forecast
horizon (reanalysis as teacher forcing — see v0-scope §A13). Without Phase 0, the
`future_dynamic` DataFrame would always be empty because the current fetch ends at
`issue_time`.

**Source-agnostic split:** The `issue_time` split is agnostic to the forcing provenance.
When NWP archive hindcast is implemented (Open Item 6), the assembly function will
concatenate reanalysis (lookback) + archived NWP (horizon) into `ModelInputs.forcing`.
The same split logic separates them into `past_dynamic` / `future_dynamic` without change.

**Note:** `ModelInputs.forcing` may be a `pl.DataFrame | xr.Dataset`. For group stacking,
only `pl.DataFrame` is supported (xr.Dataset is for elevation-band models which have their
own spatial structure). A `TypeError` is raised if `xr.Dataset` is passed.

### D5: Fake models use `for_station()` internally

Updated fake group models accept `GroupModelInputs` and call `inputs.for_station(sid)` to
get per-station data. This validates the slice method in every test that uses fakes.

---

## Changes

### Phase 0 — Extend Forcing Fetch to Cover Forecast Horizon

#### Problem

`_assemble_hindcast_inputs()` (hindcast.py, starts at line 78; forcing fetch at lines 91–99) fetches forcing only up to `issue_time`:

```python
# NO-FUTURE-LEAKAGE: end=issue_time, not issue_time + horizon
raw_forcing = forcing_source.fetch_reanalysis(
    station_configs=weather_sources,
    start=lookback_start,
    end=issue_time,
    parameters=required_features,
)
```

The `NO-FUTURE-LEAKAGE` comment conflates two distinct concerns:
- **Observation leakage** (target values like discharge/water_level beyond issue_time) —
  always wrong in hindcast. The observation fetch correctly ends at `issue_time`.
- **Forcing coverage** (weather data beyond issue_time) — intentional in hindcast.
  v0-scope §A13: "future_dynamic filled from reanalysis as teacher forcing."

With the current fetch, `stack_model_inputs()` (Phase 1C) would produce an **always-empty**
`future_dynamic` DataFrame, defeating the purpose of the `past_dynamic`/`future_dynamic`
split.

#### 0A. Extend forcing fetch in `_assemble_hindcast_inputs()`

```python
# BEFORE
lookback_start = ensure_utc(issue_time - lookback_steps * time_step)
# NO-FUTURE-LEAKAGE: end=issue_time, not issue_time + horizon
raw_forcing = forcing_source.fetch_reanalysis(
    station_configs=weather_sources,
    start=lookback_start,
    end=issue_time,
    parameters=required_features,
)

# AFTER
lookback_start = ensure_utc(issue_time - lookback_steps * time_step)
horizon_end = ensure_utc(issue_time + forecast_horizon_steps * time_step)
# Observations end at issue_time (no target leakage).
# Forcing extends through the forecast horizon: reanalysis serves as
# teacher forcing in hindcast (v0-scope §A13).
raw_forcing = forcing_source.fetch_reanalysis(
    station_configs=weather_sources,
    start=lookback_start,
    end=horizon_end,
    parameters=required_features,
)
```

Remove the misleading `NO-FUTURE-LEAKAGE` comment on the forcing fetch. The observation
fetch comment (line 101) remains unchanged — observation leakage prevention is correct.

**Station-path side effect:** `_assemble_hindcast_inputs()` is a shared helper called by
both `run_station_hindcast()` (line 186) and `run_group_hindcast()` (line 310). This change
extends forcing for **both** paths simultaneously. This is intentional — the architecture
requires `future_dynamic` to be "Always present" (architecture-context.md line 1393), and
v0-scope §A13 specifies "future_dynamic filled from reanalysis as teacher forcing."

Station models receive the extended forcing in `ModelInputs.forcing` as a single unsplit
DataFrame. The station-path split into `past_dynamic` / `future_dynamic` is deferred to
the `ModelInputs` → `StationInputData` alignment (Open Item 1). Current station models
(`FakeStationForecastModel`, `LinearRegressionDaily`) do not slice forcing by timestamp —
they use `issue_time` and `forecast_horizon_steps` to generate output.

**Station-path guardrail (mandatory in 0A):** Add an inline comment at the
`run_station_hindcast()` call to `model.predict()` warning that `ModelInputs.forcing`
now contains rows beyond `issue_time` (teacher forcing) and that station models MUST NOT
read raw forcing rows past the issue_time boundary. This is a convention-only guardrail
until the `ModelInputs` → `StationInputData` alignment (Open Item 1) enforces the split
structurally. Additionally, add a regression test in `TestNoFutureLeakage` that verifies
`LinearRegressionDaily.predict()` produces identical output regardless of whether extra
future forcing rows are present — proving the current model does not use them.

**Dependencies:** None.

#### 0B. Update `TestNoFutureLeakage` in `tests/unit/services/test_hindcast.py`

The existing test (class at line 141; assertion block at lines 212–225) asserts `ts < issue_time` for **both** observations and
forcing timestamps. After 0A, forcing legitimately contains timestamps beyond `issue_time`.

Note: `TestNoFutureLeakage` uses `run_station_hindcast` (not `run_group_hindcast`). This
is the station path — Phase 0A's shared-helper change (see 0A station-path note) is what
causes this test to break. No other test in `test_hindcast.py` asserts on forcing timestamp
ranges (verified against all 9 test classes).

Update the test to:
1. **Observations** — keep `ts < issue_time` assertion (no target leakage)
2. **Forcing** — assert `ts < horizon_end` instead (forcing covers the full window)
3. Optionally assert `ts >= lookback_start` on forcing to verify the window's lower bound
4. Add a clarifying docstring distinguishing observation leakage from forcing coverage

**Dependencies:** 0A.

#### Design note: Diagnostic-only skill scores

Using reanalysis as teacher forcing (future_dynamic) in hindcast produces **diagnostic-only**
(optimistic) skill scores that overestimate real-world operational performance
(architecture-context.md line 988). This is expected for v0 — realistic skill scores require
accumulated NWP archive (v0b+, Open Item 6). Implementers and users should not interpret v0
hindcast skill scores as operational forecasting skill.

#### Design note: NWP archive hindcast compatibility

This change is forward-compatible with NWP archive hindcast (Open Item 6). When that mode
is implemented, the assembly function will concatenate reanalysis (lookback) + archived NWP
(horizon) into a single `ModelInputs.forcing` DataFrame. The `stack_model_inputs()` split
on `issue_time` separates them into `past_dynamic` / `future_dynamic` without change. The
`ForcingType` tag on `HindcastForecast` distinguishes the two modes (`REANALYSIS` vs
`NWP_ARCHIVE`).

---

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

No validation in `__post_init__` — conscious deviation from the frozen-dataclass coding
standard (CLAUDE.md). Justification: DataFrames are validated during assembly in
`stack_model_inputs()`, and re-validating in `__post_init__` would be redundant and slow
for large DataFrames.

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
        if station_id not in self.station_ids:
            msg = f"Station {station_id} not in group {self.group_id}"
            raise ValueError(msg)
        sid_str = str(station_id)
        def _filter(df: pl.DataFrame) -> pl.DataFrame:
            return df.filter(pl.col("station_id") == sid_str).drop("station_id")

        # Edge case: if self.static is not None but filtering yields zero rows
        # (station exists in station_ids but has no row in stacked static DF),
        # return None rather than an empty DataFrame. This can happen if a station
        # in the group has no basin attributes. Callers should handle static=None.
        static_filtered: pl.DataFrame | None = None
        if self.static is not None:
            sf = _filter(self.static)
            static_filtered = sf if not sf.is_empty() else None

        return StationInputData(
            past_targets=_filter(self.past_targets),
            past_dynamic=_filter(self.past_dynamic),
            future_dynamic=_filter(self.future_dynamic),
            static=static_filtered,
        )
```

**Import note:** `StationGroupId` and `StationId` are currently under `TYPE_CHECKING` in
`model.py`. With `from __future__ import annotations` (line 1), frozen dataclass field
annotations remain strings at runtime, so `TYPE_CHECKING`-only imports are sufficient.
However, if ruff flags `TC001`/`TC002`, add `# noqa` annotations following the existing
pattern (e.g., `import polars as pl  # noqa: TC002` at line 6).

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

    Boundary semantics: the row at exactly ``issue_time`` is included in
    ``past_dynamic`` (the last known state), not in ``future_dynamic``.
    This matches encoder/decoder architectures where the encoder sees up to
    and including the current time step.

    Schema contract: all stations' forcing DataFrames must have identical
    column schemas. Mismatched schemas (e.g., station A has temperature but
    station B does not) cause ``pl.concat`` to raise ``ShapeError``. This is
    intentional — stations in the same group share ``ModelDataRequirements``,
    so schema mismatches indicate a data assembly bug upstream, not a stacking
    bug. No explicit schema validation is added here; the ``pl.concat`` error
    is sufficient.
    """
    if not inputs:
        raise ValueError("Cannot stack empty inputs dict")

    station_ids = tuple(inputs.keys())
    first = next(iter(inputs.values()))

    # Verify all stations share the same metadata (caller contract)
    for sid, inp in inputs.items():
        if inp.issue_time != first.issue_time:
            raise ValueError(
                f"Inconsistent issue_time: station {sid} has {inp.issue_time}, "
                f"expected {first.issue_time}"
            )
        if inp.forecast_horizon_steps != first.forecast_horizon_steps:
            raise ValueError(
                f"Inconsistent forecast_horizon_steps: station {sid} has "
                f"{inp.forecast_horizon_steps}, expected {first.forecast_horizon_steps}"
            )
        if inp.time_step != first.time_step:
            raise ValueError(
                f"Inconsistent time_step: station {sid} has {inp.time_step}, "
                f"expected {first.time_step}"
            )

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

**Implementation note:** `_reorder_station_id_first()` should be a module-level private
helper (not an inner function) for consistency with patterns like `_raw_forcing_to_dataframe()`
in `hindcast.py`. Same for `_filter` inside `for_station()` — prefer a module-level
`_filter_station(df, sid_str)` helper.

#### 1C addendum: Update `parameter_columns()` in `types/model.py`

```python
# BEFORE
def parameter_columns(forcing: pl.DataFrame) -> list[str]:
    return [
        c
        for c in forcing.columns
        if c != "timestamp" and not c.endswith(PROVENANCE_SUFFIX)
    ]

# AFTER
def parameter_columns(forcing: pl.DataFrame) -> list[str]:
    return [
        c
        for c in forcing.columns
        if c not in ("timestamp", "station_id") and not c.endswith(PROVENANCE_SUFFIX)
    ]
```

Without this fix, calling `parameter_columns()` on a stacked `GroupModelInputs` DataFrame
would incorrectly include `station_id` as a parameter column. Safe for per-station
DataFrames (they have no `station_id` column, so the filter is a no-op).

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

# AFTER (import at module top-level, not inline)
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

1. `TestGroupModelInputs`:
   - `test_for_station_returns_correct_slice`:
     Build `GroupModelInputs` with 2 stations. Call `for_station(sid1)`. Assert returned
     `StationInputData` contains only sid1's rows. Assert `station_id` column is dropped.

   - `test_for_station_static_none`:
     Build `GroupModelInputs` with `static=None`. Assert `for_station()` returns
     `StationInputData(static=None)`.

   - `test_for_station_unknown_station_raises`:
     Call `for_station()` with a station_id not in the group. Assert
     `ValueError` with `match="not in group"`.

2. `TestStackModelInputs`:
   - `test_stack_two_stations`:
     Build 2 `ModelInputs` objects with known DataFrames. Call `stack_model_inputs()`.
     Assert stacked DataFrames have `station_id` as first column, correct row counts,
     correct past/future split on issue_time.

   - `test_stack_preserves_provenance_columns`:
     Build `ModelInputs` with provenance columns. Stack. Assert provenance columns
     survive stacking. Call `validate_forcing_provenance()` on result — assert no error.

   - `test_stack_empty_dict_raises`:
     Call `stack_model_inputs({})`. Assert `ValueError` with
     `match="Cannot stack empty inputs dict"`.

   - `test_stack_xr_dataset_raises`:
     Build `ModelInputs` with `forcing=xr.Dataset(...)`. Call `stack_model_inputs()`.
     Assert `TypeError` with `match="requires pl.DataFrame forcing"`.

   - `test_stack_inconsistent_issue_time_raises`:
     Build 2 `ModelInputs` with different `issue_time` values. Call `stack_model_inputs()`.
     Assert `ValueError` with `match="Inconsistent issue_time"`.

   - `test_roundtrip_stack_then_slice`:
     Stack 3 stations. For each station, call `for_station()`. Assert the sliced
     DataFrames match the original per-station input DataFrames (modulo column order
     and the station_id column).

**`tests/unit/services/test_hindcast.py`**:

3. `TestGroupHindcastUsesGroupModelInputs`:
   - `test_predict_batch_receives_group_model_inputs`:
     Run `run_group_hindcast` with a recording fake that stores `self.last_inputs = inputs`
     in `predict_batch()` (delegates to `FakeGroupForecastModel` for the actual return value).
     After the call, assert `isinstance(recording.last_inputs, GroupModelInputs)` in the test
     body. (Follows the `RecordingModel` pattern already established in `test_hindcast.py`.)

**Dependencies:** Phase 1 (types), Phase 3 (service), Phase 4 (fakes).

---

## File-Level Change Summary

| File | Change type | Phase |
|---|---|---|
| `src/sapphire_flow/services/hindcast.py` | Extend forcing fetch to cover forecast horizon | 0A |
| `tests/unit/services/test_hindcast.py` | Update `TestNoFutureLeakage` — forcing may exceed `issue_time` | 0B |
| `src/sapphire_flow/types/model.py` | Add `StationInputData`, `GroupModelInputs`, `stack_model_inputs()`; update `parameter_columns()` to exclude `"station_id"` | 1A, 1B, 1C |
| `src/sapphire_flow/protocols/forecast_model.py` | Update `predict_batch` input type | 2A |
| `src/sapphire_flow/services/hindcast.py` | Call `stack_model_inputs()` before `predict_batch()` + logging | 3A |
| `tests/fakes/fake_models.py` | Update `FakeGroupForecastModel`, `FakeMultiTargetGroupForecastModel` | 4A, 4B |
| `tests/unit/types/test_model.py` | New tests for `GroupModelInputs`, `stack_model_inputs()` | 5B |
| `tests/unit/services/test_hindcast.py` | Add `TestGroupHindcastUsesGroupModelInputs` | 5B |
| `docs/architecture-context.md` | Update `predict_batch()` input type at lines 109 and 1418 from `dict[StationId, ModelInputs]` to `GroupModelInputs` (these describe `predict_batch()` generically). Lines 87/105 describe Flow 1 step 1.7 output — do NOT update yet; this plan only implements stacking in the hindcast path. **Temporary inconsistency**: after this plan, lines 87/105 will still say `dict[StationId, ModelInputs]` while lines 109/1418 say `GroupModelInputs`. This is resolved when the operational forecast service plan implements group stacking in the Flow 1 path. (Plan 005 is ARCHIVED but did not implement group model stacking in the operational path.) | 2A |
| `docs/spec/types-and-protocols.md` | Add `stack_model_inputs()` to `types/model.py` module inventory (spec line 2385); extend formal column contracts section to cover stacked DataFrames in `GroupModelInputs` (`station_id` as first column); fix prose erratum at line 1465 (Open Item 8). | 2A |
| `docs/v0-scope.md` | Update §A13 to reflect that `GroupModelInputs` and `stack_model_inputs()` are implemented (was aspirational "Fully implemented"). | 2A |

---

## Dependency Graph

```
0A (extend forcing fetch)
  └─ 0B (update TestNoFutureLeakage)

1A (StationInputData type)
  └─ 1B (GroupModelInputs type + for_station())
       └─ 1C (stack_model_inputs()) — no code dependency on 0A
       └─ 2A (Protocol signature + architecture-context.md update)
            └─ 3A (hindcast service + logging)
            └─ 4A, 4B (fake models)

5B (tests — depends on 1C, 3A, 4A/4B)
```

Phase 0 is independent of Phase 1A/1B/1C (types and the stacking function have no code
dependency on the forcing fetch). However, Phase 5B's `test_stack_two_stations` assertions
about non-empty `future_dynamic` require Phase 0 to have landed — without the extended
forcing fetch, `future_dynamic` would always be empty in tests that use
`_assemble_hindcast_inputs()`.

**Commit boundaries (four commits total):**
- Phases 0A+0B: standalone commit (behavioral change to existing code, no new types).
- Phases 1A+1B+1C: one commit (pure types, no behavioral change).
- Phases 2A+3A+4A+4B: **single atomic commit** — changing the Protocol signature
  without updating the service and fakes simultaneously would break `predict_batch()` calls.
  Includes `architecture-context.md` update to keep docs consistent.
- Phase 5B: separate commit.

```json
{
  "phases": [
    {
      "id": "phase-0",
      "tasks": ["0a", "0b"],
      "parallel": false
    },
    {
      "id": "phase-1",
      "tasks": ["1a", "1b", "1c"],
      "parallel": false
    },
    {
      "id": "phase-2",
      "tasks": ["2a", "3a", "4a", "4b"],
      "parallel": false,
      "depends_on": ["phase-1"],
      "note": "3A depends on phase-0 (extended forcing fetch) at runtime, but 2A/4A/4B do not. Grouped for atomic commit."
    },
    {
      "id": "phase-3",
      "tasks": ["5b"],
      "parallel": true,
      "depends_on": ["phase-0", "phase-2"]
    }
  ]
}
```

---

## Guardrails

- Run `uv run pytest` before starting and after each phase
- After Phase 0: verify `TestNoFutureLeakage` passes with the updated assertions
- Run `uv run pyright` after Phase 2A to verify Protocol conformance
- After Phase 3A: verify `run_group_hindcast` tests still pass with the new input type
- After Phase 4: verify `isinstance(FakeGroupForecastModel(), GroupForecastModel)` — Protocol conformance
- **Version bump per commit** (four commits total — see Commit boundaries above):
  follow the full CLAUDE.md four-step sequence for each commit:
  1. `uv run bump-my-version bump patch`
  2. Stage version files alongside code changes
  3. Commit with a conventional commit message
  4. `git tag v$(uv run bump-my-version show current_version)`
- **Logging**: Wrap the `stack_model_inputs()` call in `run_group_hindcast()` with
  `time.perf_counter()` and emit an INFO-level structured log event
  `group_inputs.stacking_completed` with `group_id`, `station_count`, `issue_time`,
  `duration_ms` as `round((t1 - t0) * 1000, 1)` (mandatory per logging standard on completion events). This follows the
  `{entity}.{action}` naming convention. Single-shot event (no `_started` pair needed —
  stacking is fast and deterministic). Serialize `group_id` and `issue_time` as `str(...)`
  to match existing event field patterns in hindcast.py.

---

## What This Plan Does NOT Do

1. **Does not refactor `ModelInputs`** — `StationForecastModel.predict()` continues to use
   `ModelInputs` with its current `forcing`/`observations` field names. Aligning the
   station-scoped path with `StationInputData`'s `past_targets`/`past_dynamic`/`future_dynamic`
   decomposition is a separate, larger change. Note: `architecture-context.md` lines 1633–1643
   already describe `ModelInputs` using the 4-slot field names (`past_targets`, `past_dynamic`,
   `future_dynamic`, `static`) — the architecture doc is ahead of both the code and this plan.
   This pre-existing inconsistency is resolved when Open Item 1 lands.

2. **Does not refactor `GroupTrainingData`** — Training continues to use the current code's
   `GroupTrainingData(station_data=dict[StationId, TrainingData])`. Note: the spec defines
   `GroupTrainingData` with stacked DataFrames (spec lines 1079–1097) — the same pattern as
   `GroupModelInputs`. Aligning `GroupTrainingData` with the spec is a separate concern, not
   in scope here.

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
   `run_station_hindcast`, and all station-scoped tests. Note that `StationForecastModel.predict()`
   in the spec takes `StationModelInputs` (not `ModelInputs`) — this is the same misalignment
   on the station path that this plan fixes on the group path. **This must land before any new
   station-scoped model is implemented** — Phase 0A extends `ModelInputs.forcing` to include
   future rows (teacher forcing), and the only guardrail preventing station models from reading
   those rows is a convention comment + one regression test on `LinearRegressionDaily`. The
   structural fix (splitting `forcing` into `past_dynamic`/`future_dynamic` in `StationInputData`)
   eliminates this risk class entirely.

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

5. ~~**`v0-scope.md` §A13 update**~~ — Promoted to File-Level Change Summary. §A13
   currently says `GroupModelInputs` is "Fully implemented" — aspirational until this plan
   lands. Update §A13 after landing to reflect that `GroupModelInputs` and
   `stack_model_inputs()` are now implemented.

6. **NWP archive hindcast** — The architecture (architecture-context.md:986–990) defines two
   hindcast forcing modes: `REANALYSIS` (teacher forcing — diagnostic skill) and `NWP_ARCHIVE`
   (archived operational NWP — realistic skill). v0 only implements `REANALYSIS` mode. NWP
   archive hindcast requires:
   - A `forcing_type: ForcingType` parameter on `run_station_hindcast` / `run_group_hindcast`
     (currently hardcoded to `REANALYSIS`)
   - A `nwp_source: WeatherForecastStore | None` parameter (or composite adapter) for fetching
     archived NWP for the forecast horizon portion
   - Assembly logic: reanalysis for `[lookback_start, issue_time)` + archived NWP for
     `(issue_time, horizon_end]` → concatenated into `ModelInputs.forcing`
   - Depends on NWP archive accumulation (Flow 1 step 1.4, v0b+)
   - The `past_dynamic`/`future_dynamic` split in `stack_model_inputs()` is already
     source-agnostic and needs no change. `ForcingType` tag on `HindcastForecast`
     distinguishes the modes. Separate plan, target v0b+.

7. **Polars timezone safety in forcing split** — `stack_model_inputs()` uses
   `pl.col("timestamp") <= issue_time` where `issue_time` is a tz-aware `UtcDatetime`.
   This works if the forcing DataFrame's timestamp column is `Datetime("us", "UTC")`, which
   is the case when Python `datetime(tzinfo=UTC)` objects are passed to Polars. If a forcing
   source ever returns naive datetimes, the filter raises `ComputeError`. The `ensure_utc()`
   convention throughout the codebase mitigates this, but an explicit
   `.cast(pl.Datetime("us", "UTC"))` before the filter would make it defensive. Low priority
   — monitor during implementation.

8. ~~**Spec prose erratum**~~ — Promoted to File-Level Change Summary. `types-and-protocols.md`
   line 1465 says "StationModelInputs.station_id (accessible via `for_station()`)" — two
   errors: (a) `for_station()` returns `StationInputData`, which has no `station_id` field;
   (b) in the `predict_batch()` context the relevant type is `GroupModelInputs`, not
   `StationModelInputs`. Correct statement: ML models access station identity via
   `GroupModelInputs.station_ids` or the `station_id` argument passed to `for_station()`.

9. **ForecastInterface adapter boundary** — The `hydrosolutions/ForecastInterface` package
   defines an external contract for ML model developers (see plan 011 §A). Plan 008's
   `GroupModelInputs` is SAPPHIRE Flow's internal assembled representation; ForecastInterface
   will define the external model-facing contract. The two are complementary:
   - `GroupModelInputs.past_dynamic`/`future_dynamic` maps to ForecastInterface's
     `past_known`/`future_known` temporality axis.
   - `GroupModelInputs` has `station_id` stacking; ForecastInterface has no station concept
     — the adapter must inject/extract station identity.
   - When ForecastInterface implements its `interface/` module, SAPPHIRE Flow models would
     be thin adapters: `GroupModelInputs` → ForecastInterface input → model →
     `ModelOutput` → `dict[str, ForecastEnsemble]`.
   Plan 008's design does not prevent this — the adapter sits outside `predict_batch()`.
   See plan 011 §A for the full investigation.
