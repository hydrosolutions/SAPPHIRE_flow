---
status: DRAFT
created: 2026-09-25
plan: 327
title: A forecast cycle that died partway cannot be resumed
scope: RESUMING a forecast cycle that died partway — specify it in the architecture (Flow 1 says nothing about idempotency today) and make an identical re-run succeed instead of failing on what it already wrote. ⛔ NOT supersession: replacing a forecast whose recomputation DIFFERS is Plan 328, split out 2026-09-25 because the two entangled this plan's phase order. NOT the review/publish lifecycle itself, NOT hindcast dedup (Plan 040, shipped), NOT the pinned-midnight scaffold's existence (Plan 326 owns that), NOT retry of anything other than the forecast cycle.
depends_on: []
blocks: []
related: [328]
open_decisions: []   # all three closed by the owner 2026-09-25; D1(2) enlarged the plan — supersession is now BUILT, not specified
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
9. 🔴 **NEW, landed 2026-09-25 while this plan was being written (Plan 340 T1, #306): every
   operational forecast now writes a `forecast_evidence` row IN THE SAME TRANSACTION** as the
   forecast and its values, with `forecast_id` a foreign key to `forecasts.id`
   (`alembic/versions/0057_forecast_evidence.py:40-42`), and blob hashes verified on read.
   ⇒ **Retry now carries an evidence obligation** (⚠️ *"three writes" is shorthand — the store
   writes forecast, values, evidence and any content-addressed blobs in one transaction,
   `store/forecast_store.py:118`*). ⛔ **Evidence disposal is NOT an open option: migration 0057
   makes `forecast_id` both PK and FK and REJECTS `UPDATE`, `DELETE` and `TRUNCATE` on evidence and
   blobs.** *An earlier version of this claim said supersession "must decide what happens to the
   evidence" — it does not get to decide; it must keep it.*
   🔴 **`EvidenceStatus` is ALWAYS `INCOMPLETE` today** — `services/forecast_evidence.py:133-145`
   appends a reason on **every** branch, including the one where the image digest is present and
   valid (`runtime_image_bytes_unpinned`). ⇒ *Incomplete evidence is the NORMAL state, not an
   anomaly*, and historical forecasts need not have an evidence row at all. ⛔ **Neither may be a
   refusal trigger** — see the decision table, where an earlier version made exactly that mistake
   and would have refused every resume.
   🔴 **And resume has a hazard of its own:** combined forecasts build their evidence from
   contributor IDs and hashes held in memory (`services/forecast_combination.py:543`), which the
   store checks against **persisted** evidence (`forecast_store.py:59`). A cycle resumed after the
   contributors committed but before their combination can therefore write an **immutable**
   `contributor_evidence_not_persisted` record. ⛔ *Correct alert selection does not fix this; the
   contributor identities must be resolved from the store BEFORE the combination's evidence is
   built.* ⚠️ *Re-measured at the moment of acting; this landed after the first review.*
10. **Machinery already exists and is unused for this.** `forecasts.version` plus
   `transition_status` is the **only** optimistic-locking path in the stores (the sole
   `ConflictError` caller, `touchpoint-maps.md:756`).

## Owner decisions

### D1 — what does "retry" MEAN? **⚖️ CLOSED — owner, 2026-09-25.**

Three different needs hide under one word, and they need different machinery:

| | need | shape |
|---|---|---|
| **(a)** | **Resume a cycle that died halfway.** Same inputs, same issue time, finish the job. | Wants *insert-if-absent* at CYCLE granularity — the stations already written stand, the rest complete. ⛔ *No orphan repair: § (4) shows a half-written forecast cannot occur.* |
| **(b)** | **Re-issue a corrected forecast** for an issue time we have already ANSWERED — a bad input was fixed, a model was repaired. ⛔ *Not "published": a `RAW` or `REVIEWED` forecast a consumer has already read needs correcting too.* | Wants **supersession**: the old row kept, marked, and a new one written. This is what the partial index was clearly built for. |
| **(c)** | **Overwrite silently**, newest wins. | ⛔ **Must NOT be allowed — now, not "once the lifecycle is live".** § (7): alerts already fire on the in-memory ensemble, so a silent overwrite makes what we kept and what we acted on diverge with no trace. Publication raises the stakes; it is not what creates the problem. |

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

**⚖️ OWNER, 2026-09-25 — all three answered:**

1. **A failed day MUST be re-runnable.** *"A missing day is a real gap: skill scoring, verification
   and anything looking back at our record will have a hole in it."* ⇒ the cheaper alternative —
   accept the gap, tomorrow covers it — is **rejected**.
2. **When a re-run's numbers DIFFER: replace, keeping the old one marked.** ⇒ 🔑 **That is
   Plan 328's**, split out because it is a mechanism in its own right and its entanglement with
   resume made this plan's phase order unexecutable (a resume task cannot verify a replacement that
   a later task builds).
