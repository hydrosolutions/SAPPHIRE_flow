---
status: DRAFT
---

> **DRAFT** — This design doc has not completed the review maturity gate. Do not treat as authoritative until `status: READY`.

# Prefect Flows and Scheduling

## Why Prefect

- Task dependencies (ingest -> forecast) handled natively
- Built-in retries with configurable backoff
- Manual re-trigger via UI or API
- Run history and observability via Prefect UI (:4200)
- Clean Python-native API — hydrologists can read flow definitions
- Self-hosted: one container, uses our PostgreSQL for metadata

## Flows vs. services

Flows are thin orchestration wrappers. Business logic lives in `services/`:
- `services/alerting.py` — threshold checking, alert raising/resolving
- `services/skill.py` — metric computation (NSE, CRPS, etc.)
- `services/rating.py` — rating curve application (pure function)
- `services/forecast_prep.py` — model input preparation, QC filtering

This means `apply_rating_curve`, `compute_metrics`, and `prepare_model_inputs`
are importable, testable functions with no Prefect or database dependency.
Flows call them; tests call them directly.

## Dependency injection

Flows receive their dependencies (adapters, stores) as parameters. A top-level
`main()` or Prefect deployment entry point constructs the concrete
implementations and passes them in. This keeps flows testable with fakes — no
database or external API needed for unit tests.

## Flow overview

```
┌─────────────────┐     on success     ┌──────────────────┐
│ ingest_weather   │───────────────────>│ run_forecasts    │
│ (scheduled)      │                    │ (triggered)       │
└─────────────────┘                    └──────────────────┘
                                               │
┌─────────────────┐     on success             │
│ ingest_stations  │──────────────────────────>│
│ (scheduled)      │                            │
└─────────────────┘                            v
                                       ┌──────────────────┐
                                       │ post_process     │
                                       │ (rating curves,  │
                                       │  store results,  │
                                       │  check alerts)   │
                                       └───────┬──────────┘
                                               │
                                       ┌───────v──────────┐
                                       │ check_flood_     │
                                       │ alerts           │
                                       │ (notify if       │
                                       │  thresholds      │
                                       │  exceeded)       │
                                       └──────────────────┘

                                       ┌──────────────────┐
                                       │ compute_skill    │
                                       │ (scheduled,      │
                                       │  e.g. weekly)    │
                                       └──────────────────┘

                                       ┌──────────────────┐
                                       │ generate_        │
                                       │ bulletin         │
                                       │ (manual trigger) │
                                       └──────────────────┘
```

## Ingest flows

### ingest_weather

```python
from prefect import flow, task

@task(retries=3, retry_delay_seconds=[60, 300, 900])
def fetch_weather_forecasts(adapter: WeatherForecastSource, station_ids: list[str]):
    ...

@flow(log_prints=True)
def ingest_weather(
    forecast_adapter: WeatherForecastSource,
    weather_store: WeatherStore,
    station_ids: list[str],
):
    forecasts = fetch_weather_forecasts(forecast_adapter, station_ids)
    weather_store.upsert_weather_forecasts(forecasts)
    # Weather forecasts are permanently archived in weather_store
    # (not just cached for 24h) — builds NWP hindcast archive for
    # future bias correction. See 03-adapters.md "NWP forecast archiving".
```

Weather station observations (SMN) are ingested via `ingest_stations` using a
second `StationDataSource` adapter — the `meteoswiss_smn` adapter. This keeps
the weather observation ingest on the same path as river gauge ingest (same
Protocol, same QC, same store). See `ingest_stations` below.

### ingest_stations

After observations are stored, the ingest flow runs automated QC and observation-based alert checking:

```python
@flow(log_prints=True)
def ingest_stations(
    adapter: StationDataSource,
    store: ObservationStore,
    alert_store: AlertStore,
    qc_service: QualityCheckService,
    station_ids: list[str],
    check_alerts: bool = True,  # False for weather station ingest
):
    observations = fetch_station_observations(adapter, station_ids)

    rows_upserted, code_to_uuid = store.upsert_observations(observations)
    logging.info("Upserted %d observations", rows_upserted)

    # Fetch previous observations for rate-of-change QC
    # code_to_uuid maps (station_code, parameter_name) → (station_uuid, parameter_uuid)
    previous = store.get_previous_observations(
        [code_to_uuid[(obs.station_code, obs.parameter)]
         for obs in observations]
    )

    # Automated quality control
    flagged = qc_service.check_observations(observations, previous_by_station=previous)
    store.update_quality_flags(flagged)

    # Observation-based flood alert checking (river stations only)
    if check_alerts:
        check_observation_alerts(observations, store, alert_store, code_to_uuid)
```

