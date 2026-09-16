from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from sapphire_flow.adapters.dhm import DhmAdapter
from sapphire_flow.config.dhm import parse_dhm_config
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.enums import FetchOutcomeCause
from tests.unit.adapters.test_dhm import NOW, START, settings, station


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
    def test_bad_metadata_fails_only_its_station(
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
            return httpx.Response(200, json={"results": []})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = DhmAdapter(
                config=parse_dhm_config(settings(bindings=bindings)),
                http_client=client,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            ).fetch_observations_batch(
                [invalid, valid], {invalid.id: START, valid.id: START}
            )
        assert len(requests) == 1
        assert result.outcomes[1].failure_cause is None
        assert result.failed[0].station_id == invalid.id
        assert result.failed[0].failure_cause is FetchOutcomeCause.CONFIGURATION_ERROR
