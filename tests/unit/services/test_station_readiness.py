from __future__ import annotations

import dataclasses
import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pandas as pd
from shapely.geometry import box

from sapphire_flow.adapters.recap_gateway import RecapTransientError
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.station_readiness import (
    ReadinessStores,
    TrainingWindowRequest,
    build_station_readiness,
)
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    InterpolationMethod,
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
    RatingCurveId,
    StationGroupId,
    StationId,
)
from sapphire_flow.types.nepal_onboarding import GATEWAY_HISTORY_SOURCE
from sapphire_flow.types.rating_curve import RatingCurve
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
        training: TrainingWindowRequest | None = None,
        gateway: object | None = None,
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
            training=training,
            gateway=gateway,  # type: ignore[arg-type]
        )

    def supplementary(self, name: str, **kwargs: object) -> tuple[CheckStatus, str]:
        (check,) = [c for c in self.report(**kwargs).supplementary if c.name == name]  # type: ignore[arg-type]
        return check.status, check.detail

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

    def test_without_model_a_group_with_no_assignment_says_so(self) -> None:
        world = World()
        world.groups.store_group(
            StationGroup(
                id=StationGroupId(uuid4()),
                name="g1",
                station_ids=frozenset({world.station.id}),
                created_at=NOW,
                tenant_id=world.station.tenant_id,
            )
        )
        status, detail = world.line("group_and_model")
        assert status is CheckStatus.PASS
        assert "assignments=none" in detail
        assert "no model assigned yet" in detail

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
        assert len(report.checks) == 13
        assert report.failed() + report.passed() <= 13


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


class TestRatingCurveAtStepSeven:
    def _level_station(self) -> World:
        world = World(forecast_targets=None)
        world.station = dataclasses.replace(
            world.station, measured_parameters=frozenset({"water_level"})
        )
        return world

    def test_unset_targets_level_station_without_curve_fails(self) -> None:
        assert self._level_station().line("rating_curve")[0] is CheckStatus.FAIL

    def test_unset_targets_level_station_with_curve_passes(self) -> None:
        world = self._level_station()
        world.ratings.store_rating_curve(
            RatingCurve(
                id=RatingCurveId(uuid4()),
                station_id=world.station.id,
                version=1,
                valid_from=NOW - timedelta(days=100),
                valid_to=None,
                points=[{"stage": 1.0, "discharge": 1.0}],
                interpolation=InterpolationMethod.LINEAR,
                uploaded_by=None,
                created_at=NOW,
            )
        )
        assert world.line("rating_curve")[0] is CheckStatus.PASS

    def test_level_target_only_is_not_applicable(self) -> None:
        world = self._level_station()
        world.station = dataclasses.replace(
            world.station, forecast_targets=frozenset({"water_level"})
        )
        assert world.line("rating_curve")[0] is CheckStatus.NOT_APPLICABLE


def _bind(world: World, role: WeatherSourceRole, status: WeatherSourceStatus) -> None:
    TestWeatherBindings()._bind(world, role, status)


def _forcing(world: World, source: str, days: range, parameter: str) -> None:
    world.forcing.store_forcing(
        [
            RawHistoricalForcing(
                station_id=world.station.id,
                source=source,
                version="v1",
                valid_time=ensure_utc(NOW - timedelta(days=i)),
                parameter=parameter,
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
                member_id=None,
                value=1.0,
            )
            for i in days
        ]
    )


