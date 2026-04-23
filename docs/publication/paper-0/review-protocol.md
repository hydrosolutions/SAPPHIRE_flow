# Review Protocol: Scoping Review of ML for Operational Sub-Daily Flood Forecasting

**Status**: Draft working protocol  
**Folder**: `docs/publication/paper-0/`  
**Applies to**: the scoping review outlined in
[outline.md](outline.md)

---

## 1. Aim

This protocol defines how the literature review will be conducted so that the
resulting synthesis is transparent, reproducible, and defensible in peer
review.

The review asks:

> What is the current state of evidence on machine-learning approaches relevant
> to operational sub-daily flood and streamflow forecasting?

The review is designed as a **scoping review**, not a meta-analysis and not a
thesis-led narrative review.

---

## 2. Review Questions

### Primary question

What has been studied, with what methods and evidence, in relation to ML for
operational sub-daily flood and streamflow forecasting?

### Secondary questions

1. What operational forecasting systems exist, and what paradigms do they use?
2. Which ML architectures are relevant to sub-daily hydrology and forcing
   mismatch?
3. How is predictive uncertainty represented in ML streamflow forecasting?
4. What evidence supports hourly or sub-hourly forecasting value?
5. What is known about transferability and domain shift?
6. What datasets and forecast products enable or constrain this work?

---

## 3. Eligibility Criteria

| Dimension | Include | Exclude |
|---|---|---|
| Topic | Streamflow or flood forecasting with clear hydrological relevance | Studies on unrelated environmental forecasting tasks |
| Task type | Forecasting, early warning, probabilistic prediction, operational system description, benchmark resource papers | Pure hindcast simulation papers unless they directly inform forecasting design |
| Model type | ML, deep learning, hybrid process-ML, uncertainty/post-processing methods relevant to ML forecasting | Purely process-based papers unless needed for operational context or comparison |
| Temporal focus | Daily, hourly, or sub-hourly studies; studies on temporal mismatch or multiresolution forecasting | Long-term climate projections or seasonal water resources studies with no forecasting relevance |
| Data/product focus | Streamflow/stage datasets, reanalysis, NWP ensembles, reforecasts, operational forcing products | Generic remote-sensing or weather datasets with no clear streamflow application |
| Evidence type | Peer-reviewed papers, dataset papers, operational documentation, selected preprints with strong relevance | Non-traceable claims, marketing material, unsupported slide decks |
| Language | English | Non-English studies unless an English abstract contains enough information for classification |
| Time window | Primary search focus: 2018-2026, plus older foundational studies cited through snowballing | Exhaustive pre-2018 coverage |

### Notes on borderline cases

- A process-based operational system paper may be included if it defines the
  operational benchmark or uncertainty paradigm against which ML work is being
  assessed.
- A dataset paper may be included even if it contains no ML model, provided it
  materially changes what experiments are feasible.
- A preprint may be included if it is central to the field and clearly labelled
  as preprint in notes and later prose.

---

## 4. Information Sources

Search across the following sources:

1. `Scopus`
2. `Web of Science`
3. `Google Scholar`
4. `Backward citation chasing` from cornerstone papers
5. `Forward citation chasing` for high-impact recent studies
6. `Operational system websites or official technical documents` when needed
   for system descriptions not captured well in journals

### Source roles

- `Scopus` and `Web of Science` provide the core structured search.
- `Google Scholar` is used for supplementary capture, recent preprints, and
  dataset/technical material that may not yet be indexed.
- Citation chasing is mandatory for cornerstone papers and recent field-defining
  papers.

---

## 5. Search Strategy

### Search principles

1. Use broad concept clusters first, then narrow with section-specific terms.
2. Adapt syntax to each database, but keep concept logic consistent.
3. Log exact strings, dates, and hit counts.
4. Use citation chasing to capture studies missed by keyword mismatch.

### Core concept blocks

- `forecast task`: `"streamflow forecast*" OR "flood forecast*" OR hydrolog*`
- `method`: `"machine learning" OR "deep learning" OR LSTM OR transformer OR "neural network*" OR hybrid`
- `uncertainty`: `ensemble OR probabilistic OR uncertainty OR CRPS OR quantile OR CMAL OR "deep ensemble"`
- `temporal`: `"sub-daily" OR hourly OR "sub-hourly" OR "15-min*" OR "multi-timescale" OR multiresolution`
- `operational/data`: `operational OR benchmark OR dataset OR reforecast OR NWP OR reanalysis`

### Planned section-level query families

| Section | Candidate query pattern |
|---|---|
| Operational systems | `("flood forecast*" OR "streamflow forecast*") AND (ensemble OR probabilistic) AND (operational OR "early warning" OR EFAS OR GloFAS OR FEWS OR "Flood Hub")` |
| ML architectures | `("streamflow forecast*" OR "rainfall-runoff") AND ("machine learning" OR "deep learning" OR LSTM OR transformer OR ConvLSTM OR "state space model") AND (hourly OR "sub-daily" OR "sub-hourly" OR multiresolution OR "multi-timescale")` |
| Uncertainty | `("streamflow forecast*" OR hydrolog*) AND ("machine learning" OR LSTM OR "neural network") AND (ensemble OR probabilistic OR uncertainty OR quantile OR CMAL OR CRPS OR "deep ensemble")` |
| Temporal resolution | `("streamflow" OR flood) AND (hourly OR "sub-hourly" OR "15-min*" OR "temporal resolution" OR "temporal disaggregation") AND (forecast* OR hydrolog*)` |
| Transfer | `("streamflow" OR hydrolog*) AND ("transfer learning" OR ungauged OR regionalization OR generalization OR "domain shift") AND ("machine learning" OR LSTM OR "deep learning")` |
| Data/products | `(CAMELS OR CAMELSH OR LamaH OR HydroCH OR "NWIS" OR GEFS OR ECMWF OR TIGGE OR reforecast OR reanalysis) AND (streamflow OR flood OR hydrolog*)` |

