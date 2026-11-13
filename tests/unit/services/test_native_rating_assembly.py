from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from sapphire_flow.services.operational_inputs import (
    assemble_station_operational_inputs,
)
from sapphire_flow.types.enums import QcStatus, WarmUpSource
from sapphire_flow.types.rating_reconciliation import RatingWindowRead
from tests.conftest import make_observation
from tests.unit.services.test_operational_inputs import (
    _MODEL_ID,
    _NWP_SOURCE,
    _make_model,
    _make_nwp_records,
    _make_stores_and_sources,
    _seed_forcing,
)
from tests.unit.services.test_rating_inputs import NOW, samples


def test_native_assembler_joins_daily_frame_and_excludes_current_day() -> None:
    rows = samples()
    sid = rows[0].station_id
    station, basin, obs, weather, state, forcing = _make_stores_and_sources(
        sid, with_obs=False, with_nwp=False
    )
    issue = NOW + timedelta(days=1, hours=6)
    cycle = NOW + timedelta(days=1)
    obs.store_observations(
        [
            replace(
                make_observation(
                    station_id=sid,
                    parameter="discharge",
                    timestamp=NOW - timedelta(days=1),
                    value=99.0,
                ),
                id=uuid4(),
            ),
            replace(
                make_observation(
                    station_id=sid,
                    parameter="discharge",
                    timestamp=NOW,
                    value=777.0,
                    qc_status=QcStatus.QC_FAILED,
                ),
                id=uuid4(),
            ),
        ]
    )
    from sapphire_flow.types.enums import (
        SpatialRepresentation,
        WeatherSourceRole,
        WeatherSourceStatus,
    )
    from sapphire_flow.types.station import StationWeatherSource

    station.store_weather_source(
        StationWeatherSource(
            station_id=sid,
            nwp_source="fixture-reanalysis",
            extraction_type=SpatialRepresentation.POINT,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.REANALYSIS,
        )
    )
    _seed_forcing(forcing, sid, NOW - timedelta(days=1), 2)
    weather.store_weather_forecasts(_make_nwp_records(sid, cycle, issue, 48))
    model = _make_model()
    model.data_requirements = replace(
        model.data_requirements,
        lookback_steps=2,
        supported_time_steps=frozenset({timedelta(days=1)}),
    )
    windows = []

    class Reader:
        def read_window(
            self,
            station_id: object,
            model_id: object,
            *,
            start: datetime,
            end: datetime,
            at: datetime,
        ) -> RatingWindowRead:
            windows.append((start, end))
            selected = tuple(row for row in rows if start <= row.timestamp < end)
            return RatingWindowRead(
                start=start,
                end=end,
                records=selected,
                held=(),
                source_count=len(selected),
            )

    result = assemble_station_operational_inputs(
        sid,
        model,
        _MODEL_ID,
        issue,
        cycle,
        _NWP_SOURCE,
        forcing,
        weather,
        obs,
        station,
        basin,
        state,
        lambda: issue,
        1,
        timedelta(days=1),
        rating_inputs=Reader(),
    )
    assert result is not None
    inputs, metadata = result
    assert windows == [(NOW - timedelta(days=1), NOW + timedelta(days=1))]
    assert inputs.data.past_targets["discharge"].to_list() == [99.0, 20.0]
    assert metadata.warm_up_source is WarmUpSource.COLD_START
    assert metadata.warm_up_state_age_hours is None
    assert metadata.input_lineage is not None
    lineage = metadata.input_lineage.rating_input
    assert lineage is not None
    assert lineage.days[0].measured_rejected == 1
    assert lineage.days[0].rejected == 1
    assert lineage.weather_record_ids
    assert lineage.forcing_versions


if TYPE_CHECKING:
    from sapphire_flow.types.rating_derived import RatingDerivedDischarge


