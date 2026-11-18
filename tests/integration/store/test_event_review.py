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
    ReviewSeriesError,
    ReviewSeriesQuery,
    ReviewSourceMode,
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


def series_query(hindcast: HindcastForecast) -> ReviewSeriesQuery:
    return ReviewSeriesQuery(
        scope=query(hindcast),
        model_id=hindcast.model_id,
        lead_hours=2,
        start=hindcast.hindcast_step,
        end=hindcast.hindcast_step + timedelta(days=1),
        requested_at=hindcast.hindcast_step + timedelta(days=10),
        source_mode=ReviewSourceMode.HINDCAST,
        hindcast_run_id=hindcast.hindcast_run_id,
        hindcast_artifact_id=hindcast.model_artifact_id,
        forcing_type=hindcast.forcing_type,
        time_step_seconds=int(hindcast.ensemble.time_step.total_seconds()),
    )


class TestPgReviewSeries:
    def test_only_selected_lead_values_consume_series_budget(
        self,
        db_connection: sa.Connection,
        hindcast: HindcastForecast,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("sapphire_flow.store.event_review.MAX_REVIEW_VALUES", 2)
        result = PgEventReviewStore(db_connection).fetch_series(series_query(hindcast))
        assert len(result.records[0].values) == 2

    def test_offered_lead_remains_offered_outside_requested_period(
        self, db_connection: sa.Connection, hindcast: HindcastForecast
    ) -> None:
        q = series_query(hindcast)
        q = replace(q, start=q.end, end=q.end + timedelta(days=1))
        result = PgEventReviewStore(db_connection).fetch_series(q)
        assert result.sources[0].lead_hours == (2,)
        assert result.records == ()

    def test_representation_mismatch_does_not_treat_quantiles_as_members(
        self, db_connection: sa.Connection, hindcast: HindcastForecast
    ) -> None:
        from sapphire_flow.services.event_review import read_review_series
        from sapphire_flow.types.event_review import ReviewPointState

        db_connection.execute(
            sa.update(hindcast_values)
            .where(hindcast_values.c.hindcast_forecast_id == hindcast.id)
            .values(quantile=0.5, member_id=None)
        )
        result = read_review_series(
            PgEventReviewStore(db_connection), series_query(hindcast)
        )
        point = result.segments[0].points[0]
        assert point.state is ReviewPointState.UNAVAILABLE
        assert point.median is None

    def test_daily_non_midnight_hindcast_preserves_exact_lead(
        self, db_connection: sa.Connection, hindcast: HindcastForecast
    ) -> None:
        # Real daily timestamps at a non-midnight phase; timezone is display metadata.
        issue = hindcast.hindcast_step + timedelta(hours=6, minutes=15)
        values = hindcast.ensemble.values.with_columns(
            (
                pl.lit(issue) + (pl.col("valid_time") - hindcast.hindcast_step) * 24
            ).alias("valid_time")
        )
        daily = replace(
            hindcast,
            id=uuid4(),
            hindcast_step=issue,
            ensemble=ForecastEnsemble.from_members(
                station_id=hindcast.station_id,
                issued_at=issue,
                parameter="discharge",
                units="m³/s",
                time_step=timedelta(days=1),
                values=values,
            ),
        )
        PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        ).store_hindcast(daily)
        db_connection.execute(
            sa.update(stations)
            .where(stations.c.id == hindcast.station_id)
            .values(timezone="Asia/Kathmandu", network="dhm")
        )
        q = replace(series_query(daily), lead_hours=48, end=issue + timedelta(days=4))
        result = PgEventReviewStore(db_connection).fetch_series(q)
        assert len(result.records) == 1
        assert result.records[0].valid_time == issue + timedelta(days=2)
        assert result.records[0].source.time_step_seconds == 86400

    def test_operational_publication_selection_controls_series(
        self, db_connection: sa.Connection, hindcast: HindcastForecast
    ) -> None:
        from sapphire_flow.store.forecast_store import PgForecastStore
        from tests.integration.store.test_forecast_publication_store import (
            _publish,
            _seed,
        )

        publication, principal, fid, sid = _seed(db_connection)
        forecast = PgForecastStore(db_connection).fetch_forecast(fid)
        assert forecast is not None
        q = replace(
            series_query(hindcast),
            scope=replace(
                query(hindcast), station_id=sid, policy=ReviewPolicy.PUBLISHED_ONLY
            ),
            model_id=forecast.model_id,
            source_mode=ReviewSourceMode.OPERATIONAL,
            lead_hours=1,
            start=forecast.issued_at,
            end=forecast.issued_at + timedelta(days=1),
            hindcast_run_id=None,
            hindcast_artifact_id=None,
            forcing_type=None,
            time_step_seconds=None,
        )
        store = PgEventReviewStore(db_connection)
        assert store.fetch_series(q).records == ()
        _publish(publication, principal, fid)
        selected = store.fetch_series(q)
        assert len(selected.records) == 1
        assert selected.records[0].forecast_id == fid
        assert len(selected.records[0].values) == 5

    def test_operational_current_revision_excludes_superseded(
        self, db_connection: sa.Connection, hindcast: HindcastForecast
    ) -> None:
        from sapphire_flow.db.metadata import forecasts
        from sapphire_flow.store.forecast_store import PgForecastStore
        from tests.integration.store.test_forecast_publication_store import _seed

        _, _, fid, sid = _seed(db_connection, with_evidence=False)
        forecast = PgForecastStore(db_connection).fetch_forecast(fid)
        assert forecast is not None
        q = replace(
            series_query(hindcast),
            scope=replace(query(hindcast), station_id=sid),
            model_id=forecast.model_id,
            source_mode=ReviewSourceMode.OPERATIONAL,
            lead_hours=1,
            start=forecast.issued_at,
            end=forecast.issued_at + timedelta(days=1),
            hindcast_run_id=None,
            hindcast_artifact_id=None,
            forcing_type=None,
            time_step_seconds=None,
        )
        store = PgEventReviewStore(db_connection)
        assert len(store.fetch_series(q).records) == 1
        db_connection.execute(
            sa.update(forecasts)
            .where(forecasts.c.id == fid)
            .values(status="superseded")
        )
        assert store.fetch_series(q).records == ()

    def test_exact_lead_bulk_values_and_provenance(
        self, db_connection: sa.Connection, hindcast: HindcastForecast
    ) -> None:
        snapshot = PgEventReviewStore(db_connection).fetch_series(
            series_query(hindcast)
        )
        assert len(snapshot.records) == 1
        row = snapshot.records[0]
        assert row.valid_time == hindcast.hindcast_step + timedelta(hours=2)
        assert row.issued_at == hindcast.hindcast_step
        assert row.forecast_id == hindcast.id
        assert row.source.artifact_id == hindcast.model_artifact_id
        assert len(row.values) == 2

    def test_window_is_valid_time_half_open_not_issue_time(
        self, db_connection: sa.Connection, hindcast: HindcastForecast
    ) -> None:
        q = series_query(hindcast)
        start = hindcast.hindcast_step + timedelta(hours=2)
        store = PgEventReviewStore(db_connection)
        assert (
            len(
                store.fetch_series(
                    replace(q, start=start, end=start + timedelta(hours=1))
                ).records
            )
            == 1
        )
        assert store.fetch_series(replace(q, end=start)).records == ()

    @pytest.mark.parametrize("field", ["hindcast_run_id", "hindcast_artifact_id"])
    def test_source_tuple_is_pinned(
        self, db_connection: sa.Connection, hindcast: HindcastForecast, field: str
    ) -> None:
        q = replace(series_query(hindcast), **{field: uuid4()})
        result = PgEventReviewStore(db_connection).fetch_series(q)
        assert result.sources == ()
        assert result.records == ()

    def test_orphan_and_fractional_values_are_not_numbers(
        self, db_connection: sa.Connection, hindcast: HindcastForecast
    ) -> None:
        db_connection.execute(
            sa.update(hindcast_values)
            .where(hindcast_values.c.hindcast_forecast_id == hindcast.id)
            .values(valid_time=hindcast_values.c.valid_time + timedelta(minutes=30))
        )
        result = PgEventReviewStore(db_connection).fetch_series(series_query(hindcast))
        assert result.records[0].values == ()
        assert result.sources[0].lead_hours == ()

    @pytest.mark.parametrize(
        "budget", ["MAX_REVIEW_POINTS", "MAX_REVIEW_HEADERS", "MAX_REVIEW_VALUES"]
    )
    def test_series_overflow_is_explicit(
        self,
        db_connection: sa.Connection,
        hindcast: HindcastForecast,
        monkeypatch: pytest.MonkeyPatch,
        budget: str,
    ) -> None:
        monkeypatch.setattr(f"sapphire_flow.store.event_review.{budget}", 0)
        with pytest.raises(ReviewSeriesError, match="narrow") as exc:
            PgEventReviewStore(db_connection).fetch_series(series_query(hindcast))
        assert exc.value.code == "review_series_limit_exceeded"

    def test_old_artifact_selection_does_not_follow_upsert(
        self, db_connection: sa.Connection, hindcast: HindcastForecast
    ) -> None:
        q = series_query(hindcast)
        db_connection.execute(
            sa.update(model_artifacts)
            .where(model_artifacts.c.id == hindcast.model_artifact_id)
            .values(status="superseded")
        )
        aid = _seed_artifact(db_connection, hindcast.model_id, hindcast.station_id)
        PgHindcastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        ).store_hindcast(replace(hindcast, model_artifact_id=aid))
        assert PgEventReviewStore(db_connection).fetch_series(q).sources == ()

    def test_publication_gate_never_reads_hindcast(
        self, db_connection: sa.Connection, hindcast: HindcastForecast
    ) -> None:
        q = series_query(hindcast)
        q = replace(q, scope=replace(q.scope, policy=ReviewPolicy.PUBLISHED_ONLY))
        with pytest.raises(ReviewSeriesError, match="publication policy"):
            PgEventReviewStore(db_connection).fetch_series(q)


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
