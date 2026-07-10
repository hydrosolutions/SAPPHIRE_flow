"""Route-C collector flow for BAFU's public operational forecast plots
(Plan 111, Status override 2026-07-10).

Hourly Prefect flow: fetches the ~54-station forecast inventory from
``hydrodaten.admin.ch``, then each station's ``q_forecast`` (discharge, all
stations) and ``p_forecast`` (level, lake stations only), and archives both
the raw payload and long-form parsed rows to a QUARANTINED filesystem path —
see the four safeguards below.

**Safeguard #2 (quarantined archive).** The flow is a no-op unless
``DeploymentConfig.bafu_forecast_archive_path`` is explicitly set — it never
falls back to any operational path, never writes to the operational DB, and
never mints a ``ModelId``. A single ``rm -rf`` of that directory discards the
whole archive.

**Safeguard #3 (evaluation-only).** Nothing in this module is wired to
training, model onboarding, or Flow 1. This is enforced by omission, not by a
runtime guard — the archive lives entirely outside the store/Protocol graph
those subsystems read from.

**Safeguard #4 (polite client).** A modest injected delay between station
fetches (see ``sleeper``), a retry cap (adapter-side, see
``adapters/bafu_forecast.py``), and raw-payload archival alongside the
parsed parquet (the endpoint is forward-only — a past forecast cannot be
re-fetched).

Dedup is keyed on ``issued_at`` (the forecast's own issue time), not fetch
time — issue time and publication time differ (Phase 0b).

# TODO(plan-111): Flow 4 staleness check. ``PipelineCheckType``
# (types/enums.py) has no member for a non-Flow-1 producer yet, and
# ``append_health_record`` is only called from run_forecast_cycle.py today.
# Wiring this collector into pipeline monitoring is a separate follow-up
# task (Gate G3's "Flow-4 monitoring hook" item in
# docs/plans/111-bafu-forecast-benchmarking.md) — not done here.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import polars as pl
import structlog
from prefect import flow, task
from prefect.cache_policies import NO_CACHE

from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.types.bafu_forecast import (
        BafuForecastRow,
        BafuForecastStation,
        BafuForecastVariant,
        BafuStationInventory,
        BafuVariantFetch,
    )
    from sapphire_flow.types.datetime import UtcDatetime

log = structlog.get_logger(__name__)

# Modest inter-station delay (safeguard #4). Injected as `sleeper` so tests
# never actually sleep.
_DEFAULT_REQUEST_DELAY_SECONDS = 1.0

_ROW_SCHEMA = {
    "station_key": pl.Utf8,
    "metric": pl.Utf8,
    "unit": pl.Utf8,
    "issued_at": pl.Datetime("us", "UTC"),
    "produced_at": pl.Datetime("us", "UTC"),
    "valid_time": pl.Datetime("us", "UTC"),
    "trace_name": pl.Utf8,
    "point_index": pl.Int64,
    "value": pl.Float64,
}


class _ForecastAdapter(Protocol):
    def fetch_station_inventory(self) -> BafuStationInventory: ...

    def fetch_variant_forecast(
        self,
        station_key: str,
        variant: BafuForecastVariant,
        produced_at: UtcDatetime,
    ) -> BafuVariantFetch | None: ...


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuForecastCollectionResult:
    stations_seen: int
    variants_fetched: int
    variants_absent: int
    variants_skipped_dedup: int
    variants_failed: int
    rows_archived: int


_EMPTY_RESULT = BafuForecastCollectionResult(
    stations_seen=0,
    variants_fetched=0,
    variants_absent=0,
    variants_skipped_dedup=0,
    variants_failed=0,
    rows_archived=0,
)


# ---------------------------------------------------------------------------
# Quarantined archive helpers (pure functions — no store/Protocol coupling)
# ---------------------------------------------------------------------------


def _variants_for_station(
    station: BafuForecastStation,
) -> tuple[BafuForecastVariant, ...]:
    # "missing" = station with no current data (BAFU's own legend); it has no
    # forecast to fetch, so skip it entirely rather than waste 404 requests.
    if station.icon == "missing":
        return ()
    # River stations only ever publish q_forecast; only lake/level stations
    # also carry p_forecast (Plan 111 Phase 0b). Deciding this station-side
    # (rather than always attempting both) keeps the client polite.
    if station.icon == "lake":
        return ("q_forecast", "p_forecast")
    return ("q_forecast",)


def _atomic_write(path: Path, write: Callable[[Path], object]) -> None:
    # Write to a sibling temp file then atomically rename, so a crash mid-write
    # never leaves a truncated file that the dedup check would treat as a
    # completed archive (the endpoint is forward-only — a lost cycle is
    # unrecoverable). os.replace is atomic within a filesystem.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    write(tmp)
    os.replace(tmp, path)


def _archive_stem(
    station_key: str, variant: BafuForecastVariant, issued_at: UtcDatetime
) -> str:
    # Single source of truth for the raw/parsed filename stem — the dedup check
    # relies on the raw and parquet names staying in lock-step.
    return f"{station_key}_{variant}_{issued_at.strftime('%Y%m%dT%H%M%SZ')}"


def _raw_payload_path(
    base_path: Path,
    station_key: str,
    variant: BafuForecastVariant,
    issued_at: UtcDatetime,
) -> Path:
    return base_path / "raw" / f"{_archive_stem(station_key, variant, issued_at)}.json"


def _parquet_path(
    base_path: Path,
    station_key: str,
    variant: BafuForecastVariant,
    issued_at: UtcDatetime,
) -> Path:
    return (
        base_path
        / "parsed"
        / f"{_archive_stem(station_key, variant, issued_at)}.parquet"
    )


def _already_archived(
    base_path: Path,
    station_key: str,
    variant: BafuForecastVariant,
    issued_at: UtcDatetime,
) -> bool:
    # Dedup on the PARQUET (written last, atomically) — the completion marker.
    # Keying on the raw file would treat a crash-truncated raw as "done" and
    # skip that issuance forever; keying on the parquet means a half-finished
    # cycle is simply re-fetched next run.
    return _parquet_path(base_path, station_key, variant, issued_at).exists()


def _write_raw_payload(
    base_path: Path,
    station_key: str,
    variant: BafuForecastVariant,
    issued_at: UtcDatetime,
    payload: dict[str, Any],
) -> Path:
    path = _raw_payload_path(base_path, station_key, variant, issued_at)
    _atomic_write(path, lambda tmp: tmp.write_text(json.dumps(payload)))
    return path


def _rows_to_dataframe(rows: list[BafuForecastRow]) -> pl.DataFrame:
    data = [
        {
            "station_key": r.station_key,
            "metric": r.metric,
            "unit": r.unit,
            "issued_at": r.issued_at,
            "produced_at": r.produced_at,
            "valid_time": r.valid_time,
            "trace_name": r.trace_name,
            "point_index": r.point_index,
            "value": r.value,
        }
        for r in rows
    ]
    return pl.DataFrame(data, schema=_ROW_SCHEMA)


def _write_rows_parquet(
    base_path: Path,
    station_key: str,
    variant: BafuForecastVariant,
    issued_at: UtcDatetime,
    rows: list[BafuForecastRow],
) -> Path:
    path = _parquet_path(base_path, station_key, variant, issued_at)
    frame = _rows_to_dataframe(rows)
    _atomic_write(path, frame.write_parquet)
    return path


# ---------------------------------------------------------------------------
# Prefect tasks
# ---------------------------------------------------------------------------


@task(
    name="fetch-bafu-forecast-inventory",
    task_run_name="fetch-bafu-forecast-inventory",
    cache_policy=NO_CACHE,
)
def _fetch_inventory_task(adapter: _ForecastAdapter) -> BafuStationInventory:
    # No try/except here: an unreachable GeoJSON is a total failure and
    # must raise (AdapterError propagates to the flow caller).
    return adapter.fetch_station_inventory()


@task(
    name="collect-bafu-station-forecasts",
    task_run_name="collect-bafu-station-forecasts",
    cache_policy=NO_CACHE,
)
def _collect_forecasts_task(
    adapter: _ForecastAdapter,
    stations: list[BafuForecastStation],
    produced_at: UtcDatetime,
    archive_base_path: Path,
    sleeper: Callable[[float], None],
    request_delay_seconds: float,
) -> BafuForecastCollectionResult:
    from sapphire_flow.exceptions import AdapterError

    variants_fetched = 0
    variants_absent = 0
    variants_skipped_dedup = 0
    variants_failed = 0
    rows_archived = 0
    request_made = False

    for station in stations:
        for variant in _variants_for_station(station):
            # Polite delay before every outbound request except the very first
            # of the run (per-request, not per-station — a lake station issues
            # two requests and must not fire them back-to-back).
            if request_made:
                sleeper(request_delay_seconds)
            request_made = True

            try:
                result = adapter.fetch_variant_forecast(
                    station.key, variant, produced_at
                )
            except AdapterError as exc:
                # A single station/variant failing is logged and counted,
                # not fatal to the run (per-station isolation).
                variants_failed += 1
                log.warning(
                    "bafu_forecast.variant_fetch_failed",
                    station_key=station.key,
                    variant=variant,
                    error=str(exc),
                )
                continue

            if result is None:
                variants_absent += 1
                continue

            if _already_archived(
                archive_base_path, station.key, variant, result.issued_at
            ):
                variants_skipped_dedup += 1
                log.debug(
                    "bafu_forecast.dedup_skip",
                    station_key=station.key,
                    variant=variant,
                    issued_at=result.issued_at.isoformat(),
                )
                continue

            _write_raw_payload(
                archive_base_path,
                station.key,
                variant,
                result.issued_at,
                result.raw_payload,
            )
            # Parquet is written last and unconditionally — it is the dedup
            # completion marker (_already_archived keys on it), so even a
            # zero-row result must leave one rather than re-fetch forever.
            _write_rows_parquet(
                archive_base_path,
                station.key,
                variant,
                result.issued_at,
                result.rows,
            )
            rows_archived += len(result.rows)

            variants_fetched += 1
            log.info(
                "bafu_forecast.variant_archived",
                station_key=station.key,
                variant=variant,
                issued_at=result.issued_at.isoformat(),
                row_count=len(result.rows),
            )

    return BafuForecastCollectionResult(
        stations_seen=len(stations),
        variants_fetched=variants_fetched,
        variants_absent=variants_absent,
        variants_skipped_dedup=variants_skipped_dedup,
        variants_failed=variants_failed,
        rows_archived=rows_archived,
    )


# ---------------------------------------------------------------------------
# Production adapter factory
# ---------------------------------------------------------------------------


def build_production_adapter() -> _ForecastAdapter:
    import httpx

    from sapphire_flow.adapters.bafu_forecast import BafuForecastAdapter

    http_client = httpx.Client(
        timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0)
    )
    return cast("_ForecastAdapter", BafuForecastAdapter(http_client=http_client))


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------


@flow(name="collect-bafu-forecasts", log_prints=False)
def collect_bafu_forecasts_flow(
    config: object = None,
    adapter: object = None,
    clock: object = None,
    sleeper: object = None,
) -> BafuForecastCollectionResult:
    if clock is None:
        clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731
    if sleeper is None:
        sleeper = time.sleep

    if config is None:
        from sapphire_flow.config.deployment import load_config

        config = load_config()
    config_t = cast("DeploymentConfig", config)

    # Safeguard #2 (quarantine): no-op unless an explicit, non-blank archive
    # path is configured. NEVER falls back to any operational path — a blank
    # value must not resolve to Path("") == the current working directory.
    _configured_path = config_t.bafu_forecast_archive_path
    if _configured_path is None or not str(_configured_path).strip():
        log.info(
            "bafu_forecast.disabled",
            reason="bafu_forecast_archive_path not configured",
        )
        return _EMPTY_RESULT

    clock_t = cast("Callable[[], UtcDatetime]", clock)
    sleeper_t = cast("Callable[[float], None]", sleeper)

    if adapter is None:
        adapter = build_production_adapter()
    adapter_t = cast("_ForecastAdapter", adapter)

    archive_base_path = Path(_configured_path)
    run_at = clock_t()
    log.info(
        "bafu_forecast.starting",
        archive_base_path=str(archive_base_path),
        run_at=run_at.isoformat(),
    )

    inventory = _fetch_inventory_task(adapter_t)
    log.info(
        "bafu_forecast.inventory_resolved",
        station_count=len(inventory.stations),
        produced_at=inventory.produced_at.isoformat(),
    )

    result = _collect_forecasts_task(
        adapter_t,
        inventory.stations,
        inventory.produced_at,
        archive_base_path,
        sleeper_t,
        _DEFAULT_REQUEST_DELAY_SECONDS,
    )

    log.info(
        "bafu_forecast.complete",
        stations_seen=result.stations_seen,
        variants_fetched=result.variants_fetched,
        variants_absent=result.variants_absent,
        variants_skipped_dedup=result.variants_skipped_dedup,
        variants_failed=result.variants_failed,
        rows_archived=result.rows_archived,
    )
    return result
