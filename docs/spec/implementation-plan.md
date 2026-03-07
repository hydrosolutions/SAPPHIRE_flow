# Implementation Plan

Phased build order with dependency chains, task-sized work units, and
verification criteria. Each task is scoped for a single Claude Code session.

Reference: `docs/spec/types-and-protocols.md` is the authoritative type spec.

### Conventions

- Every commit follows CLAUDE.md version bumping: `uv run bump-my-version bump patch`,
  stage version files, commit, tag.
- `pyright` and `testcontainers[postgres]` must be in dev dependencies before Phase 0a begins.
- Cross-references like "DD-04" refer to `docs/design/04-models.md`.

---

## Phase 0a: Domain types + Protocols

**Depends on**: types-and-protocols.md spec
**Goal**: All types, enums, and Protocols exist, pass pyright strict, and
have basic construction tests.

### Tasks

| # | Files | What | Verify |
|---|-------|------|--------|
| 0a.1 | `types/enums.py` | All Enums: StationKind, QualityFlag, EditType, FloodLevel, AlertSource, ForecastType, ForecastStatus, BulletinScope | `pyright --strict`, unit tests for enum values |
| 0a.2 | `types/observation.py`, `types/weather.py` | Observation, WeatherForecast NamedTuples | `pyright`, construction tests |
| 0a.3 | `types/station.py` | StationInfo, StationConfig, ModelAssignment, BasinInfo | `pyright`, construction tests |
| 0a.4 | `types/forecast.py` | ForecastEnsemble, Forecast, ModelInputs | `pyright`, construction tests |
| 0a.5 | `types/alert.py` | FloodThreshold, AlertEvent. Note: `FloodThreshold.level` is `str`, not `FloodLevel` — see spec open design question #1. | `pyright`, construction tests |
| 0a.6 | `types/rating.py`, `types/bulletin.py`, `types/training.py`, `types/config.py`, `types/qc.py`, `types/skill.py`, `types/observation_edit.py`, `types/adjustment.py` | RatingCurve, Bulletin, TrainingDataset, TrainResult, ModelConfig, QCConfig, Bounds, AlertConfig, ObservationEdit, ForecastAdjustment. `types/skill.py` has no dedicated type (metrics are `dict[str, float]`) but file exists per spec layout. | `pyright`, construction tests |
| 0a.7 | `types/__init__.py` | Re-export all types | `from sapphire_flow.types import Observation` works |
| 0a.8 | `protocols/adapters.py` | WeatherForecastSource, WeatherReanalysisSource (retained for v1/Nepal — not implemented in v0), StationDataSource, ThresholdSource | `pyright --strict` |
| 0a.9 | `protocols/models.py` | ForecastModel, TrainableModel (NOT ModelRegistry — it is a concrete class, implemented in 0e.4) | `pyright --strict` |
| 0a.10 | `protocols/stores.py` | All 12 store Protocols: StationStore, ObservationStore, WeatherStore, ForecastStore, RatingCurveStore, AlertStore, SkillStore, BulletinStore, TrainingStore, ObservationEditStore, ForecastAdjustmentStore, AuditLogStore | `pyright --strict` |
| 0a.11 | `protocols/notification.py` | NotificationSink | `pyright --strict` |
| 0a.12 | `protocols/__init__.py` | Re-export all Protocols | `from sapphire_flow.protocols import ObservationStore` works |
| 0a.13 | `exceptions.py` | SanityCheckFailure, InsufficientDataError, ModelLoadError, PartitionMissingError | import test |
| 0a.14 | `schemas/access_token.py`, `schemas/station.py`, `schemas/rating_curve.py`, `schemas/model_skill.py`, `schemas/audit_log.py`, `schemas/forecast_adjustment.py`, `schemas/__init__.py` | Pydantic boundary validation models for all JSONB fields (see types-and-protocols.md "JSONB Boundary Schemas" section) | `pyright --strict`, unit tests for validation (reject invalid, accept valid) |
| 0a.15 | `config/settings.py` + `tests/unit/test_settings.py` | TOML config loader with `${VAR}` env interpolation. Needed by adapters (0e), flows (0f), and API (0g). | Test: missing var raises startup error, valid TOML parsed, QC bounds loaded |

