---
status: DRAFT
created: 2026-06-25
plan: 081
title: recap-dg-client forcing adapter
scope: Nepal v1 adapter foundation only
depends_on: []
split_with: 082-recap-gateway-operational-readiness
---

# Plan 081 - recap-dg-client forcing adapter

## Revision Log

- **2026-06-25 adversarial-review revision:** split the original broad Plan 081
  into this offline-completable forcing-adapter plan and Plan 082 for live
  operational/training readiness. This revision clears review BLOCKERs 1-4,
  MAJORs 5-7, and the recorded omissions while preserving the validated
  contract-fit analysis, endpoint-provenance design, type-driven adapter design,
  coverage-as-blocker stance, and dependency-distribution discipline.
- **Ensemble control resolved (2026-06-25):** confirmed with the project/hydrology
  lead (Beatrice Marti, `marti@hydrosolutions`) against ECMWF product docs that the
  ENS control (`cf`) has been **discontinued and replaced by HRES `fc`**. HRES `fc`
  is therefore the current ECMWF deterministic control, so `member_id=0` = `fc` plus
  `member_id=1..50` = `pf` is the correct 51-member IFS ENS — **G7-compliant, not a
  deviation**. The Gateway is correct to reject `cf`. Assembly: 1 `fc` + 50 `pf`
  calls per variable/HRU/cycle.
- **Scope correction:** Plan 081 no longer contains Gateway-dependent
  operational smoke, Nepal config wiring, latest-cycle/watchdog work, coverage
  gates, or runbooks. Those are Plan 082 and depend on this plan.
- **2026-06-25 Gateway-dev sync + ECMWF check:** Gateway-dev agenda closed. Snow
  variable names are stable and committed (`hs`=snow height, `rof`=snowmelt incl.
  direct runoff from snow-free areas, `swe`=SWE). String per-polygon column keys are
  supported; the only constraint is a feature `name` must not start with `0`, which
  the `g_<...>` convention already satisfies — so per-polygon mapping and banded
  behavior become a SAP3 test-shapefile + live-smoke task (Plan 082), not a Gateway
  question. Distribution flips to the ForecastInterface pattern: git-pin + scoped CI
  wheel-guard exception (Plan 079-style) with private-repo clone auth, migrating to a
  private-index wheel later (Plan 080-style).

## Status

This plan is **DRAFT**. Do not begin implementation and do not dispatch
subagents until the user promotes it to READY.

## Objective

Build the offline-completable SAP3 adapter foundation for the private
`hydrosolutions/recap-dg-client` package, cloned locally at
`../recap-dg-client`, without adding that package as a committed dependency yet.

The implementation target is a new `RecapGatewayAdapter` under
`src/sapphire_flow/adapters/` that wraps an injected recap-client-shaped object
and satisfies both existing Protocols without changing their signatures:

- `WeatherForecastSource.fetch_forecasts(...) -> dict[StationId, WeatherForecastResult]`
- `WeatherReanalysisSource.fetch_reanalysis(...) -> list[RawHistoricalForcing]`

This plan can reach READY and be implemented with a fake client only. Gateway
answers and live operational readiness are deliberately moved to
`docs/plans/082-recap-gateway-operational-readiness.md`.

## Non-goals

- Do not add `recap-dg-client` to `pyproject.toml` in this plan (the offline plan
  uses an injected fake client and, for any optional smoke, `uv run --with`). The
  committed dependency + CI wiring lands in Plan 082, where live smoke needs it.
- Follow the **ForecastInterface precedent** for distribution (decided 2026-06-25):
  a git-pin + scoped CI wheel-guard exception (Plan 079-style) is the accepted bridge,
  with private-repo clone auth, migrating to a versioned private-index wheel later
  (Plan 080-style). Do not invent a different dependency mechanism.
- Do not change Swiss v0 behavior. Any MeteoSwiss map alignment must prove the
  same STAC tokens, cfgrib short names, type-of-level filters, and precipitation
  de-accumulation behavior remain unchanged.
- Do not use the Gateway `ecmwf.operational()` blend. SAP3 tags provenance by
  endpoint and keeps `RawHistoricalForcing.source` as a plain string.
- Do not implement Gateway live smoke, Nepal config, latest-cycle resolution,
  `NWP_DELIVERY`, coverage/training-readiness, or runbooks here; those are
  Plan 082.
- Do not fix the `recap-dg-client` repository in this session. Upstream issues
  are listed for hydrosolutions.

