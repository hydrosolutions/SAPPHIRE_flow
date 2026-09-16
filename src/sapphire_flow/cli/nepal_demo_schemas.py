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

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.nepal_demo import DemoScenario

BANNER = "Illustrative scenario — synthetic data, not an operational forecast"


def _calendar_time(value: str) -> str:
    datetime.fromisoformat(value)
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


class ForecastMetadata(Boundary):
    forecast_id: Nonempty
    issued_at: Instant
    representation: Literal["quantiles"]
    quantile_levels: list[Discharge]
    cadence_seconds: Literal[3600]
    horizon_start: Instant
    horizon_end: Instant
    valid_times: list[Instant]
    qc_status: Literal["synthetic_eligible"]
    eligibility_note: Nonempty


class Units(Boundary):
    discharge: Literal["m3/s"]


class Supersession(Boundary):
    cycle_hours: Literal[6]
    label: Literal["Single-issue illustrative scenario"]
    note: Literal["No earlier forecast cycles are supplied in this demonstration."]


class Manifest(Boundary):
    schema_version: Literal["flow-map-region-bundle/v1"]
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
    forecast: ForecastMetadata
    thresholds: None
    threshold_basis: Literal["none_available"]
    comparator: None
    date_basis: Literal["demonstration_date"]
    date_label: Nonempty
    verification_label: Nonempty
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
    window_start: Instant
    window_end: Instant
    cadence_seconds: Literal[3600]


class Outturn(ValueSeries):
    kind: Literal["verification_outturn"]
    starts_at_issue_time: Literal[False]


class Quantiles(Boundary):
    lower: list[Discharge] = Field(alias="0.25")
    median: list[Discharge] = Field(alias="0.5")
    upper: list[Discharge] = Field(alias="0.75")


class ForecastSeries(Boundary):
    source_mode: Literal["illustrative"]
    unit: Literal["m3/s"]
    forecast_id: Nonempty
    valid_times: list[Instant]
    series: Quantiles
    gaps: list[Gap]


class Series(Boundary):
    region: Literal["nepal"]
    observations: History
    forecast: ForecastSeries
    verification: Outturn
    superseded: Annotated[list[object], Field(max_length=0)]


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
    manifest: Manifest
    series: Series
    station: StationDocument
    basin: BasinDocument

    @model_validator(mode="after")
    def consistent_bundle(self) -> Self:
        m, s = self.manifest, self.series
        f, h, outturn = m.forecast, s.observations, s.verification
        point = self.station.features[0]
        if point.properties != m.station or point.geometry.coordinates != [
            m.station.longitude,
            m.station.latitude,
        ]:
            raise ValueError("station identity/coordinates mismatch")
        if s.forecast.forecast_id != f.forecast_id:
            raise ValueError("forecast identity mismatch")
        if f.quantile_levels != [0.25, 0.5, 0.75]:
            raise ValueError("quantile levels must be 0.25/0.5/0.75")
        issue = ensure_utc(datetime.fromisoformat(f.issued_at))
        history_times = [stamp(issue + timedelta(hours=h)) for h in range(-168, 0)]
        forecast_times = [stamp(issue + timedelta(hours=h)) for h in range(1, 73)]
        if h.valid_times != history_times or any(
            times != forecast_times
            for times in (f.valid_times, s.forecast.valid_times, outturn.valid_times)
        ):
            raise ValueError("valid times must match the hourly history/horizon")
        if (h.window_start, h.window_end, f.horizon_start, f.horizon_end) != (
            history_times[0],
            f.issued_at,
            forecast_times[0],
            stamp(issue + timedelta(hours=73)),
        ):
            raise ValueError("history/horizon windows must be half-open")
        for block, offsets in ((h, (-48, -42)), (outturn, (41, 43))):
            expected = [
                {
                    "start": stamp(issue + timedelta(hours=offsets[0])),
                    "end": stamp(issue + timedelta(hours=offsets[1])),
                }
            ]
            if [gap.model_dump() for gap in block.gaps] != expected:
                raise ValueError("gap intervals must match the scenario")
        if s.forecast.gaps:
            raise ValueError("demo forecast has no gaps")
        q = s.forecast.series
        DemoScenario(
            issued_at=issue,
            history=tuple(h.values),
            lower=tuple(q.lower),
            median=tuple(q.median),
            upper=tuple(q.upper),
            outturn=tuple(outturn.values),
        )
        if self.basin.attribution != self.basin.features[0].properties.source:
            raise ValueError("basin attribution must match its source")
        if m.provenance.basin.attribution != self.basin.attribution:
            raise ValueError("manifest basin attribution must match the geometry")
        return self


def stamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
