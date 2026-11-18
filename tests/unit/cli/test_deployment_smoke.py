from __future__ import annotations

import io
import json
from email.message import Message
from http.client import HTTPMessage
from typing import Any
from uuid import UUID

import pytest

from sapphire_flow.cli import deployment_smoke as smoke
from sapphire_flow.services.deployment_smoke import SmokeSetup

_STATION = UUID("00000000-0000-0000-0000-000000000001")
_FORECAST = UUID("00000000-0000-0000-0000-000000000002")
_TOKEN = "DO_NOT_RETAIN_SMOKE_CREDENTIAL"


def _prepare(monkeypatch: pytest.MonkeyPatch, **changes: object) -> None:
    payload: dict[str, object] = {
        "station_id": str(_STATION),
        "forecast_id": str(_FORECAST),
        "token": _TOKEN,
    }
    payload.update(changes)
    monkeypatch.setattr(smoke.sys, "stdin", io.StringIO(json.dumps(payload)))

    def require(role: str) -> str:
        assert role == "worker"
        return "a" * 32

    monkeypatch.setattr(smoke, "require_smoke_environment", require)

    def read_setup(run_id: str) -> SmokeSetup:
        return SmokeSetup(
            run_id=run_id,
            station_id=_STATION,
            artifact_id=_FORECAST,
            observations_passed=72,
        )

    monkeypatch.setattr(smoke, "read_setup", read_setup)


class TestInternalApiProbe:
    def test_fixed_gets_disable_proxies_and_redirects_and_keep_token_out_of_output(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _prepare(monkeypatch)
        monkeypatch.setenv("HTTP_PROXY", "http://external-proxy.invalid:8080")
        monkeypatch.setenv("ALL_PROXY", "http://external-proxy.invalid:8080")
        seen: list[tuple[str, str | None]] = []

        class Response(io.BytesIO):
            status = 200

        def open_request(opener: Any, request: Any, *, timeout: int) -> Response:
            assert not any(
                isinstance(handler, smoke.urllib.request.ProxyHandler)
                for handler in opener.handlers
            )
            redirects = [
                handler
                for handler in opener.handlers
                if isinstance(handler, smoke.urllib.request.HTTPRedirectHandler)
            ]
            assert len(redirects) == 1
            assert (
                redirects[0].redirect_request(
                    request,
                    io.BytesIO(),
                    302,
                    "redirect",
                    HTTPMessage(),
                    "http://external.invalid",
                )
                is None
            )
            assert timeout == 10
            assert request.get_method() == "GET"
            authorization = request.get_header("Authorization")
            seen.append((request.full_url, authorization))
            if authorization is None:
                raise smoke.urllib.error.HTTPError(
                    request.full_url,
                    401,
                    "unauthorized",
                    Message(),
                    io.BytesIO(_TOKEN.encode()),
                )
            return Response(b'{"ok":true}')

        monkeypatch.setattr(smoke.urllib.request.OpenerDirector, "open", open_request)
        assert smoke.main(["api-probe"]) == 0
        captured = capsys.readouterr()
        assert _TOKEN not in captured.out + captured.err
        assert json.loads(captured.out) == {
            "unauthorized_status": 401,
            "forecast_status": 200,
            "forecast": {"ok": True},
            "station_status": 200,
            "station": {"ok": True},
        }
        assert seen == [
            (f"http://api:8000/api/v1/forecasts/{_FORECAST}", None),
            (f"http://api:8000/api/v1/forecasts/{_FORECAST}", "Bearer " + _TOKEN),
            (f"http://api:8000/api/v1/stations/{_STATION}", "Bearer " + _TOKEN),
        ]

    @pytest.mark.parametrize(
        "changes",
        [
            {"forecast_id": "../../external"},
            {"station_id": str(_FORECAST)},
            {"url": "http://external.invalid"},
            {"method": "POST"},
            {"token": "x" * 4097},
        ],
    )
    def test_invalid_request_refuses_without_http_or_sensitive_output(
        self,
        changes: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _prepare(monkeypatch, **changes)

        def refuse(*args: object, **kwargs: object) -> None:
            pytest.fail("invalid input must not reach HTTP")

        monkeypatch.setattr(smoke.urllib.request.OpenerDirector, "open", refuse)
        assert smoke.main(["api-probe"]) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == "smoke application stage failed\n"

    def test_response_size_is_bounded_and_failure_does_not_echo_body(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _prepare(monkeypatch)

        class Response(io.BytesIO):
            status = 200

        def open_request(*args: object, **kwargs: object) -> Response:
            return Response((_TOKEN + "x" * 65537).encode())

        monkeypatch.setattr(smoke.urllib.request.OpenerDirector, "open", open_request)
        assert smoke.main(["api-probe"]) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert _TOKEN not in captured.err
