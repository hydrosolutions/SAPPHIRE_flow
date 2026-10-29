"""Persistent observation fixture contract, separate from transient QC context."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from sapphire_flow.types.observation_read import QcContextRequirement

if TYPE_CHECKING:
    from sapphire_flow.types.observation import Observation


def persisted_observation_fields(row: Observation) -> dict[str, object]:
    fields = asdict(row)
    assert fields.pop("qc_read_token") is None
    assert fields.pop("qc_context_segment") == 0
    assert fields.pop("qc_context_requirement") is QcContextRequirement.ORDINARY
    assert set(fields) == {
        "id",
        "station_id",
        "timestamp",
        "parameter",
        "value",
        "source",
        "rating_curve_id",
        "rating_curve_correction_version",
        "qc_status",
        "qc_flags",
        "qc_rule_version",
        "created_at",
        "delivery_id",
    }
    return fields
