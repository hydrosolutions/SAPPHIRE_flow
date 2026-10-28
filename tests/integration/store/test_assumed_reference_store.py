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
from sapphire_flow.types.observation import Observation
from sapphire_flow.types.rating_curve import RatingCurve
from sapphire_flow.types.rating_reference import (
    MeasurementFeedEvidence,
    OwnerAssumedZeroReference,
    canonical_content,
    content_digest,
)
from tests.integration.store.test_provisional_discharge_store import (
    NOW,
    permit_fixture,
    seed,
)
from tests.unit.types.test_rating_reference import SNAPSHOT_FAILURES, assumed_reference


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


class TestAssumedInnerSnapshot:
    @pytest.mark.parametrize("mutation", ["unknown", "missing"])
    def test_rejects_incomplete_inner_provenance(
        self, db_connection: sa.Connection, mutation: str
    ) -> None:
        import json

        reference = replace(assumed_reference(seed(db_connection)[3]), id=uuid4())
        data = asdict(reference)
        inner = json.loads(reference.curve.content)
        if mutation == "unknown":
            inner["unexpected"] = True
        else:
            del inner["delivery_id"]
        data["curve"]["content"] = canonical_content(inner)
        with pytest.raises(sa.exc.DBAPIError, match="assumed curve snapshot"):
            self.insert(db_connection, reference, canonical_content(data))

    def test_pretty_inner_roundtrip_preserves_bytes(
        self, db_connection: sa.Connection
    ) -> None:
        import json

        obs, curve, feed, proof = seed(db_connection)
        reference = replace(assumed_reference(proof), id=uuid4())
        data = asdict(reference)
        inner = json.dumps(json.loads(reference.curve.content), indent=2) + "\n"
        data["curve"]["content"] = inner
        content = canonical_content(data)
        self.insert(db_connection, reference, content)
        loaded = PgRatingReferenceStore(db_connection).fetch_reference_evidence(
            reference.id
        )
        assert loaded is not None
        assert loaded.curve.content == inner
        assert (
            PgProtectedReferenceWriter(db_connection).append_reference_evidence(loaded)
            == loaded.id
        )
        permit_fixture(db_connection, feed.tenant_id)
        result = convert_reference_discharge(
            observation=obs,
            curves=[curve],
            feed_evidence=feed,
            reference_evidence=loaded,
            at=NOW,
        )
        store = PgProvisionalDischargeStore(db_connection)
        assert (
            store.store_provisional_discharge(result, captured_at=NOW)
            == result.fingerprint
        )
        assert store.fetch_provisional_discharge(result.fingerprint) == result
        assert (
            json.loads(result.content)["reference_proof"]["curve"]["content"] == inner
        )
        row = db_connection.execute(
            sa.select(
                rating_reference_proofs.c.content, rating_reference_proofs.c.fingerprint
            ).where(rating_reference_proofs.c.id == reference.id)
        ).one()
        assert row.content == content
        assert row.fingerprint == content_digest(content)

    @staticmethod
    def insert(
        conn: sa.Connection, reference: OwnerAssumedZeroReference, content: str
    ) -> None:
        conn.execute(
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


class TestAssumedSnapshotContainment:
    @pytest.mark.parametrize("mutation", SNAPSHOT_FAILURES)
    def test_sql_rejects_malformed_inner(
        self, db_connection: sa.Connection, mutation: str
    ) -> None:
        from tests.unit.types.test_rating_reference import malformed_inner_snapshot

        reference = replace(assumed_reference(seed(db_connection)[3]), id=uuid4())
        data = asdict(reference)
        data["curve"]["content"] = malformed_inner_snapshot(
            reference.curve.content, mutation
        )
        with pytest.raises(sa.exc.DBAPIError, match="assumed curve snapshot"):
            TestAssumedInnerSnapshot.insert(
                db_connection, reference, canonical_content(data)
            )

    @pytest.mark.parametrize(
        "lexeme", ["1e16", "1e-05", "1.5e1", "15.0", "1E+1", "-0.0", "0", "1.25"]
    )
    def test_fetched_numeric_oracle_roundtrip(
        self, db_connection: sa.Connection, lexeme: str
    ) -> None:
        import json

        from sapphire_flow.db.metadata import rating_curves
        from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
        from sapphire_flow.types.rating_reference import curve_snapshot

        obs, curve, feed, proof = seed(db_connection)
        curve = replace(curve, id=uuid4(), version=2)
        curves = PgRatingCurveStore(db_connection)
        curves.store_rating_curve(curve)
        raw = (
            '[{"water_level":2.0,"discharge":'
            + lexeme
            + '},{"water_level":0.0,"discharge":'
            + lexeme
            + "}]"
        )
        db_connection.execute(
            sa.update(rating_curves)
            .where(rating_curves.c.id == curve.id)
            .values(points=sa.cast(sa.literal(raw), sa.JSON))
        )
        fetched = next(
            c
            for c in curves.fetch_all_curves_for_station(curve.station_id)
            if c.id == curve.id
        )
        expected_type = int if lexeme in {"1e16", "1.5e1", "1E+1", "0"} else float
        assert type(fetched.points[0]["discharge"]) is expected_type
        reference = replace(
            assumed_reference(proof),
            id=uuid4(),
            rating_curve_id=fetched.id,
            curve=curve_snapshot(fetched),
        )
        data = asdict(reference)
        data["curve"]["content"] = json.dumps(
            json.loads(reference.curve.content), indent=2
        )
        TestAssumedInnerSnapshot.insert(
            db_connection, reference, canonical_content(data)
        )
        loaded = PgRatingReferenceStore(db_connection).fetch_reference_evidence(
            reference.id
        )
        assert loaded is not None
        result = convert_reference_discharge(
            observation=obs,
            curves=[fetched],
            feed_evidence=feed,
            reference_evidence=loaded,
            at=NOW,
        )
        permit_fixture(db_connection, feed.tenant_id)
        assert (
            PgProvisionalDischargeStore(db_connection).store_provisional_discharge(
                result, captured_at=NOW
            )
            == result.fingerprint
        )

    def test_non_utc_session_microseconds_roundtrip(
        self, db_connection: sa.Connection
    ) -> None:
        import json
        from datetime import timedelta

        from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
        from sapphire_flow.types.rating_reference import curve_snapshot

        obs, curve, feed, proof = seed(db_connection)
        curve = replace(
            curve,
            id=uuid4(),
            version=2,
            created_at=curve.created_at + timedelta(microseconds=123000),
        )
        curves = PgRatingCurveStore(db_connection)
        curves.store_rating_curve(curve)
        db_connection.execute(sa.text("SET LOCAL TIME ZONE 'Asia/Kathmandu'"))
        fetched = next(
            c
            for c in curves.fetch_all_curves_for_station(curve.station_id)
            if c.id == curve.id
        )
        reference = replace(
            assumed_reference(proof),
            id=uuid4(),
            rating_curve_id=fetched.id,
            curve=curve_snapshot(fetched),
        )
        data = asdict(reference)
        data["curve"]["content"] = json.dumps(
            json.loads(reference.curve.content), indent=2
        )
        TestAssumedInnerSnapshot.insert(
            db_connection, reference, canonical_content(data)
        )
        loaded = PgRatingReferenceStore(db_connection).fetch_reference_evidence(
            reference.id
        )
        assert loaded is not None
        assert json.loads(loaded.curve.content)["created_at"].endswith(".123000+00:00")
        result = convert_reference_discharge(
            observation=obs,
            curves=[fetched],
            feed_evidence=feed,
            reference_evidence=loaded,
            at=NOW,
        )
        permit_fixture(db_connection, feed.tenant_id)
        assert (
            PgProvisionalDischargeStore(db_connection).store_provisional_discharge(
                result, captured_at=NOW
            )
            == result.fingerprint
        )


class TestAssumedRawNumericKinds:
    @pytest.mark.parametrize("lexeme", ["10", "true", '"10.0"', "1e400", "1e-400"])
    def test_sql_refuses_numeric_restatement(
        self, db_connection: sa.Connection, lexeme: str
    ) -> None:
        reference = replace(assumed_reference(seed(db_connection)[3]), id=uuid4())
        data = asdict(reference)
        data["curve"]["content"] = reference.curve.content.replace(
            '"discharge":10.0', '"discharge":' + lexeme, 1
        )
        with pytest.raises(sa.exc.DBAPIError, match="assumed curve snapshot"):
            TestAssumedInnerSnapshot.insert(
                db_connection, reference, canonical_content(data)
            )

    @pytest.mark.parametrize("lexeme", ["1E+1", "1.0e1", "10.000"])
    def test_float_spellings_read_and_convert(
        self, db_connection: sa.Connection, lexeme: str
    ) -> None:
        obs, curve, feed, proof = seed(db_connection)
        reference = replace(assumed_reference(proof), id=uuid4())
        data = asdict(reference)
        data["curve"]["content"] = reference.curve.content.replace(
            '"discharge":10.0', '"discharge":' + lexeme, 1
        )
        TestAssumedInnerSnapshot.insert(
            db_connection, reference, canonical_content(data)
        )
        loaded = PgRatingReferenceStore(db_connection).fetch_reference_evidence(
            reference.id
        )
        result = convert_reference_discharge(
            observation=obs,
            curves=[curve],
            feed_evidence=feed,
            reference_evidence=loaded,
            at=NOW,
        )
        permit_fixture(db_connection, feed.tenant_id)
        assert (
            PgProvisionalDischargeStore(db_connection).store_provisional_discharge(
                result, captured_at=NOW
            )
            == result.fingerprint
        )


def metadata_fixture(
    conn: sa.Connection, metadata: object, *, key: str = "metadata"
) -> tuple[
    Observation, RatingCurve, MeasurementFeedEvidence, OwnerAssumedZeroReference
]:
    from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
    from sapphire_flow.types.rating_reference import curve_snapshot

    obs, curve, feed, proof = seed(conn)
    curve = replace(
        curve,
        id=uuid4(),
        version=2,
        points=[p | {key: metadata} for p in curve.points],
    )
    store = PgRatingCurveStore(conn)
    store.store_rating_curve(curve)
    fetched = next(
        c
        for c in store.fetch_all_curves_for_station(curve.station_id)
        if c.id == curve.id
    )
    reference = replace(
        assumed_reference(proof),
        id=uuid4(),
        rating_curve_id=fetched.id,
        curve=curve_snapshot(fetched),
    )
    return obs, fetched, feed, reference


def nested_metadata(depth: int) -> object:
    value: object = "leaf"
    for _ in range(depth):
        value = [value]
    return value


class TestPersistedPointMetadata:
    @pytest.mark.parametrize(
        "key,metadata",
        [
            ("label", "retained source metadata"),
            (
                "metadata",
                {"label": "snow 雪", "items": [None, True, 1, 1.0, {"large": 10**400}]},
            ),
            ("metadata", nested_metadata(16)),
            ("metadata", nested_metadata(128)),
            ("metadata", 10**4299),
            ("metadata", 5e-324),
        ],
    )
    def test_lossless_native_roundtrip(
        self, db_connection: sa.Connection, key: str, metadata: object
    ) -> None:
        import json

        from sapphire_flow.types.rating_reference import AssumedCurveSnapshot

        obs, curve, feed, reference = metadata_fixture(db_connection, metadata, key=key)
        PgProtectedReferenceWriter(db_connection).append_reference_evidence(reference)
        canonical_result = convert_reference_discharge(
            observation=obs,
            curves=[curve],
            feed_evidence=feed,
            reference_evidence=reference,
            at=NOW,
        )
        permit_fixture(db_connection, feed.tenant_id)
        store = PgProvisionalDischargeStore(db_connection)
        store.store_provisional_discharge(canonical_result, captured_at=NOW)
        reference = replace(reference, id=uuid4())
        data = asdict(reference)
        inner = json.dumps(
            json.loads(reference.curve.content), ensure_ascii=False, indent=2
        )
        data["curve"]["content"] = inner
        content = canonical_content(data)
        TestAssumedInnerSnapshot.insert(db_connection, reference, content)
        loaded = PgRatingReferenceStore(db_connection).fetch_reference_evidence(
            reference.id
        )
        assert loaded is not None
        assert isinstance(loaded.curve, AssumedCurveSnapshot)
        assert loaded.curve.content == inner
        assert (
            PgProtectedReferenceWriter(db_connection).append_reference_evidence(loaded)
            == loaded.id
        )
        result = convert_reference_discharge(
            observation=obs,
            curves=[curve],
            feed_evidence=feed,
            reference_evidence=loaded,
            at=NOW,
        )
        assert (
            store.store_provisional_discharge(result, captured_at=NOW)
            == result.fingerprint
        )
        assert store.fetch_provisional_discharge(result.fingerprint) == result
        assert (
            json.loads(result.content)["reference_proof"]["curve"]["content"] == inner
        )
        with pytest.raises(ValueError, match="different content"):
            PgProtectedReferenceWriter(db_connection).append_reference_evidence(
                reference
            )

    @pytest.mark.parametrize("case", ["depth", "digits"])
    def test_python_and_sql_containment_bounds(
        self, db_connection: sa.Connection, case: str
    ) -> None:
        import json

        from sapphire_flow.types.rating_reference import assumed_curve_content

        reference = replace(assumed_reference(seed(db_connection)[3]), id=uuid4())
        data = asdict(reference)
        curve = json.loads(reference.curve.content)
        curve["points"][0]["metadata"] = (
            nested_metadata(129) if case == "depth" else "DIGITS"
        )
        inner = canonical_content(curve)
        if case == "digits":
            inner = inner.replace('"DIGITS"', "1" + "0" * 4300)
        with pytest.raises(ValueError, match="snapshot"):
            assumed_curve_content(inner)
        # Seed source metadata without Python's integer decoder.
        from sapphire_flow.db.metadata import rating_curves

        db_connection.execute(
            sa.update(rating_curves)
            .where(rating_curves.c.id == reference.rating_curve_id)
            .values(points=sa.cast(sa.literal(inner), sa.JSON)["points"])
        )
        if case == "digits":
            from sapphire_flow.store.rating_curve_store import PgRatingCurveStore

            with pytest.raises(ValueError, match="4300.*digits|4301 digits"):
                PgRatingCurveStore(db_connection).fetch_all_curves_for_station(
                    reference.station_id
                )
        data["curve"]["content"] = inner
        with pytest.raises(sa.exc.DBAPIError, match="snapshot.*(depth|digit)"):
            TestAssumedInnerSnapshot.insert(
                db_connection, reference, canonical_content(data)
            )

    @pytest.mark.parametrize(
        "mutation",
        [
            "extra",
            "missing",
            "int_float",
            "boolean",
            "array_order",
            "duplicate",
            "escaped_duplicate",
            "negative_zero",
            "underflow",
            "nul",
            "array_short",
            "nested_extra",
            "nested_missing",
            "array_duplicate",
            "numeric_string",
            "exponent_category",
            "overflow",
            "surrogate",
            "changed_string",
        ],
    )
    def test_nested_metadata_must_match_source(
        self, db_connection: sa.Connection, mutation: str
    ) -> None:
        import json

        _, _, _, reference = metadata_fixture(
            db_connection,
            {
                "number": 1.0,
                "zero": 0.0,
                "count": 1,
                "array": [1, 2, {"k": "v"}],
                "obj": {"a": 1},
                "label": "source",
            },
        )
        data = asdict(reference)
        inner = json.loads(reference.curve.content)
        metadata = inner["points"][0]["metadata"]
        if mutation == "extra":
            metadata["extra"] = 1
        elif mutation == "missing":
            del metadata["label"]
        elif mutation == "int_float":
            metadata["number"] = 1
        elif mutation == "boolean":
            metadata["number"] = True
        elif mutation == "array_order":
            metadata["array"] = [2, 1, {"k": "v"}]
        elif mutation == "array_short":
            metadata["array"] = [1]
        elif mutation == "nested_extra":
            metadata["obj"]["b"] = 1
        elif mutation == "nested_missing":
            del metadata["obj"]["a"]
        elif mutation == "numeric_string":
            metadata["number"] = "1.0"
        elif mutation == "changed_string":
            metadata["label"] = "changed"
        raw = canonical_content(inner)
        if mutation == "array_duplicate":
            raw = raw.replace('"k":"v"', '"k":"v","k":"v"', 1)
        elif mutation == "exponent_category":
            raw = raw.replace('"count":1', '"count":1e0', 1)
        elif mutation == "overflow":
            raw = raw.replace('"number":1.0', '"number":1e400', 1)
        elif mutation == "surrogate":
            raw = raw.replace('"label":"source"', '"label":"\\ud800"', 1)
        elif mutation == "duplicate":
            raw = raw.replace('"number":1.0', '"number":1.0,"number":1.0', 1)
        elif mutation == "escaped_duplicate":
            raw = raw.replace('"number":1.0', '"num\\u0062er":1.0,"number":1.0', 1)
        elif mutation == "negative_zero":
            raw = raw.replace('"zero":0.0', '"zero":-0', 1)
        elif mutation == "underflow":
            raw = raw.replace('"zero":0.0', '"zero":1e-400', 1)
        elif mutation == "nul":
            raw = raw.replace('"label":"source"', '"label":"\\u0000"', 1)
        data["curve"]["content"] = raw
        with pytest.raises(sa.exc.DBAPIError, match="assumed curve snapshot"):
            TestAssumedInnerSnapshot.insert(
                db_connection, reference, canonical_content(data)
            )


class TestAssumedGuardBranches:
    @pytest.mark.parametrize(
        "second_discharge,message",
        [(10.0, "snapshot stage order"), (20.0, "assumed curve snapshot")],
    )
    def test_persisted_ties_fail_stage_order(
        self, db_connection: sa.Connection, second_discharge: float, message: str
    ) -> None:
        from sapphire_flow.services.rating_conversion import (
            RatingConversionError,
            RatingConverter,
        )
        from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
        from sapphire_flow.types.rating_reference import curve_snapshot

        _, curve, _, proof = seed(db_connection)
        curve = replace(
            curve,
            id=uuid4(),
            version=2,
            points=[
                {"water_level": 1.0, "discharge": 10.0},
                {"water_level": 1.0, "discharge": second_discharge},
            ],
        )
        store = PgRatingCurveStore(db_connection)
        store.store_rating_curve(curve)
        fetched = next(
            c
            for c in store.fetch_all_curves_for_station(curve.station_id)
            if c.id == curve.id
        )
        with pytest.raises(RatingConversionError, match="duplicate or non-increasing"):
            RatingConverter.from_curve(fetched)
        reference = replace(assumed_reference(proof), id=uuid4())
        data = asdict(reference)
        data["rating_curve_id"] = str(fetched.id)
        data["curve"]["content"] = curve_snapshot(fetched).content
        with pytest.raises(sa.exc.DBAPIError, match=message):
            db_connection.execute(
                sa.insert(rating_reference_proofs).values(
                    id=reference.id,
                    tenant_id=reference.tenant_id,
                    station_id=reference.station_id,
                    rating_curve_id=fetched.id,
                    reference_basis=reference.basis.value,
                    content=canonical_content(data),
                    fingerprint=content_digest(canonical_content(data)),
                )
            )

    @pytest.mark.parametrize(
        "style", ["Z", "space", "fraction", "offset", "infinity", "out_of_range"]
    )
    def test_exact_timestamp_shape(
        self, db_connection: sa.Connection, style: str
    ) -> None:
        import json
        from datetime import datetime, timedelta, timezone

        reference = replace(assumed_reference(seed(db_connection)[3]), id=uuid4())
        data = asdict(reference)
        inner = json.loads(reference.curve.content)
        original = inner["created_at"]
        variants = {
            "Z": original.replace("+00:00", "Z"),
            "space": original.replace("T", " "),
            "fraction": original.replace("+00:00", ".000+00:00"),
            "offset": datetime.fromisoformat(original)
            .astimezone(timezone(timedelta(hours=1)))
            .isoformat(),
            "infinity": "infinity",
            "out_of_range": "10000-01-01T00:00:00+00:00",
        }
        inner["created_at"] = variants[style]
        data["curve"]["content"] = canonical_content(inner)
        with pytest.raises(sa.exc.DBAPIError, match="snapshot timestamp"):
            TestAssumedInnerSnapshot.insert(
                db_connection, reference, canonical_content(data)
            )

    def test_core_subnormal_roundtrip(self, db_connection: sa.Connection) -> None:
        # A constant subnormal discharge remains finite and converter-valid.
        obs, curve, feed, reference = metadata_fixture(db_connection, "subnormal-core")
        from sapphire_flow.db.metadata import rating_curves
        from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
        from sapphire_flow.types.rating_reference import curve_snapshot

        points = [p | {"discharge": 5e-324} for p in curve.points]
        db_connection.execute(
            sa.update(rating_curves)
            .where(rating_curves.c.id == curve.id)
            .values(points=points)
        )
        fetched = next(
            c
            for c in PgRatingCurveStore(db_connection).fetch_all_curves_for_station(
                curve.station_id
            )
            if c.id == curve.id
        )
        reference = replace(reference, curve=curve_snapshot(fetched))
        PgProtectedReferenceWriter(db_connection).append_reference_evidence(reference)
        result = convert_reference_discharge(
            observation=obs,
            curves=[fetched],
            feed_evidence=feed,
            reference_evidence=reference,
            at=NOW,
        )
        permit_fixture(db_connection, feed.tenant_id)
        assert (
            PgProvisionalDischargeStore(db_connection).store_provisional_discharge(
                result, captured_at=NOW
            )
            == result.fingerprint
        )
