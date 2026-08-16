"""Host-level watchdog for the SAPPHIRE Flow Mac-mini staging stack.

Probes the API health endpoint, checks backup staleness, and checks the
BAFU forecast collector's freshness heartbeat on every invocation
(scheduled by launchd every 5 min — see
`scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist`).

Hysteresis (health + both BAFU freshness checks): alerts on the first
failure, then only on every 6th consecutive failure (~30 min cadence
at 5 min intervals), and once more when the service recovers.

Backup staleness (Plan 162 T4) uses a DIFFERENT, dedicated "alert
once" policy — not the 6th-failure hysteresis above: exactly one
alert on the first stale tick, silence for as long as it stays stale,
and exactly one recovery alert — see ``_backup_notification_kind``.
A notification that fails delivery is retried every subsequent tick
(regardless of the condition) until it is actually posted.

State is kept in ``~/.sapphire-watchdog-state.json``.

Slack: reads ``./secrets/slack_webhook_url`` (host-process secret —
NOT a Docker secret; see docs/standards/security.md §Secrets
management). If the file is absent or empty the watchdog runs
log-only — structured events are still emitted.

Spec: docs/plans/046-mac-mini-staging-deployment.md §C3.

Flow 4 staleness hook (Plan 111): the BAFU forecast collector
(`flows/collect_bafu_forecasts.py`) is forward-only — a silent stop is
an unrecoverable gap. It emits a `PipelineHealthRecord`
(check_type=bafu_forecast_freshness) on every successful run; this
watchdog probes `/health/detail?check_type=bafu_forecast_freshness`
and alerts if the heartbeat is missing/stale or the last run reported
warning/critical.

Plan 136 adds a second, independently-parameterized freshness check for the
BAFU LINDAS observation archive collector (`flows/collect_bafu_observations.py`,
check_type=bafu_observation_freshness) — additive only; the forecast block
above is untouched. See § Follow-up in Plan 136 for a deferred table-driven
generalization of the two near-identical blocks.

Plan 163 adds a dead-man's switch: after every tick that COMPLETES AND
PERSISTS its state, the watchdog POSTs an empty heartbeat to an off-box
URL read from ``./secrets/deadman_url`` (missing/empty/unreadable/
undecodable -> no ping, no error). The ping is unconditional on check
OUTCOME (an unhealthy stack still pings — Slack is the channel for
*detected* failure, the dead-man is the channel for a watchdog that dies
before it can report anything) but conditional on the tick having reached
persistence: anything that raises earlier in the tick correctly suppresses
the heartbeat, since a missing heartbeat is the intended failure signal.
Plan 163 also hardens every outbound HTTP call (health probe, BAFU-detail
probe, Slack POST, dead-man POST) against more than ``httpx.HTTPError`` —
malformed hand-pasted URLs (``httpx.InvalidURL``, not an ``HTTPError``
subclass), transport/SSL/socket failures (``OSError``) and malformed
IDNA/unicode input (``UnicodeError``) must never kill a tick.
"""

from __future__ import annotations

import argparse
import functools
import json
import socket
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx
import structlog

from sapphire_flow.logging import configure_cli_logging

log = structlog.get_logger(__name__)

DEFAULT_HEALTH_URL = "http://localhost:8000/api/v1/health"
DEFAULT_BACKUP_DIR = Path("/Volumes/sapphire-backup/pg_dumps")
DEFAULT_STATE_PATH = Path.home() / ".sapphire-watchdog-state.json"
DEFAULT_SLACK_PATH = Path("./secrets/slack_webhook_url")
# Plan 147 Slice C: `/health/detail` is admin-only once auth is enforced.
# HOST secret (not a Docker/Compose mount), same convention as
# DEFAULT_SLACK_PATH — the watchdog is a launchd host process.
DEFAULT_PROBE_TOKEN_PATH = Path("./secrets/health_probe_token")
# Plan 163: dead-man's switch ping URL — same convention as the two paths
# above (HOST secret, chmod 600, git-ignored, launchd host process reads
# `./secrets/` directly). A bearer capability: anyone holding it can ping
# and thereby mask an outage.
DEFAULT_DEADMAN_PATH = Path("./secrets/deadman_url")
# Same base as DEFAULT_HEALTH_URL — the JSON API route lives under the same
# `/api/v1` prefix as `/health`, just with a `/detail` suffix + query params.
DEFAULT_BAFU_HEALTH_DETAIL_URL = (
    DEFAULT_HEALTH_URL + "/detail?check_type=bafu_forecast_freshness&limit=1"
)
# Plan 136: the BAFU LINDAS observation archive collector's freshness detail
# URL — a second, independently-parameterized copy of the pattern above (see
# run_once's additive observation-freshness block).
DEFAULT_BAFU_OBS_HEALTH_DETAIL_URL = (
    DEFAULT_HEALTH_URL + "/detail?check_type=bafu_observation_freshness&limit=1"
)


def _bafu_url_from_health(health_url: str) -> str:
    """Derive the BAFU freshness detail URL from the health URL, so overriding
    ``--health-url`` (a different host/port/prefix) automatically retargets the
    freshness probe too — otherwise it would silently keep probing the default
    host and report false stale/missing heartbeats."""
    base = health_url.rsplit("/health", 1)[0]
    return f"{base}/health/detail?check_type=bafu_forecast_freshness&limit=1"


