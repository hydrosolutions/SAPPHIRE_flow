from __future__ import annotations

from dataclasses import asdict, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pytest

from sapphire_flow.config.dhm import DhmBinding, DhmConfig
from sapphire_flow.config.provisional_rating import ProvisionalRatingConfig
from sapphire_flow.services.rating_derived import derive_rating_sample
from sapphire_flow.services.rating_inputs import NativeRatingInputs
from sapphire_flow.types.enums import ForecastDataUse
from sapphire_flow.types.rating_derived import NativeFeedCustody
from sapphire_flow.types.rating_reference import canonical_content, curve_snapshot
from tests.unit.adapters.test_recap_operational import ISSUE
from tests.unit.flows.test_run_forecast_cycle import (
    _block_real_httpx_network,  # noqa: F401
)
from tests.unit.services.test_provisional_discharge import inputs

if TYPE_CHECKING:
    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ModelId
    from sapphire_flow.types.model import GroupModelInputs
    from sapphire_flow.types.rating_derived import RatingDerivedDischarge
    from sapphire_flow.types.station import StationConfig


def rating_fixture(
    station: StationConfig, mid: ModelId, lookback_days: int
) -> tuple[NativeRatingInputs, tuple[ProvisionalRatingConfig, ...]]:
    observation, curve, _, reference = inputs()
    curve = replace(curve, station_id=station.id)
    reference = replace(
        reference,
        tenant_id=station.tenant_id,
        station_id=station.id,
        curve=curve_snapshot(curve),
    )
    start = ISSUE - timedelta(days=lookback_days)
    feed = NativeFeedCustody(
        tenant_id=station.tenant_id,
        station_id=station.id,
        endpoint=reference.endpoint,
        api_station_id=reference.api_station_id,
        evidence_reference="offline fixture custody",
        start=start,
        end=ISSUE,
    )
    sample = derive_rating_sample(
        replace(
            observation,
            station_id=station.id,
            timestamp=ISSUE - timedelta(days=1) + timedelta(hours=1),
        ),
        curve,
        file_id=uuid4(),
        file_sha256="a" * 64,
        delivered_on="2026-09-08",
        feed=feed,
        reference=reference,
        at=ISSUE,
    )
    opt_in = ProvisionalRatingConfig(
        tenant_id=station.tenant_id,
        station_id=station.id,
        station_code=station.code,
        model_ids=(str(mid),),
        reference_content=canonical_content(asdict(reference)),
        reconciliation_days=lookback_days,
        custody_start=start,
        custody_end=ISSUE,
        custody_reference=feed.evidence_reference,
    )
    dhm = DhmConfig(
        endpoint=reference.endpoint,
        bindings=(
            DhmBinding(
                network="dhm",
                station_code=station.code,
                api_station_id=reference.api_station_id,
                level_reference="gauge_zero",
            ),
        ),
        max_requests_per_batch=1,
        max_bytes_per_batch=1000,
        max_batch_seconds=30.0,
    )

    class Reader:
        def read_current(
            self,
            binding: object,
            config: object,
            *,
            start: UtcDatetime,
            end: UtcDatetime,
            at: UtcDatetime,
        ) -> tuple[RatingDerivedDischarge, ...]:
            return (sample,) if start <= sample.timestamp < end else ()

    return NativeRatingInputs(
        reader=Reader(), bindings=(opt_in.to_domain(),), dhm=dhm
    ), (opt_in,)


def test_selected_native_provisional_b2_preserves_rating_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sapphire_flow.services import forecast_combination
    from tests.unit.flows.test_selected_weather_cycle import _run_selected_cycle

    def forbidden_combination(*args: object, **kwargs: object) -> None:
        pytest.fail("provisional native output reached STANDARD combination")

    monkeypatch.setattr(
        forecast_combination, "build_combined_forecasts", forbidden_combination
    )
    forecasts = _run_selected_cycle(
        monkeypatch,
        group_run=True,
        capture_fault="none",
        data_use=ForecastDataUse.PROVISIONAL_RATING,
    )
    assert forecasts is not None and len(forecasts) == 1
    forecast = forecasts[0]
    assert forecast.data_use is ForecastDataUse.PROVISIONAL_RATING
    assert (
        forecast.input_lineage is not None
        and forecast.input_lineage.rating_input is not None
    )
    assert forecast.weather_evidence_version == 1


@pytest.mark.parametrize("fault", ["missing", "hash", "changed_fact", "oversize"])
def test_provisional_selected_capture_failure_is_failed(
    monkeypatch: pytest.MonkeyPatch,
    fault: Literal["missing", "hash", "changed_fact", "oversize"],
) -> None:
    from tests.unit.flows.test_selected_weather_cycle import _run_selected_cycle

    _run_selected_cycle(
        monkeypatch,
        group_run=True,
        capture_fault=fault,
        data_use=ForecastDataUse.PROVISIONAL_RATING,
    )


