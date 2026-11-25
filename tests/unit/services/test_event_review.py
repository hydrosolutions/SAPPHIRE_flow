from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from sapphire_flow.api.event_review_schemas import ReviewSeriesResponse
from sapphire_flow.services.event_review import read_review_options, read_review_series
from sapphire_flow.types.domain import StationThreshold
from sapphire_flow.types.enums import EnsembleRepresentation, QcStatus, ThresholdSource
from sapphire_flow.types.event_review import (
    ReviewCatalogLimitError,
    ReviewKind,
    ReviewOptionsQuery,
    ReviewPointState,
    ReviewPolicy,
    ReviewSeriesError,
    ReviewSeriesQuery,
    ReviewSeriesRecord,
    ReviewSeriesSnapshot,
    ReviewSourceMode,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.unit.api.test_event_review import Catalog, source


def series_query() -> ReviewSeriesQuery:
    selected = source()
    assert selected.first_valid_time is not None
    return ReviewSeriesQuery(
        scope=ReviewOptionsQuery(
            station_id=StationId(uuid4()),
            tenant_id=DEFAULT_TENANT_ID,
            policy=ReviewPolicy.ORDINARY,
        ),
        model_id=selected.model_id,
        lead_hours=48,
        start=selected.first_valid_time,
        end=selected.first_valid_time + timedelta(days=30),
        requested_at=selected.first_valid_time + timedelta(days=40),
        source_mode=ReviewSourceMode.HINDCAST,
        hindcast_run_id=selected.hindcast_run_id,
        hindcast_artifact_id=selected.artifact_id,
        forcing_type=selected.forcing_type,
        time_step_seconds=selected.time_step_seconds,
    )


def record(query: ReviewSeriesQuery) -> ReviewSeriesRecord:
    selected = replace(
        source(),
        hindcast_run_id=query.hindcast_run_id,
        artifact_id=query.hindcast_artifact_id,
    )
    return ReviewSeriesRecord(
        forecast_id=uuid4(),
        source=selected,
        issued_at=query.start - timedelta(hours=query.lead_hours),
        valid_time=query.start,
        stored_at=query.requested_at,
        representation=EnsembleRepresentation.MEMBERS,
        qc_status=QcStatus.RAW,
        values=((0, 1.0), (1, 2.0), (2, 3.0), (3, 4.0), (4, 5.0)),
    )


def series_store(value: ReviewSeriesRecord) -> Catalog:
    store = Catalog()
    store.series = ReviewSeriesSnapshot(sources=(value.source,), records=(value,))
    return store


class TestReadReviewSeries:
    def test_small_member_ensemble_keeps_missing_band(self) -> None:
        query = series_query()
        value = replace(record(query), values=((0, 1.0), (1, 3.0)))
        point = read_review_series(series_store(value), query).segments[0].points[0]
        assert point.median == 2.0
        assert point.p25 is point.p75 is None
        assert point.band_reason == "fewer_than_five_members"

    def test_nonfinite_reduction_is_unavailable(self) -> None:
        query = series_query()
        value = replace(record(query), values=((0, 1e308), (1, 1e308)))
        point = read_review_series(series_store(value), query).segments[0].points[0]
        assert point.state is ReviewPointState.UNAVAILABLE
        assert point.median is None

    def test_member_quartiles_and_unchecked_label(self) -> None:
        query = series_query()
        point = (
            read_review_series(series_store(record(query)), query).segments[0].points[0]
        )
        assert (point.p25, point.median, point.p75) == (2.0, 3.0, 4.0)
        assert point.reason == "qc_unchecked"
        assert point.quartile_method == "member_empirical_type7"

    @pytest.mark.parametrize(
        "values,median,lower,state",
        [
            (
                ((0.25, 4.0), (0.5, 5.0), (0.75, 6.0)),
                5.0,
                4.0,
                ReviewPointState.AVAILABLE,
            ),
            (((0.5, 5.0),), 5.0, None, ReviewPointState.AVAILABLE),
            (((0.25, 4.0), (0.75, 6.0)), None, None, ReviewPointState.UNAVAILABLE),
            (((0.5, float("nan")),), None, None, ReviewPointState.UNAVAILABLE),
            (((0.5, -1.0),), None, None, ReviewPointState.UNAVAILABLE),
            (((0.5, 5.0), (0.5, 6.0)), None, None, ReviewPointState.UNAVAILABLE),
        ],
    )
    def test_stored_quantiles_are_not_interpolated_or_treated_as_members(
        self,
        values: tuple[tuple[float, float], ...],
        median: float | None,
        lower: float | None,
        state: ReviewPointState,
    ) -> None:
        query = series_query()
        value = replace(
            record(query),
            representation=EnsembleRepresentation.QUANTILES,
            values=values,
        )
        point = read_review_series(series_store(value), query).segments[0].points[0]
        assert (point.median, point.p25, point.state) == (median, lower, state)

    @pytest.mark.parametrize("qc", [QcStatus.QC_FAILED, QcStatus.QC_SUSPECT])
    def test_rejected_qc_never_returns_numbers(self, qc: QcStatus) -> None:
        query = series_query()
        point = (
            read_review_series(
                series_store(replace(record(query), qc_status=qc)), query
            )
            .segments[0]
            .points[0]
        )
        assert point.state is ReviewPointState.QC_REJECTED
        assert point.median is point.p25 is point.p75 is None

    @pytest.mark.parametrize("spacing_days", [1, 10])
    def test_unknown_issue_cadence_does_not_infer_gaps(self, spacing_days: int) -> None:
        query = replace(series_query(), time_step_seconds=3600)
        first = record(query)
        first = replace(first, source=replace(first.source, time_step_seconds=3600))
        last = replace(
            first,
            forecast_id=uuid4(),
            issued_at=first.issued_at + timedelta(days=spacing_days),
            valid_time=first.valid_time + timedelta(days=spacing_days),
        )
        store = series_store(first)
        store.series = replace(store.series, records=(first, last))
        segment = read_review_series(store, query).segments[0]
        assert len(segment.points) == 2
        assert [point.valid_time for point in segment.points] == [
            first.valid_time,
            last.valid_time,
        ]
        assert segment.gaps == ()

    def test_missing_values_remain_explicit_null_point(self) -> None:
        query = series_query()
        point = (
            read_review_series(series_store(replace(record(query), values=())), query)
            .segments[0]
            .points[0]
        )
        assert point.state is ReviewPointState.MISSING
        assert point.median is None

    def test_unoffered_lead_is_distinct_from_empty_period(self) -> None:
        query = series_query()
        store = series_store(record(query))
        store.series = replace(store.series, records=())
        assert read_review_series(store, query).state is ReviewPointState.MISSING
        with pytest.raises(ReviewSeriesError, match="does not offer"):
            read_review_series(store, replace(query, lead_hours=49))

    def test_ambiguous_revisions_do_not_choose_uuid_order(self) -> None:
        query = series_query()
        first = record(query)
        store = series_store(first)
        store.series = replace(
            store.series, records=(first, replace(first, forecast_id=uuid4()))
        )
        with pytest.raises(ReviewSeriesError, match="Ambiguous"):
            read_review_series(store, query)

    def test_unknown_support_refuses_combined_mode_before_store(self) -> None:
        query = replace(
            series_query(), source_mode=ReviewSourceMode.OPERATIONAL_WITH_HINDCAST
        )
        store = Catalog()
        with pytest.raises(ReviewSeriesError, match="support is unknown"):
            read_review_series(store, query)
        assert store.series_queries == []

    def test_publication_policy_precedes_source_lookup(self) -> None:
        query = series_query()
        query = replace(
            query, scope=replace(query.scope, policy=ReviewPolicy.PUBLISHED_ONLY)
        )
        store = Catalog()
        with pytest.raises(ReviewSeriesError, match="publication policy"):
            read_review_series(store, query)
        assert store.series_queries == []

    def test_empty_published_period_is_generically_withheld(self) -> None:
        query = series_query()
        query = replace(
            query,
            scope=replace(query.scope, policy=ReviewPolicy.PUBLISHED_ONLY),
            source_mode=ReviewSourceMode.OPERATIONAL,
            hindcast_run_id=None,
            hindcast_artifact_id=None,
            forcing_type=None,
            time_step_seconds=None,
        )
        selected = replace(
            source(),
            kind=ReviewKind.OPERATIONAL,
            hindcast_run_id=None,
            forcing_type=None,
        )
        store = Catalog()
        store.series = ReviewSeriesSnapshot(sources=(selected,), records=())
        result = read_review_series(store, query)
        assert result.state is ReviewPointState.WITHHELD
        assert result.reason == "no_visible_published_forecasts_in_period"


class TestReadReviewOptions:
    def test_refuses_protected_hindcast_metadata_from_store(self) -> None:
        query = ReviewOptionsQuery(
            station_id=StationId(uuid4()),
            tenant_id=DEFAULT_TENANT_ID,
            policy=ReviewPolicy.PUBLISHED_ONLY,
        )
        with pytest.raises(ValueError, match="Protected hindcast metadata"):
            read_review_options(Catalog((source(),)), query)

    def test_source_limit_is_not_silent_truncation(self) -> None:
        query = ReviewOptionsQuery(
            station_id=StationId(uuid4()),
            tenant_id=DEFAULT_TENANT_ID,
            policy=ReviewPolicy.ORDINARY,
        )
        with pytest.raises(ReviewCatalogLimitError, match="source limit"):
            read_review_options(Catalog((source(),) * 101), query)


class TestReviewThresholdProbabilities:
    def threshold(self, query: ReviewSeriesQuery) -> StationThreshold:
        return StationThreshold(
            station_id=query.scope.station_id,
            danger_level="High",
            parameter="discharge",
            value=3,
            source=ThresholdSource.AUTHORITY,
            created_at=query.start,
            updated_at=query.start,
        )

    def test_canonical_snapshot_and_empirical_probability(self) -> None:
        query = series_query()
        result = read_review_series(
            series_store(record(query)), query, (self.threshold(query),)
        )
        snapshot = result.thresholds[0]
        point = result.segments[0].points[0]
        assert snapshot.value == 3
        assert snapshot.units == "m3/s"
        assert point.threshold_probabilities[snapshot.key].probability == 0.4
        assert point.reason == "qc_unchecked"
        response = ReviewSeriesResponse.from_domain(result)
        assert (
            response.model_dump(mode="json")["thresholds"][0]["source"] == "authority"
        )
        assert "not a peak" in response.probability_definition

    def test_distinct_sources_are_not_overwritten(self) -> None:
        query = series_query()
        threshold = self.threshold(query)
        result = read_review_series(
            series_store(record(query)),
            query,
            (
                threshold,
                replace(threshold, source=ThresholdSource.INFERRED, value=4),
            ),
        )
        probabilities = result.segments[0].points[0].threshold_probabilities
        assert len(probabilities) == 2
        assert sorted(p.probability for p in probabilities.values()) == [0.2, 0.4]

    @pytest.mark.parametrize(
        "qc", [QcStatus.QC_FAILED, QcStatus.QC_SUSPECT, QcStatus.MISSING]
    )
    def test_rejected_or_missing_values_have_no_probabilities(
        self, qc: QcStatus
    ) -> None:
        query = series_query()
        value = replace(record(query), qc_status=qc)
        result = read_review_series(
            series_store(value), query, (self.threshold(query),)
        )
        assert result.segments[0].points[0].threshold_probabilities == {}

    def test_forecast_units_are_not_assumed(self) -> None:
        query = series_query()
        value = record(query)
        value = replace(value, source=replace(value.source, units="l/s"))
        result = read_review_series(
            series_store(value), query, (self.threshold(query),)
        )
        probability = next(
            iter(result.segments[0].points[0].threshold_probabilities.values())
        )
        assert probability.probability is None
        assert probability.reason == "forecast_units_not_canonical_discharge"

    def test_other_parameter_threshold_is_not_used(self) -> None:
        query = series_query()
        result = read_review_series(
            series_store(record(query)),
            query,
            (replace(self.threshold(query), parameter="water_level"),),
        )
        assert result.thresholds == ()
        assert result.segments[0].points[0].threshold_probabilities == {}

    def test_wrong_station_threshold_rejected(self) -> None:
        query = series_query()
        with pytest.raises(ValueError, match="another station"):
            read_review_series(
                series_store(record(query)),
                query,
                (replace(self.threshold(query), station_id=StationId(uuid4())),),
            )

    def test_quantile_tail_serializes_bounds_not_zero(self) -> None:
        query = series_query()
        value = replace(
            record(query),
            representation=EnsembleRepresentation.QUANTILES,
            values=((0.05, 1), (0.25, 2), (0.5, 3), (0.75, 4), (0.95, 5)),
        )
        result = read_review_series(
            series_store(value), query, (replace(self.threshold(query), value=6),)
        )
        body = ReviewSeriesResponse.from_domain(result).model_dump(mode="json")
        probability = next(
            iter(body["segments"][0]["points"][0]["threshold_probabilities"].values())
        )
        assert probability["probability"] is None
        assert probability["lower"] == 0
        assert probability["upper"] == pytest.approx(0.05)
