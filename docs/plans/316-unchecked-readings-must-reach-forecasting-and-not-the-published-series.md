---
status: COMPLETE   # merged #301, 2026-09-24. ⛔ A merged plan left reading READY is a live order to an agent — the hazard this repo already booked.
created: 2026-09-24
plan: 316
title: Unchecked readings must reach forecasting as degraded, and must not reach the published series
scope: The CONSUMER half of Plan 272's D5 policy, which that plan decided and its implementation did not build — the store/Protocol/fake signature change that makes a two-status read expressible, the four accepting reads, the `DEGRADED` provenance those reads must carry, and the published-endpoint exclusion. NOT re-examination of unchecked rows (Plan 317), NOT the zero-rule telemetry (Plan 318), NOT the selection fix or the status itself (272, shipped), NOT threshold values, NOT per-station overrides (269), NOT the onboarding path (315).
depends_on: []
blocks: []
related: [272, 315, 317, 318]
open_decisions: []
reviews:
  - "codex 2026-09-24 r1 — PROBLEMS FOUND; the review classification over-applied the deployment ruling, and the excluding-reader checklist omitted calculated-station derivation"
  - "claude 2026-09-24 r2 — PROBLEMS FOUND; assess_input_quality was the unnamed signature the plumbing claim rested on, the InputQualityFlag literal omitted required detail, and the 315 ordering claim was unsupported"
source: 2026-09-23 — a completeness audit of PR #297 found 31 of 54 of Plan 272's items unshipped. This plan carries the subset that changes what the running system does. ⚠️ Every policy here was DECIDED in 272 D5; none is reopened.
---

# Plan 316 — unchecked readings must reach forecasting, and not the published series

⚠️ **Plan number PROVISIONAL until the owner grants it** (302–305 were granted in prose and never
written, so the sequence is not a reliable allocator).

## Status

**READY** — set by the orchestrator 2026-09-24 on the owner's instruction, after the independent reviews recorded in the frontmatter.

⚠️ **HIGH-RISK — on its own merits, not on deployment grounds.** ⛔ *An earlier draft called this
"ordinary work, reviewed the ordinary way", which over-applied the owner's ruling.* The ruling
retires one argument — "it runs against the live deployment" — and **retains every other trigger**
(`docs/workflow.md` § High-risk work). **T3 changes an externally visible API response**, and
external-facing contracts are a retained trigger. ⇒ one relevant independent review in addition to
the ordinary pair, for T3's surface specifically. ⚠️ T1 also touches a shared store signature,
which is its own reason for care.

## Why this plan exists

Plan 272 decided a consumer policy and shipped only the writer. Measured on `main` at `dbb9105e`:
`QC_UNCHECKED` occurs in exactly three files — `types/enums.py`, `db/metadata.py` and
`flows/ingest_observations.py`. **No consumer mentions it.**

⇒ Today a group that resolves zero rules is written `QC_UNCHECKED` and is **silently excluded from
forecast inputs**, because every consumer asks for `QC_PASSED` and the store filters by equality
(`store/observation_store.py:184`). 272's D5 says the opposite: *forecasts **yes**, with
`input_quality = DEGRADED`*. And because item 12 withdrew the write gate, **D5's split is the only
safeguard 272 retained** — the one that did not ship.

⚠️ **One exception, and it points the other way.** `api/routes/stations.py:698` excludes only
`qc_failed`, so unchecked values **do** reach the published observed series, unmarked. 272 D5
assigns that site **"No — exclude"**. Both halves are in this plan because they are the same
policy.

## What is measured

Measured on `main` at `dbb9105e`.

1. **A two-status read is not expressible today.** `fetch_observations` takes a scalar
   `qc_status: QcStatus | None` (`store/observation_store.py:160-167`); so do the Protocol
   (`protocols/stores.py:128`) and the fakes (`fake_stores.py:198`). ⇒ **T1 is a prerequisite, not
   a refactor.**
2. **The four accepting reads**, per D5: `services/operational_inputs.py:890` (model input) and
   `:940` (freshness probe); `services/track_assembly.py:285` (model input) and `:338` (freshness
   probe). ⚠️ **The two freshness probes accept the status but must NOT themselves degrade the
   forecast** — D5 says so in terms.
3. **The provenance has nowhere to go — and the gap is a SIGNATURE, not just a dataclass.**
   - `OperationalInputMetadata` (`services/operational_inputs.py:59-63`) carries
     `warm_up_source`, `warm_up_state_age_hours`, `observation_staleness_hours`, `nwp_age_hours`
     — **no quality field.**
   - 🔴 **`assess_input_quality` (`services/input_quality.py:29-40`) derives `(level, flags)` from
     scalar keywords and accepts no observation-quality input at all.** ⛔ *An earlier draft said
     "the plumbing is the work" and then named only the four reads and the dataclass — this is
     the signature that claim actually rests on, and leaving it unnamed left the hardest part
     implicit.*
   - The vocabulary exists: `InputQualityLevel` (`FULL`/`PARTIAL`/`DEGRADED`) and
     `InputQualityCategory` (`OBSERVATION`/…) at `types/enums.py:401-415`, and
     **`InputQualityFlag` requires `category`, `level` AND `detail: str`**
     (`types/domain.py:111-116`, frozen, keyword-only).
4. **The published endpoint's polarity.** `api/routes/stations.py:698` filters
   `qc_status != "qc_failed"` — an exclusion list, not an inclusion list, which is why the grep
   that built D5's inventory missed it (272 records this).

## Owner decisions

