# ruff: noqa: F811 -- Shared selector fixture.
import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from sapphire_flow.services.evaluation_forecasts import read_evaluation_forecasts
from sapphire_flow.services.swiss_overview import (
    candidate_peak,
    median_peak,
    read_bafu_headlines,
    read_current_headlines,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ForecastStatus, QcStatus
from sapphire_flow.types.swiss_overview import HeadlineAbsence
from tests.fakes.bafu_archive import write_raw_evidence
from tests.unit.services.forecast_lab.test_bafu_archive import (
    _ISSUED_AT,
    _NOW,
    _PRODUCED_AT,
    _rows_from_fixture,
    _write_run,
)
from tests.unit.services.test_evaluation_forecasts import case  # noqa: F401


class TestCurrentAuthorityParity:
    @pytest.mark.parametrize(
        "variant", ["current", "superseded", "raw", "missing", "expired", "yesterday"]
    )
    def test_panel_current_selection_matches(
        self, case: SimpleNamespace, variant: str
    ) -> None:
        as_of = ensure_utc(case.issue + timedelta(hours=1))
        if variant == "superseded":
            case.records.records = [
                replace(case.record, status=ForecastStatus.SUPERSEDED)
            ]
        elif variant == "raw":
            case.records.records = [replace(case.record, qc_status=QcStatus.RAW)]
        elif variant == "missing":
            case.records.records = []
        elif variant == "expired":
            as_of = ensure_utc(as_of + timedelta(hours=5))
        elif variant == "yesterday":
            as_of = ensure_utc(as_of + timedelta(days=1))
        case.records.fetch_forecasts_by_ids = lambda ids: {
            r.id: r for r in case.records.records if r.id in ids
        }
        case.stores.rejected.fetch_rejected_forecasts_by_ids = lambda ids: {}
        panel = read_evaluation_forecasts(
            station=case.station,
            settings=case.settings,
            assignments=(),
            stores=case.stores,
            as_of=as_of,
        )
        overview = read_current_headlines(
            station_ids=[case.station.id],
            settings=case.settings,
            stores=case.stores,
            as_of=as_of,
        )[case.station.id]
        assert (
            overview.candidate.reference.output_id if overview.candidate else None
        ) == (
            panel.parameters[0].candidate.reference.output_id
            if panel.parameters[0].candidate
            else None
        )

    def test_peak_is_median_not_member_maximum(self, case: SimpleNamespace) -> None:
        import polars as pl

        record = replace(
            case.record,
            ensemble=replace(
                case.record.ensemble,
                values=case.record.ensemble.values.with_columns(
                    pl.Series("value", [10.0, 30.0, 10.0, 90.0])
                ),
            ),
        )
        case.records.records = [record]
        panel = read_evaluation_forecasts(
            station=case.station,
            settings=case.settings,
            assignments=(),
            stores=case.stores,
            as_of=case.issue + timedelta(hours=1),
        )
        peak = candidate_peak(panel.parameters[0].candidate, as_of=case.issue)
        assert peak.value == 50.0
        assert peak.valid_time == case.issue + timedelta(hours=2)

    def test_daily_interval_and_earliest_tie(self, case: SimpleNamespace) -> None:
        first = ensure_utc(case.issue)
        second = ensure_utc(first + timedelta(days=1))
        peak = median_peak(
            [(second, 12.0), (first, 12.0)],
            step=timedelta(days=1),
            as_of=ensure_utc(first + timedelta(hours=6)),
        )
        assert peak.valid_time == first
        assert (
            median_peak([(first, 12.0)], step=timedelta(days=1), as_of=second) is None
        )


class TestBafuHeadlines:
    @pytest.mark.parametrize(
        "fault",
        [None, "missing_unit", "wrong_unit", "wrong_station", "future_produced"],
    )
    def test_strict_archive_and_one_enumeration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str | None
    ) -> None:
        rows = _rows_from_fixture(
            "2135", issued_at=_ISSUED_AT, produced_at=_PRODUCED_AT
        )
        if fault == "missing_unit":
            rows[0]["unit"] = None
        elif fault == "wrong_unit":
            rows[0]["unit"] = "ft3/s"
        elif fault == "wrong_station":
            rows[0]["station_key"] = "other"
        elif fault == "future_produced":
            for row in rows:
                row["produced_at"] = ensure_utc(_NOW + timedelta(days=1))
        _write_run(tmp_path, "2135", _ISSUED_AT, rows)
        write_raw_evidence(tmp_path, "2135", _ISSUED_AT, rows)
        enumerations = []
        original = Path.iterdir

        def listing(path: Path):
            enumerations.append(path)
            return original(path)

        monkeypatch.setattr(Path, "iterdir", listing)
        result = read_bafu_headlines(
            tmp_path, ["2135", "missing"], as_of=ensure_utc(_NOW)
        )
        assert len(enumerations) == 1
        assert result["missing"] is HeadlineAbsence.MISSING_ARCHIVE
        if fault:
            assert result["2135"] is HeadlineAbsence.INVALID_SOURCE_DATA
        else:
            run, peak = result["2135"]
            assert peak.value == max(
                p.median
                for p in run.points
                if p.valid_time + timedelta(seconds=run.native_step_seconds)
                > ensure_utc(_NOW)
            )


