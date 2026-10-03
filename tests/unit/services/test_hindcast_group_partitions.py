from __future__ import annotations

import random
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal
from uuid import UUID

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from sapphire_flow.adapters import forecast_interface as fi
from sapphire_flow.services.hindcast import (
    _stack_hindcast_inputs,  # pyright: ignore[reportPrivateUsage] - narrow helper parity
    run_group_hindcast,
)
from sapphire_flow.services.model_registry import build_station_code_resolver
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import (
    ForcingRoute,
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.forecast_evidence import StationSourceEvidence
from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId, StationId
from sapphire_flow.types.model import StationInputData, StationModelInputs
from sapphire_flow.types.station import StationGroup, StationWeatherSource
from tests.conftest import (
    make_observation,
    make_raw_historical_forcing,
    make_station_config,
)
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeHindcastStore,
    FakeObservationStore,
    FakeStationStore,
)

if TYPE_CHECKING:
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.training import HindcastStepResult

BASE = ensure_utc(datetime(2025, 1, 10, tzinfo=UTC))
SIDS = (StationId(UUID(int=801)), StationId(UUID(int=802)))
MODEL = ModelId("partition-recorder")


@dataclass(frozen=True, kw_only=True, slots=True)
class MatrixCase:
    step: timedelta
    horizon: int
    phase: Literal["aligned", "offphase"]

    @property
    def issue(self) -> UtcDatetime:
        fraction = 0.5 if self.step == timedelta(hours=1) else 0.25
        return ensure_utc(
            BASE + (self.step * fraction if self.phase == "offphase" else timedelta())
        )

    @property
    def first_future(self) -> UtcDatetime:
        return ensure_utc(
            BASE + (self.step if self.phase == "offphase" else timedelta())
        )

    @property
    def offset(self) -> int:
        return int((self.first_future - (BASE - self.step)) / self.step)


# Forcing uses the existing mean fallback, not a declared aggregation policy.
# Within-bucket variation tests partition preservation under that shipped behavior.
def values(member: int, bucket: int, quarter: int, parameter: str) -> float:
    base = {"discharge": 100.0, "temperature": 300.0, "precipitation_forecast": 500.0}[
        parameter
    ]
    return member * 1000 + base + (bucket + 4) * 10 + quarter * 2


def expected_series(
    case: MatrixCase, member: int, parameter: str, buckets: range
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "datetime": [BASE + k * case.step for k in buckets],
            parameter: [
                member * 1000
                + {
                    "discharge": 100.0,
                    "temperature": 300.0,
                    "precipitation_forecast": 500.0,
                }[parameter]
                + (k + 4) * 10
                + 3.0
                for k in buckets
            ],
        }
    )