@pytest.mark.parametrize("support", ["complete", "missing"])
def test_group_30_day_inputs_need_complete_real_tail_points(support: str) -> None:
    import json

    from sapphire_flow.config.dhm import DhmBinding, DhmConfig
    from sapphire_flow.config.provisional_rating import NativeRatingBinding
    from sapphire_flow.services.rating_inputs import NativeRatingInputs
    from sapphire_flow.services.run_group_forecast import (
        assemble_group_operational_inputs,
    )
    from sapphire_flow.store.rating_reference_store import parse_reference_evidence
    from sapphire_flow.types.enums import (
        ProvisionalIfsTailMode,
        SpatialRepresentation,
        WeatherSourceRole,
        WeatherSourceStatus,
    )
    from sapphire_flow.types.ids import StationGroupId
    from sapphire_flow.types.rating_reference import canonical_content
    from sapphire_flow.types.station import StationGroup, StationWeatherSource
    from sapphire_flow.types.weather import WeatherForecastRecord
    from tests.conftest import make_station_config
    from tests.fakes.fake_models import FakeGroupForecastModel
    from tests.fakes.fake_stores import FakeStationStore

    rows = samples()
    sid = rows[0].station_id
    _, basin, obs, weather, state, forcing = _make_stores_and_sources(
        sid, with_obs=False, with_nwp=False
    )
    station = FakeStationStore()
    station.store_station(make_station_config(station_id=sid))
    for source, role in (
        ("fixture-reanalysis", WeatherSourceRole.REANALYSIS),
        ("ifs_ecmwf", WeatherSourceRole.FORECAST),
    ):
        station.store_weather_source(
            StationWeatherSource(
                station_id=sid,
                nwp_source=source,
                extraction_type=SpatialRepresentation.POINT,
                status=WeatherSourceStatus.ACTIVE,
                role=role,
            )
        )
    issue = NOW + timedelta(days=1)
    start = issue - timedelta(days=30)
    obs.store_observations(
        [
            replace(
                make_observation(
                    station_id=sid,
                    parameter="discharge",
                    timestamp=start + timedelta(days=i),
                    value=99.0,
                ),
                id=uuid4(),
            )
            for i in range(29)
        ]
    )
    _seed_forcing(forcing, sid, start, 25)
    archive_cycle = issue - timedelta(days=5)
    records = [
        WeatherForecastRecord(
            id=uuid4(),
            station_id=sid,
            nwp_source="ifs_ecmwf",
            cycle_time=archive_cycle,
            valid_time=archive_cycle + timedelta(hours=hour),
            parameter=parameter,
            spatial_type=SpatialRepresentation.POINT,
            band_id=None,
            member_id=0,
            value=2.0,
            created_at=archive_cycle,
        )
        for hour in range(0, 144, 3)
        for parameter in ("precipitation", "temperature")
    ]
    if support == "missing":
        records = records[1:]
    weather.store_weather_forecasts(records)
    reference = parse_reference_evidence(
        canonical_content(json.loads(rows[0].content)["reference"])
    )
    binding = NativeRatingBinding(
        tenant_id=reference.tenant_id,
        station_id=sid,
        station_code="450",
        model_ids=frozenset({_MODEL_ID}),
        reference=reference,
        custody_start=start,
        custody_end=issue,
        custody_reference="fixture",
        reconciliation_days=30,
    )
    dhm = DhmConfig(
        endpoint=reference.endpoint,
        bindings=(
            DhmBinding(
                network="dhm",
                station_code="450",
                api_station_id=reference.api_station_id,
                level_reference="gauge_zero",
            ),
        ),
        max_requests_per_batch=1,
        max_bytes_per_batch=1000,
        max_batch_seconds=30.0,
    )

    class Reader:
        def read_current(
            self,
            binding: object,
            dhm: object,
            *,
            start: datetime,
            end: datetime,
            at: datetime,
        ) -> tuple[RatingDerivedDischarge, ...]:
            return tuple(row for row in rows if start <= row.timestamp < end)

    native = NativeRatingInputs(reader=Reader(), bindings=(binding,), dhm=dhm)
    model = FakeGroupForecastModel()
    model.data_requirements = replace(
        model.data_requirements,
        lookback_steps=30,
        forecast_horizon_steps=1,
        supported_time_steps=frozenset({timedelta(days=1)}),
        static_features=frozenset(),
        past_dynamic_features=frozenset({"precipitation", "temperature"}),
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
    )
    group = StationGroup(
        id=StationGroupId(uuid4()),
        name="IFS fixture",
        station_ids=frozenset({sid}),
        created_at=issue,
    )
    result = assemble_group_operational_inputs(
        group=group,
        model=model,
        model_id=_MODEL_ID,
        issue_time=issue,
        cycle_time=archive_cycle,
        nwp_source_by_station={sid: "ifs_ecmwf"},
        forcing_source=forcing,
        weather_forecast_store=weather,
        obs_store=obs,
        station_store=station,
        basin_store=basin,
        model_state_store=state,
        clock=lambda: issue,
        forecast_horizon_steps=1,
        time_step=timedelta(days=1),
        rating_inputs=native,
        provisional_ifs_tail_mode_by_station={sid: ProvisionalIfsTailMode.CONTROL_3H},
    )
    assert result is not None
    inputs, metadata = result
    assert inputs.past_dynamic.height == 30
    assert inputs.past_dynamic["precipitation"].null_count() == (
        1 if support == "missing" else 0
    )
    assert inputs.past_dynamic["temperature"].null_count() == 0
    lineage = metadata[sid].input_lineage
    assert lineage is not None and lineage.rating_input is not None
    ids = lineage.rating_input.weather_record_ids
    assert len(ids) == len(set(ids)) == len(records)