**None.** Every policy here was closed by Plan 272 D5 (2026-09-20) and its three-further-readers
table (`272:664-668`). This plan builds them. ⛔ *If an implementer finds a case D5 did not
cover, that is an escalation, not a judgement call.*

## Tasks

### T1 — Make a two-status read expressible

**Outcome.** A caller can ask for `{QC_PASSED, QC_UNCHECKED}` in one read, and every existing
single-status caller is unchanged.

**In.** `fetch_observations` on the store, the `ObservationStore` Protocol and the fakes take a
**collection** of statuses. ⚠️ **Preserve the existing calls verbatim** — a scalar call site must
keep working or this becomes a fleet-wide edit. ⛔ Do not change what any caller asks for; that
is T2.

**Out.** ⛔ Any change to which rows a consumer receives.

**Pre-change.** A RED test asserting the DESIRED behaviour: **a read for
`{QC_PASSED, QC_UNCHECKED}` returns the union** — which fails today because the signature takes a
scalar. ⛔ *Not "a two-status read cannot be expressed", which states the defect and would pass.*

**Verification.** A two-status read returns the union; every existing single-status call returns
exactly what it returned before, asserted at the store and through one Protocol fake.

### T2 — The four accepting reads, and the provenance they must carry

**Outcome.** A station whose observations are unchecked still gets a forecast, and that forecast
is marked `DEGRADED` on the `OBSERVATION` category rather than looking clean.

**In.**
- The two **model-input** reads (`operational_inputs.py:890`, `track_assembly.py:285`) accept
  `{PASSED, UNCHECKED}` and **contribute** an
  `InputQualityFlag(category=OBSERVATION, level=DEGRADED, detail=…)` when any accepted row is
  unchecked. ⛔ *An earlier draft wrote "raise" and omitted `detail`. The flag is a value that is
  aggregated (`types/domain.py:125`), not thrown, and `detail` is required — the literal as
  written would not have constructed.*
- 🔴 **`assess_input_quality` (`services/input_quality.py:29-40`) must take the observation
  quality as an input.** It derives `(level, flags)` from scalar keywords today and cannot see
  this at all. **This is the plumbing**, and it sits between the reads and the metadata.
- The two **freshness probes** (`:940`, `:338`) accept the status **without** degrading —
  D5: *"staleness probe; does not by itself degrade"*.
- 🔴 **The provenance must survive the whole route.** `OperationalInputMetadata` carries no
  quality field today, and the flag has to reach station **and group** quality assessment through
  the dataframe conversion. ⚠️ *An earlier estimate called this "four accepting reads"; an
  independent pass corrected it — the plumbing is the work, the call sites are the easy part.*
- **Every excluding read stays exactly as it is** — alerts, skill, training, hindcast
  (including the in-memory comparison at `services/hindcast.py:201`), onboarding's three, and the
  forecast-lab source. Assert; do not edit.
- 🔴 **Name `services/component_derivation.py:36-37` explicitly.** Its `_USABLE_STATUSES` is
  applied **after** an unfiltered fetch, so it is not one of the thirteen filter sites and a grep
  for the usual form will not surface it. 272's three-further-readers table assigns it **"No —
  with a consequence"**: calculated stations **do** go dark, which 272 records as an *accepted
  exception*, not an oversight. ⚠️ **Inherit that exception knowingly** — assert the exclusion and
  the consequence; do not quietly "fix" it here.

**Out.** ⛔ The published endpoint (T3). ⛔ Any new quality category or level.

**Pre-change.** A RED test asserting the DESIRED behaviour: **a station whose only observations
are unchecked DOES get forecast input, marked `DEGRADED`** — which fails today on both halves.
⛔ *Not "gets no input today", which states the defect and would pass.*

**Verification.** That station gets input, the forecast carries `DEGRADED` on `OBSERVATION`, and
the flag is readable at station and group level — not merely set inside the loader. A station
with clean observations is `FULL` and byte-identical to today. The freshness probes see the rows
and degrade nothing.

### T3 — Stop the published series showing unchecked values unmarked

**Outcome.** The published observed series excludes unchecked readings, per D5.

**In.** `api/routes/stations.py:698` — add the exclusion. ⚠️ **Mind the polarity**: this filter
lists what to reject, so a new status is included by default. That is *why* it was missed.

**Out.** ⛔ Any change to the response schema. ⛔ Marking rather than excluding — D5 chose
exclude.

**Pre-change.** A RED test asserting the DESIRED behaviour: **an unchecked reading does NOT
appear in the published series** — which fails today, because the filter excludes only
`qc_failed`. ⛔ *Not "it appears today", which states the defect and would pass.*

**Verification.** It does not appear; failed and passed rows behave exactly as before.

## Explicitly out of scope

- **Re-examination** — Plan 317. An unchecked row stays unchecked; that is a separate defect.
- **The zero-rule telemetry** — Plan 318.
- **The selection fix, the status, the migration** — Plan 272, shipped.
- **The bounded inference fetch** — 272 T2, unshipped and **needing re-justification, not
  building**: the existing two-hour window already yields a clean cadence on a healthy feed.
- **The onboarding path** — Plan 315. ⚠️ *An earlier draft said 315 "runs last" relative to these
  three; that is unsupported by the records: 315's `depends_on` names 264, 269, 303 and 313 —
  none of these — and all three of these carry `blocks: []`.* **There is no ordering relationship
  between this plan and 315 in either direction.**

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["T1"] },
    { "id": "phase-2", "tasks": ["T2", "T3"], "depends_on": ["phase-1"] }
  ]
}
```
