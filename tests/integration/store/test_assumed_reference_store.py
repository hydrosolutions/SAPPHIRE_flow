from dataclasses import asdict, replace
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import rating_reference_proofs
from sapphire_flow.services.provisional_discharge import convert_reference_discharge
from sapphire_flow.store.provisional_discharge_store import PgProvisionalDischargeStore
from sapphire_flow.store.rating_reference_store import (
    PgProtectedReferenceWriter,
    PgRatingReferenceStore,
)
from sapphire_flow.types.ids import RatingReferenceProofId
from sapphire_flow.types.rating_reference import (
    OwnerAssumedZeroReference,
    canonical_content,
    content_digest,
)
from tests.integration.store.test_provisional_discharge_store import (
    NOW,
    permit_fixture,
    seed,
)
from tests.unit.types.test_rating_reference import assumed_reference


class TestAssumedReferenceStore:
    def test_roundtrip_conversion_and_idempotent_append(
        self, db_connection: sa.Connection
    ) -> None:
        obs, curve, feed, proof = seed(db_connection)
        reference = replace(
            assumed_reference(proof), id=RatingReferenceProofId(uuid4())
        )
        writer = PgProtectedReferenceWriter(db_connection)
        assert writer.append_reference_evidence(reference) == reference.id
        assert writer.append_reference_evidence(reference) == reference.id
        reader = PgRatingReferenceStore(db_connection)
        assert reader.fetch_reference_evidence(reference.id) == reference
        with pytest.raises(ValueError, match="verified reference"):
            reader.fetch_reference_proof(reference.id)
        permit_fixture(db_connection, feed.tenant_id)
        result = convert_reference_discharge(
            observation=obs,
            curves=[curve],
            feed_evidence=feed,
            reference_evidence=reference,
            at=NOW,
        )
        store = PgProvisionalDischargeStore(db_connection)
        assert (
            store.store_provisional_discharge(result, captured_at=NOW)
            == result.fingerprint
        )
        assert store.fetch_provisional_discharge(result.fingerprint) == result

    def test_same_identity_changed_authority_refused(
        self, db_connection: sa.Connection
    ) -> None:
        proof = seed(db_connection)[3]
        reference = replace(
            assumed_reference(proof), id=RatingReferenceProofId(uuid4())
        )
        writer = PgProtectedReferenceWriter(db_connection)
        writer.append_reference_evidence(reference)
        with pytest.raises(ValueError, match="different content"):
            writer.append_reference_evidence(
                replace(reference, authority_reference="fixture:other")
            )

    @pytest.mark.parametrize(
        "field,value,message",
        [
            ("offset_m", 0, "malformed reference basis payload"),
            ("verified_by", "owner", "malformed reference basis payload"),
            ("basis", "verified", "reference basis disagreement"),
            ("assumed_at", "2026-10-01T00:00:00", "aware nonfuture time"),
            ("assumed_at", "2026-10-01T00:00:00Z", "aware nonfuture time"),
            ("assumed_at", "2026-10-01T05:45:00+05:45", "aware nonfuture time"),
            ("binding_at", "2026-09-01T00:00:00+00:00", "predates owner authority"),
            ("curve_unit_evidence_reference", " ", "missing attributed assumption"),
            ("authority_sha256", "A" * 64, "invalid assumption evidence digest"),
            ("api_station_id", "1", "invalid assumption feed identity"),
        ]
        + [
            (
                "endpoint",
                "https://example.invalid/levels/" + whitespace,
                "invalid assumption feed identity",
            )
            for whitespace in ("\u00a0", "\u2007", "\u3000", "\u001c")
        ]
        + [
            (field, whitespace, "missing attributed assumption")
            for field in (
                "assumed_by",
                "authority_reference",
                "binding_by",
                "binding_reference",
                "level_unit_evidence_reference",
                "curve_unit_evidence_reference",
            )
            for whitespace in ("\t\n", "\u001c\u00a0\u2007")
        ],
    )
    def test_direct_sql_rejects_malformed_assumption(
        self, db_connection: sa.Connection, field: str, value: object, message: str
    ) -> None:
        proof = seed(db_connection)[3]
        reference = replace(
            assumed_reference(proof), id=RatingReferenceProofId(uuid4())
        )
        data = asdict(reference)
        data[field] = value
        content = canonical_content(data)
        with pytest.raises(sa.exc.DBAPIError, match=message):
            db_connection.execute(
                sa.insert(rating_reference_proofs).values(
                    id=reference.id,
                    tenant_id=reference.tenant_id,
                    station_id=reference.station_id,
                    rating_curve_id=reference.rating_curve_id,
                    reference_basis="owner_assumed_same_zero",
                    content=content,
                    fingerprint=content_digest(content),
                )
            )

    def test_missing_explicit_basis_refused(self, db_connection: sa.Connection) -> None:
        proof = replace(seed(db_connection)[3], id=RatingReferenceProofId(uuid4()))
        content = canonical_content(asdict(proof))
        with pytest.raises(sa.exc.DBAPIError, match="basis"):
            db_connection.execute(
                sa.insert(rating_reference_proofs).values(
                    id=proof.id,
                    tenant_id=proof.tenant_id,
                    station_id=proof.station_id,
                    rating_curve_id=proof.rating_curve_id,
                    content=content,
                    fingerprint=content_digest(content),
                )
            )