### Search log requirements

For each executed search, record:

| Field | Description |
|---|---|
| Search ID | Unique identifier, e.g. `S3-WOS-2026-04-24-01` |
| Section | Which review question it serves |
| Database/source | Scopus, WoS, Scholar, citation chaining, official docs |
| Exact query | Full string as executed |
| Date searched | ISO date |
| Hit count | Reported by source |
| Notes | Filters, quirks, export issues, or scope caveats |

---

## 6. Screening Process

### Stage 1: Title and abstract screening

Screen records against the eligibility criteria. Exclude obviously irrelevant
records early, but keep borderline cases for full-text review.

### Stage 2: Full-text screening

Read the full paper and record one of:

- `include`
- `exclude`
- `include as contextual source only`
- `await verification`

### Full-text exclusion reasons

Use one primary exclusion reason:

1. Wrong task
2. Wrong domain
3. No meaningful ML relevance
4. No forecasting relevance
5. No usable methodological detail
6. Duplicate or superseded source
7. Irrelevant timescale
8. Irrelevant evidence type

### Screening rules

- When a paper is highly cited or apparently important but only marginally in
  scope, include it as a contextual source rather than forcing it into the core
  evidence set.
- If two versions of the same work exist, prefer the peer-reviewed version.
- If a preprint contains unique information not yet published, include it but
  flag the status clearly.

---

## 7. Data Charting Process

Data charting should use the reusable template in
[evidence-extraction-template.md](evidence-extraction-template.md).

Each included study should be charted for:

- citation and provenance
- publication status
- study type and research question
- geography, basin count, and hydroclimate context
- temporal resolution
- target variable and forecast horizon
- forcing and predictor types
- model family and architecture details
- uncertainty representation
- evaluation metrics
- operational realism
- limitations and external-validity concerns
- direct relevance to Paper 2
- claims the study can and cannot support

### Minimum charting rule

Every cornerstone paper cited in the manuscript must have a completed charting
record before manuscript drafting.

---

## 8. Evidence Appraisal

Because this is a scoping review, the aim is not a formal risk-of-bias score.
However, each included study should be assessed on the following dimensions:

| Dimension | Questions to record |
|---|---|
| Publication status | Peer-reviewed, preprint, technical report, official documentation |
| Operational realism | Real forecast setting or proxy benchmark only? |
| Data adequacy | Basin count, record length, resolution, forcing realism |
| External validity | Single basin, regional, cross-region, global |
| Uncertainty evaluation | Were calibration and sharpness assessed appropriately? |
| Comparison strength | Direct head-to-head comparison or isolated model claim? |
| Reproducibility | Code/data available? Method detail sufficient? |

This light-touch appraisal should be descriptive rather than numerical unless a
later manuscript version needs a formal rubric.

---

## 9. Synthesis Plan

### Descriptive mapping

For the included literature, summarise:

- publication year trends
- geography and hydroclimate coverage
- temporal resolution distribution
- target variables used
- forcing types used
- prevalence of uncertainty paradigms
- operational versus benchmark-only studies

### Narrative synthesis

For each review question:

1. Describe what the literature covers
2. Identify recurring methods and patterns
3. Summarise consistent findings
4. Note contradictions or counterevidence
5. Identify what remains uncertain
6. Extract implications for Paper 2

### Gap-statement rules

Use the following wording discipline:

- `No evidence found in current search` when the search has not identified a
  qualifying study
- `Evidence is limited` when only one or a few constrained studies exist
- `Open question` when existing studies point in different directions or are not
  directly comparable
- `High-confidence gap` only when search coverage is strong and the claim has
  been checked against citation chasing and verification notes

---

## 10. Claim Governance

Maintain a claim ledger for any statement that could be challenged in peer
review, especially:

- "first", "only", and "no study" claims
- exact dataset counts
- exact architecture details
- exact performance deltas
- claims based primarily on preprints

Each claim should be marked:

- `verified`
- `provisional`
- `contested`
- `remove before publication`

No claim should enter a publication-quality draft if it is still marked
`provisional` and central to the argument.

---

## 11. Deliverables

The review workflow should produce the following artifacts:

1. Revised scoping-review outline
2. Protocol document
3. Search log
4. Screening log
5. Evidence extraction records
6. Claim ledger
7. Manuscript draft

---

## 12. Practical Work Plan

### Phase 1: Protocol lock

- finalise review questions
- finalise eligibility criteria
- finalise query families
- align terminology across support docs

### Phase 2: Search and screening

- run database searches
- record exact strings and counts
- screen titles/abstracts
- screen full texts with reasons

### Phase 3: Charting and verification

- chart cornerstone studies
- resolve verification TODOs
- separate peer-reviewed evidence from preprints

### Phase 4: Synthesis

- rewrite support docs as evidence memos
- draft cross-cutting synthesis
- reduce duplication across sections

### Phase 5: Manuscript hardening

- audit overclaiming
- verify all tables and exact numbers
- write review limitations explicitly
- ensure each claim has traceable support

---

## 13. Success Criteria

This review is ready for a high-quality peer-review process when:

1. Another researcher could reproduce the search logic and understand why papers
   were included or excluded.
2. The review distinguishes clearly between established findings, limited
   evidence, and open questions.
3. The manuscript does not depend on unverified numbers or unsupported
   "first/only/no study" statements.
4. The resulting synthesis can stand alone even if Paper 2 ultimately changes
   direction.

