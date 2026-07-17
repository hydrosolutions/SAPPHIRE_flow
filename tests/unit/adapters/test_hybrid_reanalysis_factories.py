"""Plan 072 T3 — acceptance tests for ``default_hybrid_forcing_source``,
UPDATED by Plan 115b4 §5B — the CAMELS-CH tier is retired from every chain
(Plan 072's ``... -> CAMELS_CH`` chains no longer apply; CAMELS-CH is now a
validation reference + audit trail, not a live weather-forcing tier).

Priority chains wired by the factory (Plan 115b4 §5B):
    precipitation                : METEOSWISS_RHIRESD -> METEOSWISS_RPRELIMD
    temperature                  : METEOSWISS_TABSD    (single source)
    temperature_min              : METEOSWISS_TMIND    (single source)
    temperature_max              : METEOSWISS_TMAXD    (single source)
    relative_sunshine_duration   : METEOSWISS_SRELD     (single source)

A ``camels-ch``-tagged row in the store is NEVER returned by the hybrid
chain — it is simply not wired in (proven below), even though the row itself
remains in ``historical_forcing`` and is readable by a direct source-keyed
fetch (a different code path, not exercised here).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sapphire_flow.adapters.hybrid_reanalysis_factories import (
    default_hybrid_forcing_source,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.historical_forcing import RawHistoricalForcing
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.station import StationWeatherSource
from tests.fakes.fake_stores import FakeHistoricalForcingStore

_LEGACY_PARAMS = ["precipitation", "temperature", "temperature_min", "temperature_max"]
_SRELD_PARAM = "relative_sunshine_duration"
_ALL_PARAMS = [*_LEGACY_PARAMS, _SRELD_PARAM]
_WINDOW_START: UtcDatetime = ensure_utc(datetime(2019, 1, 1, tzinfo=UTC))
_WINDOW_END: UtcDatetime = ensure_utc(datetime(2026, 6, 1, tzinfo=UTC))
_CAMELS_ERA = ensure_utc(datetime(2019, 6, 1, tzinfo=UTC))
_POST = ensure_utc(datetime(2026, 5, 1, tzinfo=UTC))
_RHIRESD_DAY = ensure_utc(datetime(2026, 4, 1, tzinfo=UTC))


def _raw(
    *,
    source: str,
    parameter: str,
    when: UtcDatetime,
    value: float,
) -> RawHistoricalForcing:
    return RawHistoricalForcing(
        station_id=StationId("s1"),
        source=source,
        version="v1",
        valid_time=when,
        parameter=parameter,
        spatial_type=SpatialRepresentation.BASIN_AVERAGE,
        band_id=None,
        member_id=None,
        value=value,
    )


def _cfg() -> StationWeatherSource:
    return StationWeatherSource(
        station_id=StationId("s1"),
        nwp_source="unused-by-hybrid",
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=WeatherSourceStatus.ACTIVE,
        role=WeatherSourceRole.REANALYSIS,
    )


def _seed_store() -> FakeHistoricalForcingStore:
    store = FakeHistoricalForcingStore()
    store.store_forcing(
        [
            # CAMELS-CH-era rows exist in the store (audit trail, Plan 115b3)
            # but must NEVER surface through the hybrid chain post-115b4.
            _raw(
                source="camels-ch",
                parameter="precipitation",
                when=_CAMELS_ERA,
                value=1.0,
            ),
            _raw(
                source="camels-ch", parameter="temperature", when=_CAMELS_ERA, value=2.0
            ),
            _raw(
                source="camels-ch",
                parameter="temperature_min",
                when=_CAMELS_ERA,
                value=3.0,
            ),
            _raw(
                source="camels-ch",
                parameter="temperature_max",
                when=_CAMELS_ERA,
                value=4.0,
            ),
            # RhiresD (definitive) covers an earlier day than RprelimD.
            _raw(
                source="meteoswiss_rhiresd",
                parameter="precipitation",
                when=_RHIRESD_DAY,
                value=6.0,
            ),
            # Post-window MeteoSwiss per-parameter sources (the live tail).
            _raw(
                source="meteoswiss_rprelimd",
                parameter="precipitation",
                when=_POST,
                value=7.0,
            ),
            _raw(
                source="meteoswiss_tabsd",
                parameter="temperature",
                when=_POST,
                value=8.0,
            ),
            _raw(
                source="meteoswiss_tmind",
                parameter="temperature_min",
                when=_POST,
                value=9.0,
            ),
            _raw(
                source="meteoswiss_tmaxd",
                parameter="temperature_max",
                when=_POST,
                value=10.0,
            ),
            # Overlap: both RhiresD and RprelimD cover the SAME day — RhiresD
            # (definitive) must win.
            _raw(
                source="meteoswiss_rprelimd",
                parameter="precipitation",
                when=_RHIRESD_DAY,
                value=999.0,
            ),
            _raw(
                source="meteoswiss_sreld",
                parameter="relative_sunshine_duration",
                when=_POST,
                value=42.0,
            ),
        ]
    )
    return store


class TestDefaultHybridForcingSource:
    def test_camels_ch_never_surfaces_through_the_hybrid_chain(self) -> None:
        hybrid = default_hybrid_forcing_source(forcing_store=_seed_store())

        result = hybrid.fetch_reanalysis(
            [_cfg()], _WINDOW_START, _WINDOW_END, _ALL_PARAMS
        )

        assert all(r.source != "camels-ch" for r in result)
        assert all(r.valid_time != _CAMELS_ERA for r in result)

    def test_rhiresd_wins_over_rprelimd_on_the_same_day(self) -> None:
        hybrid = default_hybrid_forcing_source(forcing_store=_seed_store())

        result = hybrid.fetch_reanalysis(
            [_cfg()], _WINDOW_START, _WINDOW_END, ["precipitation"]
        )
        by_day = {r.valid_time: r for r in result}

        assert by_day[_RHIRESD_DAY].source == "meteoswiss_rhiresd"
        assert by_day[_RHIRESD_DAY].value == 6.0

    def test_rprelimd_wins_when_rhiresd_absent(self) -> None:
        hybrid = default_hybrid_forcing_source(forcing_store=_seed_store())

        result = hybrid.fetch_reanalysis(
            [_cfg()], _WINDOW_START, _WINDOW_END, ["precipitation"]
        )
        by_day = {r.valid_time: r for r in result}

        assert by_day[_POST].source == "meteoswiss_rprelimd"
        assert by_day[_POST].value == 7.0

    def test_post_window_resolves_to_per_parameter_meteoswiss(self) -> None:
        hybrid = default_hybrid_forcing_source(forcing_store=_seed_store())

        result = hybrid.fetch_reanalysis(
            [_cfg()], _WINDOW_START, _WINDOW_END, _ALL_PARAMS
        )
        post = {r.parameter: r for r in result if r.valid_time == _POST}

        assert post["temperature"].source == "meteoswiss_tabsd"
        assert post["temperature_min"].source == "meteoswiss_tmind"
        assert post["temperature_max"].source == "meteoswiss_tmaxd"
        assert post[_SRELD_PARAM].source == "meteoswiss_sreld"
        assert post[_SRELD_PARAM].value == 42.0

    def test_sreld_resolves_via_single_source_chain(self) -> None:
        hybrid = default_hybrid_forcing_source(forcing_store=_seed_store())

        result = hybrid.fetch_reanalysis(
            [_cfg()], _WINDOW_START, _WINDOW_END, [_SRELD_PARAM]
        )

        assert len(result) == 1
        assert result[0].source == "meteoswiss_sreld"
        assert result[0].parameter == _SRELD_PARAM
        assert result[0].value == 42.0

    def test_no_duplicate_station_validtime_parameter_rows(self) -> None:
        hybrid = default_hybrid_forcing_source(forcing_store=_seed_store())

        result = hybrid.fetch_reanalysis(
            [_cfg()], _WINDOW_START, _WINDOW_END, _ALL_PARAMS
        )
        keys = [(r.station_id, r.valid_time, r.parameter) for r in result]

        assert len(keys) == len(set(keys))

    def test_no_nwp_archive_rows_emitted(self) -> None:
        # NWP_ARCHIVE is reserved-but-unused in v0b; the chain must never emit it.
        # (remove when NWP_ARCHIVE is re-introduced in v0c)
        hybrid = default_hybrid_forcing_source(forcing_store=_seed_store())

        result = hybrid.fetch_reanalysis(
            [_cfg()], _WINDOW_START, _WINDOW_END, _ALL_PARAMS
        )

        assert all(r.source != "nwp_archive" for r in result)

    def test_uncovered_date_yields_no_row(self) -> None:
        # 2022 sits in the documented coverage gap (post-CAMELS-CH-retirement,
        # pre-MeteoSwiss).
        hybrid = default_hybrid_forcing_source(forcing_store=_seed_store())

        result = hybrid.fetch_reanalysis(
            [_cfg()], _WINDOW_START, _WINDOW_END, _ALL_PARAMS
        )
        gap = ensure_utc(datetime(2022, 1, 1, tzinfo=UTC))

        assert all(r.valid_time != gap for r in result)

    def test_camels_only_station_survives_the_flip_with_no_row_no_raise(self) -> None:
        # Plan 115b4 §5D: a station whose weather-source binding literally
        # reads nwp_source="camels-ch" (a pre-flip artifact) must still
        # resolve cleanly through the (now CAMELS-tier-free) hybrid chain —
        # "resolves" here means completes without raising and yields an
        # empty, well-typed result, not that CAMELS-CH values are served.
        camels_only_cfg = StationWeatherSource(
            station_id=StationId("s1"),
            nwp_source="camels-ch",
            extraction_type=SpatialRepresentation.BASIN_AVERAGE,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.REANALYSIS,
        )
        store = FakeHistoricalForcingStore()
        store.store_forcing(
            [
                _raw(
                    source="camels-ch",
                    parameter="precipitation",
                    when=_CAMELS_ERA,
                    value=1.0,
                )
            ]
        )
        hybrid = default_hybrid_forcing_source(forcing_store=store)

        result = hybrid.fetch_reanalysis(
            [camels_only_cfg], _WINDOW_START, _WINDOW_END, _ALL_PARAMS
        )

        assert result == []
