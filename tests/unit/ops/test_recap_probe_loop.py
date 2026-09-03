"""Unit tests for `_era5_daily_coverage` in ``scripts/recap_probe_loop.py``.

The probe's OWN logic had no test. ``test_recap_probe_wrapper.py`` covers the
launchd wrapper's JSONL-purity gate — what happens to the script's output — not
what the script decides to emit.

These lock the one contract the per-day sweep exists to provide: an ARCHIVE HOLE
(``complete=False``) must stay distinguishable from an OUTAGE (``ok=False``). A
window request cannot express that difference, which is why the window version
was replaced; without a test, nothing stops it being collapsed back.

Records are read back from the real JSONL file rather than by intercepting
`_emit`, so every test also exercises ``json.dumps`` serialisability — the exact
property the wrapper's purity gate depends on, and the one a hand-run of the
probe is least likely to notice breaking.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    import pytest

_SCRIPT = Path(__file__).parent.parent.parent.parent / "scripts" / "recap_probe_loop.py"


def _load_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Path]:
    """Load the script with its JSONL path redirected into tmp.

    ``_LOG_PATH`` is resolved at IMPORT time, so the env var must be set before
    the module is executed — setting it afterwards would silently write to the
    developer's real ``~/Library/Logs`` file.
    """
    log = tmp_path / "probe.jsonl"
    monkeypatch.setenv("RECAP_PROBE_LOG", str(log))
    spec = importlib.util.spec_from_file_location("probe_under_test", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, log


def _records(log: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


class _FakeClient:
    """Stand-in for RecapClient: dates in ``missing`` raise, dates in ``empty``
    return an empty frame, everything else returns one row. Records every
    requested date so a test can assert the client was not called at all."""

    def __init__(
        self,
        *,
        missing: tuple[str, ...] = (),
        empty: tuple[str, ...] = (),
        raises_all: bool = False,
    ) -> None:
        self.calls: list[date] = []
        calls = self.calls

        class _Ecmwf:
            @staticmethod
            def era5_land_reanalysis(*, start_date: date, **_: Any) -> pd.DataFrame:
                calls.append(start_date)
                iso = start_date.isoformat()
                if raises_all or iso in missing:
                    raise RuntimeError("simulated gateway failure")
                if iso in empty:
                    return pd.DataFrame()
                return pd.DataFrame({"value": [1.0]})

        self.ecmwf = _Ecmwf


class TestEra5DailyCoverage:
    def test_hole_is_incomplete_but_still_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The load-bearing case: one absent day inside good coverage must read
        as an ARCHIVE HOLE, not as the endpoint being down. A window request
        reports this identically to a total outage."""
        probe, log = _load_probe(tmp_path, monkeypatch)
        client = _FakeClient(missing=("2026-08-23",))

        probe._era5_daily_coverage(
            "ts", client, "total_precipitation", date(2026, 8, 22), date(2026, 8, 26)
        )

        (record,) = _records(log)
        assert record["ok"] is True
        assert record["complete"] is False
        assert record["days_requested"] == 5
        assert record["days_ok"] == 4
        assert record["days_missing"] == ["2026-08-23"]

    def test_full_coverage_is_ok_and_complete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        probe, log = _load_probe(tmp_path, monkeypatch)

        probe._era5_daily_coverage(
            "ts", _FakeClient(), "2m_temperature", date(2026, 8, 22), date(2026, 8, 26)
        )

        (record,) = _records(log)
        assert record["ok"] is True
        assert record["complete"] is True
        assert record["days_missing"] == []

    def test_total_outage_is_not_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every day failing is the OUTAGE signal — and it must not propagate,
        or one bad variable would abort the whole probe cycle."""
        probe, log = _load_probe(tmp_path, monkeypatch)

        probe._era5_daily_coverage(
            "ts",
            _FakeClient(raises_all=True),
            "total_precipitation",
            date(2026, 8, 22),
            date(2026, 8, 24),
        )

        (record,) = _records(log)
        assert record["ok"] is False
        assert record["complete"] is False
        assert record["days_missing"] == ["2026-08-22", "2026-08-23", "2026-08-24"]
        assert set(record["missing_causes"].values()) == {"RuntimeError"}

    def test_start_after_end_emits_zero_day_summary_without_calling_the_client(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Degenerate window: emit a transparent zero-day record rather than
        hanging or raising. `days_requested == 0` is what makes the otherwise
        odd `ok=False, complete=True` pair unambiguous to a consumer."""
        probe, log = _load_probe(tmp_path, monkeypatch)
        client = _FakeClient()

        probe._era5_daily_coverage(
            "ts", client, "total_precipitation", date(2026, 9, 3), date(2026, 9, 1)
        )

        (record,) = _records(log)
        assert record["days_requested"] == 0
        assert record["ok"] is False
        assert client.calls == []

    def test_empty_frame_counts_as_missing_with_its_own_cause(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A day that returns no rows is absent data, not zero-valued weather —
        and is labelled distinctly from an exception so the two are separable."""
        probe, log = _load_probe(tmp_path, monkeypatch)

        probe._era5_daily_coverage(
            "ts",
            _FakeClient(empty=("2026-08-23",)),
            "total_precipitation",
            date(2026, 8, 22),
            date(2026, 8, 24),
        )

        (record,) = _records(log)
        assert record["days_missing"] == ["2026-08-23"]
        assert record["missing_causes"]["2026-08-23"] == "empty_frame"
