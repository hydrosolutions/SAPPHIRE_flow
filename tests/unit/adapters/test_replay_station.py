from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import polars as pl
import pytest

from sapphire_flow.adapters.replay.station import ReplayStationAdapter
from sapphire_flow.exceptions import AdapterError, ConfigurationError
from sapphire_flow.protocols.adapters import StationDataSource
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
from sapphire_flow.types.station import StationConfig
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

if TYPE_CHECKING:
    from pathlib import Path


def _write_fixture(
    path: Path,
    rows: list[dict],  # type: ignore[type-arg]
) -> None:
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
    df.write_parquet(path)


def _make_station_config(
    code: str,
    station_id: StationId | None = None,
) -> StationConfig:
    ts = ensure_utc(datetime(2024, 1, 1, tzinfo=UTC))
    return StationConfig(
        id=station_id or StationId(uuid.uuid4()),
        code=code,
        name=f"Station {code}",
        location=GeoCoord(lon=7.0, lat=47.0),
        station_kind=StationKind.RIVER,
        basin_id=None,
        timezone="Europe/Zurich",
        regulation_type=None,
        forecast_targets=None,
        measured_parameters=frozenset({"discharge"}),
        station_status=StationStatus.OPERATIONAL,
        created_at=ts,
        updated_at=ts,
        network="bafu",
        ownership=StationOwnership.OWN,
        wigos_id=None,
        gauging_status=GaugingStatus.GAUGED,
        tenant_id=DEFAULT_TENANT_ID,
    )


def _utc(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
) -> UtcDatetime:
    return ensure_utc(datetime(year, month, day, hour, tzinfo=UTC))


def _row(
    code: str,
    ts: datetime,
    param: str = "discharge",
    value: float = 10.0,
    source: str = "measured",
) -> dict[str, object]:
    return {
        "station_code": code,
        "timestamp": ts,
        "parameter": param,
        "value": value,
        "source": source,
    }


