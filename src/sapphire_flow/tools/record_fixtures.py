from __future__ import annotations

import argparse
import time
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import structlog

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.logging import configure_cli_logging
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import GeoCoord
from sapphire_flow.types.enums import (
    GaugingStatus,
    StationKind,
    StationOwnership,
    StationStatus,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.station import StationConfig

if TYPE_CHECKING:
    from sapphire_flow.protocols.adapters import StationDataSource
    from sapphire_flow.types.datetime import UtcDatetime

log = structlog.get_logger(__name__)


def parse_stations_toml(path: Path) -> list[StationConfig]:
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Stations file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"Invalid TOML in {path}: {exc}") from exc

    entries: list[dict[str, object]] = data.get("stations", [])
    if not entries:
        raise ConfigurationError(f"No [[stations]] entries in {path}")

    now = ensure_utc(datetime.now(UTC))
    configs: list[StationConfig] = []
    for entry in entries:
        code = str(entry["code"])
        station_id = StationId(uuid.uuid5(uuid.NAMESPACE_URL, code))
        alt_raw = entry.get("altitude_masl")
        altitude = float(alt_raw) if alt_raw is not None else None  # type: ignore[arg-type]
        measured = entry.get("measured_parameters", ["discharge", "water_level"])
        status_str = str(entry.get("station_status", "operational"))
        gauging_str = str(entry.get("gauging_status", "gauged"))
        configs.append(
            StationConfig(
                id=station_id,
                code=code,
                name=str(entry["name"]),
                location=GeoCoord(
                    lon=float(entry["lon"]),  # type: ignore[arg-type]
                    lat=float(entry["lat"]),  # type: ignore[arg-type]
                    altitude_masl=altitude,
                ),
                station_kind=StationKind(str(entry.get("station_kind", "river"))),
                basin_id=None,
                timezone=str(entry.get("timezone", "Europe/Zurich")),
                regulation_type=None,
                forecast_targets=None,
                measured_parameters=frozenset(measured),  # type: ignore[arg-type]
                station_status=StationStatus(status_str),
                created_at=now,
                updated_at=now,
                network=str(entry.get("network", "BAFU")),
                ownership=StationOwnership(str(entry.get("ownership", "foreign"))),
                wigos_id=(str(entry["wigos_id"]) if "wigos_id" in entry else None),
                gauging_status=GaugingStatus(gauging_str),
            )
        )
    return configs


def record_observations(
    adapter: StationDataSource,
    station_configs: list[StationConfig],
    start: UtcDatetime,
    end: UtcDatetime,
    output_dir: Path,
) -> None:
    id_to_code: dict[StationId, str] = {cfg.id: cfg.code for cfg in station_configs}

    log.info(
        "fixture.recording_started",
        source="bafu",
        station_count=len(station_configs),
        start_date=start.isoformat(),
        end_date=end.isoformat(),
    )

    since = {cfg.id: start for cfg in station_configs}

    t0 = time.perf_counter()
    try:
        raw_observations = adapter.fetch_observations(station_configs, since)
    except Exception as exc:
        log.error(
            "fixture.recording_failed",
            source="bafu",
            error=str(exc),
        )
        raise SystemExit(1) from exc

    # Post-fetch filter: keep only observations before end_dt
    observations = [obs for obs in raw_observations if obs.timestamp < end]

    duration_ms = round((time.perf_counter() - t0) * 1000, 1)
    log.info(
        "fixture.fetch_completed",
        source="bafu",
        record_count=len(observations),
        duration_ms=duration_ms,
    )

    rows: list[dict[str, object]] = [
        {
            "station_code": id_to_code[obs.station_id],
            "timestamp": obs.timestamp,
            "parameter": obs.parameter,
            "value": obs.value,
            "source": obs.source.value,
        }
        for obs in observations
    ]

    df = pl.DataFrame(
        rows,
        schema={
            "station_code": pl.Utf8,
            "timestamp": pl.Datetime("us", "UTC"),
            "parameter": pl.Utf8,
            "value": pl.Float64,
            "source": pl.Utf8,
        },
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "bafu_observations.parquet"
    df.write_parquet(output_path)

    file_size = output_path.stat().st_size
    log.info(
        "fixture.file_written",
        output_path=str(output_path),
        row_count=len(df),
        file_size_bytes=file_size,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=("Record fixture data from live adapters for replay testing.")
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=["bafu"],
        help="Data source to record from.",
    )
    parser.add_argument(
        "--stations",
        type=Path,
        default=Path("tests/fixtures/reference/stations.toml"),
        help="Path to stations TOML file.",
    )
    parser.add_argument(
        "--start",
        type=str,
        required=True,
        help="Start date (ISO 8601).",
    )
    parser.add_argument(
        "--end",
        type=str,
        required=True,
        help="End date (ISO 8601).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/fixtures/reference/"),
        help="Output directory.",
    )

    args = parser.parse_args()

    configure_cli_logging()

    if args.source == "bafu":
        _run_bafu(args)


def _run_bafu(args: argparse.Namespace) -> None:
    import httpx

    from sapphire_flow.adapters.hydro_scraper import (
        HydroScraperAdapter,
    )

    start_dt = ensure_utc(datetime.fromisoformat(args.start))
    end_dt = ensure_utc(datetime.fromisoformat(args.end))

    try:
        with open("config.toml", "rb") as f:
            data = tomllib.load(f)
        endpoint: str = data["adapters"]["river_stations"]["endpoint"]
    except (
        FileNotFoundError,
        tomllib.TOMLDecodeError,
        KeyError,
    ) as exc:
        raise ConfigurationError(
            f"Cannot read BAFU endpoint from config.toml: {exc}"
        ) from exc

    station_configs = parse_stations_toml(args.stations)

    with httpx.Client(
        timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0)
    ) as client:
        adapter = HydroScraperAdapter(endpoint=endpoint, http_client=client)
        record_observations(
            adapter=adapter,
            station_configs=station_configs,
            start=start_dt,
            end=end_dt,
            output_dir=args.output,
        )


if __name__ == "__main__":
    main()
