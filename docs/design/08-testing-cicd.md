# Testing and CI/CD

## Design principles

The SAPPHIRE_Forecast_Tools experience taught us what happens without a testing
strategy: every change is risky, debugging is archaeology, and "it works on my
machine" is the norm. This repo takes the opposite approach.

**Core rules**:
1. Every component is testable in isolation — no component requires the full stack
2. Tests run fast by default — slow tests are opt-in and clearly marked
3. Real data (Swiss public hydro data) validates the full pipeline
4. CI enforces quality on every PR — nothing merges without passing tests
5. A staging environment on AWS runs continuously with Swiss data
6. Local development mirrors production exactly (same Docker Compose)

## Test pyramid

```
         ╱╲
        ╱E2E╲          ~10 tests, minutes, docker-compose
       ╱──────╲
      ╱Integration╲    ~50-100 tests, seconds, real PG via testcontainers
     ╱──────────────╲
    ╱   Unit tests    ╲  ~500+ tests, milliseconds, no I/O
   ╱────────────────────╲
```

### Unit tests (fast, many, no I/O)

Test business logic in `services/`, domain types, pure functions.
These run in milliseconds with no external dependencies.

**What's tested**:
- `services/qc.py` — range checks, rate-of-change (per-hour normalization)
- `services/alerting.py` — threshold comparison, alert lifecycle
- `services/skill.py` — metric computation (NSE, CRPS)
- `services/rating.py` — rating curve application
- `services/forecast_prep.py` — model input preparation, sanity checks
- Domain types — `GeoCoord` validation, `ForecastEnsemble` construction
- Adapter parsing — raw API response → domain types (with fixtures)
- Bikram Sambat calendar conversion — round-trip BS↔Gregorian, BS new year boundary (mid-April), month-length edge cases, dates near the lookup table range limits
- Timezone conversion — UTC+5:45 (Nepal) edge cases: midnight NPT = 18:15 UTC previous day, "today" query spanning two UTC dates, pentadal boundary in NPT vs UTC, Bikram Sambat new year boundary in UTC
- Seasonal flood threshold queries — non-wrapping range (Jun-Sep), wrapping range (Nov-Mar), exact boundary months, NULL months (year-round), seasonal precedence over year-round
- Pentadal/dekadal aggregation — Feb 28 non-leap, Feb 29 leap, 30-day vs 31-day months, variable pentad length (3-6 days), correct aggregation method per parameter (sum vs mean)

**How**:
- Fakes for all repository Protocols (in-memory implementations)
- No database, no network, no filesystem
- Deterministic: inject clocks and seeded RNGs

```python
# Example: testing QC service with a fake
def test_range_check_flags_negative_precipitation():
    qc = QualityCheckService(config=QCConfig(
        bounds={"precipitation": Bounds(min=0.0, max=300.0)}
    ))
    obs = Observation(station_code="CH-2009", parameter="precipitation",
                      timestamp=datetime(2026, 7, 1), value=-5.0)
    result = qc.check_observations([obs])
    assert result[0][1] == 2  # flag 2 = failed range check
```

### Integration tests (real PostgreSQL, seconds)

Test the database layer, migrations, and repository implementations against
a real PostgreSQL instance. Uses **testcontainers** — spins up a disposable
PG container per test session automatically.

**What's tested**:
- All `store/` modules against real PostgreSQL
- Alembic migrations (up AND down)
- Observation upsert behavior (ON CONFLICT)
- Time series queries with partitioned tables
- Concurrent writes (simulating parallel forecast storage)
- Index effectiveness on realistic data volumes

**How**:
- `testcontainers-python` — no manual Docker setup needed
- One PG container per test session (fast startup via connection pooling)
- Each test gets a clean schema (transaction rollback or `TRUNCATE`)
- Migrations tested separately: apply all up, then all down, then all up again

