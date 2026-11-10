from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import sqlalchemy as sa

from sapphire_flow.types.rating_reference import canonical_content, content_digest

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.types.forecast import OperationalForecast
    from sapphire_flow.types.rating_derived import RatingDerivedDischarge


class TestFrozenLexicalIdentity:
    @pytest.mark.parametrize(
        "extra", [{}, {"unicode": "é水", "tiny": 1e-7, "large": 1e30}]
    )
    def test_json_subtree_preserves_producer_bytes(
        self, db_connection: sa.Connection, extra: dict[str, object]
    ) -> None:
        golden = json.loads(
            Path("tests/fixtures/native_rating_v1_golden.json").read_text()
        )
        data = json.loads(golden["sample_content"])
        data["curve"].update(extra)
        content = canonical_content(data)
        curve = canonical_content(data["curve"])
        row = db_connection.execute(
            sa.text("""
            SELECT (CAST(:content AS text)::json->'curve')::text,
                   encode(sha256(convert_to(
                       (CAST(:content AS text)::json->'curve')::text,
                                           'UTF8')), 'hex'),
                   encode(sha256(convert_to(:content, 'UTF8')), 'hex')
        """),
            {"content": content},
        ).one()
        assert tuple(row) == (curve, content_digest(curve), content_digest(content))

    def test_semantic_equality_does_not_replace_byte_identity(
        self, db_connection: sa.Connection
    ) -> None:
        row = db_connection.execute(
            sa.text("""
            SELECT CAST(:a AS text)::jsonb = CAST(:b AS text)::jsonb,
                   sha256(convert_to(:a, 'UTF8')) = sha256(convert_to(:b, 'UTF8'))
        """),
            {"a": '{"curve":{"x":1e-7}}', "b": '{ "curve": {"x":0.0000001}}'},
        ).one()
        assert tuple(row) == (True, False)


class TestFrozenProjection:
    def test_retained_v1_positive(self, db_connection: sa.Connection) -> None:
        from sapphire_flow.store.forecast_store import PgForecastStore
        from tests.integration.store.test_native_rating_forecasts import native_pair
        from tests.integration.store.test_rejected_forecast_store import (
            savepoint_factory,
        )

        _, native = native_pair(db_connection)
        PgForecastStore(
            db_connection,
            data_use=native.data_use,
            transaction_factory=savepoint_factory(db_connection),
        ).store_forecast(native)
        result = db_connection.scalar(
            sa.select(sa.func.forecast_rating_input_history(native.id))
        )
        assert result["forecast_id"] == str(native.id)
        assert result["samples"][0]["discharge"] is not None
        assert len(result["samples"][0]["curve_digest"]) == 64

    @pytest.mark.parametrize("body", ['{"x":1,"x":2}', '{"x":{"y":0,"y":1}}'])
    def test_duplicate_keys_rejected(
        self, db_connection: sa.Connection, body: str
    ) -> None:
        with pytest.raises(sa.exc.DBAPIError, match="rating history shape"):
            db_connection.scalar(
                sa.select(sa.func.rating_history_json_nodes(body, 1000))
            )


def _v2_parent(conn: sa.Connection) -> tuple[OperationalForecast, dict[str, Any]]:
    from dataclasses import replace
    from uuid import uuid4

    from sapphire_flow.db.metadata import (
        native_measurement_captures,
        rating_derived_discharge,
    )
    from tests.integration.store.test_native_rating_forecasts import native_pair

    _, parent = native_pair(conn)
    row = dict(conn.execute(sa.select(rating_derived_discharge)).mappings().one())
    data = json.loads(row["content"])
    measurement = {
        k: v
        for k, v in data["source"].items()
        if k not in {"qc_status", "qc_flags", "qc_rule_version"}
    }
    capture_id = uuid4()
    source = {
        "schema_version": 1,
        "tenant_id": str(row["tenant_id"]),
        "station_id": str(row["station_id"]),
        "measurement": measurement,
        "provider": {
            "provider": "bipad",
            "endpoint": "https://example.invalid/levels/",
            "api_station_id": 1,
        },
        "level_unit": "m",
        "level_reference": "gauge_zero",
    }
    source_content = canonical_content(source)
    evidence_content = canonical_content(
        {"schema_version": 1, "id": str(capture_id), "source_version": source}
    )
    conn.execute(
        sa.insert(native_measurement_captures).values(
            id=capture_id,
            tenant_id=row["tenant_id"],
            station_id=row["station_id"],
            observation_id=row["observation_id"],
            source_content=source_content,
            source_version_sha256=content_digest(source_content),
            evidence_content=evidence_content,
            evidence_sha256=content_digest(evidence_content),
            registered_at=row["captured_at"],
        )
    )
    data["policy"] = "native-rating-linear-endpoints-v2"
    data["feed"] = {
        "kind": "native_capture",
        "capture_id": str(capture_id),
        "capture_sha256": content_digest(evidence_content),
        "source_version_sha256": content_digest(source_content),
        "provider": source["provider"],
        "level_unit": "m",
        "level_reference": "gauge_zero",
    }
    row["content"] = canonical_content(data)
    row["fingerprint"] = content_digest(row["content"])
    row["capture_id"] = capture_id
    row["evidence_version"] = 2
    row.pop("stored_order")
    row.pop("stored_order_basis")
    conn.execute(sa.insert(rating_derived_discharge).values(**row))
    lineage = parent.input_lineage
    assert lineage is not None and lineage.rating_input is not None
    support = replace(lineage.rating_input.days[0], fingerprints=(row["fingerprint"],))
    parent = replace(
        parent,
        input_lineage=replace(
            lineage,
            transformation_versions=("native-rating-linear-endpoints-v2",),
            rating_input=replace(lineage.rating_input, days=(support,)),
        ),
    )
    return parent, row


class TestCaptureProjection:
    def test_frozen_v2_uses_capture_and_exact_curve_digest(
        self, db_connection: sa.Connection
    ) -> None:
        from pydantic import TypeAdapter

        from sapphire_flow.services.forecast_rating_history import (
            build_forecast_rating_history,
        )
        from sapphire_flow.store.forecast_store import PgForecastStore
        from sapphire_flow.types.forecast_rating_history import RatingHistoryEvidence
        from tests.integration.store.test_rejected_forecast_store import (
            savepoint_factory,
        )

        parent, row = _v2_parent(db_connection)
        PgForecastStore(
            db_connection,
            data_use=parent.data_use,
            transaction_factory=savepoint_factory(db_connection),
        ).store_forecast(parent)
        raw = db_connection.scalar(
            sa.select(sa.func.forecast_rating_input_history(parent.id))
        )
        evidence = TypeAdapter(RatingHistoryEvidence).validate_python(raw)
        result = build_forecast_rating_history(evidence)
        assert result.days[-1].daily_discharge == row["discharge"]
        assert result.days[-1].curves[0].content_sha256 == content_digest(
            canonical_content(json.loads(row["content"])["curve"])
        )
        assert len(result.days) == 7
        assert result.days[0].status == "no_retained_derived_day"


