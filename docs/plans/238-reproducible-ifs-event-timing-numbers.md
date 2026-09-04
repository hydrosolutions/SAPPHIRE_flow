---
status: READY
created: 2026-09-04
plan: 238
title: M-A11d — make every number in § M-A11c reproducible from tracked code
scope: Freeze one convention, report only null-relative increments, attach uncertainty, and assert the document's figures against the tracked module's output. NOT a new measurement, NOT a new statistic, NOT a re-litigation of the bimodality or bias-correction lines.
depends_on: [216, 220]
source: docs/design/dhm-precipitation-m-a11-tigge-ifs-screening.md § M-A11c; Codex method review 2026-09-03; PR #249
---

# Plan 238 — M-A11d reproducible event-timing numbers

## Status

**READY.** Owner confirmed 2026-09-04, after an independent Codex review returned NEEDS-CHANGES
and its seven findings were folded (D1 weakened, D4 replaced, D7 added, one false claim removed).

## ⛔ PROPORTIONALITY IS BINDING

**One module already exists and is tested** (`scripts/dhm_precip/ifs_event_timing.py`, PR #249,
10 tests, pyright-strict clean). This plan **removes** numbers and **binds** the rest to code.
⛔ No new framework, no new statistic, no new data. **Adding length is a cost.**

## ⛔ PER-RUN SCOPE (binding)

- ⛔ **No network, no retrieval.** Every input is on disk.
- ⛔ Never write under `data/dhm_precip/era5_land*`, `imerg_early/points/`, or any `points/` tree.
- ⛔ **Do not re-open** the diurnal-bimodality line (stopped by the owner 2026-09-03) or the
  bias-correction design (WRONG-SHAPE, not built).
- **Hold at PR.** Patch bump folded into the code commit; docs in a separate unbumped commit; stage
  by explicit path, never `git add -A`.
- ⚠️ **Verify every code reference against source.** This track has shipped wrong citations and a
  constant that real data rejected.

## Why this plan exists

§ M-A11c currently carries **two sets of numbers** — a reviewer's recomputation and the tracked
module's — and **they disagree on two of four rows**. Neither can be declared right: the reviewer's
recomputation is **not reproducible from the record**, and the module's absolute rates are
**convention-dependent**. Nothing asserts the document against any code, so the figures can drift
from the module the moment either changes.

The document also still shows an **absolute missed rate**, which measurement showed moves from 0.248
to 0.216 on a **0.05 change in an arbitrary threshold**.

## Decisions

⚠️ **Revised 2026-09-04 after an independent Codex review returned NEEDS-CHANGES.** Its seven findings
are folded below; three decisions were **weakened**, one was **replaced**, and one factual claim was
**removed as false**.

- **D1 — The null-relative increment is the HEADLINE quantity; it is never published alone.**
  ⚠️ *Weakened from "absolute rates are never reportable".* The threshold perturbation shows the missed
  rate is conditional on `miss_fraction`; it does **not** establish that absolute rates are generally
  unreportable — and the module uses **different denominators** (exact and within-±6 h are fractions of
  *matched* events; missed is a fraction of *all searched* events), so a blanket rule would suppress
  information a hydrologist needs. ⇒ **Publish observed, null, increment, the matched/searched counts,
  and the frozen convention, together.** 🔴 ⛔ The missed fraction must **never** be described as a
  window-independent detection probability.

- **D2 — The document's figures are MACHINE-CHECKED against the module's output.**
  ⚠️ *Wording corrected:* a parsed table is **machine-checked transcription**, not generation. ⇒ Either
  keep transcription and say so, or have the module render **one delimited block** the document
  includes. The assertion must key on stable headings, row labels and numeric cells, ignore emphasis
  and whitespace, and **fail closed with a distinct error** on a parse failure.
  🔴 **It must also bind the CONVENTION block, not only the displayed results** — changing a default can
  leave rounded numbers unchanged, so table equality alone cannot prove that editing a default fails.
  ⛔ No document-generation framework.

- **D3 — ONE frozen convention, complete and stated in the module.** Search window, event quantile,
  decluster interval, miss fraction, lead set, init hour, null shifts, amplitude gate — **and the three
  the first draft omitted: the season tuple, `min_wet_windows`, and `min_candidates`**
  (`scripts/dhm_precip/ifs_event_timing.py:93-109`, `:351-357`), which directly govern which stations
  and events are retained. ⛔ Changing one is a reviewed commit, not a CLI habit.

- **D4 — ⛔ NO bootstrap interval. State that no reliable uncertainty estimate exists.**
  🔴 *Replaced outright.* **Six seasons are too few to present a resampled percentile range as an
  inferential interval**, and a naïve cell-resample would not bootstrap the whole estimator: event
  thresholds are computed from wet windows **pooled across seasons before events are measured**, so a
  valid resample must recompute thresholds inside every draw — exactly the apparatus this plan exists to
  avoid. ⇒ Publish `n = 6 seasons`, state plainly that **no reliable inferential interval is available**,
  and — only if a compact variability anchor is wanted — report the **descriptive range of the six
  season-specific estimates**. ⚠️ The ±12/24/36/48 h spread is **parameter sensitivity, not uncertainty**.

