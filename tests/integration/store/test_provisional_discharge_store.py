from __future__ import annotations

from dataclasses import asdict, replace
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import (
    measurement_feed_evidence,
    observations,
    provisional_discharge_permissions,
    provisional_discharges,
    rating_curves,
    rating_reference_proofs,
)
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.provisional_discharge_store import PgProvisionalDischargeStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.rating_reference_store import PgRatingReferenceStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.rating_reference import (
    MeasurementFeedEvidence,
    RatingReferenceProof,
    canonical_content,
    content_digest,
    measurement_snapshot,
)
from tests.conftest import make_station_config
from tests.unit.services.test_provisional_discharge import NOW, convert, inputs

if TYPE_CHECKING:
    from sapphire_flow.types.ids import TenantId
    from sapphire_flow.types.observation import Observation
    from sapphire_flow.types.provisional_discharge import ProvisionalDischarge
    from sapphire_flow.types.rating_curve import RatingCurve


def seed_reference(
    conn: sa.Connection,
    table: sa.Table,
    evidence: MeasurementFeedEvidence | RatingReferenceProof,
) -> None:
    content = canonical_content(asdict(evidence))
    values = {
        name: getattr(evidence, name) for name in ("id", "tenant_id", "station_id")
    }
    parent = (
        "observation_id"
        if table.name == "measurement_feed_evidence"
        else "rating_curve_id"
    )
    values[parent] = getattr(evidence, parent)
    conn.execute(
        sa.insert(table).values(
            **values, content=content, fingerprint=content_digest(content)
        )
    )


def seed(
    conn: sa.Connection,
) -> tuple[Observation, RatingCurve, MeasurementFeedEvidence, RatingReferenceProof]:
    obs, curve, feed, proof = inputs()
    obs = replace(obs, id=uuid4())
    station = make_station_config(station_id=obs.station_id, code=str(uuid4()))
    feed = replace(
        feed,
        tenant_id=station.tenant_id,
        observation_id=obs.id,
        measurement=measurement_snapshot(obs),
    )
    proof = replace(proof, tenant_id=station.tenant_id)
    PgStationStore(conn).store_station(station)
    PgRatingCurveStore(conn).store_rating_curve(curve)
    PgObservationStore(conn).store_observations([obs])
    seed_reference(conn, measurement_feed_evidence, feed)
    seed_reference(conn, rating_reference_proofs, proof)
    return obs, curve, feed, proof


def permit_fixture(conn: sa.Connection, tenant_id: TenantId) -> None:
    # Disposable owner seed only. No application writer or production grant exists.
    conn.execute(
        sa.insert(provisional_discharge_permissions).values(
            tenant_id=tenant_id,
            state="enabled",
            permission_reference="fixture-only",
            inventory_digest="a" * 64,
        )
    )


def raw_values(result: ProvisionalDischarge) -> dict[str, object]:
    return {
        name: getattr(result, name)
        for name in (
            "fingerprint",
            "tenant_id",
            "station_id",
            "observation_id",
            "rating_curve_id",
            "feed_evidence_id",
            "reference_proof_id",
            "discharge",
            "content",
        )
    }


