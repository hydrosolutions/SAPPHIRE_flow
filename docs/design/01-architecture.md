---
status: DRAFT
---

> **DRAFT** — This design doc has not completed the review maturity gate. Do not treat as authoritative until `status: READY`.

# Architecture

## Tech stack

| Layer              | Technology              | Rationale                                    |
|--------------------|-------------------------|----------------------------------------------|
| Language           | Python 3.11+            | Team expertise, ecosystem fit                |
| Data store         | PostgreSQL              | Multi-user, concurrent, proven, known by IT  |
| ML framework       | PyTorch                 | CPU + GPU, LSTM/transformer ecosystem        |
| Task orchestration | Prefect (self-hosted)   | Retries, dependencies, UI, manual triggers   |
| API                | FastAPI                 | Lightweight, async, auto-generated OpenAPI   |
| Dashboard          | FastAPI + HTMX          | One language, no JS build chain              |
| Auth               | fastapi-users + PG      | Registration, login, roles, sessions         |
| Reverse proxy      | Caddy                   | Automatic TLS via Let's Encrypt, zero-config HTTPS |
| Rate limiting      | Caddy rate_limit module | Protects API from abuse                      |
| Bulletin gen       | ieasyreports            | Excel template filling, existing library     |
| Maps               | Leaflet.js              | Lightweight, no API key, offline-capable     |
| Charts             | uPlot or Plotly.js      | Lightweight time series rendering            |
| Config             | TOML                    | Python-native (tomllib), human-readable      |
| Deployment         | Docker Compose          | Reproducible, simple, isolates from host OS  |
| Packaging          | uv + pyproject.toml     | Fast, modern, reproducible                   |
| Testing            | pytest                  | Standard, rich plugin ecosystem              |
| Linting/formatting | ruff                    | Fast, replaces flake8 + black + isort        |

## Docker Compose services

```
caddy       Caddy reverse proxy   TLS termination, rate limiting    :443, :80
db          PostgreSQL            App data + Prefect metadata       (internal)
pgbouncer   PgBouncer             Connection pooling (transaction mode) (internal)
prefect     Prefect server        Scheduler (internal only)         (internal)
worker      Prefect worker        Runs ingest + forecast flows      (internal)
api         FastAPI               REST API + dashboard              (internal)
```

Only Caddy is exposed to the network. All other services are internal to the Docker network. Prefect UI is accessible only via SSH tunnel or VPN -- it is NOT exposed externally.

## Connection pooling

PgBouncer sits between the `worker`/`api` services and PostgreSQL in transaction-pooling mode. Without it, 500 concurrent forecast tasks would exhaust PostgreSQL's default `max_connections` (100).

PgBouncer authenticates each service with its own database user via a `userlist.txt` file, preserving the least-privilege model. Each service connects to PgBouncer with its own credentials (`sapphire_api` or `sapphire_worker`), and PgBouncer forwards to PostgreSQL with that same user's privileges. The `sapphire_admin` user is never routed through PgBouncer.

Configuration:
- `pool_mode=transaction` — connections are returned to the pool after each transaction
- `default_pool_size=25` per service (worker + api = 50 pooled connections)
- `max_db_connections=100` — matches PostgreSQL's `max_connections`
- The `worker` and `api` services connect to PgBouncer, not directly to PostgreSQL
- The `prefect` service connects directly to PostgreSQL (Prefect manages its own connection pool)

**Compatibility notes**:
- SQLAlchemy/asyncpg uses prepared statements by default, which are incompatible with PgBouncer transaction mode. Disable them with `prepare_threshold=0` in the asyncpg connection parameters, or configure PgBouncer with `max_prepared_statements = 256` (requires PgBouncer 1.21+).
- Alembic migrations must bypass PgBouncer (they use advisory locks, which require session-level persistence). The migration command connects directly to PostgreSQL via `DATABASE_URL_DIRECT`, not through PgBouncer. See 07-deployment.md.
- `LISTEN/NOTIFY` is incompatible with transaction-mode pooling. If real-time push is needed in the future, use polling or a dedicated direct connection.

## System diagram

