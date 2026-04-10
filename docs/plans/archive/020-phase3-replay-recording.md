---
status: DONE
created: 2026-04-08
scope: Phase 3 / 3b — Station replay adapter + recording tool + reference dataset
depends_on: ["019"]
---

# 020 — Replay Adapters, Recording Tool, and Reference Dataset

## Problem

Integration testing of the full forecast cycle requires deterministic, repeatable
data. Currently only in-memory fakes exist. Replay adapters that serve recorded
Parquet fixtures from fixture directories are needed for fast, offline integration
tests. A recording tool is needed to capture live data as fixtures.

## Scope

One replay adapter (Step 1), one CLI recording tool (Step 2), one
test utility (Step 3, **v0b only**), one reference dataset (Step 4).

- Step 1 implements an existing adapter Protocol (Phase 3 work).
- **NWP replay moved to Plan 021**: `ReplayNwpAdapter` (Zarr-based,
  returning `GriddedForecast`) is owned entirely by Plan 021 Step 5. Rationale:
  v0 only has gridded NWP (ICON-CH2-EPS); a Parquet pre-extracted replay path
  would test a code path that doesn't exist in production. One format (Zarr),
  one adapter, matching the "each adapter returns one concrete type" principle
  (`architecture-context.md:1613`).
- Step 3 is a **test utility** — no Protocol exists for it (see design note).
  **Only needed for v0b** when FI-compatible ML models are onboarded (§A14).
- Step 4 is Phase 3b work (reference dataset recording). Delivers the
  **observation-only portion** of Tier 2 (§E1); NWP fixtures are deferred to
  Plan 021, SMN weather fixtures to Plan 022.

**v0-scope.md update**: This plan uses Parquet for observation fixture formats
(typed, compact, round-trip safe). §E2 has been updated (GRIB2/ dropped,
`ReplayNwpAdapter` moved to Plan 021). §E6 CLI example already matches
Step 2 argument names.

---

### Step 1: ReplayStationAdapter

**Create**:
- `src/sapphire_flow/adapters/replay/__init__.py`
- `src/sapphire_flow/adapters/replay/station.py`
- `tests/unit/adapters/test_replay_station.py`

**Convention note**: `adapters/replay/` is a subpackage, deviating from the flat-file
`adapters/{type}.py` convention (`conventions.md:158`). Justified because replay
adapters are a cohesive group sharing fixture conventions; Plan 021 will add
`adapters/replay/nwp.py` to the same subpackage. Precedent: `adapters/forecast_interface/`.

**Implements**: `StationDataSource` Protocol (`protocols/adapters.py:27-33`)

**Method**: `fetch_observations(station_configs: list[StationConfig], since: dict[StationId, UtcDatetime]) -> list[RawObservation]`

**Design**:
- Class `ReplayStationAdapter(fixture_path: Path, simulated_time: Callable[[], UtcDatetime])`
- Reads Parquet via polars (already a dependency)
- Filters by: station codes from `station_configs` (via `StationConfig.code`),
  `timestamp >= since[station_id]`, `timestamp < simulated_time()` (inclusive
  lower bound matches `HydroScraperAdapter` SPARQL semantics — store layer
  deduplicates on upsert; exclusive upper bound for simulated-time cutoff)
- Maps `station_code` -> `StationId` via `station_configs` lookup (build a
  `dict[str, StationConfig]` keyed by `.code`)
- Reconstructs `ObservationSource` enum from the Parquet `source` string column
  via `ObservationSource(value_str)` (value-based lookup — `ObservationSource` is
  `Enum`, not `StrEnum`, so use the call syntax, not subscript). Invalid values
  raise `AdapterError`.
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
- Unknown `source` enum value: raise `AdapterError` (fail fast — per `conventions.md`
  adapter error convention; fixture data should never contain unknown enum values)

