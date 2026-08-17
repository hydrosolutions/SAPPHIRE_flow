"""Plan 159 T1 — construction and discovery, against the REAL aquacast package. These
tests require the `aquacast` extra (`uv sync --extra aquacast`) and skip without it, in
the manner of the existing `live_*` markers. They are deliberately NOT written against a
fabricated entry point: Plan 157 shipped a shim test that monkeypatched
`importlib.metadata.entry_points` with an invented class, which was green whether or not
the real package existed and had to be deleted. The whole point of Plan 159 D17 —
keeping the shim in this repo — is that these can be real.
"""

from __future__ import annotations

import pytest

from sapphire_flow.types.enums import AlertEligibility, ModelTier, StaticNaming

aquacast = pytest.importorskip("aquacast", reason="needs the `aquacast` extra")


class TestZeroArgumentConstruction:
    """The blocker the shim exists for: `discover_models` builds every entry point with
    NO arguments, while `AquacastModel.__init__` requires a template.
    """

    def test_constructs_with_no_arguments(self) -> None:
        from sapphire_flow.models.aquacast import CmalPoolPT

        model = CmalPoolPT()

        assert model.input_requirement is not None

    def test_binds_the_vendored_config_not_an_external_path(self) -> None:
        """The config ships as package data, so construction must not depend on a
        Dropbox path or any other machine-local location.
        """
        from sapphire_flow.models.aquacast import CmalPoolPT

        req = CmalPoolPT().input_requirement

        # Read off the real artifact (Plan 159, "PT's contract"): 50 Caravan-named
        # statics,
        # a single daily branch, discharge as the only target.
        assert len(req.static) == 50
        assert set(req.targets) == {"discharge"}

    def test_the_base_class_refuses_to_construct_without_a_config(self) -> None:
        from sapphire_flow.models.aquacast import AquacastShim

        with pytest.raises(TypeError, match="CONFIG_FILENAME"):
            AquacastShim()


class TestDeclarationsDiscoverModelsRequires:
    def test_declares_tier_eligibility_and_static_naming(self) -> None:
        from sapphire_flow.models.aquacast import CmalPoolPT

        model = CmalPoolPT()

        assert model.model_tier is ModelTier.SKILL
        assert model.alert_eligibility is AlertEligibility.SKILL_FORECAST
        # Plan 155 D16 — aquacast's statics are Caravan-named, so this model opts in to
        # the
        # strict `caravan:` resolution. NATIVE here would silently feed it CAMELS-CH
        # values.
        assert model.static_naming is StaticNaming.CARAVAN


class TestRealDiscovery:
    def test_discover_models_returns_the_aquacast_model(self) -> None:
        """A POSITIVE assertion, as Plan 159 requires: post-156 `discover_models` SKIPS
        an entry point it cannot construct or represent (`services/model_registry.py`),
        so "it constructs" is not "it is registered". Only membership in the returned
        mapping proves registration.
        """
        from sapphire_flow.services.model_registry import discover_models

        assert "cmal_pool_pt" in discover_models()