### ingest_flood_thresholds

```python
@flow(log_prints=True)
def ingest_flood_thresholds(
    adapter: ThresholdSource,
    store: AlertStore,
    station_ids: list[str],
):
    """Fetch flood thresholds from hydromet API and store locally.

    Run once during setup, then periodically (e.g. monthly) to catch updates.
    """
    thresholds = adapter.fetch_flood_thresholds(station_ids)
    store.upsert_thresholds(thresholds)
```

## Forecast flow

One station's model failure must never block the other 499. Each station is
wrapped in a try/except that falls back to the station's fallback model, then
skips with a log entry if both fail.

Concurrency is limited to 1 concurrent `run_forecasts` execution via Prefect's
`concurrency` deployment setting (`concurrency_limit=1`). If a manual trigger
arrives while a scheduled run is in progress, it is queued, not dropped.

```python
@flow(log_prints=True)
def run_forecasts(
    station_store: StationStore,
    forecast_store: ForecastStore,
    observation_store: ObservationStore,
    weather_store: WeatherStore,
    alert_store: AlertStore,
    model_registry: ModelRegistry,
    alert_config: AlertConfig,
):
    station_configs = station_store.get_active_station_configs()

    futures = [
        forecast_station.submit(
            station, forecast_store, observation_store, weather_store,
            alert_store, model_registry, alert_config,
        )
        for station in station_configs
    ]

    results = [f.result(raise_on_failure=False) for f in futures]
    # Each result is now list[Forecast], not Forecast | None
    total_forecasts = sum(len(r) for r in results if r is not None)
    total_stations = sum(1 for r in results if r is not None and len(r) > 0)
    logging.info("Forecast complete: %d forecasts for %d/%d stations",
                 total_forecasts, total_stations, len(station_configs))


@task(retries=1, retry_delay_seconds=30, timeout_seconds=300)
def forecast_station(
    station: StationConfig,
    forecast_store: ForecastStore,
    observation_store: ObservationStore,
    weather_store: WeatherStore,
    alert_store: AlertStore,
    model_registry: ModelRegistry,
    alert_config: AlertConfig,
) -> list[Forecast]:
    """Produce forecasts for all configured parameters at this station."""
    results = []

    for param_config in station.forecast_configs:
        forecast = forecast_single_parameter(
            station, param_config,
            forecast_store, observation_store, weather_store,
            alert_store, model_registry, alert_config,
        )
        if forecast is not None:
            results.append(forecast)

    return results


def forecast_single_parameter(
    station: StationConfig,
    param_config: ParameterForecastConfig,
    forecast_store: ForecastStore,
    observation_store: ObservationStore,
    weather_store: WeatherStore,
    alert_store: AlertStore,
    model_registry: ModelRegistry,
    alert_config: AlertConfig,
) -> Forecast | None:
    # Load model first (needed to check needs_full_ensemble for input prep).
    try:
        model = model_registry.load(param_config.model_config)
    except ModelLoadError:
        logging.exception("Failed to load primary model for %s/%s, trying fallback",
                          station.code, param_config.parameter_name)
        if param_config.fallback_config is None:
            return None
        try:
            model = model_registry.load(param_config.fallback_config)
        except ModelLoadError:
            logging.exception("Fallback model also failed to load for %s/%s, skipping",
                              station.code, param_config.parameter_name)
            return None

    # Prepare inputs OUTSIDE model try block — input prep is not model-specific,
    # so failure here skips the station entirely (no fallback can help).
    try:
        use_full = getattr(model, "needs_full_ensemble", False)
        inputs = prepare_model_inputs(
            station, param_config, observation_store, weather_store,
            use_full_ensemble=use_full,
        )
    except Exception:
        logging.exception("Input preparation failed for %s/%s, skipping",
                          station.code, param_config.parameter_name)
        return None

    try:
        primary_obs = inputs.observations.get(param_config.parameter_name, [])
        observation_span_hours = (
            (primary_obs[-1][0] - primary_obs[0][0]).total_seconds() / 3600
            if len(primary_obs) >= 2 else 0
        )
        if observation_span_hours < model.min_lookback_hours:
            logging.warning(
                "Insufficient data for %s/%s (%.0f hrs, %d required), trying fallback",
                station.code, param_config.parameter_name,
                observation_span_hours, model.min_lookback_hours,
            )
            raise InsufficientDataError(station.code)

        ensemble = model.predict(inputs)
        validate_ensemble(ensemble, station.metadata)
    except (InsufficientDataError, SanityCheckFailure, RuntimeError):
        logging.exception("Primary model failed for %s/%s, trying fallback",
                          station.code, param_config.parameter_name)
        if param_config.fallback_config is None:
            logging.error("No fallback configured for %s/%s, skipping",
                          station.code, param_config.parameter_name)
            return None
        try:
            fallback_model = model_registry.load(param_config.fallback_config)
            # Re-prepare inputs if fallback has different ensemble requirements
            fallback_needs_full = getattr(fallback_model, "needs_full_ensemble", False)
            if fallback_needs_full != use_full:
                inputs = prepare_model_inputs(
                    station, param_config, observation_store, weather_store,
                    use_full_ensemble=fallback_needs_full,
                )
            ensemble = fallback_model.predict(inputs)
            validate_ensemble(ensemble, station.metadata)
        except (InsufficientDataError, SanityCheckFailure, ModelLoadError, RuntimeError):
            logging.exception("Fallback also failed for %s/%s, skipping",
                              station.code, param_config.parameter_name)
            return None

    # v2.0: apply_rating_curve here when discharge conversion is implemented

    forecast = forecast_store.save_forecast(station, param_config, ensemble)
    check_flood_alert(
        forecast, ensemble, station, param_config,
        alert_store, alert_config,
    )
    return forecast
```