class TestReferenceAppendTransactions:
    @pytest.mark.parametrize("conflicting", [False, True])
    def test_concurrent_appends_serialize_identity(
        self, isolated_committed_db_engine: sa.Engine, conflicting: bool
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        engine = isolated_committed_db_engine
        with engine.begin() as conn:
            reference = replace(
                assumed_reference(seed(conn)[3]), id=RatingReferenceProofId(uuid4())
            )
        contender = (
            replace(reference, authority_reference="fixture:contender")
            if conflicting
            else reference
        )
        barrier = Barrier(2)

        def append(value: OwnerAssumedZeroReference) -> str:
            with engine.begin() as conn:
                conn.execute(sa.text("SET LOCAL statement_timeout = '10s'"))
                barrier.wait(timeout=10)
                try:
                    return str(
                        PgProtectedReferenceWriter(conn).append_reference_evidence(
                            value
                        )
                    )
                except ValueError as exc:
                    assert "different content" in str(exc)
                    return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(append, value) for value in (reference, contender)]
            outcomes = [future.result(timeout=20) for future in futures]
        assert outcomes.count("conflict") == int(conflicting)
        assert outcomes.count(str(reference.id)) == 2 - int(conflicting)

    def test_caller_rollback_removes_new_append(
        self, isolated_committed_db_engine: sa.Engine
    ) -> None:
        engine = isolated_committed_db_engine
        with engine.begin() as conn:
            reference = replace(
                assumed_reference(seed(conn)[3]), id=RatingReferenceProofId(uuid4())
            )
        with engine.connect() as conn:
            tx = conn.begin()
            PgProtectedReferenceWriter(conn).append_reference_evidence(reference)
            tx.rollback()
        with engine.connect() as conn:
            assert (
                PgRatingReferenceStore(conn).fetch_reference_evidence(reference.id)
                is None
            )


class TestAssumedDischargeRejections:
    @pytest.mark.parametrize(
        "failure", ["version", "binding", "range", "newest", "qc", "disabled"]
    )
    def test_direct_sql_rechecks_assumed_inputs(
        self, db_connection: sa.Connection, failure: str
    ) -> None:
        import json
        from datetime import timedelta

        from sapphire_flow.db.metadata import (
            measurement_feed_evidence,
            observations,
            provisional_discharges,
        )
        from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
        from sapphire_flow.types.rating_reference import measurement_snapshot
        from tests.integration.store.test_provisional_discharge_store import (
            raw_values,
            seed_reference,
        )

        obs, curve, feed, proof = seed(db_connection)
        reference = replace(
            assumed_reference(proof), id=RatingReferenceProofId(uuid4())
        )
        if failure == "binding":
            reference = replace(reference, binding_at=NOW + timedelta(seconds=1))
        PgProtectedReferenceWriter(db_connection).append_reference_evidence(reference)
        result = convert_reference_discharge(
            observation=obs,
            curves=[curve],
            feed_evidence=feed,
            reference_evidence=reference,
            at=max(NOW, reference.binding_at),
        )
        if failure != "disabled":
            permit_fixture(db_connection, feed.tenant_id)
        values = raw_values(result)
        captured_at = NOW
        if failure == "version":
            document = json.loads(result.content)
            document["conversion_version"] = "expired-rating-linear-reference-v1"
            content = canonical_content(document)
            values.update(content=content, fingerprint=content_digest(content))
        elif failure == "range":
            obs = replace(obs, value=99.0)
            db_connection.execute(
                sa.update(observations)
                .where(observations.c.id == obs.id)
                .values(value=obs.value)
            )
            feed = replace(feed, id=uuid4(), measurement=measurement_snapshot(obs))
            seed_reference(db_connection, measurement_feed_evidence, feed)
            document = json.loads(result.content)
            document["measurement"] = json.loads(feed.measurement.content)
            document["feed_evidence"] = asdict(feed)
            content = canonical_content(document)
            values.update(
                feed_evidence_id=feed.id,
                content=content,
                fingerprint=content_digest(content),
            )
        elif failure == "newest":
            PgRatingCurveStore(db_connection).store_rating_curve(
                replace(
                    curve,
                    id=uuid4(),
                    version=2,
                    valid_from=curve.valid_from + timedelta(days=1),
                )
            )
        elif failure == "qc":
            db_connection.execute(
                sa.update(observations)
                .where(observations.c.id == obs.id)
                .values(qc_status="raw")
            )
        message = (
            "activation is disabled"
            if failure == "disabled"
            else "snapshot, QC, curve or reference disagreement"
        )
        with pytest.raises(sa.exc.DBAPIError, match=message):
            db_connection.execute(
                sa.insert(provisional_discharges).values(
                    **values, captured_at=captured_at
                )
            )

    def test_offset_datetime_is_canonical_before_insert(
        self, db_connection: sa.Connection
    ) -> None:
        from datetime import timedelta, timezone

        obs, curve, feed, proof = seed(db_connection)
        local = NOW.astimezone(timezone(timedelta(hours=5, minutes=45)))
        reference = replace(
            assumed_reference(proof),
            id=RatingReferenceProofId(uuid4()),
            assumed_at=local,
            binding_at=local,
        )
        PgProtectedReferenceWriter(db_connection).append_reference_evidence(reference)
        content = db_connection.scalar(
            sa.select(rating_reference_proofs.c.content).where(
                rating_reference_proofs.c.id == reference.id
            )
        )
        assert content == canonical_content(asdict(reference))
        assert "+05:45" not in content
        permit_fixture(db_connection, feed.tenant_id)
        result = convert_reference_discharge(
            observation=obs,
            curves=[curve],
            feed_evidence=feed,
            reference_evidence=reference,
            at=NOW,
        )
        stored = PgProvisionalDischargeStore(db_connection)
        assert (
            stored.store_provisional_discharge(result, captured_at=NOW)
            == result.fingerprint
        )

    def test_writer_refuses_fixed_snapshot(
        self, isolated_committed_db_engine: sa.Engine
    ) -> None:
        engine = isolated_committed_db_engine
        with engine.begin() as conn:
            reference = replace(
                assumed_reference(seed(conn)[3]), id=RatingReferenceProofId(uuid4())
            )
        with (
            engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as conn,
            pytest.raises(ValueError, match="READ COMMITTED"),
        ):
            PgProtectedReferenceWriter(conn).append_reference_evidence(reference)