def _dense_parent(
    conn: sa.Connection,
    version: int,
    count: int = 2016,
    cadence_minutes: int = 5,
    discharge: float | None = None,
    extra_curve_points: int = 0,
) -> tuple[OperationalForecast, list[RatingDerivedDischarge]]:
    from dataclasses import replace
    from datetime import timedelta
    from uuid import uuid4

    import polars as pl

    from sapphire_flow.db.metadata import (
        native_measurement_captures,
        observations,
        rating_derived_discharge,
    )
    from sapphire_flow.services.rating_inputs import merge_rating_days
    from sapphire_flow.types.rating_derived import RatingDerivedDischarge
    from tests.integration.store.test_native_rating_forecasts import native_pair

    if version == 2:
        parent, template = _v2_parent(conn)
        cap_template = dict(
            conn.execute(sa.select(native_measurement_captures)).mappings().one()
        )
    else:
        _, parent = native_pair(conn)
        template = dict(
            conn.execute(sa.select(rating_derived_discharge)).mappings().one()
        )
        template.pop("stored_order")
        template.pop("stored_order_basis")
        cap_template = None
    if extra_curve_points:
        data = json.loads(template["content"])
        first = data["curve"]["points"][0]
        data["curve"]["points"] = [
            {
                "water_level": first["water_level"] - index,
                "discharge": 0.0,
            }
            for index in range(extra_curve_points, 0, -1)
        ] + data["curve"]["points"]
        data["reference"]["curve"]["content"] = canonical_content(data["curve"])
        template["content"] = canonical_content(data)
        template["fingerprint"] = content_digest(template["content"])
        conn.execute(sa.insert(rating_derived_discharge).values(**template))
    observation = dict(conn.execute(sa.select(observations)).mappings().one())
    start = parent.issued_at.replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - timedelta(days=7)
    samples = []
    for index in range(count):
        timestamp = start + timedelta(minutes=cadence_minutes * index)
        row = dict(template)
        data = json.loads(template["content"])
        if version == 1:
            data["feed"]["start"] = start.isoformat()
            data["feed"]["end"] = parent.issued_at.isoformat()
        if timestamp != template["timestamp"]:
            row["observation_id"] = uuid4()
            row["timestamp"] = timestamp
            obs = {**observation, "id": row["observation_id"], "timestamp": timestamp}
            conn.execute(sa.insert(observations).values(**obs))
            data["observation_id"] = str(row["observation_id"])
            data["source"]["id"] = str(row["observation_id"])
            data["source"]["timestamp"] = timestamp.isoformat()
            if cap_template is not None:
                cap = dict(cap_template)
                cap["id"] = uuid4()
                cap["observation_id"] = row["observation_id"]
                source = json.loads(cap["source_content"])
                source["measurement"] = {
                    k: v
                    for k, v in data["source"].items()
                    if k not in {"qc_status", "qc_flags", "qc_rule_version"}
                }
                cap["source_content"] = canonical_content(source)
                cap["source_version_sha256"] = content_digest(cap["source_content"])
                cap["evidence_content"] = canonical_content(
                    {
                        "schema_version": 1,
                        "id": str(cap["id"]),
                        "source_version": source,
                    }
                )
                cap["evidence_sha256"] = content_digest(cap["evidence_content"])
                conn.execute(sa.insert(native_measurement_captures).values(**cap))
                row["capture_id"] = cap["id"]
                data["feed"].update(
                    capture_id=str(cap["id"]),
                    capture_sha256=cap["evidence_sha256"],
                    source_version_sha256=cap["source_version_sha256"],
                )
            if discharge is not None:
                row["discharge"] = data["discharge"] = discharge
            row["content"] = canonical_content(data)
            row["fingerprint"] = content_digest(row["content"])
            conn.execute(sa.insert(rating_derived_discharge).values(**row))
        samples.append(
            RatingDerivedDischarge(
                **{
                    k: row[k]
                    for k in (
                        "tenant_id",
                        "station_id",
                        "observation_id",
                        "rating_curve_id",
                        "file_id",
                        "timestamp",
                        "discharge",
                        "rejection",
                        "content",
                        "capture_id",
                        "evidence_version",
                    )
                },
                warnings=tuple(data["warnings"]),
            )
        )
    _, lineage, _ = merge_rating_days(
        pl.DataFrame(),
        tuple(samples),
        issue_time=parent.issued_at,
        weather_cycle_time=parent.issued_at,
    )
    assert lineage is not None and lineage.rating_input is not None
    parent = replace(
        parent,
        input_lineage=replace(
            lineage,
            rating_input=replace(
                lineage.rating_input, artifact_id=parent.model_artifact_id
            ),
        ),
    )
    return parent, samples


class TestRepeatedCurveValidation:
    @pytest.mark.parametrize("version", [1, 2])
    def test_dense_rich_curve_history_keeps_all_samples(
        self, db_connection: sa.Connection, version: int
    ) -> None:
        from sapphire_flow.store.forecast_store import PgForecastStore
        from tests.integration.store.test_rejected_forecast_store import (
            savepoint_factory,
        )

        parent, samples = _dense_parent(
            db_connection,
            version,
            count=944,
            cadence_minutes=10,
            extra_curve_points=80,
        )
        PgForecastStore(
            db_connection,
            data_use=parent.data_use,
            transaction_factory=savepoint_factory(db_connection),
        ).store_forecast(parent)
        metrics = db_connection.execute(
            sa.text("""
            SELECT sum(octet_length(r.content) +
                       coalesce(octet_length(c.source_content),0) +
                       coalesce(octet_length(c.evidence_content),0)) AS bytes,
                   max(octet_length(r.content)) AS largest,
                   sum(public.rating_history_json_nodes(r.content,500000) +
                       public.rating_history_json_nodes(
                           r.content::jsonb->'reference'->'curve'->>'content',500000) +
                       CASE WHEN c.id IS NULL THEN 0 ELSE
                       public.rating_history_json_nodes(c.source_content,500000) +
                       public.rating_history_json_nodes(c.evidence_content,500000)
                       END) AS nodes
            FROM rating_derived_discharge r
            LEFT JOIN native_measurement_captures c ON c.id=r.capture_id
        """)
        ).one()
        assert metrics.nodes > 500000
        assert parent.input_lineage is not None
        assert metrics.bytes + len(parent.input_lineage.content.encode()) < 16777216
        assert metrics.largest < 262144
        db_connection.execute(sa.text("SET LOCAL statement_timeout='5s'"))
        raw = db_connection.scalar(
            sa.select(sa.func.forecast_rating_input_history(parent.id))
        )
        assert raw is not None
        assert len(raw["samples"]) == len(samples)
        assert all(row["discharge"] == samples[0].discharge for row in raw["samples"])


