"""Plan 155 T2 (G8) — the alias projection wired into the Prefect-task
compatibility path (`flows/onboard_model.py::_validate_compatibility_task`),
not just the pure `services/caravan_statics.py` functions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sapphire_flow.flows.onboard_model import _validate_compatibility_task
from sapphire_flow.services.caravan_statics import CARAVAN_PREFIX
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ArtifactScope, SpatialRepresentation
from sapphire_flow.types.ids import BasinId, ModelId, StationId
from sapphire_flow.types.model import ModelDataRequirements
from tests.conftest import (
    make_deployment_config,
    make_station_config,
    make_training_unit,
)
from tests.fakes.fake_models import FakeStationForecastModel
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeParameterStore,
    FakeStationGroupStore,
    FakeStationStore,
)

_EPOCH = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))


def _caravan_declaring_model() -> object:
    class _CaravanModel(FakeStationForecastModel):
        artifact_scope = ArtifactScope.STATION
        data_requirements = ModelDataRequirements(
            target_parameters=frozenset({"discharge"}),
            past_dynamic_features=frozenset({"precipitation", "temperature"}),
            future_dynamic_features=frozenset(),
            static_features=frozenset({"slope", "area"}),
            supported_time_steps=frozenset({timedelta(hours=24)}),
            lookback_steps=720,
            forecast_horizon_steps=5,
            spatial_input_type=SpatialRepresentation.POINT,
        )

    return _CaravanModel()


class TestValidateCompatibilityTaskResolvesCaravanStatics:
    def test_compatibility_reports_zero_missing_statics_for_a_swiss_station(
        self,
    ) -> None:
        """T2's red assertion, verbatim (plan 155): compatibility reports
        zero missing statics for a Swiss station, exercised through the
        REAL Prefect-task wiring (`_validate_compatibility_task`), not the
        pure alias functions in isolation."""
        station_id = StationId(uuid4())
        basin_id = BasinId(uuid4())

        station_store = FakeStationStore()
        station_store.store_station(
            make_station_config(station_id=station_id, basin_id=basin_id)
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
                    "area": 999.0,  # CAMELS-CH's own bare "area" (must not win)
                    f"{CARAVAN_PREFIX}area": 250.0,
                    f"{CARAVAN_PREFIX}slp_dg_sav": 12.5,
                },
                band_geometries=None,
                created_at=_EPOCH,
                network="bafu",
            )
        )

        unit = make_training_unit(model_id=ModelId("test_model"), station_id=station_id)

        report = _validate_compatibility_task.fn(
            model_id=ModelId("test_model"),
            model=_caravan_declaring_model(),
            unit=unit,
            station_store=station_store,
            group_store=FakeStationGroupStore(),
            basin_store=basin_store,
            parameter_store=FakeParameterStore(),
            deployment_config=make_deployment_config(),
        )

        assert report.missing_static_features == frozenset()
