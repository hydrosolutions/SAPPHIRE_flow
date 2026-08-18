"""Task 4c — the final asserting gate (M-A1 exit).

Constraint 1: "the *only* skip condition is `DHM_PRECIP_XLSX` unset,
applied by the integration test alone." No other module in
`scripts/dhm_precip/` contains pytest semantics — a missing path, digest
mismatch, schema mismatch or parse failure is a runner exit code, never a
skip (see `scripts/dhm_precip/run.py`'s docstring).

Exit gate (from the plan): every `active` and `corrected` expectation
matches under D5, and every `withdrawn_unreproducible` one carries a
complete Phase-4 record (D8c) — the latter is also enforced structurally by
`ExpectationModel` at load time (`expectations.py`), so `load_expectations`
succeeding at all is itself part of the gate.
"""

from __future__ import annotations

import json
import os

import polars as pl
import pytest

from scripts.dhm_precip import stats_defects
from scripts.dhm_precip.coloc_pairs import COLOCATED_PAIRS
from scripts.dhm_precip.coloc_run import main as coloc_main
from scripts.dhm_precip.evaluate import (
    DeclaredTableMismatchError,
    ExpectationCoverageError,
    run_report,
    validate_artefacts,
    validate_expectation_coverage,
)
from scripts.dhm_precip.expectations import load_expectations
from scripts.dhm_precip.manifest_io import read_manifest
from scripts.dhm_precip.params import DEFAULT_PARAMS
from scripts.dhm_precip.run import run as run_pipeline

pytestmark = pytest.mark.skipif(
    not os.environ.get("DHM_PRECIP_XLSX"),
    reason="DHM_PRECIP_XLSX unset — the only skip condition here (constraint 1)",
)


def test_active_and_corrected_expectations_match_under_d5(tmp_path) -> None:
    report = run_report(tmp_path / "m_a1_out")

    asserted = [
        d for d in report.discrepancies if d.disposition in ("active", "corrected")
    ]
    assert asserted, (
        "expected at least one active/corrected expectation to assert against"
    )

    failures = [
        f"{d.expectation_id} ({d.disposition}): expected {d.expected!r}, "
        f"got {d.actual!r} — {d.reason}"
        for d in asserted
        if not d.matched
    ]
    assert not failures, "M-A1 reproduction gate failed:\n" + "\n".join(failures)


def test_withdrawn_unreproducible_expectations_are_exempt_but_recorded(
    tmp_path,
) -> None:
    report = run_report(tmp_path / "m_a1_out")

    withdrawn = [
        d for d in report.discrepancies if d.disposition == "withdrawn_unreproducible"
    ]
    # D8c: exempt from numeric matching — this must hold regardless of the
    # actual value (compare_expectation's own unit tests lock the mechanism;
    # this integration test confirms it holds for the real manifest too).
    assert all(d.matched for d in withdrawn)


def test_every_withdrawn_expectation_has_a_complete_phase4_record() -> None:
    import tomllib

    from scripts.dhm_precip.expectations import DEFAULT_EXPECTATIONS_PATH

    raw = tomllib.loads(DEFAULT_EXPECTATIONS_PATH.read_text())
    for entry in raw["expectation"]:
        if entry.get("disposition") != "withdrawn_unreproducible":
            continue
        has_original = (
            entry.get("original_value") is not None
            or entry.get("original_range") is not None
        )
        assert has_original, f"{entry['id']}: missing original_value/original_range"
        assert entry.get("method_comparison"), (
            f"{entry['id']}: missing method_comparison"
        )
        assert entry.get("evidence"), f"{entry['id']}: missing evidence"
        assert entry.get("successor"), f"{entry['id']}: missing successor"


