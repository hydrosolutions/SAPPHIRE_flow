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

import numpy as np
import structlog
import xarray as xr

from scripts.dhm_precip.era5_errors import (
    Era5AcquisitionError,
    Era5CredentialsError,
    Era5RequestFailedError,
    Era5StorageError,
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
    GRID_SPACING_DEG,
    AcquisitionWindow,
    Era5RequestSpec,
    build_request_payload,
    expected_grid_shape,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

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


def _with_colon_split_parts(values: list[str]) -> list[str]:
    """A modern CDS key is `uid:secret` — the CLASSIC (colon-free) client
    format is just `secret`. Some error paths interpolate only the trailing
    part (or only the leading part) rather than the whole `uid:secret`
    string, so both halves must be independently redactable, not just the
    combined value."""
    expanded = list(values)
    for value in values:
        if ":" in value:
            expanded.extend(part for part in value.split(":", 1) if part)
    return expanded


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
    # Longest first: a shorter secret that happens to be a substring of a
    # longer one (e.g. the split-out "secret" half of "uid:secret") must not
    # get redacted in a way that leaves the longer original partially intact
    # for a later, shorter-first pass to miss.
    return sorted(set(_with_colon_split_parts(values)), key=len, reverse=True)


def redact_secrets(text: str) -> str:
    """Scrub every currently-known secret value out of `text`."""
    redacted = text
    for secret in _known_secret_values():
        if secret:
            redacted = redacted.replace(secret, "***REDACTED***")
    return redacted


def _sanitize_era5_error(exc: Era5AcquisitionError) -> Era5AcquisitionError:
    """Reconstruct `exc` with a redacted message. The `CdsClient` Protocol
    (D12) can be satisfied by ANY implementation — not just `RealCdsClient`,
    which redacts its own messages via `classify_cds_exception` — so every
    exception crossing the seam must be sanitized HERE, at the boundary,
    rather than trusting the client to have done it. This is what makes the
    'never log or raise credentials' contract hold even against a hostile or
    merely careless client."""
    return type(exc)(redact_secrets(str(exc)))


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

        # `retry_max=1`: cdsapi's own `robust()` wrapper and download-resume
        # loop otherwise retry up to the library default of 500 times with
        # real (uninjectable) `time.sleep(sleep_max)` — up to 120s each —
        # BEFORE this method ever raises. That silently defeats
        # `_download_with_retry`'s bounded, injected-sleep retry contract:
        # a single "attempt" from the outer driver's perspective could block
        # for hours. `retry_max=1` makes the library try exactly once and
        # raise immediately on failure, leaving OUR outer loop as the sole
        # retry owner (`sleep_max` is left at its default: it only bounds the
        # unrelated queued/running status-poll interval for a job that is
        # genuinely still in progress, not error retries).
        try:
            client = (
                cdsapi.Client(session=self.session, retry_max=1)
                if self.session is not None
                else cdsapi.Client(retry_max=1)
            )
        except Exception as exc:  # noqa: BLE001 - construction only fails on missing/invalid config
            # Anything raised by `cdsapi.Client()` itself (before any network
            # call) is a configuration/credentials problem by construction —
            # classify it as such directly rather than through the generic
            # keyword-token classifier, whose fallback bucket (unclassified
            # -> Era5RequestFailedError, exit 3) would otherwise misclassify
            # a plain missing `~/.cdsapirc` as a "request failure".
            raise Era5CredentialsError(
                f"CDS client configuration failed: {redact_secrets(str(exc))}"
            ) from exc
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
    # Every exception the `CdsClient` seam (D12) can produce is sanitized
    # HERE before it is logged or re-raised — not just the ones `RealCdsClient`
    # happens to have already redacted — so the seam's contract holds
    # regardless of which conforming (or hostile) implementation is behind
    # it. A raw `Exception` of a type outside our own hierarchy is mapped to
    # the non-retryable `Era5RequestFailedError` bucket, per the plan's
    # "third-party failures are mapped to generic typed messages rather than
    # propagated verbatim" — an unrecognised failure mode must never be
    # retried indefinitely.
    last_exc: Era5TransientError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            client.retrieve_to_path(dataset=dataset, payload=payload, target=target)
            return
        except Era5TransientError as exc:
            sanitized = _sanitize_era5_error(exc)
            assert isinstance(sanitized, Era5TransientError)  # noqa: S101 - type preserved by _sanitize_era5_error
            last_exc = sanitized
            target.unlink(missing_ok=True)
            log.warning(
                "era5.acquire.transient_retry",
                attempt=attempt,
                max_attempts=max_attempts,
                error=str(sanitized),
            )
            if attempt < max_attempts:
                sleep(backoff_base_seconds * (2 ** (attempt - 1)))
        except (Era5CredentialsError, Era5RequestFailedError) as exc:
            target.unlink(missing_ok=True)
            raise _sanitize_era5_error(exc) from exc
        except Era5AcquisitionError as exc:
            # Any other typed ERA5 error the client might raise: sanitize
            # and propagate as-is rather than assuming it is retryable.
            target.unlink(missing_ok=True)
            raise _sanitize_era5_error(exc) from exc
        except Exception as exc:  # noqa: BLE001 - unclassified client failure, mapped + sanitized, non-retryable
            target.unlink(missing_ok=True)
            raise Era5RequestFailedError(
                f"unclassified client failure: {redact_secrets(str(exc))}"
            ) from exc
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
    grid_tol = 1e-6
    lat = ds["latitude"].values
    lon = ds["longitude"].values
    if lat.size == 0 or lon.size == 0:
        raise Era5ValidationError("empty spatial coordinates")
    expected_lat_count, expected_lon_count = expected_grid_shape(spec.area)
    if lat.size != expected_lat_count or lon.size != expected_lon_count:
        raise Era5ValidationError(
            f"spatial grid is {lat.size}x{lon.size}, expected exactly "
            f"{expected_lat_count}x{expected_lon_count} at {GRID_SPACING_DEG} "
            f"deg spacing for box {spec.area} — a subset grid is not the "
            "full requested product"
        )
    if lat.size > 1 and not np.allclose(
        np.abs(np.diff(np.sort(lat))), GRID_SPACING_DEG, atol=grid_tol
    ):
        raise Era5ValidationError("latitude spacing is not uniformly 0.1 deg")
    if lon.size > 1 and not np.allclose(
        np.abs(np.diff(np.sort(lon))), GRID_SPACING_DEG, atol=grid_tol
    ):
        raise Era5ValidationError("longitude spacing is not uniformly 0.1 deg")
    if lat.max() > north + grid_tol or lat.min() < south - grid_tol:
        raise Era5ValidationError(
            f"latitude range {lat.min()}..{lat.max()} outside requested box"
        )
    if lon.max() > east + grid_tol or lon.min() < west - grid_tol:
        raise Era5ValidationError(
            f"longitude range {lon.min()}..{lon.max()} outside requested box"
        )

    expected = window.valid_time_stamps()
    valid_time = ds["valid_time"].values
    if valid_time.size != len({np.datetime64(ts).item() for ts in valid_time}):
        raise Era5ValidationError(
            f"valid_time contains duplicate stamps: {valid_time.size} entries "
            "but fewer distinct timestamps"
        )
    observed = {
        (int(ts.astype("datetime64[Y]").astype(int)) + 1970, *_month_day_hour(ts))
        for ts in valid_time
    }
    if valid_time.size != len(expected) or frozenset(observed) != expected:
        raise Era5ValidationError(
            f"temporal coverage mismatch: {valid_time.size} observed stamps "
            f"({len(observed)} distinct) vs {len(expected)} expected for "
            f"window {window.window_id}"
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
    elif manifest.dataset != spec.dataset:
        # Acquisition-wide fields are IMMUTABLE. Reusing a manifest under a
        # different dataset would mix raw windows from two CDS products under
        # one provenance record, and the transform stage would then label the
        # final file with whichever `dataset` happened to sit at the top level
        # — a mislabelled product that no per-file checksum would catch.
        raise Era5StorageError(
            f"manifest at {manifest_path} records dataset "
            f"{manifest.dataset!r}, but this request is for "
            f"{spec.dataset!r}; acquisition-wide fields are immutable — "
            f"start a new data root rather than mixing products"
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
