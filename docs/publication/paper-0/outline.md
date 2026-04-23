# Scoping Review: Machine Learning for Operational Sub-Daily Flood Forecasting

**Purpose**: This review now serves two linked roles:
1. **Research design**: build a defensible evidence base for Paper 2
2. **Publication asset**: mature into a publishable review-style manuscript,
   commentary, or introduction section once the evidence base is complete

**Review objective**: Map and synthesise the current evidence on machine
learning approaches relevant to operational sub-daily flood and streamflow
forecasting, with emphasis on uncertainty representation, temporal resolution,
transferability, and data readiness.

**Review type**: Structured scoping review. The aim is not to prove a thesis or
identify a single "best" model. The aim is to document what has been studied,
how it has been studied, where evidence is strong or weak, and which open
questions remain after a transparent search and synthesis process.

---

## Primary Review Question

What is the current state of evidence on machine-learning approaches relevant to
operational sub-daily flood and streamflow forecasting?

## Sub-Questions

1. What operational flood and streamflow forecasting systems currently exist,
   and what modelling paradigms do they use?
2. Which ML architectures have been tested for hydrological forecasting at
   daily, hourly, or sub-hourly resolution, and how do they handle forcing
   mismatch across timescales?
3. How is predictive uncertainty represented in ML-based streamflow
   forecasting, especially when ensemble NWP is available?
4. What evidence exists on the value and limits of hourly versus sub-hourly
   prediction for operational forecasting?
5. What is known about transfer learning, domain shift, and generalisation at
   sub-daily timescales?
6. What datasets, benchmark resources, and NWP/reforecast products exist to
   support this line of work?

---

## Scope Boundaries

| Dimension | In scope | Out of scope |
|---|---|---|
| Task | Flood or streamflow forecasting; operational or operationally relevant forecasting; benchmark studies that inform forecasting design | Pure simulation papers with no forecasting relevance; inundation mapping without discharge/forecast relevance |
| Methods | ML or hybrid process-ML methods; uncertainty methods; post-processing methods; operational ensemble handling approaches | Purely process-based method papers unless needed for operational context or comparison |
| Temporal focus | Daily, hourly, and sub-hourly studies, with emphasis on sub-daily and temporal mismatch problems | Long-term climate-impact studies without forecast setting |
| Data products | Hydrological benchmark datasets, reanalysis, NWP ensembles, reforecasts, stage/discharge archives | Generic Earth-system datasets with no clear relevance to streamflow forecasting |
| Evidence types | Peer-reviewed papers, major dataset papers, operational system documentation, highly relevant preprints clearly labelled as such | Informal blog posts, slides, or unsupported claims without traceable source |
| Time window | Primary focus: 2018-2026; older foundational papers included when they define the field or methods | Exhaustive historical reconstruction of pre-deep-learning hydrology |

---

## Working Principles

1. **Evidence before interpretation**: each section first describes what the
   literature covers before drawing implications for SAPPHIRE or Paper 2.
2. **No gap statements without search coverage**: use "no evidence found in the
   current search" unless a claim has been explicitly checked and verified.
3. **Separate evidence from advocacy**: the review can motivate Paper 2, but it
   should not be written as a justification memo for a preselected design.
4. **Make uncertainty visible**: label preprints, contested findings, and
   unverified numbers clearly.
5. **Treat null coverage carefully**: absence of evidence in the reviewed
   literature is not the same as proof that no such work exists.

---

## Planned Manuscript Structure

### 1. Introduction and Rationale

- Why sub-daily flood forecasting matters operationally
- Why ML is entering operational hydrology now
- Why a scoping review is needed: the literature is fragmented across
  operational systems, ML architectures, uncertainty methods, transfer, and
  data infrastructure

### 2. Review Methods

- Review type and rationale
- Eligibility criteria
- Information sources and search strategy
- Screening process
- Data charting process
- Synthesis approach
- Review limitations

### 3. Operational Forecasting Landscape

**Question**: What operational systems exist, and where does ML currently fit?

**Evidence to capture**:
- major operational ensemble systems and their modelling paradigm
- whether forecasts are deterministic or probabilistic
- whether uncertainty comes from NWP, learned distributions, post-processing, or
  other sources
- which systems operate at daily, hourly, or finer resolutions

**Primary evidence base**:
- [01-operational-systems.md](source-reviews/01-operational-systems.md)

### 4. ML Architectures and Temporal Representation

**Question**: Which ML architectures are relevant to sub-daily hydrology, and
how do they represent multi-timescale inputs and outputs?

**Evidence to capture**:
- architecture families (LSTM, multi-timescale variants, transformers, SSMs,
  physics-informed hybrids, spatial models)
- support for future forcing, multi-resolution input, and spatiotemporal data
- whether studies use observed forcing, reanalysis, deterministic NWP, or
  ensemble NWP
- where evidence stops at daily or hourly resolution

