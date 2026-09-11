"""Plan 159 T1 — construction and discovery, against the REAL aquacast package. These
tests require the `aquacast` extra (`uv sync --extra aquacast`) and skip without it, in
the manner of the existing `live_*` markers. They are deliberately NOT written against a
fabricated entry point: Plan 157 shipped a shim test that monkeypatched
`importlib.metadata.entry_points` with an invented class, which was green whether or not
the real package existed and had to be deleted. The whole point of Plan 159 D17 —
keeping the shim in this repo — is that these can be real.

Plan 181's T1/T2/T3 behavior tests (FI-surface delegation, declaration rewrite, data
translation) moved to `test_aquacast_shim_translation.py`, which needs NO extra: the
translation functions are pure functions of `forecast_interface` types, and only
`AquacastShim.__init__` actually touches `aquacast`. Splitting them out means CI's
required `unit` job (`uv sync --frozen`, no `aquacast` extra) actually RUNS that
behavior suite instead of silently skipping the whole module (Plan 181 fixer finding).

What stays HERE is only what genuinely needs the real, installed package: construction
against the real vendored config, real `discover_models` registration, and the one
guard check that specifically proves the real config's own (daily) time step does not
trip the D1 relabel guard.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from forecast_interface import HorizonSemantics

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


class TestRealConfigPrecipitationGuard:
    """Plan 181 D1: the fabricated non-daily-step guard test lives in
    `test_aquacast_shim_translation.py` (no extra needed). This is the complementary
    real-package check: the ACTUAL vendored `cmal_pool_PT` config is daily-only, so
    binding it for real must never trip that guard.
    """

    def test_the_real_daily_config_does_not_trigger_the_guard(self) -> None:
        from sapphire_flow.models.aquacast import CmalPoolPT

        assert CmalPoolPT().input_requirement is not None


class TestCmalSmallDeclaration:
    """Plan 262 T1. Needs the real package (construction touches `aquacast`), so these
    SKIP in CI's required `unit` job — the extra-free digest assertions live in
    `test_aquacast_shim_translation.py`. Do not claim CI coverage for this class.
    """

    def test_constructs_and_binds_the_thirty_day_config(self) -> None:
        """The 30-day lookback is the entire reason this artifact is reachable on Swiss
        data and `cmal_pool_pt`'s 210 is not.
        """
        from sapphire_flow.models.aquacast import CmalSmall

        req = CmalSmall().input_requirement

        assert len(req.static) == 78
        assert set(req.targets) == {"discharge"}

    def test_declares_group_scope(self) -> None:
        """Both aquacast artifacts are GROUP-scoped: `_scope` returns STATION only when
        the config names exactly one gauge, and this one names 15,489 basins.
        """
        from sapphire_flow.models.aquacast import CmalSmall

        assert CmalSmall().artifact_scope.value == "group"

    def test_declares_the_owners_classification(self) -> None:
        """Owner decision 2026-09-09: ranked with the real forecasting models, but
        barred from raising alerts until it is seen to work on Swiss rivers. Note the
        eligibility DIFFERS from `CmalPoolPT`'s — a copied declaration would be wrong.
        """
        from sapphire_flow.models.aquacast import CmalPoolPT, CmalSmall

        assert CmalSmall.model_tier is ModelTier.SKILL
        assert CmalSmall.alert_eligibility is AlertEligibility.NO_EVENT_INFORMATION
        assert CmalSmall.static_naming is StaticNaming.CARAVAN
        assert CmalPoolPT.alert_eligibility is not CmalSmall.alert_eligibility

    def test_horizon_is_relaxable_on_every_future_known_variable(self) -> None:
        """Plan 241 landed the propagation; this records what the artifact declares, so
        `resolve_required_steps` returns min(1, 10) = 1 and the 5-day ICON feed clears
        the coverage gate.
        """
        from sapphire_flow.models.aquacast import CmalSmall

        req = CmalSmall().input_requirement
        # `dynamic` is keyed by time step, then spatial representation, then namespace.
        daily = req.dynamic[timedelta(days=1)]
        spec = next(iter(daily.data.values()))
        future_known = {
            name: var
            for namespace in spec.future_known.values()
            for name, var in namespace.items()
        }

        assert set(future_known) == {"precipitation", "temperature"}
        for var in future_known.values():
            assert var.horizon_semantics is HorizonSemantics.AT_MOST
            assert var.min_future_steps == 1
            # The TRAINED horizon; `resolve_required_steps` takes min(1, 10) = 1.
            assert var.future_steps == 10

    def test_every_past_known_variable_uses_the_thirty_day_lookback(self) -> None:
        """Measured on the real artifact: discharge, precipitation and temperature all
        declare 30 — which is what the two pilot stations' discharge depth has to reach
        before the model will produce anything.
        """
        from sapphire_flow.models.aquacast import CmalSmall

        daily = CmalSmall().input_requirement.dynamic[timedelta(days=1)]
        spec = next(iter(daily.data.values()))
        past_known = {
            name: var
            for namespace in spec.past_known.values()
            for name, var in namespace.items()
        }

        assert set(past_known) == {"discharge", "precipitation", "temperature"}
        assert {var.lookback for var in past_known.values()} == {30}

    def test_config_hash_is_the_vendored_files_digest(self) -> None:
        """The digest is taken from the same file `__init__` binds, so the hash and the
        bound config cannot drift apart.
        """
        import hashlib
        from importlib import resources

        from sapphire_flow.models.aquacast import CmalSmall

        expected = hashlib.sha256(
            resources.files("sapphire_flow.models.aquacast.configs")
            .joinpath("cmal_small.yaml")
            .read_bytes()
        ).hexdigest()

        assert CmalSmall().config_hash == expected

    def test_both_shims_expose_a_config_hash(self) -> None:
        """`config_hash` lives on the BASE class, so adding it for `cmal_small` also
        makes `cmal_pool_pt` importable for the first time. That is a deliberate,
        additive consequence: it opens a path, it promotes nothing.
        """
        from sapphire_flow.models.aquacast import CmalPoolPT, CmalSmall

        assert CmalPoolPT().config_hash != CmalSmall().config_hash
