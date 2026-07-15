"""Plan 072 T2 — LOCKED acceptance tests for ``HybridForcingSource``.

The core of Plan 072 / epic 088 M1 criterion 3: chain per-source
``WeatherReanalysisSource`` instances with PER-PARAMETER priority and emit a
unified, deduplicated row stream — AT MOST ONE winning row per
``(station_id, valid_time, parameter)``, with the winner being the
highest-priority source *for that parameter*. Coverage gaps invent no rows.

These tests RED on the current tree because
``sapphire_flow.adapters.hybrid_reanalysis`` does not exist yet.

Expected implementation contract
--------------------------------
``sapphire_flow.adapters.hybrid_reanalysis.HybridForcingSource``
    ``__init__(self, *,
                sources: dict[ForcingSource, WeatherReanalysisSource],
                priority: Mapping[str, tuple[ForcingSource, ...]]) -> None``
    ``fetch_reanalysis(self, station_configs, start, end, parameters)
        -> list[RawHistoricalForcing]``

Resolution rule (per Plan 072 D2): fan out to every source in ``sources``,
collect rows keyed on ``(station_id, valid_time, parameter)``, and for each
key walk ``priority[parameter]`` in order, keeping the first row whose
``.source`` tag equals a ``ForcingSource.value`` in that list. A key absent
from all tiers yields no row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sapphire_flow.adapters.hybrid_reanalysis import HybridForcingSource
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.forcing_sources import ForcingSource
from sapphire_flow.types.historical_forcing import RawHistoricalForcing
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.station import StationWeatherSource

_START: UtcDatetime = ensure_utc(datetime(2026, 5, 1, tzinfo=UTC))
_END: UtcDatetime = ensure_utc(datetime(2026, 5, 31, tzinfo=UTC))


def _raw(
    *,
    source: str,
    station: str = "s1",
    parameter: str = "precipitation",
    day: int = 1,
    value: float,
) -> RawHistoricalForcing:
    return RawHistoricalForcing(
        station_id=StationId(station),
        source=source,
        version="v1",
        valid_time=ensure_utc(datetime(2026, 5, day, tzinfo=UTC)),
        parameter=parameter,
        spatial_type=SpatialRepresentation.BASIN_AVERAGE,
        band_id=None,
        member_id=None,
        value=value,
    )


def _cfg(station: str = "s1") -> StationWeatherSource:
    return StationWeatherSource(
        station_id=StationId(station),
        nwp_source="unused-by-hybrid",
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=WeatherSourceStatus.ACTIVE,
        role=WeatherSourceRole.REANALYSIS,
    )


def _forecast_cfg(station: str = "s1") -> StationWeatherSource:
    return StationWeatherSource(
        station_id=StationId(station),
        nwp_source="icon_ch2_eps",
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=WeatherSourceStatus.ACTIVE,
        role=WeatherSourceRole.FORECAST,
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class _StubReanalysisSource:
    """In-memory ``WeatherReanalysisSource`` returning fixed rows.

    A faithful fake (not a mock): it honours the ``parameters`` filter so the
    resolver sees exactly the rows a real per-source reader would surface.
    """

    rows: tuple[RawHistoricalForcing, ...]

    def fetch_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[RawHistoricalForcing]:
        wanted_stations = {c.station_id for c in station_configs}
        return [
            r
            for r in self.rows
            if r.parameter in parameters
            and r.station_id in wanted_stations
            and start <= r.valid_time < end
        ]


def _by_key(
    rows: list[RawHistoricalForcing],
) -> dict[tuple[StationId, UtcDatetime, str], RawHistoricalForcing]:
    return {(r.station_id, r.valid_time, r.parameter): r for r in rows}


class TestHybridForcingSource:
    def test_higher_priority_source_wins_on_overlap(self) -> None:
        meteo = _StubReanalysisSource(
            rows=(_raw(source="meteoswiss_rprelimd", value=5.0),)
        )
        camels = _StubReanalysisSource(rows=(_raw(source="camels-ch", value=99.0),))
        hybrid = HybridForcingSource(
            sources={
                ForcingSource.METEOSWISS_RPRELIMD: meteo,
                ForcingSource.CAMELS_CH: camels,
            },
            priority={
                "precipitation": (
                    ForcingSource.METEOSWISS_RPRELIMD,
                    ForcingSource.CAMELS_CH,
                )
            },
        )

        result = hybrid.fetch_reanalysis([_cfg()], _START, _END, ["precipitation"])

        # At most one winning row for the overlapping key.
        assert len(result) == 1
        assert result[0].source == "meteoswiss_rprelimd"
        assert result[0].value == 5.0

    def test_falls_through_to_lower_priority_when_top_absent(self) -> None:
        meteo = _StubReanalysisSource(rows=())  # top tier has nothing
        camels = _StubReanalysisSource(rows=(_raw(source="camels-ch", value=99.0),))
        hybrid = HybridForcingSource(
            sources={
                ForcingSource.METEOSWISS_RPRELIMD: meteo,
                ForcingSource.CAMELS_CH: camels,
            },
            priority={
                "precipitation": (
                    ForcingSource.METEOSWISS_RPRELIMD,
                    ForcingSource.CAMELS_CH,
                )
            },
        )

        result = hybrid.fetch_reanalysis([_cfg()], _START, _END, ["precipitation"])

        assert len(result) == 1
        assert result[0].source == "camels-ch"
        assert result[0].value == 99.0

    def test_per_parameter_priority_resolves_independently(self) -> None:
        # Precipitation prefers MeteoSwiss; temperature prefers CAMELS-CH.
        rprelimd = _StubReanalysisSource(
            rows=(
                _raw(
                    source="meteoswiss_rprelimd", parameter="precipitation", value=5.0
                ),
            )
        )
        tabsd = _StubReanalysisSource(
            rows=(_raw(source="meteoswiss_tabsd", parameter="temperature", value=20.0),)
        )
        camels = _StubReanalysisSource(
            rows=(
                _raw(source="camels-ch", parameter="precipitation", value=99.0),
                _raw(source="camels-ch", parameter="temperature", value=88.0),
            )
        )
        hybrid = HybridForcingSource(
            sources={
                ForcingSource.METEOSWISS_RPRELIMD: rprelimd,
                ForcingSource.METEOSWISS_TABSD: tabsd,
                ForcingSource.CAMELS_CH: camels,
            },
            priority={
                "precipitation": (
                    ForcingSource.METEOSWISS_RPRELIMD,
                    ForcingSource.CAMELS_CH,
                ),
                "temperature": (
                    ForcingSource.CAMELS_CH,
                    ForcingSource.METEOSWISS_TABSD,
                ),
            },
        )

        result = hybrid.fetch_reanalysis(
            [_cfg()], _START, _END, ["precipitation", "temperature"]
        )
        by_param = {r.parameter: r for r in result}

        assert by_param["precipitation"].source == "meteoswiss_rprelimd"
        assert by_param["precipitation"].value == 5.0
        assert by_param["temperature"].source == "camels-ch"
        assert by_param["temperature"].value == 88.0

    def test_coverage_gap_yields_no_synthetic_row(self) -> None:
        # Only precipitation exists in any tier; temperature is requested but
        # absent everywhere — the resolver must invent nothing.
        meteo = _StubReanalysisSource(
            rows=(
                _raw(
                    source="meteoswiss_rprelimd", parameter="precipitation", value=5.0
                ),
            )
        )
        camels = _StubReanalysisSource(
            rows=(_raw(source="camels-ch", parameter="precipitation", value=99.0),)
        )
        hybrid = HybridForcingSource(
            sources={
                ForcingSource.METEOSWISS_RPRELIMD: meteo,
                ForcingSource.CAMELS_CH: camels,
            },
            priority={
                "precipitation": (
                    ForcingSource.METEOSWISS_RPRELIMD,
                    ForcingSource.CAMELS_CH,
                ),
                "temperature": (
                    ForcingSource.METEOSWISS_TABSD,
                    ForcingSource.CAMELS_CH,
                ),
            },
        )

        result = hybrid.fetch_reanalysis(
            [_cfg()], _START, _END, ["precipitation", "temperature"]
        )

        assert {r.parameter for r in result} == {"precipitation"}

    def test_absent_valid_time_yields_no_row(self) -> None:
        # Rows only on day 1; nothing on day 2 → no day-2 row appears.
        meteo = _StubReanalysisSource(
            rows=(_raw(source="meteoswiss_rprelimd", day=1, value=5.0),)
        )
        hybrid = HybridForcingSource(
            sources={ForcingSource.METEOSWISS_RPRELIMD: meteo},
            priority={"precipitation": (ForcingSource.METEOSWISS_RPRELIMD,)},
        )

        result = hybrid.fetch_reanalysis([_cfg()], _START, _END, ["precipitation"])
        day2 = ensure_utc(datetime(2026, 5, 2, tzinfo=UTC))

        assert all(r.valid_time != day2 for r in result)

    def test_no_duplicate_keys_across_overlapping_tiers(self) -> None:
        # Both tiers carry the same two keys; output must be deduplicated.
        meteo = _StubReanalysisSource(
            rows=(
                _raw(source="meteoswiss_rprelimd", day=1, value=5.0),
                _raw(source="meteoswiss_rprelimd", day=2, value=6.0),
            )
        )
        camels = _StubReanalysisSource(
            rows=(
                _raw(source="camels-ch", day=1, value=99.0),
                _raw(source="camels-ch", day=2, value=98.0),
            )
        )
        hybrid = HybridForcingSource(
            sources={
                ForcingSource.METEOSWISS_RPRELIMD: meteo,
                ForcingSource.CAMELS_CH: camels,
            },
            priority={
                "precipitation": (
                    ForcingSource.METEOSWISS_RPRELIMD,
                    ForcingSource.CAMELS_CH,
                )
            },
        )

        result = hybrid.fetch_reanalysis([_cfg()], _START, _END, ["precipitation"])
        keys = [(r.station_id, r.valid_time, r.parameter) for r in result]

        assert len(keys) == len(set(keys))
        assert len(keys) == 2
        # Higher-priority MeteoSwiss wins both keys.
        assert {r.source for r in result} == {"meteoswiss_rprelimd"}
        assert {r.value for r in _by_key(result).values()} == {5.0, 6.0}

    def test_forecast_binding_is_excluded_before_fan_out(self) -> None:
        # A FORECAST binding must never be handed to a child reanalysis
        # source — even though the stub has data for that station under a
        # tag the priority chain recognises, the row must not leak into the
        # result. Soundness: fails against an implementation that forwards
        # the raw, unfiltered station_configs list to fan-out, since the stub
        # source filters only on station_id, not role.
        meteo = _StubReanalysisSource(
            rows=(
                _raw(source="meteoswiss_rprelimd", station="s1", value=5.0),
                _raw(source="meteoswiss_rprelimd", station="s2", value=7.0),
            )
        )
        hybrid = HybridForcingSource(
            sources={ForcingSource.METEOSWISS_RPRELIMD: meteo},
            priority={"precipitation": (ForcingSource.METEOSWISS_RPRELIMD,)},
        )

        result = hybrid.fetch_reanalysis(
            [_forecast_cfg("s1"), _cfg("s2")], _START, _END, ["precipitation"]
        )

        assert {r.station_id for r in result} == {StationId("s2")}

    def test_forecast_only_list_produces_no_rows(self) -> None:
        meteo = _StubReanalysisSource(
            rows=(_raw(source="meteoswiss_rprelimd", station="s1", value=5.0),)
        )
        hybrid = HybridForcingSource(
            sources={ForcingSource.METEOSWISS_RPRELIMD: meteo},
            priority={"precipitation": (ForcingSource.METEOSWISS_RPRELIMD,)},
        )

        result = hybrid.fetch_reanalysis(
            [_forecast_cfg("s1")], _START, _END, ["precipitation"]
        )

        assert result == []