# These fixtures exercise the native assembler and the real FI admission boundary.
def _daily_fi_model(max_nan: int, q_lookback: int = 30) -> tuple:
    from sapphire_flow.adapters import forecast_interface as fi
    from tests.unit.adapters.test_forecast_interface_adapter_nan_gate import (
        RecordingFIForecastModel,
    )

    step = timedelta(days=1)
    requirement = fi.InputRequirement(
        targets={
            "discharge": fi.TargetSpec(
                unit=fi.Unit.M3_PER_S,
                representations=frozenset({fi.OutputRepresentation.DETERMINISTIC}),
            )
        },
        dynamic={
            step: fi.SpatialInputSpec(
                data={
                    fi.FISpatialRepresentation.POINT: fi.DynamicInputSpec(
                        past_known={
                            "obs": {
                                "discharge": fi.PastKnownVariable(
                                    lookback=q_lookback,
                                    max_nan=max_nan,
                                    unit=fi.Unit.M3_PER_S,
                                )
                            },
                            "reanalysis": {
                                name: fi.PastKnownVariable(
                                    lookback=30,
                                    max_nan=0,
                                    unit=unit,
                                )
                                for name, unit in (
                                    ("precipitation", fi.Unit.MM),
                                    ("temperature", fi.Unit.DEG_C),
                                )
                            },
                        },
                        future_known={
                            "nwp": {
                                name: fi.FutureKnownVariable(
                                    future_steps=1,
                                    max_nan=0,
                                    unit=unit,
                                )
                                for name, unit in (
                                    ("precipitation", fi.Unit.MM),
                                    ("temperature", fi.Unit.DEG_C),
                                )
                            }
                        },
                    ),
                }
            )
        },
    )
    # Replace with a successful, issue-matching result in the invocation helper.
    recording = RecordingFIForecastModel(
        fi.ModelFailure(
            model_name="fixture",
            issue_datetime=NOW,
            cause=fi.FailureCause.INPUT_DATA,
            message="fixture",
        ),
        artifact_scope=fi.FIArtifactScope.GROUP,
        requirement=requirement,
    )
    return fi.ForecastInterfaceAdapter(
        recording, station_code_resolver=lambda sid: str(sid)
    ), recording


