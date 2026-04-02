from sapphire_flow.types.enums import GaugingStatus, ParameterDomain


class TestParameterDomain:
    def test_has_exactly_five_values(self) -> None:
        assert len(ParameterDomain) == 5

    def test_values_match_spec(self) -> None:
        expected = {"river", "weather", "water_quality", "groundwater", "soil"}
        assert {d.value for d in ParameterDomain} == expected


class TestGaugingStatus:
    def test_has_exactly_three_values(self) -> None:
        assert len(GaugingStatus) == 3

    def test_values_match_spec(self) -> None:
        expected = {"gauged", "ungauged", "calculated"}
        assert {s.value for s in GaugingStatus} == expected

    def test_round_trips_from_string(self) -> None:
        for member in GaugingStatus:
            assert GaugingStatus(member.value) is member
