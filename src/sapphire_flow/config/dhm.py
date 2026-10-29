from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sapphire_flow.exceptions import ConfigurationError


@dataclass(frozen=True, kw_only=True, slots=True)
class DhmBinding:
    network: Literal["dhm"]
    station_code: str
    api_station_id: int
    level_reference: Literal["gauge_zero", "masl", "unknown"]

    def __post_init__(self) -> None:
        if self.network != "dhm" or not self.station_code.strip():
            raise ValueError("DHM binding requires network dhm and a station code")
        if type(self.api_station_id) is not int or self.api_station_id <= 0:
            raise ValueError("DHM API station ID must be a positive integer")
        if self.level_reference not in ("gauge_zero", "masl", "unknown"):
            raise ValueError("Invalid DHM level reference")


@dataclass(frozen=True, kw_only=True, slots=True)
class DhmConfig:
    endpoint: str
    bindings: tuple[DhmBinding, ...]
    max_requests_per_batch: int
    max_bytes_per_batch: int
    max_batch_seconds: float
    timeout_s: float = 30.0
    page_size: int = 100
    window_hours: float = 24.0
    max_pages_per_station: int = 100
    min_request_interval_s: float = 5.0

    def __post_init__(self) -> None:
        try:
            url = httpx.URL(self.endpoint)
        except httpx.InvalidURL:
            raise ValueError("Invalid DHM endpoint") from None
        if (
            url.scheme != "https"
            or not url.host
            or url.userinfo
            or url.query
            or url.fragment
        ):
            raise ValueError("DHM endpoint requires HTTPS without credentials or query")
        for limit in (
            self.timeout_s,
            self.window_hours,
            self.min_request_interval_s,
            self.max_batch_seconds,
        ):
            if (
                type(limit) not in (int, float)
                or not math.isfinite(limit)
                or limit <= 0
            ):
                raise ValueError("DHM time limits must be positive and finite")
        if timedelta(hours=self.window_hours) <= timedelta(0):
            raise ValueError("DHM window must span at least one microsecond")
        for count in (
            self.page_size,
            self.max_pages_per_station,
            self.max_requests_per_batch,
            self.max_bytes_per_batch,
        ):
            if type(count) is not int or count <= 0:
                raise ValueError("DHM page limits must be positive integers")
        if (
            self.page_size > 100
            or self.min_request_interval_s < 5
            or self.timeout_s > 30
        ):
            raise ValueError(
                "DHM requires page size <=100, pacing >=5s and timeout <=30s"
            )
        keys = [(b.network, b.station_code) for b in self.bindings]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate DHM binding key")


class _BindingPayload(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    network: Literal["dhm"]
    station_code: str = Field(min_length=1)
    api_station_id: int = Field(gt=0)
    level_reference: Literal["gauge_zero", "masl", "unknown"]


class _ConfigPayload(BaseModel):
    model_config = ConfigDict(strict=True)
    endpoint: str
    bindings: list[_BindingPayload]
    max_requests_per_batch: int = Field(gt=0)
    max_bytes_per_batch: int = Field(gt=0)
    max_batch_seconds: float = Field(gt=0, allow_inf_nan=False)
    timeout_s: float = Field(default=30.0, gt=0, le=30, allow_inf_nan=False)
    page_size: int = Field(default=100, gt=0, le=100)
    window_hours: float = Field(default=24.0, gt=0, allow_inf_nan=False)
    max_pages_per_station: int = Field(default=100, gt=0)
    min_request_interval_s: float = Field(default=5.0, ge=5, allow_inf_nan=False)


def parse_dhm_config(raw: object) -> DhmConfig:
    try:
        parsed = _ConfigPayload.model_validate(raw)
        return DhmConfig(
            endpoint=parsed.endpoint,
            bindings=tuple(
                DhmBinding(
                    network=b.network,
                    station_code=b.station_code,
                    api_station_id=b.api_station_id,
                    level_reference=b.level_reference,
                )
                for b in parsed.bindings
            ),
            max_requests_per_batch=parsed.max_requests_per_batch,
            max_bytes_per_batch=parsed.max_bytes_per_batch,
            max_batch_seconds=parsed.max_batch_seconds,
            timeout_s=parsed.timeout_s,
            page_size=parsed.page_size,
            window_hours=parsed.window_hours,
            max_pages_per_station=parsed.max_pages_per_station,
            min_request_interval_s=parsed.min_request_interval_s,
        )
    except (ValidationError, ValueError, OverflowError):
        # Validation errors can embed the input, including an endpoint credential.
        raise ConfigurationError("Invalid DHM adapter configuration") from None
