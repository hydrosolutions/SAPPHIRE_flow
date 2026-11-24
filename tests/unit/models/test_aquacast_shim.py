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
from forecast_interface import AggregationMethod, HorizonSemantics, Unit

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

    def test_past_gap_budget_is_discharge_only(self) -> None:
        from sapphire_flow.models.aquacast import CmalSmall

        daily = CmalSmall().input_requirement.dynamic[timedelta(days=1)]
        spec = next(iter(daily.data.values()))
        assert {
            name: variable.max_nan
            for namespace in spec.past_known.values()
            for name, variable in namespace.items()
        } == {"discharge": 30, "precipitation": 0, "temperature": 0}

    def test_future_weather_has_zero_gap_budget(self) -> None:
        from sapphire_flow.models.aquacast import CmalSmall

        daily = CmalSmall().input_requirement.dynamic[timedelta(days=1)]
        spec = next(iter(daily.data.values()))
        assert {
            name: variable.max_nan
            for namespace in spec.future_known.values()
            for name, variable in namespace.items()
        } == {"precipitation": 0, "temperature": 0}

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


class TestCmalNepalStaticEncoder:
    def test_is_discovered_as_a_separate_group_model(self) -> None:
        from sapphire_flow.models.aquacast import CmalNepalStaticEncoder
        from sapphire_flow.services.model_registry import discover_models

        assert "cmal_nepal_static_encoder" in discover_models()
        assert CmalNepalStaticEncoder().artifact_scope.value == "group"

    def test_preserves_static_namespace_without_enabling_alerts_or_finetuning(
        self,
    ) -> None:
        from sapphire_flow.models.aquacast import CmalNepalStaticEncoder

        model = CmalNepalStaticEncoder()
        assert model.model_tier is ModelTier.SKILL
        assert model.alert_eligibility is AlertEligibility.NO_EVENT_INFORMATION
        assert model.static_naming is StaticNaming.CARAVAN
        assert not model.FINETUNE_STRATEGIES
        assert len(model.input_requirement.static) == 78

    def test_binds_the_supplied_configuration(self) -> None:
        from sapphire_flow.models.aquacast import CmalNepalStaticEncoder

        assert CmalNepalStaticEncoder().config_hash == (
            "c171b2bc26a3cd11eec64378322fae443a113a86d1fcfb2fe6f5edd2e1e296d9"
        )

    def test_requires_full_year_weather_but_permits_missing_discharge(self) -> None:
        from sapphire_flow.models.aquacast import CmalNepalStaticEncoder

        req = CmalNepalStaticEncoder().input_requirement
        spec = next(iter(req.dynamic[timedelta(days=1)].data.values()))
        past = {name: v for ns in spec.past_known.values() for name, v in ns.items()}

        assert {name: v.lookback for name, v in past.items()} == {
            "discharge": 365,
            "precipitation": 365,
            "temperature": 365,
            "thermal_net_radiation": 365,
            "solar_net_radiation": 365,
        }
        assert {name: v.max_nan for name, v in past.items()} == {
            "discharge": 365,
            "precipitation": 0,
            "temperature": 0,
            "thermal_net_radiation": 0,
            "solar_net_radiation": 0,
        }
        assert req.targets["discharge"].unit is Unit.M3_PER_S
        assert past["discharge"].unit is Unit.M3_PER_S
        assert past["discharge"].aggregation is AggregationMethod.MEAN

    def test_future_weather_retains_distinct_units_and_flexible_horizon(self) -> None:
        from sapphire_flow.models.aquacast import CmalNepalStaticEncoder

        req = CmalNepalStaticEncoder().input_requirement
        spec = next(iter(req.dynamic[timedelta(days=1)].data.values()))
        future = {
            name: v for ns in spec.future_known.values() for name, v in ns.items()
        }

        assert {name: v.unit.value for name, v in future.items()} == {
            "precipitation": "mm",
            "temperature": "°C",
            "thermal_net_radiation": "W/m²",
            "solar_net_radiation": "W/m²",
        }
        for variable in future.values():
            assert variable.future_steps == 15
            assert variable.horizon_semantics is HorizonSemantics.AT_MOST
            assert variable.min_future_steps == 1
            assert variable.max_nan == 0


class TestCmalSmallFineTuneAllowlist:
    """The retrain refuses a donor whose recorded config hash differs from the
    installed yaml's, so the strategy allowlist must live in the shim, never the yaml.
    """

    def test_the_vendored_yaml_bytes_are_unchanged(self) -> None:
        from sapphire_flow.models.aquacast import CmalSmall

        assert (
            CmalSmall().config_hash
            == "94ebec0fe4e000cecfd33ee8d50def9b8428b8f2e2ab7dbfeb77e2d04e580e45"
        )

    def test_permits_only_qualified_fine_tuning_strategies(self) -> None:
        from sapphire_flow.models.aquacast import CmalSmall

        permitted = CmalSmall.FINETUNE_STRATEGIES

        assert permitted == {"static_only", "last_layer", "lora"}

    def test_the_allowlist_reaches_the_template_aquacast_enforces(self) -> None:
        """aquacast refuses a strategy absent from the TEMPLATE's set; deleting the
        `replace` in the shim leaves the class attribute intact and enforcement off.
        No public accessor exists, so this reads the inner model's template.
        """
        from sapphire_flow.models.aquacast import CmalPoolPT, CmalSmall

        small = CmalSmall()._inner._model_template.finetune_strategies  # pyright: ignore[reportPrivateUsage]
        pooled = CmalPoolPT()._inner._model_template.finetune_strategies  # pyright: ignore[reportPrivateUsage]

        assert small == {"static_only", "last_layer", "lora"}
        assert pooled == frozenset()

    def test_train_refuses_a_finetune_block_so_a_missing_donor_cannot_cold_train(
        self,
    ) -> None:
        from random import Random
        from typing import Any, cast

        from sapphire_flow.exceptions import ConfigurationError
        from sapphire_flow.models.aquacast import CmalSmall

        with pytest.raises(ConfigurationError, match="base_artifact_id"):
            CmalSmall().train(
                cast("Any", None),
                config={"finetune": {"strategy": "static_only"}},
                rng=Random(0),
            )

    def test_the_pooled_model_permits_no_fine_tuning(self) -> None:
        from sapphire_flow.models.aquacast import CmalPoolPT

        permitted = CmalPoolPT.FINETUNE_STRATEGIES

        assert permitted == frozenset()


