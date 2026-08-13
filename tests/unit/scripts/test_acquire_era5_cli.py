"""Plan 171 task 4a — the ERA5-Land acquisition CLI: exit-code contract and
the credential-redaction guarantee (constraint 2: never logged — tested, not
asserted).

Exit-code tests inject a typed fake failure at the `CdsClient` seam (D12) and
drive the real `run()` entry point end-to-end — this is the same seam 2a's
own tests use, so a regression in the real acquisition/transform drivers is
caught here too, not just in their own unit tests.

The redaction tests are two-part, per the plan's explicit requirement that a
fake-only test "proves nothing about the component most likely to leak":
  1. The REAL `cdsapi.Client` constructed OFFLINE — a real secret in
     `CDSAPI_KEY`/`~/.cdsapirc`, driven through `RealCdsClient`'s injectable
     `session` (an `_ExplodingSession` that fails every HTTP verb locally,
     with no socket ever opened) — proves the real component's own
     url/key/retry machinery never puts the secret into a raised exception
     or printed output.
  2. A HOSTILE fake client that deliberately raises a typed error with the
     secret embedded in its message — proves our own `redact_secrets` sweep
     (applied in `main()`'s except-block, independent of what the client did)
     scrubs it before it reaches a log line, even when the client itself
     leaks.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np
import pytest
import structlog.testing
import xarray as xr

from scripts.dhm_precip.acquire_era5 import _exit_code_for, build_parser, main, run
from scripts.dhm_precip.era5_acquire import RealCdsClient, redact_secrets
from scripts.dhm_precip.era5_errors import (
    Era5AcquisitionError,
    Era5CredentialsError,
    Era5TransientError,
)
from scripts.dhm_precip.era5_manifest import manifest_path_for, raw_artifact_path
from scripts.dhm_precip.era5_request import DEFAULT_REQUEST_SPEC, AcquisitionWindow

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_HOUR = np.timedelta64(1, "h")

_VALID_PROVENANCE = {
    "cds_portal_url": "https://cds.climate.copernicus.eu",
    "dataset_landing_page_url": (
        "https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land"
    ),
    "licence_name": "Licence to use Copernicus Products",
    "licence_version": "1.0",
    "licence_accepted_at": "2026-08-13T00:00:00Z",
}


def _write_provenance(tmp_path: Path) -> Path:
    path = tmp_path / "provenance.json"
    path.write_text(json.dumps(_VALID_PROVENANCE))
    return path


def _write_valid_raw_netcdf(path: Path, window: AcquisitionWindow) -> None:
    stamps = sorted(window.valid_time_stamps())
    times = np.array(
        [
            np.datetime64(f"{y:04d}-{m:02d}-{d:02d}T{h:02d}:00:00")
            for y, m, d, h in stamps
        ]
    )
    north, west, south, east = DEFAULT_REQUEST_SPEC.area
    lat = np.arange(south, north + 0.1, 0.1)
    lon = np.arange(west, east + 0.1, 0.1)
    tp = np.zeros((times.size, lat.size, lon.size), dtype=np.float32)
    ds = xr.Dataset(
        {"tp": (["valid_time", "latitude", "longitude"], tp)},
        coords={"valid_time": times, "latitude": lat, "longitude": lon},
    )
    ds["tp"].attrs["units"] = "m"
    ds.to_netcdf(path)


class _ScriptedClient:
    """A fake `CdsClient` (D12's seam) whose behaviour is scripted per call —
    the same style of fake 2a's own tests use, driven this time through the
    CLI's `run()` entry point."""

    def __init__(
        self,
        *,
        window: AcquisitionWindow,
        raise_sequence: list[Exception] | None = None,
    ) -> None:
        self.window = window
        self.raise_sequence = list(raise_sequence or [])
        self.call_count = 0

    def retrieve_to_path(
        self, *, dataset: str, payload: Mapping[str, object], target: Path
    ) -> None:
        self.call_count += 1
        if self.raise_sequence:
            raise self.raise_sequence.pop(0)
        _write_valid_raw_netcdf(target, self.window)


def _invoke(args: object, **overrides: object) -> int:
    """Mirrors `main()`'s try/except mapping (`run()` -> exit code) without
    calling `configure_cli_logging()` — tests that need real global logging
    state use `main()` directly instead."""
    try:
        return run(args, **overrides)  # type: ignore[arg-type]
    except Era5AcquisitionError as exc:
        return _exit_code_for(exc)


def _accumulator_from_true(valid_time: np.ndarray, true_m: np.ndarray) -> np.ndarray:
    days = valid_time.astype("datetime64[D]")
    hours = ((valid_time - days) / _HOUR).astype(int)
    acc = np.empty_like(true_m)
    running = np.zeros(true_m.shape[1:], dtype=true_m.dtype)
    for i in range(valid_time.size):
        running = true_m[i].copy() if hours[i] == 1 else running + true_m[i]
        acc[i] = running
    return acc


def _write_raw(path: Path, valid_time: np.ndarray, acc_m: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ds = xr.Dataset(
        {"tp": (["valid_time", "latitude", "longitude"], acc_m.astype(np.float32))},
        coords={
            "valid_time": valid_time,
            "latitude": np.array([26.0, 26.1]),
            "longitude": np.array([80.0, 80.1]),
        },
    )
    ds["tp"].attrs["units"] = "m"
    ds.to_netcdf(path)


def _seed_broken_2021(data_root: Path) -> None:
    """A material (far-beyond-tolerance) negative increment in 2021's raw
    file — the D7 packing post-condition must fail transform_year(2021)."""
    from scripts.dhm_precip.era5_manifest import (
        Era5ProvenanceManifest,
        OperatorProvenance,
        RawWindowRecord,
        checksum_file,
        with_raw_window,
        write_manifest_atomic,
    )

    series_start = np.datetime64("2020-12-31T01:00")
    series_end = np.datetime64("2022-01-01T00:00")
    hours = int((series_end - series_start) / _HOUR) + 1
    valid_time = series_start + np.arange(hours) * _HOUR
    rng = np.random.default_rng(0)
    true_mm_1d = rng.uniform(0.1, 2.0, size=hours)
    true_mm = np.broadcast_to(true_mm_1d[:, None, None], (hours, 2, 2)).astype(
        np.float64
    )
    acc_m = _accumulator_from_true(valid_time, true_mm / 1000.0)
    acc_m[100] -= 1.0  # a full metre: far beyond the packing tolerance

    prev_ts = np.datetime64("2020-12-31T23:00")
    year_start_ts = np.datetime64("2021-01-01T00:00")
    year_end_ts = np.datetime64("2021-12-31T23:00")
    next_ts = np.datetime64("2022-01-01T00:00")
    prev_idx = int(np.where(valid_time == prev_ts)[0][0])
    year_start_idx = int(np.where(valid_time == year_start_ts)[0][0])
    year_end_idx = int(np.where(valid_time == year_end_ts)[0][0])
    next_idx = int(np.where(valid_time == next_ts)[0][0])

    _write_raw(
        raw_artifact_path("2021", data_root),
        valid_time[year_start_idx : year_end_idx + 1],
        acc_m[year_start_idx : year_end_idx + 1],
    )
    _write_raw(
        raw_artifact_path("2020", data_root), valid_time[[prev_idx]], acc_m[[prev_idx]]
    )
    _write_raw(
        raw_artifact_path("2022", data_root), valid_time[[next_idx]], acc_m[[next_idx]]
    )

    provenance_kwargs = {
        **_VALID_PROVENANCE,
        "licence_accepted_at": datetime(2026, 8, 13, tzinfo=UTC),
    }
    manifest = Era5ProvenanceManifest(
        dataset="reanalysis-era5-land",
        client_package_version="0.7.7",
        operator_provenance=OperatorProvenance(**provenance_kwargs),
    )
    for window_id in ("2020", "2021", "2022"):
        path = raw_artifact_path(window_id, data_root)
        record = RawWindowRecord(
            window_id=window_id,
            dataset="reanalysis-era5-land",
            request_payload={"year": window_id},
            raw_request_identity=f"identity-{window_id}",
            sha256=checksum_file(path),
            client_package_version="0.7.7",
            downloaded_at=datetime(2026, 8, 13, tzinfo=UTC),
        )
        manifest = with_raw_window(manifest, record)
    write_manifest_atomic(manifest, manifest_path_for(data_root))


class TestExitCodeContract:
    def test_success_is_exit_0(self, tmp_path: Path) -> None:
        provenance = _write_provenance(tmp_path)
        window = AcquisitionWindow(year=2019, month=12, day=31)
        args = build_parser().parse_args(
            [
                "--stage",
                "acquire",
                "--window",
                "2019-12-31",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        client = _ScriptedClient(window=window)
        code = _invoke(args, client=client, sleep=lambda _s: None)
        assert code == 0
        assert client.call_count == 1

    def test_credentials_error_is_exit_2(self, tmp_path: Path) -> None:
        provenance = _write_provenance(tmp_path)
        window = AcquisitionWindow(year=2019, month=12, day=31)
        args = build_parser().parse_args(
            [
                "--stage",
                "acquire",
                "--window",
                "2019-12-31",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        client = _ScriptedClient(
            window=window,
            raise_sequence=[Era5CredentialsError("unauthorized")],
        )
        code = _invoke(args, client=client, sleep=lambda _s: None)
        assert code == 2

    def test_exhausted_retries_is_exit_3(self, tmp_path: Path) -> None:
        provenance = _write_provenance(tmp_path)
        window = AcquisitionWindow(year=2019, month=12, day=31)
        args = build_parser().parse_args(
            [
                "--stage",
                "acquire",
                "--window",
                "2019-12-31",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        n_attempts = DEFAULT_REQUEST_SPEC.max_retry_attempts
        client = _ScriptedClient(
            window=window,
            raise_sequence=[Era5TransientError("timeout") for _ in range(n_attempts)],
        )
        code = _invoke(args, client=client, sleep=lambda _s: None)
        assert code == 3

    def test_transform_post_condition_failure_is_exit_4(self, tmp_path: Path) -> None:
        provenance = _write_provenance(tmp_path)
        _seed_broken_2021(tmp_path)
        args = build_parser().parse_args(
            [
                "--stage",
                "transform",
                "--window",
                "2021",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        code = _invoke(args)
        assert code == 4

    def test_missing_provenance_file_is_exit_5(self, tmp_path: Path) -> None:
        args = build_parser().parse_args(
            [
                "--stage",
                "acquire",
                "--window",
                "2019-12-31",
                "--provenance",
                str(tmp_path / "does-not-exist.json"),
                "--data-root",
                str(tmp_path),
            ]
        )
        code = _invoke(args)
        assert code == 5


class TestCliHelpSmoke:
    def test_help_exits_0(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main(["--help"])
        assert excinfo.value.code == 0


class _ExplodingSession:
    """A fully offline stand-in for `requests.Session`: every HTTP verb
    fails locally and instantly (a plain `RuntimeError`, not a
    `requests.exceptions.ConnectionError`/`ReadTimeout` — those two are the
    only types `cdsapi.Client.robust()` retries-with-real-sleep on, so a
    generic exception type is what keeps this test instant). No network
    socket is ever opened. `cdsapi.Client.__init__` sets `.auth`/`.headers`
    on whatever `session=` it is given, so both must be plain assignable
    attributes."""

    auth: object = None
    headers: dict[str, str] = {}

    def get(self, *_a: object, **_k: object) -> object:
        raise RuntimeError("simulated offline failure (GET)")

    def post(self, *_a: object, **_k: object) -> object:
        raise RuntimeError("simulated offline failure (POST)")

    put = post


class TestRealClientOfflineDoesNotLeak:
    """Part 1 of the redaction contract: the REAL `cdsapi.Client` — url/key
    handling, its `_status` pre-check, its `robust()` retry wrapper — driven
    with an actual secret in `CDSAPI_KEY` and an offline (`_ExplodingSession`)
    transport, never puts that secret into the exception it raises or
    anything printed. `RealCdsClient`'s `session` field exists solely to make
    this test possible (era5_acquire.py docstring) — production never sets
    it, so `cdsapi.Client()` is still called exactly as before there.

    A colon-containing key is used deliberately: `cdsapi.Client.__new__`
    treats a colon-free key as belonging to a DIFFERENT (legacy) client class
    that polls the network at construction time — incompatible with an
    offline test. Format validity, not correctness, is what routes to the
    modern client under test; CDS itself would reject the value, but nothing
    in this test ever reaches CDS.
    """

    def test_offline_transport_failure_carries_no_secret(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        sentinel = "SENTINEL-9f3ac2-do-not-leak"
        monkeypatch.setenv("CDSAPI_URL", "https://cds.climate.copernicus.eu/api")
        monkeypatch.setenv("CDSAPI_KEY", f"uid-1234:{sentinel}")

        client = RealCdsClient(session=_ExplodingSession())  # type: ignore[arg-type]
        with pytest.raises(Era5AcquisitionError) as excinfo:
            client.retrieve_to_path(
                dataset="reanalysis-era5-land",
                payload={"variable": ["total_precipitation"]},
                target=tmp_path / "unused.nc",
            )

        exc_str = str(excinfo.value)
        assert sentinel not in exc_str
        assert sentinel not in redact_secrets(exc_str)
        captured = capsys.readouterr()
        assert sentinel not in captured.out
        assert sentinel not in captured.err

    def test_dotcdsapirc_secret_offline_transport_failure_carries_no_secret(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        sentinel = "SENTINEL-dotrc-77aa"
        monkeypatch.delenv("CDSAPI_URL", raising=False)
        monkeypatch.delenv("CDSAPI_KEY", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".cdsapirc").write_text(
            f"url: https://cds.climate.copernicus.eu/api\nkey: uid-5678:{sentinel}\n"
        )

        client = RealCdsClient(session=_ExplodingSession())  # type: ignore[arg-type]
        with pytest.raises(Era5AcquisitionError) as excinfo:
            client.retrieve_to_path(
                dataset="reanalysis-era5-land",
                payload={"variable": ["total_precipitation"]},
                target=tmp_path / "unused.nc",
            )
        assert sentinel not in str(excinfo.value)
        assert sentinel not in redact_secrets(str(excinfo.value))


class TestHostileFakeClientRedaction:
    """Part 2 of the redaction contract: even when a client's raw exception
    DOES embed the secret (proving this fake has teeth — unlike the real
    client above), `redact_secrets` scrubs it before it would reach a log
    line, and the written manifest never carries it either."""

    def test_hostile_message_is_scrubbed_before_logging(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        sentinel = "SENTINEL-hostile-leak-42"
        monkeypatch.setenv("CDSAPI_KEY", sentinel)  # picked up by _known_secret_values

        class _HostileClient:
            def retrieve_to_path(
                self, *, dataset: str, payload: Mapping[str, object], target: Path
            ) -> None:
                raise Era5CredentialsError(f"unauthorized: token={sentinel}")

        provenance = _write_provenance(tmp_path)
        args = build_parser().parse_args(
            [
                "--stage",
                "acquire",
                "--window",
                "2019-12-31",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )

        with pytest.raises(Era5AcquisitionError) as excinfo:
            run(args, client=_HostileClient(), sleep=lambda _s: None)
        # The fake has teeth: the raw exception really does carry the secret.
        assert sentinel in str(excinfo.value)
        assert _exit_code_for(excinfo.value) == 2

        # main()'s except-block logs `redact_secrets(str(exc))` — replicate
        # that exact call under structlog's test capture, independent of
        # global stdlib logging config (avoids cross-test pollution from
        # `configure_cli_logging()`).
        log = structlog.get_logger("scripts.dhm_precip.acquire_era5")
        with structlog.testing.capture_logs() as logs:
            log.error(
                "era5.cli.failed",
                error=redact_secrets(str(excinfo.value)),
                error_type=type(excinfo.value).__name__,
            )
        assert len(logs) == 1
        assert sentinel not in json.dumps(logs[0], default=str)

    def test_hostile_failure_leaves_no_secret_in_manifest(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        sentinel = "SENTINEL-manifest-99"
        monkeypatch.setenv("CDSAPI_KEY", sentinel)
        provenance = _write_provenance(tmp_path)
        window = AcquisitionWindow(year=2019, month=12, day=31)
        args = build_parser().parse_args(
            [
                "--stage",
                "acquire",
                "--window",
                "2019-12-31",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        client = _ScriptedClient(window=window)
        code = _invoke(args, client=client, sleep=lambda _s: None)
        assert code == 0

        manifest_text = manifest_path_for(tmp_path).read_text()
        assert sentinel not in manifest_text