3. **Silent overwrite stays forbidden.**

⇒ **This plan owns the IDENTICAL case and the boundary between them:**

| a re-run meets an existing forecast and the recomputation is… | this plan |
|---|---|
| **identical** | ✅ **succeed, write nothing, return the stored identity** — the resume case |
| **different** | 🔒 **refuse, loudly and specifically** — ⭐ *that is today's behaviour, so no regression, and it hands a named conflict to Plan 328 instead of guessing* |

### 🔑 What "identical" MEANS — the comparison, defined

⛔ *An earlier draft left this to "the equivalence policy" without saying what is compared. A review
called that out: it is the hinge, and an error in the "identical" direction silently discards the
correction the re-run existed to deliver.*

⛔ **NOT compared** — these differ on every re-run by construction and must not make a retry look
different: the row id, `created_at`/`updated_at`, and the flow-run identity.

🔑 **THE DECISION TABLE — evaluated IN ORDER, first match wins.** ⛔ *An earlier version listed
overlapping conditions with no precedence: equal values/artifact satisfied both IDENTICAL and a
QC-differs refusal, and an evidence condition cut across every row. Ordered evaluation makes the
outcomes mutually exclusive by construction.* **Plan 328 CONSUMES this table and may not
reclassify a case.**

| # | condition | outcome | who acts |
|---|---|---|---|
| 1 | the **values** differ (aligned by valid time and quantile/member) | **REPLACEABLE** ⟹ supersede | **Plan 328** |
| 2 | values equal, the **model artifact identity** differs | **REPLACEABLE** ⟹ supersede — ⭐ *the same numbers from a different model version are not the same forecast, and this is what a re-run after a repair produces* | **Plan 328** |
| 3 | values and artifact equal, the **QC verdict** differs | 🔒 **REFUSE** — unclassified. *Equal values should give equal QC unless the RULES changed, which is a real difference nobody has decided how to treat* | this plan |
| 4 | otherwise | **IDENTICAL** ⟹ succeed, write nothing, return the stored identity | this plan |

🔴 **EVIDENCE IS NOT IN THE TABLE, and an earlier version putting it there would have made this
plan a NO-OP.** ⛔ *That version refused when the stored row carried an incomplete-evidence marker.
Measured: `services/forecast_evidence.py:133-145` appends a reason on **every branch** — including
the one where the image digest is present and valid (`runtime_image_bytes_unpinned`) — so
`EvidenceStatus` is **always `INCOMPLETE`** today. Every stored forecast would have met the
refusal condition, and no resume would ever have succeeded.*

⇒ **Evidence state plays no part in classifying a retry.** A missing evidence row (a forecast
predating migration 0057) and an incomplete marker are both **irrelevant** to whether the
recomputation matches. ⚠️ *The evidence OBLIGATIONS in T2 are unchanged and remain — they are about
what a resume must not destroy or misreport, not about what counts as identical.*

⛔ **NOT compared** — these differ on every re-run by construction and must not make a retry look
different: the row id, `created_at`/`updated_at`, and the flow-run identity.

