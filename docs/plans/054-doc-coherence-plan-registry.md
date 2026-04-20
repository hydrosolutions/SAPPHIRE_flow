# Plan 054 — Doc coherence sweep + plan-registry cleanup

**Status**: READY
**Date**: 2026-04-18 (flipped to READY 2026-04-20)
**Depends on**: none (documentation-only; no code change). Coordinates with
Plan 046 in-flight (Plan 046 frontmatter update is one task in this plan).
**Scope**: Bring project-level documentation back into sync with shipped
reality. Covers six stale claims surfaced by the 2026-04-18 audit and six
plan-registry irregularities (duplicated 029, unarchived 036/037,
undocumented `DEFERRED` status on 039/042, stale Plan 046 status, missing
Plan 047 stub, absent plan index). Strictly doc edits and file moves — no
code, no behaviour change.

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
3. **docs/architecture-context.md:140, 144, 575, 1398, 1409** still describe
   or imply SMN (SwissMetNet) station observations as the v0
   training-forcing source. Plan 021 (2026-04-08) superseded this with
   CAMELS-CH basin-averaged grids. `v0-scope.md` §A12 is current;
   architecture-context.md lags.
4. **docs/architecture-context.md:3097** defines v0a/v0b as separate NWP
   phases. Plan 021 collapsed that split. v0-scope.md:195 is current.
5. **docs/standards/security.md:153–154** lists `notification_smtp_password`
   and `notification_sms_api_key` as v1 secrets, and
   **docs/handover/it-operations.md:182–183** repeats both rows in its
   "Secrets Managed by SAPPHIRE Flow" table. `handover/data-flows.md:40, 71`
   says "SMS and email delivery are not in scope for v1." Polling plus
   webhook are the canonical v1 alert-delivery channels; only webhook
   delivery is owned on the SAPPHIRE side.
5a. **docs/design/v0-flow678-training-pipeline.md:16** still lists "SMN
   station observations as pseudo-perfect forcing, tagged
   `ForcingType.REANALYSIS`" in the v0 simplifications table, predating
   Plan 021's basin-average supersession. The design doc diverges from
   `v0-scope.md` §A12 and from the corrected architecture-context prose.
6. **docs/plans/029-lindas-adapter-fix.md** exists in both `docs/plans/` AND
   `docs/plans/archive/` with identical content.
7. **docs/plans/036-hindcast-flow-standalone.md** and
   **docs/plans/037-security-audit.md** are both `status: DONE` but not
   archived, which is out of line with the current archive convention for
   completed plans.
8. **docs/plans/039-alert-data-unavailable-status.md** and
   **docs/plans/042-api-auth-client-sdk.md** use `status: DEFERRED`, but
   **docs/workflow.md** does not yet codify a plan-status vocabulary or define
   where deferred plans live.
9. **docs/plans/046-mac-mini-staging-deployment.md** frontmatter still reads
   `Status: READY` despite the plan being actively implemented in another
   session.
10. **No `docs/plans/047-*.md` exists** — Plan 046 references "Plan 047+" for
    Nepal v1 data sources. No stub means the reference dangles.
11. **No `docs/plans/README.md`** index — 17 active plans and 46 archived,
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
| D2 | **Codify the active-plan status vocabulary in `docs/workflow.md`, explicitly including `DEFERRED`, rather than rewriting 039/042.** | `DEFERRED` is semantically distinct from `DRAFT` (not planned yet) and `ARCHIVED` (abandoned). It means "scope-validated, intentionally postponed to a future version." `docs/workflow.md` currently covers readiness, not the full vocabulary. Historical archive-only labels are out of scope for this sweep. |
| D3 | **Create a minimal `docs/plans/README.md` index** generated once and updated by hand on plan status changes. Not auto-generated. | Auto-generation adds infra; even the current 17 active plan files (15 post-cleanup if this plan lands) are small enough to maintain by hand. Subagents benefit most from a one-file lookup table. |
| D4 | **Create a stub Plan 047 (Nepal v1 data sources)** with `status: DRAFT (stub)`, matching the pattern of Plan 048. | Fills the dangling reference from Plan 046 without committing to scope. Future work can expand the stub. |
| D5 | **Move plans 036 and 037 to the archive**; delete the duplicate 029 from `docs/plans/`. | Matches the current convention that completed plans are archived once their root-level bookkeeping is complete. |
| D6 | **Update Plan 046 status to `IN_PROGRESS`**. Coordinate with the in-flight session — the running session is the source of truth for when this flips to `DONE`. | Frontmatter accuracy. Skipped because Plan 046 is active; a small coordination cost. |

