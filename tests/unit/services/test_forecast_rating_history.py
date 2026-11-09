from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from uuid import UUID

import polars as pl
import pytest

from sapphire_flow.services.forecast_rating_history import build_forecast_rating_history
from sapphire_flow.services.rating_inputs import merge_rating_days
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.forecast_rating_history import (
    HistoryWarning,
    RatingHistoryEvidence,
    RatingHistorySample,
    RatingHistorySupport,
    RejectionCode,
)
from sapphire_flow.types.rating_reference import canonical_content
from tests.unit.services.test_rating_inputs import samples as producer_samples

_DAY = datetime(2026, 10, 1, tzinfo=UTC)
_ISSUE = _DAY + timedelta(days=1, hours=17, minutes=23)
_BASE_WARNINGS: tuple[HistoryWarning, ...] = (
    "accepted_measured_discharge_absent",
    "partial_daily_support",
)


def _sample(hour: int = 1, discharge: float | None = 10.0) -> RatingHistorySample:
    return RatingHistorySample(
        timestamp=_DAY + timedelta(hours=hour),
        day=_DAY,
        discharge=discharge,
        rejection=None if discharge is not None else "source_qc_rejected",
        warnings=(),
        curve_id=UUID(int=1),
        curve_digest="a" * 64,
        valid_from=_DAY - timedelta(days=30),
        valid_to=None,
    )


def _support() -> RatingHistorySupport:
    return RatingHistorySupport(
        day=_DAY,
        accepted=1,
        rejected=0,
        extrapolated=0,
        assessed=1,
        unassessed=0,
        measured_rejected=0,
        first=_sample().timestamp,
        last=_sample().timestamp,
        warnings=_BASE_WARNINGS,
    )


def _evidence() -> RatingHistoryEvidence:
    end = _ISSUE.replace(hour=0, minute=0, second=0, microsecond=0)
    return RatingHistoryEvidence(
        forecast_id=UUID(int=2),
        station_id=UUID(int=3),
        tenant_id=UUID(int=4),
        artifact_id=UUID(int=5),
        issued_at=_ISSUE,
        start=end - timedelta(days=7),
        end=end,
        days=(_support(),),
        samples=(_sample(),),
        omissions=(),
    )