```python
import pytest
from testcontainers.postgres import PostgresContainer

@pytest.fixture(scope="session")
def pg():
    with PostgresContainer("postgres:16.4") as pg:
        # Run migrations
        run_alembic_upgrade(pg.get_connection_url())
        yield pg

@pytest.fixture
def db_session(pg):
    conn = pg.get_connection_url()
    session = create_session(conn)
    yield session
    session.rollback()  # clean slate per test
```

**CI vs local**: Integration tests detect their environment. In CI (when
`DATABASE_URL` is set), they use the pre-configured GitHub Actions PostgreSQL
service. Locally (when `DATABASE_URL` is unset), they use testcontainers to
spin up a disposable PostgreSQL container. The `conftest.py` fixture checks
`os.environ.get("DATABASE_URL")` and branches accordingly.

### Adapter contract tests (real external APIs, opt-in)

Test that adapters correctly parse real API responses. These hit real
endpoints and are **not run in CI by default** — they're opt-in via
`pytest -m adapter_contract`.

**What's tested**:
- `hydro_scraper` adapter against BAFU/FOEN public API
- Response parsing produces valid domain types
- Error handling for API downtime, rate limiting
- Data freshness (recent observations exist)

**What's NOT tested here**:
- Adapters for Nepal data sources (no access until agreement signed)
- Weather API (requires API key) — tested with recorded fixtures instead

```python
@pytest.mark.adapter_contract
def test_hydro_scraper_fetches_real_observations():
    adapter = HydroScraperAdapter(base_url="https://api.existenz.ch")
    observations = adapter.fetch_observations(
        station_ids=["2009"],  # Rhein - Basel
        start=datetime.now() - timedelta(days=7),
        end=datetime.now(),
    )
    assert len(observations) > 0
    assert all(isinstance(o, Observation) for o in observations)
    assert all(o.station_code == "2009" for o in observations)
```

For adapters that require API keys or aren't publicly accessible, we use
**recorded fixtures** (VCR/responses library): record a real response once,
replay in CI forever.

```python
@responses.activate
def test_sapphire_dg_parses_ecmwf_ensemble():
    responses.add(
        responses.GET, "https://api.sapphire-dg.example.com/forecasts",
        json=load_fixture("ecmwf_51_member_response.json"),
    )
    adapter = SapphireDgAdapter(api_key="test", base_url="...")
    forecasts = adapter.fetch_forecasts(station_ids=["2009"], issued_after=...)
    assert len(forecasts) == 51 * 15  # 51 members × 15 lead times
    assert all(isinstance(f, WeatherForecast) for f in forecasts)
```

### E2E tests (full stack, docker-compose, minutes)

Test the complete pipeline: ingest → forecast → alert → API response.
Uses `docker-compose.test.yml` with Swiss data.

**What's tested**:
- Full ingest pipeline with Swiss station data (hydro_scraper)
- Forecast execution with a simple test model (linear regression)
- Alert generation when thresholds are exceeded
- API responses contain expected forecast data
- Database contains expected records after a full cycle

**How**:
- `docker-compose.test.yml` — mirrors production but with:
  - Test model (linear regression, fast, deterministic)
  - Swiss reference stations (publicly available, no API keys)
  - Pre-seeded flood thresholds for test stations
  - Smaller data volume (7-day lookback instead of full history)
- Run via: `make test-e2e` or `docker compose -f docker-compose.test.yml up --abort-on-container-exit`
- CI runs these on merge to main (not on every PR — too slow)
- `docker-compose.test.yml` includes a PgBouncer service between the test
  database and the application services, mirroring production topology.
  This catches PgBouncer-specific issues (prepared statement compatibility,
  transaction-mode limitations) that direct-to-PostgreSQL integration tests
  would miss.

**CI cleanup**: The `docker compose down -v` step runs with `if: always()` to
clean up containers and volumes even if the test run is cancelled or times out.
This prevents orphaned resources from accumulating on CI runners.

## Swiss reference dataset