**Notes**:
- Tasks 0a.1-0a.7 can be done in parallel (types are independent).
- Tasks 0a.8-0a.12 depend on types being defined (they reference them).
- Tasks 0a.13-0a.15 are independent of other 0a tasks.
- Consider doing 0a.1-0a.7 + 0a.13 as one session, 0a.8-0a.12 as another, 0a.14-0a.15 as a third.

---

## Phase 0b: Test fakes

**Depends on**: 0a (types + Protocols)
**Goal**: In-memory fake implementations of all store Protocols, plus test
factories for domain types. These enable testing services without PostgreSQL.

### Tasks

| # | Files | What | Verify |
|---|-------|------|--------|
| 0b.1 | `tests/fakes/__init__.py`, `tests/fakes/observation_store.py` | FakeObservationStore (in-memory dict) | Satisfies `ObservationStore` Protocol (`isinstance` check) |
| 0b.2 | `tests/fakes/weather_store.py` | FakeWeatherStore | Protocol check |
| 0b.3 | `tests/fakes/forecast_store.py` | FakeForecastStore | Protocol check |
| 0b.4 | `tests/fakes/alert_store.py` | FakeAlertStore | Protocol check |
| 0b.5 | `tests/fakes/skill_store.py`, `tests/fakes/bulletin_store.py`, `tests/fakes/training_store.py`, `tests/fakes/rating_curve_store.py`, `tests/fakes/station_store.py` | Remaining fakes (incl. FakeRatingCurveStore, FakeStationStore) | Protocol checks |
| 0b.6 | `tests/factories.py` | `make_observation()`, `make_weather_forecast()`, `make_ensemble()`, `make_station_config()`, `make_model_assignment()`, `make_flood_threshold()`, `make_alert_event()`, `make_forecast()`, `make_model_inputs()`, `make_rating_curve()`, `make_qc_config()`, `make_alert_config()` | Each factory produces a valid instance |
| 0b.7 | `tests/fakes/adapters.py`, `tests/fakes/notification.py` | FakeWeatherForecastSource, FakeStationDataSource (used for both river and weather station adapters), FakeThresholdSource (return canned data), FakeNotificationSink (records sent messages). FakeWeatherReanalysisSource deferred — not needed for v0. | Protocol checks |
| 0b.8 | `tests/fakes/models.py` | FakeForecastModel (returns configurable ensemble), FakeFailingModel (raises on predict), FakeModelRegistry (returns fake models by id) | Protocol checks for FakeForecastModel and FakeFailingModel (ForecastModel Protocol). FakeModelRegistry: unit test that `.load()` returns expected fake model (ModelRegistry is concrete, not a Protocol). |

**Notes**:
- Fakes are simple: dict/list-backed, no async, no validation beyond what
  the Protocol requires. They exist to make service tests fast and isolated.
- Tasks 0b.1-0b.5 can be parallelized. 0b.7-0b.8 depend on adapter and model Protocols from 0a.8-0a.9 (not just store Protocols).
- 0b.7-0b.8 are required before Phase 0f (flow tests).

---

## Phase 0c: Services layer

**Depends on**: 0a (types), 0b (fakes for testing)
**Goal**: Pure business logic functions, fully tested with fakes.

### Tasks

