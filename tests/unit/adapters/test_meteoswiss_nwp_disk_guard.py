"""LOCKED regression/acceptance tests for Plan 105 — operational disk hygiene &
NWP scratch cleanup (adapter side: D1 scratch cleanup + D2 pre-fetch tripwire).

Authored test-first (they fail RED until Plan 105 is implemented). Soundness
rules honoured (a prior WF2 run blocked on violations of these):
  * Tests target the adapter's PUBLIC `fetch_forecasts` + constructor signature,
    never the private `_fetch_grib_files`.
  * They use the plan's EXACT symbols (`disk_guard_enabled`, `DiskSoftLimitError`,
    `DiskHardLimitError`) — so they fail pre-impl because the feature is absent,
    and pass post-impl (not because of an invented/wrong interface).
  * Disk breaches are forced by monkeypatching `shutil.disk_usage` against the
    plan's DEFAULT thresholds — no guessing threshold-parameter names.
"""

from __future__ import annotations

import contextlib
from collections import namedtuple
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import pytest

from sapphire_flow import exceptions
from sapphire_flow.adapters import meteoswiss_nwp
from sapphire_flow.adapters.meteoswiss_nwp import MeteoSwissNwpAdapter
from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    from pathlib import Path

_STAC_BASE = "https://data.geo.admin.ch/api/stac/v1"
_STAC_COLLECTION = "ch.meteoschweiz.ogd-forecasting-icon-ch2"
_CYCLE = ensure_utc(datetime(2026, 7, 6, 0, 0, tzinfo=UTC))
_DiskUsage = namedtuple("_DiskUsage", ["total", "used", "free"])
_GB = 1024**3


def _empty_transport() -> httpx.MockTransport:
    # STAC search returns no items → _fetch_grib_files yields [] after creating
    # the scratch dir. Enough to exercise the fetch path deterministically.
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"features": [], "links": []})

    return httpx.MockTransport(handler)


def _make_guard_adapter(
    tmp_path: Path, *, disk_guard_enabled: bool
) -> MeteoSwissNwpAdapter:
    client = httpx.Client(transport=_empty_transport(), base_url="https://dummy")
    return MeteoSwissNwpAdapter(
        stac_base_url=_STAC_BASE,
        stac_collection=_STAC_COLLECTION,
        scratch_path=tmp_path,
        http_client=client,
        disk_guard_enabled=disk_guard_enabled,
    )


def _patch_free_gb(monkeypatch: pytest.MonkeyPatch, free_gb: float) -> None:
    """Force `shutil.disk_usage` (the source `disk_free_gb` reads) to report
    `free_gb` GB free for any path, so the pre-fetch tripwire tier is deterministic."""
    fake = _DiskUsage(
        total=100 * _GB, used=int((100 - free_gb) * _GB), free=int(free_gb * _GB)
    )
    monkeypatch.setattr(meteoswiss_nwp.shutil, "disk_usage", lambda _p: fake)


class TestDiskGuardExceptionHierarchy:
    def test_disk_limit_errors_subclass_adapter_error(self) -> None:
        # Both new exceptions MUST subclass AdapterError so the adapter's
        # `except AdapterError: raise` propagates them and `_fetch_nwp_task`'s
        # first handlers catch them ahead of `except Exception`.
        assert issubclass(exceptions.DiskSoftLimitError, exceptions.AdapterError)
        assert issubclass(exceptions.DiskHardLimitError, exceptions.AdapterError)

    def test_disk_soft_limit_is_not_a_hard_limit(self) -> None:
        assert not issubclass(
            exceptions.DiskSoftLimitError, exceptions.DiskHardLimitError
        )


class TestDiskGuardEnabledFlag:
    def test_constructor_accepts_disk_guard_enabled(self, tmp_path: Path) -> None:
        adapter = _make_guard_adapter(tmp_path, disk_guard_enabled=True)
        assert adapter is not None