Parallel execution via Prefect's `.submit()` is the default — not an optimization
for later. With 500 stations and models taking 2-5 seconds each, sequential
execution would take 15-40 minutes. Concurrent task submission brings this to
minutes.

### Forecast input preparation

`prepare_model_inputs` lives in `services/forecast_prep.py` (pure function,
reads from `ObservationStore` and `WeatherStore`). It:

- Filters out observations with `edit_type = excluded` in `observation_edits`
- Uses corrected values where edits have been applied
- Constructs `ModelInputs` with observations and weather forecasts keyed by parameter name
- This ensures the forecaster's data quality decisions propagate to models

## Flood alert flow

Alerts are persisted in the `alert_events` table, not treated as ephemeral.
This enables tracking alert lifecycle (raised, acknowledged, resolved).

```python
@task
def check_flood_alert(
    forecast: Forecast,
    ensemble: ForecastEnsemble,
    station: StationConfig,
    param_config: ParameterForecastConfig,
    store: AlertStore,
    alert_config: AlertConfig,
):
    thresholds = store.get_thresholds(station.id, param_config.parameter_id)
    if not thresholds:
        return

    # ensemble.members: list[list[tuple[int, float]]] — each member is
    # a list of (lead_time_minutes, value) pairs.
    lead_times = sorted({lt for member in ensemble.members for lt, _ in member})

    for lead_time in lead_times:
        member_values = [
            value
            for member in ensemble.members
            for lt, value in member
            if lt == lead_time
        ]
        if not member_values:
            continue

        for threshold in thresholds:
            exceedance_fraction = sum(
                1 for v in member_values if v >= threshold.value
            ) / len(member_values)

            flood_level = FloodLevel(threshold.level)  # parse str → enum
            # Per-threshold exceedance probability (or global default)
            min_probability = threshold.exceedance_probability or \
                alert_config.default_exceedance(FloodLevel(threshold.level))

            if exceedance_fraction >= min_probability:
                store.raise_alert(station, forecast, lead_time, threshold,
                                  exceedance_fraction=exceedance_fraction)

    # Auto-resolve alerts from previous forecasts that are no longer exceeded
    store.resolve_stale_alerts(station, forecast)

    # Notify on new danger-level alerts
    new_danger = store.get_unacknowledged_danger_alerts(station.id)
    if new_danger:
        # send_notification dispatches via the configured NotificationSink.
        # Injected at flow construction time; see flows/alerts.py implementation.
        send_notification(new_danger)
```

