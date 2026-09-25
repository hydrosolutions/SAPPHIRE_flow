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
3. **The store does not translate the failure.** `store_forecast` is *"a plain insert … with no
   `ON CONFLICT` and no store-boundary exception translation — a duplicate-cycle re-run raises an
   **unwrapped SQLAlchemy `IntegrityError`**, not a domain error"* (`docs/touchpoint-maps.md:757`).
   ⭐ **That same line ends: "Confirm this is intended before assuming a naive retry-on-
   `SapphireError` caller covers it."** ⇒ **It was documented as needing confirmation and never
   confirmed.** This plan is that confirmation.
4. ⚠️ **Writes are not atomic per cycle.** Flows run on an AUTOCOMMIT connection, so each statement
   commits on its own and `store_forecast` (header + values) is **not atomic as a unit**
   (`touchpoint-maps.md:755`). ⇒ A cycle killed mid-write can leave a header without its values,
   which any retry design must handle and which a plain "insert if absent" would not notice.
5. **The architecture is silent.** `docs/architecture-context.md`'s Flow 1 table (`:81-94`) has
   **no idempotency column at all**, while Flow 0's every row is marked *Idempotent* / *Idempotent
   (upsert)* / *Idempotent (resume)* (`:578-583`). ⇒ Nothing states whether a forecast cycle may be
   re-run — which is exactly what the owner asked to have specified.
6. **Nothing owns it.** No plan in `docs/plans/` covers re-running a forecast cycle. Plan 040
   solved the *hindcast* twin with a dedicated constraint including `hindcast_run_id`; the
   operational path got no equivalent.
7. **Machinery already exists and is unused for this.** `forecasts.version` plus
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
correcting a published claim, and carries an audit obligation (a) does not.

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

**In.** D2's choice, with a migration if it is (a). 🔴 **A test that the index's predicate matches a
value `ForecastStatus` can actually produce** — ⛔ *the defect this plan found is precisely a
predicate no test ever compared against the enum.*

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

**Verification.**
- A cycle interrupted after the header and before the values, then re-run, ends with both.
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
