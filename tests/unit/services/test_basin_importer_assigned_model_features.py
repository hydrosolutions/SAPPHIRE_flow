"""Plan 120 Task 3A fixer round — ``build_assigned_model_features_resolver``.

Unit-level (no DB) coverage of the PRODUCTION ``assigned_model_features``
seam: the CLI defaulting this to ``None`` was the major finding this module
fixes (``evaluate_basin_acceptance`` treats ``None`` as "no basin is
verifiably assigned", downgrading a null required-static-feature to a
warning instead of an onboarding hold). These tests exercise the resolver
directly against fakes; the CLI-facing integration path is covered by
``tests/integration/services/test_basin_importer.py``.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from shapely.geometry import Polygon

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.basin_importer import (
    build_assigned_model_features_resolver,
)
from sapphire_flow.types.basin_package import BasinRecord
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ModelAssignmentStatus
from sapphire_flow.types.ids import ModelId, StationGroupId, StationId
from sapphire_flow.types.station import (
    GroupModelAssignment,
    ModelAssignment,
    StationGroup,
)
from tests.fakes.fake_models import FakeStationForecastModel
from tests.fakes.fake_stores import FakeStationGroupStore, FakeStationStore

_NOW = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))
_VALID_GEOM = Polygon([(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)])


def _basin(**overrides: object) -> BasinRecord:
    defaults: dict[str, object] = dict(
        network="dhm",
        station_code="123",
        basin_code="123",
        gateway_hru_name="nepal_dhm_v1",
        name="g_123",
        display_name="disp",
        area_km2=10.0,
        outlet_lon=85.0,
        outlet_lat=27.0,
        delineation_method="method",
        geometry=_VALID_GEOM,
        gauge_id="nepal_123",
        latitude=27.0,
        longitude=85.0,
        regional_basin=None,
        outlet_snap_distance_m=10.0,
    )
    defaults.update(overrides)
    return BasinRecord(**defaults)  # type: ignore[arg-type]


def _model(static_features: frozenset[str]) -> FakeStationForecastModel:
    model = FakeStationForecastModel()
    model.data_requirements = dataclasses.replace(
        model.data_requirements, static_features=static_features
    )
    return model


class TestBuildAssignedModelFeaturesResolver:
    def test_unmatched_station_returns_empty_set(self) -> None:
        resolver = build_assigned_model_features_resolver(
            FakeStationStore(),
            FakeStationGroupStore(),
            resolve_station=lambda code, network: None,
            models={},
        )
        assert resolver(_basin()) == frozenset()

    def test_no_active_assignment_returns_empty_set(self) -> None:
        station_id = StationId(uuid.uuid4())
        resolver = build_assigned_model_features_resolver(
            FakeStationStore(),
            FakeStationGroupStore(),
            resolve_station=lambda code, network: station_id,
            models={},
        )
        assert resolver(_basin()) == frozenset()

    def test_active_station_assignment_unions_static_features(self) -> None:
        station_id = StationId(uuid.uuid4())
        model_id = ModelId("m1")
        station_store = FakeStationStore()
        station_store.store_model_assignment(
            ModelAssignment(
                station_id=station_id,
                model_id=model_id,
                time_step=timedelta(hours=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=1,
                created_at=_NOW,
            )
        )
        resolver = build_assigned_model_features_resolver(
            station_store,
            FakeStationGroupStore(),
            resolve_station=lambda code, network: station_id,
            models={model_id: _model(frozenset({"slope_mean", "elevation_mean"}))},
        )
        assert resolver(_basin()) == frozenset({"slope_mean", "elevation_mean"})

    def test_inactive_station_assignment_is_ignored(self) -> None:
        station_id = StationId(uuid.uuid4())
        model_id = ModelId("m1")
        station_store = FakeStationStore()
        station_store.store_model_assignment(
            ModelAssignment(
                station_id=station_id,
                model_id=model_id,
                time_step=timedelta(hours=1),
                status=ModelAssignmentStatus.INACTIVE,
                priority=1,
                created_at=_NOW,
            )
        )
        resolver = build_assigned_model_features_resolver(
            station_store,
            FakeStationGroupStore(),
            resolve_station=lambda code, network: station_id,
            models={model_id: _model(frozenset({"slope_mean"}))},
        )
        assert resolver(_basin()) == frozenset()

    def test_active_group_assignment_unions_static_features(self) -> None:
        station_id = StationId(uuid.uuid4())
        group_id = StationGroupId(uuid.uuid4())
        model_id = ModelId("group_model")

        group_store = FakeStationGroupStore()
        group_store.store_group(
            StationGroup(
                id=group_id,
                name="g1",
                station_ids=frozenset({station_id}),
                created_at=_NOW,
            )
        )
        group_store.store_group_model_assignment(
            GroupModelAssignment(
                group_id=group_id,
                model_id=model_id,
                time_step=timedelta(hours=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=1,
                created_at=_NOW,
            )
        )

        resolver = build_assigned_model_features_resolver(
            FakeStationStore(),
            group_store,
            resolve_station=lambda code, network: station_id,
            models={model_id: _model(frozenset({"aridity_index"}))},
        )
        assert resolver(_basin()) == frozenset({"aridity_index"})

    def test_undiscoverable_active_model_raises_configuration_error(self) -> None:
        """An ACTIVE assignment naming a model absent from the discovered set
        is a configuration bug — it must fail loudly, never silently resolve
        to an empty/partial feature set (the exact failure mode this fixer
        round closes for the CLI's default-None seam)."""
        station_id = StationId(uuid.uuid4())
        model_id = ModelId("missing_model")
        station_store = FakeStationStore()
        station_store.store_model_assignment(
            ModelAssignment(
                station_id=station_id,
                model_id=model_id,
                time_step=timedelta(hours=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=1,
                created_at=_NOW,
            )
        )
        resolver = build_assigned_model_features_resolver(
            station_store,
            FakeStationGroupStore(),
            resolve_station=lambda code, network: station_id,
            models={},  # model_id not discovered
        )
        with pytest.raises(ConfigurationError, match="missing_model"):
            resolver(_basin())
