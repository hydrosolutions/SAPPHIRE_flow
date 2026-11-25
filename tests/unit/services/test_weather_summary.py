from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest

from sapphire_flow.services.weather_summary import summarize_weather_use
from sapphire_flow.store.weather_summary_codec import (
    decode_weather_summary,
    encode_weather_summary,
    summary_digest,
)
from sapphire_flow.types.ids import ForecastId
from sapphire_flow.types.operational_weather import (
    ContributingWeatherUse,
    DerivedWeatherUse,
    WeatherFailureReason,
    WeatherFreshness,
    WeatherLegOutcome,
    WeatherLegState,
)
from sapphire_flow.types.operational_weather_v2 import (
    ContributingWeatherUseV2,
    DerivedWeatherUseV2,
    PastWeatherUseV2,
    WeatherUseV2,
)
from tests.unit.services.test_period_weather_evidence import assemble, entry_and_store
from tests.unit.services.test_selected_weather_evidence import source_evidence


class TestWeatherSummary:
    def test_station_v1_preserves_sources_age_and_unused_leg(self) -> None:
        use = source_evidence().weather_use
        assert use is not None
        summary = summarize_weather_use(use)
        assert summary.past.freshness == "unused"
        assert summary.past.uses == ()
        assert summary.future.freshness == "current"
        assert summary.future.uses[0].sources[0].source == "ifs_ecmwf"
        assert summary.future.uses[0].age_at_use_seconds == 0
        payload = encode_weather_summary(summary)
        assert decode_weather_summary(payload, summary_digest(payload)) == summary

    def test_combined_v1_keeps_unknown_contributor(self) -> None:
        use = source_evidence().weather_use
        assert use is not None
        combined = DerivedWeatherUse(
            station_id=use.station_id,
            issue_time=use.issue_time,
            contributors=(
                ContributingWeatherUse(
                    forecast_id=ForecastId(UUID(int=1)),
                    evidence_sha256="a" * 64,
                    weather_use=use,
                ),
                ContributingWeatherUse(
                    forecast_id=ForecastId(UUID(int=2)),
                    evidence_sha256=None,
                    weather_use=None,
                ),
            ),
        )
        summary = summarize_weather_use(combined)
        assert summary.future.freshness == "unknown"
        assert summary.past.freshness == "unknown"
        assert len(summary.future.uses) == 1

    def test_period_and_combined_stale_sources_round_trip(self) -> None:
        entry, store = entry_and_store(days=3)
        use = assemble(entry, store).use
        assert isinstance(use, WeatherUseV2)
        assert isinstance(use.past, PastWeatherUseV2)
        past = replace(
            use.past,
            freshness=WeatherFreshness.STALE,
            refresh_outcome=WeatherLegOutcome(
                state=WeatherLegState.UNAVAILABLE, reason=WeatherFailureReason.TRANSPORT
            ),
        )
        stale = replace(use, past=past, used_at=use.used_at + timedelta(hours=1))
        combined = DerivedWeatherUseV2(
            grid=use.grid,
            station_id=use.station_id,
            issue_time=use.issue_time,
            contributors=tuple(
                ContributingWeatherUseV2(
                    forecast_id=ForecastId(UUID(int=i)),
                    evidence_sha256="b" * 64,
                    weather_use=fact,
                )
                for i, fact in enumerate((use, stale), 1)
            ),
        )
        summary = summarize_weather_use(combined)
        assert summary.past.freshness == "stale"
        assert summary.future.freshness == "current"
        assert summary.past.uses[1].failure_code == "transport"
        assert summary.past.uses[1].failure_at == use.refresh_finished_at
        assert summary.past.uses[1].age_at_use_seconds == 3600
        assert summary.past.uses[0].sources == summary.past.uses[1].sources
        payload = encode_weather_summary(summary)
        assert decode_weather_summary(payload, summary_digest(payload)) == summary

    def test_codec_rejects_hash_mismatch_and_unknown_fields(self) -> None:
        use = source_evidence().weather_use
        assert use is not None
        payload = encode_weather_summary(summarize_weather_use(use))
        with pytest.raises(ValueError, match="hash mismatch"):
            decode_weather_summary(payload, "0" * 64)
        payload = payload[:-1] + ',"unexpected":true}'
        with pytest.raises(ValueError, match="unexpected"):
            decode_weather_summary(payload, summary_digest(payload))
