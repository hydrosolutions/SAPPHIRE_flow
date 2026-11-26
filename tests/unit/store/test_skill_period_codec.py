from datetime import timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from sapphire_flow.store.skill_period_codec import (
    evaluation_context_content,
    parse_evaluation_context,
)
from sapphire_flow.types.enums import AggregationMethod
from sapphire_flow.types.forecast_period import ForecastPeriodDefinition
from sapphire_flow.types.hindcast_inputs import (
    HistoricalAvailabilityPolicy,
    HistoricalSourcePeriod,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.skill import SkillEvaluationContext
from sapphire_flow.types.time_grid import PeriodLabel, TimeGrid


def _context() -> SkillEvaluationContext:
    return SkillEvaluationContext(
        forecast_period=ForecastPeriodDefinition(
            grid=TimeGrid(step=timedelta(days=1), phase=timedelta(hours=18)),
            label=PeriodLabel.START,
            aggregation=AggregationMethod.MEAN,
        ),
        reference_period=ForecastPeriodDefinition(
            grid=TimeGrid(
                step=timedelta(days=1), phase=timedelta(hours=18, minutes=15)
            ),
            label=PeriodLabel.END,
            aggregation=AggregationMethod.MEAN,
        ),
        reference_displacement=timedelta(minutes=15),
        input_availability_policy=HistoricalAvailabilityPolicy.RETROSPECTIVE_CLOSURE,
        source_periods=(
            HistoricalSourcePeriod(
                station_id=StationId(UUID(int=1)),
                source="measured",
                parameter="discharge",
                period=ForecastPeriodDefinition(
                    grid=TimeGrid(
                        step=timedelta(days=1), phase=timedelta(hours=18, minutes=15)
                    ),
                    label=PeriodLabel.END,
                    aggregation=AggregationMethod.MEAN,
                ),
                evidence="native end-labelled daily means",
            ),
        ),
    )


class TestSkillPeriodCodec:
    def test_catalogue_order_does_not_change_canonical_identity(self) -> None:
        from dataclasses import replace

        context = _context()
        source = context.source_periods[0]
        other = replace(source, parameter="precipitation")
        first = replace(context, source_periods=(source, other))
        second = replace(context, source_periods=(other, source))
        assert first == second
        assert evaluation_context_content(first) == evaluation_context_content(second)

    def test_native_source_digest_refuses_changed_content(self) -> None:
        content = evaluation_context_content(_context())
        content["source_periods"][0]["evidence"] = "changed evidence"
        with pytest.raises(ValueError, match="source digest mismatch"):
            parse_evaluation_context(content)

    def test_round_trip_retains_different_labels_and_approximation(self) -> None:
        context = _context()
        content = evaluation_context_content(context)
        assert content["reference_displacement_seconds"] == 900
        assert parse_evaluation_context(content) == context

    def test_legacy_nulls_stay_unknown(self) -> None:
        assert parse_evaluation_context({}) is None
        assert parse_evaluation_context(evaluation_context_content(None)) is None

    @pytest.mark.parametrize(
        "missing", ["reference_period_definition", "input_availability_policy"]
    )
    def test_partial_context_refuses(self, missing: str) -> None:
        content = evaluation_context_content(_context())
        content.pop(missing)
        with pytest.raises(ValidationError, match=missing):
            parse_evaluation_context(content)

    @pytest.mark.parametrize("displacement", [True, 900.1, "900"])
    def test_displacement_is_not_coerced(self, displacement: object) -> None:
        content = evaluation_context_content(_context())
        content["reference_displacement_seconds"] = displacement
        with pytest.raises(ValidationError, match="reference_displacement_seconds"):
            parse_evaluation_context(content)

    def test_mismatched_reference_phase_refuses(self) -> None:
        content = evaluation_context_content(_context())
        content["reference_displacement_seconds"] = 0
        with pytest.raises(ValueError, match="physical periods are incompatible"):
            parse_evaluation_context(content)