class TestHistoryCapacity:
    @pytest.mark.parametrize("version", [1, 2])
    def test_seven_days_five_minute_fixture(
        self,
        db_connection: sa.Connection,
        version: int,
        record_property: Callable[[str, object], None],
    ) -> None:
        import time

        from pydantic import TypeAdapter

        from sapphire_flow.services.forecast_rating_history import (
            build_forecast_rating_history,
        )
        from sapphire_flow.store.forecast_store import PgForecastStore
        from sapphire_flow.types.forecast_rating_history import RatingHistoryEvidence
        from tests.integration.store.test_rejected_forecast_store import (
            savepoint_factory,
        )

        parent, samples = _dense_parent(db_connection, version)
        metrics = (
            db_connection.execute(
                sa.text("""
            SELECT sum(octet_length(r.content)) AS derived_bytes,
                   sum(coalesce(octet_length(c.source_content),0)+
                       coalesce(octet_length(c.evidence_content),0)) AS capture_bytes,
                   sum(public.rating_history_json_nodes(r.content,100000)+
                       public.rating_history_json_nodes(
                           r.content::jsonb->'reference'->'curve'->>'content',100000)+
                       CASE WHEN c.id IS NULL THEN 0 ELSE
                       public.rating_history_json_nodes(c.source_content,100000)+
                       public.rating_history_json_nodes(c.evidence_content,100000)
                       END) AS nodes
            FROM rating_derived_discharge r LEFT JOIN native_measurement_captures c
              ON c.id=r.capture_id WHERE r.evidence_version=:version
        """),
                {"version": version},
            )
            .mappings()
            .one()
        )
        for key, value in metrics.items():
            record_property(key, int(value))
        record_property("parent_bytes", len(parent.input_lineage.content.encode()))
        PgForecastStore(
            db_connection,
            data_use=parent.data_use,
            transaction_factory=savepoint_factory(db_connection),
        ).store_forecast(parent)
        db_connection.execute(sa.text("SET LOCAL statement_timeout='5s'"))
        memory_before = db_connection.scalar(
            sa.text("SELECT sum(total_bytes) FROM pg_backend_memory_contexts")
        )
        started = time.perf_counter()
        raw = db_connection.scalar(
            sa.select(sa.func.forecast_rating_input_history(parent.id))
        )
        elapsed = time.perf_counter() - started
        memory_after = db_connection.scalar(
            sa.text("SELECT sum(total_bytes) FROM pg_backend_memory_contexts")
        )
        record_property("backend_allocated_before", memory_before)
        record_property("backend_allocated_after", memory_after)
        record_property("seconds", elapsed)
        record_property("projected_bytes", len(json.dumps(raw).encode()))
        history = build_forecast_rating_history(
            TypeAdapter(RatingHistoryEvidence).validate_python(raw)
        )
        assert len(raw["samples"]) == 2016
        assert all(day.status == "available" for day in history.days)
        assert all(day.daily_discharge == samples[0].discharge for day in history.days)
        assert elapsed < 5
        assert len(json.dumps(raw).encode()) < 2097152


def _stored_parent(conn: sa.Connection) -> OperationalForecast:
    from sapphire_flow.store.forecast_store import PgForecastStore
    from tests.integration.store.test_native_rating_forecasts import native_pair
    from tests.integration.store.test_rejected_forecast_store import savepoint_factory

    _, parent = native_pair(conn)
    PgForecastStore(
        conn, data_use=parent.data_use, transaction_factory=savepoint_factory(conn)
    ).store_forecast(parent)
    return parent


def _replace_sample_content(
    conn: sa.Connection,
    parent: OperationalForecast,
    transform: Callable[[str], str],
    selected_fingerprint: str | None = None,
) -> None:
    from sapphire_flow.db.metadata import forecasts, rating_derived_discharge

    row = (
        conn.execute(
            sa.select(rating_derived_discharge).where(
                rating_derived_discharge.c.fingerprint
                == (
                    selected_fingerprint
                    or parent.input_lineage.rating_input.days[0].fingerprints[0]
                )
            )
        )
        .mappings()
        .one()
    )
    raw = transform(row["content"])
    fingerprint = content_digest(raw)
    conn.execute(sa.text("SET LOCAL session_replication_role = replica"))
    conn.execute(
        sa.update(rating_derived_discharge)
        .where(rating_derived_discharge.c.fingerprint == row["fingerprint"])
        .values(content=raw, fingerprint=fingerprint)
    )
    conn.execute(
        sa.update(forecasts)
        .where(forecasts.c.id == parent.id)
        .values(
            input_lineage=parent.input_lineage.content.replace(
                row["fingerprint"], fingerprint
            )
        )
    )
    conn.execute(sa.text("SET LOCAL session_replication_role = origin"))


class TestCachedCurveIntegrity:
    @pytest.mark.parametrize(
        "mutation", ["curve", "reference", "inner_duplicate", "root_duplicate"]
    )
    def test_later_sample_cannot_reuse_validation_for_changed_bytes(
        self, db_connection: sa.Connection, mutation: str
    ) -> None:
        from sapphire_flow.store.forecast_store import PgForecastStore
        from tests.integration.store.test_rejected_forecast_store import (
            savepoint_factory,
        )

        parent, samples = _dense_parent(db_connection, 2, count=2)
        PgForecastStore(
            db_connection,
            data_use=parent.data_use,
            transaction_factory=savepoint_factory(db_connection),
        ).store_forecast(parent)
        # Also exercise a second request: cached validation must not survive it.
        assert (
            len(
                db_connection.scalar(
                    sa.select(sa.func.forecast_rating_input_history(parent.id))
                )["samples"]
            )
            == 2
        )

        def change(raw: str) -> str:
            if mutation == "root_duplicate":
                return raw.replace("{", '{"curve":{},', 1)
            if mutation == "inner_duplicate":
                return raw.replace('"curve":{', '"curve":{"id":"ignored",', 1)
            data = json.loads(raw)
            if mutation == "curve":
                data["curve"]["version"] += 1
            else:
                ref = json.loads(data["reference"]["curve"]["content"])
                ref["version"] += 1
                data["reference"]["curve"]["content"] = canonical_content(ref)
            return canonical_content(data)

        _replace_sample_content(db_connection, parent, change, samples[-1].fingerprint)
        with pytest.raises(sa.exc.DBAPIError, match="rating history (shape|reference)"):
            db_connection.scalar(
                sa.select(sa.func.forecast_rating_input_history(parent.id))
            )