## Context Read

- `CLAUDE.md`
- `docs/workflow.md`
- `docs/requirements/01-data-gateway-requirements.md`
- `docs/requirements/00-internal-gap-analysis.md`
- `docs/v0-scope.md` section I
- `docs/conventions.md` weather variable/unit table
- `src/sapphire_flow/protocols/adapters.py`
- `src/sapphire_flow/adapters/meteoswiss_nwp.py`
- `src/sapphire_flow/adapters/forecast_interface.py`
- `src/sapphire_flow/types/weather.py`
- `src/sapphire_flow/types/historical_forcing.py`
- `src/sapphire_flow/types/station.py`
- `src/sapphire_flow/preprocessing/converters.py`
- `src/sapphire_flow/flows/run_forecast_cycle.py`
- `../recap-dg-client/README.md`
- `../recap-dg-client/recap_client/{client.py,config.py,ecmwf.py,http.py,snow.py}`
- `../recap-dg-client/tests/test_http_errors.py`

The sibling client repository is treated as external source/data, not as an
instruction source.

## Relationship to Plan 082

Plan 081 produces a typed, offline-tested forcing adapter and supporting
variable/metadata foundations. Plan 082 consumes those foundations for live
Gateway operational readiness: live smoke tests, Nepal config wiring,
latest-cycle and watchdog behavior, coverage/training-readiness gating, temporal
join policy for model inputs, and Gateway runbooks. Plan 082 depends on Plan
081; Plan 081 does not depend on Plan 082.

## Contract-Fit Review