def _assemble_daily_discharge(
    omitted: set[int],
    *,
    max_nan: int = 0,
    q_lookback: int = 30,
    issue_hour: int = 0,
    missing_values: dict[int, float | None] | None = None,
    gated: bool = True,
    rating_samples: bool = True,
) -> tuple:
    import json

    from sapphire_flow.config.dhm import DhmBinding, DhmConfig
    from sapphire_flow.config.provisional_rating import NativeRatingBinding
    from sapphire_flow.services.rating_inputs import NativeRatingInputs
    from sapphire_flow.services.run_group_forecast import (
        assemble_group_operational_inputs_outcome,
    )
    from sapphire_flow.store.rating_reference_store import parse_reference_evidence
    from sapphire_flow.types.enums import (
        SpatialRepresentation,
        WeatherSourceRole,
        WeatherSourceStatus,
    )
    from sapphire_flow.types.ids import StationGroupId
    from sapphire_flow.types.rating_reference import canonical_content
    from sapphire_flow.types.station import StationGroup, StationWeatherSource
    from tests.fakes.fake_models import FakeGroupForecastModel

    rows = samples()
    sid = rows[0].station_id
    issue = NOW + timedelta(days=2, hours=issue_hour)
    start = NOW - timedelta(days=28)
    station, basin, obs, weather, state, forcing = _make_stores_and_sources(
        sid, with_obs=False, with_nwp=False
    )
    station.store_weather_source(
        StationWeatherSource(
            station_id=sid,
            nwp_source="fixture-reanalysis",
            extraction_type=SpatialRepresentation.POINT,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.REANALYSIS,
        )
    )
    obs.store_observations(
        [
            replace(
                make_observation(
                    station_id=sid,
                    parameter="discharge",
                    timestamp=start + timedelta(days=i),
                    value=100.0 + i,
                ),
                value=(missing_values or {}).get(i, 100.0 + i),
                id=uuid4(),
            )
            for i in range(30)
            if i not in omitted and i != 28
        ]
    )
    _seed_forcing(forcing, sid, start, 30)
    cycle = NOW + timedelta(days=2)
    weather.store_weather_forecasts(_make_nwp_records(sid, cycle, issue, 48))
    reference = parse_reference_evidence(
        canonical_content(json.loads(rows[0].content)["reference"])
    )
    binding = NativeRatingBinding(
        tenant_id=reference.tenant_id,
        station_id=sid,
        station_code="450",
        model_ids=frozenset({_MODEL_ID}),
        reference=reference,
        custody_start=start,
        custody_end=issue,
        custody_reference="fixture",
        reconciliation_days=30,
    )
    dhm = DhmConfig(
        endpoint=reference.endpoint,
        bindings=(
            DhmBinding(
                network="dhm",
                station_code="450",
                api_station_id=reference.api_station_id,
                level_reference="gauge_zero",
            ),
        ),
        max_requests_per_batch=1,
        max_bytes_per_batch=1000,
        max_batch_seconds=30.0,
    )

    class Reader:
        def read_current(
            self,
            binding: object,
            dhm: object,
            *,
            start: datetime,
            end: datetime,
            at: datetime,
        ) -> tuple:
            return tuple(
                row for row in rows if rating_samples and start <= row.timestamp < end
            )

    native = NativeRatingInputs(reader=Reader(), bindings=(binding,), dhm=dhm)
    adapter, recording = _daily_fi_model(max_nan, q_lookback)
    model = adapter
    if not gated:
        model = FakeGroupForecastModel()
        model.data_requirements = adapter.data_requirements
    group = StationGroup(
        id=StationGroupId(uuid4()),
        name="daily coverage",
        station_ids=frozenset({sid}),
        created_at=issue,
    )
    assembly = assemble_group_operational_inputs_outcome(
        group=group,
        model=model,
        model_id=_MODEL_ID,
        issue_time=issue,
        cycle_time=cycle,
        nwp_source_by_station={sid: _NWP_SOURCE},
        forcing_source=forcing,
        weather_forecast_store=weather,
        obs_store=obs,
        station_store=station,
        basin_store=basin,
        model_state_store=state,
        clock=lambda: issue,
        forecast_horizon_steps=1,
        time_step=timedelta(days=1),
        rating_inputs=native,
    )
    return assembly, adapter, recording, group


@pytest.mark.parametrize("issue_hour", [0, 6, 12])
@pytest.mark.parametrize("absent_last", [False, True])
def test_daily_interior_omissions_reach_strict_fi_gate(
    issue_hour: int,
    absent_last: bool,
) -> None:
    from sapphire_flow.types.group_input import GroupMemberInputReason

    omitted = set(range(10, 16)) | ({29} if absent_last else set())
    assembly, adapter, _, group = _assemble_daily_discharge(
        omitted,
        issue_hour=issue_hour,
    )
    assert assembly.inputs is not None
    frame = assembly.inputs.past_targets
    assert frame.height == (29 if absent_last else 30)
    assert frame["discharge"].is_nan().sum() == 6
    assert frame["discharge"].null_count() == 0
    finite = frame.filter(frame["discharge"].is_finite())
    assert finite["discharge"].to_list() == [
        20.0 if i == 28 else 100.0 + i for i in range(30) if i not in omitted
    ]
    issues = adapter.assess_group_inputs(assembly.inputs)
    assert [i.reason for i in issues] == [GroupMemberInputReason.NAN_TOLERANCE_EXCEEDED]
    lineage = assembly.metadata_by_station[next(iter(group.station_ids))].input_lineage
    assert lineage is not None and lineage.rating_input is not None
    assert len(lineage.rating_input.days) == 1
    assert lineage.rating_input.days[0].accepted == 2


