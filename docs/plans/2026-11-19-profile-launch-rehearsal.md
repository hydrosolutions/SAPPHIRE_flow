---
status: READY
created: 2026-11-19
owner: deployment orchestrator
baseline: 813313697fcad1cd150d0bcbb01a38376e1923cb
---

# Rehearse starting a private deployment profile

## Outcome and scope

An administrator selects the existing external profile and installation files,
previews the actions, then runs a disposable startup rehearsal. It starts the
standard services with the selected application configuration, fresh storage and
generated test credentials; verifies startup and authenticated API access; then
removes its resources. It never adopts or updates an existing deployment.

The owner requested this next milestone after merging #565 and authorized a
temporary Infomaniak server, with AWS as fallback, completely removed after the
test. That permits the bounded qualification below, not a production activation.
Start with local tests; provision no cloud resources before runnable, reviewed
code and matching images are available. Cloud provisioning remains an operator
step, not a feature or new dependency of the launcher.

This is the smallest bridge between configuration validation and eventual
persistent deployment. Success means **the rehearsal started and cleaned up**.
It does not prove forecasts, real credentials, agency readiness, internet access,
DNS/TLS, host-port reachability, backups/restores, upgrades or rollback. The
existing synthetic forecast smoke test remains the forecast-path qualification.

Do not implement the broader [update draft](2026-11-11-one-command-deploy.md)
under this plan. Persistent installation, schedule activation, image build/pull/
transfer, SSH orchestration, migration of existing databases, secret-manager
clients, hotfix tracks and automatic rollback are separate work.

## Existing capabilities and gaps

| Existing on main | Reuse / missing work |
| --- | --- |
| `tools/deployment_profile.py` selects external files, uses authoritative readers, renders Compose and can return `PASS_OFFLINE` | Mandatory entry gate; keep its meaning and offline behavior unchanged. No second profile schema or agency catalogue. |
| Standard Compose init migrates, provisions declared tenants, bootstraps scoped roles and registers jobs | Run this sequence only against fresh owned Postgres/Prefect storage. Registration normally activates schedules; isolate execution before starting workers. |
| `tools/image_identity_probe.py` checks default/Aquacast image identities | Probe both preloaded variants, record immutable IDs and run those IDs. No arbitrary expected-version escape hatch. |
| `tools/deployment_smoke.py` has bounded local-Docker execution, generated credentials and ownership-checked cleanup | Reuse narrowly suitable functions or extract only demonstrably shared boundaries; preserve its CLI, standard-library host prerequisite and existing qualification. It is not a generic deployment engine. |
| `tools/qualify_deployment_smoke.py` checks repeated runs and preservation of unrelated resources | Reuse its sentinel test pattern for this separate rehearsal; do not replace or weaken the forecast smoke gate. |
| Offline fixture uses fake release identity and absent secret files | Keep it offline-only. A separate runtime fixture needs real prepared image identity; no production recipe or scientific defaults are inferred from either fixture. |

## Supported selection and preview

Separate entry point: L1 delivers `preview`; `run` and `cleanup` below remain
proposed L2/L3 commands and are not accepted by the current CLI:

```sh
uv run python tools/deployment_rehearsal.py --profile-file /private/deployment/profile.toml preview --installation /private/deployment/installation.toml
uv run python tools/deployment_rehearsal.py --profile-file /private/deployment/profile.toml run --installation /private/deployment/installation.toml --report-dir /absolute/new/rehearsal-report
uv run python tools/deployment_rehearsal.py cleanup --report-dir /absolute/existing/rehearsal-report
```

`preview` uses real offline evaluation/rendering, with no daemon access, credential
read, service start or output-directory creation. It shows intended phases, exact
categories of replacements and remaining runtime prerequisites. Text may contain
local paths; JSON follows existing safe aliases and omits private labels/values.
Exit 0 means a complete rehearsal plan, not an image/host check or launch approval.
Preview leaves immutable image IDs and generated paths explicitly unresolved;
it does not emit an executable Compose recipe. Runtime binds those values and
rechecks the same finite transformation before execution.
Invalid inputs exit 1; uncovered offline inputs exit 2. `run` repeats validation;
it never trusts previously printed JSON as authority. No `--force`/ignore-pending.

