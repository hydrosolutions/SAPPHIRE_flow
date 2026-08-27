"""Host-level watchdog for the SAPPHIRE Flow Mac-mini staging stack.

Probes the API health endpoint, checks backup staleness, and checks the
BAFU forecast collector's freshness heartbeat on every invocation
(scheduled by launchd every 5 min — see
`scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist`).

Hysteresis (health + both BAFU freshness checks + forecast-production
freshness): alerts on the first failure, then only on every 6th
consecutive failure (~30 min cadence at 5 min intervals), and once more
when the service recovers.

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

Plan 116 adds a THIRD, similarly independent freshness check for the
forecast-production heartbeat (`flows/run_forecast_cycle.py`,
check_type=forecast_freshness): a cycle that stores zero forecasts is a
silent-success failure (green flow, dark product) that neither the API
health probe nor the two BAFU checks above can see. This is a SEPARATE
contract from `ForecastCycleHealth` — the emitting flow does not degrade
`health` for a freshness loss, and this watchdog block does not consult
`health` at all, only the dedicated heartbeat record. Same shape as the
BAFU blocks (found/stale/degraded -> fail, hysteresis via
`should_alert_health`), but with a DEDICATED probe and result type
(`probe_forecast_freshness`/`ForecastFreshnessResult`, not
`probe_bafu_freshness`/`BafuFreshnessResult`): the BAFU checks age by
`checked_at` (collector run time), while forecast freshness must age by
the record's `cycle_time` (forecast production time) instead — otherwise
a delayed or long-running cycle could "refresh" the heartbeat with a
stale `cycle_time` and defeat the staleness check.

Plan 163 adds a dead-man's switch: after every tick that COMPLETES AND
PERSISTS its state, the watchdog POSTs an empty heartbeat to an off-box
URL read from ``./secrets/deadman_url`` (missing/empty/unreadable/
undecodable -> no ping, no error). The ping is unconditional on check
OUTCOME (an unhealthy stack still pings — Slack is the channel for
*detected* failure, the dead-man is the channel for a watchdog that dies
before it can report anything) but conditional on the tick having reached
persistence: anything that raises earlier in the tick correctly suppresses
the heartbeat, since a missing heartbeat is the intended failure signal.
Plan 199 T1 (salvaged from the never-merged Plan 158 D14) adds a FOURTH
independent condition: free space on ``config.disk_path`` (default ``/``),
alerting when it drops below 5% of that same volume's total capacity (Plan
199 D1 — the source branch's absolute 20 GiB threshold was rejected).
Same transition-latched shape as the backup-device check (Plan 194 D6): a
distinct persisted counter and pending-notification kind, alerting only on
transition, never every tick.

Plan 163 also hardens every outbound HTTP call (health probe, BAFU-detail
probe, Slack POST, dead-man POST) against more than ``httpx.HTTPError`` —
malformed hand-pasted URLs (``httpx.InvalidURL``, not an ``HTTPError``
subclass), transport/SSL/socket failures (``OSError``) and malformed
IDNA/unicode input (``UnicodeError``) must never kill a tick. The boundary
covers owned-client cleanup too: ``probe_health``/``probe_bafu_freshness``
construct their client inside the guarded region and also guard the
``finally``-block ``close()`` of an owned client, since an unguarded close()
exception would override the try block's return value and still escape.
"""

from __future__ import annotations

import argparse
import functools
import json
import shutil
import socket
import stat
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

import httpx
import structlog

from sapphire_flow.logging import configure_cli_logging

log = structlog.get_logger(__name__)

