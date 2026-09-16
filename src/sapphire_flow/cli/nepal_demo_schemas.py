from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from shapely.geometry import Point, shape

from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.nepal_demo import (
    FORECAST_OFFSETS,
    OBSERVATION_GAPS,
    OBSERVATION_OFFSETS,
    DemoIssue,
    DemoScenario,
)

BANNER = "Illustrative scenario — synthetic data, not an operational forecast"


def parse_utc_instant(value: str) -> UtcDatetime:
    return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _calendar_time(value: str) -> str:
    parse_utc_instant(value)
    return value


Instant = Annotated[
    str,
    StringConstraints(pattern=r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$"),
    AfterValidator(_calendar_time),
]
Discharge = Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]
Nonempty = Annotated[str, Field(min_length=1)]
Position = Annotated[
    list[Annotated[float, Field(allow_inf_nan=False, strict=True)]],
    Field(min_length=2, max_length=2),
]
Ring = Annotated[list[Position], Field(min_length=4)]
Polygon = Annotated[list[Ring], Field(min_length=1)]


class Boundary(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Identity(Boundary):
    network: Literal["demo"]
    code: Literal["DEMO-NP-001"]
    display_name: Literal["Illustrative demo gauge"]
    longitude: Annotated[float, Field(ge=86.668726, le=86.668726)]
    latitude: Annotated[float, Field(ge=27.269326, le=27.269326)]
    backend_uuid: None


class SourceNote(Boundary):
    kind: Literal["real_geometry", "third_party_raster", "synthetic"]
    attribution: Nonempty


class Provenance(Boundary):
    basin: SourceNote
    basemap: SourceNote
    station: SourceNote
    observations: SourceNote
    forecast: SourceNote

    @model_validator(mode="after")
    def source_kinds(self) -> Self:
        if (
            self.basin.kind,
            self.basemap.kind,
            self.station.kind,
            self.observations.kind,
            self.forecast.kind,
        ) != (
            "real_geometry",
            "third_party_raster",
            "synthetic",
            "synthetic",
            "synthetic",
        ):
            raise ValueError(
                "provenance must distinguish real geometry from synthetic data"
            )
        return self


class ForecastCycle(Boundary):
    cycle_hours: Literal[6]
    cadence_seconds: Literal[10800]
    horizon_steps: Literal[24]
    issue_count: Literal[8]
    representation: Literal["quantiles"]
    quantile_levels: Annotated[list[Discharge], Field(min_length=3, max_length=3)]
    starts_at_issue_time: Literal[False]

    @model_validator(mode="after")
    def levels(self) -> Self:
        if self.quantile_levels != [0.25, 0.5, 0.75]:
            raise ValueError("quantile levels must be 0.25/0.5/0.75")
        return self


class Units(Boundary):
    discharge: Literal["m3/s"]


class Supersession(Boundary):
    cycle_hours: Literal[6]
    label: Literal["Eight illustrative forecast issues"]
    note: Nonempty


class Manifest(Boundary):
    schema_version: Literal["flow-map-region-bundle/v2"]
    region: Literal["nepal"]
    generated_at: Instant
    source_mode: Literal["illustrative"]
    generator_seed: Literal[20260916]
    banner_text: Literal[
        "Illustrative scenario — synthetic data, not an operational forecast"
    ]
    provenance: Provenance
    uncertainty_meaning: Literal["illustrative_spread"]
    spread_label: Literal["Illustrative spread — not calibrated uncertainty"]
    station: Identity
    units: Units
    timezone: Literal["Asia/Kathmandu"]
    forecast_cycle: ForecastCycle
    thresholds: None
    threshold_basis: Literal["none_available"]
    comparator: None
    date_basis: Literal["demonstration_date"]
    date_label: Nonempty
    verification_note: Nonempty
    supersession: Supersession


class Gap(Boundary):
    start: Instant
    end: Instant


class ValueSeries(Boundary):
    source_mode: Literal["illustrative"]
    unit: Literal["m3/s"]
    valid_times: list[Instant]
    values: list[Discharge | None]
    gaps: list[Gap]


class History(ValueSeries):
    valid_times: Annotated[list[Instant], Field(min_length=211, max_length=211)]
    values: Annotated[list[Discharge | None], Field(min_length=211, max_length=211)]
    gaps: Annotated[list[Gap], Field(min_length=2, max_length=2)]
    window_start: Instant
    window_end: Instant
    cadence_seconds: Literal[3600]


class Quantiles(Boundary):
    lower: list[Discharge] = Field(alias="0.25", min_length=24, max_length=24)
    median: list[Discharge] = Field(alias="0.5", min_length=24, max_length=24)
    upper: list[Discharge] = Field(alias="0.75", min_length=24, max_length=24)


class ForecastSeries(Boundary):
    source_mode: Literal["illustrative"]
    unit: Literal["m3/s"]
    forecast_id: Nonempty
    issued_at: Instant
    valid_times: Annotated[list[Instant], Field(min_length=24, max_length=24)]
    horizon_start: Instant
    horizon_end: Instant
    series: Quantiles
    gaps: Annotated[list[Gap], Field(max_length=0)]
    qc_status: Literal["synthetic_eligible"]
    eligibility_note: Nonempty

    @model_validator(mode="after")
    def consistent_issue(self) -> Self:
        issue = parse_utc_instant(self.issued_at)
        expected = [stamp(issue + timedelta(hours=h)) for h in FORECAST_OFFSETS]
        if self.valid_times != expected:
            raise ValueError(
                "forecast valid times must be +3..+72 hours at 3-hour cadence"
            )
        if (self.horizon_start, self.horizon_end) != (
            expected[0],
            stamp(issue + timedelta(hours=75)),
        ):
            raise ValueError("forecast horizon must be half-open")
        if self.forecast_id != f"DEMO-NP-001-{issue:%Y%m%dT%H%M%SZ}":
            raise ValueError("forecast identity must match its issue time")
        self.to_domain()
        return self

    def to_domain(self) -> DemoIssue:
        return DemoIssue(
            issued_at=parse_utc_instant(self.issued_at),
            lower=tuple(self.series.lower),
            median=tuple(self.series.median),
            upper=tuple(self.series.upper),
        )


class Series(Boundary):
    region: Literal["nepal"]
    observations: History
    forecasts: Annotated[list[ForecastSeries], Field(min_length=8, max_length=8)]


class BasinProperties(Boundary):
    basin_id: Literal["NP_1_00092"]
    name: Literal["Dudh Koshi at Rabuwa"]
    source: Nonempty


class BasinGeometry(Boundary):
    type: Literal["MultiPolygon"]
    coordinates: Annotated[list[Polygon], Field(min_length=1)]

    @model_validator(mode="after")
    def valid_geometry(self) -> Self:
        positions = (
            xy for polygon in self.coordinates for ring in polygon for xy in ring
        )
        if any(not (-180 <= xy[0] <= 180 and -90 <= xy[1] <= 90) for xy in positions):
            raise ValueError(
                "basin coordinates must be longitude/latitude in EPSG:4326"
            )
        rings = (ring for polygon in self.coordinates for ring in polygon)
        if any(ring[0] != ring[-1] for ring in rings):
            raise ValueError("basin rings must be closed")
        geometry = shape(self.model_dump())
        if geometry.is_empty or not geometry.is_valid:
            raise ValueError("basin geometry must be nonempty and valid")
        if not geometry.covers(Point(86.668726, 27.269326)):
            raise ValueError("basin must cover the Rabuwa demo outlet")
        return self


class BasinFeature(Boundary):
    type: Literal["Feature"]
    id: Literal["NP_1_00092"]
    properties: BasinProperties
    geometry: BasinGeometry


class BasinInput(Boundary):
    type: Literal["FeatureCollection"]
    features: Annotated[list[BasinFeature], Field(min_length=1, max_length=1)]


class BasinDocument(BasinInput):
    region: Literal["nepal"]
    attribution: Nonempty


class PointGeometry(Boundary):
    type: Literal["Point"]
    coordinates: Position


class StationFeature(Boundary):
    type: Literal["Feature"]
    properties: Identity
    geometry: PointGeometry


class StationDocument(Boundary):
    type: Literal["FeatureCollection"]
    region: Literal["nepal"]
    features: Annotated[list[StationFeature], Field(min_length=1, max_length=1)]


class DemoBundle(Boundary):
    model_config = ConfigDict(
        json_schema_extra={"$schema": "https://json-schema.org/draft/2020-12/schema"}
    )
    manifest: Manifest
    series: Series
    station: StationDocument
    basin: BasinDocument

    @model_validator(mode="after")
    def consistent_bundle(self) -> Self:
        m, s = self.manifest, self.series
        history = s.observations
        point = self.station.features[0]
        if point.properties != m.station or point.geometry.coordinates != [
            m.station.longitude,
            m.station.latitude,
        ]:
            raise ValueError("station identity/coordinates mismatch")
        first = parse_utc_instant(s.forecasts[0].issued_at)
        if m.generated_at != stamp(first):
            raise ValueError("generated_at must equal the fixed first issue time")
        if len({f.forecast_id for f in s.forecasts}) != len(s.forecasts):
            raise ValueError("forecast identities must be unique")
        expected_times = [
            stamp(first + timedelta(hours=h)) for h in OBSERVATION_OFFSETS
        ]
        if history.valid_times != expected_times:
            raise ValueError(
                "observation times must span -168h through final issue at +42h"
            )
        if (history.window_start, history.window_end) != (
            expected_times[0],
            stamp(first + timedelta(hours=43)),
        ):
            raise ValueError("observation window must be half-open")
        expected_gaps = [
            {
                "start": stamp(first + timedelta(hours=start)),
                "end": stamp(first + timedelta(hours=end)),
            }
            for start, end in OBSERVATION_GAPS
        ]
        if [gap.model_dump() for gap in history.gaps] != expected_gaps:
            raise ValueError("gap intervals must match the scenario")
        DemoScenario(
            first_issued_at=first,
            observations=tuple(history.values),
            forecasts=tuple(f.to_domain() for f in s.forecasts),
        )
        if self.basin.attribution != self.basin.features[0].properties.source:
            raise ValueError("basin attribution must match its source")
        if m.provenance.basin.attribution != self.basin.attribution:
            raise ValueError("manifest basin attribution must match the geometry")
        return self


def stamp(value: datetime) -> str:
    return ensure_utc(value).isoformat().replace("+00:00", "Z")
