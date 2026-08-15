"""Plan 159 T0d — the interim horizon opt-in.

The property that matters is not "the opt-in works" but **that it is bounded**: strict
by default, a floor rather than unbounded truncation, and superseded automatically the
moment a model declares its own semantics. Those are the assertions here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sapphire_flow.services.horizon_semantics import resolve_required_steps
from sapphire_flow.types.ids import ModelId

_MODEL = ModelId("some_model")


def _model(*, semantics: str | None = None, min_steps: int | None = None) -> object:
    """A stand-in for an FI model. `semantics=None` reproduces FI v0.1.19, where
    `horizon_semantics` does not exist as a field at all."""
    variable = SimpleNamespace(future_steps=15)
    if semantics is not None:
        variable.horizon_semantics = SimpleNamespace(value=semantics)
        variable.min_future_steps = min_steps
    spec = SimpleNamespace(future_known={"src": {"precipitation": variable}})
    spatial = SimpleNamespace(data={"basin_average": spec})
    return SimpleNamespace(input_requirement=SimpleNamespace(dynamic={"P1D": spatial}))


class TestStrictByDefault:
    """No model changes behaviour without an explicit entry — the whole safety case."""

    def test_a_model_with_no_declaration_and_no_opt_in_stays_strict(self) -> None:
        got = resolve_required_steps(_model(), _MODEL, 15, opt_in={})

        assert got.steps == 15
        assert got.source == "declared"
        assert not got.is_truncated

    def test_an_opt_in_for_a_different_model_does_not_leak(self) -> None:
        got = resolve_required_steps(
            _model(), _MODEL, 15, opt_in={ModelId("other_model"): 5}
        )

        assert got.steps == 15
        assert got.source == "declared"


class TestProviderOptIn:
    def test_an_opted_in_model_requires_only_its_floor(self) -> None:
        got = resolve_required_steps(_model(), _MODEL, 15, opt_in={_MODEL: 5})

        assert got.steps == 5
        assert got.source == "provider_opt_in"
        assert got.is_truncated

    def test_the_floor_never_exceeds_the_declared_horizon(self) -> None:
        """A floor above the declaration would silently DEMAND more than the model
        asks for, refusing runs that should succeed."""
        got = resolve_required_steps(_model(), _MODEL, 3, opt_in={_MODEL: 5})

        assert got.steps == 3
        assert not got.is_truncated

    def test_truncation_is_reported_not_silent(self) -> None:
        """A short-horizon forecast is not equivalent to a full one, so the result
        must let a caller tell them apart."""
        full = resolve_required_steps(_model(), _MODEL, 15, opt_in={})
        short = resolve_required_steps(_model(), _MODEL, 15, opt_in={_MODEL: 5})

        assert (full.is_truncated, short.is_truncated) == (False, True)
        assert short.declared_steps == 15


class TestTheModelsOwnDeclarationWins:
    """The self-retiring property: once a model declares AT_MOST, the interim table is
    never consulted, so it goes stale on its own rather than needing a migration."""

    def test_at_most_supersedes_the_provider_opt_in(self) -> None:
        got = resolve_required_steps(
            _model(semantics="at_most", min_steps=7),
            _MODEL,
            15,
            opt_in={_MODEL: 5},  # would say 5; the model says 7 and must win
        )

        assert got.steps == 7
        assert got.source == "model_at_most"

    def test_an_exact_declaration_stays_strict_even_with_an_opt_in(self) -> None:
        """EXACT is a model asserting it genuinely needs its full horizon. A provider
        opt-in must NOT override that — doing so is the silent-wrongness this design
        exists to prevent."""
        got = resolve_required_steps(
            _model(semantics="exact"), _MODEL, 15, opt_in={_MODEL: 5}
        )

        assert got.steps == 15
        assert got.source == "declared"


class TestReadingTheDeclarationIsDefensive:
    """This runs inside the forecast cycle, where an exception takes down the whole
    group. Anything unreadable must mean "not declared", never a crash."""

    @pytest.mark.parametrize(
        "model",
        [
            SimpleNamespace(),  # no input_requirement at all
            SimpleNamespace(input_requirement=None),
            SimpleNamespace(input_requirement=SimpleNamespace(dynamic=None)),
            SimpleNamespace(input_requirement=SimpleNamespace(dynamic={"P1D": None})),
        ],
        ids=["no-requirement", "none", "dynamic-none", "spatial-none"],
    )
    def test_unreadable_requirements_fall_back_to_strict(self, model: object) -> None:
        got = resolve_required_steps(model, _MODEL, 15, opt_in={})

        assert got.steps == 15
        assert got.source == "declared"
