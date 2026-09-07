import math

from sapphire_flow.adapters.recap_gateway import RECAP_VARIABLES


class TestRecapVariableCatalog:
    def test_source_name_mappings(self) -> None:
        precip = RECAP_VARIABLES["precipitation"]
        assert precip.era5_name == "total_precipitation"
        assert precip.ifs_name == "tp"

        temp = RECAP_VARIABLES["temperature"]
        assert temp.era5_name == "2m_temperature"
        assert temp.ifs_name == "2t"

        assert RECAP_VARIABLES["snow_depth"].snow_name == "hs"
        assert RECAP_VARIABLES["snowmelt"].snow_name == "rof"
        assert RECAP_VARIABLES["swe"].snow_name == "swe"

    def test_canonical_units(self) -> None:
        assert RECAP_VARIABLES["precipitation"].unit == "mm"
        assert RECAP_VARIABLES["temperature"].unit == "°C"
        assert RECAP_VARIABLES["snow_depth"].unit == "cm"
        assert RECAP_VARIABLES["snowmelt"].unit == "mm"
        assert RECAP_VARIABLES["swe"].unit == "mm"

    def test_precipitation_metres_to_mm(self) -> None:
        convert = RECAP_VARIABLES["precipitation"].convert
        assert convert is not None
        assert math.isclose(convert(1.0), 1000.0)

    def test_temperature_kelvin_to_celsius(self) -> None:
        convert = RECAP_VARIABLES["temperature"].convert
        assert convert is not None
        assert math.isclose(convert(300.0), 26.85)

    def test_snow_depth_metres_to_cm(self) -> None:
        """Plan 219: the Gateway serves `hs` in metres, our canonical unit is
        cm. This factor is OWNER-SUPPLIED, not measured — HRU 12300 has
        effectively no snow in any season, so no magnitude comparison could
        confirm it there. Locked here so a change is deliberate: an m/cm
        mix-up is a 100x error that 12300 would never reveal."""
        convert = RECAP_VARIABLES["snow_depth"].convert
        assert convert is not None
        assert math.isclose(convert(1.5), 150.0)

    def test_snowmelt_and_swe_are_grounded_identities(self) -> None:
        """Plan 219: `rof` and `swe` arrive in mm, matching our canonical unit.
        Stated as an explicit identity rather than left as `convert=None` —
        None is the "ungrounded, do not trust" sentinel, and these are now
        grounded."""
        for name in ("snowmelt", "swe"):
            convert = RECAP_VARIABLES[name].convert
            assert convert is not None, name
            assert math.isclose(convert(2.5), 2.5), name
