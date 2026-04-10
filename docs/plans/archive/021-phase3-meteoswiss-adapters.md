---
status: DONE
created: 2026-04-08
scope: Phase 3c — MeteoSwiss NWP adapter (ICON-CH2-EPS) + GridExtractor + Zarr archive
depends_on: ["019", "020"]
---

# 021 — MeteoSwiss NWP Adapter + GridExtractor

## Problem

Operational forecasts in production (v1 Nepal onwards) require basin-averaged weather
forcing extracted from gridded NWP — matching the spatial representation used by models
trained on gridded reanalysis products. The v0 Swiss deployment is the place to build
and validate this pipeline. v0 training forcing has been switched from SMN point
observations to basin-averaged gridded data (CAMELS-CH), so both training and inference
now use consistent basin-averaged spatial representation.

The v0a/v0b split in `v0-scope.md §A11` was designed to defer GRIB2 complexity. Two
findings invalidate that deferral:

1. **Dependency simplification**: `eccodes` (v2.43+) bundles its C binary via PyPI —
   `uv add eccodes cfgrib` installs cleanly. `exactextract` (v0.3+) also ships
   pre-built wheels. This removes one obstacle from the v0a/v0b split (system-level
   dependency concern), though §A11's rationale was broader (general complexity
   reduction — no GRIB2, no xarray, no basin geometry processing).
2. **Operational readiness**: basin-average extraction from gridded NWP is the
   production-target spatial representation (v1 Nepal requires it). Implementing it
   now exercises the full Flow 1 pipeline (steps 1.1–1.4) early, validating the
   architecture before production deployment rather than deferring integration risk.

Note: v0 training forcing (Flow 6) now uses basin-averaged gridded data from CAMELS-CH
(decision changed 2026-04-08). This eliminates the former train/inference spatial
mismatch — both training and operational inference use basin-averaged forcing. §A12
must be updated to reflect this change (see v0-scope.md updates below). The NWP
parameter definitions (e.g. `aswdir_s` = direct shortwave for `radiation`) are now
authoritative for both training and inference — consistency with the former SMN
parameter mapping (`gre000h0` = global radiation) is no longer required.

This plan delivers the gridded NWP fetch, Zarr archiving, basin-average extraction,
and extraction archiving pipeline — collapsing the former v0a/v0b distinction for NWP
into a single step.

## Scope

Five steps: NWP adapter + GridExtractor + Zarr grid archive + extraction archive +
recording tool extension.

**New dependencies** (all pip-installable, no system packages):
- `eccodes` — GRIB2 codec (bundles C binary via `eccodeslib` on Linux/macOS)
- `cfgrib` — xarray engine for GRIB2
- `exactextract` — exact fractional zonal statistics (pre-built C++ wheels)
- `rioxarray` — rasterio xarray extension (required by `exactextract` for xarray
  input; provides `.rio` accessor for CRS metadata and spatial dimension setup)
- `zarr>=2.18,<3` — chunked array store (pin v2; v3 changes codec API and
  consolidated metadata format — migrate in a dedicated plan once xarray's v3
  support is stable)
- `numcodecs` — Zarr compression codecs (zstd, used with zarr v2 encoding API)

### Step 1: MeteoSwissNwpAdapter

**Create**:
- `src/sapphire_flow/adapters/meteoswiss_nwp.py`
- `tests/unit/adapters/test_meteoswiss_nwp.py`

**Implements**: `WeatherForecastSource` Protocol
(`src/sapphire_flow/protocols/adapters.py`)

**Method**:
```python
def fetch_forecasts(
    self,
    station_configs: list[StationWeatherSource],
    cycle_time: UtcDatetime,
) -> GriddedForecast:
```

Note: this adapter **always** returns `GriddedForecast` (the gridded path). The
Protocol union `GriddedForecast | dict[StationId, WeatherForecastResult]` permits this.
Basin-average extraction happens downstream in step 1.3 via `GridExtractor`, not inside
the adapter.

**Design**:

- **Data source**: MeteoSwiss STAC API (`data.geo.admin.ch`), collection
  `ch.meteoschweiz.ogd-forecasting-icon-ch2`. No authentication required (Swiss OGD
  ordinance). No secrets management needed for v0.
- **Fetch**: uses `httpx.Client` (injected). Discovers GRIB2 asset URLs from STAC
  items filtered by `cycle_time`. Downloads relevant GRIB2 files to a configured
  scratch directory (`scratch_path` from config, must be a `tmpfs` or writable volume
  per `security.md` read-only root filesystem requirement). Parameters
  needed by models: precipitation, temperature, wind speed, humidity, snow depth
  (5 parameters; radiation deferred — see parameter mapping table below).
  **STAC query pattern** (must be validated against the live API during Step 5
  recording): query items from `stac_collection` with `datetime` filter matching
  `cycle_time`. Each STAC item may contain multiple GRIB2 assets (one per parameter
  group or one combined file — file layout must be confirmed empirically). Select
  assets by media type (`application/x-grib2` or similar). Handle STAC pagination
  (`rel="next"` links) in case the collection returns paginated results. If the
  collection uses one large multi-parameter GRIB2 file per cycle, the per-variable
  `filter_by_keys` parse strategy (below) handles variable separation. If it uses
  per-parameter files, the same strategy works with single-file lists per variable.
- **Parse**: ICON-CH2-EPS stores different parameters under different GRIB2
  `typeOfLevel` keys (`heightAboveGround` for `t_2m`, `relhum_2m`, `u_10m`, `v_10m`;
  `surface` for `tp`, `sd`). A single
  `open_mfdataset(..., engine="cfgrib")` call will fail or silently drop variables.
  Strategy: open each variable group separately with explicit `filter_by_keys`, then
  merge. **Important**: since xarray ≥0.18, `filter_by_keys` is passed as a direct
  keyword argument to `open_mfdataset`, not wrapped in `backend_kwargs` (the
  `backend_kwargs` form is deprecated and may silently skip filtering in xarray 2024+):
  ```python
  PARAM_GROUPS: list[tuple[str, str]] = [
      ("tp", "surface"),
      ("t_2m", "heightAboveGround"),
      ("relhum_2m", "heightAboveGround"),
      ("u_10m", "heightAboveGround"),
      ("v_10m", "heightAboveGround"),
      ("sd", "surface"),
  ]

  datasets = []
  for short_name, type_of_level in PARAM_GROUPS:
      ds = xr.open_mfdataset(
          grib_files, engine="cfgrib",
          filter_by_keys={
              "shortName": short_name, "typeOfLevel": type_of_level,
          },
      )
      datasets.append(ds)
  merged = xr.merge(datasets)
  ```
  Note: `u_10m` and `v_10m` are separate entries in `PARAM_GROUPS` (separate GRIB2
  `shortName` values). After the merge, both are present as data variables in `merged`,
  enabling the wind speed computation `√(u² + v²)` below.
  cfgrib names the ensemble dimension `number` (from GRIB2 `perturbationNumber`).
  Rename to `member` for consistency with our types:
  `merged = merged.rename({"number": "member"})`.
  Final Dataset dimensions: `(member, valid_time, latitude, longitude)`. Weather
  parameters are data variables (standard cfgrib convention), not a `parameter`
  dimension coordinate.
- **Deaccumulation**: ICON-CH2-EPS `tp` (total precipitation) is an accumulated field.
  The adapter must compute hourly differences to produce instantaneous hourly
  precipitation in mm. **Important**: `xr.DataArray.diff()` drops the first coordinate
  (unlike `pd.Series.diff()` which inserts NaN), so a naive `.diff("valid_time")`
  produces 119 timesteps from 120, creating a shape mismatch with other variables.
  Strategy: pad a zero at T+0 before differencing to preserve the full time axis:
  `ds["precipitation"] = ds["tp"].pad({"valid_time": (1, 0)}, constant_values=0).diff("valid_time")`.
  This yields 120 timesteps with T+0 = 0 mm (zero accumulation at the reference time).
  This happens at parse time, before constructing the `GriddedForecast`.
