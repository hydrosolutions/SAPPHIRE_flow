from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import polars as pl
import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import (
    hindcast_forecasts,
    hindcast_values,
    model_artifacts,
    stations,
)
from sapphire_flow.store.event_review import PgEventReviewStore
from sapphire_flow.store.hindcast_store import PgHindcastStore
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import ForcingType
from sapphire_flow.types.event_review import (
    ReviewCatalogLimitError,
    ReviewCompleteness,
    ReviewKind,
    ReviewOptionsQuery,
    ReviewPolicy,
    ReviewQueryTimeoutError,
)
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.integration.store.test_hindcast_store import (
    _make_hindcast,
    _seed_artifact,
    _seed_model,
    _seed_station,
    savepoint_factory,
)

if TYPE_CHECKING:
    from sapphire_flow.types.forecast import HindcastForecast


def aligned(value: HindcastForecast) -> HindcastForecast:
    ensemble = value.ensemble
    return replace(
        value,
        ensemble=ForecastEnsemble.from_members(
            station_id=value.station_id,
            issued_at=value.hindcast_step,
            parameter=ensemble.parameter,
            units=ensemble.units,
            time_step=ensemble.time_step,
            values=ensemble.values.with_columns(
                pl.col("valid_time") + (value.hindcast_step - ensemble.issued_at)
            ),
        ),
    )


@pytest.fixture
def hindcast(db_connection: sa.Connection) -> HindcastForecast:
    sid = _seed_station(db_connection)
    mid = _seed_model(db_connection)
    aid = _seed_artifact(db_connection, mid, sid)
    value = aligned(_make_hindcast(sid, mid, aid, n_steps=3, n_members=2))
    PgHindcastStore(
        db_connection, transaction_factory=savepoint_factory(db_connection)
    ).store_hindcast(value)
    return value


def query(
    hindcast: HindcastForecast, policy: ReviewPolicy = ReviewPolicy.ORDINARY
) -> ReviewOptionsQuery:
    return ReviewOptionsQuery(
        station_id=hindcast.station_id, tenant_id=DEFAULT_TENANT_ID, policy=policy
    )


