from __future__ import annotations

import random
from uuid import UUID

import pytest

from sapphire_flow.flows.compute_skills import compute_skills_flow
from sapphire_flow.types.ids import ArtifactId, ModelId, StationId

_RNG = random.Random(99)


def _uuid() -> UUID:
    return UUID(int=_RNG.getrandbits(128), version=4)


class TestNonDischargeGuard:
    def test_non_discharge_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError, match="SkillScore.parameter"):
            compute_skills_flow(
                station_id=StationId(_uuid()),
                model_id=ModelId("test"),
                artifact_id=ArtifactId(_uuid()),
                parameter="water_level",
            )
