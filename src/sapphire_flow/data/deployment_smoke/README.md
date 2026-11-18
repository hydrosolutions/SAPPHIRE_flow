# Deployment smoke scenario resources

This is an incomplete, synthetic installation diagnostic, not an operational
forecast configuration or a runnable deployment. The manifest records the missing
station-supported discharge fixture, loader/QC qualification and runtime evidence.
No discharge dataset is supplied here. Unit-test inputs do not qualify that fixture.

The fixed cycle is `2025-01-03T00:00:00Z`. Weather contains one control member (`0`)
with precipitation in mm for the three future hours. The `icon_ch2_eps` source tag
uses existing internal routing; all weather is synthetic and no provider is called.
UTC is this diagnostic's explicit time choice, not an agency display default.

The pure model requires three hourly discharge observations ending one hour before
the cycle and precipitation at cycle +1, +2 and +3 hours. For member `m` from 0 to
19 it returns `Q_last + precipitation_factor * P + member_increment * m`.
FI trajectory columns are numbered `1` through `20`. Output starts two hourly steps
after the final observation, with three steps and units m³/s. These deterministic
values carry no scientific skill; the model is explicitly supplied, never globally
registered for operational use.

`model-config.json` owns the exact-byte configuration hash. `artifact.json` stores
the matching deterministic artifact; `manifest.json` pins the resource hashes.
Read resources using `importlib.resources.files("sapphire_flow")` and the relative
path `data/deployment_smoke/`; they are packaged in the wheel independently of tests.

Tests exercise real FI input/output adaptation and the configured operational
ensemble-size floor with correctly timed inputs. Generic model-onboarding helper
qualification remains unavailable: its generated prediction inputs reuse historical
timestamps for future weather and set issue time to the final observation. The
model's declared future grid and coverage checks remain strict. The planned audited
artifact-import path does not require those helper-generated prediction inputs.

Runtime commands, worker/API proof and administrator instructions belong to later
milestones in `docs/plans/deployment-hello-world.md`.