class TestArtefactGateCatchesTampering:
    """Blocker-1 fix: `validate_artefacts` reopens every declared parquet and
    RECOMPUTES values/counts/schema from them — these mutation tests prove
    it actually catches a tampered artefact, not merely that it "looks
    right" on an untouched run."""

    def test_a_mutated_parquet_value_is_caught_by_the_recompute_check(
        self, tmp_path
    ) -> None:
        out = tmp_path / "m_a1_out"
        run_pipeline(out)
        manifest = read_manifest(out / "results.json")

        # Corrupt a persisted table's value AFTER the manifest was written —
        # results.json still claims the original number, but the parquet no
        # longer agrees with it.
        path = out / "tables" / "row_counts.parquet"
        mutated = pl.read_parquet(path).with_columns(
            pl.lit(999_999).alias("total_rows")
        )
        mutated.write_parquet(path)

        with pytest.raises(DeclaredTableMismatchError):
            validate_artefacts(manifest, out)

    def test_a_mutated_table_axis_status_is_caught(self, tmp_path) -> None:
        out = tmp_path / "m_a1_out"
        run_pipeline(out)
        manifest = read_manifest(out / "results.json")

        # Relabel a geometry table's axis_status — the (view, axis_status)
        # set recomputed from the reopened parquet no longer matches what
        # results.json declared for this table.
        path = out / "tables" / "geometry_summary.parquet"
        mutated = pl.read_parquet(path).with_columns(
            pl.lit("RAW_PROVISIONAL").alias("axis_status")
        )
        mutated.write_parquet(path)

        with pytest.raises(DeclaredTableMismatchError):
            validate_artefacts(manifest, out)

    def test_a_mutated_counts_by_view_is_caught(self, tmp_path) -> None:
        out = tmp_path / "m_a1_out"
        run_pipeline(out)
        results_path = out / "results.json"
        payload = json.loads(results_path.read_text())
        payload["counts_by_view"]["RAW"]["source_timestamp_rows"] += 1
        results_path.write_text(json.dumps(payload))
        manifest = read_manifest(results_path)

        with pytest.raises(DeclaredTableMismatchError):
            validate_artefacts(manifest, out)

    def test_deleting_a_withdrawn_expectations_value_fails_coverage(
        self, tmp_path
    ) -> None:
        out = tmp_path / "m_a1_out"
        run_pipeline(out)
        results_path = out / "results.json"
        payload = json.loads(results_path.read_text())
        expectations = load_expectations()
        withdrawn_id = next(
            e.id for e in expectations if e.disposition == "withdrawn_unreproducible"
        )
        assert withdrawn_id in payload["values"]
        del payload["values"][withdrawn_id]
        results_path.write_text(json.dumps(payload))
        manifest = read_manifest(results_path)

        with pytest.raises(ExpectationCoverageError, match=withdrawn_id):
            validate_expectation_coverage(manifest, expectations)


class TestNormalisedAxisMatchesTheRecordedRealWorkbookTotals:
    """M-A2 (Plan 172) — the real-workbook totals recorded in
    `docs/design/dhm-precipitation-milestones.md` ("Run against the real
    pinned workbook (2026-08-14)") are only ever asserted at reduced
    precision elsewhere (`axis_off_grid_observation_fraction` rounds
    `6,633 / 1,039,476` to 3 decimals; the normalised dataset's own row/gap
    counts are not asserted anywhere). Lock the exact real numbers here so a
    future regression in the reindex or the off-grid diagnostics cannot
    silently change them without failing this gate.
    """

    def test_provenance_off_grid_counts_match_the_recorded_totals(
        self, tmp_path
    ) -> None:
        out = tmp_path / "m_a2_out"
        run_pipeline(out)
        provenance = pl.read_parquet(
            out / "tables" / "normalisation_provenance.parquet"
        )
        assert provenance["off_grid_source_timestamp_rows"][0] == 3350
        assert provenance["off_grid_non_null_observations"][0] == 6633

    def test_normalised_axis_row_and_gap_counts_match_the_recorded_totals(
        self, tmp_path
    ) -> None:
        # 52,597 hourly slots x 26 live stations; 568 of those slots have no
        # row from any station, materialising as 568 x 26 = 14,768 inserted
        # NULL rows (D2: null source_row_index).
        out = tmp_path / "m_a2_out"
        run_pipeline(out)
        axis = pl.read_parquet(out / "tables" / "normalised_axis.parquet")

        assert axis["timestamp"].n_unique() == 52597
        assert axis.height == 52597 * 26
        assert axis.filter(pl.col("source_row_index").is_null()).height == 14768

    def test_normalised_axis_has_unique_station_hour_keys(self, tmp_path) -> None:
        out = tmp_path / "m_a2_out"
        run_pipeline(out)
        axis = pl.read_parquet(out / "tables" / "normalised_axis.parquet")

        key_counts = axis.group_by(["station", "timestamp"]).len()
        assert key_counts["len"].max() == 1