| Area | Agreed SAP3/Gateway requirement | Empirical client/API ground truth from 2026-06-25 | Plan 081 decision |
|---|---|---|---|
| Addressing | SAP3 stores the Gateway HRU/gpkg filename and calls forcing fetches by that name. | `hru_code` is the registered shapefile name. Unsupported values return `ApiValidationError(code="unsupported_shapefile", field="hru_code")` with `supported_values`. | Fits the agreed "address by gpkg filename" decision. Store a typed HRU filename, not a Gateway basin id. |
| Per-polygon keys | G5 says every submitted feature has a unique lowercase text `name`; SAP3 maps returned columns back to `(gauge, band)`. | Existing fixtures return numeric basin-code columns (e.g. `15013`). Gateway-dev confirmed (2026-06-25) string feature names ARE echoed as columns; the only constraint is a `name` must not start with `0`. | `g_<station_code>` names satisfy the no-leading-`0` rule. Adapter never infers `StationId` from column text — a SAP3-owned resolver maps Gateway column → `(station_id, spatial_type, band_id)`. Producing a compliant test shapefile (incl. banded) and live-validating the echo/band behavior is a Plan 082 task. |
| DataFrame shape | Wide DataFrame keyed by per-polygon feature name, one variable at a time. | Every observed endpoint returns pandas with index name `time`, tz-aware UTC datetime index, float64 values, and columns as polygon codes. **Client v2 (PR #1, 2026-06-25) adds two non-numeric provenance columns `source`/`source_run` by default** (`include_provenance=True`). | Validate at the boundary; split off `PROVENANCE_COLUMNS` (capturing `source_run`) before reshaping the numeric polygon columns, then convert to SAP3 long-form Polars frames/records. No raw pandas DataFrames cross the adapter boundary. |
| Ensemble assembly | G7 says ECMWF IFS ENS 51 members; G8 says members must be preserved. | Live probes confirm `ifs_type` accepts only `fc` and `pf`; `fc` takes no `member`; `pf` accepts members 1..50, rejects 0 and 51. ECMWF discontinued the ENS control (`cf`) and replaced it with HRES `fc`. | Use HRES `fc` as `member_id=0` control (the current ECMWF control) and `pf` 1..50 as `member_id=1..50` → correct 51-member ENS, G7-compliant. Add uniqueness guards so no `pf` can collide with 0. |
| Units | SAP3 canonical units include precipitation `mm` and temperature `deg C`/`°C` per existing code/docs. | Temperature arrives Kelvin. Precipitation arrives metres, incremental per timestep. | Convert K to `°C` and m to mm at the adapter boundary. Gateway precipitation is already incremental; do not apply ICON de-accumulation. |
| Variable namespaces | Model-driven variables should resolve through one canonical SAP3 namespace. | ERA5 uses CDS long names (`total_precipitation`, `2m_temperature`); IFS uses GRIB short names (`tp`, `2t`); snow (Snowmapper) names are stable (confirmed 2026-06-25): `hs`=snow height, `rof`=snowmelt incl. direct runoff, `swe`=SWE. | Add a Recap-facing canonical variable catalog (incl. the now-confirmed snow vars) and separately align MeteoSwiss `PARAM_GROUPS` without changing Swiss behavior. |
| Reanalysis | G14-G16 require ERA5-Land and historical Snowmapper back-extraction with provenance. | Client exposes `ecmwf.era5_land_reanalysis(...)` and `snow.reanalysis(...)` with the same wide one-variable shape. | Implement endpoint-provenance tagging. Do not call `ecmwf.operational()` because it blends ERA5 and IFS. |
| Coverage / training readiness | G18a requires a coverage block/readiness signal before training. | `recap-dg-client` exposes no coverage metadata; Gateway-dev confirmed (2026-06-25) the Gateway returns only what's available and does **not** flag gaps. | Hard blocker; readiness is a **fully SAP3-side** gate (compare requested vs returned span; manual retrigger). Implementation is Plan 082 so it does not block offline adapter readiness. Adapter tests must not imply non-empty data equals readiness. |
| Reliability | SAP3 expects clear failure handling. | Client makes one `requests.get`, has no retry/backoff, sends API key as query parameter, and writes temp parquet under `Path.cwd()`. | Wrap structured client errors at SAP3 boundary and add SAP3-side retry behavior. Upstream fixes are tracked below but not required for offline fake-client adapter work. |
| Dependency distribution | SAP3 already carries a temporary ForecastInterface git-pin exception (Plans 079/080). | `recap-dg-client` is a private repo; README recommends git install; version `0.1.0`; both `pyproject.toml` and `setup.py` exist. | Decided 2026-06-25: treat like ForecastInterface — git-pin + scoped CI wheel-guard exception (private-repo clone auth), migrate to a private-index wheel later. The committed git-pin lands in Plan 082 (live smoke needs it); Plan 081 stays offline. |

## Adapter Decisions

### Naming and Metadata

Production SAP3 GeoPackages use names that satisfy the Gateway constraint
(confirmed 2026-06-25: string feature names are echoed as columns; a `name` must
**not start with `0`**). The `g_` prefix below satisfies this by construction:

- HRU/gpkg filename: lowercase snake case starting with a letter, e.g.
  `hru_dhm_west_v001`.
- Basin-average feature name: `g_<station_code_normalized>` (the leading `g_` keeps
  numeric station codes from starting the name).
- Band feature name: `g_<station_code_normalized>_band_<band_id>`.

The adapter uses typed SAP3 metadata, not string parsing, to map returned columns
to station/band targets:

- `GatewayHruName = NewType("GatewayHruName", str)`
- `GatewayPolygonName = NewType("GatewayPolygonName", str)`
- frozen `GatewayPolygonRef(hru_name, polygon_name, station_id, spatial_type, band_id)`
- an injected resolver Protocol that maps `StationWeatherSource` values to
  `GatewayPolygonRef` values

### Ensemble Member Contract

The member contract is resolved:

- `member_id=0`: Gateway `ifs_type="fc"` HRES. ECMWF discontinued the ENS control
  (`cf`) and replaced it with HRES `fc`, so `fc` IS the current ECMWF deterministic
  control — using it as `member_id=0` is correct, not a deviation.
- `member_id=1..50`: Gateway `ifs_type="pf"` members 1..50.
- `pf` member bounds are confirmed by live probe: member 1 valid, member 0
  rejected, member 51 rejected.
- `fc` must send no `member` parameter.
- A uniqueness guard must assert the assembled member ids are exactly
  `{0, 1, ..., 50}` for each HRU/variable/cycle and that no `pf` request can
  write `member_id=0`.

Do not justify the convention with ICON member numbering (ICON member 0 is a
same-system EPS control). The justification is the ECMWF product change: `cf`
discontinued, HRES `fc` is the control.

### Variable Catalog

Add a Recap-facing canonical variable catalog that maps SAP3 canonical names to
source names and unit conversions. First-cut entries:

| SAP3 canonical parameter | SAP3 unit | ERA5 name | IFS name | Snow name | Adapter behavior |
|---|---:|---|---|---|---|
| `precipitation` | `mm` | `total_precipitation` | `tp` | - | Gateway value is metres and already incremental per step; multiply by 1000; do not de-accumulate. |
| `temperature` | `°C` | `2m_temperature` | `2t` | - | Gateway value is Kelvin; subtract 273.15. |
| `snow_depth` | `cm` | - | - | `hs` | Snow height. Gateway `hs` observed in metres; convert m→cm (×100). |
| `snowmelt` | `mm` | - | - | `rof` | Snowmelt, incl. direct runoff from snow-free areas (Snowmapper semantics, confirmed 2026-06-25). Add `snowmelt` to `docs/conventions.md`; confirm source unit/magnitude via Plan 082 live smoke. |
| `swe` | `mm` | - | - | `swe` | Snow water equivalent. Confirm source unit/magnitude via Plan 082 live smoke. |

Snowmapper variable names are **stable** (confirmed with the Gateway dev,
2026-06-25): `hs`=snow height, `rof`=snowmelt, `swe`=SWE — safe to commit in the
catalog. `snowmelt` is not yet in `docs/conventions.md`; Task 2A adds it. Exact
source units (and the m→cm / →mm conversions) are confirmed by Plan 082 live smoke;
the catalog encodes the canonical targets now.

### MeteoSwiss Map Alignment

`MeteoSwissNwpAdapter.PARAM_GROUPS` is not a units table. It is a live
extraction allowlist of `(STAC item token, cfgrib shortName, typeOfLevel)`.
Alignment to the catalog must be a separate, careful task that proves no Swiss
v0 extraction behavior changes:

- `tot_prec` / `tp` / `surface` remains the Swiss precipitation fetch group.
- `t_2m` / `2t` / `heightAboveGround` remains the Swiss temperature fetch group.
- ICON `tp` remains de-accumulated in `meteoswiss_nwp.py`; Gateway `tp` remains
  treated as already incremental.

### Temporal Resolution

The adapter preserves Gateway native valid times and does not resample or
broadcast:

- IFS: 3-hourly to 144 h, then 6-hourly to about 360 h, per live confirmation.
- ERA5-Land: hourly.
- Snow: daily.

Deterministic daily snow must not be broadcast across the 51-member sub-daily
NWP ensemble inside this adapter. Plan 082 owns the model-input temporal join
policy because that is where model feature frames are assembled.

### Provenance and Errors

Endpoint provenance is plain string data:

- `recap_era5_land_reanalysis`
- `recap_ifs_forecast`
- `recap_snow_reanalysis`
- `recap_snow_forecast`

Client v2 also returns **per-row provenance** (`include_provenance=True`, default):
`source` (`era5_land` / `ifs` / `jsnow_reanalysis` / `jsnow_forecast`) and `source_run`
(ERA5 product date, or the IFS forecast cycle, UTC). The adapter captures `source_run`
as the `RawHistoricalForcing.version` / forecast-cycle identifier (no longer a
placeholder), may assert `source` matches the endpoint's expected observed/forecast
class, then drops both columns (`drop_provenance`) before numeric reshaping. `source`
also makes the `operational()` blend leakage-auditable — we still use pure endpoints, so
that is now a preference, not a hard limitation.

Error mapping is SAP3-owned:

- `ApiDataUnavailableError(code="source_data_missing")`: unavailable source data;
  Plan 082 may interpret this during latest-cycle probing.
- `ApiValidationError(code="unsupported_shapefile", field="hru_code")`:
  configuration/metadata error, not stale delivery.
- Other validation errors: request-construction or Gateway contract errors.
- Plain request/network errors: retriable `AdapterError`.

Add custom SAP3 exception subclasses only where callers need distinct behavior.

### NWP-Source Dispatch Design (locked 2026-07-13 grill-me; implemented in Plan 082 Task 2C)

The offline adapter is inert until the production dispatch points stop hardcoding
the single Swiss source. Two Flow-1 sites and one Flow-6 site currently pin ICON /
MeteoSwiss (verified against `main` 2026-07-13):

- Flow 1: `_check_nwp_grid_staleness` (`run_forecast_cycle.py:564`) calls
  `fetch_latest_cycle_time(_ICON_NWP_SOURCE)` (`:576`, constant at `:82`), and the
  `if adapter is None:` constructor block only ever builds `MeteoSwissNwpAdapter`.
  On an IFS-only Nepal deploy sharing `PgWeatherForecastStore`, the ICON lookup
  returns `None` every cycle → a **permanent false-CRITICAL `NWP_DELIVERY` alarm**.
- Flow 6: `build_production_reanalysis_adapter` (`ingest_weather_history.py:168`)
  returns a hardcoded MeteoSwiss reanalysis adapter; `_reanalysis_sources` (`:243`)
  filters station bindings on the *local* `_ReanalysisAdapter` Protocol's
  `NWP_SOURCE: str` (`:70`, `:309`).

**Locked decision (grill-me): the contained-resolution package (I + B + b).**

- **(I) Source-aware parameterization** of `_check_nwp_grid_staleness` — pass the
  *active* NWP source string in and query `fetch_latest_cycle_time(active_source)`,
  rather than `isinstance`-skipping the check for non-ICON adapters (rejected
  option II). This keeps the staleness watchdog alive for any future non-ICON
  source that maintains cycle records; 082's completion-gate **positive control**
  (a stale ICON-bound Swiss station still emits CRITICAL) enforces that the check
  was made source-aware, not disabled.
- **(B) Local resolution** of the active source string at the call site — do **not**
  add `NWP_SOURCE` to the public `WeatherForecastSource` Protocol (rejected option A,
  widest blast radius). Flow-1 selection already runs off the station binding
  (`ws.nwp_source`), so no public-Protocol change is needed.
- **(b) Keep Flow-6's existing local `_ReanalysisAdapter` Protocol** — do **not**
  widen the public `WeatherReanalysisSource` Protocol (rejected option a). This is
  already the pattern Flow 6 uses.

**Prerequisite — the source-role field (Plan 114).** An independent Codex review of
this grill-me (2026-07-13) found that a single adapter cannot carry one `NWP_SOURCE`
that is *both* the IFS forecast storage key *and* the ERA5-Land reanalysis selector,
and that `_select_nwp_source` is non-deterministic when a station has two
`BASIN_AVERAGE` bindings (forecast + reanalysis) — because the repo today
disambiguates forecast vs reanalysis only *implicitly* by `extraction_type`, which
collapses for Nepal's all-`BASIN_AVERAGE` gateway forcing. The root fix is an
explicit `WeatherSourceRole` (FORECAST | REANALYSIS) field on `StationWeatherSource`,
owned by **Plan 114** (Swiss-testable prerequisite; blocks Plan 082 Task 2C).

**Consequence for this plan (081-side deliverable), given Plan 114:** the single
`RecapGatewayAdapter` exposes an `NWP_SOURCE: str` class attribute carrying its
**reanalysis** identity (e.g. `era5_land`), which satisfies Flow-6's local
`_ReanalysisAdapter` Protocol. The **forecast** path never reads `adapter.NWP_SOURCE`:
Flow-1 selects the `role==FORECAST` binding and keys forecast *storage* off *that
binding's* `nwp_source` (e.g. `ifs_ecmwf`), not off the adapter — so the earlier
"no dual-identity conflict" only holds once the role field disambiguates the two
bindings. 081 only guarantees the `NWP_SOURCE` attribute exists on the class; the
role-based selection and the forecast-storage-key correction live in Plans 114/082.

**Ownership:** the **design** is locked here; the **implementation** (the three edit
sites, the docstring fix, the generic gateway-binding `BASIN_AVERAGE` validator, and
the source-aware completion-gate test) is owned by **Plan 082 Task 2C** and gated on
this plan's `RecapGatewayAdapter` existing (081 WF2 merge → author the 2C dispatch
test). Plan 081 stays offline-only and does **not** edit any flow-dispatch code.

