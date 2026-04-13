# Logging Standards

> This document extends `docs/architecture-context.md` and `docs/standards/cicd.md`. It defines the logging strategy for all SAPPHIRE Flow code. For container log driver settings and retention, see `cicd.md` § Container logging. For audit log schema, see `docs/spec/types-and-protocols.md` § AuditEntry. For OWASP A09 compliance requirements, see `docs/standards/security.md`.
>
> **v0 note**: This standard applies from v0. All sections are implemented unless marked (v1+).

## Framework and configuration

structlog is the single logging framework. No stdlib `logging.getLogger()` in application code.

Logger acquisition at module level:

```python
import structlog

log = structlog.get_logger(__name__)
```

No logger dependency injection. No Protocol accepts a logger parameter.

### Shared processor chain

```python
import logging
import os
import structlog


def _shared_processors() -> list[structlog.types.Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
```

### `configure_prefect_logging()`

Called once at Prefect worker startup. Adds the Prefect context auto-processor.

```python
def configure_prefect_logging(config_level: str = "INFO") -> None:
    processors = [_shared_processors()[0], _add_prefect_context, *_shared_processors()[1:]]
    _apply_structlog_config(processors, config_level)


def _add_prefect_context(
    logger: structlog.types.WrappedLogger,
    method_name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    from prefect.runtime import flow_run, task_run

    if (frid := flow_run.id) is not None:
        event_dict.setdefault("flow_run_id", str(frid))
    if (fname := flow_run.flow_name) is not None:
        event_dict.setdefault("flow_name", fname)
    if (tname := task_run.task_name) is not None:
        event_dict.setdefault("task_name", tname)
    return event_dict
```

### `configure_api_logging()`

Called once in the FastAPI app factory. No Prefect processor — request context comes from middleware instead.

```python
def configure_api_logging(config_level: str = "INFO") -> None:
    _apply_structlog_config(_shared_processors(), config_level)
```

### `configure_cli_logging()`

Called once at entry in CLI tools (e.g., `record_fixtures.py`). No Prefect processor. Uses `_apply_structlog_config()` with the shared renderer selection (JSON in prod, console in dev).

```python
def configure_cli_logging(config_level: str = "INFO") -> None:
    _apply_structlog_config(_shared_processors(), config_level)
```

Functionally identical to `configure_api_logging()` — both delegate to `_apply_structlog_config()` with the shared processor chain. Kept as a separate entry point for semantic clarity (CLI tools vs API server). `configure_test_logging()` is intentionally excluded from this refactor — it hardcodes `ConsoleRenderer` and `DEBUG` level without going through `_apply_structlog_config()`.

### `configure_test_logging()`

Called in test fixtures or `conftest.py`. Uses shared processors (no Prefect), always console, DEBUG level.

```python
def configure_test_logging() -> None:
    processors = _shared_processors()

    structlog.configure(
        processors=[*processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(),
        ],
        foreign_pre_chain=processors,
    )

    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
```

### Shared configuration helper

```python
def _apply_structlog_config(processors: list[structlog.types.Processor], config_level: str) -> None:
    renderer = (
        structlog.dev.ConsoleRenderer()
        if os.environ.get("SAPPHIRE_ENV") == "dev"
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[*processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=processors,
    )

    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root.addHandler(handler)
    # Config level read at startup; runtime level changes use env vars
    root.setLevel(getattr(logging, config_level.upper(), logging.INFO))

    # Per-module level overrides: SAPPHIRE_LOG_ADAPTERS=DEBUG etc.
    for key, val in os.environ.items():
        if key.startswith("SAPPHIRE_LOG_"):
            module = key[len("SAPPHIRE_LOG_"):].lower().replace("__", "\x00").replace("_", ".").replace("\x00", "_")
            module_logger = logging.getLogger(f"sapphire_flow.{module}")
            module_logger.setLevel(getattr(logging, val.upper(), logging.INFO))
```

**Underscore encoding:** Single `_` maps to `.` (package separator). Double `__` maps to a literal `_` (for module names like `forecast_interface`). Example: `SAPPHIRE_LOG_ADAPTERS_FORECAST__INTERFACE=DEBUG` targets `sapphire_flow.adapters.forecast_interface`. Leading or trailing `__` in a component produces a leading or trailing `_` in the module name — this is almost always a typo.

**Warning**: Enabling DEBUG on a high-frequency adapter in production can generate 10K+ events/day at ~50 stations (scaling linearly — ~34K+/day at ~170 stations, ~200K+/day at ~1000 stations). Use targeted module overrides (e.g., `SAPPHIRE_LOG_ADAPTERS_METEOSWISS=DEBUG`), not root-level DEBUG.

