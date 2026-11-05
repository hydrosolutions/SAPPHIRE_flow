"""Parse station uploads without guessing identifiers or coordinates."""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import asdict, dataclass
from typing import Any

import fastexcel
import polars as pl
from pydantic import BaseModel, ConfigDict, Field, StrictStr


@dataclass(frozen=True, kw_only=True, slots=True)
class Coordinate:
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not (-90 <= self.latitude <= 90 and -180 <= self.longitude <= 180):
            raise ValueError("WGS84 latitude/longitude out of range")


@dataclass(frozen=True, kw_only=True, slots=True)
class StationInput:
    code: str
    display_name: str
    original: Coordinate
    source: str
    published_area_km2: float | None = None
    extraction_point: Coordinate | None = None
    adjustment_reason: str = ""

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.display_name.strip():
            raise ValueError("station code and display_name are required")
        if self.code != self.code.strip() or self.code.startswith("-"):
            raise ValueError("station code has ambiguous whitespace or leading dash")
        if any(ord(c) < 32 for c in self.code + self.display_name):
            raise ValueError("station identity contains control characters")
        if self.published_area_km2 is not None and (
            not math.isfinite(self.published_area_km2) or self.published_area_km2 <= 0
        ):
            raise ValueError("published_area_km2 must be positive and finite")
        if self.extraction_point is not None and not self.adjustment_reason.strip():
            raise ValueError("an extraction-point adjustment requires a reason")

    @property
    def point(self) -> Coordinate:
        return self.extraction_point or self.original


class InventoryRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    station_code: StrictStr = Field(min_length=1, max_length=100)
    display_name: StrictStr = Field(min_length=1, max_length=200)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    published_area_km2: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    source: StrictStr = Field(default="", max_length=2000)

    def station(self) -> StationInput:
        return StationInput(
            code=self.station_code,
            display_name=self.display_name,
            original=Coordinate(latitude=self.latitude, longitude=self.longitude),
            source=self.source,
            published_area_km2=self.published_area_km2,
        )


def validate_inventory(stations: tuple[StationInput, ...]) -> None:
    if not stations or len(stations) > 1000:
        raise ValueError("inventory must contain 1 to 1000 stations")
    codes = [s.code for s in stations]
    names = [re.sub(r"[^a-z0-9]+", "_", code.lower()) for code in codes]
    if len(set(codes)) != len(codes) or len(set(names)) != len(names):
        raise ValueError("duplicate station codes or normalized feature-name collision")


def parse_inventory(content: bytes, suffix: str) -> tuple[StationInput, ...]:
    if len(content) > 5 * 1024 * 1024:
        raise ValueError("upload exceeds 5 MiB")
    if suffix == ".csv":
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        columns = reader.fieldnames or []
        if len(columns) != len(set(columns)):
            raise ValueError("duplicate column names")
        rows: list[dict[str, Any]] = list(reader)
    elif suffix == ".xlsx":
        # No string override: numeric Excel identifiers must be corrected in Excel.
        workbook = fastexcel.read_excel(content)
        if len(workbook.sheet_names) != 1:
            raise ValueError("Excel upload must contain exactly one station sheet")
        try:
            frame = workbook.load_sheet(  # pyright: ignore[reportUnknownMemberType]
                0,
                dtype_coercion="strict",
                schema_sample_rows=None,
            ).to_polars()
        except fastexcel.FastExcelError as exc:
            raise ValueError(
                "ambiguous Excel cells; station codes must all be text"
            ) from exc
        if frame.schema.get("station_code") != pl.String:
            raise ValueError("Excel station_code cells must be text, not numbers")
        rows = frame.to_dicts()
    else:
        raise ValueError("use UTF-8 CSV or .xlsx Excel")
    stations = tuple(
        InventoryRow.model_validate(
            {
                k: (
                    None
                    if k == "published_area_km2" and v in ("", None)
                    else ""
                    if k == "source" and v is None
                    else v
                )
                for k, v in row.items()
            }
        ).station()
        for row in rows
    )
    validate_inventory(stations)
    return stations


def inventory_json(stations: tuple[StationInput, ...]) -> list[dict[str, Any]]:
    return [asdict(s) for s in stations]


def restore_inventory(rows: list[dict[str, Any]]) -> tuple[StationInput, ...]:
    stations = tuple(
        StationInput(
            code=row["code"],
            display_name=row["display_name"],
            original=Coordinate(**row["original"]),
            source=row["source"],
            published_area_km2=row["published_area_km2"],
            extraction_point=(
                Coordinate(**row["extraction_point"])
                if row["extraction_point"]
                else None
            ),
            adjustment_reason=row["adjustment_reason"],
        )
        for row in rows
    )
    validate_inventory(stations)
    return stations