---

## Task list

### T1 — CLAUDE.md Phase 3 status

**File**: `CLAUDE.md`

1. Line 5 — replace the stale Phase 3 fragment ("Phase 3 (adapters) is ~30%...")
   with text that describes Phase 3 as complete for v0. Ground truth:
   Plans 019, 020, 021, and 045 are DONE; Plan 063 is later adapter hardening,
   not evidence that Phase 3 is incomplete.
2. Rewrite the stale "Next up: Phase 3 remainder..., then Phase 6..." fragment
   to point at current post-v0 follow-on work. An acceptable replacement is:
   "Next up: remaining v0b/v0c follow-ons and staging hardening (for example
   forecast-cycle parallelisation, operational GroupForecastModel support,
   and Plan 046 validation)." Do **not** name pooled combination as future work;
   it is already implemented in code and reflected in `v0-scope.md` §A8e.
3. If the resulting sentence still reads awkwardly as "mid-implementation",
   rewrite the full line 5 summary so it stays coherent after the two content
   updates above.
4. Verify no other CLAUDE.md reference contradicts.

**Exit**: CLAUDE.md describes Phase 3 as complete and no longer points
Next-up at already-shipped work.

### T2 — v0-scope.md §H phase ladder

**File**: `docs/v0-scope.md`

1. In §H phase ladder (lines ~480–482), add `✓ done` marker to Phase 3 row.
2. If the phase ladder has a textual summary below, update it to match.
   Do not add wording that implies Phase 11 is shipped unless §E5 / nearby
   capstone text is reconciled in the same edit.
3. Verify other §H entries (5, 6, 7, 7b, 8, 9) are also up to date.
   Treat Phase 11 separately: Plan 043 is DONE, but §H / §E5 still frame
   Phase 11 in terms of "golden answers". Do not mark Phase 11 done unless
   the surrounding acceptance text is updated to match shipped reality too.

**Exit**: §H reflects the corrected shipped status for Phase 3 without
introducing new contradictions around Phase 11.

### T3 — architecture-context.md + design-doc forcing-source supersession

**Files**: `docs/architecture-context.md`, `docs/design/v0-flow678-training-pipeline.md`

`architecture-context.md`:

1. In the resolved forcing-source subsection (lines ~140–149), remove wording
   that presents SMN as the chosen v0 training-forcing source. Replace the
   decision paragraph at line ~144 with:
   "v0 training-forcing: CAMELS-CH basin-averaged gridded data (per Plan 021,
   2026-04-08 — supersedes Plan 013). SMN station observations remain used for
   real-time observation ingest (Flow 2) but are no longer the training
   forcing source."
2. Update the surrounding source-options prose if needed so it reads as a
   generic design discussion, not as a statement that v0 currently uses SMN
   for training forcing.
3. Line ~575 — in the §0.3 "Historical dynamic datasets" bullet, remove
   "SMN station observations (hourly, fetched via adapter)" from the v0
   entry and leave CAMELS-CH as the sole v0 historical-forcing source.
   Do **not** add ICON-CH2-EPS here — §0.3 is scoped to historical/training
   datasets; operational NWP forcing lives in Flow 1 and is out of scope
   for this bullet.
4. Line ~1398 (M.3 table row) and line ~1409 (M.3 note) — replace the v0
   SMN/WeatherReanalysisSource wording with CAMELS-CH-based v0 wording and
   ERA5-Land-for-Nepal v1 wording.
5. `WeatherReanalysisSource` Protocol reference remains correct for v1.
6. Verify any remaining SMN mentions in this area are intentionally about
   observation ingest or generic source-type taxonomy, not the chosen v0
   training-forcing decision.

`docs/design/v0-flow678-training-pipeline.md`:

7. Line ~16 — update the "Forcing source" row of the §1 simplifications
   table. Replace the v0 simplification cell
   ("SMN station observations as pseudo-perfect forcing, tagged
   `ForcingType.REANALYSIS`") with:
   "CAMELS-CH basin-averaged gridded forcing (per Plan 021 — supersedes
   Plan 013), tagged `ForcingType.REANALYSIS`. Nepal v1: ERA5-Land via
   `WeatherReanalysisSource`."
8. If the design doc carries a narrative section that still names SMN as
   the training forcing, bring it in line with the table row. Do not
   broaden scope beyond the forcing-source correction.

**Exit**: every reference to v0 training-forcing source in
`architecture-context.md` and `docs/design/v0-flow678-training-pipeline.md`
matches Plan 021 decision. Remaining SMN mentions are only about Flow 2
observation ingest or generic source-type taxonomy.

### T4 — architecture-context.md v0a/v0b split

**File**: `docs/architecture-context.md`

1. Line ~3097 — replace the v0a-as-SMN-phase / v0b-as-sub-daily description
   with: "v0a: daily operational pipeline. v0b: sub-daily R&D (NWP path is
   unified across v0a/v0b per Plan 021 — no separate NWP phase). v0c:
   sub-daily validation."
2. Cross-check any other site that repeats the split.

**Exit**: v0a/v0b split in architecture-context.md matches v0-scope.md:195.

### T5 — Remove email/SMS secrets from security and handover docs

**Files**: `docs/standards/security.md`, `docs/handover/it-operations.md`

`docs/standards/security.md`:

1. Lines 153–154 — remove `notification_smtp_password` and
   `notification_sms_api_key` from the secrets list.
2. Add a one-line note to the §Secrets management intro:
   "Email and SMS notifications are out of scope through v1 (see
   `docs/handover/data-flows.md`). Alert consumers can poll the API; outbound
   delivery on the SAPPHIRE side is webhook-only."
3. Verify no other section of security.md implies email/SMS support.

`docs/handover/it-operations.md`:

4. Lines 182–183 — remove the `notification_smtp_password` and
   `notification_sms_api_key` rows from the "Secrets Managed by SAPPHIRE
   Flow" table.
5. Do **not** add internal plan or version references to the handover doc
   (per the `feedback_handover_docs_no_version_churn` memory). A plain
   sentence above the table such as "Alert delivery is webhook-only; email
   and SMS integrations are out of scope for this release." is acceptable,
   or omit any replacement text and let the table stand with one fewer row.
6. Verify no other section of `it-operations.md` promises email/SMS
   channels.

**Exit**: neither `docs/standards/security.md` nor
`docs/handover/it-operations.md` lists `notification_smtp_password` or
`notification_sms_api_key` as v1 secrets, and neither implies email/SMS
channels.

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

### T8 — Codify plan status vocabulary (including `DEFERRED`)

**File**: `docs/workflow.md`

1. Add a short "Plan status vocabulary" subsection to `docs/workflow.md`
   covering active-plan states `DRAFT / READY / IN_PROGRESS / DEFERRED / DONE`
   plus `ARCHIVED` for moved plans.
2. Define `DEFERRED` as "scope-validated, intentionally postponed to a
   future version (v0b, v1, etc.)." Distinguish it from `DRAFT`
   (unplanned/not ready) and `ARCHIVED` (closed historical record).
3. Document the transition rules: `DEFERRED` plans stay in `docs/plans/`
   (not archive) until they are re-promoted or archived.
4. Clarify that this plan does **not** backfill legacy archive-only labels
   such as `COMPLETE`, `RESOLVED`, or archived `READY`; that is separate
   historical cleanup, out of scope here.
5. No change to plans 039 or 042 — they already use `DEFERRED` correctly.

**Exit**: workflow.md documents the current plan-status vocabulary,
including `DEFERRED`.

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
   - **Done in root** (DONE, not yet archived) — rare/temporary; include only
     if any such plans still exist in `docs/plans/` when the index is written.
   - **Archived** — collapsed list pointing at `archive/`.
2. Include Plan 054 itself. It will still live in `docs/plans/` at the time
   T10 runs (this sweep does not archive itself mid-commit). List it under
   Active with its current status.