**Primary evidence base**:
- [02-ml-architectures.md](source-reviews/02-ml-architectures.md)
- relevant temporal-resolution material from
  [04-sub-hourly-resolution.md](source-reviews/04-sub-hourly-resolution.md)

### 5. Uncertainty Representation and Ensemble Handling

**Question**: How is predictive uncertainty represented in ML-based streamflow
forecasting, and what evidence exists for different uncertainty paradigms?

**Evidence to capture**:
- NWP pass-through, learned distributions, deep ensembles, post-processing, and
  generative approaches
- whether uncertainty arises from forcing, model stochasticity, or residual
  error modelling
- how studies evaluate calibration, sharpness, and spread-skill
- whether any studies compare paradigms on common data and tasks

**Primary evidence base**:
- [03-uncertainty-paradigms.md](source-reviews/03-uncertainty-paradigms.md)

### 6. Temporal Resolution: Value, Limits, and Measurement Constraints

**Question**: What evidence exists on when hourly or sub-hourly prediction adds
operational value, and what limits that value?

**Evidence to capture**:
- catchment response time and scale effects
- whether sub-hourly outputs are supported by the input forcing and observation
  systems
- stage versus discharge as operational targets
- rating-curve uncertainty and its consequences for evaluation
- temporal disaggregation approaches and their evidential status

**Primary evidence base**:
- [04-sub-hourly-resolution.md](source-reviews/04-sub-hourly-resolution.md)
- relevant architecture material from [02-ml-architectures.md](source-reviews/02-ml-architectures.md)

### 7. Transferability, Domain Shift, and Generalisation

**Question**: What is known about transferring ML hydrology models across
basins, climates, and forcing domains, especially at sub-daily timescales?

**Evidence to capture**:
- multi-basin versus single-basin training
- out-of-region and cross-climate transfer
- entity awareness and static attributes
- reanalysis-to-NWP shift
- uncertainty calibration under transfer

**Primary evidence base**:
- [05-transfer-learning.md](source-reviews/05-transfer-learning.md)

### 8. Data Ecosystem and Benchmark Readiness

**Question**: What datasets and forecast products make this research feasible,
and where are the structural data bottlenecks?

**Evidence to capture**:
- curated streamflow/stage datasets by temporal resolution and geography
- reanalysis and NWP ensemble/reforecast products
- access constraints, member count mismatch, and data quality issues
- countries or hydroclimates that remain unrepresented

**Primary evidence base**:
- [06-datasets-and-nwp.md](source-reviews/06-datasets-and-nwp.md)
- [precipitation_products.md](source-reviews/precipitation_products.md)

### 9. Cross-Cutting Synthesis

This section should answer:
- where evidence is mature
- where evidence is thin but suggestive
- where direct comparison studies are missing
- which claims are currently hypotheses rather than findings
- what this means for Paper 2's experimental design

### 10. Review Limitations

- search limitations
- reliance on preprints in fast-moving areas
- potential language and database bias
- difficulty comparing studies across different basins, targets, and metrics

### 11. Conclusion and Research Agenda

- concise summary of the mapped evidence
- high-confidence gaps
- medium-confidence open questions
- immediate implications for SAPPHIRE / Paper 2

---

## How the Existing Support Documents Should Evolve

The six support files are now best treated as **evidence memos**, not draft
manuscript sections. Each memo should eventually follow a common structure:

1. Scope of the memo
2. Search coverage and any caveats
3. Evidence tables
4. Narrative synthesis
5. Limits and counterevidence
6. Claims supported by the memo
7. Open verification items

This will make it easier to merge them into a single review without carrying
over repeated claims, inconsistent wording, or unresolved numbers.

---

## Evidence Language Policy

Use the following labels consistently in the outline, support docs, and later
manuscript draft:

- **Established finding**: supported by multiple verified sources
- **Promising but limited evidence**: supported by one or a few studies with
  clear constraints
- **No evidence found in current search**: current search did not identify a
  study addressing the question
- **Provisional claim**: plausible but not yet fully verified
- **Open question**: important issue for which the literature remains unclear

Avoid:
- "gap confirmed" unless the search coverage and verification status justify it
- "first", "only", or "no study" claims unless specifically checked
- strong design prescriptions based on a single paper or unverified preprint

---

## Peer-Review Readiness Criteria

Before this review is written as a paper-quality manuscript, the following
should be complete:

1. Protocol finalised and followed
2. Search log completed with dates and exact strings
3. Screening decisions recorded with exclusion reasons
4. Evidence extraction completed for all included cornerstone studies
5. Critical factual claims either verified, softened, or removed
6. Preprints clearly separated from peer-reviewed evidence
7. Manuscript sections rewritten to separate evidence, interpretation, and
   research agenda

---

## Next Execution Steps

1. Build a search log and claim ledger from the protocol
2. Rework the six support documents into evidence memos with a shared structure
3. Chart the cornerstone studies using the evidence extraction template
4. Resolve the highest-risk verification items before drafting prose
5. Draft the manuscript from the synthesised evidence, not from the old thesis