class TestWeatherWaitFacts:
    def _all_bound(self, world: World) -> None:
        for role in WeatherSourceRole:
            _bind(world, role, WeatherSourceStatus.ACTIVE)

    def test_nothing_bound_or_stored_fails_with_every_role_state(self) -> None:
        status, detail = World().line("weather_wait_facts")
        assert status is CheckStatus.FAIL
        assert "forecast=missing reanalysis=missing operational=missing" in detail
        assert "precipitation missing 23 of 23 days" in detail

    def test_inactive_role_is_named_inactive(self) -> None:
        world = World()
        _bind(world, WeatherSourceRole.OPERATIONAL, WeatherSourceStatus.INACTIVE)
        assert "operational=inactive" in world.line("weather_wait_facts")[1]

    def test_lists_days_without_stored_reanalysis_until_yesterday(self) -> None:
        world = World()
        self._all_bound(world)
        for parameter in ("precipitation", "temperature"):
            _forcing(world, "src_reanalysis", range(8, 29), parameter)
        status, detail = world.line("weather_wait_facts")
        assert status is CheckStatus.FAIL
        assert "precipitation missing 2 of 23 days: 2026-10-19, 2026-10-20" in detail

    def test_gateway_history_is_found_under_its_stored_source_tag(self) -> None:
        # The Nepal host binds reanalysis as "era5_land" but the history job stores
        # it as GATEWAY_HISTORY_SOURCE; the binding name alone finds nothing.
        world = World()
        for role in (WeatherSourceRole.FORECAST, WeatherSourceRole.OPERATIONAL):
            _bind(world, role, WeatherSourceStatus.ACTIVE)
        world.stations.store_weather_source(
            StationWeatherSource(
                station_id=world.station.id,
                nwp_source="era5_land",
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                status=WeatherSourceStatus.ACTIVE,
                role=WeatherSourceRole.REANALYSIS,
            )
        )
        for parameter in ("precipitation", "temperature"):
            _forcing(world, GATEWAY_HISTORY_SOURCE, range(1, 32), parameter)
        status, detail = world.line("weather_wait_facts")
        assert status is CheckStatus.PASS
        assert "all 23 expected days present" in detail

    def test_passes_when_bound_and_every_day_stored(self) -> None:
        world = World()
        self._all_bound(world)
        for parameter in ("precipitation", "temperature"):
            _forcing(world, "src_reanalysis", range(1, 32), parameter)
        assert world.line("weather_wait_facts") == (
            CheckStatus.PASS,
            "forecast=bound reanalysis=bound operational=bound; "
            "stored reanalysis: all 23 expected days present; 7 newest days not "
            "expected yet (Gateway lag, filled by the operational product at "
            "forecast time)",
        )

    def test_newest_lag_days_absent_still_passes_for_a_healthy_station(self) -> None:
        world = World()
        self._all_bound(world)
        for parameter in ("precipitation", "temperature"):
            _forcing(world, "src_reanalysis", range(8, 33), parameter)
        status, detail = world.line("weather_wait_facts")
        assert status is CheckStatus.PASS
        assert "newest days not expected yet" in detail

    def test_older_missing_day_still_fails(self) -> None:
        world = World()
        self._all_bound(world)
        for parameter in ("precipitation", "temperature"):
            _forcing(
                world, "src_reanalysis", [*range(8, 20), *range(21, 33)], parameter
            )
        assert world.line("weather_wait_facts")[0] is CheckStatus.FAIL

    def test_missing_role_fails_even_when_every_day_is_stored(self) -> None:
        world = World()
        _bind(world, WeatherSourceRole.REANALYSIS, WeatherSourceStatus.ACTIVE)
        for parameter in ("precipitation", "temperature"):
            _forcing(world, "src_reanalysis", range(8, 33), parameter)
        status, detail = world.line("weather_wait_facts")
        assert status is CheckStatus.FAIL
        assert "operational=missing" in detail
        assert "precipitation missing" not in detail


class FakeEcmwf:
    def __init__(
        self,
        days: range | None = None,
        error: Exception | None = None,
        nan_days: tuple[int, ...] = (),
        raw: object | None = None,
    ) -> None:
        self.days = days if days is not None else range(1, 31)
        self.error = error
        self.nan_days = nan_days
        self.raw = raw
        self.calls: list[str] = []

    def operational(self, **kwargs: object) -> pd.DataFrame:
        self.calls.append(str(kwargs["ifs_variable_name"]))
        if self.error is not None:
            raise self.error
        if self.raw is not None:
            return self.raw  # type: ignore[return-value]
        index = pd.DatetimeIndex(
            [pd.Timestamp(NOW.date() - timedelta(days=i)) for i in self.days]
        )
        values = [None if i in self.nan_days else 1.0 for i in self.days]
        return pd.DataFrame({"syn001": values}, index=index)


