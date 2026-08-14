"""Plan 155 T2 (G8) — the alias projection wired into
`assemble_station_training_data`'s compatibility gate AND static frame
build, not just the pure `services/caravan_statics.py` functions in
isolation."""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.caravan_statics import CARAVAN_PREFIX
from sapphire_flow.services.training_data import assemble_station_training_data
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    ArtifactScope,
    SpatialRepresentation,
    StaticNaming,
)
from sapphire_flow.types.ids import BasinId, StationId
from sapphire_flow.types.model import ModelDataRequirements
from tests.conftest import (
    make_observations,
    make_station_config,
)
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_models import FakeStationForecastModel
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeObservationStore,
    FakeStationStore,
)

_START = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
_END = ensure_utc(datetime(2020, 6, 1, tzinfo=UTC))
_STEP = timedelta(hours=1)


def _caravan_declaring_model() -> object:
    """Declares its statics under Caravan's OWN names -- one aliased
    ("slope" <- caravan:slp_dg_sav) and one direct ("area" <- caravan:area,
    D15's "no bare fallback" rule for one of the seven colliding names)."""

    class _CaravanModel(FakeStationForecastModel):
        artifact_scope = ArtifactScope.STATION
        static_naming = StaticNaming.CARAVAN  # Plan 155 D16
        data_requirements = ModelDataRequirements(
            target_parameters=frozenset({"discharge"}),
            past_dynamic_features=frozenset(),
            future_dynamic_features=frozenset(),
            static_features=frozenset({"slope", "area"}),
            supported_time_steps=frozenset({timedelta(hours=1), timedelta(hours=24)}),
            lookback_steps=720,
            forecast_horizon_steps=5,
            spatial_input_type=SpatialRepresentation.POINT,
        )

    return _CaravanModel()


def _basin_with_caravan_and_camels_ch_attributes(basin_id: BasinId) -> Basin:
    return Basin(
        id=basin_id,
        code="B-001",
        name="Basin B-001",
        geometry=None,
        area_km2=100.0,
        attributes={
            "area": 100.0,  # CAMELS-CH's own bare attribute (must NOT be used)
            f"{CARAVAN_PREFIX}area": 250.0,  # Caravan's, namespaced (D15)
            f"{CARAVAN_PREFIX}slp_dg_sav": 12.5,  # aliased -> "slope"
        },
        band_geometries=None,
        created_at=_START,
        network="bafu",
    )


class TestAssembleStationTrainingDataResolvesCaravanStatics:
    def test_compatibility_gate_passes_and_frame_carries_caravans_values(self) -> None:
        """T2's red assertions, verbatim (plan 155), exercised through the
        REAL training-data assembly path (not just the pure alias
        functions): the missing-static gate does not reject the station,
        and the resulting frame carries CARAVAN's value for "area", not
        CAMELS-CH's same-named bare attribute."""
        station_id = StationId(uuid4())
        basin_id = BasinId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()

        station_store.store_station(
            make_station_config(station_id=station_id, basin_id=basin_id)
        )
        obs_store.store_observations(
            make_observations(
                n=10, station_id=station_id, start=_START, rng=random.Random(1)
            )
        )
        basin_store = FakeBasinStore()
        basin_store.store_basin(_basin_with_caravan_and_camels_ch_attributes(basin_id))

        result = assemble_station_training_data(
            station_id=station_id,
            model=_caravan_declaring_model(),
            period_start=_START,
            period_end=_END,
            time_step=_STEP,
            forcing_source=FakeWeatherReanalysisSource([]),
            obs_store=obs_store,
            basin_store=basin_store,
            station_store=station_store,
        )

        assert result is not None
        assert result.static is not None
        assert result.static["area"][0] == 250.0
        assert result.static["slope"][0] == 12.5

    def test_missing_caravan_source_fails_the_gate_not_a_stale_bare_fallback(
        self,
    ) -> None:
        """Plan 155 post-implementation-review BLOCKER, locked at the REAL
        training-data path: a Caravan-declaring model whose basin carries
        ONLY the bare CAMELS-CH "area" (no `caravan:area` at all) must be
        rejected by the missing-static gate, not silently handed the bare
        CAMELS-CH value as if it were Caravan's (D15's "no bare fallback",
        inverted at the frame boundary -- `project_declared_static_
        attributes` used to `continue` and leave the stale key standing)."""
        station_id = StationId(uuid4())
        basin_id = BasinId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()

        station_store.store_station(
            make_station_config(station_id=station_id, basin_id=basin_id)
        )
        obs_store.store_observations(
            make_observations(
                n=10, station_id=station_id, start=_START, rng=random.Random(1)
            )
        )
        basin_store = FakeBasinStore()
        basin_store.store_basin(
            Basin(
                id=basin_id,
                code="B-001",
                name="Basin B-001",
                geometry=None,
                area_km2=100.0,
                attributes={
                    "area": 100.0,  # CAMELS-CH's own bare attribute only
                    f"{CARAVAN_PREFIX}slp_dg_sav": 12.5,  # "slope" IS covered
                },
                band_geometries=None,
                created_at=_START,
                network="bafu",
            )
        )

        result = assemble_station_training_data(
            station_id=station_id,
            model=_caravan_declaring_model(),
            period_start=_START,
            period_end=_END,
            time_step=_STEP,
            forcing_source=FakeWeatherReanalysisSource([]),
            obs_store=obs_store,
            basin_store=basin_store,
            station_store=station_store,
        )

        assert result is None


class TestTrainingCollisionNamesTheStation:
    """Review MINOR: `services/training_data.py` forwards `station_code`
    into `available_declared_static_keys` so a T2 collision identifies the
    station. Without a test at THIS boundary, reverting that one argument
    would leave every other test green while the operator loses the only
    field that says WHICH station is inconsistent -- T2 requires the error
    to name "station, alias, canonical name and both values".
    """

    def test_a_collision_reports_the_station_code_not_unknown(self) -> None:
        station_id = StationId(uuid4())
        basin_id = BasinId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()

        station = make_station_config(station_id=station_id, basin_id=basin_id)
        station_store.store_station(station)
        obs_store.store_observations(
            make_observations(
                n=10, station_id=station_id, start=_START, rng=random.Random(1)
            )
        )

        # An internally inconsistent package: BOTH the raw HydroATLAS code
        # and the canonical Caravan name for `slope`, with DIFFERING finite
        # values -- exactly T2's collision case.
        basin = _basin_with_caravan_and_camels_ch_attributes(basin_id)
        attributes = dict(basin.attributes or {})
        attributes["caravan:slp_dg_sav"] = 12.5
        attributes["caravan:slope"] = 99.0
        basin_store = FakeBasinStore()
        basin_store.store_basin(replace(basin, attributes=attributes))

        with pytest.raises(ConfigurationError) as excinfo:
            assemble_station_training_data(
                station_id=station_id,
                model=_caravan_declaring_model(),
                period_start=_START,
                period_end=_END,
                time_step=_STEP,
                forcing_source=FakeWeatherReanalysisSource([]),
                obs_store=obs_store,
                basin_store=basin_store,
                station_store=station_store,
            )

        message = str(excinfo.value)
        assert "<unknown>" not in message
        assert station.code in message
