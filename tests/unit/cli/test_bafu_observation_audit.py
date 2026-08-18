from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003

import pytest

from sapphire_flow.flows import collect_bafu_observations as flow_module
from sapphire_flow.types.bafu_observation import BafuObservationRow
from sapphire_flow.types.datetime import ensure_utc


def _row(gauge_code: str, measurement_time: datetime) -> BafuObservationRow:
    return BafuObservationRow(
        gauge_code=gauge_code,
        lindas_kind="river",
        parameter="discharge",
        value=1.0,
        measurement_time=ensure_utc(measurement_time),
    )


def _write_legacy_snapshot(
    base_path: Path, *, clock_named_cycle_at: datetime, rows: list[BafuObservationRow]
) -> None:
    """Simulates a PRE-Plan-176 snapshot: written under a CLOCK-hour-derived
    path/filename that does NOT match the data's own slot — exactly what
    every archived file before this plan looks like (307 of them, per the
    plan doc)."""
    flow_module._write_rows_parquet(  # noqa: SLF001
        base_path, ensure_utc(clock_named_cycle_at), rows
    )


def _write_current_snapshot(base_path: Path, rows: list[BafuObservationRow]) -> None:
    """Simulates a POST-Plan-176 snapshot: written the way the collector
    flow itself now writes it — under its own data-derived modal slot."""
    cycle_at = flow_module._modal_cycle_at(rows)  # noqa: SLF001
    flow_module._write_rows_parquet(base_path, cycle_at, rows)  # noqa: SLF001


def _import_audit_module() -> object:
    try:
        from sapphire_flow.cli import bafu_observation_audit

        return bafu_observation_audit
    except ImportError as exc:  # pragma: no cover - red-first guard
        pytest.fail(
            "sapphire_flow.cli.bafu_observation_audit does not exist yet "
            f"(T8 not implemented): {exc}"
        )


class TestAuditReportsPresentAndMissingSlots:
    def test_reports_present_and_missing_slots_in_window(self, tmp_path: Path) -> None:
        module = _import_audit_module()
        # Present: 09:00 and 09:20. A real gap at 09:10 — never archived.
        _write_current_snapshot(
            tmp_path, [_row("2135", datetime(2026, 7, 21, 9, 0, tzinfo=UTC))]
        )
        _write_current_snapshot(
            tmp_path, [_row("2135", datetime(2026, 7, 21, 9, 20, tzinfo=UTC))]
        )

        report = module.audit_completeness(  # type: ignore[attr-defined]
            tmp_path,
            start=ensure_utc(datetime(2026, 7, 21, 9, 0, tzinfo=UTC)),
            end=ensure_utc(datetime(2026, 7, 21, 9, 30, tzinfo=UTC)),
        )
        assert report.expected_slots == 3  # 09:00, 09:10, 09:20
        assert set(report.present_slots) == {
            ensure_utc(datetime(2026, 7, 21, 9, 0, tzinfo=UTC)),
            ensure_utc(datetime(2026, 7, 21, 9, 20, tzinfo=UTC)),
        }
        assert set(report.missing_slots) == {
            ensure_utc(datetime(2026, 7, 21, 9, 10, tzinfo=UTC)),
        }
        assert report.present_count == 2
        assert report.missing_count == 1

    def test_window_end_is_exclusive(self, tmp_path: Path) -> None:
        module = _import_audit_module()
        _write_current_snapshot(
            tmp_path, [_row("2135", datetime(2026, 7, 21, 9, 30, tzinfo=UTC))]
        )
        report = module.audit_completeness(  # type: ignore[attr-defined]
            tmp_path,
            start=ensure_utc(datetime(2026, 7, 21, 9, 0, tzinfo=UTC)),
            end=ensure_utc(datetime(2026, 7, 21, 9, 30, tzinfo=UTC)),
        )
        # 09:30 sits exactly on `end` — must NOT be counted as expected
        # or present in a [start, end) window.
        assert ensure_utc(datetime(2026, 7, 21, 9, 30, tzinfo=UTC)) not in (
            report.present_slots + report.missing_slots
        )


class TestAuditReDerivesLegacySlotsFromContent:
    """Plan 176 T8: a pre-D2 snapshot's filename (and stored `cycle_at`
    column) is CLOCK-derived, not data-derived — trusting either would
    report nonsense. The audit must re-derive the observed slot from the
    parquet's own `measurement_time` values, using the same modal rule the
    collector flow uses."""

    def test_clock_keyed_legacy_file_is_recovered_by_its_own_data(
        self, tmp_path: Path
    ) -> None:
        module = _import_audit_module()
        # Rows are genuinely from the 09:00 slot, but the OLD clock-derived
        # key filed this snapshot under 10:00 (whatever hour the poll
        # happened to run in) — the exact shape of every pre-Plan-176 file.
        _write_legacy_snapshot(
            tmp_path,
            clock_named_cycle_at=datetime(2026, 7, 21, 10, 0, tzinfo=UTC),
            rows=[_row("2135", datetime(2026, 7, 21, 9, 0, tzinfo=UTC))],
        )

        report = module.audit_completeness(  # type: ignore[attr-defined]
            tmp_path,
            start=ensure_utc(datetime(2026, 7, 21, 9, 0, tzinfo=UTC)),
            end=ensure_utc(datetime(2026, 7, 21, 9, 10, tzinfo=UTC)),
        )
        # The TRUE slot (09:00, recovered from content) is PRESENT.
        assert report.expected_slots == 1
        assert report.present_slots == (
            ensure_utc(datetime(2026, 7, 21, 9, 0, tzinfo=UTC)),
        )
        assert report.missing_slots == ()

    def test_trusting_the_filename_would_have_reported_the_true_slot_missing(
        self, tmp_path: Path
    ) -> None:
        """The same fixture, windowed on the FILENAME's implied slot
        (10:00) instead — proves the legacy file is NOT double-counted
        there; it only ever counts at its true, content-derived slot."""
        module = _import_audit_module()
        _write_legacy_snapshot(
            tmp_path,
            clock_named_cycle_at=datetime(2026, 7, 21, 10, 0, tzinfo=UTC),
            rows=[_row("2135", datetime(2026, 7, 21, 9, 0, tzinfo=UTC))],
        )

        report = module.audit_completeness(  # type: ignore[attr-defined]
            tmp_path,
            start=ensure_utc(datetime(2026, 7, 21, 10, 0, tzinfo=UTC)),
            end=ensure_utc(datetime(2026, 7, 21, 10, 10, tzinfo=UTC)),
        )
        assert report.expected_slots == 1
        assert report.present_slots == ()
        assert report.missing_slots == (
            ensure_utc(datetime(2026, 7, 21, 10, 0, tzinfo=UTC)),
        )
