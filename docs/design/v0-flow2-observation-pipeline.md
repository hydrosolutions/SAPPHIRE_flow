# v0 Observation Pipeline — Design Overview

> High-level design for Vertical Slice 1: river observation ingest and station onboarding
> using Swiss public data (CAMELS-CH historical + BAFU LINDAS operational).
> Covers Flow 2 (observation ingest + QC) and Flow 5 (station onboarding, simplified).

## 1. Data sources

### 1a. Historical observations — CAMELS-CH

**Source**: [CAMELS-CH dataset](https://zenodo.org/records/7784632) (Hoege et al. 2023)
**Library**: [`camelsch`](https://github.com/hydrosolutions/camelsch) (git submodule at `.context/camelsch/`)
**Coverage**: 331 Swiss BAFU basins, 1981–2020

| Aspect | Detail |
|--------|--------|
| **Resolution** | Daily |
| **River parameters** | Specific discharge (mm/d), total discharge (derived via area) |
| **Weather parameters** | Basin-mean daily precipitation (mm/d), temperature (°C), PET, ET, SWE |
| **Catchment attributes** | 234+ static features (topography, climate, soil, geology, land cover) |
| **Station IDs** | 4-digit BAFU gauge codes (e.g. `"2004"`) — these become `stations.code` |
| **Access** | Download once from Zenodo (~1.5 GB), query locally via pandas |
| **Format** | CSV → `pd.DataFrame` with DatetimeIndex |

> **Note**: The original CAMELS-CH dataset on Zenodo provides only specific discharge (mm/d),
> not absolute discharge (m³/s). The `camelsch` library returns this as `discharge_spec`.
> **Action needed**: Verify against the raw Zenodo files whether absolute discharge is also
> available before committing to the conversion path. If not, the adapter must convert using
> `Q_m3s = discharge_spec_mmd × area_km2 × 1000 / 86400` (basin area from static attributes).

**Role in v0**: Historical training data, baseline computation, flow regime calibration,
skill evaluation. Bulk-imported during station onboarding (Flow 5 step 5.4).

**Limitation**: Daily only. Sub-daily resolution requires alternative datasets (see § 6).

### 1b. Operational observations — BAFU LINDAS

**Source**: [LINDAS SPARQL endpoint](https://lindas.admin.ch/query) (Swiss Federal Linked Data)
**Library**: [`hydro_data_scraper`](https://github.com/hydrosolutions/hydro_data_scraper) (git submodule at `.context/hydro_data_scraper/`)
**Coverage**: All BAFU river gauge stations with real-time telemetry

| Aspect | Detail |
|--------|--------|
| **Resolution** | ~10 minutes |
| **River parameters** | Discharge (m³/s), water level (m), water temperature (°C) |
| **Access** | Public SPARQL endpoint, no authentication |
| **Format** | SPARQL JSON → parsed to records |
| **Station IDs** | Same 4-digit BAFU codes as CAMELS-CH |
| **Rate limits** | None documented; sequential per-station queries |

**Role in v0**: Operational observation ingest (Flow 2). Scheduled every N minutes
to keep the observations table current.

### 1c. Station ID alignment

Both sources use the same BAFU 4-digit gauge codes. This is a key simplification for v0 —
no station ID mapping needed. The `stations.code` field directly matches both CAMELS-CH
`gauge_id` and LINDAS `site_code`.

Not all 331 CAMELS-CH basins will have real-time LINDAS data. The v0 station selection
(~10–50 stations) should be drawn from the intersection of CAMELS-CH basins that also
have operational LINDAS telemetry.

### 1d. Sub-daily datasets for v0b (temporal resolution experiments)

CAMELS-CH is daily-only. For v0b experiments exploring daily→sub-daily generalization,
several datasets with higher temporal resolution are available:

**LamaH-CE** ([Zenodo](https://zenodo.org/records/5153305), Klingler et al. 2021)
- **Coverage**: 882 gauges across Central Europe (AT, DE, CH, CZ). **25 Swiss BAFU stations**.
- **Resolution**: Daily + **hourly** discharge and 15 meteorological variables (ERA5-Land)
- **Time range**: 1981–2017
- **Format**: CSV (tab-separated). ~70 GB complete, ~5 GB daily-only.
- **Python access**: [NeuralHydrology](https://github.com/neuralhydrology/neuralhydrology) library has native LamaH-CE support
- **Limitation**: Only 25 Swiss stations (vs 331 in CAMELS-CH). Station ID mapping to
  BAFU 4-digit codes is not documented — requires contacting authors
  (christoph.klingler@boku.ac.at) or inspecting dataset metadata files.
- **Discharge units**: m³/s (absolute, no conversion needed)

**HydroCH** ([Zenodo](https://zenodo.org/records/7691294))
- **Coverage**: 291 Swiss catchments (excellent Swiss coverage)
- **Resolution**: **Hourly**
- **Time range**: 2019–2020 only (2 years — insufficient for training, useful for validation)

**CAMELS-GB** (UK) and **CAMELS** (US) provide hourly data for non-Swiss catchments.
Useful for cross-regional generalization experiments.

**Recommendation for v0b**: Use LamaH-CE's 25 Swiss hourly stations as the primary dataset
for temporal resolution experiments. Supplement with CAMELS-CH daily data for broader
station coverage. HydroCH can provide recent hourly validation data. The v0b experimental
design (publication-quality) deserves its own document.

**Note**: LamaH-CE does **not** replace CAMELS-CH for v0a. With only 25 Swiss stations
(vs 331), it lacks the station density needed for development and testing. v0a proceeds
with CAMELS-CH daily data.

---

## 2. v0 observation pipeline scope

### What v0 implements (Flow 2 steps)

| Step | Description | v0 scope |
|------|-------------|----------|
| 2.1 | Fetch latest observations | BAFU LINDAS adapter (river). SMN weather station adapter also needed for operational Flow 2 — detailed design deferred. |
| 2.2 | Store raw observations | Full — `qc_status = 'raw'` |
| 2.3 | Stage 1 QC | Range check, rate-of-change, frozen sensor, spike detection |
| 2.4 | Store QC results | Full — update flags + status |
| 2.5–2.7 | Rating curve + Stage 2 QC | **Skipped** — BAFU provides discharge directly |
| 2.8–2.10 | Threshold checks + alerts | **Optional**, disabled by default (`enable_observation_alerts`) |

**Operational polling**: Flow 2 runs on a configurable schedule (default: every 10 minutes) for all LINDAS-available
gauges. This accumulates sub-daily (10-min) observation data that will be used for testing
sub-daily forecasts in later phases, even though v0a models operate at daily resolution.

**Station selection**: v0 onboards only CAMELS-CH stations that are also available through
the LINDAS SPARQL interface (~170 automated BAFU stations have real-time telemetry).
The intersection gives us both historical training data (CAMELS-CH) and operational
real-time data (LINDAS) for the same stations.

### What v0 implements (Flow 5 steps — onboarding)

| Step | Description | v0 scope |
|------|-------------|----------|
| 5.1 | Register station metadata | From TOML config |
| 5.2 | Fetch catchment attributes | From CAMELS-CH static attributes |
| 5.3 | Configure weather sources | Deferred (no NWP in first slice) |
| 5.4 | Import historical observations | Bulk import from CAMELS-CH |
| 5.5 | Stage 1 QC on historical | Same QC service as Flow 2 |
| 5.6–5.7 | Rating curve + Stage 2 QC | **Skipped** |
| 5.8 | Compute baselines | Climatology quantiles, persistence |
| 5.9 | Compute flow regimes | Q50/Q90 from QC'd history |
| 5.10 | Model assignments | Deferred to model training slice |
| 5.11–5.12 | Model readiness + go-live | Deferred to model training slice |

---

## 3. Data transformations

### Historical path (CAMELS-CH → Observation)

```
camelsch.load_timeseries(data_dir, basin_ids, variables)
    → dict[str, pd.DataFrame]           # keyed by basin_id, DatetimeIndex, daily
    │
    ├── River discharge path (→ observations table):
    │   "discharge_spec" (mm/d) → "discharge" (convert to m³/s using basin area)
    │   Q_m3s = discharge_spec_mmd * area_km2 * 1000 / 86400
    │   Construct RawObservation per row:
    │     station_id: StationId (looked up from stations.code = gauge_id)
    │     timestamp: UtcDatetime (date → midnight UTC, daily resolution)
    │     parameter: "discharge"
    │     value: float (m³/s)
    │     source: ObservationSource.MANUAL_IMPORT
    │   → ObservationStore.store_raw_observations(batch)
    │     qc_status = 'raw', then Stage 1 QC runs
    │
    │   **Note**: Raw Zenodo CSVs contain absolute discharge (m³/s) directly.
    │   The adapter reads m³/s from raw CSVs (not via `camelsch` library's
    │   `discharge_spec`). No basin area conversion needed (open question #6 resolved).
    │
    └── Weather forcing path (→ historical_forcing table):
        "precipitation" (mm/d), "temperature" (°C)
        Construct RawHistoricalForcing per row:
          station_id: StationId
          source: "camels-ch"
          parameter: "precipitation" | "temperature"
          value: float (native units)
          valid_time: UtcDatetime
          spatial_type: "basin_average"
        → HistoricalForcingStore.store_forcing(batch)
```

### Operational path (LINDAS → Observation)

```
LINDAS SPARQL query per station
    → JSON bindings: {measurementTime, discharge, waterLevel, waterTemperature}
    │
    ├── Parameter extraction:
    │   "discharge" → float (m³/s, native units)
    │   "waterLevel" → float (m, station datum)
    │   "waterTemperature" → float (°C)
    │
    ├── Construct RawObservation per measurement:
    │   station_id: StationId (from stations.code = site_code)
    │   timestamp: UtcDatetime (parse ISO 8601, ensure_utc)
    │   parameter: "discharge" | "water_level" | "water_temperature"
    │   value: float (native units)
    │   source: ObservationSource.MEASURED
    │
    ├── Incremental: only fetch since last-seen timestamp per station
    │
    └── ObservationStore.store_raw_observations(batch)
        → qc_status = 'raw', then Stage 1 QC runs
```

### Catchment attributes path (CAMELS-CH → Basin)

```
camelsch.load_attributes(data_dir, basin_ids)
    → pd.DataFrame indexed by gauge_id, 234+ columns
    │
    ├── Extract geometry: separate source (swisstopo or CAMELS-CH boundaries)
    │
    ├── Construct Basin:
    │   id: BasinId (generated)
    │   code: str (gauge_id)
    │   name: str (from metadata)
    │   network: "bafu"
    │   geometry: MultiPolygon (from boundary shapefile)
    │   area_km2: float (from "area" attribute)
    │   attributes: dict (all CAMELS-CH static features as JSONB)
    │   band_geometries: None (v0 — no elevation bands)
    │
    └── BasinStore.store_basin(basin)
```

---

## 4. Stage 1 QC rules (v0)

All rules operate per-observation, per-station. No cross-station spatial checks.

| Rule | Input | Logic | Flag on fail |
|------|-------|-------|--------------|
| **Range check** | value, station config | `value_min <= value <= value_max` (per parameter, per station) | `QC_FAILED` |
| **Rate-of-change** | current + previous value | `abs(value - prev) / dt > max_rate` (per parameter) | `QC_SUSPECT` |
| **Frozen sensor** | N consecutive values | All identical within tolerance for N intervals | `QC_SUSPECT` |
| **Spike detection** | 3-point window | Single-interval excursion that returns to ±tolerance | `QC_SUSPECT` |
| **Gross outlier** | value, rolling climatology | `abs(value - rolling_mean) > K * rolling_std` | `QC_SUSPECT` |

**For daily CAMELS-CH data**: Rate-of-change and frozen sensor checks operate on daily
timesteps. Spike detection requires ≥3 consecutive points. These rules are still meaningful
at daily resolution but with different threshold parameters than 10-minute data.

**Rule configuration**: Per-station, per-parameter. Loaded from `config.toml` or DB.
Each rule has a `rule_id` and `rule_version` for traceability.

### QC rule configuration and temporal resolution

**Resolved.** The QC rule configuration schema is now defined in:
- `architecture-context.md` § QC rule configuration — three-layer hierarchy
  (deployment defaults → per-station overrides → rule versioning), time-step dimension,
  rule parameter table for all 5 Stage 1 rules
- `types-and-protocols.md` § QcRuleSet — `QcRuleParams`, `QcRuleSet`, `StationQcOverride`
  types, `QualityChecker` Protocol, `ClimBaseline` type for gross outlier baselines
- `config-reference.toml` § `[qc_rules]` — Swiss v0 defaults for discharge (10min, 1day),
  water level (10min, 1day), water temperature (10min), precipitation (1day),
  temperature (1day)

**Key design decisions**:
- Time-step dimension on all QC rule parameters — the QC service selects thresholds by
  matching the observation's parameter and time step
- Gross outlier baselines (rolling climatological mean/std) are pre-computed during station
  onboarding (Flow 5 step 5.8) and stored alongside climatology quantiles
- Per-station overrides use `None` for fields that inherit the deployment default
- v0: overrides in station onboarding TOML; v1: migrates to DB (dashboard-editable)

---

## 5. Adapter design considerations

### camelsch adapter (historical import)

The `camelsch` library returns pandas DataFrames. Our adapter wraps this:

- **Input**: data directory path, list of station codes, date range
- **Output**: `list[RawObservation]`
- **Protocol fit**: This is a batch import, not a `StationDataSource.fetch_observations()` call.
  It's a one-shot operation during onboarding (Flow 5 step 5.4), not a recurring fetch.
  Consider a dedicated `HistoricalObservationSource` Protocol or use `StationDataSource`
  with a `since` parameter set to the full historical range.
- **Unit conversion**: Must happen inside the adapter. CAMELS-CH discharge is specific (mm/d);
  SAPPHIRE stores absolute discharge (m³/s). Requires basin area from attributes.
- **Timezone**: CAMELS-CH dates are date-only (no time). Convention: treat as midnight UTC
  for daily data. Document this choice.

**Open question**: Should the adapter be a thin wrapper around `camelsch.load_timeseries()`,
or should it download + load in one step? Recommendation: separate download (CLI/script)
from load (adapter). The download is a one-time setup step, not part of the pipeline.

### LINDAS adapter (operational ingest)

The `hydro_data_scraper` constructs SPARQL queries. Our adapter wraps this:

- **Input**: list of station configs, last-seen timestamps
- **Output**: `list[RawObservation]`
- **Protocol fit**: Maps cleanly to `StationDataSource.fetch_observations()`.
- **Incremental**: Pass `since` timestamps to the SPARQL query (or filter client-side).
- **Error handling**: Per-station — if one station fails, others proceed.
- **Deduplication**: At the store level (upsert on station + timestamp + parameter),
  not in the adapter.

**Confirmed: LINDAS supports time-range filtering.** The SPARQL endpoint accepts standard
`FILTER` clauses on `measurementTime`:

```sparql
FILTER(?measurementTime >= "2025-03-01T00:00:00Z"^^xsd:dateTime
    && ?measurementTime <= "2025-03-16T23:59:59Z"^^xsd:dateTime)
```

**Strategy**: Poll every ~10 minutes. Use incremental fetch with SPARQL FILTER since
the last-seen timestamp per station. This captures all 10-minute observations without
data loss.

**Critical limitation**: LINDAS retains only **40 days** of historical data. No backfill
beyond this window is possible via SPARQL. For historical data, CAMELS-CH (or direct
BAFU data service requests) must be used.

**Station availability**: ~170 automated BAFU stations have real-time LINDAS telemetry
(out of ~230 total BAFU gauging stations). All use the same 4-digit codes as CAMELS-CH.
The adapter should query all available stations, not just a preconfigured subset.

---

## 6. Temporal resolution: daily vs sub-daily

### The challenge

CAMELS-CH provides daily data. Operational BAFU provides 10-minute data. Models trained
on daily data may or may not generalize to sub-daily forecasting — this is an open
research question that v0b should explore.

### v0a strategy (this slice)

- **Train on daily CAMELS-CH data** (1981–2020)
- **Forecast at daily resolution** (daily discharge forecasts)
- **Ingest operational data at 10-min** but aggregate to daily for model input
- **Test the pipeline end-to-end** at daily resolution

### v0b strategy (next slice — temporal resolution experiments)

Explore daily→sub-daily generalization using additional public datasets:

| Dataset | Resolution | Coverage | Stations | Notes |
|---------|-----------|----------|----------|-------|
| **LamaH-CE** | Hourly | Central Europe (AT, DE, CH, CZ) | 882 (25 Swiss BAFU) | 1981–2017. Best sub-daily option for Swiss stations. Station ID mapping to BAFU codes needs verification. |
| **CAMELS-GB** | Hourly (15-min available) | UK | 671 | Well-documented, good sub-daily coverage |
| **CAMELS** (US) | Hourly (some sub-hourly) | USA | 671 | Original CAMELS dataset |

**Research questions for v0b** (publication-quality experiments):
1. Can a model trained on daily data produce skillful sub-daily forecasts?
2. What is the skill degradation from daily to hourly resolution?
3. Does fine-tuning on limited sub-daily data recover skill?
4. How does the answer depend on catchment characteristics (flashy vs slow)?

These experiments will be designed carefully for publication quality — proper
cross-validation, uncertainty quantification, comparison across catchment types.
The experimental design belongs in a separate document.

**Note**: The v0b experimental design must be publication-quality. This includes proper
cross-validation design (temporal out-of-sample, spatial leave-one-out), uncertainty
quantification, comparison across catchment types (flashy alpine vs slow lowland),
and benchmarking against naive baselines. A separate design document will be created
for the v0b experimental setup.

---

## 7. Alignment with architecture-context.md

### Confirmed decisions (no changes needed)

- Flow 2 steps 2.5–2.7 (rating curve) skipped in v0 ✓
- Flow 2 steps 2.8–2.10 (alerting) optional in v0 ✓
- `StationDataSource` Protocol for operational fetch ✓
- `ObservationStore` Protocol for persistence ✓
- Stage 1 QC rules match architecture-context.md § 2.3 ✓
- Station onboarding Flow 5 simplified per v0-scope.md § A4 ✓

### Suggested refinements

1. **`HistoricalObservationSource` Protocol**: The current `StationDataSource` Protocol
   is designed for incremental operational fetch (`since` parameter). Historical bulk
   import from CAMELS-CH has a different access pattern (local files, full date range,
   different source format). Consider adding a dedicated Protocol for historical import,
   or document that `StationDataSource` is reused with a "full range" `since` parameter.

2. **Daily aggregation: pre-computed, not query-time.** The full architecture
   (`architecture-context.md` § Data retention) specifies that **daily aggregates are
   retained permanently in PostgreSQL** as a pre-computed layer, aggregated using local
   timezone day boundaries. This is distinct from the raw sub-daily observations which
   follow the hot→cold→delete lifecycle.

   For v0: query-time aggregation is viable at v0 scale (~50 stations, 2.6M rows/year)
   and can serve as a stopgap. Query-time `date_trunc('day', timestamp)` GROUP BY
   completes in <1 second for 50 stations with existing indexes.

   For v1 (Nepal, 500 stations): the architecture's pre-computed daily aggregate approach
   becomes necessary. At 500 stations × 144 obs/day × 548-day hot window ≈ 39M rows in
   PostgreSQL, query-time aggregation remains feasible (<5s) but the permanent daily
   aggregate table provides:
   - Instant reads for daily models (no aggregation at query time)
   - Survival beyond the hot window (daily aggregates are permanent, raw obs are archived)
   - Timezone-correct day boundaries (computed once, correctly)

   **v0 implementation**: Start with query-time aggregation. Add the permanent daily
   aggregate computation as part of Flow 2 (after QC, before storage completes) when
   sub-daily operational data arrives. This aligns with the architecture's design.

3. **CAMELS-CH unit convention**: CAMELS-CH uses specific discharge (mm/d) which requires
   basin area for conversion to absolute discharge (m³/s). The adapter must have access to
   basin attributes. This means basin import (step 5.2) must happen before historical
   observation import (step 5.4) — which aligns with the Flow 5 sequencing diagram.

4. ~~**Weather observations for v0a**~~: **Resolved.** CAMELS-CH basin-mean precipitation
   and temperature are imported into the `historical_forcing` table via
   `HistoricalForcingStore.store_forcing()` (source = `"camels-ch"`). They are NOT stored
   as weather station observations. This aligns with v0-scope.md § A12 and the
   `historical_forcing` DB schema.

---

## 8. Implementation phases (Vertical Slice 1)

### Phase S1: PostgreSQL store implementations (foundation)

**Scope**: Implement `StationStore`, `ObservationStore`, `BasinStore`, `ParameterStore`,
`FlowRegimeConfigStore` against real PostgreSQL using SQLAlchemy Core + asyncpg.

**Out of scope**: `ForecastStore`, `WeatherForecastStore`, `AlertStore`, model stores
(Vertical Slice 3+).

### Phase S2: CAMELS-CH adapter + station onboarding

**Scope**: Adapter wrapping `camelsch` library. Flow 5 steps 5.1, 5.2, 5.4
(register stations, import basins, import historical observations).

**Depends on**: Phase S1 (stores must exist to persist data).

### Phase S3: Stage 1 QC service

**Scope**: QC service implementing range check, rate-of-change, frozen sensor,
spike detection, gross outlier. Flow 5 step 5.5 and Flow 2 step 2.3.

**Depends on**: Phase S1 (reads/writes observations).

### Phase S4: Baseline + flow regime computation

**Scope**: Flow 5 steps 5.8 (climatology quantiles, persistence baseline) and
5.9 (Q50/Q90 flow regime boundaries).

**Depends on**: Phase S3 (needs QC'd observations).

### Phase S5: LINDAS operational adapter

**Scope**: Adapter wrapping `hydro_data_scraper` SPARQL logic.
Flow 2 step 2.1 (operational fetch).

**Depends on**: Phase S1 (store), independent of Phases S2–S4.

### Phase S6: Flow 2 orchestration (Prefect flow)

**Scope**: Wire steps 2.1→2.2→2.3→2.4 into a Prefect flow. Optional 2.8–2.10.

**Depends on**: Phase S5 (LINDAS adapter) + Phase S3 (QC service).

### Phase S7: Flow 5 orchestration (onboarding script)

**Scope**: Wire steps 5.1→5.2→5.4→5.5→5.8→5.9 into a script or simple Prefect flow.

**Depends on**: Phases S2, S3, S4.

### Dependency graph

```json
{
  "phases": [
    {
      "id": "S1",
      "name": "PostgreSQL store implementations",
      "tasks": ["station-store", "observation-store", "basin-store", "parameter-store", "flow-regime-store"],
      "parallel": true
    },
    {
      "id": "S2",
      "name": "CAMELS-CH adapter + data import",
      "tasks": ["camelsch-adapter", "station-registration", "basin-import", "historical-obs-import"],
      "parallel": false,
      "depends_on": ["S1"]
    },
    {
      "id": "S3",
      "name": "Stage 1 QC service",
      "tasks": ["qc-rules", "qc-service", "qc-tests"],
      "parallel": false,
      "depends_on": ["S1"]
    },
    {
      "id": "S4",
      "name": "Baseline + flow regime computation",
      "tasks": ["climatology-baselines", "flow-regime-boundaries"],
      "parallel": true,
      "depends_on": ["S3"]
    },
    {
      "id": "S5",
      "name": "LINDAS operational adapter",
      "tasks": ["lindas-adapter", "lindas-tests"],
      "parallel": false,
      "depends_on": ["S1"]
    },
    {
      "id": "S6",
      "name": "Flow 2 orchestration",
      "tasks": ["prefect-flow-2"],
      "parallel": false,
      "depends_on": ["S3", "S5"]
    },
    {
      "id": "S7",
      "name": "Flow 5 orchestration (onboarding)",
      "tasks": ["onboarding-script"],
      "parallel": false,
      "depends_on": ["S2", "S3", "S4"]
    }
  ]
}
```

```
S1 (stores) ──┬── S2 (CAMELS-CH adapter) ──┐
              │                              │
              ├── S3 (QC service) ──┬── S4 (baselines) ── S7 (onboarding)
              │                     │
              └── S5 (LINDAS) ──────┴── S6 (Flow 2)
```

---

## 9. Open questions

### Resolved

1. ~~**LINDAS time-range query**~~: **Yes** — SPARQL FILTER on datetime works.
   40-day retention limit. See § 5.

2. ~~**PostgreSQL aggregation**~~: **Viable for v0.** Architecture plans permanent
   daily aggregates for v1. See § 7.

### Open

1. ~~**Weather observations in v0a**~~: **Resolved.** CAMELS-CH basin-mean meteo goes into
   `historical_forcing` table (source = `"camels-ch"`), not into `observations` as
   synthetic weather stations. See § 3 and § 7.4.

2. **Station selection for v0**: Which subset of CAMELS-CH stations? Criteria:
   - Must have LINDAS real-time telemetry (~170 of 331 CAMELS-CH stations)
   - Mix of catchment sizes (small flashy + large slow)
   - Mix of regulation types (unregulated preferred)
   - Known edge cases (glacier-fed, lake-regulated)
   - Suggested: 10 stations for development, expand to ~50 for validation

3. **Daily timestamp convention**: CAMELS-CH provides dates without time. Use midnight UTC?
   This affects joins with operational 10-min data when both exist for the same station.
   The architecture specifies "local timezone day boundaries" for daily aggregates (§ Data
   retention) — so daily CAMELS-CH data should use midnight local time (Europe/Zurich), not
   midnight UTC.

4. **Specific vs absolute discharge**: CAMELS-CH provides specific discharge (mm/d).
   SAPPHIRE stores absolute (m³/s). The adapter must convert. Should we also store
   specific discharge as a secondary parameter for research convenience?

5. ~~**QC rule configuration schema**~~: **Resolved.** Three-layer hierarchy
   (deployment defaults → per-station overrides → rule versioning) with time-step
   dimension. See `architecture-context.md` § QC rule configuration,
   `types-and-protocols.md` § QcRuleSet, `config-reference.toml` § `[qc_rules]`.

6. ~~**CAMELS-CH raw data verification**~~: **Resolved.** The raw Zenodo dataset contains
   absolute discharge (m³/s) and water levels. The `camelsch` library only exposes
   `discharge_spec` (mm/d), but the adapter can read m³/s directly from the raw CSVs.
   No basin area conversion needed. Water level data opens a future possibility for
   deriving rating curves, but this is a sideline — not in v0 scope.

7. **LamaH-CE Swiss station IDs**: What are the BAFU 4-digit codes for the 25 Swiss
   stations in LamaH-CE? Contact dataset authors or inspect supplementary metadata.

8. **Daily aggregate computation timing**: When is the permanent daily aggregate computed?
   Options: (a) as part of Flow 2 after QC, (b) as a separate scheduled task, (c) at
   query time with caching. The architecture says "aggregated using local timezone day
   boundaries" but doesn't specify the computation trigger.