@pytest.mark.parametrize("omitted", [{0}, {29}, {0, 1, 29}, set()])
def test_contiguous_edges_are_not_padded_and_remain_admitted(omitted: set[int]) -> None:
    assembly, adapter, _, _ = _assemble_daily_discharge(omitted)
    assert assembly.inputs is not None
    assert assembly.inputs.past_targets.height == 30 - len(omitted)
    assert assembly.inputs.past_targets["discharge"].is_nan().sum() == 0
    assert adapter.assess_group_inputs(assembly.inputs) == ()


def test_ungated_provisional_interior_gap_retains_cadence_skip() -> None:
    from sapphire_flow.types.group_input import GroupMemberInputReason

    assembly, _, _, _ = _assemble_daily_discharge({10}, gated=False)
    assert assembly.inputs is None
    assert (
        assembly.unavailable_members[0].reason
        is GroupMemberInputReason.CADENCE_MISMATCH
    )


def test_q7_p30_delivered_column_tolerance_remains_whole_column() -> None:
    assembly, adapter, _, _ = _assemble_daily_discharge({1}, q_lookback=7)
    assert assembly.inputs is not None
    assert assembly.inputs.past_targets.height == 30
    assert adapter.data_requirements.lookback_steps == 30
    assert "discharge" not in dict(adapter.data_requirements.declared_lookbacks)
    assert len(adapter.assess_group_inputs(assembly.inputs)) == 1


def _run_daily_assembly(
    assembly: object, adapter: object, recording: object, group: object
) -> object:
    import random

    import polars as pl

    from sapphire_flow.adapters import forecast_interface as fi
    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.services.run_group_forecast import (
        GroupStatePolicy,
        run_group_forecast,
    )
    from sapphire_flow.types.domain import ForecastQcRuleSet
    from sapphire_flow.types.enums import (
        ModelArtifactStatus,
        ModelAssignmentStatus,
        NwpCycleSource,
    )
    from sapphire_flow.types.station import GroupModelAssignment
    from tests.fakes.fake_stores import FakeModelArtifactStore
    from tests.unit.services.test_run_group_forecast import _QcChecker

    inputs = assembly.inputs
    assert inputs is not None
    issue = inputs.issue_time
    excluded = {member.station_id for member in adapter.assess_group_inputs(inputs)}
    output_ids = set(inputs.station_ids) - excluded or set(inputs.station_ids)
    recording.result = fi.ModelSuccess(
        output=fi.ModelOutput(
            model_name="fixture",
            issue_datetime=issue,
            variables={
                str(sid): {
                    "discharge": fi.VariableOutput(
                        metadata=fi.VariableMetadata(
                            unit=fi.Unit.M3_PER_S,
                            timedelta=timedelta(days=1),
                            forecast_horizon=1,
                            offset=0,
                        ),
                        deterministic=fi.DeterministicData(
                            data=pl.DataFrame(
                                {
                                    "issue_datetime": [issue],
                                    "datetime": [issue],
                                    "value": [30.0],
                                }
                            )
                        ),
                        flags=frozenset(),
                        status=fi.VariableStatus.SUCCESS,
                    )
                }
                for sid in inputs.station_ids
                if sid in output_ids
            },
        )
    )
    artifacts = FakeModelArtifactStore()
    artifacts.store_artifact(
        model_id=_MODEL_ID,
        artifact_bytes=b"fixture",
        group_id=group.id,
        training_period_start=issue - timedelta(days=60),
        training_period_end=issue - timedelta(days=31),
        trained_at=issue,
        status=ModelArtifactStatus.ACTIVE,
    )
    return run_group_forecast(
        group=group,
        group_inputs=inputs,
        metadata_by_station=assembly.metadata_by_station,
        assignment=GroupModelAssignment(
            group_id=group.id,
            model_id=_MODEL_ID,
            time_step=timedelta(days=1),
            status=ModelAssignmentStatus.ACTIVE,
            priority=1,
            created_at=issue,
        ),
        model=adapter,
        artifact_store=artifacts,
        qc_checker=_QcChecker(),
        qc_rules=ForecastQcRuleSet(version="1.0", rules=()),
        qc_overrides=[],
        baselines_by_station={sid: [] for sid in inputs.station_ids},
        nwp_cycle_reference_time=issue,
        nwp_cycle_source=NwpCycleSource.PRIMARY,
        config=DeploymentConfig(max_retention_days=1000),
        clock=lambda: issue,
        id_gen=uuid4,
        rng=random.Random(42),
        state_policy=GroupStatePolicy.STATELESS_ONLY,
    )


