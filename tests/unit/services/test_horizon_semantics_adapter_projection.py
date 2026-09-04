"""Plan 241 T2 — the model's own horizon declaration must survive the FI adapter.

RED-FIRST NOTE. These tests assert BEHAVIOUR (`resolve_required_steps`'s `source`),
not the presence of a symbol. Against the pre-change code the adapter drops
`horizon_semantics`, so `_model_declared_floor` returns `None` and resolution falls
through to the strict default — `test_at_most_declaration_survives_the_adapter` fails
with `source == "declared"` where `"model_at_most"` is expected. That is the
propagation failure itself, not an AttributeError from a missing field: the plan
requires the red to prove the fault, and an import-time error would prove only that a
name changed.
"""

from __future__ import annotations

from datetime import timedelta

import forecast_interface as fi

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.services.horizon_semantics import resolve_required_steps
from sapphire_flow.types.ids import ModelId

_DAILY = timedelta(days=1)
_MODEL_ID = ModelId("test_model")


def _future(
    *,
    future_steps: int = 15,
    semantics: object | None = None,
    min_future_steps: int | None = None,
) -> fi.FutureKnownVariable:
    kwargs: dict[str, object] = {
        "future_steps": future_steps,
        "max_nan": 0,
        "unit": fi.Unit.MM_PER_DAY,
    }
    if semantics is not None:
        kwargs["horizon_semantics"] = semantics
        kwargs["min_future_steps"] = min_future_steps
    return fi.FutureKnownVariable(**kwargs)  # type: ignore[arg-type]


def _requirement(future: dict[str, fi.FutureKnownVariable]) -> fi.InputRequirement:
    return fi.InputRequirement(
        targets={
            "discharge": fi.TargetSpec(
                unit=fi.Unit.MM_PER_DAY,
                representations=frozenset({fi.OutputRepresentation.DETERMINISTIC}),
            )
        },
        dynamic={
            _DAILY: fi.SpatialInputSpec(
                data={
                    fi.SpatialRepresentation.BASIN_AVERAGE: fi.DynamicInputSpec(
                        past_known={
                            "src": {
                                "discharge": fi.PastKnownVariable(
                                    lookback=3, max_nan=0, unit=fi.Unit.MM_PER_DAY
                                )
                            }
                        },
                        future_known={"src": future},
                    )
                }
            )
        },
        static={"area"},
    )


class _FakeFiModel:
    """Minimal FI-shaped model: only what the adapter reads to project."""

    def __init__(self, requirement: fi.InputRequirement) -> None:
        self.input_requirement = requirement
        self.artifact_scope = fi.ArtifactScope.STATION

    @property
    def model_id(self) -> str:
        return str(_MODEL_ID)


def _adapter(future: dict[str, fi.FutureKnownVariable]) -> object:
    return fi_boundary.ForecastInterfaceAdapter(_FakeFiModel(_requirement(future)))


class TestHorizonDeclarationSurvivesTheAdapter:
    def test_at_most_declaration_survives_the_adapter(self) -> None:
        adapter = _adapter(
            {
                "precipitation": _future(
                    semantics=fi.HorizonSemantics.AT_MOST, min_future_steps=1
                )
            }
        )
        resolved = resolve_required_steps(adapter, _MODEL_ID, 15)
        assert resolved.source == "model_at_most"
        assert resolved.steps == 1
        assert resolved.declared_steps == 15

    def test_exact_declaration_survives_and_stays_strict(self) -> None:
        adapter = _adapter(
            {"precipitation": _future(semantics=fi.HorizonSemantics.EXACT)}
        )
        resolved = resolve_required_steps(adapter, _MODEL_ID, 15)
        assert resolved.source == "declared"
        assert resolved.steps == 15

    def test_one_exact_variable_makes_the_whole_model_strict(self) -> None:
        # An explicit "I need my full horizon" must not be overridden by another
        # variable's tolerance, and must not consult the provider opt-in.
        adapter = _adapter(
            {
                "precipitation": _future(
                    semantics=fi.HorizonSemantics.AT_MOST, min_future_steps=1
                ),
                "mean_temperature": _future(semantics=fi.HorizonSemantics.EXACT),
            }
        )
        resolved = resolve_required_steps(adapter, _MODEL_ID, 15, opt_in={_MODEL_ID: 5})
        assert resolved.source == "declared"
        assert resolved.steps == 15

    def test_binding_floor_is_the_largest_declared(self) -> None:
        # Satisfying the LEAST tolerant variable satisfies the rest.
        adapter = _adapter(
            {
                "precipitation": _future(
                    semantics=fi.HorizonSemantics.AT_MOST, min_future_steps=1
                ),
                "mean_temperature": _future(
                    semantics=fi.HorizonSemantics.AT_MOST, min_future_steps=4
                ),
            }
        )
        resolved = resolve_required_steps(adapter, _MODEL_ID, 15)
        assert resolved.source == "model_at_most"
        assert resolved.steps == 4

    def test_undeclared_model_still_falls_through_to_the_provider_opt_in(self) -> None:
        # The interim table must keep working for a model that declares nothing —
        # this is what stops Plan 241 T3 from breaking cmal_pool_pt.
        adapter = _adapter({"precipitation": _future()})
        resolved = resolve_required_steps(adapter, _MODEL_ID, 15, opt_in={_MODEL_ID: 5})
        assert resolved.source == "provider_opt_in"
        assert resolved.steps == 5

    def test_fi_default_exact_is_not_treated_as_a_declaration(self) -> None:
        """🔴 The landmine this projection had to dodge.

        FI >= 0.1.20 DEFAULTS `horizon_semantics` to EXACT, so a model that says
        nothing still reports EXACT. Reading the value alone would make every model
        look explicitly strict — the provider opt-in would never be consulted again
        and `cmal_pool_pt` would fall from its 5-step floor to its declared ~15,
        breaking a model that works today. Only `model_fields_set` distinguishes
        "declared EXACT" from "did not declare".
        """
        undeclared = _future()
        assert undeclared.horizon_semantics is fi.HorizonSemantics.EXACT
        assert "horizon_semantics" not in undeclared.model_fields_set

        adapter = _adapter({"precipitation": undeclared})
        # Projected as "no declaration", NOT as EXACT.
        assert adapter.data_requirements.declared_horizon_semantics is None  # type: ignore[attr-defined]
        # ...so the interim opt-in is still reachable.
        resolved = resolve_required_steps(adapter, _MODEL_ID, 15, opt_in={_MODEL_ID: 5})
        assert resolved.source == "provider_opt_in"

    def test_undeclared_model_with_no_opt_in_stays_strict(self) -> None:
        adapter = _adapter({"precipitation": _future()})
        resolved = resolve_required_steps(adapter, _MODEL_ID, 15, opt_in={})
        assert resolved.source == "declared"
        assert resolved.steps == 15
