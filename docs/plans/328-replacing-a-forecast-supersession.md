---
status: DRAFT
created: 2026-09-25
plan: 328
title: Replacing a forecast — supersession, and the readers that would still serve the old one
scope: Build supersession: a re-run whose recomputation DIFFERS replaces the stored forecast and leaves the original on record, marked. Includes the schema work that makes the state reachable and the reader work that stops a superseded forecast being served. NOT resuming an identical re-run (Plan 327), NOT the review/publish lifecycle, NOT hindcasts (their store already does approved atomic replacement on a six-column key).
depends_on: [327]
blocks: []
related: [327, 340]
open_decisions: []
source: 2026-09-25 — split out of Plan 327 at the owner's direction. A fourth review of 327 found it non-executable because resume and supersession were entangled: *"T3 is scheduled before T4 while T3's differing-content verification requires T4."* Every claim below was measured against `origin/main` and the live staging database that day.
---

# Plan 328 — replacing a forecast

⚠️ **Plan number PROVISIONAL until the owner grants it.** 340-344 are held by a concurrent session.

## Status

**DRAFT.** ⛔ No implementation until an independent review and a READY flip. **Depends on Plan 327**
— 327 defines what "identical" means and refuses the differing case; this plan turns that refusal
into a replacement.

## Why this plan exists

⚖️ **Owner, 2026-09-25, asked directly what should happen when a re-run's numbers differ from what
is stored:** *"Replace, keep the old marked."* The alternatives were rejected — keeping the old and
skipping the new *"quietly throws the fix away"*, and silent overwrite is forbidden outright.

⇒ **Plan 327 refuses that case today**, which is safe and matches current behaviour. This plan makes
it a replacement.

⭐ **And the schema has been waiting for it.** The forecasts table's unique index is **partial**:
`WHERE status <> 'superseded'`. Whoever wrote it clearly intended supersede-then-reinsert. But
`ForecastStatus` is `RAW` / `REVIEWED` / `PUBLISHED` — **no such member** — so the predicate excludes
a value the domain cannot produce and the index behaves as a full one. ⛔ *A reader of
`db/metadata.py` would reasonably conclude a retry path exists. It does not.*

## What is measured

`origin/main` and the live staging DB, 2026-09-25. ⚠️ *Plan 327 § What is measured carries the full
set; repeated here are only the claims THIS plan rests on.*

1. 🔴 **The predicate is dead.** Confirmed against the live `pg_indexes`:
   `CREATE UNIQUE INDEX uq_forecasts_station_model_issued_param … WHERE (status <> 'superseded')`,
   while `types/enums.py:16-19` gives `ForecastStatus` three members and none is `superseded`. The
   only `SUPERSEDED` in the codebase belongs to **`ModelArtifactStatus`** (`:55`).
2. **Making the state reachable needs the DB CHECK too**, not only the enum
   (`db/metadata.py:1130`). ⚠️ *The index predicate itself is NOT replaced — it becomes reachable.*
3. **All 39,825 live forecasts are `raw`.** Nothing has ever been reviewed or published, so there is
   **no status backfill** — but there is still a schema migration.
4. 🔴 **Evidence CANNOT be discarded.** Migration `0057_forecast_evidence` makes `forecast_id` both
   PK and FK to `forecasts.id` and **rejects `UPDATE`, `DELETE` and `TRUNCATE`** on evidence and
   blobs; the store writes forecast, values, evidence and blobs in one transaction
   (`store/forecast_store.py:118`). ⇒ **A superseded forecast keeps its evidence. That is an
   obligation, not a design choice.**
5. 🔴 **TWO READERS HAVE NO STATUS FILTER AT ALL** — the finding that makes this more than a schema
   change. `fetch_latest_forecast()` and `fetch_forecasts_for_cycle()`
   (`store/forecast_store.py:369-398`) apply **no status exclusion**, and the Forecast Lab uses
   both, taking the first matching candidate (`services/forecast_lab/db_sources.py:206-220`).
   ⇒ **After supersession these CAN serve the old row** — ⚠️ *"can", not "would": both rows share an
   `issued_at`, so which is returned depends on ordering. That makes it intermittent, which is
   worse than deterministic.* ⛔ *A plan that only filtered "status-filtering consumers" would have
   missed exactly the readers that matter.*
