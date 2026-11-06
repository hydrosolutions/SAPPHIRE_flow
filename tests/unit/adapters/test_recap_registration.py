from __future__ import annotations

import logging
from contextlib import nullcontext
from dataclasses import replace

import httpx
import pytest

from sapphire_flow.adapters.recap_registration import (
    GatewayAccessError,
    RecapRegistration,
    quiet_gateway_transport,
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


@pytest.mark.parametrize(
    "exception_type", [httpx.InvalidURL, httpx.StreamError, httpx.CookieConflict]
)
def test_all_httpx_transport_errors_are_chain_free(
    exception_type: type[Exception],
) -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        raise exception_type("synthetic failure " + str(request.url))

    adapter = RecapRegistration(
        config(), api_key=KEY, transport=httpx.MockTransport(transport)
    )
    try:
        for action in (
            lambda: adapter.upload(hru_code="new_synthetic", content=b"synthetic"),
            lambda: adapter.subscriptions(source="era5", hru_code="new_synthetic"),
        ):
            with pytest.raises(GatewayAccessError) as error:
                action()
            assert KEY not in str(error.value)
            assert error.value.__context__ is None and error.value.__cause__ is None
    finally:
        adapter.close()


def test_encoded_secret_echoes_redacted_before_truncation() -> None:
    from urllib.parse import quote, quote_plus

    secret = "aaaa/aaaa+aaaa aaaa%aaaa?"
    forms = {secret, quote(secret), quote(secret, safe=""), quote_plus(secret)}
    forms |= {
        form.replace("%2F", "%2f").replace("%2B", "%2b").replace("%3F", "%3f")
        for form in forms
    }
    echo = " credential ".join(sorted(forms))

    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"message": echo, "detail": echo, "warnings": "x" * 980 + echo}
        )

    adapter = RecapRegistration(
        config(), api_key=secret, transport=httpx.MockTransport(transport)
    )
    try:
        receipt = adapter.upload(hru_code="new_synthetic", content=b"synthetic")
    finally:
        adapter.close()
    for form in forms:
        assert form not in receipt.message + receipt.detail + str(receipt.warnings)


@pytest.mark.parametrize("method", ["POST", "PUT"])
@pytest.mark.parametrize(
    "status,reason",
    [
        (400, "invalid_request"),
        (422, "invalid_request"),
        (409, "reconciliation_required"),
        (401, "authorization_failed"),
        (403, "authorization_failed"),
        (0, "outcome_uncertain"),
        (200, "malformed_response"),
    ],
)
def test_subscription_write_preserves_typed_failure_category(
    method: str, status: int, reason: str
) -> None:
    target = Subscription(
        source="era5",
        hru_code="synthetic",
        parameter_name="temperature",
        enabled=StreamSetting.ENABLED,
    )

    def transport(request: httpx.Request) -> httpx.Response:
        assert request.method == method
        if status == 0:
            raise httpx.ReadTimeout(KEY + str(request.url), request=request)
        return httpx.Response(status, json={"detail": KEY + str(request.url)})

    adapter = RecapRegistration(
        config(), api_key=KEY, transport=httpx.MockTransport(transport)
    )
    try:
        with pytest.raises(GatewayAccessError, match=reason) as error:
            if method == "POST":
                adapter.create_subscription(target)
            else:
                adapter.update_subscription(
                    replace(target, enabled=StreamSetting.DISABLED), target
                )
        assert error.value.reason.value == reason
        assert KEY not in str(error.value)
        assert error.value.__context__ is None and error.value.__cause__ is None
    finally:
        adapter.close()


@pytest.mark.parametrize("exit_mode", ["normal", "exception"])
def test_transport_suppresses_above_critical_and_restores_nested_threshold(
    caplog: pytest.LogCaptureFixture, exit_mode: str
) -> None:
    logger = logging.getLogger("httpx")
    caplog.set_level(1, logger="httpx")
    original = logging.root.manager.disable
    logging.disable(17)
    try:
        expected = (
            pytest.raises(RuntimeError, match="synthetic exit")
            if exit_mode == "exception"
            else nullcontext()
        )
        with expected, quiet_gateway_transport():
            logger.log(60, "api_key=aaaa-reflected-aaaa")
            with quiet_gateway_transport():
                logger.log(60, "api_key=aaaa-reflected-aaaa")
                if exit_mode == "exception":
                    raise RuntimeError("synthetic exit")
            logger.log(60, "api_key=aaaa-reflected-aaaa")
        assert logging.root.manager.disable == 17
        assert "aaaa-reflected-aaaa" not in caplog.text
        logger.log(60, "safe restoration marker")
        assert "safe restoration marker" in caplog.text
    finally:
        logging.disable(original)


@pytest.mark.parametrize("scheme", ["HTTPS", "hTtPs", "HTTP"])
def test_upload_metadata_removes_case_insensitive_urls_before_truncation(
    scheme: str,
) -> None:
    secret = "aaaa/aaaa+aaaa"
    url = scheme + "://gateway.invalid/private?api_key=" + secret
    reflected = "x" * 980 + " See " + url

    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"message": url, "detail": reflected, "warnings": reflected}
        )

    adapter = RecapRegistration(
        config(), api_key=secret, transport=httpx.MockTransport(transport)
    )
    try:
        receipt = adapter.upload(hru_code="synthetic", content=b"synthetic")
    finally:
        adapter.close()
    text = receipt.message + receipt.detail + str(receipt.warnings)
    assert "://" not in text and "gateway.invalid" not in text
    assert secret not in text
