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
    6  CDS refused the request as exceeding its per-request COST LIMIT (a
       field-count ceiling). Distinct from 2: the credentials are fine.
       Re-slice the window to monthly granularity (D4, corrected
       2026-08-17).
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
    Era5RequestTooLargeError,
    Era5StorageError,
    Era5TransformFailedError,
    NonExpressibleWindowError,
)
from scripts.dhm_precip.era5_manifest import (  # noqa: E402
    DEFAULT_DATA_ROOT,
    MIN_DIAGNOSTIC_SAMPLE_DAYS,
    AccumulationDiagnosticRecord,
    checksum_file,
    load_operator_provenance,
    manifest_path_for,
    raw_artifact_path,
    read_manifest,
    with_accumulation_diagnostic,
    write_manifest_atomic,
)
from scripts.dhm_precip.era5_request import (  # noqa: E402
    ALL_ACQUISITION_WINDOWS,
    DEFAULT_REQUEST_SPEC,
    STUDY_YEARS,
    expand_for_acquisition,
    parse_window_arg,
)
from scripts.dhm_precip.era5_transform import transform_year  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable

    from scripts.dhm_precip.era5_acquire import CdsClient
    from scripts.dhm_precip.era5_manifest import RawWindowRecord, TransformYearRecord
    from scripts.dhm_precip.era5_request import AcquisitionWindow

log = structlog.get_logger(__name__)

# SUBCLASSES BEFORE PARENTS — `_exit_code_for` returns the FIRST isinstance
# match, so a subclass listed after its parent can never be reached (a prior
# bug of exactly this shape had storage errors exiting 4 instead of 5).
# `Era5RequestTooLargeError` subclasses `Era5RequestFailedError` (it is a
# request rejection, and equally non-retryable) but carries its own exit
# code, so it must precede it here.
_EXIT_BY_ERROR: tuple[tuple[type[Era5AcquisitionError], int], ...] = (
    (Era5CredentialsError, 2),
    (Era5RequestTooLargeError, 6),
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
        "YYYY-MM-DDTHH); repeatable. Defaults to the full D4 set of 74 "
        "windows (72 monthly windows over the 6 study years, plus the 2 "
        "edge-context windows). For '--stage acquire', a year-granular "
        "window is EXPANDED into its 12 monthly windows — CDS refuses a "
        "whole-year payload as exceeding its cost limit (D4, corrected "
        "2026-08-17). For '--stage transform', every resolved window's "
        "YEAR is transformed (D4: transform is year-granular) — an "
        "out-of-range year is rejected, not silently skipped.",
    )
    parser.add_argument(
        "--provenance",
        type=Path,
        required=True,
        help="Path to the gitignored D15 operator-provenance JSON file.",
    )
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    return parser