| # | Files | What | Verify |
|---|-------|------|--------|
| 0c.1 | `services/qc.py` + `tests/unit/test_qc.py` | QualityCheckService: range check, rate-of-change check | Unit: negative precip flagged, rate exceeded flagged, passed when normal, previous observation from DB used for first-in-batch, empty batch returns empty, single obs skips rate check |
| 0c.2 | `services/forecast_prep.py` + `tests/unit/test_forecast_prep.py` | `prepare_model_inputs()`: reads obs + weather, filters excluded, constructs ModelInputs. Weather ensemble reduction to median by default; full-member passthrough when model metadata requests it. `validate_ensemble()`: 7 sanity checks — (1) non-empty ensemble, (2) no empty members, (3) consistent lead-time sequences, (4) non-negative strictly increasing lead times, (5) all values finite (no NaN/inf), (6) plausibility bounds, (7) ensemble spread > 0 | Unit: excluded obs removed, empty ensemble rejected, NaN rejected, identical members rejected, plausibility bounds, inconsistent lead times rejected |
| 0c.3 | `services/alerting.py` + `tests/unit/test_alerting.py` | Threshold comparison, exceedance probability calculation, observation alert logic. Includes `forecast_horizon_hours(forecast_type: ForecastType) -> int` lookup (SUBDAILY→72, DAILY→120, PENTADAL→120, DEKADAL→240, MONTHLY→720, SEASONAL→2160) used by stale alert maintenance (0f.3b). | Unit: ensemble exceedance at various fractions, single-member ensemble (binary 0/100%), seasonal threshold selection with month wrapping (Nov-Mar), no thresholds → no alert, exact boundary month, horizon lookup for each ForecastType |
| 0c.4 | `services/skill.py` + `tests/unit/test_skill.py` | `compute_metrics()`: NSE, KGE, CRPS, RMSE, MAE, bias, percent bias. Rank histogram / PIT histogram for ensemble reliability. Pure functions over forecast+observation pairs. Match fields in `schemas/model_skill.py`. | Unit: perfect forecast NSE=1, persistence baseline, known CRPS values, uniform PIT for well-calibrated ensemble |
| 0c.5 | `services/rating.py` + `tests/unit/test_rating.py` | `apply_rating_curve()`: converts water level ensemble to discharge using stage-discharge curve | Unit: simple linear curve, interpolation, out-of-range extrapolation, empty curve data raises error |
| 0c.6 | `services/ensemble_calibration.py` + `tests/unit/test_ensemble_calibration.py` | `calibrate_ensemble()`: post-processes raw model ensembles to correct spread and bias. Implements EMOS (Ensemble Model Output Statistics) as the default method. Takes raw `ForecastEnsemble` + calibration parameters (fitted from hindcast data), returns calibrated `ForecastEnsemble`. Also `fit_calibration()`: fits EMOS parameters from hindcast forecast-observation pairs. Logs ensemble spread statistics (IQR, range) per station for monitoring calibration quality. | Unit: identity calibration (pass-through when no params), known EMOS correction (synthetic data), underdispersed ensemble → wider spread after calibration, calibration parameters reproducible from same input |

**Notes**:
- Each task is one service module + its test file.
- All tests use fakes from 0b + factories from 0b.6.
- No I/O, no database, no framework — pure functions.
- Ensemble calibration (0c.6) sits between `model.predict()` and `validate_ensemble()` in the forecast flow. For v0, calibration is optional (pass-through if no calibration parameters exist for a station). Calibration parameters are fitted from hindcast data (requires archived NWP forecasts — see 03-adapters.md "NWP forecast archiving").

---

## Phase 0d: Store layer (PostgreSQL implementations)

**Depends on**: 0a (types + Protocols)
**Goal**: Real PostgreSQL-backed implementations of all store Protocols,
with Alembic migrations for the schema.

### Tasks