Swiss hydrological data from BAFU/FOEN is publicly available, well-maintained,
and has the same structure as what we'll see from Nepal (water level,
precipitation, discharge). We use it as our integration test dataset.

### Reference stations

| Station | Code | River | Parameters | Why |
|---------|------|-------|------------|-----|
| Basel | 2009 | Rhein | water level, discharge, temperature | High-quality, continuous |
| Bern | 2135 | Aare | water level, discharge | Urban, well-gauged |
| Brienz | 2029 | Aare | water level, discharge | Lake outlet, stable |
| Brig | 2346 | Rhone | water level, discharge | Alpine, flood-prone |

These 4 stations cover enough variety (different basins, flow regimes,
data availability) to exercise the full pipeline.

### Test data management

- **Fixtures**: A snapshot of 30 days of data for all reference stations,
  stored in `tests/fixtures/swiss_reference/`. Updated quarterly.
- **Live tests**: Adapter contract tests fetch current data from BAFU API.
  These validate that the adapter still works with the live API format.
- **Synthetic data**: For unit tests, use factory functions that generate
  observations/forecasts with controllable properties (gaps, outliers,
  trends).
- **Time boundary fixtures**: A dedicated fixture set in `tests/fixtures/time_boundaries/`
  covering: Dec 31 → Jan 1 observations (partition crossing), Feb 28-Mar 1 in a leap
  year, pentadal aggregation at month-end (26-28, 26-29, 26-30, 26-31), and dekadal
  "21-end" for February. Updated as needed when edge cases are discovered.

```
tests/
├── conftest.py                  # Shared fixtures, PG container, factories
├── fixtures/
│   ├── swiss_reference/
│   │   ├── observations_2009.csv
│   │   ├── observations_2135.csv
│   │   ├── ecmwf_response.json  # Recorded ECMWF API response
│   │   └── thresholds.json      # Test flood thresholds
│   ├── time_boundaries/
│   │   ├── year_boundary_observations.csv
│   │   ├── leap_year_observations.csv
│   │   └── pentadal_edge_cases.csv
├── unit/
│   ├── test_qc.py
│   ├── test_alerting.py
│   ├── test_skill.py
│   ├── test_rating.py
│   ├── test_forecast_prep.py
│   ├── test_domain_types.py
│   └── test_bikram_sambat.py
├── integration/
│   ├── test_store_observations.py
│   ├── test_store_forecasts.py
│   ├── test_store_alerts.py
│   ├── test_store_weather.py
│   ├── test_migrations.py
│   └── test_api_routes.py
├── adapters/
│   ├── test_hydro_scraper.py    # Contract tests (opt-in)
│   ├── test_sapphire_dg.py      # Fixture-based
│   └── test_ieasyhydro.py       # Fixture-based
└── e2e/
    ├── test_ingest_pipeline.py
    ├── test_forecast_pipeline.py
    └── test_alert_pipeline.py
```

## CI/CD pipeline (GitHub Actions)

### On every PR

Fast feedback — must complete in under 5 minutes.

