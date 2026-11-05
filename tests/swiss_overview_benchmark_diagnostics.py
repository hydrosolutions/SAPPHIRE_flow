from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, Literal, TypeGuard, cast

if TYPE_CHECKING:
    from pathlib import Path

_CASES = {"production_quantiles": 10, "stress_members": 3}
_QUANTILES = [0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95]
_INT_KEYS = {
    "stations",
    "horizon_steps",
    "forecast_value_rows",
    "observation_rows",
    "bafu_files",
    "archive_names",
    "archive_enumerations",
    "raw_evidence_files",
    "raw_bytes",
    "gzip_bytes",
}
_NUMBER_KEYS = {"time_step_seconds", "p95_seconds"}
_NUMBER_ARRAY_KEYS = {"route_seconds", "serialization_seconds", "samples_seconds"}
_KEYS = {
    "case",
    "profiled",
    *_INT_KEYS,
    "members",
    "quantiles",
    *_NUMBER_KEYS,
    *_NUMBER_ARRAY_KEYS,
    "queries",
    "latency_target_met",
    "raw_payload_target_met",
}


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite_non_negative(value: object, *, key: str) -> None:
    if not _is_number(value) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{key} must contain finite non-negative numbers")


def _validate(
    record: dict[str, object],
) -> Literal["production_quantiles", "stress_members"]:
    if set(record) != _KEYS:
        raise ValueError("diagnostic keys must match the fixed allowlist")
    case_value = record["case"]
    if not isinstance(case_value, str) or case_value not in _CASES:
        raise ValueError("case must be production_quantiles or stress_members")
    if not isinstance(record["profiled"], bool):
        raise ValueError("profiled must be boolean")
    case = cast("Literal['production_quantiles', 'stress_members']", case_value)
    if case == "production_quantiles" and record["profiled"]:
        raise ValueError("production diagnostics must be unprofiled")
    for key in _INT_KEYS:
        value = record[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{key} must be a non-negative integer")
    for key in _NUMBER_KEYS:
        _finite_non_negative(record[key], key=key)
    count = _CASES[case]
    for key in _NUMBER_ARRAY_KEYS:
        values_value = record[key]
        if not isinstance(values_value, list):
            raise ValueError(f"{key} must contain exactly {count} samples")
        values = cast("list[object]", values_value)
        if len(values) != count:
            raise ValueError(f"{key} must contain exactly {count} samples")
        for value in values:
            _finite_non_negative(value, key=key)
    queries_value = record["queries"]
    if not isinstance(queries_value, list):
        raise ValueError(f"queries must contain exactly {count} non-negative integers")
    queries = cast("list[object]", queries_value)
    if len(queries) != count or any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in queries
    ):
        raise ValueError(f"queries must contain exactly {count} non-negative integers")
    expected_members = None if case == "production_quantiles" else 20
    expected_quantiles = _QUANTILES if case == "production_quantiles" else None
    if (
        record["members"] != expected_members
        or record["quantiles"] != expected_quantiles
    ):
        raise ValueError("members and quantiles must match the benchmark case")
    p95_seconds = cast("float", record["p95_seconds"])
    expected_latency = p95_seconds < 1.0 if case == "production_quantiles" else None
    if record["latency_target_met"] is not expected_latency:
        raise ValueError("latency_target_met does not match p95_seconds")
    raw_bytes = cast("int", record["raw_bytes"])
    expected_payload = raw_bytes <= 393_216
    if record["raw_payload_target_met"] is not expected_payload:
        raise ValueError("raw_payload_target_met does not match raw_bytes")
    return case


def record_then_assert_budgets(record: dict[str, object], target: Path | None) -> None:
    case = _validate(record)
    if target is not None:
        target.write_text(json.dumps(record, indent=2))
    queries = cast("list[int]", record["queries"])
    assert len(set(queries)) == 1, (
        f"query count must remain stable across samples: queries={queries!r}"
    )
    assert queries[0] <= 12, f"query budget exceeded: queries={queries[0]} limit=12"
    assert cast("int", record["raw_bytes"]) <= 393_216, (
        f"payload budget exceeded: raw_bytes={record['raw_bytes']} limit=393216"
    )
    if case == "production_quantiles":
        assert cast("float", record["p95_seconds"]) < 1.0, (
            f"latency budget exceeded: p95_seconds={record['p95_seconds']!r} limit=1.0"
        )