class TestBuildForecastRatingHistory:
    def test_seven_completed_utc_days_and_honest_absence(self) -> None:
        evidence = _evidence()
        result = build_forecast_rating_history(evidence)
        assert result.issued_at == _ISSUE
        assert result.start == datetime(2026, 9, 25, tzinfo=UTC)
        assert result.end == datetime(2026, 10, 2, tzinfo=UTC)
        assert [day.day for day in result.days] == [
            result.start + timedelta(days=n) for n in range(7)
        ]
        for day in result.days[:-1]:
            assert (day.status, day.daily_discharge, day.support, day.curves) == (
                "no_retained_derived_day",
                None,
                None,
                (),
            )
        assert result.days[-1].status == "available"
        assert result.days[-1].daily_discharge == 10.0
        assert (result.resolution, result.unit, result.final_model_tensor) == (
            "P1D",
            "m3/s",
            "not_retained",
        )

    @pytest.mark.parametrize("values", [None, (0.1, 0.2, 1e16), (1e308, 1e308, 1e308)])
    def test_sorted_daily_math_matches_real_assembly_bit_exactly(
        self, values: tuple[float, ...] | None
    ) -> None:
        source = producer_samples()
        if values is not None:
            rebuilt = []
            for index, (row, value) in enumerate(zip(source, values, strict=True)):
                body = json.loads(row.content)
                timestamp = ensure_utc(_DAY + timedelta(hours=index * 7))
                body.update(discharge=value, rejection=None)
                body["source"]["timestamp"] = timestamp.isoformat()
                rebuilt.append(
                    replace(
                        row,
                        timestamp=timestamp,
                        discharge=value,
                        rejection=None,
                        content=canonical_content(body),
                    )
                )
            source = tuple(rebuilt)
        ordered = tuple(sorted(source, key=lambda row: row.timestamp))
        frame, lineage, _ = merge_rating_days(
            pl.DataFrame(),
            ordered,
            issue_time=ensure_utc(_ISSUE),
            weather_cycle_time=ensure_utc(_DAY),
        )
        assert lineage is not None and lineage.rating_input is not None
        retained = lineage.rating_input.days[0]
        support = RatingHistorySupport(
            day=retained.day,
            accepted=retained.accepted,
            rejected=retained.rejected,
            extrapolated=retained.extrapolated,
            assessed=retained.assessed,
            unassessed=retained.unassessed,
            measured_rejected=0,
            first=retained.first,
            last=retained.last,
            warnings=cast("tuple[HistoryWarning, ...]", retained.warnings),
        )
        projected = []
        for row in source:
            curve = json.loads(row.content)["curve"]
            projected.append(
                RatingHistorySample(
                    timestamp=row.timestamp,
                    day=retained.day,
                    discharge=row.discharge,
                    rejection=cast("RejectionCode | None", row.rejection),
                    warnings=cast("tuple[HistoryWarning, ...]", row.warnings),
                    curve_id=row.rating_curve_id,
                    curve_digest=hashlib.sha256(
                        canonical_content(curve).encode()
                    ).hexdigest(),
                    valid_from=datetime.fromisoformat(curve["valid_from"]),
                    valid_to=datetime.fromisoformat(curve["valid_to"]),
                )
            )
        expected = frame["discharge"][0]
        accepted = [row.discharge for row in ordered if row.discharge is not None]
        assert expected.hex() == math.fsum(q / len(accepted) for q in accepted).hex()
        # Storage and fingerprint ordering are not assembly timestamp ordering.
        by_fingerprint = sorted(range(len(source)), key=lambda i: source[i].fingerprint)
        for indexes in (list(reversed(range(len(source)))), by_fingerprint, [1, 2, 0]):
            result = build_forecast_rating_history(
                replace(
                    _evidence(),
                    days=(support,),
                    samples=tuple(projected[i] for i in indexes),
                )
            )
            value = result.days[-1].daily_discharge
            assert value is not None and value.hex() == expected.hex()
            assert result.days[-1].support == support
        if values is None:
            assert (
                support.accepted,
                support.rejected,
                support.extrapolated,
                support.assessed,
                support.unassessed,
            ) == (2, 1, 1, 1, 1)

    def test_rejected_curve_expiry_omission_and_measured_rejection(self) -> None:
        accepted = replace(_sample(), warnings=("extrapolated_above",))
        rejected = replace(
            _sample(2, None),
            curve_id=UUID(int=6),
            curve_digest="b" * 64,
            valid_to=_ISSUE,
            warnings=("qc_unassessed",),
        )
        support = replace(
            _support(),
            rejected=1,
            extrapolated=1,
            measured_rejected=2,
            warnings=(
                *_BASE_WARNINGS,
                "extrapolated_above",
                "qc_unassessed",
                "rating_expired_at_issue",
                "rating_unreconciled_sources_omitted",
                "measured_discharge_rejected",
            ),
        )
        result = build_forecast_rating_history(
            replace(
                _evidence(),
                days=(support,),
                samples=(rejected, accepted),
                omissions=(_DAY + timedelta(hours=3),),
            )
        ).days[-1]
        assert result.daily_discharge == 10.0
        assert result.support == support
        assert (support.assessed, support.unassessed, support.first, support.last) == (
            1,
            0,
            accepted.timestamp,
            accepted.timestamp,
        )
        curves = {curve.curve_id: curve for curve in result.curves}
        assert curves[accepted.curve_id].warnings == ("extrapolated_above",)
        assert curves[rejected.curve_id].warnings == (
            "qc_unassessed",
            "rating_expired_at_issue",
        )

    def test_same_curve_id_with_distinct_frozen_digest_stays_separate(self) -> None:
        second = replace(_sample(2), curve_digest="b" * 64)
        support = replace(_support(), accepted=2, assessed=2, last=second.timestamp)
        day = build_forecast_rating_history(
            replace(_evidence(), days=(support,), samples=(second, _sample()))
        ).days[-1]
        assert [curve.content_sha256 for curve in day.curves] == ["a" * 64, "b" * 64]

    def test_conflicting_frozen_curve_validity_is_refused(self) -> None:
        second = replace(_sample(2), valid_from=_DAY - timedelta(days=5))
        support = replace(_support(), accepted=2, assessed=2, last=second.timestamp)
        with pytest.raises(ValueError, match="frozen curve identity disagrees"):
            build_forecast_rating_history(
                replace(_evidence(), days=(support,), samples=(_sample(), second))
            )

    @pytest.mark.parametrize(
        "change",
        [
            {"rejected": 1},
            {"extrapolated": 1},
            {"assessed": 0, "unassessed": 1},
            {"warnings": (*_BASE_WARNINGS, "qc_unassessed")},
            {"measured_rejected": 1},
        ],
    )
    def test_retained_counts_and_warnings_must_match(self, change: dict) -> None:
        with pytest.raises(ValueError, match="frozen rating support disagrees"):
            build_forecast_rating_history(
                replace(_evidence(), days=(replace(_support(), **change),))
            )

    def test_retained_support_requires_accepted_samples(self) -> None:
        with pytest.raises(ValueError, match="frozen rating support disagrees"):
            build_forecast_rating_history(replace(_evidence(), samples=()))

    def test_sample_without_retained_day_is_refused(self) -> None:
        with pytest.raises(ValueError, match="sample has no retained rating day"):
            build_forecast_rating_history(replace(_evidence(), days=()))

    def test_support_times_must_match_accepted_samples(self) -> None:
        with pytest.raises(ValueError, match="frozen rating support times disagree"):
            build_forecast_rating_history(
                replace(
                    _evidence(),
                    days=(replace(_support(), last=_DAY + timedelta(hours=2)),),
                )
            )


