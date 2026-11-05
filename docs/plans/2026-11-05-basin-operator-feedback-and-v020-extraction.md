---
status: READY
---

# Basin operator feedback and pinned v0.2.0 extraction

## Outcome and authority

Owner requested the UI fixes and automatic-extraction update after the Trishuli
rehearsal (Effort #451). Preserve the merged local operator boundary and approved
vision `planning/visions/2026-11-05-extract-and-approve-station-basins.md`.
The intended extractor source is release `v0.2.0`, commit
`25cc09c2aff192033abefaf6b052822ad7024d67` in `hydrosolutions/static-attrs-nepal`.
This release fixes distance-first internally and rejects the historical v2
`--snap-strategy`/`--snap-audit` options. A version string alone does not attest
source identity: the historical bundle also reported software version 0.2.0.

This is user-visible behavior and an external CLI boundary, so follow the
repository's high-risk review requirements: independent Claude and Codex passes,
plus one owner-commissioned relevant review, before READY and before PR opening.
One agent owns implementation. This plan does not authorize deployment, merge,
new EC2 resources, source changes in the extractor, or edits to the live Trishuli
workspace. The orchestrator may mark READY after required reviews and owner
resolution of findings; DRAFT is not executable. All three independent plan
reviews are complete. The owner approved the bounded corrections below on
2026-11-05. The orchestrator sets READY for this implementation pass.
The pinned extractor is fixed; no package-upgrade framework or frequent-update
scenarios are part of this work.

## Evidence and bounds

- Chrome reproduced missing reviewer rejection (HTTP 400), with the error about
  916 pixels above the viewport. Button handlers themselves worked. Client-side
  feedback must not replace server validation or imply authenticated identity.
- Precomputed Trishuli is loaded but section 2 says `not_started` and offers Start
  extraction. Attached-package and app-launched extraction are different states.
- `BasinWorkspace.extraction_argv` currently injects the v2 strategy option for
  every command; this is incompatible with the released main CLI.
- Trishuli main/v2 matched all geometry and 92 static values in the same runtime.
  The 21-station comparison was NOT exactly equivalent. Neither run exercised
  automatic extraction through this operator or a separately rebuilt main image.

Touchpoints: `basin_operator/{workspace,web,presentation}.py`, its review template,
`cli/basin_operator.py`, focused tests, the operator runbook and
`docs/operations/nepal-station-onboarding-runbook.md`. Consult the
storage/API routing map only to preserve the boundary: no database importer,
public API, Gateway, FI/model, Prefect or forecast-runtime changes. Preserve local
host/CSRF guards, immutable package checks, warning/coverage gates, fingerprint
invalidation and all human-approval/export semantics. No approval resets or
source-package mutations for a display/configuration change.

## Tasks

### T1 — Visible reviewer and action feedback

**Outcome:** Blank or whitespace-only reviewer names produce a short message
beside the decision controls, focus the reviewer field and send no decision
request. The server still rejects missing/blank reviewers and preserves state.
Network/server failures from other buttons remain visible at the action or are
scrolled/focused into view with an accessible announcement, not silently placed
above a long map. Successful export identifies the handover location with the
same visible, scroll/focus and accessible-announcement behavior as errors.
Feedback pages stay read-only and contain no mutation controls or CSRF token.

**In / Out:** Template feedback and narrow response handling/tests. Do not
redesign the page or add authentication/frontend frameworks. Preserve reasons,
acknowledgments, selection and fingerprints. Empty selection and other errors
must not be hidden by reviewer validation.

**Pre-change:** Capture failing regression behavior for visible/focused missing
reviewer feedback; existing HTTP rejection alone is not adequate evidence.

**Verification:** Focused web/review tests and a real isolated Chrome browser
exercise on a synthetic workspace: empty/whitespace reviewer, correctable error,
valid decision, server/network error, visible export success. No approval clicks
in the actual Trishuli workspace. Assert HTTP state and viewport/focus behavior,
not merely presence of JavaScript strings. Prefer the existing test stack; if a
browser runner must be added, keep it an isolated documented test extra/tool and
make the repeatable browser check explicit rather than claiming API tests prove UI.

### T2 — Honest attached-package status and extraction controls

**Outcome:** A valid precomputed package displays `Existing extraction loaded`;
Start extraction is disabled/unavailable in that launch mode, and a direct HTTP
request cannot start a process or clear approvals. No extractor configuration also
has an actionable disabled state. Configured automatic mode still requires input
confirmation and shows running/completed/failure states accurately.

**In / Out:** Presentation, launcher/workspace boundary and tests. Prefer deriving
presentation from existing package state plus explicit launch configuration over
migrating the saved ledger. Old workspaces must load without losing inventory,
package identities, approvals or handovers. Corrected inputs must not display an
old package as current; reattachment still requires a new valid package identity.
Reject ambiguous simultaneous precomputed and automatic launch configuration with
a helpful error, rather than choosing silently.

**Pre-change:** Failing attached-page status/control test and direct-start guard.
`attach_package` leaves the persisted extraction status as `not_started`; replace
that misleading display through derived presentation, not a new ledger field.

**Verification:** `tests/unit/basin_operator/test_web.py` and relevant CLI tests:
attached, empty/no-command, confirmed automatic, running, failed, completed,
corrected inputs and resume of an old-format state. Read-only copies/synthetic
workspaces only. Browser verifies disabled controls and visible state.
Explicitly test that simultaneous `--package-dir` and `--extractor-command`
fail fast with a clear error. Add one automatic-completion → restart/attach the
same unchanged package → confirm/export regression: retain decisions, fingerprint
and the recorded source ZIP hash. A different package must not inherit that hash.
This is ordinary same-package resume, not an extractor upgrade feature.

### T3 — Explicit pinned-release automatic extraction interface

**Outcome:** The launcher exposes a small typed choice between `v0.2.0` and
`historical-v2`. Document and configure `v0.2.0` explicitly for the onboarding
pipeline. Retain historical behavior as the backward-compatible default for
existing argv-prefix configurations; never infer interface from version text or
silently reinterpret/remove supplied flags. This compatibility default does NOT
change the selected release for the new pipeline.

For `v0.2.0`, append no strategy/audit flags and reject those options if present
in the supplied prefix (split and equals forms). For historical v2 retain explicit
supported distance-first behavior and rejection of alternative strategies.
Continue protecting operator-owned repeated station/output/package arguments,
including equals forms, from prefix overrides. Do not use a shell or rewrite
arbitrary command prefixes. Parse external choice once into an Enum; no domain
booleans or unrestricted internal string selectors.

The configured command must run the operator-provisioned pinned source/runtime.
Interface selection is NOT binary/source verification. Record the selected
interface and expected release/source reference with each new invocation, clearly
as configuration, alongside the exact argv/input provenance; do not fabricate a
verified hash. Existing invocation records remain untouched.

**In / Out:** Launcher argument parsing, small typed command builder/workspace
wiring and tests/runbook. No new plugin system, source download, image build,
SSH/S3 wrapper, dependency installation into SAPPHIRE, scientific thresholds,
cloud provisioning or automatic runtime probing/retries.

**Pre-change:** A subprocess that implements the release's accepted argument
shape rejects the existing automatically appended v2 flag.

**Verification:** Fast parameterized argv tests for both interfaces and invalid
configuration; a real external test subprocess (not a mocked Popen) exercises
confirm → start → valid ZIP → safe unpack → exact station/package identity →
review-ready package. The release-shaped test process must reject v2-only flags,
retain `00001`, accept corrected coordinates and honor the requested package ID.
Cover failure/invalid archive and legacy v2. Retain existing archive/immutability
regressions. Synthetic subprocess acceptance is not a new live scientific run.
Any further native-EC2 automatic-run rehearsal requires separate authorization;
list it as pending rather than claiming the earlier precomputed test covers it.

### T4 — Documentation, review and release checks

**Outcome:** The runbook clearly separates precomputed attachment, recommended
pinned-release automatic mode and legacy v2 compatibility. Show the explicit
release selector and supported argv prefix, ZIP output/same-path mount requirement,
operator responsibility for pinning source/runtime, and pending rebuilt-appliance
handover. Describe visible reviewer feedback and attached status accurately.
Document actual test limits, not exact 21-station equivalence or already-complete
native automatic acceptance. Preserve historical scientific/provenance facts.
Reconcile the Nepal onboarding runbook's `--snap-audit` example with the selected
release, or clearly label the historical-only path and link to the current guide;
do not leave it looking like a supported pinned-release invocation.

**Pre-change:** N/A for documentation; this task reconciles T1–T3 behavior.

**Verification:** Focused operator tests including browser checks; affected loader
unit regressions; ruff lint/format; pyright changed modules. Run the full suite
once after final code changes locally or in CI before merge. Use the new worktree's
own uv environment. Advance the per-PR `RELEASE_TARGET` one patch from the tested
base using `.venv/bin/python -I tools/release_target.py bump --base-sha <full-base-sha>`.
Package identity remains SCM-derived. The orchestrator confirmed this current
checked-in mechanism replaces stale inherited per-commit literal-version guidance;
do not restore removed version fields or dependencies.
Keep review reports local, not committed. Present independent findings to the
owner; do not silently resolve risk or automatically open/merge a PR. Preserve
live review server/workspace until an authorized restart, with no inferred or
replayed approval. No feature-branch tag.

## Implementation checkpoint (2026-11-05)

- T1: implemented. Real isolated Chrome reproduces RED before the change, then
  verifies reviewer focus/viewport and no blank-reviewer request, empty selection,
  server-error correction, valid decision, network failure, visible export success
  and read-only feedback. The explicit browser check passes (1 test).
- T2: implemented. Attached/direct-start guards, no-command and automatic states,
  old-ledger resume, corrected inputs, ambiguous launch rejection and same-package
  approval/fingerprint/ZIP-hash preservation have passing regressions.
- T3: implemented. Enum selection, strict argv checks and real external synthetic
  release/legacy subprocesses pass, including `00001`, corrected coordinates,
  package identity, invalid ZIP and process failure. No live extraction was run.
- T4: docs reconciled; focused operator/loader/CLI tests pass (164), changed-module
  ruff lint/format and pyright pass. Release target advances from tested base
  `8857c8294772843a9a8eff5a1794071443291e7e` to `0.1.1110` with the supported helper.
  **Full-suite gate remains blocked:** `uv run pytest` yielded 11,751 passed,
  8 failed, 54 skipped and 15 deselected. All failures are in
  `tests/unit/ops/test_watchdog_qc_unchecked.py`: real host disk free space below
  5% adds an alert to tests that do not inject disk usage. The isolated file
  reproduces the same eight failures (five passed). No unrelated code was changed.
  Full green local/CI verification, independent patch reviews, owner disposition
  and separately authorized native automatic-extraction acceptance remain pending.

## Exit commands

- `uv run pytest tests/unit/basin_operator tests/unit/services/test_basin_package_loader.py`
- Run new focused CLI nodes and the documented repeatable browser checks.
- `uv run ruff check src/sapphire_flow/basin_operator src/sapphire_flow/cli/basin_operator.py tests/unit/basin_operator` (plus added CLI/browser tests)
- `uv run ruff format --check` for all touched Python files.
- `uv run pyright src/sapphire_flow/basin_operator src/sapphire_flow/cli/basin_operator.py`
- Final pre-merge: `uv run pytest` locally or full CI after final code change.

```json
{"phases":[{"id":"feedback","tasks":["T1","T2"],"parallel":false},{"id":"extraction","tasks":["T3"],"depends_on":["feedback"]},{"id":"verification","tasks":["T4"],"depends_on":["extraction"]}]}
```
