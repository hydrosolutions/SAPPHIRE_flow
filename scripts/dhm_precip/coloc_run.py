#!/usr/bin/env python3
# ruff: noqa: T201
"""Plan 182 (M-A10) runner — composes the DHM ingest+QC-mask pipeline (Plan
170/173) and the Pyramid loader into the full co-located gauge-vs-gauge
adjudication: BOTH pairs (Lukla, Syangboche), BOTH windows (overlap +
climatological full record), the D9 per-station verdicts, the EXACT
two-station synthesis, and a report.

`run_coloc_adjudication()` is the tested, loader-agnostic core (CLAUDE.md
dependency injection: a `DhmRetainedProvider` is passed in, never a bare
call to the real pipeline inside business logic) — exercised end-to-end
against synthetic fixtures in `tests/unit/scripts/test_dhm_precip_coloc_run.py`
with no real files required. `main()` wires the REAL production DHM
ingest+mask pipeline (`loader`, `views`, `normalise`, `observations`,
`qc_mask` — the exact call sequence `pipeline.py`/`run.py` already use and
have real test coverage for) and the real `pyramid_loader`.

**Residual risk, honestly unresolved at implementation time (Plan 182
fixer round):** this runner has NOT been executed end-to-end against the
real production DHM workbook or the real Pyramid Lvl1 CSVs — neither is
present in any workspace this plan has been implemented or fixed in
(`DHM_PRECIP_XLSX` unset; `data/dhm_precip/pyramid/` gitignored and empty,
per `pyramid_loader.py`'s own docstring). The wiring in
`_production_dhm_retained_provider` below reuses ALREADY-TESTED primitives
(`load_long_frame`, `on_grid_view`, `normalise_hourly_axis`,
`iter_observations_by_station`, `qc_mask.iter_station_results` — the exact
sequence `pipeline.py` runs in production) in the same order and with the
same arguments, but the composition itself is unexecuted against real
data. Running it end-to-end the first time both data sources are available
— and re-verifying `pyramid_loader.py`'s column-name assumptions against
the real Zenodo files — is the tracked follow-on.

Usage:
    uv run python scripts/dhm_precip/coloc_run.py --out <dir>

Environment:
    DHM_PRECIP_XLSX     path to the source workbook (required)
    DHM_PRECIP_COORDS   path to the D12 coordinate table (optional)
    DHM_PRECIP_PYRAMID  path to `data/dhm_precip/pyramid/` (optional,
                        defaults to that path relative to the repo root)
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import polars as pl

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import structlog  # noqa: E402

from sapphire_flow.types.datetime import ensure_utc  # noqa: E402
from scripts.dhm_precip import normalise, observations, qc_mask  # noqa: E402
from scripts.dhm_precip.coloc_adjudication import (  # noqa: E402
    StationAdjudication,
    adjudicate_station,
)
from scripts.dhm_precip.coloc_pairs import COLOCATED_PAIRS  # noqa: E402
from scripts.dhm_precip.coloc_verdict import (  # noqa: E402
    SynthesisVerdict,
    synthesize_verdict,
)
from scripts.dhm_precip.domain_types import Station  # noqa: E402
from scripts.dhm_precip.loader import (  # noqa: E402
    PRODUCTION_SOURCE_SHA256,
    DhmPrecipLoaderError,
    load_long_frame,
    load_station_coordinates,
    resolve_coords_path,
    resolve_source_path,
)
from scripts.dhm_precip.params import DEFAULT_PARAMS, DhmPrecipParams  # noqa: E402
from scripts.dhm_precip.pyramid_loader import (  # noqa: E402
    PyramidLoaderError,
    load_pyramid_lvl1_csv,
)
from scripts.dhm_precip.views import on_grid_view  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable

log = structlog.get_logger(__name__)

DEFAULT_PYRAMID_DIR: Path = _REPO_ROOT / "data" / "dhm_precip" / "pyramid"


@dataclass(frozen=True, kw_only=True, slots=True)
class DhmRetainedWindows:
    """One DHM station's M-A3-masked, on-grid, JJAS `(station, timestamp,
    value_mm)` rows in UTC — `overlap` restricted to the pair's D5a JJAS
    overlap years, `full_record` the station's whole JJAS history (D5b)."""

    overlap: pl.DataFrame
    full_record: pl.DataFrame


class DhmRetainedProvider(Protocol):
    """Injected (CLAUDE.md testability) so `run_coloc_adjudication` never
    depends on the real production workbook to be exercised end-to-end in
    tests."""

    def __call__(self, station: Station) -> DhmRetainedWindows: ...


@dataclass(frozen=True, kw_only=True, slots=True)
class ColocAdjudicationReport:
    pair_adjudications: dict[Station, StationAdjudication]
    synthesis: SynthesisVerdict
    generated_at: datetime


def run_coloc_adjudication(
    *,
    dhm_retained: DhmRetainedProvider,
    pyramid_dir: Path,
    rng: random.Random,
    params: DhmPrecipParams = DEFAULT_PARAMS,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ColocAdjudicationReport:
    """The M-A10 Exit deliverable, composed: both co-located pairs, both
    windows, the D9 per-station verdicts and the EXACT two-station
    synthesis (`coloc_pairs.COLOCATED_PAIRS` — never an ad hoc subset)."""
    adjudications: dict[Station, StationAdjudication] = {}
    for pair in COLOCATED_PAIRS:
        windows = dhm_retained(pair.dhm_station)
        pyramid_result = load_pyramid_lvl1_csv(
            pyramid_dir / pair.pyramid_csv_filename,
            station=pair.pyramid_station,
            params=params,
        )
        adjudications[pair.dhm_station] = adjudicate_station(
            dhm_station=pair.dhm_station,
            dhm_overlap_retained=windows.overlap,
            dhm_full_record_retained=windows.full_record,
            pyramid_retained=pyramid_result.retained,
            rng=rng,
            params=params,
        )

    synthesis = synthesize_verdict(
        [adjudications[pair.dhm_station].station_verdict for pair in COLOCATED_PAIRS]
    )
    return ColocAdjudicationReport(
        pair_adjudications=adjudications, synthesis=synthesis, generated_at=clock()
    )


def _write_report(path: Path, report: ColocAdjudicationReport) -> None:
    """The Exit deliverable as Markdown: the D7 threshold ladder per
    station (with `n` beside every hour), the paired wet-hour fraction,
    the D5 bootstrap spread and adequacy, per-station verdicts, the
    synthesis, and — if H1 is supported — the correction outcome filed
    for M-A7 (this plan does not itself rewrite the vision, D9 Exit)."""
    lines = [
        "# DHM precipitation — M-A10 co-located gauge-vs-gauge adjudication",
        "",
        f"- generated: `{report.generated_at.isoformat()}`",
        "",
        "## Per-station adjudication",
        "",
    ]
    for pair in COLOCATED_PAIRS:
        adj = report.pair_adjudications[pair.dhm_station]
        lines += [
            f"### {pair.dhm_station} vs {pair.pyramid_station}",
            "",
            "D7 threshold ladder (peak hour, NPT, paired common-retained population):",
            "",
        ]
        lines += [
            f"- {threshold} mm: hour {peak}"
            for threshold, peak in sorted(adj.threshold_ladder_peaks.items())
        ]
        lines += [
            f"- Pyramid peak hour: {adj.pyramid_peak_hour}",
            f"- DHM full-record (climatological) peak hour: "
            f"{adj.dhm_full_record_peak_hour}",
            "",
            "D3 paired wet-hour fraction (common-retained population only):",
            f"- n common-retained: {adj.wet_hour_fraction.n_common_retained}",
            f"- DHM: {adj.wet_hour_fraction.dhm_wet_fraction:.3f}",
            f"- Pyramid: {adj.wet_hour_fraction.pyramid_wet_fraction:.3f}",
            "",
            "D5 bootstrap peak-hour spread:",
            f"- season-years: {adj.bootstrap.n_season_years}",
            f"- spread (h): {adj.bootstrap.spread_hours:.2f}",
            f"- adequate sample: {adj.bootstrap.adequate_sample}",
            "",
            f"**Verdict: {adj.station_verdict.verdict.value}** "
            f"(gate stopped at `{adj.station_verdict.gate_stopped_at}`"
            + (
                f", reason `{adj.station_verdict.reason.value}`)"
                if adj.station_verdict.reason is not None
                else ")"
            ),
            "",
        ]

    lines += [
        "## Synthesis",
        "",
        f"**{report.synthesis.verdict.value}**"
        + (
            f" (`{report.synthesis.reason.value}`)"
            if report.synthesis.reason is not None
            else ""
        ),
        "",
    ]
    if report.synthesis.verdict.value == "H1_SUPPORTED":
        lines += [
            "## Correction filed for M-A7",
            "",
            "H1 is supported: the Group A high-altitude diurnal signal is "
            "consistent with noise-floor contamination at this station pair. "
            "This finding is filed as a correction for M-A7 to apply — this "
            "runner does not itself rewrite the vision (D9 Exit).",
            "",
        ]
    path.write_text("\n".join(lines) + "\n")


def _production_dhm_retained_provider(params: DhmPrecipParams) -> DhmRetainedProvider:
    """Wires the REAL M-A1/M-A3 ingest+mask pipeline into a
    `DhmRetainedProvider` — loads the pinned production workbook and
    computes the M-A3 mask ONCE, reused for every co-located station.
    Mirrors `pipeline.py`'s exact call sequence (`normalise_hourly_axis` ->
    `iter_observations_by_station` -> `qc_mask.iter_station_results`)."""
    source_path = resolve_source_path()
    long_frame, inventory = load_long_frame(
        source_path, expected_sha256=PRODUCTION_SOURCE_SHA256
    )
    live_stations = frozenset(
        Station(name)
        for name in inventory.all_columns
        if name not in inventory.empty_columns
    )
    coords_path = resolve_coords_path()
    load_station_coordinates(coords_path, expected_stations=live_stations)

    on_grid = on_grid_view(long_frame, params)
    normalised = normalise.normalise_hourly_axis(on_grid, live_stations)
    now = ensure_utc(datetime.now(UTC))
    station_observations = observations.iter_observations_by_station(
        normalised, parameter="precipitation", created_at=now
    )
    mask, _accounting_rows = qc_mask.iter_station_results(station_observations, params)

    mask_df = pl.DataFrame(
        {
            "station": [str(station) for station, _ts in mask],
            "timestamp": [ts.replace(tzinfo=None) for _station, ts in mask],
        },
        schema={"station": pl.Utf8, "timestamp": pl.Datetime("us")},
    )
    retained_all = (
        on_grid.join(mask_df, on=["station", "timestamp"], how="anti")
        .filter(pl.col("value_mm").is_not_null())
        .filter(pl.col("timestamp").dt.month().is_in(params.jjas_months))
    )

    def provider(station: Station) -> DhmRetainedWindows:
        pair = next(p for p in COLOCATED_PAIRS if p.dhm_station == station)
        station_rows = retained_all.filter(pl.col("station") == str(station))
        overlap = station_rows.filter(
            pl.col("timestamp")
            .dt.year()
            .is_between(pair.overlap_start_year, pair.overlap_end_year)
        )
        return DhmRetainedWindows(overlap=overlap, full_record=station_rows)

    return provider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, required=True, help="output directory for the report"
    )
    parser.add_argument(
        "--pyramid-dir",
        type=Path,
        default=DEFAULT_PYRAMID_DIR,
        help="directory containing the Pyramid Lvl1 CSVs (gitignored)",
    )
    parser.add_argument("--seed", type=int, default=0, help="bootstrap RNG seed")
    args = parser.parse_args(argv)

    try:
        provider = _production_dhm_retained_provider(DEFAULT_PARAMS)
        report = run_coloc_adjudication(
            dhm_retained=provider,
            pyramid_dir=args.pyramid_dir,
            rng=random.Random(args.seed),
            params=DEFAULT_PARAMS,
        )
    except (DhmPrecipLoaderError, PyramidLoaderError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    out_path = args.out / "coloc_adjudication.md"
    _write_report(out_path, report)
    log.info(
        "dhm_precip.coloc_run.complete",
        out=str(out_path),
        synthesis=report.synthesis.verdict.value,
    )
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
