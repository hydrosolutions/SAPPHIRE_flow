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

Round-2 adversarial review additions (correctness bugs):
  * BLOCKER 1: sweep-before-check — stale sweep must free space before the
    tripwire evaluates free space, otherwise accumulated leftovers permanently
    defeat the self-heal.
  * BLOCKER 2: test_failed_fetch_leaves_no_scratch_leftover now makes the
    transport actually download one allowlisted GRIB file so _fetch_grib_files
    SUCCEEDS and the monkeypatched _parse_grib_files raise is genuinely reached.
  * BLOCKER 3: archive mount hard-low must raise DiskHardLimitError even when
    scratch is healthy; scratch soft + archive hard must still raise
    DiskHardLimitError (hard wins across all mounts).
"""

from __future__ import annotations

import contextlib
from collections import namedtuple
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

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

# Allowlist token from PARAM_GROUPS col-0; item IDs must contain -<token>-.
_ALLOWLIST_TOKEN = meteoswiss_nwp.PARAM_GROUPS[0][0]  # "tot_prec"
# Filename used in the fake transport's asset href — must end in .grib2 and
# contain the allowlist token so _is_grib_asset + token filter both pass.
_FAKE_GRIB_FILENAME = f"icon-ch2-eps-{_ALLOWLIST_TOKEN}-fake.grib2"
# Minimal bytes that pass _verify_grib_magic (first 4 bytes == b"GRIB").
_FAKE_GRIB_BYTES = b"GRIB" + b"\x00" * 96


def _published_transport() -> httpx.MockTransport:
    """Transport that makes _CYCLE appear published (non-empty features) so
    resolve_cycle() returns successfully and D1/D2 code paths are reachable.

    Returns an empty assets dict so no files are actually downloaded. Use
    _published_transport_with_grib() when a real download is required.
    """
    ref_dt = _CYCLE.strftime("%Y-%m-%dT%H:%M:%SZ")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "features": [
                    {
                        "id": "stub-item-001",
                        "properties": {"forecast:reference_datetime": ref_dt},
                        "assets": {},
                    }
                ],
                "links": [],
            },
        )

    return httpx.MockTransport(handler)


def _published_transport_with_grib() -> httpx.MockTransport:
    """Transport that returns one allowlisted GRIB asset so _fetch_grib_files
    actually downloads a file and returns a non-empty list before the parse
    stage is reached.

    The item ID contains -<_ALLOWLIST_TOKEN>- so the client-side allowlist
    filter passes.  The asset href ends in .grib2 so _is_grib_asset returns
    True.  The response body starts with b"GRIB" so _verify_grib_magic passes.
    The transport routes:
      * Any request URL whose path ends with /items* → STAC listing response
      * Any other URL → fake GRIB binary download
    """
    ref_dt = _CYCLE.strftime("%Y-%m-%dT%H:%M:%SZ")
    item_id = f"icon-ch2-eps-{_ALLOWLIST_TOKEN}-20260706T000000Z"
    grib_href = f"{_STAC_BASE}/fake-download/{_FAKE_GRIB_FILENAME}"

    def handler(request: httpx.Request) -> httpx.Response:
        if "/items" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "id": item_id,
                            "properties": {"forecast:reference_datetime": ref_dt},
                            "assets": {
                                _FAKE_GRIB_FILENAME: {
                                    "href": grib_href,
                                    "type": "application/x-grib2",
                                }
                            },
                        }
                    ],
                    "links": [],
                },
            )
        # Download endpoint — return fake GRIB bytes.
        return httpx.Response(200, content=_FAKE_GRIB_BYTES)

    return httpx.MockTransport(handler)


def _make_guard_adapter(
    tmp_path: Path, *, disk_guard_enabled: bool
) -> MeteoSwissNwpAdapter:
    client = httpx.Client(transport=_published_transport(), base_url="https://dummy")
    return MeteoSwissNwpAdapter(
        stac_base_url=_STAC_BASE,
        stac_collection=_STAC_COLLECTION,
        scratch_path=tmp_path,
        http_client=client,
        disk_guard_enabled=disk_guard_enabled,
    )


def _make_guard_adapter_with_archive(
    tmp_path: Path,
    archive_path: Path,
    *,
    disk_guard_enabled: bool = True,
    scratch_soft_gb: float = 1.5,
    scratch_hard_gb: float = 0.5,
    archive_soft_gb: float = 8.0,
    archive_hard_gb: float = 3.0,
) -> MeteoSwissNwpAdapter:
    """Adapter with both scratch and archive paths configured for BLOCKER-3 tests."""
    client = httpx.Client(transport=_published_transport(), base_url="https://dummy")
    return MeteoSwissNwpAdapter(
        stac_base_url=_STAC_BASE,
        stac_collection=_STAC_COLLECTION,
        scratch_path=tmp_path,
        http_client=client,
        disk_guard_enabled=disk_guard_enabled,
        disk_guard_scratch_soft_gb=scratch_soft_gb,
        disk_guard_scratch_hard_gb=scratch_hard_gb,
        disk_guard_archive_soft_gb=archive_soft_gb,
        disk_guard_archive_hard_gb=archive_hard_gb,
        nwp_grid_archive_path=archive_path,
    )


def _patch_free_gb(monkeypatch: pytest.MonkeyPatch, free_gb: float) -> None:
    """Force `shutil.disk_usage` (the source `disk_free_gb` reads) to report
    `free_gb` GB free for any path, so the pre-fetch tripwire tier is deterministic."""
    fake = _DiskUsage(
        total=100 * _GB, used=int((100 - free_gb) * _GB), free=int(free_gb * _GB)
    )
    monkeypatch.setattr(meteoswiss_nwp.shutil, "disk_usage", lambda _p: fake)


def _patch_free_gb_per_path(
    monkeypatch: pytest.MonkeyPatch,
    free_by_path: dict[str, float],
    default_gb: float = 50.0,
) -> None:
    """Monkeypatch shutil.disk_usage to return different free space per path prefix.

    Keys in free_by_path are path strings; the first matching prefix wins.
    Paths not matching any key fall back to default_gb.
    """

    def _fake_disk_usage(path: Any) -> Any:
        p = str(path)
        for prefix, gb in free_by_path.items():
            if p.startswith(prefix):
                free = int(gb * _GB)
                return _DiskUsage(total=100 * _GB, used=100 * _GB - free, free=free)
        free = int(default_gb * _GB)
        return _DiskUsage(total=100 * _GB, used=100 * _GB - free, free=free)

    monkeypatch.setattr(meteoswiss_nwp.shutil, "disk_usage", _fake_disk_usage)


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
        """BLOCKER 2 regression: the transport must actually download a GRIB file
        so _fetch_grib_files SUCCEEDS (returns a non-empty list) and the
        monkeypatched _parse_grib_files raise is genuinely reached.

        With the old transport (empty assets), _fetch_grib_files raised
        AdapterError("No matching GRIB2 files …") BEFORE _parse_grib_files was
        ever called — so the cleanup happened inside _fetch_grib_files, not the
        centralized fetch_forecasts handler, and the test passed for the wrong
        reason.  The parse-stage failure path was never exercised.
        """
        client = httpx.Client(
            transport=_published_transport_with_grib(), base_url="https://dummy"
        )
        adapter = MeteoSwissNwpAdapter(
            stac_base_url=_STAC_BASE,
            stac_collection=_STAC_COLLECTION,
            scratch_path=tmp_path,
            http_client=client,
            disk_guard_enabled=True,
        )

        parse_called = []

        def _boom_parse(_files: object) -> object:
            parse_called.append(True)
            raise ValueError("simulated parse failure")

        monkeypatch.setattr(adapter, "_parse_grib_files", _boom_parse)
        # Ensure disk appears healthy so the tripwire doesn't trip before download.
        _patch_free_gb(monkeypatch, 50.0)

        with pytest.raises(exceptions.AdapterError):
            adapter.fetch_forecasts([], _CYCLE)

        assert parse_called, (
            "_parse_grib_files was never called — download did not succeed"
        )
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


class TestSweepBeforeCheck:
    """BLOCKER 1 — the stale-cycle sweep must run BEFORE the disk tripwire.

    If the check runs first, a pre-existing scratch clog permanently defeats
    the guard: the tripwire fires on leftovers that the sweep would have freed.
    The correct order is: sweep → check disk.
    """

    def test_sweep_runs_before_disk_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stale cycle dir exists. shutil.disk_usage returns soft-breach free
        space WHILE the stale dir is present, and healthy free space AFTER it is
        deleted.  The adapter must sweep first (deleting the stale dir) and then
        probe the disk, so no DiskSoftLimitError is raised.

        With the old (buggy) code, the disk check ran BEFORE the sweep: the mock
        returned soft-breach level (stale dir still present) → DiskSoftLimitError.
        With the fix, the sweep deletes the stale dir first, the mock then returns
        healthy → no exception.
        """
        stale = tmp_path / "20260101T0000"
        stale.mkdir()
        (stale / "big_leftover.grib2").write_bytes(b"x" * 1024)

        soft_breach_free = 1.0  # below 1.5 GB soft default
        healthy_free = 50.0

        def _stateful_disk_usage(_path: object) -> Any:
            # After the sweep, the stale dir is gone → report healthy.
            # While it still exists → report soft-breach level.
            free_gb = healthy_free if not stale.exists() else soft_breach_free
            free = int(free_gb * _GB)
            return _DiskUsage(total=100 * _GB, used=100 * _GB - free, free=free)

        monkeypatch.setattr(meteoswiss_nwp.shutil, "disk_usage", _stateful_disk_usage)

        adapter = _make_guard_adapter(tmp_path, disk_guard_enabled=True)
        monkeypatch.setattr(adapter, "_parse_grib_files", lambda _f: _boom_returns())

        try:
            adapter.fetch_forecasts([], _CYCLE)
        except (exceptions.DiskSoftLimitError, exceptions.DiskHardLimitError):
            pytest.fail(
                "Disk tripwire fired before sweep could free stale leftovers "
                "(sweep-before-check invariant violated)"
            )
        except exceptions.AdapterError:
            pass  # download/parse failure — expected for this no-real-GRIB test

        assert not stale.exists(), "stale dir was not swept before disk check"


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


