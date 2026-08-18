"""Typed scalar-extraction helpers.

Polars' `.item()`/`.min()`/`.max()`/`.median()`/`.std()`/`.mean()` return a
wide `PythonLiteral | None` union in the stubs, since the DataFrame's column
dtype isn't tracked statically. These narrow to the numeric type our domain
guarantees at each call site, so pyright strict mode passes without
scattering ad-hoc casts through the statistic modules.
"""

from __future__ import annotations

from datetime import datetime


def as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    raise TypeError(f"expected a datetime scalar, got {type(value)}: {value!r}")


def as_float(value: object) -> float:
    if isinstance(value, bool):
        raise TypeError(f"expected a numeric scalar, got bool: {value!r}")
    if isinstance(value, int | float):
        return float(value)
    raise TypeError(f"expected a numeric scalar, got {type(value)}: {value!r}")


def as_int(value: object) -> int:
    if isinstance(value, bool):
        raise TypeError(f"expected an integer scalar, got bool: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise TypeError(f"expected an integer scalar, got {type(value)}: {value!r}")


def as_str(value: object) -> str:
    if isinstance(value, str):
        return value
    raise TypeError(f"expected a string scalar, got {type(value)}: {value!r}")