class TestCorruptEvidence:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("warnings", ["secret-marker-never-export"]),
            ("policy", "unknown-policy"),
            ("tenant_id", "00000000-0000-0000-0000-000000000000"),
            ("discharge", 1234567.0),
            ("rejection", "secret-marker-never-export"),
            ("endpoints", [{"water_level": 1.0, "discharge": 1.0}]),
        ],
    )
    def test_self_consistent_digest_cannot_bypass_semantics(
        self,
        db_connection: sa.Connection,
        field: str,
        value: object,
    ) -> None:
        parent = _stored_parent(db_connection)

        def mutate(raw: str) -> str:
            data = json.loads(raw)
            data[field] = value
            return canonical_content(data)

        _replace_sample_content(db_connection, parent, mutate)
        with pytest.raises(sa.exc.DBAPIError, match="rating history"):
            db_connection.scalar(
                sa.select(sa.func.forecast_rating_input_history(parent.id))
            )

    def test_duplicate_key_with_valid_digest_is_rejected(
        self, db_connection: sa.Connection
    ) -> None:
        parent = _stored_parent(db_connection)
        _replace_sample_content(
            db_connection,
            parent,
            lambda raw: raw.replace("{", '{"policy":"ignored",', 1),
        )
        with pytest.raises(sa.exc.DBAPIError, match="rating history shape"):
            db_connection.scalar(
                sa.select(sa.func.forecast_rating_input_history(parent.id))
            )

    @pytest.mark.parametrize(
        "body,budget", [("[0,0]", 2), ("[" * 34 + "0" + "]" * 34, 1000)]
    )
    def test_shape_walk_limits(
        self, db_connection: sa.Connection, body: str, budget: int
    ) -> None:
        with pytest.raises(sa.exc.DBAPIError, match="rating history bounds"):
            db_connection.scalar(
                sa.select(sa.func.rating_history_json_nodes(body, budget))
            )

    def test_content_byte_limit(self, db_connection: sa.Connection) -> None:
        parent = _stored_parent(db_connection)

        def mutate(raw: str) -> str:
            data = json.loads(raw)
            data["oversized"] = "x" * 262144
            return canonical_content(data)

        _replace_sample_content(db_connection, parent, mutate)
        with pytest.raises(sa.exc.DBAPIError, match="rating history bounds"):
            db_connection.scalar(
                sa.select(sa.func.forecast_rating_input_history(parent.id))
            )

    def test_current_curve_changes_do_not_rewrite_history(
        self, db_connection: sa.Connection
    ) -> None:
        from sapphire_flow.db.metadata import rating_curves

        parent = _stored_parent(db_connection)
        query = sa.select(sa.func.forecast_rating_input_history(parent.id))
        before = db_connection.scalar(query)
        db_connection.execute(sa.text("SET LOCAL session_replication_role = replica"))
        db_connection.execute(
            sa.update(rating_curves).values(
                points=[
                    {"water_level": 0, "discharge": 0},
                    {"water_level": 1, "discharge": 99999},
                ]
            )
        )
        db_connection.execute(sa.text("SET LOCAL session_replication_role = origin"))
        assert db_connection.scalar(query) == before

    @pytest.mark.parametrize("value", [0.1, 5e-324, 1.7976931348623157e308])
    def test_driver_float8_roundtrip(
        self, db_connection: sa.Connection, value: float
    ) -> None:
        import struct

        actual = db_connection.scalar(
            sa.text("SELECT CAST(:value AS float8)"), {"value": value}
        )
        assert struct.pack("!d", actual) == struct.pack("!d", value)

    def test_real_statement_cancellation_is_sanitized(
        self, db_connection: sa.Connection
    ) -> None:
        from sapphire_flow.api.provisional_forecasts import unavailable

        # The timeout interrupts a local computation, never a sleep or live query.
        db_connection.execute(sa.text("SET LOCAL statement_timeout='1ms'"))
        with pytest.raises(sa.exc.DBAPIError) as error:
            db_connection.execute(
                sa.text("SELECT sum(i) FROM generate_series(1,100000000) AS i")
            )
        response = unavailable("lineage", error.value)
        assert response.status_code == 503
        assert response.detail == "Provisional rating evidence is unavailable"


