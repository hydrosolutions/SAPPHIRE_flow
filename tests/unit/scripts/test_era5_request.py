"""Plan 171 task 1a — locks the CDS payload builder against the
operator-captured literal (plan `## Observed CDS payload`) and D2's
no-Cartesian-spill contract."""

from __future__ import annotations

from datetime import date

import pytest

from scripts.dhm_precip.era5_errors import NonExpressibleWindowError
from scripts.dhm_precip.era5_request import (
    ALL_ACQUISITION_WINDOWS,
    DATASET_ID,
    DEFAULT_REQUEST_SPEC,
    STUDY_AREA,
    STUDY_YEARS,
    AcquisitionWindow,
    Era5RequestSpec,
    build_request_payload,
    expand_for_acquisition,
    parse_window_arg,
    payload_implied_valid_time_stamps,
)


class TestObservedPayloadLiteral:
    """Locks the exact operator-captured payload for October 2021 (2b's
    proposed sample window) — field set, key order irrelevant, values exact.
    """

    def test_matches_observed_literal_exactly(self) -> None:
        window = AcquisitionWindow(year=2021, month=10)
        payload = build_request_payload(window)

        assert payload == {
            "variable": ["total_precipitation"],
            "year": "2021",
            "month": "10",
            "day": [f"{d:02d}" for d in range(1, 32)],
            "time": [f"{h:02d}:00" for h in range(24)],
            "data_format": "netcdf",
            "download_format": "unarchived",
            "area": [31, 80, 26, 89],
        }

    def test_dataset_id_matches_observation(self) -> None:
        assert DATASET_ID == "reanalysis-era5-land"

    def test_no_product_type_field(self) -> None:
        payload = build_request_payload(AcquisitionWindow(year=2021, month=10))
        assert "product_type" not in payload

    def test_area_is_north_west_south_east(self) -> None:
        assert STUDY_AREA == (31, 80, 26, 89)

    def test_day_and_time_lists_are_zero_padded_strings(self) -> None:
        payload = build_request_payload(AcquisitionWindow(year=2021, month=10))
        assert payload["day"][0] == "01"
        assert payload["day"][-1] == "31"
        assert payload["time"][0] == "00:00"
        assert payload["time"][-1] == "23:00"


class TestWholeYearPayloadShape:
    def test_year_and_month_scalars_vs_lists(self) -> None:
        payload = build_request_payload(AcquisitionWindow(year=2021))
        assert payload["year"] == "2021"
        assert payload["month"] == [f"{m:02d}" for m in range(1, 13)]
        assert payload["day"] == [f"{d:02d}" for d in range(1, 32)]


class TestValidTimeStampsRoundTrip:
    """D2's central test obligation: the set of valid-time stamps implied by
    a payload equals the window exactly — no missing stamps, no spill."""

    def test_october_window_round_trips_exactly(self) -> None:
        window = AcquisitionWindow(year=2021, month=10)
        payload = build_request_payload(window)
        assert payload_implied_valid_time_stamps(payload) == window.valid_time_stamps()

    def test_october_window_has_expected_cardinality(self) -> None:
        window = AcquisitionWindow(year=2021, month=10)
        assert len(window.valid_time_stamps()) == 31 * 24

    def test_single_day_window_round_trips(self) -> None:
        window = AcquisitionWindow(year=2019, month=12, day=31)
        payload = build_request_payload(window)
        assert payload_implied_valid_time_stamps(payload) == window.valid_time_stamps()
        assert len(window.valid_time_stamps()) == 24

    def test_single_hour_window_round_trips(self) -> None:
        window = AcquisitionWindow(year=2026, month=1, day=1, hour=0)
        payload = build_request_payload(window)
        assert payload_implied_valid_time_stamps(payload) == window.valid_time_stamps()
        assert window.valid_time_stamps() == {(2026, 1, 1, 0)}

    def test_february_month_window_has_no_spill(self) -> None:
        """A whole-MONTH window must not spill: February gets exactly 28/29
        days, never a padded-to-31 list (only whole-YEAR windows spill)."""
        window = AcquisitionWindow(year=2021, month=2)
        payload = build_request_payload(window)
        assert len(payload["day"]) == 28
        assert payload_implied_valid_time_stamps(payload) == window.valid_time_stamps()

    def test_whole_year_window_spills_but_valid_stamps_still_match(self) -> None:
        """D2's refinement: a whole-year payload's day list spills onto
        non-existent dates (31 Feb etc.) via the plain Cartesian product, but
        the *valid-time-only* stamps implied still equal the window exactly.
        """
        window = AcquisitionWindow(year=2021)
        payload = build_request_payload(window)
        implied = payload_implied_valid_time_stamps(payload)
        assert implied == window.valid_time_stamps()
        # A non-leap year: exactly 365 * 24 valid stamps despite the spill.
        assert len(implied) == 365 * 24