class TestPgEventReviewStore:
    def test_cancelled_query_rolls_back_only_savepoint(
        self,
        db_connection: sa.Connection,
        hindcast: HindcastForecast,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        previous_timeout = db_connection.scalar(sa.text("SHOW statement_timeout"))
        monkeypatch.setattr(
            "sapphire_flow.store.event_review._catalog_statement",
            lambda _: sa.text(
                "DO $$ BEGIN RAISE EXCEPTION 'synthetic cancellation' "
                "USING ERRCODE = '57014'; END $$"
            ),
        )
        with pytest.raises(ReviewQueryTimeoutError, match="timed out"):
            PgEventReviewStore(db_connection).fetch_options(query(hindcast))
        assert db_connection.scalar(sa.text("SELECT 1")) == 1
        assert (
            db_connection.scalar(sa.text("SHOW statement_timeout")) == previous_timeout
        )

    def test_test_candidate_metadata_is_not_enumerated(
        self,
        db_connection: sa.Connection,
        hindcast: HindcastForecast,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from tests.integration.store.test_forecast_publication_data_use import (
            seed_test_candidate,
        )

        _, _, _, candidate = seed_test_candidate(db_connection)
        monkeypatch.setattr("sapphire_flow.store.event_review.MAX_REVIEW_HEADERS", 1)
        sources = PgEventReviewStore(db_connection).fetch_options(
            replace(query(hindcast), station_id=candidate.station_id)
        )
        assert len(sources) == 1

    def test_actual_leads_historical_artifact_and_unknown_completeness(
        self, db_connection: sa.Connection, hindcast: HindcastForecast
    ) -> None:
        db_connection.execute(
            sa.update(model_artifacts)
            .where(model_artifacts.c.id == hindcast.model_artifact_id)
            .values(status="superseded")
        )
        sources = PgEventReviewStore(db_connection).fetch_options(query(hindcast))
        assert len(sources) == 1
        assert sources[0].artifact_id == hindcast.model_artifact_id
        assert sources[0].lead_hours == (1, 2, 3)
        assert sources[0].completeness is ReviewCompleteness.UNKNOWN
        assert sources[0].last_valid_time == hindcast.hindcast_step + timedelta(hours=3)

    def test_leads_are_union_of_values_not_first_header(
        self, db_connection: sa.Connection, hindcast: HindcastForecast
    ) -> None:
        second = _make_hindcast(
            hindcast.station_id,
            hindcast.model_id,
            hindcast.model_artifact_id,
            hindcast_run_id=hindcast.hindcast_run_id,
            hindcast_step=hindcast.hindcast_step + timedelta(days=1),
            n_steps=5,
            n_members=2,
        )
        PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        ).store_hindcast(aligned(second))
        sources = PgEventReviewStore(db_connection).fetch_options(query(hindcast))
        assert sources[0].lead_hours == (1, 2, 3, 4, 5)

    def test_forcing_is_part_of_source_identity(
        self, db_connection: sa.Connection, hindcast: HindcastForecast
    ) -> None:
        other = replace(hindcast, id=uuid4(), forcing_type=ForcingType.REANALYSIS)
        PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        ).store_hindcast(other)
        sources = PgEventReviewStore(db_connection).fetch_options(query(hindcast))
        assert {item.forcing_type for item in sources} == {
            ForcingType.NWP_ARCHIVE,
            ForcingType.REANALYSIS,
        }

    def test_orphan_is_incomplete_not_absent(
        self, db_connection: sa.Connection, hindcast: HindcastForecast
    ) -> None:
        db_connection.execute(
            sa.delete(hindcast_values).where(
                hindcast_values.c.hindcast_forecast_id == hindcast.id
            )
        )
        sources = PgEventReviewStore(db_connection).fetch_options(query(hindcast))
        assert sources[0].lead_hours == ()
        assert sources[0].completeness is ReviewCompleteness.INCOMPLETE
        assert sources[0].reasons == ("orphan_headers",)

    def test_floored_lead_is_not_offered(
        self, db_connection: sa.Connection, hindcast: HindcastForecast
    ) -> None:
        db_connection.execute(
            sa.update(hindcast_values)
            .where(hindcast_values.c.hindcast_forecast_id == hindcast.id)
            .values(valid_time=hindcast_values.c.valid_time + timedelta(minutes=30))
        )
        sources = PgEventReviewStore(db_connection).fetch_options(query(hindcast))
        assert sources[0].lead_hours == ()
        assert sources[0].reasons == ("invalid_lead_timestamps",)

    def test_non_midnight_hourly_phase_is_preserved(
        self, db_connection: sa.Connection, hindcast: HindcastForecast
    ) -> None:
        db_connection.execute(
            sa.update(hindcast_forecasts)
            .where(hindcast_forecasts.c.id == hindcast.id)
            .values(
                hindcast_step=hindcast_forecasts.c.hindcast_step + timedelta(minutes=15)
            )
        )
        db_connection.execute(
            sa.update(hindcast_values)
            .where(hindcast_values.c.hindcast_forecast_id == hindcast.id)
            .values(valid_time=hindcast_values.c.valid_time + timedelta(minutes=15))
        )
        db_connection.execute(
            sa.update(stations)
            .where(stations.c.id == hindcast.station_id)
            .values(timezone="Asia/Kathmandu", network="dhm")
        )
        sources = PgEventReviewStore(db_connection).fetch_options(query(hindcast))
        assert sources[0].lead_hours == (1, 2, 3)
        assert sources[0].first_valid_time == hindcast.hindcast_step + timedelta(
            hours=1, minutes=15
        )

    def test_foreign_tenant_reads_no_metadata(
        self, db_connection: sa.Connection, hindcast: HindcastForecast
    ) -> None:
        assert (
            PgEventReviewStore(db_connection).fetch_options(
                replace(query(hindcast), tenant_id=TenantId(uuid4()))
            )
            == ()
        )

    @pytest.mark.parametrize(
        "budget,value",
        [
            ("MAX_REVIEW_HEADERS", 0),
            ("MAX_REVIEW_VALUES", 1),
            ("MAX_REVIEW_SOURCES", 0),
        ],
    )
    def test_budget_overflow_is_not_partial_catalog(
        self,
        db_connection: sa.Connection,
        hindcast: HindcastForecast,
        monkeypatch: pytest.MonkeyPatch,
        budget: str,
        value: int,
    ) -> None:
        monkeypatch.setattr(f"sapphire_flow.store.event_review.{budget}", value)
        with pytest.raises(ReviewCatalogLimitError, match="work limit"):
            PgEventReviewStore(db_connection).fetch_options(query(hindcast))

    def test_gated_query_does_not_touch_hindcast_relations(
        self, db_connection: sa.Connection, hindcast: HindcastForecast
    ) -> None:
        # Temporarily remove relations in this rollback-only test transaction:
        # gated options must not need even SELECT privilege on these tables.
        db_connection.execute(
            sa.text("ALTER TABLE hindcast_values RENAME TO unavailable_hindcast_values")
        )
        db_connection.execute(
            sa.text(
                "ALTER TABLE hindcast_forecasts "
                "RENAME TO unavailable_hindcast_forecasts"
            )
        )
        assert (
            PgEventReviewStore(db_connection).fetch_options(
                query(hindcast, ReviewPolicy.PUBLISHED_ONLY)
            )
            == ()
        )

    def test_gated_options_only_include_selected_operational(
        self, db_connection: sa.Connection, hindcast: HindcastForecast
    ) -> None:
        from tests.integration.store.test_forecast_publication_store import (
            _publish,
            _seed,
        )

        store, principal, forecast_id, sid = _seed(db_connection)
        q = replace(query(hindcast, ReviewPolicy.PUBLISHED_ONLY), station_id=sid)
        assert PgEventReviewStore(db_connection).fetch_options(q) == ()
        _publish(store, principal, forecast_id)
        sources = PgEventReviewStore(db_connection).fetch_options(q)
        assert len(sources) == 1
        assert sources[0].kind is ReviewKind.OPERATIONAL

    def test_same_run_upsert_reports_new_artifact_not_stale_identity(
        self, db_connection: sa.Connection, hindcast: HindcastForecast
    ) -> None:
        db_connection.execute(
            sa.update(model_artifacts)
            .where(model_artifacts.c.id == hindcast.model_artifact_id)
            .values(status="superseded")
        )
        replacement = _seed_artifact(
            db_connection, hindcast.model_id, hindcast.station_id
        )
        PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        ).store_hindcast(replace(hindcast, model_artifact_id=replacement))
        sources = PgEventReviewStore(db_connection).fetch_options(query(hindcast))
        assert len(sources) == 1
        assert sources[0].artifact_id == replacement
