from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date

    from sapphire_flow.adapters.dhm_files import RatingTableFile


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingDeliveryFile:
    delivery_id: str
    delivered_on: date
    network: str
    station_code: str
    original: bytes
    sha256: str
    table: RatingTableFile

    def __post_init__(self) -> None:
        if not self.delivery_id.strip() or self.network != "dhm":
            raise ValueError("rating delivery requires DHM identity")
        if hashlib.sha256(self.original).hexdigest() != self.sha256:
            raise ValueError("rating delivery original hash mismatch")
        if self.table.station_code != self.station_code:
            raise ValueError("rating delivery station header mismatch")


def select_rating_deliveries(
    files: tuple[RatingDeliveryFile, ...],
) -> tuple[RatingDeliveryFile, ...]:
    selected: dict[tuple[str, str], RatingDeliveryFile] = {}
    identities: dict[tuple[str, str, str], RatingDeliveryFile] = {}
    dates: dict[str, date] = {}
    for item in files:
        if dates.setdefault(item.delivery_id, item.delivered_on) != item.delivered_on:
            raise ValueError("conflicting delivery date")
        identity = (item.delivery_id, item.network, item.station_code)
        previous = identities.setdefault(identity, item)
        if previous.sha256 != item.sha256:
            raise ValueError("conflicting rating file within delivery")
        key = (item.network, item.station_code)
        current = selected.get(key)
        if (
            current is not None
            and current.delivered_on == item.delivered_on
            and current.delivery_id != item.delivery_id
        ):
            raise ValueError("ambiguous rating deliveries on same date")
        if current is None or item.delivered_on > current.delivered_on:
            selected[key] = item
    return tuple(selected[key] for key in sorted(selected))
