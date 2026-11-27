---
status: S3-A COMPLETE_CUSTODY; S3-B COMPLETE_LOCAL; native-host preparation PENDING_OWNER_REVIEW; S3-C–D DRAFT
owner: deployment orchestrator
parent: 2026-11-26-source-inventory-admission.md
---

# S3 — qualify the retained-input inventory in the delivered image

**Deferred, 27 November:** the owner selected the
[small-deployment path](2026-11-27-small-deployment-validation.md). This broader
inventory qualification is no longer a prerequisite for it. Do not repeat this
project for each release; preserve its existing evidence and incomplete results.

## Outcome and boundary

Prove that the reviewed S1 metadata collector and S2 retained-input inventory work
inside the exact delivered application image. Use synthetic data on a disposable
Linux amd64 host, then remove its resources. This milestone neither inventories
the live application database nor deploys a replacement forecasting system.

The current candidate is release 0.1.1199, source `3f8a8b2f`, schema 0095: it matches
S2's source and the latest retained source-deployment handoff. Its source CI and
delivery runs succeeded. The retained archive, receipt and envelope are present;
read-only host checks match the owner-relayed archive size/hash and retained
metadata digests. Complete payload, CI-proof and runtime-bundle verification now
passes (S3-A result below). Installed-image checks remain required before fixture
execution; workflow success alone is insufficient.
Do not upgrade the source, publish another release or substitute current main/schema
0096 to obtain a test image. A changed source release requires an explicit new
identity and code-difference assessment.

## Reuse and remaining work

| Reuse | Required S3 work |
| --- | --- |
| S1: 54 local checks and real Prefect/worker qualification on 0.1.1197. | Bind the unchanged collector to the candidate image; check the affected worker/configuration path and identity refusals. |
| S2: 187 checks, schema-0095 production-reader parity, version-3 report and reviewed native-read mapping. | Run the complete supervised inventory with real installed constructors and synthetic native evidence. Host source tests alone do not qualify an image. |
| F4: image receipt verification, installed-source inventory, bounded launcher and owned-resource cleanup. | Copy the existing adapter into a new private directory and update explicit inputs/scenarios. Preserve the original qualified helpers and evidence. |
| Existing SQL guards, cumulative accounting and process limits. | Recheck affected runtime boundaries; change no guard, scientific calculation, migration, model, credential delivery or public command. |

Private work lives under a new qualification directory. Its `qualify_release.py`
adapts the F4 receipt/image checks; `rendered-driver.txt`, `fixture_runtime.py` and
`qualification-child.txt` retain the established fixture/launcher boundaries.
`input-manifest.json` names every loaded S1/S2/adapter module by exact path and final
hash. The standard-library `launch.py` verifies the complete manifest before either
entry path imports helpers; direct S1 script execution refuses. Use a separate output
directory for each attempt. Do not edit completed S1,
S2 or F4 directories, or treat a hash refresh as qualification.

## Sequential slices

| Slice | Work | Exit criteria |
| --- | --- | --- |
| S3-A — freeze inputs | Retain original build/delivery receipts, exact archive and image identities, source CI proof and matching source checkout. Verify bytes and metadata only; do not load or execute an image. | Archive bytes/hash, envelope, receipt, runtime bundle, source/tag and the default and Aquacast image IDs agree with independently retained evidence. Missing custody holds runtime. |
| S3-B — prepare the adapter | Copy and amend only the private qualification adapter, fixture and version-3 expectations. Keep source identity separate from fixture identity. | Unit tests, lint, changed-module types and one independent relevant patch review pass before runtime. A layout-parametrized encoder check covers synthetic stand-ins, maximum counters and partial HOLD states; it does not infer the actual cohort. |
| S3-C — isolated runtime | After custody, actual-cohort capacity and resource-packet admission, use one temporary native amd64 host, fresh synthetic credentials and an internal test network with no provider access. First load the verified archive and check the installed inventory and every S2 pin before configuration, constructor or fixture calls. | Every listed positive/refusal outcome matches; child/process group and DB backend cleanup are verified; final identities match. No incomplete or truncated result becomes PASS. |
| S3-D — close out | On success or failure, remove owned containers, network and temporary host/access resources; retain private evidence and update the generic guide. | Provider-side readback confirms owned resources removed. Record PASS, BLOCKED_CAPACITY or the failed gate, with exact image/schema/paths and limitations. Cleanup alone never makes qualification PASS. S4 remains separately admitted. |

