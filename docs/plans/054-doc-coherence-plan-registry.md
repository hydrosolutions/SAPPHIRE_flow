# Plan 054 — Doc coherence sweep + plan-registry cleanup

**Status**: DRAFT
**Date**: 2026-04-18
**Depends on**: none (documentation-only; no code change). Coordinates with
Plan 046 in-flight (Plan 046 frontmatter update is one task in this plan).
**Scope**: Bring project-level documentation back into sync with shipped
reality. Covers six stale claims surfaced by the 2026-04-18 audit and five
plan-registry irregularities (duplicated 029, unarchived 036/037, non-standard
`DEFERRED` status on 039/042, stale Plan 046 status, missing Plan 047 stub,
absent plan index). Strictly doc edits and file moves — no code, no behaviour
change.

---

## Context

### Why now

The audit found several contradictions that matter only because future
subagents read these docs as ground truth. Specifically:

1. **CLAUDE.md:5** says "Phase 3 is ~30% complete" — but plans 019, 020, 021,
   and 045 are all `status: DONE`. An agent reading CLAUDE.md before starting
   a task will waste cycles trying to "finish" work that is already done.
2. **docs/v0-scope.md §H** (line ~480) does not show Phase 3 with `✓ done`.
   Same consequence.
3. **docs/architecture-context.md:144, 575, 1406** still describe SMN
   (SwissMetNet) station observations as the v0 training-forcing source.
   Plan 021 (2026-04-08) superseded this with CAMELS-CH basin-averaged grids.
   `v0-scope.md` §A12 is current; architecture-context.md lags.
4. **docs/architecture-context.md:3094** defines v0a/v0b as separate NWP
   phases. Plan 021 collapsed that split. v0-scope.md:195 is current.
5. **docs/standards/security.md:153–154** lists `notification_smtp_password`
   and `notification_sms_api_key` as v1 secrets. `handover/data-flows.md:40,
   71` says "SMS and email delivery are not in scope for v1." Webhook-only
   v1 is the canonical decision.
6. **docs/plans/029-lindas-adapter-fix.md** exists in both `docs/plans/` AND
   `docs/plans/archive/` with identical content.
7. **docs/plans/036-hindcast-flow-standalone.md** and
   **docs/plans/037-security-audit.md** are both `status: DONE` but not
   archived (every other DONE plan is moved to `docs/plans/archive/`).
8. **docs/plans/039-alert-data-unavailable-status.md** and
   **docs/plans/042-api-auth-client-sdk.md** use `status: DEFERRED` —
   not one of the workflow-defined values (`DRAFT / READY / IN_PROGRESS /
   DONE / ARCHIVED`).
9. **docs/plans/046-mac-mini-staging-deployment.md** frontmatter still reads
   `Status: READY` despite the plan being actively implemented in another
   session.
10. **No `docs/plans/047-*.md` exists** — Plan 046 references "Plan 047+" for
    Nepal v1 data sources. No stub means the reference dangles.
11. **No `docs/plans/README.md`** index — 13 active plans and 38 archived,
    with no single file mapping IDs to titles and statuses.

### Principle

Project docs are read constantly by subagents. Every contradiction multiplies
across future sessions. A single coherence sweep is cheaper than repeatedly
explaining the current state in prompts.

### Non-goals

- No code changes.
- No changes to completed plans in `docs/plans/archive/` beyond moving new
  entries in.
- No re-opening of closed decisions (e.g., Plan 021 forcing-source decision
  is not reconsidered here — just propagated).

---

