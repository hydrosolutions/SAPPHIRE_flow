---
status: DRAFT
created: 2026-09-24
plan: 325
title: A compliance row proves our code does something — not that WMO asks for it
scope: Make each row of the WMO compliance table cite the specific clause, section or page of the WMO publication it claims conformance with, and extend the standing evidence rule to cover that half of the claim. NOT changing any QC, alerting or verification BEHAVIOUR. NOT the code-side citations in those rows (Plan 324 T1). NOT the document inventory in § 2. NOT adding WMO conformance we do not have.
depends_on: []
blocks: []
related: [023, 253, 272, 324]
open_decisions: [D1, D2, D3]
source: 2026-09-24 — the owner, on being shown that a compliance row's CODE citations had gone stale: *"data quality checks should point at the correct locations in the wmo documents. that may require a separate plan."* Every count below was measured against `docs/standards/wmo.md` on `origin/main` that day.
---

# Plan 325 — a compliance row proves our code does something, not that WMO asks for it

⚠️ **Plan number PROVISIONAL until the owner grants it.**

## Status

**DRAFT.** ⛔ No implementation until an independent review is complete and the orchestrator sets
READY. D1 is the owner's — it needs a hydrologist and the publications themselves.

## Why this plan exists

Plan 324 fixes a compliance row whose *code* citations had rotted. The owner, seeing that, named
the larger gap: **the row's other half was never precise to begin with.**

A compliance row makes a two-part claim — *WMO requires X* and *our code does X*. The table has a
standing rule, added after the Plan 023 drift, governing the second half: a row moves to *Verified*
only on evidence from the running system or a named, runnable test. ⭐ **There is no rule at all
governing the first half.** A row can therefore satisfy the standing rule completely — a green,
named test proving our code does X — while nobody has ever opened the publication to check that WMO
asks for X, or where.

That is the state of the table today.

## What is measured

`docs/standards/wmo.md` on `origin/main`, 2026-09-24.

1. 🔴 **Eleven compliance rows. NONE cites a clause, section or page.** Every one points at a
   publication:

   | reference as written | rows |
   |---|---|
   | `WMO-168 Vol I` | 4 (one adds *"(spatial consistency)"*) |
   | `WMO-1364 (sharpness dimension)` | 1 |
   | `WMO-1072, QMF-H` | 1 |
   | `WMO-1254 Tier 2/3` | 1 |
   | `WMO-1150` / `WMO-1109` / `WMO-1192` / `WHOS` | 4 (bare publication) |

   Three carry a topical hint; **none is locatable.** ⚠️ `WMO-168 Vol I` is a volume of the *Guide
   to Hydrological Practices* — a few hundred pages. Four of our eleven claims rest on it.
2. **The standing rule covers only our side.** Plan 253 T4c, at the head of the same section:
   a row is *Verified* only on *"evidence from the running system or a named, runnable test —
   **never** on a plan's `status`"*. ⇒ It disciplines the code half and is **silent on whether the
   requirement was ever located**. This plan's whole subject is the missing half of that rule.
3. ✅ **The precision is achievable, and this document already demonstrates it.** § 2 names
   *"**Ch. 6 Measurement of precipitation**"* for WMO-No. 8, and § 3 carries a correction that
   *"Vol I Ch. 6" belongs to WMO-No. 8, not to WMO-168 — whose Ch. 6 is groundwater*. ⇒ Someone has
   already read these documents closely enough to distinguish two publications' sixth chapters.
   ⛔ *So this is not a "standards are vague" problem. It is work nobody has done for the
   compliance table.*
4. **The precedent is expensive.** Plan 023 was archived at `status: READY` by a pure file move,
   and the compliance rows resting on it stayed green for **five months** while the work they
   claimed was never picked up. That drift was on the code side and produced the standing rule.
   **The WMO side has no equivalent guard.**
5. ⚠️ **Some rows may have no WMO basis at all.** *"QC flag vocabulary"* asserts our statuses map
   onto WMO-168's *good / suspect / erroneous / missing*. Whether WMO-168 Vol I states that
   vocabulary, and where, is exactly what nobody has checked. ⛔ *The honest outcome for a row may be
   that it is our own practice, consistent with WMO in spirit, and should say so — see D3.*

## Owner decisions

### D1 — who locates the clauses. **OPEN — needs the publications.**

| | option | cost |
|---|---|---|
| (a) | The owner cites each clause directly, as hydrologist. | Authoritative. Eleven rows of their time, against documents they know. |
| (b) ⭐ | **I fetch the publications and propose a clause per row with the quoted sentence; the owner confirms or corrects.** | The owner reviews rather than researches. ⚠️ Several are large PDFs behind library links; retrieval may simply fail for some, which T1 must report as a result rather than paper over. |
| (c) | Mark every row publication-level and stop. | ⛔ Insufficient alone — but see T1: doing this FIRST is right either way. |

**Recommendation: (c) immediately, then (b).** ⭐ *The honest label costs nothing and can ship today;
the research is the slow part and should not hold it.*