- **`ensure_utc`**: All datetime values parsed from GRIB2 metadata and STAC responses
  pass through `ensure_utc()` before being wrapped in `UtcDatetime`.
- **`nwp_source` string**: `"icon_ch2_eps"` (underscores, matching `config.toml`). This
  string is a FK-like discriminator in `weather_forecasts` rows — must be consistent.
- **Return**: `GriddedForecast(nwp_source="icon_ch2_eps", cycle_time=cycle_time, values=ds)`
- **Cycle matching**: ICON-CH2-EPS runs 4×/day. Exact cycle hours (00Z, 06Z, 12Z, 18Z
  or 00Z, 03Z, 06Z, 12Z) to be confirmed against the STAC API before implementation —
  see `config.toml` `expected_cycles_per_day = 4`. Adapter validates `cycle_time`
  against the confirmed cycle hours.
- **24h retention**: MeteoSwiss deletes GRIB2 files 24h after publication. Adapter must
  fetch promptly. `provider_retention_days` in config must be set to `1` to reflect
  actual file availability. Gap recovery for older cycles uses the local Zarr archive
  (step 1.2), not re-fetch from the provider.
- **STAC URLs from config only**: base URL is deployment config (`config.toml`), never
  runtime-supplied — per `security.md §OWASP A10`.
- **Ensemble**: 21 members: 1 control (member 0) + 20 perturbed (members 1–20).
  Treated as a raw ensemble for v0 basin averaging; control/perturbed member
  weighting and bias correction are deferred to v1 post-processing (WMO-1254 Tier 2/3
  for bias correction; WMO-1091 §9.1.1, §10 for ensemble interpretation — per
  `wmo.md` citation conventions).
- **Partial parse policy**: if fewer than `min_operational_ensemble_size` (default 20)
  members parse successfully, raise `AdapterError`. If ≥20 members parse, proceed
  with available members and log WARNING for missing ones.

**Parameter mapping** (ICON-CH2-EPS shortnames → canonical):

| GRIB2 shortName | Canonical | Unit | Conversion |
|---|---|---|---|
| `tp` | `precipitation` | mm | Deaccumulate (hourly diff) |
| `t_2m` | `temperature` | °C | Convert from K (subtract 273.15) |
| `u_10m` / `v_10m` | `wind_speed` | m/s | Compute magnitude √(u²+v²) |
| `relhum_2m` | `humidity` | % | None |
| `sd` | `snow_depth` | cm | Convert from m (multiply by 100) |

Note: `radiation` (`aswdir_s`) is omitted from v0 NWP fetch. No current model
declares radiation as a required input. The CAMELS-CH radiation variable identity
(direct shortwave vs global radiation) is unresolved — adding `aswdir_s` later
requires confirming consistency with the training data source first. `radiation`
remains a recognized parameter in the DB seed and `conventions.md`; it is simply
not fetched or extracted until a model needs it.

Note: `cloud_cover` (`clct`) removed — not in `conventions.md` canonical parameter
table and not a CAMELS-CH forcing variable. Can be added later if models require it
(requires adding to `conventions.md` §Parameter names and DB seed data first).

Note: `radiation` (`aswdir_s`) deferred — see parameter mapping table above.

**u/v wind colocality**: `u_10m` and `v_10m` are separate GRIB2 messages with different
`shortName` values. They are listed as **separate entries** in `PARAM_GROUPS` (see
pseudocode above), each opened individually with `filter_by_keys`. After `xr.merge()`,
both are present as data variables in the merged Dataset, enabling the wind speed
computation `wind_speed = √(u² + v²)`. This merge-then-compute approach avoids relying
on cfgrib to co-locate them in a single open call.

Unit conversion happens at parse time (boundary). Internal code sees canonical units
only.

**Memory budget**: a full ICON-CH2-EPS cycle loaded simultaneously (21 members × 120
timesteps × ~360K grid cells × 5 variables × 4 bytes/float32) ≈ **18 GB**
uncompressed (exact grid cell count to be confirmed during Step 5 recording) —
exceeding typical container limits (4–8 GB). **Mitigation strategy**:
process variables one at a time in the parse phase (open → convert units →
write partial Zarr → open next variable). `xr.Dataset.to_zarr()` supports `mode="a"`
(append) for adding variables to an existing Zarr store. This keeps peak RSS to one
variable's worth of data (~1–3.5 GB for 21 members). The wind speed computation
(`u_10m` + `v_10m` → `wind_speed`) requires both components in memory simultaneously
— this pair is processed together as a single group, still within budget.
Alternative: use `xr.open_mfdataset(..., chunks={"number": 1})` for lazy dask-backed
loading (requires adding `dask[array]` as a dependency). The per-variable approach is
preferred for v0 to avoid the dask dependency.

**Error handling**:
- STAC/HTTP errors → raise `AdapterError` (triggers Prefect retry on calling
  `@task(retries=3, retry_delay_seconds=[60, 300, 900])`)
- A failed NWP fetch is a **whole-cycle failure**, not per-station. Raise
  `AdapterError`, do not swallow.
- GRIB2 parse errors → raise `AdapterError` with context (file path, shortName)
- Circuit breaker: deferred to Phase 8 (Flow 1 orchestration layer). The calling
  Prefect task's retry policy provides short-term resilience. The circuit breaker
  (5 consecutive failures → 30 min pause) belongs in the flow layer, not the adapter,
  because it tracks state across invocations — adapters are stateless.
  **`conventions.md` update required**: change circuit breaker placement from "at
  adapter level" to "in the calling flow/task" with this justification.
  **Phase 8 tracking**: the convention change and the flow-layer implementation must
  be tracked together. Phase 8's plan must include a circuit breaker exit gate to
  prevent the convention and implementation from becoming permanently decoupled.

**Logging** (per `logging.md`):
- `structlog.get_logger(__name__)`
- Context: do NOT bind `nwp_cycle_reference_time` inside the adapter (logging.md
  §Context binding protocol item 6 — services/stores inherit context, they don't bind
  it; adapters follow the same principle). The calling Prefect
  task (Phase 8) binds `nwp_cycle_reference_time` via `bind_contextvars()`; all
  adapter log events inherit it automatically.
- Events: `nwp.fetch_started` (INFO), `nwp.fetch_completed` (INFO, `duration_ms`,
  `file_count`, `total_bytes`), `nwp.fetch_failed` (**WARNING**, `error`) — WARNING
  not ERROR, because the Prefect task will retry; ERROR is reserved for post-retry
  exhaustion at the task level per `logging.md` level semantics. (Note:
  `HydroScraperAdapter` also uses WARNING for per-station fetch failures, but
  the analogy is imperfect — observation fetch is per-station degraded-but-ok,
  while NWP fetch failure is a whole-cycle failure that raises `AdapterError`.
  The WARNING level here is justified by Prefect retry semantics, not by the
  HydroScraperAdapter precedent.)
- Per-member/per-file detail: DEBUG only
- Never log response bodies or headers (may contain auth in future deployments)