First support is deliberately narrower than all offline passes:

- The #565 stored-data path, exactly forecast and backup jobs, same clean source
  checkout, no extra Compose services or exceptional scopes.
- Exactly one declared writable tenant, matching the configured onboarding
  tenant; no global-admin deployment. Other identities remain unsupported for
  this rehearsal only. Do not tighten existing runtime/profile behavior.
- Standard named data volumes only. Refuse host data binds, external resources,
  custom resource names, runtime configs outside the checked chain, extra secret
  roles and dynamic `${...}` application-config inputs. Read-only selected config
  files and tracked standard initialization/Caddy files are the only host inputs.
- An explicit version/source triple matching the clean checkout and prepared
  default **and Aquacast** images. The normal forecast worker uses Aquacast even
  though this rehearsal runs no model. Infrastructure uses the current recipe's
  pinned PostGIS, Prefect and Caddy images, all preloaded. The prepared release
  evidence remains authoritative; a runtime probe is not release attestation.
- A local Unix-socket Docker endpoint, compatible Compose, and the documented
  project environment. Remote Docker/context overrides are refused. No host
  `.env`, ambient provider settings or arbitrary Docker credentials are inherited.

Use the existing smoke identity distinction: administrator runs default to
`release-target` (checkout target and HEAD); explicit
`--image-identity ci-development` accepts only `0.dev0+g<full checkout HEAD>` with
an existing Docker-safe tag and matching source declaration. No arbitrary version
override. L3 uses locally prepared development images of the final candidate,
built through existing authorized build tooling; it does not require publishing
an unmerged release. L4 uses owner-published release images and their matching
checkout. Record the identity kind so development evidence cannot claim release
qualification. This does not change either profile schema or identity validator.

## Freeze and transform the selected recipe

Keep the complete ordered recipe and application chain as the source of truth.
Share the existing successful evaluation/render result through a small typed
boundary if necessary; do not invoke a second independent merge/validation engine.
At run time copy the exact selected nonsecret config bytes and tracked bind files
into a new owner-only workspace. Preserve executable modes; bind snapshots
read-only. Detect input/source drift during capture; refuse before launch rather
than evaluating one version and mounting another. Execution reads the frozen
inputs, never re-renders changing operator files or reuses the installation name.

Apply only these explicit rehearsal changes to the checked render:

1. Random project/resource names and per-run ownership labels, including all
   containers, networks, data volumes and temporary probes. Assert no collision.
2. Fresh non-external data volumes with unchanged sharing/access relationships.
3. Generated independent credentials for the five existing runtime roles, in a
   private directory with the file modes required by the existing entrypoint.
   Replace the logical delivery references explicitly; never open, overwrite or
   remove the administrator's delivery files. No build secrets are requested.
4. Immutable application/infrastructure image IDs; remove build declarations and
   unused build-secret definitions. All launch commands forbid builds and pulls.
5. Both existing networks become internal; remove every published host port.
   Set only the Caddy service's `SAPPHIRE_DOMAIN` value to literal `:80`; the
   unchanged `Caddyfile` then selects HTTP without certificate provisioning.
   Original route/origin choices remain visible as untested desired settings.
6. Disable restart policies so failed/interrupted rehearsal services do not
   restart after a host reboot. Preserve commands, init sequence, privileges,
   resource limits, health checks, config order and runtime credential roles.

A finite verifier checks the exact differences against the original render before
any container starts. Unsupported differences refuse; do not weaken the existing
offline checker to pass the transformed recipe. Final serialization/re-rendering
must preserve literal dollars, shell expansion, path spaces, commands and env
values without a second interpolation. Test that boundary with real Compose.
The frozen recipe is private operational material, not shareable output.
Compare the complete final recipe: Caddy's file bytes, mount targets/access and
volume sharing remain unchanged except for the enumerated snapshots/fresh volume
names, and neither port 80 nor 443 is published. If the transformation needs an
open-ended override system or exceptions beyond this fixed list, stop and return
to the owner with the concrete mismatch; do not grow a transform framework.

## Start, verify and clean up

