"""Plan 171 task 2a — the raw acquisition driver, exercised entirely
against a fake CDS client (constraint 5: no real CDS access in CI)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np
import pytest
import xarray as xr

from scripts.dhm_precip.era5_acquire import CdsClient, acquire_window
from scripts.dhm_precip.era5_errors import Era5RequestFailedError, Era5TransientError
from scripts.dhm_precip.era5_manifest import (
    OperatorProvenance,
    manifest_path_for,
    raw_artifact_path,
    read_manifest,
)
from scripts.dhm_precip.era5_request import AcquisitionWindow, Era5RequestSpec

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_PROVENANCE = OperatorProvenance(
    cds_portal_url="https://cds.climate.copernicus.eu",
    dataset_landing_page_url="https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land",
    licence_name="Licence to use Copernicus Products",
    licence_version="1.0",
    licence_accepted_at=datetime(2026, 8, 13, tzinfo=UTC),
)


def _write_valid_raw_netcdf(
    path: Path, window: AcquisitionWindow, spec: Era5RequestSpec
) -> None:
    stamps = sorted(window.valid_time_stamps())
    times = np.array(
        [
            np.datetime64(f"{y:04d}-{m:02d}-{d:02d}T{h:02d}:00:00")
            for y, m, d, h in stamps
        ]
    )
    north, west, south, east = spec.area
    lat = np.arange(south, north + 0.1, 0.1)
    lon = np.arange(west, east + 0.1, 0.1)
    tp = np.zeros((times.size, lat.size, lon.size), dtype=np.float32)
    ds = xr.Dataset(
        {"tp": (["valid_time", "latitude", "longitude"], tp)},
        coords={"valid_time": times, "latitude": lat, "longitude": lon},
    )
    ds["tp"].attrs["units"] = "m"
    ds.to_netcdf(path)


@dataclass
class FakeCdsClient:
    """A fake `CdsClient` whose behaviour is scripted per call."""

    window: AcquisitionWindow
    spec: Era5RequestSpec
    call_count: int = 0
    raise_sequence: list[Exception] = field(default_factory=list)
    write_valid_on_success: bool = True

    def retrieve_to_path(
        self, *, dataset: str, payload: Mapping[str, object], target: Path
    ) -> None:
        self.call_count += 1
        if self.raise_sequence:
            exc = self.raise_sequence.pop(0)
            raise exc
        if self.write_valid_on_success:
            _write_valid_raw_netcdf(target, self.window, self.spec)


def _no_sleep(_seconds: float) -> None:
    return None


class TestResumeSkipsCompletedWindow:
    def test_skips_when_identity_and_checksum_match(self, tmp_path: Path) -> None:
        window = AcquisitionWindow(year=2021, month=10)
        spec = Era5RequestSpec()
        client = FakeCdsClient(window=window, spec=spec)
        clock_calls = iter(
            [datetime(2026, 8, 13, tzinfo=UTC), datetime(2026, 8, 14, tzinfo=UTC)]
        )

        acquire_window(
            window,
            spec=spec,
            provenance=_PROVENANCE,
            client=client,
            clock=lambda: next(clock_calls),
            sleep=_no_sleep,
            data_root=tmp_path,
            client_package_version="0.7.7",
        )
        assert client.call_count == 1

        acquire_window(
            window,
            spec=spec,
            provenance=_PROVENANCE,
            client=client,
            clock=lambda: next(clock_calls),
            sleep=_no_sleep,
            data_root=tmp_path,
            client_package_version="0.7.7",
        )
        assert client.call_count == 1  # not refetched


class TestInterruptedDownload:
    def test_leaves_no_file_at_final_path(self, tmp_path: Path) -> None:
        window = AcquisitionWindow(year=2021, month=10)
        spec = Era5RequestSpec(max_retry_attempts=1)
        client = FakeCdsClient(
            window=window,
            spec=spec,
            raise_sequence=[Era5TransientError("connection reset")],
        )

        with pytest.raises(Era5RequestFailedError):
            acquire_window(
                window,
                spec=spec,
                provenance=_PROVENANCE,
                client=client,
                clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
                sleep=_no_sleep,
                data_root=tmp_path,
                client_package_version="0.7.7",
            )

        assert not raw_artifact_path(window.window_id, tmp_path).exists()


class TestChecksumMismatchTriggersRefetch:
    def test_corrupted_raw_file_is_refetched(self, tmp_path: Path) -> None:
        window = AcquisitionWindow(year=2021, month=10)
        spec = Era5RequestSpec()
        client = FakeCdsClient(window=window, spec=spec)

        acquire_window(
            window,
            spec=spec,
            provenance=_PROVENANCE,
            client=client,
            clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
            sleep=_no_sleep,
            data_root=tmp_path,
            client_package_version="0.7.7",
        )
        assert client.call_count == 1

        # Corrupt the file on disk without touching the manifest.
        final_path = raw_artifact_path(window.window_id, tmp_path)
        final_path.write_bytes(final_path.read_bytes() + b"\x00corruption")

        acquire_window(
            window,
            spec=spec,
            provenance=_PROVENANCE,
            client=client,
            clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
            sleep=_no_sleep,
            data_root=tmp_path,
            client_package_version="0.7.7",
        )
        assert client.call_count == 2


class TestBoundingBoxChangeTriggersRefetch:
    def test_identity_mismatch_forces_refetch(self, tmp_path: Path) -> None:
        window = AcquisitionWindow(year=2021, month=10)
        spec_a = Era5RequestSpec(area=(31, 80, 26, 89))
        client_a = FakeCdsClient(window=window, spec=spec_a)
        acquire_window(
            window,
            spec=spec_a,
            provenance=_PROVENANCE,
            client=client_a,
            clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
            sleep=_no_sleep,
            data_root=tmp_path,
            client_package_version="0.7.7",
        )
        assert client_a.call_count == 1

        spec_b = Era5RequestSpec(area=(30, 80, 26, 89))
        client_b = FakeCdsClient(window=window, spec=spec_b)
        acquire_window(
            window,
            spec=spec_b,
            provenance=_PROVENANCE,
            client=client_b,
            clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
            sleep=_no_sleep,
            data_root=tmp_path,
            client_package_version="0.7.7",
        )
        assert client_b.call_count == 1


class TestRawFileWithNoManifestEntry:
    def test_is_refetched_not_assumed_good(self, tmp_path: Path) -> None:
        window = AcquisitionWindow(year=2021, month=10)
        spec = Era5RequestSpec()
        final_path = raw_artifact_path(window.window_id, tmp_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        _write_valid_raw_netcdf(final_path, window, spec)
        # No manifest at all yet.
        assert not manifest_path_for(tmp_path).exists()

        client = FakeCdsClient(window=window, spec=spec)
        acquire_window(
            window,
            spec=spec,
            provenance=_PROVENANCE,
            client=client,
            clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
            sleep=_no_sleep,
            data_root=tmp_path,
            client_package_version="0.7.7",
        )
        assert client.call_count == 1


class TestClockInjection:
    def test_downloaded_at_comes_from_injected_clock(self, tmp_path: Path) -> None:
        window = AcquisitionWindow(year=2021, month=10)
        spec = Era5RequestSpec()
        client = FakeCdsClient(window=window, spec=spec)
        fixed = datetime(2026, 8, 13, 12, 34, 56, tzinfo=UTC)

        record = acquire_window(
            window,
            spec=spec,
            provenance=_PROVENANCE,
            client=client,
            clock=lambda: fixed,
            sleep=_no_sleep,
            data_root=tmp_path,
            client_package_version="0.7.7",
        )
        assert record.downloaded_at == fixed

        manifest = read_manifest(manifest_path_for(tmp_path))
        assert manifest is not None
        assert manifest.raw_windows[window.window_id].downloaded_at == fixed


class TestRetryContract:
    def test_transient_failure_retries_then_succeeds(self, tmp_path: Path) -> None:
        window = AcquisitionWindow(year=2021, month=10)
        spec = Era5RequestSpec(max_retry_attempts=3, retry_backoff_base_seconds=0.01)
        client = FakeCdsClient(
            window=window,
            spec=spec,
            raise_sequence=[
                Era5TransientError("timeout"),
                Era5TransientError("timeout"),
            ],
        )
        sleep_calls: list[float] = []

        record = acquire_window(
            window,
            spec=spec,
            provenance=_PROVENANCE,
            client=client,
            clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
            sleep=sleep_calls.append,
            data_root=tmp_path,
            client_package_version="0.7.7",
        )
        assert client.call_count == 3
        assert len(sleep_calls) == 2
        assert record.window_id == window.window_id

    def test_exhausted_retries_raise_request_failed(self, tmp_path: Path) -> None:
        window = AcquisitionWindow(year=2021, month=10)
        spec = Era5RequestSpec(max_retry_attempts=2, retry_backoff_base_seconds=0.01)
        client = FakeCdsClient(
            window=window,
            spec=spec,
            raise_sequence=[
                Era5TransientError("timeout"),
                Era5TransientError("timeout"),
            ],
        )

        with pytest.raises(Era5RequestFailedError, match="exhausted 2 attempts"):
            acquire_window(
                window,
                spec=spec,
                provenance=_PROVENANCE,
                client=client,
                clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
                sleep=_no_sleep,
                data_root=tmp_path,
                client_package_version="0.7.7",
            )
        assert client.call_count == 2


class TestValidationRejectsBadArtifact:
    def test_wrong_variable_is_rejected(self, tmp_path: Path) -> None:
        window = AcquisitionWindow(year=2021, month=10)
        spec = Era5RequestSpec()

        class _WrongVarClient:
            def retrieve_to_path(
                self, *, dataset: str, payload: Mapping[str, object], target: Path
            ) -> None:
                xr.Dataset({"wrong_var": (["x"], [1.0])}).to_netcdf(target)

        assert isinstance(_WrongVarClient(), CdsClient)

        with pytest.raises(Era5RequestFailedError, match="tp"):
            acquire_window(
                window,
                spec=spec,
                provenance=_PROVENANCE,
                client=_WrongVarClient(),
                clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
                sleep=_no_sleep,
                data_root=tmp_path,
                client_package_version="0.7.7",
            )
        assert not raw_artifact_path(window.window_id, tmp_path).exists()