S3-A/B may be prepared while archive custody and cohort evidence are being obtained.
The owner accepted the review corrections on 2026-11-26. The orchestrator admits
S3-A custody preparation and S3-B local adapter implementation only. This includes
bounded read-only retrieval of the named release artifacts through the existing
authenticated path after recording the concrete transfer packet. It does not
admit image loading, fixture runtime or cloud provisioning. Resolve any new material
findings with the owner; there is no automatic review loop.
S3-B uses the frozen input contract and synthetic stand-ins; its exit does not
depend on receipt delivery or the source owner's complete cohort handoff.

The owner accepted S3-B's corrected preparation through PR #689. The
[native-host transport amendment](2026-11-26-inventory-native-host-transport.md)
now covers the remaining explicit socket, same-host fixture paths, installed
Prefect template and frozen controller environment. Its T1 is local preparation
only, with 347 passing checks and author corrections to its independent patch
review findings awaiting owner disposition. The complete source-owner comparison
list has been requested; runtime still
requires that whole layout, the capacity check and a concrete resource packet.

### Complete cohort and report capacity

The fixture cohort is synthetic. It is not permission to shrink, rename or expand
the operational cohort. Freeze the intended station/model/job mapping and every
included/excluded disposition privately before runtime admission. Exercise the
version-3 encoder with that entire member layout and counts bounded by the existing
row/byte limits, including partial HOLD states. Every admitted report must fit
16 KiB without truncation or per-job splitting to evade the cap.

A preparation-only encoder check found that 17 synthetic native-read members fit
(12,181 bytes with zero counters), while 34 returned the existing output-limit HOLD.
This is not proof for the actual cohort or its maximum counters. If the real packet
does not fit, stop and return that concrete finding for an owner decision; do not
introduce a new codec, increase the limit or silently select one model/job here.
Record this as `BLOCKED_CAPACITY`, not PASS, and do not provision the test host.
The actual-cohort preflight is an S3-C entry gate, not an S3-B exit requirement.
Multiple source jobs still require S4's explicitly reviewed whole-cohort snapshot
and identity composition; separate qualification scenarios do not supply that proof.

### Synthetic scenarios and meaningful assertions

1. **Declared native route:** real installed constructor/declaration, observed
   provisional mode and one scoped binding. Seed ordinary measured water levels
   and rating evidence through existing stores, reconcile only in the disposable
   fixture, and retain the database-assigned recording times. The supervised
   inventory must query native candidates, preserve the stored-discharge omission,
   complete the shared root/capture closure and report exact expected counts.
2. **Mixed coverage:** include ordinary reads, native reads, a deliberate omission
   and an empty-authority member. Shared observations count once in the same
   snapshot/ledger; aggregate dispositions remain visible. A genuinely queried
   zero differs from an unqueried empty window. Pending members prevent PASS.
3. **Current-evidence refusals:** exercise standard mode with a configured but
   inactive rating binding, unreconciled/stale evidence, wrong image/code identity
   and a read outside the admitted contract. Use typed closed refusals and explicit
   partial coverage; no scientific fallback or smaller scope. Perturb expected
   image IDs or pins to test identity refusals; never modify the delivered image.
4. **Runtime controls:** keep a positive metadata/worker selection check and the
   misleading launch-selection refusal on the candidate image. Recheck worker-role
   read-only repeatable read, query rejection, bounded output and a timed-out child
   with process-group/backend cleanup. Reuse unaffected S1/F4 tests rather than
   repeating every prior benchmark. Any changed launcher/transport invalidates the
   corresponding reused evidence until reviewed and tested.
