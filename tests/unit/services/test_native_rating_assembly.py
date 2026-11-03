from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from sapphire_flow.services.operational_inputs import (
    assemble_station_operational_inputs,
)
from sapphire_flow.types.enums import QcStatus, WarmUpSource
from sapphire_flow.types.rating_reconciliation import RatingWindowRead
from tests.conftest import make_observation
from tests.unit.services.test_operational_inputs import (
    _MODEL_ID,
    _NWP_SOURCE,
    _make_model,
    _make_nwp_records,
    _make_stores_and_sources,
    _seed_forcing,
)
from tests.unit.services.test_rating_inputs import NOW, samples


def test_native_assembler_joins_daily_frame_and_excludes_current_day() -> None:
    rows = samples()
    sid = rows[0].station_id
    station, basin, obs, weather, state, forcing = _make_stores_and_sources(
        sid, with_obs=False, with_nwp=False
    )
    issue = NOW + timedelta(days=1, hours=6)
    cycle = NOW + timedelta(days=1)
    obs.store_observations(
        [
            replace(
                make_observation(
                    station_id=sid,
                    parameter="discharge",
                    timestamp=NOW - timedelta(days=1),
                    value=99.0,
                ),
                id=uuid4(),
            ),
            replace(
                make_observation(
                    station_id=sid,
                    parameter="discharge",
                    timestamp=NOW,
                    value=777.0,
                    qc_status=QcStatus.QC_FAILED,
                ),
                id=uuid4(),
            ),
        ]
    )
    from sapphire_flow.types.enums import (
        SpatialRepresentation,
        WeatherSourceRole,
        WeatherSourceStatus,
    )
    from sapphire_flow.types.station import StationWeatherSource

    station.store_weather_source(
        StationWeatherSource(
            station_id=sid,
            nwp_source="fixture-reanalysis",
            extraction_type=SpatialRepresentation.POINT,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.REANALYSIS,
        )
    )
    _seed_forcing(forcing, sid, NOW - timedelta(days=1), 2)
    weather.store_weather_forecasts(_make_nwp_records(sid, cycle, issue, 48))
    model = _make_model()
    model.data_requirements = replace(
        model.data_requirements,
        lookback_steps=2,
        supported_time_steps=frozenset({timedelta(days=1)}),
    )
    windows = []

    class Reader:
        def read_window(
            self,
            station_id: object,
            model_id: object,
            *,
            start: datetime,
            end: datetime,
            at: datetime,
        ) -> RatingWindowRead:
            windows.append((start, end))
            selected = tuple(row for row in rows if start <= row.timestamp < end)
            return RatingWindowRead(
                start=start,
                end=end,
                records=selected,
                held=(),
                source_count=len(selected),
            )

    result = assemble_station_operational_inputs(
        sid,
        model,
        _MODEL_ID,
        issue,
        cycle,
        _NWP_SOURCE,
        forcing,
        weather,
        obs,
        station,
        basin,
        state,
        lambda: issue,
        1,
        timedelta(days=1),
        rating_inputs=Reader(),
    )
    assert result is not None
    inputs, metadata = result
    assert windows == [(NOW - timedelta(days=1), NOW + timedelta(days=1))]
    assert inputs.data.past_targets["discharge"].to_list() == [99.0, 20.0]
    assert metadata.warm_up_source is WarmUpSource.COLD_START
    assert metadata.warm_up_state_age_hours is None
    assert metadata.input_lineage is not None
    lineage = metadata.input_lineage.rating_input
    assert lineage is not None
    assert lineage.days[0].measured_rejected == 1
    assert lineage.days[0].rejected == 1
    assert lineage.weather_record_ids
    assert lineage.forcing_versions


if TYPE_CHECKING:
    from sapphire_flow.types.rating_derived import RatingDerivedDischarge