| # | Files | What | Verify |
|---|-------|------|--------|
| 0d.1 | `store/migrations/` + initial Alembic setup | Alembic config, initial migration creating all tables from DD-02 schema (basins, stations, parameters, observations, forecasts, forecast_values, etc.) | `alembic upgrade head` succeeds on fresh PG, `alembic downgrade base` succeeds, re-upgrade succeeds |
| 0d.2 | `store/observations.py` + `tests/integration/test_store_observations.py` | PgObservationStore: upsert, no-overwrite, get, get_latest, get_previous, update_quality_flags, detect_gaps | Integration tests with testcontainers PG |
| 0d.3 | `store/weather.py` + `tests/integration/test_store_weather.py` | PgWeatherStore: upsert, get | Integration tests |
| 0d.4 | `store/forecasts.py` + `tests/integration/test_store_forecasts.py` | PgForecastStore: save, get_by_ids, get_selected, get_past, update_status. Note: `save_forecast` accepts `StationConfig` (flow context); `get_forecasts_by_ids` accepts `str` IDs (API context). See spec design question #4. | Integration tests; test ON CONFLICT behavior, optimistic concurrency. Verify via `EXPLAIN ANALYZE` that partition pruning is active. |
| 0d.5 | `store/alerts.py` + `tests/integration/test_store_alerts.py` | PgAlertStore: upsert_thresholds, get_thresholds (with seasonal month logic), raise_alert (ON CONFLICT DO NOTHING), resolve_stale, observation alerts | Integration tests; test deduplication indexes |
| 0d.6 | `store/skill.py`, `store/bulletins.py`, `store/ratings.py`, `store/stations.py`, `store/tokens.py`, `store/training.py` | Remaining store implementations; `store/ratings.py` implements `RatingCurveStore`. `store/tokens.py` is a concrete implementation for fastapi-users access token management, not backed by a separate Protocol — it manages the `access_tokens` table and validates scope JSONB via `schemas/access_token.py`. | Integration tests for each |
| 0d.7 | Partitioning setup | pg_partman config for observations (yearly) and forecast_values (monthly), dead_letter_queue table, PartitionMissingError handling. Verify pg_partman `part_config` table has entries for both partitioned tables. | Test: insert into missing partition raises PartitionMissingError, dead letter queue catches the data, 2026 data unaffected |
| 0d.8 | `store/observation_edits.py`, `store/forecast_adjustments.py`, `store/audit_log.py` + integration tests | PgObservationEditStore, PgForecastAdjustmentStore, PgAuditLogStore. Audit log enforces append-only semantics (no update/delete methods). | Integration tests: idempotency_key uniqueness on observation edits, audit log append-only, forecast adjustment preserves original snapshot |

**Notes**:
- Task 0d.1 must come first (schema). The initial migration must include all tables: basins, stations, parameters, observations, forecasts, forecast_values, bulletin_forecasts, access_tokens, audit_log, observation_edits, forecast_adjustments, models, model_skill, dead_letter_queue, etc.
- Tasks 0d.2-0d.8 can be parallelized after 0d.1. Task 0d.7 (partitioning) should be done before or alongside 0d.2-0d.4 since integration tests need partitions to exist.
- Phases 0b+0c and 0d can proceed in parallel after 0a completes.
- Integration tests use testcontainers (auto-spin PG container). Verify via `EXPLAIN ANALYZE` that indexes are used for primary query patterns.

---

## Phase 0e: Adapters

**Depends on**: 0a (types), 0d (stores for caching)
**Goal**: MeteoSwiss and hydro_scraper adapters for v0.

### Tasks

| # | Files | What | Verify |
|---|-------|------|--------|
| 0e.1 | `adapters/hydro_scraper.py` + `tests/adapters/test_hydro_scraper.py` | HydroScraperAdapter implementing StationDataSource (river gauge data). Retry logic, circuit breaker. | Unit tests with recorded fixtures; opt-in contract test against live BAFU API |
| 0e.2 | `adapters/meteoswiss_nwp.py` + `tests/adapters/test_meteoswiss_nwp.py` | MeteoSwissNwpAdapter implementing `WeatherForecastSource`. Downloads ICON-CH2-EPS GRIB2 via STAC API, extracts nearest grid points with cfgrib+xarray. 21 ensemble members, 5-day horizon. **All fetched data is permanently archived** in `weather_forecasts` table (not just 24h cache) to build NWP hindcast archive for future bias correction. | Unit tests with minimal synthetic GRIB2 fixture (single grid point, 2-3 members, 2 lead times — do not commit real GRIB2 files); opt-in contract test against live STAC API; verify data persists beyond cache TTL |
| 0e.2b | `adapters/meteoswiss_smn.py` + `tests/adapters/test_meteoswiss_smn.py` | MeteoSwissSmnAdapter implementing `StationDataSource` (weather observations). Fetches hourly CSV data from SwissMetNet automatic stations via MeteoSwiss OGD STAC API. Key params: precipitation, temperature, humidity, radiation, wind, snow depth, reference ET. Handles Latin-1 CSV encoding, semicolon delimiter. Maps SMN 3-letter station codes to internal station IDs. | Unit tests with recorded CSV fixtures; opt-in contract test against live OGD API; verify parameter mapping for all 8 target parameters |
| 0e.3 | `adapters/notification.py` + `tests/unit/test_notification.py` | EmailSink, WebhookSink implementing NotificationSink | Unit tests with mocked SMTP/HTTP |
| 0e.4 | `models/registry.py` + `tests/unit/test_registry.py` | ModelRegistry (concrete class, NOT a Protocol): discover models via entry points, load/validate at startup, version checking against station_model_config. Lives in `src/sapphire_flow/models/`, not `adapters/`. Only depends on types (0a), not stores — could start as early as 0b completes. | Unit tests with mock entry points; test version mismatch detection, missing model detection |