**Logging** (structlog, per `docs/standards/logging.md`):
- `station.fetch_completed` — DEBUG, context: `record_count`, `duration_ms`, `station_count`
- `fixture.loaded` — DEBUG, context: `fixture_path`, `total_rows`, `duration_ms`
  (uses `fixture` entity, not `station` — this is a replay/test-tooling event, not
  a production operational event)

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
  - `--source bafu` (required; `nwp` added in Plan 021, `smn` in Plan 022)
  - `--stations` path to a TOML file listing station codes and metadata
    (default `tests/fixtures/reference/stations.toml` — this file is created by
    Step 4; if running the recording tool before Step 4, `--stations` must be
    provided explicitly). **TOML schema**: each `[[stations]]` entry must
    provide all fields needed to construct a `StationConfig` (`code`, `name`,
    `location` as `{lon, lat}`, `station_kind`, `timezone`, `network`,
    `ownership`, `measured_parameters`, etc.). The recording tool
    auto-generates a deterministic `StationId` (UUID5 from namespace + code)
    for each entry — UUIDs are ephemeral (needed to satisfy the
    `StationDataSource` Protocol) and are not persisted to Parquet. Fields
    with `None`-compatible types (`basin_id`, `regulation_type`,
    `forecast_targets`, `wigos_id`) default to `None` if omitted from TOML;
    `station_status` defaults to `ACTIVE`; `gauging_status` defaults to
    `GAUGED`; `created_at` / `updated_at` are set to the current time. Step 4 creates the reference `stations.toml`
    with real metadata for the selected BAFU stations.
  - `--start` ISO 8601 date
  - `--end` ISO 8601 date
  - `--output` directory path (default `tests/fixtures/reference/`)
- For `bafu`: instantiates `HydroScraperAdapter` with the endpoint from `config.toml`
  `[adapters.river_stations]` section (never from CLI args — OWASP A10 SSRF per
  Plan 019) and an `httpx.Client` constructed with an explicit timeout
  (e.g. `httpx.Client(timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0))`).
  **Config parsing**: `load_config()` strips the `[adapters]` subtree
  (`DeploymentConfig` has no adapter fields). The recording tool reads `config.toml`
  directly via `tomllib.load()` and accesses
  `data["adapters"]["river_stations"]["endpoint"]`. This is intentional: the
  recording tool is a developer CLI utility, not a Prefect flow — it does not need
  `DeploymentConfig`. Wrap access in a
  `try/except (KeyError, FileNotFoundError, tomllib.TOMLDecodeError)` and raise
  `ConfigurationError` with context.
  The recording tool owns the `httpx.Client` lifecycle: it creates the client as a
  context manager wrapping the entire recording session, passes it to
  `HydroScraperAdapter`, and closes it on exit. The adapter does not close the client.
  Constructs the `since` dict by mapping every
  station to `--start`: `since = {cfg.id: start_dt for cfg in station_configs}`
  (the Protocol requires `dict[StationId, UtcDatetime]`, not a single datetime).
  Calls `fetch_observations(station_configs, since)`, then filters the returned
  observations to `timestamp < --end` (post-fetch filter — the adapter Protocol
  has no `end` parameter; the upper bound is enforced by the recording tool, not
  the adapter).
- Round-trip guarantee: what the tool writes, `ReplayStationAdapter` reads back
  identically.
- `nwp` source added in Plan 021; `smn` source added in Plan 022.
- **Logging configuration**: The recording tool is a CLI process, not a Prefect
  flow or FastAPI app. It calls `configure_cli_logging()` at startup — a new
  function added to the logging module alongside the existing `configure_*`
  functions. **Implementation**: refactor `_apply_structlog_config()` signature from
  `(processors, config_level)` to `(processors, config_level, renderer=None)`.
  When `renderer` is `None` (the default), the function keeps its current
  `SAPPHIRE_ENV`-based renderer selection — **existing callers
  (`configure_prefect_logging`, `configure_api_logging`) are unaffected** (they
  continue to pass only `processors` and `config_level`). When `renderer` is
  provided, it is used directly. `configure_cli_logging()` builds its own processor
  list (shared processors, **no** Prefect context processor — the Prefect processor
  is part of the processor chain, not controlled by the renderer parameter) and
  calls `_apply_structlog_config(processors, "INFO", renderer=ConsoleRenderer())`.
  `cache_logger_on_first_use` stays `True` (unlike `configure_test_logging()` which
  uses `False` and does its own inline `structlog.configure()` — deliberately not
  refactored, because it needs `cache_logger_on_first_use=False` and
  `ProcessorFormatter` plumbing that differs from the `_apply_structlog_config`
  path). **Note**: CLI tools always use console renderer regardless of
  `SAPPHIRE_ENV` because they are interactive developer utilities with
  human-readable output, not monitored services feeding log aggregators.

