"""Plan 072 T3 — LOCKED acceptance tests for ``default_hybrid_forcing_source``.

The factory wires the v0b priority chain (MeteoSwiss -> CAMELS-CH, per
parameter) over a ``HistoricalForcingStore``. This test seeds a mixed-regime
window (CAMELS-CH pre-2020 / MeteoSwiss post-2026-04) and asserts the
per-parameter source distribution resolves to the documented winners with no
duplicate ``(station_id, valid_time, parameter)`` rows.

REDs on the current tree because
``sapphire_flow.adapters.hybrid_reanalysis_factories`` does not exist yet.

Expected implementation contract
--------------------------------
``sapphire_flow.adapters.hybrid_reanalysis_factories.default_hybrid_forcing_source``
    ``default_hybrid_forcing_source(*,
        forcing_store: HistoricalForcingStore,
        parameters_in_scope: tuple[str, ...] = (
            "precipitation", "temperature",
            "temperature_min", "temperature_max",
        ),
    ) -> HybridForcingSource``

Priority chains wired by the factory (Plan 072 §Priority chains):
    precipitation     : METEOSWISS_RPRELIMD -> CAMELS_CH
    temperature       : METEOSWISS_TABSD    -> CAMELS_CH
    temperature_min   : METEOSWISS_TMIND    -> CAMELS_CH
    temperature_max   : METEOSWISS_TMAXD    -> CAMELS_CH
"""

from __future__ import annotations

from datetime import UTC, datetime

from sapphire_flow.adapters.hybrid_reanalysis_factories import (
    default_hybrid_forcing_source,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation, WeatherSourceStatus
from sapphire_flow.types.historical_forcing import RawHistoricalForcing
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.station import StationWeatherSource
from tests.fakes.fake_stores import FakeHistoricalForcingStore

_ALL_PARAMS = ["precipitation", "temperature", "temperature_min", "temperature_max"]
_WINDOW_START: UtcDatetime = ensure_utc(datetime(2019, 1, 1, tzinfo=UTC))
_WINDOW_END: UtcDatetime = ensure_utc(datetime(2026, 6, 1, tzinfo=UTC))
_PRE = ensure_utc(datetime(2019, 6, 1, tzinfo=UTC))
_POST = ensure_utc(datetime(2026, 5, 1, tzinfo=UTC))


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
    )


def _seed_store() -> FakeHistoricalForcingStore:
    store = FakeHistoricalForcingStore()
    store.store_forcing(
        [
            # Pre-2020: only CAMELS-CH covers all four parameters.
            _raw(source="camels-ch", parameter="precipitation", when=_PRE, value=1.0),
            _raw(source="camels-ch", parameter="temperature", when=_PRE, value=2.0),
            _raw(source="camels-ch", parameter="temperature_min", when=_PRE, value=3.0),
            _raw(source="camels-ch", parameter="temperature_max", when=_PRE, value=4.0),
            # Post-2026-04: MeteoSwiss per-parameter sources.
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
            # Overlap: CAMELS-CH also has a post-window precip row — MeteoSwiss
            # must win on priority (not merely on coverage).
            _raw(
                source="camels-ch", parameter="precipitation", when=_POST, value=111.0
            ),
        ]
    )
    return store


class TestDefaultHybridForcingSource:
    def test_pre_2020_window_resolves_to_camels_ch(self) -> None:
        hybrid = default_hybrid_forcing_source(forcing_store=_seed_store())

        result = hybrid.fetch_reanalysis(
            [_cfg()], _WINDOW_START, _WINDOW_END, _ALL_PARAMS
        )
        pre = {r.parameter: r for r in result if r.valid_time == _PRE}

        assert set(pre) == set(_ALL_PARAMS)
        assert {r.source for r in pre.values()} == {"camels-ch"}

    def test_post_window_resolves_to_per_parameter_meteoswiss(self) -> None:
        hybrid = default_hybrid_forcing_source(forcing_store=_seed_store())

        result = hybrid.fetch_reanalysis(
            [_cfg()], _WINDOW_START, _WINDOW_END, _ALL_PARAMS
        )
        post = {r.parameter: r for r in result if r.valid_time == _POST}

        assert post["precipitation"].source == "meteoswiss_rprelimd"
        assert post["precipitation"].value == 7.0  # MeteoSwiss beats CAMELS overlap
        assert post["temperature"].source == "meteoswiss_tabsd"
        assert post["temperature_min"].source == "meteoswiss_tmind"
        assert post["temperature_max"].source == "meteoswiss_tmaxd"

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
        # 2022 sits in the documented coverage gap (post-CAMELS, pre-MeteoSwiss).
        hybrid = default_hybrid_forcing_source(forcing_store=_seed_store())

        result = hybrid.fetch_reanalysis(
            [_cfg()], _WINDOW_START, _WINDOW_END, _ALL_PARAMS
        )
        gap = ensure_utc(datetime(2022, 1, 1, tzinfo=UTC))

        assert all(r.valid_time != gap for r in result)