class TestNonExpressibleSpanRejected:
    def test_arbitrary_span_is_rejected(self) -> None:
        with pytest.raises(NonExpressibleWindowError):
            AcquisitionWindow.from_date_range(date(2021, 9, 30), date(2021, 11, 1))

    def test_year_crossing_span_is_rejected(self) -> None:
        with pytest.raises(NonExpressibleWindowError):
            AcquisitionWindow.from_date_range(date(2020, 12, 15), date(2021, 1, 15))

    def test_whole_month_span_is_accepted(self) -> None:
        window = AcquisitionWindow.from_date_range(
            date(2021, 10, 1), date(2021, 10, 31)
        )
        assert window == AcquisitionWindow(year=2021, month=10)

    def test_whole_year_span_is_accepted(self) -> None:
        window = AcquisitionWindow.from_date_range(date(2021, 1, 1), date(2021, 12, 31))
        assert window == AcquisitionWindow(year=2021)

    def test_single_day_span_is_accepted(self) -> None:
        window = AcquisitionWindow.from_date_range(
            date(2019, 12, 31), date(2019, 12, 31)
        )
        assert window == AcquisitionWindow(year=2019, month=12, day=31)


class TestParseWindowArg:
    def test_year_only(self) -> None:
        assert parse_window_arg("2021") == AcquisitionWindow(year=2021)

    def test_year_month(self) -> None:
        assert parse_window_arg("2021-10") == AcquisitionWindow(year=2021, month=10)

    def test_year_month_day(self) -> None:
        assert parse_window_arg("2019-12-31") == AcquisitionWindow(
            year=2019, month=12, day=31
        )

    def test_year_month_day_hour(self) -> None:
        assert parse_window_arg("2026-01-01T00") == AcquisitionWindow(
            year=2026, month=1, day=1, hour=0
        )

    def test_garbage_is_rejected(self) -> None:
        with pytest.raises(NonExpressibleWindowError):
            parse_window_arg("30-Sep-through-1-Nov")

    def test_hour_suffix_on_a_year_month_window_is_rejected_not_widened(self) -> None:
        """Review finding: the previous split()-based parser silently
        dropped an hour suffix that doesn't belong to a day, turning
        "2021-10T05" into the WHOLE month rather than rejecting it."""
        with pytest.raises(NonExpressibleWindowError):
            parse_window_arg("2021-10T05")

    def test_trailing_junk_after_a_bare_year_is_rejected_not_widened(self) -> None:
        """Review finding: "2021T00Tjunk" previously widened silently to the
        whole year because the parser only looked at `parts[0]`."""
        with pytest.raises(NonExpressibleWindowError):
            parse_window_arg("2021T00Tjunk")

    def test_trailing_junk_after_a_valid_day_window_is_rejected(self) -> None:
        with pytest.raises(NonExpressibleWindowError):
            parse_window_arg("2019-12-31extra")