def _bafu_obs_url_from_health(health_url: str) -> str:
    """Same derivation as `_bafu_url_from_health`, for the BAFU LINDAS
    observation archive collector's freshness check (Plan 136)."""
    base = health_url.rsplit("/health", 1)[0]
    return f"{base}/health/detail?check_type=bafu_observation_freshness&limit=1"


BACKUP_STALE_THRESHOLD = timedelta(hours=26)
# The BAFU collector runs hourly (Plan 111) — no heartbeat in 3h means it has
# stopped, not merely running slow.
BAFU_STALE_THRESHOLD = timedelta(hours=3)
# The BAFU LINDAS observation collector also runs hourly (Plan 136, live
# probe 2026-07-21) — stale after ~3h (three missed hourly cycles).
BAFU_OBS_STALE_THRESHOLD = timedelta(hours=3)
HEALTH_CHECK_TIMEOUT_S = 5.0
SLACK_POST_TIMEOUT_S = 5.0
# Plan 163: dead-man ping timeout. Worst-case sequential tick budget is
# health (5s) + BAFU forecast (5s) + BAFU obs (5s) + up to 4 Slack posts
# (5s each, only paid when an alert fires) + this ping (5s) = well under
# the plist's 300 s `StartInterval` even in the all-failing, all-alerting
# case (~45s), so a slow dead-man endpoint cannot cause tick overlap.
DEADMAN_POST_TIMEOUT_S = 5.0
ALERT_REPEAT_EVERY = 6  # every 6th consecutive failure (~30 min at 5 min tick)

# Plan 163 T1: the exception set an outbound HTTP call site must contain so
# a malformed URL or transport failure can never kill a watchdog tick.
# httpx.HTTPError does NOT cover httpx.InvalidURL (verified in this repo's
# httpx 0.28.1: `issubclass(httpx.InvalidURL, httpx.HTTPError)` is False),
# nor transport/SSL/socket setup failures (surface as OSError — ssl.SSLError
# and socket errors are OSError subclasses), nor malformed Unicode/IDNA
# input (UnicodeError). Every call site also adds a final defensive
# `except Exception` (never `BaseException`, which would swallow
# KeyboardInterrupt) as a containment boundary of last resort.
_HTTP_CALL_EXCEPTIONS = (httpx.HTTPError, httpx.InvalidURL, OSError, UnicodeError)


BackupNotificationKind = Literal["stale", "recovered"]


@dataclass(frozen=True, kw_only=True, slots=True)
class WatchdogState:
    """Hysteresis state persisted between invocations."""

    consecutive_health_failures: int = 0
    # Legacy field (pre-Plan-162): written on every stale tick, never read
    # by anything. Kept ONLY for backward-compatible round-tripping of state
    # files written by an older watchdog — no code path sets it anymore
    # (Plan 162 T4 replaces it with `consecutive_backup_stale_failures` +
    # `backup_notification_pending`, the real hysteresis + delivery state).
    last_backup_alert_iso: str | None = None
    consecutive_bafu_failures: int = 0
    consecutive_bafu_obs_failures: int = 0
    # Plan 162 T4: backup-staleness hysteresis, mirroring
    # consecutive_health_failures — the pre-Plan-162 backup block alerted on
    # EVERY stale tick (~288/day at 5 min intervals), absorbing Plan 161 T3.
    consecutive_backup_stale_failures: int = 0
    # Plan 162 T4: a notification this watchdog owes but has not yet
    # DELIVERED. Set when `should_alert_health` says "alert" but the Slack
    # post fails; retried every tick (regardless of the current backup
    # condition) until delivery succeeds, then cleared. Without this, a
    # recovery alert lost to a failed Slack post is lost PERMANENTLY: the
    # hysteresis policy only re-fires on prev_failures > 0, which the
    # recovery tick itself already reset to 0.
    backup_notification_pending: BackupNotificationKind | None = None

    @classmethod
    def load(cls, path: Path) -> WatchdogState:
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("watchdog.state_read_failed", path=str(path), error=str(exc))
            return cls()
        pending_raw = raw.get("backup_notification_pending")
        pending: BackupNotificationKind | None = (
            pending_raw if pending_raw in ("stale", "recovered") else None
        )
        legacy_alert_iso = raw.get("last_backup_alert_iso")
        stale_failures_raw = raw.get("consecutive_backup_stale_failures")
        if stale_failures_raw is None:
            # Plan 162 T4 review fix: a state file written by the PRE-Plan-
            # 162 watchdog has no `consecutive_backup_stale_failures` key at
            # all (KEY ABSENT, not present-and-zero — `dump()` always writes
            # the key going forward, so this branch only ever fires once,
            # on the very first tick after rollout). Treating "key absent"
            # as "no incident" would silently drop an already-unresolved
            # legacy incident: if `last_backup_alert_iso` shows the OLD
            # watchdog had alerted, the very next tick with a fresh backup
            # must still emit the one recovery notification a rollout
            # deserves. Migrate to "an incident is active" (1) rather than
            # invent a fake failure count the old watchdog never tracked.
            consecutive_backup_stale_failures = 1 if legacy_alert_iso else 0
        else:
            consecutive_backup_stale_failures = int(stale_failures_raw)
        return cls(
            consecutive_health_failures=int(raw.get("consecutive_health_failures", 0)),
            last_backup_alert_iso=legacy_alert_iso,
            # Backward compatible with state files written before the Flow 4
            # staleness hook: absent key defaults to 0.
            consecutive_bafu_failures=int(raw.get("consecutive_bafu_failures", 0)),
            # Backward compatible with state files written before Plan 136's
            # observation-freshness check: absent key defaults to 0.
            consecutive_bafu_obs_failures=int(
                raw.get("consecutive_bafu_obs_failures", 0)
            ),
            consecutive_backup_stale_failures=consecutive_backup_stale_failures,
            backup_notification_pending=pending,
        )

    def dump(self, path: Path) -> None:
        payload = {
            "consecutive_health_failures": self.consecutive_health_failures,
            "last_backup_alert_iso": self.last_backup_alert_iso,
            "consecutive_bafu_failures": self.consecutive_bafu_failures,
            "consecutive_bafu_obs_failures": self.consecutive_bafu_obs_failures,
            "consecutive_backup_stale_failures": self.consecutive_backup_stale_failures,
            "backup_notification_pending": self.backup_notification_pending,
        }
        path.write_text(json.dumps(payload, indent=2))


