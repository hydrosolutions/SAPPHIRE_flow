from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from random import Random
from typing import TYPE_CHECKING

import structlog

from sapphire_flow.cli.nepal_demo_schemas import (
    BANNER,
    BasinInput,
    DemoBundle,
    Instant,
    stamp,
)
from sapphire_flow.services.nepal_demo import build_scenario
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.nepal_demo import (
    FORECAST_OFFSETS,
    OBSERVATION_GAPS,
    OBSERVATION_OFFSETS,
)

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.nepal_demo import DemoIssue, DemoScenario

SEED = 20260916
DEFAULT_ISSUE = ensure_utc(datetime(2025, 8, 12, tzinfo=UTC))
FILENAMES = {
    "manifest": "region.json",
    "series": "series.json",
    "station": "station.geojson",
    "basin": "basin.geojson",
}


def serialize_issue(issue: DemoIssue) -> dict[str, object]:
    return {
        "source_mode": "illustrative",
        "unit": "m3/s",
        "forecast_id": f"DEMO-NP-001-{issue.issued_at:%Y%m%dT%H%M%SZ}",
        "issued_at": stamp(issue.issued_at),
        "valid_times": [
            stamp(issue.issued_at + timedelta(hours=h)) for h in FORECAST_OFFSETS
        ],
        "horizon_start": stamp(issue.issued_at + timedelta(hours=3)),
        "horizon_end": stamp(issue.issued_at + timedelta(hours=75)),
        "series": {"0.25": issue.lower, "0.5": issue.median, "0.75": issue.upper},
        "gaps": [],
        "qc_status": "synthetic_eligible",
        "eligibility_note": "Eligible only for illustrative playback; "
        "synthetic values, no operational QC or trained model.",
    }


def serialize_bundle(scenario: DemoScenario, basin: BasinInput) -> DemoBundle:
    issue = scenario.first_issued_at
    history_times = [stamp(issue + timedelta(hours=h)) for h in OBSERVATION_OFFSETS]
    station = {
        "network": "demo",
        "code": "DEMO-NP-001",
        "display_name": "Illustrative demo gauge",
        "backend_uuid": None,
        "longitude": 86.668726,
        "latitude": 27.269326,
    }
    provenance = {
        "basin": {
            "kind": "real_geometry",
            "attribution": basin.features[0].properties.source,
        },
        "basemap": {
            "kind": "third_party_raster",
            "attribution": (
                "Basemap attribution is displayed from the region configuration."
            ),
        },
        "station": {
            "kind": "synthetic",
            "attribution": "Fictional demonstration point at the Rabuwa GIS outlet; "
            "not a verified measurement site.",
        },
        "observations": {
            "kind": "synthetic",
            "attribution": (
                f"Simulated discharge history, seed {SEED}; no measurements."
            ),
        },
        "forecast": {
            "kind": "synthetic",
            "attribution": (
                f"Independent invented issue curves, seed {SEED + 1}; "
                "no trained model or observation input."
            ),
        },
    }
    manifest = {
        "schema_version": "flow-map-region-bundle/v2",
        "region": "nepal",
        "generated_at": stamp(issue),
        "source_mode": "illustrative",
        "generator_seed": SEED,
        "banner_text": BANNER,
        "provenance": provenance,
        "uncertainty_meaning": "illustrative_spread",
        "spread_label": "Illustrative spread — not calibrated uncertainty",
        "station": station,
        "units": {"discharge": "m3/s"},
        "timezone": "Asia/Kathmandu",
        "forecast_cycle": {
            "cycle_hours": 6,
            "cadence_seconds": 10800,
            "horizon_steps": 24,
            "issue_count": 8,
            "representation": "quantiles",
            "quantile_levels": [0.25, 0.5, 0.75],
            "starts_at_issue_time": False,
        },
        "thresholds": None,
        "threshold_basis": "none_available",
        "comparator": None,
        "date_basis": "demonstration_date",
        "date_label": "Demonstration date — synthetic values, "
        "not a record of conditions on this date",
        "verification_note": "Observations after each issue are synthetic "
        "verification outturn only; "
        "they are not inputs to any forecast. Agreement or disagreement is arbitrary, "
        "with no designed convergence and no evidence of forecast skill. "
        "No score is computed.",
        "supersession": {
            "cycle_hours": 6,
            "label": "Eight illustrative forecast issues",
            "note": "At each issue, earlier issues retain their full quantile bands "
            "as superseded forecasts. Hide future issues and observations after "
            "the active issue time.",
        },
    }
    series: dict[str, object] = {
        "region": "nepal",
        "observations": {
            "source_mode": "illustrative",
            "unit": "m3/s",
            "window_start": history_times[0],
            "window_end": stamp(issue + timedelta(hours=43)),
            "cadence_seconds": 3600,
            "valid_times": history_times,
            "values": scenario.observations,
            "gaps": [
                {
                    "start": stamp(issue + timedelta(hours=start)),
                    "end": stamp(issue + timedelta(hours=end)),
                }
                for start, end in OBSERVATION_GAPS
            ],
        },
        "forecasts": [serialize_issue(forecast) for forecast in scenario.forecasts],
    }
    return DemoBundle.model_validate(
        {
            "manifest": manifest,
            "series": series,
            "station": {
                "type": "FeatureCollection",
                "region": "nepal",
                "features": [
                    {
                        "type": "Feature",
                        "properties": station,
                        "geometry": {
                            "type": "Point",
                            "coordinates": [86.668726, 27.269326],
                        },
                    }
                ],
            },
            "basin": {
                **basin.model_dump(),
                "region": "nepal",
                "attribution": basin.features[0].properties.source,
            },
        }
    )