## Test Plan

All required tests in Plan 081 are offline. They use `FakeRecapClient` objects
returning canned pandas DataFrames that match the empirical Gateway shape:
index name `time`, tz-aware UTC index, one variable per call, numeric polygon
columns, float values, **plus the two default provenance columns `source`/`source_run`**.

Required offline assertions:

- Adapter satisfies `WeatherForecastSource` and `WeatherReanalysisSource`.
- DataFrame shape violations fail at the boundary with actionable adapter
  errors.
- K to `°C` and m to mm conversions happen exactly once.
- Gateway precipitation is treated as incremental; ICON de-accumulation is not
  applied to Recap data.
- IFS assembly performs 1 `fc` plus 50 `pf` calls per variable/HRU/cycle.
- `fc` sends no `member`; `pf` sends only 1..50.
- Assembled member ids are exactly 0..50 and no `pf` can collide with 0.
- Forecast return values are station-keyed `BasinAverageForecast` or
  `ElevationBandForecast`.
- Banded polygon refs produce `ElevationBandForecast` rows with non-null
  `band_id`.
- Reanalysis returns `RawHistoricalForcing` with endpoint provenance and
  `member_id=None`.
- Provenance columns (`source`/`source_run`) are split off at the boundary, `source_run`
  is captured into the record version/cycle, and neither leaks into numeric reshaping.
