"""Plan 216 (M-A11) T1 — retrieve one JJAS season of ECMWF IFS control-
forecast `tp` from TIGGE (via ECDS), deaccumulate, and extract to the 26
gauge station points. Plan 220 (M-A11b) parameterises the season by `--year`
(D1) so every overlapping JJAS season (2020-2025) is retrievable through the
same code path — never a second copy that could disagree.

⛔ Control-only (`type: cf`), one centre (ECMWF) — a phase screening, not a
correction and not an operational feed (D6: a measured ~48 h embargo makes
TIGGE research-only). Plan 216 D6 holds the source contract, MEASURED
2026-08-29 against the live ECDS API, with one correction to the plan's
draft: `tigge-forecasts` has no `grid` input at all, so what comes back is
the model's native reduced Gaussian grid (N640, ~1,600 irregular points in
`STUDY_AREA`), not a regular 0.5° 11x19 lat/lon grid. The nearest-cell
OPERATOR (haversine argmin) is unaffected. Plan 220 D2: a season's 244-run
schedule is EXPECTED, not required — a missing run is a named gap, not a
rejection (see `SeasonCompleteness`).
"""

from __future__ import annotations

import argparse
import calendar
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import numpy as np
import polars as pl
import structlog
import xarray as xr

from sapphire_flow.exceptions import SapphireError
from scripts.dhm_precip.loader import load_station_coordinates, resolve_coords_path
from scripts.dhm_precip.ma6_pairs import load_gauge_masked_population

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from scripts.dhm_precip.domain_types import Station, StationCoordinateTable

log = structlog.get_logger(__name__)

# --- D6 source contract (measured 2026-08-29) ---
TIGGE_DATASET_ID = "tigge-forecasts"
ECDS_URL = "https://ecds.ecmwf.int/api"
TIGGE_ORIGIN = "ecmwf"
TIGGE_FORECAST_TYPE = "control_forecast"
TIGGE_LEVEL_TYPE = "single_level"
TIGGE_VARIABLE = "total_precipitation"
TIGGE_DATA_VAR_NAME = "tp"  # cfgrib short name for total_precipitation
TIGGE_DATA_FORMAT = "grib"

# D1 (Plan 220) — the year is a REQUIRED CLI parameter (`--year`, no
# default): a wrong year, mislabelled downstream, is the failure mode D2
# forbids. What closes that hazard is `assert_tigge_identity` gating the
# OPENED FILE's own init axis against whichever year was requested — never a
# module-level default a caller could silently rely on and a request could
# silently disagree with. There is deliberately no `TIGGE_YEAR` constant any
# more (Plan 216 had one, defaulting both the schedule and the identity gate
# to 2025) — a value carried alongside the request instead of derived from it
# is exactly the recurring defect shape Plan 220 D1 closes by deletion.
TIGGE_MONTHS: tuple[int, ...] = (6, 7, 8, 9)
TIGGE_INIT_HOURS_UTC: tuple[int, ...] = (0, 12)

# D6, MEASURED 2026-08-29 on the real file. `cf` is the CONTROL forecast — a
# perturbed member (`pf`) is a different estimand (T1 `In`) — and `accum` is
# what makes deaccumulation meaningful.
EXPECTED_DATA_TYPE = "cf"
EXPECTED_STEP_TYPE = "accum"

# D6: N/W/S/E, exactly STUDY_AREA. The corrected grid-shape note above
# applies to what comes BACK, not to this request box, which is unchanged.
StudyArea = tuple[float, float, float, float]
STUDY_AREA: StudyArea = (31.0, 80.0, 26.0, 89.0)

# D6: units on the raw field, asserted from the file's own GRIB attribute —
# never inferred from the source name.
EXPECTED_UNITS = frozenset({"kg m**-2", "kg m-2"})

# Measured (this task, smoke test 2026-08-29): packing noise on 9 real
# steps produced negative diffs no larger than ~0.004 mm. A material
# negative well beyond that is a genuine non-monotonicity, not noise.
PACKING_TOLERANCE_MM = 0.05

_EARTH_RADIUS_KM = 6371.0088  # WGS84 spherical radius — same constant as
# era5_extract.py:_haversine_km, duplicated because that module's haversine
# is private and its public surface enforces 0.1 deg grid registration that
# an irregular point cloud does not have.


