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
DHM's published area, where the other five agree within 3.7%. It is onboarded, converted and
forecast — on DHM's own historical discharge, not on our conversion — and every value it
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
level, so its entire danger band produces no discharge figure at all. Accepted knowingly;
recorded here so an operator does not file it as an outage.

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
Every v1 discharge value therefore rests on a rating table between roughly 0.7 and 6.2 years
past its stated validity, carried under a deliberate 2,557-day (7-year) tolerance.

🔴 **That tolerance is the production configuration, not a development affordance** — and two
of the six gauges cross it in **July 2027**, roughly nine months after the v1 date. Nothing
currently plans for that date.

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
   percent out to one metre above the table. The literature puts the *true* uncertainty at an
   extrapolated high flow between 25% and a factor of two.
3. **Observation QC on these gauges is not yet trustworthy.** Plans 272 and 264 are
   prerequisites for *operational activation*, not for building. Until they land, a DHM level
   reading can be recorded as having passed quality control with zero rules having run.
4. **Nobody owns Nepali QC threshold values.** The deployed water-level thresholds are
   Swiss-calibrated. Applied to a monsoon river they will mark legitimate flood rises as
   suspect, and suspect readings are filtered out of every model read — removing the series
   during exactly the events the system exists for. This gap is unowned across all current
   plans.

---

## What would change this picture

- **DHM confirming gauge zero and units** lifts "provisional" from every derived value.
- **DHM explaining 684**, or supplying a current rating for it, restores a sixth gauge to
  full standing.
- **A feed for 447** — or confirmation that none exists — settles whether v1 is five gauges
  or six.
- **Nepali QC threshold values**, owned by someone, remove risk 4.
- **Widening `Alert` to carry provenance** unblocks alerting.

---

## How to use this document

If you are about to state a v1 capability — in a report, a meeting, or to DHM — check it
against the table above. **If the claim is not supported here, it is not yet true.** Update
this file when a decision changes what October delivers, in the same commit as the decision.