Use an injected clock, bounded subprocess runner and run-ID source. One bounded
run creates one project; no background daemon, resume mechanism or host lock
framework. Before the first Docker mutation, write and fsync the private recovery
record and its directory, verify private permissions, and record endpoint/daemon
identity, run/project labels and planned owned-resource/probe names. Persist
discovered IDs atomically; recovery also discovers correctly labelled resources
created between creation and the ID update. No credential, config content or
arbitrary deletion path belongs in that record. Keep input/image evidence
separately. New report directory, mode 0700, no symlink reuse.
Budget: fifteen minutes for preflight/start/verification plus two
minutes reserved for cleanup; individual commands are bounded by the remaining
budget. These are limits, not runtime/resource guarantees.

Order is a safety contract:

1. Complete offline/recipe checks and image preflight. Image probes have no
   protected mounts or network and are tracked for cleanup even if interrupted.
2. Start only fresh Postgres and Prefect; wait for their existing health checks.
3. Using the installed Prefect client, create all three process pools (`default`,
   `ingest`, `backup`) **paused before init or any worker starts**. Existing pools
   at this point are an isolation failure. Check the empty pool inventory after
   Prefect is healthy, then read back all three pools with `is_paused=true` before
   permitting init. Existing registrar preserves an
   already-created pool (`ObjectAlreadyExists`); do not change its global defaults.
4. Run the normal init command to successful completion. Check Alembic is at the
   checkout's current head and the declared tenant/role bootstrap succeeded.
   Verify exactly the two selected deployments; pause their schedules and
   read back schedules and all three pool states before starting any workers.
   Pool pause is the execution barrier; schedule pause is secondary. Registration
   can create scheduled records before its schedules are paused.
5. Start API, Caddy and the three normal workers. Confirm health where defined,
   worker registration/heartbeats in the expected pools and still-paused pools.
   Require no flow execution: fail with `unexpected_flow_execution` for any run
   outside Scheduled/Pending, any recorded start time or evidence of a prior
   started/finished state. A scheduler-created pending/scheduled record is not an
   executed forecast and must not be reported as one.
6. In one guarded Python process in the owned API container (on the frontend),
   probe `http://caddy:80/api/v1/health` (the existing proxied API health route)
   and `/api/v1/stations`: unauthenticated 401, then authenticated 200 with the
   expected empty paginated station collection. Use existing token primitives for
   a short-lived, tenant-scoped consumer token with empty station scope in this
   fresh database. Create the token and make HTTP requests in that same process;
   the token never leaves it, including via stdout, environment or exec arguments.
   The token and audit rows exist only in the disposable DB. The helper requires
   the rehearsal run marker and exact internal service URLs; the host invokes it
   only in an ownership-checked API container. No broad admin token or auth bypass.
   Check secret canaries in container exit output, Docker inspect data, host
   output and reports.
7. Always clean up on success, ordinary failure, timeout and catchable signals.
   Reinspect endpoint/daemon identity and both Compose-project/run labels before
   each removal. Remove only positively owned containers, networks and volumes,
   then verify their absence and erase generated credentials/config snapshots.
   Do not prune images, follow symlinks, remove source inputs or touch other projects.

`cleanup` uses only the private ownership record and Docker inspection; it must
work if the original profile or credentials are gone. A changed daemon, malformed
record or foreign resource refuses destructive action. No Compose-wide `down -v`
or global prune. Cleanup is idempotent; incomplete cleanup keeps the run failed,
preserves the minimum private recovery record and prints the recovery command.
An uncatchable kill is not a cleanup guarantee; document manual recovery.
Discover stopped containers too, including leftovers after a daemon/host restart.
Erase generated credential/config files after an attempted cleanup even if Docker
cleanup fails; retain the credential-free recovery record and safe evidence.

The first L2 Docker experiment must demonstrate startup/heartbeats without egress.
Failure is `isolated_startup_failed`, not permission to attach an external network.
Internal networks do not isolate shared host CPU, memory, disk or daemon resources.
Use a dedicated test host for qualification; the sentinel proves targeted cleanup,
not that a shared production host is safe for this workload.