def _haversine_km(
    lat1: float, lon1: float, lat2: np.ndarray, lon2: np.ndarray
) -> np.ndarray:
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


class TiggeAcquisitionError(SapphireError):
    """Base for every M-A11 TIGGE acquisition/transform error."""


class TiggeUnitsError(TiggeAcquisitionError):
    """D6 — the raw `tp` field's own GRIB units attribute is not
    `kg m**-2`. Never inferred from the source name: a silently-metres
    file would read as a plausible ~1000x wet bias, not a crash."""


class TiggeConservationError(TiggeAcquisitionError):
    """A deaccumulated increment is negative beyond `PACKING_TOLERANCE_MM`
    — the continuous-accumulation-from-forecast-start assumption (D6) is
    violated for this file."""


class TiggeStepAxisError(TiggeAcquisitionError):
    """The file's `step` axis is not the requested, contiguous 6-hourly
    sequence starting at 0 h — deaccumulation requires consecutive steps."""


class TiggeIdentityError(TiggeAcquisitionError):
    """The opened GRIB's init axis, lead axis or origin is not the JJAS
    control-forecast request this module issues for the requested `--year`
    (D2). Without this check a stale `--skip-retrieve` file would be
    screened and reported as that year's season regardless of what it
    actually contains."""


@runtime_checkable
class CdsClient(Protocol):
    """The single injected ECDS-call seam — mirrors `era5_acquire.CdsClient`
    so tests never touch the network. ⛔ ECDS, never the Copernicus CDS."""

    def retrieve_to_path(
        self, *, dataset: str, payload: Mapping[str, object], target: Path
    ) -> None: ...


@dataclass(frozen=True, kw_only=True, slots=True)
class RealTiggeClient:
    """`cdsapi.Client` pointed explicitly at ECDS. `url` is passed
    explicitly and `key` is left `None` so `cdsapi` still resolves the key
    from `~/.cdsapirc`/`CDSAPI_KEY` itself (D6: "an existing Copernicus CDS
    key works") — this module never reads or holds the credential value."""

    ecds_url: str = ECDS_URL

    def retrieve_to_path(
        self, *, dataset: str, payload: Mapping[str, object], target: Path
    ) -> None:
        import cdsapi

        client = cdsapi.Client(url=self.ecds_url)
        target.parent.mkdir(parents=True, exist_ok=True)
        client.retrieve(dataset, dict(payload), str(target))


def build_tigge_request(
    *,
    year: int,
    months: Sequence[int],
    days: Sequence[int],
    times: Sequence[int],
    leadtime_hours: Sequence[int],
    area: StudyArea = STUDY_AREA,
) -> dict[str, object]:
    """The JSON-safe ECDS payload for one control-forecast `tp` pull (D6/T1
    `In`). `area` is N/W/S/E per D6, serialised as the `"N/W/S/E"` string
    the live costing endpoint accepts (measured 2026-08-29 — an
    `{n,w,s,e}` object is REJECTED: `"is not of type 'string'"`)."""
    n, w, s, e = area
    return {
        "origin": TIGGE_ORIGIN,
        "forecast_type": TIGGE_FORECAST_TYPE,
        "level_type": TIGGE_LEVEL_TYPE,
        "variable": [TIGGE_VARIABLE],
        "year": [str(year)],
        "month": [f"{m:02d}" for m in months],
        "day": [f"{d:02d}" for d in days],
        "time": [f"{t:02d}:00" for t in times],
        "leadtime_hour": [str(h) for h in leadtime_hours],
        "data_format": TIGGE_DATA_FORMAT,
        "area": f"{n:g}/{w:g}/{s:g}/{e:g}",
    }