class TestCrossMount:
    """BLOCKER 3 — a hard breach on ANY mount must beat a soft breach on another.

    _check_disk_space must probe ALL mounts, collect their breach tiers, and
    raise DiskHardLimitError if any mount is hard-breached regardless of which
    mount was probed first or what other mounts reported.
    """

    def test_archive_hard_breach_with_healthy_scratch_raises_hard(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive_path = tmp_path / "archive"
        archive_path.mkdir()
        scratch_path = tmp_path / "scratch"
        scratch_path.mkdir()

        adapter = _make_guard_adapter_with_archive(
            scratch_path,
            archive_path,
            scratch_soft_gb=1.5,
            scratch_hard_gb=0.5,
            archive_soft_gb=8.0,
            archive_hard_gb=3.0,
        )

        # scratch: healthy (50 GB); archive: hard-breached (1.0 GB < 3.0 GB hard)
        _patch_free_gb_per_path(
            monkeypatch,
            {
                str(scratch_path): 50.0,
                str(archive_path): 1.0,
            },
        )

        with pytest.raises(exceptions.DiskHardLimitError):
            adapter.fetch_forecasts([], _CYCLE)

    def test_scratch_soft_and_archive_hard_raises_hard(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scratch below soft (→ soft breach), archive below hard (→ hard breach).
        Hard must win: DiskHardLimitError, not DiskSoftLimitError.
        """
        archive_path = tmp_path / "archive"
        archive_path.mkdir()
        scratch_path = tmp_path / "scratch"
        scratch_path.mkdir()

        adapter = _make_guard_adapter_with_archive(
            scratch_path,
            archive_path,
            scratch_soft_gb=1.5,
            scratch_hard_gb=0.5,
            archive_soft_gb=8.0,
            archive_hard_gb=3.0,
        )

        # scratch: 1.0 GB (below 1.5 soft, above 0.5 hard → soft breach)
        # archive: 1.0 GB (below 3.0 hard → hard breach)
        _patch_free_gb_per_path(
            monkeypatch,
            {
                str(scratch_path): 1.0,
                str(archive_path): 1.0,
            },
        )

        with pytest.raises(exceptions.DiskHardLimitError):
            adapter.fetch_forecasts([], _CYCLE)

    def test_both_mounts_soft_breach_raises_soft(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive_path = tmp_path / "archive"
        archive_path.mkdir()
        scratch_path = tmp_path / "scratch"
        scratch_path.mkdir()

        adapter = _make_guard_adapter_with_archive(
            scratch_path,
            archive_path,
            scratch_soft_gb=1.5,
            scratch_hard_gb=0.5,
            archive_soft_gb=8.0,
            archive_hard_gb=3.0,
        )

        # scratch: soft breach (1.0 GB); archive: below soft, above hard → soft
        _patch_free_gb_per_path(
            monkeypatch,
            {
                str(scratch_path): 1.0,
                str(archive_path): 5.0,
            },
        )

        with pytest.raises(exceptions.DiskSoftLimitError):
            adapter.fetch_forecasts([], _CYCLE)

    def test_both_mounts_healthy_no_breach(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive_path = tmp_path / "archive"
        archive_path.mkdir()
        scratch_path = tmp_path / "scratch"
        scratch_path.mkdir()

        adapter = _make_guard_adapter_with_archive(scratch_path, archive_path)

        _patch_free_gb(monkeypatch, 50.0)

        try:
            adapter.fetch_forecasts([], _CYCLE)
        except (exceptions.DiskSoftLimitError, exceptions.DiskHardLimitError):
            pytest.fail("healthy mounts must not trip any threshold")
        except exceptions.AdapterError:
            pass  # download/parse failure is expected with empty STAC assets


def _boom_returns() -> object:
    raise ValueError("simulated parse failure")
