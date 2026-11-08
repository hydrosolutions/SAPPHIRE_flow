# Actions → private staging release delivery

This route is implemented locally, not operationally qualified. It replaces the
proposed A1/A2 bootstrap for this bounded staging delivery; it does not certify
those older isolation gates. Owner merge and canonical release publication remain
separate. No workflow publishes a release or starts the application.

## Authority and trust

The owner explicitly permits a copy of the **existing staging ADMIN SSH key** in
the approval-protected `staging-release` environment. Existing repository
`RECAP_DG_CLIENT_TOKEN` and `AQUACAST_TOKEN` entries and consumers stay unchanged.
There is no inferred CI pause, key migration, replacement, rotation or deletion.
The environment gates only the new SSH copy. It does not isolate the source keys
or fix older consumers' output/cache exposure.

All code executed in the job is trusted: reviewed source, pinned actions, private
and public dependencies, base images, Docker/build services and the hosted runner
with its administrative privileges. Late key injection reduces exposure; it is
not hostile-code isolation. The fixed SSH command does not restrict the ADMIN
credential. Server-root custody is not inaccessible to CI. The owner retains the
allowlisted digest receipt outside server-admin control; this is not protection
against malicious code trusted with both job and administrator authority.

## One route and its stopping point

`private-verification.yml` is now manual-only. Dispatch the canonical published tag,
not a PR or main ref. The owner-set `STAGING_RELEASE_ADMISSION` data binds the exact
tag object, release-state, source/tree, workflow commit/ref, actor, run and attempt.
The independent admission checks validate that data; the provider environment
approval click does not itself attest to its contents. A new attempt or changed
identity needs new matching admission data and ordinary environment approval. Full-history checkout has no stored
Git credential and normalizes origin to the existing validator's canonical URL.

`tools/staging_release.py` has three fixed phases:

1. `verify`: validate the approved canonical release; run retained checks privately;
   call `release_identity.build_receipt` with the release builder adapter; build
   both actual linux/amd64 variants exactly once; prove/scan those IDs; export the
   same IDs once and create the bound envelope. Output stays outside checkout.
2. `preflight`: revalidate the same remote/source/attempt and all payload hashes.
   Invalid identity never reaches the SSH-injected step, including failed-run
   diagnostics. The transfer phase repeats this check before opening SSH.
3. `transfer`: inject only `STAGING_ADMIN_SSH_KEY` here, use pinned host trust and
   a fixed command, then compare the exact receiver acknowledgement. Package keys
   are absent from the SSH process environment. No public archive/report bridge.

`delivery_mode` is a named choice: **`deliver-only` by default**, or `load-images`.
Deliver-only succeeds after verified, durable no-clobber sealing. Loading also
requires a separately installed receiver approval equal to the complete binding
hash. Dispatch selection alone is not that approval. No mode starts Compose, init,
migrations, workers, schedules, models or data operations. A load failure does not
claim rollback; the sealed archive remains and runtime activation remains held.

The public result is fixed phase/checkpoint status and a typed digest-only JSON receipt:
source/tree/tag/state/run IDs, envelope/receipt/runtime-bundle/archive hashes,
archive length and the two raw image IDs. No labels, inventories, full reports or
arbitrary error strings are projected. Owner acceptance requires retaining this
record outside staging-admin control and comparing the received artifact binding.

The public Trivy bootstrap scans an empty directory under `RUNNER_TEMP`. Its
`cache-dir` is a separate sibling, `private-trivy-bootstrap-cache`, outside both
that target and the checkout. `cache: "false"` disables shared action caching,
not local database writes. Keeping those writes outside checkout preserves the
strict tracked/untracked source guard. Private gates still replace `TRIVY_CACHE_DIR`
with the per-attempt `work/trivy-cache`; they do not reuse the bootstrap database.

### Coarse progress markers