`AlertConfig` defines default exceedance probability thresholds per alert level.
Each threshold in the `flood_thresholds` table can override the default via its
`exceedance_probability` column (NULL means use the global default for that level).
Defaults (configurable per deployment in `config.toml`):

```toml
[alerts]
# Global defaults — overridden by per-threshold exceedance_probability in DB
default_exceedance_watch = 0.2
default_exceedance_warning = 0.5
default_exceedance_danger = 0.8
```

Higher severity requires stronger ensemble agreement by default. Watch triggers
when even a minority of members signal elevated levels (20%). Danger requires
near-consensus (80%). These defaults can be overridden per station and level via
the flood_thresholds table, allowing hydromets to tune sensitivity for specific
locations (e.g. lower danger threshold for stations protecting critical
infrastructure).

`raise_alert` is idempotent — uses `INSERT ... ON CONFLICT DO NOTHING` on
`(station_id, parameter_id, forecast_id, level)` to prevent duplicate alerts
on flow reruns.

### Notification retry

If `send_notification` fails (SMTP down, webhook unreachable), the alert is
persisted but `notified_at` remains NULL. A periodic sweep task (every 5
minutes) retries notification for all alerts where:
- `notified_at IS NULL`
- `raised_at` is within the last 24 hours
- `level` is `danger` or `warning`

After 3 failed retry attempts, the sweep logs a critical error and the
operations summary reports "unnotified danger alerts." This ensures that a
temporary notification outage does not cause a missed flood warning.

**Circuit breaker**: Notification sinks use the same circuit breaker pattern as
data adapters (see 03-adapters.md). After 5 consecutive failures, the sink stops
attempting delivery for a configurable cooldown period (default 30 minutes). This
prevents log noise and SMTP provider rate-limit bans during extended outages.
The circuit state is included in the operations summary.

### Observation-based alert checking

Alerts can also fire when real-time observations exceed thresholds, not just forecasts:

```python
@task
def check_observation_alerts(
    observations: list[Observation],
    store: ObservationStore,
    alert_store: AlertStore,
    code_to_uuid: dict[tuple[str, str], tuple[UUID, UUID]],
    # Maps (station_code, parameter_name) -> (station_id, parameter_id)
    # Built during ingest from the store's internal code→UUID resolution.
):
    """Check latest observations against flood thresholds.

    Uses the database as source of truth — not just the ingest batch.
    The code_to_uuid mapping determines which stations to check, but the
    actual threshold comparison uses the latest stored observation.
    """
    for (station_code, param_name), (station_id, parameter_id) in code_to_uuid.items():
        thresholds = alert_store.get_thresholds(station_id, parameter_id)
        if not thresholds:
            continue
        latest = store.get_latest_observation(station_id, parameter_id)
        if latest is None:
            continue

        for threshold in thresholds:
            if latest.value >= threshold.value:
                alert_store.raise_observation_alert(latest, threshold)

        # Resolve alerts for thresholds no longer exceeded —
        # uses the existing resolve_observation_alerts(station_id, parameter_id)
        # which resolves all observation alerts for this station+parameter
        # where the latest value no longer exceeds the threshold.
        alert_store.resolve_observation_alerts(station_id, parameter_id)

    new_danger = alert_store.get_unacknowledged_danger_alerts(source=AlertSource.OBSERVATION)
    if new_danger:
        send_notification(new_danger)
```

`raise_observation_alert` uses `INSERT ... ON CONFLICT DO NOTHING` on
`(station_id, parameter_id, source, level)` where `resolved_at IS NULL`,
preventing duplicate alerts across ingest cycles. Notifications are only sent
for alerts where `notified_at IS NULL`; the notification function sets
`notified_at` after successful delivery.

This task is called at the end of `ingest_stations` after observations are stored.

### Alert correlation

The same flood event at a station may trigger both a forecast-based alert and
an observation-based alert. These have different `source` values and are stored
as separate records. To avoid double-notification:

- Before sending a notification, check whether a notification was already sent
  for the same `(station_id, parameter_id, level)` within the last hour,
  regardless of source.
- The dashboard groups forecast and observation alerts for the same station
  and time window into a single visual entry with both sources shown.

