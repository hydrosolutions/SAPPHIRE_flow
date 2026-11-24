from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sapphire_flow.adapters.recap_gateway import RecapGatewayForecastAdapter
from sapphire_flow.adapters.recap_operational import operational_binding_fingerprint
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.flows.operational_weather_prepare import (
    PreparedWeatherRefresh,
    prepare_weather_refresh,
)
from sapphire_flow.types.datetime import UtcDatetime
from sapphire_flow.types.enums import (
    ModelAssignmentStatus,
    SpatialRepresentation,
    WeatherSourceRole,
)
from sapphire_flow.types.ids import ModelId, StationGroupId
from sapphire_flow.types.operational_weather import WEATHER_PARAMETERS
from sapphire_flow.types.operational_weather_v2 import (
    FutureWeatherRequestV2,
    PastWeatherRequirementV2,
)
from sapphire_flow.types.recap_intervals import (
    GatewayIntervalContract,
    GatewayIntervalQualification,
)
from sapphire_flow.types.station import (
    GroupModelAssignment,
    ModelAssignment,
    StationGroup,
)
from sapphire_flow.types.time_grid import ModelTimeGrid, PeriodLabel, TimeGrid
from tests.fakes.fake_models import FakeGroupForecastModel, FakeStationForecastModel
from tests.fakes.fake_stores import FakeStationGroupStore
from tests.unit.adapters.test_recap_operational import (
    ISSUE,
    PolygonResolver,
    make_binding,
)


def prepare(
    qualification: GatewayIntervalQualification,
    *,
    model_grid: ModelTimeGrid | None = None,
    mixed: bool = False,
    explicitly_selected: bool = True,
    issue_time: UtcDatetime = ISSUE,
) -> PreparedWeatherRefresh:
    source = make_binding()
    legacy = (
        FakeStationForecastModel() if model_grid is None else FakeGroupForecastModel()
    )
    legacy.data_requirements = replace(
        legacy.data_requirements,
        spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
        past_dynamic_features=frozenset({"precipitation", "temperature"}),
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
        time_grid=model_grid,
    )
    radiation = (
        FakeStationForecastModel() if model_grid is None else FakeGroupForecastModel()
    )
    radiation.data_requirements = replace(
        legacy.data_requirements,
        past_dynamic_features=frozenset(WEATHER_PARAMETERS),
        future_dynamic_features=frozenset(WEATHER_PARAMETERS),
        time_grid=None if mixed else model_grid,
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
    groups = None
    pairs = None
    if model_grid is not None:
        groups = FakeStationGroupStore()
        gid = StationGroupId(uuid4())
        groups.store_group(
            StationGroup(
                id=gid,
                name="grid",
                station_ids=frozenset({source.station_id}),
                created_at=ISSUE,
            )
        )
        for mid in models:
            groups.store_group_model_assignment(
                GroupModelAssignment(
                    group_id=gid,
                    model_id=mid,
                    time_step=timedelta(days=1),
                    status=ModelAssignmentStatus.ACTIVE,
                    priority=1,
                    created_at=ISSUE,
                )
            )
        if explicitly_selected:
            pairs = frozenset((source.station_id, mid) for mid in models)
    return prepare_weather_refresh(
        active_model_assignments={source.station_id: assignments},
        models=models,
        forecast_bindings={
            source.station_id: replace(
                source, nwp_source="ifs_ecmwf", role=WeatherSourceRole.FORECAST
            )
        },
        station_store=SimpleNamespace(fetch_operational_binding=lambda _: source),
        group_store=groups,
        resolver=resolver,
        adapter=adapter,
        issue_time=issue_time,
        eligible_group_pairs=pairs,
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


_GRID = ModelTimeGrid(
    grid=TimeGrid(step=timedelta(days=1), phase=timedelta(hours=18)),
    label=PeriodLabel.START,
)
_QUALIFIED = GatewayIntervalQualification(
    contract=GatewayIntervalContract.NORMALIZED_START_V1,
    revision="test-normalized-v1",
)


@pytest.mark.parametrize("hour", [0, 17, 18, 23])
def test_declared_grid_uses_floor_for_past_future_boundary(hour: int) -> None:
    issue = ISSUE + timedelta(hours=hour)
    prepared = prepare(_QUALIFIED, model_grid=_GRID, issue_time=issue)
    start = _GRID.grid.floor(issue)
    assert prepared.grid == _GRID.grid
    assert len(prepared.assignments) == 2
    for assignment in prepared.assignments.values():
        assert isinstance(assignment.past, PastWeatherRequirementV2)
        assert isinstance(assignment.future, FutureWeatherRequestV2)
        assert assignment.past.end == start
        assert assignment.future.first_period_start == start
        assert assignment.future.issue_time == issue
        assert assignment.future.eligibility_cutoff == issue
        assert all(
            req.valid_times[0] == start for req in assignment.future.requirements
        )
    assert prepared.specs[0].retained_future_bindings == frozenset()
    assert prepared.specs[0].retained_past_requirements == ()
    from sapphire_flow.adapters.recap_operational import (
        future_binding_fingerprint_v2,
        operational_binding_fingerprint_v2,
    )

    for spec in prepared.specs:
        assert spec.past_requirement is not None
        assert spec.key.binding_fingerprint == operational_binding_fingerprint_v2(
            spec.source,
            prepared.resolver.resolve(spec.source),
            _GRID.grid,
            parameters=spec.past_requirement.parameters,
            interval_qualification=_QUALIFIED,
        )
        forecast_source, ref = next(
            (source, ref)
            for source, ref in prepared.resolver.bindings
            if source.station_id == spec.source.station_id
            and source.nwp_source == "ifs_ecmwf"
        )
        for future in spec.future_requests:
            assert future.binding_fingerprint == future_binding_fingerprint_v2(
                forecast_source,
                ref,
                (0,),
                _GRID.grid,
                parameters=tuple(req.parameter for req in future.requirements),
                interval_qualification=_QUALIFIED,
            )


@pytest.mark.parametrize("case", ["mixed", "unscoped", "end"])
def test_declared_grid_refuses_incompatible_or_unscoped_assignments(case: str) -> None:
    with pytest.raises(ConfigurationError, match="time grids|daily START groups"):
        prepare(
            _QUALIFIED,
            model_grid=replace(_GRID, label=PeriodLabel.END)
            if case == "end"
            else _GRID,
            mixed=case == "mixed",
            explicitly_selected=case != "unscoped",
        )