Success (`PASS_REHEARSAL`, exit 0) requires every check **and** cleanup. Preflight
refusal exits 2; runtime/assertion/cleanup failure exits 1. The allowlisted report
contains run ID, stages, safe failure codes, image/source IDs, health/auth/worker checks,
timings and cleanup counts. No raw logs, TOML, tenant names, private paths,
connection strings or credentials. Explicitly report `host_port_verified: false`,
`forecast_executed: false`, `real_credentials_verified: false` and scientific
readiness unverified. Any debug evidence is local/private and never auto-uploaded.
`PASS_OFFLINE` is a nested prerequisite result; the top-level run result is always
the rehearsal outcome, with `disposable_rehearsal` scope and no persistent-launch
approval. Print success only after cleanup verification.

## Milestones and dispatch boundaries

One implementer per pass. Each code PR includes affected docs, focused tests,
one independent Claude patch review and full final-code CI before owner merge.
Do not start code against DRAFT. Avoid unrelated station/model/QC work.

| Milestone | Concrete paths | Acceptance and meaningful tests | Depends on |
| --- | --- | --- | --- |
| L0 — settle/review | This plan; update draft above; completed validation plan | One independent Claude plan review covering administrator clarity, developer support, isolation, credentials and cleanup. Resolve material findings; orchestrator sets READY. Historical host-specific update decisions stay separate. | Merged #565; owner scope/temporary-host authority |
| L1 — preview and frozen recipe | New `tools/deployment_rehearsal.py`; minimal shared render boundary in `tools/deployment_profile.py` only if needed; `tests/unit/tools/test_deployment_rehearsal.py`; `docs/deployment/profile-rehearsal.md` | Actual external-profile/real-Compose preview, no daemon or credential reads; fail/pending propagated; exact transform verified; input drift, interpolation, writable/external mount and ambient-selector negatives; existing offline/smoke contracts unchanged. No run command that can start services yet. | L0 |
| L2 — bounded runtime and recovery | Same tool/tests; new `src/sapphire_flow/cli/deployment_rehearsal.py` for guarded in-container checks; its unit tests; narrowly shared smoke lifecycle helpers only if justified | Fault-injected tests at each stage, timeout/signal cleanup, interrupted probes, foreign labels/daemon refusal, no-build/no-pull, pause-before-worker ordering and readback, API 401/200, secret canaries in stdout/stderr/reports, idempotent cleanup without original files. Existing registrar/auth/migration behavior unchanged. | L1 |
| L3 — real Docker acceptance and user docs | New `tests/integration/test_deployment_rehearsal.py` (explicit deployment opt-in); `tests/fixtures/deployment_rehearsal/README.md` plus minimal generic inputs; `docs/deployment/profile-rehearsal.md`, quick-start, profile reference, CI/CD/security/touchpoint docs as affected | Real CLI twice with matching images and an unrelated sentinel container/network/volume. Normal init, paused worker heartbeats, Caddy/API auth and zero flow execution demonstrated. Repeat after injected init failure; all owned resources/credentials removed and sentinel unchanged. Full suite and existing smoke qualification pass. | L2; both prepared images |
| L4 — disposable Linux qualification | Private evidence only; concise public outcome in this plan/user guide | Run the same reviewed command on a temporary Infomaniak server (AWS only if unavailable). Capture startup/API/cleanup/resource evidence. Remove server and every test-created volume/IP/security-group/key resource; verify absence. Report test and cleanup separately even on failure; do not claim remote qualification from local tests. | Merged/reviewed L3; owner-published matching images; provider access |

L1 is the first small code PR. L2/L3 ship together in the next PR to avoid exposing
an unqualified run command; they remain sequential implementation checkpoints.
Cloud qualification follows owner merge/image availability. Do not rebuild or
publish a release merely to satisfy this plan. Missing provider access or images
is an explicit L4 dependency, not permission to use a live stack.
L4's server is newly created, has no existing stack and shares no Docker daemon
with live services. Record all test-created provider resource IDs privately,
including partial provisioning failures, and verify each deletion before reporting
cloud cleanup complete. AWS fallback is already owner-authorized if Infomaniak
does not work; no duplicate approval is needed. The owner publishes release images
through the existing release process; the orchestrator only consumes them.

## Documentation ownership and follow-ons

