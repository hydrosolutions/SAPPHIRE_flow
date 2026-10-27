from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from sapphire_flow.types.daily_forecast import DailyAttemptState
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberOutcome,
    DailyMemberReason,
    DailyMemberState,
    DailyOutputReference,
    DailyOutputSource,
    finalized_outcome_state,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.forecast_reference import ForecastOutputKey
from sapphire_flow.types.ids import ModelId, StationId

SID = StationId(UUID(int=1))
ISSUED = ensure_utc(datetime(2026, 10, 26, tzinfo=UTC))


def _reference(parameter: str = "discharge") -> DailyOutputReference:
    return DailyOutputReference(
        key=ForecastOutputKey(
            station_id=SID,
            model_id=ModelId("cmal_small"),
            issued_at=ISSUED,
            parameter=parameter,
        ),
        source=DailyOutputSource.FORECAST,
        output_id=UUID(int={"discharge": 2, "water_level": 3, "other": 4}[parameter]),
    )


def _produced() -> DailyMemberOutcome:
    return DailyMemberOutcome(
        station_id=SID,
        required_parameters=("discharge", "water_level"),
        state=DailyMemberState.PRODUCED,
        references=(_reference(), _reference("water_level")),
    )


class TestDailyMemberOutcome:
    def test_every_parameter_keeps_its_exact_source_and_id(self) -> None:
        outcome = _produced()
        rejected = replace(
            outcome.references[1],
            source=DailyOutputSource.REJECTED,
            output_id=UUID(int=3),
        )
        outcome = replace(outcome, references=(outcome.references[0], rejected))
        assert outcome.references[1] == rejected

    @pytest.mark.parametrize("parameters", [(), ("discharge", "discharge"), ("",)])
    def test_required_parameters_are_nonempty_and_unique(
        self, parameters: tuple[str, ...]
    ) -> None:
        with pytest.raises(ValueError, match="required parameters"):
            replace(_produced(), required_parameters=parameters)

    @pytest.mark.parametrize(
        "references",
        [
            (),
            (_reference(),),
            (_reference(), _reference()),
            (_reference(), _reference("water_level"), _reference("other")),
        ],
    )
    def test_production_requires_exact_parameter_coverage(
        self, references: tuple[DailyOutputReference, ...]
    ) -> None:
        with pytest.raises(ValueError, match="parameter references"):
            replace(_produced(), references=references)

    def test_reference_cannot_belong_to_another_member(self) -> None:
        wrong = replace(
            _reference(),
            key=replace(_reference().key, station_id=StationId(UUID(int=99))),
        )
        with pytest.raises(ValueError, match="station"):
            replace(_produced(), references=(wrong, _reference("water_level")))

    @pytest.mark.parametrize("change", ["model", "issue", "id"])
    def test_parameter_references_cannot_mix_contexts_or_reuse_one_row(
        self, change: str
    ) -> None:
        outcome = _produced()
        ref = outcome.references[1]
        if change == "model":
            ref = replace(ref, key=replace(ref.key, model_id=ModelId("other")))
        elif change == "issue":
            ref = replace(
                ref,
                key=replace(
                    ref.key, issued_at=ensure_utc(datetime(2026, 10, 25, tzinfo=UTC))
                ),
            )
        else:
            ref = replace(ref, output_id=outcome.references[0].output_id)
        with pytest.raises(ValueError, match="parameter references"):
            replace(outcome, references=(outcome.references[0], ref))

    def test_exclusion_requires_an_explicit_matching_reason(self) -> None:
        with pytest.raises(ValueError, match="member reason"):
            DailyMemberOutcome(
                station_id=SID,
                required_parameters=("discharge",),
                state=DailyMemberState.INPUT_EXCLUDED,
                reason=DailyMemberReason.MODEL_FAILURE,
            )

    def test_pending_is_not_production(self) -> None:
        with pytest.raises(ValueError, match="references"):
            replace(_produced(), state=DailyMemberState.PENDING)

    def test_integrity_rejection_requires_committed_rejected_evidence(self) -> None:
        with pytest.raises(ValueError, match="rejected evidence"):
            replace(_produced(), state=DailyMemberState.INTEGRITY_REJECTED)


class TestFinalizedOutcomeState:
    def test_production_and_genuine_exclusion_close_the_day(self) -> None:
        excluded = DailyMemberOutcome(
            station_id=StationId(UUID(int=3)),
            required_parameters=("discharge",),
            state=DailyMemberState.INPUT_EXCLUDED,
            reason=DailyMemberReason.MISSING_REQUIRED_TARGET,
        )
        assert (
            finalized_outcome_state((_produced(), excluded))
            is DailyAttemptState.COMPLETE
        )

    @pytest.mark.parametrize(
        "state",
        [
            DailyMemberState.PENDING,
            DailyMemberState.MODEL_FAILED,
            DailyMemberState.INPUT_EXCLUDED,
            DailyMemberState.CONFIGURATION_EXCLUDED,
        ],
    )
    def test_without_production_the_day_needs_a_retry(
        self, state: DailyMemberState
    ) -> None:
        reasons = {
            DailyMemberState.PENDING: None,
            DailyMemberState.MODEL_FAILED: DailyMemberReason.MODEL_FAILURE,
            DailyMemberState.INPUT_EXCLUDED: DailyMemberReason.MISSING_REQUIRED_TARGET,
            DailyMemberState.CONFIGURATION_EXCLUDED: DailyMemberReason.NOT_OPERATIONAL,
        }
        outcome = DailyMemberOutcome(
            station_id=SID,
            required_parameters=("discharge",),
            state=state,
            reason=reasons[state],
        )
        assert finalized_outcome_state((outcome,)) is DailyAttemptState.INCOMPLETE

    def test_a_pending_sibling_prevents_completion(self) -> None:
        pending = DailyMemberOutcome(
            station_id=StationId(UUID(int=3)),
            required_parameters=("discharge",),
            state=DailyMemberState.PENDING,
        )
        assert (
            finalized_outcome_state((_produced(), pending))
            is DailyAttemptState.INCOMPLETE
        )

    def test_duplicate_member_cannot_inflate_production(self) -> None:
        with pytest.raises(ValueError, match="duplicate member"):
            finalized_outcome_state((_produced(), _produced()))

    def test_empty_roster_is_not_a_success(self) -> None:
        assert finalized_outcome_state(()) is DailyAttemptState.INCOMPLETE
