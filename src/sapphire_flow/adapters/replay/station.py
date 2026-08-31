from __future__ import annotations

import time
from typing import TYPE_CHECKING

import polars as pl
import structlog

from sapphire_flow.exceptions import AdapterError, ConfigurationError
from sapphire_flow.types.enums import FetchOutcomeCause, ObservationSource
from sapphire_flow.types.observation import (
    HydroScraperBatchResult,
    RawObservation,
    StationFetchOutcome,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import StationConfig

log = structlog.get_logger(__name__)


def _code_to_config(
    station_configs: list[StationConfig],
) -> dict[str, StationConfig]:
    """Fixture rows key on `station_code` alone (see the Parquet schema doc
    in `docs/plans/archive/020-phase3-replay-recording.md`), but `code` is
    only unique per (network, code) in the DB (`uq_stations_network_code`).
    A requested batch containing two stations that share a code across
    networks — e.g. a WEATHER and a RIVER station both coded "2135" — cannot
    be disambiguated from `station_code` alone: every matching fixture row,
    including e.g. discharge, would silently route to whichever config
    happened to be last in `station_configs`. Fail loudly instead."""
    code_to_config: dict[str, StationConfig] = {}
    duplicate_codes: set[str] = set()
    for sc in station_configs:
        if sc.code in code_to_config and code_to_config[sc.code].id != sc.id:
            duplicate_codes.add(sc.code)
        code_to_config[sc.code] = sc
    if duplicate_codes:
        raise ConfigurationError(
            "ReplayStationAdapter cannot disambiguate station code(s) "
            f"{sorted(duplicate_codes)!r}: the replay fixture keys rows by "
            "`station_code` alone, but these codes are shared by more than "
            "one requested station (codes are only unique per network). "
            "Refusing to guess which station owns the fixture rows."
        )
    return code_to_config


class ReplayStationAdapter:
    def __init__(
        self,
        fixture_path: Path,
        simulated_time: Callable[[], UtcDatetime],
    ) -> None:
        if not fixture_path.exists():
            raise ConfigurationError(f"Fixture file not found: {fixture_path}")
        self._simulated_time = simulated_time
        t0 = time.perf_counter()
        try:
            self._df = pl.read_parquet(fixture_path)
        except Exception as exc:
            raise AdapterError(
                f"Failed to read Parquet fixture {fixture_path}: {exc}"
            ) from exc
        duration_ms = (time.perf_counter() - t0) * 1000
        log.debug(
            "fixture.loaded",
            fixture_path=str(fixture_path),
            total_rows=len(self._df),
            duration_ms=round(duration_ms, 2),
        )

    def fetch_observations(
        self,
        station_configs: list[StationConfig],
        since: dict[StationId, UtcDatetime],
    ) -> list[RawObservation]:
        t0 = time.perf_counter()
        now = self._simulated_time()

        code_to_config = _code_to_config(station_configs)
        valid_codes = set(code_to_config.keys())

        if self._df.is_empty() or not valid_codes:
            log.debug(
                "station.fetch_completed",
                record_count=0,
                duration_ms=0.0,
                station_count=len(station_configs),
            )
            return []

        # Filter to configured stations
        filtered = self._df.filter(pl.col("station_code").is_in(valid_codes))

        results: list[RawObservation] = []
        for row in filtered.iter_rows(named=True):
            code: str = row["station_code"]
            config = code_to_config[code]
            station_id = config.id
            ts = row["timestamp"]

            lower = since.get(station_id)
            if lower is not None and ts < lower:
                continue
            if ts >= now:
                continue

            try:
                source = ObservationSource(row["source"])
            except ValueError as exc:
                raise AdapterError(
                    f"Unknown ObservationSource value: {row['source']!r}"
                ) from exc

            results.append(
                RawObservation(
                    station_id=station_id,
                    timestamp=ts,
                    parameter=row["parameter"],
                    value=row["value"],
                    source=source,
                    rating_curve_id=None,
                    rating_curve_correction_version=None,
                )
            )

        duration_ms = (time.perf_counter() - t0) * 1000
        log.debug(
            "station.fetch_completed",
            record_count=len(results),
            duration_ms=round(duration_ms, 2),
            station_count=len(station_configs),
        )
        return results

    def fetch_observations_batch(
        self,
        station_configs: list[StationConfig],
        since: dict[StationId, UtcDatetime],
    ) -> HydroScraperBatchResult:
        """Minor fix (Plan 175 round 2) — `BatchStationDataSource` capability
        (`protocols/adapters.py`) so this adapter stays usable as
        `ingest_observations_flow`'s `adapter=` argument: T3 changed that
        flow to call `fetch_observations_batch` exclusively, which this
        class did not previously implement (an `AttributeError` any
        `StationDataSource`-conforming replay/test double would have hit).
        A thin per-station regrouping of `fetch_observations`'s exact
        filtering/parsing behavior — NOT a second implementation to drift
        from the first."""
        try:
            observations = self.fetch_observations(station_configs, since)
        except AdapterError as exc:
            # A fixture-wide parse failure (e.g. an unknown ObservationSource
            # value) previously aborted the whole batch. Report it against
            # every requested station so a health record still reflects the
            # failure, instead of it raising past the StationDataSource
            # boundary this method exists to give per-station accounting for.
            return HydroScraperBatchResult(
                outcomes=tuple(
                    StationFetchOutcome(
                        station_id=sc.id,
                        observations=(),
                        failure_cause=FetchOutcomeCause.MALFORMED_RESPONSE,
                        failure_detail=str(exc),
                    )
                    for sc in station_configs
                )
            )

        by_station: dict[StationId, list[RawObservation]] = {
            sc.id: [] for sc in station_configs
        }
        for obs in observations:
            by_station.setdefault(obs.station_id, []).append(obs)

        return HydroScraperBatchResult(
            outcomes=tuple(
                StationFetchOutcome(
                    station_id=sc.id,
                    observations=tuple(by_station.get(sc.id, [])),
                    failure_cause=None,
                    failure_detail=None,
                )
                for sc in station_configs
            )
        )
