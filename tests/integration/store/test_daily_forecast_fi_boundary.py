from __future__ import annotations

import random
from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import polars as pl
import pytest
from polars.testing import assert_frame_equal

if TYPE_CHECKING:
    import sqlalchemy as sa

    from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId

from sapphire_flow.adapters import forecast_interface as fi
from sapphire_flow.services.daily_forecast_completion import complete_daily_group
from sapphire_flow.services.run_group_forecast import (
    GroupForecastOutcome,
    GroupStatePolicy,
    run_group_forecast,
)
from sapphire_flow.store.committed_forecast_writer import PgCommittedForecastWriter
from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.daily_forecast import DailyAttemptState
from sapphire_flow.types.daily_forecast_claim import (
    DailyClaim,
    DailyClaimKey,
    FrozenDailyAssignment,
)
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberReason,
    DailyMemberState,
    DailyOutputSource,
)
from sapphire_flow.types.daily_input_provenance import DailyInputProvenance
from sapphire_flow.types.daily_output import DailyOutputContract
from sapphire_flow.types.enums import EnsembleRepresentation, NwpCycleSource
from sapphire_flow.types.ids import ForecastId, StationId
from sapphire_flow.types.model import GroupModelInputs
from sapphire_flow.types.station import StationGroup
from tests.conftest import make_station_config
from tests.fakes.fake_stores import FakeModelArtifactStore
from tests.integration.store.test_daily_forecast_claim_store import NOW
from tests.integration.store.test_daily_forecast_claim_store import (
    assignment as assignment,
)
from tests.unit.adapters.test_forecast_interface_adapter_nan_gate import (
    RecordingFIForecastModel,
)
from tests.unit.services.test_run_group_forecast import (
    _empty_qc_rules,
    _make_assignment,
    _make_config,
    _make_metadata,
    _QcChecker,
)


