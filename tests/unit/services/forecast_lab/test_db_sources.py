"""Plan 198 T2b — database readers for the Forecast Lab snapshot.

AC19/D17a: eligibility (network=bafu, kind=river, status=operational) is
enforced on BOTH the list-all sweep and the by-code lookup.
AC20/D17b: an INACTIVE model assignment is never returned.
AC24/D12: equal-priority assignments tie-break on `model_id` ascending.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sapphire_flow.services.forecast_lab.db_sources import (
    ForecastLabStores,
    fetch_active_model_assignments,
    fetch_artifact_info,
    fetch_basin_area_km2,
    fetch_eligible_station_by_code,
    fetch_eligible_stations,
    fetch_observation_window,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    ModelAssignmentStatus,
    ObservationSource,
    QcStatus,
    StationKind,
    StationStatus,
)
from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
from sapphire_flow.types.model import ModelArtifactProvenance
from sapphire_flow.types.station import ModelAssignment
from tests.conftest import make_observation, make_station_config
from tests.fakes.fake_stores import (
    FakeArtifactProvenanceStore,
    FakeBasinStore,
    FakeForecastStore,
    FakeModelArtifactStore,
    FakeModelStore,
    FakeObservationStore,
    FakeStationStore,
)

_EPOCH = ensure_utc(datetime(2026, 8, 21, 10, 0, 0, tzinfo=UTC))


def _make_stores(
    station_store: FakeStationStore | None = None,
    observation_store: FakeObservationStore | None = None,
    basin_store: FakeBasinStore | None = None,
    artifact_store: FakeModelArtifactStore | None = None,
    provenance_store: FakeArtifactProvenanceStore | None = None,
) -> ForecastLabStores:
    return ForecastLabStores(
        station_store=station_store or FakeStationStore(),
        observation_store=observation_store or FakeObservationStore(),
        forecast_store=FakeForecastStore(),
        model_store=FakeModelStore(),
        artifact_store=artifact_store or FakeModelArtifactStore(),
        provenance_store=provenance_store or FakeArtifactProvenanceStore(),
        basin_store=basin_store or FakeBasinStore(),
    )


class TestEligibleStations:
    def test_only_operational_bafu_river_stations_are_returned(self) -> None:
        store = FakeStationStore()
        eligible = make_station_config(code="2009")
        wrong_network = make_station_config(
            code="9001", network="dhm", station_id=StationId(uuid4())
        )
        lake = make_station_config(
            code="3001",
            station_kind=StationKind.LAKE,
            station_id=StationId(uuid4()),
        )
        onboarding_status = make_station_config(
            code="2011",
            station_id=StationId(uuid4()),
        )
        from dataclasses import replace

        from sapphire_flow.types.enums import StationStatus

        onboarding = replace(onboarding_status, station_status=StationStatus.ONBOARDING)
        for s in (eligible, wrong_network, lake, onboarding):
            store.store_station(s)

        stores = _make_stores(station_store=store)
        result = fetch_eligible_stations(stores)

        assert [s.code for s in result] == ["2009"]

    def test_results_are_sorted_by_code(self) -> None:
        store = FakeStationStore()
        for code in ("3000", "1000", "2000"):
            store.store_station(
                make_station_config(code=code, station_id=StationId(uuid4()))
            )
        stores = _make_stores(station_store=store)
        result = fetch_eligible_stations(stores)
        assert [s.code for s in result] == ["1000", "2000", "3000"]

    def test_by_code_rejects_a_lake_station_with_the_same_code(self) -> None:
        store = FakeStationStore()
        store.store_station(
            make_station_config(
                code="3001",
                station_kind=StationKind.LAKE,
                station_id=StationId(uuid4()),
            )
        )
        stores = _make_stores(station_store=store)
        assert fetch_eligible_station_by_code(stores, "3001") is None

    def test_by_code_rejects_a_non_operational_station(self) -> None:
        # AC19 names status as well as kind and network. Without this an
        # implementation that filters only kind+network on the by-code path
        # passes, and an onboarding station would reach the snapshot.
        store = FakeStationStore()
        store.store_station(
            make_station_config(
                code="3003",
                station_status=StationStatus.ONBOARDING,
                station_id=StationId(uuid4()),
            )
        )
        stores = _make_stores(station_store=store)
        assert fetch_eligible_station_by_code(stores, "3003") is None

    def test_by_code_rejects_wrong_network(self) -> None:
        store = FakeStationStore()
        store.store_station(
            make_station_config(
                code="9001", network="dhm", station_id=StationId(uuid4())
            )
        )
        stores = _make_stores(station_store=store)
        assert fetch_eligible_station_by_code(stores, "9001") is None

    def test_by_code_accepts_an_eligible_station(self) -> None:
        store = FakeStationStore()
        store.store_station(make_station_config(code="2009"))
        stores = _make_stores(station_store=store)
        found = fetch_eligible_station_by_code(stores, "2009")
        assert found is not None
        assert found.code == "2009"

    def test_unknown_code_returns_none(self) -> None:
        stores = _make_stores()
        assert fetch_eligible_station_by_code(stores, "does-not-exist") is None


class TestActiveModelAssignments:
    def test_inactive_assignment_is_excluded(self) -> None:
        station = make_station_config(code="2009")
        store = FakeStationStore()
        store.store_station(station)
        store.store_model_assignment(
            ModelAssignment(
                station_id=station.id,
                model_id=ModelId("nwp_regression"),
                time_step=timedelta(days=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=10,
                created_at=_EPOCH,
            )
        )
        store.store_model_assignment(
            ModelAssignment(
                station_id=station.id,
                model_id=ModelId("retired_model"),
                time_step=timedelta(days=1),
                status=ModelAssignmentStatus.INACTIVE,
                priority=1,
                created_at=_EPOCH,
            )
        )
        stores = _make_stores(station_store=store)
        result = fetch_active_model_assignments(stores, station.id)
        assert [a.model_id for a in result] == [ModelId("nwp_regression")]

    def test_equal_priority_ties_break_on_model_id_ascending(self) -> None:
        station = make_station_config(code="2009")
        store = FakeStationStore()
        store.store_station(station)
        for model_id in ("zeta_model", "alpha_model"):
            store.store_model_assignment(
                ModelAssignment(
                    station_id=station.id,
                    model_id=ModelId(model_id),
                    time_step=timedelta(days=1),
                    status=ModelAssignmentStatus.ACTIVE,
                    priority=0,
                    created_at=_EPOCH,
                )
            )
        stores = _make_stores(station_store=store)
        result = fetch_active_model_assignments(stores, station.id)
        assert [a.model_id for a in result] == [
            ModelId("alpha_model"),
            ModelId("zeta_model"),
        ]

    def test_lower_priority_number_wins_ordering(self) -> None:
        station = make_station_config(code="2009")
        store = FakeStationStore()
        store.store_station(station)
        store.store_model_assignment(
            ModelAssignment(
                station_id=station.id,
                model_id=ModelId("low_priority"),
                time_step=timedelta(days=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=50,
                created_at=_EPOCH,
            )
        )
        store.store_model_assignment(
            ModelAssignment(
                station_id=station.id,
                model_id=ModelId("high_priority"),
                time_step=timedelta(days=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=10,
                created_at=_EPOCH,
            )
        )
        stores = _make_stores(station_store=store)
        result = fetch_active_model_assignments(stores, station.id)
        assert [a.model_id for a in result] == [
            ModelId("high_priority"),
            ModelId("low_priority"),
        ]


class TestObservationWindow:
    def test_only_measured_qc_passed_discharge_is_returned(self) -> None:
        station_id = StationId(uuid4())
        store = FakeObservationStore()
        # Distinct rng per call — make_observation's default rng seed is
        # fixed, so reusing it would mint colliding ObservationIds and the
        # dict-keyed fake store would silently drop all but the last.
        good = make_observation(
            station_id=station_id,
            parameter="discharge",
            qc_status=QcStatus.QC_PASSED,
            timestamp=_EPOCH,
            rng=random.Random(1),
        )
        raw_qc = make_observation(
            station_id=station_id,
            parameter="discharge",
            qc_status=QcStatus.RAW,
            timestamp=_EPOCH + timedelta(minutes=10),
            rng=random.Random(2),
        )
        wrong_param = make_observation(
            station_id=station_id,
            parameter="water_level",
            qc_status=QcStatus.QC_PASSED,
            timestamp=_EPOCH + timedelta(minutes=20),
            rng=random.Random(3),
        )
        # AC7 names THREE filters; without a non-`measured` row the source
        # filter is unlocked and an implementation that omits it passes.
        # `make_observation` always mints `MEASURED`, so replace() it.
        manual_import = replace(
            make_observation(
                station_id=station_id,
                parameter="discharge",
                qc_status=QcStatus.QC_PASSED,
                timestamp=_EPOCH + timedelta(minutes=30),
                rng=random.Random(4),
            ),
            source=ObservationSource.MANUAL_IMPORT,
        )
        store.store_observations([good, raw_qc, wrong_param, manual_import])

        stores = _make_stores(observation_store=store)
        result = fetch_observation_window(
            stores,
            station_id,
            window_start=_EPOCH - timedelta(hours=1),
            window_end=_EPOCH + timedelta(hours=1),
        )
        assert [o.id for o in result] == [good.id]

    def test_results_are_sorted_ascending_by_timestamp(self) -> None:
        station_id = StationId(uuid4())
        store = FakeObservationStore()
        later = make_observation(
            station_id=station_id,
            parameter="discharge",
            qc_status=QcStatus.QC_PASSED,
            timestamp=_EPOCH + timedelta(minutes=20),
            rng=random.Random(1),
        )
        earlier = make_observation(
            station_id=station_id,
            parameter="discharge",
            qc_status=QcStatus.QC_PASSED,
            timestamp=_EPOCH,
            rng=random.Random(2),
        )
        store.store_observations([later, earlier])
        stores = _make_stores(observation_store=store)
        result = fetch_observation_window(
            stores,
            station_id,
            window_start=_EPOCH - timedelta(hours=1),
            window_end=_EPOCH + timedelta(hours=1),
        )
        assert [o.id for o in result] == [earlier.id, later.id]


class TestBasinArea:
    def test_none_basin_id_returns_none(self) -> None:
        stores = _make_stores()
        assert fetch_basin_area_km2(stores, None) is None

    def test_unknown_basin_returns_none(self) -> None:
        from sapphire_flow.types.ids import BasinId

        stores = _make_stores()
        assert fetch_basin_area_km2(stores, BasinId(uuid4())) is None


class TestArtifactInfo:
    def test_none_artifact_id_returns_all_none(self) -> None:
        stores = _make_stores()
        info = fetch_artifact_info(stores, None)
        assert info.artifact_sha256 is None
        assert info.source_commit is None

    def test_sha256_from_artifact_record_source_commit_from_provenance(self) -> None:
        artifact_store = FakeModelArtifactStore()
        stored = artifact_store.store_artifact(
            ModelId("nwp_regression"),
            b"fake-bytes",
            training_period_start=_EPOCH,
            training_period_end=_EPOCH,
            trained_at=_EPOCH,
        )
        provenance_store = FakeArtifactProvenanceStore()
        provenance_store.record(
            ModelArtifactProvenance(
                artifact_id=stored.artifact_id,
                source_repository="hydrosolutions/models",
                source_commit="abc1234",
                config_hash=None,
                imported_at=_EPOCH,
                imported_by=None,
                notes=None,
            )
        )
        stores = _make_stores(
            artifact_store=artifact_store, provenance_store=provenance_store
        )
        info = fetch_artifact_info(stores, stored.artifact_id)
        assert info.artifact_sha256 == stored.sha256_hash
        assert info.source_commit == "abc1234"

    def test_no_provenance_row_yields_null_source_commit(self) -> None:
        artifact_store = FakeModelArtifactStore()
        stored = artifact_store.store_artifact(
            ModelId("nwp_regression"),
            b"fake-bytes",
            training_period_start=_EPOCH,
            training_period_end=_EPOCH,
            trained_at=_EPOCH,
        )
        stores = _make_stores(artifact_store=artifact_store)
        info = fetch_artifact_info(stores, stored.artifact_id)
        assert info.artifact_sha256 == stored.sha256_hash
        assert info.source_commit is None

    def test_unknown_artifact_id_yields_all_none(self) -> None:
        stores = _make_stores()
        info = fetch_artifact_info(stores, ArtifactId(uuid4()))
        assert info.artifact_sha256 is None
        assert info.source_commit is None