@dataclass(frozen=True, kw_only=True, slots=True)
class HealthProbeResult:
    ok: bool
    http_status: int | None
    error: str | None = None


SlackPoster = Callable[[str, str], bool]
"""(webhook_url, message) -> posted_successfully. Raises nothing."""


def probe_health(url: str, *, client: httpx.Client | None = None) -> HealthProbeResult:
    """Synchronous HTTP probe. Returns ok=True only on 2xx + status=='ok'.

    Plan 163 T1: the exception boundary wraps client construction, the
    request, response handling AND owned-client cleanup — client
    construction happens inside the ``try`` (not before it) because
    construction itself can raise (bad `timeout`, transport setup), and an
    exception there must not escape uncaught either.
    """
    owns_client = client is None
    c: httpx.Client | None = client
    try:
        if c is None:
            c = httpx.Client(timeout=HEALTH_CHECK_TIMEOUT_S)
        resp = c.get(url)
        status = resp.status_code
        if status < 200 or status >= 300:
            return HealthProbeResult(ok=False, http_status=status)
        try:
            payload = resp.json()
        except ValueError as exc:
            return HealthProbeResult(
                ok=False, http_status=status, error=f"invalid_json: {exc}"
            )
        body_status = str(payload.get("status", "")).lower()
        if body_status != "ok":
            return HealthProbeResult(
                ok=False, http_status=status, error=f"body_status:{body_status}"
            )
        return HealthProbeResult(ok=True, http_status=status)
    except _HTTP_CALL_EXCEPTIONS as exc:
        return HealthProbeResult(ok=False, http_status=None, error=str(exc))
    except Exception as exc:  # defensive containment boundary, never BaseException
        log.error("watchdog.probe_health_unexpected_error", error=str(exc))
        return HealthProbeResult(ok=False, http_status=None, error=f"unexpected: {exc}")
    finally:
        if owns_client and c is not None:
            c.close()


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuFreshnessResult:
    found: bool
    checked_at: datetime | None
    status: str | None
    error: str | None = None


def probe_bafu_freshness(
    url: str, *, client: httpx.Client | None = None, token: str | None = None
) -> BafuFreshnessResult:
    """Synchronous probe of `/health/detail?check_type=bafu_forecast_freshness`.

    Returns found=False (never raises) on any HTTP error, non-2xx, invalid
    JSON, or an empty `items` list — the caller treats all of these as
    "no heartbeat found", which is the stale case.

    Plan 147 Slice C: `/health/detail` is admin-only once auth is enforced.
    `token` (the host-secret admin probe token, read by `read_probe_token`)
    is sent as `Authorization: Bearer <token>` when present; omitted when
    None so a pre-auth deployment (or a missing/unreadable/empty token
    file) degrades to the prior unauthenticated 401 → found=False path
    rather than crashing the watchdog tick.

    Plan 163 T1: same exception boundary shape as `probe_health` — client
    construction, request, response handling and owned-client cleanup are
    all inside the guarded region.
    """
    owns_client = client is None
    c: httpx.Client | None = client
    headers = {"Authorization": f"Bearer {token}"} if token else None
    try:
        if c is None:
            c = httpx.Client(timeout=HEALTH_CHECK_TIMEOUT_S)
        resp = c.get(url, headers=headers)
        status_code = resp.status_code
        if status_code < 200 or status_code >= 300:
            return BafuFreshnessResult(
                found=False,
                checked_at=None,
                status=None,
                error=f"http_status:{status_code}",
            )
        try:
            payload: dict[str, Any] = resp.json()
        except ValueError as exc:
            return BafuFreshnessResult(
                found=False, checked_at=None, status=None, error=f"invalid_json: {exc}"
            )
        items: list[Any] = payload.get("items") or []
        if not items:
            return BafuFreshnessResult(
                found=False, checked_at=None, status=None, error="no_records"
            )
        item: dict[str, Any] = items[0]
        checked_at: datetime | None = None
        checked_at_raw: str | None = item.get("checked_at")
        if isinstance(checked_at_raw, str):
            try:
                checked_at = datetime.fromisoformat(checked_at_raw)
            except ValueError:
                checked_at = None
            # Normalize to tz-aware UTC: the `now - checked_at` comparison in
            # run_once is OUTSIDE this try/except, so a naive datetime there
            # would raise TypeError and crash the whole watchdog tick.
            if checked_at is not None and checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=UTC)
        status: str | None = item.get("status")
        return BafuFreshnessResult(
            found=True, checked_at=checked_at, status=status, error=None
        )
    except _HTTP_CALL_EXCEPTIONS as exc:
        return BafuFreshnessResult(
            found=False, checked_at=None, status=None, error=str(exc)
        )
    except Exception as exc:  # defensive containment boundary, never BaseException
        log.error("watchdog.probe_bafu_freshness_unexpected_error", error=str(exc))
        return BafuFreshnessResult(
            found=False, checked_at=None, status=None, error=f"unexpected: {exc}"
        )
    finally:
        if owns_client and c is not None:
            c.close()