@pytest.mark.parametrize("reject_ordinary", [False, True])
def test_mixed_rating_and_missing_discharge_outputs_use_matching_stores(
    monkeypatch: pytest.MonkeyPatch, reject_ordinary: bool
) -> None:
    from sapphire_flow.types.domain import ForecastQcRuleParams, ForecastQcRuleSet
    from sapphire_flow.types.enums import AlertEligibility, SpatialRepresentation
    from tests.fakes.fake_models import FakeGroupForecastModel
    from tests.fakes.fake_stores import FakeForecastStore
    from tests.unit.flows.test_run_forecast_cycle_rejected_capture import _StubStore
    from tests.unit.flows.test_selected_weather_cycle import _run_selected_cycle

    class TolerantGroupModel(FakeGroupForecastModel):
        def assess_group_inputs(self, inputs: GroupModelInputs) -> tuple:
            return ()

    model = TolerantGroupModel()
    model.alert_eligibility = AlertEligibility.SKILL_FORECAST
    model.data_requirements = replace(
        model.data_requirements,
        supported_time_steps=frozenset({timedelta(days=1)}),
        required_past_targets=frozenset({"discharge"}),
        lookback_steps=2,
        forecast_horizon_steps=10,
        past_dynamic_features=frozenset({"precipitation", "temperature"}),
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
        static_features=frozenset(),
        spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
    )
    ordinary = FakeForecastStore()
    ordinary_rejected = _StubStore()
    native_rejected = _StubStore()
    native_rejected.data_use = ForecastDataUse.PROVISIONAL_RATING
    original_read = NativeRatingInputs.read_window
    missing_sid = None

    def read_window(
        self: NativeRatingInputs, sid: object, *args: object, **kwargs: object
    ) -> object:
        result = original_read(self, sid, *args, **kwargs)
        return (
            replace(result, records=(), source_count=0)
            if sid == missing_sid
            else result
        )

    monkeypatch.setattr(NativeRatingInputs, "read_window", read_window)

    def configure(
        stores: dict, groups: object, config: DeploymentConfig, mid: ModelId
    ) -> dict:
        nonlocal missing_sid
        stations = stores["station_store"].fetch_all_stations()
        stations = [
            replace(station, code=f"fixture-{index}")
            for index, station in enumerate(stations)
        ]
        for station in stations:
            stores["station_store"].update_station(station)
        missing_sid = stations[-1].id
        fixtures = [rating_fixture(station, mid, 2) for station in stations]
        native = fixtures[0][0]
        native = replace(
            native,
            bindings=tuple(
                binding for item, _ in fixtures for binding in item.bindings
            ),
            dhm=replace(
                native.dhm,
                bindings=tuple(
                    binding for item, _ in fixtures for binding in item.dhm.bindings
                ),
            ),
        )
        options = {
            "config": config.model_copy(
                update={
                    "provisional_ratings": tuple(
                        opt for _, opts in fixtures for opt in opts
                    )
                }
            ),
            "rating_inputs": native,
            "standard_forecast_store": ordinary,
            "standard_rejected_forecast_store": ordinary_rejected,
            "rejected_forecast_store": native_rejected,
        }
        if reject_ordinary:
            # Reject every output to exercise both rejection stores in one cycle.
            options["qc_rules"] = ForecastQcRuleSet(
                version="reject",
                rules=(
                    ForecastQcRuleParams(
                        rule_id="negative_value",
                        rule_version="1",
                        parameter="discharge",
                        time_step=timedelta(days=1),
                        thresholds={"value_min": 1e9},
                    ),
                ),
            )
        return options

    def verify(result: object, stores: dict) -> None:
        if reject_ordinary:
            assert result.forecasts_stored == 0
            assert len(ordinary_rejected.calls[0]) == 1
            assert (
                ordinary_rejected.calls[0][0].payload.data_use
                is ForecastDataUse.STANDARD
            )
            assert len(native_rejected.calls[0]) == 1
        else:
            assert result.forecasts_stored == 2
            forecasts = ordinary.fetch_forecasts_for_cycle(ISSUE)
            assert len(forecasts) == 1
            assert forecasts[0].station_id == missing_sid
            assert forecasts[0].input_lineage is None
            assert forecasts[0].weather_evidence_version == 1
            assert len(stores["forecast_store"].fetch_forecasts_for_cycle(ISSUE)) == 1

    _run_selected_cycle(
        monkeypatch,
        group_run=True,
        capture_fault="none",
        station_count=2,
        model_override=model,
        omitted_observation_days=frozenset({0, 1}),
        data_use=ForecastDataUse.PROVISIONAL_RATING,
        configure_cycle=configure,
        verify_cycle=verify,
    )
