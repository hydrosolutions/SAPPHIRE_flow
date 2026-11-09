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
    conn: sa.Connection, version: int, count: int = 2016, cadence_minutes: int = 5
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
    observation = dict(conn.execute(sa.select(observations)).mappings().one())
    start = parent.issued_at.replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - timedelta(days=7)
    samples = []
    for index in range(count):
        timestamp = start + timedelta(minutes=cadence_minutes * index)
        row = dict(template)
        data = json.loads(template["content"])
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
    conn: sa.Connection, parent: OperationalForecast, transform: Callable[[str], str]
) -> None:
    from sapphire_flow.db.metadata import forecasts, rating_derived_discharge

    row = conn.execute(sa.select(rating_derived_discharge)).mappings().one()
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
            data["curve"]["version"] = f"frozen-{index}"
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

        parent, _ = _dense_parent(db_connection, 1, count=5200, cadence_minutes=1)
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
    @pytest.mark.parametrize("level_case", ["above", "below", "in_range"])
    def test_endpoint_flags_follow_actual_provisional_conversion(
        self,
        db_connection: sa.Connection,
        level_case: str,
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
