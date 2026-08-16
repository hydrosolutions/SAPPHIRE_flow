---
status: DRAFT
created: 2026-08-16
plan: 163
title: Dead-man's switch — so a dead watchdog stops looking like a healthy system
scope: Add an off-box dead-man's-switch ping to the watchdog, and harden every outbound HTTP call so a malformed URL cannot kill the tick. Extracted onto current main rather than merged from the Plan 158 branch, because Phase A of Plan 162 rewrote `watchdog.py` and the 158 branch predates it by 22 commits. Deliberately EXCLUDES the 158 branch's forecast-freshness check (its emitter is not on main, so it would alert forever) and the free-disk check (self-contained but not urgent). The system-domain conversion that stops the watchdog dying with the login session stays in Plan 158.
depends_on: []
blocks: []
supersedes: []
---

# Plan 163 — Dead-man's switch + HTTP hardening

## Status
**DRAFT** (2026-08-16). Operational reliability (category **A**). Responds to a **live, ongoing** incident.

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
- **D3 — feature-off by default.** Missing/empty/unreadable `secrets/deadman_url` ⇒ no ping, no error. The file
  is git-ignored and absent in CI and in every dev checkout.
- **D4 — the ping must never be able to break the watchdog.** A dead-man outage taking down the process it
  monitors would be perverse. All failures are caught, logged and reported as `False`.
- **⚠️ D5 — OPEN (owner): grace period.** Recommend **15 min** (3× the 300 s `StartInterval`) so one slow tick
  does not page. Also confirm the check's **Period** is 5 min — a new Healthchecks check defaults to **1 day /
  1 h grace**, which would have delayed the test alert by a day and may be why the test DOWN alert never arrived.

## Tasks

### T1 — HTTP hardening (do this first; the ping depends on it)

*In:* `src/sapphire_flow/ops/watchdog.py`. Introduce a single `_HTTP_CALL_EXCEPTIONS` tuple covering
`httpx.HTTPError` **and** `httpx.InvalidURL` (and any other non-`HTTPError` httpx raise), and use it at **every**
outbound call site — `default_slack_poster` and every probe — not just the new one.

**Red-first:** a poster/probe given a malformed URL (e.g. `not a url`, `http://`) must return `False` and log,
**not** raise. Prove it fails against current `main`, where only `httpx.HTTPError` is caught.

### T2 — the dead-man ping

*In:* `src/sapphire_flow/ops/watchdog.py`, `scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist`.

- `DEFAULT_DEADMAN_PATH = Path("./secrets/deadman_url")`; `read_deadman_url(path)` returns a stripped URL or
  `None` when missing/empty/unreadable (mirroring `read_slack_webhook`/`read_probe_token`).
- `DeadmanPoster = Callable[[str], bool]`; `default_deadman_poster(url)` POSTs an empty body with a short
  timeout, catches `_HTTP_CALL_EXCEPTIONS`, treats `status >= 300` as failure, and **never raises**.
- Called **at the end of every tick, unconditionally** (per D2) — including ticks where checks failed or Slack
  delivery failed. It must not sit behind any early return.
- Injectable (`deadman_poster` parameter) for tests, like the existing posters.
- CLI `--deadman-url-path`, and the plist passes it.

**Red-first:** (a) with a URL file present, exactly one ping per tick; (b) with the file absent, **zero** pings
and no error; (c) a poster that raises must **not** propagate out of `run_once`; (d) a tick whose health checks
**fail** still pings — this is the case that makes the switch meaningful and the one most likely to be broken by
a well-meaning refactor.

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
3. **Prove the alarm, do not assume it:** stop the watchdog deliberately, confirm the DOWN alert arrives in
   **both** Slack and email within grace, then restart and confirm recovery. The test ping on 2026-08-14 returned
   HTTP 200 but **no DOWN alert was ever observed** — so the alerting half of this chain is still unproven.

## Notes

- Extracted onto current `main` rather than merged from `docs/plan-158-session-independence`: Plan 162 Phase A
  rewrote `watchdog.py` (736 → 886 lines) and the 158 branch (1175 lines) predates it by 22 commits, so merging
  it would clobber the new notification state machine. The 158 implementation is the **design reference** — it
  was reviewed, and its shape (file-sourced URL, feature-off, never-raises) carries over unchanged.
- **Test-run caveat:** run the suite with an isolated `PREFECT_HOME`. Parallel sessions share `~/.prefect`, and
  the resulting SQLite contention makes ephemeral-server startup time out, producing failures in unrelated
  modules. See [[project_prefect_home_test_contention]].
