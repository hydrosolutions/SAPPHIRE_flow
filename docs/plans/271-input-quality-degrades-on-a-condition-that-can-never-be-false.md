---
status: DRAFT
revised: 2026-09-11
created: 2026-09-11
plan: 271
reviews:
  - "claude 2026-09-11 r1 design/proportionality — NOT READY, 2 blockers + 4 majors + 3 minors; all verified, all folded"
  - "codex 2026-09-11 r1 citation verification — NOT READY, 2 blockers + 2 majors + 2 minors; all verified, all folded"
  - "gpt-6-astra 2026-09-11 expert consultation on D1 — recommends complying with the FI mapping; folded as the recommendation, not as the decision"
title: Three documents disagree about what a state-free model's warm-up source is, and the running code picks the one that degrades every forecast
scope: Resolve the three-way conflict over what `warm_up_source` means for a model that holds no state, make the classifier honour the resolution, and correct whichever documents lose. Covers the `warm_up` category ONLY. NOT implementing warm-up state persistence (already built, see §What already exists), NOT a change to the ForecastInterface signature, NOT the `observation`/`NWP`/`forcing` categories, NOT Plan 270's forcing-gap detection.
depends_on: []
blocks: []
related: [270, 023, 262, 253]
source: 2026-09-11 — measured on the mac mini in Plan 261 T1's first post-deploy cycle (12:00Z, 514 forecasts). Plan 270 measured the same saturation independently the same day and explicitly scoped it out as unowned (`270:139-141`). Host numbers are from that host on that day and are NOT verifiable from the repository; re-measure before quoting.
---

# Plan 271 — three sources disagree, and the code picks the worst one

> ⚠️ **Plan numbers 270 and 271 are not PR numbers.** PR #270 is Plan 261's merge (`75cf80cf`).

## Status

DRAFT. The central decision (D1) is a **conflict resolution**, not a design space — three
authoritative sources already give three different answers and the plan's job is to pick one
and correct the others. A recommendation is offered below and is explicitly NOT taken; only
the owner decides.

## What was measured

Mac mini, 2026-09-11, the first cycle after Plan 261 T1 deployed (`75cf80cf` / `0.1.901`,
confirmed present in the *running* worker, not merely in the checkout).

| fact | value |
|---|---|
| Forecasts in the 12:00:01Z cycle | **514** |
| `input_quality = degraded` | **514 (100%)** |
| Carrying a `warm_up` cold-start flag | **514** — re-measured; an earlier revision said 512 |
| Also carrying an `observation` staleness flag | 2 |
| `warm_up_source = cold_start` | **514** |
| Rows in `model_states` | **0** |
| Distinct `warm_up_source` in the retained window (2026-09-04 →) | **`cold_start`, and nothing else** |

Not a Plan 261 regression, and not new: every forecast in the retained window, ~1,335/day.

## 🔴 The conflict — three sources, three answers

What should `warm_up_source` be for a model that holds no state?

| source | answer | where |
|---|---|---|
| **Our architecture document** | **`NULL`** — "NULL for ML models" | `docs/architecture-context.md:1845` |
| **The ForecastInterface contract** (co-designed with hydrosolutions) | **`FRESH`** — "a state-free FI model … always runs `WarmUpSource.FRESH` — already legal SAP3 behaviour for stateless models" | `ForecastInterface/docs/model_interface.md:82` |
| **The running code** | **`COLD_START`** | `services/operational_inputs.py:96-113` → `services/input_quality.py:120-127` |

The code's answer degrades every forecast. The other two do not.

### Why they disagree — they are answering different questions

This is not three parties contradicting each other on one question. It is three answers to
three questions, and only one of them was ever asked deliberately:

- **`NULL`** was written when warm-up was conceived as a *conceptual-model* concept.
  `architecture-context.md:108-112` describes warm-up entirely in terms of conceptual models
  deriving soil moisture, snow and groundwater, with ML models explicitly excluded ("ML models
  do not produce state; this step is a no-op for them"). `NULL` there means **the axis does
  not apply to this model**.
- **`FRESH`** answers a different question: given SAP3's *existing* three-member vocabulary,
  which member does a state-free model map onto? FI's answer is the member meaning **no
  deficit** — and it explicitly calls that "already legal SAP3 behaviour".
- **`COLD_START`** answers no question at all. It is the unconsidered default: `load_warm_up_state`
  returns `COLD_START` whenever the store is empty, and the store is empty for every model that
  does not produce state. The rule dates to commit `77937c1b` (2026-04-13), implementing
  **Plan 023 Step 2** — the WMO-compliance plan — which predates both other statements.

### 🔑 The proposed resolution (RECOMMENDED, NOT TAKEN — this is D1)

`NULL` and `FRESH` make the **same operational claim**: this forecast carries no warm-up
deficit. They differ only on whether the axis is "not applicable" or "applicable and nominal".
So the conflict is resolvable without declaring anyone wrong. The proposal:

> **`FRESH` = the axis applies to this model and there is no deficit** — including every
> state-free model, FI-routed or native.
> **`NULL` = there is no single model whose warm-up this could describe** — which today means
> combined products only (`services/forecast_combination.py:535` already does exactly this).
> **`COLD_START` = a model that CAN hold state was expected to have one and does not.**

Adopt FI's `FRESH`, and **amend `architecture-context.md:1845` in the same change** so the
repository carries one answer rather than two. Three reasons:

1. FI is the cross-organisation contract and `CLAUDE.md:70-77` makes compliance mandatory.
   This is the "our side violates the FI → fix our side" path, not the "file an FI issue" path.
2. `NULL` is already taken on the sibling field: `input_quality = None` means *unknown/legacy
   row* (`architecture-context.md:112`). Overloading `NULL` to also mean "deliberately not
   applicable" destroys the distinction between "we did not assess this" and "we assessed it
   and there is nothing to report".
3. Project type rules prefer an explicit enum member over an overloaded `None`.

⚠️ **The amendment is the deliberate part.** Correcting a trusted internal document to match an
external contract must be a recorded decision, not a quiet edit. If the owner prefers `NULL`,
that is a divergence from FI and must go upstream as an FI issue per `CLAUDE.md`, not be taken
silently.

⛔ **This is NOT an FI gap.** FI's state-free design is deliberate and documented, and FI has
already specified the SAP3 mapping. Filing an FI issue here would be the wrong one of
`CLAUDE.md`'s two paths.

## What already exists, so nobody re-scopes it

- **Warm-up state persistence is fully built and wired.** `protocols/stores.py:562`,
  `services/operational_inputs.py:96-113`, and five `store_state` call sites in
  `flows/run_forecast_cycle.py` (`:2906`, `:2939`, `:3194`, `:3273`, `:3561`). The only missing
  piece is a model that returns non-`None` state. Do not plan a build here.
- **The "not applicable" representation exists**: `WarmUpSource | None`, with
  `input_quality.py:120` suppressing the whole block on `None`.
- **`WarmUpSource` has exactly three members** today — `FRESH`, `SNAPSHOT`, `COLD_START`
  (`types/enums.py:23-26`).

## Why this is worth fixing

**A flag that is always on is not a degraded signal, it is no signal.** The two forecasts in
that cycle with a REAL problem — 85.4 h stale observations — are indistinguishable from the
512 carrying a structural artefact.

**There is exactly one real consumer, and it is broken today.** The API's `degraded_only=true`
filter (`api/routes/api_stations.py:274`, `store/forecast_store.py:243`) returns 100% of rows.
⚠️ **No dashboard indicator exists** (`architecture-context.md:112`) — an earlier revision of
this plan claimed dashboard harm that does not exist.

**It does NOT threaten the WMO claim.** `docs/standards/wmo.md:188` scopes its evidence to
"persistence and authenticated API serialisation ONLY" — it never rested on the cold-start
rule. Plan 023 owns that rule and should be cited when it changes.

## Consumers — who reads this field

| consumer | reads | effect of the change |
|---|---|---|
| `api/routes/api_stations.py:274` + `store/forecast_store.py:243` | `degraded_only` filter | Stops returning 100% of rows. **This is the point.** |
| `services/forecast_combination.py:348-375` | contributor `input_quality` flags, inherited onto combined products | Combined products stop inheriting a spurious `warm_up` flag; their own `warm_up_source` stays `None` (`:535`) |
| `docs/standards/wmo.md:188` | compliance evidence row | Unaffected — scope is persistence + serialisation |
| Dashboard | — | **No indicator exists.** No effect. |
| Tests | 17 `COLD_START` references across 5 files incl. `tests/integration/test_e2e_pipeline.py` | ⚠️ The change is NOT a one-liner. These encode the current rule. |

## The decisions

**D1 — Resolve the three-way conflict.** Recommendation above; the owner decides. Whichever
answer wins, the two losing documents must be corrected in the same change, and if the answer
diverges from FI it goes upstream as an FI issue rather than a silent divergence.

**D2 — How does the system identify a model that holds no state?**
⛔ **NOT by adapter identity.** `linear_regression_daily.py:52`, `climatology_fallback.py:38`
and `persistence_fallback.py:36` are native SAP3 classes that never touch the FI adapter and
are equally state-free — route derivation would leave all three still flagged and would not
clear the saturation. ⛔ **NOT by an empty state table or a single `None` return** — a stateful
model that LOST its state looks identical on its first cycle, and must not receive the same
exemption. ⭐ **Recommended: explicit model capability**, the mechanism FI itself reserves — an
optional `StatefulModel` sub-protocol detected by `isinstance`, exactly as SAP3 already detects
`RetrainableModel` (`forecast_interface/interface/protocol.py:55-58`). Covers native and
FI-routed models uniformly.

**D3 — What happens to the ~7 days of rows already labelled `degraded`?** All three reviews
converge: reclassify ONLY where model/version provenance establishes state-free behaviour,
remove ONLY the erroneous cold-start contribution, preserve every other flag, recompute the
aggregate, and keep the correction auditable. ⛔ Never bulk-mark the interval healthy. If
provenance cannot establish it, retain the rows and record the affected interval instead.
⚠️ Not yet measured: whether the 514 includes combined products, which changes what is
reclassifiable. Measure before deciding.

**D4 — Does this ship before Plan 262's pilot?** Owner's call. Note the pilot does NOT depend
on it: `cmal_small` is pure ML with a 30-day lookback and reconstructs its state from that
window every cycle, so it needs no persisted state and is correctly state-free
(`262:84`, `262:91`; FI `model_interface.md:73`). It will simply be one more model the resolution
covers. ⚠️ An earlier revision argued the pilot made this urgent because cold start would be a
"real degradation" for a DL model. That was wrong, and its phrasing was verbatim the
`CLAUDE.md` path-2 trigger this plan forbids.

## Tasks

Not written until D1 and D2 are settled — they determine the shape, and drafting tasks first is
the prejudging failure the time-grid family spent four review rounds undoing. **This is a
decision-framing plan, not an implementation plan yet** (same posture as Plan 270). Once D1 and
D2 land it gains: the classifier change, the document correction D1 requires, the capability
detection from D2, the historical-row handling from D3, updates to the 17 test references, and
the doc updates.

## Exit gates (provisional — firm up once tasks exist)

1. In one staging cycle, the `warm_up` category produces **no flag** for state-free models,
   while the two stale-observation forecasts still carry their `observation` flag. ⚠️ Stated on
   the `warm_up` category's flag presence, NOT on the aggregate `input_quality` level — Plan 270
   and Plan 239's forcing flags move the same aggregate, so the aggregate cannot isolate this.
2. A locking test proves a model that CAN hold state and is missing it is STILL flagged
   `COLD_START` and still degrades. ⚠️ Scope this to the **deterministic route**: stateful models
   are rejected on the ensemble fan-out as `UNSUPPORTED_STATEFUL_ENSEMBLE`
   (`services/run_station_forecast.py:434`, `:536`) and ensemble-first is a locked decision, so
   the ensemble route cannot exercise it.
3. `degraded_only=true` returns a strict subset of forecasts, not all of them.
4. Exactly one answer to "what is a state-free model's warm-up source" survives in `docs/`.
5. No change to the `observation`, `NWP` or `forcing` categories; FI unchanged, with the reason
   recorded so it does not read as an omission.

## Related, explicitly NOT in scope

- **Plan 270** — forcing-gap detection. Measured the same saturation, correctly scoped it out
  as unowned (`270:139-141`). 271 is the owner it was waiting for; neither changes the other's
  subject.
- **Plan 023** — introduced the cold-start rule (commit `77937c1b`). Its rationale should be
  read before overriding it. ⚠️ An earlier revision of this plan misattributed the rule to
  Plan 239, which added the *forcing* flags to an existing gate.
- **Plan 262** — consumes the resolution; does not make it.
- **Building warm-up state persistence.** Already built (see above). What is genuinely open is
  whether any model will ever produce state, given FI is state-free by design and stateful
  models are refused on the ensemble route. Unowned, not started here.
