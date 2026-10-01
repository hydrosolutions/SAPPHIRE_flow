from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import forecast_publication_selections, forecasts
from sapphire_flow.store.forecast_publication_store import (
    PgForecastPublicationStore,
    PublicationConflictError,
)
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.types.enums import ForecastDataUse, QcStatus
from sapphire_flow.types.forecast_lineage import (
    ForecastInputLineage,
    ForecastStaticAttributes,
)
from sapphire_flow.types.ids import ForecastId
from tests.integration.store.test_forecast_publication_store import (
    _publish,
    _seed,
    _transaction,
)

if TYPE_CHECKING:
    from sapphire_flow.types.forecast import OperationalForecast
    from sapphire_flow.types.human_auth import HumanPrincipal


def seed_test_candidate(
    conn: sa.Connection,
) -> tuple[PgForecastPublicationStore, HumanPrincipal, ForecastId, OperationalForecast]:
    store, principal, ordinary_id, sid = _seed(conn)
    ordinary = PgForecastStore(conn).fetch_forecast(ordinary_id)
    assert ordinary is not None
    candidate = replace(
        ordinary,
        id=ForecastId(uuid4()),
        data_use=ForecastDataUse.EXPIRED_RATING_TEST,
        qc_status=QcStatus.QC_PASSED,
        input_lineage=ForecastInputLineage(
            transformation_versions=("test-v1",),
            static_attributes=(
                ForecastStaticAttributes(
                    station_id=sid,
                    source="fixture",
                    version="v1",
                    values=(("area", 1.0),),
                ),
            ),
        ),
    )
    conn.execute(sa.text("DROP TRIGGER trg_forecast_test_write_refused ON forecasts"))
    PgForecastStore(
        conn,
        data_use=ForecastDataUse.EXPIRED_RATING_TEST,
        transaction_factory=lambda: _transaction(conn),
    ).store_forecast(candidate)
    conn.execute(
        sa.text(
            "CREATE TRIGGER trg_forecast_test_write_refused BEFORE INSERT ON "
            "forecasts FOR EACH ROW EXECUTE FUNCTION "
            "public.forecast_test_write_refused()"
        )
    )
    return store, principal, ordinary_id, candidate


def test_passing_test_forecast_cannot_replace_standard(
    db_connection: sa.Connection,
) -> None:
    store, principal, ordinary_id, candidate = seed_test_candidate(db_connection)
    first = _publish(store, principal, ordinary_id)
    with pytest.raises(PublicationConflictError, match="test.*cannot be published"):
        _publish(
            store,
            principal,
            candidate.id,
            expected_selection_version=1,
            idempotency_key="test",
        )
    assert store.fetch_selection(first.key).selected_forecast_id == ordinary_id


@pytest.mark.parametrize("status", ["reviewed", "published"])
def test_sql_test_status_refused(db_connection: sa.Connection, status: str) -> None:
    _, _, _, candidate = seed_test_candidate(db_connection)
    with (
        pytest.raises(sa.exc.DBAPIError, match="test.*publication"),
        db_connection.begin_nested(),
    ):
        db_connection.execute(
            sa.update(forecasts)
            .where(forecasts.c.id == candidate.id)
            .values(status=status)
        )


def test_sql_test_selection_refused(db_connection: sa.Connection) -> None:
    store, principal, ordinary_id, candidate = seed_test_candidate(db_connection)
    _publish(store, principal, ordinary_id)
    with (
        pytest.raises(sa.exc.DBAPIError, match="test.*publication"),
        db_connection.begin_nested(),
    ):
        db_connection.execute(
            sa.update(forecast_publication_selections).values(
                selected_forecast_id=candidate.id
            )
        )


@pytest.mark.parametrize(
    "relation", ["forecast_publication_decisions", "forecast_publication_events"]
)
@pytest.mark.parametrize("reference", ["forecast_id", "replaced_forecast_id"])
def test_sql_publication_references_refuse_test(
    db_connection: sa.Connection, relation: str, reference: str
) -> None:
    from sapphire_flow.db.metadata import metadata

    store, principal, ordinary_id, candidate = seed_test_candidate(db_connection)
    _publish(store, principal, ordinary_id)
    table = metadata.tables[relation]
    row = dict(db_connection.execute(sa.select(table)).mappings().one())
    row[reference] = candidate.id
    if relation == "forecast_publication_decisions":
        row.update(id=uuid4(), idempotency_key="direct-test")
    else:
        row["sequence"] += 1
    with (
        pytest.raises(sa.exc.DBAPIError, match="test.*publication"),
        db_connection.begin_nested(),
    ):
        db_connection.execute(sa.insert(table).values(**row))


def test_test_candidate_assessment_has_only_safe_reason(
    db_connection: sa.Connection,
) -> None:
    from tests.integration.store.test_forecast_publication_store import _NOW

    store, _, _, candidate = seed_test_candidate(db_connection)
    assessment = store.assess_candidate(candidate.id, _NOW)
    assert assessment.remaining_reasons == ("test forecast cannot be published",)
    assert assessment.preservation_at_publish is None


