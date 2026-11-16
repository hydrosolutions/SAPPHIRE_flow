from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
from pydantic import TypeAdapter

from sapphire_flow.db.metadata import observations, rating_derived_discharge
from sapphire_flow.types.rating_reference import canonical_content, content_digest
from sapphire_flow.types.station_history import HistoryOutcome, HistorySample
from tests.integration.store.test_forecast_rating_history import _v2_parent
from tests.integration.store.test_native_rating_forecasts import native_pair

if TYPE_CHECKING:
    from collections.abc import Callable


def reader_request(row: dict, *, standard: bool = False):
    from sapphire_flow.types.enums import ForecastDataUse
    from sapphire_flow.types.station_history import (
        HistoryResolution,
        StationHistoryRequest,
    )

    return StationHistoryRequest(
        station_id=row["station_id"],
        start=row["timestamp"],
        end=row["timestamp"] + timedelta(hours=1),
        generated_at=row["timestamp"] + timedelta(hours=1),
        resolution=HistoryResolution.NATIVE,
        purposes=(ForecastDataUse.STANDARD,)
        if standard
        else (ForecastDataUse.STANDARD, ForecastDataUse.PROVISIONAL_RATING),
    )


def reader(conn: sa.Connection, row: dict, role: str = "reviewer"):
    from sapphire_flow.api.security import Principal
    from sapphire_flow.api.station_history import StationHistoryReader
    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.config.provisional_rating import ProvisionalRatingConfig
    from sapphire_flow.types.enums import AccessTokenRole
    from sapphire_flow.types.ids import AccessTokenId

    principal = Principal(
        token_id=AccessTokenId(uuid4()),
        role=AccessTokenRole(role),
        tenant_id=row["tenant_id"],
        station_ids=frozenset({row["station_id"]}),
    )
    config = DeploymentConfig.model_construct(
        provisional_ratings=(
            ProvisionalRatingConfig.model_construct(station_id=row["station_id"]),
        )
    )
    return StationHistoryReader(conn, principal, config)


def seed(conn: sa.Connection, version: int = 1) -> dict:
    if version == 2:
        _v2_parent(conn)
    else:
        native_pair(conn)
    return dict(
        conn.execute(
            sa.select(rating_derived_discharge)
            .order_by(rating_derived_discharge.c.stored_order.desc())
            .limit(1)
        )
        .mappings()
        .one()
    )


def read(conn: sa.Connection, row: dict, enabled: bool = True) -> dict:
    return conn.scalar(
        sa.select(
            sa.func.station_rating_history(
                row["tenant_id"],
                row["station_id"],
                row["timestamp"],
                row["timestamp"] + timedelta(hours=1),
                enabled,
            )
        )
    )


