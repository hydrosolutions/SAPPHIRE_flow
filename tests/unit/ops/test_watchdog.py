"""Unit tests for `sapphire_flow.ops.watchdog`.

Dependency injection (clock, probe callable, Slack poster, filesystem
paths via tmp_path) keeps the tests deterministic without needing
respx/httpx_mock/freezegun.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path  # noqa: TC003 — used at runtime in helper

import httpx
import pytest
from structlog.testing import capture_logs

from sapphire_flow.ops.watchdog import (
    ALERT_REPEAT_EVERY,
    BACKUP_STALE_THRESHOLD,
    BAFU_OBS_STALE_THRESHOLD,
    BAFU_STALE_THRESHOLD,
    DEADMAN_PING_TIMEOUT_S,
    DEFAULT_FORECAST_STALE_THRESHOLD_HOURS,
    BafuFreshnessResult,
    DiskSpaceResult,
    ForecastFreshnessResult,
    HealthProbeResult,
    WatchdogConfig,
    WatchdogState,
    default_deadman_poster,
    main,
    newest_backup_mtime,
    probe_disk_free,
    read_deadman_url,
    read_probe_token,
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


def _forecast_ok_probe(_url: str) -> ForecastFreshnessResult:
    # Healthy heartbeat for the Plan 158 D15/T1b forecast-production-
    # freshness check — used as the default `forecast_freshness_probe` fake
    # for every pre-existing test in this file, exactly the role
    # `_bafu_ok_probe`/`_bafu_obs_ok_probe` play above.
    return ForecastFreshnessResult(found=True, cycle_time=_NOW, status="ok", error=None)


def _forecast_stale_probe(_url: str) -> ForecastFreshnessResult:
    return ForecastFreshnessResult(
        found=True,
        cycle_time=_NOW - timedelta(hours=DEFAULT_FORECAST_STALE_THRESHOLD_HOURS + 1),
        status="ok",
        error=None,
    )


def _forecast_not_found_probe(_url: str) -> ForecastFreshnessResult:
    return ForecastFreshnessResult(
        found=False, cycle_time=None, status=None, error="404"
    )


def _forecast_degraded_probe(_url: str) -> ForecastFreshnessResult:
    return ForecastFreshnessResult(
        found=True, cycle_time=_NOW, status="warning", error=None
    )


def _forecast_critical_probe(_url: str) -> ForecastFreshnessResult:
    return ForecastFreshnessResult(
        found=True, cycle_time=_NOW, status="critical", error=None
    )


class _SlackRecorder:
    def __init__(self, succeed: bool = True) -> None:
        self.calls: list[tuple[str, str]] = []
        self.succeed = succeed

    def __call__(self, url: str, message: str) -> bool:
        self.calls.append((url, message))
        return self.succeed


class _DeadmanRecorder:
    def __init__(self, succeed: bool = True) -> None:
        self.calls: list[str] = []
        self.succeed = succeed

    def __call__(self, url: str) -> bool:
        self.calls.append(url)
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
    # Deliberately not created — mirrors slack_path: every pre-existing test
    # in this file gets the missing-secret "feature off" path by default, so
    # adding the dead-man ping (Plan 158 D3) changes none of their behaviour.
    deadman_url_path = tmp / "deadman_url"
    return WatchdogConfig(
        health_url="http://localhost:8000/api/v1/health",
        backup_dir=backup_dir or (tmp / "pg_dumps_missing"),
        state_path=state_path,
        slack_path=slack_path,
        deadman_url_path=deadman_url_path,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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


_MALFORMED_URL = "https://hc-ping.com:notaport/check"
"""Plan 158 T1c F5: a malformed URL (bad port) raises httpx.InvalidURL at
request-construction time — verified NOT to be an httpx.HTTPError subclass.
A hand-pasted secret producing exactly this shape is the realistic trigger
(a typo'd webhook/probe/dead-man URL)."""


class TestProbeHealth:
    def test_probe_connection_error_returns_unreachable(self) -> None:
        from sapphire_flow.ops.watchdog import probe_health

        # unused port on localhost — should error instantly
        result = probe_health("http://127.0.0.1:1/api/v1/health")
        assert result.ok is False
        assert result.http_status is None
        assert result.error is not None

    def test_malformed_url_does_not_raise(self) -> None:
        # Plan 158 T1c F5: httpx.InvalidURL must be caught alongside
        # httpx.HTTPError, or a malformed URL escapes to main()'s
        # unrecoverable handler and kills the watchdog.
        from sapphire_flow.ops.watchdog import probe_health

        result = probe_health(_MALFORMED_URL)
        assert result.ok is False
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

    def test_malformed_url_does_not_raise(self) -> None:
        # Plan 158 T1c F5.
        from sapphire_flow.ops.watchdog import probe_bafu_freshness

        result = probe_bafu_freshness(_MALFORMED_URL)
        assert result.found is False
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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
            forecast_freshness_probe=_forecast_ok_probe,
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


class TestWatchdogStateForecastFreshnessBackwardCompat:
    def test_roundtrip_includes_new_field(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        original = WatchdogState(
            consecutive_health_failures=1,
            consecutive_forecast_freshness_failures=3,
        )
        original.dump(path)
        loaded = WatchdogState.load(path)
        assert loaded == original

    def test_state_written_before_this_plan_defaults_to_zero(
        self, tmp_path: Path
    ) -> None:
        # A state file predating Plan 158 D15/T1b has no
        # `consecutive_forecast_freshness_failures` key at all — must default
        # to 0, not raise.
        p = tmp_path / "old_state.json"
        p.write_text(
            '{"consecutive_health_failures": 2, "last_backup_alert_iso": null, '
            '"consecutive_bafu_failures": 1, "consecutive_bafu_obs_failures": 0}'
        )
        s = WatchdogState.load(p)
        assert s.consecutive_forecast_freshness_failures == 0
        assert s.consecutive_health_failures == 2


# ---------- run_once: forecast-production freshness (Plan 158 D15/T1b) --------


class TestRunOnceForecastFreshness:
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
            forecast_freshness_probe=_forecast_ok_probe,
        )

        assert state.consecutive_forecast_freshness_failures == 0
        assert slack.calls == []

    def test_overridden_health_url_retargets_forecast_probe(
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

        def _spy(url: str) -> ForecastFreshnessResult:
            captured["url"] = url
            return ForecastFreshnessResult(
                found=True, cycle_time=_NOW, status="ok", error=None
            )

        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_SlackRecorder(),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_spy,
        )
        assert captured["url"] == (
            "http://custom:9000/api/v1/health/detail"
            "?check_type=forecast_freshness&limit=1"
        )

    def test_stale_cycle_time_alerts(self, tmp_path: Path) -> None:
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
            forecast_freshness_probe=_forecast_stale_probe,
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
            forecast_freshness_probe=_forecast_not_found_probe,
        )

        assert state.consecutive_forecast_freshness_failures == 1
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "forecast production STALE" in msg
        assert "no heartbeat found" in msg

    def test_degraded_status_alerts(self, tmp_path: Path) -> None:
        # A "warning" record (partial product coverage) is a fresh WRITE but
        # still alerts — Plan 158 D15's explicit rule: recovery requires a
        # fresh AND "ok" record, never any fresh record regardless of status.
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
            forecast_freshness_probe=_forecast_degraded_probe,
        )

        assert state.consecutive_forecast_freshness_failures == 1
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "forecast production INCOMPLETE" in msg
        assert "status: warning" in msg

    def test_critical_status_alerts(self, tmp_path: Path) -> None:
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
            forecast_freshness_probe=_forecast_critical_probe,
        )

        assert state.consecutive_forecast_freshness_failures == 1
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "forecast production INCOMPLETE" in msg
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
            forecast_freshness_probe=_forecast_stale_probe,
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
            forecast_freshness_probe=_forecast_stale_probe,
        )
        assert state.consecutive_forecast_freshness_failures == 2
        assert second_slack.calls == []  # hysteresis: 2nd failure stays silent

    def test_recovery_requires_fresh_and_ok_not_merely_fresh(
        self, tmp_path: Path
    ) -> None:
        # Locks Plan 158 D15's "perpetual WARNING must never read as
        # recovered" rule: a FRESH record with status "warning" must NOT be
        # treated as `current_ok` — proven hysteresis-independently via the
        # counter, which must keep INCREASING (a wrong "recovered" reading
        # would reset it to 0 the moment prev_failures > 0, regardless of
        # what the alert-dedup schedule does around it).
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        WatchdogState(consecutive_forecast_freshness_failures=2).dump(cfg.state_path)
        slack = _SlackRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_degraded_probe,
        )

        assert state.consecutive_forecast_freshness_failures == 3
        assert all("RECOVERED" not in msg for _, msg in slack.calls)

    def test_recovery_alert_on_fresh_ok_record(self, tmp_path: Path) -> None:
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
            forecast_freshness_probe=_forecast_ok_probe,
        )

        assert state.consecutive_forecast_freshness_failures == 0
        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "forecast production RECOVERED" in msg

    def test_check_is_independent_of_health_and_bafu_checks(
        self, tmp_path: Path
    ) -> None:
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
            forecast_freshness_probe=_forecast_stale_probe,
        )

        assert state.consecutive_health_failures == 0
        assert state.consecutive_bafu_failures == 0
        assert state.consecutive_forecast_freshness_failures == 1
        assert len(slack.calls) == 1