The controller emits `STAGING_RELEASE_CHECKPOINT <PHASE> <CHECKPOINT> <OUTCOME>`.
Phases are `UNKNOWN`, `VERIFY`, `PREFLIGHT`, `TRANSFER`; outcomes are `ENTER`, `PASS`,
`FAIL`. Checkpoints are `WORK_SETUP`, `ADMISSION_CHECK`, `LOCAL_SOURCE_CHECK`,
`REMOTE_RELEASE_SNAPSHOT`, `REMOTE_RELEASE_CHECK`, `PAYLOAD_PREPARATION`,
`PAYLOAD_CHECK`. Unsupported arguments use `UNKNOWN`, never their supplied text.
The existing final `STAGING_RELEASE_PHASE_COMPLETE` / `STAGING_RELEASE_PHASE_FAILED`
tokens and exit behavior remain unchanged.

Markers contain no exception text, paths, commands, inventories or input-derived
values. Work setup passes only after private descriptor capture is active and before
phase dispatch. Payload preparation has one entry and one terminal marker; all its
repeated validations are silent, so markers do not expose gate cadence or readiness
branches. Aggregate phase timing remains observable. These fixed stdio protocol
markers bypass private capture without making it public; they are not application
logs. Marker write errors do not change operation results.

Markers locate coarse checkpoints, not proof that gates, builds or transfer ran or
that delivery succeeded. In particular, `PAYLOAD_CHECK PASS` followed by final
`FAILED` can mean a later transfer/receipt failure; it does not prove server contact.
Private phase traces from prior failed attempts are not recovered by this change.
All source validation and SSH admission checks remain mandatory.

## Retained gate mapping

| Existing substantive check | Manual release route |
| --- | --- |
| Lint/format/shell/typing/ratchet/filesystem scan | Same gate commands |
| Four unit shards, real Aquacast discovery, native libraries | Same commands |
| Unit coverage combine/report | Same aggregate and shard evidence |
| Wheel-only/private-package installation guard | Same commands |
| Heavy/rest integration and partition assertion | Same commands and aggregate |
| Default image build/import/resources/scripts/entrypoint/isolation | Sole release-builder adapter |
| Aquacast/Torch actual-image proof | Same produced Aquacast ID |
| Trivy vuln+secret/SARIF and explicit Syft SBOM for both images | Independent retained component outcomes |

PR merge-parent, PR release-target and PR map-base comparison are not fabricated
for a release event and are not reported as executed here. They remain in legacy
PR CI. This route substitutes the existing publisher's canonical origin, source
on main, exact source release target, remote tag/state validators and approved
source/tree/workflow/run equality. It does not weaken those validators.

A failed image component remains failed even when later components pass. Both
variants are attempted and named private outcomes survive missing receipt creation.
A failed run can deliver only a bounded diagnostic report, never image bytes or a
successful release result. Oversized/unavailable evidence or failed delivery fails
closed. Failed source validation does not authorize diagnostic SSH.

## Reviewed configuration scope (not installation instructions)

The `staging-release` environment needs ordinary owner approval and allowed
canonical release-tag refs. The exact source guard is stronger than a `v*` pattern;
that pattern alone is not approval. No bypass is part of this procedure.

Only new secret: `STAGING_ADMIN_SSH_KEY`, the explicitly permitted existing key copy.
Nonsecret environment variables:

- `STAGING_RELEASE_ADMISSION`: complete JSON `ReleaseBinding` from
  `tools/staging_release_transfer.py`; all fields are required. It contains version,
  source/tree/tag/state/workflow identities, run/attempt/actor, repository,
  environment, fixed target and named delivery mode. It intentionally contains no
  future receipt digest. Produced receipt/archive digests are bound after building.
- `STAGING_KNOWN_HOSTS`: independently pinned existing host trust, never `ssh-keyscan`
  trust-on-first-use during the job.
- `STAGING_MAX_ARCHIVE_BYTES`, `STAGING_MAX_EXPANDED_BYTES`,
  `STAGING_RESERVE_BYTES`, `STAGING_TRANSFER_TIMEOUT`: explicit positive limits from
  the approved capacity packet, not guesses. Sender SSH timeout must be 1261–1500s.
  The receiver limit is at most 1200s, with 60s reserved for cleanup and another
  60s sender acknowledgement margin. These are ceilings, not capacity evidence.

