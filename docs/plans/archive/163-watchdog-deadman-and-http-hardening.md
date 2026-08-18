---
status: COMPLETE
created: 2026-08-16
completed: 2026-08-18
plan: 163
title: Dead-man's switch — so a dead watchdog stops looking like a healthy system
scope: Add an off-box dead-man's-switch ping to the watchdog, and harden every outbound HTTP call so a malformed URL cannot kill the tick. Extracted onto current main rather than merged from the Plan 158 branch, because Phase A of Plan 162 rewrote `watchdog.py` and the 158 branch predates it by 22 commits. Deliberately EXCLUDES the 158 branch's forecast-freshness check (its emitter is not on main, so it would alert forever) and the free-disk check (self-contained but not urgent). The system-domain conversion that stops the watchdog dying with the login session stays in Plan 158.
depends_on: []
blocks: []
supersedes: []
---

# Plan 163 — Dead-man's switch + HTTP hardening

## Status
**COMPLETE — merged 2026-08-17 as PR #162 (`38319159`).** Built on current main rather than merged from the
Plan 158 branch. **Not yet deployed to the mac-mini.**

**READY** (2026-08-16) — D5 closed by the owner; independently reviewed (4 blockers + 8 majors folded, including
a heartbeat contract that was wrong in the first draft). Operational reliability (category **A**). Responds to a **live, ongoing** incident.

**Post-implementation fixer round (2026-08-16), independent Codex review over the committed diff — 3 majors + 1
minor resolved:**
1. **(major) Owned-client cleanup could still escape T1's boundary.** `probe_health`/`probe_bafu_freshness` guarded
   the request but not an owned client's `finally`-block `close()` — an `OSError` there would override a
   successful `return` (or an already-caught request exception) and kill the tick. Both `close()` calls are now
   wrapped in the same `_HTTP_CALL_EXCEPTIONS` + defensive `except Exception` boundary. Locked by
   `TestOwnedClientCleanupHardeningProbeHealth`/`...ProbeBafuFreshness` (fake owned client, `get()` succeeds,
   `close()` raises) — proven RED against the pre-fix code (stash-fix/run/restore).
