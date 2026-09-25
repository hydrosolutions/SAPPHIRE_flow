---
status: DRAFT
created: 2026-09-25
plan: 327
title: A forecast cycle cannot be re-run — and the escape hatch the schema appears to offer is dead
scope: Decide and specify what re-running a forecast cycle MEANS, and make the decided behaviour real — in the architecture (Flow 1 says nothing about idempotency today), in the store boundary, and in whatever mechanism the decision picks. NOT the review/publish lifecycle itself, NOT hindcast dedup (Plan 040, shipped), NOT the pinned-midnight scaffold's existence (Plan 326 owns that), NOT retry of anything other than the forecast cycle.
depends_on: []
blocks: []
open_decisions: [D1, D2, D3]
source: 2026-09-25 — the owner, after the midnight scaffold's second same-day run failed on a duplicate key: *"plan in the same-day retry. check if there is already a plan for it. it should be specified in the architecture that we do retries of forecasts."* No plan owns it. Every claim below was measured against `origin/main` and the live staging database that day.
---

# Plan 327 — a forecast cycle cannot be re-run

⚠️ **Plan number PROVISIONAL until the owner grants it.** 340-344 are held by a concurrent session.

## Status

**DRAFT.** ⛔ No implementation until an independent review and a READY flip. **D1 is a semantics
question and must be answered before anything is built** — the three things people mean by "retry"
need different machinery, and one of them must not be allowed at all.

## Why this plan exists

Demonstrated on staging 2026-09-25, not predicted: triggering the forecast cycle twice for the same
pinned issue time failed the **second run outright**.

```
psycopg.errors.UniqueViolation: duplicate key value violates unique constraint
  "uq_forecasts_station_model_issued_param"
DETAIL: Key (station_id, model_id, issued_at, parameter)=(…, cmal_small,
        2026-09-25 00:00:00+00, discharge) already exists
```

⭐ **Nothing was corrupted and nothing partial was written** — 653 rows before, 653 after. The
constraint refused the duplicate and the whole flow run went to `Failed`, loudly. *That half is
right and must survive whatever this plan does.*

⇒ **But a cycle that fails halfway cannot be re-run**, because whatever it already wrote collides.
Today the only recovery is to delete that issue time's rows for the affected models, by hand, on a
live database.

## What is measured

`origin/main` and the live staging DB, 2026-09-25.

1. 🔴 **THE ESCAPE HATCH IS DEAD.** The index is **partial**:
   `WHERE (status <> 'superseded'::text)` — confirmed against the live `pg_indexes`, not just the
   model. So the schema appears to offer supersede-then-reinsert.
   ⛔ **`ForecastStatus` has no such member.** It is `RAW`, `REVIEWED`, `PUBLISHED`
   (`types/enums.py:16-19`). The only `SUPERSEDED` in the codebase belongs to
   **`ModelArtifactStatus`** (`:55`), used by model onboarding.
   ⇒ **The predicate excludes a value the domain type cannot produce, so the index behaves as a
   FULL unique index.** ⚠️ *This is the most dangerous fact here: a reader of `metadata.py` would
   reasonably conclude a retry path exists.*
2. **Live statuses: `raw` × 39,825, and nothing else.** No forecast has ever been reviewed or
   published, so the lifecycle is entirely untested against this question.
3. **The store does not translate the failure — and that is a DELIBERATE, RECORDED decision.**
   A duplicate raises an unwrapped SQLAlchemy `IntegrityError` (`touchpoint-maps.md:757`).
   ⛔ *The first draft of this plan said that was "never confirmed". **False** —
   `docs/plans/archive/038-store-write-atomicity.md:87` records **D5, REVERSED 2026-07-08: do NOT
   catch/wrap writes in `StoreError`**, matching every other SQL-backed store.* ⇒ The open question
   is **narrower than the draft claimed**: not *"should writes be wrapped"* (answered: no) but
   *"should a semantic RETRY CONFLICT be distinguishable from an unrelated storage failure"* — which
   would revise 038's contract for that one case, not overturn it.
4. ⛔ **CORRECTED — a single forecast IS atomic, and the orphan hazard the first draft asserted
   DOES NOT EXIST.** `PgForecastStore` defaults `self._begin = conn.engine.begin`
   (`store/forecast_store.py:53-60`), so header and values share one transaction;
   `tests/integration/store/test_forecast_store.py:851`
   (`test_values_insert_failure_rolls_back_header`) proves neither survives a failure, and migration
   0028 cleaned the historical orphans. ⚠️ **`touchpoint-maps.md`'s AUTOCOMMIT line is STALE for this
   store** and misled the first draft — correcting it is now T2's.
   ⇒ **The real granularity is the CYCLE, not the forecast.** An interrupted cycle leaves whole,
   correctly-written forecasts for the stations it reached, and nothing for the rest. That is what a
   resume must handle.