class TestRawArchiveEvidence:
    @pytest.mark.parametrize(
        "fault",
        [
            None,
            "missing",
            "default_only",
            "wrong_unit",
            "wrong_axis",
            "wrong_issue",
            "wrong_points",
            "oversize",
            "null_median",
            "nonmonotonic",
            "expired",
        ],
    )
    def test_component_evidence(self, tmp_path: Path, fault: str | None) -> None:
        rows = _rows_from_fixture(
            "2135", issued_at=_ISSUED_AT, produced_at=_PRODUCED_AT
        )
        if fault in {"null_median", "nonmonotonic"}:
            for row in rows:
                if row["trace_name"] == "Median":
                    row["value"] = None if fault == "null_median" else -999.0
        _write_run(tmp_path, "2135", _ISSUED_AT, rows)
        raw = write_raw_evidence(tmp_path, "2135", _ISSUED_AT, rows)
        payload = json.loads(raw.read_text())
        if fault == "missing":
            raw.unlink()
        elif fault == "oversize":
            raw.write_bytes(b" " * (1024 * 1024 + 1))
        else:
            trace = next(t for t in payload["plot"]["data"] if t["name"] == "Median")
            if fault == "default_only":
                for t in payload["plot"]["data"]:
                    t["meta"]["unit"] = ""
            elif fault == "wrong_unit":
                trace["meta"]["unit"] = "ft3/s"
            elif fault == "wrong_axis":
                trace["yaxis"] = "y2"
            elif fault == "wrong_issue":
                payload["plot"]["layout"]["annotations"][0]["x"] = (
                    _ISSUED_AT - timedelta(hours=1)
                ).isoformat()
            elif fault == "wrong_points":
                trace["y"][0] += 1
            raw.write_text(json.dumps(payload))
        as_of = _ISSUED_AT + timedelta(days=6) if fault == "expired" else _NOW
        result = read_bafu_headlines(
            tmp_path, ["2135", "missing"], as_of=ensure_utc(as_of)
        )
        assert result["missing"] is HeadlineAbsence.MISSING_ARCHIVE
        if fault is None:
            assert not isinstance(result["2135"], HeadlineAbsence)
        else:
            assert result["2135"] is (
                HeadlineAbsence.EXPIRED_HORIZON
                if fault == "expired"
                else HeadlineAbsence.INVALID_SOURCE_DATA
            )

    @pytest.mark.parametrize(
        "stamp", ["20261340T250000Z", "20260710T096000Z", "20260700T100000Z"]
    )
    def test_malformed_names_are_not_runs(self, tmp_path: Path, stamp: str) -> None:
        parsed = tmp_path / "parsed"
        parsed.mkdir()
        (parsed / f"2135_q_forecast_{stamp}.parquet").touch()
        result = read_bafu_headlines(
            tmp_path, ["2135", "other"], as_of=ensure_utc(_NOW)
        )
        assert result == {
            "2135": HeadlineAbsence.MISSING_ARCHIVE,
            "other": HeadlineAbsence.MISSING_ARCHIVE,
        }

    def test_corrupt_selected_latest_never_falls_back(self, tmp_path: Path) -> None:
        rows = _rows_from_fixture(
            "2135", issued_at=_ISSUED_AT, produced_at=_PRODUCED_AT
        )
        _write_run(tmp_path, "2135", _ISSUED_AT, rows)
        write_raw_evidence(tmp_path, "2135", _ISSUED_AT, rows)
        later = _ISSUED_AT + timedelta(hours=1)
        (
            tmp_path
            / "parsed"
            / f"2135_q_forecast_{later.strftime('%Y%m%dT%H%M%SZ')}.parquet"
        ).write_bytes(b"corrupt")
        assert (
            read_bafu_headlines(tmp_path, ["2135"], as_of=ensure_utc(_NOW))["2135"]
            is HeadlineAbsence.SOURCE_READ_FAILED
        )


