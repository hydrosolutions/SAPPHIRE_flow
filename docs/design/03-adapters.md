---
status: DRAFT
---

> **DRAFT** — This design doc has not completed the review maturity gate. Do not treat as authoritative until `status: READY`.

# Data Source Adapters

## Adapter pattern

Each hydromet (or data provider) gets an adapter module that implements a
common Protocol. The rest of the system never talks to external APIs directly —
only through adapters.

## DataSource Protocol

```python
from typing import Protocol, runtime_checkable
from datetime import datetime

@runtime_checkable
class WeatherForecastSource(Protocol):
    """Fetches NWP ensemble forecasts (e.g. ICON-CH2-EPS, ECMWF)."""
    def fetch_forecasts(
        self,
        station_ids: list[str],
        issued_after: datetime,
    ) -> list[WeatherForecast]: ...


@runtime_checkable
class WeatherReanalysisSource(Protocol):
    """Fetches historical weather reanalysis data for model training,
    hindcasting, and skill metric calculation (e.g. ERA5, COSMO-REA6)."""
    def fetch_historical(
        self,
        station_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> list[Observation]: ...


@runtime_checkable
class StationDataSource(Protocol):
    def fetch_observations(
        self,
        station_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> list[Observation]: ...

    def list_stations(self) -> list[StationInfo]: ...


@runtime_checkable
class ThresholdSource(Protocol):
    def fetch_flood_thresholds(
        self,
        station_ids: list[str],
    ) -> list[FloodThreshold]: ...
```

Adapters return domain types (Observation, WeatherForecast, StationInfo, FloodThreshold), never raw API responses. Parsing happens inside the adapter.

## Adapter domain types

These types live in `sapphire_flow.types` and are shared across all adapters and the model interface.

**Resolved**: Domain types used across adapters and the model interface live in `sapphire_flow.types`. Types used only within SAPPHIRE_flow (Bulletin, AccessToken, ObservationEdit) also live here but are not part of the model collaborator's contract. When extraction to a shared package is needed, only the Protocol signatures and their parameter/return types need to move.

```python
from datetime import datetime
from typing import NamedTuple

class Observation(NamedTuple):
    station_code: str
    parameter: str          # e.g. "precipitation", "water_level"
    timestamp: datetime
    value: float
    quality_flag: int | None = None

class WeatherForecast(NamedTuple):
    station_code: str
    parameter: str
    issued_at: datetime
    lead_time_minutes: int
    member: int             # ensemble member index (0 = control/deterministic)
    value: float
```