Only after separate host-operation approval, install the reviewed fixed receiver
at `/opt/sapphire-flow/staging-release/`, preserving this repository layout:
`tools/staging_release_transfer.py`, `tools/receive_build_report.py`,
`tools/image_identity_probe.py`, `src/sapphire_flow/ops/image_archive.py`.
All are stdlib-only on the receiver. The SSH command runs this exact receiver through
noninteractive sudo. There is no new service, login user or authorized_keys change.
The independent administrator verifies installed source hashes before admission.

The root-owned 0600 `/etc/sapphire-flow/staging-release.json` parses `ReceiverConfig`:
complete `binding`, positive `max_archive_bytes`, `max_expanded_bytes`,
`max_bundle_bytes` (<=64MiB), `timeout_seconds` (<=1200), `reserve_bytes`, and optional
`load_approval_binding_sha256` and `load_approval_receipt_sha256`. The last field is set only after separate target
operation approval. The binding hash is exactly `ReleaseBinding.sha256`: SHA256 of
UTF-8 `json.dumps(asdict(binding), sort_keys=True, separators=(",", ":"))` plus one
trailing LF. Different spacing or a missing LF is a different hash. The receipt
hash is required for later `load-accepted`; initial `load-images` approval may bind
the source/run before receipt bytes exist, without a circular pre-build condition.
Missing approval always holds load. Store root is fixed to
`/srv/sapphire-staging-releases`, root-owned 0700 under trusted nonwritable ancestors.
It must be new/additive, not an alias of any protected original.

The wire format is an eight-byte metadata length, strict versioned JSON envelope,
then the exact bounded file bytes in sorted filename order and EOF. Diagnostics
retain the existing 64MiB report limit. Image/archive/bundle bounds are separate.
The envelope binds the existing receipt, raw and normalized config IDs, platform,
actual store mode, all gate outcomes and exact same-source allowlisted runtime
bundle. No protected data, runtime secrets or model artifacts are included.

The receiver rejects malformed/duplicate metadata, unexpected names, mismatched
hashes/lengths, trailing bytes, unsafe archives, conflicting accepted identities
and unapproved load. Partial attempts are retained for explicit operator handling;
there is no automatic overwrite or pruning. Only an exact-byte resend of the same
attempt/envelope is idempotent. An Actions rerun is a new attempt and may produce
new bytes; it conflicts with an already accepted version. Conflicting envelopes
are rejected before body allocation/receipt. Files and directories are fsynced before acceptance. The shared archive validator checks
actual layer hashes and OCI descriptor/config reachability; IDs are not redefined.
Load refuses mutable archive tags, target tag conflicts and unsupported mappings;
it uses no pull or rebuild and checks actual native target image imports/entrypoint
in disposable network-disabled containers without protected mounts.

## Capacity and live acceptance

The archive is compressed once and retained until acceptance. The local wire file
requires another private archive-sized allocation; capacity checks include it.
The receiver retains compressed and verified expanded bytes. The configured expanded
limit also bounds counted unpacked image layer bytes. Before approved loading,
check fresh free space on the actual Docker storage filesystem with the configured
reserve. Disk observations are boundary samples, **not peak measurements**.
No fit failure permits streaming, a registry, global pruning, rebuild or a larger
resource plan. Preserve the first failure and hold. The job retains its 180-minute
ceiling; observed cold cost and private-output/post-job/transport behaviour remain
unqualified until an approved real attempt completes.

Before live use: independent patch review, final full tests, owner merge and
publication, exact environment/key-copy/provider readback, approved receiver setup,
actual capacity limits and fresh per-run approval. Image load and runtime activation
are distinct later permissions. Backup commissioning stays deferred; delivery or
Docker health implies no recovery/scientific/dashboard milestone.


### Later load of an already accepted release

After separate target-operation approval, the same root-owned receiver also accepts
only the fixed `load-accepted` operation. It reads `v<version>` below the fixed store;
there is no path argument, new Actions route or second build/export/transfer.
The server configuration must match the **original accepted binding** and set both
`load_approval_binding_sha256` and `load_approval_receipt_sha256` to that binding
hash and the original accepted receipt hash. This is a later operation approval,
not a fabricated new workflow run or changed delivery envelope.

