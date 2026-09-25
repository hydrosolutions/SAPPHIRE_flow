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
   ⚠️ Missing capture writes an **incomplete-evidence marker**, and historical forecasts need not
   have an evidence row at all — so any comparison must tolerate both.
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

### D1 — what does "retry" MEAN? **OPEN — answer before anything is built.**

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

**Recommendation: build (a) now, specify (b), forbid (c) outright.** ⭐ *(a) is the operational
pain today.* ⚠️ **(c) is forbidden from today**, not from first publication — see its row.

⛔ **(b) is deferred as a SCOPE CHOICE, not because it waits on anything.** *Two earlier versions of
this line said it "becomes required once a forecast is published" and then that it "needs a review
lifecycle". Both are wrong: correcting a `RAW` forecast a consumer has already read needs no review
machinery at all.* What (b) actually needs is D2's supersession mechanism and the evidence
obligations in § (9) — and this plan chooses not to build that in the same pass as (a). ⇒ **A
deliberate deferral, with nothing external gating it.**

### D2 — fix the dead predicate, or remove it? **OPEN.**

| | option | cost |
|---|---|---|
| **(a)** ⭐ | **Add `SUPERSEDED` to `ForecastStatus`** and to the DB CHECK (`metadata.py:1130`), making the predicate REACHABLE. ⛔ *This does NOT implement D1(b)* — the transition, who may perform it and what happens to `forecast_values` are D1(b)'s, which this plan specifies and does not build. | Makes the schema's evident intent reachable. Needs the status threaded through every reader that filters on status. ⚠️ **A schema migration is required; what § (2) removes is the need for a status BACKFILL, not the migration.** |
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
⚠️ *"No backfill" is not "no migration".* ⛔ *But the two options need DIFFERENT migrations, which
an earlier line flattened into "the index must be replaced either way": **D2(a) migrates the CHECK
constraint** and leaves the predicate as it is (it becomes reachable), while **D2(b) replaces the
index** to drop the predicate.*
⛔ Implementing supersession (D1b) — D2(a) makes the predicate REACHABLE, nothing more.

**Pre-change.** 🔴 **Branches by D2 — the two options need OPPOSITE tests**, and the first draft
prescribed only the first:
- **D2(a)** — a RED test asserting **every value in the index predicate is a member of
  `ForecastStatus`** (the metadata predicate's `right.value` against
  `{st.value for st in ForecastStatus}`). Fails today, because `superseded` is not.
- **D2(b)** — ⛔ *there is no predicate left to compare.* The test asserts its **ABSENCE**
  explicitly. ⚠️ **A comparison over an empty predicate passes vacuously** and would prove nothing,
  which is how this defect survived in the first place.

**Verification.** Per D2's branch: either the predicate and the enum agree by value, or the index
carries no predicate at all — **asserted against the MIGRATED schema** as well as the model. ⛔ *An earlier line said the model
was right and only the deployment wrong — false: `db/metadata.py` carries the same unreachable
predicate. Both are wrong in the same way, which is why the test must compare a VALUE and not
merely diff the two.*

### T3 — Make a half-finished cycle re-runnable (D1a)

**Outcome.** Re-running a cycle that died partway completes it instead of failing on what it
already wrote.

**In.** D1(a)'s mechanism at the store boundary, at **CYCLE granularity**: the stations already
written stand, the rest complete. ⛔ **NO orphan-repair work** — § (4) proves a header without its
values cannot occur, and the first draft's requirement to handle one was building for an impossible
state.
🔴 **Evidence requirements, from § (9) — obligations, not observations:**
- A resumed cycle **resolves contributor identities from the STORE before building a combination's
  evidence**, so it cannot write an immutable `contributor_evidence_not_persisted` record for
  contributors that are in fact persisted.
- An **equivalent** retry leaves the existing evidence and blobs in place — ⛔ *it may not rewrite
  them; 0057 rejects `UPDATE`/`DELETE`.*
- Comparison **tolerates an incomplete-evidence marker and a legacy forecast with no evidence row**.
- The whole set still rolls back atomically on failure.

🔴 **The conflict resolution must reach ALERT SELECTION, not stop at the store.** § (7): the stored
identity is discarded and alerts consume the in-memory ensemble, so a store-level rejection still
leaves the caller free to alert on the rejected content. What the caller receives, and what alerting
is allowed to consume, are part of this task.

**Out.** ⛔ Silent overwrite (D1c) — forbidden outright, not only after publication.
⛔ **Correcting** a `REVIEWED` or `PUBLISHED` row — that is D1(b), specified not built. ⚠️ *The
REFUSAL to touch one belongs here, so the behaviour cannot regress into (c) later.*
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
- A cycle interrupted between stations, then re-run: the stations already written are **unchanged**,
  the rest complete, and 🔴 **no error is recorded for the ones that were already there.**
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
- 🔴 **A genuine duplicate — same issue time, same model, already complete — still does NOT write a
  second row.** ⭐ *The constraint's protective half must survive; this plan removes an obstacle, it
  does not remove the guard.*
- 🔴 **A cycle resumed between the contributors and their combination does NOT produce a
  `contributor_evidence_not_persisted` record** — asserted, because that record is immutable once
  written and this is the resume path's own failure mode.
- 🔴 **Alerting cannot consume content the store refused** — asserted end to end, because § (7)
  means a store-level guard alone does not achieve this.
- A **semantic retry conflict** reaches the caller as a domain error, while an **unrelated storage
  failure still propagates raw** (§ 3, preserving Plan 038 D5). ⛔ *Both halves asserted — wrapping
  everything is the regression this clause exists to prevent.*

## Explicitly out of scope

- **The review/publish lifecycle.** ⛔ *Not because D1(b) depends on it — it does not.* Correction
  is needed for any forecast a consumer has already read, `RAW` included. The lifecycle is simply
  someone else's work, and (b) is deferred by this plan's own choice (see D1's recommendation).
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
