#!/usr/bin/env python3
# ruff: noqa: T201
"""Plan 182 (M-A10) runner — composes the DHM ingest+QC-mask pipeline (Plan
170/173) and the Pyramid loader into the full co-located gauge-vs-gauge
adjudication: BOTH pairs (Lukla, Syangboche), BOTH windows — the
climatological FULL RECORD, which D11 adjudicates, and the overlap, which
corroborates — the D9 per-station verdicts, the EXACT two-station synthesis,
and a report carrying every Exit deliverable.

The Pyramid windows are built HERE, before pairing (`_year_window` over the
registry's per-station spans), so each window's reported retention is that
window's retained JJAS hours rather than the whole file's.

`run_coloc_adjudication()` is the tested, loader-agnostic core (CLAUDE.md
dependency injection: a `DhmRetainedProvider` is passed in, never a bare
call to the real pipeline inside business logic) — exercised end-to-end
against synthetic fixtures in `tests/unit/scripts/test_dhm_precip_coloc_run.py`
with no real files required — and every one of those tests drives the REAL
`COLOCATED_PAIRS` bounds (D11), never a synthetic window that bypasses them.
`main()` wires the REAL production DHM
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
    WindowResult,
    WindowUnavailable,
    adjudicate_station,
)
from scripts.dhm_precip.coloc_pairs import COLOCATED_PAIRS  # noqa: E402
from scripts.dhm_precip.coloc_verdict import (  # noqa: E402
    SynthesisVerdict,
    Verdict,
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


def _year_window(frame: pl.DataFrame, start_year: int, end_year: int) -> pl.DataFrame:
    return frame.filter(pl.col("timestamp").dt.year().is_between(start_year, end_year))


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
        # The Pyramid file spans the whole record at every month; the
        # windows must be constructed HERE, before pairing, or the
        # retention reported per window is the whole file's count rather
        # than the retained JJAS hours of that window.
        pyramid_jjas = pyramid_result.retained.filter(
            pl.col("timestamp").dt.month().is_in(params.jjas_months)
        )
        pyramid_full_record = _year_window(
            pyramid_jjas, pair.pyramid_start_year, pair.pyramid_end_year
        )
        pyramid_overlap = _year_window(
            pyramid_jjas, pair.overlap_start_year, pair.overlap_end_year
        )
        adjudications[pair.dhm_station] = adjudicate_station(
            dhm_station=pair.dhm_station,
            pyramid_station=pair.pyramid_station,
            dhm_overlap_retained=windows.overlap,
            dhm_full_record_retained=windows.full_record,
            pyramid_full_record_retained=pyramid_full_record,
            pyramid_overlap_retained=pyramid_overlap,
            rng=rng,
            params=params,
        )

    synthesis = synthesize_verdict(
        [adjudications[pair.dhm_station].station_verdict for pair in COLOCATED_PAIRS]
    )
    return ColocAdjudicationReport(
        pair_adjudications=adjudications, synthesis=synthesis, generated_at=clock()
    )


_ATTRIBUTION = (
    "Pyramid Meteorological Network data: Salerno et al. 2025, ESSD 17, 4293 "
    "(Zenodo `10.5281/zenodo.15211352`), used under **CC BY 4.0**. Lvl1 files "
    "only — never the Lvl2 gap-filled monthly reconstruction."
)

_AFFECTED_CLAIMS = (
    "- the Group A high-altitude **diurnal phase** reported in M-A1, and any "
    "elevation-banded profile M-A7 would build from it",
    "- the sub-0.1 mm wet-hour statistics that motivated H1 (22-34 % of Group A "
    "wet hours, 0.8-2.1 % of mass)",
    "- the withdrawn wet-hour-fraction contrast (DHM 54-55 % vs Pyramid 33 %), "
    "which was never resolution-matched",
    "- OD-12's correction operator, insofar as it consumes M-A7's diurnal shape",
)


def _profile_table(title: str, profile: pl.DataFrame) -> list[str]:
    """D1/D4 — the NORMALISED value and its hourly `n` only. No mm totals
    are reported anywhere (D1), so the mean column is deliberately omitted."""
    rows = profile.sort("hour")
    lines = [
        f"##### {title}",
        "",
        "| hour (NPT) | n | normalised |",
        "|---:|---:|---:|",
    ]
    lines += [
        f"| {int(row['hour'])} | {int(row['n'])} | "
        f"{float(row['normalised_value']):.3f} |"
        for row in rows.iter_rows(named=True)
    ]
    lines.append("")
    return lines


def _window_lines(label: str, window: WindowResult) -> list[str]:
    if isinstance(window, WindowUnavailable):
        return [
            f"#### {label} — UNAVAILABLE (`{window.failure.value}`)",
            "",
            f"- {window.detail}",
            f"- n DHM retained (this window): {window.n_dhm_retained}",
            f"- n Pyramid retained (this window): {window.n_pyramid_retained}",
            "",
        ]
    lines = [
        f"#### {label}",
        "",
        f"- n DHM retained (this window): {window.n_dhm_retained}",
        f"- n Pyramid retained (this window): {window.n_pyramid_retained}",
        f"- n common-retained: {window.n_common_retained}",
        f"- season-years (the smaller of the two networks'): "
        f"{window.season_year_count}",
        "",
        "D7 threshold ladder (peak hour, NPT):",
        "",
    ]
    lines += [
        f"- {threshold} mm rung: hour {peak}"
        for threshold, peak in sorted(window.threshold_ladder_peaks.items())
    ]
    lines += [
        f"- Pyramid peak hour: {window.pyramid_peak_hour}",
        "",
        "D5 bootstrap peak-hour spread (resampled on THIS window's "
        "adjudicated population):",
        f"- season-years: {window.bootstrap.n_season_years}",
        f"- circular spread (h): {window.bootstrap.spread_hours:.2f}",
        f"- adequate sample: {window.bootstrap.adequate_sample}",
        "",
    ]
    if window.wet_hour_fraction is not None:
        lines += [
            "D3 paired wet-hour fraction (common-retained population only, "
            "matched threshold on both sides):",
            f"- DHM: {window.wet_hour_fraction.dhm_wet_fraction:.3f}",
            f"- Pyramid: {window.wet_hour_fraction.pyramid_wet_fraction:.3f}",
            "",
        ]
    else:
        lines += [
            "D3 wet-hour fraction: **not reported** — this window is unpaired, "
            "and a wet-hour fraction over differently-selected populations is "
            "not a comparison.",
            "",
        ]
    for threshold, profile in sorted(window.threshold_ladder_profiles.items()):
        lines += _profile_table(
            f"DHM normalised diurnal profile — {label} — {threshold} mm rung",
            profile,
        )
    lines += _profile_table(
        f"Pyramid normalised diurnal profile — {label}", window.pyramid_profile
    )
    return lines


def _method_lines(params: DhmPrecipParams) -> list[str]:
    return [
        "## Method, uncertainty and the alternatives this test cannot exclude",
        "",
        "- **D2 alignment uncertainty: "
        f"±{params.coloc_alignment_uncertainty_hours} h.** "
        "Pyramid is NPT (UTC+5:45) and DHM is UTC period-ending, so hourly bins "
        f"cannot be made to coincide (45 min), and the Pyramid README does not "
        "state period-beginning vs period-ending (up to 1 h). Every profile "
        "below is reported in NPT and **no phase result finer than ±2 h is "
        "claimed anywhere in this report**.",
        "- **D8 — co-location is NOT identical exposure.** The pairs are "
        "1.4-1.9 km apart with 130-200 m of elevation difference in steep "
        "terrain, and neither network's instrument type, orifice height or wind "
        "exposure is documented. Normalisation cancels only *hour-independent* "
        "multiplicative undercatch, and **mountain wind is strongly diurnal** "
        "(anabatic/katabatic), so a genuine micro-climatic or wind-driven, "
        "hour-dependent catch difference remains a live alternative this test "
        "cannot exclude. Every verdict below is adjudicated against H1 **and** "
        "this alternative, never as 'co-located therefore comparable'.",
        "- **D7.3 — the intensity-dependent drizzle confound.** Thresholding at "
        "0.2 mm also removes genuine light rain, so if physical morning drizzle "
        "is systematically lighter than nocturnal storms, the DHM peak shifts "
        "under ablation even when every count is real. A DHM-only shift is "
        "therefore suggestive, not conclusive; only the matched-resolution "
        "agreement test (gate 1) raises it above suggestion.",
        "- **D1 — shape, never magnitude.** Profiles are normalised by each "
        "station's own daily mean and no totals are compared or reported.",
        "- **D11 — the FULL RECORD is adjudicated; the overlap corroborates.** "
        "The overlap is 3-4 monsoons, below the 5-season adequacy floor, and "
        "does not gate any verdict. The full-record comparison is "
        "non-contemporaneous, which is licensed only by D12's PYRAMID "
        "stationarity check reported per station below.",
        "",
        f"- {_ATTRIBUTION}",
        "",
    ]


def _write_report(
    path: Path,
    report: ColocAdjudicationReport,
    params: DhmPrecipParams = DEFAULT_PARAMS,
) -> None:
    """The Exit deliverable as Markdown: the D7 threshold ladder and the
    full normalised-profile tables for BOTH networks in BOTH windows (with
    hourly `n`), each side's retention per window, the D3 paired wet-hour
    fraction, the D5 bootstrap spread, D12's stationarity checks, the
    per-station verdicts and the two-station synthesis — plus D2's
    alignment uncertainty, D8's micro-climate/wind alternative, D7.3's
    drizzle confound, Pyramid's CC BY 4.0 attribution and, if H1 is
    supported, the affected-claims list filed for M-A7 (this plan does not
    itself rewrite the vision, D9 Exit)."""
    lines = [
        "# DHM precipitation — M-A10 co-located gauge-vs-gauge adjudication",
        "",
        f"- generated: `{report.generated_at.isoformat()}`",
        "",
    ]
    lines += _method_lines(params)
    lines += ["## Per-station adjudication", ""]
    for pair in COLOCATED_PAIRS:
        adj = report.pair_adjudications[pair.dhm_station]
        lines += [
            f"### {pair.dhm_station} vs {pair.pyramid_station}",
            "",
            f"- separation {pair.separation_km} km, elevation difference "
            f"{pair.elevation_delta_m} m (D8)",
            "",
        ]
        lines += _window_lines("Full record", adj.full_record)
        lines += _window_lines("Overlap", adj.overlap)
        lines += [
            "#### D12 stationarity",
            "",
            f"- **Pyramid (gates the verdict)**, split "
            f"{adj.pyramid_stationarity.split_year}: pre "
            f"{adj.pyramid_stationarity.pre_peak_hour} / post "
            f"{adj.pyramid_stationarity.post_peak_hour}, circular difference "
            f"{adj.pyramid_stationarity.peak_diff_hours:.1f} h "
            f"(sufficient data: {adj.pyramid_stationarity.data_sufficient})",
            f"- DHM (additional evidence ONLY — the record starts in 2020, so a "
            f"pre-2020 split is vacuous), split "
            f"{adj.dhm_stationarity.split_year}: pre "
            f"{adj.dhm_stationarity.pre_peak_hour} / post "
            f"{adj.dhm_stationarity.post_peak_hour}, circular difference "
            f"{adj.dhm_stationarity.peak_diff_hours:.1f} h "
            f"(sufficient data: {adj.dhm_stationarity.data_sufficient})",
            "",
            f"- overlap vs full-record matched-resolution peak difference: "
            f"{adj.overlap_vs_full_record_peak_diff_hours} h "
            "(reported, never an error — D11)",
            "",
            f"**Verdict: {adj.station_verdict.verdict.value}** "
            f"(gate stopped at `{adj.station_verdict.gate_stopped_at}`"
            + (
                f", reason `{adj.station_verdict.reason.value}`)"
                if adj.station_verdict.reason is not None
                else ")"
            ),
            f"- matched-resolution disagreement: "
            f"{adj.station_verdict.matched_resolution_diff_hours} h",
            f"- ablation movement: {adj.station_verdict.ablation_movement_hours} h "
            f"(toward Pyramid: {adj.station_verdict.moved_toward_pyramid})",
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
        "INDETERMINATE is a permitted, publishable outcome: it BLOCKS the M-A7 "
        "correction rather than licensing it (D9).",
        "",
    ]
    if report.synthesis.verdict == Verdict.H1_SUPPORTED:
        lines += [
            "## Affected claims — filed as a correction for M-A7",
            "",
            "H1 is supported: the Group A high-altitude diurnal signal is "
            "consistent with noise-floor contamination, subject to the D8 and "
            "D7.3 alternatives above. The following claims are affected and are "
            "filed for M-A7 to apply — this runner does not itself rewrite the "
            "vision (D9 Exit):",
            "",
            *_AFFECTED_CLAIMS,
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

    # The mask frame's timestamp dtype is DERIVED from the frame it is
    # about to be anti-joined against, never pinned: `pl.read_excel` yields
    # `Datetime("ms")`, so a hard-coded unit (either unit) makes the join
    # below raise `SchemaError` against real data. Deriving it here keeps
    # the two sides in agreement by construction whatever the loader — or a
    # future polars — produces.
    mask_df = pl.DataFrame(
        {
            "station": [str(station) for station, _ts in mask],
            "timestamp": [ts.replace(tzinfo=None) for _station, ts in mask],
        },
        schema={"station": pl.Utf8, "timestamp": on_grid.schema["timestamp"]},
    )
    retained_all = (
        on_grid.join(mask_df, on=["station", "timestamp"], how="anti")
        .filter(pl.col("value_mm").is_not_null())
        .filter(pl.col("timestamp").dt.month().is_in(params.jjas_months))
    )

    def provider(station: Station) -> DhmRetainedWindows:
        pair = next(p for p in COLOCATED_PAIRS if p.dhm_station == station)
        station_rows = retained_all.filter(pl.col("station") == str(station))
        return DhmRetainedWindows(
            overlap=_year_window(
                station_rows, pair.overlap_start_year, pair.overlap_end_year
            ),
            full_record=_year_window(
                station_rows, pair.dhm_start_year, pair.dhm_end_year
            ),
        )

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
