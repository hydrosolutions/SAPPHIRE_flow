from __future__ import annotations

import importlib.metadata
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa

if TYPE_CHECKING:
    from pathlib import Path

from sapphire_flow.db.metadata import (
    model_artifacts,
    models,
    station_group_members,
    station_groups,
    stations,
)
from sapphire_flow.exceptions import ArtifactIntegrityError
from sapphire_flow.services.training import promote_artifact
from sapphire_flow.store.model_artifact_store import PgModelArtifactStore
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ModelArtifactStatus, ModelAssignmentStatus
from sapphire_flow.types.ids import ModelId, StationGroupId, StationId
from sapphire_flow.types.station import GroupModelAssignment, StationGroup

_T0 = ensure_utc(datetime(2024, 1, 1, tzinfo=UTC))
_T1 = ensure_utc(datetime(2024, 6, 1, tzinfo=UTC))
_T2 = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_NOW = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_station(conn: sa.Connection) -> StationId:
    sid = StationId(uuid.uuid4())
    conn.execute(
        sa.insert(stations).values(
            id=sid,
            code=f"ONB-{sid.hex[:6]}",
            name="Onboarding Test Station",
            location="SRID=4326;POINT(8.5 47.4)",
            station_kind="river",
            network="bafu",
            timezone="Europe/Zurich",
            measured_parameters=["discharge"],
            ownership="own",
        )
    )
    return sid


def _seed_model(conn: sa.Connection, scope: str = "station") -> ModelId:
    mid = ModelId(f"onb_model_{uuid.uuid4().hex[:8]}")
    conn.execute(
        sa.insert(models).values(
            id=mid,
            display_name="Onboarding Test Model",
            artifact_scope=scope,
            description="Integration test",
        )
    )
    return mid


def _seed_group(conn: sa.Connection, *station_ids: StationId) -> StationGroupId:
    gid = StationGroupId(uuid.uuid4())
    conn.execute(
        sa.insert(station_groups).values(
            id=gid,
            name=f"onb-grp-{gid.hex[:6]}",
            description=None,
        )
    )
    for sid in station_ids:
        conn.execute(
            sa.insert(station_group_members).values(
                group_id=gid,
                station_id=sid,
            )
        )
    return gid


# ---------------------------------------------------------------------------
# TestArtifactStatusTransitions
# ---------------------------------------------------------------------------


