"""Plan 072 T1 — LOCKED acceptance tests for ``PerSourceStoreReader``.

A thin ``WeatherReanalysisSource`` that reads rows for a SINGLE
``ForcingSource`` tag, fixed at construction time, and ignores
``station_config.nwp_source``. These tests pin behaviour (known-answer
source filtering + value pass-through); they RED on the current tree because
``sapphire_flow.adapters.per_source_store_reader`` does not exist yet.

Expected implementation contract
--------------------------------
``sapphire_flow.adapters.per_source_store_reader.PerSourceStoreReader``
    ``__init__(self, *, forcing_store: HistoricalForcingStore,
                source: ForcingSource) -> None``
    ``fetch_reanalysis(self, station_configs: list[StationWeatherSource],
                        start: UtcDatetime, end: UtcDatetime,
                        parameters: list[str]) -> list[RawHistoricalForcing]``
    Reads via ``forcing_store.fetch_forcing(station_id=cfg.station_id,
    source=self._source.value, ...)`` — uses the ctor-fixed source tag,
    NEVER ``cfg.nwp_source``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sapphire_flow.adapters.per_source_store_reader import PerSourceStoreReader
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation, WeatherSourceStatus
from sapphire_flow.types.forcing_sources import ForcingSource
from sapphire_flow.types.historical_forcing import RawHistoricalForcing
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.station import StationWeatherSource
from tests.fakes.fake_stores import FakeHistoricalForcingStore

_START: UtcDatetime = ensure_utc(datetime(2026, 5, 1, tzinfo=UTC))
_END: UtcDatetime = ensure_utc(datetime(2026, 5, 10, tzinfo=UTC))


def _raw(
    *,
    source: str,
    station: str = "s1",
    parameter: str = "precipitation",
    day: int = 1,
    value: float = 1.0,
    version: str = "v1",
) -> RawHistoricalForcing:
    return RawHistoricalForcing(
        station_id=StationId(station),
        source=source,
        version=version,
        valid_time=ensure_utc(datetime(2026, 5, day, tzinfo=UTC)),
        parameter=parameter,
        spatial_type=SpatialRepresentation.BASIN_AVERAGE,
        band_id=None,
        member_id=None,
        value=value,
    )


def _cfg(station: str = "s1", nwp_source: str = "camels-ch") -> StationWeatherSource:
    return StationWeatherSource(
        station_id=StationId(station),
        nwp_source=nwp_source,
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=WeatherSourceStatus.ACTIVE,
    )


class TestPerSourceStoreReader:
    def test_returns_only_rows_for_the_configured_source(self) -> None:
        store = FakeHistoricalForcingStore()
        store.store_forcing(
            [
                _raw(source="meteoswiss_rprelimd", value=5.0),
                _raw(source="camels-ch", value=99.0),
            ]
        )
        reader = PerSourceStoreReader(
            forcing_store=store, source=ForcingSource.METEOSWISS_RPRELIMD
        )

        result = reader.fetch_reanalysis(
            station_configs=[_cfg()],
            start=_START,
            end=_END,
            parameters=["precipitation"],
        )

        assert len(result) == 1
        assert result[0].source == "meteoswiss_rprelimd"
        assert result[0].value == 5.0

    def test_ignores_station_config_nwp_source(self) -> None:
        # The station config points at camels-ch, but the reader is fixed to
        # METEOSWISS_RPRELIMD — it must read the ctor source, not the config.
        store = FakeHistoricalForcingStore()
        store.store_forcing(
            [
                _raw(source="meteoswiss_rprelimd", value=5.0),
                _raw(source="camels-ch", value=99.0),
            ]
        )
        reader = PerSourceStoreReader(
            forcing_store=store, source=ForcingSource.METEOSWISS_RPRELIMD
        )

        result = reader.fetch_reanalysis(
            station_configs=[_cfg(nwp_source="camels-ch")],
            start=_START,
            end=_END,
            parameters=["precipitation"],
        )

        assert {r.source for r in result} == {"meteoswiss_rprelimd"}

    def test_preserves_schema_fields(self) -> None:
        store = FakeHistoricalForcingStore()
        store.store_forcing(
            [_raw(source="camels-ch", parameter="temperature", value=7.5)]
        )
        reader = PerSourceStoreReader(
            forcing_store=store, source=ForcingSource.CAMELS_CH
        )

        result = reader.fetch_reanalysis(
            station_configs=[_cfg()],
            start=_START,
            end=_END,
            parameters=["temperature"],
        )

        assert len(result) == 1
        row = result[0]
        assert isinstance(row, RawHistoricalForcing)
        assert row.parameter == "temperature"
        assert row.value == 7.5
        assert row.spatial_type == SpatialRepresentation.BASIN_AVERAGE
        assert row.station_id == StationId("s1")

    def test_empty_when_no_rows_for_that_source(self) -> None:
        store = FakeHistoricalForcingStore()
        store.store_forcing([_raw(source="camels-ch", value=99.0)])
        reader = PerSourceStoreReader(
            forcing_store=store, source=ForcingSource.METEOSWISS_RPRELIMD
        )

        result = reader.fetch_reanalysis(
            station_configs=[_cfg()],
            start=_START,
            end=_END,
            parameters=["precipitation"],
        )

        assert result == []

    def test_reads_across_multiple_stations(self) -> None:
        store = FakeHistoricalForcingStore()
        store.store_forcing(
            [
                _raw(source="camels-ch", station="s1", value=1.0),
                _raw(source="camels-ch", station="s2", value=2.0),
            ]
        )
        reader = PerSourceStoreReader(
            forcing_store=store, source=ForcingSource.CAMELS_CH
        )

        result = reader.fetch_reanalysis(
            station_configs=[_cfg("s1"), _cfg("s2")],
            start=_START,
            end=_END,
            parameters=["precipitation"],
        )

        assert {r.station_id for r in result} == {StationId("s1"), StationId("s2")}

    def test_duplicate_station_configs_do_not_duplicate_rows(self) -> None:
        # A station carrying multiple weather-source rows (e.g. an ICON source
        # alongside a historical one) appears more than once in the flattened
        # config list. The fixed-source reader must fetch once per unique
        # station — not re-read and duplicate rows.
        store = FakeHistoricalForcingStore()
        store.store_forcing([_raw(source="camels-ch", station="s1", value=1.0)])
        reader = PerSourceStoreReader(
            forcing_store=store, source=ForcingSource.CAMELS_CH
        )

        result = reader.fetch_reanalysis(
            station_configs=[
                _cfg("s1", nwp_source="icon_ch2_eps"),
                _cfg("s1", nwp_source="camels-ch"),
            ],
            start=_START,
            end=_END,
            parameters=["precipitation"],
        )

        assert len(result) == 1
        assert result[0].value == 1.0
