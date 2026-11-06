"""Supported Gateway upload boundary; no retrieval-client modification."""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, TypeVar
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, StrictBool, StrictStr, TypeAdapter, ValidationError

from sapphire_flow.types.gateway_preparation import StreamSetting, Subscription

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sapphire_flow.config.recap_gateway import RecapGatewayConfig


class GatewayFailure(StrEnum):
    AUTHORIZATION = "authorization_failed"
    INVALID_REQUEST = "invalid_request"
    CONFLICT = "reconciliation_required"
    UNAVAILABLE = "source_unavailable"
    UNCERTAIN = "outcome_uncertain"
    MALFORMED = "malformed_response"


class GatewayAccessError(ValueError):
    def __init__(self, reason: GatewayFailure) -> None:
        self.reason = reason
        super().__init__(reason.value)


def validate_connection(base_url: str, *, verify_tls: bool) -> None:
    try:
        url = urlsplit(base_url)
        safe = (
            url.scheme == "https"
            and bool(url.hostname)
            and not url.username
            and not url.password
            and not url.query
            and not url.fragment
            and verify_tls is True
        )
    except ValueError:
        safe = False
    if not safe:
        raise ValueError(
            "Gateway preparation requires HTTPS, TLS and a credential-free URL"
        )


@contextmanager
def quiet_gateway_transport() -> Iterator[None]:
    """Suppress third-party stdlib emission only during exclusive CLI I/O.

    Query authentication makes request logs unsafe at every severity. The CLI
    is single-process/single-threaded; restore its prior logging state afterward.
    This does not modify client methods, redirects or global logging handlers.
    """
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous)


def safe_gateway_failure(exc: Exception) -> GatewayAccessError:
    status = getattr(exc, "status_code", None)
    if type(status) is int and status in (401, 403):
        return GatewayAccessError(GatewayFailure.AUTHORIZATION)
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in (
        "source_data_missing",
        "subscription_not_found",
    ):
        return GatewayAccessError(GatewayFailure.UNAVAILABLE)
    return GatewayAccessError(GatewayFailure.UNCERTAIN)


class _UploadResponse(BaseModel):
    message: StrictStr
    detail: StrictStr
    warnings: StrictStr | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class UploadReceipt:
    message: str
    detail: str
    warnings: str | None


def _safe_text(text: str, api_key: str) -> str:
    # Redact before truncation so even a boundary-spanning key cannot survive.
    text = text.replace(api_key, "[redacted]") if api_key else text
    text = re.sub(r"https?://\S+", "[URL omitted]", text)
    text = re.sub(r"(?i)api_key\s*[=:]\s*\S+", "api_key=[redacted]", text)
    return "".join(c for c in text if c.isprintable() or c == "\n")[:1000]