class PartitionRecorder:
    artifact_scope = fi.FIArtifactScope.GROUP

    def __init__(self, case: MatrixCase) -> None:
        self.case = case
        self.calls: list[tuple[datetime, fi.ModelInputs]] = []
        self.input_requirement = fi.InputRequirement(
            targets={
                "discharge": fi.TargetSpec(
                    unit=fi.Unit.M3_PER_S,
                    representations=frozenset({fi.OutputRepresentation.DETERMINISTIC}),
                )
            },
            dynamic={
                case.step: fi.SpatialInputSpec(
                    data={
                        fi.FISpatialRepresentation.POINT: fi.DynamicInputSpec(
                            past_known={
                                "obs": {
                                    "discharge": fi.PastKnownVariable(
                                        lookback=3, max_nan=0, unit=fi.Unit.M3_PER_S
                                    )
                                },
                                "nwp": {
                                    "temperature": fi.PastKnownVariable(
                                        lookback=3, max_nan=0, unit=fi.Unit.DEG_C
                                    )
                                },
                            },
                            future_known={
                                "nwp": {
                                    "precipitation_forecast": fi.FutureKnownVariable(
                                        future_steps=case.horizon,
                                        max_nan=0,
                                        unit=fi.Unit.MM,
                                    )
                                }
                            },
                        )
                    }
                )
            },
        )

    def train(
        self, inputs: fi.ModelInputs, *, config: object, rng: random.Random
    ) -> bytes:
        return b"partition-recorder"

    def serialize_artifact(self, artifact: bytes) -> bytes:
        return artifact

    def deserialize_artifact(self, raw: bytes) -> bytes:
        return raw

    def predict(
        self,
        artifact: bytes,
        *,
        inputs: fi.ModelInputs,
        issue_datetime: datetime,
        rng: random.Random,
    ) -> fi.ModelResult:
        self.calls.append((issue_datetime, inputs))
        usable: dict[str, bool] = {}
        for key, station in inputs.stations.items():
            dynamic = station.dynamic[self.case.step].data[
                fi.FISpatialRepresentation.POINT
            ]
            usable[key] = (
                dynamic.past_known["obs"]["discharge"].data.height >= 3
                and dynamic.past_known["nwp"]["temperature"].data.height >= 3
                and dynamic.future_known["nwp"]["precipitation_forecast"].data.height
                >= self.case.horizon
            )
        if not any(usable.values()):
            return fi.ModelFailure(
                model_name="partition-recorder",
                issue_datetime=issue_datetime,
                cause=fi.FailureCause.INPUT_DATA,
                message="Declared input history or horizon is short",
            )
        metadata = fi.VariableMetadata(
            unit=fi.Unit.M3_PER_S,
            timedelta=self.case.step,
            forecast_horizon=self.case.horizon,
            offset=self.case.offset,
        )
        frame = pl.DataFrame(
            {
                "issue_datetime": [issue_datetime] * self.case.horizon,
                "datetime": [
                    self.case.first_future + k * self.case.step
                    for k in range(self.case.horizon)
                ],
                "value": [10.0 + k for k in range(self.case.horizon)],
            }
        )
        outputs = {
            key: {
                "discharge": fi.VariableOutput(
                    metadata=metadata,
                    flags=frozenset(),
                    status=fi.VariableStatus.SUCCESS
                    if available
                    else fi.VariableStatus.FAILURE,
                    deterministic=fi.DeterministicData(data=frame)
                    if available
                    else None,
                )
            }
            for key, available in usable.items()
        }
        return fi.ModelSuccess(
            output=fi.ModelOutput(
                model_name="partition-recorder",
                issue_datetime=issue_datetime,
                variables=outputs,
            )
        )


def public_inputs(case: MatrixCase, future_counts: tuple[int, int]) -> fi.ModelInputs:
    stations: dict[str, fi.StationInputs] = {}
    for member, count in enumerate(future_counts):
        start = 0 if case.phase == "aligned" else 1
        dynamic = fi.DynamicInputs(
            past_known={
                "obs": {
                    "discharge": fi.InputSeries(
                        unit=fi.Unit.M3_PER_S,
                        data=expected_series(case, member, "discharge", range(-3, 0)),
                    )
                },
                "nwp": {
                    "temperature": fi.InputSeries(
                        unit=fi.Unit.DEG_C,
                        data=expected_series(case, member, "temperature", range(-3, 0)),
                    )
                },
            },
            future_known={
                "nwp": {
                    "precipitation_forecast": fi.InputSeries(
                        unit=fi.Unit.MM,
                        data=expected_series(
                            case,
                            member,
                            "precipitation_forecast",
                            range(start, start + count),
                        ),
                    )
                }
            },
        )
        stations[f"member-{member}"] = fi.StationInputs(
            dynamic={
                case.step: fi.SpatialInputs(
                    data={fi.FISpatialRepresentation.POINT: dynamic}
                )
            }
        )
    return fi.ModelInputs(stations=stations)


