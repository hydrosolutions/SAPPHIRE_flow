"""M-A4 (Plan 171) task 2a — the raw acquisition driver. Per window: build
payload -> CDS call (injected) -> download to a temp path -> reopen and
validate -> checksum -> `os.replace` -> atomic manifest update. No
transformation whatsoever (D3).
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# Precedent: src/sapphire_flow/adapters/meteoswiss_nwp.py:1 — xarray/cdsapi
# ship partial type stubs; the same three rules are relaxed repo-wide for
# every adapter that touches them.
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import structlog
import xarray as xr

from scripts.dhm_precip.era5_errors import (
    Era5CredentialsError,
    Era5RequestFailedError,
    Era5TransientError,
    Era5ValidationError,
)
from scripts.dhm_precip.era5_manifest import (
    Era5ProvenanceManifest,
    OperatorProvenance,
    RawWindowRecord,
    checksum_file,
    manifest_path_for,
    publish_atomic,
    raw_artifact_path,
    raw_dir,
    raw_request_identity,
    raw_window_is_current,
    read_manifest,
    tmp_path_for,
    with_raw_window,
    write_manifest_atomic,
)
from scripts.dhm_precip.era5_request import (
    AcquisitionWindow,
    Era5RequestSpec,
    build_request_payload,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    import numpy as np
    import requests

log = structlog.get_logger(__name__)


@runtime_checkable
class CdsClient(Protocol):
    """The single injected CDS-call seam (D12) — the client/auth/request
    layer is CDS-specific by construction; fake implementations satisfy this
    Protocol for tests."""

    def retrieve_to_path(
        self, *, dataset: str, payload: Mapping[str, object], target: Path
    ) -> None: ...


_AUTH_TOKENS = (
    "unauthorized",
    "invalid credentials",
    "401",
    "403",
    "apikey",
    "api key",
)
_REJECTED_TOKENS = (
    "licence",
    "license",
    "not accepted",
    "malformed",
    "invalid request",
    "400",
)
_TRANSIENT_TOKENS = (
    "timeout",
    "timed out",
    "connection",
    "queue",
    "temporarily",
    "50",
    "503",
)

_SECRET_ENV_TOKENS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PEPPER")


def _known_secret_values() -> list[str]:
    """Every credential-shaped value currently reachable from the
    environment/`~/.cdsapirc` (constraint 2: credentials are never logged —
    tested, not asserted). Computed fresh on every call, never cached, so a
    test's env/HOME changes are always picked up."""
    values = [
        value
        for key, value in os.environ.items()
        if value and any(token in key.upper() for token in _SECRET_ENV_TOKENS)
    ]
    cdsapirc = Path.home() / ".cdsapirc"
    if cdsapirc.exists():
        for line in cdsapirc.read_text().splitlines():
            if ":" in line:
                _key, _, value = line.partition(":")
                value = value.strip()
                if value:
                    values.append(value)
    return values


def redact_secrets(text: str) -> str:
    """Scrub every currently-known secret value out of `text`."""
    redacted = text
    for secret in _known_secret_values():
        if secret:
            redacted = redacted.replace(secret, "***REDACTED***")
    return redacted


def classify_cds_exception(
    exc: Exception,
) -> Era5CredentialsError | Era5RequestFailedError | Era5TransientError:
    """Best-effort classification of a raw client exception into the retry
    contract's three buckets (2a). Untested against the live CDS API by
    constraint 5 — treated conservatively as non-retryable when unrecognised,
    so an unknown failure mode never causes an infinite retry loop. The
    raised error's message is always redacted (constraint 2)."""
    raw = str(exc)
    message = raw.lower()
    safe = redact_secrets(raw)
    if any(token in message for token in _AUTH_TOKENS):
        return Era5CredentialsError(f"CDS authentication failed: {safe}")
    if any(token in message for token in _REJECTED_TOKENS):
        return Era5RequestFailedError(f"CDS rejected the request: {safe}")
    if any(token in message for token in _TRANSIENT_TOKENS):
        return Era5TransientError(f"transient CDS/transport failure: {safe}")
    return Era5RequestFailedError(f"unclassified CDS failure: {safe}")


@dataclass(frozen=True, kw_only=True, slots=True)
class RealCdsClient:
    """Thin wrapper over `cdsapi.Client()`. Constructed and exception-mapped
    here so the retry/validation driver never imports `cdsapi` directly.

    `session` is test-only (4a's credential-redaction contract, D12): it lets
    a test construct the REAL `cdsapi.Client` fully offline by injecting a
    session whose HTTP verbs fail locally instead of touching the network —
    `cdsapi.Client.__init__` never calls the session itself, but `.retrieve()`
    does, so this is the only seam that can exercise the real client's own
    error-handling/redaction path without a live CDS connection. Production
    never sets it: `None` reproduces the exact `cdsapi.Client()` call this
    class always made."""

    session: requests.Session | None = None

    def retrieve_to_path(
        self, *, dataset: str, payload: Mapping[str, object], target: Path
    ) -> None:
        import cdsapi  # dev-only (D13); imported lazily so this module loads

        try:
            client = (
                cdsapi.Client(session=self.session)
                if self.session is not None
                else cdsapi.Client()
            )
        except Exception as exc:  # noqa: BLE001 - reclassified into our typed hierarchy
            raise classify_cds_exception(exc) from exc
        try:
            client.retrieve(dataset, dict(payload)).download(str(target))
        except Exception as exc:  # noqa: BLE001 - reclassified into our typed hierarchy
            raise classify_cds_exception(exc) from exc