class TestQcMaskAgainstTheRealWorkbook:
    """Task 3a (Plan 173, M-A3) — the M-A1 reproduction gate evaluates
    UNMASKED statistics by design and would therefore pass against an EMPTY
    mask; these assertions run against the real workbook specifically so a
    regression that silently emptied the mask (D5's failure mode) cannot
    hide behind that gate. Real counts recorded 2026-08-15, run against the
    pinned production workbook.
    """

    def test_the_mask_is_non_empty_and_catches_the_recorded_real_defects(
        self, tmp_path
    ) -> None:
        # minor-4: aggregate counts alone would still pass with 120 WRONG
        # Sindhuli hours, 45 WRONG Lukla hours, or any 1,248 WRONG
        # Aiselukhark hours. Each station's masked timestamp set is checked
        # here against an INDEPENDENTLY derived expectation — Lukla's
        # directly from the normalised values (the same range-check
        # condition the rule applies), Sindhuli's and Aiselukhark's from
        # `stats_defects`'s own candidate-run detection (a separate code
        # path from `qc_mask.build_mask`) — so a regression that shifts,
        # truncates, or misattributes the flagged hours fails even though
        # the COUNT still matches.
        out = tmp_path / "m_a3_out"
        run_pipeline(out)
        mask = pl.read_parquet(out / "tables" / "qc_mask.parquet")
        normalised_axis = pl.read_parquet(out / "tables" / "normalised_axis.parquet")

        assert mask.height == 11381

        sindhuli = mask.filter(pl.col("station") == "Sindhuli Madhi")
        assert sindhuli.height == 120  # D3's predicted stuck-high duration
        sindhuli_ts = set(sindhuli["timestamp"].to_list())
        sindhuli_runs = stats_defects.stuck_high_candidate_runs(
            normalised_axis, DEFAULT_PARAMS
        ).filter(pl.col("station") == "Sindhuli Madhi")
        longest_stuck_run = sindhuli_runs.sort(
            "run_length_hours", descending=True
        ).head(1)
        assert longest_stuck_run.height == 1
        expected_sindhuli_ts = set(
            pl.datetime_range(
                longest_stuck_run["run_start"][0],
                longest_stuck_run["run_end"][0],
                interval="1h",
                eager=True,
            ).to_list()
        )
        assert expected_sindhuli_ts <= sindhuli_ts, (
            "the independently-detected stuck-high interval is not fully "
            "contained in the mask's flagged Sindhuli Madhi timestamps"
        )

        aiselukhark = mask.filter(pl.col("station") == "Aiselukhark")
        assert aiselukhark.height >= 52 * 24  # the 52-day run plus its siblings
        aiselukhark_ts = set(aiselukhark["timestamp"].to_list())
        aiselukhark_runs = stats_defects.candidate_zero_runs(
            normalised_axis, DEFAULT_PARAMS
        ).filter(pl.col("station") == "Aiselukhark")
        longest_zero_run = aiselukhark_runs.sort(
            "run_length_hours", descending=True
        ).head(1)
        assert longest_zero_run.height == 1
        assert (
            longest_zero_run["run_length_hours"][0] >= 52 * 24
        )  # still the 52-day run
        expected_aiselukhark_ts = set(
            pl.datetime_range(
                longest_zero_run["run_start"][0],
                longest_zero_run["run_end"][0],
                interval="1h",
                eager=True,
            ).to_list()
        )
        assert expected_aiselukhark_ts <= aiselukhark_ts, (
            "the independently-detected 52-day zero-run interval is not "
            "fully contained in the mask's flagged Aiselukhark timestamps"
        )

        lukla = mask.filter(pl.col("station") == "Lukla Airport")
        assert lukla.height == 45  # matches the milestone doc's sentinel count
        lukla_axis = normalised_axis.filter(pl.col("station") == "Lukla Airport")
        expected_lukla_ts = set(
            lukla_axis.filter(
                (pl.col("value_mm") < DEFAULT_PARAMS.qc_mask_range_check_value_min_mm)
                | (pl.col("value_mm") > DEFAULT_PARAMS.qc_mask_range_check_value_max_mm)
            )["timestamp"].to_list()
        )
        assert set(lukla["timestamp"].to_list()) == expected_lukla_ts, (
            "the mask's flagged Lukla timestamps do not exactly match the "
            "out-of-range values derived directly from the normalised axis"
        )

    def test_the_accounting_reconciles_to_the_axis_row_count(self, tmp_path) -> None:
        out = tmp_path / "m_a3_out"
        run_pipeline(out)
        accounting = pl.read_parquet(out / "tables" / "qc_removal_accounting.parquet")
        axis = pl.read_parquet(out / "tables" / "normalised_axis.parquet")

        assert accounting["count"].sum() == axis.height

    def test_the_exclusion_list_is_empty_on_real_data(self, tmp_path) -> None:
        # D8: expected, not a bug — the worst measured JJAS retention is
        # 0.830 (Lete), well above the 0.50 exclusion floor.
        out = tmp_path / "m_a3_out"
        run_pipeline(out)
        exclusion_list = pl.read_parquet(out / "tables" / "qc_exclusion_list.parquet")

        assert exclusion_list.height == 0