def run_matrix(
    case: MatrixCase, *, missing: frozenset[StationId] = frozenset()
) -> tuple[
    PartitionRecorder, FakeHindcastStore, dict[StationId, list[HindcastStepResult]]
]:
    stations = FakeStationStore()
    observations = FakeObservationStore()
    forcing = FakeWeatherReanalysisSource()
    records: list[RawHistoricalForcing] = []
    for member, sid in enumerate(SIDS):
        stations.store_station(
            make_station_config(
                station_id=sid, code=f"member-{member}", rng=random.Random(member)
            )
        )
        stations.store_weather_source(
            StationWeatherSource(
                station_id=sid,
                nwp_source="smn",
                extraction_type=SpatialRepresentation.POINT,
                status=WeatherSourceStatus.ACTIVE,
                role=WeatherSourceRole.REANALYSIS,
            )
        )
        if sid not in missing:
            observations.store_observations(
                [
                    make_observation(
                        station_id=sid,
                        timestamp=ensure_utc(
                            BASE + bucket * case.step + quarter * case.step / 4
                        ),
                        value=values(member, bucket, quarter, "discharge"),
                        rng=random.Random(member * 1000 + (bucket + 4) * 4 + quarter),
                    )
                    for bucket in range(-4, 6)
                    for quarter in range(4)
                ]
            )
        records.extend(
            [
                make_raw_historical_forcing(
                    station_id=sid,
                    parameter=parameter,
                    source="smn",
                    spatial_type=SpatialRepresentation.POINT,
                    valid_time=ensure_utc(
                        BASE + bucket * case.step + quarter * case.step / 4
                    ),
                    value=values(member, bucket, quarter, parameter),
                    rng=random.Random(member * 100 + quarter),
                )
                for bucket in range(-4, 6)
                for quarter in range(4)
                for parameter in ("temperature", "precipitation_forecast")
            ]
        )
    forcing.set_records(records)
    recorder = PartitionRecorder(case)
    adapter = fi.adapt_if_fi(
        recorder, station_code_resolver=build_station_code_resolver(stations)
    )
    assert isinstance(adapter, fi.ForecastInterfaceAdapter)
    store = FakeHindcastStore()
    group = StationGroup(
        id=StationGroupId(UUID(int=803)),
        name="partition-matrix",
        station_ids=frozenset(SIDS),
        created_at=BASE,
    )
    result = run_group_hindcast(
        model=adapter,
        artifact=b"fixture",
        group=group,
        model_id=MODEL,
        artifact_id=ArtifactId(UUID(int=804)),
        period_start=case.issue,
        period_end=ensure_utc(case.issue + case.step),
        time_step=case.step,
        forcing_source=forcing,
        obs_store=observations,
        hindcast_store=store,
        station_store=stations,
        basin_store=FakeBasinStore(),
        clock=lambda: ensure_utc(BASE + timedelta(days=20)),
        rng=random.Random(19),
        hindcast_run_id=UUID(int=805),
        lookback_steps=3,
    )
    return recorder, store, result


