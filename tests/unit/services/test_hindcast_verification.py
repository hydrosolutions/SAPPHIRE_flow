from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import polars as pl
import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.hindcast_verification import (
    declared_observation_fetch_bounds,
    declared_observation_lookup,
    evaluation_context,
    verification_sources,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import AggregationMethod, ForcingType, QcStatus
from sapphire_flow.types.forecast import HindcastForecast
from sapphire_flow.types.forecast_period import ForecastPeriodDefinition
from sapphire_flow.types.hindcast_inputs import (
    HistoricalAvailabilityPolicy,
    HistoricalSourcePeriod,
)
from sapphire_flow.types.ids import ArtifactId, HindcastForecastId, ModelId, StationId
from sapphire_flow.types.time_grid import PeriodLabel, TimeGrid
from tests.conftest import make_observation

_DAY = timedelta(days=1)
_SHIFT = timedelta(minutes=15)
_START = ensure_utc(datetime(2026, 11, 1, 18, tzinfo=UTC))


def _hindcast(label: PeriodLabel = PeriodLabel.START) -> HindcastForecast:
    station = StationId(uuid4())
    period = ForecastPeriodDefinition(
        grid=TimeGrid(step=_DAY, phase=timedelta(hours=18)),
        label=label,
        aggregation=AggregationMethod.MEAN,
    )
    ensemble = ForecastEnsemble.from_members(
        station_id=station,
        issued_at=ensure_utc(_START + timedelta(hours=6)),
        parameter="discharge",
        units="m³/s",
        time_step=_DAY,
        period_definition=period,
        values=pl.DataFrame(
            {
                "valid_time": [
                    _START + (_DAY if label is PeriodLabel.END else timedelta(0))
                ],
                "member_id": [0],
                "value": [10.0],
            }
        ),
    )
    source = HistoricalSourcePeriod(
        station_id=station,
        source="measured",
        parameter="discharge",
        period=replace(
            period, grid=TimeGrid(step=_DAY, phase=timedelta(hours=18, minutes=15))
        ),
        evidence="daily reporting interval",
        input_support_displacement=_SHIFT,
    )
    return HindcastForecast(
        id=HindcastForecastId(uuid4()),
        station_id=station,
        model_id=ModelId("period-model"),
        model_artifact_id=ArtifactId(uuid4()),
        hindcast_step=ensemble.issued_at,
        forcing_type=ForcingType.REANALYSIS,
        representation=ensemble.representation,
        hindcast_run_id=uuid4(),
        ensemble=ensemble,
        created_at=ensure_utc(_START + 10 * _DAY),
        source_periods=(source,),
        input_availability_policy=HistoricalAvailabilityPolicy.RETROSPECTIVE_CLOSURE,
    )


class TestDeclaredObservationLookup:
    @pytest.mark.parametrize("label", [PeriodLabel.START, PeriodLabel.END])
    def test_approximation_uses_actual_reference_closure(
        self, label: PeriodLabel
    ) -> None:
        hindcast = _hindcast(label)
        context = evaluation_context([hindcast], _SHIFT)
        assert context is not None
        offset = _DAY if label is PeriodLabel.END else timedelta(0)
        observation = make_observation(
            station_id=hindcast.station_id,
            timestamp=ensure_utc(_START + _SHIFT + offset),
            value=12.0,
        )
        sources = verification_sources([hindcast])
        assert (
            declared_observation_lookup(
                [observation],
                sources=sources,
                context=context,
                now=ensure_utc(_START + _DAY + timedelta(minutes=5)),
            )
            == {}
        )
        assert declared_observation_lookup(
            [observation],
            sources=sources,
            context=context,
            now=ensure_utc(_START + _DAY + _SHIFT),
        ) == {_START + offset: 12.0}
        assert context.reference_period.grid.phase == timedelta(hours=18, minutes=15)
        assert observation.timestamp == _START + _SHIFT + offset

    def test_exact_comparison_refuses_incompatible_daily_means(self) -> None:
        hindcast = _hindcast()
        context = evaluation_context([hindcast])
        assert context is not None
        observation = make_observation(
            station_id=hindcast.station_id,
            timestamp=ensure_utc(_START + _SHIFT),
            value=12.0,
        )
        with pytest.raises(ConfigurationError, match="do not nest"):
            declared_observation_lookup(
                [observation],
                sources=hindcast.source_periods,
                context=context,
                now=ensure_utc(_START + 2 * _DAY),
            )

    def test_qc_failure_leaves_an_incomplete_period(self) -> None:
        hindcast = _hindcast()
        source = replace(
            hindcast.source_periods[0],
            period=ForecastPeriodDefinition(
                grid=TimeGrid(step=timedelta(hours=1), phase=timedelta(0)),
                label=PeriodLabel.START,
                aggregation=AggregationMethod.MEAN,
            ),
            input_support_displacement=timedelta(0),
        )
        context = evaluation_context([hindcast])
        assert context is not None
        observations = [
            make_observation(
                station_id=hindcast.station_id,
                timestamp=ensure_utc(_START + timedelta(hours=i)),
                value=float(i),
                qc_status=QcStatus.QC_FAILED if i == 23 else QcStatus.QC_PASSED,
            )
            for i in range(24)
        ]
        assert (
            declared_observation_lookup(
                observations,
                sources=(source,),
                context=context,
                now=ensure_utc(_START + 2 * _DAY),
            )
            == {}
        )
        observations[-1] = replace(observations[-1], qc_status=QcStatus.QC_PASSED)
        assert declared_observation_lookup(
            observations,
            sources=(source,),
            context=context,
            now=ensure_utc(_START + 2 * _DAY),
        ) == {_START: 11.5}


class TestDeclaredObservationFetchBounds:
    def test_end_label_and_displacement_include_closing_label(self) -> None:
        hindcast = _hindcast(PeriodLabel.END)
        context = evaluation_context([hindcast], _SHIFT)
        assert context is not None
        start, end = declared_observation_fetch_bounds(
            [hindcast], context, hindcast.source_periods
        )
        closing = _START + _DAY + _SHIFT
        assert start <= closing < end
        assert end == closing + _DAY + timedelta(microseconds=1)


class TestEvaluationContext:
    def test_different_labels_cannot_share_a_scoring_cohort(self) -> None:
        with pytest.raises(ConfigurationError, match="mixed hindcast period"):
            evaluation_context(
                [_hindcast(PeriodLabel.START), _hindcast(PeriodLabel.END)]
            )

    def test_different_availability_policies_cannot_share_a_scoring_cohort(
        self,
    ) -> None:
        hindcast = _hindcast()
        other = replace(
            hindcast,
            input_availability_policy=HistoricalAvailabilityPolicy.RECORDED_ONLY,
        )
        with pytest.raises(ConfigurationError, match="mixed hindcast period"):
            evaluation_context([hindcast, other])

    def test_conflicting_native_declaration_refuses(self) -> None:
        hindcast = _hindcast()
        other = replace(
            hindcast.source_periods[0], stored_label_shift=timedelta(hours=6)
        )
        with pytest.raises(ConfigurationError, match="conflicting verification source"):
            verification_sources([hindcast], (other,))


class TestDeclaredSkillComputation:
    @pytest.mark.parametrize("label", [PeriodLabel.START, PeriodLabel.END])
    def test_scores_accepted_reference_period_and_signed_label_lead(
        self, label: PeriodLabel
    ) -> None:
        from sapphire_flow.services.skill.service import compute_skill_for_station
        from sapphire_flow.types.enums import SkillSource

        hindcast = _hindcast(label)
        offset = _DAY if label is PeriodLabel.END else timedelta(0)
        scores, diagrams = compute_skill_for_station(
            station_id=hindcast.station_id,
            model_id=hindcast.model_id,
            artifact_id=hindcast.model_artifact_id,
            hindcasts=[hindcast],
            observations=[
                make_observation(
                    station_id=hindcast.station_id,
                    timestamp=ensure_utc(_START + _SHIFT + offset),
                    value=12.0,
                )
            ],
            thresholds=[],
            flow_regime_config=None,
            seasons=[],
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=lambda: ensure_utc(_START + 10 * _DAY),
            uuid_factory=uuid4,
            parameter="discharge",
            reference_displacement=_SHIFT,
        )
        mae = next(row for row in scores if row.metric == "mae")
        assert mae.score == 2.0 and mae.sample_size == 1
        assert mae.lead_time_hours == (18 if label is PeriodLabel.END else -6)
        assert mae.computation_version == 5
        assert mae.evaluation_context == evaluation_context([hindcast], _SHIFT)
        assert all(row.evaluation_context == mae.evaluation_context for row in diagrams)

    def test_fractional_elapsed_lead_refuses_scoring(self) -> None:
        from sapphire_flow.services.skill.service import compute_skill_for_station
        from sapphire_flow.types.enums import SkillSource

        original = _hindcast()
        issue = ensure_utc(original.hindcast_step + timedelta(minutes=5))
        hindcast = replace(
            original,
            hindcast_step=issue,
            ensemble=replace(original.ensemble, issued_at=issue),
        )
        with pytest.raises(ConfigurationError, match="whole-hour elapsed leads"):
            compute_skill_for_station(
                station_id=hindcast.station_id,
                model_id=hindcast.model_id,
                artifact_id=hindcast.model_artifact_id,
                hindcasts=[hindcast],
                observations=[
                    make_observation(
                        station_id=hindcast.station_id,
                        timestamp=ensure_utc(_START + _SHIFT),
                        value=12.0,
                    )
                ],
                thresholds=[],
                flow_regime_config=None,
                seasons=[],
                skill_source=SkillSource.HINDCAST_REANALYSIS,
                forcing_type=ForcingType.REANALYSIS,
                clock=lambda: ensure_utc(_START + 10 * _DAY),
                uuid_factory=uuid4,
                parameter="discharge",
                reference_displacement=_SHIFT,
            )