The operation reuses the exact file, receipt, runtime-bundle, archive/layer and
image-identity validators in read-only mode, then calls the existing approved image
loader. Missing approval or changed bytes/identity cannot call Docker load. Accepted
payload/envelope/receipt/ledger bytes are never rewritten. A separate additive
`load-v<version>.json` operation record outside the sealed release directory records
the outcome. The original deliver-only record stays unchanged. The result is
`accepted-images-loaded`, **runtime not activated**. No live load is authorised by
this implementation or by that command's existence.

A verified producer digest record (`produced`) is emitted after final preflight,
before SSH admission. It is not receipt or load success. The receiver persists the
`delivered` ledger before attempting an approved load, so a load failure leaves a
verifiable delivery boundary rather than requiring another image build/transfer.
The final delivery/load receipt remains a separate result. Later `load-accepted`
works from this original delivery ledger even when the first load failed.


#### Target image identity and conflicts

Before loading either variant, the receiver selects the immutable target identity:

| Recorded ID kind | Target store | Required target ID |
| --- | --- | --- |
| `config` | classic | recorded config digest |
| `oci-descriptor` | classic | recorded config digest |
| `oci-descriptor` | containerd | recorded raw descriptor ID |
| `config` | containerd | unsupported: HOLD before load |

Source store mode remains provenance, not a target lookup rule. All variants must
have a supported policy before mutation. Containerd also requires the two raw IDs
to match the archive's unique top-level `index.json` descriptors; reachable nested
manifests alone do not qualify. Noncanonical index member aliases (such as
`./index.json`) are rejected by this receiver guard. The general archive validator
and wire schema are unchanged. The receiver never retries a failed lookup using
another identity.

The loader does **not** enforce absence of index name annotations. Before every
release load, independently verify that the accepted archive's `index.json` has
neither `io.containerd.image.name` nor `org.opencontainers.image.ref.name` anywhere
in its descriptor tree. Otherwise loading could introduce names outside the two
checked version tags. Retain this per-release check with the load approval;
v0.1.1076 already passed it. Top-level digest membership does not replace it.

The loaded ID must equal the selected ID. Both native probes and tagging use that
verified loaded ID. Before either probe runs, the receiver checks the image at
the exact position in its supported probe argv form. A changed helper-inspected
ID cannot execute a container. The helper's returned image ID must also match.
The proof keeps original raw/config identities and the observed loaded ID in its existing fields.
Existing version tags must be absent or point at the exact selected ID, both before
load and immediately before tagging. Docker has no atomic compare-and-swap tag
operation: exclude concurrent external tag writers throughout the approved window.
The receiver lock does not serialize unrelated Docker clients.

A receiver-only upgrade may retain an already accepted release unchanged. Record
old/new hashes and trusted ownership/modes for `staging_release_transfer.py`,
`image_identity_probe.py`, `receive_build_report.py` and `image_archive.py` in a
separate rollout record. Receiver source provenance is not payload provenance.
Recheck capacity on the actual Docker/containerd filesystems immediately before
load; the built-in check measures DockerRootDir only. Separately approve loading,
use bounded durable execution, and disarm digest-bound load approval afterwards.
A failed load can leave image data or a first tag without a success ledger. HOLD
for read-only reconciliation; never automatically retry, remove images, rebuild,
rewrite tags or infer rollback. These checks do not authorize runtime activation.

Fake-runner tests cover store/kind combinations and the native probe flow. Public
synthetic daemon checks cover reference primitives only, not private image imports
or native execution on another architecture. A nearby Docker patch-version result
is not proof of the target daemon's load/run/tag behavior; retain the explicit
version/architecture limitations and obtain owner acceptance before live loading.


### Transfer deadlines and acknowledgement reconciliation