class TestGatewayData:
    def _world(self) -> World:
        world = World()
        TestNaming()._binding(world, "syn001", "syn001")
        return world

    def _line(self, world: World, api: FakeEcmwf) -> tuple[CheckStatus, str]:
        return world.supplementary("gateway_data", gateway=lambda: api)

    def test_off_by_default(self) -> None:
        assert self._world().report().supplementary == ()

    def test_all_days_present(self) -> None:
        api = FakeEcmwf()
        assert self._line(self._world(), api) == (
            CheckStatus.PASS,
            "OK: all 30 days present",
        )
        assert sorted(api.calls) == ["2t", "tp"]

    def test_missing_days_are_listed_with_retry_hint(self) -> None:
        status, detail = self._line(self._world(), FakeEcmwf(days=range(3, 31)))
        assert status is CheckStatus.FAIL
        assert "precipitation missing 2 of 30 days" in detail
        assert detail.endswith("try again after the backfill")

    def test_nan_or_none_value_counts_as_a_missing_day(self) -> None:
        status, detail = self._line(self._world(), FakeEcmwf(nan_days=(5,)))
        assert status is CheckStatus.FAIL
        assert "precipitation missing 1 of 30 days: 2026-11-13" in detail

    def test_non_dataframe_response_is_unknown(self) -> None:
        status, _ = self._line(self._world(), FakeEcmwf(raw={"not": "a frame"}))
        assert status is CheckStatus.UNKNOWN

    def test_frame_without_the_polygon_column_is_unknown(self) -> None:
        index = pd.DatetimeIndex([pd.Timestamp("2026-11-17")])
        frame = pd.DataFrame({"other": [1.0]}, index=index)
        status, _ = self._line(self._world(), FakeEcmwf(raw=frame))
        assert status is CheckStatus.UNKNOWN

    def test_gateway_failure_is_unknown_not_pass(self) -> None:
        status, detail = self._line(
            self._world(), FakeEcmwf(error=RecapTransientError("secret"))
        )
        assert status is CheckStatus.UNKNOWN
        assert "secret" not in detail

    def test_missing_key_is_unknown(self) -> None:
        def no_key() -> FakeEcmwf:
            raise ConfigurationError("recap Gateway API key not found")

        status, _ = self._world().supplementary("gateway_data", gateway=no_key)
        assert status is CheckStatus.UNKNOWN

    def test_no_polygon_binding_is_unknown(self) -> None:
        assert World().supplementary("gateway_data", gateway=FakeEcmwf)[0] is (
            CheckStatus.UNKNOWN
        )

    def test_never_changes_the_offline_counts(self) -> None:
        world = self._world()
        without = world.report()
        with_gw = world.report(gateway=lambda: FakeEcmwf(days=range(20, 21)))
        assert (with_gw.failed(), with_gw.passed()) == (
            without.failed(),
            without.passed(),
        )


class TestTrainingSamples:
    def _request(self, minimum: int = 0) -> TrainingWindowRequest:
        return TrainingWindowRequest(
            start=ensure_utc(NOW - timedelta(days=40)),
            end=NOW,
            time_step=timedelta(hours=24),
            minimum_samples=minimum,
        )

    def _world(self, days: int) -> World:
        world = World()
        _bind(world, WeatherSourceRole.REANALYSIS, WeatherSourceStatus.ACTIVE)
        world.add_discharge(days)
        for parameter in ("precipitation", "temperature"):
            _forcing(world, "src_reanalysis", range(1, days + 1), parameter)
        return world

    def test_counts_complete_windows(self) -> None:
        status, detail = self._world(12).supplementary(
            "training_samples", model=_model(lookback=3), training=self._request()
        )
        assert status is CheckStatus.PASS
        assert detail.startswith("5 complete samples in ")

    def test_thin_history_fails_only_the_training_line(self) -> None:
        world = self._world(4)
        report = world.report(model=_model(lookback=3), training=self._request())
        (line,) = report.supplementary
        assert line.status is CheckStatus.FAIL
        assert report.failed() == world.report(model=_model(lookback=3)).failed()

    def test_minimum_samples_is_enforced(self) -> None:
        status, detail = self._world(12).supplementary(
            "training_samples",
            model=_model(lookback=3),
            training=self._request(minimum=6),
        )
        assert status is CheckStatus.FAIL
        assert "(minimum 6)" in detail

    def test_no_inputs_reports_zero_samples(self) -> None:
        status, detail = World().supplementary(
            "training_samples", model=_model(lookback=3), training=self._request()
        )
        assert status is CheckStatus.FAIL
        assert "0 complete samples" in detail

    def test_window_must_be_ordered(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="precede"):
            TrainingWindowRequest(start=NOW, end=NOW, time_step=timedelta(hours=24))
