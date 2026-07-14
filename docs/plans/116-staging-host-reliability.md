---
status: DRAFT
created: 2026-07-14
plan: 116
title: Staging host reliability — power, network, and off-host liveness detection
scope: The mac-mini staging host silently disappears. Fix the host, and fix the fact that nobody notices.
depends_on: []
blocks: []
---

# Plan 116 — Staging host reliability

## Status

**DRAFT.** Do not implement or dispatch subagents until promoted to READY.

## Provenance

**2026-07-14.** While trying to run the live DB audit that gates Plan 115a, the mac-mini staging
host (`sapphire@192.168.1.136`) was found **completely off the network**: no ICMP, no SSH, no ARP
entry, across a full `192.168.1.0/24` sweep. A status light was lit — Apple-silicon minis keep the
LED on while asleep, so the light proves nothing.

**Nobody noticed.** Not a monitor, not an alert, not a dashboard. It was discovered by accident,
because a human happened to need the box for something else. That is the actual defect; the sleep
is merely the trigger.

This is the same host and the same failure family as **Plan 100** (a launchd restart silently
dropped the `-nwp` overlay → NWP off → the forecast feed went dark for three days while every flow
reported green). Twice now, this host has failed in a way that produced **silence rather than a
signal**.

## Root cause — two defects

> ### ⚠️ RETRACTED: "the host was awake but off the network"
>
> An earlier revision of this plan claimed a **Defect 0** — that the mini stayed off the LAN even
> after being woken and logged in, i.e. a network-link failure independent of sleep. **That was
> FALSE, and it is retracted in full.**
>
> The investigating machine (the dev laptop) was itself on a **client-isolated Wi-Fi network** (a
> guest SSID). It could reach the gateway and **nothing else** — not the printer, not any other Mac,
> not the mini. Every "the host is unreachable" probe was an artefact of that vantage point. The mini
> was on the LAN the whole time, reaching its own peers fine.
>
> **What misled the diagnosis, recorded so it is not repeated:**
> - ARP entries populate from *passive broadcast learning*, which works even under client isolation —
>   so a populated ARP table looked like proof of reachability when it proved nothing.
> - An ICMP sweep is a poor instrument (macOS stealth mode ignores ping), but the deeper error was
>   **never running the control**: *"can I reach ANY peer at all?"* One `ping` at the printer would
>   have exposed the isolation immediately and saved the whole detour.
>
> **The lesson, which is the same lesson as the rest of this plan:** before concluding that a remote
> system is broken, prove your own observation path works. A blind observer and a dead host produce
> identical evidence. *(This is exactly why Phase 3's heartbeat must be evaluated off-host — but it
> is not evidence of a link defect, and this plan will not claim one.)*

### 1. The host has no power or network management. At all.

`scripts/bootstrap-mac-mini.sh` provisions Docker, secrets, the backup disk, the CAMELS dataset,
the compose stack, and the LaunchAgents. It contains **zero** host configuration: no `pmset`, no
sleep prevention, no wake-on-LAN, no auto-restart-after-power-failure, no static addressing. Nothing
in `docs/deployment/mac-mini-staging.md` covers it either.

So a consumer Mac with default Energy Saver settings is being used as an unattended 24/7 server. It
sleeps. When it sleeps it leaves the network, the Docker stack stops, and every scheduled Prefect
flow stops with it. DHCP then hands it a different address when it returns, which is why the SSH
address in the runbook keeps going stale.

### 2. The watchdog cannot watch the host it runs on.

Both launchd units are **LaunchAgents running on the mini as user `sapphire`**:

- `scripts/launchd/ch.hydrosolutions.sapphire.plist` — the compose stack.
- `scripts/launchd/ch.hydrosolutions.sapphire-watchdog.plist` — `StartInterval` 300, runs
  `sapphire_flow.ops.watchdog`.

When the host sleeps, **the watchdog sleeps too**. The component whose job is to detect failure is
subject to the exact failure it is meant to detect. There is no observer outside the box, so a dead
host emits no signal — and an absent signal is indistinguishable from a healthy quiet period.