class TestProvisionalDischargeStore:
    def test_missing_gate_refuses_store_and_direct_sql(
        self, db_connection: sa.Connection
    ) -> None:
        args = seed(db_connection)
        result = convert(*args)
        with (
            pytest.raises(sa.exc.DBAPIError, match="activation is disabled"),
            db_connection.begin_nested(),
        ):
            PgProvisionalDischargeStore(db_connection).store_provisional_discharge(
                result, captured_at=NOW
            )
        with (
            pytest.raises(sa.exc.DBAPIError, match="activation is disabled"),
            db_connection.begin_nested(),
        ):
            db_connection.execute(
                sa.insert(provisional_discharges).values(
                    **raw_values(result), captured_at=NOW
                )
            )

    def test_idempotency_and_ordinary_read_isolation(
        self, db_connection: sa.Connection
    ) -> None:
        args = seed(db_connection)
        result = convert(*args)
        permit_fixture(db_connection, result.tenant_id)
        store = PgProvisionalDischargeStore(db_connection)
        first = store.store_provisional_discharge(result, captured_at=NOW)
        assert (
            store.store_provisional_discharge(
                result, captured_at=NOW + timedelta(hours=1)
            )
            == first
        )
        assert (
            db_connection.scalar(
                sa.select(sa.func.count()).select_from(provisional_discharges)
            )
            == 1
        )
        ordinary = PgObservationStore(db_connection).fetch_observations(
            result.station_id,
            "discharge",
            NOW - timedelta(days=1),
            NOW + timedelta(days=1),
        )
        assert ordinary == []
        assert db_connection.scalar(sa.select(observations.c.value)) == args[0].value

    def test_persisted_not_caller_qc_is_authority(
        self, db_connection: sa.Connection
    ) -> None:
        args = seed(db_connection)
        result = convert(*args)
        permit_fixture(db_connection, result.tenant_id)
        db_connection.execute(sa.update(observations).values(qc_status="raw"))
        with pytest.raises(ValueError, match="QC"):
            PgProvisionalDischargeStore(db_connection).store_provisional_discharge(
                result, captured_at=NOW
            )
        with (
            pytest.raises(sa.exc.DBAPIError, match="disagreement"),
            db_connection.begin_nested(),
        ):
            db_connection.execute(
                sa.insert(provisional_discharges).values(
                    **raw_values(result), captured_at=NOW
                )
            )

    def test_changed_qc_appends_without_reapproving_feed(
        self, db_connection: sa.Connection
    ) -> None:
        obs, curve, feed, proof = seed(db_connection)
        first = convert(obs, curve, feed, proof)
        permit_fixture(db_connection, first.tenant_id)
        store = PgProvisionalDischargeStore(db_connection)
        store.store_provisional_discharge(first, captured_at=NOW)
        db_connection.execute(
            sa.update(observations).values(qc_rule_version="level-v2")
        )
        second = convert(replace(obs, qc_rule_version="level-v2"), curve, feed, proof)
        store.store_provisional_discharge(second, captured_at=NOW)
        assert first.discharge == second.discharge
        assert first.fingerprint != second.fingerprint
        assert (
            db_connection.scalar(
                sa.select(sa.func.count()).select_from(provisional_discharges)
            )
            == 2
        )

    def test_restatement_requires_new_feed_evidence(
        self, db_connection: sa.Connection
    ) -> None:
        obs, curve, feed, proof = seed(db_connection)
        first = convert(obs, curve, feed, proof)
        permit_fixture(db_connection, first.tenant_id)
        store = PgProvisionalDischargeStore(db_connection)
        store.store_provisional_discharge(first, captured_at=NOW)
        db_connection.execute(sa.update(observations).values(value=1.6))
        with pytest.raises(ValueError, match="measurement"):
            store.store_provisional_discharge(first, captured_at=NOW)
        changed = replace(obs, value=1.6)
        new_feed = replace(feed, id=uuid4(), measurement=measurement_snapshot(changed))
        seed_reference(db_connection, measurement_feed_evidence, new_feed)
        store.store_provisional_discharge(
            convert(changed, curve, new_feed, proof), captured_at=NOW
        )
        assert (
            db_connection.scalar(
                sa.select(sa.func.count()).select_from(provisional_discharges)
            )
            == 2
        )

    def test_curve_restatement_refuses_old_proof(
        self, db_connection: sa.Connection
    ) -> None:
        args = seed(db_connection)
        result = convert(*args)
        permit_fixture(db_connection, result.tenant_id)
        db_connection.execute(sa.update(rating_curves).values(version=2))
        with pytest.raises(ValueError, match="curve"):
            PgProvisionalDischargeStore(db_connection).store_provisional_discharge(
                result, captured_at=NOW
            )
        with (
            pytest.raises(sa.exc.DBAPIError, match="disagreement"),
            db_connection.begin_nested(),
        ):
            db_connection.execute(
                sa.insert(provisional_discharges).values(
                    **raw_values(result), captured_at=NOW
                )
            )

    @pytest.mark.parametrize(
        "table",
        [
            measurement_feed_evidence,
            rating_reference_proofs,
            provisional_discharges,
            provisional_discharge_permissions,
        ],
    )
    @pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
    def test_owner_mutation_refused(
        self, db_connection: sa.Connection, table: sa.Table, operation: str
    ) -> None:
        args = seed(db_connection)
        result = convert(*args)
        permit_fixture(db_connection, result.tenant_id)
        PgProvisionalDischargeStore(db_connection).store_provisional_discharge(
            result, captured_at=NOW
        )
        sql = (
            f"DELETE FROM {table.name}"
            if operation == "DELETE"
            else f"UPDATE {table.name} SET "
            f"{list(table.c)[0].name} = {list(table.c)[0].name}"
        )
        with pytest.raises(sa.exc.DBAPIError, match="immutable"):
            db_connection.execute(sa.text(sql))