```yaml
name: PR checks
on: pull_request

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/cache@v4
        with:
          path: ~/.cache/uv
          key: uv-${{ runner.os }}-${{ hashFiles('uv.lock') }}
          restore-keys: uv-${{ runner.os }}-
      - uses: astral-sh/setup-uv@v4
      - run: uv sync
      - run: uv run ruff check
      - run: uv run ruff format --check

  typecheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/cache@v4
        with:
          path: ~/.cache/uv
          key: uv-${{ runner.os }}-${{ hashFiles('uv.lock') }}
          restore-keys: uv-${{ runner.os }}-
      - uses: astral-sh/setup-uv@v4
      - run: uv sync
      - run: uv run pyright  # or mypy --strict

  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/cache@v4
        with:
          path: ~/.cache/uv
          key: uv-${{ runner.os }}-${{ hashFiles('uv.lock') }}
          restore-keys: uv-${{ runner.os }}-
      - uses: astral-sh/setup-uv@v4
      - run: uv sync
      - run: uv run pytest tests/unit/ --cov=src/sapphire_flow --cov-report=term-missing -x

  integration-tests:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16.4
        env:
          POSTGRES_DB: sapphire_test
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 5s
          --health-retries 5
    steps:
      - uses: actions/checkout@v4
      - uses: actions/cache@v4
        with:
          path: ~/.cache/uv
          key: uv-${{ runner.os }}-${{ hashFiles('uv.lock') }}
          restore-keys: uv-${{ runner.os }}-
      - uses: astral-sh/setup-uv@v4
      - run: uv sync
      - run: uv run pytest tests/integration/ -x
        env:
          DATABASE_URL: postgresql://test:test@localhost:5432/sapphire_test

  security-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - uses: actions/cache@v4
        with:
          path: ~/.cache/uv
          key: uv-${{ runner.os }}-${{ hashFiles('uv.lock') }}
          restore-keys: uv-${{ runner.os }}-
      - run: uv sync
      - run: uv run pip-audit                   # Dependency CVE scanning
      - run: uv run bandit -r src/ -c bandit.yml  # Python SAST

  e2e-critical-path:
    if: |
      contains(github.event.pull_request.labels.*.name, 'e2e') ||
      github.event.pull_request.changed_files > 0
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dorny/paths-filter@v3
        id: filter
        with:
          filters: |
            critical:
              - 'src/sapphire_flow/flows/**'
              - 'src/sapphire_flow/store/**'
              - 'docker-compose*.yml'
              - 'Dockerfile'
              - 'src/sapphire_flow/store/migrations/**'
      - if: steps.filter.outputs.critical == 'true'
        run: docker compose -f docker-compose.test.yml up --build --abort-on-container-exit
      - if: steps.filter.outputs.critical == 'true'
        run: docker compose -f docker-compose.test.yml down -v
      - if: always()
        run: docker compose -f docker-compose.test.yml down -v --remove-orphans 2>/dev/null || true
```

E2E tests run on PRs that touch critical-path code (flows, store, Docker config, migrations). Other PRs skip E2E for speed.

### On merge to main

Full validation + deploy to staging.

```yaml
name: Main pipeline
on:
  push:
    branches: [main]

jobs:
  # All PR checks run again (lint, typecheck, unit, integration)

  e2e-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker compose -f docker-compose.test.yml up --build --abort-on-container-exit
      - run: docker compose -f docker-compose.test.yml down -v
      - if: always()
        run: docker compose -f docker-compose.test.yml down -v --remove-orphans 2>/dev/null || true

  container-scan:
    needs: [e2e-tests]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t sapphire-flow:scan .
      - uses: aquasecurity/trivy-action@master
        with:
          image-ref: 'sapphire-flow:scan'
          severity: 'CRITICAL,HIGH'
          exit-code: '1'

  deploy-staging:
    needs: [e2e-tests, container-scan]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Deploy to AWS staging
        run: |
          # Build and push Docker image
          docker build -t sapphire-flow:${{ github.sha }} .
          # Push to ECR
          # Update ECS service or SSH + docker compose pull/up
```

### On release tag

Deploy to production (when we have one).

```yaml
name: Release
on:
  push:
    tags: ['v*']

jobs:
  # Full test suite
  # Build production Docker image
  # Push to registry
  # Deploy to production (manual approval gate)
```

### CI job summary

| Trigger | What runs | Time target |
|---------|-----------|-------------|
| PR opened/updated | lint + typecheck + unit + integration + security scan | < 5 min |
| Merge to main | all above + E2E + container scan + deploy staging | < 15 min |
| Release tag | all above + deploy production (manual gate) | < 20 min |

## Staging environment (AWS)

A continuously running instance on AWS that mirrors production topology.
Fed with Swiss public data. Serves two purposes:

1. **Continuous validation**: real data flowing through the real pipeline daily
2. **Demo environment**: show stakeholders the system working with real data

