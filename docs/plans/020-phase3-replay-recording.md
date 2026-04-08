---
status: DRAFT
created: 2026-04-08
scope: Phase 3 / 3b — Replay adapters + recording tool + reference dataset
depends_on: ["019"]
---

# 020 — Replay Adapters, Recording Tool, and Reference Dataset

## Problem

Integration testing of the full forecast cycle requires deterministic, repeatable
data. Currently only in-memory fakes exist. Replay adapters that serve recorded
Parquet fixtures with simulated time are needed for fast, offline integration tests.
A recording tool is needed to capture live data as fixtures.

## Scope

Three replay adapters (Steps 1, 3, 4), one CLI recording tool (Step 2), one
reference dataset (Step 5).

- Steps 1-3 implement existing adapter Protocols (Phase 3 work).
- Step 4 is a **test utility** — no Protocol exists for it (see design note).
- Step 5 is Phase 3b work (reference dataset recording).

**v0-scope.md update**: This plan uses Parquet for all fixture formats (typed,
compact, round-trip safe). v0-scope.md §E2 currently says "recorded observation
CSVs from fixtures" — update §E2 to say "recorded observation Parquet from
fixtures" when this plan moves to READY. Similarly, §E6's CLI example must be
updated to match the argument names defined here (see Step 2).

---

### Step 1: ReplayStationAdapter

**Create**:
- `src/sapphire_flow/adapters/replay/__init__.py`
- `src/sapphire_flow/adapters/replay/station.py`
- `tests/unit/adapters/test_replay_station.py`

**Implements**: `StationDataSource` Protocol (`protocols/adapters.py:27-33`)

**Method**: `fetch_observations(station_configs: list[StationConfig], since: dict[StationId, UtcDatetime]) -> list[RawObservation]`

**Design**:
- Class `ReplayStationAdapter(fixture_path: Path, simulated_time: Callable[[], UtcDatetime])`
- Reads Parquet via polars (already a dependency)
- Filters by: station codes from `station_configs` (via `StationConfig.code`),
  `timestamp >= since[station_id]`, `timestamp < simulated_time()` (half-open
  upper bound per `conventions.md`)
- Maps `station_code` -> `StationId` via `station_configs` lookup (build a
  `dict[str, StationConfig]` keyed by `.code`)
- Reconstructs `ObservationSource` enum from the Parquet `source` string column
  via `ObservationSource(value_str)`. Invalid values raise `AdapterError`.
- Constructs `RawObservation` for each row. Fields `rating_curve_id` and
  `rating_curve_correction_version` default to `None` (not stored in fixtures).

**Parquet schema** (shared with recording tool):

| Column | Type | Description |
|--------|------|-------------|
| station_code | str | External code (e.g., BAFU "2004") |
| timestamp | datetime[us, UTC] | Observation time |
| parameter | str | "discharge", "water_level", etc. |
| value | f64 | Measured value |
| source | str | `ObservationSource.value` string (e.g., "measured") |

**Error handling**:
- Missing fixture file at construction time: raise `ConfigurationError` (fail fast)
- Corrupt/unreadable Parquet: raise `AdapterError` with context
- Unknown `source` enum value: raise `AdapterError` per row (log + skip, or fail — TBD at implementation)

**Logging** (structlog, per `docs/standards/logging.md`):
- `replay.station_fetch_completed` — DEBUG, context: `record_count`, `duration_ms`, `station_count`
- `replay.station_fixture_loaded` — DEBUG, context: `fixture_path`, `total_rows`

**Tests**: Small polars DataFrames written to temp Parquet. Cases:
- Time windowing (half-open: `since <= t < simulated_time`)
- Station filtering (only configured stations returned)
- Simulated-time cutoff (future observations excluded)
- Empty fixture (returns empty list)
- `ObservationSource` round-trip (string -> enum -> `RawObservation.source`)
- Missing fixture file raises `ConfigurationError`

---

### Step 2: Recording tool

**Depends on**: Plan 019 Step 1 (`HydroScraperAdapter` must exist)

**Create**:
- `src/sapphire_flow/tools/__init__.py`
- `src/sapphire_flow/tools/record_fixtures.py`

**Note on `src/.../tools/` vs `scripts/`**: The recording tool is a package module
(`-m` invocation) because it imports from `sapphire_flow` and needs access to
adapter classes and domain types. Top-level `scripts/` is for operational scripts
that don't import the package. This follows v0-scope.md §E6.

