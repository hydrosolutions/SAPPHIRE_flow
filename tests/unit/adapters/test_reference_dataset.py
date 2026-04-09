from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from sapphire_flow.adapters.replay.station import ReplayStationAdapter
from sapphire_flow.tools.record_fixtures import parse_stations_toml
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ObservationSource
from sapphire_flow.types.observation import RawObservation

FIXTURE_DIR = Path("tests/fixtures/reference")
PARQUET_PATH = FIXTURE_DIR / "bafu_observations.parquet"
STATIONS_PATH = FIXTURE_DIR / "stations.toml"


class TestReferenceDataset:
    def test_reference_parquet_loads_via_replay_adapter(self) -> None:
        """Reference dataset is valid and loads via ReplayStationAdapter."""
        if not PARQUET_PATH.exists():
            pytest.skip("Reference dataset not yet recorded")

        station_configs = parse_stations_toml(STATIONS_PATH)

        far_future = ensure_utc(datetime(2099, 1, 1, tzinfo=UTC))
        adapter = ReplayStationAdapter(
            fixture_path=PARQUET_PATH,
            simulated_time=lambda: far_future,
        )

        epoch = ensure_utc(datetime(2000, 1, 1, tzinfo=UTC))
        since = {cfg.id: epoch for cfg in station_configs}

        observations = adapter.fetch_observations(station_configs, since)

        assert len(observations) > 0
        for obs in observations:
            assert isinstance(obs, RawObservation)
            assert isinstance(obs.source, ObservationSource)
            assert obs.value is not None

    def test_reference_parquet_schema(self) -> None:
        """Reference Parquet has expected columns."""
        if not PARQUET_PATH.exists():
            pytest.skip("Reference dataset not yet recorded")

        df = pl.read_parquet(PARQUET_PATH)
        assert set(df.columns) == {
            "station_code",
            "timestamp",
            "parameter",
            "value",
            "source",
        }
        assert df["station_code"].dtype == pl.Utf8
        assert df["timestamp"].dtype == pl.Datetime("us", "UTC")
        assert df["value"].dtype == pl.Float64

    def test_reference_parquet_size_bound(self) -> None:
        """Reference Parquet < 500 KB."""
        if not PARQUET_PATH.exists():
            pytest.skip("Reference dataset not yet recorded")
        assert PARQUET_PATH.stat().st_size < 500_000
