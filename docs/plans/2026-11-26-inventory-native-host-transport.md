---
status: T1 PREPARED_PENDING_OWNER_REVIEW; T2–T3 DRAFT
owner: deployment orchestrator
parent: 2026-11-26-current-image-inventory-qualification.md
---

# Native-host transport for the private inventory rehearsal

## Outcome and boundary

Finish the host connection and filesystem preparation for S3 without changing its
scientific readers, report format or limits. PR #689 is merged: custody and local
adapter preparation are accepted. The complete intended comparison list has been
requested from the source-deployment owner and remains an entry gate for runtime.

This amendment admits no cloud resource, image load, source access, reconstruction
or deployment. Its independently reviewed local implementation may proceed while
that handoff is pending. Only the orchestrator can mark that preparation READY;
S3-C runtime still requires all gates in the parent plan.

## Smallest implementation

Copy the accepted private preparation into a new transport directory, preserving
the original inputs and evidence. Both host controllers will execute directly on
the disposable native Linux amd64 host. Their fixture paths therefore exist on
the same machine as Docker's bind mounts. The Docker socket stays on the host;
it is never mounted into an application container.

1. Make the standard-library launcher require an explicit local Unix Docker socket
   for both entry paths. Validate mode arguments and the full frozen manifest before
   importing an adapter. The private native-host mode accepts the canonical host
   daemon socket only, with no environment-derived, TCP or SSH endpoint fallback.
   Use `/run/docker.sock` after resolving the normal `/var/run` directory alias;
   require a root-owned Unix socket without world write access. Ignore Docker/proxy
   environment selectors in explicit SDK/CLI calls and pin their API version.
2. Before resource creation, require native Linux x86-64 on the controller host
   and Linux/amd64 from Docker. Verify the engine identity remains the admitted one.
   Keep the installed image inventory and every S2 pin ahead of fixture/configuration
   or constructor execution. Preserve distinct S1/S2 Python module namespaces.
   Capture the new daemon ID and platform at preflight, then require the same values
   before resource mutation and cleanup. No prior host ID is reused. Refuse rootless
   or user-namespace-remapped engines because this fixture requires UID 1000 mapping.
3. Obtain the process-worker job template using the existing bounded exec channel
   inside the verified application container. This uses its installed Prefect
   instead of importing Prefect on the controller host. Reuse the existing real
   worker-inheritance and launch-override scenarios and constructor checks.
   Require Prefect 3.6.25, the expected template shape, one JSON document and empty
   stderr. The existing channel already separates streams; stop ignoring template
   stderr. A timeout ends the attempt and removes the tainted owned container.
4. Bind the new transport manifest explicitly to its final private directory now;
   retain that same absolute path on the disposable host, without a remap option.
   Verify copied core content hashes against accepted inputs. Keep synthetic bind
   sources in a separate, nonce-owned temporary directory, with traversable ancestors
   and read-only files; retain private helpers/evidence under the private parent.
   Require their exact bytes to be readable inside the worker, inspect the expected
   read-only mount sources/destinations, and check final host hashes. Fail before
   diagnostic execution on a mount/path/content mismatch. Record actual platform
   evidence; remove the inherited claim that execution was locally emulated.
   Reject symlinks or changes in file identity, mode, size or hash during the attempt.
5. Freeze a minimal controller environment using `uv`: Docker SDK 7.1.0 and
   Pydantic 2.12.5, both already present in the pinned release lock, plus their
   locked transitive dependencies from the existing PyPI source. Extract compatible
   Linux x86-64 / Python 3.12 wheel hashes directly from the existing release lock;
   no fresh resolution. Install with required hashes, no dependencies outside that
   closure and wheels only, into a root-owned environment. The controller runs as
   the temporary host's operator user with Docker access, which is root-equivalent
   on that disposable daemon. The application
   remains in its unchanged delivered image and Python environment. Freeze the
   bootstrap, transport inputs and environment separately from the application
   receipt and retain their hashes in the execution packet.

Load private helper modules from the verified in-memory bytes, reusing the existing
bootstrap loader pattern. Do not load helper bytecode caches or reread source after
the gate. Register the controller under its canonical module name: its rendered
driver imports that same module and must retain the verified state. Launch the host
interpreter with `-I -B`. This does not make Docker-authorized controller code a
sandbox; its authority is limited operationally to the fresh disposable host.

Use a small shared host preflight only where both controllers need the same
checks. No new deployment framework, daemon service, model, schema, dependency
source or public operator command is introduced.

## Sequential slices and acceptance

| Slice | Work | Exit evidence |
| --- | --- | --- |
| T1 — local transport preparation | Explicit socket, native-host preflight, same-host fixture verification, installed Prefect template and frozen controller environment. | Changed behavior tests, all existing local checks, lint, strict changed-module types and one independent relevant patch review. No Docker/cloud execution. |
| T2 — runtime admission packet | Bind the actual whole comparison layout and its fixed-capacity result to the final helper/environment manifests; freeze provider, resource, transfer and cleanup details. | Complete private source-owner handoff, capacity PASS, accepted review findings, immutable image identities, final concrete packet and orchestrator admission. |
| T3 — isolated execution | Run parent S3-C scenarios on the admitted native host, then parent S3-D cleanup. | Exact expected scenario outcomes, before/after identities, worker/process/backend cleanup and independent provider-side resource deletion readback. |