6. **A superseded forecast must stay reachable by id.** § (4) keeps its evidence; discarding the
   ability to read it back would make that evidence unreachable, which defeats its purpose.

## 🔴 UNDECLARED COLLISION WITH PLAN 341 — found 2026-09-25, owned by nobody

**Plan 341 (CHWRR forecast review and publication API) is independently inventing the same
mechanism**, and neither plan's frontmatter mentions the other. Measured from `341`'s own text:

| | this plan (328) | Plan 341 |
|---|---|---|
| new `ForecastStatus` member | `SUPERSEDED` | **`WITHDRAWN`** (`341:34`) |
| what it does | removes a forecast from current reads, keeps it on record | *"Withdrawal removes it from current consumer reads"* — **the same sentence** |
| the DB CHECK on `forecasts.status` | must admit the new value | must admit **its** new value — **the same constraint, two migrations** |

🔴 **Three concrete ways they collide:**

1. **The enum and the CHECK constraint.** Two plans adding a member to `ForecastStatus` and to
   `metadata.py:1130`. Whichever lands second rebases onto the first — survivable, but only if
   someone knows.
2. ⛔ **341 does not know the predicate is dead.** It never mentions `superseded` or
   `uq_forecasts_station_model_issued_param`. ⇒ **A `withdrawn` forecast would still occupy the
   unique slot**, so a replacement could not be published at the same key — 341 walks into the very
   trap § (1) documents. ⚠️ *And if this plan makes the predicate reachable for `superseded` ONLY,
   341 inherits the trap with a different value.* ⇒ **The predicate should exclude any
   not-current state, not one named value.**
3. **Both do the same reader work.** T3 here stops the unfiltered readers serving a superseded row;
   341 needs exactly that for withdrawn ones. § (5) shows `fetch_latest_forecast()` and
   `fetch_forecasts_for_cycle()` have **no status filter**, and 341 shows no sign of knowing.

⚠️ **Not urgent: 341 is `DRAFT — not implementable`, flagged high-risk pending security and
authorization work.** ⇒ **This is a sequencing note, not a blocker.** ⛔ *Recorded rather than
resolved: renumbering or re-scoping another session's plan is not this one's to do. The owner
should put the two sides in contact before either builds the status change.*

## Owner decisions

⚖️ **Both closed 2026-09-25, before this plan was split out of 327:**
- **Replace, keeping the old marked** — the answer to "a re-run's numbers differ".
- **Make the dead predicate work** (add the status) rather than delete it — *"keeps the door open …
  and matches what whoever designed this clearly intended."*

⛔ **No open decisions.** ⚠️ *Three questions below are named as T-level work, not owner decisions,
because they follow from the two answers above: which readers change (T3 measures them), what
happens to a superseded row's alerts, and how far back by-id access reaches.*

## Tasks

### T1 — Make the state reachable

**Outcome.** A forecast can be marked superseded, and the index predicate stops excluding an
impossible value.

**In.** `SUPERSEDED` added to `ForecastStatus` **and** to the DB CHECK (`metadata.py:1130`), with a
migration. ⛔ *The index predicate is untouched — it becomes reachable, not replaced (§ 2).*

**Out.** ⛔ Setting the status anywhere — T2 does that. ⛔ Backfilling (§ 3: all `raw`).
⛔ Changing the key columns `(station_id, model_id, issued_at, parameter)`; they caught a real
duplicate.

**Pre-change.** A RED test asserting **every value in the index predicate is a member of
`ForecastStatus`** — the metadata predicate's `right.value` against
`{st.value for st in ForecastStatus}`. **Fails today** because `superseded` is not.