5. **The architecture is silent.** `docs/architecture-context.md`'s Flow 1 table (`:81-94`) has
   **no idempotency column at all**, while Flow 0's every row is marked *Idempotent* / *Idempotent
   (upsert)* / *Idempotent (resume)* (`:578-583`). ⇒ Nothing states whether a forecast cycle may be
   re-run — which is exactly what the owner asked to have specified.
6. **Nothing owns it.** No plan in `docs/plans/` covers re-running a forecast cycle. Plan 040
   solved the *hindcast* twin with a dedicated constraint including `hindcast_run_id`; the
   operational path got no equivalent.
7. 🔴 **THE STORED FORECAST IS NOT WHAT ALERTS USE.** `store_forecast(fc)`'s return value is
   **discarded** (`flows/run_forecast_cycle.py:2943`), and alerting consumes the freshly computed
   in-memory ensembles (`:3677`). ⇒ **A naive `ON CONFLICT DO NOTHING` would leave storage holding
   the OLD forecast while alerts fire on the NEW one** — a silent divergence between what we keep
   and what we act on. ⚠️ *Any retry design must state what the caller gets back, not only what the
   table ends up holding.*
8. **A per-station store failure is NOT fatal on the station path** — it is caught, logged
   `forecast_cycle.store_forecast_failed` and appended to `errors` (`:2945-2950`). ⚠️ *The GROUP
   path differs and treats it as fatal, which is why the measured run failed outright. Retry
   semantics must not assume one behaviour for both.*
9. **Machinery already exists and is unused for this.** `forecasts.version` plus
   `transition_status` is the **only** optimistic-locking path in the stores (the sole
   `ConflictError` caller, `touchpoint-maps.md:756`).

## Owner decisions

### D1 — what does "retry" MEAN? **OPEN — answer before anything is built.**

Three different needs hide under one word, and they need different machinery:

| | need | shape |
|---|---|---|
| **(a)** | **Resume a cycle that died halfway.** Same inputs, same issue time, finish the job. | Wants *insert-if-absent*, plus § (4)'s header-without-values case |
| **(b)** | **Re-issue a corrected forecast** for an issue time we already published — a bad input was fixed, a model was repaired. | Wants **supersession**: the old row kept, marked, and a new one written. This is what the partial index was clearly built for. |
| **(c)** | **Overwrite silently**, newest wins. | ⛔ **Must NOT be allowed once § (2)'s lifecycle is live.** A `PUBLISHED` forecast someone acted on cannot be replaced without trace. |

⚠️ **(a) and (b) are not variants of one feature.** (a) is about completing work; (b) is about
correcting a claim we have already made, and carries an audit obligation (a) does not.
⛔ *The first draft tied (b) to PUBLICATION. Wrong: correcting a `RAW` or `REVIEWED` forecast matters
too — anything a human or a downstream consumer has already seen.*

🔴 **The question all three depend on, and which the first draft missed: SAME KEY DOES NOT MEAN SAME
CONTENT.** `(station, model, issued_at, parameter)` says nothing about the inputs, the artifact, the
configuration or the output. A re-run after a model was repaired produces a *different* forecast
under the *same* key. ⇒ **`ON CONFLICT DO NOTHING` would silently conceal the repair** — and § (7)
makes it worse: the stored row would stay stale while alerts fire on the new in-memory ensemble.

⇒ **D1 must also settle an EQUIVALENCE POLICY:** when a retry meets an existing row, is it *the same
computation* (a true retry — succeed, return the existing identity) or *a different one* (a
conflict — refuse, or supersede under (b))? ⛔ *Without this, "retry" is undefined for the only case
that actually matters operationally.*

**Recommendation: build (a) now, specify (b), forbid (c).** ⭐ *(a) is the operational pain today
and is safe while every row is `raw`. (b) becomes REQUIRED the moment a forecast is published, and
§ (2) says that has never happened — so it can be specified now and built when the review flow
lands, which is honest sequencing rather than a deferral.*

### D2 — fix the dead predicate, or remove it? **OPEN.**

