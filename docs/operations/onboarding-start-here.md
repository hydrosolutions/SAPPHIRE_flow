# Onboard stations: the guide

**Who this is for:** an organization (a hydromet service, a research group) that wants its
own stations in a SAPPHIRE Flow deployment, and the facilitator helping them. **What
"onboarding" means here:** bringing a station from "we have a list" to "its data is in the
system, checked, and ready" — one ordered journey, the same for every organization. **What
this is not:** the runbooks themselves. Each step below says what to do, which document
holds the details, and how you know you are done.

Status as of 2026-11-14, from a read-only look at the repository. Nothing was run. The
README step "Load the demo stations" is the Swiss route in this journey.

## Two routes exist today

| | Swiss route | Nepal route |
|---|---|---|
| Who it serves | Swiss stations from the public CAMELS-CH data | The Nepal deployment (tenant `chwrr`), DHM stations |
| How automated | One script or Prefect deployment does most steps ([README step 5](../../README.md), [deployment quick-start](../deployment-quickstart.md) § First data load) | Guided and partly manual; several steps are done by hydrosolutions |
| Another organization | Needs a data adapter for its own sources first — contact hydrosolutions | Same |

## Before you start

| You need | From |
|---|---|
| A running deployment and your organization declared in it (a "tenant") | Admin; [deployment quick-start](../deployment-quickstart.md), [CI/CD standard](../standards/cicd.md) |
| Station list: code, name, coordinates (keep leading zeros) | Your station register, checked by the hydrologist |
| An evidence folder **outside any Git checkout** | Facilitator creates it |
| Credentials as files (mode 600), never pasted into a command or a message | Admin |

## The steps

| # | You do | Open this | Done when | Who | Today |
|---|---|---|---|---|---|
| 0 | Deployment is up, your tenant exists | [Deployment quick-start](../deployment-quickstart.md) | Health checks pass; tenant created at startup from the host config | Admin | Ready |
| 1 | Get each station's basin outline and approve it | [Basin operator runbook](basin-operator-runbook.md) | An approved handover folder exists | Facilitator, hydrologist | Nepal: ready. Swiss: CAMELS-CH supplies basins |
| 2 | Register the basins with the weather data service, subscribe, hand off the history job, check coverage | [Gateway runbook](recap-gateway-runbook.md), top section | Coverage is verified; stations are eligible or held with a reason | Admin | Nepal: ready, with manual steps (you start the history job) |
| 3 | Create the station records | none for new stations | A station row exists per gauge | **hydrosolutions** | Swiss: the script. Nepal: **not built**, done by hand |
| 4 | Import basins and static attributes | [Basin importer runbook](basin-static-importer-runbook.md) | Basins stored per station | Facilitator | Ready once the station row exists |
| 5 | Store the weather history and bind each station to its weather source | [Six-station runbook](nepal-station-onboarding-runbook.md), [forcing runbook](nepal-forcing-runbook.md) | History stored per station | Facilitator | Nepal: six fixed gauges only |
| 6 | Load the observation history and run the quality checks | [September import](../runbooks/chwrr-dhm-history-import.md) | Daily values stored, checked, flags visible | Facilitator | Nepal: six fixed gauges only; the general route is planned ([#453 M1 plan](../plans/2026-11-14-delivered-discharge-files-for-existing-stations.md)) |
| 7 | Read the readiness report: ready or held, with a reason | six-station runbook, section "Run the six-station discharge preparation command" | Every station is accounted for | Facilitator, hydrologist | Nepal: six fixed gauges only |
| 8 | Switch on live data collection | none documented | Live readings arrive | **hydrosolutions** | Collection runs only for `operational` stations, and the forecast cycle also uses `operational` stations, so promoting a station does both |
| 9 | Evaluate models and activate forecasts | [Vision](../../planning/visions/2026-11-05-staged-station-onboarding-toward-a-production-pipeline.md) §4-5 | Hydrologist approves | Hydrologist | Swiss: Prefect model onboarding exists. Nepal: **not built** |
| 10 | Retire or reactivate a station | none | — | Admin | **Not built** |

Steps 0-7 do not activate forecasts. Step 8 can make a station eligible for the forecast cycle (see its note), and step 9 is where forecasts are approved and activated.

## When a step has no tool yet

Ask hydrosolutions for it and send: your organization, the step number, the station codes,
what you expected and the exact message you saw. **Never send observation values, delivered
files or credentials.** hydrosolutions does the manual part and records it in your
evidence folder.

## Rules that never change

- Restricted data never goes into the repository, logs, issues, commits or messages.
- Delivered files stay outside any checkout.
- No live Gateway step without the owner's go-ahead.
- The reviewer's name in any report is a label, not authentication.

## Planned, not available yet

A disposable installation check (public Swiss stations, no models) will prove that a new
installation is configured correctly before you start; it is planned, not available. See
the [vision](../../planning/visions/2026-11-05-staged-station-onboarding-toward-a-production-pipeline.md),
sections 10 (the check) and 11 (order of work).