**Error handling**:
- `HydroScraperAdapter.fetch_observations()` failure: log error, abort with
  non-zero exit code. Do not silently produce partial fixtures.
- Invalid `--stations` TOML: raise `ConfigurationError`, exit 1.

**Logging** (structlog, per `docs/standards/logging.md`):
- `fixture.recording_started` — INFO, context: `source`, `station_count`, `start_date`, `end_date`
- `fixture.fetch_completed` — INFO, context: `source`, `record_count`, `duration_ms`
- `fixture.file_written` — INFO, context: `output_path`, `row_count`, `file_size_bytes`
- `fixture.recording_failed` — ERROR, context: `source`, `error`

**Tests**: Verify recording produces valid Parquet with expected schema using
`FakeStationDataSource` from `tests/fakes/fake_adapters.py` as input. Test
the round-trip: record -> replay -> compare. Test `--end` post-fetch filtering.

---

### Step 3: ReplayForecastInterfaceLoader (test utility)

**v0b dependency**: This step is only needed when FI-compatible ML models are
onboarded (v0-scope.md §A14). Not required for v0a.

**Create**:
- `tests/fakes/replay_forecast_interface.py`
- `tests/unit/adapters/test_replay_forecast_interface.py`

**Design note**: `ForecastInterfaceAdapter` is a **concrete class**, not a Protocol.
There is no Protocol in `protocols/adapters.py` for a replay variant to implement.
This component is a **test utility** that supplies pre-recorded `ModelOutput`-shaped
data so integration tests can exercise `ForecastInterfaceAdapter.convert_output()`
without a live model run. It lives in `tests/fakes/` (not `src/.../adapters/`)
because it implements no adapter Protocol — placing it under `adapters/` would
violate the convention that adapter modules satisfy corresponding Protocols.
`tests/fakes/` is the canonical home for test utilities (alongside `FakeStationDataSource`,
`FakeWeatherForecastSource`, etc.).

**Class**: `ReplayForecastInterfaceLoader(fixture_dir: Path)`

**Method**: `load_output(model_id: ModelId, station_id: StationId, cycle_time: UtcDatetime) -> ModelOutput`
(`ModelId` is from `sapphire_flow.types.ids`, not from `forecastinterface`.)

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
- `{variable}_epistemic.parquet` — epistemic uncertainty DataFrame (if present):
  columns `issue_datetime`, `datetime`, `std`, `range`

The loader reconstructs `ModelOutput` by reading the metadata JSON and assembling
`VariableOutput` objects with the appropriate `TrajectoryData`, `QuantileData`,
`DeterministicData`, and/or `EpistemicUncertaintyData` from the Parquet files.
This avoids pickle entirely — fixtures are stable across `forecastinterface` version
changes as long as the DataFrame schemas remain compatible.

**Rationale for avoiding pickle**: `ModelOutput` is from the external
`forecastinterface` package which is under active development (v0-scope.md §A14).
Pickle is fragile across version bumps and violates the security standard's
serialization preference hierarchy (`docs/standards/security.md`; that hierarchy
is scoped to model artifacts, applied here by analogy to fixture data). Parquet + JSON
is format-native, safe (no code execution on load), and inspectable.

**Error handling**:
- Missing fixture directory: raise `ConfigurationError`
- Missing or malformed metadata.json: raise `AdapterError`
- Missing Parquet file referenced by metadata: raise `AdapterError`

**Logging** (structlog, per `docs/standards/logging.md`):
- `model.output_loaded` — DEBUG, context: `model_id`, `station_id`, `cycle_time`, `variable_count`, `duration_ms`

**Tests**: Serialize/deserialize round-trip.
- Create a `ModelOutput` with known trajectory data, write to fixture format,
  load back, assert equality
- Missing fixture directory raises `ConfigurationError`
- Partial fixtures (only deterministic, no trajectories): loads correctly

---

### Step 4: Reference test dataset (Phase 3b)

**Depends on**: Steps 1 + 2 + Plan 019 Step 1

