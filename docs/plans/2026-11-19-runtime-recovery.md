---
status: READY
---

# Portable runtime monitoring and recovery

## Outcome

Keep SAPPHIRE services recoverable on the Mac mini and Linux deployments with
one shared host-side monitoring and recovery policy. Reuse the existing watchdog,
Compose restart policies and deployment health checks. Platform-specific code is
limited to collecting runtime memory, checking the host scheduler, and
starting/restarting the runtime. No
country-specific logic, new monitoring platform, or automatic software upgrades.

## Verified evidence, 19 November 2026

- The Mac mini Docker Desktop 4.40.0 VM stopped on 18 November at 13:42 UTC.
  The kernel recorded global OOM, 116 KiB free swap out of 1 GiB, and killed
  `lifecycle-serve` with 22,117,460 KiB anonymous RSS (21.1 GiB). The largest
  Python process had about 2.9 GiB RSS. This establishes the dominant consumer,
  but not the mechanism of its growth. Do not attribute the incident to PostgreSQL
  merely because its allocation invoked the OOM killer.
- Authorized Docker Desktop restart on 19 November restored both databases,
  API, Prefect server and all three workers. API health returned ok. BAFU
  collection resumed; this alone did not establish fresh SAPPHIRE forecasts.
- Live forecast, ingest and backup containers all use `unless-stopped`.
  Previously running containers restart with the daemon. Intentionally stopped
  containers must remain stopped. Interrupted process-based flows do not gain
  checkpoint/resume guarantees from container restart.
- The host watchdog currently exits on missing `sapphire_flow._version` package
  metadata. Its launchd registration exists, but registration is not liveness.
- Follow-up at 09:32–09:33 UTC found the 18 November 12Z forecast run still
  marked Running after VM loss, holding the deployment's sole concurrency slot.
  The following 18Z, 00Z and 06Z runs were AwaitingConcurrencySlot. The restarted
  worker was running weather-history ingestion, not that forecast run. Recovery
  must detect this concrete stale-run condition; worker restart alone did not
  restore forecast production. Any state repair requires checking child lineage
  and using supported Prefect APIs, with bounded backlog handling.
- Original Docker logs are preserved locally at
  `/tmp/sapphire-docker-incident-20261119/host-logs.tar.gz`; do not publish raw
  diagnostic archives, which may contain sensitive configuration.
- Docker's 4.42.0 release notes describe a fix for hanging after OOM. This is
  relevant to recovery, not proof of a fix for this process's memory growth:
  https://docs.docker.com/desktop/release-notes/#4420

## Milestones

### Immediate recovery — completed separately

The owner authorized manual recovery on 19 November. Three obsolete queued
cycles were cancelled before the orphan task and parent were marked Crashed
through supported Prefect APIs (`force=false`). Prefect released the slot.
Exactly one current run, `1f4362ba-66f4-4a8c-a41e-dfcc1383b903`, started at
09:46:35 UTC. Observation and NWP fetching started; fresh outputs were not yet
verified. No worker kill or permanent schedule change was required.

### M1 — Restore reliable host monitoring

Identify and fix the deployed package-install/version-metadata mismatch using
the supported build/install path. Do not hardcode a pretend version or make
routine ticks sync dependencies from the network. Verify the host environment
and installed watchdog match the intended release. Keep the monitor independent
of Docker and Prefect availability, with bounded probes and a heartbeat emitted
only after a completed check. Reuse existing alert/dead-man integrations; verify
configuration without exposing secrets. Document absent external alert delivery.

Install a wheel built from a clean, identified release commit in a dedicated
host-monitor environment with uv. Record commit and wheel hash; invoke its
interpreter directly outside the source checkout. Keep the previous environment
and switch the stable launch path atomically after the install smoke check.
Retain strict package-version validation rather than hiding a broken install.
Reuse existing packaging and dependency declarations wherever possible.

Verify an off-host dead-man channel detects a monitor that never completes a
tick. Without that channel, report partial readiness and do not claim independent
monitor-failure detection or enable unattended automatic recovery. Check macOS
GUI-session/auto-login and sleep prerequisites, then rehearse host reboot;
do not silently change those settings. On Linux, use a small platform-selected
scheduler probe instead of executing launchd probes. Explicitly verify the
service identity and restart privilege for rootful versus rootless Docker.
Preserve incident evidence outside temporary directories in an access-controlled
operator location (archive mode 0600) before further restart/update exercises.

Acceptance: import/start succeeds; a scheduled tick completes; daemon-down and
monitor-failed states are independently detectable. Test the installed package,
not only imports from a source checkout.

### M2 — Plan and perform a controlled runtime update

Inventory macOS, Docker Desktop, Engine/Compose, deployed image identities,
volumes and backup recovery evidence. Select a supported stable Desktop release
compatible with this host using official release notes at execution time. Record
the exact target and rollback feasibility before maintenance. For Linux, use the
same checklist with the supported Engine package and service manager.

Schedule a maintenance window, stop admission of new work through existing
controls, account for running flows, preserve diagnostics and perform the
explicitly approved update. Reuse existing images/volumes; no prune, factory
reset, schema migration or application upgrade as a side effect. Verify database,
API, worker heartbeats, schedules and a fresh forecast cycle. Record rollback
constraints: older runtimes may not safely read upgraded runtime metadata.

Acceptance: exact versions and recovery evidence recorded; interrupted work
accounted for; fresh outputs verified. Production rollout remains owner-approved.