2. **(major) Heartbeat-ordering tests didn't actually prove ping-after-persist.** The existing raising-poster/
   raising-probe tests would have passed even if the ping were moved to immediately *before* `state.dump(...)`.
   Added `test_ping_observes_state_already_persisted_on_disk` (the injected poster asserts the state file already
   holds this tick's persisted content when invoked) and
   `test_dump_failure_before_persistence_suppresses_heartbeat` (monkeypatches `WatchdogState.dump` to raise;
   asserts the exception propagates and zero pings occur).
3. **(major) Ordinary unit tests could ping the REAL production dead-man URL.** `_config()`'s `WatchdogConfig` left
   `deadman_url_path` at its default (`DEFAULT_DEADMAN_PATH`, a *relative* `./secrets/deadman_url`) — on a
   checkout where that host secret is present (e.g. the mac-mini), every `run_once` test not overriding
   `deadman_poster` would fire a real heartbeat via `default_deadman_poster`. `_config()` now points
   `deadman_url_path` at a `tmp_path` file that is never written; the two hand-reconstructed `WatchdogConfig(...)`
   test configs (which silently dropped this and every other field not listed) now use `dataclasses.replace(base,
   ...)` instead.
4. **(minor) No test locked the dead-man poster's timeout/empty-body.** Added
   `test_posts_empty_body_with_the_5s_timeout_constant`, asserting `timeout == DEADMAN_POST_TIMEOUT_S == 5.0` and
   the absence of `json`/`data`/`content`/`files` kwargs on the captured `httpx.post` call.

**Second post-implementation fixer round (2026-08-16), 3 minors closing the last gaps in the "never raises"
contract:**
1. **(minor) The existence preflight sat OUTSIDE the guard.** `read_deadman_url` called `Path.exists()` before the
   `(OSError, UnicodeError)` `try` — on Python 3.12 `exists()` can itself re-raise a non-ignored `OSError` (e.g. a
   permission error on a parent directory). Fixed by dropping the preflight entirely and calling `read_text()`
   directly inside the guard (a missing file now degrades via `FileNotFoundError`, an `OSError` subclass, like
   every other unreadable case). Locked by
   `TestReadDeadmanUrl::test_existence_preflight_permission_error_does_not_raise` — proven RED against the
   pre-fix code (stash-fix/run/restore).
2. **(minor) Response handling lived outside the single adapter boundary.** `default_slack_poster` and
   `default_deadman_poster` accessed `resp.status_code`/`resp.text` AFTER their `try` block, so an unexpected
   response-access exception could still escape despite the "never raises" promise (masked only because
   `run_once`'s `_safe_slack_post`/`_safe_deadman_post` wrappers contained the damage for that one caller). Moved
   all response handling inside each function's `try`. Locked by
   `TestResponseHandlingInsideGuardDefaultSlackPoster` (status_code-access-raises, text-access-raises) and
   `TestResponseHandlingInsideGuardDefaultDeadmanPoster` (status_code-access-raises) — proven RED against the
   pre-fix code.
3. **(minor) Exception safety was integration-tested at only one of four Slack call sites.** Only the backup
   branch (`TestSlackExceptionDuringBackupTransition`) had a raising-poster test proving `_safe_slack_post` usage;
   a regression reintroducing a raw `slack_poster(...)` call in the health, BAFU-forecast or BAFU-observation
   branch would have passed every other existing test. Added
   `TestRaisingSlackPosterAcrossAllFourAlertBranches`, a `pytest.mark.parametrize` over all four branches
   asserting state is persisted and exactly one heartbeat is attempted in each case — verified to fail when any
   one of the four `_safe_slack_post(...)` call sites is manually reverted to a raw `slack_poster(...)` call
   (checked all four independently, restored after each).

## Problem

**The mac-mini watchdog has been silent since ~03:54 on 2026-08-16.** It had been posting a stale-backup alert
every 5 minutes; two alerts five minutes apart, then nothing.

That silence can have only two causes, and one of them is impossible:

1. *The backup became fresh* — **ruled out.** Nothing was deployed; the backup flow still fails on
   `permission denied for table access_tokens` (Plan 162 Phase A is merged but **not deployed**), and the newest
   dump in the watched directory is still `2026-08-13T02:01`.
2. **The watchdog stopped running.**

So the watchdog is dead, and **we found out only because a human noticed alerts had stopped.** That is the exact
shape of the 29 July outage, which ran undetected for **14 days**: on this host, *silence and health are
indistinguishable*.

The most likely trigger is the pending **macOS 26.6.1** update (`Action: restart`, and ~03:54 is when macOS
installs deferred updates). The watchdog is a **LaunchAgent**, so it dies with the login session.

### Why this is the highest-value thing to fix

Backups being broken is a **bounded, known** problem with a fix already merged. Not knowing whether monitoring is
alive is **unbounded**: every other alarm in the system — BAFU staleness, pipeline health, backup freshness — is
delivered by this one process. When it dies, all of them go quiet *and look fine*.

A dead-man's switch inverts the signal: instead of trusting an alert to arrive, an external service alerts when
a **heartbeat stops**. It would have paged within ~20 minutes of 03:54 instead of waiting for someone to notice.

### A second, related defect

`default_slack_poster` and every HTTP probe catch only `httpx.HTTPError`. **`httpx.InvalidURL` is NOT a subclass
of it** — verified in this repo's environment: `issubclass(httpx.InvalidURL, httpx.HTTPError)` is `False`. So a
malformed hand-pasted URL raises out of the tick and **kills the watchdog at exactly the moment it is trying to
report an outage**. This is not hypothetical: the Slack webhook and the Healthchecks ping URL were both pasted by
hand, and this plan adds a second URL of the same kind.

## Goal

The watchdog emits a heartbeat to an off-box service on every tick; if the watchdog dies, is never started, or
the host is off, that service alerts through a channel **that does not depend on the mini**. No malformed URL can
terminate a tick.

## Network model — the staging host is INSIDE the company network and stays there

**Requirement:** the mini is a company-internal machine and must **not** become publicly reachable. This design
satisfies that, and the constraint is what makes a heartbeat the right shape rather than an incidental detail.

- **Outbound-only.** The watchdog runs *on* the mini and POSTs out to the provider. **Nothing connects in** — no
  port forwarding, no inbound firewall rule, no public DNS, no reverse tunnel. The host's exposure is unchanged.
- **The alternative would not work here.** External *polling* (a service that probes the mini) requires inbound
  reachability, which is unavailable and undesirable. Push/heartbeat is the standard answer for hosts behind
  NAT/firewalls, and it is why this is a dead-man's switch rather than an uptime check.
- **The watchdog's other checks are already local** — it probes `http://localhost:8000`. It needs **zero**
  inbound connectivity.
- **Verified, not assumed:** a POST from the mini to the staged ping URL returned **HTTP 200 in 0.073 s**
  (2026-08-14), proving egress to `hc-ping.com` is permitted from inside the company network today.
- **What leaves the network:** an **empty POST** — no payload, no database content, no hostnames. The provider
  learns only *"this check was pinged"*, the source IP, and a timestamp. Note the ping URL is a **bearer
  capability**: anyone holding it can ping and thereby *mask* an outage, so it lives in `secrets/deadman_url`
  (mode 600, git-ignored) and is treated like the Slack webhook.

**⚠️ Risk this creates, and it is a real one.** If egress is ever filtered — a new proxy, TLS interception, an
allow-list change — the pings stop, and **a blocked ping is indistinguishable from a dead watchdog**: both
produce silence and both fire the alarm. That is the *safe* direction (a false alarm, never a false all-clear),
but it means the host-side diagnosis must check egress first:

```sh
curl -s -o /dev/null -w '%{http_code}\n' -X POST --max-time 10 "$(cat secrets/deadman_url)"
```

`200` ⇒ the network is fine and the watchdog really is down. Anything else ⇒ egress, not the watchdog. If the
network ever requires a proxy, `httpx` needs `HTTPS_PROXY` in the watchdog's environment; no proxy is required
today.

**Considered and rejected as the primary: self-hosting the monitor inside the company network.** It removes the
third-party dependency and keeps everything internal — but an internal monitor **dies with the site**, so a power
cut, a network failure or an office-wide outage would take down the watchdog *and* its watcher together, which
is one of the failure modes we most want to catch. A hosted external endpoint is the only version that survives
losing the building. (Healthchecks is open-source, so an *additional* internal instance is possible later as a
second opinion — not as the only one.)

## Non-goals

- **The system-domain (LaunchDaemon) conversion** — stays Plan 158 T5. Note the dead-man's switch **works
  regardless**: a LaunchAgent dying with the session is precisely what it detects. That is why this ships first.
- **The forecast-freshness check** from the 158 branch — its `pipeline_health` emitter is not on main, so it
  would find nothing and alert every tick forever.
- **The free-disk check** from the 158 branch — self-contained and worth having, but not urgent (host at 23%).
  Named follow-on.
- **Alert routing/escalation policy** — the provider's own integrations handle it.

## Decisions

- **D1 — provider. ✅ DECIDED (owner, 2026-08-14): Healthchecks.io hosted.** Free tier, open-source (BSD), so it
  can be self-hosted later **without any code change** — the ping is a plain POST to a URL. Check created; ping
  URL already staged at `secrets/deadman_url` on the mini and **verified reachable from the host (HTTP 200)**.
- **D2 — semantics. A ping means "the tick completed", NOT "the stack is healthy."** An unhealthy stack still
  pings, because Slack is the channel for *detected* failure and the dead-man is the channel for a watchdog that
  dies before it can report anything. Keeping these orthogonal is the point; conflating them would mean a
  degraded stack silences the liveness signal.
- **D2b — freshness is judged by the PROVIDER's receipt time, not the mini's clock.** Host clock skew can
  distort local staleness checks but cannot falsely refresh the dead-man — a useful independence property.
- **D3 — feature-off by default.** Missing/empty/unreadable `secrets/deadman_url` ⇒ no ping, no error. The file
  is git-ignored and absent in CI and in every dev checkout.
- **D4 — the ping must never be able to break the watchdog.** A dead-man outage taking down the process it
  monitors would be perverse. All failures are caught, logged and reported as `False`.
- **✅ D5 — CLOSED (owner, 2026-08-16).** Check configured **Period = 5 min, Grace = 15 min** exactly as
  recommended (3× the 300 s `StartInterval`, so one slow tick does not page), with Slack and email integrations
  attached. **The owner observed it working on 2026-08-15.**
  **What this proves — and it is the half we could not otherwise test:** the test ping on 2026-08-14 (HTTP 200
  from the mini) followed by silence produced a real DOWN notification. So *ping → check → DOWN → delivery* is
  **verified end-to-end**, not assumed. An earlier note in this plan speculated the check might still be on
  Healthchecks' new-check defaults (1 day / 1 h) and that this explained a missing alert; **that was wrong** —
  the alert was simply due at 14:23 UTC, shortly after it was last looked for.
  **The one link still unproven is the watchdog emitting the ping at all** — which is exactly what T2 builds.
  *Residual, deploy-time only (not a blocker):* confirm the DOWN notification lands in **both** Slack **and**
  email, since two off-box channels is the property that survives a single-vendor outage.

## Tasks

### T1 — HTTP hardening (do this first; the ping depends on it)

*In:* `src/sapphire_flow/ops/watchdog.py`.

**The call sites are exactly three** (enumerated against current main, confirmed — there is no fourth):
health GET `:215`, the shared BAFU-detail GET `:266` (invoked twice, forecast `:674` and observations `:739`),
and the Slack POST `:377`.

**⛔ The boundary must cover more than the request.** Probe clients are **constructed before** the `try`
(`:213`, `:263`) and `close()` runs in `finally` (`:235`, `:309`) — so construction and cleanup exceptions
**still escape** a tuple that only guards the request. Wrap **client construction, the request, response
handling, and owned-client cleanup** in one exception boundary.

**⛔ The exception set was underspecified.** `httpx.HTTPError` + `httpx.InvalidURL` does **not** deliver
"never raises": transport/SSL/socket setup failures can surface as **`OSError`** (`ssl.SSLError` and socket
errors are `OSError` subclasses), and malformed Unicode/IDNA input can surface as **`UnicodeError`**. httpx
normally translates many of these, but the design cannot *rely* on that while claiming containment. Enumerate at
least `httpx.HTTPError`, `httpx.InvalidURL`, `OSError`, `UnicodeError`, and add a final defensive
`except Exception` at the adapter boundary — **never `BaseException`**, which would swallow `KeyboardInterrupt`.

*Not* in the tuple: `socket.gethostname()` (`:556`) can raise `OSError`, but it is not an outbound HTTP call. If
it aborts the tick, **the absent heartbeat is the correct signal** — do not defend it.

**Red-first:** a poster/probe given a malformed URL (e.g. `not a url`, `http://`) must return `False` and log,
**not** raise. Prove it fails against current `main`, where only `httpx.HTTPError` is caught.

### T2 — the dead-man ping

*In:* `src/sapphire_flow/ops/watchdog.py`, `scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist`.

- `DEFAULT_DEADMAN_PATH = Path("./secrets/deadman_url")`; `read_deadman_url(path)` returns a stripped URL or
  `None` when missing/empty/unreadable (mirroring `read_slack_webhook`/`read_probe_token`).
- `DeadmanPoster = Callable[[str], bool]`; `default_deadman_poster(url)` POSTs an empty body with a short
  timeout, catches `_HTTP_CALL_EXCEPTIONS`, treats `status >= 300` as failure, and **never raises**.
- **⛔ THE HEARTBEAT CONTRACT — the first draft of this line was wrong and dangerous.** It said "at the end of
  every tick, **unconditionally**", which invites a `finally`. **A `finally` heartbeat would be a bug**: it would
  mark a *crashed or incomplete* tick as healthy — a false all-clear, the exact class this plan exists to
  eliminate. The correct contract is:
  > **Exactly once after every tick that COMPLETED and PERSISTED successfully** — regardless of unhealthy check
  > results or failed notification delivery, but **NOT** if the tick raised.

  **Placement is therefore exact:** read the URL and call the guarded poster **immediately after
  `state.dump(...)` (`watchdog.py:795`) and before `return` (`:796`).** Current `run_once` has **no** early
  return before that point (confirmed), but many things can raise before it — hostname lookup (`:556`), state
  load (`:558`), probes (`:561`, `:674`, `:739`), Slack calls (`:587`, `:635`, `:719`, `:782`), and persistence
  itself (`:795`). **All of those must suppress the ping**, because a fatal bug, a persistence failure or a
  mid-tick kill *should* make the dead-man fire. A tick killed mid-flight correctly emits no heartbeat.
  Placing it after persistence also means a slow or hanging ping can never block or corrupt the state write.
- Injectable (`deadman_poster` parameter) for tests, like the existing posters.
- CLI `--deadman-url-path`, and the plist passes it.

**Red-first:** (a) with a URL file present, exactly one ping per tick; (b) with the file absent, **zero** pings
and no error; (c) a poster that raises must **not** propagate out of `run_once`; (d) a tick whose health checks
**fail** still pings — this is the case that makes the switch meaningful and the one most likely to be broken by
a well-meaning refactor.

### T3 — interaction with Plan 162's notification state machine (review major)

`watchdog.py:634` — an **unexpected** Slack-poster exception exits `run_once` **before**
`backup_notification_pending` is updated and before state is persisted, which can lose precisely the
pending/recovery-pending transition Plan 162 Phase A added to survive delivery failure. Route **all four** Slack
call sites through a safe helper that converts delivery exceptions to `False`, and persist
`backup_notification_pending` **before** the dead-man ping is attempted.

**Red-first:** a raising dead-man poster must still leave the expected state file on disk; a raising Slack
delivery during a backup transition must **preserve and persist** `backup_notification_pending` and still attempt
exactly one heartbeat.

### T4 — wiring, defaults and the plist (review majors)

- **`WatchdogConfig.deadman_url_path`** defaulting to `DEFAULT_DEADMAN_PATH`, and **`deadman_poster` optional
  with a default** on `run_once` — otherwise every existing `_config`/`run_once` call site in
  `tests/unit/ops/test_watchdog.py` needs a mechanical edit. Existing configurations (no URL file) must produce
  **zero** network calls.
- **Plist:** add exactly `<string>--deadman-url-path</string>` and `<string>./secrets/deadman_url</string>`
  (`ch.hydrosolutions.sapphire-watchdog.plist:16`), **plus** parser registration and `args.deadman_url_path`
  mapping in `main()` (`:803-865`). **`plutil -lint` proves XML validity, not that the parsed path reaches
  `run_once`** — add a CLI-wiring test.
- **`RunAtLoad` is currently `false`** (`:35`), so the first execution can be delayed a full interval. Set it
  `true` for immediate deployment validation, or document and accept the gap. (This does not address the
  LaunchAgent/login-session defect — that stays Plan 158 T5.)
- **Timeout is a number, not an adjective:** define a constant (**5 s**), test that it reaches httpx, and record
  the worst-case sequential tick budget against the plist's **300 s** `StartInterval` (`:27`).
- **`read_deadman_url` must contain decoding failures too** — `Path.read_text()` can raise `UnicodeError` on
  invalid bytes, not only `OSError`. Return `None` for missing, empty, unreadable **and undecodable**; test each.
- **Success is `200 <= status < 300`**, not merely "not >= 300".
- **Per-site malformed-URL tests**: separate red tests for `default_slack_poster`, `probe_health` **and**
  `probe_bafu_freshness`. One generic test does not prove all three sites; the harness at
  `tests/unit/ops/test_watchdog.py:1039` supports this directly.

## Exit gates

Full `uv run pytest` (**use a per-checkout `PREFECT_HOME`** — a shared `~/.prefect` produces phantom failures,
see the note below), `ruff format --check` + `ruff check`, pyright ratchet, `plutil -lint` on the plist.

## Host acceptance (requires mini access)

1. **First: find out why the watchdog is dead** — `uptime`, `sw_vers` (did 26.6.1 install?),
   `launchctl list | grep watchdog`, and the last timestamp in `~/Library/Logs/sapphire-watchdog.log`. Compare
   that timestamp with boot time: if it stops at the reboot, it is the LaunchAgent-dies-with-session problem and
   Plan 158 T5 is the durable fix.
2. Deploy, then confirm `watchdog.deadman_ping_attempted pinged=True` in the log and a green check in the
   dashboard.
3. **Prove the remaining link.** The alerting half (*ping → DOWN → delivery*) is **already verified** (D5). What
   deployment must still prove is that **the watchdog itself pings on every completed tick**: confirm
   `watchdog.deadman_ping_attempted pinged=True` in the log and the check going green, then stop the watchdog
   deliberately and confirm DOWN arrives within grace in **both** Slack and email, then restart and confirm
   recovery.

## Notes

- Extracted onto current `main` rather than merged from `docs/plan-158-session-independence`: Plan 162 Phase A
  rewrote `watchdog.py` (736 → 886 lines) and the 158 branch (1175 lines) predates it by 22 commits, so merging
  it would clobber the new notification state machine. The 158 implementation is the **design reference** — it
  was reviewed, and its shape (file-sourced URL, feature-off, never-raises) carries over unchanged.
- **Test-run caveat:** run the suite with an isolated `PREFECT_HOME`. Parallel sessions share `~/.prefect`, and
  the resulting SQLite contention makes ephemeral-server startup time out, producing failures in unrelated
  modules. See [[project_prefect_home_test_contention]].
