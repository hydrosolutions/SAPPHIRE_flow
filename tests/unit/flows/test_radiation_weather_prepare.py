from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from sapphire_flow.adapters.recap_gateway import RecapGatewayForecastAdapter
from sapphire_flow.adapters.recap_operational import operational_binding_fingerprint
from sapphire_flow.flows.operational_weather_prepare import (
    PreparedWeatherRefresh,
    prepare_weather_refresh,
)
from sapphire_flow.types.enums import (
    ModelAssignmentStatus,
    SpatialRepresentation,
    WeatherSourceRole,
)
from sapphire_flow.types.ids import ModelId
from sapphire_flow.types.operational_weather import WEATHER_PARAMETERS
from sapphire_flow.types.recap_intervals import (
    GatewayIntervalContract,
    GatewayIntervalQualification,
)
from sapphire_flow.types.station import ModelAssignment
from tests.fakes.fake_models import FakeStationForecastModel
from tests.unit.adapters.test_recap_operational import (
    ISSUE,
    PolygonResolver,
    make_binding,
)


def prepare(qualification: GatewayIntervalQualification) -> PreparedWeatherRefresh:
    source = make_binding()
    legacy = FakeStationForecastModel()
    legacy.data_requirements = replace(
        legacy.data_requirements,
        spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
        past_dynamic_features=frozenset({"precipitation", "temperature"}),
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
    )
    radiation = FakeStationForecastModel()
    radiation.data_requirements = replace(
        legacy.data_requirements,
        past_dynamic_features=frozenset(WEATHER_PARAMETERS),
        future_dynamic_features=frozenset(WEATHER_PARAMETERS),
    )
    models = {ModelId("legacy"): legacy, ModelId("radiation"): radiation}
    assignments = [
        ModelAssignment(
            station_id=source.station_id,
            model_id=mid,
            time_step=timedelta(days=1),
            status=ModelAssignmentStatus.ACTIVE,
            priority=i,
            created_at=ISSUE,
        )
        for i, mid in enumerate(models)
    ]
    resolver = PolygonResolver()
    adapter = RecapGatewayForecastAdapter(
        client=SimpleNamespace(),
        resolver=resolver,
        interval_qualification=qualification,
    )
    return prepare_weather_refresh(
        active_model_assignments={source.station_id: assignments},
        models=models,
        forecast_bindings={
            source.station_id: replace(
                source, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST
            )
        },
        station_store=SimpleNamespace(fetch_operational_binding=lambda _: source),
        group_store=None,
        resolver=resolver,
        adapter=adapter,
        issue_time=ISSUE,
    )


class TestRadiationWeatherPrepare:
    def test_unqualified_assignment_does_not_block_legacy_sibling(self) -> None:
        prepared = prepare(GatewayIntervalQualification())
        assert {mid for _, mid in prepared.assignments} == {"legacy"}
        assert len(prepared.refusal_messages) == 1
        assert "radiation intervals are unqualified" in prepared.refusal_messages[0]
        assert prepared.specs[0].past_requirement.parameters == (
            "precipitation",
            "temperature",
        )

    @pytest.mark.parametrize(
        "contract,revision",
        [
            (GatewayIntervalContract.NORMALIZED_START_V1, "test-normalized-v1"),
            (
                GatewayIntervalContract.GATEWAY_NATIVE_START_V1,
                "gateway-docs:" + "a" * 40,
            ),
        ],
    )
    def test_union_precedes_cache_key_and_preserves_assignment_requirements(
        self,
        contract: GatewayIntervalContract,
        revision: str,
    ) -> None:
        qualification = GatewayIntervalQualification(
            contract=contract,
            revision=revision,
        )
        prepared = prepare(qualification)
        assert prepared.refusal_messages == ()
        assert len(prepared.specs) == 1
        spec = prepared.specs[0]
        assert set(spec.past_requirement.parameters) == set(WEATHER_PARAMETERS)
        assert spec.key.binding_fingerprint == operational_binding_fingerprint(
            spec.source,
            PolygonResolver().resolve(spec.source),
            parameters=WEATHER_PARAMETERS,
            interval_qualification=qualification,
        )
        assert prepared.assignments[
            (spec.source.station_id, ModelId("legacy"))
        ].past.parameters == ("precipitation", "temperature")