Weather forecast ensembles (e.g. ECMWF's 51 members) are preserved through ingest. The `member` field indexes ensemble members, with 0 reserved for the control/deterministic run. This is critical for propagating meteorological uncertainty into hydrological forecasts.

```python
class StationInfo(NamedTuple):
    code: str
    name: str
    lon: float
    lat: float
    elevation_m: float | None = None
    kind: str               # "weather", "river", or "virtual"
    basin_code: str | None = None
    metadata: dict[str, Any] | None = None

class FloodThreshold(NamedTuple):
    station_code: str
    parameter: str
    level: str              # "normal", "watch", "warning", "danger"
    value: float
    unit: str               # e.g. "m_gauge_zero", "m_asl", "m3s"
    valid_from_month: int | None = None  # 1-12, nullable (null = year-round)
    valid_to_month: int | None = None    # 1-12, nullable (null = year-round)
```

These are immutable value types — adapters construct them from raw API responses.
The store layer maps them to database rows. Models receive `ModelInputs`
(defined in 04-models.md), not raw `Observation` objects.

## NotificationSink Protocol

Flood alert notifications use a pluggable sink, decoupled from Prefect:

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class NotificationSink(Protocol):
    def send(self, subject: str, body: str, severity: FloodLevel) -> None: ...
```

Built-in implementations:

- `EmailSink` — sends via SMTP (configured in config.toml)
- `WebhookSink` — POSTs JSON to a URL (integrates with Telegram bots, SMS gateways, etc.)

At least one sink must be configured during deployment. Danger-level alerts
always trigger notification. Warning-level notifications are configurable.

ThresholdSource is a separate Protocol. Adapters that support it implement both StationDataSource and ThresholdSource. The ingest flow checks `isinstance(adapter, ThresholdSource)` before fetching thresholds. Thresholds can also be configured manually via the dashboard or TOML config.

## Planned adapters

| Adapter          | Implements                           | Data source                    | Region        | Phase |
|------------------|--------------------------------------|--------------------------------|---------------|-------|
| meteoswiss_nwp   | WeatherForecastSource                | MeteoSwiss ICON-CH2-EPS (GRIB2) | Switzerland | v0    |
| meteoswiss_smn   | StationDataSource                    | MeteoSwiss SwissMetNet (OGD)   | Switzerland   | v0    |
| hydro_scraper    | StationDataSource                    | hydro_data_scraper (BAFU/FOEN) | Switzerland   | v0    |
| sapphire_dg      | WeatherForecastSource                | sapphire-dg-client (ECMWF+)   | Global        | v1    |
| ieasyhydro       | StationDataSource, ThresholdSource   | ieasyhydro-python-sdk          | Central Asia  | v1    |
| (future)         | StationDataSource                    | TBD                            | Nepal         | v1    |

**v0 weather data strategy**: Two MeteoSwiss adapters serve complementary roles:

1. **`meteoswiss_nwp`** implements `WeatherForecastSource` — fetches ICON-CH2-EPS
   ensemble forecasts (GRIB2 via STAC API) for operational forecasting. 21 members,
   5-day horizon, hourly. Same interface as `sapphire_dg`, so switching to ECMWF for
   v1 requires only a config change.

2. **`meteoswiss_smn`** implements `StationDataSource` — fetches sub-daily weather
   observations (10-min and hourly) from ~160 SwissMetNet automatic weather stations
   via MeteoSwiss OGD. This provides the **historical forcing data for model training**.
   Key parameters: precipitation (`rre150h0`), temperature (`tre200h0`), humidity
   (`ure200h0`), radiation (`gre000h0`), wind (`fkl010h0`), snow depth (`htoauths`),
   reference ET (`erefaoh0`). Data available from 1981–present.

**Training data approach**: Models are trained on SMN station observations co-located
with (or near) the river gauges they forecast for. This mirrors the Nepal approach
where models will train on DHM station weather observations + ERA5-Land. The Swiss
case study validates the station-based training workflow before Nepal deployment.

**Why not gridded reanalysis for v0?** MeteoSwiss provides daily gridded climate data
(RhiresD, TabsD — 1 km, 1961–present) but these are **daily only**. Sub-daily gridded
data (ICON-CH2-EPS) is only retained for 24 hours. Bridging the daily gridded
historical record to the sub-daily operational forecasts requires temporal
disaggregation and bias correction — research that isn't ready yet. Using station
observations sidesteps this entirely and gives us richer sub-daily data (10-min
resolution since 1981). Gridded approaches are deferred to a future research phase
once archived NWP data provides enough overlap for bias correction calibration.

**Nepal parallel**: Nepal will use ERA5-Land (hourly, ~9 km) for historical training
forcing and ECMWF IFS for operational forecasts, both bias-corrected against DHM
station observations. Switzerland tests the same station-observation-centric training
workflow but with denser station coverage, surfacing issues early.

### MeteoSwiss OGD — NWP model details

Two ensemble models are available:

| Attribute | ICON-CH1-EPS | ICON-CH2-EPS |
|-----------|--------------|--------------|
| Ensemble members | 11 | 21 |
| Horizontal resolution | ~1 km | ~2.1 km |
| Forecast horizon | 33 hours | 120 hours (5 days) |
| Temporal output | 1 hour | 1 hour |
| Model runs | Every 3 hours | Every 6 hours |
| Grid type | Native icosahedral | Native icosahedral |

**v0 uses ICON-CH2-EPS** — 21 ensemble members with 5-day lead time at hourly
resolution. This is a genuine ensemble, not a deterministic-wrapped-as-single-member
workaround. ICON-CH1-EPS (33h horizon, 11 members) can supplement for short-range
high-resolution needs.

### Data format and access

- **Format**: GRIB2 (binary, one file per model run × lead time × variable)
- **Discovery**: STAC API at `https://data.geo.admin.ch/api/stac/v1/`
- **Collections**: `ch.meteoschweiz.ogd-forecasting-icon-ch1`, `ch.meteoschweiz.ogd-forecasting-icon-ch2`
- **Retention**: Files available for **24 hours only** after publication — the adapter must download promptly
- **Authentication**: None required (free OGD)
- **Grid-only**: No pre-extracted point forecasts. The adapter must extract nearest grid points to station coordinates from the GRIB2 grid.

### MeteoSwiss NWP adapter implementation (`meteoswiss_nwp`)

The adapter:
1. Queries the STAC API to discover latest ICON-CH2-EPS files for target parameters
2. Downloads GRIB2 files for each lead time
3. Extracts values at the nearest grid point to each station's coordinates using `cfgrib` + `xarray`
4. Constructs `WeatherForecast` NamedTuples (one per station × parameter × lead time × ensemble member)

**Target parameters** (hydrologically relevant subset of ICON-CH2-EPS):

| Shortname | Description | Unit | Aggregation |
|-----------|-------------|------|-------------|
| TOT_PREC | Total precipitation (accumulated) | kg/m²·s | Accumulation |
| T_2M | 2m temperature | K | Instant |
| TD_2M | 2m dew point temperature | K | Instant |
| ASWDIR_S | Direct shortwave radiation (surface) | W/m² | Average |
| ASWDIFD_S | Diffuse shortwave radiation (surface) | W/m² | Average |
| W_SNOW | Snow depth water equivalent | kg/m² | Instant |
| SNOWLMT | Snow fall limit height (MSL) | m | Instant |

Additional parameters (U_10M, V_10M for wind; RAIN_GSP, SNOW_GSP for rain/snow
partitioning) can be added as model requirements evolve. The full ICON-CH2-EPS
parameter list is available via the `params_icon-ch2-eps.csv` asset in the STAC
collection.

**Dependencies**: `cfgrib`, `xarray`, `eccodes` (for GRIB2 decoding). These are
build dependencies only for the MeteoSwiss adapter — other adapters (sapphire-dg)
don't need them.

**Station mapping**: BAFU hydrological station codes (2009, 2135, etc.) don't match
MeteoSwiss station codes. The adapter maps station lat/lon to the nearest ICON grid
point. With ~2 km resolution, the nearest grid point is sufficient for
catchment-scale hydrological input.

**Caching and archiving**: GRIB2 files are cached locally (database-backed, per the
adapter resilience pattern) since they're only available for 24 hours from
MeteoSwiss. Cache enables: (a) retry on parse failure, (b) serving stale data
during API outages, (c) avoiding redundant downloads within the same model run.

**Beyond the 24-hour cache, all downloaded GRIB2 data is archived permanently.**
This builds an NWP hindcast archive that enables future bias correction between
ICON-CH2-EPS and station observations, and supports model training on NWP-like
inputs. The archive stores extracted point values in the `weather_forecasts` DB table
(not raw GRIB2 files) — storage is modest (~21 members × 120 lead times × N stations
× N parameters per model run, 4 runs/day). See "NWP forecast archiving" section below.

### MeteoSwiss SMN adapter implementation (`meteoswiss_smn`)

The adapter fetches weather observations from SwissMetNet automatic stations via
MeteoSwiss OGD (STAC API at `data.geo.admin.ch`).

**STAC collections**:
- `ch.meteoschweiz.ogd-smn` — full measurement program (~160 stations)
- `ch.meteoschweiz.ogd-smn-precip` — precipitation-only stations (~100 stations)

**Data format**: CSV, semicolon-delimited, Latin-1 encoding. Per-station files,
up to 5.3 MB. Available granularities: 10-min, hourly, daily, monthly, yearly.
The adapter fetches **hourly** data as the primary granularity for training.

**Key parameters for hydrological training**:

| Shortname | Description | Granularity | Unit |
|-----------|-------------|-------------|------|
| rre150h0 | Precipitation; hourly total | H | mm |
| tre200h0 | Air temperature 2m; hourly mean | H | °C |
| ure200h0 | Relative air humidity 2m; hourly mean | H | % |
| gre000h0 | Global radiation; hourly mean | H | W/m² |
| fkl010h0 | Wind speed scalar; hourly mean | H | m/s |
| htoauths | Snow depth (automatic); hourly | H | cm |
| erefaoh0 | Reference evaporation (FAO); hourly | H | mm/h |
| tde200h0 | Dew point 2m; hourly mean | H | °C |

**Station metadata**: Three-letter identifiers (e.g. BER, KLO). Metadata CSV
provides coordinates, altitude, and parameter inventory with start/end dates.
The adapter maps SMN station coordinates to nearby BAFU river gauge stations.

**Historical data**: Time series start from 1981 (introduction of automatic
measurements). Some stations have manual observations from earlier periods stored
as 10-minute values. For training, the hourly archive from 1981–present provides
sufficient history for most ML and conceptual models.

**Implements**: `StationDataSource` Protocol — same as `hydro_scraper`. The
`ingest_stations` flow can accept multiple `StationDataSource` adapters (one for
river data from BAFU, one for weather data from SMN).

### NWP forecast archiving

ICON-CH2-EPS forecasts are only retained by MeteoSwiss for 24 hours. To build a
hindcast archive for future bias correction and NWP-based model training, the
`meteoswiss_nwp` adapter **permanently stores all fetched forecast data** in the
`weather_forecasts` table.

**What is archived**: Extracted point values (not raw GRIB2 files) for all target
parameters at all configured station locations. Each model run (4/day × 21 members
× ~120 hourly lead times × N stations × N parameters) is stored with full provenance
(issued_at, member index, lead_time_minutes).

**Storage estimate**: For 20 stations × 7 parameters × 21 members × 120 lead times
× 4 runs/day = ~1.4M rows/day, ~500M rows/year. At ~50 bytes/row overhead, this
is ~25 GB/year — manageable for a PostgreSQL instance with proper partitioning.

**Purpose**: Once 6–12 months of archive accumulates, it enables:
- Quantile mapping bias correction between NWP and station observations
- Training models directly on NWP forecast inputs (eliminating the NWP-observation
  mismatch at inference time)
- Evaluating NWP forecast skill at station locations

**Retention**: Archived weather forecasts follow the same partitioning as
`forecast_values` (monthly). No automatic purge — operational data is retained
indefinitely. This is a permanent dataset, not a cache.

### Local forecast CSVs (available, deferred)

MeteoSwiss now provides station-based local forecast CSVs ("Lokalprognosen") for
~6,000 points across Switzerland with a 9-day horizon, updated hourly. Parameters
include temperature, precipitation (with quantiles), wind, radiation, and cloud
cover. Format: CSV, ~300 KB per daily file.

These are attractive for v0 because they avoid GRIB2 complexity entirely — no
cfgrib/eccodes dependency, no grid-point extraction. However, they provide
**quantiles (10th/90th percentile) rather than individual ensemble members**, which
limits uncertainty propagation into hydrological models.

**Decision**: v0 uses ICON-CH2-EPS (full ensemble) for operational forecasts. Local
forecast CSVs may be added as an alternative `WeatherForecastSource` implementation
in the future, particularly for deployments where GRIB2 processing is impractical
or where quantile-based forcing is sufficient.

## Adding a new hydromet

To support a new hydromet, a developer:

1. Creates a new file in `sapphire_flow/adapters/` (e.g. `nepal.py`)
2. Implements `StationDataSource` (and optionally `WeatherForecastSource` / `WeatherReanalysisSource`)
3. Registers the adapter in the deployment's TOML config
4. Done — no changes to flows, models, API, or dashboard

## Resilience

Each adapter handles its own retry logic and caching:

- **Retry**: Exponential backoff on transient failures (network, 5xx)
- **Local cache**: Last successful fetch is cached in the database (a `cache` table or the existing observation/weather tables). Database-backed caching survives container restarts, which is critical during extended API outages. Filesystem caching is not used — Docker container filesystems are ephemeral unless explicitly mounted as volumes.
- **Fallback**: If fetch fails after retries, return cached data with a staleness flag. Each adapter defines a `max_cache_age` (e.g. 12 hours for weather forecasts, 24 hours for station observations). When cache exceeds max age: (a) data is still returned but flagged as `critically_stale`, (b) the forecast flow refuses to use critically stale weather data for new forecasts and logs a prominent warning, (c) an operational alert is triggered. The forecaster must consciously decide to override the staleness check via manual trigger.
- **Logging**: All fetch attempts logged with status, duration, record count
- **Circuit breaker**: After N consecutive failures (default 5), the adapter stops attempting the external API for a configurable cooldown period (default 30 minutes). This avoids wasting retry budget and reduces log noise during extended outages. The circuit resets automatically after the cooldown.

The Prefect flow wrapping the adapter adds an additional retry layer,
but the adapter itself should be robust to transient failures.

## Configuration

Each adapter is configured via the deployment TOML:

```toml
# v0 (Switzerland)
[adapters.weather_forecast]
type = "meteoswiss_nwp"
archive = true                 # permanently store all fetched NWP data
max_cache_age_hours = 12       # refuse critically stale data beyond this

[adapters.weather_stations]
type = "meteoswiss_smn"
max_cache_age_hours = 24

[adapters.river_stations]
type = "hydro_scraper"
max_cache_age_hours = 24

# v1 (Nepal) — same adapter interface, different implementations
# [adapters.weather_forecast]
# type = "sapphire_dg"
# api_key = "${SAPPHIRE_DG_API_KEY}"
# archive = true
#
# [adapters.stations]
# type = "ieasyhydro"
# api_key = "${IEASYHYDRO_API_KEY}"
```

`tomllib` does not support environment variable interpolation natively. The config
loader in `config/settings.py` resolves `${VAR}` references by substituting
`os.environ[VAR]` after parsing. Unresolved references raise a startup error.

Secrets are always environment variables, never in config files.

## Review History

| Round | Date | Reviewers | Blocking | Advisory | Status |
|-------|------|-----------|----------|----------|--------|
