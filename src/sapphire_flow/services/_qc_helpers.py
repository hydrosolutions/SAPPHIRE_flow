from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import timedelta


def merge_thresholds(
    base_thresholds: dict[str, float],
    overrides: list[Any],
    station_id: Any,
    rule_id: str,
    parameter: str,
    time_step: timedelta,
) -> dict[str, float]:
    result = dict(base_thresholds)
    for o in overrides:
        if (
            o.station_id == station_id
            and o.rule_id == rule_id
            and o.parameter == parameter
            and o.time_step == time_step
        ):
            for k, v in o.thresholds.items():
                if v is not None:
                    result[k] = v
    return result