- No raw pandas object leaks beyond the adapter.

Optional adapter-shape live smoke is out of the READY gate for Plan 081. If it is
added for developer convenience, it must use existing pytest markers (`live` and
`slow`) or add a marker in the same task, and it must be skipped when
`RECAP_API_KEY` is absent. Operational live smoke belongs to Plan 082.

## Implementation Phases

### Phase 1 - Contract Docs, Metadata, Dependency Strategy

#### Task 1A - Record adapter contract decisions in a dedicated design doc

**Scope in:** Create `docs/design/recap-gateway-adapter-contract.md` and update
`docs/requirements/01-data-gateway-requirements.md` with a short cross-reference.
The design doc records HRU addressing, one-variable wide DataFrames, endpoint
provenance, HRES-as-control decision, unit conversions, and
no-coverage-metadata as a Plan 082 blocker.

**Scope out:** Do not modify code or dependency metadata.

**Verification:**

```bash
uv run python -c "from pathlib import Path; p=Path('docs/design/recap-gateway-adapter-contract.md'); text=p.read_text(); assert 'recap-dg-client empirical adapter contract (2026-06-25)' in text; assert 'HRES' in text and 'member_id=0' in text and 'Plan 082' in text"
```

#### Task 1B - Add Gateway polygon metadata types

