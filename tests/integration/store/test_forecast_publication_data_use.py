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