### D2 — all eleven rows, or a priority subset. **OPEN.**

§ 2 already ranks the publications — *Critical* (1072, 1364, 1091, 1254), *High* (168, 1150, 1109,
QMF-H), and below. Observation QC sits under WMO-168 (*High*) and is the subject the owner raised.

⚠️ **The ranking is about the publications, not about our exposure.** A bare `WHOS` row claims an
interoperability conformance nobody will test until data is shared internationally; a
`WMO-168 Vol I` QC row underwrites a check that runs against every reading, every cycle, today.
⇒ Ordering by *what we would have to defend first* is not the same as ordering by § 2.

### D3 — what happens when no clause can be found. **OPEN — and the most important of the three.**

If a row's requirement cannot be located in the publication, the options are materially different:

| | option |
|---|---|
| (a) | Reword the row as *our own practice, consistent with WMO* — an honest downgrade from conformance to alignment. |
| (b) | Delete the row. |
| (c) | Keep it, marked explicitly as *claimed, requirement not located*. |

⛔ **Not an option: leaving it as-is.** ⚠️ *A reader — including an auditor, and including a future
agent — reads the present table as established conformance. That is the defect, independent of
whether any individual row turns out to be justified.*

## Tasks

### T1 — Say what the table currently is (D1c)

**Outcome.** No reader mistakes a publication-level assertion for a located requirement.

**In.**
- Each of the eleven rows' WMO reference marked as **publication-level, requirement not yet
  located** — a single consistent marker, not eleven phrasings.
- The standing rule at the head of the section extended to cover **both halves**: a row is
  *Verified* only when the requirement is cited to a clause AND the code evidence is a named,
  runnable test. ⭐ *This is the durable deliverable; the citations are the backlog it creates.*
- A line stating that the marker is a statement about **our evidence**, not about WMO — ⛔ *nothing
  here says the standards are unclear or that we fail to conform.*

**Out.** ⛔ Any change to what a row claims about our code. ⛔ Deleting or downgrading any row —
that is D3, after T2 establishes whether the clause exists. ⛔ The § 2 inventory.

**Pre-change.** N/A — documentation.

**Verification.** Every row carries the marker or a clause, with none silently in between. ⭐ The
extended standing rule, applied to the table as it stands, would mark all eleven — asserted by
reading it, because a rule that the current table already passes has changed nothing.

### T2 — Locate and cite the requirements (D1, D2)

**Outcome.** Each row in D2's scope cites the clause, section or page it rests on, with the
sentence that carries the requirement.

**In.**
- Per row: publication, edition, and the narrowest locator the document supports (clause number,
  section, or page), plus the quoted requirement sentence so a reader need not re-open the PDF.
- 🔴 **Whatever was NOT found, reported as a finding** — per row, what was searched and what came
  back. ⛔ *A row quietly left publication-level is indistinguishable from one nobody reached.*
- Each located row's *Verified* date restamped to when the clause was read.

**Out.** ⛔ Inventing a locator from a plausible chapter title. ⛔ Citing a secondary source
(a textbook, another agency's guidance) as though it were the WMO requirement. ⛔ Changing our code
to match a requirement discovered here — if a gap appears, it is a finding and a new plan.

**Pre-change.** N/A — research.

**Verification.** ⭐ **A reader with the publication open can confirm each citation without
searching** — the locator plus the quoted sentence is the test. Anything weaker is a hint, and § (1)
shows we already have four of those.

### T3 — Resolve the rows with no locatable requirement (D3)

**Outcome.** Every row is conformance, alignment, or gone — none is an unqualified claim nobody can
check.

**In.** D3's choice applied to T2's not-found list, one row at a time, each with its reason.

**Out.** ⛔ Running before T2 reports. ⛔ Treating "I could not retrieve the PDF" as "no requirement
exists" — those are different findings and T2 must distinguish them.

**Pre-change.** N/A.

**Verification.** No row remains in the state § (1) describes.

## Explicitly out of scope

- **The code-side citations** — Plan 324 T1, which corrects three stale line anchors in one row.
  ⚠️ *That plan fixes where a row points INTO OUR CODE; this one fixes where it points INTO WMO.
  Same row, different halves, and neither does the other's job.*
- **Any change to QC, alerting or verification behaviour.** Nothing here alters what the system does.
- **Claiming conformance we do not have.** If T2 finds a requirement we do not meet, that is a
  finding for a new plan, not an edit here.
- **The § 2 document inventory** — already carries chapter-level precision where it matters (§ 3).

```json
{
  "phases": [
    {"phase": 1, "tasks": ["T1"], "parallel": false,
     "note": "ships immediately — the honest label does not wait on the research"},
    {"phase": 2, "tasks": ["T2"], "parallel": false, "decision": "D1 and D2 CLOSED"},
    {"phase": 3, "tasks": ["T3"], "parallel": false, "decision": "D3 CLOSED, and T2 has reported"}
  ]
}
```
