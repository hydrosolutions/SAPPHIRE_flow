from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from sapphire_flow.config.deployment import InputQualityConfig
from sapphire_flow.services.input_quality import (
    assess_input_quality,
    assess_past_forcing_gaps,
    past_forcing_flags,
)
from sapphire_flow.services.training_data import (
    expected_past_buckets,
    floor_to_time_step,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import InputQualityFlag
from sapphire_flow.types.enums import (
    InputQualityCategory,
    InputQualityLevel,
    NwpCycleSource,
    WarmUpSource,
)


def _default_kwargs() -> dict:
    return dict(
        observation_staleness_hours=0.0,
        warm_up_source=WarmUpSource.FRESH,
        warm_up_state_age_hours=None,
        nwp_cycle_source=NwpCycleSource.PRIMARY,
        nwp_age_hours=0.0,
        obs_partial_hours=6.0,
        config=InputQualityConfig(),
        warmup_partial_hours=24.0,
        warmup_degraded_hours=42.0,
    )


class TestCallSiteValidation:
    def test_obs_partial_equal_to_degraded_raises(self) -> None:
        kwargs = _default_kwargs()
        kwargs["obs_partial_hours"] = kwargs["config"].obs_degraded_hours
        with pytest.raises(ValueError, match="obs_partial_hours"):
            assess_input_quality(**kwargs)

    def test_obs_partial_above_degraded_raises(self) -> None:
        kwargs = _default_kwargs()
        kwargs["obs_partial_hours"] = kwargs["config"].obs_degraded_hours + 1.0
        with pytest.raises(ValueError, match="obs_partial_hours"):
            assess_input_quality(**kwargs)

    def test_warmup_partial_equal_to_degraded_raises(self) -> None:
        kwargs = _default_kwargs()
        kwargs["warmup_partial_hours"] = kwargs["warmup_degraded_hours"]
        with pytest.raises(ValueError, match="warmup_partial_hours"):
            assess_input_quality(**kwargs)

    def test_warmup_partial_above_degraded_raises(self) -> None:
        kwargs = _default_kwargs()
        kwargs["warmup_partial_hours"] = kwargs["warmup_degraded_hours"] + 1.0
        with pytest.raises(ValueError, match="warmup_partial_hours"):
            assess_input_quality(**kwargs)


class TestObservationAssessment:
    def test_none_staleness_no_obs_flag(self) -> None:
        kwargs = _default_kwargs()
        kwargs["observation_staleness_hours"] = None
        level, flags = assess_input_quality(**kwargs)
        assert level == InputQualityLevel.FULL
        obs_flags = [f for f in flags if f.category == InputQualityCategory.OBSERVATION]
        assert obs_flags == []

    def test_below_partial_threshold_no_flag(self) -> None:
        kwargs = _default_kwargs()
        kwargs["observation_staleness_hours"] = 5.9
        level, flags = assess_input_quality(**kwargs)
        obs_flags = [f for f in flags if f.category == InputQualityCategory.OBSERVATION]
        assert obs_flags == []

    def test_at_partial_threshold_partial_flag(self) -> None:
        kwargs = _default_kwargs()
        kwargs["observation_staleness_hours"] = 6.0  # == obs_partial_hours
        level, flags = assess_input_quality(**kwargs)
        obs_flags = [f for f in flags if f.category == InputQualityCategory.OBSERVATION]
        assert len(obs_flags) == 1
        assert obs_flags[0].level == InputQualityLevel.PARTIAL

    def test_at_degraded_threshold_degraded_flag(self) -> None:
        kwargs = _default_kwargs()
        kwargs["observation_staleness_hours"] = 12.0  # == obs_degraded_hours
        level, flags = assess_input_quality(**kwargs)
        obs_flags = [f for f in flags if f.category == InputQualityCategory.OBSERVATION]
        assert len(obs_flags) == 1
        assert obs_flags[0].level == InputQualityLevel.DEGRADED

    def test_detail_contains_staleness_value(self) -> None:
        kwargs = _default_kwargs()
        kwargs["observation_staleness_hours"] = 8.5
        _, flags = assess_input_quality(**kwargs)
        obs_flags = [f for f in flags if f.category == InputQualityCategory.OBSERVATION]
        assert "8.5h" in obs_flags[0].detail


class TestNwpAssessment:
    def test_below_partial_threshold_no_flag(self) -> None:
        kwargs = _default_kwargs()
        kwargs["nwp_age_hours"] = 8.9
        level, flags = assess_input_quality(**kwargs)
        nwp_flags = [f for f in flags if f.category == InputQualityCategory.NWP]
        assert nwp_flags == []

    def test_at_partial_threshold_partial_flag(self) -> None:
        kwargs = _default_kwargs()
        kwargs["nwp_age_hours"] = 9.0  # == nwp_age_partial_hours
        level, flags = assess_input_quality(**kwargs)
        nwp_flags = [f for f in flags if f.category == InputQualityCategory.NWP]
        assert len(nwp_flags) == 1
        assert nwp_flags[0].level == InputQualityLevel.PARTIAL

    def test_at_degraded_threshold_degraded_flag(self) -> None:
        kwargs = _default_kwargs()
        kwargs["nwp_age_hours"] = 11.0  # == nwp_age_degraded_hours
        level, flags = assess_input_quality(**kwargs)
        nwp_flags = [f for f in flags if f.category == InputQualityCategory.NWP]
        assert len(nwp_flags) == 1
        assert nwp_flags[0].level == InputQualityLevel.DEGRADED

    def test_fallback_source_in_detail(self) -> None:
        kwargs = _default_kwargs()
        kwargs["nwp_age_hours"] = 9.0
        kwargs["nwp_cycle_source"] = NwpCycleSource.FALLBACK
        _, flags = assess_input_quality(**kwargs)
        nwp_flags = [f for f in flags if f.category == InputQualityCategory.NWP]
        assert "fallback" in nwp_flags[0].detail

    def test_primary_source_not_in_detail(self) -> None:
        kwargs = _default_kwargs()
        kwargs["nwp_age_hours"] = 9.0
        kwargs["nwp_cycle_source"] = NwpCycleSource.PRIMARY
        _, flags = assess_input_quality(**kwargs)
        nwp_flags = [f for f in flags if f.category == InputQualityCategory.NWP]
        assert "fallback" not in nwp_flags[0].detail

    def test_detail_contains_age_and_threshold(self) -> None:
        kwargs = _default_kwargs()
        kwargs["nwp_age_hours"] = 10.3
        _, flags = assess_input_quality(**kwargs)
        nwp_flags = [f for f in flags if f.category == InputQualityCategory.NWP]
        assert "10.3h" in nwp_flags[0].detail
        assert "9.0h" in nwp_flags[0].detail  # partial threshold


class TestRunoffOnlyQualifier:
    """epic-088 M4: runoff-only mode emits a distinct human-readable NWP flag.

    Primary / fallback / runoff-only produce DISTINCT details. Runoff-only
    yields a NWP-category flag regardless of NWP age (there is no NWP), with a
    "runoff-only" detail. RED on main: NwpCycleSource has no RUNOFF_ONLY and
    the assessor has no runoff branch.
    """

    def test_runoff_only_emits_nwp_flag_even_at_zero_age(self) -> None:
        kwargs = _default_kwargs()
        kwargs["nwp_cycle_source"] = NwpCycleSource.RUNOFF_ONLY
        kwargs["nwp_age_hours"] = 0.0  # no NWP → age is meaningless
        _, flags = assess_input_quality(**kwargs)
        nwp_flags = [f for f in flags if f.category == InputQualityCategory.NWP]
        assert len(nwp_flags) == 1
        assert "runoff-only" in nwp_flags[0].detail

    def test_runoff_only_detail_distinct_from_fallback(self) -> None:
        kwargs = _default_kwargs()
        kwargs["nwp_cycle_source"] = NwpCycleSource.RUNOFF_ONLY
        kwargs["nwp_age_hours"] = 0.0
        _, flags = assess_input_quality(**kwargs)
        nwp_flags = [f for f in flags if f.category == InputQualityCategory.NWP]
        assert "fallback" not in nwp_flags[0].detail

    def test_fresh_primary_emits_no_nwp_flag(self) -> None:
        kwargs = _default_kwargs()  # PRIMARY, nwp_age_hours=0.0
        level, flags = assess_input_quality(**kwargs)
        nwp_flags = [f for f in flags if f.category == InputQualityCategory.NWP]
        assert nwp_flags == []
        assert level == InputQualityLevel.FULL

    def test_fallback_note_distinct_from_runoff_only(self) -> None:
        kwargs = _default_kwargs()
        kwargs["nwp_cycle_source"] = NwpCycleSource.FALLBACK
        kwargs["nwp_age_hours"] = 9.0  # stale enough to raise the fallback note
        _, flags = assess_input_quality(**kwargs)
        nwp_flags = [f for f in flags if f.category == InputQualityCategory.NWP]
        assert len(nwp_flags) == 1
        assert "fallback" in nwp_flags[0].detail
        assert "runoff-only" not in nwp_flags[0].detail


class TestWarmUpAssessment:
    def test_none_source_no_warmup_flag(self) -> None:
        kwargs = _default_kwargs()
        kwargs["warm_up_source"] = None
        level, flags = assess_input_quality(**kwargs)
        wu_flags = [f for f in flags if f.category == InputQualityCategory.WARM_UP]
        assert wu_flags == []

    def test_fresh_source_no_warmup_flag(self) -> None:
        kwargs = _default_kwargs()
        kwargs["warm_up_source"] = WarmUpSource.FRESH
        level, flags = assess_input_quality(**kwargs)
        wu_flags = [f for f in flags if f.category == InputQualityCategory.WARM_UP]
        assert wu_flags == []

    def test_cold_start_degraded(self) -> None:
        kwargs = _default_kwargs()
        kwargs["warm_up_source"] = WarmUpSource.COLD_START
        level, flags = assess_input_quality(**kwargs)
        wu_flags = [f for f in flags if f.category == InputQualityCategory.WARM_UP]
        assert len(wu_flags) == 1
        assert wu_flags[0].level == InputQualityLevel.DEGRADED

    def test_cold_start_degraded_regardless_of_age(self) -> None:
        kwargs = _default_kwargs()
        kwargs["warm_up_source"] = WarmUpSource.COLD_START
        kwargs["warm_up_state_age_hours"] = 1.0
        level, flags = assess_input_quality(**kwargs)
        wu_flags = [f for f in flags if f.category == InputQualityCategory.WARM_UP]
        assert wu_flags[0].level == InputQualityLevel.DEGRADED

    def test_snapshot_none_age_degraded(self) -> None:
        kwargs = _default_kwargs()
        kwargs["warm_up_source"] = WarmUpSource.SNAPSHOT
        kwargs["warm_up_state_age_hours"] = None
        level, flags = assess_input_quality(**kwargs)
        wu_flags = [f for f in flags if f.category == InputQualityCategory.WARM_UP]
        assert len(wu_flags) == 1
        assert wu_flags[0].level == InputQualityLevel.DEGRADED
        assert "age unknown" in wu_flags[0].detail

    def test_snapshot_below_partial_no_flag(self) -> None:
        kwargs = _default_kwargs()
        kwargs["warm_up_source"] = WarmUpSource.SNAPSHOT
        kwargs["warm_up_state_age_hours"] = 23.9
        level, flags = assess_input_quality(**kwargs)
        wu_flags = [f for f in flags if f.category == InputQualityCategory.WARM_UP]
        assert wu_flags == []

    def test_snapshot_at_partial_threshold_partial(self) -> None:
        kwargs = _default_kwargs()
        kwargs["warm_up_source"] = WarmUpSource.SNAPSHOT
        kwargs["warm_up_state_age_hours"] = 24.0  # == warmup_partial_hours
        level, flags = assess_input_quality(**kwargs)
        wu_flags = [f for f in flags if f.category == InputQualityCategory.WARM_UP]
        assert len(wu_flags) == 1
        assert wu_flags[0].level == InputQualityLevel.PARTIAL

    def test_snapshot_at_degraded_threshold_degraded(self) -> None:
        kwargs = _default_kwargs()
        kwargs["warm_up_source"] = WarmUpSource.SNAPSHOT
        kwargs["warm_up_state_age_hours"] = 42.0  # == warmup_degraded_hours
        level, flags = assess_input_quality(**kwargs)
        wu_flags = [f for f in flags if f.category == InputQualityCategory.WARM_UP]
        assert len(wu_flags) == 1
        assert wu_flags[0].level == InputQualityLevel.DEGRADED


class TestWorstWinsAggregation:
    def test_all_full_no_flags(self) -> None:
        kwargs = _default_kwargs()
        level, flags = assess_input_quality(**kwargs)
        assert level == InputQualityLevel.FULL
        assert flags == ()

    def test_one_partial_others_full_returns_partial(self) -> None:
        kwargs = _default_kwargs()
        kwargs["nwp_age_hours"] = 9.0  # partial
        level, flags = assess_input_quality(**kwargs)
        assert level == InputQualityLevel.PARTIAL
        assert len(flags) == 1

    def test_one_degraded_others_partial_returns_degraded(self) -> None:
        kwargs = _default_kwargs()
        kwargs["nwp_age_hours"] = 11.0  # degraded
        kwargs["observation_staleness_hours"] = 6.0  # partial
        kwargs["warm_up_source"] = WarmUpSource.SNAPSHOT
        kwargs["warm_up_state_age_hours"] = 24.0  # partial
        level, flags = assess_input_quality(**kwargs)
        assert level == InputQualityLevel.DEGRADED

    def test_all_three_degraded_three_flags(self) -> None:
        kwargs = _default_kwargs()
        kwargs["observation_staleness_hours"] = 12.0  # degraded
        kwargs["nwp_age_hours"] = 11.0  # degraded
        kwargs["warm_up_source"] = WarmUpSource.COLD_START  # degraded
        level, flags = assess_input_quality(**kwargs)
        assert level == InputQualityLevel.DEGRADED
        assert len(flags) == 3


class TestPastForcingGapFlag:
    """Plan 239 T1b: a gap in past forcing must be LABELLED, never a refusal.

    Owner decision 2026-09-08: WHERE the gap sits decides severity. Two missing
    days out of 210, long ago, is not a problem; the same two days at the END
    is, because the model leans on recent conditions. The forecast is still
    produced either way — a refusal loses information the forecast still
    carries.
    """

    DAY = timedelta(days=1)
    ANCHOR = ensure_utc(datetime(2026, 1, 10, tzinfo=UTC))

    def _flag(self, missing_offsets: list[int], *, lookback: int = 210):
        """`missing_offsets` are k in `T0 - k*step`, so 1 is the most recent
        complete bucket."""
        expected = expected_past_buckets(self.ANCHOR, self.DAY, lookback)
        base = floor_to_time_step(self.ANCHOR, self.DAY)
        missing = [ensure_utc(base - k * self.DAY) for k in missing_offsets]
        assert set(missing) <= set(expected), "test bug: missing outside expected"
        return assess_past_forcing_gaps(
            series="precipitation",
            expected=expected,
            missing=missing,
            anchor=self.ANCHOR,
            time_step=self.DAY,
            recent_steps=2,
        )

    def test_no_gaps_produces_no_flag(self) -> None:
        assert self._flag([]) is None

    def test_two_old_gaps_out_of_210_are_partial_not_degraded(self) -> None:
        # The owner's own example: 2 of 210 missing, long ago -> not a problem.
        flag = self._flag([100, 150])
        assert flag is not None
        assert flag.level is InputQualityLevel.PARTIAL
        assert "precipitation" in flag.detail

    def test_a_gap_in_the_most_recent_step_is_degraded(self) -> None:
        flag = self._flag([1])
        assert flag is not None
        assert flag.level is InputQualityLevel.DEGRADED

    def test_the_owners_case_latest_two_days_missing_is_degraded(self) -> None:
        flag = self._flag([1, 2])
        assert flag is not None
        assert flag.level is InputQualityLevel.DEGRADED
        assert "precipitation" in flag.detail

    def test_the_boundary_step_just_outside_the_window_is_only_partial(self) -> None:
        # recent_steps=2 covers k=1 and k=2. k=3 is outside.
        assert self._flag([3]).level is InputQualityLevel.PARTIAL

    def test_the_boundary_step_just_inside_the_window_is_degraded(self) -> None:
        assert self._flag([2]).level is InputQualityLevel.DEGRADED

    def test_one_recent_gap_outranks_many_old_ones(self) -> None:
        # Severity is decided by WHERE, not HOW MANY.
        assert self._flag([1, 50, 60, 70, 80]).level is InputQualityLevel.DEGRADED

    def test_the_flag_is_categorised_as_forcing_not_observation(self) -> None:
        flag = self._flag([1])
        assert flag.category is InputQualityCategory.FORCING

    def test_a_gap_never_refuses_it_only_labels(self) -> None:
        # The whole point of the owner's redesign: this returns a flag, it does
        # not raise and does not signal "cannot serve".
        #
        # Independent review 2026-09-09 (minor): this previously asserted
        # `result is None or isinstance(result, InputQualityFlag)`, which a bare
        # `return None` also satisfies — it proved nothing about the "only
        # labels" claim. Each case now pins its own outcome, so an
        # implementation that refuses, raises, or returns nothing fails here.
        assert self._flag([]) is None
        for offsets in ([1], [1, 2], [100, 150]):
            result = self._flag(offsets)
            assert isinstance(result, InputQualityFlag), (
                f"gaps at {offsets} must produce a flag, got {result!r}"
            )
            assert result.level in (
                InputQualityLevel.PARTIAL,
                InputQualityLevel.DEGRADED,
            )


class TestPastForcingGapWindowIsFloored:
    """The recent window is measured from the FLOORED bucket, not the raw anchor.

    Independent review 2026-09-09 (minor): every other boundary test anchors at
    midnight and derives its timestamps with the same production
    `floor_to_time_step`, so replacing `base = floor_to_time_step(anchor, step)`
    with `base = anchor` passed all of them. These anchor at 06:00, where the
    two differ, and hard-code the expected instants rather than recomputing them.
    """

    DAY = timedelta(days=1)
    # 06:00 issue time on a daily grid: floors to 2026-01-10 00:00.
    ANCHOR = ensure_utc(datetime(2026, 1, 10, 6, 0, tzinfo=UTC))

    def _assess(self, missing: list[datetime]) -> InputQualityFlag | None:
        return assess_past_forcing_gaps(
            series="precipitation",
            expected=expected_past_buckets(self.ANCHOR, self.DAY, 5),
            missing=[ensure_utc(m) for m in missing],
            anchor=self.ANCHOR,
            time_step=self.DAY,
            recent_steps=2,
        )

    def test_the_oldest_bucket_in_the_recent_window_is_degraded(self) -> None:
        # recent_steps=2 from a floored base of 01-10 00:00 covers 01-09 and
        # 01-08. Measuring from the raw 06:00 anchor would put the cutoff at
        # 01-08 06:00 and misread this exact bucket as merely PARTIAL.
        flag = self._assess([datetime(2026, 1, 8, tzinfo=UTC)])
        assert flag is not None
        assert flag.level is InputQualityLevel.DEGRADED

    def test_the_bucket_just_outside_the_floored_window_is_partial(self) -> None:
        flag = self._assess([datetime(2026, 1, 7, tzinfo=UTC)])
        assert flag is not None
        assert flag.level is InputQualityLevel.PARTIAL

    def test_expected_buckets_are_floored_to_the_grid_not_the_anchor(self) -> None:
        expected = expected_past_buckets(self.ANCHOR, self.DAY, 5)
        assert [str(e) for e in expected] == [
            "2026-01-05 00:00:00+00:00",
            "2026-01-06 00:00:00+00:00",
            "2026-01-07 00:00:00+00:00",
            "2026-01-08 00:00:00+00:00",
            "2026-01-09 00:00:00+00:00",
        ]


class TestPerSeriesLookback:
    """Each forcing series is judged on its OWN declared window.

    Independent review 2026-09-09 (major): `lookback_steps` is the MAXIMUM
    across every declared past variable, because input assembly fetches one
    frame wide enough for all of them. Judging every series against that
    maximum reported a hole in a bucket the model never reads. The seasonal
    model is the live case: precipitation=45, temperature=14.

    Note `missing_buckets` is MEMBERSHIP-only on a shared `timestamp` column,
    so an absent bucket is absent for every series alike. That is exactly the
    real situation: one frame, one hole, and only the series whose own window
    reaches back that far should care.
    """

    DAY = timedelta(days=1)
    ANCHOR = ensure_utc(datetime(2026, 1, 10, tzinfo=UTC))

    def _frame(self, *, omit: datetime | None) -> pl.DataFrame:
        buckets = [
            b
            for b in expected_past_buckets(self.ANCHOR, self.DAY, 45)
            if omit is None or b != ensure_utc(omit)
        ]
        return pl.DataFrame(
            {
                "timestamp": buckets,
                "precipitation": [1.0] * len(buckets),
                "temperature": [1.0] * len(buckets),
            }
        )

    def _flagged_series(
        self, *, omit: datetime | None, declared: dict[str, int] | None
    ) -> set[str]:
        flags = past_forcing_flags(
            past_dynamic=self._frame(omit=omit),
            features=["precipitation", "temperature"],
            anchor=self.ANCHOR,
            time_step=self.DAY,
            lookback_steps=45,
            recent_steps=2,
            declared_lookbacks=declared,
        )
        return {
            series
            for series in ("precipitation", "temperature")
            if any(f"'{series}'" in flag.detail for flag in flags)
        }

    DECLARED = {"precipitation": 45, "temperature": 14}

    def test_a_gap_outside_a_series_own_window_is_not_reported(self) -> None:
        # 30 days back: inside precipitation's 45-day window, but temperature
        # reads only 14 days, so this bucket is none of its business.
        flagged = self._flagged_series(
            omit=datetime(2025, 12, 11, tzinfo=UTC), declared=self.DECLARED
        )
        assert flagged == {"precipitation"}

    def test_the_same_gap_is_reported_for_both_without_the_declaration(self) -> None:
        # The pre-fix behaviour, kept as the contrast case: with no per-series
        # declaration every series falls back to the collapsed 45-day maximum,
        # and temperature is flagged for a bucket it never reads.
        flagged = self._flagged_series(
            omit=datetime(2025, 12, 11, tzinfo=UTC), declared=None
        )
        assert flagged == {"precipitation", "temperature"}

    def test_a_gap_inside_both_windows_is_reported_for_both(self) -> None:
        # 3 days back is within temperature's 14-day window too.
        flagged = self._flagged_series(
            omit=datetime(2026, 1, 7, tzinfo=UTC), declared=self.DECLARED
        )
        assert flagged == {"precipitation", "temperature"}

    def test_an_intact_frame_flags_nothing(self) -> None:
        assert self._flagged_series(omit=None, declared=self.DECLARED) == set()

    def test_a_declared_window_never_exceeds_the_assembled_frame(self) -> None:
        # A declaration wider than the frame actually fetched would ask for
        # buckets that were never assembled and read every one as a gap.
        flagged = self._flagged_series(
            omit=None, declared={"precipitation": 999, "temperature": 999}
        )
        assert flagged == set()
