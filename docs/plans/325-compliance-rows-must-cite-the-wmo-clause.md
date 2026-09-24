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

1. 🔴 **Eleven compliance rows — 4 verified, 1 specified, 6 deferred. NONE cites a chapter,
   clause or page.** Every one points at a publication or a framework:

   | reference as written | rows |
   |---|---|
   | `WMO-168 Vol I` | 4 (one adds *"(spatial consistency)"*) |
   | `WMO-1364 (sharpness dimension)` | 1 |
   | `WMO-1072, QMF-H` | 1 |
   | `WMO-1254 Tier 2/3` | 1 |
   | `WMO-1150` / `WMO-1109` / `WMO-1192` / `WHOS` | 4 (bare publication) |

   Three carry a topical hint; **none is locatable.** ⚠️ `WHOS` is a system, not a numbered
   publication, so its row needs a different kind of reference than the rest. ⚠️ A further prose
   claim sits **outside** this eleven-row table and is not counted here. ⚠️ `WMO-168 Vol I` is a volume of the *Guide
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

### T1 — Say what the table currently is

**Outcome.** No reader mistakes a publication-level assertion for a located requirement — and the
document's evidence rule covers **both halves** of a compliance claim instead of one.

🔴 **The two halves are INDEPENDENT axes and T1 must keep them apart.** A row's *code* status (does
our code do X, and how do we know) and its *WMO reference* status (where does WMO ask for X) are
different questions with different evidence. ⛔ *Conflating them is how the first draft of this task
broke the existing rule — see Out.*

**In.**
- **Every row carries a WMO-reference status** — the chapter, or the marker *"publication-level;
  chapter not located"*. One consistent marker, not eleven phrasings.
  🔴 **Every row, regardless of its code status.** The table holds **4 verified, 1 specified and 6
  deferred** rows. ⛔ *A rule that bites only on `Verified` leaves 7 rows carrying no obligation at
  all, and the deferred rows make exactly the same WMO claim.*
- The standing rule extended with a **second, independent requirement**: a row states its WMO
  reference status as well as its code evidence. ⭐ *This is the durable deliverable; the chapters
  are the backlog it creates.*
- A line stating that the marker describes **our evidence**, not WMO — ⛔ *nothing here says the
  standards are unclear or that we fail to conform.*

**Out.**
- ⛔ **ANY change to the existing code-evidence rule.** It accepts *a named runnable command* **or**
  *a recorded live query with its result and date*. ⛔ *The first draft of this task rewrote it as
  "a named, runnable test", silently deleting the live-query alternative — a real rule, quietly
  narrowed while claiming only to extend it.*
- ⛔ Changing any row's *Verified* status, or its date. A row verified on code evidence **stays
  verified**; it simply also states that its WMO reference is not yet located.
- ⛔ Deleting or downgrading any row — that is D3, after T2 reports.
- ⛔ The § 2 inventory. ⛔ The separate prose claim outside the eleven-row table.

**Pre-change.** N/A — documentation.

**Verification.**
- All eleven rows carry a WMO-reference status; none is silently in between.
- 🔴 **The extended rule, read against the table as it stands, demands a marker on all ELEVEN** —
  not on the four `Verified` ones. ⛔ *Checked by reading the rule's own wording, because a rule the
  current table already satisfies has changed nothing, and manually marking eleven rows does not
  prove the rule requires eleven markers.*
- The code-evidence rule is **byte-identical** to before, both alternatives intact — asserted by
  diff, because this is the one way T1 can do damage.

### T2 — Locate and cite the chapters (D1, D2) — LOW PRIORITY

**Outcome.** Each row in D2's scope names the publication, a working URL and the chapter its claim
rests on.

**In.**
- Per row: **publication number, a working URL, and the chapter — with the chapter's TITLE**, so a
  wrong chapter is visible without opening the PDF (D1). ⛔ *Not clause, section or page: the owner
  ruled that out, and pursuing it would slow T2 for precision nobody asked for.*
- 🔴 **Whatever was NOT found, reported per row** — what was searched, and what came back.
  ⛔ *A row quietly left publication-level is indistinguishable from one nobody reached.*
  ⚠️ **"I could not retrieve the PDF" and "the chapter does not exist" are different findings** and
  must be reported as such — T3 branches on the difference.
- Each located row's **WMO-reference status** updated from the marker to the chapter.

**Out.**
- ⛔ **Touching a row's *Verified* status or its date.** Those record CODE evidence. ⛔ *The first
  draft restamped the Verified date when the WMO reference was read, which would have made a code
  claim look freshly checked because someone opened a PDF.*
- ⛔ Inventing a locator from a plausible chapter title. ⛔ Citing a secondary source as though it
  were the WMO requirement. ⛔ Changing our code to match a requirement found here — that is a
  finding and a new plan.

**Pre-change.** N/A — research.

**Verification.** ⭐ **A reader can open the URL, turn to the named chapter, and find the subject
there.** The chapter title is what makes a wrong locator visible on the page. ⚠️ Anything weaker is
a topical hint, and § (1) shows we already have three of those.

### T3 — Resolve the rows with no locatable chapter (D3) — LOW PRIORITY

**Outcome.** Every row is conformance, alignment, or gone — none is an unqualified claim nobody can
check.

**In.** D3's choice applied to T2's not-found list, one row at a time, each with its reason.

**Out.** ⛔ Running before T2 reports. ⛔ Treating *"I could not retrieve the PDF"* as *"no
requirement exists"* — T2 reports them separately and only the second reaches D3; the first is a
retrieval failure to retry. ⛔ Changing any row's code-evidence status or date.

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

## Changelog

**2026-09-24 — created**, from the owner's observation that fixing a compliance row's CODE citations
left its WMO half as imprecise as ever. **Reviewed the same day: NEEDS CHANGES, 3 medium — all
three in T1/T2's operative text, all three now applied there rather than appended.**

| finding | what was wrong | where it now lives |
|---|---|---|
| the extended rule narrowed an existing one | T1 rewrote the code-evidence rule as *"a named, runnable test"*, **silently deleting its *"or a recorded live query with its result and date"* alternative** | T1's Out forbids touching that rule at all; a diff assertion guards it |
| the rule would not mark eleven rows | it bit only on rows moving to *Verified* — **4 of 11** — leaving the 1 specified and 6 deferred rows, which make the same WMO claim, with no obligation | T1 requires a WMO-reference status on **every** row regardless of code status, on an independent axis |
| WMO research changed code evidence | T2 restamped a row's *Verified* date when its WMO reference was read, making a **code** claim look freshly checked because someone opened a PDF | T2's Out forbids touching *Verified* or its date; the two axes are separate throughout |

⚠️ Also corrected: the row breakdown (**4 verified / 1 specified / 6 deferred**), that `WHOS` is a
system rather than a numbered publication and needs a different kind of reference, and that a
further prose claim sits outside the eleven-row table.

⭐ **The shape of all three: I extended a rule and did not check what the existing rule already
said.** Same class as the plan's own subject — a claim asserted without opening the document it
rests on.