class TestArtifactStatusTransitions:
    def test_store_training_then_promote_to_active(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        station_id = _seed_station(db_connection)
        model_id = _seed_model(db_connection)
        store = PgModelArtifactStore(db_connection, tmp_path)

        aid, _ = store.store_artifact(
            model_id, b"artifact_bytes", _T0, _T1, _T2, station_id=station_id
        )

        record_before = store.fetch_artifact_record(aid)
        assert record_before is not None
        assert record_before.status == ModelArtifactStatus.TRAINING

        promote_artifact(
            artifact_store=store,
            model_id=model_id,
            new_id=aid,
            station_id=station_id,
        )

        record_after = store.fetch_artifact_record(aid)
        assert record_after is not None
        assert record_after.status == ModelArtifactStatus.ACTIVE
        assert record_after.promoted_at is not None

    def test_promote_supersedes_existing_active(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        station_id = _seed_station(db_connection)
        model_id = _seed_model(db_connection)
        store = PgModelArtifactStore(db_connection, tmp_path)

        # Artifact A: store then promote to ACTIVE
        aid_a, _ = store.store_artifact(
            model_id, b"bytes_a", _T0, _T1, _T2, station_id=station_id
        )
        store.transition_artifact_status(aid_a, ModelArtifactStatus.ACTIVE)

        active_before = store.fetch_artifacts_by_status(
            model_id, ModelArtifactStatus.ACTIVE, station_id=station_id
        )
        assert aid_a in active_before

        # Artifact B: store then promote via promote_artifact
        aid_b, _ = store.store_artifact(
            model_id, b"bytes_b", _T0, _T1, _T2, station_id=station_id
        )

        promote_artifact(
            artifact_store=store,
            model_id=model_id,
            new_id=aid_b,
            station_id=station_id,
        )

        record_a = store.fetch_artifact_record(aid_a)
        record_b = store.fetch_artifact_record(aid_b)

        assert record_a is not None
        assert record_a.status == ModelArtifactStatus.SUPERSEDED
        assert record_a.superseded_at is not None

        assert record_b is not None
        assert record_b.status == ModelArtifactStatus.ACTIVE
        assert record_b.promoted_at is not None


# ---------------------------------------------------------------------------
# TestSHA256HashRoundTrip
# ---------------------------------------------------------------------------


class TestSHA256HashRoundTrip:
    def test_hash_computed_on_store_and_verified_on_fetch(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        station_id = _seed_station(db_connection)
        model_id = _seed_model(db_connection)
        store = PgModelArtifactStore(db_connection, tmp_path)
        payload = b"\xca\xfe\xba\xbe" * 64

        aid, stored_hash = store.store_artifact(
            model_id, payload, _T0, _T1, _T2, station_id=station_id
        )

        assert len(stored_hash) == 64  # hex SHA-256

        result = store.fetch_artifact(aid)
        assert result is not None
        result_id, result_bytes = result
        assert result_id == aid
        assert result_bytes == payload

    def test_tampered_artifact_raises_integrity_error(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        station_id = _seed_station(db_connection)
        model_id = _seed_model(db_connection)
        store = PgModelArtifactStore(db_connection, tmp_path)
        payload = b"original_payload"

        aid, _ = store.store_artifact(
            model_id, payload, _T0, _T1, _T2, station_id=station_id
        )

        # Corrupt the stored hash so it no longer matches the file
        db_connection.execute(
            sa.update(model_artifacts)
            .where(model_artifacts.c.id == aid)
            .values(sha256_hash="0" * 64)
        )

        with pytest.raises(ArtifactIntegrityError):
            store.fetch_artifact(aid)


# ---------------------------------------------------------------------------
# TestGroupModelAssignments
# ---------------------------------------------------------------------------


class TestGroupModelAssignments:
    def test_group_assignment_fk_constraint(self, db_connection: sa.Connection) -> None:
        station_id = _seed_station(db_connection)
        group_id = _seed_group(db_connection, station_id)
        model_id = _seed_model(db_connection, scope="group")
        store = PgStationGroupStore(db_connection)

        group = StationGroup(
            id=group_id,
            name=f"fk-test-{group_id.hex[:6]}",
            station_ids=frozenset({station_id}),
            description=None,
            created_at=_NOW,
        )
        store.store_group(group)

        assignment = GroupModelAssignment(
            group_id=group_id,
            model_id=model_id,
            time_step=timedelta(hours=1),
            status=ModelAssignmentStatus.ACTIVE,
            priority=0,
            created_at=_NOW,
        )
        store.store_group_model_assignment(assignment)

        results = store.fetch_group_model_assignments(group_id)
        assert len(results) == 1
        assert results[0].group_id == group_id
        assert results[0].model_id == model_id

    def test_group_assignment_fk_constraint_nonexistent_group(
        self, db_connection: sa.Connection
    ) -> None:
        model_id = _seed_model(db_connection, scope="group")
        nonexistent_group_id = StationGroupId(uuid.uuid4())
        store = PgStationGroupStore(db_connection)

        assignment = GroupModelAssignment(
            group_id=nonexistent_group_id,
            model_id=model_id,
            time_step=timedelta(hours=1),
            status=ModelAssignmentStatus.ACTIVE,
            priority=0,
            created_at=_NOW,
        )

        with pytest.raises(sa.exc.IntegrityError):
            store.store_group_model_assignment(assignment)


# ---------------------------------------------------------------------------
# TestEntryPointDiscoverability
# ---------------------------------------------------------------------------


class TestEntryPointDiscoverability:
    def test_linear_regression_daily_discoverable(self) -> None:
        eps = importlib.metadata.entry_points(group="sapphire_flow.models")
        names = {ep.name for ep in eps}
        assert "linear_regression_daily" in names

    def test_linear_regression_daily_loads(self) -> None:
        eps = importlib.metadata.entry_points(group="sapphire_flow.models")
        ep = next(ep for ep in eps if ep.name == "linear_regression_daily")
        cls = ep.load()
        assert cls is not None
        # Verify it's the expected class
        assert cls.__name__ == "LinearRegressionDaily"
