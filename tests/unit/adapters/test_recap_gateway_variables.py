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

    def test_snow_vars_have_no_committed_factor(self) -> None:
        # Snow source-unit magnitudes are UNCONFIRMED → deferred to Plan 082.
        for name in ("snow_depth", "snowmelt", "swe"):
            assert RECAP_VARIABLES[name].convert is None
