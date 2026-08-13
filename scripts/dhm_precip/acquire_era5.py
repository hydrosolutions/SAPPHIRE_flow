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

from sapphire_flow.logging import configure_cli_logging  # noqa: E402
from scripts.dhm_precip.era5_acquire import (  # noqa: E402
    RealCdsClient,
    acquire_window,
    redact_secrets,
)
from scripts.dhm_precip.era5_errors import (  # noqa: E402
    Era5AcquisitionError,
    Era5CredentialsError,
    Era5RequestFailedError,
    Era5StorageError,
    Era5TransformFailedError,
)
from scripts.dhm_precip.era5_manifest import (  # noqa: E402
    DEFAULT_DATA_ROOT,
    load_operator_provenance,
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
        "--stage", choices=("acquire", "transform", "all"), default="all"
    )
    parser.add_argument(
        "--window",
        action="append",
        default=None,
        help="AcquisitionWindow spec (YYYY, YYYY-MM, YYYY-MM-DD, or "
        "YYYY-MM-DDTHH); repeatable. Defaults to the full D4 set of 8 "
        "windows / 6 study years.",
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
        requested_years = {w.year for w in windows if w.month is None}
        years = sorted(requested_years) if requested_years else list(STUDY_YEARS)
        for year in years:
            if year not in STUDY_YEARS:
                continue
            record = transform_fn(
                year, data_root=data_root, provenance=provenance, clock=resolved_clock
            )
            log.info("era5.cli.transformed", year=record.product_year)

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_cli_logging()
    try:
        return run(args)
    except Era5AcquisitionError as exc:
        log.error(
            "era5.cli.failed",
            error=redact_secrets(str(exc)),
            error_type=type(exc).__name__,
        )
        return _exit_code_for(exc)


if __name__ == "__main__":
    sys.exit(main())
