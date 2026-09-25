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
   ⇒ **After supersession these would serve the OLD row.** ⛔ *A plan that only filtered
   "status-filtering consumers" would have missed exactly the readers that matter.*
6. **A superseded forecast must stay reachable by id.** § (4) keeps its evidence; discarding the
   ability to read it back would make that evidence unreachable, which defeats its purpose.

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
- The trigger is **Plan 327's comparison**: identical ⟹ 327 succeeds and this never runs; different
  ⟹ this replaces. ⚠️ *327 also names the cases it could not classify — artifact-differs,
  QC-differs, evidence-missing — and its safe default is refuse. **This plan may not widen that
  default silently**: each case it handles, it handles deliberately.*

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

**Pre-change.** A RED test: **after a supersession, `fetch_latest_forecast()` returns the
replacement, not the original.** Fails today — it has no status filter.

**Verification.**
- Each reader in the inventory, asserted individually. ⛔ *A single "consumers exclude it" test would
  pass on one reader and prove nothing about the rest.*
- The Forecast Lab shows the replacement, and the published series does not show the original.
- By-id access to the superseded row still works, and it is distinguishable from a current one.

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
     "note": "readers last — they cannot be verified until a superseded row can exist"}
  ]
}
```
