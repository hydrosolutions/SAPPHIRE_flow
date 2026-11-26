---
status: READY — S1 locally qualified; S2–S4 DRAFT
owner: deployment orchestrator
parent: 2026-11-22-operational-deployment-recipe.md
---

# Admit the current source to the private inventory

## Outcome and boundary

Prepare one private, reviewable execution packet for the existing bounded inventory.
The maintained release 0.1.1197 passed the constructor/runtime qualification, but
the observed source now runs 0.1.1199. Its declared-grid runoff behavior and schema
have changed. Preserve the completed test; do not refresh code hashes and call its
old omission policy qualified for the newer source.

The owner chose comparison with the current operational setup once its model-input
problem is resolved. Configuration, cohort, model artifacts and scientific choices
stay outside Git. No new profile catalogue, public command, model implementation,
deployment activation, provider acquisition or source write is part of this plan.

## Existing capabilities and missing pieces

- Existing: independently verified release delivery, selected constructor/FI
  declaration guards, typed packet/report, bounded SQL accounting, worker-role
  read-only snapshot, cancellation and cleanup. The maintained-image test covers
  three synthetic scenarios and ten expected outcomes; 183 helper checks pass.
- Missing: the production source collector. The tested collector explicitly uses
  synthetic job/controller records. Relabelling those records is not source proof.
- Changed: the [declared-runoff implementation](2026-11-25-model-declared-runoff-inputs.md)
  admits period-aware rating input and recording-time availability. The old private
  planner admits declared targets only as deliberately omitted. It must refuse the
  newer route until a separately reviewed read mapping is qualified.
- Pending externally: successful current-route qualification and the exact intended
  cohort/artifact/job handoff from the source-deployment owner. A running worker,
  completed flow state or old forecast is insufficient evidence of input consumption.

## Milestones

| Milestone | Concrete work | Acceptance and dependencies |
| --- | --- | --- |
| S1 — private metadata collector | New private `source_identity.py`, `source_controller.py` and focused tests; reuse packet types and the maintained forecast-configuration checker. | Independently observed container/image/process/job/config facts; no application DB, model constructor, artifact bytes or provider access. Local implementation and isolated real Prefect/process qualification after plan/patch review. No source execution yet. |
| S2 — current read contract | Private amendment to `planning.py`, `roots.py`, `guarded_census.py`, `metadata.py` and the input manifest, only where the current route requires it. | Trace the exact installed route and schema before proposing changes. Distinguish measured roots, retained rating inputs, recording-time availability and physical periods. Independent plan review before implementation; retain whole-cohort caps and existing exclusions. |
| S3 — matching-runtime qualification | Reuse the maintained-image qualification adapter and frozen helper suite against the exact admitted current image. | Independent release custody; S1/S2 patch reviews; positive current-route synthetic cases, expected refusals, worker-role SQL/supervisor evidence and cleanup. No source/target mutation. Reuse unchanged evidence; do not repeat every historical benchmark. |
| S4 — source admission and inventory | Private expected packet, collected facts, exact invocation and aggregate report. | S1–S3, source-owner handoff and explicit orchestrator admission. Observe preflight, run one bounded read-only snapshot, then recheck identity and cleanup. Drift or missing evidence produces HOLD; no automatic retry or reduced scope. |

S1 can proceed while the source owner completes operational validation. S2 starts
as a design amendment, not permission to implement unreviewed scientific mappings.
This plan admits no B1/B2 source execution, reconstruction or commissioning by itself.

## S1 collector contract

Keep expected facts and observations separate. Expected image/source/code hashes
come from the independently verified release; expected jobs, cohort and artifact
metadata references come from the admitted private recipe and source-owner handoff.
Never derive expected values from the same observation being checked. New station,
model or artifact bindings require an explicit packet disposition.

The exact expected release must have independently retained delivery-receipt
evidence. A different observed release, raw image ID or implementation hash refuses,
even if it is newer. The collector never selects a release or refreshes its pins.
Test expected 0.1.1197 against observed 0.1.1199 as a refusal.

Keep a typed controller-observation envelope around the existing packet. Its closed
execution provenance distinguishes `LOCAL_QUALIFICATION` from `SOURCE_OBSERVATION`;
the controller assigns it, never a request field. Source acceptance requires the
admitted source endpoint, collector/helper hashes and independent observed facts,
not merely that marker. Reject synthetic job/controller records at the source
boundary and reject local-qualification evidence at S4, including relabel attempts.
S1 tests produce only a locally qualified collector, never source evidence.

