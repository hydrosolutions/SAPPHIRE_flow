# Paid Services and Business Model Notes

Context notes for future planning — not architectural.

## Potential Paid Add-Ons

- **HydroSHEDS basin delineation** — offered as a paid integration during
  station/organization onboarding. Pre-computed basin outlines worldwide; could also
  serve as quality-check for user-uploaded outlines.

- **Model retraining** — offered as a paid service for deployments of the open-source
  software.

## Maintenance Contracts

- **Rate:** 203 CHF/hour.
- **Self-service goal:** Training material will be developed to enable hydromets to
  handle maintenance mostly independently.

## Implications for Architecture

These don't change the architecture directly but inform which features should be
designed as pluggable/optional (e.g. HydroSHEDS integration behind a feature flag
or service tier).