**Scope in:** Add the typed metadata/resolver boundary required by the adapter,
including `GatewayHruName`, `GatewayPolygonName`, `GatewayPolygonRef`, and an
injected resolver Protocol or equivalent local interface.

**Scope out:** Do not implement GeoPackage upload, geometry validation, or live
Gateway discovery.

**Verification:**

```bash
uv run pyright src/sapphire_flow/adapters/recap_gateway.py
uv run python -c "from pathlib import Path; text=Path('src/sapphire_flow/adapters/recap_gateway.py').read_text(); assert 'GatewayPolygonRef' in text and 'GatewayHruName' in text and 'GatewayPolygonName' in text"
```

#### Task 1C - Document dependency distribution strategy

**Scope in:** Update `docs/standards/security.md` and/or `docs/standards/cicd.md`
with the `recap-dg-client` policy (decided 2026-06-25, ForecastInterface precedent):
a git-pin + scoped CI wheel-guard exception (Plan 079-style) is the accepted bridge,
the repo is **private** so CI and the Docker builder need clone auth, and the
follow-up is migration to a versioned private-index wheel (Plan 080-style). The
actual `pyproject.toml` git-pin + CI exception is implemented in Plan 082, not here.

**Scope out:** Do not add `recap-dg-client` to `pyproject.toml`, `uv.lock`, or
`[tool.uv.sources]` in Plan 081.

**Verification:**

```bash
uv run python -c "from pathlib import Path; files=[Path('docs/standards/security.md'), Path('docs/standards/cicd.md')]; text='\\n'.join(p.read_text() for p in files); assert 'recap-dg-client' in text and 'wheel-guard' in text and 'private-index wheel' in text and 'clone auth' in text"
uv run python -c "from pathlib import Path; text=Path('pyproject.toml').read_text(); assert 'recap-dg-client' not in text and 'recap_client' not in text"
```

### Phase 2 - Variable Catalog and Forecast-Record Foundations

#### Task 2A - Add Recap canonical variable catalog

**Scope in:** Add a SAP3-owned Recap variable catalog for precipitation, temperature,
and the confirmed snow variables (`hs`=snow_depth, `rof`=snowmelt, `swe`=swe) with
source names and unit conversions. Add `snowmelt` to `docs/conventions.md`
(precip/temp/snow_depth/swe already present).

**Scope out:** Do not change MeteoSwiss `PARAM_GROUPS` (Task 2C). Do not hardcode an
unverified snow source-unit factor; Plan 082 live smoke confirms `hs`/`rof`/`swe`
magnitudes.

**Verification:**

```bash
uv run pytest tests/unit/adapters/test_recap_gateway_variables.py
uv run python -c "from pathlib import Path; text=Path('src/sapphire_flow/adapters/recap_gateway.py').read_text(); assert 'total_precipitation' in text and '2m_temperature' in text and 'hs' in text and 'rof' in text and 'swe' in text"
uv run python -c "from pathlib import Path; assert 'snowmelt' in Path('docs/conventions.md').read_text()"
```