T1 tests must prove missing/unsupported sockets and non-native engines refuse
before fixture resources; a changed helper refuses before imports; the launcher
passes the selected endpoint to both controllers; changed template/mount/file
evidence refuses; and cleanup evidence describes the observed platform. Exercise
the changed controller entry paths with bounded fakes, not merely their helper
functions. Retain the 310 accepted local checks and the unchanged core hashes.
Add a successful launcher-path test that proves controller state is shared with its
driver, cached/changed helper refusal, and host-Prefect import rejection. Verify
timeout and signal paths reach bounded owned-resource cleanup with platform evidence.
Host tests do not prove Linux mount permissions or installed behavior; those are
explicit T3 outcomes. No new database or image is needed for T1.

## Concrete execution packet requirements

Reuse the previously qualified disposable AWS Frankfurt pattern: native amd64,
an encrypted 64-GiB disposable root disk, no instance role, one fresh test key and
owner-tagged resources. The prior instance class was `m7i.xlarge`; verify current
account, image ownership/architecture/availability, subnet and instance availability
read-only when finalizing the packet. Never reuse terminated resource identities.

The source-owner list stays local and is used only for capacity admission. Transfer
the already verified local release files, frozen private helpers and synthetic
fixtures directly to the new host. Bound transferred paths, bytes and time; verify
hashes again on arrival. No agency observations, model binaries, source configuration,
source credentials or cloud credentials go to the host. No new access rule/key on
the existing source deployment is needed.

Before transfer or image loading, verify shutdown-to-terminate behavior and the
automatic termination timer. Keep the parent's two-hour maximum host lifetime,
including bootstrap, transfer, execution and cleanup; reserve cleanup time and
stop execution when that reserve starts. Record absolute deadlines in the concrete
packet. The host controller has Docker authority only over this fresh disposable
daemon. Application/worker containers retain their existing non-root, read-only,
capability, network and resource restrictions. Do not expose application or Prefect
ports. Package installation is host bootstrap traffic; fixtures have no provider
access. Fix bootstrap package versions and provenance in the final private packet.

Detach the runner from SSH using a transient systemd unit. Arm the host termination
timer early in boot, and give the runner a monotonic deadline below the cleanup
reserve, a finite stop timeout and control-group killing as a last resort. Translate
termination/interruption signals into the existing cleanup path. Reuse the existing
bounded Docker request budget for both controller paths, including a separate finite
cleanup allowance. Forced termination is a failed qualification; independent
provider cleanup remains mandatory even when the controller cannot finish.
Bound attached exec output consumption as well as the HTTP request: the Docker
SDK's socket reader can otherwise wait indefinitely after the request returns.
Use a finite main-thread timer around the complete exec call, including the
shorter cleanup allowance. Carry the engine guard into separately created
diagnostic clients before exec creation and start.

On every exit, collect bounded evidence, verify diagnostic backends have disappeared
before database-container deletion, and remove only the owned containers/networks.
Then terminate the instance and verify disappearance of its disk, interfaces,
temporary address, security group, key and any local access material. Preserve
baseline resources; a cleanup uncertainty remains a failed gate. The automatic
backstop complements explicit cleanup and does not count as a cleanup observation.

## Documentation and limits

Keep environment locks, scripts, execution inputs and full reviews private. Update
this plan and the parent's status with concise outcomes. Update the administrator
comparison guide only when a new procedure or qualified result changes what an
administrator needs to do. No status-only PR or repeated configuration reference
is needed. Actual source inventory and operational commissioning remain separate.

```json
{"phases":[{"id":"T1","status":"PREPARED_PENDING_OWNER_REVIEW","depends_on":["independent_Claude_plan_review"]},{"id":"T2","status":"DRAFT","depends_on":["T1","complete_comparison_handoff","whole_layout_capacity","accepted_patch_review","concrete_resource_packet"]},{"id":"T3","status":"DRAFT","depends_on":["T2","orchestrator_runtime_admission"]}],"cloud_provisioning":"NOT_ADMITTED","source_execution":"NOT_ADMITTED","reconstruction":"NOT_ADMITTED"}
```

## Review disposition

One independent Claude review completed. Its corrections are incorporated for
socket/engine identity, frozen paths, template parsing, wheel/loader integrity and
interrupt cleanup. A separate synthetic fixture tree keeps the mount contract
simple; the host parent is not itself mounted. The new daemon ID is captured after
boot and pinned for the attempt, rather than inferred from a previous host. The
existing exec channel already demultiplexes streams; only its missing strict stderr
check needs changing. Full reports remain private. The orchestrator admits T1 local
implementation only under the owner's instruction to proceed; owner review of the
result remains ahead of T2/runtime. No extra review loop was commissioned.

## T1 local result — 2026-11-26

The private transport is implemented with 41 frozen inputs and an eleven-package
controller wheel set extracted from the existing release lock. The original
accepted preparation and all eighteen inventory core modules remain unchanged.
The launcher loads captured helper bytes under their canonical module names,
preserving verified controller state when its driver imports that controller.

One independent patch review found malformed embedded Python after a cleanup
refactor, unbounded attached SDK output, and missing engine checks in separate
diagnostic clients. The author corrected all three. A bounded controller rehearsal
now executes the exact dispatched sources through template, mount, process and
diagnostic stages. A real SDK/socket regression proves stalled cleanup output
times out, keeps the result HOLD and permits remaining resource cleanup. Engine
drift tests refuse both exec creation and start.

All 290 inventory/adapter/transport checks and 57 collector checks pass after the
final code change; Ruff and strict changed-module types pass. This is local test
evidence, not installed-image qualification. The review's original HOLD verdict
and correction evidence remain private for owner disposition; no second review
was commissioned and no reviewer approval of revised bytes is implied.

No host was provisioned, image loaded, source accessed or reconstruction performed.
The complete comparison list is still pending. T2 must also bind the whole-suite
deadline: eight per-scenario budgets and a separate collector budget do not by
themselves enforce the two-hour host lifetime or its cleanup reserve.