@pytest.mark.parametrize(
    "omitted,max_nan,unavailable,level",
    [
        (set(range(10, 16)), 6, 6, "partial"),
        (set(range(10, 16)) | {29}, 6, 7, "degraded"),
        ({0}, 0, 1, "partial"),
        ({29}, 0, 1, "degraded"),
    ],
)
def test_normal_group_tolerant_delivery_and_observation_coverage(
    omitted: set[int],
    max_nan: int,
    unavailable: int,
    level: str,
) -> None:
    from sapphire_flow.types.enums import InputQualityCategory

    assembly, adapter, recording, group = _assemble_daily_discharge(
        omitted, max_nan=max_nan
    )
    outcome = _run_daily_assembly(assembly, adapter, recording, group)
    assert outcome.failure is None
    assert len(outcome.results) == 1
    forecast = next(iter(outcome.results.values())).forecasts[0]
    flags = [
        flag
        for flag in forecast.input_quality_flags
        if flag.category is InputQualityCategory.OBSERVATION
        and "assembly slots" in flag.detail
    ]
    assert len(flags) == 1
    assert flags[0].level.value == level
    assert f"{unavailable} of 30 assembly slots" in flags[0].detail
    assert recording.predict_inputs is not None
    from sapphire_flow.services.forecast_evidence import restore_frame, restore_snapshot

    assert forecast.evidence is not None
    import json

    snapshot = restore_snapshot(forecast.evidence.snapshot)
    json.dumps(snapshot, allow_nan=False)
    restored = restore_frame(snapshot["frames"]["past_targets"])
    assert restored.equals(assembly.inputs.past_targets)
    from sapphire_flow.adapters import forecast_interface as fi

    delivered = (
        next(iter(recording.predict_inputs.stations.values()))
        .dynamic[timedelta(days=1)]
        .data[fi.FISpatialRepresentation.POINT]
        .past_known["obs"]["discharge"]
        .data
    )
    assert delivered.height == assembly.inputs.past_targets.height
    assert delivered["discharge"].is_nan().sum() == max_nan
    assert (
        delivered["datetime"].to_list()
        == assembly.inputs.past_targets["timestamp"].to_list()
    )


def test_normal_all_excluded_does_not_call_model_and_reports_missing_counts() -> None:
    from structlog.testing import capture_logs

    from sapphire_flow.types.group_input import GroupMemberInputReason

    assembly, adapter, recording, group = _assemble_daily_discharge(
        set(range(10, 16)) | {29}
    )
    with capture_logs() as logs:
        outcome = _run_daily_assembly(assembly, adapter, recording, group)
    assert outcome.results == {}
    assert outcome.failure is None
    assert recording.predict_inputs is None
    assert (
        outcome.unavailable_members[0].reason
        is GroupMemberInputReason.NAN_TOLERANCE_EXCEEDED
    )
    coverage = next(
        log
        for log in logs
        if log["event"] == "run_group_forecast.provisional_discharge_excluded"
    )["coverage"]
    assert "7 of 30 assembly slots" in coverage[0]
    assert (
        "6 missing assembly cells (including normalized interior omissions)"
        in coverage[0]
    )
    assert "1 undelivered edge slots" in coverage[0]


@pytest.mark.parametrize(
    "missing_values",
    [{0: float("nan")}, {29: float("nan")}, {10: float("nan"), 11: float("nan")}],
)
def test_supplied_missing_cells_stay_in_delivered_extent(missing_values: dict) -> None:
    assembly, adapter, _, _ = _assemble_daily_discharge(
        set(), missing_values=missing_values
    )
    assert assembly.inputs is not None
    assert assembly.inputs.past_targets.height == 30
    assert assembly.inputs.past_targets["discharge"].is_nan().sum() == len(
        missing_values
    )
    assert len(adapter.assess_group_inputs(assembly.inputs)) == 1