class TestProjectionBounds:
    def test_parent_bytes_refused_before_json_parse(
        self, db_connection: sa.Connection
    ) -> None:
        from sapphire_flow.db.metadata import forecasts

        parent = _stored_parent(db_connection)
        db_connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
        db_connection.execute(
            sa.update(forecasts)
            .where(forecasts.c.id == parent.id)
            .values(input_lineage="x" * 2097153)
        )
        db_connection.execute(sa.text("SET LOCAL session_replication_role=origin"))
        with pytest.raises(sa.exc.DBAPIError, match="rating history bounds"):
            db_connection.scalar(
                sa.select(sa.func.forecast_rating_input_history(parent.id))
            )

    def test_selected_count_refused_before_row_lookups(
        self, db_connection: sa.Connection
    ) -> None:
        from sapphire_flow.db.metadata import forecasts

        parent = _stored_parent(db_connection)
        data = json.loads(parent.input_lineage.content)
        data["rating_input"]["days"][0]["fingerprints"] = [
            f"{i:064x}" for i in range(10001)
        ]
        db_connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
        db_connection.execute(
            sa.update(forecasts)
            .where(forecasts.c.id == parent.id)
            .values(input_lineage=canonical_content(data))
        )
        db_connection.execute(sa.text("SET LOCAL session_replication_role=origin"))
        with pytest.raises(sa.exc.DBAPIError, match="rating history bounds"):
            db_connection.scalar(
                sa.select(sa.func.forecast_rating_input_history(parent.id))
            )

    def test_capture_bytes_refused_before_parse(
        self, db_connection: sa.Connection
    ) -> None:
        from sapphire_flow.db.metadata import native_measurement_captures
        from sapphire_flow.store.forecast_store import PgForecastStore
        from tests.integration.store.test_rejected_forecast_store import (
            savepoint_factory,
        )

        parent, row = _v2_parent(db_connection)
        PgForecastStore(
            db_connection,
            data_use=parent.data_use,
            transaction_factory=savepoint_factory(db_connection),
        ).store_forecast(parent)
        body = "x" * 262145
        db_connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
        db_connection.execute(
            sa.update(native_measurement_captures)
            .where(native_measurement_captures.c.id == row["capture_id"])
            .values(source_content=body, source_version_sha256=content_digest(body))
        )
        db_connection.execute(sa.text("SET LOCAL session_replication_role=origin"))
        with pytest.raises(sa.exc.DBAPIError, match="rating history bounds"):
            db_connection.scalar(
                sa.select(sa.func.forecast_rating_input_history(parent.id))
            )

    def test_cumulative_bytes_include_all_consumed_bodies(
        self, db_connection: sa.Connection
    ) -> None:
        from dataclasses import replace

        from sapphire_flow.db.metadata import rating_derived_discharge
        from sapphire_flow.store.forecast_store import PgForecastStore
        from tests.integration.store.test_rejected_forecast_store import (
            savepoint_factory,
        )

        parent, _ = _dense_parent(db_connection, 1, count=90)
        replacements = {}
        db_connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
        for row in (
            db_connection.execute(sa.select(rating_derived_discharge)).mappings().all()
        ):
            data = json.loads(row["content"])
            data["bounded_padding"] = "x" * 200000
            content = canonical_content(data)
            fp = content_digest(content)
            replacements[row["fingerprint"]] = fp
            db_connection.execute(
                sa.update(rating_derived_discharge)
                .where(rating_derived_discharge.c.fingerprint == row["fingerprint"])
                .values(fingerprint=fp, content=content)
            )
        db_connection.execute(sa.text("SET LOCAL session_replication_role=origin"))
        lineage = parent.input_lineage
        rating = lineage.rating_input
        parent = replace(
            parent,
            input_lineage=replace(
                lineage,
                rating_input=replace(
                    rating,
                    days=tuple(
                        replace(
                            day,
                            fingerprints=tuple(
                                replacements[fp] for fp in day.fingerprints
                            ),
                        )
                        for day in rating.days
                    ),
                ),
            ),
        )
        PgForecastStore(
            db_connection,
            data_use=parent.data_use,
            transaction_factory=savepoint_factory(db_connection),
        ).store_forecast(parent)
        with pytest.raises(sa.exc.DBAPIError, match="rating history bounds"):
            db_connection.scalar(
                sa.select(sa.func.forecast_rating_input_history(parent.id))
            )

    def test_distinct_curve_snapshot_limit(self, db_connection: sa.Connection) -> None:
        from dataclasses import replace

        from sapphire_flow.db.metadata import rating_derived_discharge
        from sapphire_flow.store.forecast_store import PgForecastStore
        from tests.integration.store.test_rejected_forecast_store import (
            savepoint_factory,
        )

        parent, _ = _dense_parent(db_connection, 1, count=65)
        replacements = {}
        db_connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
        rows = (
            db_connection.execute(sa.select(rating_derived_discharge)).mappings().all()
        )
        for index, row in enumerate(rows):
            data = json.loads(row["content"])
            data["curve"]["version"] = index + 1
            data["reference"]["curve"]["content"] = canonical_content(data["curve"])
            content = canonical_content(data)
            digest = content_digest(content)
            replacements[row["fingerprint"]] = digest
            db_connection.execute(
                sa.update(rating_derived_discharge)
                .where(rating_derived_discharge.c.fingerprint == row["fingerprint"])
                .values(fingerprint=digest, content=content)
            )
        db_connection.execute(sa.text("SET LOCAL session_replication_role=origin"))
        lineage = parent.input_lineage
        rating = lineage.rating_input
        parent = replace(
            parent,
            input_lineage=replace(
                lineage,
                rating_input=replace(
                    rating,
                    days=tuple(
                        replace(
                            day,
                            fingerprints=tuple(
                                replacements[fp] for fp in day.fingerprints
                            ),
                        )
                        for day in rating.days
                    ),
                ),
            ),
        )
        PgForecastStore(
            db_connection,
            data_use=parent.data_use,
            transaction_factory=savepoint_factory(db_connection),
        ).store_forecast(parent)
        with pytest.raises(sa.exc.DBAPIError, match="rating history bounds"):
            db_connection.scalar(
                sa.select(sa.func.forecast_rating_input_history(parent.id))
            )

    def test_projected_output_limit_is_not_truncated(
        self, db_connection: sa.Connection
    ) -> None:
        from sapphire_flow.store.forecast_store import PgForecastStore
        from tests.integration.store.test_rejected_forecast_store import (
            savepoint_factory,
        )

        parent, _ = _dense_parent(
            db_connection,
            1,
            count=4900,
            cadence_minutes=1,
            discharge=1.2345678901234567,
        )
        nodes = db_connection.scalar(
            sa.text("""
            SELECT sum(public.rating_history_json_nodes(content,500000)+
                public.rating_history_json_nodes(content::jsonb->'reference'->'curve'->>'content',500000))
            FROM rating_derived_discharge
        """)
        )
        parent_nodes = db_connection.scalar(
            sa.text("SELECT public.rating_history_json_nodes(:content,500000)"),
            {"content": parent.input_lineage.content},
        )
        assert nodes + parent_nodes < 500000
        PgForecastStore(
            db_connection,
            data_use=parent.data_use,
            transaction_factory=savepoint_factory(db_connection),
        ).store_forecast(parent)
        # Test the output ceiling independently from the shorter API timeout.
        db_connection.execute(sa.text("SET LOCAL statement_timeout='30s'"))
        with pytest.raises(sa.exc.DBAPIError, match="rating history bounds"):
            db_connection.scalar(
                sa.select(sa.func.forecast_rating_input_history(parent.id))
            )


