from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

import pytest
import sqlalchemy as sa

from sapphire_flow.api.routes import tables
from sapphire_flow.types.enums import ForecastStatus
from tests.integration.api.test_dashboard_forecasts import _client, app_overrides_clear
from tests.integration.store.test_forecast_data_use import _pair, _stores


@pytest.fixture
def mixed_forecasts(db_connection: sa.Connection) -> Iterator[tuple[str, str]]:
    tables._reflected = None
    with db_connection.begin_nested() as transaction:
        db_connection.execute(
            sa.text("DROP TRIGGER trg_forecast_test_write_refused ON forecasts")
        )
        ordinary, test = _pair(db_connection)
        import hashlib

        from tests.integration.store.test_forecast_evidence_store import _evidence

        evidence = _evidence()
        test_snapshot = b"synthetic-test-only-inputs"
        ordinary = replace(
            ordinary, status=ForecastStatus.SUPERSEDED, evidence=evidence
        )
        test = replace(
            test,
            issued_at=test.issued_at + timedelta(days=2),
            evidence=replace(
                evidence,
                snapshot=test_snapshot,
                snapshot_sha256=hashlib.sha256(test_snapshot).hexdigest(),
            ),
        )
        standard_store, test_store = _stores(db_connection)
        standard_store.store_forecast(ordinary)
        test_store.store_forecast(test)
        try:
            yield str(ordinary.id), str(test.id)
        finally:
            transaction.rollback()
            tables._reflected = None


@pytest.mark.parametrize(
    "path",
    [
        "/forecasts/",
        "/tables/forecasts/",
        "/tables/forecasts/rows",
        "/tables/forecast_values/",
        "/tables/forecast_evidence/",
    ],
)
def test_ordinary_browser_excludes_test_forecasts(
    db_connection: sa.Connection, mixed_forecasts: tuple[str, str], path: str
) -> None:
    ordinary, test = mixed_forecasts
    client = _client(db_connection)
    try:
        response = client.get(path)
        assert response.status_code == 200
        assert ordinary in response.text
        assert test not in response.text
        assert ">input_lineage<" not in response.text
    finally:
        client.close()
        app_overrides_clear()


def test_legacy_by_id_hides_test_values(
    db_connection: sa.Connection, mixed_forecasts: tuple[str, str]
) -> None:
    ordinary, test = mixed_forecasts
    client = _client(db_connection)
    try:
        assert client.get(f"/forecasts/{ordinary}/").status_code == 200
        assert client.get(f"/forecasts/{test}/").status_code == 404
        assert client.get(f"/api/v1/forecasts/{test}/data.json").json() == {
            "lead_times": [],
            "members": {},
        }
    finally:
        client.close()
        app_overrides_clear()


@pytest.mark.parametrize(
    "table_name", ["forecasts", "forecast_values", "forecast_evidence"]
)
def test_visible_count_matches_standard_rows_before_pagination(
    db_connection: sa.Connection, mixed_forecasts: tuple[str, str], table_name: str
) -> None:
    from sapphire_flow.db.metadata import metadata

    ordinary, _ = mixed_forecasts
    table = metadata.tables[table_name]
    key = table.c.id if table_name == "forecasts" else table.c.forecast_id
    expected = db_connection.scalar(
        sa.select(sa.func.count()).select_from(table).where(key == ordinary)
    )
    actual = db_connection.scalar(
        sa.select(sa.func.count())
        .select_from(table)
        .where(tables.ordinary_forecast_rows(table))
    )
    assert actual == expected


def test_blob_browser_keeps_shared_standard_reference_only(
    db_connection: sa.Connection, mixed_forecasts: tuple[str, str]
) -> None:
    import hashlib

    from tests.integration.store.test_forecast_evidence_store import _evidence

    client = _client(db_connection)
    try:
        response = client.get("/tables/forecast_evidence_blobs/")
        assert response.status_code == 200
        assert _evidence().snapshot_sha256 in response.text
        assert _evidence().artifact_sha256 in response.text
        assert (
            hashlib.sha256(b"synthetic-test-only-inputs").hexdigest()
            not in response.text
        )
    finally:
        client.close()
        app_overrides_clear()