class TestRatingHistoryDomain:
    @pytest.mark.parametrize(
        "change, message",
        [
            ({"discharge": -1.0}, "invalid rating discharge"),
            ({"discharge": float("nan")}, "invalid rating discharge"),
            ({"discharge": float("inf")}, "invalid rating discharge"),
            ({"discharge": None}, "rating result disagreement"),
            ({"rejection": "missing_source"}, "rating result disagreement"),
            ({"curve_digest": "z" * 64}, "invalid frozen curve digest"),
            ({"timestamp": _DAY + timedelta(days=1)}, "sample outside rating day"),
            ({"timestamp": _DAY.replace(tzinfo=None)}, "requires UTC"),
            (
                {"timestamp": _DAY.astimezone(timezone(timedelta(hours=2)))},
                "requires UTC",
            ),
        ],
    )
    def test_invalid_sample(self, change: dict, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            replace(_sample(), **change)

    @pytest.mark.parametrize(
        "change, message",
        [
            ({"rejected": -1}, "invalid rating support count"),
            ({"accepted": True}, "invalid rating support count"),
            ({"accepted": 0}, "invalid accepted support"),
            ({"assessed": 0}, "invalid accepted support"),
            ({"extrapolated": 2}, "invalid extrapolation support"),
            ({"first": _DAY + timedelta(hours=2)}, "invalid support timestamps"),
            ({"last": _DAY + timedelta(days=1)}, "invalid support timestamps"),
            (
                {"warnings": (*_BASE_WARNINGS, _BASE_WARNINGS[0])},
                "duplicate support warnings",
            ),
        ],
    )
    def test_invalid_support(self, change: dict, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            replace(_support(), **change)

    @pytest.mark.parametrize(
        "kind, message",
        [
            ("days", "duplicate rating days"),
            ("samples", "duplicate rating source timestamp"),
            ("end", "rating window differs from issue"),
            ("start", "invalid rating window"),
            ("omissions", "invalid rating omission timestamp"),
        ],
    )
    def test_invalid_evidence(self, kind: str, message: str) -> None:
        evidence = _evidence()
        changes = {
            "days": (evidence.days[0],) * 2,
            "samples": (evidence.samples[0],) * 2,
            "end": _ISSUE,
            "start": evidence.start - timedelta(days=1),
            "omissions": (evidence.end,),
        }
        with pytest.raises(ValueError, match=message):
            replace(evidence, **{kind: changes[kind]})