class TestReplayStationAdapter:
    def test_time_windowing(self, tmp_path: Path) -> None:
        sid = StationId(uuid.uuid4())
        cfg = _make_station_config("2004", station_id=sid)
        fixture = tmp_path / "obs.parquet"

        t1 = datetime(2024, 3, 1, 0, 0, tzinfo=UTC)
        t2 = datetime(2024, 3, 1, 6, 0, tzinfo=UTC)
        t3 = datetime(2024, 3, 1, 12, 0, tzinfo=UTC)

        _write_fixture(
            fixture,
            [
                _row("2004", t1, value=10.0),
                _row("2004", t2, value=20.0),
                _row("2004", t3, value=30.0),
            ],
        )

        since_time = ensure_utc(t2)
        sim_time = ensure_utc(t3)
        adapter = ReplayStationAdapter(fixture, lambda: sim_time)

        results = adapter.fetch_observations(
            [cfg],
            {sid: since_time},
        )

        assert len(results) == 1
        assert results[0].value == 20.0
        assert results[0].timestamp == t2

    def test_since_inclusive(self, tmp_path: Path) -> None:
        sid = StationId(uuid.uuid4())
        cfg = _make_station_config("2004", station_id=sid)
        fixture = tmp_path / "obs.parquet"

        t1 = datetime(2024, 3, 1, 6, 0, tzinfo=UTC)

        _write_fixture(fixture, [_row("2004", t1)])

        adapter = ReplayStationAdapter(
            fixture,
            lambda: _utc(2024, 3, 2),
        )
        results = adapter.fetch_observations(
            [cfg],
            {sid: ensure_utc(t1)},
        )

        assert len(results) == 1

    def test_station_filtering(self, tmp_path: Path) -> None:
        sid = StationId(uuid.uuid4())
        cfg = _make_station_config("2004", station_id=sid)
        fixture = tmp_path / "obs.parquet"

        t1 = datetime(2024, 3, 1, 0, 0, tzinfo=UTC)
        _write_fixture(
            fixture,
            [
                _row("2004", t1, value=10.0),
                _row("9999", t1, value=99.0),
            ],
        )

        adapter = ReplayStationAdapter(
            fixture,
            lambda: _utc(2024, 3, 2),
        )
        results = adapter.fetch_observations(
            [cfg],
            {sid: _utc(2024, 1, 1)},
        )

        assert len(results) == 1
        assert results[0].station_id == sid
        assert results[0].value == 10.0

    def test_simulated_time_cutoff(self, tmp_path: Path) -> None:
        sid = StationId(uuid.uuid4())
        cfg = _make_station_config("2004", station_id=sid)
        fixture = tmp_path / "obs.parquet"

        past = datetime(2024, 3, 1, 0, 0, tzinfo=UTC)
        future = datetime(2024, 6, 1, 0, 0, tzinfo=UTC)

        _write_fixture(
            fixture,
            [
                _row("2004", past, value=10.0),
                _row("2004", future, value=99.0),
            ],
        )

        sim = ensure_utc(datetime(2024, 4, 1, tzinfo=UTC))
        adapter = ReplayStationAdapter(fixture, lambda: sim)
        results = adapter.fetch_observations(
            [cfg],
            {sid: _utc(2024, 1, 1)},
        )

        assert len(results) == 1
        assert results[0].value == 10.0

    def test_empty_fixture(self, tmp_path: Path) -> None:
        cfg = _make_station_config("2004")
        fixture = tmp_path / "obs.parquet"
        _write_fixture(fixture, [])

        adapter = ReplayStationAdapter(
            fixture,
            lambda: _utc(2024, 6, 1),
        )
        results = adapter.fetch_observations(
            [cfg],
            {cfg.id: _utc(2024, 1, 1)},
        )

        assert results == []

    def test_observation_source_roundtrip(
        self,
        tmp_path: Path,
    ) -> None:
        sid = StationId(uuid.uuid4())
        cfg = _make_station_config("2004", station_id=sid)
        fixture = tmp_path / "obs.parquet"

        t1 = datetime(2024, 3, 1, 0, 0, tzinfo=UTC)
        _write_fixture(
            fixture,
            [
                _row("2004", t1, param="discharge", value=10.0),
                _row(
                    "2004",
                    t1,
                    param="water_level",
                    value=2.0,
                    source="rating_curve_derived",
                ),
            ],
        )

        adapter = ReplayStationAdapter(
            fixture,
            lambda: _utc(2024, 6, 1),
        )
        results = adapter.fetch_observations(
            [cfg],
            {sid: _utc(2024, 1, 1)},
        )

        sources = {r.source for r in results}
        assert sources == {
            ObservationSource.MEASURED,
            ObservationSource.RATING_CURVE_DERIVED,
        }

    def test_missing_fixture_raises_configuration_error(
        self,
        tmp_path: Path,
    ) -> None:
        missing = tmp_path / "nonexistent.parquet"
        with pytest.raises(ConfigurationError, match="not found"):
            ReplayStationAdapter(
                missing,
                lambda: _utc(2024, 1, 1),
            )

    def test_unknown_source_raises_adapter_error(
        self,
        tmp_path: Path,
    ) -> None:
        sid = StationId(uuid.uuid4())
        cfg = _make_station_config("2004", station_id=sid)
        fixture = tmp_path / "obs.parquet"

        t1 = datetime(2024, 3, 1, 0, 0, tzinfo=UTC)
        _write_fixture(
            fixture,
            [
                _row("2004", t1, source="bogus_source"),
            ],
        )

        adapter = ReplayStationAdapter(
            fixture,
            lambda: _utc(2024, 6, 1),
        )
        with pytest.raises(
            AdapterError,
            match="Unknown ObservationSource",
        ):
            adapter.fetch_observations(
                [cfg],
                {sid: _utc(2024, 1, 1)},
            )

    def test_protocol_conformance(self, tmp_path: Path) -> None:
        fixture = tmp_path / "obs.parquet"
        _write_fixture(fixture, [])

        adapter = ReplayStationAdapter(
            fixture,
            lambda: _utc(2024, 1, 1),
        )
        assert isinstance(adapter, StationDataSource)
