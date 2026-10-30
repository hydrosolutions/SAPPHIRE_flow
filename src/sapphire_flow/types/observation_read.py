from dataclasses import dataclass
from enum import Enum

from sapphire_flow.types.datetime import UtcDatetime
from sapphire_flow.types.enums import ObservationSource
from sapphire_flow.types.ids import StationId


class ObservationReadPurpose(Enum):
    TRUSTED = "trusted"
    QC_CONTEXT = "qc_context"
    SOURCE_AUDIT = "source_audit"


class QcContextRequirement(Enum):
    ORDINARY = "ordinary"
    UNIT_GOVERNED = "unit_governed"


@dataclass(frozen=True, kw_only=True, slots=True)
class QcContextWindow:
    station_id: StationId
    parameter: str
    start: UtcDatetime
    end: UtcDatetime
    source: ObservationSource | None = None

    def __post_init__(self) -> None:
        if self.start >= self.end or not self.parameter.strip():
            raise ValueError("QC context requires a nonempty bounded window")


@dataclass(frozen=True, kw_only=True, slots=True)
class QcReadToken:
    state_sha256: str
    context_sha256: str
    window: QcContextWindow | None = None
    qc_state_sha256: str | None = None
    cohort_size: int | None = None

    def __post_init__(self) -> None:
        if self.cohort_size is not None and self.cohort_size < 1:
            raise ValueError("QC cohort size must be positive")
        digests = (self.state_sha256, self.context_sha256)
        if self.qc_state_sha256 is not None:
            digests += (self.qc_state_sha256,)
        for digest in digests:
            if len(digest) != 64 or any(
                char not in "0123456789abcdef" for char in digest
            ):
                raise ValueError("QC read token requires SHA-256 digests")