**Tests**:
- Fetch with mocked STAC responses → returns `GriddedForecast` with correct dimensions
- Cycle time validation (valid cycle → ok, invalid → `AdapterError`)
- STAC API error → `AdapterError`
- GRIB2 parse error → `AdapterError` with context
- Unit conversion (K → °C for temperature, u/v → wind speed magnitude, m → cm for snow_depth)
- Deaccumulation of `tp` → hourly precipitation values, T+0 = 0 mm, output length equals input length (`.diff()` must not drop T+0)
- Member dimension named `member` (renamed from cfgrib's `number`)
- `ensure_utc` applied to all parsed timestamps
- Returned Dataset has expected dimensions `(member, valid_time, latitude, longitude)`
- Per-variable `filter_by_keys` correctly merges all 5 canonical parameters
- `nwp_source` string is `"icon_ch2_eps"` (consistent with config.toml)

### Step 2: ExactExtractGridExtractor

**Create**:
- `src/sapphire_flow/preprocessing/__init__.py`
- `src/sapphire_flow/preprocessing/exact_extract_grid_extractor.py`
- `tests/unit/preprocessing/__init__.py`
- `tests/unit/preprocessing/test_exact_extract_grid_extractor.py`

**Implements**: `GridExtractor` Protocol
(`src/sapphire_flow/protocols/grid_extractor.py`)

**Method**:
```python
def extract(
    self,
    grid: xr.Dataset,
    configs: list[StationWeatherSource],
    basins: dict[StationId, Basin],
    cycle_time: UtcDatetime,
    nwp_source: str,
) -> dict[StationId, BasinAverageForecast | ElevationBandForecast]:
```

**Design**:

- **v0 scope**: basin-average extraction only (`BasinAverageForecast`).
  Elevation-band extraction (`ElevationBandForecast`) is deferred to v1 (Nepal).
- **Tool**: `exactextract` Python library (v0.3+) — exact fractional pixel coverage
  weighting. Consistent with the methodology used in the former SAPPHIRE Data Gateway.
  The third positional parameter is `ops` (not `operations`) — use positional or
  named `ops=["mean"]`. Pin `exactextract>=0.3` for pre-built wheel support.
- **Flow**: For each station where `config.extraction_type == SpatialRepresentation.BASIN_AVERAGE`:
  1. Look up `basins[station_id].geometry` (shapely `MultiPolygon`, already onboarded
     via CamelsChAdapter; note: static type is `Any` in `Basin`, runtime is MultiPolygon).
     **Runtime validation**: guard with `isinstance(geom, (Polygon, MultiPolygon))` before
     passing to `exactextract` — a malformed geometry from the DB would otherwise produce
     a cryptic library error rather than a clear `ExtractionError`
  2. For each ensemble member, for each weather parameter:
     `exact_extract(grid[param].sel(member=m), gdf, ops=["mean"])` → basin-average
     time series
  3. Assemble into `pl.DataFrame` with columns
     `(valid_time, parameter, member_id, value)`. v0 raw NWP ensemble always uses
     `member_id` (never `quantile` — that is the alternative for post-processed
     output per `types-and-protocols.md`; the two are mutually exclusive).
  4. Apply `ensure_utc()` to all `valid_time` values when converting from xarray
     timestamps to Polars (xarray may return timezone-naive datetimes).
  5. Return `BasinAverageForecast(nwp_source=nwp_source, cycle_time=cycle_time, values=df)`
- **Optimisation**: Build GeoDataFrame of all basin geometries once per call. Use
  `exact_extract` in batch mode (all basins in one pass per variable/member) rather
  than looping per-station. This is critical for ~1000-station scale.
- **CRS handling**: ICON-CH2-EPS uses WGS84 (EPSG:4326). Basin geometries stored in
  EPSG:4326. No reprojection needed. **rioxarray setup required**: cfgrib-parsed
  DataArrays lack the CRS metadata that `exactextract` needs. Before calling
  `exact_extract`, set spatial dims and write CRS:
  `grid.rio.set_spatial_dims(x_dim="longitude", y_dim="latitude").rio.write_crs("EPSG:4326")`.
  This must happen once per `extract()` call, before the per-member/per-parameter loop.
- **Stations without basins**: if `station_id` not in `basins` dict, log WARNING and
  skip (do not fail the entire extraction).

**Error handling**:
- Missing basin geometry for a station → WARNING + skip station
- `exactextract` failure → `ExtractionError` with station context
- Empty result (no valid stations) → raise `ExtractionError`

`ExtractionError(SapphireError)` is a **new exception class** (added to
`exceptions.py` and `conventions.md`). Rationale: `AdapterError` maps to "Retry,
then fallback" per conventions.md, but extraction failures (corrupt geometry, library
bugs) are not retriable via the same retry policy. `ExtractionError` signals a
preprocessing failure distinct from HTTP/external-service errors.

**Logging**:
- `structlog.get_logger(__name__)`
- `extraction.started` (INFO, `station_count`, `parameter_count`)
- `extraction.completed` (INFO, `duration_ms`, `stations_extracted`, `stations_skipped`)
- `extraction.station_skipped` (WARNING, `station_id`, `reason`)
- Per-station extraction detail: DEBUG only

**Tests**:
- Synthetic xr.Dataset + simple polygon → correct basin-average values
- Multiple basins in batch → correct per-station results
- Ensemble members preserved (21 members in → 21 members out per station)
- Missing basin geometry → skipped with WARNING, others still extracted
- Empty basins dict → `ExtractionError`
- `BasinAverageForecast` fields correctly populated (nwp_source, cycle_time, DataFrame schema)
- `ensure_utc` applied to `valid_time` column values

### Step 3: NwpGridStore — Zarr archive (Flow 1, step 1.2)

**Create**:
- `src/sapphire_flow/store/zarr_nwp_grid_store.py` — implementation
- `tests/unit/store/test_zarr_nwp_grid_store.py`

**Update**:
- `src/sapphire_flow/protocols/stores.py` — add `NwpGridStore` Protocol (follows
  existing convention: all store Protocols live in `protocols/stores.py`)

**Design**:

Step 1.2 in Flow 1 archives the raw `GriddedForecast` before extraction. The
architecture-context.md retention table specifies "zstd-compressed GRIB2" as the hot
format. **This plan supersedes that**: Zarr replaces GRIB2 as the hot-tier archive
format because GRIB2 is sequential-access only (no chunk addressing, poor for replay
and re-extraction). Zarr with zstd compression provides comparable size with random
access. The architecture-context.md retention table entry for raw gridded NWP should
be updated to "zstd-compressed Zarr" accordingly.

**Protocol** (added to `protocols/stores.py`):
```python
@runtime_checkable
class NwpGridStore(Protocol):
    def archive(self, forecast: GriddedForecast, base_path: Path) -> Path:
        raise NotImplementedError

    def load(self, base_path: Path, nwp_source: str, cycle_time: UtcDatetime) -> GriddedForecast:
        raise NotImplementedError
```

**Design note**: `base_path` is a method parameter (not constructor-injected) so that
a single store instance can serve both hot and cold paths, and tests can point at
temp directories without subclassing. The calling flow reads `base_path` from config
and passes it through.

Naming follows existing store Protocol convention (`ForecastStore`, `AlertStore`, etc.
— no `Protocol` suffix). This enables fake injection in Flow 1 tests.

**Implementation** (`store/zarr_nwp_grid_store.py`):
```python
class ZarrNwpGridStore:
    def archive(self, forecast: GriddedForecast, base_path: Path) -> Path:
        zarr_path = base_path / f"{forecast.nwp_source}/{forecast.cycle_time:%Y%m%dT%H}.zarr"
        tmp_path = zarr_path.with_suffix(".zarr.tmp")
        old_path = zarr_path.with_suffix(".zarr.old")
        ds = forecast.values
        encoding = {
            v: {
                "chunks": (1, *ds[v].shape[1:]),  # one chunk per member
                "compressor": numcodecs.Zstd(level=3),
            }
            for v in ds.data_vars
        }
        ds.to_zarr(tmp_path, mode="w", consolidated=True, encoding=encoding)
        # Atomic swap: rename-old → rename-new → cleanup
        # Avoids TOCTOU gap where crash between rmtree and rename loses both copies
        if zarr_path.exists():
            zarr_path.rename(old_path)   # atomic on same filesystem
        tmp_path.rename(zarr_path)       # atomic on same filesystem
        if old_path.exists():
            shutil.rmtree(old_path)      # cleanup, non-critical if interrupted
        return zarr_path

    def load(self, base_path: Path, nwp_source: str, cycle_time: UtcDatetime) -> GriddedForecast:
        zarr_path = base_path / f"{nwp_source}/{cycle_time:%Y%m%dT%H}.zarr"
        if not zarr_path.exists():
            raise StoreError(f"NWP archive not found: {zarr_path}")
        ds = xr.open_zarr(zarr_path, consolidated=True)
        return GriddedForecast(nwp_source=nwp_source, cycle_time=cycle_time, values=ds)
```

