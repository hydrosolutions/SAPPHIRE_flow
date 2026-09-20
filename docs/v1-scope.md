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
path**, which makes the 272 → 269 dependency loop a blocker against a success criterion rather
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

**Three properties of that inversion make it better than it sounds:**

1. ⭐ **It is largely UNDOING a transformation, not modelling one.** DHM computed that
   discharge *from* level using the curve in force at the time. Inverting through the
   **contemporaneous** curve recovers approximately the original level. Plan 268 measured that
   **94,617 of 99,246 daily values have a contemporaneous curve**, so this is recovery for
   ~95% of the record, not estimation.
2. ⭐ **The rating curve is steep, so inversion COMPRESSES error.** Measured at each station's
   median flow: a **10% discharge error becomes 6–11 cm of level; 20% becomes 11–22 cm.** The
   forward direction amplifies; the inverse direction damps.
3. ⭐ **It removes the datum question from the forecast itself.** A model trained on level and
   serving on level is invariant to a constant datum offset — and the portal publishes each
   station's warning and danger levels **in the same reference as its readings** (verified
   across all 193 stations). So a level forecast can be compared against that station's own
   danger level self-consistently *even if the absolute datum is never established*.

**Two caveats that must travel with it:**

- ⚠️ **A daily-mean discharge does not invert to a daily-mean level.** The curve is convex, so
  the level that produces the mean discharge is **higher** than the mean level. Inverted
  levels are biased upward by an unquantified amount — the exact mirror of the convert-then-
  mean decision on the forward path.
- 🔴 **Train/serve datum consistency still matters, and 684 Tamor still fails it.** The
  inverted history sits on the *rating table's* datum; the live feed sits on whatever datum it
  uses. For the four gauges whose live readings convert to seasonally plausible discharge
  those agree. For 684 they demonstrably do not — so it remains the problem gauge under this
  approach too. *(An earlier note in this session suggested level forecasting would make 684
  fine. That was wrong: it removes the conversion from the serving path, not the datum
  mismatch between training and serving.)*

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

**⇒ Five of six gauges forecast. One has no input at all.**

**447 Trisuli has no operational level feed.** Measured 2026-09-20 against the public portal:
193 stations enumerated across both pages, no Trisuli/Betrawati station present. Nothing in
the pipeline is broken — there is simply no data. It stays configured so it starts working
the day a feed appears.

**684 Tamor is anomalous on three independent measurements**, from three unrelated datasets:
its live level converts through its own rating table to a discharge above its entire 24-year
record maximum while the portal reports it below warning level; its published danger level
lies above the top of its own rating table; and its delineated catchment is ~9% larger than
DHM's published area, where the other five agree within 3.7% — **measured from the
`area_diff_pct` column of `gauge_coordinates.csv` in the 2026-09-20 basin/static handover
package, which lives outside this repository.** ⚠️ **None of the three is verifiable from
this repository**: the first two rest on a point-in-time read of the public portal on
2026-09-20, the third on that package. It is onboarded, converted and forecast — on DHM's own historical discharge, not on our conversion — and every value it
produces is marked. DHM has been asked to confirm its location, area and current rating.

---

## What v1 does NOT deliver

These are consequences of decisions taken deliberately. None is an accident; none was
written down as a scope reduction until now.

### No flood alerting on these gauges — ✅ correct by design, not a gap

⚖️ **The owner's sequence is: demonstrate, evaluate, fine-tune, improve QC, and connect
alerting last.** So this is the intended v1 state and should not be read as a shortfall.

The mechanism that keeps it shut is worth knowing for when it opens: a marked value would
otherwise reach an alert **without its mark**, because `types/alert.py::Alert` carries no
provenance field (verified). **Whoever opens the alert path must widen `Alert` to carry the
derivation markers first**, or every caveat this project has built is discarded at the last
step. Named follow-on work, no plan number yet.

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

### No partner-facing visibility

The Forecast Lab export filters observations to `source = MEASURED`. Every v1 discharge
value is `RATING_CURVE_DERIVED`, so **none of the six appears in the partner snapshot**, and
the export schema carries no input-quality fields to describe them if they did.

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

**What would actually resolve it**, stated separately because they are separate: historical
hindcast skill against DHM's own record is meaningful *within its stated domain* today;
operational transfer validity needs the level-based successor; and discharge-truth uncertainty
needs rating curves nobody expects.

### No current rating tables

DHM has stated it cannot supply current rating curves; this is blocked at source, not by us.
Every v1 discharge value therefore rests on a rating table past its stated validity, carried
under a deliberate 2,557-day (7-year) tolerance. **For the five gauges that actually produce
values the range is 2.0 to 6.2 years.** *(An earlier revision said "0.7 to 6.2"; the 0.7
figure belongs to 447, which has no feed and produces nothing — it flattered the number.)*

🔴 **That tolerance is the production configuration, not a development affordance** — and
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

## The material risks, stated once

1. **The level feed's datum is unconfirmed.** Measured: the public feed mixes two conventions
   — 162 of 193 stations report a gauge reading, 31 report height above sea level — and
   nothing in the response declares which. Four of our five live gauges convert to seasonally
   plausible discharge, which is good corroboration but not proof. Until DHM confirms, every
   derived value is stamped provisional at the row level.
2. **Extrapolation past the rating tables is bounded but real.** Validated against the
   delivered data at 362 points, agreeing with DHM's own table construction to within a few
   percent out to one metre above the table. ⚠️ **That is agreement with DHM's own table
   construction — numerical consistency, not physical accuracy.** Published figures for
   extrapolated high flows at real sites range from **41% to 200%**; the often-quoted 25% is a
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
- **DHM explaining 684**, or supplying a current rating for it, restores a sixth gauge to
  full standing.
- **A feed for 447** — or confirmation that none exists — settles whether v1 is five gauges
  or six.
- **Answering Plan 268 D14** settles the Nepali thresholds, and **naming an owner for the
  rate rule's missing time normalisation** removes the underlying defect.
- **Widening `Alert` to carry provenance** unblocks alerting.

---

## How to use this document

If you are about to state a v1 capability — in a report, a meeting, or to DHM — check it
against the table above. **If the claim is not supported here, it is not yet true.** Update
this file when a decision changes what October delivers, in the same commit as the decision.
