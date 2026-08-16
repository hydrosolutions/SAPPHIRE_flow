"""Plan 174 (M-A5) task 1a — the frozen `OrographySpec` record.

D3a: task 1a's *only* exit is a frozen, committed record describing the
ROUTE to model-orography elevation for the 26 station points — it contains
no hashes (those belong to 1b's `OrographySourceRecord`, which describes
what was actually MATERIALISED). This module is where the probe becomes
code, mirroring `era5_request.py`'s "operator-captured literal" discipline.

## Observed orography route — probed 2026-08-16

Branch A (model orography) is reachable with the credentials and licence
already accepted for Plan 171's P0 — no further operator act needed. Probed
live against the public CDS dataset pages and ECMWF's parameter database
(constraint 3, "verify against the service, not the documentation"):

- The `reanalysis-era5-land` dataset's own download-form variable list
  (`https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land?tab=download`)
  includes `"geopotential"` under its "Invariant" field group, alongside
  `total_precipitation`'s own group — the SAME dataset id, SAME CDS
  client (`cdsapi.Client()`), SAME licence Plan 171 already accepted. There
  is no second account, no second licence, no second service.
- ECMWF's parameter database (`https://codes.ecmwf.int/parameter-database/api/v1/param/129`,
  param 129, shortName `z`) documents geopotential as "the gravitational
  potential energy of a unit mass, at a particular location, relative to
  mean sea level ... The geopotential height can be calculated by dividing
  the geopotential by the Earth's gravitational acceleration, g (=9.80665 m
  s-2) ... At the surface of the Earth, this parameter shows the variations
  in geopotential (height) of the surface, and is often referred to as the
  orography." Units (`unit_id` 15,
  `https://codes.ecmwf.int/parameter-database/api/v1/unit/15`) are
  `m**2 s**-2`, confirming D3a's declared Φ/g0 conversion with
  `g0 = 9.80665 m s⁻²` exactly.
- The producer states the vertical reference as "relative to mean sea
  level" and does not name a specific geoid realisation (EGM96/EGM2008) —
  recorded verbatim as `VerticalDatum.LOCAL_MSL` rather than guessing a
  geoid model the producer itself does not commit to (the same honesty
  D3b applies to the station side).
- Because `geopotential` is an invariant field of the SAME
  `reanalysis-era5-land` dataset, it is delivered on the identical 0.1°
  ERA5-Land grid already used for `total_precipitation`
  (`scripts/dhm_precip/era5_request.py:37`) — the aggregation rule
  therefore degenerates to the identity case of the one general
  area-weighted-mean aggregator 1b implements (a single source cell,
  weight 1, per target cell). 1b's exact-grid-vector check (D3a) is the
  real proof of this, run once the raster is actually materialised.

**Branch B was not evaluated.** Branch A satisfied D3a's "no operator act"
test on the first probe, so the ordered DEM candidate list's acceptance
criteria were never applied to any candidate. `rejected_candidates` is
therefore empty by construction, not by omission — this is recorded rather
than left ambiguous, per D3a's "the probe records why each rejected
candidate was rejected" (there are none to record).

Neither branch has been downloaded: this module freezes the ROUTE only.
Materialisation (1b) is gated on an operator step, exactly like Plan 171's
Task 4b — no credentials are available to this implementer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from scripts.dhm_precip.domain_types import OrographySource, VerticalDatum

# WMO standard gravity (D3a) — the sole legal divisor for the
# `geopotential_g0` conversion rule.
G0_M_PER_S2 = 9.80665

# D3a: "the aggregation rule (frozen, identical for both branches where
# applicable)" — the UNWEIGHTED arithmetic mean of the source cells whose
# centres fall inside the target 0.1 deg ERA5-Land cell. One id, mirroring
# `era5_deaccumulate.ACCUMULATION_RULE_ID`'s naming convention.
#
# CORRECTED 2026-08-16: the id previously said "area_weighted_mean", which
# the implementation never was. The plan's accepted resolution is to name
# the rule for what it does, not to implement weighting — `cos(lat)` varies
# ~0.17 % within one 0.1 deg cell, negligible against intra-cell relief of
# hundreds of metres. The id is written into the raster's attrs and into
# the extraction manifest, so it must not assert an unused method.
AGGREGATION_RULE_ID = "era5_land_orography_mean_of_contained_cells_v1"

OROGRAPHY_SCHEMA_VERSION = "1"
OROGRAPHY_CODE_VERSION = "1"


class OrographyConversionRule(StrEnum):
    """D3a — the only two legal conversion rules. A `Literal` would let a
    stray string construct a spec that type-checks but is not one of the
    two the plan actually defines; a `StrEnum` makes any third value a
    construction-time `ValueError`, never a silent divide."""

    GEOPOTENTIAL_G0 = "geopotential_g0"
    IDENTITY = "identity"


@dataclass(frozen=True, kw_only=True, slots=True)
class RejectedCandidate:
    """D3a — one entry of Branch B's rejected-candidate log."""

    product_id: str
    reason: str