| | option | cost |
|---|---|---|
| **(a)** ⭐ | **Add `SUPERSEDED` to `ForecastStatus`** and make the predicate live, implementing D1(b). | Makes the schema's evident intent real. Needs the status threaded through every reader that filters on status — and § (2) means no data migration is required today. |
| (b) | **Drop the predicate**, making the index plainly full. | Honest and smaller, but throws away the design the schema already encodes, and D1(b) would have to re-add it later. |
| (c) | Leave it. | ⛔ **Rejected.** A predicate excluding an impossible value is a trap for the next reader — see § (1). |

⚠️ **Both options need a migration** — (b) must replace the deployed index, not merely edit the
model. And (a) needs **more than the enum**: the DB CHECK constraint at `db/metadata.py:1130` must
admit the value too. 🔴 **Adding a status member is not supersession** — the transition, who may
perform it, and what it does to `forecast_values` are D1(b)'s, which this plan SPECIFIES and does
not build. ⇒ *(a) here means "make the predicate reachable", not "ship supersession"; the draft blurred
those and the reviewer was right to catch it.*

**Recommendation: (a) if D1 includes (b); otherwise (b).** ⛔ *Whichever — it must not stay as it
is.*

### D3 — where does "we may re-run a forecast" get SPECIFIED? **OPEN — the owner asked for this explicitly.**

Owner: *"it should be specified in the architecture that we do retries of forecasts."*

⇒ At minimum, Flow 1's step table gains what Flow 0 already has: an idempotency statement per step,
and **1.11 Store forecast results** says plainly what re-running does. The question is whether that
is a column on the table, a paragraph beside it, or a named subsection — ⚠️ *and whether the same
statement is owed for Flows 2, 6, 7 and 8, which were not examined here and must not be silently
assumed idempotent.*

## Tasks

### T1 — Settle the semantics and write them down (D1, D3)

**Outcome.** The architecture states what re-running a forecast cycle does, and the three meanings
are distinguished.

**In.** D1's answer in `docs/architecture-context.md` Flow 1 — including the **forbidden** case and
why. The idempotency statement for step 1.11 at minimum. ⚠️ **A note naming the flows NOT examined**
(2, 6, 7, 8) so their silence is recorded as unexamined rather than read as safe.

**Out.** ⛔ Changing any other flow's behaviour. ⛔ Asserting idempotency for a flow nobody measured.

**Pre-change.** N/A — specification.

**Verification.** A reader can answer *"may I re-run yesterday's cycle?"* from the architecture
alone, including the answer for a published forecast.

### T2 — Make the dead predicate honest (D2)

**Outcome.** The unique index and the domain type agree.

**In.** D2's choice, **with a migration either way** (§ D2) — and for (a) the DB CHECK at
`db/metadata.py:1130` as well as the enum. 🔴 **A test comparing the index predicate against
`ForecastStatus` by VALUE** — the metadata predicate's `right.value` against
`{s.value for s in ForecastStatus}`. ⛔ *The defect this plan found is precisely a predicate no test
ever compared against the enum.*
⚠️ **If D2 is (b), assert the predicate's ABSENCE explicitly** — a comparison over an empty
predicate passes vacuously and would prove nothing.
- **The stale touchpoint-map line** (§ 4): `touchpoint-maps.md`'s AUTOCOMMIT statement is wrong for
  `PgForecastStore`, which uses `engine.begin`. ⛔ *It misled this plan's own first draft — correct
  it in place.*

**Out.** ⛔ Changing the key columns `(station_id, model_id, issued_at, parameter)` — they are
correct and they caught a real duplicate. ⛔ Backfilling status on existing rows (§ 2: all `raw`).

**Pre-change.** A RED test asserting the DESIRED behaviour: **every value in the index predicate is
a member of `ForecastStatus`**, which fails today because `superseded` is not.

**Verification.** The predicate and the enum are asserted against each other, in one test, by value.

### T3 — Make a half-finished cycle re-runnable (D1a)

**Outcome.** Re-running a cycle that died partway completes it instead of failing on what it
already wrote.

**In.** D1(a)'s mechanism at the store boundary, and 🔴 **§ (4)'s header-without-values case
handled explicitly** — a forecast header whose values never committed is *not* a completed write and
must not be treated as one.

**Out.** ⛔ Silent overwrite (D1c). ⛔ Touching a `REVIEWED` or `PUBLISHED` row — that is D1(b), and
until the review flow exists there is nothing to protect, but the guard belongs here so the
behaviour cannot regress into (c) later. ⛔ Swallowing the `IntegrityError` without deciding what it
meant.

