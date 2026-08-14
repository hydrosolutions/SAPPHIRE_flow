"""Plan 155 round-2 review MAJOR ("test surface"): no Caravan test reached
the real `services/hindcast.py::_load_static_attributes` boundary --
locked directly here, the same D15/D16 no-bare-fallback contract the pure
`services/caravan_statics.py` tests already prove in isolation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sapphire_flow.services.caravan_statics import CARAVAN_PREFIX
from sapphire_flow.services.hindcast import _load_static_attributes
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import StaticNaming
from sapphire_flow.types.ids import BasinId
from tests.conftest import make_station_config
from tests.fakes.fake_stores import FakeBasinStore

_EPOCH = ensure_utc(datetime(2024, 1, 1, tzinfo=UTC))


def _basin(basin_id: BasinId) -> Basin:
    return Basin(
        id=basin_id,
        code="2009",
        name="Basin 2009",
        geometry=None,
        area_km2=100.0,
        attributes={
            "area": 100.0,  # CAMELS-CH's own bare attribute (must NOT win)
            f"{CARAVAN_PREFIX}area": 250.0,  # Caravan's, namespaced (D15)
            f"{CARAVAN_PREFIX}slp_dg_sav": 12.5,  # aliased -> "slope"
        },
        band_geometries=None,
        created_at=_EPOCH,
        network="bafu",
    )


class TestLoadStaticAttributesResolvesCaravanStatics:
    def test_caravan_naming_projects_declared_names_from_caravans_source(self) -> None:
        basin_id = BasinId(uuid.uuid4())
        basin_store = FakeBasinStore()
        basin_store.store_basin(_basin(basin_id))
        station_config = make_station_config(basin_id=basin_id, code="2009")

        frame = _load_static_attributes(
            basin_store,
            station_config,
            frozenset({"slope", "area"}),
            StaticNaming.CARAVAN,
        )

        assert frame is not None
        assert frame["area"][0] == 250.0  # Caravan's, not CAMELS-CH's 100.0
        assert frame["slope"][0] == 12.5

    def test_native_naming_leaves_the_frame_unprojected(self) -> None:
        """D16: a NATIVE model (the default) keeps today's unprojected
        frame byte-for-byte -- it must see the bare CAMELS-CH "area", not
        Caravan's, and must not see "slope" at all (no bare key for it)."""
        basin_id = BasinId(uuid.uuid4())
        basin_store = FakeBasinStore()
        basin_store.store_basin(_basin(basin_id))
        station_config = make_station_config(basin_id=basin_id, code="2009")

        frame = _load_static_attributes(
            basin_store,
            station_config,
            frozenset({"slope", "area"}),
            StaticNaming.NATIVE,
        )

        assert frame is not None
        assert frame["area"][0] == 100.0  # CAMELS-CH's own bare value
        assert "slope" not in frame.columns

    def test_caravan_naming_with_no_source_at_all_returns_none(self) -> None:
        """No `basin_id` at all: unaffected by static_naming, unchanged
        from today's behaviour."""
        station_config = make_station_config(basin_id=None, code="2009")
        basin_store = FakeBasinStore()

        frame = _load_static_attributes(
            basin_store, station_config, frozenset({"area"}), StaticNaming.CARAVAN
        )

        assert frame is None
