from __future__ import annotations

import logging
from dataclasses import replace

import httpx
import pytest

from sapphire_flow.adapters.recap_registration import (
    GatewayAccessError,
    RecapRegistration,
    validate_connection,
)
from sapphire_flow.config.recap_gateway import RecapGatewayConfig
from sapphire_flow.types.gateway_preparation import StreamSetting, Subscription

KEY = "synthetic-sentinel-secret"


def config() -> RecapGatewayConfig:
    return RecapGatewayConfig(
        base_url="https://example.invalid/sdk",
        timeout_s=5,
        verify_tls=True,
        staleness_threshold_hours=12,
        hru_metadata_source="synthetic",
        max_retries=0,
    )


@pytest.mark.parametrize(
    "url,tls",
    [
        ("http://example.invalid/sdk", True),
        ("https://example.invalid/sdk", False),
        ("https://user:password@example.invalid/sdk", True),
        ("https://example.invalid/sdk?api_key=" + KEY, True),
    ],
)
def test_https_required(url: str, tls: bool) -> None:
    with pytest.raises(ValueError, match="HTTPS") as error:
        validate_connection(url, verify_tls=tls)
    assert KEY not in str(error.value)


def test_exact_upload_and_safe_third_party_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    requests = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        logging.getLogger("httpx").critical("unsafe %s", request.url)
        return httpx.Response(
            200,
            json={
                "message": "Uploaded",
                "detail": "Geometry stored",
                "warnings": "Warning "
                + KEY
                + " https://example.invalid/?api_key="
                + KEY,
            },
        )

    caplog.set_level(logging.DEBUG)
    adapter = RecapRegistration(
        config(), api_key=KEY, transport=httpx.MockTransport(transport)
    )
    try:
        result = adapter.upload(
            hru_code="new_synthetic", content=b"exact-approved-bytes"
        )
    finally:
        adapter.close()
    assert requests[0].url.path == "/sdk/hru/vector-files/upload"
    assert requests[0].url.params["hru_code"] == "new_synthetic"
    assert b"exact-approved-bytes" in requests[0].content
    assert b'filename="basins.gpkg"' in requests[0].content
    assert KEY not in repr(result) + caplog.text
    assert len(requests) == 1


@pytest.mark.parametrize("status", [302, 400, 401, 403, 409, 422, 500])
def test_failure_has_no_raw_chain(status: int) -> None:
    calls = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            status,
            headers={"location": "https://other.invalid"},
            json={"message": KEY, "detail": str(request.url)},
        )

    adapter = RecapRegistration(
        config(), api_key=KEY, transport=httpx.MockTransport(transport)
    )
    try:
        with pytest.raises(GatewayAccessError) as error:
            adapter.upload(hru_code="new_synthetic", content=b"synthetic")
    finally:
        adapter.close()
    assert len(calls) == 1
    assert KEY not in str(error.value)
    assert error.value.__context__ is None and error.value.__cause__ is None


def test_timeout_and_malformed_response() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(KEY + str(request.url), request=request)

    adapter = RecapRegistration(
        config(), api_key=KEY, transport=httpx.MockTransport(transport)
    )
    try:
        with pytest.raises(GatewayAccessError, match="uncertain") as error:
            adapter.upload(hru_code="new_synthetic", content=b"synthetic")
    finally:
        adapter.close()
    assert error.value.__context__ is None
    assert KEY not in str(error.value)


def test_tls_rejected_before_transport() -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError("transport called")

    with pytest.raises(ValueError, match="HTTPS"):
        RecapRegistration(
            replace(config(), verify_tls=False),
            api_key=KEY,
            transport=httpx.MockTransport(forbidden),
        )


def test_subscription_update_preserves_unrequested_snow_flag() -> None:
    before = Subscription(
        source="synthetic_snow",
        hru_code="new_synthetic",
        parameter_name="swe",
        enabled=StreamSetting.ENABLED,
        forecast_enabled=StreamSetting.ENABLED,
        reanalysis_enabled=StreamSetting.DISABLED,
    )
    after = replace(before, reanalysis_enabled=StreamSetting.ENABLED)
    requests = []

    def transport(request: httpx.Request) -> httpx.Response:
        import json

        requests.append(request)
        assert request.method == "PUT"
        assert json.loads(request.content) == {"reanalysis_enabled": True}
        return httpx.Response(
            200,
            json={
                "source": before.source,
                "hru_code": before.hru_code,
                "parameter_name": "swe",
                "enabled": True,
                "forecast_enabled": True,
                "reanalysis_enabled": True,
            },
        )

    adapter = RecapRegistration(
        config(), api_key=KEY, transport=httpx.MockTransport(transport)
    )
    try:
        adapter.update_subscription(before, after)
    finally:
        adapter.close()
    assert len(requests) == 1


def test_scoped_read_rejects_another_hru() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "source": "synthetic_era5",
                    "hru_code": "other",
                    "parameter_name": "temperature",
                    "enabled": True,
                }
            ],
        )

    adapter = RecapRegistration(
        config(), api_key=KEY, transport=httpx.MockTransport(transport)
    )
    try:
        with pytest.raises(GatewayAccessError, match="malformed"):
            adapter.subscriptions(source="synthetic_era5", hru_code="new_synthetic")
    finally:
        adapter.close()