class TestPublicGroupPartitions:
    @pytest.mark.parametrize(
        "step", [timedelta(hours=1), timedelta(days=1)], ids=["hourly", "daily"]
    )
    @pytest.mark.parametrize("phase", ["aligned", "offphase"])
    @pytest.mark.parametrize("horizon", [1, 3])
    def test_preserves_partitions(
        self, step: timedelta, phase: Literal["aligned", "offphase"], horizon: int
    ) -> None:
        case = MatrixCase(step=step, phase=phase, horizon=horizon)
        recorder, store, result = run_matrix(case)
        assert len(recorder.calls) == 1
        issue, inputs = recorder.calls[0]
        assert issue == case.issue
        assert set(inputs.stations) == {"member-0", "member-1"}
        for member, sid in enumerate(SIDS):
            dynamic = (
                inputs.stations[f"member-{member}"]
                .dynamic[step]
                .data[fi.FISpatialRepresentation.POINT]
            )
            target = dynamic.past_known["obs"]["discharge"]
            past = dynamic.past_known["nwp"]["temperature"]
            future = dynamic.future_known["nwp"]["precipitation_forecast"]
            start = 0 if phase == "aligned" else 1
            assert target.unit is fi.Unit.M3_PER_S
            assert past.unit is fi.Unit.DEG_C
            assert future.unit is fi.Unit.MM
            assert_frame_equal(
                target.data, expected_series(case, member, "discharge", range(-3, 0))
            )
            assert_frame_equal(
                past.data, expected_series(case, member, "temperature", range(-3, 0))
            )
            assert_frame_equal(
                future.data,
                expected_series(
                    case,
                    member,
                    "precipitation_forecast",
                    range(start, start + horizon),
                ),
            )
            assert len(result[sid]) == 1 and result[sid][0].success
            forecasts = store.fetch_hindcasts(
                sid, MODEL, case.issue, ensure_utc(case.issue + step)
            )
            assert len(forecasts) == 1
            assert forecasts[0].ensemble.values["valid_time"].to_list() == [
                case.first_future + k * step for k in range(horizon)
            ]

    def test_missing_history_member_is_not_in_public_roster(self) -> None:
        case = MatrixCase(step=timedelta(hours=1), phase="offphase", horizon=3)
        recorder, store, result = run_matrix(case, missing=frozenset({SIDS[1]}))
        assert len(recorder.calls) == 1
        assert set(recorder.calls[0][1].stations) == {"member-0"}
        assert result[SIDS[0]][0].success
        assert result[SIDS[1]][0].error == "insufficient data"
        assert not store.fetch_hindcasts(
            SIDS[1], MODEL, case.issue, ensure_utc(case.issue + case.step)
        )
        assert (
            len(
                store.fetch_hindcasts(
                    SIDS[0], MODEL, case.issue, ensure_utc(case.issue + case.step)
                )
            )
            == 1
        )


class TestRecorderCompliance:
    @pytest.mark.parametrize("phase,offset", [("aligned", 1), ("offphase", 2)])
    def test_output_grid_and_offset(
        self, phase: Literal["aligned", "offphase"], offset: int
    ) -> None:
        case = MatrixCase(step=timedelta(hours=1), horizon=3, phase=phase)
        recorder = PartitionRecorder(case)
        result = recorder.predict(
            b"fixture",
            inputs=public_inputs(case, (3, 3)),
            issue_datetime=case.issue,
            rng=random.Random(0),
        )
        assert isinstance(result, fi.ModelSuccess)
        for variables in result.output.variables.values():
            output = variables["discharge"]
            assert output.metadata.offset == offset
            assert output.metadata.forecast_horizon == 3
            assert output.metadata.timedelta == case.step
            assert output.deterministic is not None
            assert output.deterministic.data["datetime"].to_list() == [
                case.first_future + k * case.step for k in range(3)
            ]

    def test_records_then_returns_input_failure_for_all_short(self) -> None:
        case = MatrixCase(step=timedelta(hours=1), horizon=3, phase="aligned")
        recorder = PartitionRecorder(case)
        result = recorder.predict(
            b"fixture",
            inputs=public_inputs(case, (2, 2)),
            issue_datetime=case.issue,
            rng=random.Random(0),
        )
        assert len(recorder.calls) == 1
        assert isinstance(result, fi.ModelFailure)
        assert result.cause is fi.FailureCause.INPUT_DATA

    def test_mixed_availability_is_only_a_recorder_contract(self) -> None:
        case = MatrixCase(step=timedelta(hours=1), horizon=3, phase="aligned")
        recorder = PartitionRecorder(case)
        result = recorder.predict(
            b"fixture",
            inputs=public_inputs(case, (3, 2)),
            issue_datetime=case.issue,
            rng=random.Random(0),
        )
        assert isinstance(result, fi.ModelSuccess)
        assert (
            result.output.variables["member-0"]["discharge"].status
            is fi.VariableStatus.SUCCESS
        )
        assert (
            result.output.variables["member-1"]["discharge"].status
            is fi.VariableStatus.FAILURE
        )


