"""Collector flow for the BAFU LINDAS observation archive (Plan 136).

Hourly Prefect flow: fetches the WHOLE ``foen/hydro`` graph in a single
SPARQL request (`adapters/bafu_observation.py`), then archives both the raw
SPARQL-results JSON payload and long-form parsed rows to a QUARANTINED
filesystem path, keyed by BAFU gauge code + LINDAS-observed kind
(river/lake) — NOT by ``station_id``. Decoupled from station onboarding.

**Quarantine.** The flow is a no-op unless
``DeploymentConfig.bafu_observation_archive_path`` is explicitly set — it
never falls back to any operational path, never writes observation
*values* or a ``station_id`` to the operational DB, and never constructs a
``RawObservation``. (The Flow 4 staleness heartbeat below writes one
``PipelineHealthRecord`` of run-stats *metadata* to the operational DB; see
the note at the call site.)

**Restatement-safe dedup — path-existence on a per-cycle snapshot identity.**
Archive layout is one immutable parquet snapshot per hourly slot, named by
``cycle_at = clock().replace(minute=0, second=0, microsecond=0)``. A retry
within the same hour resolves to the same ``cycle_at``, finds the snapshot
already present, and short-circuits BEFORE any network fetch (unlike the
sibling forecast collector, ``cycle_at`` is known from the clock alone —
no fetch is needed to determine it). A new hour writes a new snapshot.
Restatements are preserved by construction: a corrected value simply lands
in a later cycle's snapshot, never overwriting an earlier one.

**Heartbeat + freshness (Flow 4 staleness hook).** One best-effort
``PipelineHealthRecord`` (``check_type=BAFU_OBSERVATION_FRESHNESS``) per
non-deduped run. Freshness is NETWORK-level (the newest ``measurement_time``
across all archived rows), never a per-gauge minimum — a dead gauge can sit
in the LINDAS graph for >1 year without ever going stale itself. A total
fetch failure (HTTP error, parse/schema-drift error, a truncated fetch, or
an empty whole-graph response) writes a **CRITICAL** heartbeat FIRST, then
re-raises so the Prefect run is marked failed — mirroring
``collect_bafu_forecasts.py`` but explicitly wrapping the fetch so an
outage is never silently invisible to Flow 4.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import polars as pl
import structlog
from prefect import flow, task
from prefect.cache_policies import NO_CACHE

from sapphire_flow.exceptions import AdapterError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import PipelineCheckType, PipelineHealthStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.types.bafu_observation import BafuObservationRow
    from sapphire_flow.types.datetime import UtcDatetime

log = structlog.get_logger(__name__)

# A non-empty fetch whose NETWORK-newest measurement_time is older than this
# writes a CRITICAL heartbeat (reason "stale_measurement_time") — a
# served-but-frozen LINDAS graph would otherwise report OK forever and stay
# invisible to the watchdog (which reads only status + checked_at). The
# collection still SUCCEEDS (the snapshot is archived, the run does not
# re-raise). Kept aligned with ops/watchdog.py's BAFU_OBS_STALE_THRESHOLD
# (hourly collector → ~3 missed cycles); a plain constant, NOT config.
_STALE_MEASUREMENT_THRESHOLD = timedelta(hours=3)

_ROW_SCHEMA = {
    "gauge_code": pl.Utf8,
    "lindas_kind": pl.Utf8,
    "parameter": pl.Utf8,
    "value": pl.Float64,
    "measurement_time": pl.Datetime("us", "UTC"),
    "cycle_at": pl.Datetime("us", "UTC"),
}


class _ObservationAdapter(Protocol):
    def fetch_all_observations_with_raw(
        self,
    ) -> tuple[list[BafuObservationRow], dict[str, Any]]: ...


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuObservationCollectionResult:
    row_count: int
    gauge_count: int
    newest_measurement_time: UtcDatetime | None
    dedup_skipped: bool


_EMPTY_RESULT = BafuObservationCollectionResult(
    row_count=0,
    gauge_count=0,
    newest_measurement_time=None,
    dedup_skipped=False,
)


# ---------------------------------------------------------------------------
# Health-store plumbing (best-effort, never fatal) — mirrors
# collect_bafu_forecasts.py's identically-named helpers.
# ---------------------------------------------------------------------------


def _build_health_store_best_effort() -> tuple[object, object | None]:
    try:
        from sapphire_flow.flows._db import setup_production_stores

        conn, stores = setup_production_stores(os.environ["DATABASE_URL"])
        return conn, stores["pipeline_health_store"]
    except Exception as exc:  # noqa: BLE001
        log.warning("bafu_observation.health_store_setup_failed", error=str(exc))
        return None, None


def _dispose_conn(conn: object) -> None:
    close = getattr(conn, "close", None)
    if not callable(close):
        return
    try:
        close()
        dispose = getattr(getattr(conn, "engine", None), "dispose", None)
        if callable(dispose):
            dispose()
    except Exception as exc:  # noqa: BLE001
        log.warning("bafu_observation.db_conn_close_failed", error=str(exc))


def _append_health_record(
    pipeline_health_store: object | None,
    *,
    checked_at: UtcDatetime,
    status: PipelineHealthStatus,
    row_count: int,
    gauge_count: int,
    newest_measurement_time: UtcDatetime | None,
    error_type: str | None,
) -> None:
    if pipeline_health_store is None:
        return
    append = getattr(pipeline_health_store, "append_health_record", None)
    if not callable(append):
        return

    from sapphire_flow.types.pipeline import PipelineHealthRecord

    detail: dict[str, object] = {
        "row_count": row_count,
        "gauge_count": gauge_count,
        "newest_measurement_time": (
            newest_measurement_time.isoformat()
            if newest_measurement_time is not None
            else None
        ),
        "error_type": error_type,
    }
    try:
        append(
            PipelineHealthRecord(
                check_type=PipelineCheckType.BAFU_OBSERVATION_FRESHNESS,
                checked_at=checked_at,
                status=status,
                subject="bafu_observation_collector",
                detail=detail,
                cycle_time=None,
                created_at=checked_at,
            )
        )
    except Exception as exc:
        log.warning(
            "pipeline.health_record_write_failed",
            check_type=PipelineCheckType.BAFU_OBSERVATION_FRESHNESS.value,
            subject="bafu_observation_collector",
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Quarantined archive helpers (pure functions — no store/Protocol coupling)
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, write: Callable[[Path], object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    write(tmp)
    os.replace(tmp, path)


def _cycle_stem(cycle_at: UtcDatetime) -> str:
    return f"obs-{cycle_at.strftime('%Y%m%dT%H%M%SZ')}"


def _date_parts(cycle_at: UtcDatetime) -> tuple[str, str, str]:
    return f"{cycle_at:%Y}", f"{cycle_at:%m}", f"{cycle_at:%d}"


def _parquet_path(base_path: Path, cycle_at: UtcDatetime) -> Path:
    year, month, day = _date_parts(cycle_at)
    return (
        base_path / "parsed" / year / month / day / f"{_cycle_stem(cycle_at)}.parquet"
    )


def _raw_payload_path(base_path: Path, cycle_at: UtcDatetime) -> Path:
    year, month, day = _date_parts(cycle_at)
    return base_path / "raw" / year / month / day / f"{_cycle_stem(cycle_at)}.json"


def _already_archived(base_path: Path, cycle_at: UtcDatetime) -> bool:
    # Dedup on the PARQUET (written last, atomically) — the completion
    # marker, mirroring collect_bafu_forecasts.py's _already_archived.
    return _parquet_path(base_path, cycle_at).exists()


def _write_raw_payload(
    base_path: Path, cycle_at: UtcDatetime, payload: dict[str, Any]
) -> Path:
    path = _raw_payload_path(base_path, cycle_at)
    _atomic_write(path, lambda tmp: tmp.write_text(json.dumps(payload)))
    return path


def _rows_to_dataframe(
    rows: list[BafuObservationRow], cycle_at: UtcDatetime
) -> pl.DataFrame:
    data = [
        {
            "gauge_code": r.gauge_code,
            "lindas_kind": r.lindas_kind,
            "parameter": r.parameter,
            "value": r.value,
            "measurement_time": r.measurement_time,
            "cycle_at": cycle_at,
        }
        for r in rows
    ]
    return pl.DataFrame(data, schema=_ROW_SCHEMA)


def _write_rows_parquet(
    base_path: Path, cycle_at: UtcDatetime, rows: list[BafuObservationRow]
) -> Path:
    path = _parquet_path(base_path, cycle_at)
    frame = _rows_to_dataframe(rows, cycle_at)
    _atomic_write(path, frame.write_parquet)
    return path


# ---------------------------------------------------------------------------
# Prefect task
# ---------------------------------------------------------------------------


@task(
    name="fetch-bafu-observations",
    task_run_name="fetch-bafu-observations",
    cache_policy=NO_CACHE,
)
def _fetch_observations_task(
    adapter: _ObservationAdapter,
) -> tuple[list[BafuObservationRow], dict[str, Any]]:
    # No try/except here: the flow wraps this call so a total failure writes
    # the CRITICAL heartbeat before re-raising (see the flow body).
    return adapter.fetch_all_observations_with_raw()


# ---------------------------------------------------------------------------
# Production adapter factory
# ---------------------------------------------------------------------------


def build_production_adapter() -> _ObservationAdapter:
    import httpx

    from sapphire_flow.adapters.bafu_observation import BafuObservationAdapter

    http_client = httpx.Client(
        timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0)
    )
    endpoint = "https://lindas.admin.ch/query"
    return cast(
        "_ObservationAdapter",
        BafuObservationAdapter(endpoint=endpoint, http_client=http_client),
    )


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------


@flow(name="collect-bafu-observations", log_prints=False)
def collect_bafu_observations_flow(
    config: object = None,
    adapter: object = None,
    clock: object = None,
    pipeline_health_store: object | None = None,
) -> BafuObservationCollectionResult:
    if clock is None:
        clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731

    if config is None:
        from sapphire_flow.config.deployment import load_config

        config = load_config()
    config_t = cast("DeploymentConfig", config)

    # Quarantine gate: no-op unless an explicit, non-blank archive path is
    # configured. NEVER falls back to any operational path.
    _configured_path = config_t.bafu_observation_archive_path
    if _configured_path is None or not str(_configured_path).strip():
        log.info(
            "bafu_observation.disabled",
            reason="bafu_observation_archive_path not configured",
        )
        return _EMPTY_RESULT

    clock_t = cast("Callable[[], UtcDatetime]", clock)
    archive_base_path = Path(_configured_path)
    run_at = clock_t()
    cycle_at = ensure_utc(run_at.replace(minute=0, second=0, microsecond=0))

    _conn: object = None
    if adapter is None:
        adapter = build_production_adapter()
        if pipeline_health_store is None:
            _conn, pipeline_health_store = _build_health_store_best_effort()
    adapter_t = cast("_ObservationAdapter", adapter)

    log.info(
        "bafu_observation.starting",
        archive_base_path=str(archive_base_path),
        cycle_at=cycle_at.isoformat(),
    )

    try:
        if _already_archived(archive_base_path, cycle_at):
            log.debug(
                "bafu_observation.dedup_skip",
                cycle_at=cycle_at.isoformat(),
            )
            return BafuObservationCollectionResult(
                row_count=0,
                gauge_count=0,
                newest_measurement_time=None,
                dedup_skipped=True,
            )

        try:
            rows, raw_payload = _fetch_observations_task(adapter_t)
        except AdapterError as exc:
            _append_health_record(
                pipeline_health_store,
                checked_at=run_at,
                status=PipelineHealthStatus.CRITICAL,
                row_count=0,
                gauge_count=0,
                newest_measurement_time=None,
                error_type=str(exc),
            )
            raise

        if not rows:
            _append_health_record(
                pipeline_health_store,
                checked_at=run_at,
                status=PipelineHealthStatus.CRITICAL,
                row_count=0,
                gauge_count=0,
                newest_measurement_time=None,
                error_type="empty_response",
            )
            raise AdapterError(
                "BAFU LINDAS whole-graph fetch returned zero rows (empty response)"
            )

        gauge_count = len({(r.gauge_code, r.lindas_kind) for r in rows})
        newest_measurement_time = ensure_utc(max(r.measurement_time for r in rows))

        _write_raw_payload(archive_base_path, cycle_at, raw_payload)
        # Parquet is written last and unconditionally — it is the dedup
        # completion marker (_already_archived keys on it).
        _write_rows_parquet(archive_base_path, cycle_at, rows)

        log.info(
            "bafu_observation.complete",
            row_count=len(rows),
            gauge_count=gauge_count,
            cycle_at=cycle_at.isoformat(),
            newest_measurement_time=newest_measurement_time.isoformat(),
        )

        # Freshness gate: a served-but-frozen graph (every gauge stale) has a
        # NETWORK-newest measurement_time far behind run_at. That collection
        # still SUCCEEDED — the snapshot is archived above and we do NOT
        # re-raise — but the heartbeat must be CRITICAL so the watchdog alarms
        # (it reads only status + checked_at, never the measurement time). A
        # single dead gauge stays OK because network-newest is fresh.
        measurement_age = run_at - newest_measurement_time
        is_stale = measurement_age > _STALE_MEASUREMENT_THRESHOLD
        if is_stale:
            log.warning(
                "bafu_observation.stale_feed",
                newest_measurement_time=newest_measurement_time.isoformat(),
                age_seconds=measurement_age.total_seconds(),
                threshold_seconds=_STALE_MEASUREMENT_THRESHOLD.total_seconds(),
            )

        _append_health_record(
            pipeline_health_store,
            checked_at=run_at,
            status=(
                PipelineHealthStatus.CRITICAL if is_stale else PipelineHealthStatus.OK
            ),
            row_count=len(rows),
            gauge_count=gauge_count,
            newest_measurement_time=newest_measurement_time,
            error_type="stale_measurement_time" if is_stale else None,
        )

        return BafuObservationCollectionResult(
            row_count=len(rows),
            gauge_count=gauge_count,
            newest_measurement_time=newest_measurement_time,
            dedup_skipped=False,
        )
    finally:
        _dispose_conn(_conn)