**Pre-change.** A RED test reproducing the measured failure: **a second run for the same issue time
fails today**, and must complete after the change.
⛔ **NOT the interruption test the first draft proposed.** *A single interrupted forecast already
rolls back and re-inserts cleanly (§ 4), so that test passes before the change and proves nothing —
red for the wrong reason.* ⇒ The discriminating shape is at CYCLE granularity: **commit station A,
interrupt at station B, re-run, and assert A is untouched while B completes.**

**Verification.**
- A cycle interrupted between stations, then re-run: the stations already written are **unchanged**
  and the rest complete. ⛔ *Not "the orphan is repaired" — orphans cannot occur (§ 4).*
- 🔴 **A retry whose recomputed forecast DIFFERS from the stored one does not silently do nothing**
  (§ 7 + D1's equivalence policy). It resolves per that policy, and what the caller receives is
  asserted — not only what the table holds.
- **The station path and the GROUP path are both covered** (§ 8): one tolerates a store failure, the
  other treats it as fatal.
- ⛔ **Hindcasts are untouched** — asserted. They use separate tables and a **six-column** key
  including `hindcast_run_id` AND `forcing_type`, with approved atomic full replacement
  (`store/hindcast_store.py:87`). *Generalising either store across both is how this change would
  break Plan 040's settled behaviour.*
- 🔴 **A genuine duplicate — same issue time, same model, already complete — still does NOT write a
  second row.** ⭐ *The constraint's protective half must survive; this plan removes an obstacle, it
  does not remove the guard.*
- The failure a caller sees is a domain error, not a raw SQLAlchemy exception (§ 3).

## Explicitly out of scope

- **The review/publish lifecycle** — D1(b) depends on it and specifies against it; building it is
  not this plan's.
- **Hindcast dedup** — Plan 040 solved the twin with `hindcast_run_id`.
- **The pinned-midnight scaffold** — Plan 326. ⚠️ *It is the thing that exposed this, and its own
  "no same-day retry" hazard note is closed by T3.*
- **Flows 2, 6, 7 and 8** — named in T1 as unexamined, deliberately not assessed.

```json
{
  "phases": [
    {"phase": 1, "tasks": ["T1"], "parallel": false, "note": "semantics first — T2 and T3 both depend on D1"},
    {"phase": 2, "tasks": ["T2", "T3"], "parallel": true, "decision": "D1, D2, D3 CLOSED"}
  ]
}
```

## Changelog

**2026-09-25 — created, then corrected** after an independent review returned **4 major**. Applied
in the text above, not appended. ⭐ **Two of the draft's measured claims were simply FALSE**, and
both came from trusting a document instead of the code:

| finding | what was wrong |
|---|---|
| **M1** | The draft's orphan hazard — *"a killed cycle can leave a header without its values"* — **does not exist.** `PgForecastStore` uses `engine.begin`, a test at `test_forecast_store.py:851` proves the rollback, and migration 0028 cleaned the historical ones. ⛔ *The source was `touchpoint-maps.md`'s AUTOCOMMIT line, which is stale for this store; correcting it is now in T2.* The draft's Pre-change test would have been **red for the wrong reason** — it passes today. |
| **M2** | The draft never asked whether a retry's recomputed forecast **differs** from the stored one. Same key ≠ same content, `ON CONFLICT DO NOTHING` would conceal a repaired model, and § (7) — the stored ID is discarded while alerts use the in-memory ensemble — turns that into a divergence between what we keep and what we act on. D1 now needs an equivalence policy. Correction also matters for `RAW`/`REVIEWED`, not only published. |
| **M3** | D2 understated the work: **both** options need a migration, (a) also needs the DB CHECK at `metadata.py:1130`, and adding an enum member **is not** supersession — which this plan specifies and does not build. The draft blurred those. ⚠️ The red test **is** expressible (`right.value` vs the enum's values), but for option (b) the predicate's absence must be asserted explicitly or it passes vacuously. |
| **M4** | *"Never confirmed"* was **false**. `038:87` records D5, REVERSED: deliberately do **not** wrap store writes. The open question is narrower — whether a semantic retry conflict should be distinguishable from an unrelated storage failure. |

⚠️ **Also added from the review:** the station path tolerates a per-station store failure while the
GROUP path treats it as fatal (§ 8), and **hindcasts must be left alone** — a six-column key
including `forcing_type`, with approved atomic full replacement.

⭐ **The lesson worth keeping: I sourced two claims from `touchpoint-maps.md` rather than the store,
and one of them was stale.** A map of the code is not the code — the same class of error this plan
exists to fix, committed while writing it.