class TestScratchCleanupOnFailure:
    """D1 — the incident-class fix: a fetch that fails mid-run must not leave a
    scratch-dir behind (leftovers previously accumulated until the tmpfs filled
    and every fetch died 'no space')."""

    def test_failed_fetch_leaves_no_scratch_leftover(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = _make_guard_adapter(tmp_path, disk_guard_enabled=True)

        # Fail AFTER the scratch dir is created (parse stage) to simulate a
        # mid-run failure; the finally-cleanup must remove the cycle's dir.
        def _boom(_files: object) -> object:
            raise ValueError("simulated mid-run failure")

        monkeypatch.setattr(adapter, "_parse_grib_files", _boom)
        with pytest.raises(exceptions.AdapterError):
            adapter.fetch_forecasts([], _CYCLE)
        leftover_dirs = [p for p in tmp_path.iterdir() if p.is_dir()]
        assert leftover_dirs == [], f"scratch leftovers not cleaned: {leftover_dirs}"

    def test_fetch_prunes_pre_existing_stale_cycle_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stale = tmp_path / "20260101T0000"
        stale.mkdir()
        (stale / "leftover.grib2").write_bytes(b"x")
        adapter = _make_guard_adapter(tmp_path, disk_guard_enabled=True)
        monkeypatch.setattr(adapter, "_parse_grib_files", lambda _f: _boom_returns())
        # Even a successful-ish fetch must have swept the unrelated stale cycle dir.
        with contextlib.suppress(exceptions.AdapterError):
            adapter.fetch_forecasts([], _CYCLE)
        assert not stale.exists(), "stale (non-active) cycle dir was not pruned"

    def test_stale_sweep_skips_non_directory_entries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stray = tmp_path / "stray.txt"
        stray.write_text("not a cycle dir")
        adapter = _make_guard_adapter(tmp_path, disk_guard_enabled=True)
        monkeypatch.setattr(adapter, "_parse_grib_files", lambda _f: _boom_returns())
        with contextlib.suppress(exceptions.AdapterError):
            adapter.fetch_forecasts([], _CYCLE)
        # A stray file under the tmpfs must not break the directory-only sweep.
        assert stray.exists()

    def test_disk_guard_disabled_skips_stale_sweep(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stale = tmp_path / "20260101T0000"
        stale.mkdir()
        adapter = _make_guard_adapter(tmp_path, disk_guard_enabled=False)
        monkeypatch.setattr(adapter, "_parse_grib_files", lambda _f: _boom_returns())
        with contextlib.suppress(exceptions.AdapterError):
            adapter.fetch_forecasts([], _CYCLE)
        # With the guard OFF (the record_fixtures.py path) the stale sweep is a no-op.
        assert stale.exists(), "stale sweep ran despite disk_guard_enabled=False"


class TestPreFetchDiskTripwire:
    """D2 — pre-fetch tripwire on ABSOLUTE free-GB (plan defaults 1.5 / 0.5)."""

    def test_hard_breach_raises_disk_hard_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = _make_guard_adapter(tmp_path, disk_guard_enabled=True)
        _patch_free_gb(monkeypatch, 0.1)  # below the 0.5 GB hard default
        with pytest.raises(exceptions.DiskHardLimitError):
            adapter.fetch_forecasts([], _CYCLE)

    def test_soft_breach_raises_disk_soft_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = _make_guard_adapter(tmp_path, disk_guard_enabled=True)
        _patch_free_gb(monkeypatch, 1.0)  # below 1.5 soft, above 0.5 hard
        with pytest.raises(exceptions.DiskSoftLimitError):
            adapter.fetch_forecasts([], _CYCLE)

    def test_healthy_disk_does_not_trip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = _make_guard_adapter(tmp_path, disk_guard_enabled=True)
        _patch_free_gb(monkeypatch, 50.0)  # ample
        # No disk exception; empty STAC search yields a normal (empty) result path.
        try:
            adapter.fetch_forecasts([], _CYCLE)
        except (exceptions.DiskSoftLimitError, exceptions.DiskHardLimitError):
            pytest.fail("healthy disk must not trip the tripwire")
        except exceptions.AdapterError:
            pass  # unrelated empty-parse failure is fine for this assertion

    def test_disk_guard_disabled_skips_tripwire(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = _make_guard_adapter(tmp_path, disk_guard_enabled=False)
        _patch_free_gb(monkeypatch, 0.1)  # would be a hard breach IF the guard ran
        try:
            adapter.fetch_forecasts([], _CYCLE)
        except (exceptions.DiskSoftLimitError, exceptions.DiskHardLimitError):
            pytest.fail("tripwire ran despite disk_guard_enabled=False")
        except exceptions.AdapterError:
            pass


def _boom_returns() -> object:
    raise ValueError("simulated parse failure")