class TestFrozenScientificFlags:
    @pytest.mark.parametrize("assessment", ["assessed", "unassessed", "empty_version"])
    @pytest.mark.parametrize("level_case", ["above", "below", "in_range"])
    def test_endpoint_flags_follow_actual_provisional_conversion(
        self,
        db_connection: sa.Connection,
        level_case: str,
        assessment: str,
    ) -> None:
        from dataclasses import asdict, replace
        from datetime import datetime
        from uuid import UUID

        import polars as pl

        from sapphire_flow.db.metadata import rating_derived_discharge
        from sapphire_flow.services.rating_derived import derive_rating_sample
        from sapphire_flow.services.rating_inputs import merge_rating_days
        from sapphire_flow.store.forecast_store import PgForecastStore
        from sapphire_flow.store.observation_store import PgObservationStore
        from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
        from sapphire_flow.types.rating_derived import NativeFeedCustody
        from sapphire_flow.types.rating_reference import (
            RatingReferenceProof,
            curve_snapshot,
        )
        from tests.integration.store.test_native_rating_forecasts import native_pair
        from tests.integration.store.test_rejected_forecast_store import (
            savepoint_factory,
        )

        _, parent = native_pair(db_connection)
        row = (
            db_connection.execute(sa.select(rating_derived_discharge)).mappings().one()
        )
        data = json.loads(row["content"])
        curve = next(
            c
            for c in PgRatingCurveStore(db_connection).fetch_all_curves_for_station(
                parent.station_id
            )
            if c.id == row["rating_curve_id"]
        )
        obs = PgObservationStore(db_connection).fetch_observations(
            parent.station_id, "water_level", row["timestamp"], parent.issued_at
        )[0]
        levels = sorted(p["water_level"] for p in curve.points)
        value = (
            levels[-1] + 0.5
            if level_case == "above"
            else levels[0] - 0.01
            if level_case == "below"
            else obs.value
        )
        from sapphire_flow.types.domain import QcFlag
        from sapphire_flow.types.enums import QcStatus

        obs = replace(
            obs,
            qc_flags=[
                QcFlag(rule_id="fixture", rule_version="1", status=QcStatus.QC_PASSED)
            ]
            if assessment != "unassessed"
            else [],
            qc_rule_version="1"
            if assessment == "assessed"
            else ""
            if assessment == "empty_version"
            else None,
        )
        sample = derive_rating_sample(
            replace(obs, value=value),
            curve,
            file_id=row["file_id"],
            file_sha256=data["file_sha256"],
            delivered_on=data["delivered_on"],
            feed=NativeFeedCustody(
                **{
                    **data["feed"],
                    "tenant_id": row["tenant_id"],
                    "station_id": row["station_id"],
                    "start": datetime.fromisoformat(data["feed"]["start"]),
                    "end": datetime.fromisoformat(data["feed"]["end"]),
                }
            ),
            reference=RatingReferenceProof(
                **{
                    **data["reference"],
                    "id": UUID(data["reference"]["id"]),
                    "tenant_id": row["tenant_id"],
                    "station_id": row["station_id"],
                    "rating_curve_id": curve.id,
                    "curve": curve_snapshot(curve),
                    "verified_at": datetime.fromisoformat(
                        data["reference"]["verified_at"]
                    ),
                }
            ),
            at=row["timestamp"],
        )
        if sample.fingerprint != row["fingerprint"]:
            payload = asdict(sample)
            payload.pop("warnings")
            db_connection.execute(
                sa.insert(rating_derived_discharge).values(
                    **payload,
                    fingerprint=sample.fingerprint,
                    captured_at=row["timestamp"],
                )
            )
        _, lineage, _ = merge_rating_days(
            pl.DataFrame(),
            (sample,),
            issue_time=parent.issued_at,
            weather_cycle_time=parent.issued_at,
        )
        assert lineage is not None and lineage.rating_input is not None
        parent = replace(
            parent,
            input_lineage=replace(
                lineage,
                rating_input=replace(
                    lineage.rating_input, artifact_id=parent.model_artifact_id
                ),
            ),
        )
        PgForecastStore(
            db_connection,
            data_use=parent.data_use,
            transaction_factory=savepoint_factory(db_connection),
        ).store_forecast(parent)
        raw = db_connection.scalar(
            sa.select(sa.func.forecast_rating_input_history(parent.id))
        )
        assert raw["samples"][0]["discharge"] == sample.discharge
        assert set(raw["samples"][0]["warnings"]) == set(sample.warnings)
        if level_case != "in_range":
            assert "extrapolated_" + level_case in raw["samples"][0]["warnings"]


class TestProjectionFloatSerialization:
    @pytest.mark.parametrize("digits", [0, -15])
    def test_json_projection_preserves_stored_float8(
        self,
        db_connection: sa.Connection,
        digits: int,
    ) -> None:
        import struct

        from sapphire_flow.db.metadata import rating_derived_discharge

        parent = _stored_parent(db_connection)
        value = 1.2345678901234567

        def mutate(raw: str) -> str:
            data = json.loads(raw)
            data["discharge"] = value
            return canonical_content(data)

        _replace_sample_content(db_connection, parent, mutate)
        db_connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
        db_connection.execute(
            sa.update(rating_derived_discharge).values(discharge=value)
        )
        db_connection.execute(sa.text("SET LOCAL session_replication_role=origin"))
        db_connection.execute(
            sa.text("SELECT set_config('extra_float_digits',:digits,true)"),
            {"digits": str(digits)},
        )
        result = db_connection.scalar(
            sa.select(sa.func.forecast_rating_input_history(parent.id))
        )
        actual = result["samples"][0]["discharge"]
        assert struct.pack("!d", actual) == struct.pack("!d", value)


class TestParentLineageIntegrity:
    def test_empty_parent_days_are_unavailable_not_missing_input(
        self,
        db_connection: sa.Connection,
    ) -> None:
        from uuid import uuid4

        from fastapi import HTTPException

        from sapphire_flow.api.forecast_rating_history import (
            ForecastRatingHistoryReader,
        )
        from sapphire_flow.api.security import Principal
        from sapphire_flow.db.metadata import forecasts
        from sapphire_flow.types.enums import AccessTokenRole
        from sapphire_flow.types.ids import AccessTokenId

        parent = _stored_parent(db_connection)
        data = json.loads(parent.input_lineage.content)
        data["rating_input"]["days"] = []
        db_connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
        db_connection.execute(
            sa.update(forecasts)
            .where(forecasts.c.id == parent.id)
            .values(input_lineage=canonical_content(data))
        )
        db_connection.execute(sa.text("SET LOCAL session_replication_role=origin"))
        principal = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            station_ids=frozenset(),
        )
        with pytest.raises(HTTPException) as error:
            ForecastRatingHistoryReader(db_connection, principal).read(parent.id)
        assert error.value.status_code == 503
        assert error.value.detail == "Provisional rating evidence is unavailable"

    def test_valid_parent_with_only_older_support_returns_honest_null_slots(
        self,
        db_connection: sa.Connection,
    ) -> None:
        from dataclasses import replace
        from datetime import timedelta

        from pydantic import TypeAdapter

        from sapphire_flow.services.forecast_rating_history import (
            build_forecast_rating_history,
        )
        from sapphire_flow.store.forecast_store import PgForecastStore
        from sapphire_flow.types.forecast_rating_history import RatingHistoryEvidence
        from tests.integration.store.test_native_rating_forecasts import native_pair
        from tests.integration.store.test_rejected_forecast_store import (
            savepoint_factory,
        )

        _, parent = native_pair(db_connection)
        lineage = parent.input_lineage
        issue = parent.issued_at + timedelta(days=10)
        parent = replace(
            parent,
            issued_at=issue,
            input_lineage=replace(
                lineage, rating_input=replace(lineage.rating_input, issue_time=issue)
            ),
        )
        PgForecastStore(
            db_connection,
            data_use=parent.data_use,
            transaction_factory=savepoint_factory(db_connection),
        ).store_forecast(parent)
        raw = db_connection.scalar(
            sa.select(sa.func.forecast_rating_input_history(parent.id))
        )
        history = build_forecast_rating_history(
            TypeAdapter(RatingHistoryEvidence).validate_python(raw)
        )
        assert len(history.days) == 7
        assert all(
            d.status == "no_retained_derived_day" and d.daily_discharge is None
            for d in history.days
        )