def publish_directory(staged: Path, destination: Path) -> None:
    """Atomic no-replace directory rename; never fall back to overwriting rename."""
    libc = ctypes.CDLL(None, use_errno=True)
    source, target = os.fsencode(staged), os.fsencode(destination)
    if sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(source, target, 4)  # RENAME_EXCL
    elif sys.platform == "linux" and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(-100, source, -100, target, 1)  # AT_FDCWD, RENAME_NOREPLACE
    else:
        raise OSError("atomic no-replace export requires Linux renameat2 or macOS")
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def export_bundle(
    *, basin_file: Path, output_dir: Path, issued_at: UtcDatetime = DEFAULT_ISSUE
) -> None:
    if os.path.lexists(output_dir):
        raise FileExistsError(f"output already exists: {output_dir}")
    basin = BasinInput.model_validate_json(basin_file.read_bytes())
    bundle = serialize_bundle(
        build_scenario(
            issued_at, observation_rng=Random(SEED), forecast_rng=Random(SEED + 1)
        ),
        basin,
    )
    documents = bundle.model_dump(mode="json", by_alias=True)
    serialized = {
        FILENAMES[key]: json.dumps(
            value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True
        )
        + "\n"
        for key, value in documents.items()
    }
    serialized["schema.json"] = (
        json.dumps(
            DemoBundle.model_json_schema(),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    # TemporaryDirectory owns only its staging container, not the published path.
    with tempfile.TemporaryDirectory(
        prefix=".nepal-export-", dir=output_dir.parent
    ) as tmp:
        staged = Path(tmp) / "bundle"
        staged.mkdir()
        for filename, content in serialized.items():
            (staged / filename).write_text(content, encoding="utf-8")
        publish_directory(staged, output_dir)


def main() -> None:
    from pydantic import TypeAdapter

    parser = argparse.ArgumentParser(
        description="Export a synthetic Nepal animation bundle"
    )
    parser.add_argument("--basin-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--issued-at", default=stamp(DEFAULT_ISSUE))
    args = parser.parse_args()
    try:
        issued = TypeAdapter[str](Instant).validate_python(args.issued_at)
        export_bundle(
            basin_file=args.basin_file,
            output_dir=args.output_dir,
            issued_at=ensure_utc(datetime.fromisoformat(issued)),
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    structlog.get_logger(__name__).info(
        "nepal_demo.exported",
        output_dir=str(args.output_dir),
        source_mode="illustrative",
    )


if __name__ == "__main__":
    main()
