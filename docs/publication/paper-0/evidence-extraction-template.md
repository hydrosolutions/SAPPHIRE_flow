# Evidence Extraction Template

Use one copy of this template for each included cornerstone paper, dataset
paper, or operational source. Keep the extraction factual first; interpretation
comes later.

Recommended filename pattern:

`evidence-<short-key>.md`

Example:

`evidence-klotz-2022-cmal.md`

---

## 1. Citation and Provenance

- Full citation:
- DOI / URL:
- Publication status: `peer-reviewed` / `preprint` / `technical report` /
  `official documentation`
- Search source: `Scopus` / `Web of Science` / `Google Scholar` /
  `backward citation` / `forward citation` / `official site`
- Search ID:
- Date screened:
- Screening decision: `include` / `context only` / `await verification`
- Extracted by:
- Verified against source text: `yes` / `partly` / `no`

## 2. Why This Study Is in Scope

- Which review question(s) does it inform?
- Why is it important?
- Is it a cornerstone paper, contextual paper, dataset paper, or operational
  source?

## 3. Study Classification

| Field | Value |
|---|---|
| Study type | e.g. benchmark, operational system, method paper, dataset paper |
| Geography | |
| Hydroclimate / terrain | |
| Number of basins / sites | |
| Temporal resolution | |
| Forecast horizon | |
| Target variable | streamflow / stage / flood class / other |
| Operational setting | real-time operational / quasi-operational / benchmark only |

## 4. Data and Forcing Setup

| Field | Details |
|---|---|
| Observations used | |
| Forcing type | observed / reanalysis / deterministic NWP / ensemble NWP / mixed |
| Ensemble details | member count, source, lead time, if applicable |
| Input variables | |
| Static attributes | |
| Record length | |
| Train/validation/test split | |
| Leakage risks noted | |

## 5. Model and Method Details

| Field | Details |
|---|---|
| Model family | LSTM / transformer / hybrid / QRF / etc. |
| Specific architecture | |
| Spatial handling | lumped / distributed / gridded / graph / other |
| Multi-timescale support | yes / no / partial |
| Future forcing support | yes / no / partial |
| Uncertainty method | none / CMAL / quantile / deep ensemble / CRPS / post-processing / other |
| Training objective | |
| Baselines compared against | |

## 6. Main Findings Reported by the Study

- Finding 1:
- Finding 2:
- Finding 3:

Include exact numbers only if they have been checked directly against the
source text.

## 7. Evaluation and Evidence Strength

| Dimension | Notes |
|---|---|
| Metrics used | |
| Calibration assessed? | |
| Sharpness assessed? | |
| Spread-skill assessed? | |
| Baseline comparison strength | weak / moderate / strong |
| External validity | weak / moderate / strong |
| Reproducibility | weak / moderate / strong |

## 8. Limitations and Caveats

- What are the study's own stated limitations?
- What limitations matter for this review?
- Does the study rely on unrealistic forcing, narrow geography, or a small
  sample?
- Are there signs of overclaiming?

## 9. Claims This Study Can Support

- Supported claim 1:
- Supported claim 2:

Keep these narrow and literal.

## 10. Claims This Study Cannot Support

- Unsupported claim 1:
- Unsupported claim 2:

This section is important for avoiding confirmation bias.

## 11. Counterevidence or Tension With Other Studies

- Does this study contradict a commonly repeated claim?
- Does it only apply under certain conditions?
- What comparison papers should be read alongside it?

## 12. Relevance to Paper 2 / SAPPHIRE

- Direct design implications:
- What should this change in our experimental plan?
- What should it *not* be used to justify?

## 13. Verification Checklist

- [ ] Citation checked
- [ ] DOI / URL checked
- [ ] Publication status checked
- [ ] Exact dataset counts checked
- [ ] Exact performance numbers checked
- [ ] Architecture details checked
- [ ] Any "first/only/no study" phrasing avoided
- [ ] Notes distinguish evidence from interpretation

## 14. Extraction Summary

### One-sentence summary

-

### Confidence label

Choose one:

- `established finding`
- `promising but limited evidence`
- `no evidence found in current search` (only for a question-level memo, not an
  individual paper)
- `provisional claim`
- `open question`