@pytest.mark.parametrize("support", ["complete", "missing"])
def test_group_30_day_inputs_need_complete_real_tail_points(support: str) -> None:
    import json

    from sapphire_flow.config.dhm import DhmBinding, DhmConfig
    from sapphire_flow.config.provisional_rating import NativeRatingBinding
    from sapphire_flow.services.rating_inputs import NativeRatingInputs
    from sapphire_flow.services.run_group_forecast import (
        assemble_group_operational_inputs,
    )
    from sapphire_flow.store.rating_reference_store import parse_reference_evidence
    from sapphire_flow.types.enums import (
        ProvisionalIfsTailMode,
        SpatialRepresentation,
        WeatherSourceRole,
        WeatherSourceStatus,
    )
    from sapphire_flow.types.ids import StationGroupId
    from sapphire_flow.types.rating_reference import canonical_content
    from sapphire_flow.types.station import StationGroup, StationWeatherSource
    from sapphire_flow.types.weather import WeatherForecastRecord
    from tests.conftest import make_station_config
    from tests.fakes.fake_models import FakeGroupForecastModel
    from tests.fakes.fake_stores import FakeStationStore

    rows = samples()
    sid = rows[0].station_id
    _, basin, obs, weather, state, forcing = _make_stores_and_sources(
        sid, with_obs=False, with_nwp=False
    )
    station = FakeStationStore()
    station.store_station(make_station_config(station_id=sid))
    for source, role in (
        ("fixture-reanalysis", WeatherSourceRole.REANALYSIS),
        ("ifs_ecmwf", WeatherSourceRole.FORECAST),
    ):
        station.store_weather_source(
            StationWeatherSource(
                station_id=sid,
                nwp_source=source,
                extraction_type=SpatialRepresentation.POINT,
                status=WeatherSourceStatus.ACTIVE,
                role=role,
            )
        )
    issue = NOW + timedelta(days=1)
    start = issue - timedelta(days=30)
    obs.store_observations(
        [
            replace(
                make_observation(
                    station_id=sid,
                    parameter="discharge",
                    timestamp=start + timedelta(days=i),
                    value=99.0,
                ),
                id=uuid4(),
            )
            for i in range(29)
        ]
    )
    _seed_forcing(forcing, sid, start, 25)
    archive_cycle = issue - timedelta(days=5)
    records = [
        WeatherForecastRecord(
            id=uuid4(),
            station_id=sid,
            nwp_source="ifs_ecmwf",
            cycle_time=archive_cycle,
            valid_time=archive_cycle + timedelta(hours=hour),
            parameter=parameter,
            spatial_type=SpatialRepresentation.POINT,
            band_id=None,
            member_id=0,
            value=2.0,
            created_at=archive_cycle,
        )
        for hour in range(0, 144, 3)
        for parameter in ("precipitation", "temperature")
    ]
    if support == "missing":
        records = records[1:]
    weather.store_weather_forecasts(records)
    reference = parse_reference_evidence(
        canonical_content(json.loads(rows[0].content)["reference"])
    )
    binding = NativeRatingBinding(
        tenant_id=reference.tenant_id,
        station_id=sid,
        station_code="450",
        model_ids=frozenset({_MODEL_ID}),
        reference=reference,
        custody_start=start,
        custody_end=issue,
        custody_reference="fixture",
        reconciliation_days=30,
    )
    dhm = DhmConfig(
        endpoint=reference.endpoint,
        bindings=(
            DhmBinding(
                network="dhm",
                station_code="450",
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
            dhm: object,
            *,
            start: datetime,
            end: datetime,
            at: datetime,
        ) -> tuple[RatingDerivedDischarge, ...]:
            return tuple(row for row in rows if start <= row.timestamp < end)

    native = NativeRatingInputs(reader=Reader(), bindings=(binding,), dhm=dhm)
    model = FakeGroupForecastModel()
    model.data_requirements = replace(
        model.data_requirements,
        lookback_steps=30,
        forecast_horizon_steps=1,
        supported_time_steps=frozenset({timedelta(days=1)}),
        static_features=frozenset(),
        past_dynamic_features=frozenset({"precipitation", "temperature"}),
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
    )
    group = StationGroup(
        id=StationGroupId(uuid4()),
        name="IFS fixture",
        station_ids=frozenset({sid}),
        created_at=issue,
    )
    result = assemble_group_operational_inputs(
        group=group,
        model=model,
        model_id=_MODEL_ID,
        issue_time=issue,
        cycle_time=archive_cycle,
        nwp_source_by_station={sid: "ifs_ecmwf"},
        forcing_source=forcing,
        weather_forecast_store=weather,
        obs_store=obs,
        station_store=station,
        basin_store=basin,
        model_state_store=state,
        clock=lambda: issue,
        forecast_horizon_steps=1,
        time_step=timedelta(days=1),
        rating_inputs=native,
        provisional_ifs_tail_mode_by_station={sid: ProvisionalIfsTailMode.CONTROL_3H},
    )
    assert result is not None
    inputs, metadata = result
    assert inputs.past_dynamic.height == 30
    assert inputs.past_dynamic["precipitation"].null_count() == (
        1 if support == "missing" else 0
    )
    assert inputs.past_dynamic["temperature"].null_count() == 0
    lineage = metadata[sid].input_lineage
    assert lineage is not None and lineage.rating_input is not None
    ids = lineage.rating_input.weather_record_ids
    assert len(ids) == len(set(ids)) == len(records)