class TestStationHistoryProjection:
    @pytest.mark.parametrize("version", [1, 2])
    def test_matching_assessed_source_keeps_stored_warnings(
        self, db_connection: sa.Connection, version: int
    ) -> None:
        row = seed(db_connection, version)
        flags = [
            {
                "rule_id": "test",
                "rule_version": "1",
                "status": "qc_passed",
                "detail": None,
            }
        ]
        db_connection.execute(
            sa.update(observations)
            .where(observations.c.id == row["observation_id"])
            .values(qc_flags=flags, qc_rule_version="rules-v1")
        )
        body = json.loads(row["content"])
        body["source"].update(qc_flags=flags, qc_rule_version="rules-v1")
        body["warnings"].remove("qc_unassessed")
        newer = {
            k: v
            for k, v in row.items()
            if k not in {"stored_order", "stored_order_basis"}
        }
        newer["content"] = canonical_content(body)
        newer["fingerprint"] = content_digest(newer["content"])
        db_connection.execute(sa.insert(rating_derived_discharge).values(**newer))
        sample = read(db_connection, row)["samples"][0]
        assert sample["source_assessment"] == "assessed"
        assert sample["value"] == row["discharge"]
        assert set(sample["warnings"]) == {
            "rating_expired_at_measurement",
            "extrapolated_above",
        }

    def test_newest_timestamp_outside_window_is_still_selected(
        self, db_connection: sa.Connection
    ) -> None:
        row = seed(db_connection)
        body = json.loads(row["content"])
        changed_time = row["timestamp"] + timedelta(days=10)
        body["source"]["timestamp"] = changed_time.isoformat()
        body["feed"].update(
            start=changed_time.isoformat(),
            end=(changed_time + timedelta(days=1)).isoformat(),
        )
        newer = {
            k: v
            for k, v in row.items()
            if k not in {"stored_order", "stored_order_basis"}
        }
        newer.update(timestamp=changed_time, content=canonical_content(body))
        newer["fingerprint"] = content_digest(newer["content"])
        db_connection.execute(sa.insert(rating_derived_discharge).values(**newer))
        result = read(db_connection, row)
        assert result["candidate_count"] == 3
        assert result["samples"][0]["reason"] == "source_changed"
        assert result["samples"][0]["value"] is None

    @pytest.mark.parametrize("version", [1, 2])
    @pytest.mark.parametrize("missing", [True, False])
    def test_malformed_frozen_qc_is_unavailable(
        self, db_connection: sa.Connection, version: int, missing: bool
    ) -> None:
        row = seed(db_connection, version)
        body = json.loads(row["content"])
        if missing:
            body["source"].pop("qc_rule_version")
        else:
            body["source"]["qc_rule_version"] = True
        newer = {
            k: v
            for k, v in row.items()
            if k not in {"stored_order", "stored_order_basis"}
        }
        newer["content"] = canonical_content(body)
        newer["fingerprint"] = content_digest(newer["content"])
        db_connection.execute(sa.insert(rating_derived_discharge).values(**newer))
        with pytest.raises(sa.exc.DBAPIError, match="station history identity"):
            read(db_connection, row)

    def test_no_sources_returns_empty_without_fabricated_timestamp(
        self, db_connection: sa.Connection
    ) -> None:
        row = seed(db_connection)
        row["timestamp"] += timedelta(days=20)
        assert read(db_connection, row)["samples"] == []

    def test_retained_versions_are_counted_before_selection(
        self, db_connection: sa.Connection
    ) -> None:
        row = seed(db_connection)
        base = {
            k: v
            for k, v in row.items()
            if k not in {"stored_order", "stored_order_basis"}
        }
        # The cap must fire before reading or validating any of these older bodies.
        body = json.loads(row["content"])
        contents = [
            canonical_content({**body, "test_version": i}) for i in range(10000)
        ]
        db_connection.execute(
            sa.insert(rating_derived_discharge),
            [
                {**base, "content": content, "fingerprint": content_digest(content)}
                for content in contents
            ],
        )
        with pytest.raises(sa.exc.DBAPIError, match="station history bounds"):
            read(db_connection, row)

    @pytest.mark.parametrize("invalid", ["digest", "bytes", "identity"])
    def test_invalid_newest_evidence_fails_without_older_fallback(
        self, db_connection: sa.Connection, invalid: str
    ) -> None:
        row = seed(db_connection)
        body = json.loads(row["content"])
        if invalid == "bytes":
            body["padding"] = "x" * 262144
        else:
            body["source"]["id"] = "private-marker-invalid-uuid"
        newer = {
            k: v
            for k, v in row.items()
            if k not in {"stored_order", "stored_order_basis"}
        }
        newer["content"] = canonical_content(body)
        newer["fingerprint"] = (
            "f" * 64 if invalid == "digest" else content_digest(newer["content"])
        )
        if invalid == "digest":
            # Simulate corrupted retained storage; transaction rollback restores
            # the insertion guard after this isolated database test.
            db_connection.execute(
                sa.text(
                    "ALTER TABLE rating_derived_discharge "
                    "DROP CONSTRAINT ck_rating_derived_digest"
                )
            )
        db_connection.execute(sa.insert(rating_derived_discharge).values(**newer))
        with pytest.raises(sa.exc.DBAPIError, match="station history") as error:
            read(db_connection, row)
        assert "private-marker" not in str(error.value.orig)

    @pytest.mark.parametrize(
        "value,literal",
        [(float("nan"), "NaN"), (float("inf"), "+Inf"), (-float("inf"), "-Inf")],
    )
    def test_nonfinite_source_keeps_rejected_result(
        self, db_connection: sa.Connection, value: float, literal: str
    ) -> None:
        row = seed(db_connection)
        db_connection.execute(
            sa.update(observations)
            .where(observations.c.id == row["observation_id"])
            .values(value=value)
        )
        body = json.loads(row["content"])
        body["source"]["value"] = {"nonfinite_float": literal}
        body.update(discharge=None, rejection="nonfinite_source_value", endpoints=None)
        newer = {
            k: v
            for k, v in row.items()
            if k not in {"stored_order", "stored_order_basis"}
        }
        newer.update(
            discharge=None,
            rejection="nonfinite_source_value",
            content=canonical_content(body),
        )
        newer["fingerprint"] = content_digest(newer["content"])
        db_connection.execute(sa.insert(rating_derived_discharge).values(**newer))
        sample = read(db_connection, row)["samples"][0]
        assert sample["outcome"] == "rejected" and sample["value"] is None

    @pytest.mark.parametrize("version", [1, 2])
    def test_retained_result_and_exact_curve_digest(
        self, db_connection: sa.Connection, version: int
    ) -> None:
        row = seed(db_connection, version)
        raw = read(db_connection, row)
        samples = TypeAdapter(tuple[HistorySample, ...]).validate_python(raw["samples"])
        assert len(samples) == 1
        sample = samples[0]
        assert sample.outcome is HistoryOutcome.ACCEPTED
        assert sample.value == row["discharge"]
        assert sample.stored_at == row["captured_at"]
        assert sample.curve.content_sha256 == content_digest(
            canonical_content(json.loads(row["content"])["curve"])
        )

    @pytest.mark.parametrize("version", [1, 2])
    @pytest.mark.parametrize(
        "change",
        [
            {"value": 0.5},
            {"qc_status": "qc_suspect"},
            {"qc_rule_version": "changed"},
            {
                "qc_flags": [
                    {
                        "rule_id": "test",
                        "rule_version": "1",
                        "status": "qc_suspect",
                        "detail": None,
                    }
                ]
            },
        ],
    )
    def test_source_or_qc_change_is_held(
        self, db_connection: sa.Connection, version: int, change: dict
    ) -> None:
        row = seed(db_connection, version)
        db_connection.execute(
            sa.update(observations)
            .where(observations.c.id == row["observation_id"])
            .values(**change)
        )
        sample = read(db_connection, row)["samples"][0]
        assert (sample["outcome"], sample["reason"], sample["value"]) == (
            "held",
            "source_changed",
            None,
        )

    def test_newest_nonmatching_success_cannot_resurrect_older_match(
        self, db_connection: sa.Connection
    ) -> None:
        row = seed(db_connection)
        body = json.loads(row["content"])
        body["source"]["value"] = 0.7
        newer = {
            k: v
            for k, v in row.items()
            if k not in {"stored_order", "stored_order_basis"}
        }
        newer["content"] = canonical_content(body)
        newer["fingerprint"] = content_digest(newer["content"])
        db_connection.execute(sa.insert(rating_derived_discharge).values(**newer))
        result = read(db_connection, row)
        assert result["candidate_count"] == 3
        assert result["samples"][0]["outcome"] == "held"

    def test_latest_rejection_is_not_replaced_by_success(
        self, db_connection: sa.Connection
    ) -> None:
        row = seed(db_connection)
        body = json.loads(row["content"])
        body.update(discharge=None, rejection="source_qc_rejected", endpoints=None)
        body["source"]["qc_status"] = "qc_failed"
        db_connection.execute(
            sa.update(observations)
            .where(observations.c.id == row["observation_id"])
            .values(qc_status="qc_failed")
        )
        newer = {
            k: v
            for k, v in row.items()
            if k not in {"stored_order", "stored_order_basis"}
        }
        newer.update(
            discharge=None,
            rejection="source_qc_rejected",
            content=canonical_content(body),
        )
        newer["fingerprint"] = content_digest(newer["content"])
        db_connection.execute(sa.insert(rating_derived_discharge).values(**newer))
        sample = read(db_connection, row)["samples"][0]
        assert (sample["outcome"], sample["reason"], sample["value"]) == (
            "rejected",
            "source_qc_rejected",
            None,
        )

    def test_missing_configuration_is_held(self, db_connection: sa.Connection) -> None:
        row = seed(db_connection)
        assert (
            read(db_connection, row, False)["samples"][0]["reason"] == "not_configured"
        )

    def test_missing_conversion_is_held(self, db_connection: sa.Connection) -> None:
        row = seed(db_connection)
        obs = dict(db_connection.execute(sa.select(observations)).mappings().one())
        obs.update(id=uuid4(), timestamp=row["timestamp"] + timedelta(minutes=10))
        db_connection.execute(sa.insert(observations).values(**obs))
        samples = read(db_connection, row)["samples"]
        assert [sample["outcome"] for sample in samples] == ["accepted", "held"]
        assert [sample["reason"] for sample in samples] == [None, "unreconciled"]
        assert samples[0]["timestamp"] < samples[1]["timestamp"]

    def test_wrong_tenant_is_rejected(self, db_connection: sa.Connection) -> None:
        row = seed(db_connection)
        row["tenant_id"] = uuid4()
        with pytest.raises(sa.exc.DBAPIError, match="station history scope"):
            read(db_connection, row)


