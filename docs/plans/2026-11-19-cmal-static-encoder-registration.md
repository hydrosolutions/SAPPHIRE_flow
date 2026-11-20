---
status: READY
created: 2026-11-19
---

# Register the supplied CMAL static-encoder configuration

The owner selected the CMAL checkpoint in the supplied Nepal static-encoder
delivery and requested that onboarding continue. This slice makes that exact
configuration discoverable through the existing Aquacast/FI boundary. It does
not import, promote, assign, train, enable alerts, or claim operational readiness.

## Evidence and boundaries

The accepted 0.1.1159 Aquacast image contains Aquacast 0.1.378 and FI 0.1.20.
The supplied checkpoint identifies Aquacast 0.1.390. Isolated, network-disabled,
credential-free probes on the deployed image passed restricted deserialization,
exact tensor serialization round-trip, and two equal seeded FI predictions with
synthetic inputs: 15 daily deterministic values and seven quantiles, all finite.
No dependency upgrade is justified by version numbers alone.

The exact supplied config SHA-256 is
`c171b2bc26a3cd11eec64378322fae443a113a86d1fcfb2fe6f5edd2e1e296d9`.
Its input requirements are 365 daily past steps, up to 15 future steps with the
installed FI at-most declaration, 78 Caravan statics, and separate precipitation,
temperature, net thermal radiation and net solar radiation channels. Past
discharge permits 365 missing values; weather channels permit none.

Live radiation parameters and provider mappings are missing. Existing generic
radiation cannot substitute for either net component. Accumulated source energy
must not be relabelled as mean flux; qualified interval conversion is separate
work. This patch retains fail-closed compatibility checks.

FI does not currently require complete training provenance, while SAP3's import
API requires training dates. The owner has referred that mismatch to FI. Do not
invent dates, change the import schema, or add a private metadata workaround.
Qualified Nepal-day alignment remains separate from current-contract rehearsal.

## Implementation

1. Vendor the supplied inference YAML unchanged as
   `models/aquacast/configs/cmal_nepal_static_encoder.yaml`. Checkpoint bytes,
   training data and private paths stay outside Git.
2. Add `CmalNepalStaticEncoder`, using the existing `AquacastShim` unchanged.
   Bind only the config filename and existing model classifications:
   `ModelTier.SKILL`, `AlertEligibility.NO_EVENT_INFORMATION`. Preserve the
   default empty fine-tuning allowlist and Caravan static namespace. Expose the
   class and register entry point `cmal_nepal_static_encoder`.
3. Test the public declaration, artifact scope, exact config binding and entry
   point discovery. Verify the distinct radiation names and units, canonical
   temperature/discharge translation, 365-step lookback, zero weather NaN
   tolerance and installed FI flexible-horizon declaration. Exercise the new
   configuration through the established translation fixtures where useful;
   do not assert private object internals or add large checkpoint fixtures.
4. Update the model-onboarding guide and relevant FI/model touchpoint map to
   describe the available registration and its unresolved input/provenance
   requirements. Preserve the existing live `cmal_small` configuration and
   selection. Link this narrow slice from the Nepal onboarding plan.

## Verification and delivery

Run affected shim/declaration/translation/unit/conformance tests, lint and
format checks and the applicable type ratchet. One independent Claude plan
review precedes implementation; one independent patch review follows. Keep
review reports private. Full final suite/CI must pass before owner merge. Apply
the mandatory patch version bump at code commit. Hold at PR; no agent merge,
release publication or new host deployment is part of this slice.

After owner merge and normal verified delivery, actual import remains gated by
the coordinated provenance decision. Real-input prediction additionally needs
qualified radiation history/forecasts and other declared inputs. Do not turn
synthetic probe success or discovery into scientific or operational approval.

```json
{"review":{"status":"COMPLETE"},"registration":{"depends_on":["review"]},"verification_and_patch_review":{"depends_on":["registration"]},"owner_merge":{"depends_on":["verification_and_patch_review","final_full_CI"]},"runtime_delivery":{"status":"SEPARATE"},"artifact_import":{"status":"BLOCKED","depends_on":["runtime_delivery","FI_provenance_resolution"]},"real_input_qualification":{"status":"BLOCKED","depends_on":["qualified_radiation_inputs","runtime_delivery"]}}
```

Independent Claude review on 19 November found no necessary blockers. The
orchestrator marks this bounded registration slice READY. Include package and
registry checks, assert the actual installed horizon declaration and config
digest, and correct the stale shim prose about at-most support. This is not
readiness for importing or activating the artifact. Review report stays private.
