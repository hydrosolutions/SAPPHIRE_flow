from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from sapphire_flow.services.operational_inputs import (
    assemble_station_operational_inputs,
)
from sapphire_flow.types.enums import QcStatus, WarmUpSource
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
        def read(
            self,
            station_id: object,
            model_id: object,
            *,
            start: datetime,
            end: datetime,
            at: datetime,
        ) -> tuple[RatingDerivedDischarge, ...]:
            windows.append((start, end))
            return tuple(row for row in rows if start <= row.timestamp < end)

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