class TestExactDailyPeaks:
    @pytest.mark.parametrize("rejected", [False, True])
    @pytest.mark.parametrize("quantiles", [False, True])
    def test_exact_daily_peak_and_tie(
        self, case: SimpleNamespace, rejected: bool, quantiles: bool
    ) -> None:
        import polars as pl

        from sapphire_flow.types.enums import EnsembleRepresentation
        from sapphire_flow.types.rejected_forecast import PersistedRejectedForecast

        candidate = (
            read_evaluation_forecasts(
                station=case.station,
                settings=case.settings,
                assignments=(),
                stores=case.stores,
                as_of=case.issue + timedelta(hours=1),
            )
            .parameters[0]
            .candidate
        )
        levels = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95) if quantiles else (0, 1)
        key = "quantile" if quantiles else "member_id"
        representation = (
            EnsembleRepresentation.QUANTILES
            if quantiles
            else EnsembleRepresentation.MEMBERS
        )
        rows = [
            {
                "valid_time": case.issue + timedelta(days=day),
                key: level,
                "value": (12.0 if level == 0.5 else 99.0)
                if quantiles
                else (10.0 if level == 0 else 14.0),
            }
            for day in range(2)
            for level in levels
        ]
        ensemble = replace(
            case.record.ensemble,
            representation=representation,
            time_step=timedelta(days=1),
            values=pl.DataFrame(rows).with_columns(
                pl.col("valid_time").cast(pl.Datetime("us", "UTC"))
            ),
        )
        record = replace(case.record, representation=representation, ensemble=ensemble)
        if rejected:
            record = PersistedRejectedForecast(
                id=case.record.id,
                attempt_id=case.summary.claim.id,
                recorded_at=case.issue,
                station_id=case.station.id,
                model_id=record.model_id,
                model_artifact_id=record.model_artifact_id,
                group_id=case.summary.claim.key.group_id,
                issued_at=case.issue,
                parameter="discharge",
                representation=representation,
                units="m³/s",
                time_step_seconds=86400,
                qc_status=QcStatus.QC_FAILED,
                qc_flags=(),
                values={
                    str(level): tuple(
                        (row["valid_time"], row["value"])
                        for row in rows
                        if row[key] == level
                    )
                    for level in levels
                },
            )
        candidate = replace(candidate, record=record)
        peak = candidate_peak(candidate, as_of=case.issue + timedelta(hours=6))
        assert peak.value == 12.0
        assert peak.valid_time == case.issue
        assert peak.horizon_end == case.issue + timedelta(days=2)
        assert candidate_peak(candidate, as_of=case.issue + timedelta(days=2)) is None

    def test_missing_quantile_median_is_invalid_not_expired(
        self, case: SimpleNamespace
    ) -> None:
        import polars as pl

        from sapphire_flow.api.routes.api_swiss_overview import _cmal
        from sapphire_flow.services.swiss_overview import CurrentHeadline
        from sapphire_flow.types.enums import EnsembleRepresentation

        candidate = (
            read_evaluation_forecasts(
                station=case.station,
                settings=case.settings,
                assignments=(),
                stores=case.stores,
                as_of=case.issue + timedelta(hours=1),
            )
            .parameters[0]
            .candidate
        )
        ensemble = replace(
            case.record.ensemble,
            representation=EnsembleRepresentation.QUANTILES,
            values=case.record.ensemble.values.rename(
                {"member_id": "quantile"}
            ).with_columns(pl.col("quantile").cast(pl.Float64)),
        )
        candidate = replace(
            candidate,
            record=replace(
                case.record,
                representation=EnsembleRepresentation.QUANTILES,
                ensemble=ensemble,
            ),
        )
        result = _cmal(
            CurrentHeadline(candidate=candidate, absence=None),
            as_of=case.issue + timedelta(hours=1),
            withheld=False,
        )
        assert result.reason is HeadlineAbsence.INVALID_SOURCE_DATA