class TestRatingReferenceStore:
    def test_reads_exact_protected_evidence(self, db_connection: sa.Connection) -> None:
        _, _, feed, proof = seed(db_connection)
        store = PgRatingReferenceStore(db_connection)
        assert store.fetch_feed_evidence(feed.id) == feed
        assert store.fetch_reference_proof(proof.id) == proof
        assert store.fetch_reference_proof(uuid4()) is None

    def test_foreign_tenant_fails_composite_constraint(
        self, db_connection: sa.Connection
    ) -> None:
        _, _, feed, _ = seed(db_connection)
        foreign = replace(feed, id=uuid4(), tenant_id=uuid4())
        with pytest.raises(sa.exc.IntegrityError, match="foreign key"):
            seed_reference(db_connection, measurement_feed_evidence, foreign)

    def test_fingerprint_disagreement_fails_sql(
        self, db_connection: sa.Connection
    ) -> None:
        args = seed(db_connection)
        result = convert(*args)
        permit_fixture(db_connection, result.tenant_id)
        values = raw_values(result) | {"fingerprint": "0" * 64}
        with pytest.raises(sa.exc.IntegrityError, match="digest"):
            db_connection.execute(
                sa.insert(provisional_discharges).values(**values, captured_at=NOW)
            )

    def test_changed_proof_same_discharge_appends(
        self, db_connection: sa.Connection
    ) -> None:
        obs, curve, feed, proof = seed(db_connection)
        first = convert(obs, curve, feed, proof)
        permit_fixture(db_connection, first.tenant_id)
        store = PgProvisionalDischargeStore(db_connection)
        store.store_provisional_discharge(first, captured_at=NOW)
        revised_proof = replace(proof, id=uuid4(), evidence_reference="new-evidence")
        seed_reference(db_connection, rating_reference_proofs, revised_proof)
        second = convert(obs, curve, feed, revised_proof)
        store.store_provisional_discharge(second, captured_at=NOW)
        assert first.discharge == second.discharge
        assert first.fingerprint != second.fingerprint
        db_connection.execute(
            sa.update(observations).values(value=9.0, qc_status="raw")
        )
        assert store.fetch_provisional_discharge(first.fingerprint) == first
        assert store.fetch_provisional_discharge(second.fingerprint) == second

    def test_changed_curve_same_output_appends(
        self, db_connection: sa.Connection
    ) -> None:
        from sapphire_flow.types.rating_reference import curve_snapshot

        obs, curve, feed, proof = seed(db_connection)
        first = convert(obs, curve, feed, proof)
        permit_fixture(db_connection, first.tenant_id)
        store = PgProvisionalDischargeStore(db_connection)
        store.store_provisional_discharge(first, captured_at=NOW)
        revised_curve = replace(curve, version=2)
        db_connection.execute(sa.update(rating_curves).values(version=2))
        revised_proof = replace(proof, id=uuid4(), curve=curve_snapshot(revised_curve))
        seed_reference(db_connection, rating_reference_proofs, revised_proof)
        second = convert(obs, revised_curve, feed, revised_proof)
        store.store_provisional_discharge(second, captured_at=NOW)
        assert first.discharge == second.discharge
        assert first.fingerprint != second.fingerprint
        assert store.fetch_provisional_discharge(first.fingerprint) == first

    def test_explicit_disabled_gate_refuses(self, db_connection: sa.Connection) -> None:
        result = convert(*seed(db_connection))
        db_connection.execute(
            sa.insert(provisional_discharge_permissions).values(
                tenant_id=result.tenant_id
            )
        )
        with pytest.raises(sa.exc.DBAPIError, match="activation is disabled"):
            PgProvisionalDischargeStore(db_connection).store_provisional_discharge(
                result, captured_at=NOW
            )

    def test_direct_sql_cannot_substitute_proof(
        self, db_connection: sa.Connection
    ) -> None:
        obs, curve, feed, proof = seed(db_connection)
        first = convert(obs, curve, feed, proof)
        permit_fixture(db_connection, first.tenant_id)
        second_proof = replace(proof, id=uuid4(), evidence_reference="other-proof")
        seed_reference(db_connection, rating_reference_proofs, second_proof)
        values = raw_values(first) | {"reference_proof_id": second_proof.id}
        with pytest.raises(sa.exc.DBAPIError, match="disagreement"):
            db_connection.execute(
                sa.insert(provisional_discharges).values(**values, captured_at=NOW)
            )


