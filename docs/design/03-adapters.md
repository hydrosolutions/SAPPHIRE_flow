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
class WeatherDataSource(Protocol):
    def fetch_forecasts(
        self,
        station_ids: list[str],
        issued_after: datetime,
    ) -> list[WeatherForecast]: ...

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
    kind: str               # "weather" or "river"
    basin_code: str | None = None
    metadata: dict[str, Any] = {}

class FloodThreshold(NamedTuple):
    station_code: str
    parameter: str
    level: str              # "normal", "watch", "warning", "danger"
    value: float
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
    def send(self, subject: str, body: str, severity: str) -> None: ...
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
| meteoswiss       | WeatherDataSource                    | MeteoSwiss open data API       | Switzerland   | v0    |
| hydro_scraper    | StationDataSource                    | hydro_data_scraper (BAFU/FOEN) | Switzerland   | v0    |
| sapphire_dg      | WeatherDataSource                    | sapphire-dg-client (ECMWF+)   | Global        | v1    |
| ieasyhydro       | StationDataSource, ThresholdSource   | ieasyhydro-python-sdk          | Central Asia  | v1    |
| (future)         | StationDataSource                    | TBD                            | Nepal         | v1    |

**v0 weather data**: The MeteoSwiss open data API provides free access to
numerical weather prediction data (ICON/COSMO models) for Switzerland. This
replaces sapphire-dg during the Swiss development phase — no API key or
subscription needed. The `meteoswiss` adapter implements `WeatherDataSource`
with the same interface as `sapphire_dg`, so switching to ECMWF via sapphire-dg
for v1 requires only a config change, no code changes in flows or models.

**Note**: MeteoSwiss forecasts may be deterministic (single run) rather than
ensemble. If so, the adapter wraps the deterministic forecast as a single-member
ensemble (`member=0`). Models and the alert system handle single-member ensembles
gracefully — exceedance probability is binary (0% or 100%). For proper ensemble
testing during v0, recorded ECMWF fixtures from sapphire-dg can supplement
MeteoSwiss data.

## Adding a new hydromet

To support a new hydromet, a developer:

1. Creates a new file in `sapphire_flow/adapters/` (e.g. `nepal.py`)
2. Implements `StationDataSource` (and optionally `WeatherDataSource`)
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
[adapters.weather]
type = "sapphire_dg"
api_key = "${SAPPHIRE_DG_API_KEY}"  # env var reference
base_url = "https://api.sapphire-dg.example.com"
max_cache_age_hours = 12    # refuse critically stale data beyond this

[adapters.stations]
type = "ieasyhydro"
api_key = "${IEASYHYDRO_API_KEY}"
base_url = "https://api.ieasyhydro.example.com"
max_cache_age_hours = 24
```

`tomllib` does not support environment variable interpolation natively. The config
loader in `config/settings.py` resolves `${VAR}` references by substituting
`os.environ[VAR]` after parsing. Unresolved references raise a startup error.

Secrets are always environment variables, never in config files.
