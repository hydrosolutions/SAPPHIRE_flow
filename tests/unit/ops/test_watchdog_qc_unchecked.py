"""Plan 318 T2 — the zero-rule QC probe is a PRESENCE probe.

⛔ Its polarity is inverted from every other probe in the watchdog. The others
are freshness checks: a MISSING heartbeat is the alarm. Plan 318 T1 writes a
record only on a run that had a zero-rule group, so here a record FOUND is the
alarm and no record is the healthy steady state.

`test_no_record_is_healthy_and_silent` is the load-bearing one: an implementer
who copied the freshness shape would alert on every healthy tick, and that test
is what catches it.
"""

from __future__ import annotations

import functools
import sys
from datetime import timedelta
from pathlib import Path  # noqa: TC003 — runtime use in fixtures

import httpx
import pytest  # noqa: TC002 — MonkeyPatch used at runtime

from sapphire_flow.ops import watchdog
from sapphire_flow.ops.watchdog import (
    QC_UNCHECKED_LOOKBACK,
    QcUncheckedResult,
    WatchdogState,
    probe_qc_unchecked,
    run_once,
)
from tests.unit.ops.test_watchdog import (
    _NOW,
    _bafu_obs_ok_probe,
    _bafu_ok_probe,
    _clock,
    _config,
    _forecast_freshness_ok_probe,
    _launchd_ok_probe,
    _make_fresh_backup,
    _ok_probe,
    _SlackRecorder,
)

# ⚠️ Reuse the module's own `_config`, which isolates every test from the REAL
# production dead-man URL. A local copy would resolve it on a host where that
# secret exists — the mac-mini — and fire a real heartbeat.


def _absent(_url: str) -> QcUncheckedResult:
    """The healthy steady state: T1 wrote nothing, so nothing is found."""
    return QcUncheckedResult(
        found=False,
        checked_at=None,
        groups_affected=None,
        observations_unchecked=None,
    )


def _present(minutes_ago: int = 5) -> object:
    def probe(_url: str) -> QcUncheckedResult:
        return QcUncheckedResult(
            found=True,
            checked_at=_NOW - timedelta(minutes=minutes_ago),
            groups_affected=3,
            observations_unchecked=17,
            stations=("2135", "2289", "2457"),
        )

    return probe


def _run(tmp_path: Path, probe, slack: _SlackRecorder):
    cfg = _config(tmp_path, backup_dir=_make_fresh_backup(tmp_path, hours_ago=2))
    cfg.slack_path.write_text("https://hooks.slack.com/FAKE")
    return run_once(
        config=cfg,
        clock=_clock,
        probe=_ok_probe,
        slack_poster=slack,
        bafu_probe=_bafu_ok_probe,
        bafu_obs_probe=_bafu_obs_ok_probe,
        forecast_freshness_probe=_forecast_freshness_ok_probe,
        qc_unchecked_probe=probe,
        backup_device_verifier=lambda _: True,
        launchd_probe=_launchd_ok_probe,
    )