class TestStationHistoryReader:
    @pytest.mark.parametrize(
        "extra_curve_points,within_cap", [(180, True), (450, False)]
    )
    def test_real_sized_frozen_evidence_byte_budget(
        self, db_connection: sa.Connection, extra_curve_points: int, within_cap: bool
    ) -> None:
        from tests.integration.store.test_forecast_rating_history import _dense_parent

        parent, samples = _dense_parent(
            db_connection,
            2,
            count=944,
            cadence_minutes=10,
            extra_curve_points=extra_curve_points,
        )
        row = dict(
            db_connection.execute(sa.select(rating_derived_discharge).limit(1))
            .mappings()
            .one()
        )
        start = parent.issued_at - timedelta(days=7)
        sizes = db_connection.execute(
            sa.text("""
            WITH latest AS (
                SELECT DISTINCT ON (d.observation_id) d.*
                FROM rating_derived_discharge d
                JOIN observations o ON o.id=d.observation_id
                WHERE o.station_id=:station AND o.timestamp>=:start AND o.timestamp<:end
                ORDER BY d.observation_id,d.stored_order DESC
            ) SELECT sum(octet_length(d.content)+octet_length(c.source_content)
                +octet_length(c.evidence_content)),
                max(octet_length(d.content))
                FROM latest d JOIN native_measurement_captures c ON c.id=d.capture_id
        """),
            {"station": row["station_id"], "start": start, "end": parent.issued_at},
        ).one()
        assert sizes[0] > 16 * 1024 * 1024
        assert sizes[1] < 256 * 1024
        assert (sizes[0] <= 32 * 1024 * 1024) is within_cap
        query = sa.select(
            sa.func.station_rating_history(
                row["tenant_id"], row["station_id"], start, parent.issued_at, True
            )
        )
        db_connection.execute(sa.text("SET LOCAL statement_timeout='5s'"))
        if within_cap:
            result = db_connection.scalar(query)
            assert len(result["samples"]) == len(samples) == 944
            assert all(sample["value"] is not None for sample in result["samples"])
        else:
            with pytest.raises(sa.exc.DBAPIError, match="station history bounds"):
                db_connection.scalar(query)

    def test_seven_day_read_with_retained_out_of_window_history(
        self,
        db_connection: sa.Connection,
        record_property: Callable[[str, object], None],
    ) -> None:
        from dataclasses import replace
        from time import monotonic

        from tests.integration.store.test_forecast_rating_history import _dense_parent

        parent, samples = _dense_parent(db_connection, 1)
        row = dict(
            db_connection.execute(sa.select(rating_derived_discharge).limit(1))
            .mappings()
            .one()
        )
        obs = dict(
            db_connection.execute(
                sa.select(observations).where(
                    observations.c.id == row["observation_id"]
                )
            )
            .mappings()
            .one()
        )
        query = replace(
            reader_request(row),
            start=parent.issued_at - timedelta(days=7),
            end=parent.issued_at,
            generated_at=parent.issued_at,
        )
        old_observations = [
            {
                **obs,
                "id": uuid4(),
                "timestamp": query.start
                - timedelta(days=3)
                + timedelta(seconds=10 * i),
            }
            for i in range(20000)
        ]
        db_connection.execute(sa.insert(observations), old_observations)
        template = {
            k: v
            for k, v in row.items()
            if k not in {"stored_order", "stored_order_basis"}
        }
        retained = []
        for old in old_observations:
            body = json.loads(row["content"])
            body["observation_id"] = str(old["id"])
            body["source"].update(
                id=str(old["id"]), timestamp=old["timestamp"].isoformat()
            )
            body["feed"].update(
                start=(query.start - timedelta(days=3)).isoformat(),
                end=query.start.isoformat(),
            )
            content = canonical_content(body)
            retained.append(
                {
                    **template,
                    "observation_id": old["id"],
                    "timestamp": old["timestamp"],
                    "content": content,
                    "fingerprint": content_digest(content),
                }
            )
        db_connection.execute(sa.insert(rating_derived_discharge), retained)
        db_connection.execute(sa.text("ANALYZE observations"))
        db_connection.execute(sa.text("ANALYZE rating_derived_discharge"))
        plan = db_connection.scalar(
            sa.text("""
            EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
            SELECT fingerprint FROM rating_derived_discharge
            WHERE tenant_id=:tenant AND station_id=:station
             AND observation_id=:observation
            ORDER BY stored_order DESC LIMIT 1
        """),
            {
                "tenant": row["tenant_id"],
                "station": row["station_id"],
                "observation": row["observation_id"],
            },
        )
        assert "ix_rating_derived_observation_latest" in json.dumps(plan)
        started = monotonic()
        result = reader(db_connection, row).read(query)
        record_property("reader_seconds", monotonic() - started)
        record_property("lookup_plan", json.dumps(plan))
        assert result.series[2].counts.accepted == len(samples) == 2016

    def test_seven_day_swiss_ordinary_history_keeps_all_samples(
        self, db_connection: sa.Connection
    ) -> None:
        row = seed(db_connection)
        obs = dict(db_connection.execute(sa.select(observations)).mappings().one())
        db_connection.execute(
            sa.insert(observations),
            [
                {
                    **obs,
                    "id": uuid4(),
                    "parameter": "discharge",
                    "value": float(i),
                    "timestamp": row["timestamp"] + timedelta(minutes=5 * i),
                }
                for i in range(2016)
            ],
        )
        from dataclasses import replace

        query = replace(
            reader_request(row, standard=True),
            end=row["timestamp"] + timedelta(days=7),
            generated_at=row["timestamp"] + timedelta(days=7),
        )
        result = reader(db_connection, row, "consumer").read(query)
        assert result.series[0].counts.accepted == 2016
        assert result.series[0].points[-1].value == 2015.0

    @pytest.mark.parametrize(
        "status,value,accepted",
        [
            ("raw", 3.0, 1),
            ("qc_unchecked", 3.0, 1),
            ("qc_suspect", 3.0, 1),
            ("qc_failed", 3.0, 0),
            ("missing", None, 0),
        ],
    )
    def test_ordinary_qc_eligibility(
        self,
        db_connection: sa.Connection,
        status: str,
        value: float | None,
        accepted: int,
    ) -> None:
        row = seed(db_connection)
        obs = dict(db_connection.execute(sa.select(observations)).mappings().one())
        obs.update(
            id=uuid4(),
            parameter="discharge",
            source="manual_import",
            value=value,
            qc_status=status,
        )
        db_connection.execute(sa.insert(observations).values(**obs))
        result = reader(db_connection, row).read(reader_request(row, standard=True))
        assert result.series[1].counts.accepted == accepted
        assert result.series[1].counts.rejected_source_qc == 1 - accepted

    @pytest.mark.parametrize("version", [1, 2])
    def test_seven_day_five_minute_history_within_caps(
        self, db_connection: sa.Connection, version: int
    ) -> None:
        from dataclasses import replace

        from tests.integration.store.test_forecast_rating_history import _dense_parent

        parent, samples = _dense_parent(db_connection, version)
        row = dict(
            db_connection.execute(sa.select(rating_derived_discharge).limit(1))
            .mappings()
            .one()
        )
        query = replace(
            reader_request(row),
            start=parent.issued_at - timedelta(days=7),
            end=parent.issued_at,
            generated_at=parent.issued_at,
        )
        db_connection.execute(sa.text("SET LOCAL statement_timeout='5s'"))
        raw = db_connection.scalar(
            sa.select(
                sa.func.station_rating_history(
                    row["tenant_id"],
                    row["station_id"],
                    query.start,
                    query.end,
                    True,
                )
            )
        )
        assert len(raw["samples"]) == 2016
        result = reader(db_connection, row).read(query)
        assert result.series[2].counts.accepted == len(samples) == 2016

    def test_ordinary_and_provisional_keep_separate_values(
        self, db_connection: sa.Connection
    ) -> None:
        row = seed(db_connection, 2)
        obs = dict(db_connection.execute(sa.select(observations)).mappings().one())
        obs.update(id=uuid4(), parameter="discharge", value=12.5)
        db_connection.execute(sa.insert(observations).values(**obs))
        result = reader(db_connection, row).read(reader_request(row))
        assert result.series[0].points[0].value == 12.5
        assert result.series[2].points[0].value == row["discharge"]

    def test_consumer_standard_access_and_provisional_denial(
        self, db_connection: sa.Connection
    ) -> None:
        from fastapi import HTTPException

        row = seed(db_connection)
        consumer = reader(db_connection, row, "consumer")
        assert len(consumer.read(reader_request(row, standard=True)).series) == 2
        with pytest.raises(HTTPException) as error:
            consumer.read(reader_request(row))
        assert error.value.status_code == 403

    @pytest.mark.parametrize("scope", ["tenant", "station"])
    def test_out_of_scope_before_projection(
        self, db_connection: sa.Connection, scope: str
    ) -> None:
        from dataclasses import replace

        from fastapi import HTTPException

        row = seed(db_connection)
        service = reader(db_connection, row)
        if scope == "tenant":
            service.principal = replace(service.principal, tenant_id=uuid4())
        else:
            service.principal = replace(service.principal, station_ids=frozenset())
        with pytest.raises(HTTPException) as error:
            service.read(reader_request(row))
        assert error.value.status_code == 404