class TestTrainingDataLoadersRunInProcess:
    """aquacast's collate is a local closure, so worker processes fail under the
    `forkserver` start method Python 3.14 uses; the shim forces in-process loading.
    """

    def test_cmal_small_loads_training_data_in_process(self) -> None:
        from sapphire_flow.models.aquacast import CmalSmall

        template = CmalSmall()._inner._model_template  # pyright: ignore[reportPrivateUsage]

        assert template.config.data.num_workers == 0

    def test_the_pooled_model_loads_training_data_in_process(self) -> None:
        from sapphire_flow.models.aquacast import CmalPoolPT

        template = CmalPoolPT()._inner._model_template  # pyright: ignore[reportPrivateUsage]

        assert template.config.data.num_workers == 0


class TestMergedDischargeGapDeclaration:
    @pytest.mark.parametrize("model_id", ["cmal_small", "cmal_pool_pt"])
    def test_both_templates_remain_discoverable(self, model_id: str) -> None:
        from sapphire_flow.services.model_registry import discover_models

        assert model_id in discover_models()

    @pytest.mark.parametrize(
        "model_name,lookback", [("CmalSmall", 30), ("CmalPoolPT", 210)]
    )
    def test_default_gap_budget_preserves_both_daily_templates(
        self, model_name: str, lookback: int
    ) -> None:
        from sapphire_flow.models import aquacast as shims

        model = getattr(shims, model_name)()
        requirement = model.input_requirement
        assert set(requirement.dynamic) == {timedelta(days=1)}
        daily = next(iter(requirement.dynamic[timedelta(days=1)].data.values()))
        past = {
            name: variable
            for namespace in daily.past_known.values()
            for name, variable in namespace.items()
        }
        assert past["discharge"].lookback == lookback
        assert {name: variable.max_nan for name, variable in past.items()} == {
            "discharge": lookback,
            "precipitation": 0,
            "temperature": 0,
        }

    def test_in_memory_fraction_reaches_the_shim_without_changing_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dataclasses import replace

        from aquacast.operational.config import ModelTemplate
        from aquacast.operational.model import AquacastModel

        from sapphire_flow.models.aquacast import CmalSmall

        model = CmalSmall()
        original_hash = model.config_hash
        template = ModelTemplate.from_yaml(model.config_path)
        inference = replace(template.config.inference, min_past_discharge_fraction=0.2)
        configured = replace(
            template, config=replace(template.config, inference=inference)
        )
        monkeypatch.setattr(model, "_inner", AquacastModel(configured))

        daily = next(
            iter(model.input_requirement.dynamic[timedelta(days=1)].data.values())
        )
        assert {
            name: variable.max_nan
            for namespace in daily.past_known.values()
            for name, variable in namespace.items()
        } == {"discharge": 24, "precipitation": 0, "temperature": 0}
        assert model.config_hash == original_hash


class TestUpstreamRejectionReporting:
    def test_total_rejection_report_accepts_fi_utc_issue_time(self) -> None:
        from datetime import UTC, datetime

        import polars as pl
        from aquacast.domain import Resolution
        from aquacast.operational.config import ModelTemplate
        from aquacast.operational.model import (
            _issue_report,  # pyright: ignore[reportPrivateUsage]
        )

        from sapphire_flow.models.aquacast import CmalSmall

        config = ModelTemplate.from_yaml(CmalSmall().config_path).config
        issue = datetime(2026, 10, 20, tzinfo=UTC)
        predicted = {
            Resolution.DAILY: pl.DataFrame(
                {"forecast_issue_dt": [issue.replace(tzinfo=None)]}
            )
        }

        report = _issue_report(predicted, config, [issue], [issue])
        assert "available" in report


class TestCmalNepalStaticEncoder20261123:
    def test_cpu_configuration_and_known_grid_contract(self) -> None:
        from sapphire_flow.adapters.forecast_interface import ForecastInterfaceAdapter
        from sapphire_flow.models.aquacast import CmalNepalStaticEncoder20261123
        from sapphire_flow.types.time_grid import ModelTimeGrid, PeriodLabel, TimeGrid

        model = CmalNepalStaticEncoder20261123()
        assert (
            model.config_hash
            == "a35365043a62ffb75cc943dba9f72dc7daabcdcb138be879d5834cd83dc71bc3"
        )
        req = ForecastInterfaceAdapter(model).data_requirements
        assert req.time_grid == ModelTimeGrid(
            grid=TimeGrid(step=timedelta(days=1), phase=timedelta(hours=18)),
            label=PeriodLabel.START,
        )
        assert req.lookback_steps == 365
        assert req.forecast_horizon_steps == 15
        assert len(req.static_features) == 78
        spec = next(
            iter(model.input_requirement.dynamic[timedelta(days=1)].data.values())
        )
        past = {name: v for ns in spec.past_known.values() for name, v in ns.items()}
        assert past["discharge"].max_nan == 365
        assert all(
            value.max_nan == 0 for name, value in past.items() if name != "discharge"
        )