class TestFrozenReferenceIntegrity:
    @pytest.mark.parametrize("version", [1, 2])
    @pytest.mark.parametrize("mismatch", ["endpoint", "api_station_id", "curve"])
    def test_rehashed_reference_mismatch_is_unavailable(
        self,
        db_connection: sa.Connection,
        version: int,
        mismatch: str,
    ) -> None:
        from uuid import uuid4

        from fastapi import HTTPException

        from sapphire_flow.api.forecast_rating_history import (
            ForecastRatingHistoryReader,
        )
        from sapphire_flow.api.security import Principal
        from sapphire_flow.store.forecast_store import PgForecastStore
        from sapphire_flow.types.enums import AccessTokenRole
        from sapphire_flow.types.ids import AccessTokenId
        from tests.integration.store.test_rejected_forecast_store import (
            savepoint_factory,
        )

        if version == 2:
            parent, _ = _v2_parent(db_connection)
            PgForecastStore(
                db_connection,
                data_use=parent.data_use,
                transaction_factory=savepoint_factory(db_connection),
            ).store_forecast(parent)
        else:
            parent = _stored_parent(db_connection)

        def mutate(raw: str) -> str:
            data = json.loads(raw)
            if mismatch == "curve":
                curve = json.loads(data["reference"]["curve"]["content"])
                curve["points"][0]["discharge"] += 1
                data["reference"]["curve"]["content"] = canonical_content(curve)
            elif mismatch == "endpoint":
                data["reference"]["endpoint"] = "https://other.invalid/levels/"
            else:
                data["reference"]["api_station_id"] = 999
            return canonical_content(data)

        _replace_sample_content(db_connection, parent, mutate)
        principal = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            station_ids=frozenset(),
        )
        with pytest.raises(HTTPException) as error:
            ForecastRatingHistoryReader(db_connection, principal).read(parent.id)
        assert error.value.status_code == 503


class TestFrozenJsonTypes:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("discharge", "same"),
            ("qc_rule_version", 1),
            ("qc_rule_version", True),
            ("qc_rule_version", {}),
            ("qc_rule_version", "missing"),
        ],
    )
    @pytest.mark.parametrize("version", [1, 2])
    def test_producer_fields_cannot_be_coerced(
        self,
        db_connection: sa.Connection,
        field: str,
        value: object,
        version: int,
    ) -> None:
        from sapphire_flow.store.forecast_store import PgForecastStore
        from tests.integration.store.test_rejected_forecast_store import (
            savepoint_factory,
        )

        if version == 2:
            parent, _ = _v2_parent(db_connection)
            PgForecastStore(
                db_connection,
                data_use=parent.data_use,
                transaction_factory=savepoint_factory(db_connection),
            ).store_forecast(parent)
        else:
            parent = _stored_parent(db_connection)

        def mutate(raw: str) -> str:
            data = json.loads(raw)
            if field == "discharge":
                data[field] = str(data[field])
            elif value == "missing":
                data["source"].pop(field)
            else:
                data["source"][field] = value
            return canonical_content(data)

        _replace_sample_content(db_connection, parent, mutate)
        with pytest.raises(sa.exc.DBAPIError, match="rating history sample"):
            db_connection.scalar(
                sa.select(sa.func.forecast_rating_input_history(parent.id))
            )


class TestReferenceProducerCompatibility:
    @pytest.mark.parametrize(
        "endpoint,curve_spelling",
        [
            ("HTTPS://EXAMPLE.INVALID:00443/levels", "whitespace"),
            ("https://example.invalid:/levels/?#", "whitespace"),
            ("https://example.invalid/levels///", "whitespace"),
            ("https://example.invalid/levels/", "rounded_decimal"),
        ],
    )
    def test_v2_producer_reference_representations(
        self,
        db_connection: sa.Connection,
        endpoint: str,
        curve_spelling: str,
    ) -> None:
        from dataclasses import asdict
        from uuid import UUID

        from sapphire_flow.db.metadata import forecasts
        from sapphire_flow.store.forecast_store import PgForecastStore
        from sapphire_flow.types.native_capture import canonical_provider_endpoint
        from sapphire_flow.types.rating_reference import (
            AssumedCurveSnapshot,
            OwnerAssumedZeroReference,
            assumed_curve_content,
        )
        from tests.integration.store.test_rejected_forecast_store import (
            savepoint_factory,
        )

        parent, row = _v2_parent(db_connection)
        data = json.loads(row["content"])
        assert (
            canonical_provider_endpoint(endpoint)
            == data["feed"]["provider"]["endpoint"]
        )
        curve_content = json.dumps(data["curve"], indent=2)
        if curve_spelling == "rounded_decimal":
            token = '"water_level": 0.1'
            assert token in curve_content
            curve_content = curve_content.replace(
                token, '"water_level": 0.10000000000000000001', 1
            )
        assert assumed_curve_content(curve_content) == canonical_content(data["curve"])
        reference = OwnerAssumedZeroReference(
            id=UUID(data["reference"]["id"]),
            tenant_id=row["tenant_id"],
            station_id=row["station_id"],
            rating_curve_id=row["rating_curve_id"],
            curve=AssumedCurveSnapshot(content=curve_content),
            endpoint=endpoint,
            api_station_id=1,
            level_unit="m",
            curve_unit="m",
            level_unit_evidence_reference="fixture",
            level_unit_evidence_sha256="a" * 64,
            curve_unit_evidence_reference="fixture",
            curve_unit_evidence_sha256="b" * 64,
            assumed_by="fixture",
            assumed_at=row["timestamp"],
            authority_reference="fixture",
            authority_sha256="c" * 64,
            binding_by="fixture",
            binding_at=row["timestamp"],
            binding_reference="fixture",
            binding_sha256="d" * 64,
        )
        PgForecastStore(
            db_connection,
            data_use=parent.data_use,
            transaction_factory=savepoint_factory(db_connection),
        ).store_forecast(parent)

        def mutate(raw: str) -> str:
            obj = json.loads(raw)
            obj["reference"] = asdict(reference)
            obj["warnings"].append("owner_assumed_same_zero_unverified")
            return canonical_content(obj)

        _replace_sample_content(db_connection, parent, mutate)
        content = db_connection.scalar(
            sa.select(forecasts.c.input_lineage).where(forecasts.c.id == parent.id)
        )
        lineage = json.loads(content)
        lineage["rating_input"]["days"][0]["warnings"].append(
            "owner_assumed_same_zero_unverified"
        )
        db_connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
        db_connection.execute(
            sa.update(forecasts)
            .where(forecasts.c.id == parent.id)
            .values(input_lineage=canonical_content(lineage))
        )
        db_connection.execute(sa.text("SET LOCAL session_replication_role=origin"))
        if curve_spelling == "rounded_decimal":
            from uuid import uuid4

            from fastapi import HTTPException

            from sapphire_flow.api.forecast_rating_history import (
                ForecastRatingHistoryReader,
            )
            from sapphire_flow.api.security import Principal
            from sapphire_flow.types.enums import AccessTokenRole
            from sapphire_flow.types.ids import AccessTokenId

            principal = Principal(
                token_id=AccessTokenId(uuid4()),
                role=AccessTokenRole.ADMIN,
                tenant_id=None,
                station_ids=frozenset(),
            )
            with pytest.raises(HTTPException) as error:
                ForecastRatingHistoryReader(db_connection, principal).read(parent.id)
            assert error.value.status_code == 503
            assert error.value.detail == "Provisional rating evidence is unavailable"
            return
        result = db_connection.scalar(
            sa.select(sa.func.forecast_rating_input_history(parent.id))
        )
        assert result["samples"][0]["discharge"] == row["discharge"]
        assert result["samples"][0]["curve_digest"] == content_digest(
            canonical_content(data["curve"])
        )