def _download_with_retry(
    *,
    client: CdsClient,
    dataset: str,
    payload: Mapping[str, object],
    target: Path,
    max_attempts: int,
    backoff_base_seconds: float,
    sleep: Callable[[float], None],
) -> None:
    last_exc: Era5TransientError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            client.retrieve_to_path(dataset=dataset, payload=payload, target=target)
            return
        except Era5TransientError as exc:
            last_exc = exc
            target.unlink(missing_ok=True)
            log.warning(
                "era5.acquire.transient_retry",
                attempt=attempt,
                max_attempts=max_attempts,
                error=str(exc),
            )
            if attempt < max_attempts:
                sleep(backoff_base_seconds * (2 ** (attempt - 1)))
        except (Era5CredentialsError, Era5RequestFailedError):
            target.unlink(missing_ok=True)
            raise
    target.unlink(missing_ok=True)
    raise Era5RequestFailedError(
        f"exhausted {max_attempts} attempts for dataset={dataset}"
    ) from last_exc


def _validate_raw_artifact(
    path: Path, *, window: AcquisitionWindow, spec: Era5RequestSpec
) -> None:
    """Precedent: `meteoswiss_open_data_reanalysis.py:911` rejects unparsable
    payloads and missing variables the same way. A stable checksum is not
    validation (2a) — this is what makes a checkpoint trustworthy."""
    try:
        with xr.open_dataset(path) as ds:
            _validate_variable(ds, window=window, spec=spec)
    except Era5ValidationError:
        raise
    except Exception as exc:  # noqa: BLE001 - any parse failure is a validation failure
        raise Era5ValidationError(
            f"raw artifact at {path} failed to open: {exc}"
        ) from exc


def _validate_variable(
    ds: xr.Dataset, *, window: AcquisitionWindow, spec: Era5RequestSpec
) -> None:
    if "tp" not in ds:
        raise Era5ValidationError(
            f"expected variable 'tp' absent; got {list(ds.data_vars)}"
        )
    units = str(ds["tp"].attrs.get("units", "")).strip().lower()
    if units not in ("m", "metres", "meters"):
        raise Era5ValidationError(f"'tp' units attribute {units!r} is not metres")
    for coord in ("valid_time", "latitude", "longitude"):
        if coord not in ds.coords and coord not in ds.dims:
            raise Era5ValidationError(f"missing coordinate {coord!r}")
    north, west, south, east = spec.area
    tol = 0.2
    lat = ds["latitude"].values
    lon = ds["longitude"].values
    if lat.size == 0 or lon.size == 0:
        raise Era5ValidationError("empty spatial coordinates")
    if lat.max() > north + tol or lat.min() < south - tol:
        raise Era5ValidationError(
            f"latitude range {lat.min()}..{lat.max()} outside requested box"
        )
    if lon.max() > east + tol or lon.min() < west - tol:
        raise Era5ValidationError(
            f"longitude range {lon.min()}..{lon.max()} outside requested box"
        )

    expected = window.valid_time_stamps()
    valid_time = ds["valid_time"].values
    observed = {
        (int(ts.astype("datetime64[Y]").astype(int)) + 1970, *_month_day_hour(ts))
        for ts in valid_time
    }
    if frozenset(observed) != expected:
        raise Era5ValidationError(
            f"temporal coverage mismatch: {len(observed)} observed stamps vs "
            f"{len(expected)} expected for window {window.window_id}"
        )


def _month_day_hour(ts: np.datetime64) -> tuple[int, int, int]:
    py = ts.astype("datetime64[s]").item()
    return (py.month, py.day, py.hour)


def acquire_window(
    window: AcquisitionWindow,
    *,
    spec: Era5RequestSpec,
    provenance: OperatorProvenance,
    client: CdsClient,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
    data_root: Path,
    client_package_version: str,
) -> RawWindowRecord:
    payload = build_request_payload(window, spec)
    identity = raw_request_identity(spec.dataset, payload)
    final_path = raw_artifact_path(window.window_id, data_root)
    manifest_path = manifest_path_for(data_root)

    manifest = read_manifest(manifest_path)
    if manifest is None:
        manifest = Era5ProvenanceManifest(
            dataset=spec.dataset,
            client_package_version=client_package_version,
            operator_provenance=provenance,
        )

    if raw_window_is_current(
        manifest,
        window_id=window.window_id,
        expected_identity=identity,
        final_path=final_path,
    ):
        log.info("era5.acquire.skip_resume", window_id=window.window_id)
        existing = manifest.raw_windows[window.window_id]
        return existing

    raw_dir(data_root).mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_path_for(final_path)
    tmp_path.unlink(missing_ok=True)
    try:
        _download_with_retry(
            client=client,
            dataset=spec.dataset,
            payload=payload,
            target=tmp_path,
            max_attempts=spec.max_retry_attempts,
            backoff_base_seconds=spec.retry_backoff_base_seconds,
            sleep=sleep,
        )
        _validate_raw_artifact(tmp_path, window=window, spec=spec)
        sha256 = checksum_file(tmp_path)
        publish_atomic(tmp_path, final_path)
    finally:
        # A validation failure raises out of the try body above without
        # publishing; make sure no half-written temp file is left around
        # (the final path itself is only ever touched by publish_atomic).
        if tmp_path.exists():
            os.remove(tmp_path)

    record = RawWindowRecord(
        window_id=window.window_id,
        dataset=spec.dataset,
        request_payload=payload,
        raw_request_identity=identity,
        sha256=sha256,
        client_package_version=client_package_version,
        downloaded_at=clock(),
    )
    updated_manifest = with_raw_window(manifest, record)
    write_manifest_atomic(updated_manifest, manifest_path)
    log.info("era5.acquire.complete", window_id=window.window_id, sha256=sha256)
    return record
