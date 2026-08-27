"""Unit tests for `sapphire_flow.ops.watchdog`.

Dependency injection (clock, probe callable, Slack poster, filesystem
paths via tmp_path) keeps the tests deterministic without needing
respx/httpx_mock/freezegun.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence  # noqa: TC003 — annotation-only here
from dataclasses import replace as _replace
from datetime import UTC, datetime, timedelta
from pathlib import Path  # noqa: TC003 — used at runtime in helper

import pytest

from sapphire_flow.ops.watchdog import (
    ALERT_REPEAT_EVERY,
    BACKUP_STALE_THRESHOLD,
    BAFU_OBS_STALE_THRESHOLD,
    BAFU_STALE_THRESHOLD,
    FORECAST_FRESHNESS_STALE_THRESHOLD,
    BafuFreshnessResult,
    ForecastFreshnessResult,
    HealthProbeResult,
    WatchdogConfig,
    WatchdogState,
    default_slack_poster,
    newest_backup_mtime,
    probe_bafu_freshness,
    probe_forecast_freshness,
    probe_health,
    read_probe_token,
    read_slack_webhook,
    run_once,
    should_alert_health,
)

try:
    # Plan 194: guarded separately so an absent symbol fails
    # TestBackupTargetVerified's assertions as genuine RED, not a
    # collection-time ImportError that would mask every OTHER test in
    # this file behind an unrelated error.
    from sapphire_flow.ops.watchdog import backup_target_verified
except ImportError:
    backup_target_verified = None  # type: ignore[assignment]

try:
    # Plan 199 T1: same guarded-import convention as `backup_target_verified`
    # above — an absent symbol (pre-implementation) fails only the tests that
    # need it, as a genuine assertion/TypeError, not a collection-time
    # ImportError that would mask every OTHER test in this file.
    from sapphire_flow.ops.watchdog import (
        DEFAULT_DISK_PATH,
        DISK_FREE_THRESHOLD_PCT,
        DiskSpaceResult,
        probe_disk_free,
    )
except ImportError:
    DEFAULT_DISK_PATH = None  # type: ignore[assignment]
    DISK_FREE_THRESHOLD_PCT = None  # type: ignore[assignment]
    DiskSpaceResult = None  # type: ignore[assignment,misc]
    probe_disk_free = None  # type: ignore[assignment]

try:
    # Plan 195: same guard as backup_target_verified above. Fallback values
    # keep every OTHER test in this file (including the ~90 pre-existing
    # `run_once` calls this plan adds `launchd_probe=` to) collectible —
    # those fail with a genuine per-test TypeError (unexpected keyword
    # argument) once `run_once` is actually called, the same "signature
    # growth" RED shape Plan 194 established for `backup_device_verifier`.
    # The placeholder labels below are deliberately NOT the real production
    # labels, so TestMonitoredLaunchdLabelsParity fails as a genuine
    # mismatch assertion (not a collection error) before implementation.
    from sapphire_flow.ops.watchdog import (
        LAUNCHD_PROBE_TIMEOUT_S,
        MONITORED_LAUNCHD_LABELS,
        LaunchdVerdict,
        parse_launchctl_list_output,
        probe_launchd_agents,
    )
except ImportError:
    LAUNCHD_PROBE_TIMEOUT_S = 5.0
    MONITORED_LAUNCHD_LABELS = (
        "__plan195_not_implemented_a__",
        "__plan195_not_implemented_b__",
    )
    LaunchdVerdict = None  # type: ignore[assignment,misc]
    parse_launchctl_list_output = None  # type: ignore[assignment]
    probe_launchd_agents = None  # type: ignore[assignment]

_NOW = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)


def _ok_probe(_url: str) -> HealthProbeResult:
    return HealthProbeResult(ok=True, http_status=200)


def _fail_probe(_url: str) -> HealthProbeResult:
    return HealthProbeResult(ok=False, http_status=503)


def _unreachable_probe(_url: str) -> HealthProbeResult:
    return HealthProbeResult(ok=False, http_status=None, error="ConnectError")


def _bafu_ok_probe(_url: str) -> BafuFreshnessResult:
    # Healthy heartbeat, exactly at `_NOW` — never stale, never degraded.
    # Used as the default `bafu_probe` fake for every pre-existing
    # health/backup test in this file so the new BAFU check doesn't change
    # their behaviour (they exercise health/backup independently).
    return BafuFreshnessResult(found=True, checked_at=_NOW, status="ok", error=None)


def _bafu_stale_probe(_url: str) -> BafuFreshnessResult:
    return BafuFreshnessResult(
        found=True,
        checked_at=_NOW - BAFU_STALE_THRESHOLD - timedelta(hours=1),
        status="ok",
        error=None,
    )


def _bafu_not_found_probe(_url: str) -> BafuFreshnessResult:
    return BafuFreshnessResult(found=False, checked_at=None, status=None, error="404")


def _bafu_degraded_probe(_url: str) -> BafuFreshnessResult:
    return BafuFreshnessResult(
        found=True, checked_at=_NOW, status="warning", error=None
    )


def _bafu_obs_ok_probe(_url: str) -> BafuFreshnessResult:
    # Healthy heartbeat for the Plan 136 observation check — used as the
    # default `bafu_obs_probe` fake for every pre-existing test in this file
    # so the new additive check doesn't change their behaviour (exactly the
    # role `_bafu_ok_probe` plays for the forecast check above).
    return BafuFreshnessResult(found=True, checked_at=_NOW, status="ok", error=None)


def _bafu_obs_stale_probe(_url: str) -> BafuFreshnessResult:
    return BafuFreshnessResult(
        found=True,
        checked_at=_NOW - BAFU_OBS_STALE_THRESHOLD - timedelta(hours=1),
        status="ok",
        error=None,
    )


def _bafu_obs_not_found_probe(_url: str) -> BafuFreshnessResult:
    return BafuFreshnessResult(found=False, checked_at=None, status=None, error="404")


def _bafu_obs_degraded_probe(_url: str) -> BafuFreshnessResult:
    return BafuFreshnessResult(
        found=True, checked_at=_NOW, status="warning", error=None
    )


def _forecast_freshness_ok_probe(_url: str) -> ForecastFreshnessResult:
    # Healthy heartbeat for the Plan 116 forecast-freshness check — used as
    # the default `forecast_freshness_probe` fake for every pre-existing
    # test in this file so the new additive check doesn't change their
    # behaviour (exactly the role `_bafu_obs_ok_probe` plays for Plan 136).
    # `cycle_time` (not `checked_at`) is what `run_once` ages by (fixer
    # round, correctness requirement 2) — both are `_NOW` here since these
    # fakes aren't testing the checked_at/cycle_time distinction itself (see
    # `TestRunOnceForecastFreshness.test_stale_cycle_time_alerts_even_when_
    # checked_at_is_fresh` for that).
    return ForecastFreshnessResult(
        found=True, checked_at=_NOW, cycle_time=_NOW, status="ok", error=None
    )


_LABEL_A, _LABEL_B = MONITORED_LAUNCHD_LABELS


def _launchd_ok_probe(labels: Sequence[str]) -> dict[str, LaunchdVerdict]:
    # Plan 195: healthy verdict for every monitored label — used as the
    # default `launchd_probe` fake for every pre-existing test in this file
    # so the new additive check doesn't change their behaviour (the same
    # isolation role `_bafu_ok_probe` and `backup_device_verifier=lambda _:
    # True` play for their respective checks). A REAL default would shell
    # out to `launchctl` and, on a host/CI runner without the two monitored
    # labels installed, report them ABSENT — turning ~90 unrelated tests
    # red.
    return {label: LaunchdVerdict(kind="ok") for label in labels}


def _launchd_verdicts(
    *,
    failing: Sequence[str] = (),
    absent: Sequence[str] = (),
    unknown: bool = False,
    unknown_labels: Sequence[str] = (),
) -> dict[str, LaunchdVerdict]:
    """Build a full MONITORED_LAUNCHD_LABELS verdict map for run_once tests.
    `unknown=True` simulates a probe-unreadable tick (Plan 195 D4) — every
    label degrades together, matching `parse_launchctl_list_output`'s
    all-or-nothing contract for unparseable output. `unknown_labels`
    simulates a MIXED-result probe (fixer round, blocker 1) — the probe
    overall is readable (other labels resolve normally), but a specific
    row was individually unparseable/absent-from-the-parse, e.g. a
    malformed launchctl row for just that one label."""
    if unknown:
        return {
            label: LaunchdVerdict(kind="unknown") for label in MONITORED_LAUNCHD_LABELS
        }
    verdicts: dict[str, LaunchdVerdict] = {}
    for label in MONITORED_LAUNCHD_LABELS:
        if label in unknown_labels:
            verdicts[label] = LaunchdVerdict(kind="unknown")
        elif label in absent:
            verdicts[label] = LaunchdVerdict(kind="absent")
        elif label in failing:
            verdicts[label] = LaunchdVerdict(kind="failing", status=1)
        else:
            verdicts[label] = LaunchdVerdict(kind="ok")
    return verdicts


class _LaunchdProbeSequence:
    """Returns each dict in `ticks` in call order; the last entry repeats
    once exhausted, so a test only needs to spell out the ticks that
    matter."""

    def __init__(self, ticks: list[dict[str, LaunchdVerdict]]) -> None:
        self._ticks = ticks
        self._i = 0

    def __call__(self, labels: Sequence[str]) -> dict[str, LaunchdVerdict]:
        tick = self._ticks[min(self._i, len(self._ticks) - 1)]
        self._i += 1
        return tick


def _forecast_freshness_stale_probe(_url: str) -> ForecastFreshnessResult:
    stale_time = _NOW - FORECAST_FRESHNESS_STALE_THRESHOLD - timedelta(hours=1)
    return ForecastFreshnessResult(
        found=True,
        checked_at=stale_time,
        cycle_time=stale_time,
        status="ok",
        error=None,
    )


def _forecast_freshness_not_found_probe(_url: str) -> ForecastFreshnessResult:
    return ForecastFreshnessResult(
        found=False, checked_at=None, cycle_time=None, status=None, error="404"
    )


def _forecast_freshness_critical_probe(_url: str) -> ForecastFreshnessResult:
    return ForecastFreshnessResult(
        found=True, checked_at=_NOW, cycle_time=_NOW, status="critical", error=None
    )


class _SlackRecorder:
    def __init__(self, succeed: bool = True) -> None:
        self.calls: list[tuple[str, str]] = []
        self.succeed = succeed

    def __call__(self, url: str, message: str) -> bool:
        self.calls.append((url, message))
        return self.succeed


def _make_fresh_backup(tmp: Path, *, hours_ago: float) -> Path:
    path = tmp / "pg_dumps"
    path.mkdir(parents=True, exist_ok=True)
    # Plan 162 T4: the watchdog matches exactly `sapphire_*.dump` — an
    # underscore after `sapphire`, not a hyphen.
    dump = path / "sapphire_20260422_120000_abcd1234.dump"
    dump.write_bytes(b"dummy")
    ts = (_NOW - timedelta(hours=hours_ago)).timestamp()
    import os

    os.utime(dump, (ts, ts))
    return path


def _config(tmp: Path, *, backup_dir: Path | None = None) -> WatchdogConfig:
    state_path = tmp / "state.json"
    slack_path = tmp / "slack_webhook_url"
    return WatchdogConfig(
        health_url="http://localhost:8000/api/v1/health",
        backup_dir=backup_dir or (tmp / "pg_dumps_missing"),
        state_path=state_path,
        slack_path=slack_path,
        # Plan 163 fixer round: isolate every ordinary test from the REAL
        # production dead-man URL/poster. `WatchdogConfig.deadman_url_path`
        # defaults to the RELATIVE `DEFAULT_DEADMAN_PATH`
        # ("./secrets/deadman_url"); on a checkout where that host secret is
        # present (e.g. the mac-mini), every `run_once` test that doesn't
        # override `deadman_poster` would resolve it and fire a REAL
        # heartbeat at the production Healthchecks check via the real
        # `default_deadman_poster` — masking an actually-dead watchdog.
        # Point at a tmp_path file that is never written, so
        # `read_deadman_url` always returns None for the ~100 tests that use
        # this helper without opting into `_config_with_deadman`.
        deadman_url_path=tmp / "deadman_url_not_configured",
    )


def _clock() -> datetime:
    return _NOW


@pytest.fixture(autouse=True)
def _default_disk_usage_is_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fixer round (independent review, MAJOR): `run_once`'s `disk_probe`
    parameter defaults to `probe_disk_free`, which calls the REAL
    `shutil.disk_usage` on `config.disk_path` (`/` by default) unless a
    test injects its own `disk_probe`. Of the ~100 `run_once` call sites in
    this file, only the disk-space test classes do that — every other
    call depends on the ACTUAL free space of the machine running the
    suite, which can (and, on the mac mini, does) fall below the 5% floor
    and start asserting Slack calls no test author wrote the test to
    expect. Deterministically healthy for every test by default.

    A test that injects `disk_probe=` directly into `run_once` bypasses
    `probe_disk_free`/`shutil.disk_usage` entirely, so this fixture is
    inert for it either way. A test that monkeypatches
    `shutil.disk_usage` itself (see `TestDiskSpaceOk`) simply applies its
    own patch afterwards, in the same `monkeypatch` fixture instance, and
    wins. `TestDiskSpaceOk.test_probe_disk_free_uses_real_shutil_disk_usage`
    opts OUT of this fixture (via `monkeypatch.undo()`) to prove the
    genuine, unstubbed production path still works.
    """
    import shutil

    class _HealthyUsage:
        total = 1_000_000_000_000
        free = 900_000_000_000
        used = 100_000_000_000

    monkeypatch.setattr(shutil, "disk_usage", lambda _path: _HealthyUsage())


# ---------- should_alert_health ------------------------------------------------


class TestShouldAlertHealth:
    def test_first_failure_alerts(self) -> None:
        assert should_alert_health(0, current_ok=False, current_fail=True) is True

    def test_second_failure_does_not_alert(self) -> None:
        assert should_alert_health(1, current_ok=False, current_fail=True) is False

    def test_sixth_failure_alerts(self) -> None:
        # prev=5 + current fail -> count becomes 6 -> % 6 == 0
        assert (
            should_alert_health(
                ALERT_REPEAT_EVERY - 1, current_ok=False, current_fail=True
            )
            is True
        )

    def test_seventh_failure_does_not_alert(self) -> None:
        assert (
            should_alert_health(ALERT_REPEAT_EVERY, current_ok=False, current_fail=True)
            is False
        )

    def test_recovery_alerts(self) -> None:
        assert should_alert_health(3, current_ok=True, current_fail=False) is True

    def test_all_ok_no_previous_failures_does_not_alert(self) -> None:
        assert should_alert_health(0, current_ok=True, current_fail=False) is False


# ---------- read_slack_webhook ------------------------------------------------


class TestReadSlackWebhook:
    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert read_slack_webhook(tmp_path / "nope") is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "slack"
        p.write_text("")
        assert read_slack_webhook(p) is None

    def test_whitespace_only_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "slack"
        p.write_text("   \n  \n")
        assert read_slack_webhook(p) is None

    def test_populated_returns_stripped(self, tmp_path: Path) -> None:
        p = tmp_path / "slack"
        p.write_text("https://hooks.slack.com/XXX\n")
        assert read_slack_webhook(p) == "https://hooks.slack.com/XXX"


# ---------- read_probe_token (Plan 147 Slice C) -------------------------------


