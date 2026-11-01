from __future__ import annotations

import json
from datetime import date  # noqa: TC003
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from sapphire_flow.adapters.dhm_files import parse_rating_tables
from sapphire_flow.types.rating_delivery import (
    RatingDeliveryFile,
    select_rating_deliveries,
)


class _File(BaseModel):
    model_config = ConfigDict(extra="forbid")
    station_code: str = Field(min_length=1)
    network: Literal["dhm"]
    path: str = Field(min_length=1)
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class _Delivery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    delivery_id: str = Field(min_length=1)
    delivered_on: date
    files: list[_File] = Field(min_length=1, max_length=100)


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deliveries: list[_Delivery] = Field(min_length=1, max_length=20)


def load_rating_manifest(path: Path) -> tuple[RatingDeliveryFile, ...]:
    manifest = _Manifest.model_validate(json.loads(path.read_text()))
    result: list[RatingDeliveryFile] = []
    for delivery in manifest.deliveries:
        for item in delivery.files:
            location = path.parent / item.path
            if location.stat().st_size > 10_000_000:
                raise ValueError("rating file exceeds bounded admission size")
            original = location.read_bytes()
            result.append(
                RatingDeliveryFile(
                    delivery_id=delivery.delivery_id,
                    delivered_on=delivery.delivered_on,
                    network=item.network,
                    station_code=item.station_code,
                    sha256=item.sha256,
                    original=original,
                    table=parse_rating_tables(original.decode("utf-8-sig")),
                )
            )
    files = tuple(result)
    select_rating_deliveries(files)
    return files


if TYPE_CHECKING:
    from pathlib import Path