## Architecture decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Update CLAUDE.md, v0-scope.md, architecture-context.md in a single coordinated sweep**. Do not split across sessions. | These docs reference each other; a partial update leaves one of them temporarily wrong and the next subagent will see the wrong version. |
| D2 | **Add `DEFERRED` to the workflow status vocabulary** (in `docs/workflow.md`), rather than rewriting 039/042. | `DEFERRED` is semantically distinct from `DRAFT` (not planned yet) and `ARCHIVED` (abandoned). It means "scope-validated, intentionally postponed to a future version." Two plans already use it; formalising is lower churn than rewriting. |
| D3 | **Create a minimal `docs/plans/README.md` index** generated once and updated by hand on plan status changes. Not auto-generated. | Auto-generation adds infra; 13 active plans is small enough to maintain by hand. Subagents benefit most from a one-file lookup table. |
| D4 | **Create a stub Plan 047 (Nepal v1 data sources)** with `status: DRAFT (stub)`, matching the pattern of Plan 048. | Fills the dangling reference from Plan 046 without committing to scope. Future work can expand the stub. |
| D5 | **Move plans 036 and 037 to the archive**; delete the duplicate 029 from `docs/plans/`. | Matches the established convention: every `status: DONE` plan lives in `archive/` only. |
| D6 | **Update Plan 046 status to `IN_PROGRESS`**. Coordinate with the in-flight session — the running session is the source of truth for when this flips to `DONE`. | Frontmatter accuracy. Skipped because Plan 046 is active; a small coordination cost. |

---

## Task list

### T1 — CLAUDE.md Phase 3 status

**File**: `CLAUDE.md`

1. Line 5 — replace "Phase 3 (adapters) is ~30% (protocols + fakes + converters;
   no production or replay adapters)." with "Phase 3 (adapters) complete for
   v0 (plans 019 BAFU, 020 replay/recording, 021 MeteoSwiss NWP, 045 NWP
   wiring all DONE)."
2. Line 5 — replace "Next up: Phase 3 remainder (production + replay adapters),
   then Phase 6..." with "Next up: Phase 8 v0b remainders (task.map
   parallelisation, GroupForecastModel support, pooled combination)."
3. Verify no other CLAUDE.md reference contradicts.

**Exit**: CLAUDE.md describes Phase 3 as complete and points Next-up at v0b.

### T2 — v0-scope.md §H phase ladder

**File**: `docs/v0-scope.md`

1. In §H phase ladder (line ~480), add `✓ done` marker to Phase 3 row.
2. If the phase ladder has a textual summary below, update it to match.
3. Verify other §H entries (5, 6, 7, 7b, 8, 9) are also up to date.

**Exit**: §H phase ladder reflects shipped state.

### T3 — architecture-context.md forcing-source supersession

**File**: `docs/architecture-context.md`

1. Line ~144 — replace the SMN-observations paragraph with:
   "v0 training-forcing: CAMELS-CH basin-averaged gridded data (per Plan 021,
   2026-04-08 — supersedes Plan 013). SMN station observations remain used for
   real-time observation ingest (Flow 2) but are no longer the training
   forcing source."
2. Line ~575 — remove "SMN station observations (hourly, fetched via
   adapter)" from the v0 dynamic-dataset list. Replace with "CAMELS-CH
   basin-averaged grids; ICON-CH2-EPS for operational forecast."
3. Line ~1406 (M.3) — replace "v0: SMN observation adapter (used as
   pseudo-reanalysis). v1: ERA5-Land via WeatherReanalysisSource." with
   "v0: CAMELS-CH basin-averaged gridded data. v1: ERA5-Land via
   WeatherReanalysisSource (Nepal)."
4. `WeatherReanalysisSource` Protocol reference remains correct for v1.

**Exit**: every reference to forcing source in architecture-context.md
matches Plan 021 decision.

### T4 — architecture-context.md v0a/v0b split

**File**: `docs/architecture-context.md`

1. Line ~3094 — replace the v0a-as-SMN-phase / v0b-as-sub-daily description
   with: "v0a: daily operational pipeline. v0b: sub-daily R&D (NWP path is
   unified across v0a/v0b per Plan 021 — no separate NWP phase). v0c:
   sub-daily validation."
2. Cross-check any other site that repeats the split.

**Exit**: v0a/v0b split in architecture-context.md matches v0-scope.md:195.

### T5 — security.md SMS/email secrets

**File**: `docs/standards/security.md`

1. Lines 153–154 — remove `notification_smtp_password` and
   `notification_sms_api_key` from the secrets list.
2. Add a one-line note to the §Secrets management intro:
   "Email and SMS notifications are out of scope through v1 (see
   `docs/handover/data-flows.md`). Only webhook delivery is supported."
