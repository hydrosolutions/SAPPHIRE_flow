"""recap Data Gateway coverage manifest + training-readiness gate (Plan 082 Phase 3).

The Gateway exposes NO coverage metadata (Resolved Gateway Question 3) — SAP3
maintains its own SUPERVISED manifest (a human-recorded record of which
historical span has actually been back-extracted and verified per Gateway
column) and gates automatic Flow-6 training on it. This module never infers
coverage from row counts or non-empty DataFrames — a fixture with data but no
DECLARED span yields no coverage (Task 3A requirement (c)).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime

_REQUIRED_ROW_FIELDS: tuple[str, ...] = (
    "gateway_hru_name",
    "name",
    "dataset",
    "variable",
    "spatial_type",
    "start",
    "end",
)


@dataclass(frozen=True, kw_only=True, slots=True)
class GatewayCoverageKey:
    """member_id is DELIBERATELY not part of the key — coverage is member-
    agnostic (every ensemble member of a cycle shares its span)."""

    gateway_hru_name: str
    name: str
    dataset: str
    variable: str
    band_id: int | None


@dataclass(frozen=True, kw_only=True, slots=True)
class GatewayCoverageSpan:
    start: UtcDatetime
    end: UtcDatetime

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError(
                f"coverage span start {self.start} must precede end {self.end}"
            )

    def contains(self, window: GatewayCoverageSpan) -> bool:
        return self.start <= window.start and window.end <= self.end


@dataclass(frozen=True, slots=True)
class GatewayCoverageManifest:
    """A SUPERVISED coverage record: ``entries`` is populated ONLY from
    explicit ``parse_coverage_manifest_row``/``build_coverage_manifest`` calls
    that require an explicit ``start``/``end`` in every row. There is no
    alternate constructor that derives a span from row counts or non-empty
    data — a key absent from ``entries`` means "no declared coverage",
    checked by :func:`coverage_spans_window`.
    """

    entries: dict[GatewayCoverageKey, GatewayCoverageSpan]

    def get(self, key: GatewayCoverageKey) -> GatewayCoverageSpan | None:
        return self.entries.get(key)


def parse_coverage_manifest_row(
    raw: dict[str, Any],
) -> tuple[GatewayCoverageKey, GatewayCoverageSpan]:
    """Parse one supervised-manifest row (boundary parse, Task 3A).

    Raises ``ConfigurationError`` when a required field is absent — including
    ``band_id`` when ``spatial_type == "elevation_band"`` — rather than
    silently accepting a partial/ambiguous row.
    """
    missing = [f for f in _REQUIRED_ROW_FIELDS if f not in raw]
    if missing:
        raise ConfigurationError(
            f"Gateway coverage manifest row missing required field(s): "
            f"{', '.join(missing)}"
        )

    spatial_type = SpatialRepresentation(raw["spatial_type"])
    band_id: int | None
    if spatial_type is SpatialRepresentation.ELEVATION_BAND:
        if "band_id" not in raw or raw["band_id"] is None:
            raise ConfigurationError(
                "Gateway coverage manifest row missing required field: "
                "band_id (required when spatial_type=elevation_band)"
            )
        band_id = cast("int", raw["band_id"])
    else:
        band_id = None

    key = GatewayCoverageKey(
        gateway_hru_name=cast("str", raw["gateway_hru_name"]),
        name=cast("str", raw["name"]),
        dataset=cast("str", raw["dataset"]),
        variable=cast("str", raw["variable"]),
        band_id=band_id,
    )
    span = GatewayCoverageSpan(
        start=ensure_utc(raw["start"]), end=ensure_utc(raw["end"])
    )
    return key, span


def build_coverage_manifest(
    rows: list[dict[str, Any]],
) -> GatewayCoverageManifest:
    entries: dict[GatewayCoverageKey, GatewayCoverageSpan] = {}
    for row in rows:
        key, span = parse_coverage_manifest_row(row)
        entries[key] = span
    return GatewayCoverageManifest(entries=entries)


def coverage_spans_window(
    manifest: GatewayCoverageManifest,
    requested_window: GatewayCoverageSpan,
    required_keys: list[GatewayCoverageKey],
) -> bool:
    """Training-readiness gate (Task 3B item 1).

    Refuses (returns ``False``) unless the manifest's DECLARED covered span
    contains ``requested_window`` for EVERY required key. A required key
    ABSENT from the manifest is treated as no-coverage (refuse) — never
    inferred as "probably fine".
    """
    for key in required_keys:
        span = manifest.get(key)
        if span is None or not span.contains(requested_window):
            return False
    return True


def assert_returned_span_covers_request(
    requested: GatewayCoverageSpan, returned: GatewayCoverageSpan
) -> None:
    """Training HARD-BLOCKS (raises) when the returned span is short of the
    requested window (Task 3B item 2). Callers on the OPERATIONAL forecast
    path must NOT call this — they log a WARNING and continue instead
    (a short horizon is still usable), matching the existing
    ``operational_inputs.no_nwp`` graceful-degrade precedent.
    """
    if returned.start > requested.start or returned.end < requested.end:
        raise ConfigurationError(
            f"returned span [{returned.start}, {returned.end}) is shorter "
            f"than the requested training window "
            f"[{requested.start}, {requested.end})"
        )
