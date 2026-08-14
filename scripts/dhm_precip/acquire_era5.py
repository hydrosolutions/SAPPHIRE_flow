"""M-A4 (Plan 171) task 4a — ERA5-Land acquisition CLI.

Acquires (CDS) and/or transforms (local, D6-D9) hourly ERA5-Land total
precipitation over the Nepal study box for 2020-2025, per the D2-D11
design. See `docs/plans/171-era5-land-acquisition.md`.

Usage:
    uv run python scripts/dhm_precip/acquire_era5.py \
        --provenance data/dhm_precip/era5_land_provenance.json \
        --stage all

    # 2b's sample window, acquire-only:
    uv run python scripts/dhm_precip/acquire_era5.py \
        --provenance data/dhm_precip/era5_land_provenance.json \
        --stage acquire --window 2021-10

    # 3a's convention diagnostic, against the 2b sample already on disk:
    uv run python scripts/dhm_precip/acquire_era5.py \
        --provenance data/dhm_precip/era5_land_provenance.json \
        --stage diagnose --window 2021-10

Environment:
    Credentials for `cdsapi.Client()` come from `~/.cdsapirc` or the
    `CDSAPI_URL`/`CDSAPI_KEY` environment variables — never a CLI flag,
    never logged (D15, 4a's credential-redaction contract).

Exit codes:
    0  success
    2  CDS credentials absent or invalid
    3  CDS rejected the request, or a transient failure exhausted retries
    4  a D6/D7/D8/D9 transform post-condition failed
    5  storage/manifest write (or read) failed, including a missing or
       incomplete `--provenance` file
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# Precedent: src/sapphire_flow/adapters/meteoswiss_nwp.py:1 — xarray ships
# partial type stubs; the same three rules are relaxed repo-wide for every
# adapter that touches it.
from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

# Bootstrap: running this file directly (`python scripts/dhm_precip/acquire_era5.py`,
# per the Usage line above) puts this file's own directory at sys.path[0], not
# the repo root — `scripts.dhm_precip.*` would not resolve. Insert the repo
# root ahead of it so the package imports below work either way (direct
# script execution or `-m scripts.dhm_precip.acquire_era5`). Precedent:
# scripts/dhm_precip/run.py:44.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import structlog  # noqa: E402
import xarray as xr  # noqa: E402

from sapphire_flow.logging import configure_cli_logging  # noqa: E402
from scripts.dhm_precip.era5_acquire import (  # noqa: E402
    RealCdsClient,
    acquire_window,
    redact_secrets,
)
from scripts.dhm_precip.era5_deaccumulate import (  # noqa: E402
    DAY_START_HOUR,
    diagnose_accumulation_convention,
)
from scripts.dhm_precip.era5_errors import (  # noqa: E402
    Era5AcquisitionError,
    Era5CredentialsError,
    Era5RequestFailedError,
    Era5StorageError,
    Era5TransformFailedError,
    NonExpressibleWindowError,
)
from scripts.dhm_precip.era5_manifest import (  # noqa: E402
    DEFAULT_DATA_ROOT,
    load_operator_provenance,
    raw_artifact_path,
)
from scripts.dhm_precip.era5_request import (  # noqa: E402
    ALL_ACQUISITION_WINDOWS,
    DEFAULT_REQUEST_SPEC,
    STUDY_YEARS,
    parse_window_arg,
)
from scripts.dhm_precip.era5_transform import transform_year  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable

    from scripts.dhm_precip.era5_acquire import CdsClient
    from scripts.dhm_precip.era5_manifest import RawWindowRecord, TransformYearRecord
    from scripts.dhm_precip.era5_request import AcquisitionWindow

log = structlog.get_logger(__name__)

_EXIT_BY_ERROR: tuple[tuple[type[Era5AcquisitionError], int], ...] = (
    (Era5CredentialsError, 2),
    (Era5RequestFailedError, 3),
    (Era5TransformFailedError, 4),
    (Era5StorageError, 5),
)


def _exit_code_for(exc: Era5AcquisitionError) -> int:
    for exc_type, code in _EXIT_BY_ERROR:
        if isinstance(exc, exc_type):
            return code
    return 1


def _cdsapi_version() -> str:
    try:
        from importlib.metadata import version

        return version("cdsapi")
    except Exception:  # noqa: BLE001 - version metadata is best-effort only
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="acquire_era5", description=__doc__)
    parser.add_argument(
        "--stage",
        choices=("acquire", "transform", "diagnose", "all"),
        default="all",
        help="'diagnose' (3a) runs the accumulation-convention diagnostic "
        "against an already-acquired raw window and reports/records the "
        "observed convention against D6 — it is never bundled into 'all', "
        "since it is a one-off operator confirmation step (2b), not part of "
        "the regular acquire/transform pipeline.",
    )
    parser.add_argument(
        "--window",
        action="append",
        default=None,
        help="AcquisitionWindow spec (YYYY, YYYY-MM, YYYY-MM-DD, or "
        "YYYY-MM-DDTHH); repeatable. Defaults to the full D4 set of 8 "
        "windows / 6 study years. For '--stage transform', every resolved "
        "window's YEAR is transformed (D4: transform is year-granular) — "
        "an out-of-range year is rejected, not silently skipped.",
    )
    parser.add_argument(
        "--provenance",
        type=Path,
        required=True,
        help="Path to the gitignored D15 operator-provenance JSON file.",
    )
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    return parser


def run(
    args: argparse.Namespace,
    *,
    acquire_fn: Callable[..., RawWindowRecord] = acquire_window,
    transform_fn: Callable[..., TransformYearRecord] = transform_year,
    client: CdsClient | None = None,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> int:
    data_root: Path = args.data_root
    provenance = load_operator_provenance(args.provenance)

    windows: list[AcquisitionWindow] = (
        [parse_window_arg(w) for w in args.window]
        if args.window
        else list(ALL_ACQUISITION_WINDOWS)
    )
    resolved_client: CdsClient = client if client is not None else RealCdsClient()
    resolved_clock: Callable[[], datetime] = (
        clock if clock is not None else (lambda: datetime.now(UTC))
    )
    resolved_sleep: Callable[[float], None] = sleep if sleep is not None else time.sleep

    if args.stage in ("acquire", "all"):
        for window in windows:
            record = acquire_fn(
                window,
                spec=DEFAULT_REQUEST_SPEC,
                provenance=provenance,
                client=resolved_client,
                clock=resolved_clock,
                sleep=resolved_sleep,
                data_root=data_root,
                client_package_version=_cdsapi_version(),
            )
            log.info("era5.cli.acquired", window_id=record.window_id)

    if args.stage in ("transform", "all"):
        if args.stage == "all" and args.window and any(w.month for w in windows):
            # A review finding: `--stage all --window 2021-10` acquired ONE
            # month and then asked for a whole-2021 transform, which needs the
            # full year plus its neighbours. It would fail deep inside the
            # transform on missing artifacts, reported as a data problem rather
            # than as the usage error it is. `--stage all` is only coherent for
            # a year-granular window; the 2b sample is `--stage acquire`.
            raise NonExpressibleWindowError(
                f"--stage all requires a year-granular --window, but "
                f"{args.window!r} is sub-year. Acquisition and transform have "
                "different granularities (D4): acquire works on any window, "
                "transform is year-granular and additionally needs the "
                "neighbouring windows. Use --stage acquire for a sample "
                "window, then --stage transform on a whole year."
            )
        if args.window:
            # ANY window granularity contributes its year (not just a whole
            # year, D4's month=None shape) — a review finding showed a
            # sub-year window (e.g. "2021-10") previously fell through to
            # the "nothing requested" fallback and silently transformed ALL
            # SIX study years instead of just 2021.
            requested_years = sorted({w.year for w in windows})
            out_of_range = [y for y in requested_years if y not in STUDY_YEARS]
            if out_of_range:
                # Previously a silent no-op ("success", nothing transformed)
                # — an out-of-range year is a real usage error and must be
                # reported, not swallowed.
                raise NonExpressibleWindowError(
                    f"--window resolved to year(s) {out_of_range} outside "
                    f"the study range {STUDY_YEARS}; transform is "
                    "year-granular (D4) and only ever produces one of the "
                    "study years — use --stage acquire for "
                    "boundary-context-only windows"
                )
            years = requested_years
        else:
            years = list(STUDY_YEARS)
        for year in years:
            record = transform_fn(
                year, data_root=data_root, provenance=provenance, clock=resolved_clock
            )
            log.info("era5.cli.transformed", year=record.product_year)

    if args.stage == "diagnose":
        for window in windows:
            path = raw_artifact_path(window.window_id, data_root)
            if not path.exists():
                raise Era5StorageError(
                    f"no raw artifact for window {window.window_id!r} at "
                    f"{path}; run --stage acquire first"
                )
            with xr.open_dataset(path) as raw_ds:
                diagnostic = diagnose_accumulation_convention(raw_ds.load())
            if diagnostic.reset_hour != DAY_START_HOUR or not (
                diagnostic.monotone_within_day
            ):
                raise Era5TransformFailedError(
                    f"observed accumulation convention for window "
                    f"{window.window_id!r} disagrees with D6's assumed "
                    f"reset hour {DAY_START_HOUR}: observed "
                    f"reset_hour={diagnostic.reset_hour} "
                    f"monotone_within_day={diagnostic.monotone_within_day} — "
                    "the deaccumulation rule needs correcting (and the "
                    "correction recording in the plan) before any transform "
                    "is trusted"
                )
            log.info(
                "era5.diagnose.confirmed",
                window_id=window.window_id,
                reset_hour=diagnostic.reset_hour,
                terminal_hour=diagnostic.terminal_hour,
                monotone_within_day=diagnostic.monotone_within_day,
                sample_size_days=diagnostic.sample_size_days,
            )

    return 0


def main(
    argv: list[str] | None = None,
    *,
    client: CdsClient | None = None,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> int:
    """`client`/`clock`/`sleep` are test-only injection points (mirroring
    `run()`'s own) — production always leaves them `None`, which resolves to
    the same `RealCdsClient()`/real-clock/`time.sleep` `run()` always used.
    They exist so the credential-redaction contract can be exercised through
    the REAL CLI entry point end-to-end (a hostile client raising through
    `main()`), not just through `run()` with the exit-code mapping
    replicated by hand."""
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_cli_logging()
    try:
        return run(args, client=client, clock=clock, sleep=sleep)
    except Era5AcquisitionError as exc:
        # `_download_with_retry`/`RealCdsClient` already sanitize every
        # exception crossing the CdsClient seam (D12) before it reaches
        # here; this second `redact_secrets` pass is defense-in-depth, not
        # the only line of defense.
        log.error(
            "era5.cli.failed",
            error=redact_secrets(str(exc)),
            error_type=type(exc).__name__,
        )
        return _exit_code_for(exc)
    except OSError as exc:
        # A storage boundary neither `era5_manifest.py` nor the drivers
        # already wrap into `Era5StorageError` (e.g. an unexpected
        # permission failure elsewhere in the filesystem path) still exits 5
        # rather than crashing with a bare traceback (exit 1) — every
        # documented exit code in this module's docstring is reachable.
        log.error(
            "era5.cli.failed",
            error=redact_secrets(str(exc)),
            error_type=type(exc).__name__,
        )
        return _exit_code_for(Era5StorageError(str(exc)))


if __name__ == "__main__":
    sys.exit(main())