**Notes**:
- ~~MeteoSwiss reanalysis source research spike~~ — **Resolved**: v0 training uses SwissMetNet station observations (hourly, 1981–present) co-located with river gauges, not gridded reanalysis. This mirrors the Nepal approach (DHM station observations + ERA5-Land). The `WeatherReanalysisSource` Protocol is retained in the spec for v1 (Nepal will use ERA5-Land) but is not implemented for v0. See 03-adapters.md for full rationale.
- MeteoSwiss NWP adapter (`meteoswiss_nwp`) requires `cfgrib`, `xarray`, `eccodes` dependencies.
- MeteoSwiss SMN adapter (`meteoswiss_smn`) has no special dependencies (CSV parsing only).
- ICON-CH2-EPS forecasts are archived permanently to build a hindcast archive for future bias correction. This is a deliberate design choice — see 03-adapters.md "NWP forecast archiving".
- Adapter tests use `responses` library for HTTP mocking and recorded CSV/GRIB2 fixtures.
- Task 0e.4 (ModelRegistry) is placed here for convenience but has no store dependency. It can start in parallel with 0d.

---

## Phase 0f: Prefect flows

**Depends on**: 0c (services), 0d (stores), 0e (adapters)
**Goal**: All Prefect flows wired up, tested with fakes.

### Tasks