3. Verify no other section of security.md implies email/SMS support.

**Exit**: security.md does not list email/SMS secrets for v1.

### T6 — Delete duplicate Plan 029

**File operation**: `rm docs/plans/029-lindas-adapter-fix.md` (keep the
archive copy only).

1. Verify `docs/plans/archive/029-lindas-adapter-fix.md` is identical to
   the active copy (`diff` returns nothing).
2. Delete the active copy.
3. Confirm no plan links target `docs/plans/029-lindas-adapter-fix.md`
   (links should target archive or no path at all).

**Exit**: `ls docs/plans/029*` returns only the archive copy.

### T7 — Archive Plans 036 and 037

**File operations**:

1. `git mv docs/plans/036-hindcast-flow-standalone.md
   docs/plans/archive/036-hindcast-flow-standalone.md`
2. `git mv docs/plans/037-security-audit.md
   docs/plans/archive/037-security-audit.md`
3. Verify both have `status: DONE` in frontmatter.
4. If either has a `## Follow-up plans` section referencing open work,
   ensure those follow-ups are captured in the current plan index (T10)
   or in DRAFT plans elsewhere.

**Exit**: `docs/plans/036*` and `docs/plans/037*` live only in `archive/`.

### T8 — Formalise `DEFERRED` status

**File**: `docs/workflow.md`

1. Add `DEFERRED` to the status enum: "scope-validated, intentionally
   postponed to a future version (v0b, v1, etc.). Distinct from `DRAFT`
   (unplanned) and `ARCHIVED` (abandoned or superseded)."
2. Document the transition rules: `DEFERRED` plans stay in `docs/plans/`
   (not archive) until they are re-promoted or archived.
3. No change to plans 039 or 042 — they already use `DEFERRED` correctly.

**Exit**: workflow.md lists `DEFERRED` as a recognised status.

### T9 — Plan 046 frontmatter: READY → IN_PROGRESS

**File**: `docs/plans/046-mac-mini-staging-deployment.md`

1. Coordinate with the session running Plan 046 — flip the frontmatter line
   `**Status**: READY` to `**Status**: IN_PROGRESS` ONLY IF the other session
   has not already done so.
2. If the other session has already flipped it (or flipped to `DONE`), do not
   edit.

**Exit**: Plan 046 frontmatter reflects the running session's state.

### T10 — Create `docs/plans/README.md` (index)

**File**: `docs/plans/README.md` (new)

1. Write a one-screen index with sections:
   - **Active** (DRAFT / READY / IN_PROGRESS) — one line per plan with ID,
     title, status, one-sentence summary.
   - **Deferred** (DEFERRED) — same format.
   - **Archived** — collapsed list pointing at `archive/`.
2. Keep the file strictly under ~200 lines; linebreak for readability.
3. Add a maintenance note at the top: "Update this index whenever a plan's
   status changes or a new plan is added. Do not auto-generate."

**Exit**: `docs/plans/README.md` exists and lists every plan currently in
`docs/plans/`.

### T11 — Create Plan 047 stub (Nepal v1 data sources)

**File**: `docs/plans/047-nepal-v1-data-sources.md` (new)

1. Follow the stub pattern from Plan 048:
   - `Status: DRAFT (stub)`
   - `Phase: v1`
   - `Depends on: v0 complete`
   - Short "Why this exists" section pointing at Plan 046's
     "Plan 047+" reference.
   - Sketch scope: ECMWF IFS adapter, DHM station adapter, ERA5-Land
     reanalysis, elevation-band NWP extraction.
   - "Not in scope": everything that is v2.
   - Open questions and exit gates as placeholders.
2. Keep total length under ~80 lines (stub-size).

**Exit**: `docs/plans/047-*.md` exists; Plan 046's reference no longer
dangles.

---

## Dependency graph