### Architecture

Same `docker-compose.yml` as production, running on a single EC2 instance.

```
AWS staging (t3.medium)
├── Caddy          :443     TLS (self-signed or staging domain)
├── PostgreSQL     internal
├── Prefect        internal  (SSH tunnel for UI)
├── Worker         internal  runs Swiss ingest + forecast every 6h
└── API            internal  proxied by Caddy
```

### Swiss data configuration (v0)

```toml
[adapters.stations]
type = "hydro_scraper"
stations = ["2009", "2135", "2029", "2346"]

[adapters.weather]
type = "meteoswiss"
# MeteoSwiss open data API — no API key needed
# Provides ICON/COSMO NWP data for Switzerland

[models.default]
type = "linear_regression"   # simple model, fast, no GPU
```

The v0 staging environment uses the MeteoSwiss open data API for weather
forecasts, avoiding sapphire-dg API key costs during development. For v1,
the weather adapter switches to `sapphire_dg` (ECMWF) via a config change.

### What staging catches that tests don't

- Slow memory leaks over days of operation
- Database growth patterns and query performance with real data volumes
- Prefect scheduling edge cases (DST transitions, midnight rollover)
- TLS certificate renewal
- Docker container restart behavior
- Disk space accumulation from WAL archiving and backups

### Staging deployment

Automated on merge to main. The CI pipeline:
1. Builds the Docker image
2. Pushes to ECR (or GitHub Container Registry)
3. Deploys to staging with a post-deploy health check:
   ```bash
   ssh staging 'cd /opt/sapphire && docker compose pull && docker compose up -d \
     && sleep 15 && curl -f http://localhost:8000/api/v1/health \
     || (docker compose down && docker compose up -d --no-build && exit 1)'
   ```
   If the health check fails, the deployment rolls back to the previous image.

No manual steps. Every merge to main is live on staging within minutes.

## docker-compose.test.yml

For local E2E testing and CI.

```yaml
services:
  db:
    image: postgres:16.4
    environment:
      POSTGRES_DB: sapphire_test
      POSTGRES_USER: test
      POSTGRES_PASSWORD: test
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U test"]
      interval: 5s
      retries: 5

  pgbouncer:
    image: edoburu/pgbouncer:1.23
    environment:
      DATABASE_URL: postgresql://test:test@db/sapphire_test
      POOL_MODE: transaction
      DEFAULT_POOL_SIZE: 10
      MAX_DB_CONNECTIONS: 20
    depends_on:
      db:
        condition: service_healthy

  migrate:
    build: .
    command: sapphire-flow migrate
    environment:
      DATABASE_URL: postgresql://test:test@db/sapphire_test
    depends_on:
      db:
        condition: service_healthy

  seed:
    build: .
    command: sapphire-flow seed-test-data --swiss-reference
    environment:
      DATABASE_URL: postgresql://test:test@db/sapphire_test
    depends_on:
      migrate:
        condition: service_completed_successfully

  test-runner:
    build: .
    command: uv run pytest tests/e2e/ -v --tb=short
    environment:
      DATABASE_URL: postgresql://test:test@pgbouncer/sapphire_test
      PREFECT_API_URL: http://prefect:4200/api
    depends_on:
      seed:
        condition: service_completed_successfully
      prefect:
        condition: service_started

  prefect:
    image: prefecthq/prefect:3.2-python3.11
    command: prefect server start --host 0.0.0.0
    environment:
      PREFECT_API_DATABASE_CONNECTION_URL: postgresql+asyncpg://test:test@db/prefect_test
    depends_on:
      db:
        condition: service_healthy
```

## Local development workflow

```bash
# Run unit tests (instant, no dependencies)
uv run pytest tests/unit/

# Run integration tests (needs Docker for testcontainers PG)
uv run pytest tests/integration/

# Run adapter contract tests against live Swiss API
uv run pytest tests/adapters/ -m adapter_contract

# Run full E2E locally
docker compose -f docker-compose.test.yml up --build --abort-on-container-exit

# Run everything that CI runs on a PR
make test-pr    # alias for: lint + typecheck + unit + integration
```

