from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId


@dataclass(frozen=True, kw_only=True, slots=True)
class SwissRosterEntry:
    station_id: StationId
    network: str
    code: str


class HeadlineAbsence(Enum):
    PENDING_CURRENT_ISSUE = "pending_current_issue"
    NO_CURRENT_CANDIDATE = "no_current_candidate"
    AUTHORITY_INTEGRITY_ERROR = "authority_integrity_error"
    EXPIRED_HORIZON = "expired_horizon"
    MISSING_ARCHIVE = "missing_archive"
    INVALID_SOURCE_DATA = "invalid_source_data"
    SOURCE_READ_FAILED = "source_read_failed"
    NOT_ONBOARDED = "not_onboarded"


@dataclass(frozen=True, kw_only=True, slots=True)
class MedianPeak:
    value: float
    valid_time: UtcDatetime
    horizon_start: UtcDatetime
    horizon_end: UtcDatetime
