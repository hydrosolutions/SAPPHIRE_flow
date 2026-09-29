from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from sapphire_flow.services.nepal_onboarding import (
    GatewayHistoryReader,
    complete_training_rows,
    count_training_samples,
    history_binding,
)
from sapphire_flow.services.training_data import resample_to_time_step
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.model import StationTrainingData
from sapphire_flow.types.nepal_onboarding import GATEWAY_HISTORY_SOURCE, TrainingWindow
from tests.conftest import make_raw_historical_forcing, make_station_config
from tests.fakes.fake_stores import FakeHistoricalForcingStore


class TestGatewayHistoryReader:
    def test_other_bound_source_cannot_fill_missing_gateway_history(self) -> None:
        station = make_station_config()
        start = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
        store = FakeHistoricalForcingStore()
        store.store_forcing(
            [
                make_raw_historical_forcing(
                    station_id=station.id,
                    source="other_source",
                    valid_time=start,
                    parameter="temperature",
                    spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                )
            ]
        )
        binding = history_binding(station.id)
        reader = GatewayHistoryReader(
            store, source_mapping={"era5_land": GATEWAY_HISTORY_SOURCE}
        )
        assert (
            reader.fetch_reanalysis(
                [binding, replace(binding, nwp_source="other_source")],
                start,
                ensure_utc(start + timedelta(days=1)),
                ["temperature"],
            )
            == []
        )


class TestCountTrainingSamples:
    def test_nepal_1986_transition_uses_existing_utc_resampling(self) -> None:
        local = ZoneInfo("Asia/Kathmandu")
        stamps = [
            ensure_utc(datetime(1985, 12, 28, tzinfo=local) + timedelta(days=i))
            for i in range(10)
        ]
        original = pl.DataFrame({"timestamp": stamps, "discharge": [1.0] * 10})
        targets = resample_to_time_step(original, timedelta(days=1))
        forcing = pl.DataFrame(
            {
                "timestamp": targets["timestamp"],
                "temperature": [2.0] * 10,
            }
        )
        data = StationTrainingData(
            past_targets=targets,
            past_dynamic=forcing,
            future_dynamic=pl.DataFrame({"timestamp": targets["timestamp"]}),
            static=None,
            time_step=timedelta(days=1),
            val_start=None,
        )
        assert (
            count_training_samples(
                complete_training_rows(data), timedelta(days=1), 2, 1
            )
            == 8
        )
        assert original["timestamp"].to_list() == stamps
        assert (stamps[-1] - stamps[0]) != timedelta(days=9)

    def test_missing_and_nonfinite_forcing_break_complete_windows(self) -> None:
        start = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
        stamps = [start + timedelta(days=i) for i in range(6)]
        data = StationTrainingData(
            past_targets=pl.DataFrame({"timestamp": stamps, "discharge": [1.0] * 6}),
            past_dynamic=pl.DataFrame(
                {
                    "timestamp": stamps,
                    "temperature": [1.0, 1.0, float("nan"), 1.0, 1.0, 1.0],
                }
            ),
            future_dynamic=pl.DataFrame(
                {"timestamp": stamps, "precipitation": [1.0, None, 1.0, 1.0, 1.0, 1.0]}
            ),
            static=None,
            time_step=timedelta(days=1),
            val_start=None,
        )
        assert (
            count_training_samples(
                complete_training_rows(data), timedelta(days=1), 2, 1
            )
            == 1
        )

    def test_gap_breaks_training_windows(self) -> None:
        start = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
        frame = pl.DataFrame(
            {"timestamp": [start + timedelta(days=i) for i in (0, 1, 2, 4, 5, 6)]}
        )
        assert count_training_samples(frame, timedelta(days=1), 2, 1) == 2

    def test_window_requires_positive_sample_count(self) -> None:
        start = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
        with pytest.raises(ValueError, match="minimum_samples"):
            TrainingWindow(
                start=start,
                end=start + timedelta(days=30),
                time_step=timedelta(days=1),
                minimum_samples=0,
            )
