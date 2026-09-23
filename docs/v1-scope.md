# v1 scope — what October 2026 actually delivers

**Status: current as of 2026-09-20.** Written at the owner's instruction after a cross-plan
decision review returned *incoherent* — not because individual decisions were wrong, but
because nothing stated what they jointly produce. `docs/v0-scope.md` is the equivalent
document for v0; there was no v1 equivalent until now, and its absence is how the gap below
went unnoticed.

⚠️ **Read this before quoting a v1 capability to anyone outside the project.**

---

## Purpose — v1 is a DEMONSTRATION

*Owner, 2026-09-20. Recorded here because its absence is what let the tactical decisions below
collide: there was nothing above them to arbitrate.*

> **v1 is something DHM can test and evaluate.** We propose it, they assess it, and from there
> we fine-tune, improve QC, and eventually connect alerting. **Nobody is going to act on the
> output any time soon.**

⇒ This settles a class of question that kept re-opening. Alerting being closed is **correct,
not a gap**. An accuracy score that cannot yet be believed is **acceptable**. A provisional,
marked value is **fine** — the system is not load-bearing for anyone's safety in v1.

⚠️ **But one thing gets HARDER, not easier, under a demonstration framing.** The audience is
DHM — the people who know these rivers. A number that is visibly wrong to a domain expert
discredits the demonstration far more than a gap does. *That* is why, for example, a flood-day
figure computed only from the hours below a conversion ceiling is unacceptable: not because
someone would act on it, but because the evaluator would spot it.

## QC posture — LOOSE FIRST, narrowed with data

*Owner, 2026-09-23. Recorded because it settles, in one stroke, a set of questions that had
generated six review rounds across four plans without converging.*

> **This is a first iteration and a test deployment. We do not need a finished product
> tomorrow.** We can delete flags, re-establish them and change them freely for now. **We will
> not use this data operationally until QC is fine-tuned** — and only from that point do the
> data-retention guarantees have to hold as planned.

**What this means, concretely:**

1. **Thresholds start LOOSE and are narrowed with experience.** ⛔ Do NOT carry narrow
   thresholds in from the beginning. ⚠️ **This EXTENDS a precipitation-specific posture; it does
   not restore a general standard.** `docs/standards/wmo.md:89-91` states the impossibility-gate
   wording, but inside the *precipitation-undercatch* section, and its cited `200 mm/h` is the
   offline mask script's default (`scripts/dhm_precip/params.py:144`), not a deployed QC rule —
   the deployed precipitation ceiling is `500 mm/day` (`config.toml:369`). wmo.md's QC entries
   state **no** threshold posture. ⛔ *So there is no "drift from a standard"; an earlier revision
   of this section claimed one. This decision rests on the measured case below, not on wmo.md —
   which is exactly the stale-plausible-evidence trap Plan 272 T4 warns about.*
2. **Some thresholds are system- or deployment-wide; maximum LEVELS and LEVEL CHANGES are
   per station.** A single fleet-wide ceiling cannot be both a physical-impossibility gate in
   Switzerland and one in Nepal.
3. ⭐ **Relative change may generalise better than absolute.** Expressing a level-change limit
   as a proportion — of the station's own observed range, or of its current stage — rather than
   in metres per step is worth designing for, because it transfers across stations and datums
   instead of needing calibration per gauge.
