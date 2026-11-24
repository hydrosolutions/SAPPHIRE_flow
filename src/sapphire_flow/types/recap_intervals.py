from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.operational_weather import OperationalWeatherSource

RADIATION_PARAMETERS = frozenset({"solar_net_radiation", "thermal_net_radiation"})
RADIATION_CONTRACT_REVISION = "rad-int-v1"


class GatewayIntervalContract(StrEnum):
    UNQUALIFIED = "unqualified"
    NORMALIZED_START_V1 = "normalized_start_v1"
    GATEWAY_NATIVE_START_V1 = "gateway_native_start_v1"


@dataclass(frozen=True, kw_only=True, slots=True)
class GatewayIntervalQualification:
    contract: GatewayIntervalContract = GatewayIntervalContract.UNQUALIFIED
    revision: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.contract, GatewayIntervalContract):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("gateway interval contract must be typed")
        if self.contract is not GatewayIntervalContract.UNQUALIFIED:
            if (
                not isinstance(self.revision, str)
                or not self.revision
                or self.revision != self.revision.strip()
            ):
                raise ValueError(
                    "qualified start intervals require a qualification revision"
                )
            if (
                self.contract is GatewayIntervalContract.GATEWAY_NATIVE_START_V1
                and re.fullmatch(r"gateway-docs:[0-9a-f]{40}", self.revision) is None
            ):
                raise ValueError(
                    "native intervals require revision gateway-docs:<40-hex-commit>"
                )
        elif self.revision is not None:
            raise ValueError(
                "unqualified intervals cannot declare a normalization revision"
            )

    def require_radiation(self) -> None:
        if self.contract is GatewayIntervalContract.UNQUALIFIED:
            raise ValueError("radiation requires qualified start intervals")


UNQUALIFIED_INTERVALS = GatewayIntervalQualification()


def qualified_ifs_horizon_hours(source_run: UtcDatetime) -> int:
    if (
        source_run.utcoffset() != timedelta(0)
        or source_run.hour % 6
        or any((source_run.minute, source_run.second, source_run.microsecond))
    ):
        raise ValueError("IFS horizon requires a six-hour UTC source cycle")
    return 360 if source_run.hour in (0, 12) else 72


def qualified_interval_duration(
    start: UtcDatetime,
    source_run: UtcDatetime,
    source: Literal["era5_land", "ifs"] | OperationalWeatherSource,
) -> timedelta:
    if any(t.utcoffset() != timedelta(0) for t in (start, source_run)):
        raise ValueError("interval timestamps must be UTC")
    if any((start.minute, start.second, start.microsecond)):
        raise ValueError("interval start is off the source grid")
    if source == "era5_land":
        if (
            any(
                (
                    source_run.hour,
                    source_run.minute,
                    source_run.second,
                    source_run.microsecond,
                )
            )
            or source_run.date() != start.date()
        ):
            raise ValueError("ERA5 interval requires its source UTC day")
        return timedelta(hours=1)
    if (
        source != "ifs"
        or source_run.hour % 6
        or any((source_run.minute, source_run.second, source_run.microsecond))
    ):
        raise ValueError("IFS interval requires a six-hour source cycle")
    lead = (start - source_run).total_seconds() / 3600
    step = 3 if lead < 144 else 6
    maximum = qualified_ifs_horizon_hours(source_run)
    if lead < 0 or lead % step or lead + step > maximum:
        raise ValueError("interval exceeds the qualified source schedule")
    return timedelta(hours=step)


def radiation_mean_flux(
    energy: float,
    *,
    start: UtcDatetime,
    source_run: UtcDatetime,
    source: Literal["era5_land", "ifs"] | OperationalWeatherSource,
    qualification: GatewayIntervalQualification,
) -> float:
    qualification.require_radiation()
    if isinstance(energy, bool) or not math.isfinite(energy):
        raise ValueError("radiation energy must be finite")
    return (
        energy / qualified_interval_duration(start, source_run, source).total_seconds()
    )
