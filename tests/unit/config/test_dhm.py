from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from sapphire_flow.adapters.dhm import DhmAdapter
from sapphire_flow.config.dhm import parse_dhm_config
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.enums import FetchOutcomeCause
from tests.unit.adapters.test_dhm import NOW, START, response, settings, station


class TestParseDhmConfig:
    @pytest.mark.parametrize(
        "override",
        [
            {"endpoint": "http://dhm.example/api/v1/"},
            {"endpoint": "https://user:secret@dhm.example/"},
            {"endpoint": "https://dhm.example/?token=secret"},
            {"timeout_s": True},
            {"timeout_s": float("inf")},
            {"window_hours": 0},
            {"window_hours": 1e100},
            {"page_size": 1.5},
            {"max_pages_per_station": 0},
            {"min_request_interval_s": -1},
        ],
    )
    def test_invalid_global_configuration_is_sanitised(
        self, override: dict[str, object]
    ) -> None:
        with pytest.raises(
            ConfigurationError, match="Invalid DHM adapter configuration"
        ):
            parse_dhm_config(settings(**override))

    @pytest.mark.parametrize("identifier", [True, 0, -1, "168"])
    def test_station_id_requires_positive_integer(self, identifier: object) -> None:
        with pytest.raises(ConfigurationError, match="Invalid DHM"):
            parse_dhm_config(
                settings(
                    bindings=[
                        {
                            "network": "dhm",
                            "station_code": "DHM-1",
                            "api_station_id": identifier,
                            "level_reference": "unknown",
                        }
                    ]
                )
            )

    def test_duplicate_binding_keys_are_rejected(self) -> None:
        binding = {
            "network": "dhm",
            "station_code": "DHM-1",
            "api_station_id": 168,
            "level_reference": "unknown",
        }
        with pytest.raises(ConfigurationError, match="Invalid DHM"):
            parse_dhm_config(settings(bindings=[binding, binding]))

    @pytest.mark.parametrize(
        "unit, datum, reference",
        [
            (None, None, "unknown"),
            ("cm", None, "unknown"),
            ("m a.s.l.", None, "gauge_zero"),
            ("m", 123.0, "gauge_zero"),
            ("m", 123.0, "unknown"),
        ],
    )
    def test_bad_metadata_preflight_aborts_batch(
        self, unit: str | None, datum: float | None, reference: str
    ) -> None:
        valid = station()
        invalid = replace(
            station("DHM-2"), water_level_unit=unit, water_level_datum_masl=datum
        )
        bindings = [
            {
                "network": "dhm",
                "station_code": "DHM-1",
                "api_station_id": 168,
                "level_reference": "unknown",
            },
            {
                "network": "dhm",
                "station_code": "DHM-2",
                "api_station_id": 169,
                "level_reference": reference,
            },
        ]
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return response(200, json={"results": []})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = DhmAdapter(
                config=parse_dhm_config(settings(bindings=bindings)),
                http_client=client,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            ).fetch_observations_batch(
                [invalid, valid], {invalid.id: START, valid.id: START}
            )
        assert requests == []
        assert result.outcomes[1].failure_cause is FetchOutcomeCause.BATCH_ABORTED
        assert result.failed[0].station_id == invalid.id
        assert result.failed[0].failure_cause is FetchOutcomeCause.CONFIGURATION_ERROR


class TestDhmBudgetConfig:
    @pytest.mark.parametrize(
        "field", ["max_requests_per_batch", "max_bytes_per_batch", "max_batch_seconds"]
    )
    def test_required_operator_bounds(self, field: str) -> None:
        raw = settings()
        del raw[field]
        with pytest.raises(ConfigurationError, match="Invalid DHM"):
            parse_dhm_config(raw)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("max_requests_per_batch", True),
            ("max_requests_per_batch", 0),
            ("max_requests_per_batch", 1.5),
            ("max_bytes_per_batch", -1),
            ("max_bytes_per_batch", True),
            ("max_batch_seconds", True),
            ("max_batch_seconds", 0),
            ("max_batch_seconds", float("nan")),
            ("max_batch_seconds", float("inf")),
            ("page_size", 101),
            ("min_request_interval_s", 4.99),
            ("timeout_s", 30.01),
            ("window_hours", 0.1 / 3600000000),
        ],
    )
    def test_parser_and_direct_dataclass_reject_invalid_bounds(
        self, field: str, value: object
    ) -> None:
        with pytest.raises(ConfigurationError, match="Invalid DHM"):
            parse_dhm_config(settings(**{field: value}))
        valid = parse_dhm_config(settings())
        with pytest.raises((ValueError, OverflowError), match="DHM"):
            replace(valid, **{field: value})

    def test_safe_defaults_and_exact_boundaries(self) -> None:
        config = parse_dhm_config(
            settings(
                max_requests_per_batch=1, max_bytes_per_batch=1, max_batch_seconds=0.001
            )
        )
        assert (config.page_size, config.min_request_interval_s, config.timeout_s) == (
            100,
            5,
            30,
        )