@dataclass(frozen=True, kw_only=True, slots=True)
class OrographySpec:
    """D3a — frozen by task 1a. Describes the ROUTE: product identity,
    where to fetch it, its licence, its physical/coordinate metadata, and
    the frozen aggregation/conversion rules. Contains no hashes — those
    belong to `OrographySourceRecord` (1b), which describes what was
    actually materialised under this spec."""

    source: OrographySource
    product_id: str
    product_version: str
    download_url: str
    licence_name: str
    licence_version: str
    licence_url: str
    source_crs: str
    vertical_reference: VerticalDatum
    units: str
    no_data_sentinel: str
    aggregation_rule_id: str
    conversion_rule: OrographyConversionRule
    probe_date: date
    rejected_candidates: tuple[RejectedCandidate, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "product_id",
            "product_version",
            "licence_name",
            "licence_version",
            "source_crs",
            "units",
            "no_data_sentinel",
            "aggregation_rule_id",
        ):
            value = getattr(self, field_name)
            if not value.strip():
                raise ValueError(f"{field_name} must not be blank")
        for url_field in ("download_url", "licence_url"):
            value = getattr(self, url_field)
            if not value.startswith("https://"):
                raise ValueError(f"{url_field} must be an https URL, got {value!r}")


def geopotential_to_elevation_m(phi_m2_s2: float) -> float:
    """D3a Branch A — `z = Φ / g0`."""
    return phi_m2_s2 / G0_M_PER_S2


# The frozen record itself (1a's deliverable). Branch A, per the probe above.
OBSERVED_OROGRAPHY_SPEC = OrographySpec(
    source=OrographySource.MODEL_OROGRAPHY,
    product_id="reanalysis-era5-land:geopotential",
    product_version=(
        "ECMWF parameter 129 (shortName 'z'), time-invariant field of the "
        "reanalysis-era5-land dataset — no separate dataset version; "
        "requested with any single valid timestamp since the field does "
        "not vary in time"
    ),
    download_url=(
        "https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land?tab=download"
    ),
    licence_name="Licence to use Copernicus Products",
    licence_version="1.0",
    licence_url=(
        "https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land?tab=licence"
    ),
    source_crs=(
        "regular latitude/longitude grid, WGS84 (EPSG:4326) — identical "
        "grid definition to the acquired total_precipitation product "
        "(scripts/dhm_precip/era5_request.py:37), confirmed by dataset "
        "commonality and re-verified exactly by 1b's grid-vector check"
    ),
    vertical_reference=VerticalDatum.LOCAL_MSL,
    units="m**2 s**-2 (geopotential; ECMWF parameter-database unit id 15)",
    no_data_sentinel=(
        "NaN (CF fill value; matches era5_transform.py's own _FillValue "
        "convention, scripts/dhm_precip/era5_transform.py:87)"
    ),
    aggregation_rule_id=AGGREGATION_RULE_ID,
    conversion_rule=OrographyConversionRule.GEOPOTENTIAL_G0,
    probe_date=date(2026, 8, 16),
    rejected_candidates=(),
)
