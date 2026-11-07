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
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ModelId
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
