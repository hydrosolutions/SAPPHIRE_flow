from sapphire_flow.types.enums import (
    GaugingStatus,
    NwpCycleSource,
    ParameterDomain,
)


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


class TestNwpCycleSource:
    """epic-088 M4: RUNOFF_ONLY joins PRIMARY/FALLBACK as a provenance source."""

    def test_runoff_only_member_exists(self) -> None:
        assert hasattr(NwpCycleSource, "RUNOFF_ONLY")

    def test_runoff_only_value(self) -> None:
        assert NwpCycleSource.RUNOFF_ONLY.value == "runoff_only"

    def test_runoff_only_round_trips_from_string(self) -> None:
        assert NwpCycleSource("runoff_only") is NwpCycleSource.RUNOFF_ONLY

    def test_primary_and_fallback_unchanged(self) -> None:
        assert NwpCycleSource.PRIMARY.value == "primary"
        assert NwpCycleSource.FALLBACK.value == "fallback"

    def test_has_exactly_three_sources(self) -> None:
        assert {s.value for s in NwpCycleSource} == {
            "primary",
            "fallback",
            "runoff_only",
        }
