"""Plan 240 (M-A12) T2 — the comparison driver's D6 support ASSERTION.

⛔ The contract under test is that the driver FAILS CLOSED. `build_cells`
silently drops any (station, season) that lacks either side, so GraphCast's
gaps and IFS's gaps can leave the two products ranked on different support
— and a cell missing on one side is indistinguishable, in the printed
report, from a difference in model skill. Intersecting is available only
behind an explicit opt-in; it is never the default and never a fallback.

No network, no data files — these exercise the pure support resolver.
"""

from __future__ import annotations

import pytest

from scripts.dhm_precip.graphcast_ifs_compare import (
    SupportPolicy,
    resolve_shared_support,
)
from scripts.dhm_precip.ifs_event_timing import EventTimingInputError


class TestResolveSharedSupport:
    def test_identical_support_is_returned_unchanged(self) -> None:
        support = {("Dhangadhi", 2022), ("Jumla", 2022)}
        assert (
            resolve_shared_support(
                support_graphcast=set(support), support_ifs=set(support)
            )
            == support
        )

    def test_refuses_to_rank_when_graphcast_has_a_cell_ifs_lacks(self) -> None:
        with pytest.raises(EventTimingInputError, match="REFUSES to rank"):
            resolve_shared_support(
                support_graphcast={("Jumla", 2022), ("Dhangadhi", 2022)},
                support_ifs={("Jumla", 2022)},
            )

    def test_refuses_to_rank_when_ifs_has_a_cell_graphcast_lacks(self) -> None:
        with pytest.raises(EventTimingInputError, match="REFUSES to rank"):
            resolve_shared_support(
                support_graphcast={("Jumla", 2022)},
                support_ifs={("Jumla", 2022), ("Dhangadhi", 2022)},
            )

    def test_the_refusal_names_the_differing_cells(self) -> None:
        """A refusal that does not say WHICH cells differ cannot be acted on."""
        with pytest.raises(EventTimingInputError) as excinfo:
            resolve_shared_support(
                support_graphcast={("Jumla", 2022), ("Ghorepani", 2024)},
                support_ifs={("Jumla", 2022), ("Rajbiraj Airport", 2023)},
            )
        message = str(excinfo.value)
        assert "Ghorepani" in message
        assert "Rajbiraj Airport" in message

    def test_intersecting_requires_the_explicit_opt_in(self) -> None:
        """The SAME mismatch that refuses by default succeeds only when the
        caller asks for the intersection on the record."""
        graphcast = {("Jumla", 2022), ("Ghorepani", 2024)}
        ifs = {("Jumla", 2022)}
        with pytest.raises(EventTimingInputError):
            resolve_shared_support(support_graphcast=graphcast, support_ifs=ifs)
        assert resolve_shared_support(
            support_graphcast=graphcast,
            support_ifs=ifs,
            policy=SupportPolicy.ALLOW_INTERSECTION,
        ) == {("Jumla", 2022)}

    def test_the_default_policy_is_require_identical(self) -> None:
        """⛔ Pins the DEFAULT itself — a change of default is the whole
        defect, and would otherwise pass every other test here."""
        with pytest.raises(EventTimingInputError, match="REFUSES to rank"):
            resolve_shared_support(
                support_graphcast={("Jumla", 2022), ("Ghorepani", 2024)},
                support_ifs={("Jumla", 2022)},
                policy=SupportPolicy.REQUIRE_IDENTICAL,
            )

    def test_an_empty_intersection_is_refused_even_under_the_opt_in(self) -> None:
        with pytest.raises(EventTimingInputError, match="nothing to"):
            resolve_shared_support(
                support_graphcast={("Jumla", 2022)},
                support_ifs={("Ghorepani", 2024)},
                policy=SupportPolicy.ALLOW_INTERSECTION,
            )

    @pytest.mark.parametrize("policy", list(SupportPolicy))
    def test_an_empty_side_is_refused_under_every_policy(
        self, policy: SupportPolicy
    ) -> None:
        with pytest.raises(EventTimingInputError, match="NO \\(station, season\\)"):
            resolve_shared_support(
                support_graphcast=set(),
                support_ifs={("Jumla", 2022)},
                policy=policy,
            )
