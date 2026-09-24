---
status: DRAFT
created: 2026-09-24
revised: 2026-09-24
plan: 325
title: A compliance row proves our code does something — not that WMO asks for it
priority: LOW for T2/T3 (owner, 2026-09-24); T1 approved to ship immediately
scope: Make each row of the WMO compliance table cite the specific clause, section or page of the WMO publication it claims conformance with, and extend the standing evidence rule to cover that half of the claim. NOT changing any QC, alerting or verification BEHAVIOUR. NOT the code-side citations in those rows (Plan 324 T1). NOT the document inventory in § 2. NOT adding WMO conformance we do not have.
depends_on: []
blocks: []
related: [023, 253, 272, 324]
open_decisions: [D3]  # D1/D2 closed by the owner 2026-09-24; D3 goes live only if T2 reports a not-found
source: 2026-09-24 — the owner, on being shown that a compliance row's CODE citations had gone stale: *"data quality checks should point at the correct locations in the wmo documents. that may require a separate plan."* Every count below was measured against `docs/standards/wmo.md` on `origin/main` that day.
---

# Plan 325 — a compliance row proves our code does something, not that WMO asks for it

⚠️ **Plan number PROVISIONAL until the owner grants it.**

## Status

**DRAFT.** D1 and D2 are closed (below). ⭐ **T1 is approved by the owner to ship**; it still needs
an independent review and a READY flip before implementation, like anything else. **T2 and T3 are
LOW PRIORITY backlog** and must not displace the v1 critical path.

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

### D1 — how precise a locator, and who finds it. **⚖️ CLOSED — owner, 2026-09-24.**

Owner: *"no need to cite exact rows in the wmo documents, but the document/report number/url and
chapters would be good to have correctly eventually."*

⇒ **The target is publication number + URL + CHAPTER.** ⛔ *Not clause, section or page — the draft
asked for more precision than the owner wants, and chasing it would make T2 far slower for no
benefit the owner asked for.*

⇒ **Who: I propose, the owner confirms.** The chapter for each row, with the chapter TITLE (so a
wrong chapter is visible without opening the PDF) rather than a quoted requirement sentence, which
clause-level precision would have needed.

⚠️ **Retrieval may simply fail for some publications** — several are large PDFs behind library
links. T2 reports that as a result per row, ⛔ never as silence.

### D2 — scope and priority. **⚖️ CLOSED — owner, 2026-09-24: LOW PRIORITY.**

Owner: *"put that into a plan with low priority for now."*

⇒ **T2 and T3 are backlog.** They are not sequenced against the v1 critical path and should not
displace Nepal onboarding, the pilot, or QC work. ⛔ *Recorded so a future agent does not read an
unstarted DRAFT as neglected and "helpfully" pick it up ahead of the owner's actual priorities.*

⇒ **T1 is NOT low priority — the owner approved it to ship now** (*"ok to the cheap honest step"*).
⭐ That is the whole reason T1 was separated from T2: the honest label costs nothing and does not
wait on research.

### D3 — what happens when no chapter can be found. **OPEN, but much less likely to bite.**

The options are unchanged — reword the row as *our own practice, consistent with WMO*, delete it, or
keep it marked *claimed, chapter not located*. ⛔ **Not an option: leaving it unmarked.**

⚠️ **D1's relaxation changes the odds, not the question.** A chapter is far easier to locate than a
clause, so most rows should resolve — but § (5) still stands: a row may have no WMO basis at all,
and *"QC flag vocabulary"* is the one to watch. ⇒ **D3 becomes live only if T2 reports a not-found**,
and the owner answers it then, against a real list rather than a hypothetical.

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
- Per row: **publication number, a working URL, and the chapter** — with the chapter's TITLE, so a
  wrong chapter is visible without opening the PDF (D1). ⛔ *Not clause or page: the owner ruled that
  out, and pursuing it would slow T2 for precision nobody asked for.*
- 🔴 **Whatever was NOT found, reported as a finding** — per row, what was searched and what came
  back. ⛔ *A row quietly left publication-level is indistinguishable from one nobody reached.*
- Each located row's *Verified* date restamped to when the clause was read.

**Out.** ⛔ Inventing a locator from a plausible chapter title. ⛔ Citing a secondary source
(a textbook, another agency's guidance) as though it were the WMO requirement. ⛔ Changing our code
to match a requirement discovered here — if a gap appears, it is a finding and a new plan.

**Pre-change.** N/A — research.

**Verification.** ⭐ **A reader can open the URL, turn to the named chapter, and find the subject
there.** The chapter title is what makes a wrong locator visible on the page rather than only to
someone who reads the whole volume. ⚠️ Anything weaker is a topical hint, and § (1) shows we already
have three of those.

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