`docs/deployment-quickstart.md` remains the entry point. Link to one short
`docs/deployment/profile-rehearsal.md` for prerequisites, select/preview/run,
interpret results and recover/clean up. `docs/deployment/profiles.md` remains the
schema/default/precedence reference; link rather than duplicate. History and
review outcomes stay here; full review reports stay local. Public examples are
generic synthetic data only; administrator profiles remain outside the repository.

Persistent launch needs a separate reviewed decision on credential delivery,
host exposure, onboarding and when to activate schedules. Upgrades then need
backup, release admission and schema compatibility work from the existing update
draft. Secret managers continue to supply logical files through the existing
delivery boundary; this rehearsal selects no vendor and tests no manager.

```json
{"phases":[{"id":"L0","depends_on":[]},{"id":"L1","depends_on":["L0"]},{"id":"L2","depends_on":["L1"]},{"id":"L3","depends_on":["L2"]},{"id":"L4","depends_on":["L3"]}]}
```

## Review and implementation evidence

One independent Claude plan review completed on 2026-11-19. It found the scope
feasible and required six concrete clarifications: existing proxy/API routes,
exact Caddy substitution, pool-pause execution barrier, pool readback before init,
an in-process token channel and durable recovery metadata. All are folded in
above, with explicit negative tests. Nonblocking improvements include an isolation
stopping rule, stopped-container recovery and dedicated-host qualification.

The reviewer sampled Compose, Caddy, registrar and token code; it did not inspect
the proposed shared helpers or all linked standards. The orchestrator checked
those boundaries directly: reuse is conditional and narrow, not a promise of an
already generic lifecycle library. Implementation review must cover actual shared
code changes and preserve the existing smoke tool's standard-library contract.

Sequencing decisions are settled: L1 is separate; L2/L3 ship together. L3 uses
the existing source-bound development identity, avoiding a publish-before-merge
dependency; L4 awaits the owner's release. The review suggestion to reconfirm AWS
does not apply because the owner explicitly authorized that fallback. No material
owner decision remains for L1. The orchestrator sets READY; reports stay local.
At READY, no launcher code, cloud resources or live configuration changes had
occurred. Subsequent implementation evidence follows below.

### L1 implementation checkpoint

Preview-only implementation now shares the existing checker’s evaluated render.
It captures bounded nonsecret inputs, rejects protected-file aliases and drift,
checks the clean source/version identity and narrower tenant/storage selection,
and verifies the complete finite symbolic recipe. The public CLI exposes only
`preview`; no executable recipe, generated files or image-probe claims are emitted.
Actual external CLI tests use a separate tracked synthetic checkout. Compose
serialization tests preserve literal dollars and shell expressions with
`--no-interpolate`; snapshot binds explicitly forbid implicit directory creation.
Generated secret names are explicit; symbolic image references remain unresolved
and preserve the original image-sharing groups.
Focused checks and independent review are complete; full final-code CI is the
remaining delivery gate, recorded in the implementation PR. L2–L4 are not
implemented or qualified by this checkpoint.

One independent Claude patch review completed. Expected config-reread and render-
shape failures now produce sanitized fixed diagnostics; failed development
previews preserve their identity kind. Actual CLI regressions demonstrate that
the existing checker already rejects extra runtime secret grants, hostile network
settings and custom Compose extensions. No redundant secret schema was added: the
canonical build secrets remain valid offline inputs and are removed only by the
finite rehearsal transform. Missing/malformed recipe inputs cannot produce a ready
plan. The guide now distinguishes unsupported/dirty-source pending evidence from
invalid identity, selector and drift failures. After corrections, 44 preview tests,
Ruff/format, focused Pyright and whitespace checks passed. Earlier compatibility
checks passed 214 related tests and all 31 then-current preview tests on Compose
2.38.2 as well as 5.6.0. Full final-code CI remains required.

CI exposed a test-only renderer-discovery mismatch: the real roundtrip test looked
only for standalone Compose while production also supports trusted system plugins.
The test now observes the executable selected by the real production render and
also exercises plugin-only discovery. Real rendering stays mandatory; no Docker
wrapper, private credential configuration or daemon is used.