5. **Oversized report refusal:** a synthetic layout within the member cap but
   exceeding 16 KiB must produce exactly the existing `OUTPUT_LIMIT` HOLD in the
   installed image. This qualifies refusal behavior, not successful inventory of
   that layout. An oversized actual cohort still blocks S3-C admission.

S3-B must also reject a stale module-manifest entry and a mismatched bundle digest
before loading any helper module. A single S2 implementation-pin mismatch must
refuse before configuration, constructor or arithmetic calls. Recheck the F4
launcher against the new bundle rather than reusing its old-bundle result.

Choose fixture issue/validation clocks explicitly around normally inserted recording
times, with observations inside the declared physical windows and binding authority.
Do not backdate rows, disable triggers, modify the image or infer original arrival
from migration 0095. Include nonempty before/after recording-bound cases; equality
and reducer semantics remain covered by S2's pinned-reader tests and must be rerun
if their implementation differs. No reducer, tensor assembly or model prediction
is added to the inventory. Source forecasts' runoff consumption is still a separate
operational acceptance check.

### Limits, custody and cleanup

Keep the existing 128-member, 64-KiB SQL, cumulative row/256-MiB byte, 16-round
closure, 8-second statement, 1-second lock, 55-second child/65-second controller,
2-GiB address-space, 30/35-second CPU and 16-KiB output ceilings. S1 retains its
documented metadata/file/request caps. Fixture setup has its own bounded budget;
it must not extend an inventory deadline or reset accounting between members.

Keep archive custody separate from installed identity. The receipt's `default`
and `aquacast` entries contain immutable local Docker image IDs, not registry
digests; retain the envelope's separate configuration digests without substituting
them for those IDs. Cross-check both variants, load only the verified archive and
run the selected Aquacast image by its expected immutable ID. Confirm Linux/amd64,
release labels, installed-source inventory and S2 pins after loading. Do not pull
or rebuild an application image or select it by a mutable tag.

The private transfer packet must name the exact release files, destination, byte
and time bounds, and the permitted read-only retrieval method. Reuse the existing
authenticated SSH path without new source keys, rules or permissions; if its
authority or method is unresolved, S3-A holds at in-place hashes. Expected archive,
receipt and envelope hashes come from independently retained original delivery
evidence, with the source CI proof checked against its retained artifact. A relay
or a second hash from the same host is not independent delivery proof. This plan
does not grant broader source access.

Reuse the approved temporary-host pattern: native amd64, encrypted disposable disk,
automatic termination backstop and a two-hour maximum host lifetime. Freeze the
concrete provider/resource/transfer packet before provisioning; retain owner tags
and exact resource IDs. Verify the termination backstop before transferring or
loading images. Transfer only the verified release bundle and synthetic
fixtures. Do not copy agency data, source secrets or source configuration. Prefer
the existing transfer path; a new access rule or temporary credential on the source
requires its own explicit authorization. Do not recreate or reuse terminated hosts.

On any failure, retain bounded private evidence, stop that attempt and clean up
exactly owned resources. Never broaden scope or limits to turn a failure into a
pass. Verify that diagnostic DB backends are gone before removing their containers,
and destroy the fresh synthetic credentials. Recheck deletion of the host, disks,
addresses, network interfaces, security
groups, temporary keys and tunnels that this attempt created. Cleanup uncertainty
leaves the milestone incomplete.

The concrete S3-C packet must bind the separately prepared native-host transport
to the actual host and record its observed platform. Local tests of its socket,
mount and dependency checks do not establish installed Linux behavior.

## Documentation and final acceptance

