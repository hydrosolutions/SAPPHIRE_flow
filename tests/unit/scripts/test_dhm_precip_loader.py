"""Task 1a — dependencies and loaders (D3, D9b, D12). Synthetic fixtures only
(constraint 1) — no real DHM data is ever read by this module."""

from __future__ import annotations

import os
import random
import sys
from datetime import UTC, datetime

import pytest

from scripts.dhm_precip import loader
from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.fixtures import (
    DEFAULT_SENTINEL_STATION,
    build_synthetic_workbook_frame,
    write_synthetic_workbook,
)
from scripts.dhm_precip.loader import (
    EXPECTED_WORKBOOK_COLUMNS,
    ParseFailureError,
    SchemaMismatchError,
    Sha256MismatchError,
    SourcePathUnsetError,
    SourceUnreadableError,
    compute_sha256,
    load_long_frame,
    load_station_coordinates,
    resolve_coords_path,
    resolve_source_path,
)


class TestResolveSourcePath:
    def test_raises_when_unset(self) -> None:
        with pytest.raises(SourcePathUnsetError, match="DHM_PRECIP_XLSX"):
            resolve_source_path(env={})

    def test_returns_the_configured_path(self, tmp_path) -> None:
        path = resolve_source_path(env={"DHM_PRECIP_XLSX": str(tmp_path / "x.xlsx")})
        assert path == tmp_path / "x.xlsx"


class TestResolveCoordsPath:
    def test_defaults_to_the_committed_path(self) -> None:
        assert resolve_coords_path(env={}) == loader.DEFAULT_COORDS_PATH

    def test_honours_override(self, tmp_path) -> None:
        path = resolve_coords_path(env={"DHM_PRECIP_COORDS": str(tmp_path / "c.csv")})
        assert path == tmp_path / "c.csv"


requires_permission_enforcement = pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="permission bits are not enforceable on Windows or when running as root",
)


@pytest.fixture()
def synthetic_workbook(tmp_path):
    rng = random.Random(158)
    frame = build_synthetic_workbook_frame(
        start=datetime(2024, 6, 1, tzinfo=UTC).replace(tzinfo=None),
        n_hours=72,
        rng=rng,
    )
    path = tmp_path / "synthetic.xlsx"
    write_synthetic_workbook(path, frame)
    return path


class TestLoadLongFrameDigestGate:
    """D9b — the digest-injection seam, and constraint 1's 'must not fail open'."""

    def test_rejects_a_wrong_injected_digest(self, synthetic_workbook) -> None:
        # The KEY constraint-1 acceptance criterion: any digest mismatch is a
        # typed error, never a silent pass-through or skip.
        with pytest.raises(Sha256MismatchError, match="sha256 mismatch"):
            load_long_frame(synthetic_workbook, expected_sha256="0" * 64)

    def test_accepts_the_matching_injected_digest(self, synthetic_workbook) -> None:
        digest = compute_sha256(synthetic_workbook)
        frame, inventory = load_long_frame(synthetic_workbook, expected_sha256=digest)
        assert inventory.total_rows == 72
        assert frame.height == 72 * 37

    def test_missing_file_raises_source_unreadable(self, tmp_path) -> None:
        with pytest.raises(SourceUnreadableError):
            load_long_frame(tmp_path / "nope.xlsx", expected_sha256="0" * 64)

    @requires_permission_enforcement
    def test_a_permission_denied_source_raises_source_unreadable_not_exit_5(
        self, synthetic_workbook
    ) -> None:
        # D9: an unreadable path is exit 2 (SourceUnreadableError), never
        # exit 5 (ParseFailureError) — the content was never even reached.
        # compute_sha256() runs before pl.read_excel(), so an unreadable
        # file must be caught there, not fall through as an uncaught OSError
        # or get mis-typed as a parse failure.
        os.chmod(synthetic_workbook, 0o000)
        try:
            with pytest.raises(SourceUnreadableError):
                load_long_frame(synthetic_workbook, expected_sha256="0" * 64)
        finally:
            os.chmod(synthetic_workbook, 0o644)


class TestLoadLongFrameSchema:
    def test_rejects_a_short_station_column_set(self, tmp_path) -> None:
        import polars as pl

        bad = pl.DataFrame(
            {loader.TIME_COLUMN: [datetime(2024, 1, 1)], "Only One (mm)": [1.0]}
        )
        path = tmp_path / "bad.xlsx"
        write_synthetic_workbook(path, bad)
        digest = compute_sha256(path)
        with pytest.raises(SchemaMismatchError, match="37-name"):
            load_long_frame(path, expected_sha256=digest)

    def test_rejects_a_non_numeric_cell(self, tmp_path) -> None:
        import polars as pl

        columns = {loader.TIME_COLUMN: [datetime(2024, 1, 1)]}
        for col in EXPECTED_WORKBOOK_COLUMNS:
            columns[col] = [0.0]
        columns[EXPECTED_WORKBOOK_COLUMNS[0]] = ["not-a-number"]
        bad = pl.DataFrame(columns)
        path = tmp_path / "bad2.xlsx"
        write_synthetic_workbook(path, bad)
        digest = compute_sha256(path)
        with pytest.raises(ParseFailureError, match="non-numeric"):
            load_long_frame(path, expected_sha256=digest)