class TestProvisionalWritePermission:
    def test_owner_can_disable_but_not_reenable(
        self, db_connection: sa.Connection
    ) -> None:
        result = convert(*seed(db_connection))
        permit_fixture(db_connection, result.tenant_id)
        store = PgProvisionalDischargeStore(db_connection)
        store.store_provisional_discharge(result, captured_at=NOW)
        db_connection.execute(
            sa.update(provisional_discharge_permissions).values(state="disabled")
        )
        with (
            pytest.raises(sa.exc.DBAPIError, match="activation is disabled"),
            db_connection.begin_nested(),
        ):
            store.store_provisional_discharge(result, captured_at=NOW)
        with (
            pytest.raises(sa.exc.DBAPIError, match="immutable"),
            db_connection.begin_nested(),
        ):
            db_connection.execute(
                sa.update(provisional_discharge_permissions).values(state="enabled")
            )
        assert (
            db_connection.scalar(sa.select(provisional_discharge_permissions.c.state))
            == "disabled"
        )

    def test_disable_cannot_rewrite_permission_metadata(
        self, db_connection: sa.Connection
    ) -> None:
        result = convert(*seed(db_connection))
        permit_fixture(db_connection, result.tenant_id)
        with pytest.raises(sa.exc.DBAPIError, match="immutable"):
            db_connection.execute(
                sa.update(provisional_discharge_permissions).values(
                    state="disabled", permission_reference="changed-proof"
                )
            )

    @pytest.mark.parametrize("writer", ["store", "sql"])
    def test_future_capture_cannot_expire_a_current_curve(
        self,
        db_connection: sa.Connection,
        writer: str,
    ) -> None:
        from sapphire_flow.services.provisional_discharge import (
            convert_provisional_discharge,
        )
        from sapphire_flow.types.rating_reference import curve_snapshot

        obs, curve, feed, proof = seed(db_connection)
        actual_now = db_connection.scalar(sa.select(sa.func.clock_timestamp()))
        current_curve = replace(curve, valid_to=actual_now + timedelta(days=1))
        db_connection.execute(
            sa.update(rating_curves).values(valid_to=current_curve.valid_to)
        )
        current_proof = replace(proof, id=uuid4(), curve=curve_snapshot(current_curve))
        seed_reference(db_connection, rating_reference_proofs, current_proof)
        future_capture = actual_now + timedelta(days=2)
        result = convert_provisional_discharge(
            observation=obs,
            curves=[current_curve],
            feed_evidence=feed,
            reference_proof=current_proof,
            at=future_capture,
        )
        permit_fixture(db_connection, result.tenant_id)
        if writer == "store":
            with pytest.raises(ValueError, match="future"):
                PgProvisionalDischargeStore(db_connection).store_provisional_discharge(
                    result, captured_at=future_capture
                )
        else:
            with (
                pytest.raises(sa.exc.DBAPIError, match="future"),
                db_connection.begin_nested(),
            ):
                db_connection.execute(
                    sa.insert(provisional_discharges).values(
                        **raw_values(result), captured_at=future_capture
                    )
                )
        assert (
            db_connection.scalar(sa.select(rating_curves.c.valid_to))
            == current_curve.valid_to
        )

    def test_current_database_capture_is_allowed(
        self, db_connection: sa.Connection
    ) -> None:
        result = convert(*seed(db_connection))
        permit_fixture(db_connection, result.tenant_id)
        actual_now = db_connection.scalar(sa.select(sa.func.clock_timestamp()))
        assert (
            PgProvisionalDischargeStore(db_connection).store_provisional_discharge(
                result, captured_at=actual_now
            )
            == result.fingerprint
        )