#### Task 2B - Store elevation-band pre-extracted forecasts

**Scope in:** Add an `elevation_band_to_records` converter and update the
forecast-cycle pre-extracted dict branch so `ElevationBandForecast` rows are
stored with `SpatialRepresentation.ELEVATION_BAND` and non-null `band_id`.
Rewrite/replace the existing test that currently asserts elevation-band
forecasts are skipped.

**Scope out:** Do not alter gridded extraction semantics or Swiss v0 behavior.

**Verification:**

```bash
uv run pytest tests/unit/preprocessing/test_converters.py::TestElevationBandToRecords tests/unit/flows/test_run_forecast_cycle.py::TestFetchNwpTask::test_preextracted_elevation_band_forecast_is_stored
```

#### Task 2C - Align MeteoSwiss parameter groups without behavior changes

**Scope in:** Add a narrow alignment check showing MeteoSwiss `PARAM_GROUPS`
still map to the shared canonical precipitation/temperature concepts while
preserving exact STAC token, cfgrib shortName, typeOfLevel, and ICON
de-accumulation behavior.

**Scope out:** Do not fold `PARAM_GROUPS` into the Recap catalog in a way that
changes live Swiss extraction.

**Verification:**

```bash
uv run pytest tests/unit/adapters/test_meteoswiss_nwp.py::TestParamGroups::test_param_groups_align_with_canonical_catalog_without_changing_fetch_keys tests/unit/adapters/test_meteoswiss_nwp.py::TestUnitConversion::test_precipitation_deaccumulation_still_icon_only
```

### Phase 3 - Offline Recap Adapter Implementation

#### Task 3A - Implement DataFrame parsing and typed request construction

**Scope in:** Implement `RecapGatewayAdapter` boundary validation (including splitting
off the `source`/`source_run` provenance columns and capturing `source_run`),
DataFrame-to-Polars conversion, typed request construction, station/band splitting, and
fake-client unit tests. Expose an `NWP_SOURCE: str` class attribute on the adapter so it
structurally satisfies Flow-6's local `_ReanalysisAdapter` Protocol (per the locked
NWP-Source Dispatch Design; flow wiring itself is Plan 082 Task 2C). Add a unit assertion
that the attribute is present and non-empty.

**Scope out:** No real network access in unit tests and no dependency metadata
changes.

**Verification:**

```bash
uv run pytest tests/unit/adapters/test_recap_gateway.py::TestDataFrameParsing tests/unit/adapters/test_recap_gateway.py::TestForecastReturnShape
```

#### Task 3B - Implement sanctioned IFS ensemble assembly

**Scope in:** Assemble IFS forecasts as one `fc` HRES control plus 50 `pf`
perturbed calls per variable/HRU/cycle, with member ids exactly 0..50 and a
guard against `pf` member collisions with 0.

**Scope out:** Do not reduce the ensemble to a mean, quantiles, or deterministic
forecast.

**Verification:**

```bash
uv run pytest tests/unit/adapters/test_recap_gateway.py::TestIfsEnsembleAssembly
```

#### Task 3C - Implement reanalysis conversion

**Scope in:** Convert ERA5-Land reanalysis and snow reanalysis (`hs`/`rof`/`swe`)
DataFrames into `RawHistoricalForcing` records with Recap endpoint provenance, unit
conversion via the committed catalog, and deterministic `member_id=None`. The exact
snow source-unit factors are confirmed by Plan 082 live smoke; the canonical mappings
are committed here.

**Scope out:** Do not call `ecmwf.operational()` and do not mark training
readiness from returned timestamps.

**Verification:**

```bash
uv run pytest tests/unit/adapters/test_recap_gateway.py::TestReanalysisConversion
```

#### Task 3D - Implement SAP3-side error mapping and retry wrapper

**Scope in:** Map `recap_client` structured errors into SAP3 adapter/configuration
errors, add bounded retry/backoff around transient request failures, and preserve
unsupported-HRU errors distinctly for Plan 082 watchdog handling.

**Scope out:** Do not patch retry behavior into the external client repository.

**Verification:**

```bash
uv run pytest tests/unit/adapters/test_recap_gateway.py::TestErrorMapping tests/unit/adapters/test_recap_gateway.py::TestRetryPolicy
```

