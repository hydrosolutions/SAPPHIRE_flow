from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.enums import StationKind
from sapphire_flow.types.ids import AQUACAST_CMAL_POOL_PT_MODEL_ID, ModelId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.conftest import make_station_config
from tests.fakes.fake_stores import FakeStationStore

_SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "import_caravan_attributes.py"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location(
        "import_caravan_attributes_script", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["import_caravan_attributes_script"] = module
    spec.loader.exec_module(module)
    return module


class TestDeriveSwissCaravanManifest:
    """Plan 188 D1: the T0a manifest rule, re-derived live -- discharge in
    both `forecast_targets` and `measured_parameters`, network "bafu",
    `StationKind.RIVER`, the `sapphire` tenant, minus the one owner-dropped
    regulated canal (2446)."""

    def test_a_default_discharge_station_is_included(self, mod) -> None:
        store = FakeStationStore()
        store.store_station(make_station_config(code="2009"))

        assert mod.derive_swiss_caravan_manifest(store) == frozenset({"2009"})

    def test_a_lake_station_kind_is_excluded(self, mod) -> None:
        store = FakeStationStore()
        store.store_station(
            make_station_config(code="2004", station_kind=StationKind.LAKE)
        )

        assert mod.derive_swiss_caravan_manifest(store) == frozenset()

    def test_a_station_without_discharge_forecast_target_is_excluded(self, mod) -> None:
        store = FakeStationStore()
        store.store_station(
            make_station_config(
                code="2004",
                forecast_targets=frozenset({"water_level"}),
                measured_parameters=frozenset({"water_level"}),
            )
        )

        assert mod.derive_swiss_caravan_manifest(store) == frozenset()

    def test_a_non_bafu_network_station_is_excluded(self, mod) -> None:
        store = FakeStationStore()
        store.store_station(make_station_config(code="9001", network="smn"))

        assert mod.derive_swiss_caravan_manifest(store) == frozenset()

    def test_a_non_default_tenant_station_is_excluded(self, mod) -> None:
        from uuid import uuid4

        from sapphire_flow.types.ids import TenantId

        store = FakeStationStore()
        store.store_station(
            make_station_config(code="9002", tenant_id=TenantId(uuid4()))
        )

        assert mod.derive_swiss_caravan_manifest(store) == frozenset()

    def test_gampelen_zihlbruecke_2446_is_dropped_even_though_it_otherwise_qualifies(
        self, mod
    ) -> None:
        store = FakeStationStore()
        store.store_station(make_station_config(code="2446"))
        store.store_station(make_station_config(code="2009"))

        assert mod.derive_swiss_caravan_manifest(store) == frozenset({"2009"})

    def test_default_tenant_id_used_is_the_sapphire_default(self, mod) -> None:
        # Locks that the module derives against the SAME constant this
        # test imports from the real types module, not a re-typed literal.
        store = FakeStationStore()
        store.store_station(
            make_station_config(code="2009", tenant_id=DEFAULT_TENANT_ID)
        )

        assert mod.derive_swiss_caravan_manifest(store) == frozenset({"2009"})


class TestCheckManifestMatchesPinned:
    """Plan 188 D1: SET IDENTITY, not cardinality -- `len(...) == 148` also
    passes a fleet that lost one station and gained another."""

    def test_identical_sets_pass_silently(self, mod) -> None:
        mod.check_manifest_matches_pinned(
            frozenset({"2009", "2010"}), frozenset({"2009", "2010"})
        )  # no raise

    def test_a_one_out_one_in_swap_raises_and_names_the_symmetric_difference(
        self, mod
    ) -> None:
        derived = frozenset({"2009", "2011"})  # 2011 replaces 2010
        pinned = frozenset({"2009", "2010"})

        with pytest.raises(ConfigurationError) as exc_info:
            mod.check_manifest_matches_pinned(derived, pinned)

        message = str(exc_info.value)
        assert "2011" in message  # only in derived
        assert "2010" in message  # only in pinned
        # Same cardinality (2 vs 2) -- a len(...) == 148-style check would
        # have missed this; the identity check must not.
        assert len(derived) == len(pinned)

    def test_a_pure_cardinality_match_with_differing_membership_still_raises(
        self, mod
    ) -> None:
        with pytest.raises(ConfigurationError, match="2011"):
            mod.check_manifest_matches_pinned(frozenset({"2011"}), frozenset({"2010"}))


class TestResolveRequiredStaticNames:
    """Plan 188 D2: read required statics from the DISCOVERED adapter --
    never a hand-typed literal -- and fail naming the model id when it is
    absent from `discover_models()`, rather than a bare KeyError or an
    empty static set."""

    def test_model_absent_from_registry_raises_naming_the_model_id(self, mod) -> None:
        # Real, unmocked discover_models(): this dev checkout has no
        # `aquacast` extra installed, so `cmal_pool_pt` is genuinely
        # absent -- exercising the real D2 preflight, not a fake.
        with pytest.raises(ConfigurationError, match=AQUACAST_CMAL_POOL_PT_MODEL_ID):
            mod.resolve_required_static_names()

    def test_a_registered_model_returns_its_declared_static_features(
        self, mod, monkeypatch
    ) -> None:
        fake_model = MagicMock()
        fake_model.data_requirements.static_features = frozenset({"area", "slope"})
        monkeypatch.setattr(
            "sapphire_flow.services.model_registry.discover_models",
            MagicMock(return_value={ModelId("fake_model"): fake_model}),
        )

        result = mod.resolve_required_static_names(model_id=ModelId("fake_model"))

        assert result == frozenset({"area", "slope"})