class RecapRegistration:
    def __init__(
        self,
        config: RecapGatewayConfig,
        *,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        validate_connection(config.base_url, verify_tls=config.verify_tls)
        if not api_key:
            raise ValueError("Gateway credential is required")
        self._base = config.base_url.rstrip("/")
        self._key = api_key
        self._client = httpx.Client(
            verify=True,
            follow_redirects=False,
            timeout=config.timeout_s,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def upload(self, *, hru_code: str, content: bytes) -> UploadReceipt:
        if re.fullmatch(r"[a-z_][a-z0-9_]*", hru_code) is None:
            raise ValueError("new Gateway HRU must be a lowercase identifier")
        # Exact bytes already verified against the durable intent by the service.
        failure: GatewayFailure | None = None
        receipt: UploadReceipt | None = None
        with quiet_gateway_transport():
            try:
                response = self._client.post(
                    self._base + "/hru/vector-files/upload",
                    params={"hru_code": hru_code, "api_key": self._key},
                    files={
                        "files": (
                            "basins.gpkg",
                            content,
                            "application/geopackage+sqlite3",
                        )
                    },
                    follow_redirects=False,
                )
                status = response.status_code
                if status in (401, 403):
                    failure = GatewayFailure.AUTHORIZATION
                elif status == 409:
                    failure = GatewayFailure.CONFLICT
                elif status in (400, 422):
                    failure = GatewayFailure.INVALID_REQUEST
                elif not 200 <= status < 300:
                    failure = GatewayFailure.UNCERTAIN
                else:
                    body = _UploadResponse.model_validate_json(response.content)
                    receipt = UploadReceipt(
                        message=_safe_text(body.message, self._key),
                        detail=_safe_text(body.detail, self._key),
                        warnings=_safe_text(body.warnings, self._key)
                        if body.warnings
                        else None,
                    )
            except (httpx.HTTPError, OSError):
                failure = GatewayFailure.UNCERTAIN
            except (ValidationError, ValueError):
                failure = GatewayFailure.MALFORMED
        # Outside except: neither a secret-bearing context nor cause is retained.
        if failure is not None:
            raise GatewayAccessError(failure)
        if receipt is None:
            raise GatewayAccessError(GatewayFailure.MALFORMED)
        return receipt

    def _json(
        self,
        method: Literal["GET", "POST", "PUT"],
        path: str,
        boundary: TypeAdapter[_Reply],
        *,
        params: dict[str, str] | None = None,
        payload: dict[str, str | bool | None] | None = None,
    ) -> _Reply:
        result: _Reply | None = None
        failure: GatewayFailure | None = None
        with quiet_gateway_transport():
            try:
                response = self._client.request(
                    method,
                    self._base + path,
                    params={**(params or {}), "api_key": self._key},
                    json=payload,
                    follow_redirects=False,
                )
                if response.status_code in (401, 403):
                    failure = GatewayFailure.AUTHORIZATION
                elif not 200 <= response.status_code < 300:
                    failure = GatewayFailure.UNCERTAIN
                else:
                    result = boundary.validate_json(response.content)
            except (httpx.HTTPError, OSError):
                failure = GatewayFailure.UNCERTAIN
            except (ValidationError, ValueError):
                failure = GatewayFailure.MALFORMED
        if failure is not None:
            raise GatewayAccessError(failure)
        if result is None:
            raise GatewayAccessError(GatewayFailure.MALFORMED)
        return result

    def parameters(
        self, catalog: Literal["era5", "ifs", "jsnow"]
    ) -> tuple[ParameterCatalogEntry, ...]:
        if catalog not in ("era5", "ifs", "jsnow"):
            raise ValueError("unsupported catalog")
        rows = self._json(
            "GET",
            "/catalogs/parameters/" + catalog,
            TypeAdapter(list[_ParameterResponse]),
        )

        def flag(value: bool | None) -> StreamSetting:
            if value is None:
                return StreamSetting.UNSPECIFIED
            return StreamSetting.ENABLED if value else StreamSetting.DISABLED

        return tuple(
            ParameterCatalogEntry(
                name=row.name,
                forecast_type=row.type,
                level_type=row.level_type,
                level=row.level,
                grid_resolution=row.grid_resolution,
                forecast_enabled=flag(row.forecast_enabled),
                reanalysis_enabled=flag(row.reanalysis_enabled),
            )
            for row in rows
        )

    def subscriptions(self, *, source: str, hru_code: str) -> tuple[Subscription, ...]:
        rows = self._json(
            "GET",
            "/catalogs/subscriptions/",
            TypeAdapter(list[_SubscriptionResponse]),
            params={"source": source, "hru_code": hru_code},
        )
        if any(row.source != source or row.hru_code != hru_code for row in rows):
            raise GatewayAccessError(GatewayFailure.MALFORMED)
        if len({row.parameter_name for row in rows}) != len(rows):
            raise GatewayAccessError(GatewayFailure.MALFORMED)
        return tuple(row.domain() for row in rows)

    def create_subscription(self, row: Subscription) -> None:
        reply = self._json(
            "POST",
            "/catalogs/subscriptions/",
            TypeAdapter(_SubscriptionResponse),
            payload=_subscription_payload(row),
        )
        if reply.domain() != row:
            raise GatewayAccessError(GatewayFailure.MALFORMED)

    def update_subscription(self, before: Subscription, after: Subscription) -> None:
        from urllib.parse import quote

        if (before.source, before.hru_code, before.parameter_name) != (
            after.source,
            after.hru_code,
            after.parameter_name,
        ):
            raise ValueError("subscription identity cannot change")
        old, new = _subscription_payload(before), _subscription_payload(after)
        fields = ("enabled", "forecast_enabled", "reanalysis_enabled")
        delta = {key: new[key] for key in fields if old[key] != new[key]}
        if not delta:
            return
        path = "/catalogs/subscriptions/" + "/".join(
            quote(value, safe="")
            for value in (after.source, after.hru_code, after.parameter_name)
        )
        reply = self._json(
            "PUT", path, TypeAdapter(_SubscriptionResponse), payload=delta
        )
        if reply.domain() != after:
            raise GatewayAccessError(GatewayFailure.MALFORMED)


class _SubscriptionResponse(BaseModel):
    source: StrictStr
    hru_code: StrictStr
    parameter_name: StrictStr
    enabled: StrictBool
    forecast_enabled: StrictBool | None = None
    reanalysis_enabled: StrictBool | None = None

    def domain(self) -> Subscription:
        def setting(value: bool | None) -> StreamSetting:
            if value is None:
                return StreamSetting.UNSPECIFIED
            return StreamSetting.ENABLED if value else StreamSetting.DISABLED

        return Subscription(
            source=self.source,
            hru_code=self.hru_code,
            parameter_name=self.parameter_name,
            enabled=setting(self.enabled),
            forecast_enabled=setting(self.forecast_enabled),
            reanalysis_enabled=setting(self.reanalysis_enabled),
        )


class _ParameterResponse(BaseModel):
    name: StrictStr
    type: Literal["fc", "pf"] | None = None
    level_type: Literal["sfc", "pl", "ml"] | None = None
    level: int | None = None
    grid_resolution: Literal["0p25", "0p1"] | None = None
    forecast_enabled: StrictBool | None = None
    reanalysis_enabled: StrictBool | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class ParameterCatalogEntry:
    name: str
    forecast_type: Literal["fc", "pf"] | None
    level_type: Literal["sfc", "pl", "ml"] | None
    level: int | None
    grid_resolution: Literal["0p25", "0p1"] | None
    forecast_enabled: StreamSetting
    reanalysis_enabled: StreamSetting


def _subscription_payload(row: Subscription) -> dict[str, str | bool | None]:
    def wire(setting: StreamSetting) -> bool | None:
        return (
            None
            if setting == StreamSetting.UNSPECIFIED
            else setting == StreamSetting.ENABLED
        )

    return {
        "source": row.source,
        "hru_code": row.hru_code,
        "parameter_name": row.parameter_name,
        "enabled": wire(row.enabled),
        "forecast_enabled": wire(row.forecast_enabled),
        "reanalysis_enabled": wire(row.reanalysis_enabled),
    }


_Reply = TypeVar("_Reply")
