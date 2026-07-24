from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import polars as pl

from sapphire_flow.adapters.replay.station import ReplayStationAdapter
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import GeoCoord
from sapphire_flow.types.enums import (
    GaugingStatus,
    ObservationSource,
    StationKind,
    StationOwnership,
    StationStatus,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.observation import RawObservation
from sapphire_flow.types.station import StationConfig
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

if TYPE_CHECKING:
    from pathlib import Path


def _make_station_config(code: str) -> StationConfig:
    now = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
    return StationConfig(
        id=StationId(uuid.uuid5(uuid.NAMESPACE_URL, code)),
        code=code,
        name=f"Station {code}",
        location=GeoCoord(lon=7.5, lat=46.9),
        station_kind=StationKind.RIVER,
        basin_id=None,
        timezone="Europe/Zurich",
        regulation_type=None,
        forecast_targets=None,
        measured_parameters=frozenset({"discharge", "water_level"}),
        station_status=StationStatus.OPERATIONAL,
        created_at=now,
        updated_at=now,
        network="BAFU",
        ownership=StationOwnership.FOREIGN,
        wigos_id=None,
        gauging_status=GaugingStatus.GAUGED,
        tenant_id=DEFAULT_TENANT_ID,
    )


def _make_observations(
    station_id: StationId,
    timestamps: list[UtcDatetime],
) -> list[RawObservation]:
    return [
        RawObservation(
            station_id=station_id,
            timestamp=ts,
            parameter="discharge",
            value=100.0 + i,
            source=ObservationSource.MEASURED,
        )
        for i, ts in enumerate(timestamps)
    ]


class FakeSinceFilteringSource:
    def __init__(self, observations: list[RawObservation]) -> None:
        self._observations = observations

    def fetch_observations(
        self,
        station_configs: list[StationConfig],
        since: dict[StationId, UtcDatetime],
    ) -> list[RawObservation]:
        valid_ids = {cfg.id for cfg in station_configs}
        results: list[RawObservation] = []
        for obs in self._observations:
            if obs.station_id not in valid_ids:
                continue
            lower = since.get(obs.station_id)
            if lower is not None and obs.timestamp < lower:
                continue
            results.append(obs)
        return results


class TestRecordFixtures:
    def test_round_trip(self, tmp_path: Path) -> None:
        from sapphire_flow.tools.record_fixtures import (
            record_observations,
        )

        cfg = _make_station_config("2009")
        start = ensure_utc(datetime(2025, 3, 1, tzinfo=UTC))
        end = ensure_utc(datetime(2025, 3, 2, tzinfo=UTC))
        timestamps = [
            ensure_utc(datetime(2025, 3, 1, 6, 0, tzinfo=UTC)),
            ensure_utc(datetime(2025, 3, 1, 12, 0, tzinfo=UTC)),
        ]
        observations = _make_observations(cfg.id, timestamps)
        adapter = FakeSinceFilteringSource(observations)

        record_observations(
            adapter=adapter,  # type: ignore[arg-type]
            station_configs=[cfg],
            start=start,
            end=end,
            output_dir=tmp_path,
        )

        parquet_path = tmp_path / "bafu_observations.parquet"
        assert parquet_path.exists()

        replay = ReplayStationAdapter(
            fixture_path=parquet_path,
            simulated_time=lambda: end,
        )
        since = {cfg.id: start}
        replayed = replay.fetch_observations([cfg], since)
        assert len(replayed) == 2
        assert {obs.parameter for obs in replayed} == {"discharge"}
        assert {obs.value for obs in replayed} == {100.0, 101.0}
        assert all(obs.station_id == cfg.id for obs in replayed)
        assert all(obs.source == ObservationSource.MEASURED for obs in replayed)

    def test_end_date_filtering(self, tmp_path: Path) -> None:
        from sapphire_flow.tools.record_fixtures import (
            record_observations,
        )

        cfg = _make_station_config("2009")
        start = ensure_utc(datetime(2025, 3, 1, tzinfo=UTC))
        end = ensure_utc(datetime(2025, 3, 1, 10, 0, tzinfo=UTC))
        timestamps = [
            ensure_utc(datetime(2025, 3, 1, 6, 0, tzinfo=UTC)),
            ensure_utc(datetime(2025, 3, 1, 12, 0, tzinfo=UTC)),
        ]
        observations = _make_observations(cfg.id, timestamps)
        adapter = FakeSinceFilteringSource(observations)

        record_observations(
            adapter=adapter,  # type: ignore[arg-type]
            station_configs=[cfg],
            start=start,
            end=end,
            output_dir=tmp_path,
        )

        df = pl.read_parquet(tmp_path / "bafu_observations.parquet")
        assert len(df) == 1
        assert df["timestamp"][0] == datetime(2025, 3, 1, 6, 0, tzinfo=UTC)

    def test_parquet_schema(self, tmp_path: Path) -> None:
        from sapphire_flow.tools.record_fixtures import (
            record_observations,
        )

        cfg = _make_station_config("2009")
        start = ensure_utc(datetime(2025, 3, 1, tzinfo=UTC))
        end = ensure_utc(datetime(2025, 3, 2, tzinfo=UTC))
        timestamps = [ensure_utc(datetime(2025, 3, 1, 6, 0, tzinfo=UTC))]
        observations = _make_observations(cfg.id, timestamps)
        adapter = FakeSinceFilteringSource(observations)

        record_observations(
            adapter=adapter,  # type: ignore[arg-type]
            station_configs=[cfg],
            start=start,
            end=end,
            output_dir=tmp_path,
        )

        df = pl.read_parquet(tmp_path / "bafu_observations.parquet")
        expected_schema = {
            "station_code": pl.Utf8,
            "timestamp": pl.Datetime("us", "UTC"),
            "parameter": pl.Utf8,
            "value": pl.Float64,
            "source": pl.Utf8,
        }
        assert dict(df.schema) == expected_schema

    def test_stations_toml_parsing(self, tmp_path: Path) -> None:
        from sapphire_flow.tools.record_fixtures import (
            parse_stations_toml,
        )

        toml_content = """\
[[stations]]
code = "2009"
name = "Bern Schoenau"
lon = 7.45
lat = 46.93
measured_parameters = ["discharge", "water_level"]

[[stations]]
code = "2033"
name = "Basel Rheinhalle"
lon = 7.62
lat = 47.56
altitude_masl = 245.0
measured_parameters = ["discharge"]
"""
        toml_path = tmp_path / "stations.toml"
        toml_path.write_text(toml_content)

        configs = parse_stations_toml(toml_path)
        assert len(configs) == 2
        assert configs[0].code == "2009"
        assert configs[0].name == "Bern Schoenau"
        assert configs[0].location.lon == 7.45
        assert configs[0].location.lat == 46.93
        assert configs[0].location.altitude_masl is None
        assert configs[0].id == StationId(uuid.uuid5(uuid.NAMESPACE_URL, "2009"))
        assert configs[0].station_kind == StationKind.RIVER
        assert configs[0].station_status == StationStatus.OPERATIONAL
        assert configs[0].gauging_status == GaugingStatus.GAUGED
        assert configs[1].code == "2033"
        assert configs[1].location.altitude_masl == 245.0
        assert configs[1].measured_parameters == frozenset({"discharge"})
