"""Plan 192 Stage B — unit tests for scripts/nepal_forcing_run.py.

The script is not importable as a package module (``scripts/`` is never in the
image and is not on the path), so it is loaded from its file — the same
file-path convention the other ops tests use.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

_SCRIPT = (
    Path(__file__).parent.parent.parent.parent / "scripts" / "nepal_forcing_run.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("nepal_forcing_run", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


nfr = _load()


class TestResolveCycle:
    def test_floors_to_todays_00z(self) -> None:
        got = nfr.resolve_cycle(datetime(2026, 8, 20, 16, 42, 9, tzinfo=UTC))
        assert got == datetime(2026, 8, 20, 0, 0, tzinfo=UTC)

    def test_already_00z_is_unchanged(self) -> None:
        got = nfr.resolve_cycle(datetime(2026, 8, 20, 0, 0, tzinfo=UTC))
        assert got == datetime(2026, 8, 20, 0, 0, tzinfo=UTC)

    def test_does_not_floor_to_the_6h_cadence(self) -> None:
        """The whole point of D5: 08:00 must NOT become the short 06Z run."""
        got = nfr.resolve_cycle(datetime(2026, 8, 20, 8, 0, tzinfo=UTC))
        assert got.hour == 0

    def test_non_utc_input_is_converted_before_flooring(self) -> None:
        from datetime import timedelta, timezone

        # 01:30 at +05:45 (Nepal) is 2026-08-19T19:45Z — the 19th's cycle.
        npt = timezone(timedelta(hours=5, minutes=45))
        got = nfr.resolve_cycle(datetime(2026, 8, 20, 1, 30, tzinfo=npt))
        assert got == datetime(2026, 8, 19, 0, 0, tzinfo=UTC)


class TestFetchFailure:
    """`fetch_failure` is the guard against a same-cycle re-run reporting the
    PREVIOUS run's rows as a fresh success. `summarize_stored` queries the
    cycle's existing rows, so without this the dead-man stays green through a
    real Gateway outage — the 2026-08-29/30 failure mode exactly."""

    class _Outcome:
        """Stand-in for `_NwpFetchOutcome`. Only the fields the guard reads."""

        def __init__(
            self,
            *,
            nwp_unavailable: bool = False,
            fetch_failure_reason: str | None = None,
        ) -> None:
            self.nwp_unavailable = nwp_unavailable
            self.fetch_failure_reason = fetch_failure_reason

    def test_healthy_outcome_is_not_a_failure(self) -> None:
        assert nfr.fetch_failure(self._Outcome()) is None

    def test_none_outcome_is_a_failure(self) -> None:
        assert nfr.fetch_failure(None) == "outcome_none"

    def test_generic_failure_reason_is_reported(self) -> None:
        got = nfr.fetch_failure(self._Outcome(fetch_failure_reason="transport_error"))
        assert got == "transport_error"

    def test_nwp_unavailable_is_a_failure(self) -> None:
        """THE regression case. `source_data_missing` sets `nwp_unavailable`
        and leaves `fetch_failure_reason` None, so the Plan 223 check alone
        passed it through as success."""
        got = nfr.fetch_failure(self._Outcome(nwp_unavailable=True))
        assert got == "nwp_unavailable"

    def test_generic_reason_takes_precedence_over_unavailable(self) -> None:
        """Both set: report the specific cause, not the generic flag."""
        got = nfr.fetch_failure(
            self._Outcome(nwp_unavailable=True, fetch_failure_reason="boom")
        )
        assert got == "boom"

    def test_outcome_lacking_the_newer_field_still_reports_unavailable(self) -> None:
        """Host script and container image have repeatedly been at different
        versions here. An outcome with no `fetch_failure_reason` attribute must
        degrade to the older signal, not raise AttributeError mid-run."""

        class _OldOutcome:
            nwp_unavailable = True

        assert nfr.fetch_failure(_OldOutcome()) == "nwp_unavailable"


class TestClassify:
    def test_full_horizon_run_is_ok(self) -> None:
        assert nfr.classify({"rows": 8568, "horizon_days": 14.75}) == (True, None)

    def test_zero_rows_is_not_ok(self) -> None:
        ok, reason = nfr.classify({"rows": 0, "horizon_days": 14.75})
        assert ok is False
        assert reason == "no_rows_stored"

    def test_short_horizon_is_not_ok(self) -> None:
        """A 06Z-style 2-day run stores rows but is exactly what we must catch."""
        ok, reason = nfr.classify({"rows": 204, "horizon_days": 2.0})
        assert ok is False
        assert reason is not None
        assert "short_horizon" in reason

    def test_missing_horizon_is_not_ok(self) -> None:
        ok, reason = nfr.classify({"rows": 10})
        assert ok is False
        assert reason == "no_horizon"


class TestBuildRecord:
    def _cycle(self) -> datetime:
        return datetime(2026, 8, 20, 0, 0, tzinfo=UTC)

    def test_successful_run_record_is_json_serialisable_and_ok(self) -> None:
        record = nfr.build_record(
            run_ts="2026-08-20T16:00:00+00:00",
            cycle=self._cycle(),
            stored={"rows": 8568, "members": 51, "steps": 84, "horizon_days": 14.75},
            duration_s=22.94,
            error=None,
        )
        assert record["ok"] is True
        assert record["rows"] == 8568
        assert record["members"] == 51
        assert record["cycle_requested"] == "2026-08-20T00:00:00+00:00"
        assert record["duration_s"] == 22.9
        json.loads(json.dumps(record, default=str))

    def test_error_record_carries_code_and_is_not_ok(self) -> None:
        exc = RuntimeError("No IFS dataset found for run_date 2026-08-20")
        exc.code = "source_data_missing"  # type: ignore[attr-defined]
        record = nfr.build_record(
            run_ts="2026-08-20T16:00:00+00:00",
            cycle=self._cycle(),
            stored=None,
            duration_s=1.5,
            error=exc,
        )
        assert record["ok"] is False
        assert record["error_code"] == "source_data_missing"
        assert record["error_type"] == "RuntimeError"
        assert "No IFS dataset" in record["error_msg"]

    def test_error_record_never_leaks_row_stats(self) -> None:
        record = nfr.build_record(
            run_ts="t",
            cycle=self._cycle(),
            stored={"rows": 99},
            duration_s=1.0,
            error=ValueError("boom"),
        )
        assert "rows" not in record

    def test_short_horizon_run_records_the_reason(self) -> None:
        record = nfr.build_record(
            run_ts="t",
            cycle=self._cycle(),
            stored={"rows": 204, "horizon_days": 2.0},
            duration_s=3.0,
            error=None,
        )
        assert record["ok"] is False
        assert "short_horizon" in record["degraded_reason"]

    def test_long_error_messages_are_truncated(self) -> None:
        record = nfr.build_record(
            run_ts="t",
            cycle=self._cycle(),
            stored=None,
            duration_s=1.0,
            error=ValueError("x" * 1000),
        )
        assert len(record["error_msg"]) == 300


class TestEmit:
    def test_writes_one_jsonl_line_and_a_summary(self, tmp_path, capsys) -> None:
        sink = tmp_path / "out.jsonl"
        record = {"run_ts": "t", "ok": True, "rows": 8568}
        nfr.emit(record, str(sink))
        nfr.emit(record, str(sink))
        lines = sink.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["rows"] == 8568
        assert "nepal-forcing" in capsys.readouterr().out

    def test_dev_stderr_is_written_not_reopened(self, capsys) -> None:
        """Reopening /dev/stderr fails as non-root under `docker run` — the
        wrapper's sink must go to the inherited stream."""
        nfr.emit({"run_ts": "t", "ok": True, "rows": 1}, "/dev/stderr")
        captured = capsys.readouterr()
        assert json.loads(captured.err.strip())["rows"] == 1

    def test_summary_surfaces_the_failure_reason(self, tmp_path, capsys) -> None:
        nfr.emit(
            {"run_ts": "t", "ok": False, "error_code": "source_data_missing"},
            str(tmp_path / "o"),
        )
        assert "source_data_missing" in capsys.readouterr().out