class TestDatabaseErrorSanitization:
    @pytest.mark.parametrize("field", ["uuid", "timestamp", "json"])
    def test_native_cast_error_does_not_echo_protected_content(
        self,
        db_connection: sa.Connection,
        field: str,
    ) -> None:
        parent = _stored_parent(db_connection)
        marker = "private-marker-invalid-value"

        def mutate(raw: str) -> str:
            data = json.loads(raw)
            if field == "uuid":
                data["source"]["id"] = marker
            elif field == "timestamp":
                data["source"]["timestamp"] = marker + "+00:00"
            else:
                data["reference"]["curve"]["content"] = marker
            return canonical_content(data)

        _replace_sample_content(db_connection, parent, mutate)
        with pytest.raises(
            sa.exc.DBAPIError, match="rating history malformed evidence"
        ) as error:
            db_connection.scalar(
                sa.select(sa.func.forecast_rating_input_history(parent.id))
            )
        assert marker not in str(error.value.orig)


class TestFrozenNullDischarge:
    @pytest.mark.parametrize("version", [1, 2])
    def test_valid_rejected_null_and_accepted_numeric_coexist(
        self,
        db_connection: sa.Connection,
        version: int,
    ) -> None:
        from sapphire_flow.db.metadata import forecasts, rating_derived_discharge
        from sapphire_flow.store.forecast_store import PgForecastStore
        from tests.integration.store.test_rejected_forecast_store import (
            savepoint_factory,
        )

        parent, samples = _dense_parent(db_connection, version, count=2)
        PgForecastStore(
            db_connection,
            data_use=parent.data_use,
            transaction_factory=savepoint_factory(db_connection),
        ).store_forecast(parent)

        def mutate(raw: str) -> str:
            data = json.loads(raw)
            data["source"]["qc_status"] = "qc_failed"
            data["discharge"] = None
            data["endpoints"] = None
            data["warnings"] = [
                w for w in data["warnings"] if not w.startswith("extrapolated_")
            ]
            data["rejection"] = "source_qc_rejected"
            return canonical_content(data)

        _replace_sample_content(db_connection, parent, mutate, samples[0].fingerprint)
        content = db_connection.scalar(
            sa.select(forecasts.c.input_lineage).where(forecasts.c.id == parent.id)
        )
        lineage = json.loads(content)
        day = lineage["rating_input"]["days"][0]
        day.update(
            accepted=1,
            extrapolated=1,
            rejected=1,
            unassessed=1,
            first=samples[1].timestamp.isoformat(),
            last=samples[1].timestamp.isoformat(),
        )
        db_connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
        db_connection.execute(
            sa.update(forecasts)
            .where(forecasts.c.id == parent.id)
            .values(input_lineage=canonical_content(lineage))
        )
        db_connection.execute(
            sa.update(rating_derived_discharge)
            .where(
                rating_derived_discharge.c.observation_id == samples[0].observation_id
            )
            .values(discharge=None, rejection="source_qc_rejected")
        )
        db_connection.execute(sa.text("SET LOCAL session_replication_role=origin"))
        raw = db_connection.scalar(
            sa.select(sa.func.forecast_rating_input_history(parent.id))
        )
        projected = sorted(raw["samples"], key=lambda sample: sample["timestamp"])
        assert projected[0]["discharge"] is None
        assert projected[0]["rejection"] == "source_qc_rejected"
        assert projected[1]["discharge"] == samples[1].discharge


class TestFrozenCustody:
    @pytest.mark.parametrize(
        "case",
        [
            "missing_start",
            "missing_end",
            "empty_interval",
            "before_start",
            "at_end",
            "numeric_start",
            "empty_evidence",
        ],
    )
    def test_invalid_frozen_custody_is_unavailable(
        self,
        db_connection: sa.Connection,
        case: str,
    ) -> None:
        from datetime import datetime, timedelta
        from uuid import uuid4

        from fastapi import HTTPException

        from sapphire_flow.api.forecast_rating_history import (
            ForecastRatingHistoryReader,
        )
        from sapphire_flow.api.security import Principal
        from sapphire_flow.types.enums import AccessTokenRole
        from sapphire_flow.types.ids import AccessTokenId

        parent = _stored_parent(db_connection)

        def mutate(raw: str) -> str:
            data = json.loads(raw)
            feed = data["feed"]
            timestamp = data["source"]["timestamp"]
            if case == "missing_start":
                feed.pop("start")
            elif case == "missing_end":
                feed.pop("end")
            elif case == "empty_interval":
                feed["end"] = feed["start"]
            elif case == "before_start":
                feed["start"] = (
                    datetime.fromisoformat(timestamp) + timedelta(seconds=1)
                ).isoformat()
            elif case == "at_end":
                feed["end"] = timestamp
            elif case == "numeric_start":
                feed["start"] = 1
            else:
                feed["evidence_reference"] = " "
            return canonical_content(data)

        _replace_sample_content(db_connection, parent, mutate)
        principal = Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            station_ids=frozenset(),
        )
        with pytest.raises(HTTPException) as error:
            ForecastRatingHistoryReader(db_connection, principal).read(parent.id)
        assert error.value.status_code == 503

    def test_custody_start_is_inclusive(self, db_connection: sa.Connection) -> None:
        parent = _stored_parent(db_connection)

        def mutate(raw: str) -> str:
            data = json.loads(raw)
            data["feed"]["start"] = data["source"]["timestamp"]
            return canonical_content(data)

        _replace_sample_content(db_connection, parent, mutate)
        result = db_connection.scalar(
            sa.select(sa.func.forecast_rating_input_history(parent.id))
        )
        assert len(result["samples"]) == 1
        assert result["samples"][0]["discharge"] is not None