def assert_tp_units(ds: xr.Dataset, *, variable: str = TIGGE_DATA_VAR_NAME) -> None:
    """D6 / T1 Verify — the ONE defect that would look like a plausible
    result: a units mismatch inflating (or deflating) precipitation by a
    factor of ~1000 with no crash. Asserted from the field's own GRIB
    attribute, never from the request or the filename."""
    if variable not in ds.data_vars:
        raise TiggeUnitsError(
            f"variable {variable!r} not present in dataset; found {list(ds.data_vars)}"
        )
    units = str(
        ds[variable].attrs.get("units") or ds[variable].attrs.get("GRIB_units") or ""
    ).strip()
    if units.lower() not in EXPECTED_UNITS:
        raise TiggeUnitsError(
            f"{variable!r} units attribute is {units!r}; expected one of "
            f"{sorted(EXPECTED_UNITS)} (kg m**-2 == millimetres) — a metres-valued "
            "file would read as a ~1000x wet bias"
        )


def expected_init_schedule(
    *,
    year: int,
    months: Sequence[int] = TIGGE_MONTHS,
    init_hours_utc: Sequence[int] = TIGGE_INIT_HOURS_UTC,
) -> tuple[datetime, ...]:
    """The EXPECTED set of initialisations one JJAS season SHOULD contain
    (JJAS x 00/12 UTC = 244 for every year, since JJAS never spans a leap
    day). Derived, never hard-coded, so there is no second literal to keep
    in sync. Plan 220 D2 — this is the EXPECTED schedule, not a required
    count: `assert_tigge_identity` reports a season's shortfall against it as
    named gaps rather than rejecting the season outright."""
    return tuple(
        datetime(year, month, day, hour)
        for month in months
        for day in range(1, calendar.monthrange(year, month)[1] + 1)
        for hour in init_hours_utc
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class SeasonCompleteness:
    """Plan 220 D2 — how much of one season's EXPECTED 244-run schedule the
    retrieved file actually contains. A missing expected init is a named gap
    ("real seasons have gaps... sometimes forecasts are not published"), not
    a rejection; `assert_tigge_identity` still rejects an init OUTSIDE the
    expected schedule, a duplicate, or a wrong lead/centre/step-0 axis — a
    retrieval fault, which is different from an incomplete archive."""

    year: int
    expected_count: int
    actual_count: int
    missing_inits: tuple[datetime, ...]

    @property
    def completeness_fraction(self) -> float:
        return self.actual_count / self.expected_count if self.expected_count else 0.0


def assert_tigge_identity(
    ds: xr.Dataset,
    *,
    expected_leadtime_hours: Sequence[int],
    expected_year: int,
    expected_months: Sequence[int] = TIGGE_MONTHS,
    expected_init_hours_utc: Sequence[int] = TIGGE_INIT_HOURS_UTC,
    expected_init_times: Sequence[datetime] | None = None,
    variable: str = TIGGE_DATA_VAR_NAME,
) -> SeasonCompleteness:
    """D2/D6/T1 Verify — the file's own init axis, lead axis, type/step
    attributes, source attribute and forecast-start accumulation must BE
    the request this module issues for `expected_year`, never assumed from
    a filename. Raises `TiggeIdentityError` on any REAL mismatch (wrong
    year/month/hour, a duplicate, an init outside the expected schedule, a
    wrong lead/centre/step-0 axis). `expected_init_times` defaults to the
    FULL `expected_init_schedule()`, so an init the schedule does not
    recognise is rejected; tests (and only tests) narrow it to their
    fixture's init.

    Plan 220 D2: a season SHORT of its expected schedule is no longer
    rejected outright — real seasons have unpublished runs. The shortfall is
    returned as a `SeasonCompleteness`, never silently absorbed into a
    smaller `n`."""
    assert_tp_units(ds, variable=variable)

    attrs = ds[variable].attrs
    data_type = str(attrs.get("GRIB_dataType") or "").strip().lower()
    if data_type != EXPECTED_DATA_TYPE:
        raise TiggeIdentityError(
            f"GRIB dataType is {data_type!r}; expected {EXPECTED_DATA_TYPE!r} "
            "(ECMWF CONTROL forecast) — a perturbed-member file is a "
            "different estimand, not this screen's"
        )
    step_type = str(attrs.get("GRIB_stepType") or "").strip().lower()
    if step_type != EXPECTED_STEP_TYPE:
        raise TiggeIdentityError(
            f"GRIB stepType is {step_type!r}; expected {EXPECTED_STEP_TYPE!r} — "
            "deaccumulation is only meaningful on an accumulated field"
        )

    init_times = np.atleast_1d(ds["time"].values).astype("datetime64[s]").astype(object)
    bad_years = sorted({t.year for t in init_times if t.year != expected_year})
    if bad_years:
        raise TiggeIdentityError(
            f"init axis contains year(s) {bad_years}; expected only "
            f"{expected_year} — refusing to silently label this file as "
            f"JJAS {expected_year}"
        )
    bad_months = sorted(
        {t.month for t in init_times if t.month not in set(expected_months)}
    )
    if bad_months:
        raise TiggeIdentityError(
            f"init axis contains month(s) {bad_months} outside JJAS "
            f"{sorted(expected_months)}"
        )
    bad_hours = sorted(
        {t.hour for t in init_times if t.hour not in set(expected_init_hours_utc)}
    )
    if bad_hours:
        raise TiggeIdentityError(
            f"init axis contains hour(s)-of-day {bad_hours}; expected only "
            f"{sorted(expected_init_hours_utc)} UTC"
        )

    expected_inits = sorted(
        expected_init_times
        if expected_init_times is not None
        else expected_init_schedule(
            year=expected_year,
            months=expected_months,
            init_hours_utc=expected_init_hours_utc,
        )
    )
    actual_inits = sorted(init_times)
    duplicates = sorted({t for t in actual_inits if actual_inits.count(t) > 1})
    if duplicates:
        raise TiggeIdentityError(
            f"init axis repeats {len(duplicates)} initialisation(s) "
            f"(first: {duplicates[0]}) — every init must appear exactly once"
        )
    # Plan 220 D2 — an init OUTSIDE the expected schedule means the
    # RETRIEVAL is wrong (a bug, not archive incompleteness) and is still
    # rejected. An init the schedule expected but the file lacks is a named
    # gap, reported below, never a rejection.
    extra = sorted(set(actual_inits) - set(expected_inits))
    if extra:
        raise TiggeIdentityError(
            f"init axis contains {len(extra)} initialisation(s) outside the "
            f"expected schedule (first {extra[0]}) — the retrieval, not the "
            "archive, is wrong"
        )
    missing = sorted(set(expected_inits) - set(actual_inits))

    steps_h = sorted((ds["step"].values / np.timedelta64(1, "h")).astype(int).tolist())
    expected_steps = sorted(expected_leadtime_hours)
    if steps_h != expected_steps:
        raise TiggeIdentityError(
            f"lead (step) axis {steps_h} does not match the expected request "
            f"{expected_steps}"
        )

    # Best-effort source-attribute check: a `GRIB_centre` key is not
    # independently MEASURED for this dataset (D6's own rule), so its
    # ABSENCE is not itself an error — but if cfgrib does surface it, a
    # value other than ECMWF's is a real identity mismatch, not noise.
    centre = (
        str(ds.attrs.get("GRIB_centre") or ds[variable].attrs.get("GRIB_centre") or "")
        .strip()
        .lower()
    )
    if centre and centre != "ecmf":
        raise TiggeIdentityError(
            f"GRIB centre attribute is {centre!r}; expected 'ecmf' (ECMWF) — "
            "this file's origin does not match the D6 request"
        )

    # D6 — accumulation runs from FORECAST START (measured: step 0 is
    # exactly zero). A step-0 field already carrying mass accumulates from
    # somewhere else, so every differenced increment would be offset. NaN is
    # left alone: T1 carries gaps, it never fills them.
    axis_steps_h = (ds["step"].values / np.timedelta64(1, "h")).astype(int)
    if 0 in axis_steps_h.tolist():
        # ⛔ index the file's OWN step axis, never the sorted copy above.
        zero_step = ds[variable].isel(step=int(np.argmin(np.abs(axis_steps_h)))).values
        over = np.abs(zero_step) > PACKING_TOLERANCE_MM
        if bool(over.any()):
            raise TiggeIdentityError(
                f"{int(over.sum())} step-0 value(s) exceed "
                f"{PACKING_TOLERANCE_MM} mm (max "
                f"{float(np.nanmax(np.abs(zero_step))):.4g}) — this field does "
                "not accumulate from forecast start"
            )

    return SeasonCompleteness(
        year=expected_year,
        expected_count=len(expected_inits),
        actual_count=len(actual_inits),
        missing_inits=tuple(missing),
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class DeaccumulatedIncrement:
    """One (init_time, ending_lead_hours) 6-hourly increment at every grid
    point retained in `values` — `mm` is period-ending: the window
    `(ending_lead_hours - 6, ending_lead_hours]` after `init_time`."""

    init_time_utc: np.datetime64
    ending_lead_hours: int
    mm: np.ndarray  # shape (n_points,) aligned to the source ds's `values` dim


def _iter_inits(ds: xr.Dataset) -> list[tuple[np.datetime64, np.ndarray]]:
    """Normalise the two shapes `cfgrib` can hand back for this request: a
    single init (`time` is a scalar coordinate, data is `(step, values)`)
    when only one (year, month, day, time-of-day) combination is present,
    or many inits (`time` is a dimension, data is `(time, step, values)`)
    for a real multi-day pull. Returns `(init_time, step-ordered raw
    values array of shape (step, values))` per init."""
    time_values = ds["time"].values
    if "time" in ds[TIGGE_DATA_VAR_NAME].dims:
        return [
            (np.datetime64(t, "ns"), ds[TIGGE_DATA_VAR_NAME].isel(time=i).values)
            for i, t in enumerate(np.atleast_1d(time_values))
        ]
    return [(np.datetime64(time_values, "ns"), ds[TIGGE_DATA_VAR_NAME].values)]


def deaccumulate(
    ds: xr.Dataset, *, variable: str = TIGGE_DATA_VAR_NAME
) -> list[DeaccumulatedIncrement]:
    """T1 — turn the raw forecast-start accumulator into 6-hourly period-
    ending increments, one per (init, step>0). Accumulation is continuous
    from forecast start (measured: no daily reset, unlike ERA5-Land's 01 UTC
    accumulator), so each increment is `total[step] - total[step - 6h]`.
    Handles a single init (`time` scalar) and a full season (`time` a
    dimension) alike."""
    assert_tp_units(ds, variable=variable)
    steps_h = (ds["step"].values / np.timedelta64(1, "h")).astype(int)
    order = np.argsort(steps_h)
    steps_h = steps_h[order]
    if steps_h[0] != 0 or not np.array_equal(
        np.diff(steps_h), np.full(len(steps_h) - 1, 6)
    ):
        raise TiggeStepAxisError(
            "expected a contiguous 6-hourly step axis starting at 0 h; "
            f"got {steps_h.tolist()}"
        )

    increments: list[DeaccumulatedIncrement] = []
    for init_time, raw in _iter_inits(ds):
        values = raw[order, :]  # (step, points), kg m**-2 == mm
        for i in range(1, len(steps_h)):
            diff = values[i] - values[i - 1]
            # A masked raw point gives a NaN increment, which passes both
            # the negativity test (NaN < x is False) and `np.clip` unchanged:
            # T1 carries gaps. It is rejected at the paired-statistic
            # boundary (`tigge_gauge_timing.restrict_to_pinned_season`).
            material_negative = diff < -PACKING_TOLERANCE_MM
            if bool(material_negative.any()):
                count = int(material_negative.sum())
                raise TiggeConservationError(
                    f"{count} negative increment(s) at init {init_time} step "
                    f"{steps_h[i]}h beyond tolerance {PACKING_TOLERANCE_MM} mm "
                    "— accumulation-from-forecast-start assumption violated"
                )
            increments.append(
                DeaccumulatedIncrement(
                    init_time_utc=init_time,
                    ending_lead_hours=int(steps_h[i]),
                    mm=np.clip(diff, 0.0, None),
                )
            )
    return increments


def nearest_point_index(
    *, lat: np.ndarray, lon: np.ndarray, station_lat: float, station_lon: float
) -> int:
    """Nearest-cell operator (D1 reuse, adapted): argmin haversine distance
    over the file's own irregular point cloud. A reduced-Gaussian grid has
    no row/col registration, so this is a linear argmin rather than
    era5_extract's grid lookup — the SAME operator, nearest by
    great-circle distance."""
    dist = _haversine_km(station_lat, station_lon, lat, lon)
    return int(np.argmin(dist))


def extract_station_series(
    ds: xr.Dataset,
    increments: list[DeaccumulatedIncrement],
    coords: StationCoordinateTable,
) -> pl.DataFrame:
    """T1 `Out` — one row per (station, init, ending lead). `valid_time_utc`
    is the period-ending timestamp `init + ending_lead_hours`. Gaps are
    never filled: a (station, init, lead) combination simply does not
    appear if `increments` does not cover it."""
    lat = ds["latitude"].values
    lon = ds["longitude"].values
    nearest: dict[Station, int] = {
        station: nearest_point_index(
            lat=lat, lon=lon, station_lat=c.lat, station_lon=c.lon
        )
        for station, c in coords.by_station.items()
    }
    stations = list(nearest.keys())
    idx_by_station = np.array([nearest[s] for s in stations])
    n_stations = len(stations)
    n_inc = len(increments)

    station_col = np.tile(np.array([str(s) for s in stations]), n_inc)
    init_col = np.repeat(
        np.array([np.datetime64(inc.init_time_utc, "ns") for inc in increments]),
        n_stations,
    )
    lead_col = np.repeat(
        np.array([inc.ending_lead_hours for inc in increments], dtype=np.int64),
        n_stations,
    )
    valid_col = init_col + lead_col.astype("timedelta64[h]")
    mm_col = np.concatenate([inc.mm[idx_by_station] for inc in increments]).astype(
        np.float64
    )

    return pl.DataFrame(
        {
            "station": station_col,
            "init_time_utc": init_col,
            "ending_lead_hours": lead_col,
            "valid_time_utc": valid_col,
            "tigge_mm": mm_col,
        }
    ).sort(["station", "init_time_utc", "ending_lead_hours"])


# D6 — attribution to ECMWF and acknowledgement of TIGGE are licence
# conditions on any output. Shared by T1 and T2 so every derived artefact
# carries the same record — never stdout-only, which does not travel.
TIGGE_ATTRIBUTION_TEXT = "ECMWF"
TIGGE_ACKNOWLEDGEMENT_TEXT = "Contains modified data from TIGGE."
TIGGE_LICENCE_NOTE = (
    "Research use only (D6): ECDS Terms of use + TIGGE licence accepted; "
    "measured ~48h embargo — never an operational dependency, for any centre."
)


def write_tigge_attribution(
    output_path: Path, *, extra: Mapping[str, object] | None = None
) -> Path:
    """Write the D6 attribution/acknowledgement record adjacent to
    `output_path`, as `<name>.attribution.json` — every T1/T2 derived
    artefact gets one."""
    record: dict[str, object] = {
        "attribution": TIGGE_ATTRIBUTION_TEXT,
        "acknowledgement": TIGGE_ACKNOWLEDGEMENT_TEXT,
        "licence_note": TIGGE_LICENCE_NOTE,
        "source_dataset": TIGGE_DATASET_ID,
        "for_file": output_path.name,
    }
    if extra:
        record.update(extra)
    sidecar = output_path.with_name(output_path.name + ".attribution.json")
    sidecar.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return sidecar


# --- CLI (T1 driver) ---
DEFAULT_TIGGE_ROOT = Path("data/dhm_precip/tigge")


def raw_filename(year: int) -> str:
    """Plan 220 D1 — filename derives from `year`, never a second literal
    that could disagree with the request. `year=2025` reproduces the exact
    on-disk name Plan 216 wrote, so the existing JJAS 2025 artefact is
    addressed by the same path without being re-downloaded."""
    return f"tigge_ecmwf_cf_tp_jjas{year}.grib"


def points_filename(year: int) -> str:
    """Plan 220 D1 companion to `raw_filename` — same derivation rule."""
    return f"tigge_station_series_jjas{year}.parquet"


# D3/T2 lead bands, defined here (not in T2) because they determine exactly
# which leadtime_hours T1 needs to request. D+1 is the first COMPLETE band
# (all four 6-hourly clock positions) — a band starting at step 0 would
# only ever cover 3 of 4 (no window ends at lead 0), so there is no D+0.
LEAD_BANDS: dict[str, tuple[int, ...]] = {
    "D+1": (24, 30, 36, 42),
    "D+2": (48, 54, 60, 66),
    "D+3": (72, 78, 84, 90),
}
# The full contiguous step axis T1 must retrieve: every band's steps, plus
# each band's immediate predecessor step (e.g. 18 for band D+1's 24) so
# every band-step's increment can be deaccumulated from its neighbour.
REQUEST_LEADTIME_HOURS: tuple[int, ...] = tuple(
    range(0, max(h for band in LEAD_BANDS.values() for h in band) + 1, 6)
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--year",
        type=int,
        required=True,
        help=(
            "JJAS season year (Plan 220 D1) — no default, so a wrong year is "
            "never silently assumed"
        ),
    )
    ap.add_argument("--out-root", type=Path, default=DEFAULT_TIGGE_ROOT)
    ap.add_argument(
        "--skip-retrieve", action="store_true", help="reuse an existing raw file"
    )
    args = ap.parse_args()
    year: int = args.year

    raw_path = args.out_root / "raw" / raw_filename(year)
    if args.skip_retrieve and not raw_path.exists():
        raise FileNotFoundError(
            f"--skip-retrieve was given but {raw_path} does not exist — "
            "run without --skip-retrieve to fetch it, or fix the path"
        )
    if not args.skip_retrieve:
        client = RealTiggeClient()
        payload = build_tigge_request(
            year=year,
            months=TIGGE_MONTHS,
            days=range(1, 32),
            times=TIGGE_INIT_HOURS_UTC,
            leadtime_hours=REQUEST_LEADTIME_HOURS,
            area=STUDY_AREA,
        )
        log.info("tigge_ifs.retrieve.start", raw_path=str(raw_path), year=year)
        client.retrieve_to_path(
            dataset=TIGGE_DATASET_ID, payload=payload, target=raw_path
        )
        log.info("tigge_ifs.retrieve.done", raw_path=str(raw_path), year=year)

    ds = xr.open_dataset(raw_path, engine="cfgrib")
    # D2/D6 — the opened file must actually BE the JJAS `year` control-
    # forecast request (never assumed from the filename, which is exactly
    # what a stale `--skip-retrieve` file would defeat).
    completeness = assert_tigge_identity(
        ds, expected_leadtime_hours=REQUEST_LEADTIME_HOURS, expected_year=year
    )
    increments = deaccumulate(ds)
    coords_path = resolve_coords_path()
    # D8-style cardinality tripwire (mirrors extract_era5.py): the
    # expected-station set must come from an INDEPENDENT inventory, never a
    # second read of the coordinate file being validated — that would make
    # the equality check a tautology.
    gauge_population = load_gauge_masked_population()
    all_stations = frozenset(gauge_population.by_station.keys())
    coords = load_station_coordinates(coords_path, expected_stations=all_stations)
    series = extract_station_series(ds, increments, coords)

    points_path = args.out_root / "points" / points_filename(year)
    points_path.parent.mkdir(parents=True, exist_ok=True)
    series.write_parquet(points_path)
    attribution_path = write_tigge_attribution(
        points_path,
        extra={
            "season_year": year,
            "expected_inits": completeness.expected_count,
            "actual_inits": completeness.actual_count,
            "completeness_fraction": round(completeness.completeness_fraction, 4),
            "missing_inits": [t.isoformat() for t in completeness.missing_inits],
        },
    )

    n_inits = series["init_time_utc"].n_unique()
    n_station_days = (
        series.select("station", pl.col("valid_time_utc").dt.date().alias("date"))
        .unique()
        .height
    )
    print(
        f"wrote {points_path}: {series.height} rows, "
        f"{len(coords.by_station)} stations x {n_inits} inits x "
        f"{series['ending_lead_hours'].n_unique()} leads, "
        f"{n_station_days} station-days "
        f"({series['valid_time_utc'].min()} .. {series['valid_time_utc'].max()})"
    )
    print(
        f"completeness: {completeness.actual_count}/{completeness.expected_count} "
        f"({completeness.completeness_fraction:.1%}) — "
        f"{len(completeness.missing_inits)} missing init(s)"
    )
    print(f"wrote {attribution_path}")
    print(f"Attribution: {TIGGE_ATTRIBUTION_TEXT}. {TIGGE_ACKNOWLEDGEMENT_TEXT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