def _write_synthetic_pyramid_csv(path, *, years, npt_peak_hour: int = 14) -> None:
    """A minimal file in the REAL Lvl1 shape (semicolon-delimited, CR-only
    line endings, `year;month;day;hour;AT;RR;AP;RH;WS;WD`, NPT wall-clock).
    The real Zenodo CSVs are gitignored and absent, but the DHM side of this
    runner — the side that was broken — is the REAL workbook."""
    lines = ["year;month;day;hour;AT;RR;AP;RH;WS;WD"]
    lines += [
        f"{year};7;{day};{hour};;{5.0 if hour == npt_peak_hour else 0.0};;;;"
        for year in years
        for day in range(1, 7)
        for hour in range(24)
    ]
    path.write_bytes(("\r".join(lines) + "\r\n").encode("utf-8"))


class TestColocRunnerReachesItsReportAgainstTheRealWorkbook:
    """Plan 182 (M-A10) — every unit test of the runner injects a
    `DhmRetainedProvider` and therefore never touches
    `_production_dhm_retained_provider`, where the runner-built QC-mask
    frame meets the workbook-loaded ON_GRID frame. A dtype pinned there
    (the workbook's timestamps are `Datetime('ms')`, not `us`) makes that
    anti-join raise `SchemaError`, so `main()` could not write its report
    against real data while the whole suite stayed green. This drives
    `main()` end-to-end on the real workbook."""

    def test_main_writes_the_report_from_the_real_workbook(self, tmp_path) -> None:
        for pair in COLOCATED_PAIRS:
            _write_synthetic_pyramid_csv(
                tmp_path / pair.pyramid_csv_filename,
                years=range(pair.pyramid_start_year, pair.pyramid_end_year + 1),
            )

        exit_code = coloc_main(
            ["--out", str(tmp_path / "m_a10_out"), "--pyramid-dir", str(tmp_path)]
        )

        assert exit_code == 0
        report = tmp_path / "m_a10_out" / "coloc_adjudication.md"
        assert report.exists()
        text = report.read_text()
        for pair in COLOCATED_PAIRS:
            assert f"### {pair.dhm_station} vs {pair.pyramid_station}" in text
        assert "n DHM retained (this window)" in text