Flood alert notifications are **mandatory** for danger-level alerts — not
optional. Configure at least one notification channel (email or webhook) during
deployment. Notifications use a pluggable `NotificationSink` Protocol, allowing
email, Slack, SMS, or webhook without Prefect dependency.

## Automated quality control

QC runs automatically after each station ingest. The QC service is a pure
function in `services/qc.py` — no database or framework dependencies.

```python
from sapphire_flow.services.qc import QualityCheckService

class QualityCheckService:
    def __init__(self, config: QCConfig):
        self.config = config

    def check_observations(
        self,
        observations: list[Observation],
        previous_by_station: dict[tuple[str, str], Observation] | None = None,
    ) -> list[tuple[Observation, int]]:
        """Returns (observation, quality_flag) pairs for all observations.

        `previous_by_station` maps (station_code, parameter) to the most recent
        observation before this batch — needed for rate-of-change checks on the
        first observation in the batch. Callers obtain this from the database.
        """
        previous_by_station = previous_by_station or {}

        # Group by (station, parameter) and sort by time for consecutive checks
        grouped: dict[tuple[str, str], list[Observation]] = {}
        for obs in observations:
            key = (obs.station_code, obs.parameter)
            grouped.setdefault(key, []).append(obs)

        flagged = []
        for key, group in grouped.items():
            group.sort(key=lambda o: o.timestamp)
            prev = previous_by_station.get(key)

            for obs in group:
                flag = self._run_checks(obs, prev)
                flagged.append((obs, flag))
                prev = obs

        return flagged

    def _run_checks(self, obs: Observation, prev: Observation | None) -> int:
        # Range check: is the value physically plausible?
        bounds = self.config.get_bounds(obs.parameter)
        if bounds and not (bounds.min <= obs.value <= bounds.max):
            return 2  # failed range check

        # Rate-of-change check: is the change per unit time too large?
        if prev is not None and self._exceeds_rate_of_change(obs, prev):
            return 3  # failed rate-of-change check

        return 1  # passed all checks

    def _exceeds_rate_of_change(self, obs: Observation, prev: Observation) -> bool:
        max_rate = self.config.get_max_rate(obs.parameter)
        if max_rate is None:
            return False
        hours_elapsed = (obs.timestamp - prev.timestamp).total_seconds() / 3600
        if hours_elapsed <= 0:
            return False
        rate = abs(obs.value - prev.value) / hours_elapsed
        return rate > max_rate
```

### QC configuration

Checks are configured per parameter in TOML:

```toml
[qc.precipitation]
min = 0.0
max = 300.0                   # mm/day — extreme but plausible
max_rate_of_change_per_hour = 50.0  # mm/hr between consecutive readings

[qc.water_level]
min = -2.0                    # m below gauge zero
max = 20.0                    # m — deployment-specific
max_rate_of_change_per_hour = 0.5   # m/hr between consecutive readings

[qc.temperature]
min = -50.0
max = 55.0                    # degC
max_rate_of_change_per_hour = 5.0   # degC/hr between consecutive readings
```

Bounds are intentionally generous — the goal is to catch sensor malfunctions
and transmission errors, not to enforce climatological norms. Forecasters
review suspect observations on the dashboard and decide whether to exclude them.

## Verification / skill computation flow

```python
@flow(log_prints=True)
def compute_model_skill(
    forecast_store: ForecastStore,
    observation_store: ObservationStore,
    skill_store: SkillStore,
    station_configs: list[StationConfig],
    lookback_days: int = 90,
    clock: Callable[[], datetime] = datetime.now,
):
    for station in station_configs:
        for param_config in station.forecast_configs:
            forecasts = forecast_store.get_past_forecasts(station, lookback_days=lookback_days)
            observations = observation_store.get_observations(
                station.id, param_config.parameter_id,
                start=clock() - timedelta(days=lookback_days),
                end=clock(),
            )

            model_ids = [param_config.model_config.model_id]
            if param_config.fallback_config:
                model_ids.append(param_config.fallback_config.model_id)
            for model_id in model_ids:
                model_forecasts = [
                    f for f in forecasts
                    if f.model_id == model_id and f.parameter_id == param_config.parameter_id
                ]
                if not model_forecasts:
                    continue
                model_version = model_forecasts[0].model_version
                forecast_type = model_forecasts[0].forecast_type
                # compute_metrics returns per-lead-time scores
                lead_time_metrics = compute_metrics(model_forecasts, observations)
                for lead_time_minutes, metrics in lead_time_metrics.items():
                    skill_store.save_skill_scores(
                        station, param_config.parameter_id,
                        model_id, model_version, forecast_type,
                        lead_time_minutes,
                        period_start=clock() - timedelta(days=lookback_days),
                        period_end=clock(),
                        metrics=metrics,
                    )
```

