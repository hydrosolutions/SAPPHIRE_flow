from __future__ import annotations

import dataclasses
import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from shapely.geometry import box

from sapphire_flow.services.station_readiness import (
    ReadinessStores,
    build_station_readiness,
)
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    ModelAssignmentStatus,
    ObservationSource,
    QcStatus,
    SpatialRepresentation,
    WaterLevelGaugeType,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.historical_forcing import RawHistoricalForcing
from sapphire_flow.types.ids import (
    BasinId,
    ModelId,
    StationGroupId,
    StationId,
)
from sapphire_flow.types.station import (
    GatewayPolygonBindingRow,
    GroupModelAssignment,
    StationConfig,
    StationGroup,
    StationWeatherSource,
)
from sapphire_flow.types.station_readiness import CheckStatus, StationReadinessReport
from tests.conftest import make_observation, make_station_config
from tests.fakes.fake_models import FakeStationForecastModel
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeHistoricalForcingStore,
    FakeObservationStore,
    FakeRatingCurveStore,
    FakeStationGroupStore,
    FakeStationStore,
)

NOW = ensure_utc(datetime(2026, 11, 18, 12, tzinfo=UTC))
MODEL_ID = ModelId("m1")


class FakePolygons:
    def __init__(self) -> None:
        self.rows: list[GatewayPolygonBindingRow] = []

    def fetch_bindings_for_station(
        self, station_id: StationId
    ) -> list[GatewayPolygonBindingRow]:
        return [r for r in self.rows if r.station_id == station_id]

    def store_binding(self, binding: GatewayPolygonBindingRow) -> None:
        self.rows.append(binding)


class World:
    def __init__(self, **station_kwargs: object) -> None:
        self.rng = random.Random(7)
        self.stations = FakeStationStore()
        self.basins = FakeBasinStore()
        self.observations = FakeObservationStore()
        self.forcing = FakeHistoricalForcingStore()
        self.ratings = FakeRatingCurveStore()
        self.groups = FakeStationGroupStore()
        self.polygons = FakePolygons()
        self.station: StationConfig = make_station_config(
            tz="UTC",
            network="research",
            measured_parameters=frozenset({"discharge"}),
            **station_kwargs,  # type: ignore[arg-type]
        )
        self.stations.store_station(self.station)

    def report(
        self,
        *,
        model: object | None = None,
        model_id: ModelId | None = None,
        scope: tuple[str, str | None] | None = None,
    ) -> StationReadinessReport:
        return build_station_readiness(
            self.station,
            ReadinessStores(
                stations=self.stations,
                basins=self.basins,
                observations=self.observations,
                forcing=self.forcing,
                ratings=self.ratings,
                groups=self.groups,
                polygons=self.polygons,
            ),
            model=model,  # type: ignore[arg-type]
            model_id=model_id,
            scope_hru_name=scope[0] if scope else None,
            scope_polygon_name=scope[1] if scope else None,
            now=NOW,
        )

    def line(self, name: str, **kwargs: object) -> tuple[CheckStatus, str]:
        (check,) = [c for c in self.report(**kwargs).checks if c.name == name]  # type: ignore[arg-type]
        return check.status, check.detail

    def add_discharge(
        self, days: int, *, qc: QcStatus = QcStatus.QC_PASSED, offset: int = 0
    ) -> None:
        for i in range(offset, offset + days):
            self.observations.store_observations(
                [
                    make_observation(
                        station_id=self.station.id,
                        value=None if qc is QcStatus.MISSING else 1.0 + i,
                        timestamp=ensure_utc(NOW - timedelta(days=i + 1)),
                        qc_status=qc,
                        rng=self.rng,
                    )
                ]
            )


def _basin(attributes: dict[str, object] | None) -> Basin:
    return Basin(
        id=BasinId(uuid4()),
        code="b",
        name="B",
        geometry=box(0, 0, 1, 1),
        area_km2=1.0,
        attributes=attributes,
        band_geometries=None,
        created_at=NOW,
        network="research",
    )


def _model(statics: frozenset[str] = frozenset(), lookback: int = 5) -> object:
    model = FakeStationForecastModel()
    model.data_requirements = dataclasses.replace(  # type: ignore[misc]
        FakeStationForecastModel.data_requirements,
        static_features=statics,
        lookback_steps=lookback,
    )
    return model


class TestDatum:
    def test_not_applicable_for_discharge_only_station(self) -> None:
        assert World().line("datum")[0] is CheckStatus.NOT_APPLICABLE

    def test_fails_when_water_level_without_datum(self) -> None:
        world = World()
        world.station = dataclasses.replace(
            world.station, measured_parameters=frozenset({"water_level"})
        )
        assert world.line("datum") == (
            CheckStatus.FAIL,
            "water_level_datum_masl is not set",
        )

    def test_passes_and_reports_gauge_type_and_datum(self) -> None:
        world = World()
        world.station = dataclasses.replace(
            world.station,
            measured_parameters=frozenset({"water_level"}),
            water_level_datum_masl=1250.0,
            water_level_gauge_type=WaterLevelGaugeType.STAFF_OR_PRESSURE,
        )
        status, detail = world.line("datum")
        assert status is CheckStatus.PASS
        assert "1250.0" in detail


