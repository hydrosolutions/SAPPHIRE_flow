from __future__ import annotations

import math
from typing import TYPE_CHECKING

from sapphire_flow.types.nepal_demo import DemoScenario

if TYPE_CHECKING:
    from random import Random

    from sapphire_flow.types.datetime import UtcDatetime


def build_scenario(issued_at: UtcDatetime, rng: Random) -> DemoScenario:
    history = tuple(
        None
        if 120 <= i < 126
        else round(110 + 0.2 * i + 5 * math.sin(i / 9) + rng.uniform(-1, 1), 3)
        for i in range(168)
    )
    median = tuple(
        round(145 + 250 * math.exp(-(((h - 28) / 13) ** 2)) + rng.uniform(-1, 1), 3)
        for h in range(1, 73)
    )
    outturn = tuple(
        None
        if h in (41, 42)
        else round(
            140 + 190 * math.exp(-(((h - 35) / 12) ** 2)) + rng.uniform(-1, 1), 3
        )
        for h in range(1, 73)
    )
    return DemoScenario(
        issued_at=issued_at,
        history=history,
        lower=tuple(round(v * 0.78, 3) for v in median),
        median=median,
        upper=tuple(round(v * 1.27, 3) for v in median),
        outturn=outturn,
    )
