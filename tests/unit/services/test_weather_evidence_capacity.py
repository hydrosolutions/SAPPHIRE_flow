import json
import zlib

import pytest

from sapphire_flow.config.operational_weather import (
    DEFAULT_SELECTED_WEATHER_EVIDENCE_BYTES,
    MAX_SELECTED_WEATHER_EVIDENCE_BYTES,
)
from sapphire_flow.services.selected_weather_evidence import (
    restore_bounded_weather_snapshot,
)


class TestBoundedWeatherSnapshot:
    @pytest.mark.parametrize(
        "limit",
        [DEFAULT_SELECTED_WEATHER_EVIDENCE_BYTES, MAX_SELECTED_WEATHER_EVIDENCE_BYTES],
    )
    def test_exact_limit_and_one_byte_over(self, limit: int) -> None:
        raw = b'{"data":"' + b"x" * (limit - 11) + b'"}'
        assert len(raw) == limit
        compressed = zlib.compress(raw)
        value = restore_bounded_weather_snapshot(compressed, max_bytes=limit)["data"]
        assert isinstance(value, str)
        assert len(value) == limit - 11
        with pytest.raises(ValueError, match="oversized or malformed"):
            restore_bounded_weather_snapshot(zlib.compress(raw + b" "), max_bytes=limit)

    def test_higher_reader_ceiling_preserves_old_capture_configuration(self) -> None:
        raw = b'{"data":"' + b"x" * DEFAULT_SELECTED_WEATHER_EVIDENCE_BYTES + b'"}'
        compressed = zlib.compress(raw)
        with pytest.raises(ValueError, match="oversized or malformed"):
            restore_bounded_weather_snapshot(
                compressed, max_bytes=DEFAULT_SELECTED_WEATHER_EVIDENCE_BYTES
            )
        assert restore_bounded_weather_snapshot(
            compressed, max_bytes=MAX_SELECTED_WEATHER_EVIDENCE_BYTES
        ) == json.loads(raw)

    @pytest.mark.parametrize("fault", ["truncated", "trailing", "concatenated"])
    def test_invalid_stream_fails_closed(self, fault: str) -> None:
        compressed = zlib.compress(b'{"data":1}')
        damaged = {
            "truncated": compressed[:-1],
            "trailing": compressed + b"unexpected",
            "concatenated": compressed + compressed,
        }[fault]
        with pytest.raises(ValueError, match="oversized or malformed"):
            restore_bounded_weather_snapshot(
                damaged, max_bytes=MAX_SELECTED_WEATHER_EVIDENCE_BYTES
            )

    @pytest.mark.parametrize("limit", [0, -1, 134217729])
    def test_invalid_limit_is_rejected(self, limit: int) -> None:
        with pytest.raises(ValueError, match="invalid selected weather evidence limit"):
            restore_bounded_weather_snapshot(zlib.compress(b"{}"), max_bytes=limit)


def test_full_cmal_capture_opt_in_and_default_reader() -> None:
    import random

    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.config.operational_weather import (
        OperationalPastWeatherRoute,
        OperationalWeatherConfig,
    )
    from sapphire_flow.exceptions import WeatherEvidenceCaptureError
    from sapphire_flow.services.forecast_evidence import capture_group_evidence
    from sapphire_flow.services.selected_weather_evidence import (
        restore_selected_weather,
    )
    from sapphire_flow.types.domain import ForecastQcRuleSet
    from sapphire_flow.types.forecast_evidence import ForecastEvidence
    from sapphire_flow.types.ids import ModelId
    from tests.fakes.weather_capacity import cmal_capacity_inputs

    inputs = cmal_capacity_inputs()
    model_id = ModelId("capacity_fixture")

    def capture(limit: int) -> ForecastEvidence:
        return capture_group_evidence(
            inputs=inputs,
            model=object(),
            model_id=model_id,
            artifact_bytes=b"fixture",
            rng_state=random.Random(1).getstate(),
            config=DeploymentConfig(
                max_retention_days=1000,
                operational_weather=OperationalWeatherConfig(
                    route=OperationalPastWeatherRoute.RECAP_COMBINED,
                    max_evidence_bytes=limit,
                ),
            ),
            qc_rules=ForecastQcRuleSet(version="1", rules=()),
            qc_overrides=[],
            baselines_by_station={},
            water_level_datums_masl={},
        )

    with pytest.raises(
        WeatherEvidenceCaptureError, match="selected group evidence capture failed"
    ):
        capture(OperationalWeatherConfig().max_evidence_bytes)
    evidence = capture(MAX_SELECTED_WEATHER_EVIDENCE_BYTES)
    assert evidence.snapshot is not None
    assert (
        32 * 1024 * 1024 < len(zlib.decompress(evidence.snapshot)) <= 96 * 1024 * 1024
    )
    # Readers use the absolute bound, independent of current capture config.
    assert OperationalWeatherConfig().max_evidence_bytes == 32 * 1024 * 1024
    assert restore_selected_weather(
        evidence, model_id=model_id, issue_time=inputs.issue_time
    ) == {
        station_id: source.weather_use for station_id, source in inputs.source_evidence
    }