class TestExitCodes:
    def test_codes_are_distinct(self) -> None:
        assert {nfr.EXIT_OK, nfr.EXIT_RUN_FAILED, nfr.EXIT_CONFIG} == {0, 1, 2}


class TestSnowSeverity:
    """Plan 219 D4: a snow-only gap is RECORDED, never PAGED.

    This feed exists for IFS operating evidence; snow is an additional
    observation on top. Clearing `ok` for a snow gap would page the dead-man as
    loudly as a total IFS outage, and a monitor that cries wolf stops being
    believed — the same reasoning that kept the IFS path paging.
    """

    def test_snow_variables_are_all_three(self) -> None:
        assert frozenset({"snow_depth", "snowmelt", "swe"}) == nfr._SNOW_VARIABLES

    def test_a_snow_gap_does_not_clear_ok(self) -> None:
        """The regression guard: `ok` is decided by `classify` on the IFS rows
        alone. Snow degradation is recorded alongside it, never folded into it."""
        stored = {"rows": 8568, "horizon_days": 14.75}
        ok, reason = nfr.classify(stored)
        assert ok is True
        assert reason is None

    def test_an_ifs_failure_still_fails(self) -> None:
        ok, reason = nfr.classify({"rows": 0, "horizon_days": 14.75})
        assert ok is False
        assert reason == "no_rows_stored"
