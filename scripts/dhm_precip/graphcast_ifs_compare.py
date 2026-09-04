"""Plan 240 (M-A12) T2 — run the UNCHANGED `ifs_event_timing` estimator
against GraphCast and against IFS control on the SAME four JJAS seasons,
and print both reports side by side.

⛔ PROPORTIONALITY: this module contains no statistic of its own. Every
number in its output comes from `ifs_event_timing.build_cells` and
`ifs_event_timing.report`, called twice with a different `tigge_root` (D3)
— once for GraphCast (`graphcast_acquire`'s output tree), once for IFS
control (the production `data/dhm_precip/tigge` tree). The only logic
here is D6's support check: `build_cells` silently drops any
(station, season) that lacks BOTH sides, so GraphCast's gaps and IFS's
gaps can leave the two products with different station-season support —
ranking on that would let a missing cell on one side masquerade as a
model difference. This module intersects the two support sets BEFORE
calling `report`, and states the intersection explicitly, rather than
letting the estimator run on cells report() never asked to align.

D7 — GraphCast here is GFS-initialised; IFS control is ECMWF-initialised.
This module states that confound in its own header; it answers "which
archived operational product scores higher against these gauges", never
"AI vs physics" (Plan 240).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.dhm_precip.graphcast_acquire import (
    DEFAULT_GRAPHCAST_ROOT,
    DEFAULT_GRAPHCAST_SEASONS,
    EXPECTED_INIT_MODEL,
    EXPECTED_MODEL_NAME,
    EXPECTED_MODEL_VERSION,
    GRAPHCAST_PREFIX,
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
    f"D7 confound: GraphCast here is {EXPECTED_MODEL_NAME} {EXPECTED_MODEL_VERSION} "
    f"initialised from {EXPECTED_INIT_MODEL} ({GRAPHCAST_PREFIX}); IFS control below "
    "is initialised from its own ECMWF analysis. This answers 'which archived "
    "operational product scores higher against these gauges', NOT 'AI vs physics'."
)


def _support(cells: list[StationSeasonCell]) -> set[tuple[str, int]]:
    return {(str(cell.station), cell.year) for cell in cells}


def _restrict(
    cells: list[StationSeasonCell], support: set[tuple[str, int]]
) -> list[StationSeasonCell]:
    return [cell for cell in cells if (str(cell.station), cell.year) in support]


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
    args = parser.parse_args()

    seasons = tuple(sorted(set(args.seasons)))
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
    common = support_graphcast & support_ifs

    print("=" * 78)
    print("D6 — station-season support check (BEFORE any ranking)")
    print(f"  GraphCast cells: {len(support_graphcast)}")
    print(f"  IFS control cells: {len(support_ifs)}")
    print(f"  shared support: {len(common)}")
    only_graphcast = sorted(support_graphcast - support_ifs)
    only_ifs = sorted(support_ifs - support_graphcast)
    if only_graphcast:
        print(f"  ⛔ only in GraphCast ({len(only_graphcast)}): {only_graphcast}")
    if only_ifs:
        print(f"  ⛔ only in IFS control ({len(only_ifs)}): {only_ifs}")
    print(_D7_CONFOUND)
    print("=" * 78)

    if not common:
        raise EventTimingInputError(
            "no (station, season) cell is present on BOTH sides — nothing to "
            "compare (D6)"
        )

    graphcast_common = _restrict(graphcast_cells, common)
    ifs_common = _restrict(ifs_cells, common)

    print()
    print(f"### GraphCast ({GRAPHCAST_PREFIX}) — restricted to shared support ###")
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
    print("### IFS control (ECMWF) — restricted to shared support ###")
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