# ---------- read_deadman_url (Plan 158 D3) ------------------------------------


class TestReadDeadmanUrl:
    """Mirrors TestReadSlackWebhook/TestReadProbeToken — same
    missing/empty/unreadable -> None contract."""

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert read_deadman_url(tmp_path / "nope") is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "deadman_url"
        p.write_text("")
        assert read_deadman_url(p) is None

    def test_whitespace_only_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "deadman_url"
        p.write_text("   \n  \n")
        assert read_deadman_url(p) is None

    def test_populated_returns_stripped(self, tmp_path: Path) -> None:
        p = tmp_path / "deadman_url"
        p.write_text("https://hc-ping.com/abc-123\n")
        assert read_deadman_url(p) == "https://hc-ping.com/abc-123"

    def test_unreadable_file_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p = tmp_path / "deadman_url"
        p.write_text("https://hc-ping.com/abc-123\n")

        def _raise(self: Path, *args: object, **kwargs: object) -> str:
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "read_text", _raise)
        assert read_deadman_url(p) is None


# ---------- default_deadman_poster (Plan 158 D3) -------------------------------


class TestDefaultDeadmanPoster:
    def test_timeout_returns_false_without_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise_timeout(*args: object, **kwargs: object) -> httpx.Response:
            raise httpx.TimeoutException("timed out")

        monkeypatch.setattr(httpx, "post", _raise_timeout)
        assert default_deadman_poster("https://hc-ping.com/abc-123") is False

    def test_timeout_logs_failure_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise_timeout(*args: object, **kwargs: object) -> httpx.Response:
            raise httpx.TimeoutException("timed out")

        monkeypatch.setattr(httpx, "post", _raise_timeout)
        with capture_logs() as logs:
            default_deadman_poster("https://hc-ping.com/abc-123")

        events = [e for e in logs if e.get("event") == "watchdog.deadman_ping_failed"]
        assert len(events) == 1, logs

    def test_non_2xx_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _fake_post(*args: object, **kwargs: object) -> httpx.Response:
            return httpx.Response(500, request=httpx.Request("POST", args[0]))

        monkeypatch.setattr(httpx, "post", _fake_post)
        assert default_deadman_poster("https://hc-ping.com/abc-123") is False

    def test_non_2xx_logs_failure_event_with_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fake_post(*args: object, **kwargs: object) -> httpx.Response:
            return httpx.Response(500, request=httpx.Request("POST", args[0]))

        monkeypatch.setattr(httpx, "post", _fake_post)
        with capture_logs() as logs:
            default_deadman_poster("https://hc-ping.com/abc-123")

        events = [e for e in logs if e.get("event") == "watchdog.deadman_ping_failed"]
        assert len(events) == 1, logs
        assert events[0]["http_status"] == 500

    def test_success_posts_to_url_with_correct_timeout_and_returns_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # This is the finding this test locks: a "poster" that never calls
        # HTTP and always returns False must NOT pass — we assert both the
        # True return AND that httpx.post was actually invoked with the
        # right URL and the DEADMAN_PING_TIMEOUT_S timeout (5.0s).
        calls: list[tuple[str, dict[str, object]]] = []

        def _fake_post(url: str, **kwargs: object) -> httpx.Response:
            calls.append((url, kwargs))
            return httpx.Response(200, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx, "post", _fake_post)
        result = default_deadman_poster("https://hc-ping.com/abc-123")

        assert result is True
        assert DEADMAN_PING_TIMEOUT_S == 5.0
        assert calls == [
            ("https://hc-ping.com/abc-123", {"timeout": DEADMAN_PING_TIMEOUT_S})
        ]

    def test_malformed_url_returns_false_without_raising(self) -> None:
        # Plan 158 T1c F5: a malformed hand-pasted secret (e.g. a bad port)
        # raises httpx.InvalidURL, which does NOT subclass httpx.HTTPError —
        # the docstring says "Never raises"; this locks it. No monkeypatch:
        # httpx.InvalidURL is raised organically by the real httpx.post call.
        assert default_deadman_poster(_MALFORMED_URL) is False

    def test_malformed_url_logs_failure_event(self) -> None:
        with capture_logs() as logs:
            default_deadman_poster(_MALFORMED_URL)

        events = [e for e in logs if e.get("event") == "watchdog.deadman_ping_failed"]
        assert len(events) == 1, logs