**Verification.** Predicate and enum agree by value, asserted against the **migrated schema** as
well as the model. ⛔ *`db/metadata.py` carries the same unreachable predicate as the deployed
index, so diffing the two would prove nothing — compare against the ENUM.*

### T2 — Supersede and replace, atomically

**Outcome.** A re-run whose recomputation differs marks the stored forecast superseded and writes
the replacement, in one transaction.

**In.**
- The transition and the replacement insert, **atomic together**.
- 🔴 **The original's evidence and blobs are KEPT** (§ 4) — the replacement gets its own.
- 🔑 **The trigger is Plan 327's DECISION TABLE, consumed as written.** ⛔ **This plan may not
  reclassify a case.** *A review of the split warned that two plans each deciding "what counts as
  different" is how they drift.* Specifically: 327 classifies **values differ** and **values equal
  but artifact differs** as REPLACEABLE — both come here. It classifies **QC-differs** and
  **evidence-missing** as REFUSE — ⛔ *those do NOT come here, and turning one into a replacement
  means amending 327's table first.*

**Out.** ⛔ Deleting or mutating any evidence row or blob (§ 4 — the migration forbids it anyway;
stated so nobody tries). ⛔ Superseding on an **identical** re-run. ⛔ A supersession that is not
atomic with its replacement. ⛔ Hindcasts.

**Pre-change.** A RED test: **a re-run with differing numbers leaves the original readable and
marked, and the replacement current.** ⭐ *Genuinely red today — `test_forecast_store.py:720`
already shows a differing same-key forecast raising `IntegrityError`.*

**Verification.**
- Original readable and marked; its evidence intact; replacement current with its own evidence.
- An identical re-run supersedes **nothing**.
- 🔴 **Interrupted between the mark and the insert: the ORIGINAL REMAINS CURRENT with its values and
  evidence intact, and the replacement is ABSENT.** ⛔ *Not "neither survives" — the original
  pre-existed and must not be lost.*

### T3 — Stop the readers serving a superseded forecast

**Outcome.** No consumer serves a superseded forecast as if it were current.

⭐ **This task exists because of § (5), and it is the part a schema-only plan would have missed.**

**In.**
- 🔴 **`fetch_latest_forecast()` and `fetch_forecasts_for_cycle()` select CURRENT only** — they have
  no status filter today (§ 5), and the Forecast Lab takes the first candidate either way.
- **An inventory of every forecast reader, measured**, with each one's disposition recorded —
  ⛔ *not "every status-filtering consumer", which by construction omits the two that matter.*
- **Alert selection** resolves the persisted result, not the in-memory ensemble alone — Plan 327
  § (7): the stored identity is discarded today.
- ⚠️ **By-id access to a superseded forecast is PRESERVED** (§ 6), and range/list/summary behaviour
  is stated per reader rather than left to inference.

**Out.** ⛔ Changing what any reader returns for forecasts that are not superseded. ⛔ Hiding a
superseded forecast from by-id reads.

**Pre-change.** A RED test that is **deterministically** red — ⛔ *and it is the FIXTURE that makes
it so, not the assertion. Two earlier attempts changed only the assertion and stayed tie-dependent:
with an original and a replacement sharing an `issued_at`, the reader (`forecast_store.py:380`)
returns an arbitrary one of the two, so even "the returned id is not the superseded id" can pass
today by luck.*
⇒ **Seed a fixture whose ONLY eligible candidate is superseded, and assert the latest-reader returns
`None`.** Cover replacement-selection as a separate case, and assert `fetch_forecasts_for_cycle()`
**excludes the superseded id** by identity.

**Verification.**
- Each reader in the inventory, asserted individually. ⛔ *A single "consumers exclude it" test would
  pass on one reader and prove nothing about the rest.*
- The Forecast Lab shows the replacement, and the published series does not show the original.
- By-id access to the superseded row still works, and it is distinguishable from a current one.

### T4 — Update the architecture (handed over by Plan 327 D3)

**Outcome.** Flow 1 § 1.11 states the replace-and-mark behaviour, once it exists.

