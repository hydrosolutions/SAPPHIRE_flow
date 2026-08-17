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
from scripts.dhm_precip.era5_acquire import (
    RealCdsClient,
    classify_cds_exception,
    redact_secrets,
)
from scripts.dhm_precip.era5_errors import (
    Era5AcquisitionError,
    Era5CredentialsError,
    Era5PackingPostConditionError,
    Era5TransformFailedError,
    Era5TransientError,
    NonExpressibleWindowError,
)
from scripts.dhm_precip.era5_manifest import (
    PackingAccounting,
    RawWindowRecord,
    TransformYearRecord,
    manifest_path_for,
    raw_artifact_path,
)
from scripts.dhm_precip.era5_request import (
    DEFAULT_REQUEST_SPEC,
    AcquisitionWindow,
    expected_grid_shape,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_HOUR = np.timedelta64(1, "h")

# The VERBATIM body observed on the first real task-4b attempt (2026-08-17).
_OBSERVED_COST_LIMIT_BODY = (
    "403 Client Error: Forbidden for url: "
    "https://cds.climate.copernicus.eu/api/retrieve/v1/processes/"
    "reanalysis-era5-land/execution\n"
    "cost limits exceeded\n"
    "Your request is too large, please reduce your selection."
)

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
    # An EXACT point count (`expected_grid_shape`) via `np.linspace`, not
    # `np.arange` with a float step — `np.arange`'s float accumulation is
    # not guaranteed to land on exactly the expected count, which would
    # make a genuinely valid fixture fail the exact-grid-shape check the
    # raw validator now enforces.
    lat_count, lon_count = expected_grid_shape(DEFAULT_REQUEST_SPEC.area)
    lat = np.linspace(south, north, lat_count)
    lon = np.linspace(west, east, lon_count)
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

    # D4 (corrected 2026-08-17): the product year is twelve MONTHLY raw
    # artifacts, and the boundary context is December 2020 / January 2022.
    # Seeding the old yearly shape would make this fixture fail on missing
    # artifacts (also exit 4) instead of on the D7 packing post-condition it
    # exists to exercise.
    prev_ts = np.datetime64("2020-12-31T23:00")
    next_ts = np.datetime64("2022-01-01T00:00")
    prev_idx = int(np.where(valid_time == prev_ts)[0][0])
    next_idx = int(np.where(valid_time == next_ts)[0][0])

    window_ids = ["2020-12", *(f"2021-{m:02d}" for m in range(1, 13)), "2022-01"]
    for month in range(1, 13):
        month_start = np.datetime64(f"2021-{month:02d}-01T00:00")
        month_end = (
            np.datetime64(f"2021-{month + 1:02d}-01T00:00")
            if month < 12
            else np.datetime64("2022-01-01T00:00")
        ) - _HOUR
        start_idx = int(np.where(valid_time == month_start)[0][0])
        end_idx = int(np.where(valid_time == month_end)[0][0])
        _write_raw(
            raw_artifact_path(f"2021-{month:02d}", data_root),
            valid_time[start_idx : end_idx + 1],
            acc_m[start_idx : end_idx + 1],
        )
    _write_raw(
        raw_artifact_path("2020-12", data_root),
        valid_time[[prev_idx]],
        acc_m[[prev_idx]],
    )
    _write_raw(
        raw_artifact_path("2022-01", data_root),
        valid_time[[next_idx]],
        acc_m[[next_idx]],
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
    for window_id in window_ids:
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
        # Assert the REASON as well as the code. Every transform failure —
        # including a missing raw artifact — exits 4, so a code-only
        # assertion would still pass if the fixture stopped producing the D7
        # packing violation it exists to produce (which is exactly what the
        # D4 monthly re-slicing would have done to it).
        with pytest.raises(Era5PackingPostConditionError, match="material negative"):
            run(args)
        assert _invoke(args) == 4

    def test_cost_limit_rejection_is_exit_6_not_2(self, tmp_path: Path) -> None:
        """Plan 171, corrected 2026-08-17. The real 4b failure exited 2
        ("inputs absent") on valid credentials. It gets its own code, and
        the dispatch table must reach it despite `Era5RequestTooLargeError`
        being a SUBCLASS of `Era5RequestFailedError` (exit 3)."""
        provenance = _write_provenance(tmp_path)
        window = AcquisitionWindow(year=2021)
        args = build_parser().parse_args(
            [
                "--stage",
                "acquire",
                "--window",
                "2021",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        client = _ScriptedClient(
            window=window,
            raise_sequence=[
                classify_cds_exception(RuntimeError(_OBSERVED_COST_LIMIT_BODY))
            ],
        )
        code = _invoke(args, client=client, sleep=lambda _s: None)
        assert code == 6
        # Deterministic rejection: one attempt, no retry storm.
        assert client.call_count == 1

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
    client above), the secret must be gone BEFORE the exception ever leaves
    `_download_with_retry` (the `CdsClient` seam, D12) — not merely scrubbed
    later at a particular logging call site. The plan is explicit: the
    sentinel must appear in NONE of stdout, stderr, captured structlog
    output, any raised exception's string form, or the written manifest."""

    def test_hostile_message_never_leaks_from_run(
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
        # The seam sanitizes BEFORE re-raising — the secret must already be
        # gone here, regardless of what any downstream caller does with it.
        assert sentinel not in str(excinfo.value)
        assert _exit_code_for(excinfo.value) == 2

        log = structlog.get_logger("scripts.dhm_precip.acquire_era5")
        with structlog.testing.capture_logs() as logs:
            log.error(
                "era5.cli.failed",
                error=redact_secrets(str(excinfo.value)),
                error_type=type(excinfo.value).__name__,
            )
        assert len(logs) == 1
        assert sentinel not in json.dumps(logs[0], default=str)

    def test_hostile_transient_error_never_leaks_through_main(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Drives the REAL CLI entry point (`main()`, not `run()` with the
        exit-code mapping replicated by hand) end-to-end with a hostile
        TRANSIENT client that exhausts every retry — captured stdout,
        stderr, and the manifest must all be clean."""
        sentinel = "SENTINEL-transient-leak-77"
        monkeypatch.setenv("CDSAPI_KEY", sentinel)

        class _HostileTransientClient:
            def retrieve_to_path(
                self, *, dataset: str, payload: Mapping[str, object], target: Path
            ) -> None:
                raise Era5TransientError(f"connection reset: {sentinel}")

        provenance = _write_provenance(tmp_path)
        exit_code = main(
            [
                "--stage",
                "acquire",
                "--window",
                "2019-12-31",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ],
            client=_HostileTransientClient(),
            sleep=lambda _s: None,
        )
        assert exit_code == 3  # retries exhausted -> Era5RequestFailedError

        captured = capsys.readouterr()
        assert sentinel not in captured.out
        assert sentinel not in captured.err

        manifest_path = manifest_path_for(tmp_path)
        if manifest_path.exists():
            assert sentinel not in manifest_path.read_text()

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


class TestTransformWindowScoping:
    """A review finding: `--window`'s year-scoping for `--stage transform`
    only counted whole-year windows (`month is None`), so a sub-year window
    (e.g. "2021-10") fell through to the "nothing requested" fallback and
    silently transformed ALL SIX study years — and an out-of-range whole
    year silently did NOTHING (a "successful" no-op) rather than erroring.
    Exercised with an INJECTED `transform_fn` (`run()`'s own seam) so the
    scoping logic is proven without needing real raw files on disk."""

    def _fake_transform_year(self, year: int, **_kwargs: object) -> TransformYearRecord:
        return TransformYearRecord(
            product_year=year,
            transform_identity="id",
            sha256="a" * 64,
            accumulation_convention="era5_land_01_00_accumulation_day_v1",
            units_conversion="metres_to_mm_x1000",
            packing=PackingAccounting(
                packing_corrected_cells=0, max_correction_mm=0.0, mass_adjustment_mm=0.0
            ),
            non_finite_cell_count=0,
            dropped_boundary_stamp=None,
            transformed_at=datetime(2026, 8, 13, tzinfo=UTC),
        )

    def test_partial_window_transforms_only_that_year_not_all_six(
        self, tmp_path: Path
    ) -> None:
        provenance = _write_provenance(tmp_path)
        args = build_parser().parse_args(
            [
                "--stage",
                "transform",
                "--window",
                "2021-10",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        called_years: list[int] = []

        def fake_transform(year: int, **kwargs: object) -> TransformYearRecord:
            called_years.append(year)
            return self._fake_transform_year(year, **kwargs)

        code = run(args, transform_fn=fake_transform)
        assert code == 0
        assert called_years == [2021]

    def test_stage_all_with_a_sub_year_window_is_a_usage_error(
        self, tmp_path: Path
    ) -> None:
        """`--stage all --window 2021-10` acquires ONE month and then asks for
        a whole-2021 transform, which needs the full year plus both
        neighbouring windows. An earlier version of this test asserted it
        "worked" — but only because `transform_fn` was faked. Against the real
        transform it fails deep inside on missing artifacts, reported as a data
        defect rather than the usage error it is. Acquisition and transform
        have different granularities (D4), so `--stage all` is only coherent
        for a year-granular window; the 2b sample is `--stage acquire`."""
        provenance = _write_provenance(tmp_path)
        window = AcquisitionWindow(year=2021, month=10)
        args = build_parser().parse_args(
            [
                "--stage",
                "all",
                "--window",
                "2021-10",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        called_years: list[int] = []

        def fake_transform(year: int, **kwargs: object) -> TransformYearRecord:
            called_years.append(year)
            return self._fake_transform_year(year, **kwargs)

        client = _ScriptedClient(window=window)
        with pytest.raises(NonExpressibleWindowError, match="year-granular"):
            run(args, client=client, transform_fn=fake_transform, sleep=lambda _s: None)
        assert called_years == [], "no transform may be attempted for a sub-year window"

    def test_out_of_range_window_year_is_rejected_not_silently_skipped(
        self, tmp_path: Path
    ) -> None:
        provenance = _write_provenance(tmp_path)
        args = build_parser().parse_args(
            [
                "--stage",
                "transform",
                "--window",
                "2019",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        with pytest.raises(NonExpressibleWindowError):
            run(args)


class TestAcquisitionIsMonthly:
    """D4 CORRECTED 2026-08-17 — the acquisition stage must never issue a
    year-granular payload: CDS refuses 8,760 hourly fields outright. Driven
    through `run()`'s own `acquire_fn` seam, so no netCDF is written and the
    assertion is purely about WHICH windows are requested."""

    def _record(self, window_id: str) -> RawWindowRecord:
        return RawWindowRecord(
            window_id=window_id,
            dataset="reanalysis-era5-land",
            request_payload={},
            raw_request_identity="id",
            sha256="a" * 64,
            client_package_version="0.7.7",
            downloaded_at=datetime(2026, 8, 13, tzinfo=UTC),
        )

    def _run_and_collect(self, args: object) -> list[str]:
        seen: list[str] = []

        def fake_acquire(
            window: AcquisitionWindow, **_kwargs: object
        ) -> RawWindowRecord:
            seen.append(window.window_id)
            return self._record(window.window_id)

        assert run(args, acquire_fn=fake_acquire) == 0  # type: ignore[arg-type]
        return seen

    def test_a_year_granular_window_expands_into_twelve_months(
        self, tmp_path: Path
    ) -> None:
        provenance = _write_provenance(tmp_path)
        args = build_parser().parse_args(
            [
                "--stage",
                "acquire",
                "--window",
                "2021",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        assert self._run_and_collect(args) == [f"2021-{m:02d}" for m in range(1, 13)]

    def test_the_default_window_set_is_74_windows_none_of_them_yearly(
        self, tmp_path: Path
    ) -> None:
        provenance = _write_provenance(tmp_path)
        args = build_parser().parse_args(
            [
                "--stage",
                "acquire",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        seen = self._run_and_collect(args)
        assert len(seen) == 74
        # A bare "YYYY" window id is exactly the payload CDS rejected.
        assert [w for w in seen if len(w) == 4] == []


class TestDiagnoseStage:
    """3a's accumulation-convention diagnostic, wired to a real (fake-client
    -acquired-on-disk) raw window rather than left as dead code reachable
    only from synthetic-fixture unit tests."""

    def _write_diagnosable_raw(
        self, tmp_path: Path, *, reset_hour: int, n_days: int = 31
    ) -> None:
        valid_time = np.datetime64("2021-10-01T00:00") + np.arange(24 * n_days) * _HOUR
        rng = np.random.default_rng(0)
        true_mm_1d = rng.uniform(0.1, 2.0, size=valid_time.size)
        true_mm = np.broadcast_to(
            true_mm_1d[:, None, None], (valid_time.size, 2, 2)
        ).astype(np.float64)
        days = valid_time.astype("datetime64[D]")
        hours = ((valid_time - days) / _HOUR).astype(int)
        acc = np.empty_like(true_mm)
        running = np.zeros(true_mm.shape[1:])
        for i in range(valid_time.size):
            running = (
                true_mm[i].copy() if hours[i] == reset_hour else running + true_mm[i]
            )
            acc[i] = running
        window = AcquisitionWindow(year=2021, month=10)
        path = raw_artifact_path(window.window_id, tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        ds = xr.Dataset(
            {
                "tp": (
                    ["valid_time", "latitude", "longitude"],
                    (acc / 1000.0).astype(np.float32),
                )
            },
            coords={
                "valid_time": valid_time,
                "latitude": np.array([26.0, 26.1]),
                "longitude": np.array([80.0, 80.1]),
            },
        )
        ds["tp"].attrs["units"] = "m"
        ds.to_netcdf(path)
        # B5/M-7 — `--stage diagnose` now reconciles the raw file against the
        # acquisition manifest's own sha256 BEFORE decoding it, so the
        # window must carry real acquisition provenance. A hand-placed file
        # with no recorded origin is exactly what an untrustworthy gate
        # accepts.
        self._record_raw_window(tmp_path, window=window, path=path)

    def _record_raw_window(
        self, tmp_path: Path, *, window: AcquisitionWindow, path: Path
    ) -> None:
        from scripts.dhm_precip.era5_manifest import (
            Era5ProvenanceManifest,
            RawWindowRecord,
            checksum_file,
            load_operator_provenance,
            manifest_path_for,
            with_raw_window,
            write_manifest_atomic,
        )

        provenance = load_operator_provenance(_write_provenance(tmp_path))
        manifest = Era5ProvenanceManifest(
            dataset="reanalysis-era5-land",
            client_package_version="0.7.7",
            operator_provenance=provenance,
        )
        manifest = with_raw_window(
            manifest,
            RawWindowRecord(
                window_id=window.window_id,
                dataset="reanalysis-era5-land",
                request_payload={},
                raw_request_identity="r",
                sha256=checksum_file(path),
                client_package_version="0.7.7",
                downloaded_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        )
        write_manifest_atomic(manifest, manifest_path_for(tmp_path))

    def test_confirms_d6_convention_and_succeeds(self, tmp_path: Path) -> None:
        self._write_diagnosable_raw(tmp_path, reset_hour=1)  # D6's own rule
        provenance = _write_provenance(tmp_path)
        args = build_parser().parse_args(
            [
                "--stage",
                "diagnose",
                "--window",
                "2021-10",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        assert run(args) == 0

    def test_disagreeing_convention_is_rejected_not_silent(
        self, tmp_path: Path
    ) -> None:
        # A calendar-midnight reset (hour 0) — NOT D6's hour-1 reset.
        self._write_diagnosable_raw(tmp_path, reset_hour=0)
        provenance = _write_provenance(tmp_path)
        args = build_parser().parse_args(
            [
                "--stage",
                "diagnose",
                "--window",
                "2021-10",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        with pytest.raises(Era5TransformFailedError):
            run(args)

    def test_diagnose_without_an_explicit_window_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """M-7/B5 — with no `--window` the stage resolved ALL eight D4
        windows, including the one-hour boundary window the diagnostic must
        reject. Exactly one explicit approved window is now required."""
        self._write_diagnosable_raw(tmp_path, reset_hour=1)
        provenance = _write_provenance(tmp_path)
        args = build_parser().parse_args(
            [
                "--stage",
                "diagnose",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        with pytest.raises(NonExpressibleWindowError, match="exactly one"):
            run(args)

    def test_diagnose_of_two_windows_is_rejected(self, tmp_path: Path) -> None:
        self._write_diagnosable_raw(tmp_path, reset_hour=1)
        provenance = _write_provenance(tmp_path)
        args = build_parser().parse_args(
            [
                "--stage",
                "diagnose",
                "--window",
                "2021-10",
                "--window",
                "2021-11",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        with pytest.raises(NonExpressibleWindowError, match="exactly one"):
            run(args)

    def test_diagnose_of_the_one_hour_edge_window_is_rejected(
        self, tmp_path: Path
    ) -> None:
        self._write_diagnosable_raw(tmp_path, reset_hour=1)
        provenance = _write_provenance(tmp_path)
        args = build_parser().parse_args(
            [
                "--stage",
                "diagnose",
                "--window",
                "2026-01-01T00",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        with pytest.raises(NonExpressibleWindowError, match="whole month or year"):
            run(args)

    def test_diagnose_reconciles_the_sha256_before_decoding_the_payload(
        self, tmp_path: Path
    ) -> None:
        """B5 — the diagnostic decoded the raw file and only then checksummed
        it, so a file that disagreed with its acquisition manifest still
        produced a record. The tampered payload here is not even valid
        NetCDF: if the reconcile ran after the decode, the error would be a
        decoder error rather than the typed checksum failure."""
        self._write_diagnosable_raw(tmp_path, reset_hour=1)
        window = AcquisitionWindow(year=2021, month=10)
        raw_artifact_path(window.window_id, tmp_path).write_bytes(b"not a netcdf file")
        provenance = _write_provenance(tmp_path)
        args = build_parser().parse_args(
            [
                "--stage",
                "diagnose",
                "--window",
                "2021-10",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        with pytest.raises(Era5TransformFailedError, match="sha256"):
            run(args)

    def test_diagnose_of_a_window_with_no_acquisition_provenance_is_a_storage_error(
        self, tmp_path: Path
    ) -> None:
        self._write_diagnosable_raw(tmp_path, reset_hour=1)
        from scripts.dhm_precip.era5_manifest import manifest_path_for

        manifest_path_for(tmp_path).unlink()
        provenance = _write_provenance(tmp_path)
        args = build_parser().parse_args(
            [
                "--stage",
                "diagnose",
                "--window",
                "2021-10",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        assert _invoke(args) == 5

    def test_diagnose_of_a_sample_below_the_pinned_minimum_is_rejected(
        self, tmp_path: Path
    ) -> None:
        self._write_diagnosable_raw(tmp_path, reset_hour=1, n_days=5)
        provenance = _write_provenance(tmp_path)
        args = build_parser().parse_args(
            [
                "--stage",
                "diagnose",
                "--window",
                "2021-10",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        with pytest.raises(Era5TransformFailedError, match="sample_size_days"):
            run(args)

    def test_a_passing_diagnose_run_writes_a_record_that_gates_a_publish(
        self, tmp_path: Path
    ) -> None:
        """The whole point of 1c: the record it writes must satisfy the
        (now strict) passing predicate M-A5 consumes."""
        from scripts.dhm_precip.era5_manifest import (
            manifest_path_for,
            passing_accumulation_diagnostic,
            read_manifest,
        )

        self._write_diagnosable_raw(tmp_path, reset_hour=1)
        provenance = _write_provenance(tmp_path)
        args = build_parser().parse_args(
            [
                "--stage",
                "diagnose",
                "--window",
                "2021-10",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        assert run(args) == 0
        manifest = read_manifest(manifest_path_for(tmp_path))
        assert manifest is not None
        assert manifest.accumulation_diagnostics["2021-10"].source_sha256
        assert (
            passing_accumulation_diagnostic(manifest, expected_reset_hour=1) is not None
        )

    def test_missing_raw_artifact_is_a_storage_error(self, tmp_path: Path) -> None:
        provenance = _write_provenance(tmp_path)
        args = build_parser().parse_args(
            [
                "--stage",
                "diagnose",
                "--window",
                "2021-10",
                "--provenance",
                str(provenance),
                "--data-root",
                str(tmp_path),
            ]
        )
        assert _invoke(args) == 5