class TestPresencePolarity:
    def test_no_record_is_healthy_and_silent(self, tmp_path: Path) -> None:
        """⭐ THE test. An implementer copying the freshness pattern would
        treat "not found" as the alarm and post on every healthy tick."""
        slack = _SlackRecorder()
        state = _run(tmp_path, _absent, slack)

        assert state.consecutive_qc_unchecked_failures == 0
        assert slack.calls == []

    def test_a_recent_record_alerts(self, tmp_path: Path) -> None:
        slack = _SlackRecorder()
        state = _run(tmp_path, _present(minutes_ago=5), slack)

        assert state.consecutive_qc_unchecked_failures == 1
        assert len(slack.calls) == 1
        message = slack.calls[0][1]
        assert "UNCHECKED" in message
        assert "station_parameter_groups: 3" in message
        assert "observations: 17" in message
        # T2 Verification: it must report the affected STATIONS, not just a
        # count — a count says something is wrong, the ids say where.
        assert "2135" in message
        assert "2289" in message
        assert "2457" in message

    def test_a_record_older_than_the_lookback_is_silent(self, tmp_path: Path) -> None:
        """Records are written only on affected runs, so without a lookback a
        single bad run would alert for ever."""
        stale_minutes = int(QC_UNCHECKED_LOOKBACK.total_seconds() // 60) + 60
        slack = _SlackRecorder()
        state = _run(tmp_path, _present(minutes_ago=stale_minutes), slack)

        assert state.consecutive_qc_unchecked_failures == 0
        assert slack.calls == []


class TestFailSafeDirection:
    def test_a_broken_probe_is_silent_not_alarming(self, tmp_path: Path) -> None:
        """Every failure path returns found=False, i.e. healthy. A presence
        probe that cannot reach the API must not cry wolf. ⚠️ The cost is
        stated in `probe_qc_unchecked`: it reports nothing rather than
        reporting itself."""

        def broken(_url: str) -> QcUncheckedResult:
            return QcUncheckedResult(
                found=False,
                checked_at=None,
                groups_affected=None,
                observations_unchecked=None,
                error="http_status:503",
            )

        slack = _SlackRecorder()
        state = _run(tmp_path, broken, slack)

        assert state.consecutive_qc_unchecked_failures == 0
        assert slack.calls == []


class TestProbeParsesTheRecord:
    """⛔ The other tests inject a `QcUncheckedResult` and so never exercise
    `probe_qc_unchecked` itself. T2's pre-change test is specified record →
    watchdog; these close that gap by driving the real probe over a mocked
    transport, which is where the record's shape is actually decoded.
    """

    @staticmethod
    def _client(payload: dict) -> httpx.Client:
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_it_decodes_stations_groups_and_counts_from_a_real_record(self) -> None:
        payload = {
            "items": [
                {
                    "checked_at": "2026-02-01T11:55:00+00:00",
                    "status": "warning",
                    "detail": {
                        "zero_rule_groups": [
                            {"station_id": "2289", "parameter": "discharge"},
                            {"station_id": "2135", "parameter": "discharge"},
                        ],
                        "groups_affected": 2,
                        "observations_unchecked": 9,
                    },
                }
            ]
        }
        result = probe_qc_unchecked(
            "http://x/health/detail", client=self._client(payload)
        )

        assert result.found is True
        assert result.groups_affected == 2
        assert result.observations_unchecked == 9
        assert result.stations == ("2135", "2289")  # sorted, de-duplicated

    def test_an_empty_items_list_is_healthy_not_an_error(self) -> None:
        """⭐ The ordinary steady state. A freshness probe calls this
        `no_records` and treats it as the alarm; here it is the healthy case
        and must NOT set `error`."""
        result = probe_qc_unchecked(
            "http://x/health/detail", client=self._client({"items": []})
        )

        assert result.found is False
        assert result.error is None

    def test_a_401_reports_not_found_and_says_why(self) -> None:
        """The admin-only endpoint. `main()` binds the probe token for exactly
        this reason — unbound, the check would silently never fire."""

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={})

        result = probe_qc_unchecked(
            "http://x/health/detail",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        assert result.found is False
        assert result.error == "http_status:401"


class TestWiringThatNothingElseGuards:
    """⛔ Both defects below were found by an independent REVIEWER, not by the
    suite — mutating each one produced ZERO failures. They are the kind that
    make the check silently never fire, so a green suite is exactly what you
    would see. These tests exist so the next removal is caught."""

    def test_the_counter_round_trips_through_the_state_file(
        self, tmp_path: Path
    ) -> None:
        """Without this in `dump()`/`load()` the counter resets every
        invocation: a persistent condition re-alerts on EVERY tick and
        recovery is never reported."""
        path = tmp_path / "state.json"
        WatchdogState(consecutive_qc_unchecked_failures=4).dump(path)

        assert WatchdogState.load(path).consecutive_qc_unchecked_failures == 4

    def test_main_binds_the_admin_token_to_the_probe(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`/health/detail` is admin-only. Unbound, the probe gets 401 →
        found=False → "healthy" → the check SILENTLY NEVER FIRES, and the
        fail-safe direction that protects against a flapping probe is exactly
        what hides it.

        ⛔ An earlier version of this test asserted on the module's SOURCE
        TEXT. A review rated that HIGH: it passes whether or not `main()`
        actually uses the bound probe. This one captures what `main()` really
        hands to `run_once` and calls it with a stub server, so it constrains
        BEHAVIOUR.
        """
        token_path = tmp_path / "probe_token"
        token_path.write_text("secret-admin-token")
        captured: dict[str, object] = {}

        def _capture(**kwargs: object) -> object:
            captured.update(kwargs)
            return WatchdogState()

        monkeypatch.setattr(watchdog, "run_once", _capture)
        monkeypatch.setattr(
            watchdog, "read_probe_token", lambda _p: "secret-admin-token"
        )
        monkeypatch.setattr(
            sys,
            "argv",
            ["watchdog", "--state-path", str(tmp_path / "state.json")],
        )
        watchdog.main()

        bound = captured.get("qc_unchecked_probe")
        assert bound is not None, "main() must pass a qc_unchecked_probe"

        seen: dict[str, str | None] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["auth"] = req.headers.get("Authorization")
            return httpx.Response(200, json={"items": []})

        # Drive the probe main() actually bound, against a stub that records
        # the header. An unbound probe sends none and this assertion fails.
        bound(  # type: ignore[operator]
            "http://x/health/detail",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        assert seen["auth"] == "Bearer secret-admin-token"


class TestRecordToWatchdogEndToEnd:
    """T2's Verification is specified record → watchdog. Every other test here
    either stops at the probe or injects a result, so the CHAIN was untested —
    a review found exactly that gap. This drives `run_once` through the REAL
    `probe_qc_unchecked` against a stub serving a realistic T1 record."""

    def test_a_real_record_reaches_the_slack_message(self, tmp_path: Path) -> None:
        payload = {
            "items": [
                {
                    "checked_at": (_NOW - timedelta(minutes=5)).isoformat(),
                    "status": "warning",
                    "detail": {
                        "zero_rule_groups": [
                            {"station_id": "2135", "parameter": "discharge"},
                            {"station_id": "2289", "parameter": "water_level"},
                        ],
                        "groups_affected": 2,
                        "observations_unchecked": 11,
                    },
                }
            ]
        }

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        real_probe = functools.partial(probe_qc_unchecked, client=client)

        slack = _SlackRecorder()
        state = _run(tmp_path, real_probe, slack)

        assert state.consecutive_qc_unchecked_failures == 1
        assert len(slack.calls) == 1
        message = slack.calls[0][1]
        assert "2135" in message
        assert "2289" in message
        assert "station_parameter_groups: 2" in message
        assert "observations: 11" in message

    def test_a_malformed_detail_does_not_hide_the_record(self, tmp_path: Path) -> None:
        """⛔ `detail` is stored JSONB and can be any JSON value. A truthy
        non-dict used to raise inside the parser, and the outer handler turned
        that into found=False — SILENTLY HIDING a record that exists. The worst
        direction for a presence probe."""

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "checked_at": (_NOW - timedelta(minutes=5)).isoformat(),
                            "status": "warning",
                            "detail": "not-a-dict",
                        }
                    ]
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        real_probe = functools.partial(probe_qc_unchecked, client=client)

        slack = _SlackRecorder()
        state = _run(tmp_path, real_probe, slack)

        # The record EXISTS, so the condition is real and must still alert —
        # with "unknown" where the detail could not be read.
        assert state.consecutive_qc_unchecked_failures == 1
        assert len(slack.calls) == 1