class TestReadProbeToken:
    """Mirrors TestReadSlackWebhook — the admin probe token is a HOST
    secret file, same convention, same missing/empty/unreadable handling."""

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert read_probe_token(tmp_path / "nope") is None

    def test_unreadable_file_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The `except OSError` branch (permission-denied, etc) — not
        reachable via chmod alone in CI (often runs as root, which bypasses
        POSIX permission checks), so monkeypatch `Path.read_text` directly
        to raise, mirroring how `path.exists()` still sees the real file."""
        p = tmp_path / "token"
        p.write_text("abc123.secret\n")

        def _raise(self: Path, *args: object, **kwargs: object) -> str:
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "read_text", _raise)
        assert read_probe_token(p) is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "token"
        p.write_text("")
        assert read_probe_token(p) is None

    def test_whitespace_only_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "token"
        p.write_text("   \n  \n")
        assert read_probe_token(p) is None

    def test_populated_returns_stripped(self, tmp_path: Path) -> None:
        p = tmp_path / "token"
        p.write_text("abc123.secret\n")
        assert read_probe_token(p) == "abc123.secret"


# ---------- newest_backup_mtime ----------------------------------------------


class TestNewestBackupMtime:
    def test_missing_dir_returns_none(self, tmp_path: Path) -> None:
        assert newest_backup_mtime(tmp_path / "nope") is None

    def test_empty_dir_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "dumps").mkdir()
        assert newest_backup_mtime(tmp_path / "dumps") is None

    def test_picks_newest_dump(self, tmp_path: Path) -> None:
        import os

        d = tmp_path / "dumps"
        d.mkdir()
        old = d / "sapphire_old_11111111.dump"
        new = d / "sapphire_new_22222222.dump"
        old.write_bytes(b"1")
        new.write_bytes(b"2")
        old_ts = (_NOW - timedelta(hours=48)).timestamp()
        new_ts = (_NOW - timedelta(hours=2)).timestamp()
        os.utime(old, (old_ts, old_ts))
        os.utime(new, (new_ts, new_ts))
        result = newest_backup_mtime(d)
        assert result is not None
        assert abs((result - (_NOW - timedelta(hours=2))).total_seconds()) < 1.0

    def test_ignores_files_not_matching_sapphire_prefix(self, tmp_path: Path) -> None:
        # Only `sapphire_*.dump` counts as evidence — a stray `.dump` file
        # (pre-Plan-162 glob was bare `*.dump`) must not satisfy freshness.
        import os

        d = tmp_path / "dumps"
        d.mkdir()
        other = d / "other-2026-04-22.dump"
        other.write_bytes(b"x")
        os.utime(other, (_NOW.timestamp(), _NOW.timestamp()))
        assert newest_backup_mtime(d) is None

    def test_zero_byte_file_is_not_fresh(self, tmp_path: Path) -> None:
        """Plan 162 T4 — freshness requires size > 0. RED against the
        pre-T4 watchdog: it only checked mtime via `entry.stat()`, so a
        0-byte file (a failed dump under the old direct-write path) with a
        fresh mtime was reported as evidence of a good backup — exactly the
        "loud on failure" defect this plan closes."""
        import os

        d = tmp_path / "dumps"
        d.mkdir()
        empty = d / "sapphire_20260422_120000_deadbeef.dump"
        empty.write_bytes(b"")
        os.utime(empty, (_NOW.timestamp(), _NOW.timestamp()))
        assert newest_backup_mtime(d) is None

    def test_zero_byte_file_does_not_shadow_an_older_valid_one(
        self, tmp_path: Path
    ) -> None:
        import os

        d = tmp_path / "dumps"
        d.mkdir()
        valid = d / "sapphire_old_11111111.dump"
        valid.write_bytes(b"real content")
        valid_ts = (_NOW - timedelta(hours=5)).timestamp()
        os.utime(valid, (valid_ts, valid_ts))

        empty = d / "sapphire_new_22222222.dump"
        empty.write_bytes(b"")
        os.utime(empty, (_NOW.timestamp(), _NOW.timestamp()))

        result = newest_backup_mtime(d)
        assert result is not None
        assert abs((result - (_NOW - timedelta(hours=5))).total_seconds()) < 1.0

    def test_symlink_is_not_fresh(self, tmp_path: Path) -> None:
        """Plan 162 T4 — `lstat()` + `stat.S_ISREG`, not `Path.is_file()`
        (which follows symlinks). RED against the pre-T4 watchdog: a fresh
        symlink named like a dump satisfied `entry.stat()` (which follows
        the link) and was reported as evidence, even pointing at nothing."""
        import os

        d = tmp_path / "dumps"
        d.mkdir()
        target = tmp_path / "not_a_backup.txt"
        target.write_bytes(b"not a real dump")
        link = d / "sapphire_20260422_120000_cafef00d.dump"
        link.symlink_to(target)
        os.utime(link, (_NOW.timestamp(), _NOW.timestamp()), follow_symlinks=False)
        assert newest_backup_mtime(d) is None

    def test_directory_matching_glob_is_not_fresh(self, tmp_path: Path) -> None:
        d = tmp_path / "dumps"
        d.mkdir()
        (d / "sapphire_20260422_120000_00000000.dump").mkdir()
        assert newest_backup_mtime(d) is None


# ---------- parse_launchctl_list_output (Plan 195 D1/T1) ----------------------


class TestParseLaunchctlListOutput:
    """Real captured `launchctl list` shape (PID, last exit status, label —
    `launchctl(1)`, NOT `print`, whose format Apple explicitly disclaims).
    Guarded (see the module-level try/except above) so an absent symbol
    fails every method here as one genuine RED assertion, not a
    collection-time ImportError."""

    @pytest.fixture(autouse=True)
    def _require_parse_launchctl_list_output(self) -> None:
        assert parse_launchctl_list_output is not None, (
            "parse_launchctl_list_output is not implemented yet (Plan 195)"
        )

    _REAL_OUTPUT = (
        "PID\tStatus\tLabel\n"
        "-\t0\tcom.apple.something\n"
        f"1234\t0\t{_LABEL_A}\n"
        f"-\t1\t{_LABEL_B}\n"
    )

    def test_ok_for_zero_status(self) -> None:
        verdicts = parse_launchctl_list_output(self._REAL_OUTPUT, [_LABEL_A])
        assert verdicts[_LABEL_A] == LaunchdVerdict(kind="ok")

    def test_failing_for_positive_status(self) -> None:
        verdicts = parse_launchctl_list_output(self._REAL_OUTPUT, [_LABEL_B])
        assert verdicts[_LABEL_B] == LaunchdVerdict(kind="failing", status=1)

    def test_failing_for_negative_status_sigterm(self) -> None:
        # The Plan-105-style trap: `status > 0` alone would miss this — a
        # negated stopping signal (SIGTERM == 15) is still a failure.
        output = f"PID\tStatus\tLabel\n-\t-15\t{_LABEL_A}\n"
        verdicts = parse_launchctl_list_output(output, [_LABEL_A])
        assert verdicts[_LABEL_A] == LaunchdVerdict(kind="failing", status=-15)

    def test_absent_for_label_missing_from_output(self) -> None:
        # Neither _LABEL_A nor _LABEL_B is in _REAL_OUTPUT under a label
        # that never appears there at all.
        missing_label = "ch.hydrosolutions.sapphire-never-installed"
        verdicts = parse_launchctl_list_output(self._REAL_OUTPUT, [missing_label])
        assert verdicts[missing_label] == LaunchdVerdict(kind="absent")

    def test_unknown_for_unparseable_output(self) -> None:
        garbage = "this is not launchctl output at all\nneither is this line\n"
        verdicts = parse_launchctl_list_output(garbage, MONITORED_LAUNCHD_LABELS)
        assert all(v == LaunchdVerdict(kind="unknown") for v in verdicts.values())

    def test_unknown_for_empty_output(self) -> None:
        verdicts = parse_launchctl_list_output("", MONITORED_LAUNCHD_LABELS)
        assert all(v == LaunchdVerdict(kind="unknown") for v in verdicts.values())

    def test_unknown_for_nonintegral_status_not_absent(self) -> None:
        # Three whitespace-separated fields (the documented shape), but the
        # status column is not an integer at all — a naive `except
        # ValueError: continue` drops the row silently, which would leave
        # this label out of `entries` and misreport it as ABSENT ("not
        # loaded") rather than UNKNOWN ("could not be read").
        output = f"PID\tStatus\tLabel\n-\tnot-a-number\t{_LABEL_A}\n"
        verdicts = parse_launchctl_list_output(output, [_LABEL_A])
        assert verdicts[_LABEL_A] == LaunchdVerdict(kind="unknown")

    def test_unknown_for_nonintegral_pid_not_ok(self) -> None:
        # The PID column is neither `-` nor a number — the documented
        # table shape is violated even though the status column parses
        # fine and would otherwise be accepted as OK.
        output = f"weird-pid\t0\t{_LABEL_A}\n"
        verdicts = parse_launchctl_list_output(output, [_LABEL_A])
        assert verdicts[_LABEL_A] == LaunchdVerdict(kind="unknown")

    def test_malformed_row_for_one_label_does_not_affect_a_wellformed_sibling(
        self,
    ) -> None:
        output = f"PID\tStatus\tLabel\n-\tbroken\t{_LABEL_A}\n-\t0\t{_LABEL_B}\n"
        verdicts = parse_launchctl_list_output(output, [_LABEL_A, _LABEL_B])
        assert verdicts[_LABEL_A] == LaunchdVerdict(kind="unknown")
        assert verdicts[_LABEL_B] == LaunchdVerdict(kind="ok")


# ---------- probe_launchd_agents (Plan 195 D1/D4/T1) ---------------------------


class TestProbeLaunchdAgents:
    @pytest.fixture(autouse=True)
    def _require_probe_launchd_agents(self) -> None:
        assert probe_launchd_agents is not None, (
            "probe_launchd_agents is not implemented yet (Plan 195)"
        )

    def test_timeout_is_contained_as_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise_timeout(*_args: object, **_kwargs: object) -> None:
            raise subprocess.TimeoutExpired(cmd="launchctl", timeout=5.0)

        monkeypatch.setattr(subprocess, "run", _raise_timeout)
        verdicts = probe_launchd_agents(MONITORED_LAUNCHD_LABELS)
        assert all(v.kind == "unknown" for v in verdicts.values())

    def test_missing_executable_is_contained_as_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise_not_found(*_args: object, **_kwargs: object) -> None:
            raise FileNotFoundError("launchctl not found")

        monkeypatch.setattr(subprocess, "run", _raise_not_found)
        verdicts = probe_launchd_agents(MONITORED_LAUNCHD_LABELS)
        assert all(v.kind == "unknown" for v in verdicts.values())

    def test_malformed_output_is_contained_as_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _FakeCompleted:
            stdout = "garbage\nnot a launchctl table\n"
            returncode = 0

        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: _FakeCompleted(),  # noqa: ARG005
        )
        verdicts = probe_launchd_agents(MONITORED_LAUNCHD_LABELS)
        assert all(v.kind == "unknown" for v in verdicts.values())

    def test_nonzero_exit_is_contained_as_unknown_even_with_parseable_stdout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed `launchctl list` invocation must never be trusted as
        healthy merely because its stdout happens to parse — `check=False`
        means a non-zero exit does not raise, so the return code has to be
        checked explicitly."""

        class _FakeCompleted:
            stdout = f"PID\tStatus\tLabel\n-\t0\t{_LABEL_A}\n"
            returncode = 1

        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: _FakeCompleted(),  # noqa: ARG005
        )
        verdicts = probe_launchd_agents(MONITORED_LAUNCHD_LABELS)
        assert all(v.kind == "unknown" for v in verdicts.values())

    def test_undecodable_output_degrades_to_unknown_not_a_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`text=True` decodes stdout, and launchctl output is not guaranteed
        valid UTF-8. UnicodeDecodeError is neither OSError nor TimeoutExpired,
        so an uncaught one would propagate out of `run_once` and skip BAFU,
        forecast freshness, disk, the state dump and the dead-man ping — D4's
        "must never abort the tick"."""

        def _raise_decode(*_args: object, **_kwargs: object) -> object:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

        monkeypatch.setattr(subprocess, "run", _raise_decode)
        verdicts = probe_launchd_agents(MONITORED_LAUNCHD_LABELS)
        assert {v.kind for v in verdicts.values()} == {"unknown"}, verdicts

    def test_unexpected_probe_error_degrades_to_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The final defensive boundary: any unanticipated failure in the
        probe degrades to UNKNOWN rather than taking the whole tick down."""

        def _raise_odd(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("something unforeseen")

        monkeypatch.setattr(subprocess, "run", _raise_odd)
        verdicts = probe_launchd_agents(MONITORED_LAUNCHD_LABELS)
        assert {v.kind for v in verdicts.values()} == {"unknown"}, verdicts

    def test_subprocess_call_receives_the_configured_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not merely that a SIMULATED timeout is caught — that the real
        subprocess call is actually given the 5.0s budget, not left to the
        platform default (which could hang the tick near the plist's 300s
        `StartInterval`)."""
        captured: dict[str, object] = {}

        class _FakeCompleted:
            stdout = "PID\tStatus\tLabel\n"
            returncode = 0

        def _fake_run(*args: object, **kwargs: object) -> _FakeCompleted:
            captured["args"] = args
            captured["kwargs"] = kwargs
            return _FakeCompleted()

        monkeypatch.setattr(subprocess, "run", _fake_run)
        probe_launchd_agents(MONITORED_LAUNCHD_LABELS)

        assert captured["args"] == (["launchctl", "list"],)
        assert captured["kwargs"]["timeout"] == LAUNCHD_PROBE_TIMEOUT_S  # type: ignore[index]
        # Lock the VALUE, not just the wiring: comparing only against the
        # constant passes for any value, including a "finite" 300 s that
        # would consume the watchdog's whole 300 s StartInterval and delay
        # every later probe — the exact weakness Plan 195 D4 was revised to
        # close. 5.0 matches the sibling *_TIMEOUT_S budgets.
        assert LAUNCHD_PROBE_TIMEOUT_S == 5.0


class TestLaunchdParserRobustness:
    """D1 contract corners the first Codex round on the implementation named."""

    def test_extra_column_row_is_unknown_not_absent(self) -> None:
        """A monitored label on a structurally malformed row must degrade to
        UNKNOWN. Falling through to ABSENT would open a false 'agent
        unloaded' incident from nothing but an unexpected column."""
        label = MONITORED_LAUNCHD_LABELS[0]
        other = MONITORED_LAUNCHD_LABELS[1]
        out = f"-\t0\t{other}\n-\t0\textra\t{label}\n"
        verdicts = parse_launchctl_list_output(out, MONITORED_LAUNCHD_LABELS)
        assert verdicts[label].kind == "unknown", verdicts
        assert verdicts[other].kind == "ok", verdicts

    def test_never_run_dash_pid_with_spaces_is_ok(self) -> None:
        """The real never-run shape is `-  0  <label>` (measured on the mini
        2026-08-21: docker-prune reads `runs = 0` under `print` and `-  0`
        under `list`), and columns may be space- rather than tab-separated."""
        label = MONITORED_LAUNCHD_LABELS[0]
        verdicts = parse_launchctl_list_output(
            f"-   0   {label}\n", MONITORED_LAUNCHD_LABELS
        )
        assert verdicts[label].kind == "ok", verdicts


# ---------- MONITORED_LAUNCHD_LABELS parity with the installer (Plan 195 D2) --


class TestMonitoredLaunchdLabelsParity:
    def test_matches_installer_plists_minus_watchdog(self) -> None:
        installer = (
            Path(__file__).resolve().parents[3]
            / "scripts"
            / "launchd"
            / "install-launchd.sh"
        )
        text = installer.read_text()
        import re

        match = re.search(r"PLISTS=\((.*?)\)", text, re.DOTALL)
        assert match is not None, "install-launchd.sh PLISTS array not found"
        installer_labels = {
            entry.strip().strip('"').removesuffix(".plist")
            for entry in match.group(1).splitlines()
            if entry.strip()
        }
        expected = installer_labels - {"ch.hydrosolutions.sapphire-watchdog"}
        assert set(MONITORED_LAUNCHD_LABELS) == expected, (
            "MONITORED_LAUNCHD_LABELS has drifted from install-launchd.sh's "
            "PLISTS array — a label added to the installer must also be "
            "added to MONITORED_LAUNCHD_LABELS, or it stays unmonitored."
        )


# ---------- backup_target_verified (Plan 194 D1) ------------------------------


class TestBackupTargetVerified:
    """The predicate `run_once`'s wrong-device check is built on. Before
    this function existed, the guarded import above set it to None; the
    autouse fixture below turns that into one genuine RED assertion per
    test method (not a collection-time ImportError that would mask every
    OTHER test in this file), the red-first starting point for Plan 194
    T3."""

    @pytest.fixture(autouse=True)
    def _require_backup_target_verified(self) -> None:
        assert backup_target_verified is not None, (
            "backup_target_verified is not implemented yet (Plan 194)"
        )

    def test_missing_backup_dir_is_not_verified(self, tmp_path: Path) -> None:
        assert backup_target_verified(tmp_path / "nope", data_dir=tmp_path) is False

    def test_plain_directory_same_device_is_not_verified(self, tmp_path: Path) -> None:
        """The exact mac-mini bug (2026-08-20): a bind-mount host path
        Docker silently created is a plain directory on the SAME device as
        the data it protects."""
        backup_dir = tmp_path / "pg_dumps"
        backup_dir.mkdir()
        assert backup_target_verified(backup_dir, data_dir=tmp_path) is False

    def test_different_device_but_not_a_mount_point_is_not_verified(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Defense-in-depth (Plan 194 D1): even if the device id happens to
        differ, a parent directory `Path.is_mount()` disagrees with is not
        proof of a real attached volume."""
        backup_dir = tmp_path / "pg_dumps"
        backup_dir.mkdir()
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        def fake_device_id(path: Path) -> int | None:
            return 99 if path == backup_dir else 1

        monkeypatch.setattr("sapphire_flow.ops.watchdog._device_id", fake_device_id)
        monkeypatch.setattr(Path, "is_mount", lambda self: False)

        assert backup_target_verified(backup_dir, data_dir=data_dir) is False

    def test_distinct_device_and_real_mount_point_is_verified(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backup_dir = tmp_path / "pg_dumps"
        backup_dir.mkdir()
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        def fake_device_id(path: Path) -> int | None:
            if path == backup_dir:
                return 99
            if path == data_dir:
                return 1
            return None

        monkeypatch.setattr("sapphire_flow.ops.watchdog._device_id", fake_device_id)
        monkeypatch.setattr(Path, "is_mount", lambda self: True)

        assert backup_target_verified(backup_dir, data_dir=data_dir) is True


# ---------- run_once: happy path ---------------------------------------------


class TestRunOnceHappyPath:
    def test_healthy_with_fresh_backup_no_alert(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_health_failures == 0
        assert slack.calls == []


# ---------- run_once: health hysteresis --------------------------------------


class TestRunOnceHealth:
    def test_first_failure_alerts_slack(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_fail_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_health_failures == 1
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "health check FAILED" in msg
        assert "http_status: 503" in msg

    def test_second_failure_no_new_alert(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        # seed state as if we already alerted once
        WatchdogState(consecutive_health_failures=1).dump(cfg.state_path)
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_fail_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_health_failures == 2
        assert slack.calls == []

    def test_sixth_failure_alerts_again(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        WatchdogState(consecutive_health_failures=5).dump(cfg.state_path)
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_fail_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_health_failures == 6
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "health check FAILED" in msg

    def test_recovery_alerts(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        WatchdogState(consecutive_health_failures=3).dump(cfg.state_path)
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_health_failures == 0
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "RECOVERED" in msg

    def test_unreachable_probe_formats_correctly(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        run_once(
            config=cfg,
            clock=_clock,
            probe=_unreachable_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "http_status: unreachable" in msg


# ---------- run_once: backup staleness ---------------------------------------


class TestRunOnceBackup:
    def test_stale_backup_alerts(self, tmp_path: Path) -> None:
        # newest dump 30h old (> 26h threshold)
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=30)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "backup STALE" in msg
        hrs = int(BACKUP_STALE_THRESHOLD.total_seconds() // 3600)
        assert f"threshold: {hrs}h" in msg

    def test_no_dumps_alerts(self, tmp_path: Path) -> None:
        d = tmp_path / "empty_dumps"
        d.mkdir()
        cfg = _config(tmp_path, backup_dir=d)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "none found" in msg


# ---------- run_once: backup notification state machine (Plan 162 T4) --------


class TestRunOnceBackupNotificationStateMachine:
    """Absorbs Plan 161 T3: the pre-T4 watchdog alerted on EVERY stale tick
    (~288/day at 5 min intervals) — `stale -> repeated-stale -> recovered ->
    stale` must alert exactly on the 1st stale tick, stay SILENT on the
    repeat, alert once on recovery, then alert again on the NEW incident.
    RED against the pre-T4 watchdog: the 2nd (repeated-stale) tick asserts
    zero Slack calls but the old code posts unconditionally every tick.
    """

    def test_stale_then_repeated_stale_then_recovered_then_stale(
        self, tmp_path: Path
    ) -> None:
        import os

        backup_dir = tmp_path / "pg_dumps"
        backup_dir.mkdir()
        stale_dump = backup_dir / "sapphire_stale_11111111.dump"
        stale_dump.write_bytes(b"dummy")
        stale_ts = (_NOW - timedelta(hours=30)).timestamp()  # > 26h threshold
        os.utime(stale_dump, (stale_ts, stale_ts))

        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        # Tick 1: first stale tick -> alerts.
        slack1 = _SlackRecorder()
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack1,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.consecutive_backup_stale_failures == 1
        assert len(slack1.calls) == 1
        assert "backup STALE" in slack1.calls[0][1]

        # Tick 2: repeated stale -> hysteresis stays SILENT.
        slack2 = _SlackRecorder()
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack2,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.consecutive_backup_stale_failures == 2
        assert slack2.calls == []

        # Tick 3: a fresh dump lands (recovery) -> a single recovery alert.
        fresh_dump = backup_dir / "sapphire_fresh_22222222.dump"
        fresh_dump.write_bytes(b"dummy")
        fresh_ts = (_NOW - timedelta(hours=2)).timestamp()
        os.utime(fresh_dump, (fresh_ts, fresh_ts))

        slack3 = _SlackRecorder()
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack3,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.consecutive_backup_stale_failures == 0
        assert len(slack3.calls) == 1
        assert "backup RECOVERED" in slack3.calls[0][1]

        # Tick 4: the fresh dump ages out (no new backup landed) — a NEW
        # incident, so it alerts on the 1st tick again.
        fresh_dump.unlink()

        slack4 = _SlackRecorder()
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack4,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.consecutive_backup_stale_failures == 1
        assert len(slack4.calls) == 1
        assert "backup STALE" in slack4.calls[0][1]

    def test_failed_recovery_delivery_is_retried_until_it_succeeds(
        self, tmp_path: Path
    ) -> None:
        """The exact bug the `backup_notification_pending` field exists to
        close: a recovery alert lost to a failed Slack post must NOT be lost
        forever. RED against a state machine without persistent pending
        state: the 3rd tick's hysteresis check sees prev_failures == 0 (the
        recovery tick already reset it) and current_ok with prev_failures
        not > 0, so `should_alert_health` returns False and the retry never
        fires — this test's final assertion (exactly one successful post)
        would instead see zero.
        """
        import os

        backup_dir = tmp_path / "pg_dumps"
        backup_dir.mkdir()
        stale_dump = backup_dir / "sapphire_stale_11111111.dump"
        stale_dump.write_bytes(b"dummy")
        stale_ts = (_NOW - timedelta(hours=30)).timestamp()
        os.utime(stale_dump, (stale_ts, stale_ts))

        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        # Establish an incident (alert delivered successfully).
        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_SlackRecorder(succeed=True),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        # A fresh dump lands (recovery) — the RECOVERY tick's delivery FAILS.
        fresh_dump = backup_dir / "sapphire_fresh_22222222.dump"
        fresh_dump.write_bytes(b"dummy")
        fresh_ts = (_NOW - timedelta(hours=2)).timestamp()
        os.utime(fresh_dump, (fresh_ts, fresh_ts))

        failing_slack = _SlackRecorder(succeed=False)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=failing_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(failing_slack.calls) == 1
        assert state.backup_notification_pending == "recovered"
        assert state.consecutive_backup_stale_failures == 0

        # Next tick: still not stale, no NEW hysteresis trigger — but the
        # pending recovery notification must be retried.
        retry_slack = _SlackRecorder(succeed=True)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=retry_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(retry_slack.calls) == 1
        assert "backup RECOVERED" in retry_slack.calls[0][1]
        assert state.backup_notification_pending is None

        # A further tick must NOT re-send — pending was cleared.
        quiet_slack = _SlackRecorder(succeed=True)
        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=quiet_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert quiet_slack.calls == []

    def test_alert_once_survives_past_the_health_hysteresis_sixth_tick(
        self, tmp_path: Path
    ) -> None:
        """Plan 162 T4 review fix, red-first: reusing `should_alert_health`
        for backup staleness re-fires on the 6th consecutive failure (and
        every 6th after that) — not truly "alert once". RED against that
        reuse: after `ALERT_REPEAT_EVERY + 2` consecutive stale ticks, a
        SECOND alert would have fired at the 6th tick (per
        `ALERT_REPEAT_EVERY`), so `total_calls` would be 2, not 1."""
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=30)  # stays stale
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        total_calls = 0
        for _ in range(ALERT_REPEAT_EVERY + 2):
            slack = _SlackRecorder()
            run_once(
                config=cfg,
                clock=_clock,
                probe=_ok_probe,
                slack_poster=slack,
                bafu_probe=_bafu_ok_probe,
                bafu_obs_probe=_bafu_obs_ok_probe,
                forecast_freshness_probe=_forecast_freshness_ok_probe,
                backup_device_verifier=lambda _: True,
                launchd_probe=_launchd_ok_probe,
            )
            total_calls += len(slack.calls)

        assert total_calls == 1

    def test_failed_stale_delivery_followed_by_recovery_before_retry_reports_recovered(
        self, tmp_path: Path
    ) -> None:
        """Plan 162 T4 review fix, red-first: the empirical interleaving
        bug. Tick 1 goes stale and Slack delivery FAILS
        (`backup_notification_pending == "stale"`); tick 2 a FRESH dump
        lands before the retry. The eventually-delivered message must say
        RECOVERED — the CURRENT, true condition — never the stale
        `pending` value. RED against a fix that blindly resends `pending`:
        tick 2 would re-send "backup STALE" for a dump that is now fresh,
        and because delivery then "succeeds", the real recovery would
        never be reported."""
        import os

        backup_dir = tmp_path / "pg_dumps"
        backup_dir.mkdir()
        stale_dump = backup_dir / "sapphire_stale_11111111.dump"
        stale_dump.write_bytes(b"dummy")
        stale_ts = (_NOW - timedelta(hours=30)).timestamp()
        os.utime(stale_dump, (stale_ts, stale_ts))

        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        # Tick 1: goes stale, delivery FAILS.
        failing_slack = _SlackRecorder(succeed=False)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=failing_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(failing_slack.calls) == 1
        assert "backup STALE" in failing_slack.calls[0][1]
        assert state.backup_notification_pending == "stale"

        # A fresh dump lands BEFORE the retry.
        fresh_dump = backup_dir / "sapphire_fresh_22222222.dump"
        fresh_dump.write_bytes(b"dummy")
        fresh_ts = (_NOW - timedelta(hours=1)).timestamp()
        os.utime(fresh_dump, (fresh_ts, fresh_ts))

        # Tick 2: the retry delivers — must report RECOVERED, not STALE.
        retry_slack = _SlackRecorder(succeed=True)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=retry_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(retry_slack.calls) == 1
        assert "backup RECOVERED" in retry_slack.calls[0][1]
        assert state.backup_notification_pending is None
        assert state.consecutive_backup_stale_failures == 0

        # Tick 3: must stay silent — nothing pending, nothing changed.
        quiet_slack = _SlackRecorder(succeed=True)
        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=quiet_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert quiet_slack.calls == []

    def test_failed_recovery_then_new_staleness_before_retry_reports_stale(
        self, tmp_path: Path
    ) -> None:
        """Mirror of the case above: a recovery alert fails to deliver,
        then a NEW staleness event occurs before the retry. The
        eventually-delivered message must say STALE (the current truth),
        not the stale `pending == "recovered"` value."""
        import os

        backup_dir = tmp_path / "pg_dumps"
        backup_dir.mkdir()
        stale_dump = backup_dir / "sapphire_stale_11111111.dump"
        stale_dump.write_bytes(b"dummy")
        stale_ts = (_NOW - timedelta(hours=30)).timestamp()
        os.utime(stale_dump, (stale_ts, stale_ts))

        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        # Establish an incident (delivered successfully).
        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_SlackRecorder(succeed=True),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        # A fresh dump lands (recovery) — delivery FAILS.
        fresh_dump = backup_dir / "sapphire_fresh_22222222.dump"
        fresh_dump.write_bytes(b"dummy")
        fresh_ts = (_NOW - timedelta(hours=1)).timestamp()
        os.utime(fresh_dump, (fresh_ts, fresh_ts))

        failing_slack = _SlackRecorder(succeed=False)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=failing_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.backup_notification_pending == "recovered"

        # The fresh dump ages back out into staleness BEFORE the retry.
        fresh_dump.unlink()

        retry_slack = _SlackRecorder(succeed=True)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=retry_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(retry_slack.calls) == 1
        assert "backup STALE" in retry_slack.calls[0][1]
        assert state.backup_notification_pending is None
        assert state.consecutive_backup_stale_failures == 1

    def test_absent_webhook_does_not_set_pending(self, tmp_path: Path) -> None:
        # Log-only mode (no Slack configured) is not a delivery FAILURE —
        # it must not be retried forever once a webhook eventually appears
        # for an unrelated reason.
        stale_dir = _make_fresh_backup(tmp_path, hours_ago=30)
        cfg = _config(tmp_path, backup_dir=stale_dir)
        # deliberately do not write cfg.slack_path

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_SlackRecorder(),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.backup_notification_pending is None

    def test_pending_survives_webhook_becoming_absent_then_delivers_on_return(
        self, tmp_path: Path
    ) -> None:
        """Plan 162 T1-fixer-round MAJOR: a pending notification must be
        cleared ONLY by confirmed delivery — never merely because the
        webhook happens to be absent/unreadable on a LATER tick. Tick 1
        establishes an incident (delivered fine). Tick 2's recovery
        delivery FAILS -> pending == "recovered". Tick 3 the webhook file
        is gone entirely (absent/unreadable) -> pending must SURVIVE,
        unposted. Tick 4 the webhook returns -> the deferred RECOVERED
        message must finally be delivered and pending cleared.

        RED against the pre-fix branch (`else: ... pending=None`
        unconditionally whenever `webhook` is falsy): tick 3 would discard
        the pending "recovered" notification outright. By tick 4,
        `consecutive_backup_stale_failures` is already 0 from tick 2's
        recovery and stays 0 through tick 3 (not stale), so
        `_backup_notification_kind` sees `pending=None`,
        `was_stale=False`, `is_stale=False` -> returns `None`: NOTHING is
        sent at tick 4 either. The final assertion (exactly one delivered
        RECOVERED message) would instead see zero."""
        import os

        backup_dir = tmp_path / "pg_dumps"
        backup_dir.mkdir()
        stale_dump = backup_dir / "sapphire_stale_11111111.dump"
        stale_dump.write_bytes(b"dummy")
        stale_ts = (_NOW - timedelta(hours=30)).timestamp()
        os.utime(stale_dump, (stale_ts, stale_ts))

        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        # Tick 1: establish an incident, delivered fine.
        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_SlackRecorder(succeed=True),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        # Tick 2: a fresh dump lands (recovery) — delivery FAILS.
        fresh_dump = backup_dir / "sapphire_fresh_22222222.dump"
        fresh_dump.write_bytes(b"dummy")
        fresh_ts = (_NOW - timedelta(hours=2)).timestamp()
        os.utime(fresh_dump, (fresh_ts, fresh_ts))

        failing_slack = _SlackRecorder(succeed=False)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=failing_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(failing_slack.calls) == 1
        assert state.backup_notification_pending == "recovered"

        # Tick 3: the webhook file is gone entirely (absent/unreadable).
        # The still-pending recovery must NOT be attempted (no webhook to
        # post to) and must NOT be discarded either.
        cfg.slack_path.unlink()
        no_webhook_slack = _SlackRecorder()
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=no_webhook_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert no_webhook_slack.calls == []
        assert state.backup_notification_pending == "recovered"

        # Tick 4: the webhook returns — the deferred RECOVERED message
        # must finally be delivered, and pending cleared.
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        retry_slack = _SlackRecorder(succeed=True)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=retry_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(retry_slack.calls) == 1
        assert "backup RECOVERED" in retry_slack.calls[0][1]
        assert state.backup_notification_pending is None


# ---------- run_once: backup DEVICE verification (Plan 194 D1/D4/D6) ---------


class TestRunOnceBackupDeviceVerification:
    """A condition DISTINCT from staleness (D4): the backup dir can look
    perfectly fresh while sitting on the same device as the data it
    protects. D6: alerts on TRANSITION only, never every tick — before
    this existed, `backup_device_verifier` was not even accepted by
    `run_once`, so every case here fails red as a TypeError against the
    pre-Plan-194 signature."""

    def test_unverified_then_repeated_unverified_then_recovered(
        self, tmp_path: Path
    ) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)  # fresh: NOT stale
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        # Tick 1: first unverified tick -> alerts.
        slack1 = _SlackRecorder()
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack1,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: False,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.consecutive_backup_device_unverified_ticks == 1
        assert len(slack1.calls) == 1
        assert "backup volume NOT MOUNTED" in slack1.calls[0][1]

        # Tick 2: still unverified -> hysteresis stays SILENT (never every
        # tick — the mini's condition would otherwise fire ~288/day).
        slack2 = _SlackRecorder()
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack2,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: False,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.consecutive_backup_device_unverified_ticks == 2
        assert slack2.calls == []

        # Tick 3: the volume is verified again -> a single recovery alert.
        slack3 = _SlackRecorder()
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack3,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.consecutive_backup_device_unverified_ticks == 0
        assert len(slack3.calls) == 1
        assert "backup volume VERIFIED" in slack3.calls[0][1]

    def test_device_alert_does_not_suppress_or_duplicate_staleness_alert(
        self, tmp_path: Path
    ) -> None:
        """D4: the two conditions must never be folded together — a
        SIMULTANEOUSLY unverified AND stale backup must alert on BOTH,
        exactly once each, not have one swallow the other."""
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=30)  # stale
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: False,  # unverified too
            launchd_probe=_launchd_ok_probe,
        )

        messages = [msg for _, msg in slack.calls]
        assert len(messages) == 2
        assert any("backup volume NOT MOUNTED" in m for m in messages)
        assert any("backup STALE" in m for m in messages)

    def test_verified_and_fresh_backup_has_no_device_alert(
        self, tmp_path: Path
    ) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        slack = _SlackRecorder()

        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert slack.calls == []

    def test_device_check_receives_configured_backup_dir(self, tmp_path: Path) -> None:
        """The injected verifier must be called with `config.backup_dir` —
        not some other derived path — so a caller-supplied fake can assert
        on it."""
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        received: list[Path] = []

        def _spy_verifier(path: Path) -> bool:
            received.append(path)
            return True

        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_SlackRecorder(),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=_spy_verifier,
            launchd_probe=_launchd_ok_probe,
        )

        assert received == [backup_dir]


class TestRunOnceBackupDeviceNotificationStateMachine:
    """Fixer round (review of Plan 194): `TestRunOnceBackupDeviceVerification`
    above only locks the hysteresis/transition behaviour assuming every
    Slack post SUCCEEDS. The device block shares its pending-retry shape
    byte-for-byte with the staleness block's `_backup_notification_kind`
    (see `_backup_device_notification_kind`), whose OWN delivery-failure
    and condition-reversal-before-retry behaviour is locked by
    `TestRunOnceBackupNotificationStateMachine` — but the device block had
    no equivalent multi-tick lock of its own. Mirrors that class's
    `test_failed_recovery_delivery_is_retried_until_it_succeeds` and
    `test_failed_stale_delivery_followed_by_recovery_before_retry_reports_recovered`,
    but for the DISTINCT wrong-device condition."""

    def test_failed_unverified_delivery_is_retried_until_it_succeeds(
        self, tmp_path: Path
    ) -> None:
        """A NOT MOUNTED alert lost to a failed Slack post must not be lost
        forever."""
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)  # fresh: NOT stale
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        # Tick 1: unverified, delivery FAILS.
        failing_slack = _SlackRecorder(succeed=False)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=failing_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: False,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(failing_slack.calls) == 1
        assert "backup volume NOT MOUNTED" in failing_slack.calls[0][1]
        assert state.backup_device_notification_pending == "unverified"
        assert state.consecutive_backup_device_unverified_ticks == 1

        # Tick 2: still unverified — no NEW hysteresis transition (already
        # latched) — but the pending notification must be retried.
        retry_slack = _SlackRecorder(succeed=True)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=retry_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: False,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(retry_slack.calls) == 1
        assert "backup volume NOT MOUNTED" in retry_slack.calls[0][1]
        assert state.backup_device_notification_pending is None

        # A further tick must NOT re-send — pending was cleared, and the
        # condition has not transitioned again.
        quiet_slack = _SlackRecorder(succeed=True)
        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=quiet_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: False,
            launchd_probe=_launchd_ok_probe,
        )
        assert quiet_slack.calls == []

    def test_failed_verified_delivery_is_retried_until_it_succeeds(
        self, tmp_path: Path
    ) -> None:
        """The gap this class's docstring CLAIMED to cover but did not.

        It says it mirrors the staleness block's
        `test_failed_recovery_delivery_is_retried_until_it_succeeds`, but the
        two tests here only covered a failed UNVERIFIED (alert) delivery and a
        condition reversal before retry. A recovery — the VERIFIED post after
        the volume comes back — that is lost to a failed Slack call was never
        locked, so an implementation that dropped it would still pass.

        RED against a state machine without persistent pending state: the
        retry tick sees a verified device with no transition, so nothing is
        re-sent and the final assertion (exactly one successful VERIFIED post)
        sees zero.
        """
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)  # fresh: NOT stale
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        # Tick 1: unverified, alert DELIVERED cleanly — establishes the incident.
        opening = _SlackRecorder(succeed=True)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=opening,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: False,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(opening.calls) == 1
        assert "backup volume NOT MOUNTED" in opening.calls[0][1]
        assert state.backup_device_notification_pending is None

        # Tick 2: the volume comes back — the RECOVERY delivery FAILS.
        failing_recovery = _SlackRecorder(succeed=False)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=failing_recovery,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(failing_recovery.calls) == 1
        assert state.backup_device_notification_pending == "verified", (
            "a lost recovery post must stay pending, or the operator is never "
            "told the volume came back"
        )

        # Tick 3: still verified, no new transition — the pending recovery
        # must be retried anyway.
        retry = _SlackRecorder(succeed=True)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=retry,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(retry.calls) == 1
        assert state.backup_device_notification_pending is None

        # Tick 4: quiet — pending cleared, condition unchanged.
        quiet = _SlackRecorder(succeed=True)
        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=quiet,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert quiet.calls == []

    def test_failed_unverified_delivery_then_recovery_before_retry_reports_verified(
        self, tmp_path: Path
    ) -> None:
        """Condition-reversal-before-retry: tick 1 goes unverified and
        delivery FAILS (`pending == "unverified"`); tick 2 the volume is
        VERIFIED again before the retry. The eventually-delivered message
        must say VERIFIED — the CURRENT, true condition — never resend the
        stale `pending == "unverified"` value (which would report "NOT
        MOUNTED" for a volume that is, by tick 2, actually fine)."""
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        # Tick 1: unverified, delivery FAILS.
        failing_slack = _SlackRecorder(succeed=False)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=failing_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: False,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(failing_slack.calls) == 1
        assert "backup volume NOT MOUNTED" in failing_slack.calls[0][1]
        assert state.backup_device_notification_pending == "unverified"

        # Tick 2: verified again BEFORE the retry — the retry delivers and
        # must report VERIFIED, not NOT MOUNTED.
        retry_slack = _SlackRecorder(succeed=True)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=retry_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(retry_slack.calls) == 1
        assert "backup volume VERIFIED" in retry_slack.calls[0][1]
        assert state.backup_device_notification_pending is None
        assert state.consecutive_backup_device_unverified_ticks == 0

        # Tick 3: must stay silent — nothing pending, nothing changed.
        quiet_slack = _SlackRecorder(succeed=True)
        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=quiet_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert quiet_slack.calls == []


# ---------- run_once: launchd agent health (Plan 195) --------------------------


def _run_once_launchd(
    cfg: WatchdogConfig,
    *,
    slack: _SlackRecorder,
    launchd_probe: Callable[[Sequence[str]], dict[str, LaunchdVerdict]],
) -> WatchdogState:
    return run_once(
        config=cfg,
        clock=_clock,
        probe=_ok_probe,
        slack_poster=slack,
        bafu_probe=_bafu_ok_probe,
        bafu_obs_probe=_bafu_obs_ok_probe,
        forecast_freshness_probe=_forecast_freshness_ok_probe,
        backup_device_verifier=lambda _: True,
        launchd_probe=launchd_probe,
    )


class TestRunOnceLaunchdAgentHealth:
    """T2 blockers' tests (Plan 195 D3/D4). Each mirrors the shape already
    locked for the backup-device condition
    (`TestRunOnceBackupDeviceVerification` /
    `TestRunOnceBackupDeviceNotificationStateMachine`), but for the
    per-label failing-set + probe-unreadable pair introduced here."""

    def test_failing_agent_alerts_once_then_silent(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        tick1 = _SlackRecorder()
        state = _run_once_launchd(
            cfg,
            slack=tick1,
            launchd_probe=lambda _l: _launchd_verdicts(failing=[_LABEL_A]),
        )
        assert len(tick1.calls) == 1
        assert _LABEL_A in tick1.calls[0][1]
        assert "LAST RUN FAILED" in tick1.calls[0][1]
        assert state.failing_launchd_labels == (_LABEL_A,)

        tick2 = _SlackRecorder()
        _run_once_launchd(
            cfg,
            slack=tick2,
            launchd_probe=lambda _l: _launchd_verdicts(failing=[_LABEL_A]),
        )
        assert tick2.calls == []

    def test_second_agent_failing_raises_a_second_alert(self, tmp_path: Path) -> None:
        """The case a single boolean would swallow: A is already latched
        failing, then B fails too — B must still raise, and A must not
        repeat."""
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        tick1 = _SlackRecorder()
        _run_once_launchd(
            cfg,
            slack=tick1,
            launchd_probe=lambda _l: _launchd_verdicts(failing=[_LABEL_A]),
        )
        assert len(tick1.calls) == 1

        tick2 = _SlackRecorder()
        state = _run_once_launchd(
            cfg,
            slack=tick2,
            launchd_probe=lambda _l: _launchd_verdicts(failing=[_LABEL_A, _LABEL_B]),
        )
        assert len(tick2.calls) == 1, "B's new failure must raise exactly once"
        assert f"label: {_LABEL_B}," in tick2.calls[0][1]
        # _LABEL_A ("ch.hydrosolutions.sapphire") is a PREFIX of _LABEL_B
        # ("ch.hydrosolutions.sapphire-docker-prune") — a bare substring
        # check would false-fail on B's own message, so check the exact
        # "label: <A>," field boundary instead.
        assert f"label: {_LABEL_A}," not in tick2.calls[0][1]
        assert set(state.failing_launchd_labels) == {_LABEL_A, _LABEL_B}

    def test_failing_then_unknown_raises_no_recovery_and_preserves_incident(
        self, tmp_path: Path
    ) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        tick1 = _SlackRecorder()
        state = _run_once_launchd(
            cfg,
            slack=tick1,
            launchd_probe=lambda _l: _launchd_verdicts(failing=[_LABEL_A]),
        )
        assert state.failing_launchd_labels == (_LABEL_A,)

        tick2 = _SlackRecorder()
        state = _run_once_launchd(
            cfg, slack=tick2, launchd_probe=lambda _l: _launchd_verdicts(unknown=True)
        )
        messages = [msg for _, msg in tick2.calls]
        assert not any("launchd agent RECOVERED" in m for m in messages), (
            "an UNKNOWN tick must not report A recovered"
        )
        assert state.failing_launchd_labels == (_LABEL_A,), (
            "the incident must survive an UNKNOWN tick unchanged"
        )

    def test_mixed_result_probe_does_not_false_recover_the_unknown_label(
        self, tmp_path: Path
    ) -> None:
        """Fixer round blocker 1: a probe where SOME labels are known
        (probe_readable is True) must not treat a label whose OWN verdict
        is `unknown` as 'not failing'. A implementation that only guards
        the whole-probe UNKNOWN case (D4) and forgets the per-label case
        would resolve B's unknown verdict to `is_failing=False`, see
        `was_failing=True`, and fabricate a RECOVERED for an incident that
        never actually cleared."""
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        entry = _SlackRecorder()
        state = _run_once_launchd(
            cfg,
            slack=entry,
            launchd_probe=lambda _l: _launchd_verdicts(failing=[_LABEL_A, _LABEL_B]),
        )
        assert set(state.failing_launchd_labels) == {_LABEL_A, _LABEL_B}

        mixed = _SlackRecorder()
        state = _run_once_launchd(
            cfg,
            slack=mixed,
            launchd_probe=lambda _l: _launchd_verdicts(
                failing=[_LABEL_A], unknown_labels=[_LABEL_B]
            ),
        )
        messages = [msg for _, msg in mixed.calls]
        assert not any("RECOVERED" in m and _LABEL_B in m for m in messages), (
            "B's own unknown verdict must not be reported as recovered"
        )
        assert _LABEL_B in state.failing_launchd_labels, (
            "B's prior failing membership must carry forward unchanged "
            "while its own verdict is unknown"
        )
        assert _LABEL_A in state.failing_launchd_labels

    def test_mixed_result_probe_preserves_pending_payload_for_the_unknown_label(
        self, tmp_path: Path
    ) -> None:
        """Same mixed-result scenario, but B has an undelivered pending
        notification going in — that payload must be carried forward
        UNCHANGED (never dropped, never resolved) while B's own verdict is
        unknown."""
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        entry = _SlackRecorder(succeed=False)
        state = _run_once_launchd(
            cfg,
            slack=entry,
            launchd_probe=lambda _l: _launchd_verdicts(failing=[_LABEL_A, _LABEL_B]),
        )
        assert dict(state.launchd_notification_pending) == {
            _LABEL_A: "failing",
            _LABEL_B: "failing",
        }

        mixed = _SlackRecorder(succeed=True)
        state = _run_once_launchd(
            cfg,
            slack=mixed,
            launchd_probe=lambda _l: _launchd_verdicts(
                failing=[_LABEL_A], unknown_labels=[_LABEL_B]
            ),
        )
        # A's pending "failing" was successfully delivered (a repeat, since
        # A is still failing and nothing has changed for it) and cleared;
        # B's pending payload must survive B's own unknown verdict
        # untouched, ready to retry once B is readable again.
        assert dict(state.launchd_notification_pending) == {_LABEL_B: "failing"}
        assert _LABEL_B in state.failing_launchd_labels

    def test_absent_opens_an_incident(self, tmp_path: Path) -> None:
        """An implementation that only alerts on FAILING(status) would pass
        every other test in this class while an unloaded agent stays
        invisible — this plan's headline failure mode."""
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        slack = _SlackRecorder()
        state = _run_once_launchd(
            cfg,
            slack=slack,
            launchd_probe=lambda _l: _launchd_verdicts(absent=[_LABEL_A]),
        )
        assert len(slack.calls) == 1
        assert "ABSENT" in slack.calls[0][1]
        assert _LABEL_A in slack.calls[0][1]
        assert state.failing_launchd_labels == (_LABEL_A,)

    def test_failing_to_ok_emits_exactly_one_recovery(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        _run_once_launchd(
            cfg,
            slack=_SlackRecorder(),
            launchd_probe=lambda _l: _launchd_verdicts(failing=[_LABEL_A]),
        )

        recovery_tick = _SlackRecorder()
        state = _run_once_launchd(
            cfg, slack=recovery_tick, launchd_probe=_launchd_ok_probe
        )
        assert len(recovery_tick.calls) == 1
        assert "RECOVERED" in recovery_tick.calls[0][1]
        assert _LABEL_A in recovery_tick.calls[0][1]
        assert state.failing_launchd_labels == ()

        quiet_tick = _SlackRecorder()
        _run_once_launchd(cfg, slack=quiet_tick, launchd_probe=_launchd_ok_probe)
        assert quiet_tick.calls == []

    def test_two_simultaneous_transitions_both_survive_a_delivery_failure(
        self, tmp_path: Path
    ) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        failing_tick = _SlackRecorder(succeed=False)
        state = _run_once_launchd(
            cfg,
            slack=failing_tick,
            launchd_probe=lambda _l: _launchd_verdicts(failing=[_LABEL_A, _LABEL_B]),
        )
        assert len(failing_tick.calls) == 2
        pending = dict(state.launchd_notification_pending)
        assert pending == {_LABEL_A: "failing", _LABEL_B: "failing"}

        retry_tick = _SlackRecorder(succeed=True)
        state = _run_once_launchd(
            cfg,
            slack=retry_tick,
            launchd_probe=lambda _l: _launchd_verdicts(failing=[_LABEL_A, _LABEL_B]),
        )
        assert len(retry_tick.calls) == 2
        assert state.launchd_notification_pending == ()

    def test_failed_entry_delivery_is_retried_through_an_intervening_unknown_tick(
        self, tmp_path: Path
    ) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        entry = _SlackRecorder(succeed=False)
        state = _run_once_launchd(
            cfg,
            slack=entry,
            launchd_probe=lambda _l: _launchd_verdicts(failing=[_LABEL_A]),
        )
        assert len(entry.calls) == 1
        assert dict(state.launchd_notification_pending) == {_LABEL_A: "failing"}

        unknown_tick = _SlackRecorder(succeed=True)
        state = _run_once_launchd(
            cfg,
            slack=unknown_tick,
            launchd_probe=lambda _l: _launchd_verdicts(unknown=True),
        )
        # The per-label pending must be untouched by an UNKNOWN tick (only
        # the separate probe-unreadable slot may be delivered here).
        assert dict(state.launchd_notification_pending) == {_LABEL_A: "failing"}

        retry = _SlackRecorder(succeed=True)
        state = _run_once_launchd(
            cfg,
            slack=retry,
            launchd_probe=lambda _l: _launchd_verdicts(failing=[_LABEL_A]),
        )
        messages = [msg for _, msg in retry.calls]
        assert any(_LABEL_A in m and "LAST RUN FAILED" in m for m in messages)
        assert dict(state.launchd_notification_pending) == {}

    def test_reversal_before_retry_sends_recovery_not_stale_failure(
        self, tmp_path: Path
    ) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        entry = _SlackRecorder(succeed=False)
        state = _run_once_launchd(
            cfg,
            slack=entry,
            launchd_probe=lambda _l: _launchd_verdicts(failing=[_LABEL_A]),
        )
        assert dict(state.launchd_notification_pending) == {_LABEL_A: "failing"}

        retry = _SlackRecorder(succeed=True)
        state = _run_once_launchd(cfg, slack=retry, launchd_probe=_launchd_ok_probe)
        assert len(retry.calls) == 1
        assert "RECOVERED" in retry.calls[0][1]
        assert "LAST RUN FAILED" not in retry.calls[0][1]
        assert dict(state.launchd_notification_pending) == {}

    def test_probe_unreadable_pending_delivery_is_itself_retried(
        self, tmp_path: Path
    ) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        entry = _SlackRecorder(succeed=False)
        state = _run_once_launchd(
            cfg, slack=entry, launchd_probe=lambda _l: _launchd_verdicts(unknown=True)
        )
        assert len(entry.calls) == 1
        assert state.launchd_probe_notification_pending == "unreadable"

        retry = _SlackRecorder(succeed=True)
        state = _run_once_launchd(
            cfg, slack=retry, launchd_probe=lambda _l: _launchd_verdicts(unknown=True)
        )
        assert len(retry.calls) == 1
        assert state.launchd_probe_notification_pending is None

    def test_probe_reversal_before_retry_sends_recovery_not_stale_unreadable(
        self, tmp_path: Path
    ) -> None:
        """Fixer round majors 4/7 — the probe-level mirror of
        `test_reversal_before_retry_sends_recovery_not_stale_failure`. The
        entry tick's Slack post fails while the probe is unreadable
        (`launchd_probe_notification_pending == "unreadable"`); before the
        retry fires, the CONDITION ITSELF reverses (the probe becomes
        readable again). A naive `_launchd_probe_notification_kind` coded
        as `if pending is not None: return pending` (replaying the stale
        kind instead of recomputing from the current verdict) would resend
        UNREADABLE here — the exact stale-failure-resend bug Plan
        162/194/195 all exist to prevent. The correct behaviour is exactly
        one RECOVERED message, never a stale UNREADABLE."""
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        entry = _SlackRecorder(succeed=False)
        state = _run_once_launchd(
            cfg, slack=entry, launchd_probe=lambda _l: _launchd_verdicts(unknown=True)
        )
        assert len(entry.calls) == 1
        assert state.launchd_probe_notification_pending == "unreadable"

        retry = _SlackRecorder(succeed=True)
        state = _run_once_launchd(cfg, slack=retry, launchd_probe=_launchd_ok_probe)
        assert len(retry.calls) == 1
        assert "RECOVERED" in retry.calls[0][1]
        assert "UNREADABLE" not in retry.calls[0][1]
        assert state.launchd_probe_notification_pending is None

    def test_probe_unreadable_alerts_once_stays_silent_then_recovers_once(
        self, tmp_path: Path
    ) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        baseline = _SlackRecorder()
        _run_once_launchd(cfg, slack=baseline, launchd_probe=_launchd_ok_probe)
        assert baseline.calls == []

        first = _SlackRecorder()
        state = _run_once_launchd(
            cfg, slack=first, launchd_probe=lambda _l: _launchd_verdicts(unknown=True)
        )
        assert len(first.calls) == 1
        assert "UNREADABLE" in first.calls[0][1]
        assert state.failing_launchd_labels == (), (
            "per-label state must stay unchanged while the probe is unreadable"
        )

        silent = _SlackRecorder()
        state = _run_once_launchd(
            cfg, slack=silent, launchd_probe=lambda _l: _launchd_verdicts(unknown=True)
        )
        assert silent.calls == []
        assert state.failing_launchd_labels == ()

        recovered = _SlackRecorder()
        state = _run_once_launchd(cfg, slack=recovered, launchd_probe=_launchd_ok_probe)
        assert len(recovered.calls) == 1
        assert "RECOVERED" in recovered.calls[0][1]
        assert state.failing_launchd_labels == ()

    def test_later_probe_still_runs_after_launchd_probe_times_out(
        self, tmp_path: Path
    ) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        deadman_path = _write_deadman_url(tmp_path)
        cfg = _config_with_deadman(
            tmp_path, deadman_path=deadman_path, backup_dir=backup_dir
        )
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        deadman = _DeadmanRecorder()

        bafu_calls: list[str] = []

        def _counting_bafu_probe(url: str) -> BafuFreshnessResult:
            bafu_calls.append(url)
            return _bafu_ok_probe(url)

        # A timed-out real probe would raise subprocess.TimeoutExpired
        # INSIDE probe_launchd_agents and be contained there, returning
        # UNKNOWN for every label — simulate that contained outcome here.
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_SlackRecorder(),
            bafu_probe=_counting_bafu_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=lambda _l: _launchd_verdicts(unknown=True),
            deadman_poster=deadman,
        )

        assert len(bafu_calls) == 1, "BAFU freshness must still probe"
        assert state.consecutive_bafu_failures == 0
        assert len(deadman.calls) == 1, "the dead-man heartbeat must still fire"

    def test_condition_neither_suppresses_nor_duplicates_the_health_alert(
        self, tmp_path: Path
    ) -> None:
        """A simultaneous, unrelated failure (the API health check) must
        keep alerting exactly as before — this condition is additive."""
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        slack = _SlackRecorder()
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_fail_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=lambda _l: _launchd_verdicts(failing=[_LABEL_A]),
        )
        messages = [msg for _, msg in slack.calls]
        assert any("health check FAILED" in m for m in messages)
        assert any(
            "launchd agent LAST RUN FAILED" in m and _LABEL_A in m for m in messages
        )
        assert len(slack.calls) == 2
        assert state.consecutive_health_failures == 1
        assert state.failing_launchd_labels == (_LABEL_A,)


# ---------- run_once: Slack absent => log-only -------------------------------


class TestRunOnceSlackBehaviour:
    def test_slack_absent_logs_only(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        # intentionally do NOT create cfg.slack_path
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_fail_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_health_failures == 1
        # hysteresis said "alert" but Slack was absent -> no post
        assert slack.calls == []

    def test_slack_present_posts(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/REAL")
        slack = _SlackRecorder()

        run_once(
            config=cfg,
            clock=_clock,
            probe=_fail_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert len(slack.calls) == 1
        url, _ = slack.calls[0]
        assert url == "https://hooks.slack.com/REAL"


# ---------- state round-trip -------------------------------------------------


class TestStateRoundTrip:
    def test_dump_load_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        original = WatchdogState(
            consecutive_health_failures=5,
            last_backup_alert_iso="2026-04-22T12:00:00+00:00",
            consecutive_bafu_failures=3,
        )
        original.dump(path)
        loaded = WatchdogState.load(path)
        assert loaded == original

    def test_load_missing_returns_defaults(self, tmp_path: Path) -> None:
        s = WatchdogState.load(tmp_path / "nope")
        assert s.consecutive_health_failures == 0
        assert s.last_backup_alert_iso is None
        assert s.consecutive_bafu_failures == 0

    def test_load_corrupt_returns_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "corrupt.json"
        p.write_text("not json at all {{{")
        s = WatchdogState.load(p)
        assert s.consecutive_health_failures == 0
        assert s.consecutive_bafu_failures == 0

    def test_load_state_written_before_bafu_hook_defaults_to_zero(
        self, tmp_path: Path
    ) -> None:
        # Backward compatibility: a state file predating the Flow 4
        # staleness hook has no `consecutive_bafu_failures` key at all.
        p = tmp_path / "old_state.json"
        p.write_text(
            '{"consecutive_health_failures": 2, "last_backup_alert_iso": null}'
        )
        s = WatchdogState.load(p)
        assert s.consecutive_bafu_failures == 0
        assert s.consecutive_health_failures == 2


# ---------- probe_health exercises httpx path --------------------------------


class TestProbeHealth:
    def test_probe_connection_error_returns_unreachable(self) -> None:
        from sapphire_flow.ops.watchdog import probe_health

        # unused port on localhost — should error instantly
        result = probe_health("http://127.0.0.1:1/api/v1/health")
        assert result.ok is False
        assert result.http_status is None
        assert result.error is not None


# ---------- probe_bafu_freshness exercises httpx path -------------------------


class TestProbeBafuFreshness:
    def test_probe_connection_error_returns_not_found(self) -> None:
        from sapphire_flow.ops.watchdog import probe_bafu_freshness

        # unused port on localhost — should error instantly, never raise
        result = probe_bafu_freshness("http://127.0.0.1:1/api/v1/health/detail")
        assert result.found is False
        assert result.checked_at is None
        assert result.error is not None

    def test_derives_bafu_url_from_custom_health_url(self) -> None:
        # Overriding --health-url must retarget the freshness probe too.
        from sapphire_flow.ops.watchdog import _bafu_url_from_health

        assert _bafu_url_from_health("http://custom:9000/api/v2/health") == (
            "http://custom:9000/api/v2/health/detail"
            "?check_type=bafu_forecast_freshness&limit=1"
        )

    def test_naive_checked_at_is_normalized_to_tz_aware(self) -> None:
        # A naive checked_at (no offset) must be normalized to tz-aware UTC, or
        # the `now - checked_at` comparison in run_once (outside try/except)
        # would raise TypeError and crash the whole watchdog tick.
        import httpx

        from sapphire_flow.ops.watchdog import probe_bafu_freshness

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "items": [{"checked_at": "2026-07-13T09:00:00", "status": "ok"}],
                    "total": 1,
                    "limit": 1,
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        result = probe_bafu_freshness("http://x/health/detail", client=client)
        assert result.found is True
        assert result.checked_at is not None
        assert result.checked_at.tzinfo is not None

    def test_sends_bearer_header_when_token_provided(self) -> None:
        """Plan 147 Slice C: /health/detail is admin-only once auth is
        enforced — the watchdog must present its admin probe token as a
        Bearer header, or every probe 401s and reports false staleness."""
        import httpx

        from sapphire_flow.ops.watchdog import probe_bafu_freshness

        seen_auth_headers: list[str | None] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen_auth_headers.append(req.headers.get("authorization"))
            return httpx.Response(200, json={"items": [], "total": 0, "limit": 1})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        probe_bafu_freshness(
            "http://x/health/detail", client=client, token="prefix.secret"
        )
        assert seen_auth_headers == ["Bearer prefix.secret"]

    def test_omits_authorization_header_when_token_absent(self) -> None:
        import httpx

        from sapphire_flow.ops.watchdog import probe_bafu_freshness

        seen_auth_headers: list[str | None] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen_auth_headers.append(req.headers.get("authorization"))
            return httpx.Response(200, json={"items": [], "total": 0, "limit": 1})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        probe_bafu_freshness("http://x/health/detail", client=client, token=None)
        assert seen_auth_headers == [None]


# ---------- probe_forecast_freshness: dedicated Plan 116 probe ---------------
# Fixer round: a SEPARATE probe/result from `probe_bafu_freshness` because
# `run_once` must age forecast freshness by the record's `cycle_time`
# (production time), not `checked_at` (write time) — see
# `TestRunOnceForecastFreshness
# .test_stale_cycle_time_alerts_even_when_checked_at_is_fresh`.


class TestProbeForecastFreshness:
    def test_probe_connection_error_returns_not_found(self) -> None:
        # unused port on localhost — should error instantly, never raise
        result = probe_forecast_freshness("http://127.0.0.1:1/api/v1/health/detail")
        assert result.found is False
        assert result.checked_at is None
        assert result.cycle_time is None
        assert result.error is not None

    def test_derives_forecast_freshness_url_from_custom_health_url(self) -> None:
        from sapphire_flow.ops.watchdog import _forecast_freshness_url_from_health

        assert _forecast_freshness_url_from_health(
            "http://custom:9000/api/v2/health"
        ) == (
            "http://custom:9000/api/v2/health/detail?check_type=forecast_freshness&limit=1"
        )

    def test_parses_cycle_time_distinct_from_checked_at(self) -> None:
        """The core fixer-round contract: `cycle_time` is parsed from its
        OWN JSON field, independent of `checked_at` — a record written
        just now (`checked_at`) can still describe a long-past production
        cycle (`cycle_time`)."""
        import httpx

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "checked_at": "2026-07-13T09:00:00+00:00",
                            "cycle_time": "2026-07-12T03:00:00+00:00",
                            "status": "ok",
                        }
                    ],
                    "total": 1,
                    "limit": 1,
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        result = probe_forecast_freshness("http://x/health/detail", client=client)
        assert result.found is True
        assert result.checked_at == datetime(2026, 7, 13, 9, 0, tzinfo=UTC)
        assert result.cycle_time == datetime(2026, 7, 12, 3, 0, tzinfo=UTC)
        # Fixer round (major 2): this test previously never asserted
        # `status` at all, leaving the HTTP `status` field's parsing
        # unlocked here.
        assert result.status == "ok"

    def test_naive_cycle_time_is_normalized_to_tz_aware(self) -> None:
        # Same normalization requirement as `checked_at` — a naive
        # `cycle_time` would raise TypeError in run_once's `now - cycle_time`
        # comparison (outside this function's try/except).
        import httpx

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "items": [{"cycle_time": "2026-07-13T09:00:00", "status": "ok"}],
                    "total": 1,
                    "limit": 1,
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        result = probe_forecast_freshness("http://x/health/detail", client=client)
        assert result.found is True
        assert result.cycle_time is not None
        assert result.cycle_time.tzinfo is not None

    def test_missing_cycle_time_field_is_none_not_error(self) -> None:
        import httpx

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"checked_at": "2026-07-13T09:00:00+00:00", "status": "ok"}
                    ],
                    "total": 1,
                    "limit": 1,
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        result = probe_forecast_freshness("http://x/health/detail", client=client)
        assert result.found is True
        assert result.cycle_time is None

    def test_sends_bearer_header_when_token_provided(self) -> None:
        import httpx

        seen_auth_headers: list[str | None] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen_auth_headers.append(req.headers.get("authorization"))
            return httpx.Response(200, json={"items": [], "total": 0, "limit": 1})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        probe_forecast_freshness(
            "http://x/health/detail", client=client, token="prefix.secret"
        )
        assert seen_auth_headers == ["Bearer prefix.secret"]


# ---------- run_once: BAFU forecast collector freshness (Flow 4 hook) --------


class TestRunOnceBafuFreshness:
    def test_fresh_ok_heartbeat_no_alert(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_bafu_failures == 0
        assert slack.calls == []

    def test_overridden_health_url_retargets_bafu_probe(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        base = _config(tmp_path, backup_dir=backup_dir)
        # `replace()`, not a manual field-by-field reconstruction: a manual
        # rebuild silently drops any field `_config` sets that isn't listed
        # here (it already did, for `deadman_url_path` — see the Plan 163
        # fixer round note on `_config`), re-pointing this test at the REAL
        # default `./secrets/deadman_url` production URL.
        cfg = _replace(base, health_url="http://custom:9000/api/v1/health")
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        captured: dict[str, str] = {}

        def _spy(url: str) -> BafuFreshnessResult:
            captured["url"] = url
            return BafuFreshnessResult(
                found=True, checked_at=_NOW, status="ok", error=None
            )

        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_SlackRecorder(),
            bafu_probe=_spy,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert captured["url"] == (
            "http://custom:9000/api/v1/health/detail"
            "?check_type=bafu_forecast_freshness&limit=1"
        )

    def test_stale_heartbeat_alerts(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_stale_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_bafu_failures == 1
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "BAFU forecast collector STALE" in msg

    def test_no_record_found_alerts(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_not_found_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_bafu_failures == 1
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "BAFU forecast collector STALE" in msg
        assert "no heartbeat found" in msg

    def test_degraded_status_alerts(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_degraded_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_bafu_failures == 1
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "BAFU forecast collector DEGRADED" in msg
        assert "status: warning" in msg

    def test_dedup_alerts_once_then_silent(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        first_slack = _SlackRecorder()
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=first_slack,
            bafu_probe=_bafu_stale_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.consecutive_bafu_failures == 1
        assert len(first_slack.calls) == 1

        second_slack = _SlackRecorder()
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=second_slack,
            bafu_probe=_bafu_stale_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.consecutive_bafu_failures == 2
        assert second_slack.calls == []  # hysteresis: 2nd failure stays silent

    def test_recovery_alert(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        WatchdogState(consecutive_bafu_failures=3).dump(cfg.state_path)
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_bafu_failures == 0
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "BAFU forecast collector RECOVERED" in msg

    def test_bafu_check_is_independent_of_health_and_backup_checks(
        self, tmp_path: Path
    ) -> None:
        # A BAFU alert must fire even when health + backup are both healthy,
        # and must not itself affect health/backup dedup counters.
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_stale_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_health_failures == 0
        assert state.consecutive_bafu_failures == 1
        assert len(slack.calls) == 1


# ---------- run_once: BAFU observation-collector freshness (Plan 136, additive) --


class TestBafuObsStaleThresholdValueAndBoundary:
    """Plan 176 D4: BAFU_OBS_STALE_THRESHOLD (the HEARTBEAT gate — ~3 missed
    polls at D1's <=4 min ceiling) is re-derived from the 10-minute grid and
    is no longer coincidentally equal to the flow-side measurement-age
    threshold (30 min)."""

    def test_threshold_is_fifteen_minutes(self) -> None:
        assert timedelta(minutes=15) == BAFU_OBS_STALE_THRESHOLD

    def test_age_exactly_at_threshold_is_not_stale(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        def _probe(_url: str) -> BafuFreshnessResult:
            return BafuFreshnessResult(
                found=True,
                checked_at=_NOW - BAFU_OBS_STALE_THRESHOLD,
                status="ok",
                error=None,
            )

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.consecutive_bafu_obs_failures == 0
        assert slack.calls == []

    def test_age_one_second_past_threshold_is_stale(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        def _probe(_url: str) -> BafuFreshnessResult:
            return BafuFreshnessResult(
                found=True,
                checked_at=_NOW - BAFU_OBS_STALE_THRESHOLD - timedelta(seconds=1),
                status="ok",
                error=None,
            )

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.consecutive_bafu_obs_failures == 1
        assert len(slack.calls) == 1
        assert "BAFU observation collector STALE" in slack.calls[0][1]


class TestRunOnceBafuObsFreshness:
    def test_fresh_ok_heartbeat_no_alert(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_bafu_obs_failures == 0
        assert slack.calls == []

    def test_overridden_health_url_retargets_bafu_obs_probe(
        self, tmp_path: Path
    ) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        base = _config(tmp_path, backup_dir=backup_dir)
        # `replace()`, not a manual field-by-field reconstruction — see the
        # sibling forecast-probe test above for why.
        cfg = _replace(base, health_url="http://custom:9000/api/v1/health")
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        captured: dict[str, str] = {}

        def _spy(url: str) -> BafuFreshnessResult:
            captured["url"] = url
            return BafuFreshnessResult(
                found=True, checked_at=_NOW, status="ok", error=None
            )

        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_SlackRecorder(),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_spy,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert captured["url"] == (
            "http://custom:9000/api/v1/health/detail"
            "?check_type=bafu_observation_freshness&limit=1"
        )

    def test_stale_heartbeat_alerts(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_stale_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
            hostname="test-host",
        )

        assert state.consecutive_bafu_obs_failures == 1
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        # Plan 176 D4: BAFU_OBS_STALE_THRESHOLD is sub-hour (15m) — the OLD
        # `hours = int(threshold // 3600)` formatter renders any sub-hour
        # threshold as "threshold: 0h", silently lying about the alert's
        # own trigger condition. The formatter must be minutes-aware. Locked
        # to the COMPLETE alert string (hostname pinned via `hostname=` so
        # the expected literal is fully deterministic) — a substring check
        # alone would miss a regression elsewhere in the same message.
        expected_last_heartbeat = (
            _NOW - BAFU_OBS_STALE_THRESHOLD - timedelta(hours=1)
        ).isoformat()
        assert msg == (
            "[SAPPHIRE staging] BAFU observation collector STALE — "
            "host: test-host, time: 2026-04-22T12:00:00+00:00, "
            f"last_heartbeat: {expected_last_heartbeat}, threshold: 15m"
        )

    def test_no_record_found_alerts(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_not_found_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_bafu_obs_failures == 1
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "BAFU observation collector STALE" in msg
        assert "no heartbeat found" in msg

    def test_degraded_status_alerts(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_degraded_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_bafu_obs_failures == 1
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "BAFU observation collector DEGRADED" in msg
        assert "status: warning" in msg

    def test_dedup_alerts_once_then_silent(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        first_slack = _SlackRecorder()
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=first_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_stale_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.consecutive_bafu_obs_failures == 1
        assert len(first_slack.calls) == 1

        second_slack = _SlackRecorder()
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=second_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_stale_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.consecutive_bafu_obs_failures == 2
        assert second_slack.calls == []  # hysteresis: 2nd failure stays silent

    def test_recovery_alert(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        WatchdogState(consecutive_bafu_obs_failures=3).dump(cfg.state_path)
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_bafu_obs_failures == 0
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "BAFU observation collector RECOVERED" in msg

    def test_independent_of_forecast_check_and_health_and_backup(
        self, tmp_path: Path
    ) -> None:
        # An observation-check alert must fire even when health, backup, AND
        # the forecast-freshness check are all healthy, and must not affect
        # their dedup counters (purely additive — DC/T9 requirement).
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_stale_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_health_failures == 0
        assert state.consecutive_bafu_failures == 0
        assert state.consecutive_bafu_obs_failures == 1
        assert len(slack.calls) == 1


class TestRunOnceForecastFreshness:
    """Plan 116: the forecast-production freshness watchdog block —
    probes ``PipelineCheckType.FORECAST_FRESHNESS``
    (`flows/run_forecast_cycle.py` emits it; a cycle that stores zero
    forecasts is CRITICAL). This is the ACCEPTANCE test's second half:
    the WATCHDOG must treat a CRITICAL/missing freshness record as
    failed and alert, independent of the API health probe and the two
    BAFU checks."""

    def test_fresh_ok_heartbeat_no_alert(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_forecast_freshness_failures == 0
        assert slack.calls == []

    def test_overridden_health_url_retargets_forecast_freshness_probe(
        self, tmp_path: Path
    ) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        base = _config(tmp_path, backup_dir=backup_dir)
        cfg = _replace(base, health_url="http://custom:9000/api/v1/health")
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        captured: dict[str, str] = {}

        def _spy(url: str) -> ForecastFreshnessResult:
            captured["url"] = url
            return ForecastFreshnessResult(
                found=True, checked_at=_NOW, cycle_time=_NOW, status="ok", error=None
            )

        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_SlackRecorder(),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_spy,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert captured["url"] == (
            "http://custom:9000/api/v1/health/detail"
            "?check_type=forecast_freshness&limit=1"
        )

    def test_stale_heartbeat_alerts(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_stale_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_forecast_freshness_failures == 1
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "forecast production STALE" in msg

    def test_stale_cycle_time_alerts_even_when_checked_at_is_fresh(
        self, tmp_path: Path
    ) -> None:
        """Fixer round blocker fix (correctness requirement 2): freshness
        must age by the record's `cycle_time` (the forecast PRODUCTION
        time), not `checked_at` (the write time). A delayed or long-running
        cycle can write its heartbeat "now" while the `cycle_time` it
        describes is already stale — that must still alarm. Against the
        buggy code (which compared `now - checked_at`), this record would
        have read as fresh and stayed silent."""
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        def _fresh_checked_at_stale_cycle_time(_url: str) -> ForecastFreshnessResult:
            return ForecastFreshnessResult(
                found=True,
                checked_at=_NOW,  # written just now …
                cycle_time=(
                    _NOW - FORECAST_FRESHNESS_STALE_THRESHOLD - timedelta(hours=1)
                ),  # … but describes a long-stale production cycle
                status="ok",
                error=None,
            )

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_fresh_checked_at_stale_cycle_time,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_forecast_freshness_failures == 1
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "forecast production STALE" in msg

    def test_no_record_found_alerts(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_not_found_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_forecast_freshness_failures == 1
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "forecast production STALE" in msg
        assert "no heartbeat found" in msg

    def test_critical_status_alerts(self, tmp_path: Path) -> None:
        """The acceptance scenario's watchdog half: a cycle that stored
        zero forecasts emits status="critical" (not merely stale) — the
        watchdog must treat it as failed and alert, distinctly from the
        stale/missing-heartbeat message."""
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_critical_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_forecast_freshness_failures == 1
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "forecast cycle stored ZERO forecasts" in msg
        assert "status: critical" in msg

    def test_critical_status_via_real_probe_alerts(self, tmp_path: Path) -> None:
        """Fixer round (major 2): the sibling test above (and the whole
        acceptance scenario) exercised the critical-status alert path only
        through a hand-built `_forecast_freshness_critical_probe` fake --
        never through the REAL `probe_forecast_freshness`. A broken real
        probe that discarded or hard-coded the HTTP status would have
        passed every such test while production silently ignored CRITICAL
        records. This drives the actual HTTP-to-alert boundary end to
        end: a `MockTransport` response carrying `status: "critical"`
        (with a FRESH `cycle_time`, isolating the critical-status path
        from the staleness path) is parsed by the real probe and fed into
        `run_once`."""
        import httpx

        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "checked_at": _NOW.isoformat(),
                            "cycle_time": _NOW.isoformat(),
                            "status": "critical",
                        }
                    ],
                    "total": 1,
                    "limit": 1,
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))

        def real_probe(url: str) -> ForecastFreshnessResult:
            return probe_forecast_freshness(url, client=client)

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=real_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_forecast_freshness_failures == 1
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "forecast cycle stored ZERO forecasts" in msg
        assert "status: critical" in msg

    def test_critical_status_with_partial_write_alerts_accurately(
        self, tmp_path: Path
    ) -> None:
        """Fixer round (minor): a forced-CRITICAL record from the
        mid-cycle group-store-failure path (Plan 116 fixer round, major
        1/blocker) carries `detail.forecasts_stored > 0` — some forecasts
        DID store before the fatal failure. The alert must say so instead
        of the factually wrong "stored ZERO forecasts", which this test
        drives through the REAL `probe_forecast_freshness` end to end."""
        import httpx

        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "checked_at": _NOW.isoformat(),
                            "cycle_time": _NOW.isoformat(),
                            "status": "critical",
                            "detail": {"forecasts_stored": 3},
                        }
                    ],
                    "total": 1,
                    "limit": 1,
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))

        def real_probe(url: str) -> ForecastFreshnessResult:
            return probe_forecast_freshness(url, client=client)

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=real_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_forecast_freshness_failures == 1
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "stored ZERO forecasts" not in msg
        assert "stored 3 forecast(s) then failed" in msg
        assert "status: critical" in msg

    def test_dedup_alerts_once_then_silent(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        first_slack = _SlackRecorder()
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=first_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_critical_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.consecutive_forecast_freshness_failures == 1
        assert len(first_slack.calls) == 1

        second_slack = _SlackRecorder()
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=second_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_critical_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.consecutive_forecast_freshness_failures == 2
        assert second_slack.calls == []  # hysteresis: 2nd failure stays silent

    def test_recovery_alert(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        WatchdogState(consecutive_forecast_freshness_failures=3).dump(cfg.state_path)
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_forecast_freshness_failures == 0
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "forecast production RECOVERED" in msg

    def test_independent_of_health_backup_and_bafu_checks(self, tmp_path: Path) -> None:
        # A forecast-freshness alert must fire even when health, backup, AND
        # both BAFU checks are all healthy, and must not affect their dedup
        # counters (purely additive).
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_critical_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_health_failures == 0
        assert state.consecutive_bafu_failures == 0
        assert state.consecutive_bafu_obs_failures == 0
        assert state.consecutive_forecast_freshness_failures == 1
        assert len(slack.calls) == 1

    def test_does_not_consult_forecast_cycle_health_field(self, tmp_path: Path) -> None:
        """Requirement 1 (watchdog half): the probe result carries only
        `status`/`checked_at` from the FORECAST_FRESHNESS record — there is
        no `ForecastCycleHealth` field for the watchdog to consult even if
        it wanted to. A DEGRADED-but-forecasts-stored cycle emits an OK
        freshness record (see the flow-level test), which this probe
        surfaces as `status="ok"` — same as any other healthy heartbeat."""
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        def _degraded_cycle_but_ok_freshness(_url: str) -> ForecastFreshnessResult:
            return ForecastFreshnessResult(
                found=True, checked_at=_NOW, cycle_time=_NOW, status="ok", error=None
            )

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_degraded_cycle_but_ok_freshness,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        assert state.consecutive_forecast_freshness_failures == 0
        assert slack.calls == []


class TestWatchdogStateBackupNotificationBackwardCompat:
    """Plan 162 T4 — the new hysteresis + pending-notification fields, and
    the retired-but-still-round-tripped legacy `last_backup_alert_iso`."""

    def test_roundtrip_includes_new_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        original = WatchdogState(
            consecutive_backup_stale_failures=2,
            backup_notification_pending="recovered",
        )
        original.dump(path)
        loaded = WatchdogState.load(path)
        assert loaded == original

    def test_state_written_before_this_plan_with_no_prior_alert_defaults_to_no_incident(
        self, tmp_path: Path
    ) -> None:
        # A state file predating Plan 162 has neither new key at all, AND
        # the legacy field is null — the old watchdog never alerted, so
        # there is genuinely no incident to migrate.
        p = tmp_path / "old_state.json"
        p.write_text(
            '{"consecutive_health_failures": 0, "last_backup_alert_iso": null}'
        )
        s = WatchdogState.load(p)
        assert s.consecutive_backup_stale_failures == 0
        assert s.backup_notification_pending is None
        assert s.last_backup_alert_iso is None

    def test_legacy_state_with_a_prior_alert_migrates_to_active_incident(
        self, tmp_path: Path
    ) -> None:
        """Plan 162 T4 review fix, red-first: a state file predating Plan
        162 has NO `consecutive_backup_stale_failures` key (key ABSENT, not
        present-and-zero). If `last_backup_alert_iso` shows the OLD
        watchdog had already alerted, treating the absent key as "no
        incident" would make an unresolved legacy incident invisible — the
        very first Phase-A tick with a fresh backup would then infer
        "nothing changed" and never emit the recovery notification the
        rollout owes. RED against a fix that only defaults the absent key
        to 0 unconditionally: `consecutive_backup_stale_failures` would be
        0 here instead of the migrated 1."""
        p = tmp_path / "old_state.json"
        p.write_text(
            '{"consecutive_health_failures": 0, '
            '"last_backup_alert_iso": "2026-04-22T12:00:00+00:00"}'
        )
        s = WatchdogState.load(p)
        assert s.consecutive_backup_stale_failures == 1
        assert s.backup_notification_pending is None
        # Legacy field still round-trips for anything reading state files
        # directly, even though no code path consults it anymore.
        assert s.last_backup_alert_iso == "2026-04-22T12:00:00+00:00"

    def test_legacy_incident_state_migrates_then_emits_recovery_on_first_fresh_tick(
        self, tmp_path: Path
    ) -> None:
        """Full `run_once` proof of the migration: a legacy (pre-Plan-162)
        state file with a prior alert, and a NOW-fresh backup, must emit
        exactly one 'backup RECOVERED' notification on the very first
        post-rollout tick — not silence, which is what "absent key -> no
        incident" would otherwise produce."""
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)  # fresh
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        cfg.state_path.write_text(
            '{"consecutive_health_failures": 0, '
            '"last_backup_alert_iso": "2026-04-22T10:00:00+00:00"}'
        )

        slack = _SlackRecorder()
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        backup_calls = [c for c in slack.calls if "backup" in c[1]]
        assert len(backup_calls) == 1
        assert "backup RECOVERED" in backup_calls[0][1]
        assert state.consecutive_backup_stale_failures == 0

    def test_legacy_incident_state_stays_silent_while_still_stale(
        self, tmp_path: Path
    ) -> None:
        """The mirror case: a legacy incident that is STILL stale must not
        double-alert on the migration tick — the incident was already
        reported (by the old watchdog); only a NEW transition or a
        recovery may notify."""
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=30)  # still stale
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        cfg.state_path.write_text(
            '{"consecutive_health_failures": 0, '
            '"last_backup_alert_iso": "2026-04-22T10:00:00+00:00"}'
        )

        slack = _SlackRecorder()
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )

        backup_calls = [c for c in slack.calls if "backup" in c[1]]
        assert backup_calls == []
        assert state.consecutive_backup_stale_failures == 2

    def test_garbage_pending_value_normalizes_to_none(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        p.write_text('{"backup_notification_pending": "not-a-real-kind"}')
        s = WatchdogState.load(p)
        assert s.backup_notification_pending is None

    def test_new_backup_ticks_no_longer_write_the_legacy_field(
        self, tmp_path: Path
    ) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=30)  # stale
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_SlackRecorder(),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.last_backup_alert_iso is None


class TestWatchdogStateBackupDeviceBackwardCompat:
    """Plan 194 — the new wrong-device hysteresis + pending-notification
    fields. Brand new (no predecessor field to migrate from): absent keys
    default cleanly to 0/None."""

    def test_roundtrip_includes_new_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        original = WatchdogState(
            consecutive_backup_device_unverified_ticks=3,
            backup_device_notification_pending="unverified",
        )
        original.dump(path)
        loaded = WatchdogState.load(path)
        assert loaded == original

    def test_state_file_without_the_new_keys_defaults_to_zero(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "old_state.json"
        p.write_text('{"consecutive_health_failures": 0}')
        s = WatchdogState.load(p)
        assert s.consecutive_backup_device_unverified_ticks == 0
        assert s.backup_device_notification_pending is None


class TestWatchdogStateBafuObsBackwardCompat:
    def test_roundtrip_includes_new_field(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        original = WatchdogState(
            consecutive_health_failures=1,
            consecutive_bafu_failures=2,
            consecutive_bafu_obs_failures=3,
        )
        original.dump(path)
        loaded = WatchdogState.load(path)
        assert loaded == original

    def test_state_written_before_this_plan_defaults_obs_to_zero(
        self, tmp_path: Path
    ) -> None:
        # A state file predating Plan 136 has no `consecutive_bafu_obs_failures`
        # key at all — must default to 0, not raise.
        p = tmp_path / "old_state.json"
        p.write_text(
            '{"consecutive_health_failures": 2, "last_backup_alert_iso": null, '
            '"consecutive_bafu_failures": 1}'
        )
        s = WatchdogState.load(p)
        assert s.consecutive_bafu_obs_failures == 0
        assert s.consecutive_bafu_failures == 1
        assert s.consecutive_health_failures == 2


class TestWatchdogStateLaunchdBackwardCompat:
    """Fixer round major 8 — Plan 195's four new fields
    (`failing_launchd_labels`, `launchd_notification_pending`,
    `launchd_probe_unreadable_ticks`, `launchd_probe_notification_pending`)
    get the same dedicated backward-compat test every prior watchdog field
    addition got. Brand new (no predecessor field to migrate from): a
    state file written before this plan has none of these four keys at
    all, and `WatchdogState.load` must default them cleanly rather than
    KeyError."""

    def test_roundtrip_includes_new_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        original = WatchdogState(
            failing_launchd_labels=(_LABEL_A, _LABEL_B),
            launchd_notification_pending=((_LABEL_A, "failing"),),
            launchd_probe_unreadable_ticks=2,
            launchd_probe_notification_pending="unreadable",
        )
        original.dump(path)
        loaded = WatchdogState.load(path)
        assert loaded == original

    def test_state_file_without_the_new_keys_defaults_cleanly(
        self, tmp_path: Path
    ) -> None:
        # A real pre-Plan-195 state file — other keys present, these four
        # entirely absent (not present-and-empty). If `load` ever switched
        # from `raw.get(...)` to `raw[...]` for any of the four, this is
        # the test that would catch it: the mini's actual on-disk
        # state.json predates this plan and would crash the very first
        # Plan 195 tick otherwise.
        p = tmp_path / "old_state.json"
        p.write_text('{"consecutive_health_failures": 0}')
        s = WatchdogState.load(p)
        assert s.failing_launchd_labels == ()
        assert s.launchd_notification_pending == ()
        assert s.launchd_probe_unreadable_ticks == 0
        assert s.launchd_probe_notification_pending is None


# ---------- Plan 163 T1: malformed-URL hardening across all outbound sites ----
#
# httpx.InvalidURL is NOT a subclass of httpx.HTTPError (verified in this
# repo's httpx 0.28.1: `issubclass(httpx.InvalidURL, httpx.HTTPError)` is
# False) — a malformed hand-pasted URL must not raise out of ANY outbound
# HTTP call site. Two distinct malformed URLs exercise two distinct
# exception classes that the pre-Plan-163 `except httpx.HTTPError` did not
# cover:
#   - `http://[::1`      -> httpx.InvalidURL directly (malformed IPv6 host)
#   - `http://xn--/`      -> idna.core.IDNAError, a UnicodeError subclass,
#                            NOT an httpx.HTTPError/InvalidURL subclass
# Per-site: one generic test does not prove all three call sites guard the
# boundary independently (see harness note at the top of this file).


_INVALID_URL_MALFORMED = "http://[::1"
_IDNA_UNICODE_ERROR_MALFORMED = "http://xn--/"


class TestMalformedUrlHardeningDefaultSlackPoster:
    def test_invalid_url_returns_false_not_raises(self) -> None:
        assert default_slack_poster(_INVALID_URL_MALFORMED, "test message") is False

    def test_idna_unicode_error_returns_false_not_raises(self) -> None:
        assert (
            default_slack_poster(_IDNA_UNICODE_ERROR_MALFORMED, "test message") is False
        )


class TestMalformedUrlHardeningProbeHealth:
    def test_invalid_url_returns_not_ok_not_raises(self) -> None:
        result = probe_health(_INVALID_URL_MALFORMED)
        assert result.ok is False
        assert result.http_status is None

    def test_idna_unicode_error_returns_not_ok_not_raises(self) -> None:
        result = probe_health(_IDNA_UNICODE_ERROR_MALFORMED)
        assert result.ok is False
        assert result.http_status is None


class TestMalformedUrlHardeningProbeBafuFreshness:
    def test_invalid_url_returns_not_found_not_raises(self) -> None:
        result = probe_bafu_freshness(_INVALID_URL_MALFORMED)
        assert result.found is False

    def test_idna_unicode_error_returns_not_found_not_raises(self) -> None:
        result = probe_bafu_freshness(_IDNA_UNICODE_ERROR_MALFORMED)
        assert result.found is False


# ---------- Plan 163 fixer round: owned-client cleanup must not override --
#            a successful (or already-caught) result -----------------------
#
# `probe_health`/`probe_bafu_freshness` construct their `httpx.Client`
# INSIDE the guarded region when the caller doesn't inject one ("owns" it)
# and close() it in `finally`. Left unguarded, an exception from that
# close() call REPLACES whatever the try block already produced (a `return`
# or a caught-and-handled request exception) and escapes the boundary these
# functions exist to provide. These tests use a fake, injected-via-monkeypatch
# `httpx.Client` whose `.get()` succeeds normally but whose `.close()`
# raises — proving the CALLER-VISIBLE result is unaffected.


class _FakeHealthResponse:
    status_code = 200

    def json(self) -> dict[str, str]:
        return {"status": "ok"}


class _FakeClientCloseRaises:
    """A fake owned `httpx.Client` whose `get()` succeeds but `close()`
    raises `OSError` — simulates a half-torn-down transport."""

    def __init__(self, *_a: object, **_k: object) -> None:
        pass

    def get(self, url: str, **_k: object) -> _FakeHealthResponse:
        return _FakeHealthResponse()

    def close(self) -> None:
        raise OSError("transport already closed")


class TestOwnedClientCleanupHardeningProbeHealth:
    def test_close_failure_does_not_override_a_successful_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sapphire_flow.ops.watchdog as watchdog_module

        monkeypatch.setattr(watchdog_module.httpx, "Client", _FakeClientCloseRaises)

        result = probe_health("http://localhost:8000/api/v1/health")

        assert result.ok is True
        assert result.http_status == 200


class _FakeBafuResponse:
    status_code = 200

    def json(self) -> dict[str, object]:
        return {
            "items": [
                {"checked_at": "2026-04-22T12:00:00+00:00", "status": "ok"},
            ]
        }


class _FakeBafuClientCloseRaises:
    """Same shape as `_FakeClientCloseRaises`, returning a BAFU-detail-shaped
    payload instead of a health payload."""

    def __init__(self, *_a: object, **_k: object) -> None:
        pass

    def get(self, url: str, **_k: object) -> _FakeBafuResponse:
        return _FakeBafuResponse()

    def close(self) -> None:
        raise OSError("transport already closed")


class TestOwnedClientCleanupHardeningProbeBafuFreshness:
    def test_close_failure_does_not_override_a_successful_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sapphire_flow.ops.watchdog as watchdog_module

        monkeypatch.setattr(watchdog_module.httpx, "Client", _FakeBafuClientCloseRaises)

        result = probe_bafu_freshness(
            "http://localhost:8000/api/v1/health/detail"
            "?check_type=bafu_forecast_freshness&limit=1"
        )

        assert result.found is True
        assert result.status == "ok"


class TestMalformedUrlHardeningDefaultDeadmanPoster:
    """Bonus: same boundary, the fourth outbound call site added by Plan
    163 T2 (not one of the three the plan enumerates for T1, but built on
    the identical `_HTTP_CALL_EXCEPTIONS` boundary, so it must behave the
    same way)."""

    def test_invalid_url_returns_false_not_raises(self) -> None:
        import sapphire_flow.ops.watchdog as watchdog_module

        try:
            default_deadman_poster = watchdog_module.default_deadman_poster
        except AttributeError:
            pytest.fail("default_deadman_poster not implemented (Plan 163 T2)")
        assert default_deadman_poster(_INVALID_URL_MALFORMED) is False


# ---------- Plan 163 fixer round (minor 2): response handling must live -----
#            INSIDE the single adapter boundary, not after it -------------
#
# `default_slack_poster`/`default_deadman_poster` promise to "never raise".
# The buggy implementation caught request exceptions but then accessed
# `resp.status_code`/`resp.text` OUTSIDE the `try` — an unexpected exception
# from either escapes uncaught. This is currently MASKED for `run_once`
# because its `_safe_slack_post`/`_safe_deadman_post` wrappers contain the
# damage — but the functions themselves promise never to raise, and any
# other caller (including tests, and any future caller) relies on that
# promise directly, not on `run_once`'s wrapper.


class _StatusCodeAccessRaises:
    """A fake httpx response whose `.status_code` property raises when
    accessed — simulates an unexpected response-object defect."""

    @property
    def status_code(self) -> int:
        raise RuntimeError("status_code access exploded")


class _SlackTextAccessRaises:
    """A fake httpx response with a normal (failing) `status_code` but a
    `.text` property that raises — reaches the `resp.text[:200]` line in
    `default_slack_poster`'s failure-logging branch specifically."""

    status_code = 500

    @property
    def text(self) -> str:
        raise RuntimeError("text access exploded")


class TestResponseHandlingInsideGuardDefaultSlackPoster:
    def test_status_code_access_raises_returns_false_not_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sapphire_flow.ops.watchdog as watchdog_module

        monkeypatch.setattr(
            watchdog_module.httpx,
            "post",
            lambda *a, **k: _StatusCodeAccessRaises(),
        )
        assert default_slack_poster("https://hooks.slack.com/x", "msg") is False

    def test_text_access_raises_returns_false_not_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sapphire_flow.ops.watchdog as watchdog_module

        monkeypatch.setattr(
            watchdog_module.httpx, "post", lambda *a, **k: _SlackTextAccessRaises()
        )
        assert default_slack_poster("https://hooks.slack.com/x", "msg") is False


class TestResponseHandlingInsideGuardDefaultDeadmanPoster:
    def test_status_code_access_raises_returns_false_not_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sapphire_flow.ops.watchdog as watchdog_module

        try:
            default_deadman_poster = watchdog_module.default_deadman_poster
        except AttributeError:
            pytest.fail("default_deadman_poster not implemented (Plan 163 T2)")
        monkeypatch.setattr(
            watchdog_module.httpx,
            "post",
            lambda *a, **k: _StatusCodeAccessRaises(),
        )
        assert default_deadman_poster("https://hc-ping.com/x") is False


# ---------- Plan 163 T2: read_deadman_url ----------------------------------


class TestReadDeadmanUrl:
    """Mirrors TestReadSlackWebhook/TestReadProbeToken — the dead-man ping
    URL is the same HOST-secret-file convention. `read_deadman_url` must
    ALSO handle UnicodeError (undecodable bytes), which
    `read_slack_webhook`/`read_probe_token` do not need to (Plan 163 T4)."""

    @staticmethod
    def _read(path: Path) -> str | None:
        import sapphire_flow.ops.watchdog as watchdog_module

        try:
            read_deadman_url = watchdog_module.read_deadman_url
        except AttributeError:
            pytest.fail("read_deadman_url not implemented (Plan 163 T2)")
        return read_deadman_url(path)

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert self._read(tmp_path / "nope") is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "deadman_url"
        p.write_text("")
        assert self._read(p) is None

    def test_whitespace_only_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "deadman_url"
        p.write_text("   \n  \n")
        assert self._read(p) is None

    def test_populated_returns_stripped(self, tmp_path: Path) -> None:
        p = tmp_path / "deadman_url"
        p.write_text("https://hc-ping.com/abc-123-def\n")
        assert self._read(p) == "https://hc-ping.com/abc-123-def"

    def test_undecodable_bytes_return_none(self, tmp_path: Path) -> None:
        # Path.read_text() can raise UnicodeDecodeError (a UnicodeError) on
        # invalid bytes, not only OSError.
        p = tmp_path / "deadman_url"
        p.write_bytes(b"\xff\xfe\x00\x01 not valid utf-8 \x80\x81")
        assert self._read(p) is None

    def test_unreadable_file_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p = tmp_path / "deadman_url"
        p.write_text("https://hc-ping.com/abc\n")

        def _raise(self: Path, *args: object, **kwargs: object) -> str:
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "read_text", _raise)
        assert self._read(p) is None

    def test_existence_preflight_permission_error_does_not_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plan 163 fixer round (minor 1): the buggy implementation called
        `path.exists()` as a preflight OUTSIDE the `(OSError, UnicodeError)`
        guard. On Python 3.12, `exists()` (which calls `stat()`) can itself
        re-raise a non-ignored `OSError` — e.g. a permission error on a
        parent directory — escaping uncaught and violating the documented
        "unreadable => None, no error" contract. The fixed implementation
        must not call `exists()`/`stat()` at all: it reads directly inside
        the guard, so a nonexistent (or otherwise inaccessible) path
        degrades to "unreadable" => None like every other case, without
        ever touching `exists()`/`stat()`.

        Patches BOTH `Path.exists` and `Path.stat` to raise `PermissionError`
        (the real-world shape of a parent-directory permission error) and
        points at a path whose parent directory does not exist either, so
        the fixed code's `read_text()` call fails on its own merits
        (`FileNotFoundError`, an `OSError` subclass) without depending on
        `exists()`/`stat()` ever being called.
        """

        def _raise(self: Path, *args: object, **kwargs: object) -> object:
            raise PermissionError("permission denied for parent directory")

        monkeypatch.setattr(Path, "exists", _raise)
        monkeypatch.setattr(Path, "stat", _raise)

        p = tmp_path / "unreadable_parent" / "deadman_url"
        assert self._read(p) is None


# ---------- Plan 163 T2: default_deadman_poster success band ----------------


class TestDefaultDeadmanPoster:
    """Success is `200 <= status < 300`, not merely `not (status >= 300)`
    (Plan 163 T4) — a 1xx informational status must not count as success."""

    @staticmethod
    def _poster() -> object:
        import sapphire_flow.ops.watchdog as watchdog_module

        try:
            return watchdog_module.default_deadman_poster
        except AttributeError:
            pytest.fail("default_deadman_poster not implemented (Plan 163 T2)")

    def test_200_is_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import sapphire_flow.ops.watchdog as watchdog_module

        class _FakeResponse:
            status_code = 200

        monkeypatch.setattr(
            watchdog_module.httpx, "post", lambda *a, **k: _FakeResponse()
        )
        assert self._poster()("https://hc-ping.com/x") is True

    def test_300_is_not_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import sapphire_flow.ops.watchdog as watchdog_module

        class _FakeResponse:
            status_code = 300

        monkeypatch.setattr(
            watchdog_module.httpx, "post", lambda *a, **k: _FakeResponse()
        )
        assert self._poster()("https://hc-ping.com/x") is False

    def test_100_is_not_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import sapphire_flow.ops.watchdog as watchdog_module

        class _FakeResponse:
            status_code = 100

        monkeypatch.setattr(
            watchdog_module.httpx, "post", lambda *a, **k: _FakeResponse()
        )
        assert self._poster()("https://hc-ping.com/x") is False

    def test_posts_empty_body_with_the_5s_timeout_constant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plan 163 fixer round: nothing previously asserted on the
        `httpx.post` call itself — a fake `resp` swallowing all args/kwargs
        would still pass every test above even if the timeout were dropped
        or changed, or if a payload (json/data/content/files) were added,
        which would violate D2b/the 'empty POST, no payload' network-model
        claim."""
        import sapphire_flow.ops.watchdog as watchdog_module

        captured: dict[str, object] = {}

        class _FakeResponse:
            status_code = 200

        def _fake_post(url: str, **kwargs: object) -> _FakeResponse:
            captured["url"] = url
            captured["kwargs"] = kwargs
            return _FakeResponse()

        monkeypatch.setattr(watchdog_module.httpx, "post", _fake_post)
        assert self._poster()("https://hc-ping.com/x") is True

        assert captured["url"] == "https://hc-ping.com/x"
        kwargs = captured["kwargs"]
        assert isinstance(kwargs, dict)
        assert kwargs.get("timeout") == watchdog_module.DEADMAN_POST_TIMEOUT_S
        assert watchdog_module.DEADMAN_POST_TIMEOUT_S == 5.0
        assert "json" not in kwargs
        assert "data" not in kwargs
        assert "content" not in kwargs
        assert "files" not in kwargs


# ---------- Plan 163 T2/T3: run_once dead-man heartbeat contract ------------
#
# THE HEARTBEAT CONTRACT: exactly once after every tick that COMPLETES AND
# PERSISTS successfully — regardless of unhealthy check results or failed
# Slack delivery, but NOT if the tick raised before persistence. A `finally`
# heartbeat would be a bug: it would mark a crashed/incomplete tick as
# healthy (a false all-clear) — the exact class of failure this plan exists
# to eliminate.


class _DeadmanRecorder:
    def __init__(self, succeed: bool = True) -> None:
        self.calls: list[str] = []
        self.succeed = succeed

    def __call__(self, url: str) -> bool:
        self.calls.append(url)
        return self.succeed


class _RaisingDeadmanPoster:
    def __call__(self, url: str) -> bool:
        raise RuntimeError("deadman endpoint exploded")


class _RaisingSlackPoster:
    def __call__(self, url: str, message: str) -> bool:
        raise RuntimeError("slack endpoint exploded")


class _RaisingProbe:
    def __call__(self, url: str) -> HealthProbeResult:
        raise RuntimeError("probe exploded before persistence")


def _write_deadman_url(
    tmp: Path, url: str = "https://hc-ping.com/test-check-id"
) -> Path:
    p = tmp / "deadman_url"
    p.write_text(url + "\n")
    return p


def _config_with_deadman(
    tmp_path: Path,
    *,
    deadman_path: Path | None = None,
    backup_dir: Path | None = None,
) -> WatchdogConfig:
    base = _config(tmp_path, backup_dir=backup_dir)
    try:
        return _replace(
            base, deadman_url_path=deadman_path or (tmp_path / "no_deadman_url_here")
        )
    except TypeError:
        pytest.fail("WatchdogConfig.deadman_url_path not implemented (Plan 163 T4)")


class TestRunOnceDeadmanHeartbeat:
    def test_url_file_present_pings_exactly_once(self, tmp_path: Path) -> None:
        deadman_path = _write_deadman_url(tmp_path)
        cfg = _config_with_deadman(tmp_path, deadman_path=deadman_path)
        deadman = _DeadmanRecorder()

        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_SlackRecorder(),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
            deadman_poster=deadman,
        )

        assert deadman.calls == ["https://hc-ping.com/test-check-id"]

    def test_url_file_absent_zero_pings_no_error(self, tmp_path: Path) -> None:
        cfg = _config_with_deadman(tmp_path)  # deadman path never written
        deadman = _DeadmanRecorder()

        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_SlackRecorder(),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
            deadman_poster=deadman,
        )

        assert deadman.calls == []

    def test_unhealthy_tick_still_pings(self, tmp_path: Path) -> None:
        # The case most likely to be broken by a well-meaning refactor: an
        # UNHEALTHY tick (failing health probe) must still emit the
        # heartbeat — a ping means "the tick completed", NOT "the stack is
        # healthy" (Plan 163 D2).
        deadman_path = _write_deadman_url(tmp_path)
        cfg = _config_with_deadman(tmp_path, deadman_path=deadman_path)
        deadman = _DeadmanRecorder()

        run_once(
            config=cfg,
            clock=_clock,
            probe=_fail_probe,
            slack_poster=_SlackRecorder(),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
            deadman_poster=deadman,
        )

        assert len(deadman.calls) == 1

    def test_raising_deadman_poster_does_not_propagate_and_state_persisted(
        self, tmp_path: Path
    ) -> None:
        deadman_path = _write_deadman_url(tmp_path)
        cfg = _config_with_deadman(tmp_path, deadman_path=deadman_path)

        # Must NOT raise.
        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_SlackRecorder(),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
            deadman_poster=_RaisingDeadmanPoster(),
        )

        assert cfg.state_path.exists()

    def test_tick_that_raises_before_persistence_does_not_ping(
        self, tmp_path: Path
    ) -> None:
        deadman_path = _write_deadman_url(tmp_path)
        cfg = _config_with_deadman(tmp_path, deadman_path=deadman_path)
        deadman = _DeadmanRecorder()

        with pytest.raises(RuntimeError, match="probe exploded"):
            run_once(
                config=cfg,
                clock=_clock,
                probe=_RaisingProbe(),
                slack_poster=_SlackRecorder(),
                bafu_probe=_bafu_ok_probe,
                bafu_obs_probe=_bafu_obs_ok_probe,
                forecast_freshness_probe=_forecast_freshness_ok_probe,
                backup_device_verifier=lambda _: True,
                launchd_probe=_launchd_ok_probe,
                deadman_poster=deadman,
            )

        assert deadman.calls == []

    def test_ping_observes_state_already_persisted_on_disk(
        self, tmp_path: Path
    ) -> None:
        """Plan 163 fixer round: none of the tests above actually prove the
        ping happens AFTER `state.dump(...)` — they only prove it happens
        (or doesn't) for a given outcome, which would equally pass if the
        implementation pinged immediately BEFORE `state.dump(...)` (the
        plan's central false-all-clear bug: an implementation that pings
        before persisting would mark an incomplete tick healthy). This test
        fails under that bug: the injected poster inspects the state file
        AT THE MOMENT it is invoked and requires it to already exist with
        this tick's persisted content.
        """
        deadman_path = _write_deadman_url(tmp_path)
        cfg = _config_with_deadman(tmp_path, deadman_path=deadman_path)
        assert not cfg.state_path.exists()

        observed: dict[str, object] = {}

        def _poster(url: str) -> bool:
            observed["state_persisted"] = cfg.state_path.exists()
            if cfg.state_path.exists():
                payload = json.loads(cfg.state_path.read_text())
                observed["consecutive_health_failures"] = payload[
                    "consecutive_health_failures"
                ]
            return True

        run_once(
            config=cfg,
            clock=_clock,
            probe=_fail_probe,
            slack_poster=_SlackRecorder(),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
            deadman_poster=_poster,
        )

        assert observed["state_persisted"] is True
        # A failing health probe on a fresh state increments the counter to
        # 1 — proves the poster sees THIS tick's write, not a stale/absent
        # file merely left over from a previous test.
        assert observed["consecutive_health_failures"] == 1

    def test_dump_failure_before_persistence_suppresses_heartbeat(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Locks the other half of the same ordering claim: if
        `state.dump(...)` itself raises (persistence did NOT complete), the
        exception must propagate out of `run_once` (contract: 'NOT if the
        tick raised') and the dead-man poster must never be called —
        proving the ping is gated on `dump()` actually succeeding, not just
        textually placed after the call.
        """
        deadman_path = _write_deadman_url(tmp_path)
        cfg = _config_with_deadman(tmp_path, deadman_path=deadman_path)
        deadman = _DeadmanRecorder()

        def _raise_dump(self: WatchdogState, path: Path) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(WatchdogState, "dump", _raise_dump)

        with pytest.raises(OSError, match="disk full"):
            run_once(
                config=cfg,
                clock=_clock,
                probe=_ok_probe,
                slack_poster=_SlackRecorder(),
                bafu_probe=_bafu_ok_probe,
                bafu_obs_probe=_bafu_obs_ok_probe,
                forecast_freshness_probe=_forecast_freshness_ok_probe,
                backup_device_verifier=lambda _: True,
                launchd_probe=_launchd_ok_probe,
                deadman_poster=deadman,
            )

        assert deadman.calls == []


class TestSlackExceptionDuringBackupTransition:
    """Plan 163 T3 — an UNEXPECTED Slack-poster exception during a backup
    stale/recovered transition must not exit `run_once` before
    `backup_notification_pending` is updated AND persisted (the Plan 162
    Phase A delivery-failure-survival transition), and the dead-man
    heartbeat must still be attempted exactly once."""

    def test_raising_slack_delivery_preserves_pending_and_still_pings(
        self, tmp_path: Path
    ) -> None:
        # No dumps present at all -> newest_backup_mtime returns None ->
        # is_stale=True on a state that starts with no prior incident, so
        # this tick owes exactly one "stale" notification.
        missing_backup_dir = tmp_path / "pg_dumps_missing"
        deadman_path = _write_deadman_url(tmp_path)
        cfg = _config_with_deadman(
            tmp_path, deadman_path=deadman_path, backup_dir=missing_backup_dir
        )
        cfg.slack_path.write_text("https://hooks.slack.com/services/T00/B00/XXX\n")
        deadman = _DeadmanRecorder()

        # Must NOT raise even though the Slack poster explodes while
        # attempting the stale-backup alert.
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_RaisingSlackPoster(),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            launchd_probe=_launchd_ok_probe,
            deadman_poster=deadman,
        )

        assert state.backup_notification_pending == "stale"
        reloaded = WatchdogState.load(cfg.state_path)
        assert reloaded.backup_notification_pending == "stale"
        assert len(deadman.calls) == 1


def _check_health_branch_state(state: WatchdogState) -> None:
    assert state.consecutive_health_failures == 1


def _check_backup_branch_state(state: WatchdogState) -> None:
    assert state.backup_notification_pending == "stale"


def _check_bafu_forecast_branch_state(state: WatchdogState) -> None:
    assert state.consecutive_bafu_failures == 1


def _check_bafu_observation_branch_state(state: WatchdogState) -> None:
    assert state.consecutive_bafu_obs_failures == 1


def _check_forecast_freshness_branch_state(state: WatchdogState) -> None:
    assert state.consecutive_forecast_freshness_failures == 1


def _check_disk_branch_state(state: WatchdogState) -> None:
    assert state.disk_notification_pending == "low"


def _disk_ok_probe(_path: Path) -> DiskSpaceResult:
    return DiskSpaceResult(ok=True, free_bytes=999, total_bytes=1000)


def _disk_low_probe(_path: Path) -> DiskSpaceResult:
    return DiskSpaceResult(ok=False, free_bytes=1, total_bytes=1000)


class TestRaisingSlackPosterAcrossAllFiveAlertBranches:
    """Plan 163 fixer round (minor 3) + Plan 116 fixer round + Plan 199
    fixer round: locks the raising-poster scenario for every one of the SIX
    Slack call sites (health, backup, BAFU-forecast, BAFU-observation,
    forecast-freshness, disk-space). A regression reintroducing a RAW
    `slack_poster(...)` call — instead of the `_safe_slack_post(...)`
    wrapper — in any branch would pass every OTHER existing test, because
    none of them drives a raising poster through the other branches.
    `forecast_freshness_probe` was previously hardwired to
    `_forecast_freshness_ok_probe` in every case here (Plan 116's own
    fixer round finding) — a raw poster call swapped into that fifth
    branch would not have failed this suite; the disk-space branch (Plan
    199) was added the same way. Parameterizes the identical scenario
    across all six sites: each case's own hysteresis/pending field must be
    correctly PERSISTED to disk, and exactly one dead-man heartbeat must
    be attempted, even though the Slack poster explodes.

    Each case is set up so ONLY its target branch's condition triggers an
    alert (the other five checks report healthy/fresh), isolating which
    call site is actually exercised.
    """

    @pytest.mark.parametrize(
        (
            "probe",
            "bafu_probe",
            "bafu_obs_probe",
            "forecast_freshness_probe",
            "backup_fresh",
            "disk_probe",
            "check",
        ),
        [
            pytest.param(
                _fail_probe,
                _bafu_ok_probe,
                _bafu_obs_ok_probe,
                _forecast_freshness_ok_probe,
                True,
                _disk_ok_probe,
                _check_health_branch_state,
                id="health",
            ),
            pytest.param(
                _ok_probe,
                _bafu_ok_probe,
                _bafu_obs_ok_probe,
                _forecast_freshness_ok_probe,
                False,
                _disk_ok_probe,
                _check_backup_branch_state,
                id="backup",
            ),
            pytest.param(
                _ok_probe,
                _bafu_stale_probe,
                _bafu_obs_ok_probe,
                _forecast_freshness_ok_probe,
                True,
                _disk_ok_probe,
                _check_bafu_forecast_branch_state,
                id="bafu_forecast",
            ),
            pytest.param(
                _ok_probe,
                _bafu_ok_probe,
                _bafu_obs_stale_probe,
                _forecast_freshness_ok_probe,
                True,
                _disk_ok_probe,
                _check_bafu_observation_branch_state,
                id="bafu_observation",
            ),
            pytest.param(
                _ok_probe,
                _bafu_ok_probe,
                _bafu_obs_ok_probe,
                _forecast_freshness_stale_probe,
                True,
                _disk_ok_probe,
                _check_forecast_freshness_branch_state,
                id="forecast_freshness",
            ),
            pytest.param(
                _ok_probe,
                _bafu_ok_probe,
                _bafu_obs_ok_probe,
                _forecast_freshness_ok_probe,
                True,
                _disk_low_probe,
                _check_disk_branch_state,
                id="disk",
            ),
        ],
    )
    def test_raising_slack_poster_persists_state_and_pings_once(
        self,
        tmp_path: Path,
        probe: object,
        bafu_probe: object,
        bafu_obs_probe: object,
        forecast_freshness_probe: object,
        backup_fresh: bool,
        disk_probe: object,
        check: object,
    ) -> None:
        backup_dir = (
            _make_fresh_backup(tmp_path, hours_ago=1.0)
            if backup_fresh
            else tmp_path / "pg_dumps_missing"
        )
        deadman_path = _write_deadman_url(tmp_path)
        cfg = _config_with_deadman(
            tmp_path, deadman_path=deadman_path, backup_dir=backup_dir
        )
        cfg.slack_path.write_text("https://hooks.slack.com/services/T00/B00/XXX\n")
        deadman = _DeadmanRecorder()

        # Must NOT raise, regardless of which branch's Slack call explodes.
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=probe,  # type: ignore[arg-type]
            slack_poster=_RaisingSlackPoster(),
            bafu_probe=bafu_probe,  # type: ignore[arg-type]
            bafu_obs_probe=bafu_obs_probe,  # type: ignore[arg-type]
            forecast_freshness_probe=forecast_freshness_probe,  # type: ignore[arg-type]
            backup_device_verifier=lambda _: True,
            disk_probe=disk_probe,  # type: ignore[arg-type]
            launchd_probe=_launchd_ok_probe,
            deadman_poster=deadman,
        )

        check(state)  # type: ignore[operator]
        reloaded = WatchdogState.load(cfg.state_path)
        check(reloaded)  # type: ignore[operator]
        assert len(deadman.calls) == 1


# ---------- Plan 163 T4: CLI wiring for --deadman-url-path -------------------


class TestMainCliDeadmanWiring:
    """`plutil -lint` on the plist proves XML validity, not that the
    parsed `--deadman-url-path` value reaches `run_once`'s config — this
    closes that specific gap."""

    def test_deadman_url_path_flag_reaches_run_once_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sapphire_flow.ops.watchdog as watchdog_module

        captured: dict[str, WatchdogConfig] = {}

        def _fake_run_once(
            *, config: WatchdogConfig, **_kwargs: object
        ) -> WatchdogState:
            captured["config"] = config
            return WatchdogState()

        monkeypatch.setattr(watchdog_module, "run_once", _fake_run_once)
        monkeypatch.setattr(
            watchdog_module, "configure_cli_logging", lambda *_a, **_k: None
        )

        custom_path = tmp_path / "custom_deadman_url"
        exit_code = watchdog_module.main(["--deadman-url-path", str(custom_path)])

        assert exit_code == 0
        assert captured["config"].deadman_url_path == custom_path

    def test_default_deadman_url_path_is_the_module_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sapphire_flow.ops.watchdog as watchdog_module

        captured: dict[str, WatchdogConfig] = {}

        def _fake_run_once(
            *, config: WatchdogConfig, **_kwargs: object
        ) -> WatchdogState:
            captured["config"] = config
            return WatchdogState()

        monkeypatch.setattr(watchdog_module, "run_once", _fake_run_once)
        monkeypatch.setattr(
            watchdog_module, "configure_cli_logging", lambda *_a, **_k: None
        )

        exit_code = watchdog_module.main([])

        assert exit_code == 0
        assert captured["config"].deadman_url_path == Path(
            str(watchdog_module.DEFAULT_DEADMAN_PATH)
        )


class TestMainCliDiskPathWiring:
    """Mirrors `TestMainCliDeadmanWiring` for `--disk-path` (Plan 199
    fixer round, minor): the parsed value must reach `run_once`'s
    `WatchdogConfig.disk_path` — the pre-existing coverage only
    constructed `WatchdogConfig` directly, never proving the CLI flag is
    actually wired through `main`."""

    def test_disk_path_flag_reaches_run_once_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sapphire_flow.ops.watchdog as watchdog_module

        captured: dict[str, WatchdogConfig] = {}

        def _fake_run_once(
            *, config: WatchdogConfig, **_kwargs: object
        ) -> WatchdogState:
            captured["config"] = config
            return WatchdogState()

        monkeypatch.setattr(watchdog_module, "run_once", _fake_run_once)
        monkeypatch.setattr(
            watchdog_module, "configure_cli_logging", lambda *_a, **_k: None
        )

        custom_path = tmp_path / "some-other-volume"
        exit_code = watchdog_module.main(["--disk-path", str(custom_path)])

        assert exit_code == 0
        assert captured["config"].disk_path == custom_path

    def test_default_disk_path_is_the_module_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sapphire_flow.ops.watchdog as watchdog_module

        captured: dict[str, WatchdogConfig] = {}

        def _fake_run_once(
            *, config: WatchdogConfig, **_kwargs: object
        ) -> WatchdogState:
            captured["config"] = config
            return WatchdogState()

        monkeypatch.setattr(watchdog_module, "run_once", _fake_run_once)
        monkeypatch.setattr(
            watchdog_module, "configure_cli_logging", lambda *_a, **_k: None
        )

        exit_code = watchdog_module.main([])

        assert exit_code == 0
        assert captured["config"].disk_path == Path(
            str(watchdog_module.DEFAULT_DISK_PATH)
        )


class TestDiskSpaceOk:
    """Plan 199 D1: the threshold is 5% of the volume's OWN total capacity,
    not an absolute byte count. Pre-implementation, `DISK_FREE_THRESHOLD_PCT`
    is None (see the guarded import above), so every comparison here fails
    as a genuine assertion/TypeError, not a collection-time ImportError."""

    def test_threshold_constant_is_five_percent(self) -> None:
        assert pytest.approx(0.05) == DISK_FREE_THRESHOLD_PCT

    def test_default_disk_path_is_root(self) -> None:
        assert Path("/") == DEFAULT_DISK_PATH

    def test_exactly_at_threshold_is_ok(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Driven through `probe_disk_free` (not a hand-built
        `DiskSpaceResult`, which can't detect a `>` vs `>=` regression since
        its `ok` field is asserted directly rather than computed): 5% of
        1000 is EXACTLY 50, so this is the `>=` boundary itself. A `>`
        regression would make this fail."""
        import sapphire_flow.ops.watchdog as watchdog_module

        class _FakeUsage:
            total = 1000
            free = 50
            used = 950

        monkeypatch.setattr(
            watchdog_module.shutil, "disk_usage", lambda _path: _FakeUsage()
        )

        result = probe_disk_free(tmp_path)

        assert result.ok is True

    def test_just_below_threshold_is_not_ok(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The mirror of the boundary test above: one byte under 5% of
        1000 must NOT be ok — otherwise the boundary test alone could pass
        a broken threshold that treats everything as ok."""
        import sapphire_flow.ops.watchdog as watchdog_module

        class _FakeUsage:
            total = 1000
            free = 49
            used = 951

        monkeypatch.setattr(
            watchdog_module.shutil, "disk_usage", lambda _path: _FakeUsage()
        )

        result = probe_disk_free(tmp_path)

        assert result.ok is False

    def test_probe_disk_free_computes_ratio_from_one_call(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The mac mini's own numbers (Plan 199 D1): 152 GB free on a 3.6 TB
        volume is ~4.2% free — below the 5% floor -> not ok. Also locks
        that `probe_disk_free` reads `shutil.disk_usage` EXACTLY ONCE: two
        separate calls (one for `free`, one for `total`) could observe two
        different instants and silently miscompute the ratio, which a fake
        returning the SAME values on every call could never catch."""
        import sapphire_flow.ops.watchdog as watchdog_module

        total = 3_600 * 1024**3  # ~3.6 TB
        free = 152 * 1024**3  # 152 GB -> ~4.2% free, below the 5% floor
        call_count = 0

        class _FakeUsage:
            def __init__(self) -> None:
                self.total = total
                self.free = free
                self.used = total - free

        def _fake_disk_usage(_path: Path) -> _FakeUsage:
            nonlocal call_count
            call_count += 1
            return _FakeUsage()

        monkeypatch.setattr(watchdog_module.shutil, "disk_usage", _fake_disk_usage)

        result = probe_disk_free(tmp_path)

        assert result.ok is False
        assert result.free_bytes == free
        assert result.total_bytes == total
        assert call_count == 1

    def test_format_disk_space_alert_reports_percentage_and_both_byte_totals(
        self,
    ) -> None:
        """Locks the rendered alert TEXT itself (Plan 199 D1: "the
        percentage is the contract; report the bytes too") — a message
        that dropped the percentage or either byte total would still pass
        the `run_once` tests below, which only check for the "disk space
        LOW" label."""
        from sapphire_flow.ops.watchdog import _format_disk_space_alert

        result = DiskSpaceResult(
            ok=False, free_bytes=152 * 1024**3, total_bytes=3_600 * 1024**3
        )

        message = _format_disk_space_alert(
            hostname="sapphire-mini", path=Path("/"), result=result
        )

        assert "4.2% free" in message
        assert "152 GB of 3600 GB" in message
        assert "threshold: 5%" in message

    def test_probe_disk_free_above_threshold_is_ok(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import sapphire_flow.ops.watchdog as watchdog_module

        total = 3_600 * 1024**3
        free = 500 * 1024**3  # ~13.9% free -> comfortably above 5%

        class _FakeUsage:
            def __init__(self) -> None:
                self.total = total
                self.free = free
                self.used = total - free

        monkeypatch.setattr(
            watchdog_module.shutil, "disk_usage", lambda _path: _FakeUsage()
        )

        result = probe_disk_free(tmp_path)

        assert result.ok is True

    def test_probe_disk_free_unreadable_path_is_not_ok(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import sapphire_flow.ops.watchdog as watchdog_module

        def _raise(_path: Path) -> None:
            raise OSError("no such volume")

        monkeypatch.setattr(watchdog_module.shutil, "disk_usage", _raise)

        result = probe_disk_free(tmp_path)

        assert result.ok is False
        assert result.free_bytes is None
        assert result.error is not None

    def test_probe_disk_free_uses_real_shutil_disk_usage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No stubbing at all — opts OUT of the module's autouse
        `_default_disk_usage_is_healthy` fixture (which every other test in
        this file relies on for determinism) to prove the genuine,
        production `shutil.disk_usage` call still works end-to-end against
        a real filesystem path."""
        monkeypatch.undo()

        result = probe_disk_free(tmp_path)

        assert result.error is None
        assert result.free_bytes is not None
        assert result.total_bytes is not None
        assert result.total_bytes > 0
        assert result.free_bytes <= result.total_bytes


class TestRunOnceDiskSpace:
    """Mirrors `TestRunOnceBackupDeviceVerification` (Plan 194 D6) for the
    DISTINCT free-disk-space condition (Plan 199 T1). Pre-implementation,
    `run_once` does not accept `disk_probe` at all, so every case here fails
    red as a TypeError against the pre-Plan-199 signature — same as the
    backup-device tests did against the pre-Plan-194 signature.

    A single fires-below-threshold assertion is NOT enough (independent
    review of Plan 199's T1): it would be satisfied by an implementation
    that alerts every tick and posts directly via `slack_poster` rather than
    `_safe_slack_post` — this class locks sustained-low SILENCE after the
    first alert, exactly one recovery, retry-on-failed-delivery, and
    independence from a simultaneous backup-device alert."""

    # The plan's own mac-mini figures (Plan 199 D1): 152 GB free of a
    # 3.6 TB volume, ~4.2% free — realistic enough that the rendered
    # percentage/byte-totals assertions below are meaningful, not just
    # rounding artefacts of a toy 1000-byte volume.
    def _low_probe(self, _path: Path) -> DiskSpaceResult:
        return DiskSpaceResult(
            ok=False, free_bytes=152 * 1024**3, total_bytes=3_600 * 1024**3
        )

    def _ok_probe(self, _path: Path) -> DiskSpaceResult:
        return DiskSpaceResult(ok=True, free_bytes=999, total_bytes=1000)

    def test_low_then_repeated_low_then_recovered(self, tmp_path: Path) -> None:
        # A fresh, verified backup dir + verifier isolates this from the
        # (independent) staleness and device-verification checks, so only
        # the disk-space condition is under test here.
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        # Tick 1: first low tick -> alerts exactly once.
        slack1 = _SlackRecorder()
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack1,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            disk_probe=self._low_probe,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.consecutive_disk_low_ticks == 1
        assert len(slack1.calls) == 1
        assert "disk space LOW" in slack1.calls[0][1]
        # Not just the label — the percentage AND both byte totals must be
        # in the message (Plan 199 D1: "the percentage is the contract;
        # report the bytes too").
        assert "4.2% free" in slack1.calls[0][1]
        assert "152 GB of 3600 GB" in slack1.calls[0][1]

        # Tick 2: still low -> hysteresis stays SILENT (never every tick).
        slack2 = _SlackRecorder()
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack2,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            disk_probe=self._low_probe,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.consecutive_disk_low_ticks == 2
        assert slack2.calls == []

        # Tick 3: space recovers -> a single recovery alert.
        slack3 = _SlackRecorder()
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack3,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            disk_probe=self._ok_probe,
            launchd_probe=_launchd_ok_probe,
        )
        assert state.consecutive_disk_low_ticks == 0
        assert len(slack3.calls) == 1
        assert "disk space RECOVERED" in slack3.calls[0][1]

    def test_disk_alert_does_not_suppress_or_duplicate_backup_device_alert(
        self, tmp_path: Path
    ) -> None:
        """The two conditions must never be folded together — a
        SIMULTANEOUSLY low-disk AND unverified-backup-device tick must
        alert on BOTH, exactly once each, not have one swallow the other."""
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)  # fresh: NOT stale
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: False,  # unverified too
            disk_probe=self._low_probe,
            launchd_probe=_launchd_ok_probe,
        )

        messages = [msg for _, msg in slack.calls]
        assert len(messages) == 2
        assert any("backup volume NOT MOUNTED" in m for m in messages)
        assert any("disk space LOW" in m for m in messages)

    def test_disk_alert_is_independent_of_backup_staleness(
        self, tmp_path: Path
    ) -> None:
        """The disk condition must be independent of BACKUP STALENESS too.

        The sibling test above pairs disk-low with an unverified DEVICE. This
        pairs it with a STALE backup, which is a different block entirely.
        Without this, an implementation like `if disk_kind and not is_stale:`
        would suppress the disk alert exactly when backups are already stale —
        and would still pass every other disk test here, because they all use
        a FRESH backup.
        """
        import os

        backup_dir = tmp_path / "pg_dumps"
        backup_dir.mkdir()
        stale_dump = backup_dir / "sapphire_stale_99999999.dump"
        stale_dump.write_bytes(b"dummy")
        stale_ts = (_NOW - timedelta(hours=30)).timestamp()  # > 26 h threshold
        os.utime(stale_dump, (stale_ts, stale_ts))

        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,  # device fine; backup STALE
            disk_probe=self._low_probe,
            launchd_probe=_launchd_ok_probe,
        )

        messages = [msg for _, msg in slack.calls]
        assert any("disk space LOW" in m for m in messages), (
            "a stale backup must NOT swallow the disk alert"
        )
        assert len(messages) == 2, (
            f"expected exactly one stale alert and one disk alert, got: {messages}"
        )

    def test_ok_disk_space_has_no_alert(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        slack = _SlackRecorder()

        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            disk_probe=self._ok_probe,
            launchd_probe=_launchd_ok_probe,
        )

        assert slack.calls == []

    def test_disk_check_receives_configured_disk_path(self, tmp_path: Path) -> None:
        """The injected probe must be called with `config.disk_path` — not
        some other derived path — so a caller-supplied fake can assert on
        it."""
        custom_path = tmp_path / "some-volume"
        cfg = _config(tmp_path)
        cfg = _replace(cfg, disk_path=custom_path)
        received: list[Path] = []

        def _spy_probe(path: Path) -> DiskSpaceResult:
            received.append(path)
            return DiskSpaceResult(ok=True, free_bytes=999, total_bytes=1000)

        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_SlackRecorder(),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            disk_probe=_spy_probe,
            launchd_probe=_launchd_ok_probe,
        )

        assert received == [custom_path]


class TestRunOnceDiskSpaceNotificationStateMachine:
    """Mirrors `TestRunOnceBackupDeviceNotificationStateMachine` for the
    disk-space condition: a LOW alert lost to a failed Slack post must not
    be lost forever, and delivery must go through `_safe_slack_post` (not a
    direct, unguarded `slack_poster(...)` call) so a raising poster cannot
    abort the tick before state persistence and the dead-man ping."""

    def _low_probe(self, _path: Path) -> DiskSpaceResult:
        return DiskSpaceResult(
            ok=False, free_bytes=152 * 1024**3, total_bytes=3_600 * 1024**3
        )

    def test_failed_low_delivery_is_retried_until_it_succeeds(
        self, tmp_path: Path
    ) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        # Tick 1: low, delivery FAILS.
        failing_slack = _SlackRecorder(succeed=False)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=failing_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            disk_probe=self._low_probe,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(failing_slack.calls) == 1
        assert "disk space LOW" in failing_slack.calls[0][1]
        assert "4.2% free" in failing_slack.calls[0][1]
        assert "152 GB of 3600 GB" in failing_slack.calls[0][1]
        assert state.disk_notification_pending == "low"
        assert state.consecutive_disk_low_ticks == 1

        # Tick 2: still low — no NEW hysteresis transition (already
        # latched) — but the pending notification must be retried.
        retry_slack = _SlackRecorder(succeed=True)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=retry_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            disk_probe=self._low_probe,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(retry_slack.calls) == 1
        assert "disk space LOW" in retry_slack.calls[0][1]
        assert state.disk_notification_pending is None

        # A further tick must NOT re-send — pending was cleared, and the
        # condition has not transitioned again.
        quiet_slack = _SlackRecorder(succeed=True)
        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=quiet_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            disk_probe=self._low_probe,
            launchd_probe=_launchd_ok_probe,
        )
        assert quiet_slack.calls == []

    def test_raising_slack_poster_does_not_abort_the_tick(self, tmp_path: Path) -> None:
        """A raising `slack_poster` must be contained by `_safe_slack_post`
        (mirroring the backup-device and health blocks), so the tick still
        reaches state persistence and the dead-man ping (watchdog.py's
        documented ordering). The branch this was salvaged from called
        `slack_poster` directly for the disk-space block — this test fails
        against that shape.

        Mirrors `TestSlackExceptionDuringBackupTransition` and
        `TestRaisingSlackPosterAcrossAllFiveAlertBranches`: a dead-man URL
        and recorder are actually configured here (the earlier version of
        this test configured neither and only checked
        `cfg.state_path.exists()`, which a disk-alert path returning after
        persistence but BEFORE the heartbeat would still pass)."""

        def _raising_poster(_url: str, _message: str) -> bool:
            raise RuntimeError("simulated Slack outage")

        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        deadman_path = _write_deadman_url(tmp_path)
        cfg = _config_with_deadman(
            tmp_path, deadman_path=deadman_path, backup_dir=backup_dir
        )
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        deadman = _DeadmanRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_raising_poster,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            disk_probe=self._low_probe,
            deadman_poster=deadman,
            launchd_probe=_launchd_ok_probe,
        )

        # The tick completed and persisted (state reflects the low tick),
        # rather than propagating the poster's exception.
        assert state.consecutive_disk_low_ticks == 1
        assert state.disk_notification_pending == "low"
        reloaded = WatchdogState.load(cfg.state_path)
        assert reloaded.disk_notification_pending == "low"
        assert len(deadman.calls) == 1

    def test_failed_recovered_delivery_is_retried_until_it_succeeds(
        self, tmp_path: Path
    ) -> None:
        """Mirrors `TestRunOnceBackupDeviceNotificationStateMachine
        .test_failed_verified_delivery_is_retried_until_it_succeeds`: a
        RECOVERY post — not just the opening LOW alert — that is lost to a
        failed Slack call must not be lost forever. Only the LOW-delivery
        case was locked before this; an implementation that dropped a
        failed recovery post silently would still have passed every
        previously-committed disk-space test."""
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        # Tick 1: low, alert DELIVERED cleanly — establishes the incident.
        opening = _SlackRecorder(succeed=True)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=opening,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            disk_probe=self._low_probe,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(opening.calls) == 1
        assert "disk space LOW" in opening.calls[0][1]
        assert state.disk_notification_pending is None

        # Tick 2: space recovers — the RECOVERY delivery FAILS.
        def _ok_probe_local(_path: Path) -> DiskSpaceResult:
            return DiskSpaceResult(ok=True, free_bytes=999, total_bytes=1000)

        failing_recovery = _SlackRecorder(succeed=False)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=failing_recovery,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            disk_probe=_ok_probe_local,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(failing_recovery.calls) == 1
        assert state.disk_notification_pending == "recovered", (
            "a lost recovery post must stay pending, or the operator is "
            "never told disk space came back"
        )

        # Tick 3: still recovered, no new transition — the pending
        # recovery must be retried anyway.
        retry = _SlackRecorder(succeed=True)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=retry,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            disk_probe=_ok_probe_local,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(retry.calls) == 1
        assert "disk space RECOVERED" in retry.calls[0][1]
        assert state.disk_notification_pending is None

        # Tick 4: quiet — pending cleared, condition unchanged.
        quiet = _SlackRecorder(succeed=True)
        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=quiet,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            disk_probe=_ok_probe_local,
            launchd_probe=_launchd_ok_probe,
        )
        assert quiet.calls == []

    def test_failed_low_delivery_then_recovery_before_retry_reports_recovered(
        self, tmp_path: Path
    ) -> None:
        """Condition-reversal-before-retry: mirrors
        `TestRunOnceBackupDeviceNotificationStateMachine
        .test_failed_unverified_delivery_then_recovery_before_retry_reports_verified`.
        Tick 1 goes low and delivery FAILS (`pending == "low"`); tick 2
        space recovers BEFORE the retry. The eventually-delivered message
        must say RECOVERED — the CURRENT, true condition — never resend
        the stale `pending == "low"` value (which would report "LOW" for a
        volume that is, by tick 2, actually fine)."""

        def _ok_probe_local(_path: Path) -> DiskSpaceResult:
            return DiskSpaceResult(ok=True, free_bytes=999, total_bytes=1000)

        backup_dir = _make_fresh_backup(tmp_path, hours_ago=1)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")

        # Tick 1: low, delivery FAILS.
        failing_slack = _SlackRecorder(succeed=False)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=failing_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            disk_probe=self._low_probe,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(failing_slack.calls) == 1
        assert "disk space LOW" in failing_slack.calls[0][1]
        assert state.disk_notification_pending == "low"

        # Tick 2: recovered BEFORE the retry — the retry delivers and must
        # report RECOVERED, not LOW.
        retry_slack = _SlackRecorder(succeed=True)
        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=retry_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            disk_probe=_ok_probe_local,
            launchd_probe=_launchd_ok_probe,
        )
        assert len(retry_slack.calls) == 1
        assert "disk space RECOVERED" in retry_slack.calls[0][1]
        assert state.disk_notification_pending is None
        assert state.consecutive_disk_low_ticks == 0

        # Tick 3: must stay silent — nothing pending, nothing changed.
        quiet_slack = _SlackRecorder(succeed=True)
        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=quiet_slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_freshness_ok_probe,
            backup_device_verifier=lambda _: True,
            disk_probe=_ok_probe_local,
            launchd_probe=_launchd_ok_probe,
        )
        assert quiet_slack.calls == []


class TestWatchdogStateDiskSpaceBackwardCompat:
    """Plan 199 T1 — the new disk-space hysteresis + pending-notification
    fields. Brand new (no predecessor field to migrate from): absent keys
    default cleanly to 0/None."""

    def test_roundtrip_includes_new_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        original = WatchdogState(
            consecutive_disk_low_ticks=3,
            disk_notification_pending="low",
        )
        original.dump(path)
        loaded = WatchdogState.load(path)
        assert loaded == original

    def test_state_file_without_the_new_keys_defaults_to_zero(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "old_state.json"
        p.write_text('{"consecutive_health_failures": 0}')
        s = WatchdogState.load(p)
        assert s.consecutive_disk_low_ticks == 0
        assert s.disk_notification_pending is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