def test_dashboard_and_inventory_exclude_newer_test_forecast(
    db_connection: sa.Connection, mixed_forecasts: tuple[str, str]
) -> None:
    client = _client(db_connection)
    try:
        response = client.get("/")
        assert response.status_code == 200
        assert response.context["forecast_count"] == 1
        assert response.context["forecast_latest"].startswith("2025-01-01")
        assert response.context["forecast_statuses"] == [
            {"status": "superseded", "count": 1}
        ]
        inventory = client.get("/tables/")
        forecast_table = next(
            item for item in inventory.context["tables"] if item["name"] == "forecasts"
        )
        assert forecast_table["rows"] == 1
    finally:
        client.close()
        app_overrides_clear()


def test_rejection_browser_projects_only_standard_rows(
    db_connection: sa.Connection, mixed_forecasts: tuple[str, str]
) -> None:
    import threading

    from sapphire_flow.db.metadata import forecasts, rejected_forecasts
    from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
    from sapphire_flow.types.enums import ForecastDataUse
    from sapphire_flow.types.forecast_lineage import ForecastInputLineage
    from tests.integration.store.test_rejected_forecast_store import (
        _entry,
        savepoint_factory,
    )

    row = db_connection.execute(
        sa.select(forecasts).where(forecasts.c.id == mixed_forecasts[0])
    ).one()
    entry = _entry(station_id=row.station_id, model_id=row.model_id)
    db_connection.execute(
        sa.text(
            "DROP TRIGGER trg_rejected_forecast_test_write_refused "
            "ON rejected_forecasts"
        )
    )
    for purpose in ForecastDataUse:
        lineage = (
            None
            if purpose is ForecastDataUse.STANDARD
            else ForecastInputLineage(
                provisional_discharge_fingerprints=("a" * 64,),
                transformation_versions=("v1",),
            )
        )
        store = PgRejectedForecastStore(
            db_connection,
            transaction_factory=savepoint_factory(db_connection),
            data_use=purpose,
        )
        store.write_batch(
            [
                replace(
                    entry,
                    payload=replace(
                        entry.payload, data_use=purpose, input_lineage=lineage
                    ),
                )
            ],
            abandon=threading.Event(),
        )
    identities = dict(
        db_connection.execute(
            sa.select(rejected_forecasts.c.data_use, rejected_forecasts.c.id)
        ).all()
    )
    client = _client(db_connection)
    try:
        for path in ["/tables/rejected_forecasts/", "/tables/rejected_forecasts/rows"]:
            response = client.get(path)
            assert response.status_code == 200
            assert str(identities["standard"]) in response.text
            assert str(identities["expired_rating_test"]) not in response.text
            assert ">input_lineage<" not in response.text
            assert "a" * 64 not in response.text
    finally:
        client.close()
        app_overrides_clear()


def test_blob_count_and_pages_exclude_unreferenced_payload(
    db_connection: sa.Connection, mixed_forecasts: tuple[str, str]
) -> None:
    from sapphire_flow.db.metadata import forecast_evidence_blobs

    db_connection.execute(
        sa.insert(forecast_evidence_blobs).values(
            sha256="f" * 64, payload=b"unreferenced-data", byte_length=17
        )
    )
    predicate = tables.ordinary_forecast_rows(forecast_evidence_blobs)
    count = db_connection.scalar(
        sa.select(sa.func.count()).select_from(forecast_evidence_blobs).where(predicate)
    )
    paged = [
        db_connection.scalar(
            sa.select(forecast_evidence_blobs.c.sha256)
            .where(predicate)
            .order_by(forecast_evidence_blobs.c.sha256)
            .limit(1)
            .offset(offset)
        )
        for offset in range(count)
    ]
    assert len(paged) == 2
    assert "f" * 64 not in paged
    client = _client(db_connection)
    try:
        response = client.get("/tables/forecast_evidence_blobs/")
        assert response.status_code == 200
        assert "unreferenced-data" not in response.text
        assert "compressed-as-used-inputs" not in response.text
        assert "model-weights" not in response.text
    finally:
        client.close()
        app_overrides_clear()


def test_blob_query_uses_uncorrelated_standard_membership() -> None:
    from sqlalchemy.dialects import postgresql

    from sapphire_flow.db.metadata import forecast_evidence_blobs

    sql = str(
        sa.select(forecast_evidence_blobs.c.sha256)
        .where(tables.ordinary_forecast_rows(forecast_evidence_blobs))
        .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert sql.count(" IN (SELECT ") == 2
    assert "EXISTS" not in sql
    assert (
        "ordinary_evidence.snapshot_sha256 = forecast_evidence_blobs.sha256" not in sql
    )
