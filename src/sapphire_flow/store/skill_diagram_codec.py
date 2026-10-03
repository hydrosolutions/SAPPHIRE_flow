from __future__ import annotations

import math
from typing import Literal, cast

from sapphire_flow.exceptions import SkillDiagramEncodingError

_RELIABILITY = frozenset({"bins", "forecast_freq", "sample_counts", "observed_freq"})
_ROC = frozenset(
    {"thresholds", "hit_rate", "false_alarm_rate", "n_events", "n_non_events"}
)
_FIELDS = _RELIABILITY | _ROC
_Path = tuple[object, ...]
_Nonfinite = tuple[_Path, str, float]


def _plain_nonfinite(data: object) -> list[_Nonfinite] | None:
    pending: list[tuple[object, _Path, str, Literal["enter", "exit"]]] = [
        (data, (), "$", "enter")
    ]
    active: set[int] = set()
    found: list[_Nonfinite] = []
    while pending:
        value, path, label, action = pending.pop()
        if action == "exit":
            active.remove(id(value))
            continue
        if type(value) in (dict, list, tuple):
            if id(value) in active:
                return None
            active.add(id(value))
            pending.append((value, path, label, "exit"))
            if type(value) is dict:
                items = list(cast("dict[object, object]", value).items())
                for index, (key, item) in reversed(list(enumerate(items))):
                    if key is not None and not isinstance(key, (str, int, float)):
                        return None
                    field = (
                        key
                        if not path and type(key) is str and key in _FIELDS
                        else f"key#{index}"
                    )
                    pending.append(
                        (item, (*path, key)[:3], f"{label}[{field}]"[:128], "enter")
                    )
            else:
                items_list = cast("list[object] | tuple[object, ...]", value)
                for index in reversed(range(len(items_list))):
                    pending.append(
                        (
                            items_list[index],
                            (*path, index)[:3],
                            f"{label}[{index}]"[:128],
                            "enter",
                        )
                    )
        elif value is None or isinstance(value, (str, int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                found.append((path, label, value))
        else:
            return None
    return found


def _finite(value: object) -> bool:
    return not isinstance(value, bool) and (
        isinstance(value, int) or isinstance(value, float) and math.isfinite(value)
    )


def _support(value: object) -> bool:
    if not _finite(value):
        return False
    number = cast("int | float", value)
    return number >= 0 and (isinstance(number, int) or number.is_integer())


def _rate(value: object) -> bool:
    return (
        value is None
        or _finite(value)
        or isinstance(value, float)
        and math.isnan(value)
    )


def _eligible(kind: str, data: dict[str, object]) -> set[tuple[str, int]]:
    keys: frozenset[str] = (
        _RELIABILITY
        if kind == "reliability"
        else _ROC
        if kind == "roc"
        else frozenset()
    )
    if not keys or any(type(key) is not str for key in data) or data.keys() != keys:
        return set()
    sequence_keys = (
        keys if kind == "reliability" else keys - {"n_events", "n_non_events"}
    )
    if any(type(data[key]) not in (list, tuple) for key in sequence_keys):
        return set()
    arrays = {
        key: cast("list[object] | tuple[object, ...]", data[key])
        for key in sequence_keys
    }
    lengths = {len(values) for values in arrays.values()}
    if len(lengths) != 1 or 0 in lengths:
        return set()
    if kind == "reliability":
        if not all(
            _finite(v) for key in ("bins", "forecast_freq") for v in arrays[key]
        ):
            return set()
        if not all(_support(v) for v in arrays["sample_counts"]) or not all(
            _rate(v) for v in arrays["observed_freq"]
        ):
            return set()
        return {
            ("observed_freq", i)
            for i, count in enumerate(arrays["sample_counts"])
            if count == 0
        }
    if not all(_finite(v) for v in arrays["thresholds"]):
        return set()
    if not all(_support(data[key]) for key in ("n_events", "n_non_events")):
        return set()
    if not all(
        _rate(v) for key in ("hit_rate", "false_alarm_rate") for v in arrays[key]
    ):
        return set()
    return {
        (key, i)
        for key, support in (
            ("hit_rate", "n_events"),
            ("false_alarm_rate", "n_non_events"),
        )
        if data[support] == 0
        for i in range(len(arrays[key]))
    }


def _replace(
    data: dict[str, object], positions: set[tuple[str, int]], replacement: float | None
) -> dict[str, object]:
    if not positions:
        return data
    result = data.copy()
    for key in {key for key, _ in positions}:
        original = cast("list[object] | tuple[object, ...]", data[key])
        values = list(original)
        for field, index in positions:
            if field == key:
                values[index] = replacement
        result[key] = tuple(values) if type(original) is tuple else values
    return result


def encode_skill_diagram_data(kind: str, data: dict[str, object]) -> dict[str, object]:
    nonfinite = _plain_nonfinite(data)
    if nonfinite is None or not nonfinite:
        return data
    eligible = _eligible(kind, data)
    positions: set[tuple[str, int]] = set()
    for path, label, value in nonfinite:
        if math.isnan(value) and path in eligible:
            positions.add(cast("tuple[str, int]", path))
            continue
        name = kind if kind in ("reliability", "roc", "rank_histogram") else "other"
        reason = "unsupported_nan" if math.isnan(value) else "unsupported_infinity"
        raise SkillDiagramEncodingError(
            f"skill diagram {name} at {label}: {reason}"[:192]
        )
    return _replace(data, positions, None)


def decode_skill_diagram_data(kind: str, data: dict[str, object]) -> dict[str, object]:
    if _plain_nonfinite(data) is None:
        return data
    eligible = _eligible(kind, data)
    positions = {
        (key, index)
        for key, index in eligible
        if cast("list[object] | tuple[object, ...]", data[key])[index] is None
    }
    return _replace(data, positions, float("nan"))
