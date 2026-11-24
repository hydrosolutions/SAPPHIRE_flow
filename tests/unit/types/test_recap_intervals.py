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
    qualified_ifs_horizon_hours,
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
        "revision", [None, "", " ", "corrected-v1", "gateway-docs:abc"]
    )
    def test_native_contract_requires_pinned_documentation(
        self, revision: str | None
    ) -> None:
        with pytest.raises(ValueError, match="revision"):
            GatewayIntervalQualification(
                contract=GatewayIntervalContract.GATEWAY_NATIVE_START_V1,
                revision=revision,
            )

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

    @pytest.mark.parametrize("hour", [0, 12])
    @pytest.mark.parametrize("lead", [-3, 1, 145, 147, 357, 360])
    def test_invalid_ifs_start_does_not_borrow_neighbor_duration(
        self, hour: int, lead: int
    ) -> None:
        run = ensure_utc(ISSUE + timedelta(hours=hour))
        with pytest.raises(ValueError, match="qualified source schedule"):
            qualified_interval_duration(
                ensure_utc(run + timedelta(hours=lead)), run, "ifs"
            )

    @pytest.mark.parametrize(
        ("lead", "hours"), [(69, 3), (72, 3), (141, 3), (144, 6), (258, 6), (354, 6)]
    )
    def test_noon_cycle_signed_flux_uses_the_long_schedule(
        self, lead: int, hours: int
    ) -> None:
        run = ensure_utc(ISSUE + timedelta(hours=12))
        assert (
            radiation_mean_flux(
                -100.0 * hours * 3600,
                start=ensure_utc(run + timedelta(hours=lead)),
                source_run=run,
                source="ifs",
                qualification=QUALIFIED,
            )
            == -100.0
        )

    @pytest.mark.parametrize("hour", [6, 18])
    def test_short_cycles_keep_their_qualified_limit(self, hour: int) -> None:
        run = ensure_utc(ISSUE + timedelta(hours=hour))
        assert qualified_interval_duration(
            ensure_utc(run + timedelta(hours=69)), run, "ifs"
        ) == timedelta(hours=3)
        with pytest.raises(ValueError, match="qualified source schedule"):
            qualified_interval_duration(
                ensure_utc(run + timedelta(hours=72)), run, "ifs"
            )

    def test_noon_cycle_refuses_off_hour_interval(self) -> None:
        run = ensure_utc(ISSUE + timedelta(hours=12))
        with pytest.raises(ValueError, match="off the source grid"):
            qualified_interval_duration(
                ensure_utc(run + timedelta(hours=72, minutes=1)), run, "ifs"
            )

    def test_horizon_refuses_non_cycle_source_run(self) -> None:
        with pytest.raises(ValueError, match="six-hour UTC source cycle"):
            qualified_ifs_horizon_hours(ensure_utc(ISSUE + timedelta(hours=1)))

    def test_era5_original_day_required(self) -> None:
        with pytest.raises(ValueError, match="source UTC day"):
            qualified_interval_duration(
                ensure_utc(ISSUE + timedelta(days=1)), ISSUE, "era5_land"
            )


class TestRadiationDailyReduction:
    @pytest.mark.parametrize("hour", [0, 12])
    def test_mixed_intervals_conserve_energy_and_missing_remains_missing(
        self, hour: int
    ) -> None:
        run = ensure_utc(ISSUE + timedelta(hours=hour))
        start = ensure_utc(run + timedelta(hours=138))
        grid = TimeGrid(step=timedelta(days=1), phase=timedelta(hours=start.hour))
        samples = tuple(
            OperationalWeatherSample(
                parameter="thermal_net_radiation",
                source=OperationalWeatherSource.IFS,
                source_run=run,
                valid_time=ensure_utc(run + timedelta(hours=lead)),
                interval_end=ensure_utc(run + timedelta(hours=lead + step)),
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