```
                    ┌──────────────────────┐
                    │   External APIs      │
                    │  ┌────────────────┐  │
                    │  │ MeteoSwiss NWP │  │  ICON-CH2-EPS ensemble (v0)
                    │  │ MeteoSwiss SMN │  │  weather station obs (v0)
                    │  │ hydro_scraper  │  │  river gauge data (v0)
                    │  │ sapphire-dg    │  │  ECMWF forecasts (v1)
                    │  │ ieasyhydro     │  │  station data (Central Asia)
                    │  │ (future)       │  │  station data (Nepal, ...)
                    │  └────────────────┘  │
                    └──────────┬───────────┘
                               │
                    ┌──────────v───────────┐
                    │   Ingest adapters    │
                    │   (DataSource        │
                    │    Protocol)         │
                    └──────────┬───────────┘
                               │
                    ┌──────────v───────────┐
                    │   PostgreSQL         │
                    │  ┌────────────────┐  │
                    │  │ time series    │  │  observations + forecasts
                    │  │ rating curves  │  │  versioned
                    │  │ model registry │  │  trained model metadata
                    │  │ model skill    │  │  verification scores
                    │  │ flood thresh.  │  │  alert levels per station
                    │  │ observation    │  │  edit audit trail
                    │  │   edits        │  │
                    │  │ bulletins      │  │  generated report log
                    │  │ access tokens  │  │  scoped external access
                    │  │ audit log      │  │  forecast adjustments
                    │  │ job history    │  │  (managed by Prefect)
                    │  └────────────────┘  │
                    └──────────┬───────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
   ┌──────────v──────┐ ┌──────v───────┐ ┌──────v───────┐
   │ Forecast engine │ │  Caddy       │ │  Prefect UI  │
   │ (ForecastModel  │ │  (:443/:80)  │ │  (internal)  │
   │  Protocol)      │ │  TLS + rate  │ │  SSH tunnel  │
   │                 │ │  limiting    │ │  only        │
   │  LSTM, ...      │ │      │       │ │  run status  │
   │  pydrology, ... │ │      v       │ │  manual      │
   └─────────────────┘ │  REST API   │ │  triggers    │
                       │  (FastAPI)   │ └──────────────┘
                       │              │
                       │  + dashboard │
                       │  + bulletins │
                       │  (ieasy-    │
                       │   reports)  │
                       └──────┬──────┘
                              │
                    ┌─────────v────────────┐
                    │   External consumers │
                    │  ┌────────────────┐  │
                    │  │ Scoped tokens  │  │  other hydromets
                    │  │ (read-only,    │  │  government agencies
                    │  │  station-      │  │  ministries
                    │  │  filtered)     │  │
                    │  └────────────────┘  │
                    └──────────────────────┘
```

## Package structure

