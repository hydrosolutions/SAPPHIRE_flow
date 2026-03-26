---
status: DRAFT
created: 2026-03-26
scope: source code alignment with spec field renames
depends_on: [001, 006]
---

# 007 — Align source code with spec field renames

Plans 001 and 006 updated the spec and design docs to use `data_requirements: ModelDataRequirements`
(replacing `required_features` + `required_static_attributes`) and `forecast_targets` (replacing
`forecast_target`). The Python source code still uses the old names.

Note: this plan covers only the field renames that are straightforward to apply now. The deeper
structural changes described in the Flow 13 design doc (new input/training container types,
multi-target predict return type, DB migration for `forecast_targets`) are a larger scope and
belong to the plan that implements Flow 13.

---

## Changes

### 1. Introduce `ModelDataRequirements` type

**`src/sapphire_flow/types/model.py`**
- Add `ModelDataRequirements` frozen dataclass (lines 89–99 area, before `ModelRecord`):
  ```python
  @dataclass(frozen=True, kw_only=True, slots=True)
  class ModelDataRequirements:
      target_parameters: frozenset[str]
      past_dynamic_features: frozenset[str]
      future_dynamic_features: frozenset[str]
      static_features: frozenset[str]
      supported_time_steps: frozenset[timedelta]
      lookback_steps: int
      spatial_input_type: SpatialRepresentation
  ```
- Update `ModelRegistryEntry` (lines 89–99): drop `required_features`, `required_static_attributes`,
  `spatial_input_type`, `supported_time_steps`; add `data_requirements: ModelDataRequirements`.

### 2. Update Protocol fields in `StationForecastModel` and `GroupForecastModel`

**`src/sapphire_flow/protocols/forecast_model.py`**
- Line 24–27 (`StationForecastModel`): drop `required_features`, `required_static_attributes`,
  `spatial_input_type`, `supported_time_steps`; add `data_requirements: ModelDataRequirements`.
- Line 53–56 (`GroupForecastModel`): same four field removals + same addition.
- Add `ModelDataRequirements` to the `TYPE_CHECKING` import block from `sapphire_flow.types.model`.

### 3. Rename `forecast_target` → `forecast_targets` in `StationConfig` and DB layer

**`src/sapphire_flow/types/station.py`**
- Line 33: `forecast_target: Literal["discharge", "water_level", "both"] | None`
  → `forecast_targets: frozenset[str] | None`

**`src/sapphire_flow/db/metadata.py`**
- Line 85: `sa.Column("forecast_target", sa.Text, nullable=True)`
  → `sa.Column("forecast_targets", JSONB, nullable=True)` (matches DB schema spec).

**`src/sapphire_flow/store/station_store.py`**
- Line 110: `forecast_target=station.forecast_target,` → `forecast_targets=list(station.forecast_targets) if station.forecast_targets else None,`
- Line 244: `forecast_target=row["forecast_target"],` → `forecast_targets=frozenset(row["forecast_targets"]) if row["forecast_targets"] else None,`

**`alembic/versions/`** — new migration `0014_rename_forecast_target_to_targets.py`:
- `upgrade()`: add `forecast_targets JSONB` column; backfill from `forecast_target`
  (`"discharge"` → `["discharge"]`, `"water_level"` → `["water_level"]`,
  `"both"` → `["discharge", "water_level"]`, `NULL` → `NULL`); drop `forecast_target`.
- `downgrade()`: reverse (reconstruct `forecast_target TEXT` from first element or NULL;
  drop `forecast_targets`).

### 4. Update services and adapters

**`src/sapphire_flow/services/model_registry.py`** (lines 54–64)
- Replace `required_features=model.required_features`, `required_static_attributes=model.required_static_attributes`,
  `spatial_input_type=model.spatial_input_type`, `supported_time_steps=model.supported_time_steps`
  with `data_requirements=model.data_requirements`.

**`src/sapphire_flow/services/training_data.py`**
- Line 76: `parameter = station.forecast_target or "discharge"`
  → `parameter = next(iter(station.forecast_targets), "discharge") if station.forecast_targets else "discharge"`
- Lines 98, 103, 106, 112: `model.required_features` → `model.data_requirements.past_dynamic_features`
- Lines 125–126, 137, 141: `model.required_static_attributes` → `model.data_requirements.static_features`

**`src/sapphire_flow/services/hindcast.py`**
- Line 84: parameter `required_features: list[str]` → `required_features: list[str]` (kept as local
  extracted list — extract at call site from `model.data_requirements.past_dynamic_features`)