## Bulletin generation flow

```python
@flow(log_prints=True)
def generate_bulletin(
    forecast_store: ForecastStore,
    bulletin_store: BulletinStore,
    template_loader: TemplateLoader,
    scope: BulletinScope,
    generated_by: UUID,
    basin_id: str | None = None,
    template_id: str = "default",
    forecast_ids: list[str] | None = None,
):
    template = template_loader.load(template_id)

    if forecast_ids:
        forecasts = forecast_store.get_forecasts_by_ids(forecast_ids)
    elif scope == "basin" and basin_id:
        forecasts = forecast_store.get_selected_forecasts_for_basin(basin_id)
    else:
        forecasts = forecast_store.get_all_selected_forecasts()

    # Prepare data for ieasyreports
    report_data = prepare_bulletin_data(forecasts)
    output_path = render_bulletin(template, report_data)

    # Store bulletin record (generated_by comes from the triggering request context)
    bulletin_store.save_bulletin(scope, basin_id, template_id, output_path, forecast_ids, generated_by)

    # Update forecast statuses to published
    forecast_store.update_forecast_status(forecast_ids, status=ForecastStatus.PUBLISHED)

    return output_path
```

**Idempotency**: `save_bulletin` and `update_forecast_status` are wrapped in a
single database transaction — either both succeed or neither does. On retry,
duplicate bulletin detection uses the combination of
`(scope, basin_id, template_id, forecast_ids)` — if a bulletin with the same
forecast set already exists, the flow returns the existing file path instead of
regenerating.

The `ieasyreports` library handles Excel template filling. Each hydromet
can have multiple templates (e.g. daily forecast, pentadal summary,
seasonal outlook). Templates are stored in the deployment's config directory.

## Scheduling

Prefect deployments define the schedule:

```python
from prefect.deployments import Deployment
from prefect.server.schemas.schedules import CronSchedule

# Run ingest every 3 hours
ingest_weather_deployment = ingest_weather.to_deployment(
    name="ingest-weather",
    schedule=CronSchedule(cron="0 */3 * * *"),
)

# Run station ingest every hour
ingest_stations_deployment = ingest_stations.to_deployment(
    name="ingest-stations",
    schedule=CronSchedule(cron="0 * * * *"),
)

# Compute model skill weekly (Sunday 02:00)
compute_skill_deployment = compute_model_skill.to_deployment(
    name="compute-skill",
    schedule=CronSchedule(cron="0 2 * * 0"),
)

# Refresh flood thresholds monthly
ingest_thresholds_deployment = ingest_flood_thresholds.to_deployment(
    name="ingest-thresholds",
    schedule=CronSchedule(cron="0 3 1 * *"),
)

# Forecasts and bulletins are triggered, not scheduled
```

### Scheduling and NWP forecast availability

Weather forecast ingest is scheduled around ICON-CH2-EPS availability (every 6
hours: 00, 06, 12, 18 UTC; for v1/Nepal: ECMWF IFS on same schedule). All fetched
NWP data is permanently archived — see 03-adapters.md "NWP forecast archiving".
Station ingest (river gauges + weather stations) runs more frequently (hourly)
since real-time data arrives continuously.

~~**Event-mode forecasting**~~ — **Deferred to v2.0**: During active flood events,
hydromets may want higher-frequency forecast updates. NWP data (ICON-CH2-EPS for v0,
ECMWF IFS for v1) limits forecast updates to 6-hourly, but real-time rainfall
observations could refine forecasts between NWP cycles (e.g. simple updating/blending).
Requires research into nowcasting approaches. Not blocking for v0/v1 — standard
NWP-cycle-driven forecasting is sufficient. See 00-overview.md.

## Late data and manual re-triggers

### Scenario: data arrives late