### Makefile targets

```makefile
.PHONY: test test-unit test-integration test-e2e test-pr lint typecheck

test-unit:
	uv run pytest tests/unit/ -x --tb=short

test-integration:
	uv run pytest tests/integration/ -x --tb=short

test-adapters:
	uv run pytest tests/adapters/ -m adapter_contract -x --tb=short

test-e2e:
	docker compose -f docker-compose.test.yml up --build --abort-on-container-exit
	docker compose -f docker-compose.test.yml down -v

test-pr: lint typecheck test-unit test-integration

test: test-pr test-e2e

lint:
	uv run ruff check
	uv run ruff format --check

typecheck:
	uv run pyright

coverage:
	uv run pytest tests/unit/ tests/integration/ \
		--cov=src/sapphire_flow --cov-report=term-missing --cov-report=html
```

## Migration testing

Database migrations are a common source of production incidents. Every
migration is tested bidirectionally:

```python
def test_migrations_up_down_up(pg):
    """Apply all migrations, revert all, re-apply all. No errors."""
    alembic_upgrade(pg, "head")
    alembic_downgrade(pg, "base")
    alembic_upgrade(pg, "head")
```

```python

def test_release_downgrade_preserves_data(pg):
    """Verify that downgrading a specific release preserves data."""
    # Apply migrations up to previous release
    alembic_upgrade(pg, previous_release_revision)
    # Seed representative data
    seed_test_data(pg)
    # Apply new release migrations
    alembic_upgrade(pg, "head")
    # Downgrade to previous release
    alembic_downgrade(pg, previous_release_revision)
    # Verify all seeded data is intact
    verify_test_data(pg)
```

Each release includes a downgrade-with-data test. This catches cases where a migration's downgrade path silently drops or corrupts data — a scenario not covered by the generic up/down/up test.

Additionally, each new migration gets its own test that verifies:
1. The schema change applies cleanly
2. Existing data is preserved (seed data before, verify after)
3. The downgrade reverses cleanly
4. Performance-sensitive indexes exist after migration

## Test data factories

Instead of sprawling fixture files, use factory functions for deterministic
test data:

```python
def make_observation(
    station_code: str = "CH-2009",
    parameter: str = "water_level",
    timestamp: datetime | None = None,
    value: float = 3.5,
    quality_flag: int | None = None,
) -> Observation:
    return Observation(
        station_code=station_code,
        parameter=parameter,
        timestamp=timestamp or datetime(2026, 7, 1, 12, 0),
        value=value,
        quality_flag=quality_flag,
    )

def make_ensemble(
    n_members: int = 51,
    n_lead_times: int = 15,
    base_value: float = 3.5,
    spread: float = 0.5,
    rng: random.Random | None = None,
) -> ForecastEnsemble:
    rng = rng or random.Random(42)  # deterministic by default
    ...
```

## What changes from SAPPHIRE_Forecast_Tools

| Problem in Forecast_Tools | Solution here |
|---------------------------|---------------|
| Can't test without full stack running | Protocol-based DI: unit tests use fakes, no DB/API |
| No CI — breakage discovered manually | GitHub Actions on every PR, merge blocked on failure |
| Integration tests require manual setup | testcontainers spins up PG automatically |
| No staging environment | AWS staging with Swiss data, auto-deployed on merge |
| Test data is ad-hoc and brittle | Factory functions + Swiss reference dataset |
| Database changes untested | Migration up/down/up tests + data preservation checks |
| Adapter changes break silently | Contract tests against live Swiss API + recorded fixtures |
| "Works on my machine" | docker-compose.test.yml identical to production |
| Slow feedback loop | Test pyramid: seconds for unit, minutes for full E2E |
