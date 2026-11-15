import random
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.config.operational_weather import (
    OperationalPastWeatherRoute,
    OperationalWeatherConfig,
)
from sapphire_flow.config.weather_gap import WeatherGapConfig
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.forecast_evidence import capture_station_evidence
from sapphire_flow.services.input_quality import selected_weather_quality_flags
from sapphire_flow.services.operational_weather_projection import future_receipt_scope
from sapphire_flow.services.operational_weather_proof import (
    WeatherProofError,
    prove_future_receipt,
)
from sapphire_flow.services.selected_weather_evidence import restore_selected_weather
from sapphire_flow.services.selected_weather_inputs import assemble_selected_weather
from sapphire_flow.store.operational_weather_codec import (
    decode_cache_entry,
    encode_cache_entry,
    make_past_snapshot,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import ForecastQcRuleSet
from sapphire_flow.types.enums import EnsembleMode
from sapphire_flow.types.forecast_evidence import StationSourceEvidence
from sapphire_flow.types.ids import ModelId
from sapphire_flow.types.operational_weather import (
    FutureWeatherAttempt,
    FutureWeatherRequest,
    FutureWeatherRequirement,
    PastWeatherRequirement,
    PastWeatherUse,
    WeatherAttemptState,
    WeatherFailureReason,
    WeatherLegOutcome,
    WeatherLegState,
)
from sapphire_flow.types.weather_gap import WeatherGapMode
from tests.fakes.fake_models import FakeStationForecastModel
from tests.fakes.fake_stores import FakeWeatherForecastStore
from tests.fakes.operational_weather import ISSUE, make_operational_window
from tests.unit.adapters.test_forecast_interface_adapter_predict import (
    _station_model_inputs,
)
from tests.unit.services.test_operational_weather_projection import (
    LIMIT,
    successful_entry,
)
from tests.unit.services.test_recap_daily_weather import qualified_rows


class TestSelectedWeatherRecovery:
    @pytest.mark.parametrize(
        "tail_days,mode",
        [
            (1, WeatherGapMode.ALWAYS),
            (2, WeatherGapMode.ALWAYS),
            (1, WeatherGapMode.STRICT_MODELS),
            (1, WeatherGapMode.DISABLED),
        ],
    )
    def test_retained_history_and_verified_forecast_fill_only_short_tail(
        self, tail_days: int, mode: WeatherGapMode
    ) -> None:
        entry = successful_entry()
        sid = entry.attempt.key.station_id
        old_issue = ensure_utc(ISSUE - timedelta(days=tail_days))
        cycle = ensure_utc(old_issue - timedelta(days=1))
        rows = [replace(row, station_id=sid) for row in qualified_rows(cycle)]
        past_window = make_operational_window(sid, issue_time=old_issue)
        snapshot = make_past_snapshot(
            window=past_window,
            retrieved_at=old_issue,
            snapshot_id=uuid4(),
            max_bytes=LIMIT,
        )
        original_request = FutureWeatherRequest(
            station_id=sid,
            binding_fingerprint="c" * 64,
            source="ifs_ecmwf",
            issue_time=cycle,
            requirements=tuple(
                FutureWeatherRequirement(
                    parameter=name,
                    member_ids=(0,),
                    valid_times=tuple(
                        ensure_utc(cycle + timedelta(days=i))
                        for i in range(tail_days + 2)
                    ),
                )
                for name in ("precipitation", "temperature")
            ),
        )
        scope = future_receipt_scope(original_request, cycle_time=cycle)
        assert scope is not None
        receipt = prove_future_receipt(
            scope=scope,
            cycle_time=cycle,
            validated_at=cycle,
            fetched=rows,
            readback=rows,
        )
        current_request = replace(
            original_request,
            issue_time=ISSUE,
            requirements=tuple(
                replace(req, valid_times=(ISSUE,))
                for req in original_request.requirements
            ),
        )
        entry = replace(
            entry,
            state=WeatherAttemptState.FAILED,
            past_outcome=WeatherLegOutcome(
                state=WeatherLegState.UNAVAILABLE, reason=WeatherFailureReason.TRANSPORT
            ),
            past_snapshot=snapshot,
            future_attempts=(
                FutureWeatherAttempt(
                    scope=current_request,
                    outcome=WeatherLegOutcome(
                        state=WeatherLegState.UNAVAILABLE,
                        reason=WeatherFailureReason.TRANSPORT,
                    ),
                ),
            ),
            future_receipts=(receipt,),
        )
        entry = decode_cache_entry(
            encode_cache_entry(entry, max_past_bytes=LIMIT), max_past_bytes=LIMIT
        )
        assert entry.past_snapshot == snapshot
        store = FakeWeatherForecastStore()
        store.store_weather_forecasts(rows)
        arguments = dict(
            station_id=sid,
            issue_time=ISSUE,
            used_at=ISSUE,
            entry=entry,
            expected_attempt=entry.attempt,
            past_requirement=PastWeatherRequirement(
                start=cycle, end=ISSUE, parameters=("precipitation", "temperature")
            ),
            future_requirement=current_request,
            store=store,
            earliest_cycle=cycle,
            max_past_bytes=LIMIT,
            ensemble_mode=EnsembleMode.SINGLE,
            model=FakeStationForecastModel(),
            model_id=ModelId("fixture"),
            gap_config=WeatherGapConfig(mode=mode),
        )
        if tail_days > 1 or mode is not WeatherGapMode.ALWAYS:
            with pytest.raises(
                ConfigurationError, match="past weather is blocked: missing_support"
            ):
                assemble_selected_weather(**arguments)
            return
        result = assemble_selected_weather(**arguments)
        assert isinstance(result.use.past, PastWeatherUse)
        assert result.use.past.snapshot_sha256 == snapshot.payload_sha256
        assert result.use.past.days[-1].samples == ()
        if tail_days == 1:
            assert result.past_dynamic["precipitation"].to_list() == [8.0, 8.0]
            assert result.past_dynamic["temperature"].to_list() == [10.0, 10.0]
            assert len(result.use.past.gap_fills) == 2
            flags = selected_weather_quality_flags(
                StationSourceEvidence(weather_use=result.use)
            )
            assert any(
                "Missing weather data estimated" in flag.detail for flag in flags
            )
            assert any("weather STALE" in flag.detail for flag in flags)
        else:
            assert result.past_dynamic["precipitation"].to_list() == [8.0, None, None]
            assert not result.use.past.gap_fills

        template = _station_model_inputs()
        inputs = replace(
            template,
            station_id=sid,
            issue_time=ISSUE,
            time_step=timedelta(days=1),
            data=replace(
                template.data,
                past_dynamic=result.past_dynamic,
                future_dynamic=result.future_dynamic,
            ),
            source_evidence=StationSourceEvidence(
                weather_use=result.use, future_weather=result.future_records
            ),
        )
        evidence = capture_station_evidence(
            inputs=inputs,
            model=FakeStationForecastModel(),
            model_id=ModelId("fixture"),
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
        assert (
            restore_selected_weather(
                evidence, model_id=ModelId("fixture"), issue_time=ISSUE
            )[sid]
            == result.use
        )

        # A changed donor row invalidates its receipt; no interpolation hides it.
        corrupt = FakeWeatherForecastStore()
        corrupt.store_weather_forecasts(
            [
                replace(row, value=row.value + 1)
                if row.valid_time == old_issue
                else row
                for row in rows
            ]
        )
        with pytest.raises(WeatherProofError, match="complete receipt"):
            assemble_selected_weather(**{**arguments, "store": corrupt})