# ---------- default_slack_poster (F5 InvalidURL coverage) ----------------------


class TestDefaultSlackPoster:
    def test_malformed_url_returns_false_without_raising(self) -> None:
        # Plan 158 T1c F5: the Slack poster catches only httpx.HTTPError
        # today — a malformed hand-pasted webhook URL (this repo's REAL
        # incident: today's Slack URL was hand-pasted) would kill the
        # watchdog precisely when it first tries to report an outage.
        from sapphire_flow.ops.watchdog import default_slack_poster

        assert default_slack_poster(_MALFORMED_URL, "test message") is False

    def test_malformed_url_logs_failure_event(self) -> None:
        from sapphire_flow.ops.watchdog import default_slack_poster

        with capture_logs() as logs:
            default_slack_poster(_MALFORMED_URL, "test message")

        events = [e for e in logs if e.get("event") == "watchdog.slack_post_failed"]
        assert len(events) == 1, logs


# ---------- probe_forecast_freshness exercises httpx path (Plan 158 D15/T1b) ---


class TestProbeForecastFreshness:
    def test_probe_connection_error_returns_not_found(self) -> None:
        from sapphire_flow.ops.watchdog import probe_forecast_freshness

        result = probe_forecast_freshness("http://127.0.0.1:1/api/v1/health/detail")
        assert result.found is False
        assert result.cycle_time is None
        assert result.error is not None

    def test_malformed_url_does_not_raise(self) -> None:
        # Plan 158 T1c F5: "T1b's new probe too".
        from sapphire_flow.ops.watchdog import probe_forecast_freshness

        result = probe_forecast_freshness(_MALFORMED_URL)
        assert result.found is False
        assert result.error is not None

    def test_derives_forecast_url_from_custom_health_url(self) -> None:
        from sapphire_flow.ops.watchdog import _forecast_freshness_url_from_health

        assert _forecast_freshness_url_from_health(
            "http://custom:9000/api/v2/health"
        ) == (
            "http://custom:9000/api/v2/health/detail"
            "?check_type=forecast_freshness&limit=1"
        )

    def test_reads_cycle_time_not_checked_at(self) -> None:
        # The defining behavioural difference from probe_bafu_freshness: it
        # must read the `cycle_time` field, not `checked_at` — ageing on the
        # write time would let a replayed historical cycle reset freshness.
        import httpx

        from sapphire_flow.ops.watchdog import probe_forecast_freshness

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            # A record WRITTEN now but for an OLD cycle.
                            "checked_at": _NOW.isoformat(),
                            "cycle_time": "2020-01-01T00:00:00+00:00",
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
        assert result.cycle_time is not None
        assert result.cycle_time.year == 2020

    def test_naive_cycle_time_is_normalized_to_tz_aware(self) -> None:
        import httpx

        from sapphire_flow.ops.watchdog import probe_forecast_freshness

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