- Line 179: `required_features = list(model.required_features)` → `required_features = list(model.data_requirements.past_dynamic_features)`
- Line 180: `parameter = station_config.forecast_target or "discharge"` → `parameter = next(iter(station_config.forecast_targets), "discharge") if station_config.forecast_targets else "discharge"`
- Line 280: `cfg.forecast_target or "discharge"` → `next(iter(cfg.forecast_targets), "discharge") if cfg.forecast_targets else "discharge"`
- Line 284: `required_features = list(model.required_features)` → `required_features = list(model.data_requirements.past_dynamic_features)`

**`src/sapphire_flow/services/onboarding.py`**
- Lines 105, 112: `existing.forecast_target` / `station.forecast_target`
  → `existing.forecast_targets` / `station.forecast_targets` (both are now `frozenset[str] | None`)
- Lines 95–113 (the `station_target` build loop): `station_target` currently maps `StationId → str`.
  With `forecast_targets: frozenset[str]`, the comment and population logic change.
  For v0 single-target QC/baseline/regime (steps 5–7), keep `station_target: dict[StationId, str]`
  by taking `next(iter(...))` from the frozenset when non-empty. Update the inline comment to
  reflect the new field name.
- Lines 164, 197, 226: log event `"station_no_forecast_target"` → `"station_no_forecast_targets"`.

**`src/sapphire_flow/adapters/camelsch_adapter.py`** (lines 163–188)
- Local variables `forecast_target = "water_level"` / `"discharge"` (lines 163, 167, 176)
  → `forecast_targets: frozenset[str] = frozenset({"water_level"})` / `frozenset({"discharge"})`
- Line 188: `forecast_target=forecast_target,` → `forecast_targets=forecast_targets,`

### 5. Update tests and fakes

**`tests/fakes/fake_models.py`**
- Lines 23–28 (`FakeStationForecastModel`): drop `required_features`, `required_static_attributes`,
  `spatial_input_type`, `supported_time_steps` class attrs; add:
  ```python
  data_requirements: ModelDataRequirements = ModelDataRequirements(
      target_parameters=frozenset({"discharge"}),
      past_dynamic_features=frozenset({"precipitation", "temperature"}),
      future_dynamic_features=frozenset(),
      static_features=frozenset(),
      supported_time_steps=frozenset({timedelta(hours=1), timedelta(hours=24)}),
      lookback_steps=720,
      spatial_input_type=SpatialRepresentation.POINT,
  )
  ```
- Lines 81–84 (`FakeGroupForecastModel`): same replacement.
- Update imports to include `ModelDataRequirements`.

**`tests/conftest.py`**
- Line 88: `forecast_target: str | None = "discharge"` → `forecast_targets: frozenset[str] | None = frozenset({"discharge"})`
- Line 110: `forecast_target=forecast_target,` → `forecast_targets=forecast_targets,`

**`tests/unit/services/test_model_registry.py`**
- Lines 28–30: assertions `entry.required_features == frozenset(...)` and
  `entry.required_static_attributes == frozenset()` and `entry.spatial_input_type == ...`
  → `entry.data_requirements.past_dynamic_features == frozenset({"precipitation", "temperature"})`
  and `entry.data_requirements.static_features == frozenset()`
  and `entry.data_requirements.spatial_input_type == SpatialRepresentation.POINT`.

**`tests/unit/services/test_hindcast.py`**
- Lines 162–165 (`RecordingModel`): drop `required_features`, `required_static_attributes`,
  `spatial_input_type`, `supported_time_steps`; add `data_requirements = FakeStationForecastModel.data_requirements`.
- Lines 256–261 (`BombModel`): same replacement.

**`tests/unit/services/test_training_pipeline.py`**
- Line 105: update comment `# Create station with no basin (model has no required_static_attributes)`
  → `# Create station with no basin (model has no static_features requirement)`

**`tests/unit/services/test_onboarding.py`**
- Line 350: `forecast_target="water_level"` → `forecast_targets=frozenset({"water_level"})`

**`tests/unit/adapters/test_camelsch_adapter.py`**
- Lines 149, 256, 265, 272: `station.forecast_target == "discharge"` / `"water_level"`
  → `station.forecast_targets == frozenset({"discharge"})` / `frozenset({"water_level"})`

**`tests/integration/store/test_station_store.py`**
- Line 81: `forecast_target=None,` → `forecast_targets=None,`

---

## Verification

- Grep for `required_features` in `src/` — zero matches (except imports of `ModelDataRequirements`)
- Grep for `required_static_attributes` in `src/` — zero matches
- Grep for `forecast_target[^s]` in `src/` — zero matches
- `uv run pytest` passes
- `uv run ruff check` clean
