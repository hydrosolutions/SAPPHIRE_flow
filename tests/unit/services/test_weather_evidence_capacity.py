import zlib

import pytest

from sapphire_flow.config.operational_weather import (
    DEFAULT_SELECTED_WEATHER_EVIDENCE_BYTES,
    MAX_SELECTED_WEATHER_EVIDENCE_BYTES,
)
from sapphire_flow.services.selected_weather_evidence import (
    restore_bounded_weather_snapshot,
)


def _compressed_string_payload(value_bytes: int, trailing: bytes = b"") -> bytes:
    compressor = zlib.compressobj()
    chunks = [compressor.compress(b'{"data":"')]
    for offset in range(0, value_bytes, 65536):
        chunks.append(compressor.compress(b"x" * min(65536, value_bytes - offset)))
    chunks.append(compressor.compress(b'"}' + trailing))
    chunks.append(compressor.flush())
    return b"".join(chunks)


class TestBoundedWeatherSnapshot:
    @pytest.mark.parametrize(
        "limit",
        [DEFAULT_SELECTED_WEATHER_EVIDENCE_BYTES, MAX_SELECTED_WEATHER_EVIDENCE_BYTES],
    )
    def test_exact_limit_and_one_byte_over(self, limit: int) -> None:
        compressed = _compressed_string_payload(limit - 11)
        value = restore_bounded_weather_snapshot(compressed, max_bytes=limit)["data"]
        assert isinstance(value, str)
        assert len(value) == limit - 11
        del value
        with pytest.raises(ValueError, match="oversized or malformed"):
            restore_bounded_weather_snapshot(
                _compressed_string_payload(limit - 11, trailing=b" "), max_bytes=limit
            )

    def test_higher_reader_ceiling_preserves_old_capture_configuration(self) -> None:
        value_bytes = 128 * 1024 * 1024
        compressed = _compressed_string_payload(value_bytes)
        with pytest.raises(ValueError, match="oversized or malformed"):
            restore_bounded_weather_snapshot(
                compressed, max_bytes=DEFAULT_SELECTED_WEATHER_EVIDENCE_BYTES
            )
        value = restore_bounded_weather_snapshot(
            compressed, max_bytes=MAX_SELECTED_WEATHER_EVIDENCE_BYTES
        )["data"]
        assert isinstance(value, str)
        assert len(value) == value_bytes

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

    @pytest.mark.parametrize("limit", [0, -1, 402653185])
    def test_invalid_limit_is_rejected(self, limit: int) -> None:
        with pytest.raises(ValueError, match="invalid selected weather evidence limit"):
            restore_bounded_weather_snapshot(zlib.compress(b"{}"), max_bytes=limit)


@pytest.mark.parametrize(
    ("station_count", "lower_mib", "upper_mib"),
    [(6, 32, 96), pytest.param(21, 128, 288, marks=pytest.mark.slow)],
)
def test_full_cmal_capture_opt_in_and_default_reader(
    station_count: int, lower_mib: int, upper_mib: int
) -> None:
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

    inputs = cmal_capacity_inputs(station_count=station_count)
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
    evidence = capture(upper_mib * 1024 * 1024)
    assert evidence.snapshot is not None
    assert (
        lower_mib * 1024 * 1024
        < len(zlib.decompress(evidence.snapshot))
        <= upper_mib * 1024 * 1024
    )
    # Readers use the absolute bound, independent of current capture config.
    assert OperationalWeatherConfig().max_evidence_bytes == 32 * 1024 * 1024
    assert restore_selected_weather(
        evidence, model_id=model_id, issue_time=inputs.issue_time
    ) == {
        station_id: source.weather_use for station_id, source in inputs.source_evidence
    }