`SAPPHIRE_ENV=prod` (default) selects `JSONRenderer`. `SAPPHIRE_ENV=dev` selects `ConsoleRenderer`.

Ruff rule `T201` bans `print()` — no exceptions.

## Mandatory context fields

| Field | Bound at | Description |
|---|---|---|
| `flow_run_id` | Auto-processor (Prefect only) | Prefect flow run UUID. Correlation ID for the pipeline run. |
| `flow_name` | Auto-processor (Prefect only) | Prefect flow name (e.g., `ingest_weather`) |
| `task_name` | Auto-processor (Prefect only) | Prefect task name |
| `station_id` | Per-station task (explicit) | Station being processed. Only in station-scoped operations. |
| `component` | `add_logger_name` | Auto-set from `__name__` (e.g., `sapphire_flow.adapters.meteoswiss`) |
| `request_id` | FastAPI middleware (explicit) | UUID per HTTP request. Only in API context. |
| `parent_flow_run_id` | Sub-flow caller (explicit) | Parent flow's run ID. Only when calling sub-flows. |

### Recommended context fields

| Field | Bound at | Description |
|---|---|---|
| `parameter` | `bind_contextvars(parameter=...)` in `compute_skills_task` | The forecast parameter being scored (e.g., `discharge`, `water_level`). Not mandatory globally — most flows operate on a single implicit parameter. |
| `model_id` | `bind_contextvars(model_id=str(model_id))` at flow entry in `onboard_model_flow`, and per-station-per-model iteration in `run_forecast_cycle` | Model being processed. In Flow 13 (`onboard_model_flow`), bound at flow entry for the duration of the run. In Flow 1, bound within the per-station-per-model loop so all events in that iteration (including `forecast.input_quality_assessed`) include it automatically. |
| `group_id` | `bound_contextvars(group_id=str(group_id))` in per-unit loop (group-scoped units) | Station group being processed. Scoped to the per-unit iteration. |

## Context binding protocol

1. **Flow entry**: Auto-processor reads `prefect.runtime`. No manual binding.
2. **Task entry**: Auto-processor reads `prefect.runtime`. No manual binding.
3. **Per-station fan-out**: Use `bound_contextvars()` when possible (scopes the binding, restores parent context on exit):
   ```python
   with structlog.contextvars.bound_contextvars(station_id=str(station_id)):
       result = process_station(station_id)
   ```
   When a context manager doesn't fit (e.g., Prefect task functions where the body IS the scope), use `bind_contextvars` WITHOUT clearing first — parent context (e.g., `nwp_cycle_reference_time`) is useful for debugging:
   ```python
   structlog.contextvars.bind_contextvars(station_id=str(station_id))
   ```
   The auto-processor handles `flow_run_id`/`flow_name`/`task_name` regardless.
   Prefect's `ThreadPoolTaskRunner` copies contextvars per submission, so sibling tasks are isolated.
4. **FastAPI middleware**: `clear_contextvars()` IS correct here — each request starts fresh, no parent context to preserve:
   ```python
   structlog.contextvars.clear_contextvars()
   structlog.contextvars.bind_contextvars(
       request_id=str(uuid4()), method=request.method, path=request.url.path
   )
   ```
5. **Sub-flows**: Before calling the sub-flow:
   ```python
   structlog.contextvars.bind_contextvars(
       parent_flow_run_id=str(prefect.runtime.flow_run.id)
   )
   ```
   The sub-flow's own `flow_run_id` is set by the auto-processor.
6. **Services/stores/adapters**: Do NOT bind context. They inherit context from the calling flow/task/request. Service code calls `log.info(...)` with event-specific keyword args only.

## Event naming

Pattern: `{entity}.{action}` — entity is the domain object, action is what happened.

Developers create new event names following this pattern. Core-subsystem events (observation QC, forecast QC, alerting) are canonicalized in the table below for discoverability.

Examples:

| Entity | Example events |
|---|---|
| `nwp` | `fetch_started`, `fetch_completed`, `fetch_failed`, `archive_started`, `archive_completed`, `archive_loaded`, `archive_not_found` |
| `extraction` | `started`, `completed`, `station_skipped` |
| `observation` | `ingested`, `qc_passed`, `qc_failed`, `qc_suspect` |
| `forecast` | `run_started`, `run_completed`, `stored`, `qc_passed`, `qc_failed`, `qc_suspect` |
| `alert` | `raised`, `resolved`, `suppressed` |
| `model` | `loaded`, `prediction_completed` |
| `station` | `onboarding_started`, `status_changed`, `fetch_completed` |
| `fixture` | `loaded`, `recording_started`, `fetch_completed`, `file_written`, `recording_failed` |
| `pipeline` | `health_check_completed` |
| `request` | `started`, `completed` |

