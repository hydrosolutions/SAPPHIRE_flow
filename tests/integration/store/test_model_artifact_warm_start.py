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
    resolve_donor_config,
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


class TestRetrainOfARetrain:
    """Plan 405 T1 — a retrain of a retrain (generation 2), which Plan 399 D1
    explicitly permits.

    🔴 The inherited branch of `resolve_donor_config` carries the donor's path and
    hash forward but DROPS `base_config_unknown_reason`, so a NULL path arrives
    with no reason and `WarmStartRecord` rejects it — *after* the new artifact has
    already been stored, leaving a saved model with no provenance.

    ⚠️ The generation-1 donor is pinned to a NULL config path DELIBERATELY. Once
    T2 records a real installed path the inherited branch stops returning NULL, and
    a test that did not force this case would silently stop exercising the branch it
    exists for.
    """

    @staticmethod
    def _gen1_as_the_flow_writes_it(
        conn: sa.Connection, child: ArtifactId, base: ArtifactId
    ) -> None:
        """Exactly the shape `_record_warm_start_provenance` produces today: the
        flow always passes `installed_config_path=None`, so an imported donor
        yields a NULL path WITH a reason."""
        record_warm_start(
            conn,
            WarmStartRecord(
                artifact_id=child,
                base_artifact_id=base,
                run_config={"finetuning": {"strategy": "last_layer"}},
                base_config_path=None,
                base_config_sha256="a" * 64,
                base_config_unknown_reason=(
                    "donor's config hash is recorded in its provenance, but no "
                    "installed config path was supplied to pair with it"
                ),
                base_params_path=None,
                base_params_unknown_reason="donor was imported, not trained by SAP3",
            ),
        )

    def test_resolving_a_retrained_donor_keeps_a_reason_for_its_null_path(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """The defect, at the resolver: a NULL path must never come back
        unexplained."""
        mid = _seed_model(db_connection)
        sid = _seed_station(db_connection)
        gen0 = _seed_artifact(db_connection, tmp_path, mid, sid)
        gen1 = _seed_artifact(db_connection, tmp_path, mid, sid)
        self._gen1_as_the_flow_writes_it(db_connection, gen1, gen0)

        path, sha256, reason = resolve_donor_config(
            db_connection, gen1, installed_config_path=None
        )

        assert path is None, "gen 1 has no config path, so gen 2 inherits none"
        assert sha256 == "a" * 64
        assert reason, (
            "a NULL path MUST carry a reason — returning None here is what makes "
            "the next WarmStartRecord unconstructable, after its artifact is stored"
        )

    def test_the_reason_describes_the_immediate_donor_not_an_ancestor(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """🔴 And the reason must be TRUE of generation 1.

        Generation 1 is SAP3-produced and has NO `model_artifact_provenance` row —
        that is precisely why the inherited branch is taken. So propagating the
        imported ancestor's sentence verbatim asserts something false about it.
        """
        mid = _seed_model(db_connection)
        sid = _seed_station(db_connection)
        gen0 = _seed_artifact(db_connection, tmp_path, mid, sid)
        gen1 = _seed_artifact(db_connection, tmp_path, mid, sid)
        self._gen1_as_the_flow_writes_it(db_connection, gen1, gen0)

        _, _, reason = resolve_donor_config(
            db_connection, gen1, installed_config_path=None
        )

        assert reason is not None
        assert "recorded in its provenance" not in reason, (
            "gen 1 has no provenance row; inheriting the imported ancestor's "
            "sentence verbatim states something false about the immediate donor"
        )
        # 🔴 POSITIVE content — ⛔ a placeholder like "unknown" or "TODO" must NOT
        # pass. The reason has to say what is actually true of THIS donor: that it
        # is SAP3-produced and its own record carries no path.
        assert "produced by SAP3" in reason
        assert "carries no config path" in reason

    def test_generation_two_record_is_constructable(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """The consequence the flow actually hits: build the gen-2 record from
        exactly what the resolver returned."""
        mid = _seed_model(db_connection)
        sid = _seed_station(db_connection)
        gen0 = _seed_artifact(db_connection, tmp_path, mid, sid)
        gen1 = _seed_artifact(db_connection, tmp_path, mid, sid)
        gen2 = _seed_artifact(db_connection, tmp_path, mid, sid)
        self._gen1_as_the_flow_writes_it(db_connection, gen1, gen0)

        path, sha256, reason = resolve_donor_config(
            db_connection, gen1, installed_config_path=None
        )
        # ⛔ No try/except: an exception here IS the defect.
        record_warm_start(
            db_connection,
            WarmStartRecord(
                artifact_id=gen2,
                base_artifact_id=gen1,
                run_config={},
                base_config_path=path,
                base_config_sha256=sha256,
                base_config_unknown_reason=reason,
                base_params_path=None,
                base_params_unknown_reason="donor is SAP3-produced",
            ),
        )

        got = fetch_warm_start(db_connection, gen2)
        assert got is not None
        assert got.base_artifact_id == gen1

    def test_generation_three_also_carries_a_true_reason(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """⛔ A fix that survives only one more generation is a postponement.
        And "completes" alone proves nothing — any non-empty placeholder satisfies
        the invariant — so this asserts the CONTENT."""
        mid = _seed_model(db_connection)
        sid = _seed_station(db_connection)
        gen0 = _seed_artifact(db_connection, tmp_path, mid, sid)
        gen1 = _seed_artifact(db_connection, tmp_path, mid, sid)
        gen2 = _seed_artifact(db_connection, tmp_path, mid, sid)
        gen3 = _seed_artifact(db_connection, tmp_path, mid, sid)
        self._gen1_as_the_flow_writes_it(db_connection, gen1, gen0)

        for child, donor in ((gen2, gen1), (gen3, gen2)):
            path, sha256, reason = resolve_donor_config(
                db_connection, donor, installed_config_path=None
            )
            assert reason, f"a NULL path with no reason at {child} <- {donor}"
            assert "recorded in its provenance" not in reason
            # 🔴 POSITIVE, per generation — a placeholder must not survive here
            # either, which is what "completes" alone would have allowed.
            assert "produced by SAP3" in reason
            assert "carries no config path" in reason
            record_warm_start(
                db_connection,
                WarmStartRecord(
                    artifact_id=child,
                    base_artifact_id=donor,
                    run_config={},
                    base_config_path=path,
                    base_config_sha256=sha256,
                    base_config_unknown_reason=reason,
                    base_params_path=None,
                    base_params_unknown_reason="donor is SAP3-produced",
                ),
            )

        got = fetch_warm_start(db_connection, gen3)
        assert got is not None
        assert got.base_artifact_id == gen2
        assert got.base_config_unknown_reason

    def test_a_donor_with_a_config_path_inherits_it_and_owes_no_reason(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """🔴 The other half of "only when the inherited path is NULL".

        ⚠️ T2 makes this the production-NORMAL branch (it starts recording a real
        installed path), so leaving it untested would mean the untested branch is
        the common one exactly when it starts mattering. A record must never carry
        BOTH a path and an "unknown" reason — `__post_init__` does not catch that.
        """
        mid = _seed_model(db_connection)
        sid = _seed_station(db_connection)
        gen0 = _seed_artifact(db_connection, tmp_path, mid, sid)
        gen1 = _seed_artifact(db_connection, tmp_path, mid, sid)
        record_warm_start(
            db_connection,
            WarmStartRecord(
                artifact_id=gen1,
                base_artifact_id=gen0,
                run_config={},
                base_config_path="models/aquacast/configs/cmal_small.yaml",
                base_config_sha256="b" * 64,
                base_params_path=None,
                base_params_unknown_reason="donor was imported",
            ),
        )

        path, sha256, reason = resolve_donor_config(
            db_connection, gen1, installed_config_path=None
        )

        assert path == "models/aquacast/configs/cmal_small.yaml"
        assert sha256 == "b" * 64
        assert reason is None, (
            "nothing is missing, so no reason is owed — a path AND an 'unknown' "
            "reason together is a record the invariant cannot catch"
        )
