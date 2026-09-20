# v1 scope — what October 2026 actually delivers

**Status: current as of 2026-09-20.** Written at the owner's instruction after a cross-plan
decision review returned *incoherent* — not because individual decisions were wrong, but
because nothing stated what they jointly produce. `docs/v0-scope.md` is the equivalent
document for v0; there was no v1 equivalent until now, and its absence is how the gap below
went unnoticed.

⚠️ **Read this before quoting a v1 capability to anyone outside the project.**

---

## The one-line version

> **v1 is daily discharge forecasts for real Nepali DHM river gauges, running on the staging
> host, in October 2026.**

That sentence is what the project has been repeating. It is optimistic in four specific ways,
each traceable to a decision that was made deliberately and for good reasons.

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

### No flood alerting on these gauges

The alert path is held closed for DHM stations on purpose, because a marked value would
otherwise reach an alert without its mark — `types/alert.py::Alert` carries no provenance
field. **Whoever opens the alert path must widen `Alert` to carry the derivation markers
first.** That is named follow-on work with no plan number yet.

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

**This resolves itself** once enough operational discharge has accumulated to retrain on —
a documented temporary state with a defined end, not a permanent caveat. The retrain trigger
still needs an owner.

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