def test_test_candidate_does_not_disclose_class_without_grants(
    db_connection: sa.Connection,
) -> None:
    from sapphire_flow.db.metadata import human_station_grants
    from sapphire_flow.store.forecast_publication_store import PublicationForbiddenError

    store, principal, _, candidate = seed_test_candidate(db_connection)
    db_connection.execute(
        sa.delete(human_station_grants).where(
            human_station_grants.c.user_id == principal.user_id
        )
    )
    with pytest.raises(
        PublicationForbiddenError, match="human has no station publication grant"
    ):
        _publish(store, principal, candidate.id)


def test_sql_test_selection_insert_refused(db_connection: sa.Connection) -> None:
    _, principal, _, candidate = seed_test_candidate(db_connection)
    with (
        pytest.raises(sa.exc.DBAPIError, match="test.*publication"),
        db_connection.begin_nested(),
    ):
        db_connection.execute(
            sa.insert(forecast_publication_selections).values(
                tenant_id=principal.tenant_id,
                station_id=candidate.station_id,
                parameter=candidate.ensemble.parameter,
                issued_at=candidate.issued_at,
                selected_forecast_id=candidate.id,
                version=1,
            )
        )


@pytest.mark.parametrize(
    "purpose", [ForecastDataUse.STANDARD, ForecastDataUse.EXPIRED_RATING_TEST]
)
def test_publication_readers_filter_class_not_superseded_status(
    db_connection: sa.Connection,
    purpose: ForecastDataUse,
) -> None:
    from datetime import timedelta

    from sapphire_flow.db.metadata import (
        forecast_publication_decisions,
        forecast_publication_events,
    )

    store, principal, ordinary_id, candidate = seed_test_candidate(db_connection)
    decision = _publish(store, principal, ordinary_id)
    visible_id = ordinary_id if purpose is ForecastDataUse.STANDARD else candidate.id
    # Transactional fixture only: coherent legacy refs cannot be written in production.
    with db_connection.begin_nested():
        if purpose is ForecastDataUse.EXPIRED_RATING_TEST:
            tables = (
                forecast_publication_selections,
                forecast_publication_decisions,
                forecast_publication_events,
            )
            for table in tables:
                db_connection.execute(
                    sa.text(
                        f"DROP TRIGGER trg_{table.name}_standard_only ON {table.name}"
                    )
                )
            db_connection.execute(
                sa.update(forecast_publication_selections).values(
                    selected_forecast_id=candidate.id
                )
            )
            row = dict(
                db_connection.execute(sa.select(forecast_publication_decisions))
                .mappings()
                .one()
            )
            row.update(
                id=uuid4(), forecast_id=candidate.id, idempotency_key="synthetic-test"
            )
            db_connection.execute(
                sa.insert(forecast_publication_decisions).values(**row)
            )
            event = dict(
                db_connection.execute(sa.select(forecast_publication_events))
                .mappings()
                .one()
            )
            event.update(
                sequence=event["sequence"] + 1,
                decision_id=row["id"],
                forecast_id=candidate.id,
            )
            db_connection.execute(
                sa.insert(forecast_publication_events).values(**event)
            )
            for table in tables:
                db_connection.execute(
                    sa.text(
                        f"CREATE TRIGGER trg_{table.name}_standard_only BEFORE INSERT "
                        f"OR UPDATE ON {table.name} FOR EACH ROW EXECUTE FUNCTION "
                        f"public.reject_test_forecast_publication()"
                    )
                )
        db_connection.execute(
            sa.update(forecasts)
            .where(forecasts.c.id == visible_id)
            .values(status="superseded")
        )
        selection = store.fetch_selection(decision.key)
        ids, total = store.fetch_selected_ids(
            candidate.station_id,
            candidate.issued_at - timedelta(days=1),
            candidate.issued_at + timedelta(days=1),
        )
        latest = store.fetch_latest_selected_id(
            candidate.station_id, candidate.ensemble.parameter
        )
        decisions = store.fetch_decisions(visible_id)
        events = store.fetch_events(
            after_sequence=0, limit=50, tenant_ids=frozenset({principal.tenant_id})
        )
        if purpose is ForecastDataUse.STANDARD:
            assert (
                selection is not None and selection.selected_forecast_id == ordinary_id
            )
            assert (ids, total) == ([ordinary_id], 1)
            assert latest == ordinary_id
            assert [d.forecast_id for d in decisions] == [ordinary_id]
        else:
            assert selection is None
            assert (ids, total) == ([], 0)
            assert latest is None
            assert decisions == []
        assert [e.decision.forecast_id for e in events] == [ordinary_id]


@pytest.mark.parametrize(
    "purpose", [ForecastDataUse.STANDARD, ForecastDataUse.EXPIRED_RATING_TEST]
)
def test_forecast_store_public_purpose_is_read_only(
    db_connection: sa.Connection,
    purpose: ForecastDataUse,
) -> None:
    from sapphire_flow.protocols.stores import ForecastStore
    from tests.fakes.fake_stores import FakeForecastStore

    for store in (
        PgForecastStore(db_connection, data_use=purpose),
        FakeForecastStore(data_use=purpose),
    ):
        assert isinstance(store, ForecastStore)
        assert store.data_use is purpose
        with pytest.raises(AttributeError, match="no setter"):
            store.data_use = ForecastDataUse.STANDARD  # pyright: ignore[reportAttributeAccessIssue]