```
SAPPHIRE_flow/
├── docs/design/            # These design documents
├── src/sapphire_flow/
│   ├── __init__.py
│   ├── protocols/          # All Protocol definitions (DataSource, ForecastModel, Repository, etc.)
│   ├── types/              # Shared domain types (Observation, ForecastEnsemble, ModelInputs, etc.)
│   ├── schemas/           # Pydantic boundary validation (JSONB fields)
│   ├── adapters/           # Data source adapters
│   │   ├── __init__.py
│   │   ├── meteoswiss_nwp.py  # ICON-CH2-EPS ensemble forecasts (v0)
│   │   ├── meteoswiss_smn.py  # SwissMetNet weather stations (v0)
│   │   ├── hydro_scraper.py   # BAFU/FOEN river gauges (v0)
│   │   ├── sapphire_dg.py     # ECMWF weather via dg-client (v1+)
│   │   └── ieasyhydro.py      # Central Asia stations
│   ├── models/             # Forecast model interface
│   │   ├── __init__.py
│   │   └── registry.py     # Model discovery + loading
│   ├── flows/              # Prefect flows
│   │   ├── __init__.py
│   │   ├── ingest.py       # Data ingestion (weather, stations, thresholds)
│   │   ├── forecast.py     # Forecast execution (station/basin/all)
│   │   ├── alerts.py       # Flood threshold checking + notifications
│   │   ├── verification.py # Model skill computation
│   │   ├── bulletin.py     # Bulletin generation via ieasyreports
│   │   └── training.py     # Model training (also standalone)
│   ├── services/           # Business logic (pure, no I/O framework deps)
│   │   ├── __init__.py
│   │   ├── alerting.py     # Flood threshold checking
│   │   ├── qc.py           # Automated quality control (range, rate-of-change)
│   │   ├── skill.py        # Metric computation
│   │   ├── rating.py       # Rating curve application
│   │   └── forecast_prep.py # Model input preparation, sanity checks, prepare_model_inputs
│   ├── store/              # Database layer
│   │   ├── __init__.py
│   │   ├── observations.py # ObservationStore implementation
│   │   ├── weather.py      # WeatherStore implementation
│   │   ├── forecasts.py    # ForecastStore implementation
│   │   ├── alerts.py       # AlertStore implementation (thresholds + alerts)
│   │   ├── skill.py        # SkillStore implementation
│   │   ├── bulletins.py    # BulletinStore implementation
│   │   ├── ratings.py      # Rating curve management
│   │   ├── stations.py     # Station metadata CRUD
│   │   ├── tokens.py       # Scoped access token management
│   │   └── migrations/     # Schema migrations (alembic)
│   ├── api/                # FastAPI application
│   │   ├── __init__.py
│   │   ├── app.py          # App factory
│   │   ├── auth.py         # User auth (fastapi-users) + token scoping
│   │   └── routes/
│   │       ├── stations.py     # Station CRUD + comparison
│   │       ├── forecasts.py    # Forecasts + adjustment + status
│   │       ├── observations.py # Observation editing + exclusion
│   │       ├── ratings.py      # Rating curve management
│   │       ├── alerts.py       # Flood alerts + thresholds
│   │       ├── bulletins.py    # Bulletin generation + download
│   │       ├── skill.py        # Model verification scores
│   │       ├── admin.py        # Token management, user admin
│   │       └── operations.py   # Manual triggers, health
│   ├── bulletin/           # Bulletin generation logic
│   │   ├── __init__.py
│   │   ├── engine.py       # ieasyreports integration
│   │   └── templates/      # Excel templates per hydromet
│   ├── dashboard/          # Optional HTMX dashboard
│   │   ├── templates/      # Jinja2 templates
│   │   │   ├── base.html
│   │   │   ├── overview.html       # Country map + status table
│   │   │   ├── alerts.html         # Flood priority inbox
│   │   │   ├── station.html        # Station detail + obs editing
│   │   │   ├── compare.html        # Multi-station comparison
│   │   │   ├── forecasts.html      # Model selection + review
│   │   │   ├── skill.html          # Model performance
│   │   │   ├── bulletin.html       # Bulletin builder
│   │   │   └── ratings.html        # Rating curve management
│   │   └── static/
│   └── config/
│       ├── __init__.py
│       └── settings.py     # TOML config loading (deployment settings, QC params — NOT station config)
├── tests/
├── docker-compose.yml
├── Caddyfile
├── Dockerfile
├── pyproject.toml
└── README.md
```

## Security architecture

### Network
- Caddy terminates TLS on ports 443/80 and reverse-proxies to the API on the internal Docker network
- PostgreSQL and Prefect are never exposed outside the Docker network
- Prefect UI is accessible only via SSH tunnel for IT staff

### Authentication
- Dashboard: cookie-based sessions with TOTP MFA (forecaster + admin roles)
- API consumers: short-lived JWT (15 min) with refresh token rotation
- External institutions: scoped bearer tokens (hashed, mandatory expiry)
- See 06-api.md for details

### Database access (least privilege)
Three PostgreSQL users:
- `sapphire_api` -- read-only on most tables, write on observation_edits, forecast_adjustments, bulletins
- `sapphire_worker` -- read-write on observations, forecasts, forecast_values, model_skill, alert_events
- `sapphire_prefect` -- owns the `prefect` database only, no access to application data

### Audit logging
Security-relevant events (logins, failed auth, admin actions, token creation/revocation, flow triggers) are logged to an append-only `audit_log` table. Observation edits and forecast adjustments have their own dedicated audit tables.

## Layering principle

```
routes/  -->  services/  -->  store/
flows/   -->  services/  -->  store/
```

- `routes/` and `flows/` are thin wiring layers (HTTP handling / Prefect orchestration)
- `services/` contains business logic: pure functions and classes with no framework dependencies
- `store/` provides data access behind repository Protocols (see store/protocol.py)
- This separation ensures business logic is testable without FastAPI, Prefect, or PostgreSQL

## Station configuration