# ---------- run_once: dead-man's-switch ping (Plan 158 D3, T1) -----------------


class TestRunOnceDeadman:
    def test_healthy_stack_pings(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.deadman_url_path.write_text("https://hc-ping.com/abc-123")
        deadman = _DeadmanRecorder()

        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_SlackRecorder(),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_ok_probe,
            deadman_poster=deadman,
        )

        assert deadman.calls == ["https://hc-ping.com/abc-123"]

    def test_unhealthy_stack_still_pings(self, tmp_path: Path) -> None:
        # A ping means "the tick completed", not "the stack is healthy" —
        # Slack carries the health signal, the dead-man carries only silence.
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.deadman_url_path.write_text("https://hc-ping.com/abc-123")
        deadman = _DeadmanRecorder()

        run_once(
            config=cfg,
            clock=_clock,
            probe=_fail_probe,
            slack_poster=_SlackRecorder(),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_ok_probe,
            deadman_poster=deadman,
        )

        assert len(deadman.calls) == 1

    def test_missing_secret_skips_ping_without_error(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        # cfg.deadman_url_path deliberately not created.
        deadman = _DeadmanRecorder()

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_SlackRecorder(),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_ok_probe,
            deadman_poster=deadman,
        )

        assert deadman.calls == []
        assert state.consecutive_health_failures == 0  # tick completed normally

    def test_empty_secret_skips_ping(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.deadman_url_path.write_text("   \n")
        deadman = _DeadmanRecorder()

        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_SlackRecorder(),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_ok_probe,
            deadman_poster=deadman,
        )

        assert deadman.calls == []

    def test_ping_failure_does_not_raise_and_tick_completes(
        self, tmp_path: Path
    ) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.deadman_url_path.write_text("https://hc-ping.com/abc-123")
        failing_deadman = _DeadmanRecorder(succeed=False)

        state = run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=_SlackRecorder(),
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_ok_probe,
            deadman_poster=failing_deadman,
        )

        assert failing_deadman.calls == ["https://hc-ping.com/abc-123"]
        assert state.consecutive_health_failures == 0

    def test_exception_earlier_in_tick_skips_ping(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.deadman_url_path.write_text("https://hc-ping.com/abc-123")
        deadman = _DeadmanRecorder()

        def _raising_probe(_url: str) -> HealthProbeResult:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            run_once(
                config=cfg,
                clock=_clock,
                probe=_raising_probe,
                slack_poster=_SlackRecorder(),
                bafu_probe=_bafu_ok_probe,
                bafu_obs_probe=_bafu_obs_ok_probe,
                forecast_freshness_probe=_forecast_ok_probe,
                deadman_poster=deadman,
            )

        assert deadman.calls == []  # ping never reached

    def test_skip_logs_no_url_event(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        # cfg.deadman_url_path deliberately not created.

        with capture_logs() as logs:
            run_once(
                config=cfg,
                clock=_clock,
                probe=_ok_probe,
                slack_poster=_SlackRecorder(),
                bafu_probe=_bafu_ok_probe,
                bafu_obs_probe=_bafu_obs_ok_probe,
                forecast_freshness_probe=_forecast_ok_probe,
                deadman_poster=_DeadmanRecorder(),
            )

        events = [
            e for e in logs if e.get("event") == "watchdog.deadman_skipped_no_url"
        ]
        assert len(events) == 1, logs

    def test_ping_attempted_logs_event_with_pinged_flag(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.deadman_url_path.write_text("https://hc-ping.com/abc-123")

        with capture_logs() as logs:
            run_once(
                config=cfg,
                clock=_clock,
                probe=_ok_probe,
                slack_poster=_SlackRecorder(),
                bafu_probe=_bafu_ok_probe,
                bafu_obs_probe=_bafu_obs_ok_probe,
                forecast_freshness_probe=_forecast_ok_probe,
                deadman_poster=_DeadmanRecorder(),
            )

        events = [
            e for e in logs if e.get("event") == "watchdog.deadman_ping_attempted"
        ]
        assert len(events) == 1, logs
        assert events[0]["pinged"] is True

    def test_state_dump_failure_skips_ping(self, tmp_path: Path) -> None:
        # Ordering proof: the ping fires AFTER state.dump(). Force dump() to
        # fail by pointing state_path at a directory instead of a file —
        # WatchdogState.load() tolerates that (falls back to defaults, logs
        # a warning) but Path.write_text() in dump() raises IsADirectoryError
        # (unguarded), so this isolates a dump-specific failure rather than
        # a load-specific one.
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.state_path.mkdir(parents=True, exist_ok=True)
        cfg.deadman_url_path.write_text("https://hc-ping.com/abc-123")
        deadman = _DeadmanRecorder()

        with pytest.raises(OSError):
            run_once(
                config=cfg,
                clock=_clock,
                probe=_ok_probe,
                slack_poster=_SlackRecorder(),
                bafu_probe=_bafu_ok_probe,
                bafu_obs_probe=_bafu_obs_ok_probe,
                forecast_freshness_probe=_forecast_ok_probe,
                deadman_poster=deadman,
            )

        assert deadman.calls == []  # dump() raised before the ping ran


# ---------- main(): exception -> return 2, no ping (Plan 158 D3, T1) -----------


class TestMainDeadmanOnUnrecoverableError:
    def test_unrecoverable_error_returns_2_and_skips_ping(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sapphire_flow.ops.watchdog as watchdog_module

        def _raise(_url: str) -> HealthProbeResult:
            raise RuntimeError("boom")

        deadman_recorder = _DeadmanRecorder()
        monkeypatch.setattr(watchdog_module, "probe_health", _raise)
        monkeypatch.setattr(watchdog_module, "default_deadman_poster", deadman_recorder)

        deadman_path = tmp_path / "deadman_url"
        deadman_path.write_text("https://hc-ping.com/abc-123")

        argv = [
            "--state-path",
            str(tmp_path / "state.json"),
            "--slack-path",
            str(tmp_path / "slack_webhook_url"),
            "--deadman-url-path",
            str(deadman_path),
            "--backup-dir",
            str(tmp_path / "pg_dumps_missing"),
            "--probe-token-path",
            str(tmp_path / "probe_token"),
        ]

        rc = main(argv)

        assert rc == 2
        assert deadman_recorder.calls == []


class TestMainWiresDeadmanAndDiskProbes:
    """main() must wire the REAL default_deadman_poster and probe_disk_free
    into run_once on a successful tick — the only test above exercises the
    unrecoverable-error path, where the dead-man is skipped by design. This
    proves the successful-tick wiring: an actual httpx POST to the
    configured URL, and an actual disk-space check."""

    def test_successful_tick_pings_deadman_and_checks_disk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sapphire_flow.ops.watchdog as watchdog_module

        # Keep CLI logging config untouched by main() so nothing here
        # depends on structlog's global renderer configuration; the actual
        # wiring proof below is via the httpx.post recorder, not logs.
        monkeypatch.setattr(
            watchdog_module, "configure_cli_logging", lambda *a, **k: None
        )
        monkeypatch.setattr(watchdog_module, "probe_health", _ok_probe)

        def _bafu_ok_probe_flexible(
            _url: str, *, token: str | None = None
        ) -> BafuFreshnessResult:
            return BafuFreshnessResult(
                found=True, checked_at=datetime.now(UTC), status="ok", error=None
            )

        monkeypatch.setattr(
            watchdog_module, "probe_bafu_freshness", _bafu_ok_probe_flexible
        )

        def _forecast_ok_probe_flexible(
            _url: str, *, token: str | None = None
        ) -> ForecastFreshnessResult:
            return ForecastFreshnessResult(
                found=True, cycle_time=datetime.now(UTC), status="ok", error=None
            )

        monkeypatch.setattr(
            watchdog_module, "probe_forecast_freshness", _forecast_ok_probe_flexible
        )

        post_calls: list[tuple[str, dict[str, object]]] = []

        def _fake_post(url: str, **kwargs: object) -> httpx.Response:
            post_calls.append((url, kwargs))
            return httpx.Response(200, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx, "post", _fake_post)

        deadman_path = tmp_path / "deadman_url"
        deadman_path.write_text("https://hc-ping.com/abc-123")

        argv = [
            "--state-path",
            str(tmp_path / "state.json"),
            "--slack-path",
            str(tmp_path / "slack_webhook_url"),
            "--deadman-url-path",
            str(deadman_path),
            "--backup-dir",
            str(tmp_path / "pg_dumps_missing"),
            "--probe-token-path",
            str(tmp_path / "probe_token"),
            "--disk-path",
            str(tmp_path),
        ]

        with capture_logs() as logs:
            rc = main(argv)

        assert rc == 0
        # The dead-man poster was really invoked (not a stub that always
        # returns False without calling HTTP): correct URL, correct timeout.
        assert post_calls == [
            ("https://hc-ping.com/abc-123", {"timeout": DEADMAN_PING_TIMEOUT_S})
        ]
        # The disk probe was really invoked (against the real filesystem —
        # main() always wires probe_disk_free, never leaves it None).
        disk_events = [
            e for e in logs if e.get("event") == "watchdog.disk_space_check_completed"
        ]
        assert len(disk_events) == 1, logs
        assert disk_events[0]["path"] == str(tmp_path)
        assert disk_events[0]["free_bytes"] is not None
        ping_events = [
            e for e in logs if e.get("event") == "watchdog.deadman_ping_attempted"
        ]
        assert len(ping_events) == 1, logs
        assert ping_events[0]["pinged"] is True


# ---------- --forecast-stale-threshold-hours validation (Plan 158 D15) --------


class TestParsePositiveFloatThreshold:
    """Plan 158 D15 reviewer major-fix: argparse's own `type=float` raises
    SystemExit(2) DURING parse_args() — before main()'s try/except and
    therefore before the dead-man ping — so a malformed
    --forecast-stale-threshold-hours would brick monitoring silently. The
    CLI arg is accepted as a raw string and validated here instead, with a
    safe fallback, never a hard exit."""

    def test_valid_value_parses(self) -> None:
        from sapphire_flow.ops.watchdog import _parse_positive_float

        assert _parse_positive_float("8.5", default=12.0, arg_name="--x") == 8.5

    def test_non_numeric_falls_back_to_default(self) -> None:
        from sapphire_flow.ops.watchdog import _parse_positive_float

        assert (
            _parse_positive_float("not-a-number", default=12.0, arg_name="--x") == 12.0
        )

    def test_non_numeric_does_not_raise(self) -> None:
        from sapphire_flow.ops.watchdog import _parse_positive_float

        # The locked behaviour: this must NOT raise SystemExit/ValueError.
        _parse_positive_float("garbage", default=12.0, arg_name="--x")

    def test_zero_falls_back_to_default(self) -> None:
        from sapphire_flow.ops.watchdog import _parse_positive_float

        assert _parse_positive_float("0", default=12.0, arg_name="--x") == 12.0

    def test_negative_falls_back_to_default(self) -> None:
        from sapphire_flow.ops.watchdog import _parse_positive_float

        assert _parse_positive_float("-5", default=12.0, arg_name="--x") == 12.0

    def test_nan_falls_back_to_default(self) -> None:
        from sapphire_flow.ops.watchdog import _parse_positive_float

        assert _parse_positive_float("nan", default=12.0, arg_name="--x") == 12.0

    def test_infinity_falls_back_to_default(self) -> None:
        from sapphire_flow.ops.watchdog import _parse_positive_float

        assert _parse_positive_float("inf", default=12.0, arg_name="--x") == 12.0

    def test_invalid_value_logs_fallback_event(self) -> None:
        from sapphire_flow.ops.watchdog import _parse_positive_float

        with capture_logs() as logs:
            _parse_positive_float("garbage", default=12.0, arg_name="--x")

        events = [
            e for e in logs if e.get("event") == "watchdog.invalid_threshold_fallback"
        ]
        assert len(events) == 1, logs


class TestMainMalformedThresholdDoesNotCrash:
    def test_malformed_threshold_does_not_prevent_the_dead_man_ping(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The end-to-end proof: a malformed --forecast-stale-threshold-hours
        # must not crash main() before the dead-man ping — argparse's own
        # type=float would have raised SystemExit here, well before
        # main()'s try/except.
        import sapphire_flow.ops.watchdog as watchdog_module

        monkeypatch.setattr(
            watchdog_module, "configure_cli_logging", lambda *a, **k: None
        )
        monkeypatch.setattr(watchdog_module, "probe_health", _ok_probe)

        def _bafu_ok_probe_flexible(
            _url: str, *, token: str | None = None
        ) -> BafuFreshnessResult:
            return BafuFreshnessResult(
                found=True, checked_at=datetime.now(UTC), status="ok", error=None
            )

        monkeypatch.setattr(
            watchdog_module, "probe_bafu_freshness", _bafu_ok_probe_flexible
        )

        def _forecast_ok_probe_flexible(
            _url: str, *, token: str | None = None
        ) -> ForecastFreshnessResult:
            return ForecastFreshnessResult(
                found=True, cycle_time=datetime.now(UTC), status="ok", error=None
            )

        monkeypatch.setattr(
            watchdog_module, "probe_forecast_freshness", _forecast_ok_probe_flexible
        )

        deadman_recorder = _DeadmanRecorder()
        monkeypatch.setattr(watchdog_module, "default_deadman_poster", deadman_recorder)

        deadman_path = tmp_path / "deadman_url"
        deadman_path.write_text("https://hc-ping.com/abc-123")

        argv = [
            "--state-path",
            str(tmp_path / "state.json"),
            "--slack-path",
            str(tmp_path / "slack_webhook_url"),
            "--deadman-url-path",
            str(deadman_path),
            "--backup-dir",
            str(tmp_path / "pg_dumps_missing"),
            "--probe-token-path",
            str(tmp_path / "probe_token"),
            "--disk-path",
            str(tmp_path),
            "--forecast-stale-threshold-hours",
            "not-a-number",
        ]

        rc = main(argv)

        assert rc == 0
        assert len(deadman_recorder.calls) == 1


# ---------- probe_disk_free / run_once free-space check (Plan 158 D14) ---------


class TestProbeDiskFree:
    def test_ample_free_space_is_ok(self, tmp_path: Path) -> None:
        result = probe_disk_free(tmp_path)
        assert result.ok is True
        assert result.free_bytes is not None
        assert result.free_bytes > 0

    def test_missing_path_returns_not_ok(self, tmp_path: Path) -> None:
        result = probe_disk_free(tmp_path / "does" / "not" / "exist")
        assert result.ok is False
        assert result.error is not None


class TestRunOnceDiskSpace:
    def test_disk_probe_none_skips_check(self, tmp_path: Path) -> None:
        # disk_probe defaults to None: every pre-existing call site (and
        # every test in this file that doesn't pass it) is unaffected —
        # main() always wires in the real probe in production.
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        slack = _SlackRecorder()

        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_ok_probe,
        )

        assert slack.calls == []

    def test_low_free_space_alerts(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        def _low_disk(_path: Path) -> DiskSpaceResult:
            return DiskSpaceResult(ok=False, free_bytes=1024)

        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_ok_probe,
            disk_probe=_low_disk,
        )

        assert len(slack.calls) == 1
        _, msg = slack.calls[0]
        assert "disk space LOW" in msg

    def test_ample_free_space_no_alert(self, tmp_path: Path) -> None:
        backup_dir = _make_fresh_backup(tmp_path, hours_ago=2)
        cfg = _config(tmp_path, backup_dir=backup_dir)
        cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
        slack = _SlackRecorder()

        def _ample_disk(_path: Path) -> DiskSpaceResult:
            return DiskSpaceResult(ok=True, free_bytes=100 * 1024**3)

        run_once(
            config=cfg,
            clock=_clock,
            probe=_ok_probe,
            slack_poster=slack,
            bafu_probe=_bafu_ok_probe,
            bafu_obs_probe=_bafu_obs_ok_probe,
            forecast_freshness_probe=_forecast_ok_probe,
            disk_probe=_ample_disk,
        )

        assert slack.calls == []


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
