"""Plan 115b4 — the two headline regression tests named in the plan's
``## Tests`` section (verbatim):

* **The double-dark regression:** with the MeteoSwiss binding present and
  ``hybrid`` default, rows written under product tags are readable end to
  end by the default consumer. *Must fail against today's wiring* (i.e.
  against the pre-115b4 ``single`` default — see the soundness check this
  test's docstring describes).
* **Priority, not supersession:** for a ``(station, valid_time, parameter)``
  covered by BOTH precip sources, a direct source-keyed fetch returns BOTH
  rows, while the hybrid reader returns only the ``RhiresD`` winner.

"Double-dark" (see project memory / Plan 115 track): a station's ONE
``station_weather_sources`` binding carries the ADAPTER's identity
(``nwp_source="meteoswiss_open_data_reanalysis"``), but the rows the
adapter's writer path actually persists carry PER-PRODUCT source tags
(``meteoswiss_rhiresd``, ``meteoswiss_tabsd``, ...) — never the adapter
identity string itself. ``StoreBackedReanalysisSource`` (the ``single``
reader) queries ``historical_forcing WHERE source = cfg.nwp_source``
literally, which can NEVER match a per-product tag — so under ``single`` the
feed is dark even though rows exist. Only the ``hybrid`` reader (which fans
out over registered ``ForcingSource`` tags, ignoring ``cfg.nwp_source`` for
the lookup key) can read them.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sapphire_flow.adapters.hybrid_reanalysis_factories import (
    select_reanalysis_source,
)
from sapphire_flow.adapters.meteoswiss_open_data_reanalysis import (
    MeteoSwissOpenDataReanalysisAdapter,
)
from sapphire_flow.config.deployment import DeploymentConfig
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
from tests.fakes.fake_stores import FakeHistoricalForcingStore

_START: UtcDatetime = ensure_utc(datetime(2026, 4, 1, tzinfo=UTC))
_END: UtcDatetime = ensure_utc(datetime(2026, 6, 1, tzinfo=UTC))
_DAY: UtcDatetime = ensure_utc(datetime(2026, 5, 1, tzinfo=UTC))
# max_retention_days must exceed forecast_hot_days (default 548).
_RETENTION = 600


def _raw(
    *, source: str, parameter: str = "precipitation", value: float
) -> RawHistoricalForcing:
    return RawHistoricalForcing(
        station_id=StationId("s1"),
        source=source,
        version="v1",
        valid_time=_DAY,
        parameter=parameter,
        spatial_type=SpatialRepresentation.BASIN_AVERAGE,
        band_id=None,
        member_id=None,
        value=value,
    )


def _meteoswiss_binding() -> StationWeatherSource:
    # The station's ONE weather-source binding carries the ADAPTER's own
    # identity string — never a per-product tag.
    return StationWeatherSource(
        station_id=StationId("s1"),
        nwp_source=MeteoSwissOpenDataReanalysisAdapter.NWP_SOURCE,
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=WeatherSourceStatus.ACTIVE,
        role=WeatherSourceRole.REANALYSIS,
    )


class TestDoubleDarkRegression:
    def test_default_config_serves_product_tagged_rows_end_to_end(self) -> None:
        store = FakeHistoricalForcingStore()
        store.store_forcing(
            [
                _raw(source=ForcingSource.METEOSWISS_RHIRESD.value, value=6.0),
                _raw(
                    source=ForcingSource.METEOSWISS_TABSD.value,
                    parameter="temperature",
                    value=12.0,
                ),
            ]
        )
        default_config = DeploymentConfig(max_retention_days=_RETENTION)

        reader = select_reanalysis_source(
            forcing_store=store, mode=default_config.reanalysis_source
        )
        rows = reader.fetch_reanalysis(
            [_meteoswiss_binding()], _START, _END, ["precipitation", "temperature"]
        )

        assert {r.parameter: r.value for r in rows} == {
            "precipitation": 6.0,
            "temperature": 12.0,
        }

    def test_single_mode_cannot_see_the_same_rows_the_soundness_proof(self) -> None:
        # Soundness: this is exactly what fails against the PRE-115b4
        # wiring (default "single") — proving the double-dark regression is
        # real, and that the fix is specifically the default-mode flip, not
        # an incidental side effect.
        store = FakeHistoricalForcingStore()
        store.store_forcing(
            [_raw(source=ForcingSource.METEOSWISS_RHIRESD.value, value=6.0)]
        )

        reader = select_reanalysis_source(forcing_store=store, mode="single")
        rows = reader.fetch_reanalysis(
            [_meteoswiss_binding()], _START, _END, ["precipitation"]
        )

        assert rows == []


class TestPriorityNotSupersession:
    """Plan 115b4 — for a key covered by BOTH precip sources, a direct
    source-keyed fetch returns BOTH rows; the hybrid reader returns only the
    RhiresD winner."""

    def test_direct_fetch_returns_both_hybrid_returns_only_the_winner(self) -> None:
        store = FakeHistoricalForcingStore()
        store.store_forcing(
            [
                _raw(source=ForcingSource.METEOSWISS_RHIRESD.value, value=6.0),
                _raw(source=ForcingSource.METEOSWISS_RPRELIMD.value, value=999.0),
            ]
        )

        # A direct, source-keyed fetch sees BOTH rows — no priority applied.
        direct_rhiresd = store.fetch_forcing(
            StationId("s1"), ForcingSource.METEOSWISS_RHIRESD.value, _START, _END
        )
        direct_rprelimd = store.fetch_forcing(
            StationId("s1"), ForcingSource.METEOSWISS_RPRELIMD.value, _START, _END
        )
        assert len(direct_rhiresd) == 1
        assert len(direct_rprelimd) == 1

        # The hybrid reader returns ONLY the RhiresD (definitive) winner.
        hybrid_reader = select_reanalysis_source(forcing_store=store, mode="hybrid")
        hybrid_rows = hybrid_reader.fetch_reanalysis(
            [_meteoswiss_binding()], _START, _END, ["precipitation"]
        )
        assert len(hybrid_rows) == 1
        assert hybrid_rows[0].source == "meteoswiss_rhiresd"
        assert hybrid_rows[0].value == 6.0
