"""Unit tests for `sapphire_flow.ops.watchdog`.

Dependency injection (clock, probe callable, Slack poster, filesystem
paths via tmp_path) keeps the tests deterministic without needing
respx/httpx_mock/freezegun.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path  # noqa: TC003 — used at runtime in helper

import pytest

from sapphire_flow.ops.watchdog import (
    ALERT_REPEAT_EVERY,
    BACKUP_STALE_THRESHOLD,
    BAFU_OBS_STALE_THRESHOLD,
    BAFU_STALE_THRESHOLD,
    BafuFreshnessResult,
    HealthProbeResult,
    WatchdogConfig,
    WatchdogState,
    newest_backup_mtime,
    read_slack_webhook,
    run_once,
    should_alert_health,
)

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
    dump = path / "sapphire-2026-04-22.dump"
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
    )


def _clock() -> datetime:
    return _NOW


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
        old = d / "old.dump"
        new = d / "new.dump"
        old.write_bytes(b"1")
        new.write_bytes(b"2")
        old_ts = (_NOW - timedelta(hours=48)).timestamp()
        new_ts = (_NOW - timedelta(hours=2)).timestamp()
        os.utime(old, (old_ts, old_ts))
        os.utime(new, (new_ts, new_ts))
        result = newest_backup_mtime(d)
        assert result is not None
        assert abs((result - (_NOW - timedelta(hours=2))).total_seconds()) < 1.0


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
        )

        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "none found" in msg


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
        )

        assert state.consecutive_bafu_failures == 0
        assert slack.calls == []

    def test_overridden_health_url_retargets_bafu_probe(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        base = _config(tmp_path, backup_dir=backup_dir)
        cfg = WatchdogConfig(
            health_url="http://custom:9000/api/v1/health",
            backup_dir=base.backup_dir,
            state_path=base.state_path,
            slack_path=base.slack_path,
        )
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
        )

        assert state.consecutive_health_failures == 0
        assert state.consecutive_bafu_failures == 1
        assert len(slack.calls) == 1


# ---------- run_once: BAFU observation-collector freshness (Plan 136, additive) --


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
        )

        assert state.consecutive_bafu_obs_failures == 0
        assert slack.calls == []

    def test_overridden_health_url_retargets_bafu_obs_probe(
        self, tmp_path: Path
    ) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        base = _config(tmp_path, backup_dir=backup_dir)
        cfg = WatchdogConfig(
            health_url="http://custom:9000/api/v1/health",
            backup_dir=base.backup_dir,
            state_path=base.state_path,
            slack_path=base.slack_path,
        )
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
        )

        assert state.consecutive_bafu_obs_failures == 1
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "BAFU observation collector STALE" in msg

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
        )

        assert state.consecutive_health_failures == 0
        assert state.consecutive_bafu_failures == 0
        assert state.consecutive_bafu_obs_failures == 1
        assert len(slack.calls) == 1


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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