3. Keep the file strictly under ~200 lines; linebreak for readability.
4. Add a maintenance note at the top: "Update this index whenever a plan's
   status changes or a new plan is added. List every plan file currently under
   `docs/plans/`. Do not auto-generate."

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
  "phases": [
    {"id": "content", "tasks": ["T1", "T2", "T3", "T4", "T5"], "parallel": true},
    {"id": "registry", "tasks": ["T6", "T7", "T8", "T9", "T11"], "parallel": true},
    {"id": "index", "tasks": ["T10"], "parallel": false, "depends_on": ["registry"]}
  ]
}
```

Notes that do not fit the JSON shape:

- Within the `content` phase, T3 and T4 both touch
  `docs/architecture-context.md`; a subagent running them concurrently must
  serialize writes to that file (or one subagent owns both tasks).
- T9 in the `registry` phase is gated on "only flip frontmatter if the
  in-flight Plan 046 session has not already done so" — see T9 body.
- `index` (T10) depends on `registry` so the plan index reflects the
  post-cleanup state (029 gone, 036/037 moved, 047 added, 046 status synced).
  It does not need `content` to finish first.

---

## Files to modify

| Path | Task | Change |
|---|---|---|
| `CLAUDE.md` | T1 | Phase 3 → complete; Next-up → current post-v0 follow-ons |
| `docs/v0-scope.md` | T2 | §H — Phase 3 ✓ done |
| `docs/architecture-context.md` | T3, T4 | Forcing-source sweep (resolved subsection + Flow 0 + M.3); v0a/v0b split |
| `docs/design/v0-flow678-training-pipeline.md` | T3 | §1 simplifications table — forcing-source row → CAMELS-CH |
| `docs/standards/security.md` | T5 | Remove SMS/email secrets; add polling+webhook scope note |
| `docs/handover/it-operations.md` | T5 | Remove SMS/email rows from secrets table |
| `docs/workflow.md` | T8 | Codify plan-status vocabulary including `DEFERRED` |
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
2. `Grep -l "notification_smtp\|notification_sms" docs/` returns **no hits
   outside `docs/plans/` and `docs/plans/archive/`** (historical plan
   records may legitimately cite the old secret names).
3. `docs/plans/029-lindas-adapter-fix.md` does not exist; archive copy
   present.
4. `docs/plans/036*` and `docs/plans/037*` live only in `archive/`.
5. `docs/workflow.md` documents the current plan-status vocabulary,
   including `DEFERRED`.
6. `docs/plans/README.md` exists and lists every plan under ~200 lines.
7. `docs/plans/047-*.md` exists as a DRAFT stub.
8. Plan 046 frontmatter reflects the running-session state.
9. Full repo grep for "SMN station observations" returns no hits in a
   training-forcing context outside `docs/plans/` and
   `docs/plans/archive/`. Specifically, the live targets —
   `docs/architecture-context.md` and
   `docs/design/v0-flow678-training-pipeline.md` — contain no such phrase
   describing v0 training forcing. Remaining matches must be either
   (a) Flow 2 observation-ingest context, or (b) historical plan records
   in `docs/plans/archive/`.
10. Version bump applied per CLAUDE.md (this is a doc-only commit, but the
    project's Version Bumping mandate has no exception for doc-only commits).

---

## Risks

| Risk | Mitigation |
|---|---|
| T9 (Plan 046 frontmatter) conflicts with in-flight session edits | T9 explicitly gates on "only if not already done." Coordinate via git — this plan lands only after the in-flight session commits. |
| T10 index goes stale the moment a new plan is added | Maintenance note at the top of `README.md` (T10 step 3). Future plans must update the index as part of their own exit gates. |
| T7 loses cross-references from plans that link at `docs/plans/036-*.md` | Before moving, `Grep -l "plans/036\|plans/037"` across `docs/` and archive. Update any in-repo link to use the archive path. |
| T3 edits conflict with a parallel doc change in another session | Doc sweep is contained to `architecture-context.md` and `docs/design/v0-flow678-training-pipeline.md`; both low traffic. If conflict, re-apply the same changes. |
| T8 codifying the plan-status vocabulary in workflow.md confuses existing agents that cached the older guidance | Acceptable — next read picks up the clarified vocabulary. |

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
   skip T9. If it remains in `docs/plans/`, T10 lists it under `Done in root`;
   if it is already archived by then, T10 just reflects the archived state.)
