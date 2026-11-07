from __future__ import annotations

import hashlib
import json
import math
from typing import TYPE_CHECKING

from sapphire_flow.exceptions import StoreError
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.operational_weather import FutureWeatherReceipt, require_utc

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.operational_weather import FutureWeatherScope
    from sapphire_flow.types.weather import WeatherForecastRecord


class WeatherProofError(StoreError):
    pass


def _scope_digest(
    records: Sequence[WeatherForecastRecord],
    *,
    scope: FutureWeatherScope,
    cycle_time: UtcDatetime,
) -> tuple[str, int]:
    require_utc(cycle_time)
    expected = {
        (requirement.parameter, valid_time, member_id)
        for requirement in scope.requirements
        for valid_time in requirement.valid_times
        for member_id in requirement.member_ids
    }
    selected: dict[tuple[str, UtcDatetime, int | None], tuple[object, ...]] = {}
    for record in records:
        if (
            record.station_id != scope.station_id
            or record.nwp_source != scope.source
            or record.cycle_time != cycle_time
        ):
            raise WeatherProofError("future archive rows do not match receipt identity")
        key = (record.parameter, record.valid_time, record.member_id)
        if key not in expected:
            continue
        require_utc(record.valid_time)
        if key in selected:
            raise WeatherProofError(
                "future archive contains duplicate required identities"
            )
        if (
            record.spatial_type is not SpatialRepresentation.BASIN_AVERAGE
            or record.band_id is not None
            or record.is_gap
            or record.gap_status is not None
            or isinstance(record.value, bool)
            or not math.isfinite(record.value)
        ):
            raise WeatherProofError(
                "future archive required row has unsupported value or support"
            )
        selected[key] = (
            str(record.station_id),
            record.nwp_source,
            record.cycle_time.isoformat(),
            record.valid_time.isoformat(),
            record.parameter,
            record.spatial_type.value,
            record.band_id,
            record.member_id,
            float(record.value).hex(),
        )
    if selected.keys() != expected:
        raise WeatherProofError("future archive does not cover complete receipt scope")
    rows = sorted(selected.values(), key=lambda row: json.dumps(row))
    encoded = json.dumps(rows, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest(), len(rows)


def prove_future_receipt(
    *,
    scope: FutureWeatherScope,
    cycle_time: UtcDatetime,
    validated_at: UtcDatetime,
    fetched: Sequence[WeatherForecastRecord],
    readback: Sequence[WeatherForecastRecord],
) -> FutureWeatherReceipt:
    expected = _scope_digest(fetched, scope=scope, cycle_time=cycle_time)
    actual = _scope_digest(readback, scope=scope, cycle_time=cycle_time)
    if actual != expected:
        raise WeatherProofError("future archive readback differs from fetched values")
    return FutureWeatherReceipt(
        scope=scope,
        cycle_time=cycle_time,
        validated_at=validated_at,
        row_sha256=actual[0],
        row_count=actual[1],
    )


def verify_future_receipt(
    receipt: FutureWeatherReceipt,
    records: Sequence[WeatherForecastRecord],
) -> None:
    actual = _scope_digest(records, scope=receipt.scope, cycle_time=receipt.cycle_time)
    if actual != (receipt.row_sha256, receipt.row_count):
        raise WeatherProofError("future archive no longer matches its complete receipt")