**Note on dask**: the original design used `xr.Dataset.chunk()` which requires `dask`
as a runtime dependency. The zarr-native `encoding={"chunks": ...}` approach above
achieves the same per-member chunking in the Zarr store without requiring dask. This
avoids adding a heavyweight dependency. If future features (e.g. lazy out-of-core
processing) require dask, it can be added in a dedicated plan.

`StoreError(SapphireError)` is a **new exception class** (added to `exceptions.py`
and `conventions.md`). Used by `ZarrNwpGridStore` for archive-not-found and
corrupt-archive errors. Distinct from `AdapterError` (external-service) and
`ExtractionError` (preprocessing). Applicable to all store implementations that need
a domain-specific error for data retrieval failures.

**Atomicity**: three-phase swap — rename existing to `.zarr.old`, rename `.zarr.tmp`
to final path, then remove `.zarr.old`. Both renames are atomic on the same filesystem
(Docker named volume). A crash at any point leaves either the old or new archive intact,
never neither. A crash mid-write leaves only `.zarr.tmp`, which is ignored by `load()`.
A startup cleanup task should remove stale `.zarr.tmp` and `.zarr.old` directories —
this is a **documented startup step**, not optional. `Path.rename()` is only atomic
within the same filesystem; the Docker named volume guarantees this.

**Chunking**: zarr-native `encoding={"chunks": (1, *shape[1:])}` produces one chunk
per ensemble member per variable. This enables efficient per-member extraction during
replay and re-extraction without loading the full grid into memory. For a 5-day hourly
ICON-CH2-EPS Dataset (120 timesteps × 21 members × ~400×300 grid), this keeps each
chunk at ~50 MB rather than a single ~1 GB monolith per variable.

**Zarr v2**: the `numcodecs.Zstd` compressor and `consolidated=True` use the zarr v2
API. If zarr v3 is adopted later, the encoding dict must be updated to use zarr v3's
codec pipeline.

- **Compression**: zstd level 3 (good ratio, fast decompression)
- **Path convention**: `{base_path}/{nwp_source}/{cycle_time:%Y%m%dT%H}.zarr`
- **Tiered retention**: per architecture-context.md step 1.2 — hot storage for
  `weather_hot_days` (default 180), then cold, then deleted at `max_retention_days`.
  Cold path follows `cold/nwp_grids/{nwp_source}/{cycle_date}/` convention from
  architecture-context.md. Retention logic is a separate scheduled task, not this store.

**Logging**:
- `nwp.archive_started` (INFO, `zarr_path`) — Zarr archival is not sub-second for
  full ICON-CH2-EPS grids; the `_started`/`_completed` pair is needed for
  observability of slow I/O
- `nwp.archive_completed` (INFO, `duration_ms`, `zarr_path`, `size_bytes`)
- `nwp.archive_loaded` (DEBUG, `zarr_path`)
- `nwp.archive_not_found` (WARNING, `zarr_path`) — WARNING not DEBUG, because a
  missing archive is a degraded state that should be visible in production logs.
  The caller (Phase 8 flow layer) may escalate to ERROR if the missing archive is
  terminal (e.g. gap recovery exhausted)

**Tests**:
- Round-trip: archive → load → identical Dataset
- Zarr files use zstd compression
- Chunks follow `(1, *shape[1:])` strategy (one chunk per member) via zarr encoding
- Path convention followed
- No `.zarr.tmp` or `.zarr.old` directory left after successful archive
- Load non-existent path → `StoreError` (domain exception, consistent with other stores)
- Load corrupt/partial Zarr → meaningful error (not `FileNotFoundError`)

### Step 4: Extraction archive (Flow 1, step 1.4)

**Scope**: Step 1.4 archives the `dict[StationId, BasinAverageForecast]` returned by
`GridExtractor` to the `weather_forecasts` table before post-processing. This uses the
existing `WeatherForecastStore` Protocol and `PgWeatherForecastStore` implementation
(Phase 2).

**What this plan delivers**:
- `basin_avg_to_records()` conversion function (in
  `src/sapphire_flow/preprocessing/converters.py`) — needed by the integration test
  below and reused by Phase 8's flow layer
- Verify that `BasinAverageForecast.values` DataFrame schema is compatible with
  `WeatherForecastRecord` construction (columns map to record fields)
- Add an integration test: extract → convert to `WeatherForecastRecord` rows → upsert
  to `PgWeatherForecastStore` → read back and verify

**Conversion** (implemented in this plan, reused by Phase 8 flow layer):
```python
def basin_avg_to_records(
    station_id: StationId,
    forecast: BasinAverageForecast,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], uuid.UUID],
) -> list[WeatherForecastRecord]:
    """Convert BasinAverageForecast DataFrame rows to WeatherForecastRecord list."""
    ...
```

Each row in `BasinAverageForecast.values` maps to one `WeatherForecastRecord` with
`spatial_type = SpatialRepresentation.BASIN_AVERAGE` (denormalized at archival time per
architecture-context.md step 1.4). Note: `id` and `created_at` are non-optional fields
on `WeatherForecastRecord` — the conversion function must accept injected `clock` and
`id_gen` dependencies per the project's testability requirements (no `datetime.now()` or
`uuid4()` directly in domain logic). Gap fields use defaults: `is_gap=False`,
`gap_status=None`. Note: these gap recovery fields exist on `WeatherForecastRecord`
but `PgWeatherForecastStore.store_weather_forecasts()` does not currently persist them
to the DB. Setting defaults on the in-memory record is correct; the store and DB schema
will need updating when Flow 11 (gap recovery) is implemented.

### Step 5: Extend recording tool + record reference fixtures

**Depends on**: Plan 020 Step 2 (`record_fixtures.py` must exist).

**Update**: `src/sapphire_flow/tools/record_fixtures.py`

**Create**:
- `src/sapphire_flow/adapters/replay/nwp.py`
- `tests/unit/adapters/test_replay_nwp.py`

**Add to recording tool**:
- `--source nwp` — records ICON-CH2-EPS GRIB2 via `MeteoSwissNwpAdapter`, converts
  to Zarr via `ZarrNwpGridStore.archive()`, stores in `tests/fixtures/reference/nwp/`.
  Records 3–5 NWP cycles (per v0-scope.md §E1 Tier 2 requirements).
  **Source-specific arg groups**: `--source nwp` uses `--cycles N` (number of most
  recent available cycles to record) instead of `--start`/`--end` (NWP cycles are
  discrete, not date-ranged). `--stations` is **not used** for NWP recording — the
  adapter fetches the full grid; station filtering happens downstream at extraction
  time. `--output` is shared across all sources. Arg validation: `--cycles` is
  required when `--source nwp` and forbidden when `--source bafu`; `--stations`,
  `--start`, `--end` are required when `--source bafu` and forbidden when
  `--source nwp`.

**Create `ReplayNwpAdapter`** (moved from Plan 020 — see Plan 020 review, 2026-04-09):