**Design**:
- Invocable as `uv run python -m sapphire_flow.tools.record_fixtures`
- Args:
  - `--source bafu` (required; `smn` and `nwp` added in Plan 021)
  - `--stations` path to a TOML file listing station codes and metadata
    (default `tests/fixtures/reference/stations.toml`)
  - `--start` ISO 8601 date
  - `--end` ISO 8601 date
  - `--output` directory path (default `tests/fixtures/reference/`)
- For `bafu`: instantiates `HydroScraperAdapter` with endpoint from `config.toml`
  `[adapters.river_stations]` section (never from CLI args — OWASP A10 SSRF per
  Plan 019). Calls `fetch_observations`, writes Parquet using the schema above.
- Round-trip guarantee: what the tool writes, `ReplayStationAdapter` reads back
  identically.
- `smn` and `nwp` sources added in Plan 021 when those adapters exist.

**Error handling**:
- `HydroScraperAdapter.fetch_observations()` failure: log error, abort with
  non-zero exit code. Do not silently produce partial fixtures.
- Invalid `--stations` TOML: raise `ConfigurationError`, exit 1.

**Logging** (structlog, per `docs/standards/logging.md`):
- `recording.started` — INFO, context: `source`, `station_count`, `start`, `end`
- `recording.fetch_completed` — INFO, context: `source`, `record_count`, `duration_ms`
- `recording.file_written` — INFO, context: `output_path`, `row_count`, `file_size_bytes`
- `recording.failed` — ERROR, context: `source`, `error`

**Tests**: Verify recording produces valid Parquet with expected schema using a
fake adapter as input. Test the round-trip: record -> replay -> compare.

---

### Step 3: ReplayNwpAdapter

**Create**:
- `src/sapphire_flow/adapters/replay/nwp.py`
- `tests/unit/adapters/test_replay_nwp.py`

**Implements**: `WeatherForecastSource` Protocol (`protocols/adapters.py:17-23`)

**Method**: `fetch_forecasts(station_configs: list[StationWeatherSource], cycle_time: UtcDatetime) -> GriddedForecast | dict[StationId, WeatherForecastResult]`

**Design**:
- Class `ReplayNwpAdapter(fixture_dir: Path, simulated_time: Callable[[], UtcDatetime])`
- Loads Parquet fixture matching `nwp_source` and `cycle_time` from `fixture_dir`
- Returns `dict[StationId, WeatherForecastResult]` (satisfies the union return type)
- Fixture filename convention: `{nwp_source}_{cycle_time}.parquet`
  (e.g., `icon-ch2-eps_20250601T0000Z.parquet`). The `nwp_source` prefix is
  required because `StationWeatherSource.nwp_source` differentiates NWP sources
  and the adapter must select the correct fixture.
- Determines `nwp_source` from `station_configs[0].nwp_source` (all configs in a
  single call share the same source per the orchestration design)
- Filters returned stations to only those whose `station_id` appears in
  `station_configs` (uses `StationWeatherSource.station_id`, not `code`)
- Only returns data for `cycle_time <= simulated_time()` (raises `AdapterError`
  if cycle is in the future relative to simulated time)

**Error handling**:
- Missing fixture file for requested `nwp_source` + `cycle_time`: raise `AdapterError`
- Corrupt Parquet: raise `AdapterError`
- Missing fixture_dir at construction: raise `ConfigurationError`

**Logging** (structlog):
- `replay.nwp_fetch_completed` — DEBUG, context: `nwp_source`, `cycle_time`, `station_count`, `duration_ms`

**Tests**: Hand-crafted fixtures. Cases:
- Matching cycle: correct stations returned
- Missing cycle: `AdapterError` raised
- Station filtering: only requested `station_id`s returned
- Multiple `WeatherForecastResult` variants: fixture contains both `PointForecast`
  and `BasinAverageForecast` entries, adapter returns both correctly (per v0-scope.md §I1)
- Future cycle vs simulated_time: `AdapterError`

---

### Step 4: ReplayForecastInterfaceLoader (test utility)

**v0b dependency**: This step is only needed when FI-compatible ML models are
onboarded (v0-scope.md §A14). Not required for v0a.

**Create**:
- `src/sapphire_flow/adapters/replay/forecast_interface.py`
- `tests/unit/adapters/test_replay_forecast_interface.py`

**Design note**: `ForecastInterfaceAdapter` is a **concrete class**, not a Protocol.
There is no Protocol in `protocols/adapters.py` for a replay variant to implement.
This component is a **test utility** that supplies pre-recorded `ModelOutput`-shaped
data so integration tests can exercise `ForecastInterfaceAdapter.convert_output()`
without a live model run.

