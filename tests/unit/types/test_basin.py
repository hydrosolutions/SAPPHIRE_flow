"""Codex review (Plan 120 fixer round, major): a required static feature
whose value is None/NaN must count as missing, not just an absent key."""

from __future__ import annotations

import math

from sapphire_flow.types.basin import is_missing_static_value, non_null_static_keys


class TestIsMissingStaticValue:
    def test_none_is_missing(self) -> None:
        assert is_missing_static_value(None) is True

    def test_nan_is_missing(self) -> None:
        assert is_missing_static_value(float("nan")) is True

    def test_zero_is_not_missing(self) -> None:
        assert is_missing_static_value(0.0) is False

    def test_populated_value_is_not_missing(self) -> None:
        assert is_missing_static_value(1200.0) is False


class TestNonNullStaticKeys:
    def test_none_attributes_returns_empty(self) -> None:
        assert non_null_static_keys(None) == frozenset()

    def test_empty_attributes_returns_empty(self) -> None:
        assert non_null_static_keys({}) == frozenset()

    def test_excludes_none_valued_keys(self) -> None:
        attrs = {"elevation_mean": None, "area_km2": 42.0}
        assert non_null_static_keys(attrs) == frozenset({"area_km2"})

    def test_excludes_nan_valued_keys(self) -> None:
        attrs = {"elevation_mean": math.nan, "area_km2": 42.0}
        assert non_null_static_keys(attrs) == frozenset({"area_km2"})

    def test_all_populated_returns_all_keys(self) -> None:
        attrs = {"elevation_mean": 1200.0, "area_km2": 42.0}
        assert non_null_static_keys(attrs) == frozenset({"elevation_mean", "area_km2"})
