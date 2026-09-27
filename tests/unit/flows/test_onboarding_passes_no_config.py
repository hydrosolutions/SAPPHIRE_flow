"""Plan 405 T4 — the four onboarding sites still pass an EMPTY config.

🔑 399 T2 required this assertion on the training flow **and** on the onboarding
sites; only the training-flow half was written (§ 8), which is the divergence the
bullet existed to prevent. This file is the other half.

⚠️ **Two of the four sites pass the config POSITIONALLY.**
`services/model_onboarding.py` calls `train_*_model(model, training_data, {}, rng)`,
while `flows/onboard_model.py` uses `params={}`. ⛔ A recorder that only inspected
`kwargs["params"]` would silently pass on half the sites while appearing to cover
all four — which is why each recorder below captures BOTH.

⛔ Every assertion here pairs the value with a CALL COUNT. "Every captured call
passed `{}`" is vacuously true of zero captured calls, and that vacuous shape is
exactly what T4's Verification forbids.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sapphire_flow.flows import onboard_model as onboard_flow
from sapphire_flow.types.ids import StationGroupId
from tests.conftest import make_training_unit

if TYPE_CHECKING:
    import pytest

    from sapphire_flow.types.training import TrainingUnit

_RNG = random.Random(0)


class _ConfigRecorder:
    """Captures the config each site passes, positionally OR by keyword."""

    def __init__(self) -> None:
        self.calls: list[object] = []

    def __call__(self, *args: Any, **kwargs: Any) -> bytes:
        if "params" in kwargs:
            self.calls.append(kwargs["params"])
        else:
            # 🔴 The positional form: train_*_model(model, data, params, rng).
            self.calls.append(args[2])
        return b"artifact"


def _station_unit() -> TrainingUnit:
    return make_training_unit()


def _group_unit() -> TrainingUnit:
    return make_training_unit(group_id=StationGroupId(UUID(int=7, version=4)))


class TestOnboardingFlowSites:
    """`flows/onboard_model.py` — the two `params={}` keyword sites."""

    def test_the_station_site_passes_an_empty_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _ConfigRecorder()
        monkeypatch.setattr(onboard_flow, "train_station_model", recorder)

        onboard_flow._train_onboarding_model_task.fn(  # noqa: SLF001
            _station_unit(), object(), object(), _RNG
        )

        assert len(recorder.calls) == 1, "the station site was never reached"
        assert recorder.calls[0] == {}

    def test_the_group_site_passes_an_empty_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _ConfigRecorder()
        monkeypatch.setattr(onboard_flow, "train_group_model", recorder)

        onboard_flow._train_onboarding_model_task.fn(  # noqa: SLF001
            _group_unit(), object(), object(), _RNG
        )

        assert len(recorder.calls) == 1, "the group site was never reached"
        assert recorder.calls[0] == {}
