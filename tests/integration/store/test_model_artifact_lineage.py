"""Plan 120 Task 2D — train-time lineage write wiring.

Covers the helper's discriminating verification points (a)/(b)/(d)/(e)/(f)
directly against a real DB — (c) (flow wiring) and (g) (the D-UP upstream
gate) are covered by `tests/unit/flows/test_train_models.py` /
`tests/unit/flows/test_onboard_model_flow.py` and
`tests/unit/services/test_training_data.py` respectively.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from shapely.geometry import MultiPolygon, Polygon

if TYPE_CHECKING:
    from pathlib import Path

from sapphire_flow.db.metadata import (
    basin_versions,
    model_artifact_basin_versions,
    models,
    station_group_members,
    station_groups,
    stations,
)
from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.store.model_artifact_lineage import (
    PgArtifactLineageWriter,
    record_artifact_basin_lineage,
)
from sapphire_flow.store.model_artifact_store import PgModelArtifactStore
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import (
    ArtifactId,
    BasinId,
    ModelId,
    StationGroupId,
    StationId,
)
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

_T0 = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_T1 = ensure_utc(datetime(2025, 6, 1, tzinfo=UTC))
_T2 = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))

_GEOM = MultiPolygon(
    [Polygon([(7.0, 46.0), (8.0, 46.0), (8.0, 47.0), (7.0, 47.0), (7.0, 46.0)])]
)


def _seed_basin(conn: sa.Connection) -> BasinId:
    basin = Basin(
        id=BasinId(uuid.uuid4()),
        code=f"LINEAGE-{uuid.uuid4().hex[:8]}",
        name="Lineage Test Basin",
        geometry=_GEOM,
        area_km2=42.0,
        attributes=None,
        regional_basin=None,
        band_geometries=None,
        created_at=_T0,
        network="dhm",
    )
    PgBasinStore(conn).store_basin(basin)
    return basin.id


def _seed_station(conn: sa.Connection, basin_id: BasinId | None) -> StationId:
    sid = StationId(uuid.uuid4())
    conn.execute(
        sa.insert(stations).values(
            id=sid,
            code=f"LINEAGE-STA-{sid.hex[:6]}",
            name="Lineage Test Station",
            location="SRID=4326;POINT(7.5 46.5)",
            station_kind="river",
            basin_id=basin_id,
            network="dhm",
            timezone="Asia/Kathmandu",
            measured_parameters=["discharge"],
            ownership="own",
            tenant_id=DEFAULT_TENANT_ID,
        )
    )
    return sid


def _seed_model(conn: sa.Connection, scope: str = "station") -> ModelId:
    mid = ModelId(f"lineage_test_model_{uuid.uuid4().hex[:8]}")
    conn.execute(
        sa.insert(models).values(
            id=mid,
            display_name="Lineage Test Model",
            artifact_scope=scope,
            description="Integration test",
        )
    )
    return mid


def _seed_group(conn: sa.Connection, station_ids: list[StationId]) -> StationGroupId:
    gid = StationGroupId(uuid.uuid4())
    conn.execute(
        sa.insert(station_groups).values(
            id=gid, name=f"grp-{gid.hex[:6]}", tenant_id=DEFAULT_TENANT_ID
        )
    )
    for sid in station_ids:
        conn.execute(
            sa.insert(station_group_members).values(
                group_id=gid, station_id=sid, tenant_id=DEFAULT_TENANT_ID
            )
        )
    return gid


def _seed_artifact(
    conn: sa.Connection,
    tmp_path: Path,
    model_id: ModelId,
    *,
    station_id: StationId | None = None,
    group_id: StationGroupId | None = None,
) -> ArtifactId:
    store = PgModelArtifactStore(conn, tmp_path)
    aid, _ = store.store_artifact(
        model_id,
        b"payload",
        _T0,
        _T1,
        _T2,
        station_id=station_id,
        group_id=group_id,
    )
    return aid


def _lineage_basin_version_ids(
    conn: sa.Connection, artifact_id: ArtifactId
) -> list[uuid.UUID]:
    rows = conn.execute(
        sa.select(model_artifact_basin_versions.c.basin_version_id).where(
            model_artifact_basin_versions.c.model_artifact_id == artifact_id
        )
    ).all()
    return [row[0] for row in rows]


class TestLineageWriteHelper:
    def test_station_scoped_artifact_writes_one_row(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        basin_id = _seed_basin(db_connection)
        station_id = _seed_station(db_connection, basin_id)
        model_id = _seed_model(db_connection)
        artifact_id = _seed_artifact(
            db_connection, tmp_path, model_id, station_id=station_id
        )

        record_artifact_basin_lineage(db_connection, artifact_id, {station_id})

        rows = _lineage_basin_version_ids(db_connection, artifact_id)
        assert len(rows) == 1

        current_version_id = db_connection.execute(
            sa.select(basin_versions.c.id).where(
                sa.and_(
                    basin_versions.c.basin_id == basin_id,
                    basin_versions.c.superseded_at.is_(None),
                )
            )
        ).scalar_one()
        assert rows[0] == current_version_id

    def test_group_scoped_artifact_writes_only_trained_subset(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """(b): a group with N members where one was SKIPPED (no usable
        data) must write N-1 rows, not N — the helper must be called with
        the trained subset, not full group membership."""
        basin_a = _seed_basin(db_connection)
        basin_b = _seed_basin(db_connection)
        station_a = _seed_station(db_connection, basin_a)
        station_b = _seed_station(db_connection, basin_b)
        station_skipped = _seed_station(db_connection, _seed_basin(db_connection))
        model_id = _seed_model(db_connection, scope="group")
        group_id = _seed_group(db_connection, [station_a, station_b, station_skipped])
        artifact_id = _seed_artifact(
            db_connection, tmp_path, model_id, group_id=group_id
        )

        # Trained subset excludes station_skipped (simulating
        # GroupTrainingData.station_ids, the post-skip subset).
        record_artifact_basin_lineage(
            db_connection, artifact_id, (station_a, station_b)
        )

        rows = _lineage_basin_version_ids(db_connection, artifact_id)
        assert len(rows) == 2

    def test_legacy_basin_resolves_to_version_1(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """(d): a station on a legacy (pre-120, backfilled) basin writes a
        lineage row pointing at that basin's version=1 row. `store_basin`
        (Task 0A) always writes version=1 for a fresh basin, so this is
        exercised the same way as any other basin creation."""
        basin_id = _seed_basin(db_connection)
        station_id = _seed_station(db_connection, basin_id)
        model_id = _seed_model(db_connection)
        artifact_id = _seed_artifact(
            db_connection, tmp_path, model_id, station_id=station_id
        )

        record_artifact_basin_lineage(db_connection, artifact_id, {station_id})

        version_row = db_connection.execute(
            sa.select(basin_versions.c.version).where(
                basin_versions.c.basin_id == basin_id
            )
        ).scalar_one()
        assert version_row == 1

    def test_null_basin_id_skips_without_raising(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """(e): a no-static-feature model on a station with basin_id IS
        NULL trains and the helper SKIPS the lineage row without raising."""
        station_id = _seed_station(db_connection, None)
        model_id = _seed_model(db_connection)
        artifact_id = _seed_artifact(
            db_connection, tmp_path, model_id, station_id=station_id
        )

        record_artifact_basin_lineage(db_connection, artifact_id, {station_id})

        assert _lineage_basin_version_ids(db_connection, artifact_id) == []

    def test_basin_with_no_current_version_raises(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """(f): a station whose basin has no current basin_versions row
        (Task 0A invariant violated) makes the helper RAISE with a clear
        message — proving the NULL-vs-dangling split, not a blanket skip."""
        basin_id = _seed_basin(db_connection)
        station_id = _seed_station(db_connection, basin_id)
        model_id = _seed_model(db_connection)
        artifact_id = _seed_artifact(
            db_connection, tmp_path, model_id, station_id=station_id
        )

        # Simulate a corrupted/violated invariant: no current basin_versions
        # row survives for this basin.
        db_connection.execute(
            sa.update(basin_versions)
            .where(basin_versions.c.basin_id == basin_id)
            .values(superseded_at=_T0)
        )

        with pytest.raises(ValueError, match="no current basin_versions row"):
            record_artifact_basin_lineage(db_connection, artifact_id, {station_id})

    def test_unknown_station_raises(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        # A real station backs the artifact itself (satisfies the
        # model_artifacts scope-xor constraint); the lineage call is passed
        # an UNRELATED, never-seeded station id to exercise the "station not
        # found" branch.
        basin_id = _seed_basin(db_connection)
        seeded_station_id = _seed_station(db_connection, basin_id)
        model_id = _seed_model(db_connection)
        artifact_id = _seed_artifact(
            db_connection, tmp_path, model_id, station_id=seeded_station_id
        )

        with pytest.raises(ValueError, match="not found"):
            record_artifact_basin_lineage(
                db_connection, artifact_id, {StationId(uuid.uuid4())}
            )

    def test_pg_artifact_lineage_writer_delegates(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        basin_id = _seed_basin(db_connection)
        station_id = _seed_station(db_connection, basin_id)
        model_id = _seed_model(db_connection)
        artifact_id = _seed_artifact(
            db_connection, tmp_path, model_id, station_id=station_id
        )

        PgArtifactLineageWriter(db_connection).record(artifact_id, {station_id})

        assert len(_lineage_basin_version_ids(db_connection, artifact_id)) == 1