def _approved_diagnostic_window(
    window_args: list[str] | None, windows: list[AcquisitionWindow]
) -> AcquisitionWindow:
    """M-7/B5 — `--stage diagnose` requires EXACTLY ONE explicitly named,
    approved window.

    Without `--window` the stage resolved the full D4 set of eight windows,
    which includes a single-DAY boundary window and a single-HOUR one; a
    one-hour sample cannot establish a daily accumulation convention, yet it
    used to be diagnosed and recorded like any other. An approved window is
    therefore a whole month or a whole year (`day is None`).
    """
    if not window_args or len(windows) != 1:
        raise NonExpressibleWindowError(
            "--stage diagnose requires exactly one explicit --window (the "
            "diagnostic is a publish gate, and the default window set "
            "includes boundary-context windows too short to diagnose); got "
            f"{window_args!r}"
        )
    window = windows[0]
    if window.day is not None:
        raise NonExpressibleWindowError(
            f"--stage diagnose needs a whole month or year window, but "
            f"{window.window_id!r} is day- or hour-granular and cannot "
            "establish a daily accumulation convention"
        )
    return window


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
    # Validate the invocation BEFORE doing any work. A review finding: this
    # guard originally sat in the transform block, which runs AFTER the
    # acquire loop — so an invalid `--stage all --window 2021-10` would hit
    # CDS, download a month, and only then report a usage error. Argument
    # validation must never cost a network round-trip.
    if args.stage == "all" and args.window and any(w.month for w in windows):
        # `--stage all --window 2021-10` acquires ONE month and then asks for
        # a whole-2021 transform, which needs the full year plus BOTH
        # neighbouring windows. Acquisition and transform have different
        # granularities (D4), so `--stage all` is only coherent for a
        # year-granular window; the 2b sample is `--stage acquire`.
        raise NonExpressibleWindowError(
            f"--stage all requires a year-granular --window, but "
            f"{args.window!r} is sub-year. Acquisition works on any window, "
            "but transform is year-granular and additionally needs the "
            "neighbouring windows (D4). Use --stage acquire for a sample "
            "window, then --stage transform on a whole year."
        )

    resolved_client: CdsClient = client if client is not None else RealCdsClient()
    resolved_clock: Callable[[], datetime] = (
        clock if clock is not None else (lambda: datetime.now(UTC))
    )
    resolved_sleep: Callable[[float], None] = sleep if sleep is not None else time.sleep

    if args.stage in ("acquire", "all"):
        # D4 (corrected 2026-08-17): the ACQUISITION unit is one calendar
        # month. A year-granular `--window` names a product year, not a
        # payload — expand it rather than sending 8,760 fields CDS will
        # refuse. The default set is already monthly, so this is a no-op
        # there.
        for window in expand_for_acquisition(windows):
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
        # B5 / M-7 (CORRECTED 2026-08-16) — the persisted diagnostic is a
        # GATE (M-A5 D5.2 refuses to publish without one), so the stage that
        # writes it must itself be trustworthy. Three corrections:
        #   1. exactly ONE explicit approved window (no `--window` used to
        #      resolve all eight D4 windows, including the one-hour boundary
        #      window the diagnostic must reject);
        #   2. the raw file is reconciled against the acquisition manifest's
        #      own sha256 BEFORE it is decoded as a dataset;
        #   3. a minimum whole-day sample is pinned.
        window = _approved_diagnostic_window(args.window, windows)
        manifest_path = manifest_path_for(data_root)
        manifest = read_manifest(manifest_path)
        if manifest is None:
            raise Era5StorageError(
                f"no acquisition manifest at {manifest_path}; the diagnostic "
                "is a publish gate and may only run against a window with "
                "recorded acquisition provenance — run --stage acquire first"
            )
        raw_record = manifest.raw_windows.get(window.window_id)
        if raw_record is None:
            raise Era5StorageError(
                f"the acquisition manifest carries no raw-window record for "
                f"{window.window_id!r}; the diagnostic may only run against "
                "a window this manifest actually acquired"
            )
        path = raw_artifact_path(window.window_id, data_root)
        if not path.exists():
            raise Era5StorageError(
                f"no raw artifact for window {window.window_id!r} at "
                f"{path}; run --stage acquire first"
            )
        # Reconcile BEFORE decoding (m-2: checksumming necessarily reads raw
        # bytes; the guarantee is that no DECODE of those bytes happens
        # first).
        observed_sha256 = checksum_file(path)
        if observed_sha256 != raw_record.sha256:
            raise Era5TransformFailedError(
                f"raw artifact for window {window.window_id!r} has sha256 "
                f"{observed_sha256}, but the acquisition manifest records "
                f"{raw_record.sha256} — refusing to decode it, and refusing "
                "to record a diagnostic against bytes of unknown origin"
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
        if diagnostic.sample_size_days < MIN_DIAGNOSTIC_SAMPLE_DAYS:
            raise Era5TransformFailedError(
                f"window {window.window_id!r} covers only "
                f"sample_size_days={diagnostic.sample_size_days} whole "
                f"accumulation days, below the pinned minimum of "
                f"{MIN_DIAGNOSTIC_SAMPLE_DAYS}; too short a sample cannot "
                "establish a daily accumulation convention"
            )
        log.info(
            "era5.diagnose.confirmed",
            window_id=window.window_id,
            reset_hour=diagnostic.reset_hour,
            terminal_hour=diagnostic.terminal_hour,
            monotone_within_day=diagnostic.monotone_within_day,
            sample_size_days=diagnostic.sample_size_days,
        )
        # Plan 174 (M-A5) task 1c / D5.2 — persist a PASSING diagnostic into
        # the acquisition manifest, atomically, with the injected clock,
        # keyed by window id (records are stored PER WINDOW). Only reached
        # once none of the raises above has fired, so a failing diagnostic
        # writes no record.
        record = AccumulationDiagnosticRecord(
            window_id=window.window_id,
            source_sha256=observed_sha256,
            reset_hour=diagnostic.reset_hour,
            terminal_hour=diagnostic.terminal_hour,
            monotone_within_day=diagnostic.monotone_within_day,
            sample_size_days=diagnostic.sample_size_days,
            recorded_at=resolved_clock(),
        )
        write_manifest_atomic(
            with_accumulation_diagnostic(manifest, record), manifest_path
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