class TestLoadLongFrameCanonicalisation:
    def test_reports_empty_columns_present_but_empty(self, tmp_path) -> None:
        rng = random.Random(1)
        empty = (EXPECTED_WORKBOOK_COLUMNS[5], EXPECTED_WORKBOOK_COLUMNS[6])
        frame = build_synthetic_workbook_frame(
            start=datetime(2024, 6, 1),
            n_hours=24,
            rng=rng,
            empty_stations=empty,
        )
        path = tmp_path / "empty.xlsx"
        write_synthetic_workbook(path, frame)
        digest = compute_sha256(path)
        _, inventory = load_long_frame(path, expected_sha256=digest)
        assert set(inventory.empty_columns) == {c[: -len(" (mm)")] for c in empty}
        assert len(inventory.all_columns) == 37

    def test_long_frame_has_the_canonical_four_columns(
        self, synthetic_workbook
    ) -> None:
        digest = compute_sha256(synthetic_workbook)
        frame, _ = load_long_frame(synthetic_workbook, expected_sha256=digest)
        assert frame.columns == ["source_row_index", "station", "timestamp", "value_mm"]

    def test_sentinel_signature_survives_the_reshape(self, synthetic_workbook) -> None:
        digest = compute_sha256(synthetic_workbook)
        frame, _ = load_long_frame(synthetic_workbook, expected_sha256=digest)
        station = loader._strip_mm_suffix(DEFAULT_SENTINEL_STATION)
        sentinel_count = frame.filter(
            (frame["station"] == station) & (frame["value_mm"] == -9999999.0)
        ).height
        assert sentinel_count == 5


class TestLoadLongFrameNanHandling:
    """A cell holding the literal string "NaN" casts successfully to a float
    NaN (not a cast failure) but must never survive as a numeric reading —
    it silently poisons every downstream sum/quantile it touches. Discovered
    against the real workbook (`Lete (FNEP) (mm)`, 146 cells) during Phase 4a
    reconciliation; locked here with a synthetic fixture (constraint 1)."""

    def test_a_literal_nan_string_cell_becomes_a_null_observation(
        self, tmp_path
    ) -> None:
        import polars as pl

        columns: dict[str, list[object]] = {loader.TIME_COLUMN: [datetime(2024, 1, 1)]}
        for col in EXPECTED_WORKBOOK_COLUMNS:
            columns[col] = [0.0]
        columns[EXPECTED_WORKBOOK_COLUMNS[6]] = ["NaN"]  # "Lete (FNEP) (mm)"
        bad = pl.DataFrame(columns)
        path = tmp_path / "nan_cell.xlsx"
        write_synthetic_workbook(path, bad)
        digest = compute_sha256(path)
        frame, _ = load_long_frame(path, expected_sha256=digest)
        station = loader._strip_mm_suffix(EXPECTED_WORKBOOK_COLUMNS[6])
        row = frame.filter(frame["station"] == station)
        assert row.height == 1
        assert row["value_mm"][0] is None


class TestLoadStationCoordinates:
    def test_missing_file_raises(self, tmp_path) -> None:
        with pytest.raises(SourceUnreadableError):
            load_station_coordinates(
                tmp_path / "nope.csv", expected_stations=frozenset()
            )

    @requires_permission_enforcement
    def test_a_permission_denied_coords_file_raises_source_unreadable_not_parse_failure(
        self, tmp_path
    ) -> None:
        # D9: an unreadable coordinate table is exit 2, never exit 5 — a
        # permission error must not be caught by the generic parse-failure
        # handler and mis-reported as malformed content.
        path = tmp_path / "coords.csv"
        path.write_text("station,excel_col,lat,lon,elev\nA,A (mm),27.0,85.0,1000\n")
        os.chmod(path, 0o000)
        try:
            with pytest.raises(SourceUnreadableError):
                load_station_coordinates(path, expected_stations=frozenset())
        finally:
            os.chmod(path, 0o644)

    def test_rejects_wrong_schema(self, tmp_path) -> None:
        path = tmp_path / "coords.csv"
        path.write_text("station,lat,lon\nA,1.0,2.0\n")
        with pytest.raises(SchemaMismatchError, match="columns"):
            load_station_coordinates(path, expected_stations=frozenset({Station("A")}))

    def test_rejects_station_set_mismatch(self, tmp_path) -> None:
        path = tmp_path / "coords.csv"
        path.write_text("station,excel_col,lat,lon,elev\nA,A (mm),27.0,85.0,1000\n")
        with pytest.raises(
            SchemaMismatchError, match="does not match the live station set"
        ):
            load_station_coordinates(path, expected_stations=frozenset({Station("B")}))

    def test_happy_path_loads_the_table(self, tmp_path) -> None:
        path = tmp_path / "coords.csv"
        path.write_text(
            "station,excel_col,lat,lon,elev\n"
            "A,A (mm),27.0,85.0,1000\n"
            "B,B (mm),28.0,86.0,2000\n"
        )
        table = load_station_coordinates(
            path, expected_stations=frozenset({Station("A"), Station("B")})
        )
        assert table.by_station[Station("A")].elev_m == 1000.0
