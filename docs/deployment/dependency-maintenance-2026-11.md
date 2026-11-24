# Dependency maintenance — November 2026 experiment

This began as an owner-authorized SAP-only compatibility experiment, not a deployment
or a claim of upstream Aquacast support, using Aquacast revision
`c271067bc34a0fee1dc0d8a710e95c888d29f72e`. Its declared `torch==2.9.*`
requirement is overridden by exact SAP pins `torch==2.10.0` and
`torchvision==0.25.0` to address CVE-2026-24747. The existing Rich override stays.

The targeted uv 0.11.7 resolution changes only Torch, torchvision,
`nvidia-nvshmem-cu12`, Triton, and the required new `cuda-bindings` and
`cuda-pathfinder` records. CUDA/Triton dependencies retain their platform markers.
Python, GIS, scientific configuration, FI, Recap and Aquacast source stay fixed.
No checkpoint change, retraining or source-side model workaround is included.

The same change incorporates the pending setup-uv action 10.3.3 SHA and Caddy
2.11.7 index-digest refresh. The uv binary remains 0.11.7. These updates do not
activate services. We will not manually close the independent Dependabot proposals
before merge; Dependabot may automatically close superseded proposals afterward.

Local Linux/x86_64 CPU qualification on Python 3.14.8 passed with the unchanged
trusted cmal_small checkpoint and recorded historical workshop inputs. Predict
and hindcast each repeated exactly, and baseline/candidate outputs matched
exactly. Those two-day inputs retain their raw-water-level, expired-rating,
unknown-zero, partial-support and historical-imputation warnings. This is
dependency compatibility evidence, not native operational forecast acceptance.

The independently measured Linux baseline reports the existing AQ/Rich mismatch
and missing eccodeslib. The candidate adds only the authorized AQ/Torch mismatch.
Dependency checks still exit nonzero; these reports are not hidden or represented
as clean installs. No GIS repair is included. Full final-commit CI and normal
default/Aquacast release-image proofs plus source/default/Aquacast image security
gates remain required. No baseline HIGH finding is automatically accepted.

The existing Aquacast image-proof CI job now scans its actual normal Dockerfile
image with the existing pinned Trivy action after a successful authorized build.
HIGH/CRITICAL vulnerability and secret findings block the job; the existing
ignore-unfixed policy is unchanged. Its job budget increases from 30 to 35 minutes
to fit the five-minute scan while preserving the prior three-minute overhead.
The no-token degraded case reports that no
image proof or scan ran and remains unavailable when `uv.lock` changes.

## Merged upstream discharge-gap support

AquaCast PR144 merged on 2026-11-09. The dependency now pins main's immutable
merge `cea602191534c37615c069c1c5416e2c99149668` (package 0.1.378), replacing
staging-only PR revision `b57a1872188e87e443fed2aa960b0885d4d537e2` (0.1.377).
There is no corresponding GitHub release tag; this is a commit pin, not a mutable
`main` dependency. ForecastInterface remains 0.1.20. Torch/Rich compatibility
exceptions above are unchanged and are not resolved by PR144.

The merged model configures the minimum finite discharge fraction with
`inference.min_past_discharge_fraction` (default 0). Its declared budget is
`lookback - ceil(fraction * lookback)`. Zero requires effective history
whole-channel discharge masking in the training configuration. The unchanged
vendored daily templates have history `p_channel=0.4`, zero target-day/std
filters, and `drop_input_nan_target_forecast` validation. Their discharge budgets
remain 30 (`cmal_small`) and 210 (`cmal_pool_pt`); precipitation and temperature
remain strict (`max_nan=0`). Neither registered template is multi-resolution.
Do not copy this tolerance to a differently trained checkpoint by editing its
saved config or declaring augmentation it never used.

SAP's FI gate passes admitted missing values unchanged. The model owns calendar,
shape and finite-history checks and uses its existing mask/indicator processing.
Missing discharge product is not made optional. Successful degraded output carries
`DATA_AVAILABILITY` through the existing FI boundary warning; it is not a new
persisted ensemble flag. The shim's vocabulary, physical unit conversion and
aggregation translation remain required; no SAP missing-discharge patch is removed.

Qualification must compare baseline and candidate in isolated normal Linux images,
using the same trusted checkpoint, saved/serving config, historical inputs and seed.
Complete and accepted-gap numerical outputs must match exactly. Short/absent rows
and non-finite values need measured outcomes, not assumed equivalence. Test-only
fraction changes stay in memory; the real checkpoint and vendored YAML stay intact.
Restricted CPU loading must never fall back to unsafe deserialization. Missing
image credentials or proof block acceptance; they are not a passing degraded case.
Historical execution and gap flags do not establish hydrological skill or authorize
live activation. This source change does not change checkpoints, training, ingest,
weather routing, assignments, API services, or deployed images.

### Known upstream failure-reporting exception

The owner accepted source-only adoption with the following upstream defect open
and will communicate it to the modeller. This is not full FI conformance approval.
`_issue_report` is identical in 0.1.377 and 0.1.378: it compares naive prediction
stamps with UTC-aware FI issue times and can raise `TypeError` instead of returning
`ModelFailure(INPUT_DATA)`. PR144's new positive-fraction total-rejection path
exposes it. At a test-only fraction0.2, 25 missing values in a30-day history trigger
the error in direct predict and all-rejected hindcast. SAP's normal NaN gate
correctly refuses that case before predict; that does not repair the model contract.

The serving templates retain default fraction0. No saved configuration, checkpoint,
input timestamps, gate or SAP exception handling is changed. Default-zero success
and measured failure paths matched the baseline in native historical checks;
other latent upstream failure paths are not proved safe. A strict expected-failure
test records the reporting defect and must be revisited when the upstream pin
changes. A new failure on the actual default Nepal SAP path or a numerical/config
regression still blocks adoption. No upstream issue was published by this change.

## Start-stamped CMAL qualification (24 November)

The isolated replacement-model branch pins Aquacast 0.1.396 at
`fc772226703940f26d29d8e62a5c39175851b2f8` and merged FI 0.2.10 at
`c1faa2e70b3832502efc5c22c99274635c027bb4`. Aquacast's dependency metadata
still requests FI 0.1.20; the explicit uv override resolves both consumers to the
same tested FI revision. The adapter's exact-version gate must match that pin.
The owner requested no further Aquacast iteration. FI #23 now reserves the
existing nullable integer `available_at_us` column. The shim preserves its values
and dtype during unit conversion and value-column renaming. End-to-end adoption
remains a release gate: observation availability must not be silently lost or treated as
a physical value by unit conversion. Grid metadata preservation alone does not
qualify the new model or authorize a live worker upgrade. Existing Rich/Torch
compatibility exceptions remain in force.

The prior rejection-report timezone defect is fixed in this revision. Its test
is now an ordinary regression test rather than an expected failure. The actual
23 November checkpoint loads with restricted Torch deserialization and declares
a daily START grid at 18:00 UTC; its discharge tolerance is 365 missing days,
while each of its four weather channels permits zero missing days. These are
construction/contract checks, not proof of operational forecasts or model skill.

FI 0.2.10 rejects duplicate output timestamps during construction. The adapter's
independent valid-time count regression therefore injects a malformed copy after
construction, retaining coverage of SAP's defensive check without relaxing FI.