Being LaunchAgents (not LaunchDaemons) compounds it: they are bound to a GUI login session and
depend on auto-login surviving every reboot.

> **The design rule this violates:** a monitor must not share a failure domain with the thing it
> monitors. Flow 4 pipeline monitoring can tell you a *data source* went dark. It cannot tell you
> the *host* went dark, because it is not running when that happens.

## Objective

Make the staging host stay up — and, more importantly, make its absence **loud**. If the box
disappears again, we should learn it from an alert within minutes, not from a human tripping over it
days later.

## Scope

### Phase 1 — Host hardening (idempotent, folded into the bootstrap script)

Add a `harden_host()` step to `scripts/bootstrap-mac-mini.sh`, in keeping with the script's existing
idempotent style, and document it in `docs/deployment/mac-mini-staging.md`:

```bash
sudo pmset -a sleep 0            # never sleep the machine
sudo pmset -a disksleep 0        # never spin the disks down
sudo pmset -a displaysleep 10    # screen off is fine; it saves power and changes nothing else
sudo pmset -a womp 1             # wake on network access
sudo pmset -a autorestart 1      # come back automatically after a power cut
sudo pmset -a standby 0          # no deep standby — it tears down the network stack
sudo pmset -a hibernatemode 0
sudo systemsetup -setcomputersleep Never
```

Verify with `pmset -g` and assert the settings, rather than assuming the commands took.

**Also required, and not scriptable from here — call them out in the runbook as manual prerequisites:**

- **DHCP reservation** on the router for the mini's MAC, or a static IP. The address churn is a
  live operational cost: the documented SSH address has gone stale at least twice, and an
  address-based runbook that lies is worse than none.
- **Wired Ethernet, not Wi-Fi.** Wi-Fi drops on sleep and rejoins slowly and unreliably.
- **Auto-login** must survive reboots (already assumed by the LaunchAgents; make it an asserted
  prerequisite rather than a hope).

**Networking — what is actually true (2026-07-14, verified):**

- The mini runs on **Wi-Fi** (`en1`); Ethernet (`en0`) is **inactive**. It reaches its LAN peers
  normally.
- Its Wi-Fi MAC is a **randomized Private Wi-Fi Address** (`9a:a3:…`, locally-administered bit set).
  **This is a real problem for a server, independent of everything else**: a rotating MAC means a
  DHCP reservation cannot hold, so the address moves, and the documented SSH address goes stale — as
  it repeatedly has.

**Therefore, in scope:**

- **Disable Private Wi-Fi Address** for this network on the mini → a stable MAC.
- **DHCP reservation** on the router pinning the address → the runbook stops lying.
- **Prefer wired Ethernet** if a cable can reach the machine — better for an unattended host (stable
  MAC by construction, no Wi-Fi drop-on-sleep, and wake-on-LAN actually works). **Not** required to
  fix a defect; the network itself is healthy. If no cable can reach it, Wi-Fi with a fixed MAC and a
  reservation is acceptable.
- **Auto-login** must survive reboots (the LaunchAgents assume it); assert it rather than hope.

### Phase 2 — LaunchAgent vs LaunchDaemon

Decide, explicitly, whether the stack and watchdog should be **LaunchDaemons** (system-level, no
login session required, start at boot) instead of LaunchAgents.

- **For:** a daemon survives logout and does not depend on auto-login; it is the correct unit type
  for an unattended service.
- **Against:** Docker Desktop on macOS is a GUI application and effectively needs a user session, so
  a pure daemon may not be able to drive it.

This is a genuine fork and needs verification against the actual Docker Desktop setup on the box
before it is decided. **Do not guess.** If Docker Desktop truly requires the GUI session, then
LaunchAgent + auto-login is correct — but say so, and make auto-login an asserted, tested
prerequisite rather than an implicit one.

*(Consider whether Colima or the Docker CLI with a daemon-mode VM would remove the GUI dependency
entirely. Out of scope to implement; in scope to note as the strategic answer if the GUI dependency
turns out to be the blocker.)*

### Phase 3 — Off-host liveness detection (the part that actually matters)

**The host must prove it is alive to something that is not the host.** Two shapes; pick one:

**(a) Dead-man's switch / heartbeat push (recommended).** The mini pushes a heartbeat on an interval
to an external endpoint. If the heartbeat stops arriving, *the external service alerts* — no
cooperation from the dead host required. This is the only pattern that reports a host that is
asleep, powered off, unplugged, or off-network, because the alert is triggered by **absence**, not
by an error the dead machine would have to send.

The existing webhook alert channel is the natural delivery path (per the project's webhook-only
alerting decision), but note the **failure domain**: the thing evaluating the heartbeat must not run
on the mini.

**(b) External poller.** Something off-box periodically probes the mini's health endpoint and alerts
on failure. Simpler conceptually, but requires an always-on machine to do the polling, which we may
not have.

**Open question for the owner:** what off-host infrastructure do we actually have available for this
— a cloud host, a free-tier dead-man's-switch service, an existing monitoring endpoint? This
decision gates Phase 3's implementation and is genuinely unanswerable from the repo.

**What the heartbeat must assert** (not merely "the process is running"):

- the host is up **and** on the network;
- Docker is up and the compose stack's containers are running;
- the API health endpoint returns `status=ok`;
- the Prefect worker is polling — the Plan 098 lesson (poll-starvation) means "the process exists"
  is not the same as "work is being picked up";
- **the NWP overlay is actually active** — the specific Plan 100 regression. A heartbeat that would
  have stayed green through the 3-day blackout is not worth having.

That last point is the real design constraint: **the heartbeat must be able to fail.** Every check
above must be one that would have caught a real, previously-observed incident.

### Phase 4 — Make the runbook honest

`docs/deployment/mac-mini-staging.md` gains: the power settings and how to verify them, the DHCP
reservation as a prerequisite, how to wake the box when it is unreachable (physical key-press —
wake-on-LAN will only work *after* `womp 1` is set, so it is not a recovery path for the current
state), and the heartbeat's meaning and alert path.

Also fix the address-churn trap: the runbook should tell the reader how to *find* the host
(Bonjour/`sapphire-staging.local`, or the reserved address once set), not hardcode an IP that DHCP
will invalidate.

## Verification

This plan is about failures being noticed, so the exit gate is **an induced failure**, not a passing
test suite:

- Put the host to sleep deliberately → **the alert fires** within the heartbeat interval.
- Pull the network cable → the alert fires.
- Power-cycle → the host comes back **unattended** (`autorestart`), the stack starts, the heartbeat
  resumes without a human.
- Simulate the Plan 100 regression (drop the `-nwp` overlay) → the heartbeat's NWP assertion **goes
  red**. If it stays green, Phase 3 has failed its purpose.
- `pmset -g` shows the asserted settings after a reboot.

## Relationship to other plans

- **Plan 100** (NWP-off-on-restart blackout) — same host, same silence-not-signal failure family.
  116 Phase 3 is the detection layer 100 needed and did not have.
- **Plan 098** (obs-ingest poll-starvation) — the reason the heartbeat must assert *work is being
  picked up*, not merely *the process is alive*.
- **Plan 115a** — its live DB audit is **blocked** on this host being reachable. That is what
  surfaced this plan.
- **Plans 046 / 075 / 111b** — the existing mac-mini deployment/bootstrap plans this extends.
- **Plan 091** — stale; claims `mac-mini.toml` disables NWP (it does not, `mac-mini.toml:10`).
  Correct or archive while in this area.

## Open questions (owner)

1. **What off-host infrastructure exists for the heartbeat receiver?** (Phase 3 — gates the design.)
2. **Is this host meant to be 24/7 at all**, or is it acceptable for it to be down outside working
   hours? If the latter, the alerting must know the expected schedule, or it will cry wolf nightly.
   *(The forecast cycle runs `0 */6 * * *`, so the honest answer is probably "yes, 24/7" — but it
   should be stated, not assumed.)*
3. **Is a Mac mini on a home LAN the right home for staging** as we approach a v1 deployment with an
   Oct-2026 date? Not a reason to block this plan — 116 is worth doing regardless — but the fact
   that it has now failed silently twice is data worth weighing.