class TestDirectSqlJsonAgreement:
    def test_pretty_json_is_readable_and_consumable(
        self, db_connection: sa.Connection
    ) -> None:
        import json

        obs, curve, feed, proof = seed(db_connection)
        reference = replace(
            assumed_reference(proof), id=RatingReferenceProofId(uuid4())
        )
        content = (
            json.dumps(json.loads(canonical_content(asdict(reference))), indent=2)
            + "\n"
        )
        db_connection.execute(
            sa.insert(rating_reference_proofs).values(
                id=reference.id,
                tenant_id=reference.tenant_id,
                station_id=reference.station_id,
                rating_curve_id=reference.rating_curve_id,
                reference_basis=reference.basis.value,
                content=content,
                fingerprint=content_digest(content),
            )
        )
        loaded = PgRatingReferenceStore(db_connection).fetch_reference_evidence(
            reference.id
        )
        assert loaded == reference
        permit_fixture(db_connection, feed.tenant_id)
        result = convert_reference_discharge(
            observation=obs,
            curves=[curve],
            feed_evidence=feed,
            reference_evidence=loaded,
            at=NOW,
        )
        assert (
            PgProvisionalDischargeStore(db_connection).store_provisional_discharge(
                result, captured_at=NOW
            )
            == result.fingerprint
        )

    @pytest.mark.parametrize("lexeme", ["1e0", "0.1e1", "1.0"])
    def test_raw_noninteger_json_lexeme_refused(
        self, db_connection: sa.Connection, lexeme: str
    ) -> None:
        proof = seed(db_connection)[3]
        reference = replace(
            assumed_reference(proof), id=RatingReferenceProofId(uuid4())
        )
        content = canonical_content(asdict(reference)).replace(
            '"api_station_id":1,', '"api_station_id":' + lexeme + ","
        )
        with pytest.raises(sa.exc.DBAPIError, match="invalid assumption feed identity"):
            db_connection.execute(
                sa.insert(rating_reference_proofs).values(
                    id=reference.id,
                    tenant_id=reference.tenant_id,
                    station_id=reference.station_id,
                    rating_curve_id=reference.rating_curve_id,
                    reference_basis=reference.basis.value,
                    content=content,
                    fingerprint=content_digest(content),
                )
            )

    def test_verified_reference_refuses_assumed_version(
        self, db_connection: sa.Connection
    ) -> None:
        import json

        from sapphire_flow.db.metadata import provisional_discharges
        from tests.integration.store.test_provisional_discharge_store import (
            convert,
            raw_values,
        )

        result = convert(*seed(db_connection))
        permit_fixture(db_connection, result.tenant_id)
        document = json.loads(result.content)
        document["conversion_version"] = "expired-rating-owner-assumed-zero-v1"
        content = canonical_content(document)
        values = raw_values(result) | dict(
            content=content, fingerprint=content_digest(content)
        )
        with pytest.raises(sa.exc.DBAPIError, match="reference disagreement"):
            db_connection.execute(
                sa.insert(provisional_discharges).values(**values, captured_at=NOW)
            )