**Scope**:
- Run recording tool against live BAFU LINDAS for **5-10 representative stations**
  over 3-5 days of data. This delivers the **observation portion** of Tier 2
  (§E1) as a starting point — station count matches, but the time range (3-5
  days) is minimal (~0.7% of §E1's "2 years hourly discharge" spec). §E1
  presents Tier 2 as a single complete dataset; Plan 020 begins building it
  incrementally — the §E1 update in the "v0-scope.md updates" section below
  documents this incremental approach. The full Tier 2
  dataset is built incrementally: observation time range extended when CAMELS-CH
  integration is complete, NWP fixtures added by Plan 021, SMN weather
  observations (for Flow 2 QC, not model forcing) by Plan 022.
  "Known edge cases baked in" (§E1) and "known-correct golden answers" are **not
  addressed** by Plan 020 — edge cases require deliberate scenario design (assign
  ownership in a future plan); golden answers require forecast outputs (deferred to
  Phase 8)
- Station selection criteria: at least one with discharge + water_level, at least
  one with only discharge, geographic spread. Document which stations and why in
  `tests/fixtures/reference/README.md`.
- **Size bound** (self-imposed implementation constraint, not from §E1): Reference
  Parquet must be < 500 KB total. This covers only the observation portion of
  Tier 2; the full Tier 2 dataset (~50-100 MB including NWP cycles, SMN weather
  observations, and multi-year data per §E1) is built up by later plans (021+). Reference
  fixtures are small, versioned test data — `data/` is gitignored but
  `tests/fixtures/` is not, so no `.gitignore` changes are needed.
- Store Parquet in `tests/fixtures/reference/`
- Write a test that loads via `ReplayStationAdapter`, verifies schema + produces
  valid `RawObservation` instances

**Integrity check during recording**: After recording, the recording tool logs
the total record count and date range per station. The developer visually
confirms these are reasonable (e.g., ~24 records/day/parameter for hourly data)
before committing. No automated anomaly detection in v0.

**CI placement**: The reference dataset test runs in the existing
`uv run pytest tests/unit/` gate. Although `docs/architecture-context.md` classifies
adapter tests as Integration (VCR-style recorded responses), this test has no I/O
dependencies — no network, no DB — so it is classified as unit.

**This component is a developer utility only** — it is never run inside a Prefect
flow or scheduled task.

---

## Exit gates

**Code**:
- `isinstance(ReplayStationAdapter(...), StationDataSource)` passes
- **[v0b only]** `ReplayForecastInterfaceLoader` — no Protocol check (test utility); round-trip
  test passes instead
- All unit tests pass: `uv run pytest tests/unit/`
- Recording tool produces valid Parquet round-trippable by `ReplayStationAdapter`
- Reference dataset exists in `tests/fixtures/reference/`, < 500 KB
- Type check: `uv run pyright --strict src/sapphire_flow/adapters/replay/ src/sapphire_flow/tools/record_fixtures.py`
- Lint clean: `uv run ruff check && uv run ruff format --check`
- Version bump: `uv run bump-my-version bump patch` + tag

**Documentation** (required — per workflow.md "every code change updates affected docs"):
- `docs/v0-scope.md` §E2 updated (`ReplayNwpAdapter` moved to Plan 021) —
  already applied (2026-04-09)
- `docs/v0-scope.md` §E1 updated: Tier 2 built incrementally across Plans
  020–022 (observation seed → NWP fixtures → SMN weather). Golden answers
  deferred to Phase 8. (Apply at Step 4 completion.)
- `docs/v0-scope.md` §E3 updated: `ReplayStationAdapter` satisfies the
  observation-data dependency; full scenario execution still requires
  `ReplayNwpAdapter` (Plan 021) and forecast cycle (Phase 8). (Apply at
  Step 4 completion.)
- `docs/standards/logging.md` updated: `configure_cli_logging()` added (with
  `_apply_structlog_config()` renderer parameter refactor), `fixture`
  entity added, `station` entity events updated
- `tests/fixtures/reference/README.md` created (Step 4 — station selection rationale)
- `docs/architecture-context.md` updated:
  - Component map: add `tools/` entry (Step 2 creates `src/sapphire_flow/tools/`,
    absent from the current map)
  - Layering rule: add `tools/` — "CLI utilities — may import from `adapters/`,
    `config/`, `types/`, and `protocols/`; may not import from `services/`, `store/`,
    or `flows/`". Also update the layering **diagram** to include `tools/` as a
    peer entry point alongside `flows/` and `api/`.
  - Test layer mapping table: add exception for replay fixture tests — classified as
    unit (no I/O: no network, no DB) despite living under `adapters/` (which the
    table currently classifies as Integration)
- `docs/standards/security.md` updated:
  - A10 SSRF entry: extend from "NWP source URLs" to cover all adapter endpoint
    URLs (NWP + river station). After Plan 020, both are config-restricted.

## v0-scope.md updates required

1. ~~§E2: Update `ReplayNwpAdapter` bullet — moved to Plan 021 (Zarr-based,
   `GriddedForecast` only).~~ **Applied** (2026-04-09).
2. §E1 Tier 2: Add a note that Tier 2 is built incrementally across Plans 020–022
   (Plan 020 delivers the observation-only seed; NWP fixtures added by Plan 021;
   SMN weather observations added by Plan 022). **Cross-reference**: Plan 021 also
   updates §E1 Tier 2 (SMN → CAMELS-CH forcing source change). These edits target
   different sentences — whichever plan applies second must preserve the other's
   changes. Also note that "known-correct golden answers
   for regression testing" (§E1 Tier 2) are not addressed by Plan 020 — golden
   answers require forecast outputs to compare against, which are produced by the
   forecast cycle; defer to Phase 8 (forecast cycle integration). "Known edge
   cases baked in" (§E1 Tier 2) requires deliberate scenario design — not addressed
   by Plans 020–022; assign ownership in a future plan (likely Phase 8 or a
   dedicated test-data plan). Apply when Step 4 is implemented.
3. §E3: Add a note that `ReplayStationAdapter` (Plan 020) unblocks station-data
   dependencies for §E3 scenarios ("Normal cycle", "Missing observations", "Full
   onboarding → forecast"), but full scenario execution remains blocked on
   `ReplayNwpAdapter` (Plan 021) and forecast cycle implementation (Phase 8).
   Apply when Step 4 is implemented.

**Already applied** (no action needed):
- §E2 already says "recorded observation Parquet" (not CSVs)
- §E6 CLI example already matches Step 2 argument names

## docs/standards/logging.md update required

- Add `configure_cli_logging()` alongside existing `configure_*` functions. Refactor
  `_apply_structlog_config()` to accept an optional `renderer` parameter; the CLI
  function passes `ConsoleRenderer()` explicitly. INFO level, no Prefect context
  processor. Used by the recording tool and future CLI utilities.
- Document that `configure_test_logging()` is **intentionally excluded** from the
  `_apply_structlog_config()` refactor: it requires `cache_logger_on_first_use=False`
  and its own `ProcessorFormatter` plumbing, which differ from the shared path.
  This prevents future implementers from "fixing" the divergence.
- **New entity**: add `fixture` to the entity/example events table:
  `| fixture | loaded, recording_started, fetch_completed, file_written, recording_failed |`
  This entity covers the recording tool, replay adapters, and future fixture
  management utilities. `fixture.loaded` is used by `ReplayStationAdapter` (replay/
  test-tooling event — does not belong under the `station` production entity).
- **Existing entity update**: add `fetch_completed` to the `station` entity's
  example events (currently only `onboarding_started`, `status_changed`). This is
  used by `ReplayStationAdapter` for the actual observation fetch event (a
  station-domain action, unlike fixture loading which is tooling infrastructure).

---

## Dependency graph

```json
{
  "phases": [
    {
      "id": "replay-adapter-and-recording",
      "tasks": ["step-1", "step-2"],
      "parallel": true,
      "note": "Step 1 (ReplayStationAdapter) and Step 2 (recording tool) are independent. Step 2 depends on Plan 019 Step 1 (HydroScraperAdapter must exist)."
    },
    {
      "id": "test-utility",
      "tasks": ["step-3"],
      "parallel": false,
      "depends_on": [],
      "note": "v0b only — independent of all other steps"
    },
    {
      "id": "reference-dataset",
      "tasks": ["step-4"],
      "parallel": false,
      "depends_on": ["replay-adapter-and-recording"],
      "note": "Step 4 depends on Steps 1 + 2 + Plan 019 Step 1"
    }
  ]
}
```