The transfer step is bounded to 40 minutes; the whole job remains 180 minutes.
The internal transfer deadline is 2340s, starting **before** repeated preflight.
After source checks, payload hashing and wire-file creation, the sender recomputes
its remaining SSH budget. It reserves 60s for local cleanup and refuses to start
SSH unless the remaining budget exceeds the 1200s receiver cap plus a 60s
acknowledgement margin. The configured sender ceiling remains 1500s. The internal
alarm also bounds pre-SSH work; no expensive preflight is outside that accounting.
Receiver work stops early enough to reserve up to 60s of its own deadline for
cleanup; all target Docker calls, including owned-probe cleanup, share that deadline.
The operator checks both installed receiver code/config and sender limits before
admission. Budget exhaustion is a hold, not permission to extend this job.

The step and internal deadline add 600s of preparation headroom, not SSH time.
With 441s of preparation (as observed in run 41606771468 attempt 1), the sender
still receives at most 1500s, leaving 339s of internal slack plus the external
60s cleanup margin. Full sender allowance remains available through 780s of
preparation. At 1019s the allowance is 1261s; at 1020s it is 1260s and SSH is
refused. Preflight and wire creation share the original deadline; it is not reset.
The maximum secret-in-step and possible key-on-disk exposure window grows from
30 to 40 minutes. Credential scope, injection point, SSH bounds and cleanup
behavior are unchanged; no secret or private diagnostic output becomes public.

Before dispatch, retain the full-job timeline and assess remaining job headroom,
not only the transfer step limit. Run 41606771468 began at 09:01:01Z and entered
transfer at 10:55:06Z: about 114 minutes elapsed and 66 minutes remained. The
180-minute job limit still supersedes the 40-minute step and can interrupt its
cleanup ordering. Insufficient whole-job headroom is a hold, not permission to
retry or increase the job limit. These budgets do not guarantee delivery.

A finished hosted runner is not payload retention. When no accepted or partial
server bytes and no uploaded artifacts exist, a Produced receipt alone cannot
recover the images. A new approved build is needed unless independently verified
recoverable bytes appear. Do not mutate a published release tag or rebind an old
receipt to avoid rebuilding; use a fresh release and admission after review.

No deadline eliminates network acknowledgement ambiguity. After a lost acknowledgement,
compare the independently retained `produced` digest record with the server's
original envelope, receipt and `delivered.json` under separately approved read-only
reconciliation. Do not rebuild or start a new Actions attempt to resolve it. Matching
already delivered bytes need no re-delivery; use receipt-bound `load-accepted` only
if loading is separately approved. A mismatch, missing durable delivery ledger,
partial upload or uncertain load state remains held for explicit reconciliation.
No acknowledgement failure implies rollback, no images loaded, or runtime activation.