**Class**: `ReplayForecastInterfaceLoader(fixture_dir: Path)`

**Method**: `load_output(model_id: ModelId, station_id: StationId, cycle_time: UtcDatetime) -> ModelOutput`

**Fixture format — Parquet + JSON sidecar** (NOT pickle):

Each fixture is a directory named `{model_id}_{station_id}_{cycle_time}/` containing:
- `metadata.json` — `issue_datetime` (ISO 8601), variable names, units, statuses,
  flags, timedelta per variable
- `{variable}_trajectories.parquet` — trajectory DataFrame (if present):
  columns `issue_datetime`, `datetime`, plus integer member columns `"1"`, `"2"`, ...
- `{variable}_quantiles.parquet` — quantile DataFrame (if present):
  columns per QuantileData spec
- `{variable}_deterministic.parquet` — deterministic DataFrame (if present):
  columns `issue_datetime`, `datetime`, `value`

The loader reconstructs `ModelOutput` by reading the metadata JSON and assembling
`VariableOutput` objects with the appropriate `TrajectoryData`, `QuantileData`,
or `DeterministicData` from the Parquet files. This avoids pickle entirely —
fixtures are stable across `forecastinterface` version changes as long as the
DataFrame schemas remain compatible.

**Rationale for avoiding pickle**: `ModelOutput` is from the external
`forecastinterface` package which is under active development (v0-scope.md §A14).
Pickle is fragile across version bumps and violates the security standard's
serialization preference hierarchy (`docs/standards/security.md`). Parquet + JSON
is format-native, safe (no code execution on load), and inspectable.

**Error handling**:
- Missing fixture directory: raise `ConfigurationError`
- Missing or malformed metadata.json: raise `AdapterError`
- Missing Parquet file referenced by metadata: raise `AdapterError`

**Logging** (structlog):
- `replay.fi_load_completed` — DEBUG, context: `model_id`, `station_id`, `cycle_time`, `variable_count`, `duration_ms`

**Tests**: Serialize/deserialize round-trip.
- Create a `ModelOutput` with known trajectory data, write to fixture format,
  load back, assert equality
- Missing fixture directory raises `ConfigurationError`
- Partial fixtures (only deterministic, no trajectories): loads correctly

---

### Step 5: Reference test dataset (Phase 3b)

**Depends on**: Steps 1 + 2 + Plan 019 Step 1

**Scope**:
- Run recording tool against live BAFU LINDAS for **3-5 representative stations**
  over 3-5 days of data
- Station selection criteria: at least one with discharge + water_level, at least
  one with only discharge, geographic spread. Document which stations and why in
  `tests/fixtures/reference/README.md`.
- **Size bound**: Reference Parquet must be < 500 KB total. This is an exception
  to the `data/` gitignore policy — reference fixtures are small, versioned test
  data, not bulk downloads. Add `tests/fixtures/reference/` to `.gitignore`
  exclusion if needed.
- Store Parquet in `tests/fixtures/reference/`
- Write a test that loads via `ReplayStationAdapter`, verifies schema + produces
  valid `RawObservation` instances

**Integrity check during recording**: After recording, the recording tool logs
the total record count and date range per station. The developer visually
confirms these are reasonable (e.g., ~24 records/day/parameter for hourly data)
before committing. No automated anomaly detection in v0.

**CI placement**: The reference dataset test runs in the existing
`uv run pytest tests/unit/adapters/` gate (it is a unit test — no network, no DB).

**This component is a developer utility only** — it is never run inside a Prefect
flow or scheduled task.

---

## Exit gates

- `isinstance(ReplayStationAdapter(...), StationDataSource)` passes
- `isinstance(ReplayNwpAdapter(...), WeatherForecastSource)` passes
- `ReplayForecastInterfaceLoader` — no Protocol check (test utility); round-trip
  test passes instead
- All unit tests pass: `uv run pytest tests/unit/adapters/`
- Recording tool produces valid Parquet round-trippable by replay adapters
- Reference dataset exists in `tests/fixtures/reference/`, < 500 KB
- Type check: `uv run pyright --strict src/sapphire_flow/adapters/replay/ src/sapphire_flow/tools/record_fixtures.py`
- Lint clean: `uv run ruff check && uv run ruff format --check`
- Version bump: `uv run bump-my-version bump patch` + tag

## v0-scope.md updates required

When this plan moves to READY, update `docs/v0-scope.md`:
1. §E2: "recorded observation CSVs" → "recorded observation Parquet"
2. §E6: Update CLI example to match Step 2 argument names (`--source`, `--stations` as TOML path, `--output`)
