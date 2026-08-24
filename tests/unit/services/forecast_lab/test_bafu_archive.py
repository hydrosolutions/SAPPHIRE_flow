"""Plan 198 T2a — BAFU forecast archive reader.

AC8/F3: the percentile-band polygon splits forward (ascending valid_time) =
p25 (LOWER edge) then backward (descending valid_time, reversed to
ascending) = p75 (UPPER edge). An earlier draft of the domain comment this
reader implements had the two edges backwards. `TestPercentileOrientation`
below is directional: it fails under the inverted (first-half -> p75, D6's
rejected earlier draft) mapping and passes only under the corrected one.

Uses the CHECKED-IN reference payload
`tests/fixtures/reference/bafu_q_forecast_2135.json` (a real BAFU
`q_forecast` response for station 2135, issued 2026-07-10T07:00:00+02:00) —
independent corroboration inside the repo, per F3's own citation of this
same fixture (a different station, a different polygon length, same
orientation).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from sapphire_flow.services.forecast_lab.bafu_archive import (
    BafuArchiveRun,
    BafuRunUnavailable,
    find_latest_run_issued_at,
    read_latest_bafu_run,
)
from sapphire_flow.types.datetime import ensure_utc

_ROOT = Path(__file__).resolve().parents[4]
_FIXTURE = _ROOT / "tests/fixtures/reference/bafu_q_forecast_2135.json"

_SCHEMA = {
    "station_key": pl.Utf8,
    "metric": pl.Utf8,
    "unit": pl.Utf8,
    "issued_at": pl.Datetime("us", "UTC"),
    "produced_at": pl.Datetime("us", "UTC"),
    "valid_time": pl.Datetime("us", "UTC"),
    "trace_name": pl.Utf8,
    "point_index": pl.Int64,
    "value": pl.Float64,
}


def _rows_from_fixture(
    station_key: str,
    *,
    issued_at: datetime,
    produced_at: datetime,
    traces: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    payload = json.loads(_FIXTURE.read_text())
    rows: list[dict[str, Any]] = []
    for trace in payload["plot"]["data"]:
        name = trace["name"]
        xs = trace["x"]
        ys = trace["y"]
        if traces is not None and name in traces:
            xs, ys = traces[name]
        unit = trace.get("meta", {}).get("unit") or "m³/s"
        for i, (x, y) in enumerate(zip(xs, ys, strict=True)):
            rows.append(
                {
                    "station_key": station_key,
                    "metric": "discharge_ms",
                    "unit": unit,
                    "issued_at": ensure_utc(issued_at),
                    "produced_at": ensure_utc(produced_at),
                    "valid_time": ensure_utc(datetime.fromisoformat(x))
                    if isinstance(x, str)
                    else x,
                    "trace_name": name,
                    "point_index": i,
                    "value": y,
                }
            )
    return rows


def _write_run(
    base_path: Path,
    station_key: str,
    issued_at: datetime,
    rows: list[dict[str, Any]],
) -> Path:
    frame = pl.DataFrame(rows, schema=_SCHEMA)
    parsed_dir = base_path / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    stamp = ensure_utc(issued_at).strftime("%Y%m%dT%H%M%SZ")
    path = parsed_dir / f"{station_key}_q_forecast_{stamp}.parquet"
    frame.write_parquet(path)
    return path


_ISSUED_AT = datetime(2026, 7, 10, 7, 0, 0, tzinfo=UTC)
_PRODUCED_AT = datetime(2026, 7, 10, 9, 0, 0, tzinfo=UTC)
_NOW = datetime(2026, 7, 10, 10, 0, 0, tzinfo=UTC)


def _write_fixture_run(base_path: Path, station_key: str = "2135") -> BafuArchiveRun:
    rows = _rows_from_fixture(
        station_key, issued_at=_ISSUED_AT, produced_at=_PRODUCED_AT
    )
    _write_run(base_path, station_key, _ISSUED_AT, rows)
    result = read_latest_bafu_run(base_path, station_key, now=ensure_utc(_NOW))
    assert isinstance(result, BafuArchiveRun)
    return result


class TestPercentileOrientation:
    """F3/D6/AC8 — directional: must fail under the inverted mapping."""

    def test_forward_half_is_the_lower_p25_edge(self, tmp_path: Path) -> None:
        run = _write_fixture_run(tmp_path)
        last = run.points[-1]
        # Measured directly from the fixture (see module docstring): the
        # FORWARD half's last value is 102.8, the BACKWARD half's (reversed)
        # is 105.1 — forward is the lower series.
        assert last.p25 == pytest.approx(102.8)
        assert last.p75 == pytest.approx(105.1)
        assert last.p25 < last.p75

    def test_p25_never_exceeds_p75_across_the_whole_horizon(
        self, tmp_path: Path
    ) -> None:
        run = _write_fixture_run(tmp_path)
        assert all(p.p25 <= p.p75 for p in run.points)

    def test_min_max_orientation_matches_f4(self, tmp_path: Path) -> None:
        # F4: "Min. / Max." (periods) is the upper envelope, "Min / Max" is
        # the lower — verified independently of the percentile band.
        run = _write_fixture_run(tmp_path)
        assert all(p.minimum <= p.maximum for p in run.points)
        last = run.points[-1]
        assert last.minimum == pytest.approx(101.2)
        assert last.maximum == pytest.approx(107.9)


class TestEnvelopeShape:
    def test_point_count_matches_the_horizon_grid(self, tmp_path: Path) -> None:
        run = _write_fixture_run(tmp_path)
        assert run.point_count == 116  # half = (233 - 1) // 2

    def test_full_monotonic_ordering_holds(self, tmp_path: Path) -> None:
        run = _write_fixture_run(tmp_path)
        for p in run.points:
            assert p.minimum <= p.p25 <= p.median <= p.p75 <= p.maximum
        assert run.quality_flags == ()

    def test_issued_at_and_inventory_produced_at_stay_distinct(
        self, tmp_path: Path
    ) -> None:
        run = _write_fixture_run(tmp_path)
        assert run.issued_at == ensure_utc(_ISSUED_AT)
        assert run.inventory_produced_at == ensure_utc(_PRODUCED_AT)
        assert run.issued_at != run.inventory_produced_at

    def test_points_are_ordered_ascending_by_valid_time(self, tmp_path: Path) -> None:
        run = _write_fixture_run(tmp_path)
        times = [p.valid_time for p in run.points]
        assert times == sorted(times)


class TestQualityFlags:
    def test_non_monotonic_envelope_is_flagged_not_dropped(
        self, tmp_path: Path
    ) -> None:
        payload = json.loads(_FIXTURE.read_text())
        traces = {t["name"]: (t["x"], list(t["y"])) for t in payload["plot"]["data"]}
        # Corrupt the Median trace at one point so it exceeds its own p75 —
        # a VALUE-level anomaly, not a geometry one; the run must still be
        # emitted (available), just flagged.
        median_x, median_y = traces["Median"]
        median_y = list(median_y)
        median_y[0] = 9999.0
        traces["Median"] = (median_x, median_y)
        rows = _rows_from_fixture(
            "2135", issued_at=_ISSUED_AT, produced_at=_PRODUCED_AT, traces=traces
        )
        _write_run(tmp_path, "2135", _ISSUED_AT, rows)

        result = read_latest_bafu_run(tmp_path, "2135", now=ensure_utc(_NOW))
        assert isinstance(result, BafuArchiveRun)
        assert "non_monotonic_envelope" in result.quality_flags
        assert result.points[0].median == 9999.0  # retained, not dropped


class TestUnreconstructableGeometry:
    def test_mismatched_halves_return_parse_error_not_available(
        self, tmp_path: Path
    ) -> None:
        payload = json.loads(_FIXTURE.read_text())
        traces = {t["name"]: (t["x"], list(t["y"])) for t in payload["plot"]["data"]}
        band_x, band_y = traces[_percentile_trace_name(payload)]
        # Break the geometry: shift one backward-half timestamp so the
        # reversed-backward sequence no longer equals the forward sequence.
        band_x = list(band_x)
        band_x[120] = "2099-01-01T00:00:00.000+02:00"
        traces[_percentile_trace_name(payload)] = (band_x, band_y)
        rows = _rows_from_fixture(
            "2135", issued_at=_ISSUED_AT, produced_at=_PRODUCED_AT, traces=traces
        )
        _write_run(tmp_path, "2135", _ISSUED_AT, rows)

        result = read_latest_bafu_run(tmp_path, "2135", now=ensure_utc(_NOW))
        assert isinstance(result, BafuRunUnavailable)
        assert result.reason == "parse_error"

    def test_even_point_count_returns_parse_error(self, tmp_path: Path) -> None:
        payload = json.loads(_FIXTURE.read_text())
        traces = {t["name"]: (t["x"], list(t["y"])) for t in payload["plot"]["data"]}
        name = _percentile_trace_name(payload)
        band_x, band_y = traces[name]
        traces[name] = (list(band_x)[:-1], list(band_y)[:-1])  # drop closer -> even n
        rows = _rows_from_fixture(
            "2135", issued_at=_ISSUED_AT, produced_at=_PRODUCED_AT, traces=traces
        )
        _write_run(tmp_path, "2135", _ISSUED_AT, rows)

        result = read_latest_bafu_run(tmp_path, "2135", now=ensure_utc(_NOW))
        assert isinstance(result, BafuRunUnavailable)
        assert result.reason == "parse_error"


def _percentile_trace_name(payload: dict[str, Any]) -> str:
    for trace in payload["plot"]["data"]:
        if "Percentile" in trace["name"]:
            name: str = trace["name"]
            return name
    raise AssertionError("fixture has no percentile trace")


class TestNoMatchingRun:
    def test_missing_station_returns_no_matching_run(self, tmp_path: Path) -> None:
        result = read_latest_bafu_run(tmp_path, "9999", now=ensure_utc(_NOW))
        assert isinstance(result, BafuRunUnavailable)
        assert result.reason == "no_matching_run"

    def test_run_outside_lookback_window_is_not_found(self, tmp_path: Path) -> None:
        rows = _rows_from_fixture(
            "2135", issued_at=_ISSUED_AT, produced_at=_PRODUCED_AT
        )
        _write_run(tmp_path, "2135", _ISSUED_AT, rows)
        far_future_now = ensure_utc(_ISSUED_AT + timedelta(days=30))
        result = read_latest_bafu_run(
            tmp_path, "2135", now=far_future_now, lookback=timedelta(days=1)
        )
        assert isinstance(result, BafuRunUnavailable)
        assert result.reason == "no_matching_run"


class TestLatestRunSelection:
    def test_picks_the_newest_run_not_the_first_found(self, tmp_path: Path) -> None:
        older = _ISSUED_AT
        newer = ensure_utc(_ISSUED_AT + timedelta(hours=6))
        rows_older = _rows_from_fixture(
            "2135", issued_at=older, produced_at=_PRODUCED_AT
        )
        rows_newer = _rows_from_fixture(
            "2135", issued_at=newer, produced_at=_PRODUCED_AT
        )
        _write_run(tmp_path, "2135", older, rows_older)
        _write_run(tmp_path, "2135", newer, rows_newer)

        picked = find_latest_run_issued_at(
            tmp_path, "2135", now=ensure_utc(newer + timedelta(hours=1))
        )
        assert picked == newer

    def test_no_run_at_all_returns_none(self, tmp_path: Path) -> None:
        assert find_latest_run_issued_at(tmp_path, "2135", now=ensure_utc(_NOW)) is None
