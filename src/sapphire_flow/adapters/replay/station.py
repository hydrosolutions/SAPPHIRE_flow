from __future__ import annotations

import time
from typing import TYPE_CHECKING

import polars as pl
import structlog

from sapphire_flow.exceptions import AdapterError, ConfigurationError
from sapphire_flow.types.enums import ObservationSource
from sapphire_flow.types.observation import RawObservation

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import StationConfig

log = structlog.get_logger(__name__)


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

        code_to_config: dict[str, StationConfig] = {
            sc.code: sc for sc in station_configs
        }
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