### Canonical model onboarding events (Flow 13)

All `*_completed` / `*_failed` events include `duration_ms`. Fast sub-steps (compatibility, smoke test, skill gate) emit only `_completed`/`_failed` — `_started` omitted since these complete in <1s.

| Event | Level | Notes |
|---|---|---|
| `model.onboarding_started` | INFO | Flow entry; bind `model_id` at this point |
| `model.onboarding_unit_started` | INFO | Per-unit; with `station_id` (station-scoped) or `group_id` (group-scoped) |
| `model.onboarding_unit_completed` | INFO | Per-unit timing summary |
| `model.compatibility_completed` | INFO | Expected outcome; include `is_compatible` field |
| `model.compatibility_failed` | INFO | Expected per-unit skip (incompatible station) — not an error condition |
| `model.smoke_test_completed` | INFO | Function returned normally |
| `model.smoke_test_failed` | ERROR | Unexpected exception; use `error=str(exc)` — not `passed=False` |
| `model.skill_gate_completed` | INFO if `passed=True`, WARNING if `passed=False` | Level-conditional. Include `passed`, `failing_metrics` (list[str]) |
| `model.skill_gate_failed` | ERROR | Unexpected exception during gate evaluation; use `error=str(exc)` |
| `model.promotion_completed` | INFO | Artifact transitioned TRAINING → ACTIVE |
| `model.assignment_skipped_inactive` | WARNING | Operator deliberately disabled this model for station/group |
| `model.onboarding_completed` | INFO | Flow exit; include `promoted_count`, `failed_count`, `skipped_count` |

Rules:

- Past tense for completed events (`completed`, `stored`, `failed`). Use `_started` / `_completed` pairs.
- Always use `{entity}.{action}` pattern — no f-string messages.
- Additional context goes in keyword arguments, not the event string.
- **List-of-dicts kwargs** are accepted when an event carries variable-length structured sub-items. Example: `forecast.input_quality_assessed` passes `flags` as `list[dict]` with keys `category`, `level`, and `detail`. Use this pattern sparingly — prefer flat kwargs for simple scalar context.

### Canonical forecast cycle events (Flow 1)

| Event | Level | Notes |
|---|---|---|
| `forecast.input_quality_assessed` | INFO if `input_quality == "partial"`, WARNING if `input_quality == "degraded"` | Level-conditional. Emitted when input quality is not FULL. Not emitted when quality is FULL. Kwargs: `input_quality` (str), `flags` (list of dicts: `{"category": str, "level": str, "detail": str}`). Bind `model_id` and `station_id` via `bind_contextvars` before this event so both appear automatically. |

```python
# CORRECT
log.info("forecast.run_completed", lead_time_hours=120, ensemble_size=21, duration_ms=3400)

# WRONG — free-text event name
log.info(f"Forecast completed for {station_id} in {duration}ms")
```

## Log levels

**ERROR** — Unrecoverable failure. Requires human attention.
- Adapter connection failure after all retries exhausted
- Database write failure
- Model artifact not found or corrupted
- Unhandled exception propagating to Prefect

**WARNING** — Degraded state. Operation continues.
- NWP cycle older than expected (fallback used)
- Station skipped due to missing data (flow continues with remaining stations)
- Observation QC suspect
- Ensemble size below `min_operational_ensemble_size` (alert logic skipped)

**INFO** — Key operational events.
- Flow/task started and completed (with `duration_ms`)
- Per-step timing (D6 instrumentation)
- Observation batch ingested (with `record_count`)
- Forecast stored
- Alert raised/resolved
- Health check results

**DEBUG** — Diagnostic detail. Off in production by default.
- Adapter HTTP request metadata (URL, status code, response size — NOT response body)
- SQL query timing
- Individual ensemble member processing
- Config values loaded

## Debugging workflows

### Scenario 1: "Flow 1 failed overnight — which station?"

Production (JSON logs):
```bash
docker logs sapphire-worker --since 6h | jq 'select(.log_level == "error")'
# Shows: flow_run_id, station_id, component, event, exception
```

Dev (console logs):
```bash
docker logs sapphire-worker --since 6h | grep ERROR
```

Cross-reference: Prefect UI shows flow run state and task-level failures. Match the `flow_run_id` from logs to the Prefect UI run page.

### Scenario 2: "Dashboard shows stale forecast for station X"

1. Look up the station's most recent forecast in the DB:
   ```sql
   SELECT flow_run_id FROM forecasts WHERE station_id = '...' ORDER BY issue_time DESC LIMIT 1;
   ```
