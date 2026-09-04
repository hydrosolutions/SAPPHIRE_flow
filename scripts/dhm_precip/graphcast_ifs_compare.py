"""Plan 240 (M-A12) T2 — run the UNCHANGED `ifs_event_timing` estimator
against GraphCast and against IFS control on the SAME four JJAS seasons,
and print both reports side by side.

⛔ PROPORTIONALITY: this module contains no statistic of its own. Every
number in its output comes from `ifs_event_timing.build_cells` and
`ifs_event_timing.report`, called twice with a different `tigge_root` (D3)
— once for GraphCast (`graphcast_acquire`'s output tree), once for IFS
control (the production `data/dhm_precip/tigge` tree). The only logic
here is D6's support ASSERTION: `build_cells` silently drops any
(station, season) that lacks BOTH sides, so GraphCast's gaps and IFS's
gaps can leave the two products with different station-season support —
ranking on that would let a missing cell on one side masquerade as a
model difference.

⛔ D6 therefore FAILS CLOSED: if the two products' actual (station, season)
support is not IDENTICAL, this module REFUSES to rank them and names the
differing cells. Intersecting instead is available only behind the explicit
`--allow-support-intersection` opt-in, which is never the default — a
silent intersection is exactly the failure D6 exists to prevent, because
the resulting numbers are computed on a support neither product was
retrieved for and nothing in the printed report says so.

D7 — GraphCast here is GFS-initialised; IFS control is ECMWF-initialised.
This module states that confound in its own header; it answers "which
archived operational product scores higher against these gauges", never
"AI vs physics" (Plan 240).

D2 — the archive changed model version mid-record: 2022-2024 are GraphCast
v1 (`version` "1_2023-10-14"), 2025 is v3 ("3_2025-02-20"). ⛔ The two are
NEVER pooled: this driver reads the version each requested season recorded
and REFUSES to run if the requested seasons span more than one, so a
version group is always run on its own. Because the version is perfectly
confounded with the year (v3 exists only in 2025), the headline is each
group's skill RELATIVE TO IFS on the SAME seasons — IFS meets the same
weather, which partly controls for it — never the raw GraphCast numbers.
"""

from __future__ import annotations

import argparse
from enum import Enum, auto
from pathlib import Path

from scripts.dhm_precip.graphcast_acquire import (
    DEFAULT_GRAPHCAST_ROOT,
    DEFAULT_GRAPHCAST_SEASONS,
    EXPECTED_INIT_MODEL,
    EXPECTED_MODEL_NAME,
    GRAPHCAST_PREFIX,
    GraphcastAcquisitionError,
    season_versions,
)
from scripts.dhm_precip.ifs_event_timing import (
    CONTINUOUS_DAY_LEADS,
    DEFAULT_DECLUSTER_H,
    DEFAULT_EVENT_QUANTILE,
    DEFAULT_INIT_HOUR,
    DEFAULT_MISS_FRACTION,
    DEFAULT_NULL_SHIFT_DAYS,
    DEFAULT_SEARCH_WINDOWS_H,
    DEFAULT_TIGGE_ROOT,
    MIN_HARMONIC_AMPLITUDE,
    EventTimingInputError,
    EventTimingParams,
    StationSeasonCell,
    build_cells,
    consumed_input_digests,
    report,
)

_D7_CONFOUND = (
    f"D7 confound: GraphCast here is {EXPECTED_MODEL_NAME} initialised from "
    f"{EXPECTED_INIT_MODEL} ({GRAPHCAST_PREFIX}); IFS control below is initialised "
    "from its own ECMWF analysis. This answers 'which archived operational product "
    "scores higher against these gauges', NOT 'AI vs physics'."
)

_D2_YEAR_CONFOUND = (
    "⛔ D2 confound: GraphCast model version is PERFECTLY confounded with year in "
    "this archive (v3 exists only from 2025), so a raw v1-vs-v3 difference could be "
    "that year's weather. Read each group only against the IFS control below it, "
    "which met the same weather. ⛔ A single season supports NO claim of model "
    "improvement — interannual variability on this track is large (the measured IFS "
    "offset spans ~9 h across six seasons)."
)


