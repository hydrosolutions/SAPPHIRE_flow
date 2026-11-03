from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import TYPE_CHECKING

from sapphire_flow.types.rating_reference import canonical_content

if TYPE_CHECKING:
    from sapphire_flow.types.native_capture import Sha256Digest
    from sapphire_flow.types.rating_derived import NativeFeedCustody


class RatingEvidenceMode(Enum):
    LEGACY_CUSTODY = "legacy_custody"
    NATIVE_CAPTURE = "native_capture"


@dataclass(frozen=True, kw_only=True, slots=True)
class ApprovedLegacyRatingSegment:
    feed: NativeFeedCustody
    feed_content: str
    feed_sha256: Sha256Digest
    reference_sha256: Sha256Digest
    fingerprints: frozenset[Sha256Digest]

    def __post_init__(self) -> None:
        from sapphire_flow.types.native_capture import Sha256Digest

        if (
            canonical_content(json.loads(self.feed_content)) != self.feed_content
            or canonical_content(asdict(self.feed)) != self.feed_content
            or Sha256Digest.of(self.feed_content) != self.feed_sha256
            or not self.fingerprints
        ):
            raise ValueError("legacy segment requires exact approved custody evidence")