Inspect only the named deployment, pool/template and admitted containers/processes.
Use GET-only Prefect metadata operations, bounded Docker inspection and bounded
in-container reads as the actual service user. Parse external objects once into
frozen domain values. Reuse existing configuration loading/checking at its documented
boundary; do not infer effective configuration from file names or a Compose render.

Each invocation selects one deployment and one corresponding worker/child path.
It reports only objects named in the expected packet. Unexpected relevant objects
produce a count and refusal, never expanded membership. Unrelated processes do not
become new cohort members. Repeat the identity/job/config observations before
accepting S1's result; a changed, missing or ambiguous identity refuses. Separate
jobs may need separate identity observations, but S4 still uses one whole-cohort
database snapshot and cumulative limits.

| S1 boundary | Fixed maximum per invocation |
| --- | --- |
| Selected metadata objects | 2 named containers, 1 deployment, 1 pool/template, 1 explicitly identified flow run |
| Prefect operations | 6 exact-ID GET requests including final rechecks; no listing/filter endpoints, pagination or redirects |
| Docker operations | 12 inspect/top/exec operations including final rechecks; no container create/start/stop/copy calls |
| Process inspection | 64 process-list entries, 8 ancestry levels, 8 selected process reads; refuse ambiguity/PID reuse |
| Response body | 1 MiB per HTTP/Docker object, 8 MiB cumulative control responses, counted while streaming |
| Process environment | 128 entries and 64 KiB per selected process; values stay in host memory |
| Nonsecret configuration | 32 admitted files, 1 MiB each, 16 MiB cumulative |
| Installed source inventory | 1024 admitted files, 16 MiB each, 32 MiB cumulative; expected paths and hashes frozen in the release packet |
| Result | 256 KiB private typed packet and 16 KiB closed summary; neither contains credentials or their hashes |
| Time | 65-second shared controller deadline, 5-second request deadline, 55-second child deadline; no additive per-call reset or automatic retry |

Exceeding any cap refuses before materialization or output; no truncation-to-success.
Qualification setup/cleanup uses the existing isolated-fixture envelope separately.
S1 reads no application data, retained-input timestamps or application database;
SQL budgets are unused and any application database access is a test failure.
The prior installed application/model/interface source inventory contains 667
Python/YAML files and about 7.3 MB; this informs the file cap but does not qualify
the current installed inventory. A current release above the cap still refuses.

Bind container start identity, image, installed package/source inventory, ordered
nonsecret configuration bytes and relevant selectors, deployment parameters and
launch overrides. Verify read-only source/config mounts and effective UID. A Docker
exec probe has its own parent; do not mistake that parent for the Prefect worker.
Identify the actual worker and actual forecast child using their process ancestry,
start identity and matching Prefect run/deployment identity. Registered per-run
environment values alone cannot prove the child's selection. If no current child
or independently retained same-identity child proof is available, report that gate
as unverified; do not launch a forecast to manufacture it.

Whole environments, credentials, raw rendered Compose, arbitrary job fields and
provider file contents must not leave the host. Selectors/configuration evidence
must be classified as nonsecret before hashing/export. Secret interpolation is
checked in host memory and yields equality/refusal only: no secret values or hashes.
Private identifiers and configuration hashes stay in the private packet; public
output contains only closed statuses, counts and release identifiers. Unknown
fields, oversized responses, unreadable files and partial reads refuse. Do not
save helper files on the source or change permissions to make a read succeed.

The private recipe explicitly classifies each admitted nonsecret config file and
selector. Export only the three configuration selectors and the distinct forecast
configuration selector documented by the existing loader, parsed as canonical
admitted paths/closed flags. No free-form selector value qualifies. Other keys
produce only required presence/equality checks in memory, not names or digests.
An unclassified file, unexpected interpolation dependency or credential-bearing
value refuses export. Test a secret sentinel placed in an otherwise allowlisted
key; an allowlisted name alone must not authorize its value.

Use an explicit allowlist of endpoints/fields and fixed command arguments, with
bounded response bodies and request deadlines. Build the local qualification with
synthetic credentials and real Prefect process workers. Prove worker overrides,
launch overrides, absent/ambiguous children, PID reuse, container restart, changed
configuration order/bytes and before/after job drift. Include secret sentinels in
environment, errors and unknown fields; none may reach output or evidence.