```json
{
  "stream-1-content": {
    "tasks": ["T1", "T2", "T3", "T4", "T5"],
    "parallel": "all five in parallel — independent files",
    "depends_on": []
  },
  "stream-2-registry": {
    "tasks": ["T6", "T7", "T8", "T9", "T11"],
    "parallel": "T6, T7, T8, T11 in parallel; T9 coordinated with external session",
    "depends_on": []
  },
  "stream-3-index": {
    "tasks": ["T10"],
    "sequential": true,
    "depends_on": ["T6", "T7", "T11"]
  }
}
```

T10 runs last so the index reflects the post-cleanup state (029 gone, 036/037
moved, 047 added).

---

## Files to modify

| Path | Task | Change |
|---|---|---|
| `CLAUDE.md` | T1 | Phase 3 → complete; Next-up → v0b |
| `docs/v0-scope.md` | T2 | §H — Phase 3 ✓ done |
| `docs/architecture-context.md` | T3, T4 | Forcing source (3 sites); v0a/v0b split |
| `docs/standards/security.md` | T5 | Remove SMS/email secrets; add webhook-only note |
| `docs/workflow.md` | T8 | Add `DEFERRED` to status vocabulary |
| `docs/plans/046-mac-mini-staging-deployment.md` | T9 | Status → IN_PROGRESS (if not already) |

## Files to create

| Path | Task | Purpose |
|---|---|---|
| `docs/plans/README.md` | T10 | Plan index |
| `docs/plans/047-nepal-v1-data-sources.md` | T11 | Stub for Nepal v1 data adapters |

## Files to delete / move

| Path | Task | Action |
|---|---|---|
| `docs/plans/029-lindas-adapter-fix.md` | T6 | Delete (archive copy retained) |
| `docs/plans/036-hindcast-flow-standalone.md` | T7 | `git mv` to `archive/` |
| `docs/plans/037-security-audit.md` | T7 | `git mv` to `archive/` |

---

## Exit gates

1. CLAUDE.md, v0-scope.md §H, architecture-context.md all describe Phase 3
   as complete and forcing source as CAMELS-CH.
2. `Grep -l "notification_smtp\|notification_sms" docs/` returns nothing.
3. `docs/plans/029-lindas-adapter-fix.md` does not exist; archive copy
   present.
4. `docs/plans/036*` and `docs/plans/037*` live only in `archive/`.
5. `docs/workflow.md` lists `DEFERRED` as a recognised status.
6. `docs/plans/README.md` exists and lists every plan under ~200 lines.
7. `docs/plans/047-*.md` exists as a DRAFT stub.
8. Plan 046 frontmatter reflects the running-session state.
9. Full repo grep for "SMN station observations" in a training-forcing
   context returns no stale hits.
10. Version bump applied per CLAUDE.md (this is a doc-only commit, but the
    project's Version Bumping mandate has no exception for doc-only commits).

---

## Risks

| Risk | Mitigation |
|---|---|
| T9 (Plan 046 frontmatter) conflicts with in-flight session edits | T9 explicitly gates on "only if not already done." Coordinate via git — this plan lands only after the in-flight session commits. |
| T10 index goes stale the moment a new plan is added | Maintenance note at the top of `README.md` (T10 step 3). Future plans must update the index as part of their own exit gates. |
| T7 loses cross-references from plans that link at `docs/plans/036-*.md` | Before moving, `Grep -l "plans/036\|plans/037"` across `docs/` and archive. Update any in-repo link to use the archive path. |
| T3 edits conflict with a parallel doc change in another session | Doc sweep is contained to architecture-context.md; low traffic. If conflict, re-apply the same changes. |
| T8 adding `DEFERRED` to workflow.md confuses existing agents that cached the older enum | Acceptable — next read picks up the new status. |

---

## Open questions

Not blocking DRAFT → READY:

1. Should Plan 047 stub include placeholder dependency graph and exit gates
   now, or stay minimal like Plan 048? (Recommendation: minimal — Plan 048 is
   the pattern.)
2. Should `docs/plans/README.md` group plans by phase or by ID? (Recommendation:
   by ID for stability; status in the line-label handles the rest.)
3. If the running Plan 046 session flips status to `DONE` before T9 runs,
   should this plan still edit the frontmatter? (Recommendation: no — just
   skip T9.)