⚠️ **Row 3 is the only refusal, and it is deliberate.** *Refusing is today's behaviour, so nothing
regresses.* ⇒ **Whoever wants it handled amends THIS table**, not 328.

### D2 — the dead predicate. **⚖️ CLOSED — owner: make it work. 🔑 MOVED TO PLAN 328.**

The unique index is partial on `status <> 'superseded'` while `ForecastStatus` has no such member
(§ 1), so the predicate excludes an impossible value. The owner chose to **make it reachable** by
adding the status.

⇒ **That belongs with the mechanism that uses it — Plan 328.** ⛔ *Adding a status nothing sets, in
the plan that does not set it, would leave a second dead thing where there was one.* This plan
records the trap (§ 1) and does not touch it.

### D3 — where does "we may re-run a forecast" get SPECIFIED? **⚖️ CLOSED — owner: in the architecture.**

Owner: *"it should be specified in the architecture that we do retries of forecasts."*

⇒ Flow 1's step table gains what Flow 0 already has, and **1.11 Store forecast results** states
plainly what re-running does **as shipped by this plan**: an identical re-run succeeds, a differing
one is refused. ⚠️ **The replace-and-mark behaviour is Plan 328's, and 328 owns the follow-up
architecture edit** — ⛔ *documenting a behaviour before it exists is how this repo previously
carried five months of false compliance.*

⚖️ **The sub-question closed by the orchestrator: Flows 2, 6, 7 and 8 are NAMED AS UNEXAMINED, not
assessed.** ⛔ *Measuring four more flows to answer a question nobody asked would widen this plan
past its subject; recording their silence as unchecked costs one line and prevents the far worse
outcome of a reader assuming they are safe.*

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

### T2 — Make a half-finished cycle re-runnable

**Outcome.** Re-running a cycle that died partway completes it instead of failing on what it
already wrote.

**In.** D1(a)'s mechanism at the store boundary, at **CYCLE granularity**: the stations already
written stand, the rest complete. ⛔ **NO orphan-repair work** — § (4) proves a header without its
values cannot occur, and the first draft's requirement to handle one was building for an impossible
state.
🔴 **Evidence requirements, from § (9) — obligations, not observations:**
- A resumed cycle **resolves contributor identities AND their persisted evidence references from
  the STORE before building a combination's evidence.**
  🔴 **IDs alone are not enough.** `services/forecast_evidence.py:350` also copies the **in-memory
  evidence hash**, which the store checks against persisted evidence — so correcting only the ids
  trades an immutable `contributor_evidence_not_persisted` record for an immutable
  `contributor_evidence_mismatch` one. ⛔ *And fetching the forecast does not hydrate its evidence,
  so the reference must be fetched deliberately.* ⇒ **A resume test must assert COMPLETE evidence,
  not merely the absence of the first marker.**
- An **equivalent** retry leaves the existing evidence and blobs in place — ⛔ *it may not rewrite
  them; 0057 rejects `UPDATE`/`DELETE`.*
- 🔴 **A stored forecast carrying the ordinary incomplete-evidence marker is still classified by the
  table** — i.e. an identical re-run of one **succeeds**. ⛔ *Every forecast has that marker (§ 9);
  a test that only covers synthetic complete evidence proves nothing about the real path.*
- The whole set still rolls back atomically on failure.

🔴 **The REFUSAL must reach ALERT SELECTION, not stop at the store.** § (7): the stored identity is
discarded and alerts consume the in-memory ensemble, so a store-level rejection still leaves the
caller free to alert on content the store would not keep. ⇒ **A refused forecast must not be
alerted on** — that is this plan's, because the divergence exists the moment refusal does.
⚠️ *What a SUPERSEDED forecast means for alert selection is Plan 328's.*