Update [the comparison guide](../deployment/operational-comparison.md) only with the
qualified runtime and what its result proves. Keep private instructions, full
reviews, agency identities and detailed evidence outside Git. Link the existing
[S2 result](2026-11-26-retained-input-inventory.md#s2-local-result--2026-11-26)
and [source-admission plan](2026-11-26-source-inventory-admission.md); do not duplicate
their configuration or scientific reference material.

S3 success means the listed synthetic, single-job-layout scenarios pass on native
Linux amd64 in the exact receipt-bound 0.1.1199 image. Native amd64 is new evidence:
S1 and S2's local runtime checks used amd64 emulation on an arm64 host. The actual
cohort's encoder preflight establishes report capacity only, not successful live
execution. S3 does not prove every layout up to the 128-member cap, multi-job
identity composition, complete source data, usable model inputs, historical
consumption, weather/tensor coverage, restoration or a finished fresh deployment.
S4 still needs a current source-owner handoff, identity/capacity observations and
separate execution admission. No release or operational commissioning follows
automatically from this milestone.

```json
{"phases":[{"id":"S3-A","status":"COMPLETE_CUSTODY","depends_on":["S1_COMPLETE_LOCAL","S2_COMPLETE_LOCAL","independent_plan_review"]},{"id":"S3-B","status":"COMPLETE_LOCAL","depends_on":["S3-A_input_contract"]},{"id":"S3-C","status":"DRAFT","depends_on":["S3-A_custody_complete","S3-B","native_host_transport","actual_cohort_output_preflight","independent_patch_review","concrete_resource_packet"]},{"id":"S3-D","status":"DRAFT","depends_on":["S3-C_attempt_ended"],"required_after_failure":true}],"capacity_failure":"BLOCKED_CAPACITY","source_execution":"NOT_ADMITTED","reconstruction":"NOT_ADMITTED"}
```

## Independent review disposition

One independent Claude plan review supported custody and adapter preparation after
clarifying their boundaries; it did not admit runtime. This revision separates
byte verification from image execution, moves the actual-cohort capacity check to
runtime admission, makes capacity failure explicitly blocking, defines receipt
image identities and transfer custody, and adds concrete hash/pin, output-limit
and cleanup assertions. No review loop or additional reviewer was commissioned.
The full report remains private. Following owner acceptance, only S3-A/B were admitted;
S3-C/D runtime and S4 source execution retain their separate gates. No implementation
or runtime is admitted by the review verdict alone.

## S3-A custody result — 2026-11-26

The retained 0.1.1199 archive, receipt, envelope, runtime bundle, report and CI proof
were copied through the existing read-only artifact path. Independent retained
digests matched. The existing release verifier passed both image mappings, labels,
platforms and archive contents; runtime-bundle files matched the exact source and
the frozen CI proof matched the retained source-CI artifact. Assurance remains
source CI reused plus fresh delivery gates; the release-version suite was not rerun.

No image was loaded or executed, no server was provisioned, and no live application
data, configuration or schedules were accessed. Installed-source checks and runtime
qualification remain S3-C work. The complete intended cohort is still required.

## S3-B local preparation — 2026-11-26

The private adapter and synthetic scenarios are prepared against the pinned release
source. One independent patch review requested corrections to fixture authority
windows, the deliberately empty window, timeout-backend evidence, conservative
report sizing, complete mixed-coverage assertions and import ordering. The author
implemented those corrections and added focused regressions: 256 inventory/adapter
tests and 54 collector tests pass, with lint and changed-module strict types clean.
The original S1/S2/F4 inputs remain unchanged. No second review was commissioned;
the review's original changes-required verdict and correction evidence are retained
privately. The owner accepted the corrected preparation through PR #689; this is
not reviewer approval of the revised bytes.

Preparation now covers full planned custody intervals, genuinely empty off-grid
authority, an identified worker backend before timeout and its absence afterward,
exact ordered member/count/coverage assertions, and the longer excluded-member
classification at the report-size boundary. Local checks exercise the refusal and
assertion logic; actual backend cleanup and installed-image outcomes remain untested
until S3-C. No server, container, provider call, source inventory or reconstruction
was performed. The separate transport review disposition, complete cohort capacity
check and concrete native-host execution/cleanup packet still precede runtime
admission.