- **Implements**: `WeatherForecastSource` Protocol (`protocols/adapters.py:17-23`)
- **Returns**: `GriddedForecast` only. No Parquet pre-extracted path — v0 only has
  gridded NWP (ICON-CH2-EPS), so the replay adapter mirrors the production adapter's
  return type. This preserves the "each adapter returns one concrete type" principle
  (`architecture-context.md:1613`). A pre-extracted replay path can be added later
  if a pre-extracted production source is onboarded.
- Class `ReplayNwpAdapter(fixture_dir: Path, grid_store: NwpGridStore)`
- `grid_store` is required (not optional) — this adapter always returns
  `GriddedForecast` via `grid_store.load()`.
- **Presence-based gating**: if no Zarr fixture exists for the requested `cycle_time`,
  the adapter raises `AdapterError`. Tests control which fixtures exist in
  `fixture_dir` to simulate temporal ordering. No `simulated_time` parameter needed
  (NWP cycles are discrete fixtures; presence/absence is the natural gate).
- Fixture path convention: `{fixture_dir}/{nwp_source}/{cycle_time:%Y%m%dT%H}.zarr`
  — matches `ZarrNwpGridStore` path convention exactly.
- Determines `nwp_source` from `station_configs[0].nwp_source`. Guard: raise
  `AdapterError` if `station_configs` is empty. Assert all configs share the same
  `nwp_source` (per orchestration design).

**Error handling**:
- Missing `fixture_dir` at construction: raise `ConfigurationError`
- Missing Zarr fixture for requested `cycle_time`: raise `AdapterError`
- Corrupt Zarr: raise `AdapterError`
- Empty `station_configs`: raise `AdapterError`

**Logging** (structlog, per `docs/standards/logging.md`):
- `nwp.fetch_completed` — DEBUG, context: `nwp_source`, `cycle_time`, `duration_ms`

**Tests**:
- Round-trip: archive Zarr fixture → replay → returns `GriddedForecast`
- Missing fixture for requested cycle: `AdapterError` raised
- Empty `station_configs`: `AdapterError` raised
- Missing `fixture_dir`: `ConfigurationError` raised

- Reference fixture path convention:
  `tests/fixtures/reference/nwp/{nwp_source}/{cycle_time:%Y%m%dT%H}.zarr`

**Record reference dataset**:
```bash
uv run python -m sapphire_flow.tools.record_fixtures \
    --source nwp \
    --cycles 5 \
    --output tests/fixtures/reference/nwp/
```

**Tests**:
- `--source nwp` produces valid Zarr fixtures
- `ReplayNwpAdapter` loads Zarr fixtures and returns `GriddedForecast`
- Recorded fixtures pass round-trip: fetch → archive → replay → extract → verify
  basin averages are non-trivial

## What this plan does NOT include

- **MeteoSwissSmnAdapter**: SMN point observations are not needed for model forcing
  (CAMELS-CH trains on basin-averaged data). SMN is needed for weather observation QC
  in Flow 2 — this will be delivered in a **separate Plan 022** scoped to Flow 2's
  weather observation ingest. The `--source smn` recording tool extension (deferred
  from Plan 020) also moves to Plan 022.
- **Elevation-band extraction**: `GridExtractor` returns `BasinAverageForecast` only.
  `ElevationBandForecast` is deferred to v1 (Nepal / ECMWF IFS).
- **NWP post-processing** (step 1.5): bias correction / ensemble calibration remain
  pass-through per v0-scope.md.
- **NWP lateness fallback**: three-stage strategy (wait → fallback cycle → skip)
  deferred. Manual monitoring suffices initially. Now that v0 has gridded NWP, this
  should be revisited before production deployment.
- **Tiered retention automation**: hot → cold → delete lifecycle is a scheduled task,
  not part of this plan. Cold path convention:
  `cold/nwp_grids/{nwp_source}/{cycle_date}/` per architecture-context.md.
- **Flow 1 orchestration wiring** (Phase 8): this plan delivers the components (adapter,
  extractor, stores); the Prefect flow that calls them is Phase 8's responsibility.
  Circuit breaker logic (5 consecutive failures → 30 min pause per `conventions.md`)
  lives in the flow layer.

**Rollback note**: the v0a/v0b collapse removes the point-only NWP path from the
default configuration, but the `WeatherForecastSource` Protocol union
(`GriddedForecast | dict[StationId, WeatherForecastResult]`) is unchanged. If the
new dependencies (`eccodes`, `cfgrib`, `exactextract`) cause CI or deployment issues,
the pre-existing point-only adapter path remains functional as a fallback — adapters
returning `dict[StationId, WeatherForecastResult]` skip steps 1.2–1.4 by design.

**Unaffected v0b references**: the v0a/v0b collapse in this plan applies **only to
NWP**. Other v0b gates in `v0-scope.md` are model-onboarding-triggered and remain
unchanged: §A4 (catchment attribute fetching), §A8d (pooled multi-model alert
strategy), §A14 (ForecastInterfaceAdapter), §G (ForecastInterfaceAdapter in types
listing), §I4 (ensemble-aware models timing). Note: §A3 receives a minor text update
(removing "v0b" qualifier from "GridExtractor subflow concurrency") but the
substantive PgBouncer deferral decision is unchanged.

**Post-collapse coherence note**: after these updates, v0-scope.md will use v0a/v0b
labels only in model-onboarding-gated sections (§A4, §A8d, §A14, §G, §I4). To prevent
reader confusion, the §A11 update should include a sentence: "The v0a/v0b distinction
for NWP is collapsed by Plan 021. Remaining v0a/v0b references in this document are
model-onboarding gates, not NWP-related."

## v0-scope.md updates required

This plan collapses the v0a/v0b split for NWP. The following sections need updating:

