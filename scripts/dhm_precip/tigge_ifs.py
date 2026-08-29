"""Plan 216 (M-A11) T1 — retrieve one JJAS 2025 season of ECMWF IFS control-
forecast `tp` from TIGGE (via ECDS), deaccumulate, and extract to the 26
gauge station points.

⛔ Control-only (`type: cf`), one season (D2), one centre (ECMWF) — this is a
phase screening, not a correction and not an operational feed (D6: a
measured ~48 h embargo on TIGGE makes it research-only). See
`docs/plans/216-ifs-diurnal-timing-re-evaluation.md` D6 for the source
contract, MEASURED 2026-08-29 against the live ECDS API — including one
correction to the plan's own draft: the grid returned is NOT a regular 0.5°
lat/lon grid. `tigge-forecasts` on ECDS has no `grid` interpolation input at
all (confirmed against the process's own input schema) — it returns the
model's native reduced Gaussian grid (measured: N640, ~1,600 irregularly
spaced points inside `STUDY_AREA`, not 11x19=209 regular cells). The nearest-
cell OPERATOR (haversine argmin) is unchanged and still correct; only the
grid-geometry claim was wrong.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
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

# D2 — "One monsoon season: JJAS 2025." This track answers ONE screening
# question against ONE season; there is no `--year` CLI option (removed —
# see `TiggeIdentityError`/`assert_tigge_identity` below) because a wrong
# year silently mislabelled as "JJAS 2025" in every downstream filename,
# CSV and report is exactly the failure mode D2 forbids.
TIGGE_YEAR = 2025
TIGGE_MONTHS: tuple[int, ...] = (6, 7, 8, 9)
TIGGE_INIT_HOURS_UTC: tuple[int, ...] = (0, 12)

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
# scripts/dhm_precip/era5_extract.py:_haversine_km; duplicated (not
# imported) because that module's haversine is private and this module's
# nearest-neighbour search is over an IRREGULAR point cloud, not a
# registered regular grid — importing era5_extract's public surface would
# drag in 0.1 deg grid-registration validation that does not apply here.


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
    """The opened GRIB's init axis, lead axis or origin does not match the
    JJAS `TIGGE_YEAR` control-forecast request this module issues (D2 pins
    this track to exactly ONE season). Proceeding without this check would
    let a stale or wrong `--skip-retrieve` file — a different year, a
    different init/lead axis, a different centre — be silently reported
    as "JJAS `TIGGE_YEAR`"."""


@runtime_checkable
class CdsClient(Protocol):
    """The single injected ECDS-call seam — mirrors
    `scripts/dhm_precip/era5_acquire.CdsClient` so tests never touch the
    network; the real implementation talks to ECDS, never the Copernicus
    CDS (⛔ plan 216 per-run scope)."""

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


def assert_tigge_identity(
    ds: xr.Dataset,
    *,
    expected_leadtime_hours: Sequence[int],
    expected_year: int = TIGGE_YEAR,
    expected_months: Sequence[int] = TIGGE_MONTHS,
    expected_init_hours_utc: Sequence[int] = TIGGE_INIT_HOURS_UTC,
    variable: str = TIGGE_DATA_VAR_NAME,
) -> None:
    """D2/D6/T1 Verify — the file's own init axis, lead axis and (where
    available) source attribute must actually BE the one JJAS
    `expected_year` control-forecast request this module issues, never
    assumed from a filename or a `--skip-retrieve` re-use. Raises
    `TiggeIdentityError` on any mismatch."""
    assert_tp_units(ds, variable=variable)

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
    ending increments, one `DeaccumulatedIncrement` per (init, step>0).
    Continuous accumulation from forecast start (measured 2026-08-29:
    `step=0` is exactly zero and every later step is the running total —
    no daily reset, unlike ERA5-Land's 01 UTC accumulator), so each
    increment is `total[step] - total[step - 6h]`. Handles both a single
    init (`time` scalar) and a full season (`time` a dimension)."""
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
    over the file's own irregular point cloud — a native reduced-Gaussian
    grid has no row/col registration to exploit, so this is a linear
    argmin rather than era5_extract's regular-grid lookup, but it is the
    SAME operator (nearest by great-circle distance)."""
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


# D6 — "Attribution to ECMWF and acknowledgement of TIGGE are licence
# conditions on any output." Fixed text, shared by T1 and T2 so every
# derived artefact (raw extraction AND the T2 comparison CSV) carries the
# same record — never stdout-only, which does not travel with the file.
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
_RAW_FILENAME = "tigge_ecmwf_cf_tp_jjas2025.grib"
_POINTS_FILENAME = "tigge_station_series_jjas2025.parquet"

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
    ap.add_argument("--out-root", type=Path, default=DEFAULT_TIGGE_ROOT)
    ap.add_argument(
        "--skip-retrieve", action="store_true", help="reuse an existing raw file"
    )
    args = ap.parse_args()

    raw_path = args.out_root / "raw" / _RAW_FILENAME
    if args.skip_retrieve and not raw_path.exists():
        raise FileNotFoundError(
            f"--skip-retrieve was given but {raw_path} does not exist — "
            "run without --skip-retrieve to fetch it, or fix the path"
        )
    if not args.skip_retrieve:
        client = RealTiggeClient()
        payload = build_tigge_request(
            year=TIGGE_YEAR,
            months=TIGGE_MONTHS,
            days=range(1, 32),
            times=TIGGE_INIT_HOURS_UTC,
            leadtime_hours=REQUEST_LEADTIME_HOURS,
            area=STUDY_AREA,
        )
        log.info("tigge_ifs.retrieve.start", raw_path=str(raw_path))
        client.retrieve_to_path(
            dataset=TIGGE_DATASET_ID, payload=payload, target=raw_path
        )
        log.info("tigge_ifs.retrieve.done", raw_path=str(raw_path))

    ds = xr.open_dataset(raw_path, engine="cfgrib")
    # D2/D6 — the opened file must actually BE the JJAS TIGGE_YEAR
    # control-forecast request (never assumed from the filename, which is
    # exactly what a stale `--skip-retrieve` file would defeat).
    assert_tigge_identity(ds, expected_leadtime_hours=REQUEST_LEADTIME_HOURS)
    increments = deaccumulate(ds)
    coords_path = resolve_coords_path()
    # D8-style cardinality tripwire (mirrors extract_era5.py's
    # `_default_expected_stations()`): the expected-station set must come
    # from an INDEPENDENT inventory, never a second read of the very same
    # coordinate file `load_station_coordinates` is about to validate —
    # that would make the equality check inside it a tautology that can
    # never fail.
    gauge_population = load_gauge_masked_population()
    all_stations = frozenset(gauge_population.by_station.keys())
    coords = load_station_coordinates(coords_path, expected_stations=all_stations)
    series = extract_station_series(ds, increments, coords)

    points_path = args.out_root / "points" / _POINTS_FILENAME
    points_path.parent.mkdir(parents=True, exist_ok=True)
    series.write_parquet(points_path)
    attribution_path = write_tigge_attribution(points_path)

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
    print(f"wrote {attribution_path}")
    print(f"Attribution: {TIGGE_ATTRIBUTION_TEXT}. {TIGGE_ACKNOWLEDGEMENT_TEXT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