2. Use the `flow_run_id` to find all worker logs from that run:
   ```bash
   docker logs sapphire-worker | jq 'select(.flow_run_id == "abc-123")'
   ```
3. Cross-service tracing: the `forecasts` table records `flow_run_id` (set during Flow 1 step 1.11), linking API-visible data back to the producing flow run.
4. Prefect UI: search by flow run ID to see task states, durations, and retries.

### Scenario 3: "Observation QC flags too aggressive for station X"

```bash
docker logs sapphire-worker | jq 'select(.station_id == "X" and .event | startswith("observation.qc"))'
```

This shows every QC decision for the station: `observation.qc_passed`, `observation.qc_failed`, `observation.qc_suspect` with their attached context (observed value, threshold, check name). Cross-reference with Prefect UI to see the containing flow run's overall state.

## Per-step timing instrumentation (D6)

Mandatory from v0 for every Flow 1 step (see `v0-scope.md` D6).

```python
import time
import structlog

log = structlog.get_logger(__name__)

# Inside a @task:
t0 = time.perf_counter()
result = do_work()
duration_ms = (time.perf_counter() - t0) * 1000
log.info("nwp.fetch_completed", duration_ms=round(duration_ms, 1), record_count=len(result))
```

- `duration_ms` is mandatory on all `*.completed` events.
- Use `time.perf_counter()`, not `time.time()`.
- Round to one decimal place.

## Prefect-specific settings

- Use `log_prints=False` on any `@task` or `@flow` used in high-fan-out `task.map()` patterns, and on all tasks in Flows 1 and 2.
- Production: `PREFECT_LOGGING_LEVEL=WARNING` — suppresses Prefect's internal chatter. Our structlog events provide the operational picture.
- Dev: `PREFECT_LOGGING_LEVEL=INFO`.
- Prefect UI remains useful for flow run state inspection. Operational diagnostics come from structlog.

## Audit log vs application log

Three distinct destinations:

| Destination | Medium | Retention | Purpose | Decision rule |
|---|---|---|---|---|
| Application log | structlog -> stdout -> Docker `json-file` | Ephemeral (50 MB x 5 files per container; plan 013: increase `max-file` for worker at >300 stations — see cicd.md line 125) | Debugging, performance, incident response | "What happened and why?" |
| Audit log **(v1)** | `audit_log` DB table (INSERT-only) | Permanent | Security and compliance | "Who did what?" |
| Pipeline health | `pipeline_health` DB table | 30 days | Flow 4 watchdog, `/api/v1/health/detail` | "Is the system healthy?" |

In v0, only **application logs** and **pipeline health** are active. The audit log destination is not implemented until v1.

Different destinations serve different audiences. The same underlying problem (e.g., corrupted model artifact) may appear as an ERROR in application logs (with traceback, for developers) and as a `pipeline_health` record (status check, for ops). This is not duplication — each destination records the event for its own purpose. What to avoid: logging the same event twice to the *same* destination.

## Security: what NOT to log

- **Never**: secrets, API keys, passwords, tokens (even partially)
- **Never**: raw request/response bodies from external APIs (may contain credentials in headers)
- **Never**: database connection strings
- **IP addresses**: audit_log only, for failed logins (per `security.md`). Not in application logs.
- **Observation values**: OK at DEBUG level (public government data). Not at INFO (too verbose).
- **Exception tracebacks**: Use structlog's `format_exc_info` processor (already configured). Do not add custom redaction logic in v0. (v1+) Add secret redaction processor.

## Configuration

Add to `config-reference.toml`:

```toml
[logging]
level = "INFO"      # Root log level. Per-module override: env var SAPPHIRE_LOG_{MODULE}=DEBUG
```

Per-module level overrides via environment variables (e.g., `SAPPHIRE_LOG_ADAPTERS_METEOSWISS=DEBUG`), not config file.

## Testing

`structlog.testing.capture_logs()` bypasses processors entirely — it captures the raw event dict before any processing. This means tests don't need `configure_*_logging()` at all for simple assertions:

```python
import structlog

def test_logs_error_on_missing_artifact():
    with structlog.testing.capture_logs() as cap:
        load_model("nonexistent")
    assert any(e["log_level"] == "error" and e["event"] == "model.loaded" for e in cap)
```

For tests that exercise the full logging pipeline (e.g., verifying processor behavior), use `configure_test_logging()` — shared processors, console renderer, DEBUG level, no Prefect processor.

Integration tests running inside Prefect (e.g., end-to-end pipeline tests) use `configure_prefect_logging()` — same as production.

Do NOT assert on log messages as behavioral contracts — logs are diagnostics. Exception: asserting that ERROR-level events are emitted for specific failure modes is acceptable.