⚠️ **Plan 327 D3 assigns this here and 327 does NOT do it** — it documents only what it ships
(identical succeeds, differing refuses). ⛔ *Without this task the handoff is dropped, and a reader
of the architecture would never learn that a differing re-run replaces.*

**In.** The § 1.11 statement extended: a differing re-run **supersedes and replaces**, the original
stays on record marked, and its evidence is retained.

**Out.** ⛔ Writing it before T2 ships. *Documenting a behaviour before it exists is how this repo
carried five months of false compliance.*

**Pre-change.** N/A — documentation.

**Verification.** A reader can answer *"what happens if I re-run a day and the numbers differ?"*
from the architecture alone.

## Explicitly out of scope

- **Resuming an identical re-run** — Plan 327, which this depends on.
- **The review/publish lifecycle.** ⛔ *Not a prerequisite:* correcting a forecast a consumer has
  already read needs no review machinery.
- **Hindcasts** — a six-column key including `hindcast_run_id` and `forcing_type`, with approved
  atomic full replacement (`store/hindcast_store.py:87`, Plan 040). ⛔ *Generalising either store
  across both is how this change would break settled behaviour.*
- **Storage growth.** Superseded forecasts and their evidence are permanent by § (4). ⚠️ *A
  retention question, named so it is not discovered later as a surprise — not answered here.*

```json
{
  "phases": [
    {"phase": 1, "tasks": ["T1"], "parallel": false, "note": "the state must exist before anything sets it"},
    {"phase": 2, "tasks": ["T2"], "parallel": false, "note": "supersede + replace, atomically"},
    {"phase": 3, "tasks": ["T3"], "parallel": false,
     "note": "readers — they cannot be verified until a superseded row can exist"},
    {"phase": 4, "tasks": ["T4"], "parallel": false,
     "note": "architecture LAST — never document a behaviour before it ships"}
  ]
}
```

## Changelog

**2026-09-25 — created by splitting Plan 327**, then corrected by a review of the split
(327: 2 major + 1 minor, 328: 1 major + 1 minor).

| finding | effect here |
|---|---|
| **Shared major — the two plans could drift** on what counts as "different" | 327 now carries **one decision table** with three outcomes (identical / replaceable / refused); T2 **consumes** it and is explicitly forbidden from reclassifying a case |
| **Minor — T3's red test was not reliably red** | the original and replacement share an `issued_at`, so "latest returns the replacement" could pass by luck. Now asserts **exclusion by identity** |
| **Q3 qualification** | § (5) said the unfiltered readers *would* serve the old row; corrected to **can** — ordering decides, which makes it intermittent and therefore worse |

✅ **Verified by the reviewer against the code:** neither `fetch_latest_forecast` nor
`fetch_forecasts_for_cycle` filters status and the Forecast Lab calls both
(`forecast_lab/db_sources.py:155,206`); migration 0057's triggers reject `UPDATE`/`DELETE`/
`TRUNCATE` on both evidence tables; and `test_forecast_store.py:720` seeds differing values under
one key expecting `IntegrityError`, so **T2's red test genuinely fails today**.

**2026-09-25 — post-split review: 1 major, 2 minor.**

| finding | effect |
|---|---|
| **shared major** — the classification still contradicted 327's | 327's table is now **ordered, first match wins**, and T2 consumes it by row number rather than restating it. ⚠️ Evidence is no longer a classifier at all — it would have made 327 a no-op |
| 🔴 **the red test was STILL tie-dependent** — third attempt | ⛔ *The first two changed the ASSERTION; the tie stayed. `forecast_store.py:380` returns an arbitrary one of two rows sharing an `issued_at`, so even "the id returned is not the superseded one" can pass by luck.* ⇒ the **FIXTURE** now makes it deterministic: the only eligible candidate is superseded, and the reader must return `None` |
| **the architecture handoff was dropped** | 327 D3 assigned it here and no task required it. **T4 is new**, and sequenced LAST — ⛔ *never document a behaviour before it ships* |