def newest_backup_mtime(backup_dir: Path) -> datetime | None:
    """Return the newest *evidence-backed* `sapphire_*.dump` mtime as a UTC
    datetime, or None if none exist (Plan 162 T4).

    Matches the exact published-artifact glob (`sapphire_*.dump`), and
    requires each candidate be a REGULAR file with size > 0, checked via
    `lstat()` + `stat.S_ISREG` — `Path.is_file()` follows symlinks, so a
    fresh symlink (pointing anywhere, including nowhere) or a same-named
    directory would otherwise satisfy freshness without being evidence of
    anything. A 0-byte file (a failed dump under the pre-T3 direct-write
    path, or any other zero-length artifact) is likewise never "fresh".
    """
    if not backup_dir.exists() or not backup_dir.is_dir():
        return None
    newest: float | None = None
    for entry in backup_dir.glob("sapphire_*.dump"):
        try:
            entry_stat = entry.lstat()
        except OSError:
            continue
        if not stat.S_ISREG(entry_stat.st_mode):
            continue
        if entry_stat.st_size <= 0:
            continue
        if newest is None or entry_stat.st_mtime > newest:
            newest = entry_stat.st_mtime
    if newest is None:
        return None
    return datetime.fromtimestamp(newest, tz=UTC)


def read_slack_webhook(path: Path) -> str | None:
    """Return a stripped webhook URL, or None if file absent/empty/unreadable."""
    if not path.exists():
        return None
    try:
        value = path.read_text().strip()
    except OSError as exc:
        log.warning(
            "watchdog.slack_webhook_read_failed", path=str(path), error=str(exc)
        )
        return None
    return value or None


def read_probe_token(path: Path) -> str | None:
    """Return a stripped admin probe token, or None if
    missing/unreadable/empty (mirrors `read_slack_webhook`). Plan 147 Slice
    C: `/health/detail` is admin-only; this is the HOST-secret token
    (`./secrets/health_probe_token`, NOT a Docker/Compose mount — the
    watchdog is a launchd host process reading `./secrets/` directly, same
    as the Slack webhook)."""
    if not path.exists():
        return None
    try:
        value = path.read_text().strip()
    except OSError as exc:
        log.warning("watchdog.probe_token_read_failed", path=str(path), error=str(exc))
        return None
    return value or None


def read_deadman_url(path: Path) -> str | None:
    """Return a stripped dead-man ping URL, or None if the file is
    missing/empty/unreadable/undecodable (Plan 163, mirrors
    `read_slack_webhook`/`read_probe_token`). `Path.read_text()` can raise
    `UnicodeError` on invalid bytes, not only `OSError` — both must degrade
    to "no URL configured", never crash the tick."""
    if not path.exists():
        return None
    try:
        value = path.read_text().strip()
    except (OSError, UnicodeError) as exc:
        log.warning("watchdog.deadman_url_read_failed", path=str(path), error=str(exc))
        return None
    return value or None


def default_slack_poster(url: str, message: str) -> bool:
    payload = {"text": message}
    try:
        resp = httpx.post(url, json=payload, timeout=SLACK_POST_TIMEOUT_S)
    except _HTTP_CALL_EXCEPTIONS as exc:
        log.warning("watchdog.slack_post_failed", error=str(exc))
        return False
    except Exception as exc:  # defensive containment boundary, never BaseException
        log.error("watchdog.slack_post_unexpected_error", error=str(exc))
        return False
    if resp.status_code >= 300:
        log.warning(
            "watchdog.slack_post_failed",
            http_status=resp.status_code,
            body=resp.text[:200],
        )
        return False
    return True


DeadmanPoster = Callable[[str], bool]
"""(ping_url) -> posted_successfully. Raises nothing."""


def default_deadman_poster(url: str) -> bool:
    """POST an empty body to the dead-man ping URL. Success is
    `200 <= status < 300`. Never raises (Plan 163 D4: a dead-man outage
    must never be able to break the process it monitors)."""
    try:
        resp = httpx.post(url, timeout=DEADMAN_POST_TIMEOUT_S)
    except _HTTP_CALL_EXCEPTIONS as exc:
        log.warning("watchdog.deadman_post_failed", error=str(exc))
        return False
    except Exception as exc:  # defensive containment boundary, never BaseException
        log.error("watchdog.deadman_post_unexpected_error", error=str(exc))
        return False
    if 200 <= resp.status_code < 300:
        return True
    log.warning("watchdog.deadman_post_failed", http_status=resp.status_code)
    return False


