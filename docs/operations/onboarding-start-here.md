# Start here: onboard stations

**What this is:** the map of the station-onboarding journey for Nepal. It tells a
facilitator which document to open at each stage, what you get, and what is still
done by hand. **What this is not:** a runbook. It repeats no commands except where
one is the only entry point, and it activates no forecasts. (The README step
"Onboard initial data (CAMELS-CH)" is Swiss developer setup, not station onboarding.)

Status as of 2026-11-14, from a read-only look at the repository. Nothing was run.

## Who does what

| Role | Does |
|---|---|
| Facilitator | Runs the tools, keeps the evidence folder, chases open items |
| Hydrologist | Decides whether each basin and station is suitable; owns the scope |
| Admin | Holds the deployment, secrets and Gateway key; runs the Gateway steps |
| hydrosolutions | Does the manual gaps below; owns the Gateway and models |

## Inputs you need before you start

| Input | Where it comes from |
|---|---|
| Station list: code, name, coordinates (leading zeros kept) | DHM published list, checked by the hydrologist |
| Running deployment and tenant (`chwrr` for Nepal) | Admin |
| Basin extraction tool and its result | Facilitator, see stage 1 |
| New Gateway name, history years, forecast scope | Agreed with the Gateway admin and the hydrologist |
| Gateway API key file (mode 600, never pasted) | Admin |
| Evidence folder **outside any Git checkout** | Facilitator creates it |
| Delivered DHM files, if any | DHM; stored outside any checkout |

## The stages

| Stage | Who | Open this | Status | What you get |
|---|---|---|---|---|
| 0. Deployment and tenant exist | Admin | [cicd standard](../standards/cicd.md) (`init`, `provision_tenants`), [deployment quick-start](../deployment-quickstart.md) | Ready | A running stack with the tenant created from the host's config |
| 1. Basin extraction, review, handover | Facilitator, hydrologist | [Basin operator runbook](basin-operator-runbook.md) | Ready | An approved, immutable handover folder |
| 2. Gateway registration, subscription, history | Admin | [Gateway runbook](recap-gateway-runbook.md), top section | Ready with manual steps (you start the history job yourself) | Basins registered and covered in the Gateway |
| 3a. Station records for new stations | hydrosolutions | none | **Not built** (see gaps) | A station row per gauge |
| 3b. Basin and static package import | Facilitator | [Basin importer runbook](basin-static-importer-runbook.md) | Ready once the station row exists (the generic importer looks stations up by code); the Nepal wrapper covers the six September gauges only | Basins and static attributes in the database |
| 3c. Weather binding and ERA5 history | Facilitator | [Six-station runbook](nepal-station-onboarding-runbook.md) (sections "Confirm station rows exist" and "Run the six-station discharge preparation command"), [forcing runbook](nepal-forcing-runbook.md) | Partly: six fixed gauges only | Stored weather history per station |
| 3d. Observations: delivered history | Facilitator | [September import](../runbooks/chwrr-dhm-history-import.md) | Partly: six fixed gauges only. The general route is a draft plan, not built | Daily discharge history |
| 3d. Observations: live collection | hydrosolutions | none | Partly: no per-station switch documented | Live readings |
| 3e. Quality checks and readiness report | Facilitator | six-station runbook, section "Run the six-station discharge preparation command" (`onboard_nepal qualify`) | Partly: six fixed gauges, per-station held or ready report | Ready or held, with a reason |
| 4. Models, evaluation, activation | hydrosolutions, hydrologist | [Vision](../../planning/visions/2026-11-05-staged-station-onboarding-toward-a-production-pipeline.md) §4-5 | Not built for onboarding (`cmal_small` runs only in the Swiss pipeline) | Nothing yet |
| 5. Retire or reactivate a station | Admin | none | Not built | Nothing yet |

No stage above activates forecasts. New stations stay in status `onboarding`, and
the forecast cycle only picks up `operational` stations (`run_forecast_cycle.py`).

## Known gaps today (done by hand by hydrosolutions)

| Gap | Why it matters | What happens today |
|---|---|---|
| No general "register a new station" command | `import_dhm_delivery stations` accepts only codes 447, 450, 604.5, 647, 670, 684; `onboard_nepal` is limited to the `chwrr` tenant and those six | hydrosolutions creates the station row by hand; stages 3b-3e need it first |
| Nepal commands are tied to the six-gauge batch and one Gateway name | New batches cannot reuse them unchanged | Treat as manual until the general route exists |
| General October delivery route | Only a draft plan | The workshop uses the September data |
| Separating "collect observations" from "run forecasts" | No documented switch found; normal ingestion depends on operational station state (see the [#453 vision](../../planning/visions/2026-11-07-prepare-new-stations-in-sapphire.md), "Reuse evidence and known gaps") | Ask hydrosolutions before enabling live collection |
| Model evaluation and activation for new stations | Not built | Out of scope until after the workshop |
| Retirement and reactivation | Not built | Not available |

## Rules that never change

- Restricted DHM data never goes into the repository, logs, issues or commits.
- Delivered files stay outside any checkout.
- No live Gateway step without the owner's go-ahead.
- No onboarding step activates a forecast.
- The reviewer's name is a label, not authentication.

## Planned, not available yet

A disposable installation check (Swiss stations, no models) is planned, not
available. See the [vision](../../planning/visions/2026-11-05-staged-station-onboarding-toward-a-production-pipeline.md),
section 10 (the check) and section 11 (order of work to the workshop).