Station metadata (code, name, location, basin, model assignment) is stored in the
`stations` database table — not in TOML files. This supports 500+ stations and
allows runtime management via the API/dashboard. TOML configuration is reserved
for deployment-level settings (database URLs, QC parameters, adapter config,
scheduling). Virtual stations (ungauged sites with derived runoff calculations)
are also managed in the database.

**Model configuration**: Model assignments (which model runs for which station,
artifact paths, fallback models) are stored in the `station_model_config` database
table. This supports runtime changes via API/dashboard without container restarts.
TOML serves as an optional bootstrap format for initial import
(`sapphire-flow import-model-config --file models.toml`). See 02-data-model.md
for the table schema and 04-models.md for the operational workflow.

## Repository Protocols

Defined in `protocols/stores.py`. Each Protocol is organized by entity — every
method is defined in exactly one Protocol, eliminating duplication. Flows and
routes depend on only the Protocols they need (narrow interfaces), and each fake
in tests implements methods in one place.

**Key design rule**: Repository Protocols contain only data access. Business logic
(e.g. preparing model inputs, computing aggregations) lives in `services/`.

```python
from typing import Protocol, runtime_checkable
from datetime import datetime
from uuid import UUID

@runtime_checkable
class StationStore(Protocol):
    def list_stations(self, kind: StationKind | None = None, basin_id: UUID | None = None,
                      limit: int = 50, after: str | None = None) -> tuple[list[StationConfig], str | None]: ...
    def get_station_by_id(self, station_id: UUID) -> StationConfig | None: ...
    def get_station_by_code(self, code: str) -> StationConfig | None: ...
    def create_station(self, info: StationInfo) -> StationConfig: ...
    def upsert_stations(self, stations: list[StationInfo]) -> int: ...
    def get_active_station_configs(self) -> list[StationConfig]: ...
    def list_basins(self) -> list[BasinInfo]: ...
    def upsert_model_assignment(self, station_id: UUID, model_id: str, model_version: str,
                                artifact_path: str, fallback_model_id: str | None = None,
                                fallback_artifact: str | None = None) -> None: ...


@runtime_checkable
class ObservationStore(Protocol):
    def upsert_observations(self, observations: list[Observation]) -> int: ...
    def insert_observations_no_overwrite(self, observations: list[Observation]) -> int: ...
    def get_observations(
        self, station_id: str, parameter_id: str,
        start: datetime, end: datetime,
    ) -> list[Observation]: ...
    def get_latest_observation(
        self, station_id: str, parameter_id: str,
    ) -> Observation | None: ...
    def get_previous_observations(
        self, station_param_pairs: list[tuple[str, str]],
    ) -> dict[tuple[str, str], Observation]: ...
    def update_quality_flags(
        self, flagged: list[tuple[Observation, int]],
    ) -> None: ...
    def detect_gaps(
        self, station_ids: list[str],
        lookback_days: int = 7,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[tuple[str, datetime, datetime]]: ...


@runtime_checkable
class WeatherStore(Protocol):
    def upsert_weather_forecasts(self, forecasts: list[WeatherForecast]) -> int: ...
    def get_weather_forecasts(
        self, station_id: str, parameter_id: str,
        start: datetime, end: datetime,
    ) -> list[WeatherForecast]: ...


@runtime_checkable
class ForecastStore(Protocol):
    """Single source of truth for all forecast data access."""
    def save_forecast(self, station: StationConfig, ensemble: ForecastEnsemble) -> Forecast: ...
    def get_forecasts_by_ids(self, ids: list[str]) -> list[Forecast]: ...
    def get_selected_forecasts_for_basin(self, basin_id: str) -> list[Forecast]: ...
    def get_all_selected_forecasts(self) -> list[Forecast]: ...
    def get_past_forecasts(
        self, station: StationConfig, lookback_days: int,
    ) -> list[Forecast]: ...
    def update_forecast_status(self, forecast_ids: list[str], status: ForecastStatus) -> None: ...


@runtime_checkable
class RatingCurveStore(Protocol):
    def get_active_rating_curve(self, station: StationConfig) -> RatingCurve | None: ...
    def save_rating_curve(self, rating_curve: RatingCurve) -> None: ...
    def list_rating_curves(self, station_id: UUID) -> list[RatingCurve]: ...


@runtime_checkable
class AlertStore(Protocol):
    """Combines threshold management and alert lifecycle (previously split
    across AlertRepository and ThresholdRepository)."""
    def upsert_thresholds(self, thresholds: list[FloodThreshold]) -> int: ...
    def get_thresholds(self, station_id: str, parameter_id: str) -> list[FloodThreshold]: ...
    def get_thresholds_batch(
        self, station_param_pairs: set[tuple[str, str]],
    ) -> dict[tuple[str, str], list[FloodThreshold]]: ...
    def raise_alert(
        self, station: StationConfig, forecast: Forecast, lead_time: int,
        threshold: FloodThreshold,
        exceedance_fraction: float | None = None,
    ) -> None: ...
    def raise_observation_alert(self, observation: Observation, threshold: FloodThreshold) -> None: ...
    def resolve_stale_alerts(self, station: StationConfig, forecast: Forecast) -> None: ...
    def resolve_observation_alerts(self, station_id: str, parameter_id: str) -> None: ...
    def get_unacknowledged_danger_alerts(
        self, station_id: str | None = None, source: AlertSource | None = None,
    ) -> list[AlertEvent]: ...


@runtime_checkable
class SkillStore(Protocol):
    """Skill score storage only. Reads forecasts and observations via
    ForecastStore and ObservationStore (injected into the skill service)."""
    def save_skill_scores(self, station: StationConfig, model_id: str, metrics: dict[str, float]) -> None: ...


@runtime_checkable
class BulletinStore(Protocol):
    """Bulletin record storage only. Reads forecasts via ForecastStore
    (injected into the bulletin flow)."""
    def save_bulletin(
        self, scope: BulletinScope, basin_id: str | None,
        template_id: str, path: str, forecast_ids: list[str],
    ) -> None: ...
    def get_bulletin(self, bulletin_id: UUID) -> Bulletin | None: ...
    def list_bulletins(
        self, scope: BulletinScope | None = None, basin_id: str | None = None,
    ) -> list[Bulletin]: ...


@runtime_checkable
class TrainingStore(Protocol):
    def prepare_training_data(self, station_id: str) -> TrainingDataset: ...
    def log_training_result(self, station_id: str, result: TrainResult) -> None: ...
```