| # | Files | What | Verify |
|---|-------|------|--------|
| 0f.1 | `flows/ingest.py` + `tests/unit/test_flow_ingest.py` | `ingest_weather`, `ingest_stations`, `ingest_flood_thresholds` flows | Unit tests with fake adapters + fake stores |
| 0f.2 | `flows/forecast.py` + `tests/unit/test_flow_forecast.py` | `run_forecasts`, `forecast_station` with fallback logic. Must check `station.fallback_config is not None` before attempting fallback. After `model.predict()`, apply `calibrate_ensemble()` if calibration parameters exist for this station (pass-through otherwise), then `validate_ensemble()`. | Test: primary fails → fallback succeeds; both fail → station skipped; sanity check failure → fallback; no fallback config → skip gracefully; calibration applied when params exist; calibration skipped when no params |
| 0f.3 | `flows/alerts.py` + `tests/unit/test_flow_alerts.py` | `check_flood_alert` (Prefect task, called within forecast flow), `check_observation_alerts` (task, called within ingest flow), notification retry sweep (flow, scheduled every 5 min) | Test: alert raised, deduplicated on rerun, resolved when forecast drops below, retry sweep picks up unnotified alerts, stops after 3 attempts, logs critical on exhaustion |
| 0f.3b | `flows/maintenance.py` + `tests/unit/test_flow_maintenance.py` | Stale alert maintenance sweep (daily): flags alerts older than 2x forecast horizon as `stale_unresolvable`. Offline station alert flagging: alerts for stations with no recent observation flagged as `station_offline`. Neither auto-resolved. DLQ sweep: counts dead_letter_queue rows, logs critical if count > 0. **Dependencies**: AlertStore (stale + offline), ForecastStore (superseding forecast check), ObservationStore (last observation for offline detection). DLQ count is a direct SQL query (no Protocol needed — the `dead_letter_queue` table is an infrastructure concern, not a domain entity). Horizon derived from ForecastType lookup (e.g. SUBDAILY→72h, DAILY→120h). | Test: stale alert flagged after 2x horizon, 1x horizon alert NOT flagged (boundary), offline station alert NOT resolved, DLQ count reported |
| 0f.4 | `flows/verification.py` + `tests/unit/test_flow_verification.py` | `compute_model_skill` flow | Test: skill scores saved for each model, comparison report shows primary vs. fallback per station |
| 0f.5 | `flows/bulletin.py` + `bulletin/engine.py` + `tests/unit/test_flow_bulletin.py` | `generate_bulletin` flow with `bulletin/engine.py` (ieasyreports integration for template rendering). `TemplateLoader` is a thin wrapper around ieasyreports, defined in `bulletin/engine.py` alongside the engine. | Test: forecasts selected, bulletin record saved, status updated to published |
| 0f.6 | `flows/training.py` + `tests/unit/test_flow_training.py` | `train_model` flow | Test: training data prepared, model trained, artifact validated, result logged |
| 0f.6b | `flows/hindcast.py` + `tests/unit/test_flow_hindcast.py` | `run_hindcast` flow: runs `predict()` over a held-out historical period using station weather observations (from `ObservationStore` — SMN data ingested by `meteoswiss_smn` adapter), computes skill scores, compares against persistence/climatology baseline. Also fits ensemble calibration parameters via `fit_calibration()` from hindcast forecast-observation pairs. Gates model deployment on skill exceeding baseline. Once NWP archive has sufficient history (6-12 months), an NWP-based hindcast variant can be added. | Test: hindcast produces skill scores for held-out period, skill exceeds persistence baseline → model accepted, skill below baseline → model rejected, calibration params fitted and stored |
| 0f.7 | Flow deployment / scheduling | Prefect deployment definitions, cron schedules, concurrency limits | Manual verification in dev Prefect instance |
| 0f.8 | `flows/historical_import.py` + `tests/unit/test_flow_historical.py` | `import_historical_data` flow: batch import from API or CSV, resumability via MAX(timestamp), gap detection | Tests: batch chunking, resume from interruption, CSV vs API source, no-overwrite semantics |
| 0f.9 | `flows/catch_up.py` + `tests/unit/test_flow_catch_up.py` | Forecast catch-up flow: checks if latest weather ingest is newer than latest forecast, triggers `run_forecasts` if needed and none in progress | Unit: catch-up triggers when weather newer than forecast; no trigger when forecast is current; no trigger when run_forecasts already in progress |

---

## Phase 0g: API + Dashboard

**Depends on**: 0c (services), 0d (stores)
**Goal**: FastAPI app with all REST endpoints, auth, HTMX dashboard.

### Tasks