- **§A11**: Remove "v0a: point-only pre-extracted" simplification. v0 now starts with
  gridded ICON-CH2-EPS + GridExtractor. Steps 1.2–1.4 are active from v0 onwards.
  Rationale: (1) CAMELS-CH basin-average training data requires basin-average inference;
  (2) `eccodes`/`cfgrib`/`exactextract` are now pip-installable with bundled binaries;
  (3) `exactextract` ships pre-built wheels, removing the basin geometry processing
  obstacle. **Preserve** the Compatibility sentence ("Service and flow signatures
  accept `WeatherForecastResult` (the full union type) from day one") — it is a
  forward-compatibility rule referenced by §I1. Reword without v0a/v0b framing.
- **§A12**: **Substantive update required.** v0 training forcing has switched from SMN
  point observations to basin-averaged gridded data (CAMELS-CH). Update §A12 to:
  (1) replace "Use SMN station observations" with "Use basin-averaged forcing extracted
  from gridded data (CAMELS-CH for training, ICON-CH2-EPS for operational inference)";
  (2) note that both training and inference now use consistent basin-averaged spatial
  representation; (3) remove references to SMN as the v0 forcing source (SMN is only
  used for weather observation QC in Flow 2, not model forcing). **Preserve** the v1
  sentence ("v1: Switch to ERA5-Land via `WeatherReanalysisSource` Protocol for
  Nepal") — it remains correct. **Also update or
  supersede the `→ DECISION (plan 013)` annotation** within §A12 — it currently
  describes SMN co-location as the binding constraint. Add: "Superseded on 2026-04-08:
  training forcing switched from SMN to CAMELS-CH basin-averaged data (Plan 021). SMN
  co-location is no longer the binding constraint." Leaving the plan 013 annotation
  unchanged would create an internal contradiction within §A12. §I2 should also be
  updated — see below.
- **§E1 (Tier 2 description)**: Update "corresponding SMN weather" to "corresponding
  CAMELS-CH basin-averaged forcing" — SMN is no longer the model forcing source.
  **Cross-reference**: Plan 020 also updates §E1 Tier 2 (at Step 4 completion) to add
  the incremental-build note. These edits target different sentences and are compatible
  — whichever plan applies second must preserve the other's changes.
- **§E2**: Already updated by Plan 020 (2026-04-09). `ReplayNwpAdapter` bullet now
  says "recorded Zarr fixtures (`GriddedForecast` path only)". Lead sentence already
  generalized to adapter-specific gating. No further §E2 changes needed from this plan.
- **§A3**: Update "v0b's GridExtractor subflow concurrency" → "GridExtractor subflow
  concurrency" (GridExtractor is now active from v0, not deferred to v0b). The
  substantive PgBouncer deferral decision is unchanged.
- **§I1**: Update narrative — remove references to v0a/v0b sequencing for NWP (the
  substance of keeping union types open remains correct, only the timeline language
  is stale).
- **§I2**: Update "Keep forcing source injectable" — the example text mentions SMN
  station observations for v0 ML model lookback windows. Update to reflect that v0
  uses basin-averaged gridded forcing (CAMELS-CH for training, ICON-CH2-EPS via
  GridExtractor for inference). The injection-point design at Flow 1 step 1.7 remains
  correct; only the example source names change. **Preserve** the sentence "Nepal v1
  will use ERA5-Land via `WeatherReanalysisSource`" — it remains correct. Note: §I2
  does not use v0a/v0b labels (it already says "v0" flat) — only the substance
  (SMN → CAMELS-CH) changes.
- **Flow 1 table** (line 25): Remove "v0a: point weather forecast data (pre-extracted);
  steps 1.2, 1.3, 1.4 skipped entirely" and replace with: "Gridded NWP (ICON-CH2-EPS)
  via STAC API; steps 1.2–1.4 active from v0 onwards." **Preserve unchanged**: the
  existing text about steps 1.5/1.9 (pass-through throughout v0) and step 1.10
  (active throughout v0) must be retained.
- **Deferred table**: Update "NWP lateness fallback" row — change "v0b or v1" to "v1".
  Update trigger-condition wording: the former trigger ("when gridded NWP is added")
  has now fired. New rationale: "manual monitoring suffices for initial v0 deployment;
  **high priority** — revisit before production scaling."
- **§H (phase ladder)**: **Remove Phase 3b as a separate step** from the ladder diagram
  and add a parenthetical note on the Phase 3 line: "(includes recording tool +
  reference dataset, formerly Phase 3b — delivered across Plan 020 Step 4 (observation
  fixtures) and Plan 021 Step 5 (NWP fixtures))". Leaving Phase 3b in the diagram with
  only an annotation would give contradictory signals about whether it is a required
  future step.

## architecture-context.md updates required

- **Data retention table** (line ~2658): Update raw gridded NWP **cold format** column
  from "zstd-compressed GRIB2" to "Zarr (zstd-compressed internally)". (Note: this
  column is the cold-tier format, not hot-tier. The hot-tier stores Zarr directly at
  archival time; cold-tier receives the same Zarr directory via move — no additional
  compression step is needed because Zarr chunks are already zstd-compressed at
  archival time.)
- **Data retention table — cold archival lifecycle** (line ~2675 **and** line ~101):
  Both locations currently say "compress (zstd), move to cold path". Change **both**
  to: "move to cold path" (Zarr is already zstd-compressed internally — no separate
  compression step). **Also update the idempotency sequence** at line ~101: change
  "compress → verify → move → verify → delete hot copy" to "move → verify → delete
  hot copy" (the compression step is removed because Zarr chunks are already
  zstd-compressed at archival time; the sequence is still idempotent — a partial move
  is detected by the presence of both hot and cold copies). Also update line ~101
  hot-tier description: change "original
  format" to "Zarr (zstd-compressed internally)" and "local disk / object store" to "named volume
  (`/data/nwp_grids/`)" since the adapter fetches GRIB2 but the archive format is Zarr
  on a Docker named volume from the moment of archival. **Implementer note**: verify
  both line ~2675 and line ~101 are updated — the "compress" wording appears in two
  places.
- **Line ~101 first sentence**: Update "Archives the raw gridded NWP data (e.g. GRIB2
  files) to object storage" → "Archives the raw gridded NWP data (as Zarr) to named
  volume (`/data/nwp_grids/`)" for consistency with the step 1.2 output column update.
- **Flow 1 step 1.2 output column** (line ~82): Change "Persisted raw gridded data
  (e.g. GRIB2) to object store" → "Persisted raw gridded data (Zarr) to named volume
  (`/data/nwp_grids/`)".
- **Flow 1 step 1.3 note** (line ~102): Remove "v0a: skipped entirely (point weather
  data only — see v0-scope.md §A11). v0b+: GridExtractor on ICON-CH2-EPS." Steps
  1.2–1.4 are active from v0 onwards.
- **Flow 1 step 1.4 note**: Update to clarify `spatial_type` provenance for both
  paths: "spatial_type is denormalized from `station_weather_sources.extraction_type`.
  For gridded sources (adapters returning `GriddedForecast`), the GridExtractor
  produces the per-station result with the appropriate `spatial_type`. For
  pre-extracted sources (adapters returning `dict[StationId, WeatherForecastResult]`),
  the adapter return value directly carries `spatial_type` derived from the same
  config." This preserves the FK-chain provenance while clarifying both paths.
- **Weather forecast data flows type table** (line ~1611): The current cell lists both
  sources together ("ICON-CH2-EPS GRIB2, ECMWF IFS GRIB2"). Split into two entries or
  use a semicolon to distinguish: "ICON-CH2-EPS (fetched as GRIB2, archived as Zarr);
  ECMWF IFS GRIB2 (v1 archival strategy TBD)".