1. Scheduled ingest runs at 06:00, but station API has no new data
2. Ingest flow completes with "no new data" status
3. At 08:00, data becomes available
4. **Option A**: Next scheduled ingest (09:00) picks it up, triggers forecast
5. **Option B**: Forecaster sees stale data in dashboard, clicks "Re-run ingest"
   -> Prefect API triggers a new ingest_stations run
   -> On success, triggers run_forecasts

### Manual re-trigger

The Prefect UI (:4200) allows one-click re-triggering of any flow.
Additionally, our REST API exposes:

```
POST /api/v1/flows/ingest/trigger
POST /api/v1/flows/forecast/trigger
POST /api/v1/flows/forecast/trigger?station=ABC-001    (single station)
POST /api/v1/flows/forecast/trigger?basin=BASIN-01     (all stations in basin)
```

These call the Prefect API under the hood.

### Ingest → forecast trigger

The forecast flow is triggered by a Prefect automation:

    Automation: "trigger-forecasts-after-ingest"
    Trigger: ALL of:
      - Flow "ingest-weather" completed successfully
      - Flow "ingest-stations" completed successfully
      - Both completions within a 30-minute window
    Action: Run deployment "run-forecasts"

If the automation misfires (Prefect server crash between ingest and trigger),
the catch-up flow (every 15 minutes) detects the gap and triggers a forecast
run.

Manual triggers via API (`POST /api/v1/flows/forecast/trigger`) bypass the
automation and run immediately.

### Forecast catch-up

A safety-net flow runs every 15 minutes and checks whether forecasts have been
produced for the latest available weather data. If the latest weather ingest is
newer than the latest forecast run, and no `run_forecasts` flow is currently
in progress, a catch-up forecast is triggered. This handles:

- Prefect automation misfires (server crash between ingest success and forecast trigger)
- Manual ingest without subsequent forecast trigger
- Any other gap in the ingest → forecast chain

## Training flow (optional)

```python
@flow(log_prints=True)
def train_model(
    store: TrainingStore,
    model_registry: ModelRegistry,
    station_id: str,
    model_type: str,
    config: ModelConfig,
):
    training_data = store.prepare_training_data(station_id)
    model = model_registry.create(model_type)
    result = model.train(training_data, config)

    # Validate before deployment: re-predict on training period as sanity check.
    # Construct minimal ModelInputs from training data for validation.
    validation_inputs = ModelInputs(
        station_id=training_data.station_id,
        parameter_id=list(training_data.observations.keys())[0],
        observations=training_data.observations,
        weather_forecasts=training_data.weather_history,
        forecast_type=ForecastType.DAILY,
        metadata=training_data.metadata,
    )
    test_ensemble = model.predict(validation_inputs)
    validate_ensemble(test_ensemble, training_data.metadata)

    model_registry.save(model, station_id)
    store.log_training_result(station_id, result)
```

Training is intentionally decoupled — it can run as a Prefect flow
on the server, or as a standalone script on a workstation with GPU.

## Historical data import flow

Used during initial deployment to backfill historical observations from the
hydromet's existing database or CSV files.

```python
@flow(log_prints=True)
def import_historical_data(
    source: StationDataSource | Path,
    store: ObservationStore,
    station_ids: list[str],
    start: datetime,
    end: datetime,
    batch_days: int = 90,
):
    """Import historical observations in batches.

    Accepts either a StationDataSource adapter (for API-based import)
    or a Path to a CSV directory (for file-based import).
    """
    current = start
    while current < end:
        batch_end = min(current + timedelta(days=batch_days), end)

        if isinstance(source, Path):
            observations = load_csv_observations(source, station_ids, current, batch_end)
        else:
            observations = source.fetch_observations(station_ids, current, batch_end)

        store.insert_observations_no_overwrite(observations)
        logging.info("Imported %d observations for %s to %s", len(observations), current, batch_end)
        current = batch_end

    # Validate completeness
    for station_id in station_ids:
        gaps = store.detect_gaps([station_id], start=start, end=end)
        if gaps:
            logging.warning("Station %s has %d gaps after import", station_id, len(gaps))
```

**Resumability**: On restart after interruption, the import determines the
resume point by querying `MAX(timestamp)` from observations for each station.
The `start` parameter is advanced to `max(start, last_imported_timestamp)`,
skipping already-imported batches. The `upsert_observations` call handles any
overlap safely. Operators can also pass `--resume` to the CLI to resume from
the last successful batch.

