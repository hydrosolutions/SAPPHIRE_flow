from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from sapphire_flow.adapters.dhm import DhmAdapter
from sapphire_flow.config.dhm import parse_dhm_config
from sapphire_flow.types.datetime import ensure_utc
from tests.unit.adapters.test_dhm import NOW, response, settings, station


def _run(*, historical: bool, days_back: int) -> list[httpx.Request]:
    config = station()
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response(200, json={"results": []})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        adapter = DhmAdapter(
            config=parse_dhm_config(settings()),
            http_client=client,
            clock=lambda: NOW,
            sleeper=lambda _: None,
        )
        start = ensure_utc(NOW - timedelta(days=days_back))
        if historical:
            adapter.fetch_observations_window([config], start=start, end=NOW)
        else:
            adapter.fetch_observations_batch([config], {config.id: start})
    return requests


def test_stale_cursor_plans_at_most_lookback_requests() -> None:
    assert len(_run(historical=False, days_back=60)) == 7


def test_cursor_inside_lookback_is_not_clamped() -> None:
    assert len(_run(historical=False, days_back=3)) == 3


def test_historical_mode_is_not_clamped() -> None:
    assert len(_run(historical=True, days_back=20)) == 20


def test_lookback_is_configurable_and_validated() -> None:
    assert parse_dhm_config(settings()).polling_lookback_days == 7.0
    two = parse_dhm_config(settings(polling_lookback_days=2))
    assert two.polling_lookback_days == 2
    with pytest.raises(Exception, match="Invalid DHM adapter configuration"):
        parse_dhm_config(settings(polling_lookback_days=0))