- **Post-processing pipeline table** (line ~1624): **Remove both** `GridExtractor` rows
  from the step 1.5 post-processing table: "Spatial extraction (basin-avg)" **and**
  "Spatial extraction (elevation-band)". Both are Flow 1 step 1.3 (spatial extraction /
  preprocessing), not step 1.5 post-processing transforms. The current table rows place
  them under post-processing, which is architecturally incorrect now that step 1.3 is
  concretized as a distinct pipeline stage. Also update the example pipeline immediately
  below the table (line ~1630: `GriddedForecast → [downscale] → GriddedForecast →
  [extract_basin_avg] → BasinAverageForecast`): move the `[extract_basin_avg]` stage
  out of the post-processing example and note that it is a step 1.3 operation that
  precedes step 1.5 in the Flow 1 pipeline. **Replacement example pipeline** for step
  1.5: `BasinAverageForecast → [bias_correct] → BasinAverageForecast` (step 1.5
  receives already-extracted data from step 1.3; pass-through in v0). Add a separate
  note above or below: "Step 1.3 (spatial extraction): `GriddedForecast →
  [GridExtractor] → BasinAverageForecast | ElevationBandForecast` — precedes step 1.5
  in the Flow 1 pipeline."
- **Component map `protocols/` annotation** (line ~2881): The current annotation lists
  categories ("Store, adapter, model, notification Protocols"), not individual names.
  Update to: "Store (including NwpGridStore), adapter, model, notification, grid
  extractor Protocols" — adding `NwpGridStore` as a parenthetical on the existing
  "Store" category rather than as a standalone entry.
- **`preprocessing/` directory annotation** (line ~2893): Remove "not yet implemented"
  — this plan implements `ExactExtractGridExtractor`.
- **Flow 11 (NWP gap recovery) config example** (line ~1200): `provider_retention_days = 7` → add a
  comment clarifying this is source-dependent (MeteoSwiss: 1 day, other providers may
  differ). The example value can stay at `7` as a generic default, but the comment
  must note that `config.toml` overrides per-source.
- **Flow 11 (NWP gap recovery) cold archival reference** (line ~1180): Change "compressed with zstd in
  cold" to "moved to cold path" for consistency with the Zarr archival update above.
- **Gridded spatial type description** (line ~1600): Update "(e.g. ICON-CH2-EPS GRIB2,
  ECMWF IFS)" to "(e.g. ICON-CH2-EPS, ECMWF IFS). Fetched as GRIB2, represented as
  xarray.Dataset" — the in-memory and archive format is no longer raw GRIB2.
- **`weather_forecasts` schema description** (line ~1940): Change "never raw GRIB2" to
  "never raw gridded NWP" — format-agnostic since GRIB2 is no longer the only possible
  source format.
- **Resolved: ML model lookback window forcing source** (heading line ~128, decision
  text line ~135): This section currently says "Use SMN station observations (hourly,
  1981-present)" per the plan 013 decision. Update to reflect the CAMELS-CH change: v0 training forcing is now
  basin-averaged gridded data (CAMELS-CH), not SMN point observations. Add a
  supersession note: "This decision was updated on 2026-04-08 when training forcing
  switched from SMN to CAMELS-CH basin-averaged data — see Plan 021." The `ForcingType`
  mapping example below it (line ~140, "Station observations (SMN) → categorized as
  `'reanalysis'`") should also be updated to "Basin-averaged gridded data (CAMELS-CH) →
  categorized as `'reanalysis'`". **Clarify both paths in the ForcingType mapping**:
  training uses CAMELS-CH → `'reanalysis'` (pseudo-perfect historical forcing);
  operational inference lookback uses archived NWP extractions → `'nwp_archive'`.
  Both mappings must be shown so that hindcast tagging (Flow 7 step H.2) is
  unambiguous.

## docs/standards/logging.md update required

**Existing events confirmed** (already in logging.md, used as-is by this plan):
- `nwp` entity: `nwp.fetch_started`, `nwp.fetch_completed`, `nwp.fetch_failed`

**New events on existing `nwp` entity**:
- `nwp.archive_started` (INFO), `nwp.archive_completed` (INFO),
  `nwp.archive_loaded` (DEBUG), `nwp.archive_not_found` (WARNING)

These reuse the existing `nwp` entity with more specific action verbs, avoiding
compound entity names (`nwp_archive`) which have no precedent in the logging standard.
The `archive_` prefix distinguishes them from existing `fetch_` events.

**Context binding update**: logging.md §Context binding protocol item 6 currently
names "Services/stores" but not "adapters". Since this plan applies the same principle
to adapters (they inherit context from the calling task, they do not bind it), update
item 6 to: "**Services/stores/adapters**: Do NOT bind context. They inherit context
from the calling flow/task/request."

**New `extraction` entity**:
- `extraction.started`, `extraction.completed`, `extraction.station_skipped`

**Delete** existing `nwp.extraction_completed` from the `nwp` entity row — it is
replaced by `extraction.completed` under the new `extraction` entity.

## docs/standards/wmo.md update required

- **WMO-1254 row** (line ~107): Change "v0a" to "v0" — the v0a/v0b distinction for
  NWP is collapsed by this plan.

## config.toml updates required

Add to `[adapters.weather_forecast]`:
```toml
stac_base_url = "https://data.geo.admin.ch/api/stac/v1"
stac_collection = "ch.meteoschweiz.ogd-forecasting-icon-ch2"
archive_format = "zarr"       # supplements existing `archive = true` (boolean gate);
                               # this key specifies the format when archiving is enabled
archive_base_path = "/data/nwp_grids"
scratch_path = "/tmp/sapphire_nwp"
```

Note: existing `archive = true` is the boolean gate that enables/disables NWP
archiving. `archive_format` specifies *how* to archive when `archive = true`. Both
keys coexist.

Update `[adapters.weather_forecast.monitoring]`:
```toml
provider_retention_days = 1  # MeteoSwiss deletes GRIB2 files 24h after publication
```

**Note**: `archive_base_path` uses `/data/nwp_grids` to match the hot-tier path in
architecture-context.md. `scratch_path` requires a tmpfs mount in Docker (see cicd.md
updates below). The entrypoint must `chown` both paths before dropping privileges (see
security.md updates below).

## docs/spec/config-reference.toml updates required

Add the five new `[adapters.weather_forecast]` keys (`stac_base_url`,
`stac_collection`, `archive_format`, `archive_base_path`, `scratch_path`) and update
`provider_retention_days` default to `1`.

## docs/spec/types-and-protocols.md updates required

- Add `NwpGridStore` Protocol definition (added to `protocols/stores.py`)
- **Spec bug fix**: `GriddedForecast.values` dimension comment (line 2223) currently
  says `# dimensions: time × parameter × y × x`. This is incorrect — cfgrib produces
  weather parameters as separate data variables, not a dimension coordinate. The
  original spec assumed a 4D hypercube layout that was never how cfgrib works. Change
  to `# dimensions: member × valid_time × latitude × longitude; weather parameters
  are data variables, not a dimension coordinate`. This matters for downstream
  consumers (GridExtractor, replay) that index into the Dataset.
- Clarify `BasinAverageForecast.values` column contract — the spec already documents
  `valid_time, parameter, member_id|quantile, value` (line 2241). Add a note that v0
  raw NWP ensemble always uses `member_id`; `quantile` is the alternative for
  post-processed output (mutually exclusive per row). **Do not drop the `|quantile`
  alternative** — it is needed for post-processed ensemble output in v1.
  **Forward-compatibility gap**: `WeatherForecastRecord` currently has `member_id:
  int | None` but no `quantile` field, and `PgWeatherForecastStore` only writes
  `member_id`. Before v1 post-processing is implemented, the record type will need
  a `quantile: float | None` field and a corresponding DB schema migration. This is
  not blocking for v0 (always `member_id`) but must be tracked for v1

## docs/standards/cicd.md updates required

- **Named volume**: add `nwp_grids` volume to the Named volumes table:
  `| nwp_grids | /data/nwp_grids | prefect-worker (rw, v0), prefect-worker-ops (rw, v1), prefect-worker-hindcast (ro, v1) | NWP Zarr archive hot tier | v0+ |`
  This is persistent storage for the Zarr NWP archive. Required for data to survive
  container restarts. The hindcast worker needs read access for re-extraction from
  the Zarr archive during hindcast generation (Flow 7).
- **tmpfs**: add `tmpfs: /tmp/sapphire_nwp` (size-limited, `size=4g`) on
  `prefect-worker` for GRIB2 download scratch space. 4 GB accommodates a full
  ICON-CH2-EPS cycle (5+ params × 21 members × 120 timesteps in GRIB2 packing) with
  headroom — empirical sizing should be verified against actual file sizes before
  production deployment. Dedicated mount preferred over full `/tmp` (principle of
  least privilege). Required for `read_only: true` filesystem compliance per
  security.md.

## docs/standards/security.md updates required

- **Volume permissions list**: add `/data/nwp_grids` entry — `prefect-worker` (rw in
  v0), `prefect-worker-ops` (rw in v1), `api` (no mount needed — NWP grid data is not
  served directly; extracted values are in `weather_forecasts` table).
- **Entrypoint pattern**: the entrypoint script must `chown app:app /data/nwp_grids`
  and `mkdir -p /tmp/sapphire_nwp && chown app:app /tmp/sapphire_nwp` before dropping
  to the app user. This follows the existing pattern for `/run/secrets`.
- **Confirm `cap_drop: [ALL]` is unaffected**: the new named volume and tmpfs mounts do
  not require additional Linux capabilities. Document that the security posture is
  maintained.

## docs/conventions.md updates required

