"""Plan 399 T4 — warm-start provenance, against a real database.

The two behaviours that can only be proven here:

* **RESTRICT on the donor.** Deleting a base artifact something was fine-tuned
  from is REFUSED. ⛔ A `CASCADE` would delete this row instead — which passes a
  naive "no orphan remains" check while destroying the only answer to "what was
  this fine-tuned from?", the question the table exists for.
* **Survival across supersession.** Supersession marks status, it does not
  delete, so the lineage must still resolve afterwards.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa

if TYPE_CHECKING:
    from pathlib import Path

from sapphire_flow.db.metadata import model_artifacts, models, stations
from sapphire_flow.store.model_artifact_store import PgModelArtifactStore
from sapphire_flow.store.model_artifact_warm_start import (
    WarmStartRecord,
    fetch_warm_start,
    record_warm_start,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ModelArtifactStatus
from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

_T0 = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
_T1 = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
_T2 = ensure_utc(datetime(2026, 9, 26, tzinfo=UTC))


def _seed_model(conn: sa.Connection) -> ModelId:
    mid = ModelId(f"warm_start_test_{uuid.uuid4().hex[:8]}")
    conn.execute(
        sa.insert(models).values(
            id=mid,
            display_name="Warm Start Test Model",
            artifact_scope="station",
            description="Integration test",
        )
    )
    return mid


def _seed_station(conn: sa.Connection) -> StationId:
    sid = StationId(uuid.uuid4())
    conn.execute(
        sa.insert(stations).values(
            id=sid,
            code=f"WS-STA-{sid.hex[:6]}",
            name="Warm Start Test Station",
            location="SRID=4326;POINT(7.5 46.5)",
            station_kind="river",
            network="bafu",
            timezone="Europe/Zurich",
            measured_parameters=["discharge"],
            ownership="own",
            tenant_id=DEFAULT_TENANT_ID,
        )
    )
    return sid


def _seed_artifact(
    conn: sa.Connection, tmp_path: Path, model_id: ModelId, station_id: StationId
) -> ArtifactId:
    # `ck_model_artifacts_scope_xor` requires EXACTLY ONE of station_id/group_id.
    # ⚠️ The FAKE store does not enforce that, so an artifact seeded with neither
    # passes every unit test and fails only against a real database — which is
    # exactly what happened here.
    aid, _ = PgModelArtifactStore(conn, tmp_path).store_artifact(
        model_id, b"payload", _T0, _T1, _T2, station_id=station_id
    )
    return aid


def _record(child: ArtifactId, base: ArtifactId) -> WarmStartRecord:
    return WarmStartRecord(
        artifact_id=child,
        base_artifact_id=base,
        run_config={"finetuning": {"strategy": "last_layer", "lr": 0.0001}},
        base_config_path="models/aquacast/configs/cmal_small.yaml",
        base_config_sha256="d" * 64,
        base_params_path=None,
        base_params_unknown_reason="donor was imported, not trained by SAP3",
    )


class TestWarmStartProvenance:
    def test_a_record_round_trips_with_its_run_config(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        mid = _seed_model(db_connection)
        sid = _seed_station(db_connection)
        base = _seed_artifact(db_connection, tmp_path, mid, sid)
        child = _seed_artifact(db_connection, tmp_path, mid, sid)

        record_warm_start(db_connection, _record(child, base))
        got = fetch_warm_start(db_connection, child)

        assert got is not None
        assert got.base_artifact_id == base
        # D3's condition: which strategy produced this artifact is answerable.
        assert got.run_config == {
            "finetuning": {"strategy": "last_layer", "lr": 0.0001}
        }
        assert got.base_params_path is None
        assert got.base_params_unknown_reason

    def test_a_freshly_trained_artifact_has_no_record(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        mid = _seed_model(db_connection)
        sid = _seed_station(db_connection)
        fresh = _seed_artifact(db_connection, tmp_path, mid, sid)
        assert fetch_warm_start(db_connection, fresh) is None

    def test_deleting_a_referenced_donor_is_refused(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """🔴 RESTRICT, not CASCADE. A cascade would silently delete the child's
        provenance and pass a 'no orphan remains' test."""
        mid = _seed_model(db_connection)
        sid = _seed_station(db_connection)
        base = _seed_artifact(db_connection, tmp_path, mid, sid)
        child = _seed_artifact(db_connection, tmp_path, mid, sid)
        record_warm_start(db_connection, _record(child, base))

        with pytest.raises(sa.exc.IntegrityError):
            db_connection.execute(
                sa.delete(model_artifacts).where(model_artifacts.c.id == base)
            )
        db_connection.rollback()

    def test_the_lineage_survives_superseding_the_donor(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """Supersession MARKS, it does not delete — so the record must resolve."""
        mid = _seed_model(db_connection)
        sid = _seed_station(db_connection)
        base = _seed_artifact(db_connection, tmp_path, mid, sid)
        child = _seed_artifact(db_connection, tmp_path, mid, sid)
        record_warm_start(db_connection, _record(child, base))

        PgModelArtifactStore(db_connection, tmp_path).transition_artifact_status(
            base, ModelArtifactStatus.SUPERSEDED
        )

        got = fetch_warm_start(db_connection, child)
        assert got is not None
        assert got.base_artifact_id == base
        # …and the donor row itself is still there to resolve to.
        row = db_connection.execute(
            sa.select(model_artifacts.c.status).where(model_artifacts.c.id == base)
        ).scalar_one()
        assert row == ModelArtifactStatus.SUPERSEDED.value