| # | Files | What | Verify |
|---|-------|------|--------|
| 0g.1 | `api/app.py`, `api/auth.py` | FastAPI app factory accepting injected store instances via `Depends` overrides. fastapi-users setup (cookie sessions, JWT, roles, MFA stub). Auth middleware writes login/failed-auth events to audit_log. v0: basic JWT + cookie auth with username/password only; MFA, TLS, brute-force lockout deferred to v1. | App starts, `/api/v1/ping` returns 200, unauthenticated GET `/api/v1/stations` returns 401, JWT login+access round-trip works, role check denies viewer from POST `/api/v1/admin/tokens` |
| 0g.2 | `api/routes/stations.py` + `tests/integration/test_api_stations.py` | Station CRUD, observations endpoint with observation edit operations (`POST /observations/{id}/edit` for exclude/correct/restore, `GET /observations/{id}/edits` for audit trail), station comparison | API tests with TestClient. Test: excluded observation not returned in model inputs, corrected value used, edit audit trail preserved |
| 0g.3 | `api/routes/forecasts.py` + `tests/integration/test_api_forecasts.py` | Forecast endpoints, adjustment, status transitions (including `selected` status for bulletin workflow), model comparison view data, aggregation. Forecaster sets status to `selected`; `get_selected_forecasts` returns only selected. | Test optimistic concurrency (409 on stale version), selection workflow: set selected → get_selected returns it → bulletin uses selected |
| 0g.4 | `api/routes/alerts.py` + `tests/integration/test_api_alerts.py` | Alert listing, acknowledge, notes, threshold management | Test filter by source, severity sort |
| 0g.5 | `api/routes/bulletins.py`, `api/routes/skill.py`, `api/routes/ratings.py` | Remaining REST endpoints | API tests |
| 0g.6 | `api/routes/admin.py` | Token CRUD, model config management. Admin routes include `GET /api/v1/admin/audit-log` with date range + user filter. | Test scoped token creation + filtering, audit log query returns events |
| 0g.7 | `api/routes/operations.py` | Health, operations summary, flow triggers. DLQ count in health response (direct SQL query, same as 0f.3b). Staleness thresholds for ingest freshness and station offline detection are configurable (loaded from config, not hard-coded). | Test: operations summary includes `last_ingest_weather`, `last_ingest_stations`, `last_forecast_run`, `stale_station_count`, `active_alerts_by_severity`, `failed_flows_24h`, `unnotified_danger_count`, `dlq_count`. Rate limiting on flow trigger endpoints. |
| 0g.8 | `dashboard/templates/` + `dashboard/static/` | HTMX dashboard: base, overview, alerts, station detail, forecast (with model comparison and selection controls), bulletin, skill (with primary vs. fallback comparison view), ratings | Manual browser testing + Jinja2 render tests |

---

## Phase 0h: Docker Compose integration

**Depends on**: all above
**Goal**: `docker compose up` starts the full system.

**Note**: Task 0h.5 (CLI entry points) is just `[project.scripts]` in `pyproject.toml` + thin
`cli.py` wrappers. Consider creating these as early as Phase 0d so `sapphire-flow migrate` is
available during store development. A minimal `docker-compose.dev.yml` (db + pgbouncer + prefect)
is also useful during Phases 0d-0f for integration work.

### Tasks

| # | Files | What | Verify |
|---|-------|------|--------|
| 0h.1 | `Dockerfile`, `.dockerignore` | Multi-stage build: install deps with uv, copy source, entry point. `.dockerignore` excludes `.git`, `tests/`, `docs/`, `backups/`, `secrets/`, `.env`, `__pycache__`, `.venv`, `.mypy_cache`, `.ruff_cache`. | `docker build` succeeds, image does not contain test/doc files |
| 0h.2 | `docker-compose.yml` | All 6 services (caddy, db, pgbouncer, prefect, worker, api), healthchecks, secrets | `docker compose up` and all services healthy |
| 0h.3 | `docker-compose.test.yml` | Test variant with seed data | `make test-e2e` passes |
| 0h.4 | `Caddyfile`, `pgbouncer/` config | Reverse proxy + connection pooler config | TLS works, rate limiting works |
| 0h.5 | CLI entry points | Add `[project.scripts]` to `pyproject.toml`: `sapphire-flow = "sapphire_flow.cli:main"`. Commands: `serve`, `worker`, `worker-health`, `migrate`, `create-user`, `import-stations`, `import-model-config`, `seed-test-data`. | Each command runs without error |
| 0h.6 | `Makefile` | All targets from DD-08 (test-unit, test-integration, test-e2e, lint, typecheck, coverage) | `make test-pr` passes |
| 0h.7 | `.github/workflows/` | CI pipeline: PR checks (lint, typecheck, unit, integration, security-scan with pip-audit + bandit) + main pipeline (E2E, container scan with trivy, staging deploy) | GitHub Actions green |
| 0h.8 | `tests/load/` | Load test scripts from DD-08: `generate_synthetic_data.py`, `test_load.py`. Run against docker-compose.test.yml, verify all acceptance criteria pass. | 500-station forecast cycle <5 min, P95 reads <50ms, no deadlocks |