### M3 — Add memory visibility to the shared watchdog

Collect host memory pressure, runtime/VM available memory, swap use and bounded
container usage samples. Container totals alone miss this incident's dominant
consumer. Include Docker internal process attribution where supported. Choose
and verify a bounded, least-privilege collection method for Desktop; unsupported
metrics must report unavailable, not healthy or zero. Linux collects corresponding
host/cgroup metrics without assuming a Desktop VM exists.

Prefer a bounded read of VM-wide `/proc/meminfo` through an existing healthy
container where verified to expose the runtime's memory; do not launch privileged
diagnostic containers on every tick. Host memory/swap and VM-process RSS remain
available independently of the daemon. Internal process attribution is optional
diagnostic evidence. Verify metric semantics on Mac and Linux before accepting
them. Distinguish unavailable and stale measurements. The incident OOM record
places `lifecycle-serve` in the root cgroup (`task_memcg=/`), outside application
container accounting; its precise growth mechanism remains unproven.

Use fixed initial policy defaults and bounded local retention, not a large new
configuration surface. Propose warning below 15% runtime memory available and
critical below 5%, sustained over two 5-minute ticks; calibrate against measured
baseline before enabling alerts. Include swap exhaustion and availability of the
measurement. High memory alone alerts; it does not restart healthy workloads.

Acceptance: VM pressure is visible even when containers look small; missing
telemetry is explicit; logs rotate; checks have bounded CPU, I/O and duration.

### M4 — Recover an unavailable runtime and verify useful service

Run outside the managed runtime: launchd on macOS, systemd timer/service on Linux.
Share detection, state, lock, cooldown and verification logic; use narrow runtime
operations for Docker Desktop and Linux Docker Engine. Reuse native service
restart behavior where sufficient. No general plugin framework.

Initial policy: two consecutive failed daemon probes at 5-minute intervals;
one bounded restart attempt, then a 30-minute cooldown; at most two attempts in
24 hours before alert-only lockout. Persist counters across monitor restarts.
Use an explicit maintenance inhibit, single-owner lock and startup grace period.
Preserve bounded diagnostics before restart, but never let capture block recovery.
Recognize intentional shutdown via the documented maintenance control. Do not
restart the runtime merely because the API, one worker or one model is unhealthy.

Specify a 60-second runtime restart command timeout and a separate readiness
window of up to 5 minutes, persisted across ticks so no tick blocks for the whole
window. Count every issued restart command as an attempt even if it times out.
Do not automatically force-kill Desktop processes or reboot the host after a
timeout: alert and apply the attempt budget. Provide an explicit, audited local
operator command to reset lockout, without discarding incident history. Implement
and test the maintenance inhibit if no suitable existing control exists; a manual
Desktop quit without the inhibit will otherwise be treated as an outage. Use
portable locking and atomic state writes. Ship alert-only until staging recovery
tests pass, with one explicit deployment-level enable switch.

Restart existing runtime/containers only. Never build, pull latest, prune, reset,
recreate volumes or bring intentionally stopped services up. Check expected
running services, database/API health and worker heartbeats after recovery.
Keep infrastructure recovery separate from forecast freshness in status/alerts.
Inventory interrupted/stale runs and occupied concurrency slots using supported
Prefect APIs. Reuse existing retry/idempotency behavior where verified; no blanket
resubmission, state rewriting or backlog replay. Escalate unresolved stale runs.
Any automatic catch-up requires a separately explicit bounded/idempotent policy.

Before adding stale-run logic, inspect the deployed Prefect version for supported
heartbeat/lease recovery and both deployment and work-pool concurrency controls.
An old timestamp alone is not proof of a dead run. Require evidence of a runtime
restart after run start and absent original process/child ownership, or a supported
heartbeat failure signal; ambiguous cases alert for operator reconciliation.

Acceptance: daemon-down recovery works on Mac and Linux; API-only failure does
not restart Docker; maintenance inhibit, locking, timeout, cooldown and restart
budget work; failed recovery alerts. Verify a post-recovery scheduled forecast
and prove unavailable/failed stations cannot be mistaken for full recovery.

## Delivery and validation

Sequential handoffs; one implementer owns each pass. Use an isolated current-main
checkout because the user's main workspace has unrelated changes. Consult
`docs/touchpoint-maps.md` (Prefect / Docker / deployment), security, orchestration,
logging and CI/CD standards. The referenced `docs/workflow.md` is currently absent;
follow the supplied AGENTS instructions without recreating retired workflows.

Independent Claude review completed on 19 November (cost $0.104891); the
orchestrator addressed its required planning findings above and set READY under
delegated authority. Full review remains local at
`/tmp/sapphire-runtime-plan-review/review.md`, outside commits. The urgent
recovery recommendation was already completed. No claim is made that a specific
Docker update fixes the memory growth. Platform metric and privilege verification
remain explicit milestone acceptance gates, not assumed capabilities.
Implementation gets one independent review,
focused deterministic tests and the required final full suite. Exercise runtime
failure on disposable/staging systems; do not deliberately OOM the live host.
Validate Mac and Linux behavior before claiming portable recovery. Update affected
runbooks and deployment checks with each change. No monitoring infrastructure
subscription or automatic upgrade scheduling is introduced.

```json
{"milestones":[{"id":"M1","depends_on":[]},{"id":"M2","depends_on":["M1"]},{"id":"M3","depends_on":["M2"]},{"id":"M4","depends_on":["M3"]}]}
```