def should_alert_health(
    prev_failures: int, current_ok: bool, current_fail: bool
) -> bool:
    """Hysteresis decision: alert on 1st failure, every 6th fail, and recovery."""
    if current_ok and prev_failures > 0:
        return True  # recovery
    if current_fail:
        new_count = prev_failures + 1
        if new_count == 1:
            return True  # first failure
        if new_count % ALERT_REPEAT_EVERY == 0:
            return True  # every 6th consecutive failure
    return False


def _backup_notification_kind(
    *, was_stale: bool, is_stale: bool, pending: BackupNotificationKind | None
) -> BackupNotificationKind | None:
    """Plan 162 T4 review fix: the notification this tick OWES, computed
    fresh from the CURRENT condition every time — never by resending
    whatever kind happened to be `pending`.

    A `pending` kind only ever means "the previous tick tried to notify and
    delivery failed"; it is NOT ground truth about what to say now. If the
    condition has moved on since then (a stale alert that failed to deliver,
    followed by a fresh backup landing before the retry — or the mirror: a
    recovery alert that failed to deliver, followed by a NEW staleness
    event), resending the stale `pending` value would report a
    self-contradictory message (e.g. "STALE" for a dump that is now 0h
    old) and — because delivery would then "succeed" — silently swallow the
    real, current-condition alert forever. Recomputing from `is_stale` on
    every tick a notification is owed closes that gap in both directions.
    """
    if pending is not None:
        return "stale" if is_stale else "recovered"
    if is_stale and not was_stale:
        return "stale"
    if was_stale and not is_stale:
        return "recovered"
    return None


def _format_health_alert(
    *, hostname: str, now: datetime, probe: HealthProbeResult
) -> str:
    status_str: str
    if probe.http_status is not None:
        status_str = str(probe.http_status)
    else:
        status_str = "unreachable"
    return (
        f"[SAPPHIRE staging] health check FAILED — host: {hostname}, "
        f"time: {now.isoformat()}, http_status: {status_str}"
    )


def _format_recovery_alert(*, hostname: str, now: datetime) -> str:
    return (
        f"[SAPPHIRE staging] health check RECOVERED — host: {hostname}, "
        f"time: {now.isoformat()}"
    )


