from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

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
from sapphire_flow.store.model_artifact_store import PgModelArtifactStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ModelArtifactStatus
from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId, StationId

_T0 = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_T1 = ensure_utc(datetime(2025, 6, 1, tzinfo=UTC))
_T2 = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))


def _seed_station(conn: sa.Connection) -> StationId:
    sid = StationId(uuid.uuid4())
    conn.execute(
        sa.insert(stations).values(
            id=sid,
            code=f"ART-{sid.hex[:6]}",
            name="Artifact Test Station",
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
    mid = ModelId(f"test_art_model_{uuid.uuid4().hex[:8]}")
    conn.execute(
        sa.insert(models).values(
            id=mid,
            display_name="Artifact Test Model",
            artifact_scope=scope,
            description="Integration test",
        )
    )
    return mid


def _seed_group(conn: sa.Connection, station_id: StationId) -> StationGroupId:
    gid = StationGroupId(uuid.uuid4())
    conn.execute(
        sa.insert(station_groups).values(
            id=gid,
            name=f"grp-{gid.hex[:6]}",
            description=None,
        )
    )
    conn.execute(
        sa.insert(station_group_members).values(
            group_id=gid,
            station_id=station_id,
        )
    )
    return gid


class TestPgModelArtifactStore:
    def test_store_and_fetch_artifact(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        station_id = _seed_station(db_connection)
        model_id = _seed_model(db_connection)
        store = PgModelArtifactStore(db_connection, tmp_path)
        payload = b"\xde\xad\xbe\xef"

        aid = store.store_artifact(
            model_id,
            payload,
            _T0,
            _T1,
            _T2,
            station_id=station_id,
        )
        result = store.fetch_artifact(aid)

        assert result is not None
        result_id, result_bytes = result
        assert result_id == aid
        assert result_bytes == payload

    def test_fetch_artifact_nonexistent(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        store = PgModelArtifactStore(db_connection, tmp_path)
        assert store.fetch_artifact(ArtifactId(uuid.uuid4())) is None

    def test_fetch_active_artifact_direct(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        station_id = _seed_station(db_connection)
        model_id = _seed_model(db_connection)
        store = PgModelArtifactStore(db_connection, tmp_path)
        payload = b"active_bytes"

        aid = store.store_artifact(
            model_id, payload, _T0, _T1, _T2, station_id=station_id
        )
        result = store.fetch_active_artifact(model_id, station_id=station_id)

        assert result is not None
        assert result[0] == aid
        assert result[1] == payload

    def test_fetch_active_artifact_for_station_direct(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        station_id = _seed_station(db_connection)
        model_id = _seed_model(db_connection)
        store = PgModelArtifactStore(db_connection, tmp_path)
        payload = b"direct_station_bytes"

        aid = store.store_artifact(
            model_id, payload, _T0, _T1, _T2, station_id=station_id
        )
        result = store.fetch_active_artifact_for_station(station_id, model_id)

        assert result is not None
        assert result[0] == aid
        assert result[1] == payload

    def test_fetch_active_artifact_for_station_group_fallback(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        station_id = _seed_station(db_connection)
        group_id = _seed_group(db_connection, station_id)
        model_id = _seed_model(db_connection, scope="group")
        store = PgModelArtifactStore(db_connection, tmp_path)
        payload = b"group_artifact_bytes"

        aid = store.store_artifact(model_id, payload, _T0, _T1, _T2, group_id=group_id)
        result = store.fetch_active_artifact_for_station(station_id, model_id)

        assert result is not None
        assert result[0] == aid
        assert result[1] == payload

    def test_fetch_active_artifact_for_station_direct_beats_group(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        station_id = _seed_station(db_connection)
        group_id = _seed_group(db_connection, station_id)
        model_id = _seed_model(db_connection)
        store = PgModelArtifactStore(db_connection, tmp_path)

        # Store group-level artifact, then supersede it and add station-level
        group_aid = store.store_artifact(
            model_id, b"group_bytes", _T0, _T1, _T2, group_id=group_id
        )
        store.transition_artifact_status(group_aid, ModelArtifactStatus.SUPERSEDED)

        station_aid = store.store_artifact(
            model_id, b"station_bytes", _T0, _T1, _T2, station_id=station_id
        )

        result = store.fetch_active_artifact_for_station(station_id, model_id)

        assert result is not None
        assert result[0] == station_aid
        assert result[1] == b"station_bytes"

    def test_fetch_artifact_record(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        station_id = _seed_station(db_connection)
        model_id = _seed_model(db_connection)
        store = PgModelArtifactStore(db_connection, tmp_path)

        aid = store.store_artifact(
            model_id, b"record_bytes", _T0, _T1, _T2, station_id=station_id
        )
        record = store.fetch_artifact_record(aid)

        assert record is not None
        assert record.id == aid
        assert record.model_id == model_id
        assert record.station_id == station_id
        assert record.group_id is None
        assert record.status == ModelArtifactStatus.ACTIVE
        assert record.training_period_start == _T0
        assert record.training_period_end == _T1
        assert record.trained_at == _T2
        assert record.promoted_at == _T2
        assert record.promoted_by is None
        assert record.superseded_at is None

    def test_fetch_artifact_record_nonexistent(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        store = PgModelArtifactStore(db_connection, tmp_path)
        assert store.fetch_artifact_record(ArtifactId(uuid.uuid4())) is None

    def test_fetch_artifacts_by_status(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        station_id = _seed_station(db_connection)
        model_id = _seed_model(db_connection)
        store = PgModelArtifactStore(db_connection, tmp_path)

        aid1 = store.store_artifact(
            model_id, b"a1", _T0, _T1, _T2, station_id=station_id
        )
        # Supersede aid1 before inserting aid2 (partial unique index enforces one active
        # per station+model scope)
        store.transition_artifact_status(aid1, ModelArtifactStatus.SUPERSEDED)
        aid2 = store.store_artifact(
            model_id, b"a2", _T0, _T1, _T2, station_id=station_id
        )

        active = store.fetch_artifacts_by_status(
            model_id, ModelArtifactStatus.ACTIVE, station_id=station_id
        )
        superseded = store.fetch_artifacts_by_status(
            model_id, ModelArtifactStatus.SUPERSEDED, station_id=station_id
        )

        assert aid2 in active
        assert aid1 not in active
        assert aid1 in superseded
        assert aid2 not in superseded

    def test_transition_status_to_superseded(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        station_id = _seed_station(db_connection)
        model_id = _seed_model(db_connection)
        store = PgModelArtifactStore(db_connection, tmp_path)

        aid = store.store_artifact(
            model_id, b"supersede_me", _T0, _T1, _T2, station_id=station_id
        )
        store.transition_artifact_status(aid, ModelArtifactStatus.SUPERSEDED)

        record = store.fetch_artifact_record(aid)
        assert record is not None
        assert record.status == ModelArtifactStatus.SUPERSEDED
        assert record.superseded_at is not None

    def test_transition_status_to_active_sets_timestamps(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        station_id = _seed_station(db_connection)
        model_id = _seed_model(db_connection)
        store = PgModelArtifactStore(db_connection, tmp_path)

        # Insert directly with training status to test promotion
        aid = ArtifactId(uuid.uuid4())
        artifact_path = tmp_path / model_id / f"{aid}.bin"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(b"pending")
        db_connection.execute(
            sa.insert(model_artifacts).values(
                id=aid,
                model_id=model_id,
                station_id=station_id,
                group_id=None,
                status="training",
                artifact_path=str(artifact_path),
                training_period_start=_T0,
                training_period_end=_T1,
                trained_at=_T2,
                promoted_at=None,
                promoted_by=None,
                superseded_at=None,
            )
        )

        store.transition_artifact_status(aid, ModelArtifactStatus.ACTIVE)

        record = store.fetch_artifact_record(aid)
        assert record is not None
        assert record.status == ModelArtifactStatus.ACTIVE
        assert record.promoted_at is not None
        assert record.superseded_at is None
