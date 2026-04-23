"""Host-level watchdog for the SAPPHIRE Flow Mac-mini staging stack.

Probes the API health endpoint and checks backup staleness on every
invocation (scheduled by launchd every 5 min — see
`scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist`).

Hysteresis: alerts on the first failure, then only on every 6th
consecutive failure (~30 min cadence at 5 min intervals), and once
more when the service recovers. State is kept in
``~/.sapphire-watchdog-state.json``.

Slack: reads ``./secrets/slack_webhook_url`` (host-process secret —
NOT a Docker secret; see docs/standards/security.md §Secrets
management). If the file is absent or empty the watchdog runs
log-only — structured events are still emitted.

Spec: docs/plans/046-mac-mini-staging-deployment.md §C3.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import structlog

from sapphire_flow.logging import configure_cli_logging

log = structlog.get_logger(__name__)

DEFAULT_HEALTH_URL = "http://localhost:8000/api/v1/health"
DEFAULT_BACKUP_DIR = Path("/Volumes/sapphire-backup/pg_dumps")
DEFAULT_STATE_PATH = Path.home() / ".sapphire-watchdog-state.json"
DEFAULT_SLACK_PATH = Path("./secrets/slack_webhook_url")

BACKUP_STALE_THRESHOLD = timedelta(hours=26)
HEALTH_CHECK_TIMEOUT_S = 5.0
SLACK_POST_TIMEOUT_S = 5.0
ALERT_REPEAT_EVERY = 6  # every 6th consecutive failure (~30 min at 5 min tick)


@dataclass(frozen=True, kw_only=True, slots=True)
class WatchdogState:
    """Hysteresis state persisted between invocations."""

    consecutive_health_failures: int = 0
    last_backup_alert_iso: str | None = None

    @classmethod
    def load(cls, path: Path) -> WatchdogState:
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("watchdog.state_read_failed", path=str(path), error=str(exc))
            return cls()
        return cls(
            consecutive_health_failures=int(raw.get("consecutive_health_failures", 0)),
            last_backup_alert_iso=raw.get("last_backup_alert_iso"),
        )

    def dump(self, path: Path) -> None:
        payload = {
            "consecutive_health_failures": self.consecutive_health_failures,
            "last_backup_alert_iso": self.last_backup_alert_iso,
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
    """Synchronous HTTP probe. Returns ok=True only on 2xx + status=='ok'."""
    owns_client = client is None
    c = client or httpx.Client(timeout=HEALTH_CHECK_TIMEOUT_S)
    try:
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
    except httpx.HTTPError as exc:
        return HealthProbeResult(ok=False, http_status=None, error=str(exc))
    finally:
        if owns_client:
            c.close()


def newest_backup_mtime(backup_dir: Path) -> datetime | None:
    """Return the newest *.dump mtime as a UTC datetime, or None if none exist."""
    if not backup_dir.exists() or not backup_dir.is_dir():
        return None
    newest: float | None = None
    for entry in backup_dir.glob("*.dump"):
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if newest is None or mtime > newest:
            newest = mtime
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


def default_slack_poster(url: str, message: str) -> bool:
    payload = {"text": message}
    try:
        resp = httpx.post(url, json=payload, timeout=SLACK_POST_TIMEOUT_S)
    except httpx.HTTPError as exc:
        log.warning("watchdog.slack_post_failed", error=str(exc))
        return False
    if resp.status_code >= 300:
        log.warning(
            "watchdog.slack_post_failed",
            http_status=resp.status_code,
            body=resp.text[:200],
        )
        return False
    return True


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


@dataclass(frozen=True, kw_only=True, slots=True)
class WatchdogConfig:
    health_url: str = DEFAULT_HEALTH_URL
    backup_dir: Path = DEFAULT_BACKUP_DIR
    state_path: Path = DEFAULT_STATE_PATH
    slack_path: Path = DEFAULT_SLACK_PATH


def run_once(
    *,
    config: WatchdogConfig,
    clock: Callable[[], datetime],
    probe: Callable[[str], HealthProbeResult],
    slack_poster: SlackPoster,
    hostname: str | None = None,
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
            posted = slack_poster(webhook, message)
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

    # --- Backup staleness ---
    newest = newest_backup_mtime(config.backup_dir)
    is_stale = newest is None or (now - newest) > BACKUP_STALE_THRESHOLD
    log.info(
        "watchdog.backup_check_completed",
        backup_dir=str(config.backup_dir),
        newest=newest.isoformat() if newest else None,
        stale=is_stale,
    )

    if is_stale:
        message = _format_backup_alert(newest=newest, threshold=BACKUP_STALE_THRESHOLD)
        # For simplicity, alert every tick on backup staleness; in
        # practice the operator is paged on the first one and silences
        # subsequent ones manually. Dedupe-by-day would add complexity
        # without operational value — revisit if alert fatigue is seen.
        log.warning("watchdog.backup_stale_alert", message=message)
        if webhook:
            posted = slack_poster(webhook, message)
            log.info("watchdog.slack_post_attempted", posted=posted)
        else:
            log.info("watchdog.slack_skipped_log_only")
        state = replace(state, last_backup_alert_iso=now.isoformat())

    state.dump(config.state_path)
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
    args = parser.parse_args(argv)

    configure_cli_logging("INFO")

    config = WatchdogConfig(
        health_url=args.health_url,
        backup_dir=Path(args.backup_dir),
        state_path=Path(args.state_path),
        slack_path=Path(args.slack_path),
    )

    try:
        run_once(
            config=config,
            clock=_utc_now,
            probe=probe_health,
            slack_poster=default_slack_poster,
        )
    except Exception as exc:  # unrecoverable: let launchd see the non-zero
        log.error("watchdog.unrecoverable_error", error=str(exc))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
