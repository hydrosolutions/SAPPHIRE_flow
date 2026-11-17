import random
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.config.operational_weather import (
    OperationalPastWeatherRoute,
    OperationalWeatherConfig,
)
from sapphire_flow.config.weather_gap import WeatherGapConfig
from sapphire_flow.exceptions import ConfigurationError, WeatherEvidenceCaptureError
from sapphire_flow.services.forecast_evidence import capture_station_evidence
from sapphire_flow.services.input_quality import selected_weather_quality_flags
from sapphire_flow.services.operational_weather_projection import (
    future_receipt_scope,
    project_past_weather,
)
from sapphire_flow.services.operational_weather_proof import prove_future_receipt
from sapphire_flow.services.selected_weather_evidence import (
    SelectedWeatherIssuanceRefusal,
    restore_selected_weather,
    selected_weather_issuance_refusal,
)
from sapphire_flow.services.selected_weather_inputs import (
    AssignmentWeatherInputs,
    assemble_selected_weather,
)
from sapphire_flow.store.operational_weather_cache import complete_weather_entry
from sapphire_flow.store.operational_weather_codec import (
    decode_cache_entry,
    encode_cache_entry,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import ForecastQcRuleSet
from sapphire_flow.types.enums import EnsembleMode
from sapphire_flow.types.forecast_evidence import (
    ForecastEvidence,
    StationSourceEvidence,
)
from sapphire_flow.types.ids import ModelId
from sapphire_flow.types.operational_weather import (
    WeatherAttemptState,
    WeatherFailureReason,
    WeatherFreshness,
    WeatherLegOutcome,
    WeatherLegState,
)
from sapphire_flow.types.operational_weather_v2 import (
    FutureWeatherAttemptV2,
    PastWeatherRequirementV2,
    PastWeatherUseV2,
    WeatherCacheCompletionV2,
    WeatherCacheEntryV2,
    WeatherUseV2,
)
from sapphire_flow.types.weather_gap import WeatherGapMode
from tests.fakes.fake_models import FakeStationForecastModel
from tests.fakes.fake_stores import FakeWeatherForecastStore
from tests.fakes.weather_periods import (
    FIRST,
    GRID,
    ISSUE,
    SID,
    period_attempt,
    period_request,
    period_snapshot,
)
from tests.unit.adapters.test_forecast_interface_adapter_predict import (
    _station_model_inputs,
)
from tests.unit.services.test_recap_daily_weather import qualified_rows

LIMIT = 2 * 1024 * 1024
MID = ModelId("fixture")


def entry_and_store(
    *, days: int = 1, missing_offsets: tuple[int, ...] = ()
) -> tuple[WeatherCacheEntryV2, FakeWeatherForecastStore]:
    attempt = period_attempt()
    request = period_request()
    cycle = ensure_utc(ISSUE - timedelta(days=1))
    rows = [
        replace(row, station_id=SID, created_at=ISSUE) for row in qualified_rows(cycle)
    ]
    store = FakeWeatherForecastStore()
    store.store_weather_forecasts(rows)
    scope = future_receipt_scope(request, cycle_time=cycle)
    assert scope is not None
    receipt = prove_future_receipt(
        scope=scope, cycle_time=cycle, validated_at=ISSUE, fetched=rows, readback=rows
    )
    pending = WeatherLegOutcome(state=WeatherLegState.PENDING)
    entry = WeatherCacheEntryV2(
        attempt=attempt,
        state=WeatherAttemptState.PENDING,
        past_outcome=pending,
        future_attempts=(FutureWeatherAttemptV2(scope=request, outcome=pending),),
    )
    success = WeatherLegOutcome(state=WeatherLegState.SUCCESS)
    entry = complete_weather_entry(
        entry,
        attempt=attempt,
        completion=WeatherCacheCompletionV2(
            finished_at=ISSUE,
            past_outcome=success,
            past_snapshot=period_snapshot(days=days, missing_offsets=missing_offsets),
            future_attempts=(
                FutureWeatherAttemptV2(
                    scope=request, outcome=success, resolved_cycle=cycle
                ),
            ),
            future_receipts=(receipt,),
        ),
        earliest_cycle=cycle,
        max_past_bytes=LIMIT,
        max_receipts=32,
    )
    restored = decode_cache_entry(
        encode_cache_entry(entry, max_past_bytes=LIMIT), max_past_bytes=LIMIT
    )
    assert isinstance(restored, WeatherCacheEntryV2)
    return restored, store


def assemble(
    entry: WeatherCacheEntryV2, store: FakeWeatherForecastStore
) -> AssignmentWeatherInputs:
    assert entry.past_snapshot is not None
    return assemble_selected_weather(
        station_id=SID,
        issue_time=ISSUE,
        used_at=ISSUE,
        entry=entry,
        expected_attempt=entry.attempt,
        past_requirement=PastWeatherRequirementV2(
            grid=GRID,
            start=entry.past_snapshot.window.start,
            end=FIRST,
            parameters=("precipitation", "temperature"),
        ),
        future_requirement=period_request(),
        store=store,
        earliest_cycle=ensure_utc(ISSUE - timedelta(hours=60)),
        max_past_bytes=LIMIT,
        ensemble_mode=EnsembleMode.SINGLE,
        model=FakeStationForecastModel(),
        model_id=MID,
        gap_config=WeatherGapConfig(mode=WeatherGapMode.ALWAYS),
    )


def capture(result: AssignmentWeatherInputs) -> ForecastEvidence:
    template = _station_model_inputs()
    inputs = replace(
        template,
        station_id=SID,
        issue_time=ISSUE,
        data=replace(
            template.data,
            past_dynamic=result.past_dynamic,
            future_dynamic=result.future_dynamic,
        ),
        source_evidence=StationSourceEvidence(
            weather_use=result.use, future_weather=result.future_records
        ),
    )
    return capture_station_evidence(
        inputs=inputs,
        model=FakeStationForecastModel(),
        model_id=MID,
        artifact_bytes=b"artifact",
        prior_state=None,
        rng_state=random.Random(1).getstate(),
        config=DeploymentConfig(
            max_retention_days=1000,
            operational_weather=OperationalWeatherConfig(
                route=OperationalPastWeatherRoute.RECAP_COMBINED
            ),
        ),
        qc_rules=ForecastQcRuleSet(version="test", rules=()),
        qc_overrides=[],
        baselines=[],
        water_level_datum_masl=None,
    )


class TestPeriodWeatherEvidence:
    def test_cross_midnight_inputs_capture_restore_and_actual_age(self) -> None:
        entry, store = entry_and_store()
        result = assemble(entry, store)
        assert result.past_dynamic["timestamp"].to_list() == [FIRST - GRID.step]
        assert result.past_dynamic["precipitation"].to_list() == [24.0]
        assert result.future_dynamic["timestamp"].to_list() == [
            FIRST + GRID.step * i for i in range(3)
        ]
        assert result.future_dynamic["precipitation"].to_list() == [8.0, 8.0, None]
        assert result.future_dynamic["temperature"].to_list() == [10.0] * 3
        evidence = capture(result)
        assert evidence.weather_evidence_version == 2
        assert (
            restore_selected_weather(evidence, model_id=MID, issue_time=ISSUE)[SID]
            == result.use
        )
        source = StationSourceEvidence(weather_use=result.use)
        assert (
            selected_weather_issuance_refusal(
                source,
                issue_time=ISSUE,
                at=ensure_utc(ISSUE + timedelta(hours=36)),
                max_age_hours=60,
            )
            is None
        )
        assert (
            selected_weather_issuance_refusal(
                source,
                issue_time=ISSUE,
                at=ensure_utc(ISSUE + timedelta(hours=36, microseconds=1)),
                max_age_hours=60,
            )
            is SelectedWeatherIssuanceRefusal.WEATHER_EXPIRED
        )
        with pytest.raises(WeatherEvidenceCaptureError, match="unavailable"):
            restore_selected_weather(
                replace(evidence, weather_evidence_version=1),
                model_id=MID,
                issue_time=ISSUE,
            )

    @pytest.mark.parametrize("missing, degraded", [(2, False), (3, True)])
    def test_filled_gap_recency_uses_past_end_and_explicit_intervals(
        self, missing: int, degraded: bool
    ) -> None:
        entry, store = entry_and_store(days=7, missing_offsets=(missing,))
        result = assemble(entry, store)
        assert result.past_dynamic["precipitation"].to_list() == [24.0] * 7
        assert isinstance(result.use.past, PastWeatherUseV2)
        assert len(result.use.past.gap_fills) == 2
        flags = selected_weather_quality_flags(
            StationSourceEvidence(weather_use=result.use),
            issue_time=ensure_utc(ISSUE + timedelta(days=30)),
        )
        assert bool(flags) == degraded
        if degraded:
            fill = result.use.past.gap_fills[0]
            assert (
                f"[{fill.start.isoformat()}, {fill.end.isoformat()})" in flags[0].detail
            )
        assert (
            restore_selected_weather(capture(result), model_id=MID, issue_time=ISSUE)[
                SID
            ]
            == result.use
        )

    def test_long_gap_remains_unavailable(self) -> None:
        entry, store = entry_and_store(days=7, missing_offsets=(2, 3, 4))
        with pytest.raises(ConfigurationError, match="missing_support"):
            assemble(entry, store)

    def test_stale_snapshot_can_cover_an_exact_smaller_window(self) -> None:
        entry, _store = entry_and_store(days=7)
        snapshot = entry.past_snapshot
        assert snapshot is not None
        entry = replace(
            entry,
            state=WeatherAttemptState.FAILED,
            past_outcome=WeatherLegOutcome(
                state=WeatherLegState.UNAVAILABLE, reason=WeatherFailureReason.TRANSPORT
            ),
        )
        requirement = PastWeatherRequirementV2(
            grid=GRID,
            start=ensure_utc(FIRST - GRID.step * 5),
            end=ensure_utc(FIRST - GRID.step * 2),
            parameters=("precipitation",),
        )
        result = project_past_weather(
            entry,
            expected_attempt=entry.attempt,
            requirement=requirement,
            max_past_bytes=LIMIT,
        )
        assert isinstance(result, PastWeatherUseV2)
        assert result.freshness is WeatherFreshness.STALE
        assert len(result.periods) == 3
        assert result.snapshot_sha256 == snapshot.payload_sha256

    def test_changed_grid_is_not_reinterpreted(self) -> None:
        from sapphire_flow.types.time_grid import TimeGrid

        entry, _store = entry_and_store()
        shifted = TimeGrid(step=GRID.step, phase=timedelta(hours=12))
        requirement = PastWeatherRequirementV2(
            grid=shifted,
            start=ensure_utc(FIRST - GRID.step - timedelta(hours=6)),
            end=ensure_utc(FIRST - timedelta(hours=6)),
            parameters=("precipitation",),
        )
        with pytest.raises(ValueError, match="grid|context"):
            project_past_weather(
                entry,
                expected_attempt=entry.attempt,
                requirement=requirement,
                max_past_bytes=LIMIT,
            )

    def test_retained_donor_receipt_fills_tail_and_changed_archive_is_refused(
        self,
    ) -> None:
        from sapphire_flow.services.operational_weather_proof import WeatherProofError

        entry, store = entry_and_store(days=3, missing_offsets=(2,))
        cycle = ensure_utc(ISSUE - timedelta(days=2))
        rows = [
            replace(row, station_id=SID, created_at=ISSUE)
            for row in qualified_rows(cycle)
        ]
        old_request = replace(
            period_request(first=ensure_utc(FIRST - GRID.step)),
            issue_time=ensure_utc(ISSUE - GRID.step),
            eligibility_cutoff=ensure_utc(ISSUE - GRID.step),
        )
        scope = future_receipt_scope(old_request, cycle_time=cycle)
        assert scope is not None
        receipt = prove_future_receipt(
            scope=scope,
            cycle_time=cycle,
            validated_at=old_request.issue_time,
            fetched=rows,
            readback=rows,
        )
        store.store_weather_forecasts(rows)
        entry = replace(entry, future_receipts=(*entry.future_receipts, receipt))
        result = assemble(entry, store)
        assert result.past_dynamic["precipitation"].to_list() == [24.0, 24.0, 8.0]
        assert len(result.use.past.gap_fills) == 2
        assert (
            restore_selected_weather(capture(result), model_id=MID, issue_time=ISSUE)[
                SID
            ]
            == result.use
        )
        # A failed proof is not hidden by the linear fallback.
        changed = FakeWeatherForecastStore()
        changed.store_weather_forecasts(
            [replace(row, value=row.value + 1) for row in rows]
        )
        for current in entry.future_receipts[:-1]:
            changed.store_weather_forecasts(
                store.fetch_weather_forecasts(
                    station_id=SID,
                    nwp_source="ifs_ecmwf",
                    cycle_time=current.cycle_time,
                    parameters=["precipitation", "temperature"],
                )
            )
        with pytest.raises(WeatherProofError, match="complete receipt"):
            assemble(entry, changed)


class TestGroupedPeriodEvidence:
    @pytest.mark.parametrize("phase, allowed", [(18, True), (12, False)])
    def test_group_requires_one_grid(self, phase: int, allowed: bool) -> None:
        from sapphire_flow.services.forecast_evidence import capture_group_evidence
        from sapphire_flow.types.operational_weather import WeatherUnused
        from sapphire_flow.types.time_grid import TimeGrid
        from tests.unit.adapters.test_forecast_interface_adapter_predict import (
            _group_model_inputs,
        )

        inputs = _group_model_inputs()
        sources = tuple(
            (
                sid,
                StationSourceEvidence(
                    weather_use=WeatherUseV2(
                        grid=GRID
                        if i == 0
                        else TimeGrid(step=GRID.step, phase=timedelta(hours=phase)),
                        eligibility_cutoff=ISSUE,
                        station_id=sid,
                        issue_time=ISSUE,
                        used_at=ISSUE,
                        attempt=None,
                        refresh_finished_at=None,
                        past=WeatherUnused(),
                        future=WeatherUnused(),
                    )
                ),
            )
            for i, sid in enumerate(inputs.station_ids)
        )
        inputs = replace(inputs, issue_time=ISSUE, source_evidence=sources)
        arguments = dict(
            inputs=inputs,
            model=object(),
            model_id=MID,
            artifact_bytes=b"artifact",
            rng_state=random.Random(1).getstate(),
            config=DeploymentConfig(
                max_retention_days=1000,
                operational_weather=OperationalWeatherConfig(
                    route=OperationalPastWeatherRoute.RECAP_COMBINED
                ),
            ),
            qc_rules=ForecastQcRuleSet(version="test", rules=()),
            qc_overrides=[],
            baselines_by_station={},
            water_level_datums_masl={},
        )
        if not allowed:
            with pytest.raises(WeatherEvidenceCaptureError, match="capture failed"):
                capture_group_evidence(**arguments)
        else:
            evidence = capture_group_evidence(**arguments)
            assert evidence.weather_evidence_version == 2
            restored = restore_selected_weather(
                evidence, model_id=MID, issue_time=ISSUE
            )
            assert restored == {sid: source.weather_use for sid, source in sources}

    @pytest.mark.parametrize("second_version", [None, 1, 2])
    def test_combination_requires_uniform_versions(
        self, second_version: int | None
    ) -> None:
        from sapphire_flow.services.forecast_evidence import capture_combined_evidence
        from sapphire_flow.types.ids import ArtifactId
        from tests.integration.store.test_forecast_store import _make_forecast

        entry, store = entry_and_store()
        evidence = capture(assemble(entry, store))
        first = replace(
            _make_forecast(SID, MID, ArtifactId(UUID(int=10)), issued_at=ISSUE),
            evidence=evidence,
            weather_evidence_version=2,
        )
        second = replace(
            first, id=UUID(int=20), weather_evidence_version=second_version
        )
        arguments = dict(
            model_id=ModelId("combined"),
            strategy="equal",
            contributors=(first, second),
            weights=None,
            qc_rules=ForecastQcRuleSet(version="test", rules=()),
            qc_overrides=[],
            baselines=[],
            water_level_datum_masl=None,
        )
        if second_version != 2:
            with pytest.raises(
                ConfigurationError, match="uniform contributor versions"
            ):
                capture_combined_evidence(**arguments)
        else:
            combined = capture_combined_evidence(**arguments)
            assert combined.weather_evidence_version == 2
            restored = restore_selected_weather(
                combined, model_id=ModelId("combined"), issue_time=ISSUE
            )[SID]
            assert len(restored.contributors) == 2
            assert restored.grid == GRID


@pytest.mark.parametrize("newer", ["issue", "cutoff"])
def test_replay_refuses_later_snapshot_context_as_missing_support(newer: str) -> None:
    from sapphire_flow.store.operational_weather_codec import make_past_snapshot
    from sapphire_flow.types.operational_weather import (
        WeatherBlocked,
        WeatherUseBlockReason,
    )

    entry, _store = entry_and_store()
    snapshot = entry.past_snapshot
    assert snapshot is not None
    attempt = entry.attempt
    if newer == "issue":
        snapshot = make_past_snapshot(
            window=replace(snapshot.window, issue_time=ensure_utc(ISSUE + GRID.step)),
            retrieved_at=ISSUE,
            snapshot_id=snapshot.id,
            max_bytes=LIMIT,
        )
    else:
        attempt = replace(
            attempt, eligibility_cutoff=ensure_utc(ISSUE - timedelta(hours=3))
        )
    entry = replace(
        entry,
        attempt=attempt,
        state=WeatherAttemptState.FAILED,
        past_outcome=WeatherLegOutcome(
            state=WeatherLegState.UNAVAILABLE, reason=WeatherFailureReason.TRANSPORT
        ),
        past_snapshot=snapshot,
        future_attempts=(),
        future_receipts=(),
    )
    requirement = PastWeatherRequirementV2(
        grid=GRID, start=snapshot.window.start, end=FIRST, parameters=("precipitation",)
    )
    assert project_past_weather(
        entry, expected_attempt=attempt, requirement=requirement, max_past_bytes=LIMIT
    ) == WeatherBlocked(reason=WeatherUseBlockReason.MISSING_SUPPORT)


def test_group_cycle_guard_checks_v2_before_prediction() -> None:
    from sapphire_flow.services.selected_weather_evidence import (
        require_common_weather_cycle,
    )

    first_entry, store = entry_and_store()
    first = assemble(first_entry, store).use
    assert isinstance(first, WeatherUseV2)
    request = period_request()
    cycle = ensure_utc(ISSUE - timedelta(days=2))
    rows = [replace(row, station_id=SID) for row in qualified_rows(cycle)]
    scope = future_receipt_scope(request, cycle_time=cycle)
    assert scope is not None
    receipt = prove_future_receipt(
        scope=scope, cycle_time=cycle, validated_at=ISSUE, fetched=rows, readback=rows
    )
    second = replace(
        first,
        future=replace(
            first.future,
            receipt=receipt,
            refresh_attempt=replace(first.future.refresh_attempt, resolved_cycle=cycle),
        ),
    )
    with pytest.raises(ConfigurationError, match="cannot mix source cycles"):
        require_common_weather_cycle([first, second])
    assert require_common_weather_cycle([first, first]) == ISSUE - timedelta(days=1)