Key design decisions:
- **Batch processing**: Imports in configurable chunks (default 90 days) to avoid memory issues with 25 years of data
- **Dual source**: Supports both API adapters and CSV files (common for historical data handoff)
- **Gap detection**: Reports missing data after import so the operator knows what's incomplete
- **Idempotent**: Uses the same `upsert_observations` as live ingest — safe to re-run
- **Parallel operation safety**: Historical import uses `INSERT ... ON CONFLICT DO NOTHING` (not `DO UPDATE`) — existing operational data always takes precedence over historical backfill. This prevents a backfill from overwriting QC flags or corrections applied by operational ingest. The import calls `store.insert_observations_no_overwrite()`, a separate method from the operational `upsert_observations`.

## Observability

- **Prefect UI** (:4200): run history, logs, task-level status, flow diagrams
- **Our dashboard**: forecast-specific views (which stations have fresh forecasts),
  flood alert inbox, bulletin generation status
- **Logs**: structured logging (JSON) from all flows, queryable
- **Flow failure notifications** (mandatory): At least one notification channel
  (email or webhook) must be configured. All flow failures trigger an immediate
  notification. This is not optional for a flood warning system.
- **Health endpoint**: `GET /api/v1/health` returns system status including
  last successful ingest/forecast times, number of active alerts

## Failure modes and recovery

### Prefect server crash

Prefect uses PostgreSQL-backed state. If the server container dies, in-progress
flow runs are marked as "Crashed" on restart. Workers reconnect automatically.
No manual intervention is needed for recovery — the next scheduled run proceeds
normally.

### Flow idempotency

All flows are designed for safe re-execution:

- **Ingest flows**: `INSERT ... ON CONFLICT DO UPDATE` makes re-ingesting the same
  data a no-op for unchanged values and a correction for changed values.
- **Forecast flow**: `save_forecast` uses `ON CONFLICT` on
  `(station_id, parameter_id, issued_at, model_id, forecast_type)` — re-running
  produces the same forecast row, not a duplicate. The `forecast_type` column
  prevents conflicts when the same model produces both daily and subdaily
  forecasts for the same station at the same time.
- **Alert flow**: `raise_alert` and `raise_observation_alert` use `ON CONFLICT DO NOTHING`
  on their natural keys — re-checking the same forecast/observation does not create
  duplicate alerts.

A crash between `save_forecast` and `check_flood_alert` is safe: on rerun,
`save_forecast` is a no-op (conflict), and `check_flood_alert` creates the
alert that was missed.

### Worker crash

Prefect marks the in-progress task as failed. The retry policy (1 retry, 30s
delay) handles transient failures. Persistent failures trigger flow failure
notifications.

### Database unavailable

All store operations fail immediately. Prefect retries handle brief outages
(seconds). Extended outages cause flow failures, caught by mandatory failure
notifications. The system resumes automatically when the database recovers —
the next scheduled ingest catches up.

### External API unavailable

Handled by adapter resilience (see 03-adapters.md): retry with backoff,
circuit breaker, cached fallback. Forecasts run with available data.

### Stale alert resolution

If a model fails repeatedly for a station, no new forecast is produced and
existing alerts for that station are never resolved by `resolve_stale_alerts`.
To prevent permanently stale alerts:

- A periodic maintenance task (daily) scans for alerts where `raised_at` is
  older than 2x the forecast horizon (e.g. 48 hours for a daily model) AND
  no superseding forecast exists.
- These alerts are flagged as `stale_unresolvable` in the dashboard and
  surfaced in the operations summary with the reason ("no forecast produced
  for 48+ hours").
- Stale alerts are NOT auto-resolved — the flood could still be happening.
  A forecaster must manually acknowledge or resolve them.

### Offline station alert handling

Observation-based alerts are resolved when a new observation falls below the
threshold. If a station goes offline, no new observations arrive and the alert
remains active indefinitely. To handle this:

- The health endpoint reports stations whose last observation is older than
  a configurable threshold (default: 2x the expected reporting interval).
- Observation-based alerts for offline stations are flagged as
  `station_offline` in the dashboard, not auto-resolved.
- The operations summary includes a "stations offline with active alerts"
  count for the morning briefing.

## Review History

| Round | Date | Reviewers | Blocking | Advisory | Status |
|-------|------|-----------|----------|----------|--------|
