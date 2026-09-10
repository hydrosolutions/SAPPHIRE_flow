"""Plan 261 T1 — the operational past-forcing leg is completed from stored
forecasts at its tail, and only there."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import polars as pl
from structlog.testing import capture_logs

from sapphire_flow.services.operational_inputs import fill_past_forcing_tail
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import AggregationMethod, SpatialRepresentation
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.weather import WeatherForecastRecord
from tests.fakes.fake_stores import FakeWeatherForecastStore

_STATION = StationId(uuid4())
_NWP = "icon_ch2_eps"
_DAY = timedelta(hours=24)
_AGG = {
    "precipitation": AggregationMethod.SUM,
    "temperature": AggregationMethod.MEAN,
}


def _ts(year: int, month: int, day: int, hour: int = 0) -> UtcDatetime:
    return ensure_utc(datetime(year, month, day, hour, tzinfo=UTC))


def _reanalysis_frame(
    last_day: int, *, parameters: tuple[str, ...] = ("precipitation", "temperature")
) -> pl.DataFrame:
    """Daily reanalysis, 2026-09-01 .. 2026-09-``last_day``, one row per day."""
    rows = []
    for day in range(1, last_day + 1):
        row: dict[str, object] = {"timestamp": _ts(2026, 9, day)}
        for parameter in parameters:
            row[parameter] = float(day)
        rows.append(row)
    return pl.DataFrame(rows)


def _hourly_forecast(
    *,
    day: int,
    parameter: str,
    cycle_day: int,
    value_per_hour: float,
    hours: range = range(24),
    member_id: int | None = 0,
) -> list[WeatherForecastRecord]:
    return [
        WeatherForecastRecord(
            id=UUID(int=day * 100000 + hour * 100 + cycle_day),
            station_id=_STATION,
            nwp_source=_NWP,
            # 18Z of the PREVIOUS day, so no `valid_time` coincides with the
            # cycle stamp. A same-instant stamp is the de-accumulated lead-0
            # zero, which the fill deliberately never uses — a fixture that
            # collides with it is testing an artefact, not the rule.
            cycle_time=ensure_utc(_ts(2026, 9, cycle_day) - timedelta(hours=6)),
            valid_time=_ts(2026, 9, day, hour),
            parameter=parameter,
            spatial_type=SpatialRepresentation.BASIN_AVERAGE,
            band_id=None,
            member_id=member_id,
            value=value_per_hour,
            created_at=_ts(2026, 9, cycle_day),
        )
        for hour in hours
    ]


def _fill(
    frame: pl.DataFrame,
    records: list[WeatherForecastRecord],
    *,
    window_end: UtcDatetime,
    parameters: list[str] | None = None,
) -> pl.DataFrame:
    store = FakeWeatherForecastStore()
    store.store_weather_forecasts(records)
    return fill_past_forcing_tail(
        frame,
        station_id=_STATION,
        nwp_source=_NWP,
        weather_forecast_store=store,  # type: ignore[arg-type]
        parameters=parameters or ["precipitation", "temperature"],
        window_end=window_end,
        time_step=_DAY,
        aggregation_methods=_AGG,
    )


class TestFillPastForcingTail:
    def test_extends_the_series_to_the_window_end(self) -> None:
        """The measured leg stops on 09-07; the window runs to 09-09."""
        frame = _reanalysis_frame(7)
        records = [
            *_hourly_forecast(
                day=7, parameter="precipitation", cycle_day=7, value_per_hour=1.0
            ),
            *_hourly_forecast(
                day=8, parameter="precipitation", cycle_day=8, value_per_hour=1.0
            ),
            *_hourly_forecast(
                day=8, parameter="temperature", cycle_day=8, value_per_hour=4.0
            ),
        ]

        filled = _fill(frame, records, window_end=_ts(2026, 9, 9))

        assert filled.get_column("timestamp").to_list()[-1] == _ts(2026, 9, 8)
        assert filled.height == 8

    def test_never_writes_past_the_window_end(self) -> None:
        """`window_end` is the aligned bound, which EXCLUDES the in-progress
        bucket. A forecast covering 09-09 must not be appended when the window
        ends there — that is the partial-trailing-bucket defect Plan 239 T1a
        exists to prevent."""
        frame = _reanalysis_frame(7)
        records = [
            *_hourly_forecast(
                day=8, parameter="precipitation", cycle_day=8, value_per_hour=1.0
            ),
            *_hourly_forecast(
                day=9, parameter="precipitation", cycle_day=9, value_per_hour=1.0
            ),
        ]

        filled = _fill(frame, records, window_end=_ts(2026, 9, 9))

        assert _ts(2026, 9, 9) not in filled.get_column("timestamp").to_list()

    def test_leaves_the_last_measured_bucket_untouched(self) -> None:
        """The seam bucket already holds a daily reanalysis total. Adding 24
        hourly increments to it would double-count precipitation."""
        frame = _reanalysis_frame(7)
        records = _hourly_forecast(
            day=7, parameter="precipitation", cycle_day=7, value_per_hour=99.0
        )

        filled = _fill(frame, records, window_end=_ts(2026, 9, 9))

        seam = filled.filter(pl.col("timestamp") == _ts(2026, 9, 7))
        assert seam.get_column("precipitation").to_list() == [7.0]

    def test_uses_the_freshest_cycle_covering_the_bucket(self) -> None:
        """D3: the most recently issued run covering a step wins, because it is
        the closest thing to an observation the store holds."""
        frame = _reanalysis_frame(7)
        records = [
            *_hourly_forecast(
                day=8, parameter="precipitation", cycle_day=5, value_per_hour=1.0
            ),
            *_hourly_forecast(
                day=8, parameter="precipitation", cycle_day=8, value_per_hour=2.0
            ),
            *_hourly_forecast(
                day=8, parameter="precipitation", cycle_day=6, value_per_hour=3.0
            ),
        ]

        filled = _fill(frame, records, window_end=_ts(2026, 9, 9))

        row = filled.filter(pl.col("timestamp") == _ts(2026, 9, 8))
        assert row.get_column("precipitation").to_list() == [48.0]  # 24 h x 2.0, SUM

    def test_uses_the_control_member_not_the_ensemble(self) -> None:
        """D4: the past frame has no member dimension, so the fill must collapse
        to ONE value — the control, matching the deterministic series it
        continues."""
        frame = _reanalysis_frame(7)
        records = [
            # Perturbed member FIRST: with an equal cycle_time, an
            # order-dependent selection would take this one.
            *_hourly_forecast(
                day=8,
                parameter="precipitation",
                cycle_day=8,
                value_per_hour=50.0,
                member_id=1,
            ),
            *_hourly_forecast(
                day=8,
                parameter="precipitation",
                cycle_day=8,
                value_per_hour=2.0,
                member_id=0,
            ),
        ]

        filled = _fill(frame, records, window_end=_ts(2026, 9, 9))

        row = filled.filter(pl.col("timestamp") == _ts(2026, 9, 8))
        assert row.get_column("precipitation").to_list() == [48.0]

    def test_leaves_a_partly_covered_bucket_absent(self) -> None:
        """A bucket with only some of its native steps would resample to a
        silently LOW precipitation total with no null — invisible to `max_nan`
        and to the past-forcing gap flag alike."""
        frame = _reanalysis_frame(7)
        records = _hourly_forecast(
            day=8,
            parameter="precipitation",
            cycle_day=8,
            value_per_hour=1.0,
            hours=range(6),
        ) + _hourly_forecast(
            day=8, parameter="temperature", cycle_day=8, value_per_hour=1.0
        )

        filled = _fill(frame, records, window_end=_ts(2026, 9, 9))

        row = filled.filter(pl.col("timestamp") == _ts(2026, 9, 8))
        assert row.get_column("precipitation").to_list() == [None]

    def test_leaves_interior_holes_alone(self) -> None:
        """Owner decision 2026-09-09: the TAIL only. An interior hole stays a
        hole even when forecasts covering it are in the store."""
        frame = _reanalysis_frame(7).filter(pl.col("timestamp") != _ts(2026, 9, 4))
        records = [
            *_hourly_forecast(
                day=4, parameter="precipitation", cycle_day=4, value_per_hour=1.0
            ),
            *_hourly_forecast(
                day=8, parameter="precipitation", cycle_day=8, value_per_hour=1.0
            ),
        ]

        filled = _fill(frame, records, window_end=_ts(2026, 9, 9))

        assert _ts(2026, 9, 4) not in filled.get_column("timestamp").to_list()

    def test_leaves_a_parameter_with_no_forecast_counterpart_short(self) -> None:
        """Only precipitation and temperature exist in `weather_forecasts`;
        the other MeteoSwiss products stay as measured."""
        frame = _reanalysis_frame(7, parameters=("relative_sunshine_duration",))

        filled = _fill(
            frame,
            [],
            window_end=_ts(2026, 9, 9),
            parameters=["relative_sunshine_duration"],
        )

        assert filled.get_column("timestamp").to_list()[-1] == _ts(2026, 9, 7)

    def test_off_midnight_cycle_appends_no_partial_bucket(self) -> None:
        """A 06Z cycle for a daily model. `window_end` is the aligned bound
        (09-09 00:00Z), NOT the 06:00Z issue time: filling to the issue time
        would append a 6-hour bucket presented as a whole day."""
        frame = _reanalysis_frame(7)
        records = [
            *_hourly_forecast(
                day=8, parameter="precipitation", cycle_day=8, value_per_hour=1.0
            ),
            *_hourly_forecast(
                day=9,
                parameter="precipitation",
                cycle_day=9,
                value_per_hour=1.0,
                hours=range(6),
            ),
        ]

        filled = _fill(frame, records, window_end=_ts(2026, 9, 9))

        timestamps = filled.get_column("timestamp").to_list()
        assert timestamps[-1] == _ts(2026, 9, 8)
        assert filled.filter(pl.col("timestamp") == _ts(2026, 9, 8)).get_column(
            "precipitation"
        ).to_list() == [24.0]

    def test_series_with_different_anchors_fill_only_their_own_tails(self) -> None:
        """The products publish independently, so the anchor is PER SERIES.

        With temperature two days behind precipitation, the read necessarily
        spans buckets where precipitation is already measured. Those must be
        left exactly as measured while temperature is extended — the case a
        uniform-anchor test cannot reach, and the one that makes both the
        tail-only rule and reanalysis precedence load-bearing.
        """
        frame = _reanalysis_frame(7).with_columns(
            pl.when(pl.col("timestamp") > _ts(2026, 9, 5))
            .then(None)
            .otherwise(pl.col("temperature"))
            .alias("temperature")
        )
        records = [
            *_hourly_forecast(
                day=6, parameter="precipitation", cycle_day=6, value_per_hour=99.0
            ),
            *_hourly_forecast(
                day=7, parameter="precipitation", cycle_day=7, value_per_hour=99.0
            ),
            *_hourly_forecast(
                day=8, parameter="precipitation", cycle_day=8, value_per_hour=1.0
            ),
            *_hourly_forecast(
                day=6, parameter="temperature", cycle_day=6, value_per_hour=10.0
            ),
            *_hourly_forecast(
                day=7, parameter="temperature", cycle_day=7, value_per_hour=20.0
            ),
        ]

        filled = _fill(frame, records, window_end=_ts(2026, 9, 9))

        by_day = {row["timestamp"]: row for row in filled.iter_rows(named=True)}
        # precipitation was already measured on 06 and 07 — untouched by the
        # 99.0/h forecast covering the same days.
        assert by_day[_ts(2026, 9, 6)]["precipitation"] == 6.0
        assert by_day[_ts(2026, 9, 7)]["precipitation"] == 7.0
        # temperature stopped on 05 and is extended from its own anchor.
        assert by_day[_ts(2026, 9, 6)]["temperature"] == 10.0
        assert by_day[_ts(2026, 9, 7)]["temperature"] == 20.0

    def test_an_interior_hole_within_reach_of_the_read_stays_a_hole(self) -> None:
        """The decisive interior-hole case. A second series anchored further
        back pulls the read start behind precipitation's own hole, so the
        forecast covering that hole IS fetched — and must still not fill it.

        The earlier interior-hole test cannot prove this: there the hole sits
        before the read even begins, so the read bound protects it and the rule
        is never exercised.
        """
        frame = _reanalysis_frame(7).with_columns(
            pl.when(pl.col("timestamp") == _ts(2026, 9, 6))
            .then(None)
            .otherwise(pl.col("precipitation"))
            .alias("precipitation"),
            pl.when(pl.col("timestamp") > _ts(2026, 9, 4))
            .then(None)
            .otherwise(pl.col("temperature"))
            .alias("temperature"),
        )
        records = [
            *_hourly_forecast(
                day=6, parameter="precipitation", cycle_day=6, value_per_hour=99.0
            ),
            *_hourly_forecast(
                day=5, parameter="temperature", cycle_day=5, value_per_hour=10.0
            ),
        ]

        filled = _fill(frame, records, window_end=_ts(2026, 9, 9))

        by_day = {row["timestamp"]: row for row in filled.iter_rows(named=True)}
        # Fetched, and deliberately not used: 09-06 is interior to precipitation.
        assert by_day[_ts(2026, 9, 6)]["precipitation"] is None
        # Temperature's own tail is extended from its own anchor.
        assert by_day[_ts(2026, 9, 5)]["temperature"] == 10.0

    def test_selects_the_control_even_when_the_store_ignores_the_filter(
        self,
    ) -> None:
        """The store-side `member_ids` filter is a COST measure; selecting the
        control run is correctness. A store that returns every member — a
        relaxed query, a different backend — must still yield the control
        value, not an arbitrary member.
        """

        class _PermissiveStore(FakeWeatherForecastStore):
            def fetch_lookback(  # type: ignore[override]
                self,
                station_id: StationId,
                nwp_source: str,
                start: UtcDatetime,
                end: UtcDatetime,
                parameters: list[str] | None = None,
                member_ids: frozenset[int | None] | None = None,
            ) -> list[WeatherForecastRecord]:
                return super().fetch_lookback(
                    station_id, nwp_source, start, end, parameters, None
                )

        store = _PermissiveStore()
        store.store_weather_forecasts(
            [
                *_hourly_forecast(
                    day=8,
                    parameter="precipitation",
                    cycle_day=8,
                    value_per_hour=50.0,
                    member_id=1,
                ),
                *_hourly_forecast(
                    day=8,
                    parameter="precipitation",
                    cycle_day=8,
                    value_per_hour=2.0,
                    member_id=0,
                ),
            ]
        )

        filled = fill_past_forcing_tail(
            _reanalysis_frame(7),
            station_id=_STATION,
            nwp_source=_NWP,
            weather_forecast_store=store,  # type: ignore[arg-type]
            parameters=["precipitation", "temperature"],
            window_end=_ts(2026, 9, 9),
            time_step=_DAY,
            aggregation_methods=_AGG,
        )

        row = filled.filter(pl.col("timestamp") == _ts(2026, 9, 8))
        assert row.get_column("precipitation").to_list() == [48.0]

    def test_an_evenly_sparse_bucket_is_not_filled(self) -> None:
        """Independent review 2026-09-09 (major). Inferring the native cadence
        from the rows being validated is circular: a bucket holding only the
        even hours is "evenly spaced 2-hourly", completes at 12 steps, and
        yields half the real precipitation with no null anywhere. The grid is
        DECLARED, so this bucket is short and stays unfilled.
        """
        frame = _reanalysis_frame(7)
        records = _hourly_forecast(
            day=8,
            parameter="precipitation",
            cycle_day=8,
            value_per_hour=1.0,
            hours=range(0, 24, 2),
        )

        filled = _fill(frame, records, window_end=_ts(2026, 9, 9))

        assert filled.equals(frame)

    def test_a_parameter_outside_the_fillable_set_is_never_filled(self) -> None:
        """D5 scope. `member_id=None` means "deterministic", which is both an
        ensemble-free control run AND how Nepal's snow is stored — so without
        an explicit parameter scope a past-snow model would silently receive
        forecast-filled snow on a path this plan defers entirely.
        """
        frame = pl.DataFrame(
            [
                {"timestamp": _ts(2026, 9, day), "snow_depth": float(day)}
                for day in range(1, 8)
            ]
        )
        records = [
            WeatherForecastRecord(
                id=UUID(int=900000 + hour),
                station_id=_STATION,
                nwp_source=_NWP,
                cycle_time=_ts(2026, 9, 8),
                valid_time=_ts(2026, 9, 8, hour),
                parameter="snow_depth",
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
                member_id=None,  # deterministic, exactly like recap snow
                value=5.0,
                created_at=_ts(2026, 9, 8),
            )
            for hour in range(24)
        ]

        filled = _fill(
            frame, records, window_end=_ts(2026, 9, 9), parameters=["snow_depth"]
        )

        assert filled.equals(frame)

    def test_a_full_count_of_off_grid_timestamps_is_not_filled(self) -> None:
        """Why the coverage check compares SETS rather than counts: 24 readings
        stamped at :30 past each hour are a full count on a grid they never
        touch. A count-compare accepts them; the bucket they aggregate into is
        offset from the one being filled.
        """
        frame = _reanalysis_frame(7)
        records = [
            WeatherForecastRecord(
                id=UUID(int=800000 + hour),
                station_id=_STATION,
                nwp_source=_NWP,
                cycle_time=_ts(2026, 9, 8),
                valid_time=ensure_utc(_ts(2026, 9, 8, hour) + timedelta(minutes=30)),
                parameter="precipitation",
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
                member_id=0,
                value=1.0,
                created_at=_ts(2026, 9, 8),
            )
            for hour in range(24)
        ]

        filled = _fill(frame, records, window_end=_ts(2026, 9, 9))

        assert filled.equals(frame)

    def test_a_cadence_mismatch_is_logged_not_silent(self) -> None:
        """`_NWP_NATIVE_STEP` is declared, so a source delivering another
        cadence never completes a bucket and the fill quietly stops being a
        fill. Failing closed is only safe when it is visible: the warning names
        the expected and observed step counts, which is what identifies the
        cause.
        """
        frame = _reanalysis_frame(7)
        records = _hourly_forecast(
            day=8,
            parameter="precipitation",
            cycle_day=8,
            value_per_hour=1.0,
            hours=range(0, 24, 3),  # a 3-hourly source
        )

        with capture_logs() as logs:
            filled = _fill(frame, records, window_end=_ts(2026, 9, 9))

        assert filled.equals(frame)
        warning = next(
            entry
            for entry in logs
            if entry["event"] == "operational_inputs.past_forcing_tail_unfilled"
        )
        assert warning["reason"] == "no_complete_bucket"
        assert warning["expected_steps_per_bucket"] == 24
        assert warning["observed_steps_per_bucket"] == {
            "precipitation@2026-09-08 00:00:00+00:00": 8
        }

    def test_a_source_with_no_declared_cadence_is_not_filled(self) -> None:
        """Independent review round 2 (major). The native cadence is a property
        of the SOURCE. `ifs_ecmwf` is stored verbatim by the recap Gateway and
        is lead-dependent (3-hourly, then 6-hourly), so a global hourly grid
        would have silently accepted 8 three-hourly steps as a whole day on the
        Nepal route -- which Plan 261 defers entirely. An undeclared source is
        not filled, and says so before paying for the read.
        """
        frame = _reanalysis_frame(7)
        store = FakeWeatherForecastStore()
        store.store_weather_forecasts(
            _hourly_forecast(
                day=8, parameter="precipitation", cycle_day=8, value_per_hour=1.0
            )
        )

        with capture_logs() as logs:
            filled = fill_past_forcing_tail(
                frame,
                station_id=_STATION,
                nwp_source="ifs_ecmwf",
                weather_forecast_store=store,  # type: ignore[arg-type]
                parameters=["precipitation", "temperature"],
                window_end=_ts(2026, 9, 9),
                time_step=_DAY,
                aggregation_methods=_AGG,
            )

        assert filled.equals(frame)
        warning = next(
            entry
            for entry in logs
            if entry["event"] == "operational_inputs.past_forcing_tail_unfilled"
        )
        assert warning["reason"] == "undeclared_native_cadence"
        assert warning["declared_sources"] == ["icon_ch2_eps"]

    def test_lead_zero_steps_are_never_used(self) -> None:
        """Independent review 2026-09-10 (blocker), confirmed on the staging
        host: ALL 96,726 stored lead-0 precipitation rows are exactly 0.0,
        because ingest de-accumulates against a zero pad, so the value at
        `valid_time == cycle_time` is `tp(0) - 0` by construction.

        "Freshest covering cycle" takes the maximum cycle_time, which for a
        valid_time that IS a cycle stamp is always that cycle's lead 0. Four of
        every twenty-four hourly increments would be zeroed and the daily total
        under-read by ~17% -- with a COMPLETE 24-stamp grid and no null, so
        neither the grid check nor `max_nan` would see it.

        Here: 6-hourly cycles, 1.0 mm every hour, and the lead-0 row of each
        cycle stored as 0.0 exactly as production does.
        """
        frame = _reanalysis_frame(7)
        records: list[WeatherForecastRecord] = []
        for cycle_hour in (0, 6, 12, 18):
            cycle = ensure_utc(_ts(2026, 9, 8) + timedelta(hours=cycle_hour))
            for lead in range(0, 24 - cycle_hour):
                valid = ensure_utc(cycle + timedelta(hours=lead))
                records.append(
                    WeatherForecastRecord(
                        id=UUID(int=700000 + cycle_hour * 100 + lead),
                        station_id=_STATION,
                        nwp_source=_NWP,
                        cycle_time=cycle,
                        valid_time=valid,
                        parameter="precipitation",
                        spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                        band_id=None,
                        member_id=0,
                        # The de-accumulated lead-0 step is a structural zero.
                        value=0.0 if lead == 0 else 1.0,
                        created_at=cycle,
                    )
                )
        # The 00Z bucket stamp itself must come from an EARLIER cycle, as it
        # does in production (yesterday's 18Z run at lead 6).
        prior = ensure_utc(_ts(2026, 9, 7) + timedelta(hours=18))
        records.append(
            WeatherForecastRecord(
                id=UUID(int=799999),
                station_id=_STATION,
                nwp_source=_NWP,
                cycle_time=prior,
                valid_time=_ts(2026, 9, 8),
                parameter="precipitation",
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
                member_id=0,
                value=1.0,
                created_at=prior,
            )
        )

        filled = _fill(frame, records, window_end=_ts(2026, 9, 9))

        total = filled.filter(pl.col("timestamp") == _ts(2026, 9, 8)).get_column(
            "precipitation"
        )[0]
        assert total == 24.0, "lead-0 zeros must not be counted as real increments"

    def test_a_runs_first_temperature_reading_is_kept(self) -> None:
        """The mirror of the lead-0 rule, and the reason it is scoped rather
        than unconditional (independent review, 2026-09-10).

        Only precipitation is de-accumulated at ingest. A run's first
        TEMPERATURE row is a genuine point reading and the freshest one
        available, so discarding it would replace the best value with an older
        run's forecast — or, with no older run covering that stamp, fail the
        grid check and decline to fill a bucket that was perfectly good.

        Here the freshest run's first step is the ONLY source for 00:00, and it
        must be used.
        """
        frame = _reanalysis_frame(7)
        cycle = _ts(2026, 9, 8)  # a run starting exactly at the bucket boundary
        records = [
            WeatherForecastRecord(
                id=UUID(int=600000 + hour),
                station_id=_STATION,
                nwp_source=_NWP,
                cycle_time=cycle,
                valid_time=ensure_utc(cycle + timedelta(hours=hour)),
                parameter="temperature",
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
                member_id=0,
                value=10.0,
                created_at=cycle,
            )
            for hour in range(24)
        ]

        filled = _fill(frame, records, window_end=_ts(2026, 9, 9))

        row = filled.filter(pl.col("timestamp") == _ts(2026, 9, 8))
        assert row.get_column("temperature").to_list() == [10.0], (
            "a run's first temperature reading is real and must not be skipped"
        )

    def test_no_forecasts_leaves_the_frame_unchanged(self) -> None:
        frame = _reanalysis_frame(7)

        filled = _fill(frame, [], window_end=_ts(2026, 9, 9))

        assert filled.equals(frame)