- **Circuit breaker placement**: change "at adapter level" to "in the calling
  flow/task". Justification: circuit breakers track state across invocations
  (consecutive failure count, pause timer); adapters are stateless data-fetching
  components. The orchestration layer owns retry/circuit-breaker state.
  **Note**: `docs/standards/orchestration.md` does not yet document circuit breaker
  patterns — it owns flow-layer concerns but is silent on this topic. Phase 8's plan
  must include a section specifying the circuit breaker implementation (failure count
  tracking, pause duration, reset conditions) in orchestration.md. This plan's
  conventions.md change establishes *where* circuit breakers live; Phase 8 documents
  *how* they work.
  **Audit**: verify existing adapters (`HydroScraperAdapter`, `BafuStationAdapter`)
  do not contain circuit breaker logic. If they do, remove it as part of this
  convention change. Also verify that adapters raise `AdapterError` on external-service
  failures per the conventions.md exception table — `HydroScraperAdapter` currently
  catches `httpx.HTTPError, ValueError, KeyError` per-station, logs WARNING, and
  continues without raising `AdapterError` at all (it does not even import
  `AdapterError`). This is a pre-existing convention deviation. Note: for
  per-station observation fetching, warn-and-continue is arguably correct behaviour
  (one station failure should not block others), but `AdapterError` should still be
  raised after exhausting retries on the whole batch, or at minimum the deviation
  should be documented. Fix as part of this audit if scope permits, otherwise track
  for a follow-up.
- **Exception table**: add `ExtractionError` row:
  `| ExtractionError | Preprocessing/extraction failure (GridExtractor) | Log, skip station or fail cycle depending on scope |`
  This is distinct from `AdapterError` (HTTP/external-service retriable failures).
- **§Parameter names table**: Update the SMN shortname column to be explicitly labelled
  as "SMN shortname (informational)" rather than the authoritative mapping source.
  Add an "NWP shortname" column (or footnote) documenting the ICON-CH2-EPS GRIB2
  shortnames per parameter (e.g. `tp`, `t_2m`, `u_10m`/`v_10m`, `relhum_2m`, `sd`).
  Leave the `radiation` row's NWP shortname blank or mark "deferred" — `aswdir_s` is
  not fetched in v0 (see parameter mapping table in Step 1).
- **`WeatherReanalysisSource` comment** (line ~160): Update parenthetical from
  "training uses station observations" to "training uses basin-averaged gridded data
  (CAMELS-CH)" to reflect the §A12 forcing source change.
- **Exception table**: add `StoreError` row:
  `| StoreError | Store data retrieval failure (archive not found, corrupt data) | Log, raise to caller |`

## Exit gates

**Code**:
- [ ] `MeteoSwissNwpAdapter` satisfies `WeatherForecastSource` Protocol (returns `GriddedForecast`)
- [ ] cfgrib per-variable `filter_by_keys` strategy works against live ICON-CH2-EPS GRIB2
- [ ] `ExactExtractGridExtractor` satisfies `GridExtractor` Protocol; raises `ExtractionError` (not `AdapterError`)
- [ ] `ZarrNwpGridStore` satisfies `NwpGridStore`; archives and loads Zarr round-trip with atomicity (three-phase swap: old→tmp rename, tmp→final rename, old cleanup)
- [ ] `ExtractionError(SapphireError)` added to `exceptions.py` and `conventions.md`
- [ ] `StoreError(SapphireError)` added to `exceptions.py` and `conventions.md`
- [ ] Step 1.4 integration test: extraction → `WeatherForecastRecord` → `store_weather_forecasts()` → read back
- [ ] All unit tests pass
- [ ] Recording tool supports `--source nwp`
- [ ] `ReplayNwpAdapter` created in `adapters/replay/nwp.py`; satisfies `WeatherForecastSource` Protocol; returns `GriddedForecast` only (no Parquet pre-extracted path)
- [ ] `isinstance(ReplayNwpAdapter(...), WeatherForecastSource)` passes
- [ ] Reference NWP fixtures recorded (3–5 cycles) in `tests/fixtures/reference/nwp/`
- [ ] Round-trip verified: fetch → Zarr archive → replay → extract basin averages
- [ ] New dependencies added to `pyproject.toml`: `eccodes`, `cfgrib`, `exactextract`, `rioxarray`, `zarr>=2.18,<3`, `numcodecs`
- [ ] `basin_avg_to_records()` implemented in `preprocessing/converters.py` and used by Step 4 integration test
- [ ] `ExactExtractGridExtractor` validates `Basin.geometry` type at runtime (`isinstance` check → `ExtractionError` on invalid geometry)
- [ ] Docker image builds with new wheels in `python:3.11-slim` on CI runner architecture (verify `eccodes` C binary works on x86_64; ARM compatibility noted but not blocking for v0)
- [ ] Dev smoke test on macOS arm64: `uv run python -c "import cfgrib; import eccodes; import exactextract; import rioxarray"`
- [ ] Lint clean (`ruff check`, `ruff format`, `pyright --strict`)

**Performance (§D6)**:
- [ ] NWP fetch completes within 15–30s target per §D6 (instrumented with `duration_ms`)
- [ ] Zarr archival instrumented with `duration_ms` (no hard budget yet — establish baseline)
- [ ] Spatial extraction completes within 5s for ~170 stations, 5–15s for ~1000 stations (per §D6 range)
- [ ] Peak RSS during NWP parse + Zarr archival stays below 4 GB (per-variable processing strategy verified)
- [ ] ICON-CH2-EPS cycle hours confirmed against STAC API
- [ ] Actual GRIB2 cycle size measured during Step 5 recording; tmpfs `size=4g` confirmed adequate (or updated if not)

**Documentation**:
- [ ] `v0-scope.md` updated (§A11 with Compatibility sentence preserved, §A12 training+inference forcing update + plan 013 annotation superseded + v1 ERA5-Land sentence preserved, §A3, §E1 Tier 2 "corresponding SMN weather" → CAMELS-CH, §E2 lead sentence + ReplayNwpAdapter bullet, §I1, §I2 with v1 ERA5-Land sentence preserved, Flow 1 table with steps 1.5/1.9/1.10 preserved, Deferred table with urgency note, §H phase ladder Phase 3b removed from diagram)
- [ ] `architecture-context.md` updated (retention table cold format + lifecycle + idempotency sequence, step 1.2 output/note, line ~101 hot-tier storage medium + format, step 1.3 note, step 1.4 note qualified, type table, gridded spatial type line ~1600, weather_forecasts schema line ~1940, post-processing table both GridExtractor rows + replacement example pipeline, component map protocols annotation, preprocessing annotation, Flow 11 config + cold ref, ML model lookback window section ~128 superseded, ForcingType mapping ~139 with both training and inference paths)
- [ ] `docs/spec/types-and-protocols.md` updated (`NwpGridStore`, `GriddedForecast.values` dimensions, `BasinAverageForecast.values` column contract)
- [ ] `docs/spec/config-reference.toml` updated (new keys + `provider_retention_days = 1`)
- [ ] `docs/standards/logging.md` updated (new `nwp.archive_*` events on `nwp` entity + new `extraction` entity, delete `nwp.extraction_completed`, §Context binding item 6 extended to include adapters)
- [ ] `docs/standards/cicd.md` updated (`nwp_grids` volume with full table row, tmpfs `size=4g` for scratch)
- [ ] `docs/standards/security.md` updated (volume permissions for `/data/nwp_grids`, entrypoint chown, cap_drop confirmation)
- [ ] `docs/conventions.md` updated (circuit breaker placement → flow layer + adapter audit, `ExtractionError` + `StoreError` added, §Parameter names NWP shortname column + radiation deferred, `WeatherReanalysisSource` comment updated)
- [ ] `docs/standards/wmo.md` updated (WMO-1254 row: "v0a" → "v0")
- [ ] `config.toml` updated with STAC/archive/scratch keys and `provider_retention_days = 1`

- [ ] Phase 3 NWP pipeline complete: production adapter + GridExtractor + Zarr archive + extraction archive + replay
