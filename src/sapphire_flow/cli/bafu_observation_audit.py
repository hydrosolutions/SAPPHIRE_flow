"""On-demand completeness audit for the BAFU LINDAS observation archive
(Plan 176 D7/T8).

Reports, for a given window, which 10-minute grid slots the archive holds
and which are missing. Because a Prefect run can succeed having fetched a
slot the archive already holds (a dedup skip), and a run that never started
leaves no state at all, the audit is derived from the archive's OWN
contents — never from Prefect run states.

**Every snapshot's slot is re-derived from its own `measurement_time`
values** (the same modal rule `flows/collect_bafu_observations.py` uses to
key `cycle_at`), never trusted from the filename or the stored `cycle_at`
column. This is necessary, not merely careful: a pre-Plan-176 ("legacy")
snapshot is named by a CLOCK-hour `cycle_at` and its `cycle_at` column was
also stamped from the clock — trusting either would report nonsense for
every legacy file. A post-Plan-176 snapshot's own modal slot already equals
its filename by construction, so re-deriving from content is not an
approximation for those either — it is one code path correct for both.

This is deliberately an ON-DEMAND audit, not automatic detection — no
schedule, no alert. Run it before/after a change to get a before/after
completeness number; wiring it to a schedule would be new monitoring
infrastructure, out of scope here (see the plan's § Goal).

Run via:
    python -m sapphire_flow.cli.bafu_observation_audit \\
        --base-path /data/bafu_observations \\
        --start 2026-08-17T00:00:00Z --end 2026-08-18T00:00:00Z
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import structlog

from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    import argparse

    from sapphire_flow.types.datetime import UtcDatetime

log = structlog.get_logger(__name__)

# Mirrors flows/collect_bafu_observations.py's _GRID_MINUTES — LINDAS
# publishes on this grid.
_GRID_MINUTES = 10


def _truncate_to_grid(ts: UtcDatetime) -> UtcDatetime:
    minute = (ts.minute // _GRID_MINUTES) * _GRID_MINUTES
    return ensure_utc(ts.replace(minute=minute, second=0, microsecond=0))


def _expected_slots(start: UtcDatetime, end: UtcDatetime) -> list[UtcDatetime]:
    """Every grid slot in the HALF-OPEN window ``[start, end)``."""
    slots: list[UtcDatetime] = []
    cur = _truncate_to_grid(start)
    if cur < start:
        cur = ensure_utc(cur + timedelta(minutes=_GRID_MINUTES))
    while cur < end:
        slots.append(cur)
        cur = ensure_utc(cur + timedelta(minutes=_GRID_MINUTES))
    return slots


def _observed_slot(frame: pl.DataFrame) -> UtcDatetime | None:
    """Re-derive a snapshot's TRUE observed slot from its own
    ``measurement_time`` column — the modal timestamp across distinct
    ``(gauge_code, lindas_kind)`` identities, truncated to the grid. Mirrors
    ``flows/collect_bafu_observations.py``'s ``_modal_cycle_at`` exactly,
    but reads from an already-written parquet's rows rather than
    freshly-fetched ones."""
    if frame.is_empty():
        return None
    per_identity = frame.select(
        ["gauge_code", "lindas_kind", "measurement_time"]
    ).unique(subset=["gauge_code", "lindas_kind"], keep="first")
    counts = per_identity["measurement_time"].value_counts()
    max_count = counts["count"].max()
    modal_candidates = counts.filter(pl.col("count") == max_count)["measurement_time"]
    # Tie-break: earliest timestamp among the modal candidates — mirrors the
    # collector flow's own tie-break (see its D2 robustness note).
    modal_ts = ensure_utc(min(modal_candidates.to_list()))
    return _truncate_to_grid(modal_ts)


@dataclass(frozen=True, kw_only=True, slots=True)
class CompletenessReport:
    window_start: UtcDatetime
    window_end: UtcDatetime
    expected_slots: int
    present_slots: tuple[UtcDatetime, ...]
    missing_slots: tuple[UtcDatetime, ...]

    @property
    def present_count(self) -> int:
        return len(self.present_slots)

    @property
    def missing_count(self) -> int:
        return len(self.missing_slots)


def audit_completeness(
    base_path: Path, *, start: UtcDatetime, end: UtcDatetime
) -> CompletenessReport:
    """Walk every parsed parquet snapshot under ``base_path`` and report
    which grid slots in ``[start, end)`` are present vs missing."""
    if end <= start:
        raise ValueError(
            f"audit window end ({end.isoformat()}) must be after start "
            f"({start.isoformat()}) — a zero-width or reversed range has no "
            "expected slots and would report a false-success (0 missing)"
        )
    expected = _expected_slots(start, end)
    observed: set[UtcDatetime] = set()
    for parquet_path in sorted((base_path / "parsed").glob("**/*.parquet")):
        frame = pl.read_parquet(parquet_path)
        slot = _observed_slot(frame)
        if slot is not None and start <= slot < end:
            observed.add(slot)

    present = tuple(s for s in expected if s in observed)
    missing = tuple(s for s in expected if s not in observed)
    return CompletenessReport(
        window_start=start,
        window_end=end,
        expected_slots=len(expected),
        present_slots=present,
        missing_slots=missing,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Audit BAFU LINDAS observation archive completeness (Plan 176 D7). "
            "On-demand only — not wired to any schedule or alert."
        )
    )
    parser.add_argument(
        "--base-path",
        required=True,
        type=Path,
        help="Archive root (contains parsed/ and raw/).",
    )
    parser.add_argument(
        "--start",
        required=True,
        help="ISO-8601 UTC window start, e.g. 2026-08-17T00:00:00Z",
    )
    parser.add_argument(
        "--end", required=True, help="ISO-8601 UTC window end (exclusive)."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from datetime import datetime

    from sapphire_flow.logging import configure_cli_logging

    configure_cli_logging()
    args = _parse_args(argv)
    start = ensure_utc(datetime.fromisoformat(args.start.replace("Z", "+00:00")))
    end = ensure_utc(datetime.fromisoformat(args.end.replace("Z", "+00:00")))

    try:
        report = audit_completeness(args.base_path, start=start, end=end)
    except ValueError as exc:
        # A swapped/equal --start/--end is a CLI usage error, not a
        # completeness result — must not fall through to `0 missing` == exit 0.
        raise SystemExit(f"bafu_observation_audit: {exc}") from exc

    log.info(
        "bafu_observation_audit.complete",
        window_start=report.window_start.isoformat(),
        window_end=report.window_end.isoformat(),
        expected_slots=report.expected_slots,
        present_slots=report.present_count,
        missing_slots=report.missing_count,
    )
    for slot in report.missing_slots:
        log.warning("bafu_observation_audit.missing_slot", slot=slot.isoformat())

    return 0 if report.missing_count == 0 else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
