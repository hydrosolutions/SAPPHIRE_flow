from __future__ import annotations

from sapphire_flow.types.forcing_sources import SOURCE_ATTRIBUTIONS, ForcingSource

# Plan 146 D3: the owner-confirmed licence string recorded in the plan. An
# equality assertion (not mere membership) is the only mechanism that
# actually rejects a placeholder value — see D3 "Test scope".
_EXPECTED_SNOW_ATTRIBUTION = "SnowMapper Operational (MIT License, 2026)"

# The persisted literal `_SNOW_SOURCE` in adapters/recap_gateway.py. Compared
# by value (not imported) so this test stays a pure provenance-layer check.
_PERSISTED_SNOW_SOURCE_LITERAL = "recap_snow_reanalysis"


class TestSourceAttributionsCompleteness:
    def test_every_forcing_source_has_an_attribution(self) -> None:
        missing = [
            member for member in ForcingSource if member not in SOURCE_ATTRIBUTIONS
        ]
        assert missing == []


class TestRecapSnowReanalysisAttribution:
    def test_attribution_equals_owner_confirmed_licence_string(self) -> None:
        assert (
            SOURCE_ATTRIBUTIONS[ForcingSource.RECAP_SNOW_REANALYSIS]
            == _EXPECTED_SNOW_ATTRIBUTION
        )

    def test_value_round_trips_to_persisted_snow_source_literal(self) -> None:
        assert (
            ForcingSource.RECAP_SNOW_REANALYSIS.value == _PERSISTED_SNOW_SOURCE_LITERAL
        )