- **D5 — Remove the non-reproducible figures; keep a dated historical note.** The reviewer's
  recomputation cannot be regenerated from anything in the repo, and keeping two disagreeing sets in a
  live scientific result preserves ambiguity rather than evidence. ⇒ Keep a dated note naming the
  disagreement and pointing at the prior revision/PR, ⛔ **without the obsolete figures themselves**.
  The note explains **why the conventions were frozen and why observed/null/increment are now reported
  together** — not "why absolutes are no longer reported" (D1 no longer says that).

- **D6 — Nothing here changes M-A11/M-A11b's published offsets.** Their two recorded limits (the
  `[−18,+6)` branch is not rotation-equivariant; the ±3 h figure is a sampling interval, not an
  uncertainty bound) stay as written. ⛔ This plan does not re-estimate the climatological phase.

- **D7 — 🔴 PIN THE CONSUMED INPUTS.** *(new — the largest reproducibility hole.)* The gauge workbook is
  SHA-256 pinned, but the **six TIGGE point files are merely found and read**; everything under `data/`
  is gitignored and the attribution sidecars carry **no content hashes**. ⇒ The same filename can address
  different bytes on another machine, so the published numbers are **not reproducible even with frozen
  defaults**. ⇒ The module must **emit the SHA-256 of every consumed input** into its report, and the
  binding of D2 must cover them.

## Tasks

### T1 — freeze the complete convention; report observed, null and increment together
**Outcome:** the module's report carries, for every statistic, observed + null + increment + matched
and searched counts + the full frozen convention + the SHA-256 of every consumed input (D1, D3, D7).
**In:** `scripts/dhm_precip/ifs_event_timing.py`, `tests/unit/scripts/test_ifs_event_timing.py`.
**Out:** any new statistic; any change to the estimator; any resampling.
**Verification:** `uv run pytest tests/unit/scripts/test_ifs_event_timing.py` — new nodes assert the
report schema (every field present), the serialized defaults, and that the emitted input digests match
`shasum -a 256` of the six TIGGE parquet files and the workbook.
⚠️ **Acceptance uses the ALREADY-MEASURED two-run values on checksum-pinned inputs** — exact-window
0.179 / null 0.095, within ±6 h 0.442 / null 0.309, missed 0.248 / null 0.400, increments +0.130 /
+0.133 / +0.134 / +0.118 at ±12/24/36/48 h. ⛔ Not "moves measurably", ⛔ not an interval used as its own
tolerance — both were unfalsifiable.
**Pre-change:** today the report omits null counts, the three defaults of D3, and all input digests; the
new nodes fail against `origin/main`.

### T2 — bind every published event-timing RESULT in § M-A11c to the module
**Outcome:** editing either the document's figures or a module default fails a test.
**In:** `docs/design/dhm-precipitation-m-a11-tigge-ifs-screening.md` § M-A11c; the binding test.
**Out:** M-A11/M-A11b's own sections beyond D6; any other document.
**Verification:** the binding test parses § M-A11c and compares against the module's output; ⛔ it must
cover **result figures in PROSE as well as the table** — normalisation effects, IQRs, event counts,
threshold sensitivity and reconciliation ranges are currently prose and would otherwise escape the
binding. Either bind them or delete them. **Prove both directions by reverting**: perturb a table cell,
then a module default, and confirm each fails.
**Pre-change:** no test references the document today; both edits pass silently.

```json
{
  "phases": [
    {"id": "phase-1", "tasks": ["T1"], "parallel": false},
    {"id": "phase-2", "tasks": ["T2"], "parallel": false, "depends_on": ["phase-1"]}
  ]
}
```

## Exit gates

- **Every published event-timing RESULT** in § M-A11c is produced by
  `scripts/dhm_precip/ifs_event_timing.py` under a complete frozen convention, over inputs pinned by
  SHA-256, reported as observed + null + increment with its matched/searched counts.
- ⚠️ *"Result", not "number"* — dates, lead definitions, thresholds and code references are also
  numbers and are **not** measured results.
- ⛔ **No uncertainty interval is published.** The report states `n = 6 seasons` and that none is
  available (D4).
- The binding test fails in **both** directions: perturb a figure in § M-A11c, and separately perturb a
  module default. ⛔ Prove each by reverting.
- T1's acceptance values reproduce on checksum-pinned inputs: exact-window 0.179 / null 0.095,
  within ±6 h 0.442 / null 0.309, missed 0.248 / null 0.400, increments +0.130 / +0.133 / +0.134 /
  +0.118 at ±12/24/36/48 h.

```bash
uv run pytest tests/unit/scripts/test_ifs_event_timing.py
uv run ruff check scripts/dhm_precip/ifs_event_timing.py && uv run ruff format --check scripts/dhm_precip/ifs_event_timing.py
DHM_PRECIP_XLSX=data/dhm_precip/combined_precipitation_37_stations.xlsx uv run python -m scripts.dhm_precip.ifs_event_timing
uv run pytest tests/unit
```

## Non-goals

Re-estimating the climatological phase (D6) · the diurnal-bimodality line (owner-stopped 2026-09-03) ·
the IMERG bias-correction design (WRONG-SHAPE) · any new data or retrieval · a doc-generation framework ·
**any bootstrap or resampling** (D4) · basin-average verification, which needs gateway data with no
point-level access.
