from sapphire_flow.types.enums import ParameterDomain


class TestParameterDomain:
    def test_has_exactly_five_values(self) -> None:
        assert len(ParameterDomain) == 5

    def test_values_match_spec(self) -> None:
        expected = {"river", "weather", "water_quality", "groundwater", "soil"}
        assert {d.value for d in ParameterDomain} == expected
