from __future__ import annotations

import threading
from dataclasses import replace
from datetime import timedelta

import pytest
import sqlalchemy as sa

from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
from sapphire_flow.types.enums import ForecastDataUse, QcStatus
from sapphire_flow.types.forecast_lineage import ForecastInputLineage
from tests.integration.store.test_rejected_forecast_store import (
    _NOW,
    _entry,
    _seed_model,
    _seed_station,
    savepoint_factory,
)


def test_rejected_test_write_disabled(db_connection: sa.Connection) -> None:
    sid = _seed_station(db_connection)
    mid = _seed_model(db_connection)
    entry = _entry(station_id=sid, model_id=mid)
    entry = replace(
        entry,
        payload=replace(
            entry.payload,
            data_use=ForecastDataUse.EXPIRED_RATING_TEST,
            input_lineage=ForecastInputLineage(
                provisional_discharge_fingerprints=("a" * 64,),
                transformation_versions=("v1",),
            ),
        ),
    )
    store = PgRejectedForecastStore(
        db_connection,
        transaction_factory=savepoint_factory(db_connection),
        data_use=ForecastDataUse.EXPIRED_RATING_TEST,
    )
    with pytest.raises(sa.exc.DBAPIError, match="test rejection writes are disabled"):
        store.write_batch([entry], abandon=threading.Event())
    assert store.fetch_rejected_forecasts(
        sid, _NOW - timedelta(days=1), _NOW + timedelta(days=1)
    ) == ([], 0)


def test_mixed_batch_refused_before_any_rows(db_connection: sa.Connection) -> None:
    sid = _seed_station(db_connection)
    mid = _seed_model(db_connection)
    entry = _entry(station_id=sid, model_id=mid)
    test = replace(
        entry,
        payload=replace(entry.payload, data_use=ForecastDataUse.EXPIRED_RATING_TEST),
    )
    store = PgRejectedForecastStore(
        db_connection, transaction_factory=savepoint_factory(db_connection)
    )
    with pytest.raises(ValueError, match="purpose"):
        store.write_batch([entry, test], abandon=threading.Event())
    assert store.fetch_rejected_forecasts(
        sid, _NOW - timedelta(days=1), _NOW + timedelta(days=1)
    ) == ([], 0)


@pytest.mark.parametrize("invalid", ["missing", "standard_lineage", "passing"])
def test_lineage_and_qc_validated_at_write_not_payload_construction(
    db_connection: sa.Connection, invalid: str
) -> None:
    sid = _seed_station(db_connection)
    mid = _seed_model(db_connection)
    entry = _entry(station_id=sid, model_id=mid)
    lineage = ForecastInputLineage(
        provisional_discharge_fingerprints=("a" * 64,), transformation_versions=("v1",)
    )
    purpose = (
        ForecastDataUse.STANDARD
        if invalid == "standard_lineage"
        else ForecastDataUse.EXPIRED_RATING_TEST
    )
    parameters = (
        tuple(
            replace(p, qc_status=QcStatus.QC_PASSED) for p in entry.payload.parameters
        )
        if invalid == "passing"
        else entry.payload.parameters
    )
    entry = replace(
        entry,
        payload=replace(
            entry.payload,
            data_use=purpose,
            input_lineage=None if invalid == "missing" else lineage,
            parameters=parameters,
        ),
    )
    store = PgRejectedForecastStore(
        db_connection,
        transaction_factory=savepoint_factory(db_connection),
        data_use=purpose,
    )
    with pytest.raises(ValueError, match="lineage|QC-failed"):
        store.write_batch([entry], abandon=threading.Event())


def test_structural_class_reads_counts_and_immutable_lineage(
    db_connection: sa.Connection,
) -> None:
    from sapphire_flow.db.metadata import rejected_forecasts
    from sapphire_flow.types.rejected_forecast import RejectedParameterPayload
    from tests.conftest import make_forecast_ensemble

    sid = _seed_station(db_connection)
    mid = _seed_model(db_connection)
    ordinary = _entry(station_id=sid, model_id=mid)
    lineage = ForecastInputLineage(
        provisional_discharge_fingerprints=("a" * 64,), transformation_versions=("v1",)
    )
    test = replace(
        ordinary,
        payload=replace(
            ordinary.payload,
            data_use=ForecastDataUse.EXPIRED_RATING_TEST,
            input_lineage=lineage,
            parameters=(
                *ordinary.payload.parameters,
                RejectedParameterPayload(
                    ensemble=make_forecast_ensemble(
                        station_id=sid, parameter="water_level"
                    ),
                    qc_status=QcStatus.QC_PASSED,
                ),
            ),
        ),
    )
    standard_store = PgRejectedForecastStore(
        db_connection, transaction_factory=savepoint_factory(db_connection)
    )
    test_store = PgRejectedForecastStore(
        db_connection,
        transaction_factory=savepoint_factory(db_connection),
        data_use=ForecastDataUse.EXPIRED_RATING_TEST,
    )
    # Only disposable transactional fixture removes the dormant write refusal.
    db_connection.execute(
        sa.text(
            "DROP TRIGGER trg_rejected_forecast_test_write_refused ON "
            "rejected_forecasts"
        )
    )
    standard_store.write_batch([ordinary], abandon=threading.Event())
    test_store.write_batch([test], abandon=threading.Event())
    window = (sid, _NOW - timedelta(days=1), _NOW + timedelta(days=1))
    assert standard_store.fetch_rejected_forecasts(*window)[1] == 1
    rows, count = test_store.fetch_rejected_forecasts(*window, limit=1)
    assert count == 2
    assert len(rows) == 1
    assert rows[0].data_use is ForecastDataUse.EXPIRED_RATING_TEST
    assert rows[0].input_lineage == lineage
    for values in (
        {"data_use": "standard", "input_lineage": None},
        {"input_lineage": "{}"},
    ):
        with (
            pytest.raises(sa.exc.DBAPIError, match="append-only"),
            db_connection.begin_nested(),
        ):
            db_connection.execute(
                sa.update(rejected_forecasts)
                .where(rejected_forecasts.c.id == rows[0].id)
                .values(**values)
            )
    abandoned = threading.Event()
    abandoned.set()
    from sapphire_flow.exceptions import CaptureAbandonedError

    with pytest.raises(CaptureAbandonedError, match="abandoned"):
        test_store.write_batch([test], abandon=abandoned)
    assert test_store.fetch_rejected_forecasts(*window)[1] == 2


@pytest.mark.parametrize("lineage", [None, "{}", "[]", '{"snapshots":null}'])
def test_sql_rejection_requires_lineage_shape(
    db_connection: sa.Connection, lineage: str | None
) -> None:
    from uuid import uuid4

    from sapphire_flow.db.metadata import rejected_forecasts

    sid = _seed_station(db_connection)
    mid = _seed_model(db_connection)
    db_connection.execute(
        sa.text(
            "DROP TRIGGER trg_rejected_forecast_test_write_refused "
            "ON rejected_forecasts"
        )
    )
    with (
        pytest.raises(sa.exc.DBAPIError, match="lineage"),
        db_connection.begin_nested(),
    ):
        db_connection.execute(
            sa.insert(rejected_forecasts).values(
                id=uuid4(),
                attempt_id=uuid4(),
                station_id=sid,
                model_id=mid,
                issued_at=_NOW,
                parameter="discharge",
                units="m3/s",
                representation="members",
                time_step_seconds=3600,
                values={},
                qc_status="qc_failed",
                data_use="expired_rating_test",
                input_lineage=lineage,
            )
        )