### How flows use these Protocols

Previously, `BulletinRepository` and `SkillRepository` duplicated forecast query
methods. Now, flows that need both forecast data and their own storage receive
multiple stores:

```python
# Bulletin flow receives ForecastStore + BulletinStore
def generate_bulletin(
    forecast_store: ForecastStore,
    bulletin_store: BulletinStore,
    ...
): ...

# Skill flow receives ForecastStore + ObservationStore + SkillStore
def compute_model_skill(
    forecast_store: ForecastStore,
    observation_store: ObservationStore,
    skill_store: SkillStore,
    ...
): ...

# Forecast flow receives RatingCurveStore separately for discharge conversion
def forecast_station(
    station: StationConfig,
    forecast_store: ForecastStore,
    rating_curve_store: RatingCurveStore,
    ...
): ...
```

Each method is defined once, tested once, faked once.

### What moved to services

`prepare_model_inputs` (previously on `ForecastRepository`) is business logic —
it filters excluded observations, joins weather data, and constructs `ModelInputs`.
It now lives in `services/forecast_prep.py` as a pure function that reads from
`ObservationStore`, `WeatherStore`, and `ForecastStore`:

```python
# services/forecast_prep.py
def prepare_model_inputs(
    station: StationConfig,
    observation_store: ObservationStore,
    weather_store: WeatherStore,
) -> ModelInputs: ...
```

Domain types (`Observation`, `Forecast`, `AlertEvent`, `FloodThreshold`, etc.)
referenced here are defined in `sapphire_flow.types`. Adapter-specific and
model-specific types are documented in 03-adapters.md and 04-models.md.

## Health check endpoints

Two health endpoints serve different purposes:

- `GET /api/v1/ping` — returns `200 OK` with no database access. Used by load balancers and external uptime monitors to verify the process is alive.
- `GET /api/v1/health` — returns system status including database connectivity, last ingest/forecast times, active alert counts, and disk usage. Used for operational monitoring. Times out gracefully (returns HTTP 503 with partial status) if the database does not respond within 5 seconds.

## Review History

| Round | Date | Reviewers | Blocking | Advisory | Status |
|-------|------|-----------|----------|----------|--------|
