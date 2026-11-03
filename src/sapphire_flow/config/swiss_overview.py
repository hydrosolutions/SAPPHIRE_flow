from __future__ import annotations

import hashlib
from importlib.resources import files
from uuid import UUID  # noqa: TC003 -- Pydantic resolves annotations at runtime.

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from sapphire_flow.types.ids import StationId
from sapphire_flow.types.swiss_overview import SwissRosterEntry


class SwissOverviewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    roster_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    roster_count: int = Field(ge=1, le=200)


class _RosterEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    station_id: UUID
    network: str = Field(min_length=1)
    code: str = Field(min_length=1)


def load_swiss_roster(config: SwissOverviewConfig) -> tuple[SwissRosterEntry, ...]:
    raw = (
        files("sapphire_flow.config").joinpath("swiss_display_roster.json").read_bytes()
    )
    if hashlib.sha256(raw).hexdigest() != config.roster_sha256:
        raise ValueError("Swiss roster digest mismatch")
    parsed = TypeAdapter(list[_RosterEntry]).validate_json(raw)
    if len(parsed) != config.roster_count:
        raise ValueError("Swiss roster cardinality mismatch")
    if len({row.station_id for row in parsed}) != len(parsed) or len(
        {row.code for row in parsed}
    ) != len(parsed):
        raise ValueError("Swiss roster contains duplicates")
    return tuple(
        SwissRosterEntry(
            station_id=StationId(row.station_id), network=row.network, code=row.code
        )
        for row in parsed
    )