def _format_backup_alert(*, newest: datetime | None, threshold: timedelta) -> str:
    newest_str = newest.isoformat() if newest is not None else "none found"
    hours = int(threshold.total_seconds() // 3600)
    return (
        f"[SAPPHIRE staging] backup STALE — newest dump: {newest_str}, "
        f"threshold: {hours}h"
    )


def _format_backup_recovery_alert(*, hostname: str, now: datetime) -> str:
    return (
        f"[SAPPHIRE staging] backup RECOVERED — host: {hostname}, "
        f"time: {now.isoformat()}"
    )


def _format_bafu_stale_alert(
    *, hostname: str, now: datetime, result: BafuFreshnessResult
) -> str:
    last_str = (
        result.checked_at.isoformat() if result.checked_at else "no heartbeat found"
    )
    hours = int(BAFU_STALE_THRESHOLD.total_seconds() // 3600)
    return (
        f"[SAPPHIRE staging] BAFU forecast collector STALE — host: {hostname}, "
        f"time: {now.isoformat()}, last_heartbeat: {last_str}, threshold: {hours}h"
    )


def _format_bafu_degraded_alert(
    *, hostname: str, now: datetime, result: BafuFreshnessResult
) -> str:
    return (
        f"[SAPPHIRE staging] BAFU forecast collector DEGRADED — host: {hostname}, "
        f"time: {now.isoformat()}, status: {result.status}"
    )


def _format_bafu_recovery_alert(*, hostname: str, now: datetime) -> str:
    return (
        f"[SAPPHIRE staging] BAFU forecast collector RECOVERED — host: {hostname}, "
        f"time: {now.isoformat()}"
    )


def _format_bafu_obs_stale_alert(
    *, hostname: str, now: datetime, result: BafuFreshnessResult
) -> str:
    last_str = (
        result.checked_at.isoformat() if result.checked_at else "no heartbeat found"
    )
    hours = int(BAFU_OBS_STALE_THRESHOLD.total_seconds() // 3600)
    return (
        f"[SAPPHIRE staging] BAFU observation collector STALE — host: {hostname}, "
        f"time: {now.isoformat()}, last_heartbeat: {last_str}, threshold: {hours}h"
    )


def _format_bafu_obs_degraded_alert(
    *, hostname: str, now: datetime, result: BafuFreshnessResult
) -> str:
    return (
        f"[SAPPHIRE staging] BAFU observation collector DEGRADED — host: {hostname}, "
        f"time: {now.isoformat()}, status: {result.status}"
    )


def _format_bafu_obs_recovery_alert(*, hostname: str, now: datetime) -> str:
    return (
        f"[SAPPHIRE staging] BAFU observation collector RECOVERED — "
        f"host: {hostname}, time: {now.isoformat()}"
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class WatchdogConfig:
    health_url: str = DEFAULT_HEALTH_URL
    backup_dir: Path = DEFAULT_BACKUP_DIR
    state_path: Path = DEFAULT_STATE_PATH
    slack_path: Path = DEFAULT_SLACK_PATH
    # None → derive from health_url at use (so --health-url retargets it too).
    bafu_health_detail_url: str | None = None
    # Plan 136: same semantics as bafu_health_detail_url, for the BAFU LINDAS
    # observation archive collector's freshness check.
    bafu_obs_health_detail_url: str | None = None
    # Plan 147 Slice C: admin-scoped probe token for the now-authenticated
    # `/health/detail`.
    probe_token_path: Path = DEFAULT_PROBE_TOKEN_PATH
    # Plan 163: dead-man's-switch ping URL.
    deadman_url_path: Path = DEFAULT_DEADMAN_PATH


def _safe_slack_post(poster: SlackPoster, url: str, message: str) -> bool:
    """Plan 163 T3: an INJECTED `slack_poster` is untrusted at this boundary
    — `default_slack_poster` itself never raises, but a caller-supplied
    fake or a future poster implementation might. Without this, an
    unexpected exception here would exit `run_once` before
    `backup_notification_pending` is updated and persisted, losing the
    Plan 162 Phase A delivery-failure-survival transition."""
    try:
        return poster(url, message)
    except Exception as exc:  # defensive: converts delivery exceptions to False
        log.warning("watchdog.slack_post_unexpected_error", error=str(exc))
        return False


def _safe_deadman_post(poster: DeadmanPoster, url: str) -> bool:
    """Same containment as `_safe_slack_post`, for the injected dead-man
    poster: a raising poster must never propagate out of `run_once`."""
    try:
        return poster(url)
    except Exception as exc:  # defensive: converts delivery exceptions to False
        log.warning("watchdog.deadman_post_unexpected_error", error=str(exc))
        return False


def run_once(
    *,
    config: WatchdogConfig,
    clock: Callable[[], datetime],
    probe: Callable[[str], HealthProbeResult],
    slack_poster: SlackPoster,
    hostname: str | None = None,
    bafu_probe: Callable[[str], BafuFreshnessResult] = probe_bafu_freshness,
    bafu_obs_probe: Callable[[str], BafuFreshnessResult] = probe_bafu_freshness,
    deadman_poster: DeadmanPoster = default_deadman_poster,
) -> WatchdogState:
    """Single watchdog tick. Returns the updated state (also persisted)."""
    now = clock()
    host = hostname or socket.gethostname()

    state = WatchdogState.load(config.state_path)

    # --- Health probe ---
    result = probe(config.health_url)
    log.info(
        "pipeline.health_check_completed",
        url=config.health_url,
        ok=result.ok,
        http_status=result.http_status,
        error=result.error,
        prev_failures=state.consecutive_health_failures,
    )

    alert_now = should_alert_health(
        state.consecutive_health_failures,
        current_ok=result.ok,
        current_fail=not result.ok,
    )

    webhook = read_slack_webhook(config.slack_path)

    if alert_now:
        if result.ok:
            message = _format_recovery_alert(hostname=host, now=now)
            log.info("watchdog.health_recovery_alert", message=message)
        else:
            message = _format_health_alert(hostname=host, now=now, probe=result)
            log.warning("watchdog.health_failure_alert", message=message)
        if webhook:
            posted = _safe_slack_post(slack_poster, webhook, message)
            log.info("watchdog.slack_post_attempted", posted=posted)
        else:
            log.info("watchdog.slack_skipped_log_only")

    if result.ok:
        state = replace(state, consecutive_health_failures=0)
    else:
        state = replace(
            state,
            consecutive_health_failures=state.consecutive_health_failures + 1,
        )

    # --- Backup staleness (Plan 162 T4) ---
    # Dedicated "alert once, then only on recovery" policy — NOT
    # `should_alert_health`'s hysteresis, which re-fires on every 6th
    # consecutive failure (~288/day at 5 min intervals absorbing down to
    # ~48/day, still not "once"). `_backup_notification_kind` decides
    # purely from the CURRENT condition (`is_stale`) vs. the condition on
    # entry to this tick (`was_stale`), recomputed fresh whenever a
    # notification is still `pending` from a failed delivery — see its
    # docstring for why blindly resending the pending value is wrong.
    newest = newest_backup_mtime(config.backup_dir)
    is_stale = newest is None or (now - newest) > BACKUP_STALE_THRESHOLD
    log.info(
        "watchdog.backup_check_completed",
        backup_dir=str(config.backup_dir),
        newest=newest.isoformat() if newest else None,
        stale=is_stale,
    )

    was_stale = state.consecutive_backup_stale_failures > 0
    pending = state.backup_notification_pending
    notification_kind = _backup_notification_kind(
        was_stale=was_stale, is_stale=is_stale, pending=pending
    )

    if notification_kind is not None:
        if notification_kind == "stale":
            message = _format_backup_alert(
                newest=newest, threshold=BACKUP_STALE_THRESHOLD
            )
            log.warning("watchdog.backup_stale_alert", message=message)
        else:
            message = _format_backup_recovery_alert(hostname=host, now=now)
            log.info("watchdog.backup_recovery_alert", message=message)

        if webhook:
            posted = _safe_slack_post(slack_poster, webhook, message)
            log.info("watchdog.slack_post_attempted", posted=posted)
            state = replace(
                state,
                backup_notification_pending=None if posted else notification_kind,
            )
        elif pending is not None:
            # Third variant of the notification-loss class: `pending` was
            # ALREADY set on entry to this tick — a previous delivery
            # attempt failed and is awaiting retry. The webhook merely
            # being absent/unreadable RIGHT NOW (a transient secrets-mount
            # hiccup, a rotation in progress) is not a successful delivery
            # and must not be treated as one — clearing pending here would
            # discard the notification forever, since a steady-state tick
            # with no further condition change never recomputes it from
            # scratch. A pending notification is cleared ONLY by confirmed
            # delivery (the `if webhook:` branch above); every other path
            # must leave it exactly as `_backup_notification_kind` just
            # recomputed it, to retry on a later tick.
            log.info("watchdog.slack_skipped_pending_retry_deferred")
            state = replace(state, backup_notification_pending=notification_kind)
        else:
            # Fresh notification, no webhook EVER configured this tick or
            # before: log-only mode, not a delivery failure — never
            # retried, mirrors the health/BAFU checks' behaviour.
            log.info("watchdog.slack_skipped_log_only")
            state = replace(state, backup_notification_pending=None)

    if is_stale:
        state = replace(
            state,
            consecutive_backup_stale_failures=state.consecutive_backup_stale_failures
            + 1,
        )
    else:
        state = replace(state, consecutive_backup_stale_failures=0)

    # --- BAFU forecast collector freshness (Flow 4 staleness hook) ---
    bafu_url = config.bafu_health_detail_url or _bafu_url_from_health(config.health_url)
    bafu_result = bafu_probe(bafu_url)
    bafu_stale = (
        not bafu_result.found
        or bafu_result.checked_at is None
        or (now - bafu_result.checked_at) > BAFU_STALE_THRESHOLD
    )
    bafu_degraded = bafu_result.status in {"warning", "critical"}
    bafu_fail = bafu_stale or bafu_degraded
    log.info(
        "watchdog.bafu_freshness_check_completed",
        url=bafu_url,
        found=bafu_result.found,
        checked_at=bafu_result.checked_at.isoformat()
        if bafu_result.checked_at
        else None,
        status=bafu_result.status,
        error=bafu_result.error,
        stale=bafu_stale,
        degraded=bafu_degraded,
        prev_failures=state.consecutive_bafu_failures,
    )

    # Reuses the health-check hysteresis policy (1st failure, every 6th,
    # recovery) — the decision function is generic, not health-specific.
    bafu_alert_now = should_alert_health(
        state.consecutive_bafu_failures,
        current_ok=not bafu_fail,
        current_fail=bafu_fail,
    )

    if bafu_alert_now:
        if not bafu_fail:
            message = _format_bafu_recovery_alert(hostname=host, now=now)
            log.info("watchdog.bafu_recovery_alert", message=message)
        elif bafu_stale:
            message = _format_bafu_stale_alert(
                hostname=host, now=now, result=bafu_result
            )
            log.warning("watchdog.bafu_stale_alert", message=message)
        else:
            message = _format_bafu_degraded_alert(
                hostname=host, now=now, result=bafu_result
            )
            log.warning("watchdog.bafu_degraded_alert", message=message)
        if webhook:
            posted = _safe_slack_post(slack_poster, webhook, message)
            log.info("watchdog.slack_post_attempted", posted=posted)
        else:
            log.info("watchdog.slack_skipped_log_only")

    if bafu_fail:
        state = replace(
            state, consecutive_bafu_failures=state.consecutive_bafu_failures + 1
        )
    else:
        state = replace(state, consecutive_bafu_failures=0)

    # --- BAFU LINDAS observation collector freshness (Plan 136, additive) ---
    # A second, independently-parameterized copy of the block above — the
    # shipped forecast block's structure is left untouched (zero risk to the
    # green forecast check). See § Follow-up in Plan 136 for the deferred
    # table-driven generalization.
    bafu_obs_url = config.bafu_obs_health_detail_url or _bafu_obs_url_from_health(
        config.health_url
    )
    bafu_obs_result = bafu_obs_probe(bafu_obs_url)
    bafu_obs_stale = (
        not bafu_obs_result.found
        or bafu_obs_result.checked_at is None
        or (now - bafu_obs_result.checked_at) > BAFU_OBS_STALE_THRESHOLD
    )
    bafu_obs_degraded = bafu_obs_result.status in {"warning", "critical"}
    bafu_obs_fail = bafu_obs_stale or bafu_obs_degraded
    log.info(
        "watchdog.bafu_obs_freshness_check_completed",
        url=bafu_obs_url,
        found=bafu_obs_result.found,
        checked_at=bafu_obs_result.checked_at.isoformat()
        if bafu_obs_result.checked_at
        else None,
        status=bafu_obs_result.status,
        error=bafu_obs_result.error,
        stale=bafu_obs_stale,
        degraded=bafu_obs_degraded,
        prev_failures=state.consecutive_bafu_obs_failures,
    )

    bafu_obs_alert_now = should_alert_health(
        state.consecutive_bafu_obs_failures,
        current_ok=not bafu_obs_fail,
        current_fail=bafu_obs_fail,
    )

    if bafu_obs_alert_now:
        if not bafu_obs_fail:
            message = _format_bafu_obs_recovery_alert(hostname=host, now=now)
            log.info("watchdog.bafu_obs_recovery_alert", message=message)
        elif bafu_obs_stale:
            message = _format_bafu_obs_stale_alert(
                hostname=host, now=now, result=bafu_obs_result
            )
            log.warning("watchdog.bafu_obs_stale_alert", message=message)
        else:
            message = _format_bafu_obs_degraded_alert(
                hostname=host, now=now, result=bafu_obs_result
            )
            log.warning("watchdog.bafu_obs_degraded_alert", message=message)
        if webhook:
            posted = _safe_slack_post(slack_poster, webhook, message)
            log.info("watchdog.slack_post_attempted", posted=posted)
        else:
            log.info("watchdog.slack_skipped_log_only")

    if bafu_obs_fail:
        state = replace(
            state,
            consecutive_bafu_obs_failures=state.consecutive_bafu_obs_failures + 1,
        )
    else:
        state = replace(state, consecutive_bafu_obs_failures=0)

    state.dump(config.state_path)

    # --- Dead-man's switch heartbeat (Plan 163) -----------------------------
    # THE HEARTBEAT CONTRACT: exactly once after every tick that completed
    # AND persisted successfully — regardless of unhealthy check results or
    # failed Slack delivery, but NOT if the tick raised. This block is
    # placed strictly after `state.dump(...)` and is the LAST thing
    # `run_once` does: everything above it (hostname lookup, state load,
    # probes, Slack posts, persistence itself) can raise, and if it does the
    # absent heartbeat is the correct signal — do NOT move this into a
    # `finally` or wrap the whole function in a blanket try/except, either
    # of which would mark a crashed/incomplete tick as healthy (a false
    # all-clear). Reading the URL and pinging happen after persistence so a
    # slow or hanging ping can never block or corrupt the state write.
    deadman_url = read_deadman_url(config.deadman_url_path)
    if deadman_url:
        pinged = _safe_deadman_post(deadman_poster, deadman_url)
        log.info("watchdog.deadman_ping_attempted", pinged=pinged)
    else:
        log.info("watchdog.deadman_ping_skipped_no_url")

    return state


def _utc_now() -> datetime:
    return datetime.now(UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sapphire-watchdog",
        description="Host-level watchdog for the SAPPHIRE Mac-mini stack.",
    )
    parser.add_argument(
        "--health-url",
        default=DEFAULT_HEALTH_URL,
        help=f"API health endpoint (default: {DEFAULT_HEALTH_URL})",
    )
    parser.add_argument(
        "--backup-dir",
        default=str(DEFAULT_BACKUP_DIR),
        help="Directory containing *.dump files to check for staleness",
    )
    parser.add_argument(
        "--state-path",
        default=str(DEFAULT_STATE_PATH),
        help="Hysteresis state file (JSON)",
    )
    parser.add_argument(
        "--slack-path",
        default=str(DEFAULT_SLACK_PATH),
        help="Path to file containing the Slack webhook URL (chmod 600)",
    )
    parser.add_argument(
        "--bafu-health-detail-url",
        default=None,
        help=(
            "BAFU forecast collector freshness endpoint "
            "(default: derived from --health-url)"
        ),
    )
    parser.add_argument(
        "--bafu-obs-health-detail-url",
        default=None,
        help=(
            "BAFU LINDAS observation collector freshness endpoint "
            "(default: derived from --health-url)"
        ),
    )
    parser.add_argument(
        "--probe-token-path",
        default=str(DEFAULT_PROBE_TOKEN_PATH),
        help=(
            "Path to a file containing the admin-scoped bearer token used to "
            "probe /health/detail (chmod 600, HOST secret — not a Docker "
            f"mount; default: {DEFAULT_PROBE_TOKEN_PATH})"
        ),
    )
    parser.add_argument(
        "--deadman-url-path",
        default=str(DEFAULT_DEADMAN_PATH),
        help=(
            "Path to a file containing the dead-man's-switch ping URL "
            "(chmod 600, HOST secret, git-ignored). Missing/empty/unreadable "
            f"-> no ping, no error (default: {DEFAULT_DEADMAN_PATH})"
        ),
    )
    args = parser.parse_args(argv)

    configure_cli_logging("INFO")

    config = WatchdogConfig(
        health_url=args.health_url,
        backup_dir=Path(args.backup_dir),
        state_path=Path(args.state_path),
        slack_path=Path(args.slack_path),
        bafu_health_detail_url=args.bafu_health_detail_url,
        bafu_obs_health_detail_url=args.bafu_obs_health_detail_url,
        probe_token_path=Path(args.probe_token_path),
        deadman_url_path=Path(args.deadman_url_path),
    )

    probe_token = read_probe_token(config.probe_token_path)
    bafu_probe_bound = functools.partial(probe_bafu_freshness, token=probe_token)

    try:
        run_once(
            config=config,
            clock=_utc_now,
            probe=probe_health,
            slack_poster=default_slack_poster,
            bafu_probe=bafu_probe_bound,
            bafu_obs_probe=bafu_probe_bound,
        )
    except Exception as exc:  # unrecoverable: let launchd see the non-zero
        log.error("watchdog.unrecoverable_error", error=str(exc))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