---

## Dependency graph (visual)

```
0a (types+protocols)
├── 0b (fakes) ─── 0c (services) ──┬── 0f (flows) ──┐
│                                   │                 │
├── 0d (stores) ───────────────────┼── 0g (API+dash) ├── 0h (Docker)
│        │                          │                 │
│        └── 0e (adapters) ────────┘                  │
│                                                     │
└─────────────────────────────────────────────────────┘
```

**Reading the graph:**
- 0b+0c and 0d can proceed in parallel after 0a completes.
- 0e depends on 0a (types) and 0d (stores for caching). 0e.4 (ModelRegistry) only depends on 0a.
- 0f depends on 0c, 0d, and 0e
- 0g depends on 0c and 0d (not 0e)
- 0h depends on all above (but CLI entry points 0h.5 should be created early, see note)

## Estimated session count

| Phase | Sessions | Notes |
|-------|----------|-------|
| 0a | 3-5 | Types in one, Protocols in another, schemas + config in a third |
| 0b | 2-3 | Store fakes + adapter/model fakes + factories |
| 0c | 4-6 | One per service module + ensemble calibration |
| 0d | 4-6 | Migrations + one per store |
| 0e | 4-5 | One per adapter + model registry + reanalysis research spike |
| 0f | 5-7 | One per flow + historical import + catch-up + hindcast |
| 0g | 4-6 | Auth + routes + dashboard |
| 0h | 3-4 | Docker + CI + load tests |
| **Total** | **~30-45** | |

---

## v0 minimal slice (recommended first target)

For the fastest path to an end-to-end demo with Swiss data:

1. **0a**: All types + Protocols + config (foundation)
2. **0b**: Fakes (store + adapter + model fakes)
3. **0c.1-0c.3**: QC + forecast prep + alerting services
4. **0d.1-0d.5**: Migrations + observation/weather/forecast/alert stores
5. **0e.1-0e.2b**: hydro_scraper + MeteoSwiss NWP + MeteoSwiss SMN adapters
6. **0f.1-0f.3**: Ingest + forecast + alert flows
7. **0g.1, 0g.7**: Minimal API (ping, health — note: health returns partial data in this slice, no dashboard)
8. **0h.1-0h.2, 0h.5**: Docker Compose + CLI entry points

This gets data flowing end-to-end including alerts. Bulletins, dashboard,
skill computation, and observation editing follow incrementally.

### Pipeline validation milestone (after minimal slice)

After steps 1-8 above, validate the data pipeline works end-to-end:

1. `docker compose up` starts all services, health endpoint returns 200
2. Ingest flow runs and stores observations + weather forecasts
3. Forecast flow runs, produces ensembles for all configured stations
4. Alert flow detects threshold exceedance and persists an alert event
5. Health endpoint reports correct last_ingest and last_forecast timestamps
6. Re-running ingest + forecast is idempotent (no duplicates)

### Full workflow validation milestone (after all phases)

After all 8 phases are complete, validate the full forecaster morning cycle
before declaring v0 done. Manual walkthrough checklist:

1. Log in to dashboard, see station overview with data freshness indicators
2. Identify stations with QC flags or missing data at a glance
3. Review forecast ensemble for a station (spread, exceedance probabilities)
4. Compare primary vs fallback model output
5. Adjust a forecast (override), verify original is preserved with reason logged
6. Select and publish the adjusted forecast
7. Generate a bulletin (preview, then publish)
8. Verify flood alert was raised for a threshold-exceeding station
9. Acknowledge the alert from the dashboard
10. Confirm API responses include units, timestamps, and threshold context
11. Simulate a data source outage (disable adapter), verify operations summary reports stale data and health endpoint degrades to warning state

This is not automated — a manual walkthrough by someone with operational
forecasting experience suffices. Gaps found here feed back as issues before
Nepal deployment.