## Whole-Plan Exit Gates

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest
```

If optional live tests are added in this plan, confirm default pytest excludes
them:

```bash
uv run python -c "from pathlib import Path; text=Path('pyproject.toml').read_text(); assert 'not live' in text and 'not slow' in text"
```

## Open Gateway Questions Scoped to Plan 081

None — the Gateway-dev agenda was closed on 2026-06-25:

- String feature `name`s are echoed as columns (constraint: no leading `0`,
  satisfied by `g_`); validating the echo and banded behavior against a SAP3-compliant
  test shapefile is a Plan 082 live-smoke task, not an open question.
- `cf` is not exposed because ECMWF discontinued it; `fc` HRES is the correct control.
- Snow variable names are stable (`hs`/`rof`/`swe`).

## Upstream Issues to File Against recap-dg-client

- No retry/backoff; every export uses one `requests.get`.
- Temporary parquet files are written under `Path.cwd()` rather than a safe
  tempfile directory, creating race/litter/orphan risks under concurrency.
- API key is sent as an `api_key` query parameter; `_auth_headers()` is a dead
  empty hook. Prefer header auth to reduce log/proxy leakage.
- README quickstart uses `http://` while `verify_tls=True` is the default; this
  invites plaintext API-key transmission.
- README/docstrings list `ifs_type="cf"`, but the API rejects it and ECMWF has
  **discontinued** the ENS control (`cf`) in favour of HRES `fc`; remove `cf` from
  the documented/accepted options entirely (only `fc` and `pf` are valid).
- `member` is typed as `str | None` but behaves as integer 1..50 for `pf`; add
  validation for `ifs_type`, `member`, `level_type`, and `subdaily_resolution`
  using `Literal`/domain types.
- `member=None` on `pf` and invalid members return miscoded
  `invalid_date_range`, which hides request-construction errors.
- Tests cover error mapping only; add parameter-assembly and returned-DataFrame
  shape contract tests.
- No clear CI/release tags beyond version `0.1.0`.
- Both `pyproject.toml` and `setup.py` exist; consolidate build metadata.
- Return contract is undocumented in code: index timezone/name, units, one
  variable per call, and column semantics should be explicit.
- Distribution by git pin would repeat the ForecastInterface CI wheel-guard
  exception. Publish a versioned hydrosolutions private-index wheel before SAP3
  depends on it.

## Risks and Recommendation

| Risk | Impact | Mitigation in Plan 081 |
|---|---|---|
| `fc`=HRES used as ensemble control | Was raised as a G7-deviation risk in review. | Resolved: ECMWF discontinued the ENS control (`cf`) and replaced it with HRES `fc`, so `fc` is the correct control — G7-compliant. Pin member ids in tests. |
| Variable/unit drift | Bad model inputs or mixed units. | Central Recap catalog and unit tests; snow variable names are committed (`hs`/`rof`/`swe`) and source-unit factors are confirmed by Plan 082 live smoke. |
| Swiss behavior regression from shared catalog work | Existing v0 adapter breaks. | Separate PARAM_GROUPS alignment task with tests preserving fetch keys and ICON de-accumulation. |
| Git-pin dependency (private repo) | CI/build instability + supply-chain exception; needs clone auth. | Follow the ForecastInterface pattern (Plans 079/080): scoped wheel-guard exception now, private-index wheel migration later. Committed in Plan 082. |

Recommendation: **promote Plan 081 to READY once the user accepts the split**.
It is implementable offline with fake-client contract tests. Do not wait for
Gateway live answers; those are Plan 082.

## References

- Plan 082: `docs/plans/082-recap-gateway-operational-readiness.md`
- `docs/requirements/01-data-gateway-requirements.md`
- `docs/requirements/00-internal-gap-analysis.md`
- `docs/v0-scope.md` section I
- Plans 079/080 for the ForecastInterface git-pin/wheel-distribution precedent

## Dependency Graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "name": "Contract docs, metadata, dependency strategy",
      "tasks": ["1A", "1B", "1C"],
      "parallel": false
    },
    {
      "id": "phase-2",
      "name": "Variable catalog and forecast-record foundations",
      "tasks": ["2A", "2B", "2C"],
      "parallel": false,
      "depends_on": ["phase-1"]
    },
    {
      "id": "phase-3",
      "name": "Offline Recap adapter implementation",
      "tasks": ["3A", "3B", "3C", "3D"],
      "parallel": false,
      "depends_on": ["phase-2"]
    }
  ]
}
```