4. **Consumer policy is STAGED, and the two stages differ:**
   - **Now, while QC is being built:** unchecked data **may** flow through, forecasting
     included (D5's per-site split stands). We are developing; a blocked pipeline teaches us nothing.
   - **Once QC is in place and fine-tuned:** unchecked data **must NOT** enter forecasting.
     ⚠️ Note this end state is **stricter** than the interim policy Plan 272 D5 records.

**Why the loose-first rule is not theoretical — two measured cases in the deployed config:**

- **Discharge `value_max = 5000 m³/s`** *(as deployed until 2026-09-23; now `30000`)* was an impossibility gate in Switzerland and a
  plausibility filter in Nepal: **1,125 of 1,126 range-check flags on the delivered DHM record
  fall on one station**, whose genuine monsoon peaks exceed it. Plan 268 calls it *"not QC; a
  calibration error wearing QC's clothes."*
- **Water level `−2 … 20 m` (600 s) and `−5 … 30 m` (daily)** *(as deployed until 2026-09-23; now `−10 … 9000` at both)* assumed a gauge-height datum. The Nepali feed mixes gauge height
  with metres above sea level and **nothing declares which** — measured, **31 of 193 stations
  report height above sea level** (§ The material risks) — so those 31 are "out of range" on
  every reading they ever send. ⛔ *An earlier revision of this bullet illustrated the point with
  "a station reporting ~162 m.a.s.l."; 162 is the count of stations on the OTHER side of that
  split, not a level. No reading of 162 m is measured anywhere. The datum-mixing claim holds;
  the illustration was fabricated.*

5. ⚖️ **This iteration accepts UNDETECTED selection changes — stated, not assumed** (owner,
   2026-09-23). No before/after comparison harness is built. If the selection fix silently
   changes a verdict, nothing alarms. That is acceptable **only** because nobody acts on this
   data; it stops being acceptable the moment it becomes operational.
6. 🔴 **The "flags may be changed freely" premise must be CONFIRMED, not assumed.** Measured:
   on the scheduled ingest path there is exactly one writer of a QC verdict
   (`store/observation_store.py::update_qc`), and Plan 272 D3 forbids re-judging rows already
   stored `QC_PASSED`. ⇒ **Today a changed verdict cannot be undone.** The ability to rewrite
   flags on the test deployment — a scoped `UPDATE`, not a feature — is what makes every later
   loosening possible, and it is an assumption until someone has actually run it. **Confirm it
   before relying on it.**

**What this does to the plans in flight:** the rollout apparatus that Plans 272 and 314 spent
six review rounds failing to make coherent — a canary, an automatic abort, a loss threshold, a
revert artefact — was justified by a risk that **does not exist in a test
deployment whose data is not operationally used and whose flags are freely mutable.** Size the
activation work to the iteration we are actually in.

### Success criteria — what "v1 worked" means

Owner's, verbatim in substance, and all four are testable:

1. **Produce a forecast every day.**
2. **Allow fine-tuning of configuration for Nepal cases.**
3. **Allow onboarding of further stations.**
4. **Allow re-training of models.**

🔑 **Note what is NOT on that list: forecast accuracy.** v1 demonstrates that the machinery
runs, is configurable, and can absorb more stations — not that its numbers are good. Accuracy
is what the fine-tuning *after* evaluation is for.

⭐ **Criterion 2 re-prioritises existing work.** "Fine-tuning of configuration for Nepal cases"
is, almost verbatim, Plan 269 — per-station QC thresholds declared in configuration. It has
been treated as blocked and off the critical path. **On these criteria it is ON the critical
path**, which makes the 272 → 269 → 268 dependency chain a blocker against a success criterion rather
than a scheduling nuisance.

### Who DHM are

**A project partner and beneficiary. They hold all the data, and they will be the users of
this system.** Not a supplier we design around — the eventual operators. Where a gap of theirs
forces a workaround on our side, the workaround is a bridge to something built *with* them,
not a permanent accommodation.

---

## Where this is going — level forecasting, and why

*Owner, 2026-09-20.* **The destination is to forecast water LEVEL rather than discharge.**
DHM is realistically not going to produce good rating curves for all these stations, so a
system whose every output depends on a current rating curve has no stable future.

**The obstacle, and the owner's answer to it:** level forecasting needs a history of levels to
train on, and we have none — Plan 268's delivery contained discharge and rating tables, no
level. Waiting for DHM's level archive is not realistic either. ⇒ **We invert: convert the
historical DISCHARGE back through the rating tables to reconstruct historical LEVEL, and train
on that.**

**What it produces, stated precisely.** ⛔ *An earlier revision of this section called it
"recovery, not estimation" and claimed three favourable properties. An independent technical
review found all three overstated. Corrected:*

**It produces rating-derived EQUIVALENT STAGES for approximately 95% of daily discharge
values, subject to methodological uncertainty.** Not "the original level".

- ⚠️ **"Recovery" would require DHM to have computed one daily level statistic and converted
  it once.** If they instead converted each reading and averaged the *discharges* — at least
  as likely — then inverting yields the stage *equivalent to the mean discharge*, which is a
  different quantity from the mean stage. 🔴 **Plan 268 measured that the daily aggregation
  rule is not determinable from the delivered files**, so the assumption the claim rests on
  cannot be checked.
- ⚠️ **95.34% is temporal CURVE COVERAGE, not recovery accuracy.** It establishes that a
  candidate curve exists for those dates — not that it was the curve used, nor that the
  discharge range is covered.

**What does hold, with its limits:**

- **Inversion damps error rather than amplifying it.** At each station's *median* flow a 10%
  discharge error maps to 6–11 cm of level, 20% to 11–22 cm. ⚠️ Measured at the median only,
  in one direction; the response is not symmetric, and it bounds none of the datum,
  curve-selection or aggregation error.
- **The threshold comparison is genuinely datum-free.** The portal publishes each station's
  warning and danger levels in the same reference as its readings, so a level forecast can be
  compared against that station's own danger level even if the absolute datum is never
  established. 🔴 **But a level-to-level model is NOT automatically offset-safe** — that
  requires *translation equivariance* (shifting every input by `c` shifts the output by `c`),
  which ordinary regressions, trees and neural networks do not guarantee. It is a property to
  **require of the model**, not one the approach confers.
- **The convexity bias is real, and conditional.** The stage equivalent to a mean discharge is
  **at least as high as** the mean stage — strictly higher only where the day's variation
  spans a genuinely non-linear part of the curve, and **absent entirely if DHM averaged stages
  before converting.** 🔴 **It cannot be corrected from what we hold**: the missing quantity is
  *within-day* variance, and variance across daily means does not supply it.

**🔴 The two objections that matter most, neither of which the approach answers:**

1. **Level forecasting does NOT escape the rating-curve problem — it RELOCATES it.** Curves go
   stale because channels change: scour, deposition, control shifts, backwater, gauge
   relocation. A model trained on reconstructed historical level absorbs those *same*
   historical hydraulic relationships, and does so invisibly — with no validity window and no
   expiry date. A stale rating table at least announces itself.
2. **Daily targets cannot teach within-day peak timing or threshold crossings.** An equivalent
   stage can sit well above the daily mean and still far below the flood peak. Feeding the
   model 10-minute live input does not restore information that was never in the training
   targets. ⚠️ **This bears directly on the eventual alerting destination**, which is about
   exactly those crossings.

**And one that carries over unchanged:** 🔴 **train/serve datum consistency, where 684 Tamor
still fails.** The reconstructed history sits on the *rating table's* datum, the live feed on
its own. For the four gauges whose live readings convert plausibly those agree; for 684 they
demonstrably do not. *(An earlier note in this session said level forecasting would make 684
fine. Wrong — it removes the conversion from the serving path, not the mismatch between
training and serving.)*

⇒ **Net: defensible as a source of historical proxy targets. It does not establish that those
targets are interchangeable with present-day observed levels, and it needs its own validation
rather than inheriting the discharge path's.**

**What this means for the conversion work now under way:** it is a **bridge, not the
destination**. v1 serves discharge models from live level, because discharge models are what
we have. The inversion above is what enables the successor. Both use the same rating-table
machinery, in opposite directions.

---

## The v1 headline, restated honestly

> **v1 is a daily-running, configurable, extensible forecasting demonstration on real Nepali
> DHM gauges, for DHM to evaluate.**

It is optimistic in four specific ways, each traceable to a decision made deliberately.

---

## The six gauges, as they actually stand

⛔ **This table describes INTENT, not running capability. None of the conversion path is
built.** Plans 302, 272 and 264 are all `DRAFT` and unmerged; `RATING_CURVE_DERIVED` exists
as an enum member and is called from nowhere in ingest. Read every ✅ below as “designed and
expected to work”, not “observed working”.

| gauge | live level feed | converts to discharge | trainable model | discharge in its danger band |
|---|---|---|---|---|
| 447 Trisuli / Betrawati | ⛔ **absent** | — | — | — |
| 450 Narayani / Devghat | ✅ | ✅ | ✅ | ✅ |
| 604.5 Arun / Turkeghat | ✅ | ✅ | ✅ | ✅ |
| 647 Tamakoshi / Busti | ✅ | ✅ | ✅ | ✅ |
| 670 Dudh Koshi / Rabuwa | ✅ | ✅ | ✅ | ✅ |
| 684 Tamor / Majhitar | ✅ | ✅ (marked contradicted) | ✅ on DHM's own history | ⛔ **no** |

**⇒ As designed, five of six gauges would forecast; one has no live input.**
⚠️ **"Would", not "does".** No part of this is running: see the caveat above.

**447 Trisuli has no operational level feed** — measured 2026-09-20 against the public portal
(193 stations across both pages, no Trisuli/Betrawati present; ⚠️ a point-in-time read, not a
standing fact). Nothing is broken; there is simply no live input.

⚠️ **Two distinctions an earlier revision blurred.** It **is trainable** — Plan 268 records
1977–2019 of published discharge for it, so absent *live* input is not absent *training* data.
And it will **not** "start working the day a feed appears": `adapters/dhm.py` requires an
explicit station-to-API binding, so a new feed needs configuration, validation and activation
before anything flows.

**684 Tamor is anomalous on three independent measurements**, from three unrelated datasets:
its live level converts through its own rating table to a discharge above its entire 24-year
record maximum while the portal reports it below warning level; its published danger level
lies above the top of its own rating table; and its delineated catchment is ~9% larger than
DHM's published area, where the other five agree within 3.7% — **measured from the
`area_diff_pct` column of `gauge_coordinates.csv` in the 2026-09-20 basin/static handover
package, which lives outside this repository.** ⚠️ **None of the three is verifiable from
this repository**: the first two rest on a point-in-time read of the public portal on
2026-09-20, the third on that package. It is **to be** onboarded, converted and forecast — on DHM's own historical discharge, not on our conversion — and every value it
produces is marked. DHM has been asked to confirm its location, area and current rating.

---

## What v1 does NOT deliver

These are consequences of decisions taken deliberately. None is an accident; none was
written down as a scope reduction until now.

### No flood alerting on these gauges — ✅ correct by design, not a gap

⚖️ **The owner's sequence is: demonstrate, evaluate, fine-tune, improve QC, and connect
alerting last.** So this is the intended v1 state and should not be read as a shortfall.

⚠️ **What actually keeps it shut is configuration** — `enable_forecast_alerts = false` and
`enable_observation_alerts = false` in `config.toml` (verified). *An earlier revision said the
missing provenance field disabled it; that is wrong — the field's absence is a reason not to
re-enable it, not the mechanism.*

**Three things must happen before alerting opens, not one:** `types/alert.py::Alert` gains
provenance fields (verified absent today), **the markers are actually propagated into them**,
and the path is explicitly activated and validated. Widening the type alone changes nothing —
it just stops the caveats being discarded at the last step. Named follow-on work, no plan
number yet.

At 684 specifically, the conversion ceiling sits below the gauge's own published danger
level, so readings above it are refused.

**⚖️ Owner decision, 2026-09-20 — the rule turns on WHY readings are missing:**
- **Lost at random** (a dropout, a brief outage) ⇒ the day is **published with a coverage
  marker**. The average is noisier but not systematically wrong.
- **Lost to an envelope refusal** (above the ceiling, or below the floor) ⇒ **no daily figure
  at all.** Those losses remove the *highest* — or lowest — values by construction, so the
  surviving average is wrong in a predictable direction and would look entirely ordinary.

⇒ **On a flood day at 684 there is no daily discharge figure**, which is the original
acceptance. *(Two earlier revisions each stated one half of this and contradicted the other.)*

### 🔴 No named route by which DHM actually evaluates this

**This is the gap that most directly threatens the purpose**, and it was not previously
listed as one. v1 exists for DHM to test and evaluate — and nothing states *how they see
anything*.

The Forecast Lab export excludes these gauges twice over: station eligibility requires
`network == "bafu"`, and observations are filtered to `source = MEASURED` while every v1
discharge value would be `RATING_CURVE_DERIVED`. Removing one filter would not admit them.
The export schema also carries no input-quality fields, so it could not show the provenance
markers this project spent its effort building.

The REST API exists and can serve the data. **But no one has named the interface, the access
route, or how a provisional marker is presented to a DHM evaluator.** Until that is decided,
"DHM can test and evaluate" is an intention without a mechanism.

### No believable accuracy score, yet

Models train on DHM's published daily discharge and serve on our rating-derived discharge.
Whether a model trained on the first may legitimately serve on the second is an open
scientific question (Plan 302 D4), accepted rather than solved. It does not block building
the pipeline; it does block trusting a skill number.

⚖️ **Acceptable for v1** — accuracy is not a success criterion, and the fine-tuning that
follows DHM's evaluation is where it gets addressed.

⛔ **An earlier revision said this "resolves itself once enough operational discharge has
accumulated to retrain on". Withdrawn — twice wrong.** Retraining reduces a train/serve
*distribution* mismatch; it does not validate a stale rating, an unconfirmed datum, or the
conversion used to build the reference series. And the owner's actual direction is **not** to
accumulate operational discharge but to **reconstruct historical level by inverting the
discharge record** (§ Where this is going), which needs no waiting at all.

**What would actually resolve it**, stated separately because they are separate:
**historical hindcast skill against DHM's own record is meaningful today**, within its stated
domain — the limitation is specifically **operational** transfer, not evaluation as such;
operational transfer validity is **unvalidated** and could be investigated directly rather than
only via the level successor (which is the chosen development direction, and needs its own
validation — it does not inherit this one's); and discharge-truth uncertainty needs rating
curves nobody expects.

### No current rating tables

DHM has stated it cannot supply current rating curves; this is blocked at source, not by us.
Every v1 discharge value therefore rests on a rating table past its stated validity, carried
under a deliberate 2,557-day (7-year) tolerance. **For the five gauges that actually produce
values the range is 2.0 to 6.2 years.** *(An earlier revision said "0.7 to 6.2"; the 0.7
figure belongs to 447, which has no feed and produces nothing — it flattered the number.)*

🔴 **That tolerance is the INTENDED deployment setting, not a development affordance** (it
is not configured anywhere yet) — and
**four** of the six gauges cross it inside an eleven-day window in **July 2027**: 604.5 and
647 on the 11th, 670 and 684 on the 21st. That is **four of the five gauges that have a
feed** — 80% of the operational fleet — roughly nine months after the v1 date. Nothing
currently plans for it. *(An earlier revision said "two"; corrected 2026-09-20.)*

Worse, the staleness figure measures the wrong clock: it counts days since the table was last
*in force*, not days since the rating was *established*. The 112 delivered tables are only 65
distinct point sets — DHM reuses them. On the establishment clock, five of the six gauges are
between 7 and 40 years old. The published number is honest only if read with that sentence
attached.

---

## The delivery route — how DHM actually evaluates this

*Owner, 2026-09-20, answering the gap the review found under § Purpose.*

**Three surfaces, in order of how DHM will use them:**

1. **A small dashboard, deliberately kept at MVP**, for visual inspection of forecasts.
   ⛔ **Built in a separate repository (`sapphire-flow-map`) — NOT in this one.** Nothing in
   this repo implements it. What this repo owes is the data contract behind it.
2. **CSV download** of forecasts *and* forecast skill metrics, for DHM's own internal
   processing. 🔴 **No CSV export exists today** — nothing in `api/routes/` produces one. This
   is new work, not wiring.
3. **API access**, so they can experiment with retrieval. ✅ **Close to ready**: token-based
   auth already exists with a station-scoped `consumer` role and an `admin` role, so issuing
   DHM a scoped token is configuration rather than a build.

**⛔ Their own dashboard is the destination, ours is a courtesy.** DHM plan to integrate our
forecasts into their existing forecast dashboard. Ours is available to them if they want it,
but **it is not a deliverable** and should not be built as though it were.

### Every value carries its flags — and this is where the whole marker effort lands

⚖️ **Owner: all data is provided with flags.** That is the delivery-side obligation of
everything this project has built — the curve age, the range flag, the datum confidence, the
thin-day coverage marker and the input-quality signal.

🔴 **It matters twice over, because of surface 3.** Once DHM pull our forecasts into their own
dashboard, the data has left our sight. **A caveat that is not in the payload cannot be added
back.** If a provisional or extrapolated value arrives in their system unmarked, it is
indistinguishable from a confident one, permanently.

**Measured state today:** the forecast schema carries `input_quality` and
`input_quality_flags` (`api/schemas.py:104-105`). ⚠️ **The observation schema carries only
`source`** — none of the derivation markers. So a CSV of derived discharge would currently
say nothing about which values are extrapolated, provisional, or from a contradicted gauge.
**Closing that is a v1 delivery requirement, not a refinement.**

### QC output is a deliverable, not diagnostics

⚖️ **Owner: QC results must be an output somewhere, because there will be a lot of QC
fine-tuning.**

🔑 **This is success criterion 2 with a surface attached.** "Allow fine-tuning of
configuration for Nepal cases" is unachievable if nobody can see what the current
configuration *did* — which rules ran, which fired, on what, and which groups resolved no
rules at all. **You cannot tune what you cannot observe.** It connects directly to Plan 272's
zero-rule observability and to the per-station threshold work.

### Nepal day — ⚠️ ALREADY OWNED; this is not a new finding

⚖️ **Owner: the Nepal day is the output, and the dashboard displays Nepali time. Internally
the forecast tool can stay in UTC.** Nepal is **UTC+05:45**.

⛔ **An earlier revision of this section presented the day boundary as an undiscovered
problem. It is not.** The owner raised NPT on **2026-09-04** while reviewing Plan 253, and a
plan family already exists for it:

| plan | status | what it owns |
|---|---|---|
| **252** — *a time grid is a step AND a phase* | `DRAFT` | `TimeGrid(step, phase)`; **declaring the operational day boundary per deployment**, with a per-station override |
| **254** — *phase-aware execution* | `DRAFT` | **the resampler call sites**, fetch-bound helpers, daily-model anchoring, Forecast Lab bounds, the Swiss retrain |
| **258** — *point or interval* | `DRAFT` | temporal support |
| **234** — *honour declared aggregation* | — | threading aggregation end to end |
| **099** — *dashboard display timezone* | `PARTIAL` | axis labelling (P1 shipped) and the display toggle (P2 open) |

**The observation that the resampler has no phase offset is correct and is precisely what
Plan 254 exists to fix.** Nothing further is needed here beyond honouring it.

**🔑 And the training-mismatch worry raised earlier is MOOT today.** Plan 254 states it
directly: *"Out: Nepal, which has no artifacts to retrain and no cutover."* There are no
Nepal models yet, so there is nothing mis-trained. The owner's expectation is that the
modeller converted Nepali data to UTC and trained in UTC, which is consistent with the
UTC-internal design. **It becomes a live question when Nepal artifacts are first built** — at
which point 252's declared day boundary governs, not an ad-hoc choice.

**⚠️ Two things this delivery route DOES add, which the existing plans may not cover:**

1. **Plan 099 is about THIS repository's dashboard** (`api/templates/`), and its recorded
   owner direction is *"default to the viewer's own browser locale"* (2026-09-09). The DHM
   route uses **`sapphire-flow-map`**, a different dashboard in a different repository, and
   today's direction for it is **Nepali time specifically**. In practice a DHM evaluator's
   browser locale *is* Nepal time, so they usually coincide — **but they are not the same
   rule**, and a viewer abroad or with a misconfigured browser would see a different day.
   Worth stating which rule the DHM-facing dashboard follows.
2. **The CSV export is a new consumer of the day-boundary decision.** 254 covers the resampler
   and the Forecast Lab bounds; a Nepal-day CSV for DHM is an additional surface that must
   take its boundary from 252's declaration rather than choosing one.

✅ **One piece of good news for the route:** `sapphire-flow-map` is **already an authenticated
consumer of this API** — Plan 215 records its consumer token holding 37 station grants as of
2026-08-29. The delivery route is extending an existing integration, not building one.

### Open questions for DHM, arising from this route

1. 🔴 **Do you want level or discharge?** Your portal, your thresholds and your operational
   practice are all in **level**; v1 delivers **discharge**. Integrating our forecast into your
   dashboard means either displaying a quantity you do not normally use, or converting it back
   through the same rating tables — which is circular. *If the answer is level, the inversion
   work moves up the priority list considerably.*
2. **What day convention is your published daily discharge on?** See above.
3. **What do you want the skill metrics to mean?** They are operationally unvalidated (§ risks);
   the caveat must travel **inside** the CSV, as a column or header, not in a covering email.

### Two smaller things the route needs

- **Deliberate gaps must be distinguishable from failures.** There are now several reasons a
  value legitimately does not exist — a refused reading above a conversion ceiling, a cadence
  gap, unchecked data. On a dashboard these render identically to a fault. **If DHM file them
  as defects, the evaluation measures the wrong thing.**
- **Reproducibility.** A download today and a download after a retrain or a rule change will
  differ. The rule-version stamps this system already keeps internally belong in the export,
  or a comparison between two downloads means nothing.

## The material risks, stated once

⚠️ **Read these as EVALUATION risks, not safety risks.** Nobody acts on v1 output, so none of
them endangers anyone. They matter because each one can make the demonstration **impossible
for DHM to judge fairly** — a wrong number they spot, or a caveat they cannot see, costs the
evaluation either way.

1. **The level feed's datum is unconfirmed.** Measured: the public feed mixes two conventions
   — 162 of 193 stations report a gauge reading, 31 report height above sea level — and
   nothing in the response declares which. Four of our five live gauges convert to seasonally
   plausible discharge, which is good corroboration but not proof. Until DHM confirms, every
   derived value is stamped provisional at the row level.
2. **Extrapolation past the rating tables is bounded but real.** Validated against the
   delivered data at 362 points, agreeing with DHM's own table construction to within a few
   percent out to one metre above the table. ⚠️ **That is agreement with DHM's own table
   construction — numerical consistency, not physical accuracy.** Published figures for
   extrapolated high flows at **one published site** range from **41% to 200%** (full-width 95%
   intervals across methods); the often-quoted 25% is a
   different quantity (~26%, at twice the highest *gauged* flow) and should not be used here.
   ⚠️ The 41–200% figures are full-width uncertainty intervals from a **single published site
   study**, not a Nepal-specific estimate — **uncertainty at these six gauges is
   unquantified.**
3. **Observation QC on these gauges is not yet trustworthy.** Plans 272 and 264 are
   prerequisites for *operational activation*, not for building. Until they land, a DHM level
   reading can be recorded as having passed quality control with zero rules having run.
4. **Nepali QC threshold values are unresolved — owned by Plan 268 D14, which is OPEN.** The
   deployed water-level thresholds are Swiss-calibrated and nobody has calibrated Nepali ones.

   ⛔ **An earlier revision claimed the rate rule was "three to nine times too tight" and
   would "mark every legitimate monsoon rise as suspect". That was an arithmetic error —
   it compared a *per-day* stage rise against a *per-reading* threshold — and is withdrawn.**
   Tested against a real captured station-day, the largest change between consecutive readings
   was **0.066 m** against a 0.5 m limit: the rule did not fire. ⚠️ **But the margin is
   unquantified.** Dividing a daily rise by the number of readings gives a *uniform-spread
   average*, and a monsoon rise is not uniform — concentrated into a few hours, the same rise
   approaches the threshold. **The peak rate is unmeasured, and the cadence was measured at a
   different station in a different basin.**

   🔴 **There are TWO exposures, and an earlier revision deleted the measured one along with
   the wrong one.** Restoring it:

   - **The range check — measured, and the serious one.** Plan 268 ran the Swiss rule over the
     delivered record: **1,125 of 1,126 flags fall on station 450**, whose genuine monsoon
     peaks exceed the Swiss ceiling by nearly threefold. It would mark the single most
     important part of the record — the floods — as out of range. In 268's own words, *"not
     QC; a calibration error wearing QC's clothes."* ⚠️ This is the rule that marks a reading
     **failed**, not merely suspect. **Plan 268 D14 owns the calibration.**
   - **The rate rule — direction known, magnitude not.** It never divides by elapsed time, so
     it is insensitive at a dense cadence and over-sensitive across a gap. **Nobody owns that
     defect.**

---

## What would change this picture

- **DHM confirming gauge zero and units** lifts "provisional" from every derived value.
- **DHM explaining 684**, or supplying a current rating for it, restores **the fifth live
  gauge** to full standing — not a sixth, while 447 has no feed.
- **A feed for 447** — or confirmation that none exists — settles whether v1 is five gauges
  or six.
- **Answering Plan 268 D14** settles the Nepali thresholds, and **naming an owner for the
  rate rule's missing time normalisation** removes the underlying defect.
- **Widening `Alert` to carry provenance** unblocks alerting.

---

## Provenance of the figures in this document

*Added 2026-09-20 after an independent review asked which claims are checkable.*

**Independently reproduced from this repository**: the 0.066 m largest consecutive-reading
change on the captured station-day; that `Alert` carries no provenance field; that alerting is
disabled by configuration; the Forecast Lab's two exclusion filters; that `assemble_station_
training_data` returns nothing on an empty observation set; the 2.0–6.2 year staleness range;
the four-gauge July 2027 grouping and arithmetic.

**Reproduced from Plan 268's own measurements, not re-derived** (the delivered DHM files sit
outside this repository by deliberate constraint): 1,125 of 1,126 range-check flags at station
450; 94,617 of 99,246 daily values with a contemporaneous curve; the 112 tables.

**⚠️ NOT independently verified — treat as reported, not established**: the 193-station portal
enumeration and the 162/31 datum split (a point-in-time read on 2026-09-20); 684's three
anomalies; the catchment-area comparison (from a handover package outside this repository);
the 362-point extrapolation comparison; the 6–11 / 11–22 cm inversion sensitivities; the 65
distinct tables and 7–40-year establishment ages; the largest one-day stage rises.

🔴 **Exact refusal dates in July 2027 are unverified** — Plan 268 converts end dates to a
next-day exclusive `valid_to`, and the comparison's boundary semantics could shift them by a
day.

⛔ **Repeated point sets establish that tables were REUSED. They do not establish when a rating
was last independently checked.** The 7–40-year figures should be read as "days since this
point set was first put in force", nothing stronger.

## How to use this document

If you are about to state a v1 capability — in a report, a meeting, or to DHM — check it
against the table above. **If the claim is not supported here, it is not yet true.** Update
this file when a decision changes what October delivers, in the same commit as the decision.