def _resolve_version(graphcast_root: Path, seasons: tuple[int, ...]) -> str:
    """D2 — one version group per run. ⛔ Refuses to pool two model versions
    into one number."""
    by_season = season_versions(out_root=graphcast_root, seasons=seasons)
    distinct = sorted({v for v in by_season.values() if v is not None})
    if len(distinct) != 1:
        raise GraphcastAcquisitionError(
            f"requested seasons {list(seasons)} span GraphCast versions "
            f"{by_season} — run ONE version group per invocation; two model "
            "versions must never be pooled (D2)"
        )
    return distinct[0]


class SupportPolicy(Enum):
    """D6 — what to do when the two products' (station, season) support is
    not identical. ⛔ `REQUIRE_IDENTICAL` is the only default; the other
    member exists so that a deliberate, argued exception is visible in the
    command line and in the printed report rather than applied silently."""

    REQUIRE_IDENTICAL = auto()
    ALLOW_INTERSECTION = auto()


def _support(cells: list[StationSeasonCell]) -> set[tuple[str, int]]:
    return {(str(cell.station), cell.year) for cell in cells}


def _restrict(
    cells: list[StationSeasonCell], support: set[tuple[str, int]]
) -> list[StationSeasonCell]:
    return [cell for cell in cells if (str(cell.station), cell.year) in support]