class TestTypedHindcastStack:
    def test_dynamic_and_nonempty_static_round_trip(self) -> None:
        inputs = typed_inputs()
        grouped = _stack_hindcast_inputs(StationGroupId(UUID(int=803)), inputs)
        assert grouped.station_ids == SIDS
        assert grouped.group_id == StationGroupId(UUID(int=803))
        assert grouped.issue_time == BASE
        assert grouped.forecast_horizon_steps == 3
        assert grouped.time_step == timedelta(hours=1)
        for sid, original in inputs.items():
            actual = grouped.for_station(sid)
            assert_frame_equal(actual.past_targets, original.data.past_targets)
            assert_frame_equal(actual.past_dynamic, original.data.past_dynamic)
            assert_frame_equal(actual.future_dynamic, original.data.future_dynamic)
            assert actual.static is not None and original.data.static is not None
            assert_frame_equal(actual.static, original.data.static)

    @pytest.mark.parametrize("partition", ["past_dynamic", "future_dynamic"])
    @pytest.mark.parametrize("empty_members", [(0,), (0, 1)])
    def test_compatible_empty_partitions(
        self, partition: str, empty_members: tuple[int, ...]
    ) -> None:
        inputs = typed_inputs()
        for member in empty_members:
            sid = SIDS[member]
            data = inputs[sid].data
            inputs[sid] = replace(
                inputs[sid],
                data=replace(data, **{partition: getattr(data, partition).head(0)}),
            )
        grouped = _stack_hindcast_inputs(StationGroupId(UUID(int=803)), inputs)
        for sid in SIDS:
            assert_frame_equal(
                getattr(grouped.for_station(sid), partition),
                getattr(inputs[sid].data, partition),
            )

    def test_zero_column_optional_frames_do_not_invent_rows(self) -> None:
        inputs = {
            sid: replace(
                inp,
                data=replace(
                    inp.data, past_dynamic=pl.DataFrame(), future_dynamic=pl.DataFrame()
                ),
            )
            for sid, inp in typed_inputs().items()
        }
        grouped = _stack_hindcast_inputs(StationGroupId(UUID(int=803)), inputs)
        for frame in (grouped.past_dynamic, grouped.future_dynamic):
            assert frame.schema == {"station_id": pl.String}
            assert frame.height == 0

    @pytest.mark.parametrize(
        "states",
        [("none", "none"), ("none", "full"), ("empty", "empty"), ("empty", "full")],
    )
    def test_static_subset_and_empty_slice_normalization(
        self, states: tuple[str, str]
    ) -> None:
        inputs = typed_inputs()
        for sid, state in zip(SIDS, states, strict=True):
            frame = inputs[sid].data.static
            assert frame is not None
            selected = (
                None
                if state == "none"
                else frame.head(0)
                if state == "empty"
                else frame
            )
            inputs[sid] = replace(
                inputs[sid], data=replace(inputs[sid].data, static=selected)
            )
        grouped = _stack_hindcast_inputs(StationGroupId(UUID(int=803)), inputs)
        if states == ("none", "none"):
            assert grouped.static is None
        else:
            assert grouped.static is not None
            assert grouped.static.schema == {
                "station_id": pl.String,
                "elevation": pl.Float64,
            }
            assert grouped.static.height == states.count("full")
        for sid, state in zip(SIDS, states, strict=True):
            if state == "full":
                actual = grouped.for_station(sid).static
                expected = inputs[sid].data.static
                assert actual is not None and expected is not None
                assert_frame_equal(actual, expected)
            else:
                assert grouped.for_station(sid).static is None

    @pytest.mark.parametrize(
        "field,value",
        [
            ("issue_time", ensure_utc(BASE + timedelta(hours=1))),
            ("time_step", timedelta(days=1)),
            ("forecast_horizon_steps", 1),
        ],
    )
    def test_inconsistent_metadata_refused(self, field: str, value: object) -> None:
        inputs = typed_inputs()
        inputs[SIDS[1]] = replace(inputs[SIDS[1]], **{field: value})
        with pytest.raises(ValueError, match=f"Inconsistent {field}"):
            _stack_hindcast_inputs(StationGroupId(UUID(int=803)), inputs)

    def test_empty_mapping_refused(self) -> None:
        with pytest.raises(ValueError, match="Cannot stack empty"):
            _stack_hindcast_inputs(StationGroupId(UUID(int=803)), {})

    def test_source_evidence_keeps_each_member_and_order(self) -> None:
        inputs = typed_inputs()
        evidence = {
            sid: StationSourceEvidence(
                observations=(
                    make_observation(
                        station_id=sid,
                        timestamp=BASE,
                        value=float(member),
                        rng=random.Random(member),
                    ),
                )
            )
            for member, sid in enumerate(SIDS)
        }
        reversed_inputs = {
            sid: replace(inputs[sid], source_evidence=evidence[sid])
            for sid in reversed(SIDS)
        }
        grouped = _stack_hindcast_inputs(StationGroupId(UUID(int=803)), reversed_inputs)
        assert grouped.station_ids == tuple(reversed(SIDS))
        assert grouped.source_evidence == tuple(
            (sid, evidence[sid]) for sid in reversed(SIDS)
        )
        reversed_inputs[SIDS[1]] = replace(
            reversed_inputs[SIDS[1]], source_evidence=None
        )
        assert _stack_hindcast_inputs(
            StationGroupId(UUID(int=803)), reversed_inputs
        ).source_evidence == ((SIDS[0], evidence[SIDS[0]]),)
        assert (
            _stack_hindcast_inputs(
                StationGroupId(UUID(int=803)), inputs
            ).source_evidence
            == ()
        )

    def test_member_identity_mismatch_refused_before_relabelling(self) -> None:
        inputs = typed_inputs()
        inputs[SIDS[0]] = replace(
            inputs[SIDS[0]],
            station_id=SIDS[1],
            source_evidence=StationSourceEvidence(
                observations=(
                    make_observation(
                        station_id=SIDS[1], timestamp=BASE, rng=random.Random(0)
                    ),
                )
            ),
        )
        with pytest.raises(ValueError, match="station_id mismatch"):
            _stack_hindcast_inputs(StationGroupId(UUID(int=803)), inputs)

    def test_unsupported_route_refused(self) -> None:
        inputs = typed_inputs()
        inputs[SIDS[0]] = replace(inputs[SIDS[0]], forcing_route=ForcingRoute.PER_TRACK)
        with pytest.raises(ValueError, match="Unsupported forcing_route"):
            _stack_hindcast_inputs(StationGroupId(UUID(int=803)), inputs)


def typed_inputs() -> dict[StationId, StationModelInputs]:
    result: dict[StationId, StationModelInputs] = {}
    for member, sid in enumerate(SIDS):
        frame = pl.DataFrame(
            {
                "timestamp": [BASE - timedelta(hours=1)],
                "temperature": [float(member)],
                "temperature_provenance": ["reanalysis"],
            }
        )
        future = frame.with_columns(pl.lit(BASE).alias("timestamp"))
        result[sid] = StationModelInputs(
            station_id=sid,
            issue_time=BASE,
            forecast_horizon_steps=3,
            time_step=timedelta(hours=1),
            data=StationInputData(
                past_targets=frame.rename(
                    {
                        "temperature": "discharge",
                        "temperature_provenance": "discharge_provenance",
                    }
                ),
                past_dynamic=frame,
                future_dynamic=future,
                static=pl.DataFrame({"elevation": [100.0 + member]}),
            ),
        )
    return result
