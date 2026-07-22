"""Domain types for the BAFU LINDAS observation archive collector (Plan 136).

EVALUATION-ONLY, quarantined archive — see
``src/sapphire_flow/adapters/bafu_observation.py`` and
``src/sapphire_flow/flows/collect_bafu_observations.py`` for the safeguards.
These types are never written to the operational DB and never referenced by
a ``StationId`` — the archive is keyed on the raw BAFU gauge code + the
LINDAS-observed river/lake kind (see DC-2/DC-3 in the plan), not on any
onboarded station identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime

# The LINDAS parameter set is fixed and small (mirrors
# adapters/hydro_scraper.py's _PARAM_MAP target values). The T2 parser is the
# sole producer of this type and only ever emits these three values — a
# Literal cannot be enforced at dataclass-construction runtime, but it is
# checked statically (pyright) and documents the contract at the boundary.
BafuObservationParameter = Literal["discharge", "water_level", "water_temperature"]

# The URI-path segment (/river/observation/... vs /lake/observation/...) the
# subject-grouping parser (T2, DC-3) discriminates on. Independent of any
# onboarding StationKind classification — LINDAS is ground truth here.
LindasKind = Literal["river", "lake"]


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuObservationRow:
    gauge_code: str
    lindas_kind: LindasKind
    parameter: BafuObservationParameter
    value: float
    measurement_time: UtcDatetime