The source execution adapter must preserve the existing 65-second control budget,
5-second request deadlines, 55-second child deadline, 2-GiB address-space ceiling,
30/35-second CPU limits and SQL/row/byte ceilings. Collect only admitted metadata;
do not turn the preflight into an unbounded survey. At admission specify operational
memory reserve as a required positive packet value with no default, and require
the full probe allowance plus that reserve in both host and selected container.
An absent reserve or insufficient headroom yields HOLD without retry. A free-memory
sample is not a reservation; recheck at start, and never pause/restart workers or
widen limits to obtain capacity. The source owner settles the operational reserve
before S4; local tests use explicit synthetic reserve values and boundary failures.

## Current-route and reconstruction dependencies

S2 must follow the actual release's declaration, rating binding and reader branch.
Reuse its period calculations and availability rules; do not reimplement the model,
shift timestamps, widen reconciliation authority or infer target windows from the
weather-ingestion schedule. Trace the migration's complete column/trigger/reader
changes and update the private ledger before treating any category as covered.
The old 35-table ledger is a starting inventory, not proof of the new schema.

`recorded_at` now affects whether a forecast may consume a retained input. It is
scientific input evidence, not the renewable bookkeeping covered by the owner's
earlier temporary-comparison exception. Preserve its meaning in the manifest.
How to restore that evidence through the existing insert protections is a later
reconstruction decision; no backdating, trigger bypass or new preservation-policy
exception is authorized here. Keep reconstruction blocked until resolved and tested.

Weather contents, final tensors, protected/history closure, trained artifact bytes
and forecast skill retain their explicit unchecked status. Source model validation
must use an eligible issue instant; evidence recorded after a historical issue
cannot qualify that issue. The source owner runs and evaluates that validation;
deployment preparation does not create runs or change schedules.

## Reviews, documentation and exit

One independent Claude review covers this plan and its administrator implications.
One relevant independent patch review covers each implemented slice; reports remain
private. Resolve material findings with the owner before admitting the affected
work. The orchestrator alone records READY and the exact admitted slice.

Update the existing [comparison guide](../deployment/operational-comparison.md)
with the operator consequence and link the maintained configuration/model references.
Keep source identity, full reviews, diagnostics and execution history private;
record only concise generic outcomes in this plan and its parent. A code PR requires
affected tests, lint, changed-module types and the full final suite locally or in CI.

```json
{"phases":[{"id":"S1","status":"COMPLETE_LOCAL","depends_on":["independent_plan_review"]},{"id":"S2","status":"DRAFT","depends_on":["current_route_design_review"]},{"id":"S3","status":"DRAFT","depends_on":["S1","S2","independent_patch_reviews","verified_current_image"]},{"id":"S4","status":"DRAFT","depends_on":["S3","source_owner_handoff","execution_packet_admission"]}],"source_execution":"DRAFT","reconstruction":"DRAFT"}
```

## Independent review disposition

One independent Claude review requested concrete collection limits and an explicit
source-versus-test evidence boundary before S1 implementation. The revised contract
adds those limits, controller-owned provenance with independent source checks,
exact-release refusal, before/after identity checks, a mandatory reserve value and
explicit nonsecret selector/file admission. No repeat review was requested.

The owner accepted proceeding with the isolated read-only checker on 2026-11-26,
after the review requirements and scope were explained. The orchestrator admits
S1 local implementation and isolated qualification only. Patch review must complete
before runtime qualification. S2–S4, source execution and reconstruction retain
their separate gates. The PR remains draft at the owner's explicit request.
The full review and detailed evidence remain private.

## S1 local qualification — 2026-11-26

The private collector passed its isolated real-Prefect/process test against the
independently verified 0.1.1197 image: worker inheritance and the actual launch
override passed; the misleading advertised selection refused; secret-error output
was redacted; the timed-out probe was reaped and its process group removed.
All five outcomes matched. The final helper suite passed 54 checks, Ruff and strict
changed-module types. One independent patch reviewer covered the implementation
and the bounded transport/cleanup corrections; reports and failed-attempt evidence
remain private. No collection limit was widened.

The test used Linux amd64 emulation on a local arm64 Docker host, with synthetic
configuration, an internal network and no application database or provider access.
Owned containers and the network were removed and verified. Concurrent Docker
activity changed the shared host baseline, so this is not a whole-host
unchanged-baseline assertion or native current-source qualification.

No source inventory, live forecast, deployment change or reconstruction occurred.
S2–S4 retain their separate gates. The administrator consequence is documented in
the [comparison guide](../deployment/operational-comparison.md); this private helper
is not a new public operator command. The PR remains draft.