**Out.** ⛔ Silent overwrite (D1c) — forbidden outright, not only after publication.
⛔ **Replacing any row** — that is Plan 328 entirely. ⚠️ *This plan REFUSES the differing case; it
neither overwrites nor supersedes.*
⛔ **Translating every `IntegrityError` into a domain error.** § (3): Plan 038 D5 deliberately leaves
store writes unwrapped, and that stands. **Only a semantic retry conflict becomes a domain error**;
an unrelated storage failure keeps propagating raw.

**Pre-change.** A RED test that **fails today for the reason the defect exists**, which took two
attempts to state:
- ⛔ *The first draft asked for an interrupted single forecast. It rolls back and re-inserts cleanly
  (§ 4) — passes today.*
- ⛔ *The second asked for "commit A, interrupt at B, re-run, A unchanged and B completes". **Also
  passes today on the STATION path**, because A's duplicate error is caught and logged (§ 8) and B
  proceeds regardless.*
- ✅ **The discriminating shape: the re-run must complete with NO error recorded.** Today A's
  collision is caught and appended to `errors`, so the cycle reports a failure it should not have.
  Assert the run is clean, not merely that B exists. ⭐ **Or exercise the GROUP path, where the same
  collision is fatal** (§ 8) — that one fails visibly today.

**Verification.**
- A cycle interrupted between stations, then re-run **with an identical recomputation**: the
  stations already written are **unchanged**, the rest complete, and 🔴 **no error is recorded for
  the ones that were already there.** ⚠️ *The qualifier matters — "unchanged" is the right outcome
  only when the content matches; when it differs this plan REFUSES (D1).*
  ⛔ *Not "the orphan is repaired" — orphans cannot occur (§ 4).*