def resolve_shared_support(
    *,
    support_graphcast: set[tuple[str, int]],
    support_ifs: set[tuple[str, int]],
    policy: SupportPolicy = SupportPolicy.REQUIRE_IDENTICAL,
) -> set[tuple[str, int]]:
    """D6's assertion, BEFORE any ranking. Returns the support both reports
    are restricted to.

    ⛔ Fails closed. Under the default `REQUIRE_IDENTICAL` any asymmetry —
    a cell present for one product and not the other — raises, naming the
    differing cells, because a difference in what was searched is
    indistinguishable in the output from a difference in model skill.
    `ALLOW_INTERSECTION` is an explicit opt-in, never a fallback: it still
    refuses an empty intersection."""
    if not support_graphcast or not support_ifs:
        raise EventTimingInputError(
            "one side has NO (station, season) cell at all "
            f"(GraphCast {len(support_graphcast)}, IFS {len(support_ifs)}) — "
            "there is nothing to compare (D6)"
        )
    only_graphcast = sorted(support_graphcast - support_ifs)
    only_ifs = sorted(support_ifs - support_graphcast)
    if not only_graphcast and not only_ifs:
        return set(support_graphcast)
    detail = (
        f"only in GraphCast ({len(only_graphcast)}): {only_graphcast}; "
        f"only in IFS control ({len(only_ifs)}): {only_ifs}"
    )
    if policy is SupportPolicy.REQUIRE_IDENTICAL:
        raise EventTimingInputError(
            "D6 REFUSES to rank: the two products do not share identical "
            f"(station, season) support — {detail}. A cell missing on one "
            "side masquerades as a model difference. Re-retrieve the missing "
            "cells, or pass --allow-support-intersection to compare on the "
            "intersection deliberately and on the record."
        )
    common = support_graphcast & support_ifs
    if not common:
        raise EventTimingInputError(
            "no (station, season) cell is present on BOTH sides — nothing to "
            f"compare (D6); {detail}"
        )
    return common


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "M-A12 (Plan 240) — GraphCast vs IFS control, same seasons, "
            "same station-season support, through the unchanged M-A11c estimator"
        )
    )
    parser.add_argument(
        "--seasons", type=int, nargs="+", default=list(DEFAULT_GRAPHCAST_SEASONS)
    )
    parser.add_argument(
        "--leads", type=int, nargs="+", default=list(CONTINUOUS_DAY_LEADS)
    )
    parser.add_argument("--graphcast-root", type=Path, default=DEFAULT_GRAPHCAST_ROOT)
    parser.add_argument("--tigge-root", type=Path, default=DEFAULT_TIGGE_ROOT)
    parser.add_argument(
        "--allow-support-intersection",
        action="store_true",
        help=(
            "⛔ D6 opt-out, NOT a default: compare on the INTERSECTION of the "
            "two products' (station, season) support instead of refusing an "
            "asymmetry. Only for a deliberate, argued exception — the "
            "resulting numbers rest on a support neither product was "
            "retrieved for"
        ),
    )
    args = parser.parse_args()
    policy = (
        SupportPolicy.ALLOW_INTERSECTION
        if args.allow_support_intersection
        else SupportPolicy.REQUIRE_IDENTICAL
    )

    seasons = tuple(sorted(set(args.seasons)))
    version = _resolve_version(args.graphcast_root, seasons)
    leads = tuple(args.leads)
    windows = tuple(DEFAULT_SEARCH_WINDOWS_H)
    base = EventTimingParams(
        search_window_h=windows[0],
        event_quantile=DEFAULT_EVENT_QUANTILE,
        decluster_h=DEFAULT_DECLUSTER_H,
        miss_fraction=DEFAULT_MISS_FRACTION,
    )

    graphcast_cells = build_cells(
        tigge_root=args.graphcast_root,
        seasons=seasons,
        leads=leads,
        init_hour=DEFAULT_INIT_HOUR,
        params=base,
    )
    ifs_cells = build_cells(
        tigge_root=args.tigge_root,
        seasons=seasons,
        leads=leads,
        init_hour=DEFAULT_INIT_HOUR,
        params=base,
    )

    support_graphcast = _support(graphcast_cells)
    support_ifs = _support(ifs_cells)

    print("=" * 78)
    print(f"GraphCast model version: {version}   seasons: {list(seasons)}")
    print(_D2_YEAR_CONFOUND)
    print("-" * 78)
    print("D6 — station-season support ASSERTION (BEFORE any ranking)")
    print(f"  GraphCast cells: {len(support_graphcast)}")
    print(f"  IFS control cells: {len(support_ifs)}")
    print(f"  policy: {policy.name}")

    # ⛔ Fails closed BEFORE anything is ranked or printed as a result.
    common = resolve_shared_support(
        support_graphcast=support_graphcast,
        support_ifs=support_ifs,
        policy=policy,
    )
    if common == support_graphcast == support_ifs:
        print(f"  identical support on both sides: {len(common)} cells")
    else:
        print(
            f"  ⛔ SUPPORT ASYMMETRY ACCEPTED under an explicit opt-in — "
            f"ranked on the intersection ({len(common)} cells), which is NOT "
            "what either product was retrieved for"
        )
        print(f"  ⛔ only in GraphCast: {sorted(support_graphcast - support_ifs)}")
        print(f"  ⛔ only in IFS control: {sorted(support_ifs - support_graphcast)}")
    print(_D7_CONFOUND)
    print("=" * 78)

    graphcast_common = _restrict(graphcast_cells, common)
    ifs_common = _restrict(ifs_cells, common)

    print()
    print(
        f"### GraphCast {version} ({GRAPHCAST_PREFIX}) seasons {list(seasons)} "
        "— restricted to shared support ###"
    )
    graphcast_digests = consumed_input_digests(
        tigge_root=args.graphcast_root, seasons=seasons
    )
    report(
        graphcast_common,
        windows_h=windows,
        base=base,
        leads=leads,
        init_hour=DEFAULT_INIT_HOUR,
        seasons=seasons,
        null_shift_days=tuple(DEFAULT_NULL_SHIFT_DAYS),
        min_amplitude=MIN_HARMONIC_AMPLITUDE,
        input_digests=graphcast_digests,
    )

    print()
    print(
        f"### IFS control (ECMWF) seasons {list(seasons)} — restricted to shared "
        f"support — the control for GraphCast {version} above ###"
    )
    ifs_digests = consumed_input_digests(tigge_root=args.tigge_root, seasons=seasons)
    report(
        ifs_common,
        windows_h=windows,
        base=base,
        leads=leads,
        init_hour=DEFAULT_INIT_HOUR,
        seasons=seasons,
        null_shift_days=tuple(DEFAULT_NULL_SHIFT_DAYS),
        min_amplitude=MIN_HARMONIC_AMPLITUDE,
        input_digests=ifs_digests,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