@pytest.mark.parametrize("horizon", [1, 10])
@pytest.mark.parametrize(
    "mode", ["accepted", "qc_rejected", "nan_gate", "model_failure", "mixed"]
)
def test_real_fi_boundary_to_committed_daily_finalization(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    horizon: int,
    mode: str,
) -> None:
    key, frozen = assignment
    sid = frozen.members[0].station_id
    gated, failed = StationId(uuid4()), StationId(uuid4())
    members = (sid, gated, failed) if mode == "mixed" else (sid,)
    codes = {sid: "gauge", gated: "gated", failed: "failed"}
    if mode == "mixed":
        with isolated_committed_db_engine.begin() as conn:
            for extra in (gated, failed):
                PgStationStore(conn).store_station(
                    make_station_config(
                        station_id=extra, code=str(extra), tenant_id=key.tenant_id
                    )
                )
    step = timedelta(days=1)
    levels = [0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95]
    frozen = replace(
        frozen,
        members=tuple(
            replace(
                frozen.members[0], station_id=member, required_parameters=("discharge",)
            )
            for member in members
        ),
        output_contracts=(
            DailyOutputContract(
                parameter="discharge",
                units="m³/s",
                representation=EnsembleRepresentation.QUANTILES,
                time_step=step,
                first_lead=timedelta(0),
                horizon_steps=horizon,
                series_keys=tuple(levels),
            ),
        ),
    )
    store = PgDailyForecastStore(isolated_committed_db_engine.begin)
    claim = store.claim(
        key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
    )
    assert isinstance(claim, DailyClaim)
    requirement = fi.InputRequirement(
        targets={
            "discharge": fi.TargetSpec(
                unit=fi.Unit.M3_PER_S,
                representations=frozenset({fi.OutputRepresentation.QUANTILES}),
            )
        },
        dynamic={
            step: fi.SpatialInputSpec(
                data={
                    fi.FISpatialRepresentation.POINT: fi.DynamicInputSpec(
                        future_known={
                            "nwp": {
                                "temperature": fi.FutureKnownVariable(
                                    unit=fi.Unit.DEG_C, future_steps=horizon, max_nan=0
                                )
                            }
                        },
                        past_known={
                            "obs": {
                                "discharge": fi.PastKnownVariable(
                                    unit=fi.Unit.M3_PER_S, lookback=1, max_nan=0
                                )
                            }
                        },
                    )
                }
            )
        },
    )
    frame = pl.DataFrame(
        {
            "issue_datetime": [NOW] * horizon,
            "datetime": [NOW + i * step for i in range(horizon)],
            **{str(q): [10.0 + q] * horizon for q in levels},
        }
    )
    output = fi.ModelSuccess(
        output=fi.ModelOutput(
            model_name="synthetic-fi",
            issue_datetime=NOW,
            variables={
                "gauge": {
                    "discharge": fi.VariableOutput(
                        metadata=fi.VariableMetadata(
                            unit=fi.Unit.M3_PER_S,
                            timedelta=step,
                            forecast_horizon=horizon,
                            offset=0,
                        ),
                        quantiles=fi.QuantileData(quantile_levels=levels, data=frame),
                        status=fi.VariableStatus.SUCCESS,
                        flags=frozenset(),
                    )
                }
            },
        )
    )
    if mode == "model_failure":
        output = fi.ModelFailure(
            model_name="synthetic-fi",
            issue_datetime=NOW,
            cause=fi.FailureCause.INPUT_DATA,
            message="synthetic short history",
        )
    received: list[set[str]] = []

    class MixedFIModel(RecordingFIForecastModel):
        def predict(
            self,
            artifact: object,
            *,
            inputs: fi.ModelInputs,
            issue_datetime: datetime,
            rng: random.Random,
        ) -> fi.ModelResult:
            received.append(set(inputs.stations))
            assert isinstance(self.result, fi.ModelSuccess)
            successful = self.result.output.variables["gauge"]["discharge"]
            return fi.ModelSuccess(
                output=fi.ModelOutput(
                    model_name="synthetic-fi",
                    issue_datetime=issue_datetime,
                    variables={
                        code: {
                            "discharge": (
                                fi.VariableOutput(
                                    metadata=successful.metadata,
                                    status=fi.VariableStatus.FAILURE,
                                    flags=frozenset(
                                        {fi.ForecastFlag.DATA_AVAILABILITY}
                                    ),
                                )
                                if code == "failed"
                                else successful
                            )
                        }
                        for code in inputs.stations
                    },
                )
            )

    inner = (MixedFIModel if mode == "mixed" else RecordingFIForecastModel)(
        output, artifact_scope=fi.FIArtifactScope.GROUP, requirement=requirement
    )
    model = fi.ForecastInterfaceAdapter(inner, station_code_resolver=codes.__getitem__)
    group = StationGroup(
        id=key.group_id,
        name="synthetic",
        station_ids=frozenset(members),
        created_at=NOW,
    )
    inputs = GroupModelInputs(
        group_id=key.group_id,
        station_ids=members,
        past_targets=pl.DataFrame(
            {
                "station_id": [str(member) for member in members],
                "timestamp": [NOW - step] * len(members),
                "discharge": [
                    float("nan") if mode == "nan_gate" or member == gated else 10.0
                    for member in members
                ],
            }
        ),
        past_dynamic=pl.DataFrame(
            {
                "station_id": [str(m) for m in members],
                "timestamp": [NOW - step] * len(members),
            }
        ),
        future_dynamic=pl.DataFrame(
            {
                "station_id": [str(m) for m in members for _ in range(horizon)],
                "timestamp": [NOW + (i + 1) * step for i in range(horizon)]
                * len(members),
                "temperature": [10.0] * horizon * len(members),
            }
        ),
        static=None,
        issue_time=NOW,
        forecast_horizon_steps=horizon,
        time_step=step,
    )

    # The database owns the frozen artifact identity; the fake only supplies bytes.
    class FrozenArtifacts(FakeModelArtifactStore):
        def fetch_active_artifact(
            self,
            model_id: ModelId,
            station_id: object = None,
            group_id: StationGroupId | None = None,
        ) -> tuple[ArtifactId, bytes]:
            assert frozen.artifact_id is not None
            return frozen.artifact_id, b"synthetic"

    def run(group: StationGroup, inputs: GroupModelInputs) -> GroupForecastOutcome:
        return run_group_forecast(
            group=group,
            group_inputs=inputs,
            metadata_by_station={m: _make_metadata(0.0) for m in group.station_ids},
            assignment=replace(
                _make_assignment(group), model_id=key.model_id, time_step=step
            ),
            model=model,
            artifact_store=FrozenArtifacts(),
            qc_checker=_QcChecker(sid if mode == "qc_rejected" else None),
            qc_rules=_empty_qc_rules(),
            qc_overrides=[],
            baselines_by_station={},
            nwp_cycle_reference_time=NOW,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            config=_make_config(),
            clock=lambda: NOW,
            id_gen=uuid4,
            rng=random.Random(1),
            state_policy=GroupStatePolicy.STATELESS_ONLY,
        )

    result = run(group, inputs)
    if mode == "mixed":
        assert received == [{"gauge", "failed"}]
        assert set(result.expected_station_ids) == set(members)
        assert set(result.results) == {sid}
        assert {issue.station_id for issue in result.unavailable_members} == {gated}
        alone_inputs = replace(
            inputs,
            station_ids=(sid,),
            past_targets=inputs.past_targets.filter(pl.col("station_id") == str(sid)),
            past_dynamic=inputs.past_dynamic.filter(pl.col("station_id") == str(sid)),
            future_dynamic=inputs.future_dynamic.filter(
                pl.col("station_id") == str(sid)
            ),
        )
        alone = run(replace(group, station_ids=frozenset({sid})), alone_inputs)
        actual = result.results[sid].forecasts[0]
        control = alone.results[sid].forecasts[0]
        assert_frame_equal(actual.ensemble.values, control.ensemble.values)
        assert actual.qc_status == control.qc_status
        assert actual.qc_flags == control.qc_flags
        assert actual.input_quality == control.input_quality
        assert actual.input_quality_flags == control.input_quality_flags
        assert actual.ensemble.model_id == key.model_id
    if mode in ("accepted", "qc_rejected", "mixed"):
        raw = model.predict_batch(b"synthetic", inputs, random.Random(1))[sid][0][
            "discharge"
        ]
        assert raw.model_id is None
    provenance = frozen.members[0].provenance
    if mode in ("accepted", "mixed"):
        forecast = result.results[sid].forecasts[0]
        provenance = DailyInputProvenance(
            nwp_source=forecast.nwp_cycle_source,
            nwp_reference_time=forecast.nwp_cycle_reference_time,
            nwp_age_hours=0.0,
            observation_staleness_hours=forecast.observation_staleness_hours,
            input_quality=forecast.input_quality,
            input_quality_flags=forecast.input_quality_flags,
        )
    with isolated_committed_db_engine.connect() as conn:
        completed = complete_daily_group(
            claim=claim,
            result=result,
            exclusions=(),
            provenance={sid: provenance},
            forecast_writer=PgCommittedForecastWriter(
                isolated_committed_db_engine.begin
            ),
            rejected_writer=PgRejectedForecastStore(
                conn, transaction_factory=isolated_committed_db_engine.begin
            ),
            store=store,
            clock=lambda: NOW,
        )
    outcome = completed.outcomes[0]
    if mode == "mixed":
        assert completed.state is DailyAttemptState.INCOMPLETE
        persisted = store.read_day(key)
        assert persisted.authoritative == persisted.latest
        attempt = persisted.latest
        assert attempt is not None
        assert attempt.claim == claim
        assert attempt.state is DailyAttemptState.INCOMPLETE
        assert attempt.outcomes is not None
        assert attempt.outcomes == completed.outcomes
        by_station = {member.station_id: member for member in attempt.outcomes}
        assert set(by_station) == set(members)
        assert by_station[gated].state is DailyMemberState.INPUT_EXCLUDED
        assert by_station[gated].reason is DailyMemberReason.NAN_TOLERANCE_EXCEEDED
        assert by_station[gated].references == ()
        assert by_station[failed].state is DailyMemberState.MODEL_FAILED
        assert by_station[failed].reason is DailyMemberReason.MODEL_FAILURE
        assert by_station[failed].references == ()
        healthy = by_station[sid]
        assert healthy.state is DailyMemberState.PRODUCED
        assert healthy.reason is None
        assert len(healthy.references) == 1
        ref = healthy.references[0]
        forecast = result.results[sid].forecasts[0]
        assert ref.source is DailyOutputSource.FORECAST
        assert ref.output_id == forecast.id
        assert ref.key.station_id == sid
        assert ref.key.model_id == key.model_id
        assert ref.key.issued_at == NOW
        assert ref.key.parameter == "discharge"
        with isolated_committed_db_engine.connect() as conn:
            forecast_store = PgForecastStore(conn)
            stored = forecast_store.fetch_forecast(ForecastId(ref.output_id))
            cycle_outputs = forecast_store.fetch_forecasts_for_cycle(NOW)
            assert {f.id for f in cycle_outputs if f.model_id == key.model_id} == {
                ref.output_id
            }
        assert stored is not None
        assert stored.model_artifact_id == frozen.artifact_id
        assert stored.model_id == key.model_id
        assert_frame_equal(stored.ensemble.values, forecast.ensemble.values)
    elif mode == "nan_gate":
        assert inner.predict_inputs is None
        assert outcome.state is DailyMemberState.INPUT_EXCLUDED
        assert outcome.reason is DailyMemberReason.NAN_TOLERANCE_EXCEEDED
    elif mode == "model_failure":
        assert outcome.state is DailyMemberState.MODEL_FAILED
    else:
        assert completed.state is DailyAttemptState.COMPLETE
        assert outcome.state is DailyMemberState.PRODUCED
        assert store.read_day(key).authoritative is not None