- 🔴 **A retry whose recomputed forecast DIFFERS from the stored one does not silently do nothing**
  (§ 7 + D1's equivalence policy). It resolves per that policy, and what the caller receives is
  asserted — not only what the table holds.
- **The station path and the GROUP path are both covered** (§ 8): one tolerates a store failure, the
  other treats it as fatal.
- ⛔ **Hindcasts are untouched** — asserted. They use separate tables and a **six-column** key
  including `hindcast_run_id` AND `forcing_type`, with approved atomic full replacement
  (`store/hindcast_store.py:87`). *Generalising either store across both is how this change would
  break Plan 040's settled behaviour.*
- 🔴 **An identical duplicate — same issue time, same model, same values and artifact, already
  complete — still does NOT write a second row.** ⭐ *The constraint's protective half must survive;
  this plan removes an obstacle, it does not remove the guard.*
- 🔴 **A DIFFERING recomputation is REFUSED with a conflict that names what differed** — not
  written, not silently skipped. ⛔ *Plan 328 turns this refusal into a replacement; until it lands,
  refusing is correct and is what happens today.*
- 🔴 **A cycle resumed between the contributors and their combination does NOT produce a
  `contributor_evidence_not_persisted` record** — asserted, because that record is immutable once
  written and this is the resume path's own failure mode.
- 🔴 **Alerting cannot consume content the store refused** — asserted end to end, because § (7)
  means a store-level guard alone does not achieve this.
- A **semantic retry conflict** reaches the caller as a domain error, while an **unrelated storage
  failure still propagates raw** (§ 3, preserving Plan 038 D5). ⛔ *Both halves asserted — wrapping
  everything is the regression this clause exists to prevent.*
  ⚠️ **An IDENTICAL retry is not a conflict and raises nothing** — it succeeds quietly. *The error is
  for the differing case only; Plan 328 replaces that error with a replacement.*

## Explicitly out of scope

- 🔑 **Supersession — replacing a forecast whose recomputation differs — is PLAN 328.** This plan
  refuses that case, which is what happens today, so nothing regresses while 328 is built.
- **The review/publish lifecycle.** ⛔ *Not a prerequisite for either plan* — correcting a forecast a
  consumer has already read needs no review machinery. It is simply someone else's work.
- **Hindcast dedup** — Plan 040 solved the twin with `hindcast_run_id`.
- **The pinned-midnight scaffold** — Plan 326. ⚠️ *It is the thing that exposed this, and its own
  "no same-day retry" hazard note is closed by **T2**.*
- **Flows 2, 6, 7 and 8** — named in T1 as unexamined, deliberately not assessed.

```json
{
  "phases": [
    {"phase": 1, "tasks": ["T1"], "parallel": false, "note": "specify before building"},
    {"phase": 2, "tasks": ["T2"], "parallel": false,
     "note": "resume only — the differing case REFUSES here and is built by Plan 328"}
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

**2026-09-25 — round 2 on the fold: NEEDS CHANGES again, 4 major, all PARTIAL.** ⛔ *I corrected the
measured CLAIMS and left the DECISIONS and TASKS saying the old thing — the precise failure mode
this project has already booked, committed twice in one plan.* What the operative text now says:

| was still wrong | now |
|---|---|
| D1(a) and T3's In still required the **impossible orphan** case | removed; resume is at CYCLE granularity |
| The revised red test **also passed today** — A's collision is caught on the station path and B proceeds anyway | the discriminating assertion is *no error recorded*, or the GROUP path where it is fatal |
| D1(b)/(c) still framed around **publication** | (b) is "already answered", (c) is forbidden **from today** — § (7) means the divergence exists before anything is published |
| D2(a) still claimed to **implement** D1(b) | it makes the predicate REACHABLE; the transition is D1(b)'s, specified not built |
| T2's test prescribed a predicate comparison **unconditionally**, though D2(b) deletes the predicate | branches by option; absence asserted explicitly, because an empty comparison passes vacuously |
| T3 still demanded a domain error **for everything** | only a semantic retry conflict; unrelated storage failures stay raw, preserving Plan 038 D5 |
| Conflict resolution stopped at the **store** | it must reach alert selection — § (7): a store-level refusal still leaves the caller alerting on rejected content |

✅ **Verified clean in round 2:** no renumbering errors from inserting claims 7 and 8, and all three
newly added claims confirmed against the code — the discarded return and in-memory alert input, the
station-tolerates / group-fatal asymmetry, and the hindcast six-column key with atomic replacement.

**2026-09-25 — round 3: 2 major + 1 minor, and 4 of 6 round-2 items confirmed FIXED.** ⭐ *The
reviewer verified from source that the corrected red test now genuinely fails today — station
collisions append to `errors` (`run_forecast_cycle.py:2961`, `:3254`) and GROUP collisions re-raise
(`:3627`) and escape (`:3670`).* What changed this round:

| finding | now |
|---|---|
| **The lifecycle dependency survived the last fold** — the option rows were corrected while the RECOMMENDATION still said (b) "needs a review lifecycle", and § Explicitly out of scope repeated it | Both corrected. **(b) is deferred by this plan's CHOICE, gated by nothing external** — correcting a `RAW` forecast a consumer has read needs no review machinery |
| **Evidence disposal is not an open question** — I wrote that supersession "must decide" what happens to a superseded forecast's evidence | ⛔ It does not decide: **migration 0057 rejects `UPDATE`, `DELETE` and `TRUNCATE`** on evidence and blobs. It must keep it |
| **The evidence consequences sat in the measured claim, not the contract** | T3's In now carries them as requirements, with verification |
| 🔴 **A resume-specific hazard I had not drawn out** | A combination builds its evidence from **in-memory** contributor IDs, which the store checks against **persisted** evidence. Resuming after contributors committed but before their combination writes an **immutable** `contributor_evidence_not_persisted` record. Contributor identities must be resolved from the store first |
| **T2's schema prose was wrong twice** | The options need *different* migrations — (a) the CHECK, (b) the index — and `metadata.py` carries the same unreachable predicate, so it is not "model right, deployment wrong" |

✅ **Verified again: every `§ (n)` reference still resolves** after inserting claim 9 and renumbering
the old 9 to 10.

⚠️ **Still not executable, and deliberately so:** D1, D2 and D3 are OPEN. They are implementation
gates — equivalence policy, concrete conflict behaviour, and the schema branch — ⛔ *not things an
implementer may infer.*

**2026-09-25 — all three decisions CLOSED by the owner, and the plan GREW.**

| decision | answer |
|---|---|
| **D1** | A failed day **must** be re-runnable (the cheap alternative — accept the gap, tomorrow covers it — was rejected: *"skill scoring, verification and anything looking back at our record will have a hole in it"*). On differing numbers: **replace, keep the old marked**. Silent overwrite stays forbidden. |
| **D2** | **(a) — make the dead exception work.** Add the status so the predicate becomes reachable. |
| **D3** | In the architecture, at Flow 1 step 1.11. Flows 2/6/7/8 named as **unexamined**, not assessed. |

🔴 **The scope consequence, stated rather than absorbed:** D1's second answer moves supersession
from *specified* to **BUILT** — it is the ordinary conflict path now, not a future refinement. Two
earlier drafts deferred it. **T4 is new**, and the phase graph is resequenced: the CHECK migration
(T2) must land before the mechanism that needs it (T4), and T3's conflict path calls into T4.

⚠️ **What the owner's answers make load-bearing:** the equivalence comparison. It selects between
*"succeed, change nothing"* and *"supersede"*, so an error in the "identical" direction silently
discards the correction the re-run existed to deliver — the exact failure D1(c) forbids, reached by
accident instead of by design.

⛔ **Dropped, not carried:** T2's absence-assertion branch for D2(b). *Keeping a test for a rejected
option is how a plan accumulates contradictions — this one had two phase graphs at one point.*

**2026-09-25 — SPLIT, at the owner's direction.** A fourth review found the plan not executable and
its minor finding named why: *"phase-level ordering is right; task completion ordering is not — T3
is scheduled before T4 while T3's differing-content verification requires T4."* ⇒ **Resume and
supersession were entangled, and the split dissolves it rather than patching the schedule.**

| stays here (327) | moves to **328** |
|---|---|
| the architecture specification (T1) | the CHECK-constraint migration and the `SUPERSEDED` status (was T2 / D2) |
| resuming an **identical** re-run (T2) | the supersession mechanism (was T4) |
| **refusing** a differing re-run — today's behaviour, no regression | turning that refusal into a replacement |
| what "identical" MEANS — now defined, not deferred to an unnamed policy | the consumer work: who may serve a superseded row |

⭐ **The split also fixes a contradiction the review flagged three times**: this plan kept saying
supersession was "deferred" and "specified, not built" while the owner had chosen to build it. Both
are now true statements — it is not deferred, it is **Plan 328's**, and this plan genuinely does not
build it.

⚠️ **Also folded from round 4 before splitting:** "unchanged" and "no second row" now carry an
**equivalent-content** qualifier (they were unconditional, which contradicted the differing case);
an identical retry raises **nothing** rather than a semantic conflict; and the refusal must reach
**alert selection**, because § (7) means a store-level refusal alone still leaves the caller free to
alert on content the store would not keep.

**2026-09-25 — post-split review: 2 major. The first would have made this plan do NOTHING.**

| finding | effect |
|---|---|
| 🔴 **The evidence refusal made the plan a no-op** | My table refused when the stored row carried an incomplete-evidence marker. `forecast_evidence.py:133-145` appends a reason on **every** branch — including the valid-digest one — so **every forecast is `INCOMPLETE`**. Every resume would have refused. ⇒ **Evidence is removed from the table entirely**; its obligations stay in T2, where they belong |
| **The table's conditions overlapped** | equal values/artifact satisfied both IDENTICAL and the QC refusal, and the evidence condition cut across every row with no precedence. ⇒ **Ordered evaluation, first match wins** — mutually exclusive by construction |
| *Q3 qualification* | ids alone **can** produce `contributor_evidence_mismatch`, not inevitably — only when the hashes disagree. Intermittent, which is worse |

⭐ **The lesson: a conservative default is not automatically safe.** *"Refuse when unsure" read as
prudent and would have disabled the feature, silently, while every test on synthetic complete
evidence passed.*