@pytest.mark.parametrize(
    "fault", ["duplicate", "off_grid", "out_of_bounds", "naive", "nanosecond"]
)
def test_malformed_merged_calendar_uses_cadence_skip(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    import polars as pl

    from sapphire_flow.services import rating_inputs
    from sapphire_flow.types.group_input import GroupMemberInputReason

    original = rating_inputs.merge_rating_days

    def malformed(*args: object, **kwargs: object) -> tuple:
        frame, lineage, latest = original(*args, **kwargs)
        if fault == "duplicate":
            frame = pl.concat([frame, frame.head(1)])
        elif fault == "off_grid":
            frame = frame.with_columns(pl.col("timestamp") + timedelta(hours=1))
        elif fault == "out_of_bounds":
            frame = frame.with_columns(pl.col("timestamp") - timedelta(days=1))
        elif fault == "nanosecond":
            frame = frame.with_columns(
                pl.col("timestamp").cast(pl.Datetime("ns", "UTC"))
                + pl.duration(nanoseconds=1)
            )
        else:
            frame = frame.with_columns(pl.col("timestamp").dt.replace_time_zone(None))
        return frame, lineage, latest

    monkeypatch.setattr(rating_inputs, "merge_rating_days", malformed)
    assembly, _, _, _ = _assemble_daily_discharge(set())
    assert assembly.inputs is None
    assert (
        assembly.unavailable_members[0].reason
        is GroupMemberInputReason.CADENCE_MISMATCH
    )


@pytest.mark.parametrize("channel", ["past", "future"])
@pytest.mark.parametrize("parameter", ["precipitation", "temperature"])
def test_discharge_tolerance_does_not_relax_weather(
    channel: str, parameter: str
) -> None:
    import polars as pl

    assembly, adapter, recording, group = _assemble_daily_discharge({10}, max_nan=1)
    inputs = assembly.inputs
    assert inputs is not None
    name = "past_dynamic" if channel == "past" else "future_dynamic"
    frame = getattr(inputs, name).with_columns(pl.lit(float("nan")).alias(parameter))
    assembly = replace(assembly, inputs=replace(inputs, **{name: frame}))
    outcome = _run_daily_assembly(assembly, adapter, recording, group)
    assert outcome.results == {}
    assert outcome.failure is None
    assert recording.predict_inputs is None


@pytest.mark.parametrize("diagnostic_failure", [False, True])
def test_mixed_station_extents_isolate_strict_nan_exclusion(
    monkeypatch: pytest.MonkeyPatch,
    diagnostic_failure: bool,
) -> None:
    import polars as pl

    cases = [_assemble_daily_discharge(omitted) for omitted in (set(), {10}, {29})]
    assemblies = [case[0] for case in cases]
    station_ids = tuple(
        sid for assembly in assemblies for sid in assembly.inputs.station_ids
    )
    first, adapter, recording, group = cases[0]
    group = replace(group, station_ids=frozenset(station_ids))
    inputs = replace(
        first.inputs,
        station_ids=station_ids,
        **{
            name: pl.concat([getattr(assembly.inputs, name) for assembly in assemblies])
            for name in ("past_targets", "past_dynamic", "future_dynamic")
        },
    )
    assembly = replace(
        first,
        inputs=inputs,
        expected_station_ids=station_ids,
        metadata_by_station={
            sid: metadata
            for part in assemblies
            for sid, metadata in part.metadata_by_station.items()
        },
    )
    if diagnostic_failure:
        from sapphire_flow.services import run_group_forecast as service

        original_flags = service._provisional_discharge_flags

        def failed_exclusion_diagnostic(**kwargs: object) -> list:
            if kwargs["station_id"] == station_ids[1]:
                raise ValueError("diagnostic fixture failure")
            return original_flags(**kwargs)

        monkeypatch.setattr(
            service, "_provisional_discharge_flags", failed_exclusion_diagnostic
        )
    outcome = _run_daily_assembly(assembly, adapter, recording, group)
    assert outcome.failure is None
    assert set(outcome.results) == {station_ids[0], station_ids[2]}
    healthy = outcome.results[station_ids[0]].forecasts[0]
    edge = outcome.results[station_ids[2]].forecasts[0]
    assert not any(
        "assembly slots" in flag.detail for flag in healthy.input_quality_flags
    )
    assert any(
        "1 undelivered edge slots" in flag.detail for flag in edge.input_quality_flags
    )
    assert [i.station_id for i in outcome.unavailable_members] == [station_ids[1]]
    assert set(recording.predict_inputs.stations) == {
        str(station_ids[0]),
        str(station_ids[2]),
    }


def test_exact_nanosecond_midnights_preserve_finite_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import polars as pl

    from sapphire_flow.services import rating_inputs

    original = rating_inputs.merge_rating_days

    def nanosecond_midnights(*args: object, **kwargs: object) -> tuple:
        frame, lineage, latest = original(*args, **kwargs)
        return frame.cast({"timestamp": pl.Datetime("ns", "UTC")}), lineage, latest

    monkeypatch.setattr(rating_inputs, "merge_rating_days", nanosecond_midnights)
    assembly, _, _, _ = _assemble_daily_discharge({10})
    frame = assembly.inputs.past_targets
    assert frame["timestamp"].dtype == pl.Datetime("ns", "UTC")
    assert frame["discharge"].is_nan().sum() == 1
    assert frame.filter(pl.col("discharge").is_finite())["discharge"].to_list() == [
        20.0 if i == 28 else 100.0 + i for i in range(30) if i != 10
    ]


@pytest.mark.parametrize("max_nan", [0, 29, 30])
def test_absent_discharge_reaches_fi_gate_without_rating_claim(max_nan: int) -> None:
    from sapphire_flow.types.enums import ForecastDataUse, InputQualityCategory

    assembly, adapter, recording, group = _assemble_daily_discharge(
        set(range(30)), max_nan=max_nan, rating_samples=False
    )
    assert assembly.inputs is not None
    assert assembly.inputs.past_targets["discharge"].is_nan().sum() == 30
    assert all(m.input_lineage is None for m in assembly.metadata_by_station.values())
    outcome = _run_daily_assembly(assembly, adapter, recording, group)
    if max_nan < 30:
        assert outcome.results == {}
        assert recording.predict_inputs is None
    else:
        forecast = next(iter(outcome.results.values())).forecasts[0]
        assert forecast.data_use is ForecastDataUse.STANDARD
        assert forecast.input_lineage is None
        assert any(
            flag.category is InputQualityCategory.OBSERVATION
            and flag.level.value == "degraded"
            and "30 of 30" in flag.detail
            for flag in forecast.input_quality_flags
        )
        assert recording.predict_inputs is not None


@pytest.mark.parametrize("omitted", [set(), set(range(30))])
def test_no_rating_days_still_skip_ungated_models(omitted: set[int]) -> None:
    assembly, _, recording, _ = _assemble_daily_discharge(
        omitted, rating_samples=False, gated=False
    )
    assert assembly.inputs is None
    assert recording.predict_inputs is None


def test_measured_discharge_without_rating_additions_still_forecasts() -> None:
    from sapphire_flow.types.enums import ForecastDataUse

    assembly, adapter, recording, group = _assemble_daily_discharge(
        {29}, max_nan=0, rating_samples=False
    )
    assert assembly.inputs is not None
    assert assembly.inputs.past_targets["discharge"].to_list() == [
        100.0 + i for i in range(28)
    ]
    outcome = _run_daily_assembly(assembly, adapter, recording, group)
    forecast = next(iter(outcome.results.values())).forecasts[0]
    assert forecast.data_use is ForecastDataUse.STANDARD
    assert forecast.input_lineage is None


def test_success_coverage_fault_does_not_change_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sapphire_flow.services import run_group_forecast as service

    assembly, adapter, recording, group = _assemble_daily_discharge({10}, max_nan=1)

    def failed_coverage(**kwargs: object) -> list:
        raise ValueError("coverage fixture fault")

    monkeypatch.setattr(service, "_provisional_discharge_flags", failed_coverage)
    outcome = _run_daily_assembly(assembly, adapter, recording, group)
    assert outcome.failure is None
    assert set(outcome.results) == group.station_ids
    assert recording.predict_inputs is not None
