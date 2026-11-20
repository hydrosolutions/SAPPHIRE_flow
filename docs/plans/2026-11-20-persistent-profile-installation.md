---
status: READY
created: 2026-11-20
owner: deployment orchestrator
baseline: 77fd6e8abf1ac7b4b8aedc8ee5c5cfde923bb659
---

# Install a private profile and leave it paused

## Outcome and owner decision

An administrator installs a **new** persistent stack from an external profile,
verifies it, and leaves it running for subsequent onboarding. On 2026-11-20 the
owner selected paused jobs and server-local access. This is infrastructure
readiness, not permission to issue forecasts or a claim of scientific readiness.

Reuse the existing configuration mechanism, Compose topology and release images.
No agency catalogue, new configuration framework, manager SDK, UI, SSH controller
or cloud provisioning feature. Agency profiles and station/model choices remain
private. No existing deployment is adopted, migrated, upgraded or reconfigured.
This planning pass changes no runtime configuration, credentials or infrastructure.

The owner deferred Proton Pass server automation and investigation of NHS's
authentication integration until this deployment work is finished. Neither is
an installation prerequisite. Keep the protected-file delivery boundary neutral
to the secrets provider; verify NHS compatibility separately before activation.

## Existing capabilities and the missing boundary

| Existing, verified mechanism | What this plan adds |
| --- | --- |
| `config/deployment_profile.py`, `tools/deployment_profile.py`: external selection, provenance, secret references and `PASS_OFFLINE` for the bounded stored-data path | Installation actions using that same typed selection; no second manifest/schema |
| `tools/deployment_rehearsal.py`: frozen nonsecret inputs, finite recipe transform, prepared-image probes, daemon/resource ownership, bounded startup | Durable private installation state and retained data; real delivered credentials; loopback HTTP |
| `cli/deployment_rehearsal.py`: paused pools before init, paused schedules, worker/API checks, no execution | Reuse the applicable checks under a distinct persistent-installation guard; revoke the temporary probe token |
| Existing init: Alembic, declared tenants, role bootstrap and registrar | Ordered invocation against exclusively new storage; no migration authoring or registrar-default changes |
| Disposable release qualification completed in [L4](2026-11-19-profile-launch-rehearsal.md#l4-disposable-linux-qualification-complete) | Persistent stop/start, daemon-restart, credential-delivery and host-loopback qualification |

The rehearsal is **not** an installer with cleanup disabled. It substitutes test
credentials, removes ports, disables restarts and destroys storage. Preserve its
existing contract and synthetic forecast smoke tests. The older
[update draft](2026-11-11-one-command-deploy.md) remains DRAFT and undispatched.

## First supported installation

- One dedicated Linux amd64 Docker host, local rootful daemon and standard bridge
  networking. Enforce Docker Engine 28 or newer in preflight: older engines can expose localhost
  publications to the local network ([Docker port-publishing reference](https://docs.docker.com/engine/network/port-publishing/)).
  Custom routing, remote daemons, Swarm, rootless Docker and shared live hosts are
  outside this first qualification. Do not modify daemon settings or firewalls.
- The current complete offline path: one explicit writable tenant, stored
  reanalysis, forecast adapter disabled, exactly `forecast-cycle` and
  `backup-database`, no optional operator/provider or station/rating-file scopes.
  Retain all required scientific declarations; invent none to obtain a pass.
- Clean source checkout and already prepared matching default/Aquacast images.
  Reuse existing image identity probes and pin immutable image IDs. Infrastructure
  images stay digest-pinned. The installer never builds, pulls, transfers images,
  publishes releases or grants release/receiver admission. Existing release/load
  approval remains an upstream prerequisite, not something an image probe proves.
- Explicit HTTP bootstrap route `SAPPHIRE_DOMAIN=":80"` and same-origin CORS.
  Publish only Caddy `127.0.0.1:<http-port>:80/tcp`; no wildcard, IPv6, database,
  Prefect or direct API publication. Default port 8080 is a neutral convenience;
  an occupied port refuses installation, never silently selects another.
- Use standard named volumes and standard service privileges, commands, mounts
  and configuration order. No external/custom-named volumes or arbitrary binds.
  Preserve the standard backend isolation/frontend bridge; local ingress is not
  an outbound-network isolation claim. No provider connection is tested.

Display timezone, hydrological grid and model training conventions remain distinct
external inputs. This plan changes none of their semantics or the time-grid work.

## Administrator contract (proposed, not implemented)

New host command: `tools/deployment_install.py`. Use the documented installed
project/uv environment; installing host dependencies is an administrator step.

| Command | Meaning |
| --- | --- |
| `--profile-file P preview --installation I --state-dir D [--http-port 8080]` | Read-only proposed actions and differences from the selected recipe; no daemon access or secret reads |
| `--profile-file P install --installation I --state-dir D [--http-port 8080]` | Repeat checks, create a new owned installation and verify it remains paused |
| `verify --state-dir D` | Read-only paused-phase inspection; no token minting, init, schedule changes or database writes |
| `stop --state-dir D` | Stop only owned containers; preserve volumes, secrets, frozen configuration and records |
| `start --state-dir D` | Start only recorded long-lived containers in explicit service order excluding init; no recreation, registration or update |

`D` is an absolute administrator-owned private directory outside the checkout,
created exclusively with mode 0700 under a stable parent. It is installation
state, **not a temporary report folder**: preserve it alongside the database.
Preview describes paths only in local text; shareable JSON uses safe aliases.
Changing the original profile later does not update the frozen installation.

Use the existing exit convention: 0 for the named successful operation, 1 for a
runtime failure, 2 for an unmet prerequisite/unsupported case. Distinguish
`PLAN_READY`, `INSTALLED_PAUSED`, `VERIFIED_PAUSED` and `STOPPED`; none means
forecasting is enabled. Machine-readable reports list checks and unverified
facts. A repeated `install`, occupied state directory, existing project/volume,
or partial previous attempt refuses rather than adopting, overwriting or deleting.

## Durable state, credentials and startup

1. **Preflight and ownership.** Acquire a host-wide advisory lock at
   `/run/lock/sapphire-flow-install.lock` before collision checks and hold it
   through recording success/failure; lifecycle commands use the same lock.
   Require local administrative access to the lock and daemon; never put the
   lock inside the not-yet-created installation directory. Preflight refusals
   leave that directory and persistent resources unused, so corrected inputs can
   be retried. Temporary owned image/credential probes have bounded cleanup and
   their own private recovery record. Record daemon identity, project, random installation ID,
   image IDs, source, selected port and owned resource IDs. Persist the intent
   before mutations and checkpoint created resources. Recheck daemon and both
   installation/project labels before every mutating operation.
2. **Freeze the nonsecret recipe.** Reuse bounded capture and drift checks.
   Keep private config copies and approved executable bits in `D`. Generate and
   verify a finite transformed recipe: immutable images, no build/build secrets,
   stable owned names/labels, frozen read-only binds without implicit directory
   creation, the one loopback port and HTTP Caddy route. Retain standard
   `unless-stopped` service policies and `init`'s `restart: no`. Do not copy the
   disposable transform and silently change its cleanup rules.
3. **Consume delivery files, never manage a vault.** The same five runtime
   `secret_bindings` supply real files. Any manager/agent/manual delivery can
   satisfy this boundary. Preview never opens them. Runtime checks readability
   for the actual consumers, nonempty supported content and distinct DB-role
   credentials without printing values. Mount deliveries read-only and verify
   actual mount flags; never
   generate, replace, copy into reports, chmod/chown or delete the original files.
   Concrete delivery default: mode 0444 files inside an administrator-owned 0700
   directory, restricting host access while permitting mounted readers.
   App images define `app` as UID/GID 1000. Their entrypoint reads the service DB
   password as root, then init reads the three scoped-role files and API reads
   the pepper as UID 1000. Backup also reads its password file as UID 1000.
   Prefect reads the owner file in its root startup command; Postgres uses its
   upstream bootstrap. Verify the pinned images' effective users. Read-only
   mounts make the entrypoint's attempted `chown` fail harmlessly; never rely on
   that attempt to fix permissions. Test actual consumers and unchanged host
   bytes, uid/gid, mode and mtime after install/failure/stop/start.
   Manager symlinks may resolve to a stable local regular file. The selected
   resolved delivery path must remain available for the installation lifetime;
   following a newly retargeted manager alias is not an automatic update.
   Use the credential-change policy below, not inode/mtime alone. Keep delivered files
   available across restarts. Changing a delivery is **not** credential rotation;
   rotation and manager boot-time orchestration remain separate workflows.
4. **Respect the current credential format.** `docker/entrypoint.sh` interpolates
   DB passwords into URLs using shell substitution. For this first installer,
   accept only nonempty ASCII URL-unreserved DB passwords (`A–Z`, `a–z`, digits,
   `-._~`), with an optional final LF; reject other content before persistent
   mutation, never rewrite it. Recommend independently generated 64-hex-character
   values. The pepper uses the existing nonempty file contract. This is a
   documented installer limit, not a change to existing deployments/readers.
   General password encoding is a separate demonstrated follow-up, not a
   provider-specific adapter. The pepper reader strips surrounding whitespace;
   changing its effective value invalidates existing tokens. Reject secret-value
   environment overrides (`DB_PASSWORD`, `ACCESS_TOKEN_PEPPER` and equivalent
   reader fallbacks); missing files cannot fall back to ambient values. Never
   include secret values or credential fingerprints in shareable reports.
5. **Start safely.** Start fresh PostgreSQL and Prefect, create and read back all
   three paused pools, then run normal init. Verify migration head, declared
   tenant and least-privilege roles. Pause and read back both deployments'
   schedules before starting API, Caddy and workers. Verify worker heartbeats,
   pool/schedule pause and complete bounded flow history: scheduled/pending may
   exist, but any execution fails the installation. Never change default
   registration behavior for other deployments.
6. **Verify access without publishing credentials.** Check host loopback health
   200 and protected collection 401, plus internal Caddy authenticated 200 using
   a short-lived tenant-scoped consumer token confined to its helper process.
   Revoke the token in a finalizer and verify revocation; failures are reported,
   never upgraded to success. Do not issue a permanent admin credential. The
   later operator follows the existing explicit token-bootstrap instructions.
   `sapphire_api` already has token UPDATE and audit INSERT for the existing
   `revoke_token` operation; no new privilege is needed. This token-producing
   probe runs at install/qualification only, never in read-only `verify`.
   Inspect actual publications on every service; a route merely configured as
   `:80` is not proof of loopback-only publication. Report inspected bindings
   and observed host responses, not a claim that every external route was tested.
7. **Retain, report and hand off.** Success retains resources/configuration and
   records `INSTALLED_PAUSED`. `verify` checks current ownership, image/config,
   credential availability, services, publication and pause state, not initial
   emptiness or scientific suitability. Legitimate later onboarding changes are
   not silently repaired; state outside this paused contract is reported as such.
   `verify` reports `STOPPED` for a deliberately stopped stack; the completed
   one-shot init container is expected to stay exited. Outside the paused phase
   (changed job/pool inventory, active schedules or prior execution), report the
   observed state and exit 2 without claiming `VERIFIED_PAUSED` or repairing it.
   `start` similarly refuses to start workers outside that contract, or when
   containers/deliveries are missing or fail the selected credential-change
   policy; it cannot repair partial init.
   Start PostgreSQL, wait for health, then Prefect and a pause readback, then
   API/Caddy and workers. Always exclude init from the explicit start list and
   prove its identity/finish timestamp unchanged. Never run whole-project start.

The operator owns credential delivery before first start and before host/daemon
restart. Restart persistence qualification uses durable files; ephemeral manager
deliveries require a separately verified host boot-order setup.
The explicit `start` command can check before starting workers; automatic
daemon/host restart cannot. Its no-execution guarantee relies on persisted
Prefect pool and schedule pauses, not on Compose dependency ordering. PI4 must
prove that persistence and that a manually stopped stack stays stopped on reboot.

## Failure and recovery

Use the rehearsal's bounded command/startup deadlines (15 minutes plus a separate
two-minute containment reserve), injected executor/clock and allowlisted errors.
On failure or catchable interruption, stop owned workers first, then API/Caddy,
Prefect and PostgreSQL last. Give PostgreSQL a bounded graceful stop allowance
within the containment reserve; report incomplete containment if it expires.
Retain all volumes, configuration and recovery state. Never execute
`down -v`, a global prune, a schema downgrade or automatic restore. A failed stop
records containment as incomplete. An uncatchable kill is not a containment
guarantee; the durable record lets `verify` and `stop` discover owned resources.

An incomplete initial installation requires developer-assisted inspection; the
first version has no resume/reconcile engine or destructive reset command.
Loss of `D` leaves an orphaned installation: refuse automatic adoption. The guide
provides an inspection-first, label-and-daemon-verified manual stop procedure and
explains how to back up private installation state alongside the database.
The recovery guide identifies the failed stage, retained resources and next
inspection steps, and links normal backup/restore procedures. It never advises
deleting the database to retry. A new empty install must use fresh state/resources.

Backups are registered but paused. Installation does not prove a backup or restore
and is not an operational handover. Before operational activation, a separate
review must settle backup destination/retention and a restore test, real source
and model onboarding, controlled test execution, public access/TLS if wanted,
and which schedules/pools to enable. No activation flag is added in this plan.

## Milestones, ownership and tests

One implementer owns each pass. Dispatch only a READY milestone; one independent
reviewer covers the relevant privilege, credential and persistence concerns.
Every implementation PR includes its affected administrator docs and full final
CI. No milestone authorizes a production deployment or release publication.

| Milestone | Concrete paths / work | Acceptance and meaningful tests | Depends on |
| --- | --- | --- | --- |
| PI0 — settle scope/review | This plan; link from the rehearsal and update plans | Owner's paused/local decision recorded; one independent Claude review; resolve material findings before orchestrator sets READY | Merged #579 |
| PI1 — preview and delivery contract | New `tools/deployment_install.py`; reuse `tools/deployment_profile.py`, `config/deployment_profile.py`; new `tests/unit/tools/test_deployment_install.py`; initial `docs/deployment/profile-installation.md` and corrected `docs/secrets-bootstrap.md` | Real copied external profile/Compose preview; required inputs/provenance preserved; no daemon/secret read; exact finite transform; unsupported recipe, alias, dirty source and identity cases. No executable install advertised yet | PI0 |
| PI2 — fresh install and retained lifecycle | Installer runtime; new `src/sapphire_flow/cli/deployment_install.py`; narrowly share existing pool/job/worker checks via `cli/deployment_checks.py` if needed, preserving rehearsal guards; unit/fault tests beside both CLIs | Credential permission/content failures before persistent mutation; pause-before-init/workers ordering; scoped role/auth checks; host port mismatch; scheduled-run inventory overflow reports unverified rather than silently truncating or inventing execution; every startup failure/interruption stops owned containers and preserves data; collision/concurrent launch/foreign daemon refusal; repeated install refused; verify/stop/start do not migrate/register/recreate or remove data | PI1 |
| PI3 — hosted persistent qualification and operator handoff | New `tests/integration/test_deployment_install.py`, generic fixture instructions under `tests/fixtures/deployment_installation/`; reuse hosted dedicated-daemon qualification tooling/CI rather than a second build pipeline; user docs below | Real CLI install, host-loopback 200/401 and internal authenticated 200, token revocation, all pauses/no execution. Container stop/start preserves DB/volume sentinels and pauses, with init never restarted. Daemon/host restart remains PI4 until a controlled host is available. Inject init failure; retain failed state and prove guarded stop/repeated refusal. Missing secret at restart fails without fallback. Original profiles/secret files and unrelated sentinel unchanged. Explicit test-harness teardown removes only test-owned resources; full CI after final change | PI2; matching CI-built images |
| PI4 — release/host acceptance | Private qualification evidence; concise outcome here | Same CLI on an owner-authorized disposable Linux host, owner-published matching images and synthetic inputs. Verify running-stack persistence and stopped-stack persistence across daemon restart and host reboot with durable deliveries, loopback publication and test/provider cleanup. Infrastructure/software inventories and measurements recorded; missing access means not performed | PI3; applicable owner host authority and approved release images |

PI3 explicitly reuses the rehearsal's `ci-development` identity mode for its
CI-built images and reports development qualification, not release admission.
Operator installs default to `release-target`; PI4 proves that path. Existing
owner authorization is reused when it covers the host/test; do not request it
again merely because this is a later milestone.

If CI workflow bytes change, update its existing attestation hash/producer
inventory and release target under the current CI/CD policy. Coordinate shared
`Dockerfile`, CI and release-target edits; claim no migration number. Do not edit
the station-onboarding agent's `docs/operations/new-station-by-hand.md`.

## Documentation and maintained boundaries

- `docs/deployment-quickstart.md`: link the new supported installation step after
  offline checking; distinguish installed/paused from operational.
- New `docs/deployment/profile-installation.md`: prerequisites, copy/select/check,
  install/verify/stop/start, credential delivery/permissions, expected results,
  retained-state recovery and onboarding handoff. Only implemented commands are
  user-facing instructions; link field details and existing token/onboarding docs.
- `docs/deployment/profiles.md`: remains the schema/default/precedence reference;
  update only actual supported differences. No duplicated config catalogue.
- `docs/secrets-bootstrap.md` (PI1): replace its stale single-password bootstrap with
  the five current logical credentials and the manager-neutral delivery contract;
  distinguish runtime credentials from build tokens and deferred identity features.
  Reconcile relevant stale first-deploy snippets in `docs/standards/cicd.md` and
  link the implemented security/bootstrap reference instead of future user/TOTP UX.
- `docs/touchpoint-maps.md` and relevant security/orchestration/CI standards:
  record only changed contracts and link the administrator instructions.
- Implementation history and concise review outcomes stay here; full reviews,
  raw config/credential/provider evidence remain private. Public examples are
  generic and synthetic; no real agency configuration or credentials.

```json
{"phases":[{"id":"PI0","depends_on":[]},{"id":"PI1","depends_on":["PI0"]},{"id":"PI2","depends_on":["PI1"]},{"id":"PI3","depends_on":["PI2"]},{"id":"PI4","depends_on":["PI3"]}]}
```

## Review status

One independent Claude review completed on 2026-11-20, covering administrator
use and developer support. Concrete findings were incorporated: explicit consumer
UID/file-readability and read-only mounts; original-delivery preservation tests;
secret-bootstrap corrections in PI1; start order excluding init; read-only,
paused-phase verification; and an honest distinction between explicit start and
automatic host restart. Existing API grants support probe-token revocation.
Full review evidence remains local, outside commits.

**Resolved owner decision — credential changes.** The owner accepted private
credential fingerprints and deferred Proton server automation and NHS identity
integration until after deployment work. Retain fingerprints in protected
installation state, permitting same-content file redelivery while detecting
changed effective credentials, including the pepper. Inode/mtime equality alone
is not a restart gate. Credential rotation remains a separate coordinated step.

PI2 stores a versioned record in a mode-0600 file inside mode-0700 `D`, with an
independent random 32-byte salt per logical credential and SHA-256 of that salt
followed by the effective credential bytes. Generate salts at the runtime
boundary with injected randomness for tests. For DB passwords, remove only the
permitted optional final LF; for the pepper, match the existing reader's
text decoding and whitespace stripping, then encode as UTF-8. Never store raw
values in installation state or include salts/fingerprints in reports, logs or
command arguments. Treat the record and its backups as sensitive: salted hashes
permit guesses against weak credentials if disclosed; they are change detectors,
not a vault or evidence of successful database authentication.

Explicit `start` checks all five values before starting any service; `verify`
reports drift without mutation. Missing, malformed or mismatched fingerprint
state refuses with exit 2; never silently establish a new baseline. `stop` must
remain available for verified owned resources even when credential checks fail.
PI2 tests same-value redelivery despite inode/mtime changes, supported newline
and pepper whitespace equivalence, changes to each of the five values, missing
or corrupt records, and absence of sensitive material in public output. These
checks supplement the actual reader/read-only mount and authentication checks;
automatic daemon restart retains the limitations documented above.

The orchestrator records READY after the independent review and resolution of
its material findings. PI0 is complete; PI1 is the next implementation milestone.
This status does not approve or merge the PR, publish a release, or authorize a
production deployment.

## PI1 implementation

`tools/deployment_install.py preview` now reuses profile/rehearsal preparation,
requires an explicit local HTTP/same-origin route, and verifies a separate finite
persistent recipe. It reports safe offline provenance and runtime gaps without
opening credentials, contacting the daemon, writing installation state or exposing
an executable recipe. Runtime commands and fingerprint creation remain PI2.

Administrator instructions are in `docs/deployment/profile-installation.md`;
`docs/secrets-bootstrap.md` now describes the five runtime deliveries, reader
permissions and manager-neutral boundary. Related standards and entry points link
those maintained instructions. No existing runtime recipe or reader changed.

Local validation: 242 installation/profile/rehearsal tests passed, including 39
new preview/transform cases; changed-file Ruff and Pyright passed. One independent
Claude patch review completed. Fixed defect-versus-input error classification and
strengthened administrator guidance and contract tests. Rendered-route forwarding
and required Caddy were already enforced by the shared checker; new real-Compose
tests verify both. No unresolved material findings; full final-head CI remains
required before merge. Full review evidence stays private.