class TestNoCredentialsAnywhere:
    def test_payload_has_no_credential_shaped_keys(self) -> None:
        payload = build_request_payload(AcquisitionWindow(year=2021, month=10))
        forbidden = {"key", "url", "uid", "api_key", "token", "credentials"}
        assert forbidden.isdisjoint(k.lower() for k in payload)

    def test_window_and_spec_repr_carry_no_secret_shaped_content(self) -> None:
        window = AcquisitionWindow(year=2021, month=10)
        spec = DEFAULT_REQUEST_SPEC
        combined = repr(window) + repr(spec)
        for forbidden in ("key=", "token=", "secret=", "password="):
            assert forbidden not in combined.lower()

    def test_spec_has_no_credential_fields(self) -> None:
        field_names = {f for f in Era5RequestSpec.__dataclass_fields__}
        assert field_names.isdisjoint({"key", "url", "uid", "api_key", "token"})


class TestDefaultAcquisitionWindowSetIsMonthly:
    """Plan 171 D4, CORRECTED 2026-08-17: "A CALENDAR YEAR EXCEEDS THE CDS
    COST LIMIT". CDS caps FIELD COUNT per request — a year is 8,760 hourly
    fields and is refused outright, a month is 744 and succeeds (proven by
    task 2b). The default set is therefore 72 monthly windows for 2020-2025
    plus the two edge-context windows, which were already month-or-smaller
    and are unaffected."""

    def test_is_seventy_two_monthly_windows_plus_two_edge_windows(self) -> None:
        assert len(ALL_ACQUISITION_WINDOWS) == 74

    def test_every_study_year_month_is_present_exactly_once(self) -> None:
        monthly = [
            w.window_id
            for w in ALL_ACQUISITION_WINDOWS
            if w.month is not None and w.day is None
        ]
        expected = [
            f"{year:04d}-{month:02d}" for year in STUDY_YEARS for month in range(1, 13)
        ]
        assert sorted(monthly) == sorted(expected)
        assert len(monthly) == len(set(monthly)) == 72

    def test_no_year_granular_window_survives_in_the_default_set(self) -> None:
        # A year-granular window in the acquisition set is exactly the
        # payload CDS rejected on the first real 4b attempt.
        assert [w.window_id for w in ALL_ACQUISITION_WINDOWS if w.month is None] == []

    def test_the_two_edge_context_windows_are_unchanged(self) -> None:
        edges = [w.window_id for w in ALL_ACQUISITION_WINDOWS if w.day is not None]
        assert edges == ["2019-12-31", "2026-01-01T00"]

    def test_a_monthly_window_payload_stays_within_the_proven_field_count(self) -> None:
        # 744 fields (31 x 24) is the largest month and is proven to succeed;
        # 8,760 (a year) is proven to fail. This locks the unit, not a guess.
        for window in ALL_ACQUISITION_WINDOWS:
            assert len(window.valid_time_stamps()) <= 744


class TestExpandForAcquisition:
    """D4 — the ACQUISITION stage never issues a year-granular payload, even
    when the operator names a year on the command line. It expands into that
    year's twelve monthly windows instead of sending one doomed request."""

    def test_a_year_expands_into_twelve_months(self) -> None:
        expanded = expand_for_acquisition([AcquisitionWindow(year=2021)])
        assert [w.window_id for w in expanded] == [
            f"2021-{m:02d}" for m in range(1, 13)
        ]

    def test_sub_year_windows_pass_through_unchanged(self) -> None:
        windows = [
            AcquisitionWindow(year=2021, month=10),
            AcquisitionWindow(year=2019, month=12, day=31),
            AcquisitionWindow(year=2026, month=1, day=1, hour=0),
        ]
        assert list(expand_for_acquisition(windows)) == windows

    def test_the_default_set_is_already_fully_expanded(self) -> None:
        assert (
            expand_for_acquisition(ALL_ACQUISITION_WINDOWS) == ALL_ACQUISITION_WINDOWS
        )