class TestBasinAndStatics:
    def test_basin_fails_without_assignment(self) -> None:
        assert World().line("basin")[0] is CheckStatus.FAIL

    def test_statics_not_applicable_without_model(self) -> None:
        assert World().line("statics")[0] is CheckStatus.NOT_APPLICABLE

    def test_statics_reports_missing_names(self) -> None:
        basin = _basin({"slope": 1.0})
        world = World(basin_id=basin.id)
        world.basins.store_basin(basin)
        status, detail = world.line(
            "statics", model=_model(frozenset({"slope", "area"}))
        )
        assert status is CheckStatus.FAIL
        assert detail.startswith("1 of 2 missing: area")

    def test_statics_pass_when_all_resolve(self) -> None:
        basin = _basin({"slope": 1.0})
        world = World(basin_id=basin.id)
        world.basins.store_basin(basin)
        assert (
            world.line("statics", model=_model(frozenset({"slope"})))[0]
            is CheckStatus.PASS
        )


class TestWeatherBindings:
    def _bind(
        self, world: World, role: WeatherSourceRole, status: WeatherSourceStatus
    ) -> None:
        world.stations.store_weather_source(
            StationWeatherSource(
                station_id=world.station.id,
                nwp_source=f"src_{role.value}",
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                status=status,
                role=role,
            )
        )

    def test_none_bound_fails_naming_every_role(self) -> None:
        status, detail = World().line("weather_bindings")
        assert status is CheckStatus.FAIL
        assert "forecast=none reanalysis=none operational=none" in detail

    def test_inactive_binding_fails(self) -> None:
        world = World()
        for role in WeatherSourceRole:
            self._bind(world, role, WeatherSourceStatus.ACTIVE)
        assert world.line("weather_bindings")[0] is CheckStatus.PASS
        world2 = World()
        self._bind(world2, WeatherSourceRole.FORECAST, WeatherSourceStatus.INACTIVE)
        assert "forecast=inactive(src_forecast)" in world2.line("weather_bindings")[1]


class TestObservationsAndHistory:
    def test_empty_station_fails_observations_and_history(self) -> None:
        world = World()
        assert world.line("observations")[0] is CheckStatus.FAIL
        assert world.line("weather_history")[0] is CheckStatus.FAIL
        assert world.line("discharge_series")[0] is CheckStatus.FAIL

    def test_counts_range_age_and_qc_share(self) -> None:
        world = World()
        world.add_discharge(3)
        world.add_discharge(1, qc=QcStatus.QC_FAILED, offset=3)
        status, detail = world.line("observations")
        assert status is CheckStatus.PASS
        assert "n=4" in detail
        assert "newest 1d old" in detail
        assert "qc_failed=25% qc_passed=75%" in detail

    def test_weather_history_reports_count_and_overlap(self) -> None:
        world = World()
        world.add_discharge(10)
        world.forcing.store_forcing(
            [
                RawHistoricalForcing(
                    station_id=world.station.id,
                    source="era5_land",
                    version="v1",
                    valid_time=ensure_utc(NOW - timedelta(days=i)),
                    parameter="precipitation",
                    spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                    band_id=None,
                    member_id=None,
                    value=1.0,
                )
                for i in range(5)
            ]
        )
        status, detail = world.line("weather_history")
        assert status is CheckStatus.PASS
        assert "precipitation n=5" in detail
        assert "overlap with observations: 4 days" in detail

    def test_discharge_series_lists_sources(self) -> None:
        world = World()
        world.add_discharge(2)
        assert world.line("discharge_series") == (
            CheckStatus.PASS,
            f"{ObservationSource.MEASURED.value}=2",
        )


class TestRatingCurve:
    def test_not_applicable_when_discharge_is_not_a_target(self) -> None:
        world = World(forecast_targets=None)
        assert world.line("rating_curve")[0] is CheckStatus.NOT_APPLICABLE

    def test_fails_without_active_curve(self) -> None:
        assert World().line("rating_curve")[0] is CheckStatus.FAIL


class TestGroupAndModel:
    def _group(self, world: World, status: ModelAssignmentStatus) -> None:
        group = StationGroup(
            id=StationGroupId(uuid4()),
            name="g1",
            station_ids=frozenset({world.station.id}),
            created_at=NOW,
            tenant_id=world.station.tenant_id,
        )
        world.groups.store_group(group)
        world.groups.seed_group_model_assignment(
            group.id,
            MODEL_ID,
            GroupModelAssignment(
                group_id=group.id,
                model_id=MODEL_ID,
                time_step=timedelta(hours=24),
                status=status,
                priority=1,
                created_at=NOW,
            ),
        )

    def test_station_in_no_group_fails(self) -> None:
        assert World().line("group_and_model")[0] is CheckStatus.FAIL

    def test_requested_model_must_be_actively_assigned(self) -> None:
        world = World()
        self._group(world, ModelAssignmentStatus.ACTIVE)
        assert world.line("group_and_model", model_id=MODEL_ID)[0] is CheckStatus.PASS
        assert (
            world.line("group_and_model", model_id=ModelId("other"))[0]
            is CheckStatus.FAIL
        )

    def test_inactive_assignment_fails(self) -> None:
        world = World()
        self._group(world, ModelAssignmentStatus.INACTIVE)
        assert world.line("group_and_model", model_id=MODEL_ID)[0] is CheckStatus.FAIL