DEFAULT_HEALTH_URL = "http://localhost:8000/api/v1/health"
DEFAULT_BACKUP_DIR = Path("/Volumes/sapphire-backup/pg_dumps")
# Plan 199 T1 (salvaged from Plan 158 D14): filesystem path checked for free
# space. `/` on the mac mini is the boot volume — the same volume the
# database, dumps and forecasts all ultimately share.
DEFAULT_DISK_PATH = Path("/")
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
# Plan 116: the forecast-production freshness heartbeat's detail URL — a
# third, independently-parameterized copy of the pattern above (see
# run_once's additive forecast-freshness block).
DEFAULT_FORECAST_FRESHNESS_HEALTH_DETAIL_URL = (
    DEFAULT_HEALTH_URL + "/detail?check_type=forecast_freshness&limit=1"
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


def _forecast_freshness_url_from_health(health_url: str) -> str:
    """Same derivation as `_bafu_url_from_health`, for the forecast-
    production freshness check (Plan 116)."""
    base = health_url.rsplit("/health", 1)[0]
    return f"{base}/health/detail?check_type=forecast_freshness&limit=1"


# Plan 199 D1: 5% of the volume's OWN total capacity (from the SAME
# `shutil.disk_usage()` call as the free bytes, so the ratio is never
# computed from two different instants) — NOT an absolute byte count. The
# branch this was salvaged from (Plan 158 D14) used a fixed 20 GiB, which is
# 0.55% of the mac mini's 3.6 TB volume: it would fire far too late to be
# actionable. 5% of that same volume is a ~184 GB floor, which the host has
# already crossed in living memory (95% used, 214 GB free, nothing alerted).
DISK_FREE_THRESHOLD_PCT = 0.05
BACKUP_STALE_THRESHOLD = timedelta(hours=26)
# The BAFU collector runs hourly (Plan 111) — no heartbeat in 3h means it has
# stopped, not merely running slow.
BAFU_STALE_THRESHOLD = timedelta(hours=3)
# The BAFU LINDAS observation collector over-polls a 10-minute publish grid
# at roughly 2.5x (Plan 176 D1, max cyclic gap <=4 min) — stale after ~15
# min (~3 missed polls), NOT the old hourly-cadence "~3h" (Plan 176 D4: that
# value was derived from the falsified hourly-cadence assumption, and was
# only ever coincidentally equal to the flow-side measurement-age
# threshold, which is a different quantity — see
# flows/collect_bafu_observations.py's _STALE_MEASUREMENT_THRESHOLD).
BAFU_OBS_STALE_THRESHOLD = timedelta(minutes=15)
# The forecast cycle runs every 6h by default (SCHEDULE_FORECAST_CYCLE,
# cli/register_deployments.py) — stale after ~18h (three missed cycles),
# matching the "3x the run interval" ratio used for the two BAFU checks
# above.
FORECAST_FRESHNESS_STALE_THRESHOLD = timedelta(hours=18)
HEALTH_CHECK_TIMEOUT_S = 5.0
SLACK_POST_TIMEOUT_S = 5.0
# Plan 163: dead-man ping timeout. Worst-case sequential tick budget is
# health (5s) + BAFU forecast (5s) + BAFU obs (5s) + up to 4 Slack posts
# (5s each, only paid when an alert fires) + this ping (5s) = well under
# the plist's 300 s `StartInterval` even in the all-failing, all-alerting
# case (~45s), so a slow dead-man endpoint cannot cause tick overlap.
DEADMAN_POST_TIMEOUT_S = 5.0
# Plan 195 D4: `launchctl list` timeout. "Finite" alone was too weak — a
# timeout anywhere near the plist's 300 s `StartInterval` would consume the
# whole cadence and delay every later probe (BAFU, forecast freshness,
# state persistence, dead-man). 5.0 s matches the budget above.
LAUNCHD_PROBE_TIMEOUT_S = 5.0
ALERT_REPEAT_EVERY = 6  # every 6th consecutive failure (~30 min at 5 min tick)

# Plan 195 D2: the installer-managed launchd labels this watchdog monitors —
# scripts/launchd/install-launchd.sh's PLISTS, minus the watchdog's own
# label (it cannot observe its own failure; Plan 163's dead-man switch
# covers that separately, within ~20 min). `-recap-probe` and
# `-nepal-forcing` are deliberately excluded: manually bootstrapped, not
# installer-managed (docs/operations/recap-probe-runbook.md,
# docs/operations/nepal-forcing-runbook.md). An explicit constant, not a
# read of the installer script at runtime (Bash array, not Python-
# importable) — kept honest by a parity test in test_watchdog.py that
# fails the suite if PLISTS gains a label this constant does not also gain.
MONITORED_LAUNCHD_LABELS: tuple[str, ...] = (
    "ch.hydrosolutions.sapphire",
    "ch.hydrosolutions.sapphire-docker-prune",
)

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
# Plan 194 D4: a condition DISTINCT from staleness — the backup dir looks
# fresh (correct filename, current mtime) but sits on the same device as
# the data it protects. "unverified"/"verified" mirror "stale"/"recovered"
# above deliberately (same transition-latched shape, D6), but are a
# separate Literal so the two conditions can never be confused for a value
# meant for the other's format function.
BackupDeviceNotificationKind = Literal["unverified", "verified"]

DiskNotificationKind = Literal["low", "recovered"]
# Plan 199 T1: a condition independent of both backup checks above (free
# space on `config.disk_path`, default `/`) — its own Literal so it can
# never be confused for a value meant for the backup conditions' format
# functions, same reasoning as `BackupDeviceNotificationKind` vs
# `BackupNotificationKind`.
LaunchdNotificationKind = Literal["failing", "recovered"]
# Plan 195 D3: per-label transition-latched notification kind for a
# monitored launchd agent's last-exit-status verdict — same shape as
# BackupNotificationKind above, deliberately a separate Literal so the two
# conditions (backup staleness vs. agent health) can never be confused for
# one another's format function.
LaunchdProbeNotificationKind = Literal["unreadable", "readable"]
# Plan 195 D4: the launchd probe ITSELF being unreadable (timeout, missing
# `launchctl`, or unparseable output) is a condition DISTINCT from any
# per-label verdict — latched separately so "the monitor stopped
# monitoring" can never present as "no agents failing".


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
    # Plan 116: forecast-production freshness hysteresis, same shape as the
    # two BAFU counters above.
    consecutive_forecast_freshness_failures: int = 0
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
    # Plan 194 D4/D6: hysteresis for the DISTINCT wrong-device condition,
    # same shape as the two backup-staleness fields above but tracking a
    # different failure mode (device, not freshness) — never folded
    # together, so the two can never suppress or duplicate each other.
    consecutive_backup_device_unverified_ticks: int = 0
    backup_device_notification_pending: BackupDeviceNotificationKind | None = None
    # Plan 199 T1: hysteresis for the free-disk-space condition, same shape
    # as the two backup-device fields above but tracking a different
    # failure mode (disk space, not backup device) — never folded together.
    consecutive_disk_low_ticks: int = 0
    disk_notification_pending: DiskNotificationKind | None = None
    # Plan 195 D3: the set of monitored launchd labels currently FAILING or
    # ABSENT, persisted as a sorted tuple (JSON has no set type, and a
    # frozen dataclass field default must be immutable). Transition-latched
    # membership, not a per-tick flag — see `_launchd_notification_kind`.
    failing_launchd_labels: tuple[str, ...] = ()
    # Plan 195 D3: per-label pending notification, as sorted (label, kind)
    # pairs — the per-label equivalent of `backup_notification_pending`,
    # since two labels can be simultaneously pending and one's failed Slack
    # post must never suppress or swallow the other's.
    launchd_notification_pending: tuple[tuple[str, LaunchdNotificationKind], ...] = ()
    # Plan 195 D4: hysteresis for the probe-unreadable condition, same shape
    # as `consecutive_backup_device_unverified_ticks` — counts consecutive
    # ticks the launchd probe itself could not be read.
    launchd_probe_unreadable_ticks: int = 0
    launchd_probe_notification_pending: LaunchdProbeNotificationKind | None = None

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
        device_pending_raw = raw.get("backup_device_notification_pending")
        device_pending: BackupDeviceNotificationKind | None = (
            device_pending_raw
            if device_pending_raw in ("unverified", "verified")
            else None
        )
        disk_pending_raw = raw.get("disk_notification_pending")
        disk_pending: DiskNotificationKind | None = (
            disk_pending_raw if disk_pending_raw in ("low", "recovered") else None
        )
        failing_labels_raw = raw.get("failing_launchd_labels")
        failing_launchd_labels: tuple[str, ...] = (
            tuple(
                sorted(str(label) for label in cast("list[object]", failing_labels_raw))
            )
            if isinstance(failing_labels_raw, list)
            else ()
        )
        launchd_pending_raw = raw.get("launchd_notification_pending")
        launchd_notification_pending: tuple[
            tuple[str, LaunchdNotificationKind], ...
        ] = (
            tuple(
                sorted(
                    (str(label), kind)
                    for label, kind in cast(
                        "dict[object, object]", launchd_pending_raw
                    ).items()
                    if kind in ("failing", "recovered")
                )
            )
            if isinstance(launchd_pending_raw, dict)
            else ()
        )
        launchd_probe_pending_raw = raw.get("launchd_probe_notification_pending")
        launchd_probe_notification_pending: LaunchdProbeNotificationKind | None = (
            launchd_probe_pending_raw
            if launchd_probe_pending_raw in ("unreadable", "readable")
            else None
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
            # Backward compatible with state files written before Plan 116's
            # forecast-freshness check: absent key defaults to 0.
            consecutive_forecast_freshness_failures=int(
                raw.get("consecutive_forecast_freshness_failures", 0)
            ),
            # Backward compatible with state files written before Plan 194's
            # wrong-device check: absent key defaults to 0/None.
            consecutive_backup_device_unverified_ticks=int(
                raw.get("consecutive_backup_device_unverified_ticks", 0)
            ),
            backup_device_notification_pending=device_pending,
            # Backward compatible with state files written before Plan 199's
            # disk-space check: absent key defaults to 0/None.
            consecutive_disk_low_ticks=int(raw.get("consecutive_disk_low_ticks", 0)),
            disk_notification_pending=disk_pending,
            # Backward compatible with state files written before Plan 195's
            # launchd-agent-health check: absent key defaults to ()/0/None.
            failing_launchd_labels=failing_launchd_labels,
            launchd_notification_pending=launchd_notification_pending,
            launchd_probe_unreadable_ticks=int(
                raw.get("launchd_probe_unreadable_ticks", 0)
            ),
            launchd_probe_notification_pending=launchd_probe_notification_pending,
        )

    def dump(self, path: Path) -> None:
        payload = {
            "consecutive_health_failures": self.consecutive_health_failures,
            "last_backup_alert_iso": self.last_backup_alert_iso,
            "consecutive_bafu_failures": self.consecutive_bafu_failures,
            "consecutive_bafu_obs_failures": self.consecutive_bafu_obs_failures,
            "consecutive_backup_stale_failures": self.consecutive_backup_stale_failures,
            "backup_notification_pending": self.backup_notification_pending,
            "consecutive_forecast_freshness_failures": (
                self.consecutive_forecast_freshness_failures
            ),
            "consecutive_backup_device_unverified_ticks": (
                self.consecutive_backup_device_unverified_ticks
            ),
            "backup_device_notification_pending": (
                self.backup_device_notification_pending
            ),
            "consecutive_disk_low_ticks": self.consecutive_disk_low_ticks,
            "disk_notification_pending": self.disk_notification_pending,
            "failing_launchd_labels": list(self.failing_launchd_labels),
            "launchd_notification_pending": dict(self.launchd_notification_pending),
            "launchd_probe_unreadable_ticks": self.launchd_probe_unreadable_ticks,
            "launchd_probe_notification_pending": (
                self.launchd_probe_notification_pending
            ),
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
        # Plan 163 fixer round: an owned client's close() can itself raise
        # (OSError from a half-torn-down transport, etc). Left unguarded,
        # that exception replaces whatever `return`/exception the try block
        # already produced and escapes the boundary this function exists to
        # provide — the same containment as the request itself, applied to
        # cleanup.
        if owns_client and c is not None:
            try:
                c.close()
            except _HTTP_CALL_EXCEPTIONS as exc:
                log.warning("watchdog.probe_health_client_close_failed", error=str(exc))
            except Exception as exc:  # never BaseException
                log.error(
                    "watchdog.probe_health_client_close_unexpected_error",
                    error=str(exc),
                )


def _parse_probe_timestamp(raw: object) -> datetime | None:
    """Parse an ISO-8601 timestamp from a `/health/detail` JSON item field,
    normalized to tz-aware UTC (a naive datetime would blow up the `now -
    ts` comparisons in `run_once` with a TypeError). Returns None on any
    non-string, malformed, or absent value — never raises."""
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


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
        checked_at = _parse_probe_timestamp(item.get("checked_at"))
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
        # Plan 163 fixer round: same cleanup guard as `probe_health` — an
        # owned client's close() can raise, and unguarded that would
        # override the try block's return/exception and escape containment.
        if owns_client and c is not None:
            try:
                c.close()
            except _HTTP_CALL_EXCEPTIONS as exc:
                log.warning(
                    "watchdog.probe_bafu_freshness_client_close_failed", error=str(exc)
                )
            except Exception as exc:  # never BaseException
                log.error(
                    "watchdog.probe_bafu_freshness_client_close_unexpected_error",
                    error=str(exc),
                )


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastFreshnessResult:
    """Plan 116 fixer round (blocker fix): a DEDICATED probe result for
    `check_type=forecast_freshness`, distinct from `BafuFreshnessResult`.

    The BAFU checks correctly age by `checked_at` (when the collector last
    RAN). Forecast freshness must age by `cycle_time` (the forecast
    PRODUCTION time the record describes) instead — correctness
    requirement 2: a delayed or long-running cycle that writes its
    heartbeat "now" but carries a stale `cycle_time` must still alarm.
    Reusing `BafuFreshnessResult` (which has no `cycle_time` field) made
    that distinction impossible to enforce; this type makes the missing
    field a `mypy`/`pyright` error instead of a silent semantic bug.
    """

    found: bool
    checked_at: datetime | None
    cycle_time: datetime | None
    status: str | None
    error: str | None = None
    # Fixer round (minor): parsed from `detail.forecasts_stored` so the
    # CRITICAL alert can distinguish "stored zero forecasts" from a
    # forced-CRITICAL partial-store failure (Plan 116 fixer round, major
    # 1/blocker) where `forecasts_stored > 0`. None when the record has no
    # `detail.forecasts_stored` (e.g. not present, or the wrong type).
    forecasts_stored: int | None = None


def probe_forecast_freshness(
    url: str, *, client: httpx.Client | None = None, token: str | None = None
) -> ForecastFreshnessResult:
    """Synchronous probe of `/health/detail?check_type=forecast_freshness`.

    Same shape and exception boundary as `probe_bafu_freshness`, but ALSO
    parses the record's `cycle_time` (`api/schemas.py:PipelineHealthRecordResponse
    .cycle_time`) — the field `run_once` must compare against, not
    `checked_at`. Returns found=False (never raises) on any HTTP error,
    non-2xx, invalid JSON, or an empty `items` list.
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
            return ForecastFreshnessResult(
                found=False,
                checked_at=None,
                cycle_time=None,
                status=None,
                error=f"http_status:{status_code}",
            )
        try:
            payload: dict[str, Any] = resp.json()
        except ValueError as exc:
            return ForecastFreshnessResult(
                found=False,
                checked_at=None,
                cycle_time=None,
                status=None,
                error=f"invalid_json: {exc}",
            )
        items: list[Any] = payload.get("items") or []
        if not items:
            return ForecastFreshnessResult(
                found=False,
                checked_at=None,
                cycle_time=None,
                status=None,
                error="no_records",
            )
        item: dict[str, Any] = items[0]
        checked_at = _parse_probe_timestamp(item.get("checked_at"))
        cycle_time = _parse_probe_timestamp(item.get("cycle_time"))
        status: str | None = item.get("status")
        detail_raw = item.get("detail")
        forecasts_stored: int | None = None
        if isinstance(detail_raw, dict):
            detail = cast("dict[str, object]", detail_raw)
            raw_forecasts_stored = detail.get("forecasts_stored")
            if isinstance(raw_forecasts_stored, int):
                forecasts_stored = raw_forecasts_stored
        return ForecastFreshnessResult(
            found=True,
            checked_at=checked_at,
            cycle_time=cycle_time,
            status=status,
            error=None,
            forecasts_stored=forecasts_stored,
        )
    except _HTTP_CALL_EXCEPTIONS as exc:
        return ForecastFreshnessResult(
            found=False, checked_at=None, cycle_time=None, status=None, error=str(exc)
        )
    except Exception as exc:  # defensive containment boundary, never BaseException
        log.error("watchdog.probe_forecast_freshness_unexpected_error", error=str(exc))
        return ForecastFreshnessResult(
            found=False,
            checked_at=None,
            cycle_time=None,
            status=None,
            error=f"unexpected: {exc}",
        )
    finally:
        # Same cleanup guard as `probe_bafu_freshness` — an owned client's
        # close() can raise, and unguarded that would override the try
        # block's return/exception and escape containment.
        if owns_client and c is not None:
            try:
                c.close()
            except _HTTP_CALL_EXCEPTIONS as exc:
                log.warning(
                    "watchdog.probe_forecast_freshness_client_close_failed",
                    error=str(exc),
                )
            except Exception as exc:  # never BaseException
                log.error(
                    "watchdog.probe_forecast_freshness_client_close_unexpected_error",
                    error=str(exc),
                )


def _device_id(path: Path) -> int | None:
    """Filesystem device id for `path`, or None if it cannot be stat'd."""
    try:
        return path.stat().st_dev
    except OSError:
        return None


def backup_target_verified(backup_dir: Path, *, data_dir: Path) -> bool:
    """Plan 194 D1: the backup target is verified only if BOTH hold — its
    device id differs from `data_dir`'s (the path that actually holds the
    data — never `/`; on the launchd host this is `Path.home()`, i.e.
    `/Users/sapphire`), AND its mount root (`backup_dir.parent`) is a
    REAL, currently-mounted volume, not merely a directory Docker
    silently creates for a missing bind-mount host path.

    Accepted limitation (Plan 194 D1): `st_dev` is a *volume* id, not a
    *physical disk* id — a second APFS volume on the boot container's own
    disk would satisfy this and still share its single point of hardware
    failure. Detecting that needs `diskutil info -plist` parentage, more
    apparatus than this guard is worth; it catches the failure actually
    seen on the mini (no volume at all) and is honest about the one it
    cannot.
    """
    if not backup_dir.is_dir():
        return False
    backup_dev = _device_id(backup_dir)
    data_dev = _device_id(data_dir)
    if backup_dev is None or data_dev is None or backup_dev == data_dev:
        return False
    return backup_dir.parent.is_mount()


def default_backup_target_verifier(backup_dir: Path) -> bool:
    """Default `backup_device_verifier` for `run_once` (Plan 194): wraps
    `backup_target_verified` against the real filesystem, using
    `Path.home()` as the data path — a pure derivation, matching
    `DEFAULT_STATE_PATH`'s existing convention, so no new config field or
    CLI flag is needed. Injected (like every other `run_once` probe) so
    tests can stub the OS-level device/mount checks without a real
    distinct device."""
    return backup_target_verified(backup_dir, data_dir=Path.home())


@dataclass(frozen=True, kw_only=True, slots=True)
class DiskSpaceResult:
    ok: bool
    free_bytes: int | None
    total_bytes: int | None
    error: str | None = None


def _disk_space_ok(*, free_bytes: int, total_bytes: int) -> bool:
    """True iff `free_bytes` is at least `DISK_FREE_THRESHOLD_PCT` of
    `total_bytes` (Plan 199 D1). `total_bytes <= 0` cannot yield a
    meaningful ratio and is treated as NOT ok — fail-safe, matching
    `probe_disk_free`'s own "unreadable counts as ok=False" convention."""
    if total_bytes <= 0:
        return False
    return (free_bytes / total_bytes) >= DISK_FREE_THRESHOLD_PCT


def probe_disk_free(path: Path) -> DiskSpaceResult:
    """Free space on the filesystem containing `path`, checked against
    `DISK_FREE_THRESHOLD_PCT` of that SAME volume's total capacity (Plan
    199 D1) — `free` and `total` come from one `shutil.disk_usage()` call,
    so the ratio can never be computed from two different instants. Never
    raises: an unreadable/missing path counts as ok=False (fail-safe: a
    false alert is better than a silently-skipped check)."""
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        return DiskSpaceResult(
            ok=False, free_bytes=None, total_bytes=None, error=str(exc)
        )
    return DiskSpaceResult(
        ok=_disk_space_ok(free_bytes=usage.free, total_bytes=usage.total),
        free_bytes=usage.free,
        total_bytes=usage.total,
    )


LaunchdVerdictKind = Literal["ok", "failing", "absent", "unknown"]


@dataclass(frozen=True, kw_only=True, slots=True)
class LaunchdVerdict:
    """Plan 195 D1: per-label read of `launchctl list`'s documented
    two-column format (`launchctl(1)`, NOT `print`, whose structure Apple
    explicitly disclaims: "Do NOT rely on the structure or information
    emitted for ANY reason."). `status` is only meaningful when `kind ==
    "failing"` — it carries the raw second-column value, which may be
    negative (the negation of the stopping signal, e.g. `-15` == SIGTERM)."""

    kind: LaunchdVerdictKind
    status: int | None = None


def parse_launchctl_list_output(
    output: str, labels: Sequence[str]
) -> dict[str, LaunchdVerdict]:
    """Pure parser (Plan 195 D1) for `launchctl list`'s three whitespace-
    separated columns (PID, last exit status, label):

    - a label present with status `0` -> OK. This conflates never-run with
      succeeded, deliberately: a periodic agent that has not yet fired is
      normal, and only a non-zero status is evidence of failure.
    - a label present with a non-zero status (including negative) -> FAILING.
    - a label the output does not mention at all -> ABSENT, itself treated
      as failing by the caller (D1: an unloaded agent is the loudest
      version of this defect, not a reason for silence).
    - output containing no parseable data row at all -> every requested
      label degrades to UNKNOWN, never guessed at as ABSENT.
    - a row that HAS the documented three-column shape but fails to
      validate (PID is neither `-` nor an integer, or status is not an
      integer) -> that label, specifically, degrades to UNKNOWN. It is
      mentioned in the output, so it must never silently fall through to
      ABSENT (which would misreport a structurally-malformed row as "not
      loaded") — see `_valid_launchctl_pid`.
    """
    entries: dict[str, int] = {}
    malformed_labels: set[str] = set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 3:
            # A row we cannot parse STRUCTURALLY must not let its label fall
            # through to ABSENT either (D1: unparseable -> UNKNOWN). The
            # pid/status guard below already covers three-column rows; a row
            # with an unexpected column count reaches only here, and
            # `launchctl list` output that mentions a monitored label is
            # evidence the agent exists, not evidence it is unloaded.
            malformed_labels.update(token for token in parts if token in labels)
            continue
        pid, status_str, label = parts
        if pid == "PID" and status_str == "Status" and label == "Label":
            continue  # header row
        if not _valid_launchctl_pid(pid) or not _is_int(status_str):
            malformed_labels.add(label)
            continue
        entries[label] = int(status_str)

    if not entries and not malformed_labels:
        return {label: LaunchdVerdict(kind="unknown") for label in labels}

    verdicts: dict[str, LaunchdVerdict] = {}
    for label in labels:
        if label in malformed_labels:
            verdicts[label] = LaunchdVerdict(kind="unknown")
        elif label not in entries:
            verdicts[label] = LaunchdVerdict(kind="absent")
        elif entries[label] == 0:
            verdicts[label] = LaunchdVerdict(kind="ok")
        else:
            verdicts[label] = LaunchdVerdict(kind="failing", status=entries[label])
    return verdicts


def _is_int(value: str) -> bool:
    try:
        int(value)
    except ValueError:
        return False
    return True


def _valid_launchctl_pid(value: str) -> bool:
    """`launchctl list`'s documented PID column is either `-` (not
    running) or a numeric PID — anything else means the row does not
    actually have the documented shape, even though it split into three
    whitespace-separated fields."""
    return value == "-" or _is_int(value)


def probe_launchd_agents(
    labels: Sequence[str] = MONITORED_LAUNCHD_LABELS,
    *,
    timeout: float = LAUNCHD_PROBE_TIMEOUT_S,
) -> dict[str, LaunchdVerdict]:
    """Injectable probe (Plan 195 T1) — same seam as `backup_device_verifier`:
    tests inject a fake so they never shell out to the real `launchctl`.
    Never raises: a missing executable, a timeout, or a non-zero exit all
    degrade every requested label to UNKNOWN, exactly like unparseable
    stdout does inside `parse_launchctl_list_output`.
    """
    try:
        # Fixed argv, no shell, no user-controlled input.
        completed = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        # UnicodeError: `text=True` decodes stdout, and launchctl output is
        # not guaranteed valid UTF-8. It is neither an OSError nor a
        # TimeoutExpired, so without it a decode failure would propagate out
        # of `run_once` and skip BAFU, forecast freshness, disk, the state
        # dump and the dead-man ping — D4's "must never abort the tick".
        log.warning("watchdog.launchd_probe_failed", error=str(exc))
        return {label: LaunchdVerdict(kind="unknown") for label in labels}
    except Exception as exc:  # defensive containment boundary, never BaseException
        # Same final boundary the HTTP probes use: an unanticipated failure
        # in the probe must degrade to UNKNOWN, never take the tick down.
        log.error("watchdog.launchd_probe_unexpected_error", error=str(exc))
        return {label: LaunchdVerdict(kind="unknown") for label in labels}
    if completed.returncode != 0:
        # `check=False` means a non-zero exit does NOT raise — stdout from
        # a failed invocation is not the documented table and must never
        # be trusted as healthy just because it happens to parse.
        log.warning(
            "watchdog.launchd_probe_failed",
            error="non-zero exit",
            returncode=completed.returncode,
        )
        return {label: LaunchdVerdict(kind="unknown") for label in labels}
    return parse_launchctl_list_output(completed.stdout, labels)


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
    to "no URL configured", never crash the tick.

    Plan 163 fixer round (minor 1): deliberately does NOT preflight with
    `path.exists()` — on Python 3.12, `exists()` (which calls `stat()`) can
    itself re-raise a non-ignored `OSError` (e.g. a permission error on a
    parent directory), and a preflight call sitting OUTSIDE this guard
    would violate the documented "unreadable => None, no error" contract.
    A missing file is just another `read_text()` failure (`FileNotFoundError`,
    an `OSError` subclass) caught by the same guard as every other
    unreadable case.
    """
    try:
        value = path.read_text().strip()
    except (OSError, UnicodeError) as exc:
        log.warning("watchdog.deadman_url_read_failed", path=str(path), error=str(exc))
        return None
    return value or None


def default_slack_poster(url: str, message: str) -> bool:
    """Plan 163 fixer round (minor 2): response handling (`status_code`,
    `text`) happens INSIDE this try — an unexpected exception accessing
    either (e.g. a fake/wrapped response in a test, or a future httpx
    change) is caught by the same boundary as the request itself, so this
    function's 'never raises' promise holds for callers other than
    `run_once` too (whose `_safe_slack_post` wrapper would otherwise be the
    only thing containing the damage)."""
    payload = {"text": message}
    try:
        resp = httpx.post(url, json=payload, timeout=SLACK_POST_TIMEOUT_S)
        if resp.status_code >= 300:
            log.warning(
                "watchdog.slack_post_failed",
                http_status=resp.status_code,
                body=resp.text[:200],
            )
            return False
        return True
    except _HTTP_CALL_EXCEPTIONS as exc:
        log.warning("watchdog.slack_post_failed", error=str(exc))
        return False
    except Exception as exc:  # defensive containment boundary, never BaseException
        log.error("watchdog.slack_post_unexpected_error", error=str(exc))
        return False


DeadmanPoster = Callable[[str], bool]
"""(ping_url) -> posted_successfully. Raises nothing."""


def default_deadman_poster(url: str) -> bool:
    """POST an empty body to the dead-man ping URL. Success is
    `200 <= status < 300`. Never raises (Plan 163 D4: a dead-man outage
    must never be able to break the process it monitors).

    Plan 163 fixer round (minor 2): `status_code` access happens INSIDE
    this try, same rationale as `default_slack_poster`."""
    try:
        resp = httpx.post(url, timeout=DEADMAN_POST_TIMEOUT_S)
        if 200 <= resp.status_code < 300:
            return True
        log.warning("watchdog.deadman_post_failed", http_status=resp.status_code)
        return False
    except _HTTP_CALL_EXCEPTIONS as exc:
        log.warning("watchdog.deadman_post_failed", error=str(exc))
        return False
    except Exception as exc:  # defensive containment boundary, never BaseException
        log.error("watchdog.deadman_post_unexpected_error", error=str(exc))
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


def _backup_device_notification_kind(
    *,
    was_unverified: bool,
    is_unverified: bool,
    pending: BackupDeviceNotificationKind | None,
) -> BackupDeviceNotificationKind | None:
    """Same shape as `_backup_notification_kind`, for the DISTINCT
    wrong-device condition (Plan 194 D4/D6): alert on TRANSITION only —
    when the volume goes unverified, and again when it becomes verified —
    never on every tick (the mini's condition would otherwise be TRUE
    forever), and never resend a stale `pending` kind once the condition
    has moved on (same reasoning as `_backup_notification_kind`)."""
    if pending is not None:
        return "unverified" if is_unverified else "verified"
    if is_unverified and not was_unverified:
        return "unverified"
    if was_unverified and not is_unverified:
        return "verified"
    return None


def _disk_notification_kind(
    *, was_low: bool, is_low: bool, pending: DiskNotificationKind | None
) -> DiskNotificationKind | None:
    """Same shape as `_backup_device_notification_kind` (Plan 199, mirroring
    Plan 194 D6): alert on TRANSITION only — never every tick (the mini's
    condition can stay true for days), and never resend a stale `pending`
    kind once the condition has moved on."""
    if pending is not None:
        return "low" if is_low else "recovered"
    if is_low and not was_low:
        return "low"
    if was_low and not is_low:
        return "recovered"
    return None


def _launchd_notification_kind(
    *, was_failing: bool, is_failing: bool, pending: LaunchdNotificationKind | None
) -> LaunchdNotificationKind | None:
    """Same shape as `_backup_notification_kind`, per launchd label (Plan
    195 D3): alert on TRANSITION only, and never resend a stale `pending`
    kind once the condition has moved on — same reasoning as the backup
    blocks' equivalents applies here."""
    if pending is not None:
        return "failing" if is_failing else "recovered"
    if is_failing and not was_failing:
        return "failing"
    if was_failing and not is_failing:
        return "recovered"
    return None


def _launchd_probe_notification_kind(
    *,
    was_unreadable: bool,
    is_unreadable: bool,
    pending: LaunchdProbeNotificationKind | None,
) -> LaunchdProbeNotificationKind | None:
    """Same shape again, for the probe-unreadable condition (Plan 195 D4) —
    distinct from every per-label verdict, so a dead probe cannot present
    as "no agents failing"."""
    if pending is not None:
        return "unreadable" if is_unreadable else "readable"
    if is_unreadable and not was_unreadable:
        return "unreadable"
    if was_unreadable and not is_unreadable:
        return "readable"
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


def _format_backup_device_alert(*, backup_dir: Path) -> str:
    return (
        "[SAPPHIRE staging] backup volume NOT MOUNTED — dumps would land on "
        f"the boot disk: {backup_dir}"
    )


def _format_backup_device_recovery_alert(*, hostname: str, now: datetime) -> str:
    return (
        f"[SAPPHIRE staging] backup volume VERIFIED — host: {hostname}, "
        f"time: {now.isoformat()}"
    )


def _format_disk_space_alert(
    *, hostname: str, path: Path, result: DiskSpaceResult
) -> str:
    """The percentage is the contract (Plan 199 D1); the bytes are reported
    alongside because "4.1% free" alone is not actionable — the operator
    needs "4.1% free (152 GB of 3654 GB)" to judge urgency."""
    if result.free_bytes is not None and result.total_bytes:
        pct = 100.0 * result.free_bytes / result.total_bytes
        free_gb = result.free_bytes / (1024**3)
        total_gb = result.total_bytes / (1024**3)
        space_str = f"{pct:.1f}% free ({free_gb:.0f} GB of {total_gb:.0f} GB)"
    else:
        space_str = "unknown"
    detail = f", error: {result.error}" if result.error else ""
    return (
        f"[SAPPHIRE staging] disk space LOW — host: {hostname}, path: {path}, "
        f"{space_str}, threshold: {DISK_FREE_THRESHOLD_PCT * 100:.0f}%{detail}"
    )


def _format_disk_space_recovery_alert(*, hostname: str, now: datetime) -> str:
    return (
        f"[SAPPHIRE staging] disk space RECOVERED — host: {hostname}, "
        f"time: {now.isoformat()}"
    )


def _format_launchd_alert(
    *, hostname: str, now: datetime, label: str, verdict: LaunchdVerdict
) -> str:
    # D1: "last run failed", never "is failing now" — a KeepAlive agent can
    # be RUNNING right now while its last-exit-status column still shows a
    # previous invocation's non-zero code (measured on the mini: the
    # stack-starter shows `state = running`, `last exit code = 1`).
    # ABSENT is a distinct condition (unloaded entirely, no exit status to
    # report) and gets its own word, not a fabricated exit code.
    if verdict.kind == "absent":
        return (
            f"[SAPPHIRE staging] launchd agent ABSENT — label: {label}, "
            f"host: {hostname}, time: {now.isoformat()}"
        )
    return (
        f"[SAPPHIRE staging] launchd agent LAST RUN FAILED — label: {label}, "
        f"last exit status: {verdict.status}, host: {hostname}, "
        f"time: {now.isoformat()}"
    )


def _format_launchd_recovery_alert(*, hostname: str, now: datetime, label: str) -> str:
    return (
        f"[SAPPHIRE staging] launchd agent RECOVERED — label: {label}, "
        f"host: {hostname}, time: {now.isoformat()}"
    )


def _format_launchd_probe_unreadable_alert(*, hostname: str, now: datetime) -> str:
    return (
        "[SAPPHIRE staging] launchd probe UNREADABLE — agent health cannot "
        f"be verified, host: {hostname}, time: {now.isoformat()}"
    )


def _format_launchd_probe_recovery_alert(*, hostname: str, now: datetime) -> str:
    return (
        "[SAPPHIRE staging] launchd probe RECOVERED — agent health checks "
        f"resumed, host: {hostname}, time: {now.isoformat()}"
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


def _format_minutes_aware_duration(threshold: timedelta) -> str:
    """Render a threshold as whole hours when it divides evenly, otherwise
    whole minutes. Plan 176 D4: BAFU_OBS_STALE_THRESHOLD became sub-hour
    (15m) — `int(seconds // 3600)` alone silently renders ANY sub-hour
    threshold as "0h", lying about the alert's own trigger condition."""
    total_seconds = int(threshold.total_seconds())
    if total_seconds % 3600 == 0:
        return f"{total_seconds // 3600}h"
    return f"{total_seconds // 60}m"


def _format_bafu_obs_stale_alert(
    *, hostname: str, now: datetime, result: BafuFreshnessResult
) -> str:
    last_str = (
        result.checked_at.isoformat() if result.checked_at else "no heartbeat found"
    )
    threshold_str = _format_minutes_aware_duration(BAFU_OBS_STALE_THRESHOLD)
    return (
        f"[SAPPHIRE staging] BAFU observation collector STALE — host: {hostname}, "
        f"time: {now.isoformat()}, last_heartbeat: {last_str}, "
        f"threshold: {threshold_str}"
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


def _format_forecast_freshness_stale_alert(
    *, hostname: str, now: datetime, result: ForecastFreshnessResult
) -> str:
    # Ages by `cycle_time` (the forecast's production time), not
    # `checked_at` (when the record was written) — correctness requirement
    # 2. A missing record (found=False) or one with no cycle_time both read
    # as "no heartbeat found".
    last_str = (
        result.cycle_time.isoformat() if result.cycle_time else "no heartbeat found"
    )
    hours = int(FORECAST_FRESHNESS_STALE_THRESHOLD.total_seconds() // 3600)
    return (
        f"[SAPPHIRE staging] forecast production STALE — host: {hostname}, "
        f"time: {now.isoformat()}, last_cycle: {last_str}, threshold: {hours}h"
    )


def _format_forecast_freshness_critical_alert(
    *, hostname: str, now: datetime, result: ForecastFreshnessResult
) -> str:
    # Fixer round (minor): a forced-CRITICAL record (Plan 116 fixer round,
    # major 1/blocker — a mid-cycle fatal store failure AFTER some
    # forecasts already stored) has `forecasts_stored > 0`, so the old
    # unconditional "stored ZERO forecasts" wording was factually wrong
    # for that case. `forecasts_stored is None` covers records where the
    # detail wasn't parseable — keep the original zero wording rather
    # than assert a specific (unknown) count.
    if result.forecasts_stored is not None and result.forecasts_stored > 0:
        outcome = f"stored {result.forecasts_stored} forecast(s) then failed"
    else:
        outcome = "stored ZERO forecasts"
    return (
        f"[SAPPHIRE staging] forecast cycle {outcome} — "
        f"host: {hostname}, time: {now.isoformat()}, status: {result.status}"
    )


def _format_forecast_freshness_recovery_alert(*, hostname: str, now: datetime) -> str:
    return (
        f"[SAPPHIRE staging] forecast production RECOVERED — "
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
    # Plan 116: same semantics as bafu_health_detail_url, for the
    # forecast-production freshness check.
    forecast_freshness_health_detail_url: str | None = None
    # Plan 147 Slice C: admin-scoped probe token for the now-authenticated
    # `/health/detail`.
    probe_token_path: Path = DEFAULT_PROBE_TOKEN_PATH
    # Plan 163: dead-man's-switch ping URL.
    deadman_url_path: Path = DEFAULT_DEADMAN_PATH
    # Plan 199 T1 (salvaged from Plan 158 D14): filesystem path checked for
    # free space.
    disk_path: Path = DEFAULT_DISK_PATH


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
    forecast_freshness_probe: Callable[
        [str], ForecastFreshnessResult
    ] = probe_forecast_freshness,
    deadman_poster: DeadmanPoster = default_deadman_poster,
    # Plan 194: the backup-target device predicate, injected like every
    # other probe above so tests can stub the OS-level device/mount checks
    # without a real distinct device.
    backup_device_verifier: Callable[[Path], bool] = default_backup_target_verifier,
    # Plan 199 T1: the free-disk-space probe, injected like every other
    # probe above so tests can stub the OS-level disk_usage() call without
    # a real filesystem in the required state.
    disk_probe: Callable[[Path], DiskSpaceResult] = probe_disk_free,
    # Plan 195: the launchd-agent-health predicate, injected like every
    # other probe above so tests never shell out to the real `launchctl`.
    launchd_probe: Callable[
        [Sequence[str]], dict[str, LaunchdVerdict]
    ] = probe_launchd_agents,
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

    # --- Backup target device verification (Plan 194 D1/D4) -----------------
    # A condition DISTINCT from staleness (D4), evaluated BEFORE it: the
    # backup directory can look perfectly fresh (correct filename, current
    # mtime) while sitting on the SAME device as the data it protects —
    # Docker silently creates a missing bind-mount host path, so an
    # absent/unmounted external disk becomes a healthy-looking plain
    # directory. D6: alerts on TRANSITION only (never every tick — on this
    # host the condition is permanently true today), via the same
    # pending-retry shape as the staleness block below.
    device_verified = backup_device_verifier(config.backup_dir)
    is_device_unverified = not device_verified
    log.info(
        "watchdog.backup_device_check_completed",
        backup_dir=str(config.backup_dir),
        verified=device_verified,
    )

    was_device_unverified = state.consecutive_backup_device_unverified_ticks > 0
    device_pending = state.backup_device_notification_pending
    device_notification_kind = _backup_device_notification_kind(
        was_unverified=was_device_unverified,
        is_unverified=is_device_unverified,
        pending=device_pending,
    )

    if device_notification_kind is not None:
        if device_notification_kind == "unverified":
            device_message = _format_backup_device_alert(backup_dir=config.backup_dir)
            log.warning(
                "watchdog.backup_device_unverified_alert", message=device_message
            )
        else:
            device_message = _format_backup_device_recovery_alert(
                hostname=host, now=now
            )
            log.info("watchdog.backup_device_recovery_alert", message=device_message)

        if webhook:
            device_posted = _safe_slack_post(slack_poster, webhook, device_message)
            log.info("watchdog.slack_post_attempted", posted=device_posted)
            state = replace(
                state,
                backup_device_notification_pending=(
                    None if device_posted else device_notification_kind
                ),
            )
        elif device_pending is not None:
            # Same delivery-loss reasoning as the staleness block below: a
            # webhook merely absent/unreadable RIGHT NOW must not be
            # treated as delivery of an already-pending notification.
            log.info("watchdog.slack_skipped_pending_retry_deferred")
            state = replace(
                state, backup_device_notification_pending=device_notification_kind
            )
        else:
            log.info("watchdog.slack_skipped_log_only")
            state = replace(state, backup_device_notification_pending=None)

    if is_device_unverified:
        state = replace(
            state,
            consecutive_backup_device_unverified_ticks=(
                state.consecutive_backup_device_unverified_ticks + 1
            ),
        )
    else:
        state = replace(state, consecutive_backup_device_unverified_ticks=0)

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

    # --- Launchd agent health (Plan 195) -------------------------------------
    # D1: probed via `launchctl list` (documented format), never `print`
    # (explicitly disclaimed by Apple). D2: only the installer-managed
    # labels in MONITORED_LAUNCHD_LABELS. D3: one distinct condition,
    # latched PER LABEL — the currently-failing-label SET, alerting on
    # entry/exit, never every tick. D4: the probe being unreadable at all
    # (timeout, missing `launchctl`, unparseable output) is its OWN
    # latched condition, separate from every per-label verdict, and
    # per-label state is held UNCHANGED while the probe is unreadable.
    launchd_verdicts = launchd_probe(MONITORED_LAUNCHD_LABELS)
    probe_readable = any(
        launchd_verdicts.get(label, LaunchdVerdict(kind="unknown")).kind != "unknown"
        for label in MONITORED_LAUNCHD_LABELS
    )
    log.info(
        "watchdog.launchd_probe_completed",
        readable=probe_readable,
        verdicts={
            label: launchd_verdicts.get(label, LaunchdVerdict(kind="unknown")).kind
            for label in MONITORED_LAUNCHD_LABELS
        },
    )

    if probe_readable:
        prev_failing = set(state.failing_launchd_labels)
        prev_pending = dict(state.launchd_notification_pending)
        new_failing: set[str] = set()
        new_pending: dict[str, LaunchdNotificationKind] = {}

        for label in MONITORED_LAUNCHD_LABELS:
            verdict = launchd_verdicts.get(label, LaunchdVerdict(kind="unknown"))
            was_failing = label in prev_failing

            if verdict.kind == "unknown":
                # Mixed-result probe (Plan 195 fixer round): the probe AS A
                # WHOLE returned something readable (`probe_readable` is
                # True), but THIS label's own row was individually
                # unparseable/absent-from-the-parse. Resolving that to
                # "not failing" would fabricate a spurious RECOVERED for an
                # incident that never actually cleared, and resolving it to
                # "failing" would fabricate a spurious FAILING with no
                # evidence. Carry the label's prior membership and pending
                # payload forward UNCHANGED and skip notification entirely
                # — exactly the same contract D4 applies to the WHOLE probe
                # being unreadable, applied per-label here.
                if was_failing:
                    new_failing.add(label)
                if label in prev_pending:
                    new_pending[label] = prev_pending[label]
                continue

            is_failing = verdict.kind in ("failing", "absent")
            if is_failing:
                new_failing.add(label)

            kind = _launchd_notification_kind(
                was_failing=was_failing,
                is_failing=is_failing,
                pending=prev_pending.get(label),
            )
            if kind is None:
                continue

            if kind == "failing":
                message = _format_launchd_alert(
                    hostname=host, now=now, label=label, verdict=verdict
                )
                log.warning("watchdog.launchd_agent_failing_alert", message=message)
            else:
                message = _format_launchd_recovery_alert(
                    hostname=host, now=now, label=label
                )
                log.info("watchdog.launchd_agent_recovery_alert", message=message)

            if webhook:
                posted = _safe_slack_post(slack_poster, webhook, message)
                log.info("watchdog.slack_post_attempted", posted=posted)
                if not posted:
                    new_pending[label] = kind
            elif prev_pending.get(label) is not None:
                # Same delivery-loss reasoning as the backup blocks above:
                # the webhook merely being absent/unreadable RIGHT NOW must
                # not be treated as a successful delivery of an
                # already-pending notification.
                log.info("watchdog.slack_skipped_pending_retry_deferred")
                new_pending[label] = kind
            else:
                log.info("watchdog.slack_skipped_log_only")

        state = replace(
            state,
            failing_launchd_labels=tuple(sorted(new_failing)),
            launchd_notification_pending=tuple(sorted(new_pending.items())),
        )

    was_probe_unreadable = state.launchd_probe_unreadable_ticks > 0
    is_probe_unreadable = not probe_readable
    probe_pending = state.launchd_probe_notification_pending
    probe_notification_kind = _launchd_probe_notification_kind(
        was_unreadable=was_probe_unreadable,
        is_unreadable=is_probe_unreadable,
        pending=probe_pending,
    )

    if probe_notification_kind is not None:
        if probe_notification_kind == "unreadable":
            probe_message = _format_launchd_probe_unreadable_alert(
                hostname=host, now=now
            )
            log.warning(
                "watchdog.launchd_probe_unreadable_alert", message=probe_message
            )
        else:
            probe_message = _format_launchd_probe_recovery_alert(hostname=host, now=now)
            log.info("watchdog.launchd_probe_recovery_alert", message=probe_message)

        if webhook:
            probe_posted = _safe_slack_post(slack_poster, webhook, probe_message)
            log.info("watchdog.slack_post_attempted", posted=probe_posted)
            state = replace(
                state,
                launchd_probe_notification_pending=(
                    None if probe_posted else probe_notification_kind
                ),
            )
        elif probe_pending is not None:
            log.info("watchdog.slack_skipped_pending_retry_deferred")
            state = replace(
                state, launchd_probe_notification_pending=probe_notification_kind
            )
        else:
            log.info("watchdog.slack_skipped_log_only")
            state = replace(state, launchd_probe_notification_pending=None)

    if is_probe_unreadable:
        state = replace(
            state,
            launchd_probe_unreadable_ticks=state.launchd_probe_unreadable_ticks + 1,
        )
    else:
        state = replace(state, launchd_probe_unreadable_ticks=0)

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

    # --- Forecast-production freshness (Plan 116, additive) -----------------
    # A third, independently-parameterized freshness check, probing
    # `PipelineCheckType.FORECAST_FRESHNESS` (`flows/run_forecast_cycle.py`
    # emits it — a cycle that stores zero forecasts is CRITICAL, at least
    # one is OK). Deliberately probes THIS record, not `/health` (Prefect
    # flow-run state) and not `ForecastCycleHealth` — a cycle can be
    # DEGRADED (snow loss, partial NWP, fallback drift) yet still have
    # shipped forecasts, and must NOT alarm here.
    #
    # Fixer-round blocker fix: unlike the two BAFU checks (which correctly
    # age by `checked_at` — when the collector last ran), this check ages
    # by the record's `cycle_time` — the forecast PRODUCTION time — via the
    # dedicated `ForecastFreshnessResult`/`probe_forecast_freshness`. Aging
    # by `checked_at` would let a delayed or long-running cycle "refresh"
    # the heartbeat with an old `cycle_time`, silently defeating the
    # staleness check (correctness requirement 2).
    forecast_freshness_url = (
        config.forecast_freshness_health_detail_url
        or _forecast_freshness_url_from_health(config.health_url)
    )
    forecast_freshness_result = forecast_freshness_probe(forecast_freshness_url)
    forecast_freshness_stale = (
        not forecast_freshness_result.found
        or forecast_freshness_result.cycle_time is None
        or (now - forecast_freshness_result.cycle_time)
        > FORECAST_FRESHNESS_STALE_THRESHOLD
    )
    forecast_freshness_critical = forecast_freshness_result.status == "critical"
    forecast_freshness_fail = forecast_freshness_stale or forecast_freshness_critical
    log.info(
        "watchdog.forecast_freshness_check_completed",
        url=forecast_freshness_url,
        found=forecast_freshness_result.found,
        checked_at=forecast_freshness_result.checked_at.isoformat()
        if forecast_freshness_result.checked_at
        else None,
        cycle_time=forecast_freshness_result.cycle_time.isoformat()
        if forecast_freshness_result.cycle_time
        else None,
        status=forecast_freshness_result.status,
        error=forecast_freshness_result.error,
        stale=forecast_freshness_stale,
        critical=forecast_freshness_critical,
        prev_failures=state.consecutive_forecast_freshness_failures,
    )

    forecast_freshness_alert_now = should_alert_health(
        state.consecutive_forecast_freshness_failures,
        current_ok=not forecast_freshness_fail,
        current_fail=forecast_freshness_fail,
    )

    if forecast_freshness_alert_now:
        if not forecast_freshness_fail:
            message = _format_forecast_freshness_recovery_alert(hostname=host, now=now)
            log.info("watchdog.forecast_freshness_recovery_alert", message=message)
        elif forecast_freshness_stale:
            message = _format_forecast_freshness_stale_alert(
                hostname=host, now=now, result=forecast_freshness_result
            )
            log.warning("watchdog.forecast_freshness_stale_alert", message=message)
        else:
            message = _format_forecast_freshness_critical_alert(
                hostname=host, now=now, result=forecast_freshness_result
            )
            log.warning("watchdog.forecast_freshness_critical_alert", message=message)
        if webhook:
            posted = _safe_slack_post(slack_poster, webhook, message)
            log.info("watchdog.slack_post_attempted", posted=posted)
        else:
            log.info("watchdog.slack_skipped_log_only")

    if forecast_freshness_fail:
        state = replace(
            state,
            consecutive_forecast_freshness_failures=(
                state.consecutive_forecast_freshness_failures + 1
            ),
        )
    else:
        state = replace(state, consecutive_forecast_freshness_failures=0)

    # --- Free disk space (Plan 199 T1, salvaged from Plan 158 D14) ----------
    # A condition independent of every check above: 5% of the volume's OWN
    # total capacity (Plan 199 D1), not an absolute byte count, so the rule
    # travels to any host. Same transition-latched shape as the backup
    # device check (Plan 194 D6) — alert on TRANSITION only, never every
    # tick, and never resend a stale pending kind once the condition has
    # moved on. Distinct persisted counter and pending kind: no collision
    # with `backup_device_notification_pending`, because it never shares
    # state with it.
    disk_result = disk_probe(config.disk_path)
    is_disk_low = not disk_result.ok
    log.info(
        "watchdog.disk_space_check_completed",
        path=str(config.disk_path),
        free_bytes=disk_result.free_bytes,
        total_bytes=disk_result.total_bytes,
        ok=disk_result.ok,
        error=disk_result.error,
    )

    was_disk_low = state.consecutive_disk_low_ticks > 0
    disk_pending = state.disk_notification_pending
    disk_notification_kind = _disk_notification_kind(
        was_low=was_disk_low,
        is_low=is_disk_low,
        pending=disk_pending,
    )

    if disk_notification_kind is not None:
        if disk_notification_kind == "low":
            disk_message = _format_disk_space_alert(
                hostname=host, path=config.disk_path, result=disk_result
            )
            log.warning("watchdog.disk_space_low_alert", message=disk_message)
        else:
            disk_message = _format_disk_space_recovery_alert(hostname=host, now=now)
            log.info("watchdog.disk_space_recovery_alert", message=disk_message)

        if webhook:
            disk_posted = _safe_slack_post(slack_poster, webhook, disk_message)
            log.info("watchdog.slack_post_attempted", posted=disk_posted)
            state = replace(
                state,
                disk_notification_pending=(
                    None if disk_posted else disk_notification_kind
                ),
            )
        elif disk_pending is not None:
            # Same delivery-loss reasoning as the backup-device block above:
            # a webhook merely absent/unreadable RIGHT NOW must not be
            # treated as delivery of an already-pending notification.
            log.info("watchdog.slack_skipped_pending_retry_deferred")
            state = replace(state, disk_notification_pending=disk_notification_kind)
        else:
            log.info("watchdog.slack_skipped_log_only")
            state = replace(state, disk_notification_pending=None)

    if is_disk_low:
        state = replace(
            state,
            consecutive_disk_low_ticks=state.consecutive_disk_low_ticks + 1,
        )
    else:
        state = replace(state, consecutive_disk_low_ticks=0)

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
        "--forecast-freshness-health-detail-url",
        default=None,
        help=(
            "Forecast-production freshness endpoint "
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
    parser.add_argument(
        "--disk-path",
        default=str(DEFAULT_DISK_PATH),
        help=(
            "Filesystem path to check free space on, alerting below "
            f"{DISK_FREE_THRESHOLD_PCT * 100:.0f}% of that volume's total "
            f"capacity (default: {DEFAULT_DISK_PATH})"
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
        forecast_freshness_health_detail_url=args.forecast_freshness_health_detail_url,
        probe_token_path=Path(args.probe_token_path),
        deadman_url_path=Path(args.deadman_url_path),
        disk_path=Path(args.disk_path),
    )

    probe_token = read_probe_token(config.probe_token_path)
    bafu_probe_bound = functools.partial(probe_bafu_freshness, token=probe_token)
    forecast_freshness_probe_bound = functools.partial(
        probe_forecast_freshness, token=probe_token
    )

    try:
        run_once(
            config=config,
            clock=_utc_now,
            probe=probe_health,
            slack_poster=default_slack_poster,
            bafu_probe=bafu_probe_bound,
            bafu_obs_probe=bafu_probe_bound,
            forecast_freshness_probe=forecast_freshness_probe_bound,
        )
    except Exception as exc:  # unrecoverable: let launchd see the non-zero
        log.error("watchdog.unrecoverable_error", error=str(exc))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
