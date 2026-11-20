from dataclasses import replace
from datetime import timedelta
from typing import Literal

import pytest

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.operational_weather import (
    OperationalWeatherSample,
    OperationalWeatherSource,
    WeatherProductIdentity,
    daily_weather_value,
)
from sapphire_flow.types.recap_intervals import (
    RADIATION_CONTRACT_REVISION,
    GatewayIntervalContract,
    GatewayIntervalQualification,
    qualified_interval_duration,
    radiation_mean_flux,
)
from sapphire_flow.types.time_grid import TimeGrid
from tests.fakes.operational_weather import ISSUE

QUALIFIED = GatewayIntervalQualification(
    contract=GatewayIntervalContract.NORMALIZED_START_V1,
    revision="synthetic-normalized-fixture-v1",
)


class TestRadiationMeanFlux:
    @pytest.mark.parametrize(
        ("source", "lead", "energy"),
        [
            ("era5_land", 0, 360000.0),
            ("ifs", 0, 1080000.0),
            ("ifs", 144, 2160000.0),
            ("ifs", 354, 2160000.0),
        ],
    )
    def test_native_interval_conversion(
        self, source: Literal["era5_land", "ifs"], lead: int, energy: float
    ) -> None:
        assert (
            radiation_mean_flux(
                energy,
                start=ensure_utc(ISSUE + timedelta(hours=lead)),
                source_run=ISSUE,
                source=source,
                qualification=QUALIFIED,
            )
            == 100.0
        )

    def test_signed_thermal_is_not_clipped(self) -> None:
        assert (
            radiation_mean_flux(
                -360000.0,
                start=ISSUE,
                source_run=ISSUE,
                source="era5_land",
                qualification=QUALIFIED,
            )
            == -100.0
        )

    def test_unqualified_refused(self) -> None:
        with pytest.raises(ValueError, match="requires qualified"):
            radiation_mean_flux(
                360000.0,
                start=ISSUE,
                source_run=ISSUE,
                source="era5_land",
                qualification=GatewayIntervalQualification(),
            )

    @pytest.mark.parametrize("energy", [float("nan"), float("inf"), True])
    def test_nonfinite_and_boolean_refused(self, energy: float | bool) -> None:
        with pytest.raises(ValueError, match="must be finite"):
            radiation_mean_flux(
                energy,
                start=ISSUE,
                source_run=ISSUE,
                source="era5_land",
                qualification=QUALIFIED,
            )

    @pytest.mark.parametrize("lead", [-3, 1, 145, 147, 360])
    def test_invalid_ifs_start_does_not_borrow_neighbor_duration(
        self, lead: int
    ) -> None:
        with pytest.raises(ValueError, match="qualified source schedule"):
            qualified_interval_duration(
                ensure_utc(ISSUE + timedelta(hours=lead)), ISSUE, "ifs"
            )

    def test_era5_original_day_required(self) -> None:
        with pytest.raises(ValueError, match="source UTC day"):
            qualified_interval_duration(
                ensure_utc(ISSUE + timedelta(days=1)), ISSUE, "era5_land"
            )


class TestRadiationDailyReduction:
    def test_mixed_intervals_conserve_energy_and_missing_remains_missing(self) -> None:
        start = ensure_utc(ISSUE + timedelta(hours=138))
        grid = TimeGrid(step=timedelta(days=1), phase=timedelta(hours=18))
        samples = tuple(
            OperationalWeatherSample(
                parameter="thermal_net_radiation",
                source=OperationalWeatherSource.IFS,
                source_run=ISSUE,
                valid_time=ensure_utc(ISSUE + timedelta(hours=lead)),
                interval_end=ensure_utc(ISSUE + timedelta(hours=lead + step)),
                value=value,
                product=WeatherProductIdentity.CONTROL,
                contract_revision=RADIATION_CONTRACT_REVISION,
            )
            for lead, step, value in (
                (138, 3, 100.0),
                (141, 3, 100.0),
                (144, 6, 200.0),
                (150, 6, 200.0),
                (156, 6, 200.0),
            )
        )
        assert (
            daily_weather_value(start, samples, "thermal_net_radiation", grid=grid)
            == 175.0
        )
        assert (
            daily_weather_value(start, samples[1:], "thermal_net_radiation", grid=grid)
            is None
        )
        with pytest.raises(ValueError, match="overlap or duplicate"):
            daily_weather_value(
                start, samples + samples[:1], "thermal_net_radiation", grid=grid
            )
        with pytest.raises(ValueError, match="interval"):
            replace(samples[0], interval_end=samples[1].interval_end)
