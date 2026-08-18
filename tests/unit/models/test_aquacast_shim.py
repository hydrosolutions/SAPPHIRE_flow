"""Plan 159 T1 — construction and discovery, against the REAL aquacast package. These
tests require the `aquacast` extra (`uv sync --extra aquacast`) and skip without it, in
the manner of the existing `live_*` markers. They are deliberately NOT written against a
fabricated entry point: Plan 157 shipped a shim test that monkeypatched
`importlib.metadata.entry_points` with an invented class, which was green whether or not
the real package existed and had to be deleted. The whole point of Plan 159 D17 —
keeping the shim in this repo — is that these can be real.

Plan 181 extends this file with the FI-surface proxy (T1), the declaration rewrite (T2)
and the boundary data translation (T3). Those tests also construct the REAL `CmalPoolPT`
(cheap: it only parses the vendored YAML, no weights load), then inject a FAKE `_inner`
so predict/train/hindcast can be exercised without a real trained artifact — Plan 181
T1 is explicit that delegation must be asserted, not `hasattr`, so a fake that RECORDS
calls is what proves the shim actually reaches its inner model.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from random import Random
from typing import Any

import forecast_interface as fi
import polars as pl
import pytest

from sapphire_flow.exceptions import ConfigurationError
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


# ---------------------------------------------------------------------------------
# Plan 181 — fixtures shared by the T1/T2/T3 sections below.
# ---------------------------------------------------------------------------------

_ISSUE = datetime(2025, 1, 1, tzinfo=UTC)
_VALID = datetime(2025, 1, 2, tzinfo=UTC)
_DAILY = timedelta(days=1)


def _target(unit: fi.Unit) -> fi.TargetSpec:
    return fi.TargetSpec(
        unit=unit, representations=frozenset({fi.OutputRepresentation.DETERMINISTIC})
    )


def _past(unit: fi.Unit, *, lookback: int = 3) -> fi.PastKnownVariable:
    return fi.PastKnownVariable(lookback=lookback, max_nan=0, unit=unit)


def _future(unit: fi.Unit, *, future_steps: int = 2) -> fi.FutureKnownVariable:
    return fi.FutureKnownVariable(future_steps=future_steps, max_nan=0, unit=unit)


def _requirement_with_precip_at(time_step: timedelta) -> fi.InputRequirement:
    """A fabricated (non-real) requirement carrying a `precipitation` future_known
    variable at `time_step`. Used ONLY to exercise the D1 daily-step guard: the real
    `cmal_pool_PT` config is daily-only, so the guard cannot be triggered against it.
    """
    return fi.InputRequirement(
        targets={"discharge": _target(fi.Unit.MM_PER_DAY)},
        dynamic={
            time_step: fi.SpatialInputSpec(
                data={
                    fi.SpatialRepresentation.BASIN_AVERAGE: fi.DynamicInputSpec(
                        past_known={
                            "aquacast": {"discharge": _past(fi.Unit.MM_PER_DAY)}
                        },
                        future_known={
                            "aquacast": {"precipitation": _future(fi.Unit.MM_PER_DAY)}
                        },
                    )
                }
            )
        },
        static={"area"},
    )


class _FakeInner:
    """Stands in for `aquacast.operational.model.AquacastModel`: exposes exactly the
    surface `AquacastShim.__init__` binds to `self._inner`, and RECORDS every call so
    delegation (not mere method-presence) can be asserted (Plan 181 T1 acceptance).
    """

    def __init__(
        self,
        *,
        input_requirement: fi.InputRequirement,
        predict_result: fi.ModelResult | None = None,
        train_result: object = None,
        hindcast_result: fi.ModelResult | None = None,
    ) -> None:
        self.input_requirement = input_requirement
        self.artifact_scope = fi.ArtifactScope.GROUP
        self.predict_calls: list[dict[str, Any]] = []
        self.train_calls: list[dict[str, Any]] = []
        self.hindcast_calls: list[dict[str, Any]] = []
        self.serialize_calls: list[object] = []
        self.deserialize_calls: list[bytes] = []
        self._predict_result = predict_result
        self._train_result = train_result
        self._hindcast_result = hindcast_result

    def predict(
        self,
        artifact: object,
        *,
        inputs: fi.ModelInputs,
        issue_datetime: object,
        rng: object,
    ) -> fi.ModelResult | None:
        self.predict_calls.append(
            {
                "artifact": artifact,
                "inputs": inputs,
                "issue_datetime": issue_datetime,
                "rng": rng,
            }
        )
        return self._predict_result

    def train(self, inputs: fi.ModelInputs, *, config: object, rng: object) -> object:
        self.train_calls.append({"inputs": inputs, "config": config, "rng": rng})
        return self._train_result

    def serialize_artifact(self, artifact: object) -> bytes:
        self.serialize_calls.append(artifact)
        return b"fake-serialized"

    def deserialize_artifact(self, raw: bytes) -> object:
        self.deserialize_calls.append(raw)
        return f"deserialized:{raw!r}"

    def hindcast(
        self,
        artifact: object,
        *,
        inputs: fi.ModelInputs,
        issue_datetimes: object,
        rng: object,
    ) -> fi.ModelResult | None:
        self.hindcast_calls.append(
            {
                "artifact": artifact,
                "inputs": inputs,
                "issue_datetimes": issue_datetimes,
                "rng": rng,
            }
        )
        return self._hindcast_result


class _FakeInnerWithoutHindcast:
    """A minimal inner WITHOUT `hindcast` — some future aquacast config might not
    provide one; the shim must fail clearly at call time rather than pretend."""

    def __init__(self, *, input_requirement: fi.InputRequirement) -> None:
        self.input_requirement = input_requirement
        self.artifact_scope = fi.ArtifactScope.GROUP

    def predict(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("not exercised in this test")

    def train(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("not exercised in this test")

    def serialize_artifact(self, artifact: object) -> bytes:
        return b""

    def deserialize_artifact(self, raw: bytes) -> object:
        return raw


def _series(name: str, value: float, unit: fi.Unit) -> fi.InputSeries:
    frame = pl.DataFrame({"datetime": [_ISSUE], name: [value]}).with_columns(
        pl.col("datetime").cast(pl.Datetime("us", "UTC"))
    )
    return fi.InputSeries(unit=unit, data=frame)


def _canonical_station_inputs(
    *,
    area_km2: float | None,
    discharge_m3_s: float,
    precip_mm: float,
    temp_c: float,
) -> fi.StationInputs:
    """A STATION's worth of inputs in SAP3's canonical vocabulary — the shape the
    shim's `input_requirement` (post-T2 rewrite) declares, and therefore the shape the
    real `ForecastInterfaceAdapter` would deliver at `predict`/`train` time."""
    dynamic_inputs = fi.DynamicInputs(
        past_known={
            "aquacast": {
                "discharge": _series("discharge", discharge_m3_s, fi.Unit.M3_PER_S),
                "precipitation": _series("precipitation", precip_mm, fi.Unit.MM),
                "temperature": _series("temperature", temp_c, fi.Unit.DEG_C),
            }
        },
        future_known={
            "aquacast": {
                "precipitation": _series("precipitation", precip_mm, fi.Unit.MM),
                "temperature": _series("temperature", temp_c, fi.Unit.DEG_C),
            }
        },
    )
    static: dict[str, int | float | str] = {}
    if area_km2 is not None:
        static["area"] = area_km2
    return fi.StationInputs(
        dynamic={
            _DAILY: fi.SpatialInputs(
                data={fi.SpatialRepresentation.BASIN_AVERAGE: dynamic_inputs}
            )
        },
        static=static,
    )


def _discharge_success(values_mm_per_day: dict[str, float]) -> fi.ModelResult:
    variables: dict[str, dict[str, fi.VariableOutput]] = {}
    for station_key, mm_per_day in values_mm_per_day.items():
        frame = pl.DataFrame(
            {
                "issue_datetime": [_ISSUE],
                "datetime": [_VALID],
                "value": [mm_per_day],
            }
        ).with_columns(
            pl.col("issue_datetime").cast(pl.Datetime("us", "UTC")),
            pl.col("datetime").cast(pl.Datetime("us", "UTC")),
        )
        variables[station_key] = {
            "discharge": fi.VariableOutput(
                metadata=fi.VariableMetadata(
                    unit=fi.Unit.MM_PER_DAY,
                    timedelta=_DAILY,
                    forecast_horizon=1,
                    offset=0,
                ),
                deterministic=fi.DeterministicData(data=frame),
                status=fi.VariableStatus.SUCCESS,
            )
        }
    return fi.ModelSuccess(
        output=fi.ModelOutput(
            model_name="cmal_pool_pt", issue_datetime=_ISSUE, variables=variables
        )
    )


def _shim_with_fake_inner(**fake_kwargs: Any) -> Any:
    from sapphire_flow.models.aquacast import CmalPoolPT

    shim = CmalPoolPT()
    native_requirement = shim._inner.input_requirement  # noqa: SLF001 -- capture BEFORE swapping
    fake_kwargs.setdefault("input_requirement", native_requirement)
    shim._inner = _FakeInner(**fake_kwargs)  # noqa: SLF001 -- injecting a fake collaborator
    return shim


class TestFISurfaceDelegation:
    """Plan 181 T1 — a `predict` call must REACH the inner model, and the other four
    FI methods must proxy through too. Asserted on delegation (a fake that RECORDS
    calls and returns a sentinel), never on `hasattr` alone.
    """

    def test_predict_reaches_the_inner_model(self) -> None:
        shim = _shim_with_fake_inner(
            predict_result=_discharge_success({"gauge-a": 1.0})
        )
        rng = Random(0)
        inputs = fi.ModelInputs(
            stations={
                "gauge-a": _canonical_station_inputs(
                    area_km2=86.4, discharge_m3_s=1.0, precip_mm=2.0, temp_c=3.0
                )
            }
        )

        shim.predict(object(), inputs=inputs, issue_datetime=_ISSUE, rng=rng)

        assert len(shim._inner.predict_calls) == 1  # noqa: SLF001
        assert shim._inner.predict_calls[0]["issue_datetime"] is _ISSUE  # noqa: SLF001
        assert shim._inner.predict_calls[0]["rng"] is rng  # noqa: SLF001

    def test_train_reaches_the_inner_model_and_returns_its_result(self) -> None:
        sentinel = object()
        shim = _shim_with_fake_inner(train_result=sentinel)
        rng = Random(0)
        inputs = fi.ModelInputs(
            stations={
                "gauge-a": _canonical_station_inputs(
                    area_km2=86.4, discharge_m3_s=1.0, precip_mm=2.0, temp_c=3.0
                )
            }
        )

        result = shim.train(inputs, config={"x": 1}, rng=rng)

        assert len(shim._inner.train_calls) == 1  # noqa: SLF001
        assert shim._inner.train_calls[0]["config"] == {"x": 1}  # noqa: SLF001
        assert result is sentinel

    def test_serialize_artifact_reaches_the_inner_model(self) -> None:
        shim = _shim_with_fake_inner()

        out = shim.serialize_artifact("artifact-x")

        assert shim._inner.serialize_calls == ["artifact-x"]  # noqa: SLF001
        assert out == b"fake-serialized"

    def test_deserialize_artifact_reaches_the_inner_model(self) -> None:
        shim = _shim_with_fake_inner()

        out = shim.deserialize_artifact(b"raw-bytes")

        assert shim._inner.deserialize_calls == [b"raw-bytes"]  # noqa: SLF001
        assert out == "deserialized:b'raw-bytes'"

    def test_hindcast_reaches_the_inner_model_when_it_provides_one(self) -> None:
        shim = _shim_with_fake_inner(
            hindcast_result=_discharge_success({"gauge-a": 1.0})
        )
        rng = Random(0)
        inputs = fi.ModelInputs(
            stations={
                "gauge-a": _canonical_station_inputs(
                    area_km2=86.4, discharge_m3_s=1.0, precip_mm=2.0, temp_c=3.0
                )
            }
        )

        shim.hindcast(object(), inputs=inputs, issue_datetimes=[_ISSUE], rng=rng)

        assert len(shim._inner.hindcast_calls) == 1  # noqa: SLF001

    def test_hindcast_raises_clearly_when_the_inner_model_has_none(self) -> None:
        from sapphire_flow.models.aquacast import CmalPoolPT

        shim = CmalPoolPT()
        native_requirement = shim._inner.input_requirement  # noqa: SLF001
        shim._inner = _FakeInnerWithoutHindcast(  # noqa: SLF001
            input_requirement=native_requirement
        )
        inputs = fi.ModelInputs(
            stations={
                "gauge-a": _canonical_station_inputs(
                    area_km2=86.4, discharge_m3_s=1.0, precip_mm=2.0, temp_c=3.0
                )
            }
        )

        with pytest.raises(ConfigurationError, match="hindcast"):
            shim.hindcast(
                object(), inputs=inputs, issue_datetimes=[_ISSUE], rng=Random(0)
            )


class TestDeclarationTranslation:
    """Plan 181 T2 — the declared names/units must be SAP3-canonical, and the rewrite
    must not otherwise alter the contract's shape.
    """

    def test_every_declared_unit_has_a_sap3_canonical_mapping(self) -> None:
        from sapphire_flow.adapters.forecast_interface import fi_unit_to_canonical
        from sapphire_flow.models.aquacast import CmalPoolPT

        req = CmalPoolPT().input_requirement

        for spec in req.targets.values():
            fi_unit_to_canonical(spec.unit)  # must not raise
        for spatial_spec in req.dynamic.values():
            for dyn in spatial_spec.data.values():
                for variables in (*dyn.past_known.values(), *dyn.future_known.values()):
                    for variable in variables.values():
                        fi_unit_to_canonical(variable.unit)  # must not raise

    def test_discharge_target_is_declared_in_m3_per_s(self) -> None:
        from sapphire_flow.models.aquacast import CmalPoolPT

        req = CmalPoolPT().input_requirement

        assert req.targets["discharge"].unit is fi.Unit.M3_PER_S

    def test_temperature_is_renamed_from_mean_temperature(self) -> None:
        from sapphire_flow.models.aquacast import CmalPoolPT

        req = CmalPoolPT().input_requirement
        [spatial_spec] = req.dynamic.values()
        [dyn] = spatial_spec.data.values()

        assert "temperature" in dyn.past_known["aquacast"]
        assert "mean_temperature" not in dyn.past_known["aquacast"]
        assert "temperature" in dyn.future_known["aquacast"]
        assert "mean_temperature" not in dyn.future_known["aquacast"]

    def test_precipitation_is_relabeled_mm_at_the_daily_step(self) -> None:
        from sapphire_flow.models.aquacast import CmalPoolPT

        req = CmalPoolPT().input_requirement
        [(time_step, spatial_spec)] = req.dynamic.items()
        assert time_step == _DAILY
        [dyn] = spatial_spec.data.values()

        assert dyn.past_known["aquacast"]["precipitation"].unit is fi.Unit.MM
        assert dyn.future_known["aquacast"]["precipitation"].unit is fi.Unit.MM

    def test_discharge_past_input_is_declared_in_m3_per_s(self) -> None:
        from sapphire_flow.models.aquacast import CmalPoolPT

        req = CmalPoolPT().input_requirement
        [spatial_spec] = req.dynamic.values()
        [dyn] = spatial_spec.data.values()

        assert dyn.past_known["aquacast"]["discharge"].unit is fi.Unit.M3_PER_S

    def test_rewrite_preserves_lookback_future_steps_max_nan_and_ensemble_mode(
        self,
    ) -> None:
        from sapphire_flow.models.aquacast import (
            AQUACAST_TO_CANONICAL_NAME,
            CmalPoolPT,
        )

        shim = CmalPoolPT()
        native = shim._inner.input_requirement  # noqa: SLF001
        rewritten = shim.input_requirement

        [(_, native_spatial)] = native.dynamic.items()
        [(_, rewritten_spatial)] = rewritten.dynamic.items()
        [native_dyn] = native_spatial.data.values()
        [rewritten_dyn] = rewritten_spatial.data.values()

        for name, native_var in native_dyn.past_known["aquacast"].items():
            canonical_name = AQUACAST_TO_CANONICAL_NAME.get(name, name)
            rewritten_var = rewritten_dyn.past_known["aquacast"][canonical_name]
            assert rewritten_var.lookback == native_var.lookback
            assert rewritten_var.max_nan == native_var.max_nan

        for name, native_var in native_dyn.future_known["aquacast"].items():
            canonical_name = AQUACAST_TO_CANONICAL_NAME.get(name, name)
            rewritten_var = rewritten_dyn.future_known["aquacast"][canonical_name]
            assert rewritten_var.future_steps == native_var.future_steps
            assert rewritten_var.max_nan == native_var.max_nan
            assert rewritten_var.ensemble_mode == native_var.ensemble_mode


class TestPrecipitationRelabelGuard:
    """Plan 181 D1: the mm/day -> mm relabel is only numerically identical at a daily
    step; a non-daily branch must raise loudly rather than silently be wrong by up to
    8x.
    """

    def test_refuses_a_non_daily_precipitation_relabel(self) -> None:
        shim = _shim_with_fake_inner(
            input_requirement=_requirement_with_precip_at(timedelta(hours=3))
        )

        with pytest.raises(ConfigurationError, match="daily"):
            _ = shim.input_requirement

    def test_the_real_daily_config_does_not_trigger_the_guard(self) -> None:
        from sapphire_flow.models.aquacast import CmalPoolPT

        assert CmalPoolPT().input_requirement is not None


class TestBoundaryTranslationData:
    """Plan 181 T3 — a NUMERIC round trip through predict, not a shape check: a
    no-op/relabel-only translation would also make `predict` "return something".
    """

    def test_inbound_translation_converts_canonical_values_to_aquacast_native(
        self,
    ) -> None:
        shim = _shim_with_fake_inner(
            predict_result=_discharge_success({"gauge-a": 2.0})
        )
        # 1.0 m3/s over 86.4 km2 is exactly 1.0 mm/day (the `_units.py` defining case).
        inputs = fi.ModelInputs(
            stations={
                "gauge-a": _canonical_station_inputs(
                    area_km2=86.4, discharge_m3_s=1.0, precip_mm=5.0, temp_c=-2.0
                )
            }
        )

        shim.predict(object(), inputs=inputs, issue_datetime=_ISSUE, rng=Random(0))

        received = shim._inner.predict_calls[0]["inputs"]  # noqa: SLF001
        [dyn] = received.stations["gauge-a"].dynamic.values()
        [spatial] = dyn.data.values()
        past = spatial.past_known["aquacast"]
        future = spatial.future_known["aquacast"]

        assert "mean_temperature" in past
        assert "temperature" not in past
        assert past["discharge"].unit is fi.Unit.MM_PER_DAY
        assert past["discharge"].data["discharge"][0] == pytest.approx(1.0)
        assert past["precipitation"].unit is fi.Unit.MM_PER_DAY
        assert past["precipitation"].data["precipitation"][0] == pytest.approx(5.0)
        assert past["mean_temperature"].data["mean_temperature"][0] == pytest.approx(
            -2.0
        )
        assert future["mean_temperature"].data["mean_temperature"][0] == pytest.approx(
            -2.0
        )
        assert future["precipitation"].data["precipitation"][0] == pytest.approx(5.0)

    def test_outbound_translation_converts_discharge_to_m3_per_s(self) -> None:
        shim = _shim_with_fake_inner(
            predict_result=_discharge_success({"gauge-a": 1.0})
        )
        inputs = fi.ModelInputs(
            stations={
                "gauge-a": _canonical_station_inputs(
                    area_km2=86.4, discharge_m3_s=0.0, precip_mm=0.0, temp_c=0.0
                )
            }
        )

        result = shim.predict(
            object(), inputs=inputs, issue_datetime=_ISSUE, rng=Random(0)
        )

        assert isinstance(result, fi.ModelSuccess)
        variable = result.output.variables["gauge-a"]["discharge"]
        assert variable.metadata.unit is fi.Unit.M3_PER_S
        assert variable.deterministic is not None
        # 1.0 mm/day over 86.4 km2 is exactly 1.0 m3/s.
        assert variable.deterministic.data["value"][0] == pytest.approx(1.0)

    def test_group_predict_converts_each_station_with_its_own_area(self) -> None:
        """Per-station, verified: a batch-wide (single-area) bug would produce the
        SAME converted number for both stations."""
        shim = _shim_with_fake_inner(
            predict_result=_discharge_success({"a": 1.0, "b": 1.0})
        )
        inputs = fi.ModelInputs(
            stations={
                "a": _canonical_station_inputs(
                    area_km2=86.4, discharge_m3_s=1.0, precip_mm=0.0, temp_c=0.0
                ),
                "b": _canonical_station_inputs(
                    area_km2=864.0, discharge_m3_s=1.0, precip_mm=0.0, temp_c=0.0
                ),
            }
        )

        result = shim.predict(
            object(), inputs=inputs, issue_datetime=_ISSUE, rng=Random(0)
        )

        received = shim._inner.predict_calls[0]["inputs"]  # noqa: SLF001

        def _received_discharge_mm(station_key: str) -> float:
            [dyn] = received.stations[station_key].dynamic.values()
            [spatial] = dyn.data.values()
            return spatial.past_known["aquacast"]["discharge"].data["discharge"][0]

        a_mm = _received_discharge_mm("a")
        b_mm = _received_discharge_mm("b")
        assert a_mm == pytest.approx(1.0)
        assert b_mm == pytest.approx(0.1)
        assert a_mm != b_mm

        assert isinstance(result, fi.ModelSuccess)
        a_m3s = result.output.variables["a"]["discharge"].deterministic.data["value"][0]
        b_m3s = result.output.variables["b"]["discharge"].deterministic.data["value"][0]
        assert a_m3s == pytest.approx(1.0)
        assert b_m3s == pytest.approx(10.0)
        assert a_m3s != b_m3s


class TestAreaMissingAtPredictTime:
    """Plan 181 D3: no fallback to a basin lookup — raise, naming the station."""

    def test_missing_area_raises_naming_the_station(self) -> None:
        shim = _shim_with_fake_inner()
        station_inputs = _canonical_station_inputs(
            area_km2=None, discharge_m3_s=1.0, precip_mm=0.0, temp_c=0.0
        )
        inputs = fi.ModelInputs(stations={"thun": station_inputs})

        with pytest.raises(ConfigurationError, match="thun"):
            shim.predict(object(), inputs=inputs, issue_datetime=_ISSUE, rng=Random(0))
