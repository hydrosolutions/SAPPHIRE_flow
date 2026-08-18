from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003

import pytest

from sapphire_flow.flows import collect_bafu_observations as flow_module
from sapphire_flow.types.bafu_observation import BafuObservationRow
from sapphire_flow.types.datetime import ensure_utc


def _row(
    gauge_code: str, measurement_time: datetime, *, parameter: str = "discharge"
) -> BafuObservationRow:
    return BafuObservationRow(
        gauge_code=gauge_code,
        lindas_kind="river",
        parameter=parameter,  # type: ignore[arg-type]
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


class TestInvalidWindowIsRejected:
    """A swapped/equal CLI range must not silently pass as a complete
    window: `_expected_slots` on an empty-or-reversed range returns zero
    slots, so `missing_count == 0` and `main()` would otherwise exit 0 —
    a false-success completeness audit."""

    def test_reversed_range_raises_value_error(self, tmp_path: Path) -> None:
        module = _import_audit_module()
        with pytest.raises(ValueError, match="must be after start"):
            module.audit_completeness(  # type: ignore[attr-defined]
                tmp_path,
                start=ensure_utc(datetime(2026, 7, 21, 10, 0, tzinfo=UTC)),
                end=ensure_utc(datetime(2026, 7, 21, 9, 0, tzinfo=UTC)),
            )

    def test_equal_start_and_end_raises_value_error(self, tmp_path: Path) -> None:
        module = _import_audit_module()
        same = ensure_utc(datetime(2026, 7, 21, 9, 0, tzinfo=UTC))
        with pytest.raises(ValueError, match="must be after start"):
            module.audit_completeness(tmp_path, start=same, end=same)  # type: ignore[attr-defined]

    def test_main_exits_nonzero_for_reversed_cli_range(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = _import_audit_module()
        # Stub `configure_cli_logging` — `main()` calls it, and reconfiguring
        # the process-global structlog/logging setup here would break later
        # log-assertion tests in the full suite (test pollution; mirrors the
        # same stub in tests/unit/cli/test_access_tokens.py).
        monkeypatch.setattr(
            "sapphire_flow.logging.configure_cli_logging", lambda *a, **k: None
        )
        with pytest.raises(SystemExit) as exc_info:
            module.main(  # type: ignore[attr-defined]
                [
                    "--base-path",
                    str(tmp_path),
                    "--start",
                    "2026-07-21T10:00:00Z",
                    "--end",
                    "2026-07-21T09:00:00Z",
                ]
            )
        assert exc_info.value.code not in (0, None)

    def test_main_exits_nonzero_for_equal_cli_range(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = _import_audit_module()
        monkeypatch.setattr(
            "sapphire_flow.logging.configure_cli_logging", lambda *a, **k: None
        )
        with pytest.raises(SystemExit) as exc_info:
            module.main(  # type: ignore[attr-defined]
                [
                    "--base-path",
                    str(tmp_path),
                    "--start",
                    "2026-07-21T09:00:00Z",
                    "--end",
                    "2026-07-21T09:00:00Z",
                ]
            )
        assert exc_info.value.code not in (0, None)


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


class TestObservedSlotIsIdentityWeightedNotRowWeighted:
    """`_observed_slot` mirrors the collector flow's `_modal_cycle_at`: the
    mode is over DISTINCT (gauge_code, lindas_kind) identities, not raw
    rows. Every other fixture in this file gives each identity exactly one
    row, so a subtly wrong row-weighted mode would still pass them by
    coincidence. Here the AHEAD identity carries THREE parameter rows (all
    at its own slot) while TWO SEPARATE bulk identities each carry only ONE
    row on the earlier slot — row-weighted counting would wrongly pick the
    ahead identity's slot (3 rows > 2 rows); identity-weighted counting
    correctly picks the bulk slot (2 identities > 1 identity)."""

    def test_ahead_identity_with_several_rows_does_not_outvote_the_bulk(
        self, tmp_path: Path
    ) -> None:
        module = _import_audit_module()
        bulk_ts = datetime(2026, 7, 21, 12, 10, tzinfo=UTC)
        ahead_ts = datetime(2026, 7, 21, 12, 20, tzinfo=UTC)  # one slot AHEAD
        rows = [
            _row("2135", bulk_ts, parameter="discharge"),
            _row("2200", bulk_ts, parameter="discharge"),
            _row("9999", ahead_ts, parameter="discharge"),
            _row("9999", ahead_ts, parameter="water_level"),
            _row("9999", ahead_ts, parameter="water_temperature"),
        ]
        flow_module._write_rows_parquet(  # noqa: SLF001
            tmp_path, ensure_utc(bulk_ts), rows
        )

        report = module.audit_completeness(  # type: ignore[attr-defined]
            tmp_path,
            start=ensure_utc(datetime(2026, 7, 21, 12, 0, tzinfo=UTC)),
            end=ensure_utc(datetime(2026, 7, 21, 12, 30, tzinfo=UTC)),
        )
        assert report.present_slots == (ensure_utc(bulk_ts),)
        assert ensure_utc(ahead_ts) not in report.present_slots
