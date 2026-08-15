"""Plan 159 T1 — the unit boundary, asserted NUMERICALLY. The plan is explicit that
asserting `fi_unit_to_canonical` merely *succeeds* proves nothing: `M3_PER_S` and `MM`
already map, so such a test passes on day one and would pass against a shim that did no
conversion at all. These tests therefore assert on **values**, against an independently
derived figure.
"""

from __future__ import annotations

import math

import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.models.aquacast._units import (
    m3_per_s_to_mm_per_day,
    mm_per_day_to_m3_per_s,
)


class TestAreaAwareDischargeConversion:
    def test_one_mm_per_day_over_86_4_km2_is_exactly_one_m3_per_s(self) -> None:
        """The defining case, derived by hand rather than from the implementation: 1
        mm/day over 86.4 km² = 86.4e6 m² * 1e-3 m / 86400 s = 1.0 m³/s exactly. A shim
        that forgot the area entirely would return 1.0 here too, which is why the second
        case below uses a different area.
        """
        assert mm_per_day_to_m3_per_s(1.0, area_km2=86.4, station="X") == pytest.approx(
            1.0
        )

    def test_conversion_scales_with_area(self) -> None:
        """Ten times the catchment, ten times the flow for the same depth — this is the
        assertion a no-op or area-blind implementation cannot satisfy.
        """
        small = mm_per_day_to_m3_per_s(2.0, area_km2=86.4, station="X")
        large = mm_per_day_to_m3_per_s(2.0, area_km2=864.0, station="X")
        assert small == pytest.approx(2.0)
        assert large == pytest.approx(20.0)

    def test_a_real_swiss_catchment(self) -> None:
        """2695.5 km² (station 2446's catchment, from CAMELS-CH) at 3.2 mm/day: 3.2 *
        2695.5 / 86.4 = 99.83... m³/s.
        """
        got = mm_per_day_to_m3_per_s(3.2, area_km2=2695.5, station="2446")
        assert got == pytest.approx(3.2 * 2695.5 / 86.4)
        assert 99.0 < got < 100.0

    @pytest.mark.parametrize("area", [100.0, 2695.5, 0.5])
    @pytest.mark.parametrize("value", [0.0, 0.1, 3.2, 250.0])
    def test_round_trip_is_lossless(self, value: float, area: float) -> None:
        back = m3_per_s_to_mm_per_day(
            mm_per_day_to_m3_per_s(value, area_km2=area, station="X"),
            area_km2=area,
            station="X",
        )
        assert back == pytest.approx(value)


class TestAreaIsValidatedLoudly:
    """Area is a divisor in one direction, so a bad value must raise rather than
    silently produce inf/nan discharge and hand it to an alerting pipeline.
    """

    @pytest.mark.parametrize(
        "bad",
        [0.0, -1.0, math.inf, -math.inf, math.nan],
        ids=["zero", "negative", "inf", "-inf", "nan"],
    )
    def test_non_positive_or_non_finite_area_raises(self, bad: float) -> None:
        with pytest.raises(ConfigurationError, match="area"):
            mm_per_day_to_m3_per_s(1.0, area_km2=bad, station="2009")
        with pytest.raises(ConfigurationError, match="area"):
            m3_per_s_to_mm_per_day(1.0, area_km2=bad, station="2009")

    @pytest.mark.parametrize("bad", ["100", None, True], ids=["str", "none", "bool"])
    def test_non_numeric_area_raises(self, bad: object) -> None:
        with pytest.raises(ConfigurationError, match="numeric"):
            mm_per_day_to_m3_per_s(1.0, area_km2=bad, station="2009")  # type: ignore[arg-type]

    def test_the_error_names_the_station(self) -> None:
        with pytest.raises(ConfigurationError, match="2446"):
            mm_per_day_to_m3_per_s(1.0, area_km2=0.0, station="2446")