The frozen dependency graph has security floors of `certifi>=2026.10.19`
(compromised trust-root remediation), `pillow>=13.0.0` (image-codec security fixes),
and `filelock>=4.2.1` (CVE-2026-112940; no fixed 3.x release).
These are resolver constraints, not overrides: transitive dependency placement
is unchanged, and Pillow remains Aquacast-only. The filelock update also requires
`virtualenv==21.10.0` (allows filelock 4.x) and `python-discovery==1.6.0`
(virtualenv's minimum); all other package records and private source pins are retained.
Both resulting image variants still require fresh security/runtime proofs; prior
image receipts do not qualify changed dependencies.

The affected test selection includes the complete standalone
`tests/unit/tools/test_image_identity_probe.py` suite. Its runtime-command contract
asserts immutable image selection together with `--pull never --network none`,
while retaining the non-root user, explicit Python entrypoint and stdin checks.

### Hosted containerd exporter qualification

The manual workflow also accepts `delivery_mode: exporter-smoke-only`. This is
an execution route, not a delivery mode in an approval or envelope. Its green
workflow/environment deployment record is **not delivery, load or activation**.
The strict public route check and positive private-route predicates prevent it
from reaching package secrets, private verification, SSH or admin-key steps,
including failure paths. New events require both `delivery_mode` and
`verification_profile`; the profile defaults to `ci-reuse`. Smoke-only is still
public diagnostics, regardless of profile, and never admits a source CI proof.

Before private input, the fresh hosted Linux/x64 job requires stock Docker
28.0.4 at the default local socket and zero containers. It accepts the normal
systemd `--containerd=/run/containerd/containerd.sock` argument, but rejects
custom endpoints/configuration. A root-owned atomic daemon-config merge enables
only `features.containerd-snapshotter`; the existing service is restarted only
when its store needs changing. Unexpected host configuration fails closed.
No installer, public daemon log streamer or post-job journal export is added.

The public smoke uses the actual pinned docker-container builder, two tiny
scratch/COPY/label images, production `.Id` capture and raw-ID save, and the
unchanged archive and wire-identity validators. It keeps tags until after export.
It caps the archive at 256 MiB and client work at five minutes. The 2 GiB growth
STOP threshold samples root, Docker/containerd filesystems and owned bytes;
it is not a quota or proof of exclusive use/cancellation. Only exact fixture
tags/files are removed; failures may retain owned evidence/cache on the ephemeral
runner. All work is outside checkout.

First qualify this exact hosted path with the public-only route after reviewed
publication. Local Docker 29.8.2 evidence does not qualify hosted 28.0.4.
Any hosted engine-version drift blocks all release delivery routes until reviewed
requalification. A later private attempt reruns setup/smoke and fresh image
scans, SBOMs and runtime proofs. The `full` profile also reruns the release-version
test suite; `ci-reuse` requires the source qualification below instead.
The 180-minute job and bounded 40-minute transfer limits apply;
worst-case work does not guarantee diagnostic delivery before cancellation.

Before strict route success, failures (including checkout or setup-uv failure)
produce no private diagnostic: the route check is skipped or fails, and every
private step remains unreachable. This is intentionally fail-closed. Failures
in preparation after route success may still use the approved diagnostic path.

Smoke includes the unchanged expanded-layer byte counter, including shared gzip
layers. It does not create or validate a release envelope or run receiver-side
load checks. The later full private attempt still supplies those boundaries.
The public command runner currently merges stderr with stdout for comparisons;
a benign tool warning can therefore fail smoke where production's stdout-only
capture would not. This is a disclosed fail-closed availability/parity limit,
not a reason to weaken the actual checks or retry with different bytes.

### Fresh canonical release snapshots

Each authority check lists the complete main/release-state/v* ref map twice.
Main, state and canonical version tags are fetched into private refs in explicit
batches (at most 128 refspecs and 32 KiB of refspec arguments per command).
Every fetched object ID and locally peeled tag is checked. The before/after maps
must match, including added/deleted/moved malformed-version v* names that are not
fetched. Concurrent publication therefore refuses the snapshot; there is no
cached authority, latest-tag shortcut or automatic retry.

A single 600-second monotonic deadline covers both listings, all fetch batches and
local snapshot validation. Expiry kills the transport process group, with at most
one additional second for reaping. Partial fetches and stale private refs cannot
qualify. Subsequent unchanged state/first-parent validation is outside this budget.
Private phase logs record snapshot and remote-release-check elapsed seconds.
Public checkpoints, receipts and release formats do not change. This is batching,
not source-CI reuse, and it does not imply a measured end-to-end speedup.

### FULL source CI reuse (source implementation; rollout held)

New dispatches require explicit `delivery_mode` and `verification_profile`.
`full` uses the unchanged v1 approval/envelope and performs no CI-proof API work.
`ci-reuse` is the UI default but requires a strict v2 approval with the source CI
run, attempt, artifact ID and archive SHA-256. A default is not approval. Existing
v1 records do not authorize omitted profile fields on new dispatches. Operators
with a v1 approval must explicitly choose `full`; the default does not upgrade it.

Only successful FULL **push/main** CI on the exact canonical merged source can
qualify. PR runs, dev images and old runs without the terminal artifact cannot be
relabeled. The terminal `ci-attest` job runs only for push/main; it is deliberately skipped
on PR runs without changing their substantive test gates. The producer also
refuses qualification for other or missing event/ref contexts before Git or API
work. It requires all 12 mandatory legs and 61 enforcing predicates from the same
attempt. Admission checks the exact job-name set from that same producer inventory,
without maintaining a second step inventory. The unit job allows 40 minutes for
35 minutes of conservatively summed bounded steps, including the scripts fake
contracts; the terminal job allows 10 minutes for 7 minutes of bounded steps.
These margins do not guarantee completion of unbounded work. Individual step
bounds and timeout/mutual-exclusion policy checks remain enforced. Its inventory is bound to the
whole reviewed `ci.yml` hash, including commands and pinned actions. Workflow
changes require an inventory/hash review. Failed or skipped mandatory checks
emit no FULL artifact; optional failed scan-report retries do not replace the
successful final enforcing scans. Carried-over jobs from an older attempt cannot
qualify a partial rerun. A successful terminal command without an artifact is not
FULL qualification. Substantive CI checkouts use `github.sha`.
The existing filesystem vulnerability scan remains mandatory, without a duplicate
scanner. Scripts gates also include `tests/fakes/test_fakes.py`.

Admission uses the attempt-specific `run_started_at`, not run creation/update or
a job-derived timestamp. One aware UTC sample must be nonfuture and at most 30
days after that start, inclusively and with subsecond precision. The approved
attempt must be latest at entry and at the final canonical run check. A newer
attempt in any state refuses. That final check is the last API operation before
atomic publication of the read-only proof. Later checks use frozen bytes only;
there is no CI refresh, retry or sliding freshness window.

The reader has a shared 120-second deadline and 64-call cap. Metadata is bounded
to 2 MiB per response and 16 MiB total; collections allow four 100-entry pages.
The ZIP is at most 1 MiB, with one exact JSON member of at most 64 KiB and an
expansion ratio at most 100. Only the canonical authenticated API endpoint can
delegate one HTTPS storage request. Every DNS answer must be ordinary public
IPv4/IPv6. Connections pin a numeric address and verify the original TLS host and
peer. Storage receives no bearer, cookie or proxy credentials; a second redirect
or failed response refuses without retry. DNS children and transport work share
the deadline. Streaming bodies, including connection-close framing, remain
bounded. Private evidence retains the exact age decision and latest-attempt
samples; public output remains coarse.

Only the proof run step is explicitly passed `GH_TOKEN`. The job's read-only
permissions still apply to its pinned actions; this is not step-level permission
isolation. The proof step runs before engine/buildx preparation and rejects
package/Aquacast/SSH credentials. Runtime bearer variables
are removed before generic environment capture and private child execution.
Failed proof admission prevents tests/builds/exports but leaves independently
approved report-only diagnostics reachable after strict route admission. Actions
step outcomes and skipped preparation remain visible; controller checkpoints do
not publish private proof, age or report content.

The controller checks the frozen proof against the proof step's SHA-256 and v2
approval. It retains that original file digest, rechecks the payload after fresh
gates immediately before sealing, and uses only the retained digest in the
envelope. It never rebinds changed proof bytes to a new digest.
Source gates are recorded as **reused**, never locally passed. Fresh
image builds, scans, SBOMs, archive checks and immutable-image runtime probes
remain required. Aquacast discovery must expose both `cmal_pool_pt` and
`cmal_small` with networking disabled; this downloads no model data and does not
claim scientific qualification. Canonical release checks and existing delivery,
capacity, load and owner-approval boundaries remain in force.

V2 release custody has exactly five files, adding only `ci-proof.json` to the
existing four-file release set. Diagnostics contain only `report.bin`.
Private diagnostic gate records can retain an attempted source reuse when proof
admission succeeded but later preparation or fresh image work failed. Those
records are not release qualification or proof custody at the receiver. The
public diagnostic owner receipt carries no proof hash or qualified source-reuse
assurance; it remains explicitly diagnostic-only. Coverage distinguishes
`canonical_ci`/`not_run_here` from local image gates. The owner receipt binds CI identity and proof hash. Receiver
admission and load use local proof checks, not API requests. V1 serialized fields
and encoding remain unchanged; old parsers reject v2.

The release assurance is: **Source CI reused; delivery gates only; release-version
suite not rerun**. Fresh image scans do not refresh the source scan's age.
No end-to-end speedup or sub-five-minute target is established by this work.
Operational use requires independent patch review and qualification. These source
changes do not authorize receiver installation, workflow publication, release
target reservation or deployment. An exact merged-main FULL run and separately
approved receiver rollout are required before operational reuse.