class TestDischargeLookback:
    def test_default_is_30_days_and_lists_missing_dates(self) -> None:
        world = World()
        world.add_discharge(28)
        status, detail = world.line("discharge_lookback")
        assert status is CheckStatus.FAIL
        assert detail.startswith("28 of 30 days usable (default lookback)")
        assert "2026-10-20" in detail

    def test_suspect_counts_as_missing_and_unchecked_counts(self) -> None:
        world = World()
        world.add_discharge(3, qc=QcStatus.QC_UNCHECKED)
        world.add_discharge(2, qc=QcStatus.QC_SUSPECT, offset=3)
        assert (
            "3 of 5 days usable (model lookback)"
            in world.line("discharge_lookback", model=_model(lookback=5))[1]
        )


class TestNaming:
    def _binding(self, world: World, hru: str, name: str) -> None:
        world.polygons.store_binding(
            GatewayPolygonBindingRow(
                station_id=world.station.id,
                basin_id=BasinId(uuid4()),
                gateway_hru_name=hru,
                name=name,
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
            )
        )

    def test_missing_binding_fails(self) -> None:
        assert World().line("naming")[0] is CheckStatus.FAIL

    def test_mismatch_shows_both_spellings(self) -> None:
        world = World()
        self._binding(world, "syn001", "syn001")
        status, detail = world.line("naming", scope=("syn01", "syn001"))
        assert status is CheckStatus.FAIL
        assert "'syn01'" in detail
        assert "'syn001'" in detail

    def test_skipped_without_scope_and_pass_when_matching(self) -> None:
        world = World()
        self._binding(world, "syn001", "syn001")
        assert "comparison skipped" in world.line("naming")[1]
        assert world.line("naming", scope=("syn001", "syn001"))[0] is CheckStatus.PASS


class TestNoSingleVerdict:
    def test_report_has_no_ready_field_and_counts_add_up(self) -> None:
        report = World().report()
        assert not any("ready" in f.name for f in dataclasses.fields(report))
        assert len(report.checks) == 12
        assert report.failed() + report.passed() <= 12


class TestReviewFixes:
    def test_complete_daily_series_up_to_yesterday_passes_lookback(self) -> None:
        world = World()
        world.add_discharge(30)
        assert world.line("discharge_lookback")[0] is CheckStatus.PASS

    def test_naming_fails_when_station_not_in_scope(self) -> None:
        world = World()
        TestNaming()._binding(world, "syn001", "syn001")
        status, detail = world.line("naming", scope=("syn001", None))
        assert status is CheckStatus.FAIL
        assert "not in the weather scope" in detail

    def test_naming_fails_on_polygon_name_mismatch_alone(self) -> None:
        world = World()
        TestNaming()._binding(world, "syn001", "wrong_poly")
        status, detail = world.line("naming", scope=("syn001", "syn001"))
        assert status is CheckStatus.FAIL
        assert "'wrong_poly'" in detail

    def test_basin_fails_when_row_is_missing(self) -> None:
        world = World(basin_id=BasinId(uuid4()))
        assert world.line("basin")[0] is CheckStatus.FAIL

    def test_basin_passes_when_row_exists(self) -> None:
        basin = _basin(None)
        world = World(basin_id=basin.id)
        world.basins.store_basin(basin)
        assert world.line("basin")[0] is CheckStatus.PASS

    def test_inactive_binding_alone_does_not_make_roles_pass(self) -> None:
        world = World()
        helper = TestWeatherBindings()
        for role in WeatherSourceRole:
            helper._bind(
                world,
                role,
                WeatherSourceStatus.INACTIVE
                if role is WeatherSourceRole.OPERATIONAL
                else WeatherSourceStatus.ACTIVE,
            )
        status, detail = world.line("weather_bindings")
        assert status is CheckStatus.FAIL
        assert "operational=inactive" in detail

    def test_level_only_station_targeting_level_has_no_rating_line(self) -> None:
        world = World()
        world.station = dataclasses.replace(
            world.station,
            measured_parameters=frozenset({"water_level"}),
            forecast_targets=frozenset({"water_level"}),
        )
        assert world.line("rating_curve")[0] is CheckStatus.NOT_APPLICABLE

    def test_discharge_series_fails_when_only_missing_rows(self) -> None:
        world = World()
        world.add_discharge(3, qc=QcStatus.MISSING)
        assert world.line("discharge_series")[0] is CheckStatus.FAIL

    def test_untrusted_row_is_excluded_from_the_summary(self) -> None:
        world = World()
        world.add_discharge(2)
        (obs,) = world.observations.fetch_observations(
            world.station.id, "discharge", NOW - timedelta(days=30), NOW
        )[:1]
        world.observations.hold_unit_observation(obs.id, "h1")
        assert "n=1" in world.line("observations")[1]
