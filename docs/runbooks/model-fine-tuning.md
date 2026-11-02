# Fine-tuning a deployed model (warm-start retrain)

How an operator fine-tunes an existing model artifact on this deployment's data.
Written so DHM colleagues can eventually run it themselves. Status: **draft** —
sections marked *OPEN* are settled by the first real staging run and edited then.

A fine-tune starts from an existing artifact (the **donor**), trains it further,
and stores the result as a **new, unpromoted** artifact. Nothing is promoted,
assigned, or served automatically. A person decides that separately.

## What you need before you start

1. **A donor artifact.** Its ID, supplied by you. The run never picks "the active
   one" for you. ⛔ **Always pass `base_artifact_id`.** Without it the flow runs an
   ordinary from-scratch training and *promotes* the result. For `cmal_small` the
   shim refuses a `finetune` block on that path, but that guard is per model: do not
   rely on it for any other model.
2. **A model that allows the strategy.** Each model lists the fine-tuning strategies
   this deployment permits, in its shim class (`FINETUNE_STRATEGIES`). Anything not
   listed is refused. Currently `cmal_small`: `static_only`, `last_layer`. Adding one
   is a code change (one line + review), made after that strategy has been run.
3. **An unchanged model template.** The donor records the hash of the model's
   config file. If the installed config differs, the run is refused before any
   training. Do not edit the vendored config file to change run behaviour; put
   run settings in the run parameters below.
4. **Training data for the target stations** in the deployment database, over the
   training window you choose.

## Aquacast artifact compatibility after a dependency update

The current immutable Aquacast source pin declares 0.1.376; ForecastInterface
remains at 0.1.20. This is a merged Git source pin, not evidence of an upstream
published wheel or tag. The existing Rich 14 compatibility override remains an
unresolved exception to Aquacast's declared Rich >=15 requirement.

Aquacast's artifact loader, reached through the SAP shim, now uses restricted
loading on CPU, with no unrestricted fallback. Qualify the exact stored bytes for every ACTIVE assignment before
model execution with a new image. Deploying the image affects existing ACTIVE
artifacts even without a new import. A construction test alone is insufficient.

- Compare declarations, effective settings and fixed-input CPU outputs against
  the previous pin. Two newly added training settings,
  `training_supervision_file` and `training_samples_per_epoch`, default to `None`;
  unchanged prior fields do not make the complete parsed configurations identical.
- Test portable export/reload separately from cross-version output comparison.
  Within-version determinism is not a promise of cross-version bitwise equality.
- Keep trusted producer, portable-export and stored-byte digests separate. The
  importer preserves supplied bytes; it does not normalize training checkpoints.
- Export clears the known daily-encoder checkpoint path in a copy, not in the
  original. Bundle reconstruction uses embedded weights. This is not a general
  path scrubber or a guarantee of donor-independent training-pipeline resume.
- The scoped Torch safe-global registry is process-global. Require isolated,
  non-overlapping loading across forecast, hindcast, import, onboarding and
  training flows. Existing grants for the same types also overlap. Verify the
  initial approved registry is restored; a sequential local test is not proof of
  deployed process isolation. Do not add a broad allowlist or unsafe fallback.
- Check each actual artifact's posterior. Lazy Laplace import does not prove
  first-use availability. Restricted loading does not establish provenance or
  resource safety.
- Roll back an image only if no new-version artifacts were created/promoted, or
  all such artifacts are qualified against the previous runtime.

The Aquacast-extra artifact-policy tests use synthetic bundles, not private
checkpoints. Run them with `pytest -rs` and require no relevant skips. Real
ACTIVE-artifact qualification and deployment approval remain separate gates.

## The run parameters

The `train-models` flow takes these (all per run, all recorded with the artifact):

| Parameter | Meaning |
|---|---|
| `model_ids` | the model, e.g. `["cmal_small"]` |
| `group_ids` | the station group to train on |
| `base_artifact_id` | the donor's artifact ID |
| `training_params` | the model's own settings, passed through unchanged |

For aquacast models `training_params` has two blocks:

- `trainer`: **required:** `train` (date range), `lr`, `batch_size`, `max_epochs`.
  Optional: `val`, `test`, `optimizer`, `weight_decay`, `early_stopping`.
- `finetune`: `strategy` plus that strategy's settings. (The key is `finetune` in
  aquacast's run config, checked against the aquacast build on the staging host.)

### Fine-tuning strategies

| Strategy | What changes | Extra settings |
|---|---|---|
| `static_only` | one bias vector on the basin-attribute branch (a handful of values); needs a model built with that branch, else refused | `lr` |
| `last_layer` | only the final prediction layer (the head) | `lr` |
| `full_model` | everything | `lr` |
| `lora`, `lora_ensemble` | small added low-rank weights | `rank`, `alpha`, `lora_target`, `lora_layers`, `n_members` |
| `svd`, `svd_ensemble` | singular values of the layers chosen by `lora_target` / `lora_layers` (default target: the head) | `lora_target`, `lora_layers`, `svd_init_std`, `svd_train_head`, `n_members` |

`normalization` (`pretrained` keeps the donor's input scaling, `refit` recomputes it
on the new data) applies to all. Use `pretrained` unless you are deliberately
testing rescaling: it changes every input the model sees.

`static_only` trains very few values, so the stored artifact is expected to differ
from the donor only slightly in its weights. How much that changes the forecasts is
something to measure, not assume.

The first plumbing run on staging uses `strategy = static_only`, `lr = 1e-4`,
`normalization = pretrained`, `max_epochs = 2`. It checks that the mechanics work,
not that the model is good.

## Procedure

1. Pick the donor and record its ID. Confirm it is the artifact you mean.
2. Confirm the strategy is on the model's allowlist (above).
3. Confirm the run window has observations and forcing for every target station.
4. Start the `train-models` flow with the parameters above. *OPEN: exact command
   and deployment name, recorded after the first run.*
5. Read the outcome (below). Do not retry anything you cannot explain.

## After the run: what must be true

- A **new artifact** exists, in a non-active state. The donor is untouched and the
  donor's artifact is still the one serving forecasts.
- One row in the warm-start provenance record names the donor, the config it used
  and the settings supplied. The settings supplied, received and recorded are the
  same values.
- The new artifact loads back from storage and can predict.
- The past-leg forcing used is the operational reanalysis source.

Passing these means the pipeline works. It does **not** mean the model is better.
Judging quality (skill against the current artifact) is a separate step. Promoting
the result is a separate, owner-level decision.

## When it stops

| What you see | Meaning | Action |
|---|---|---|
| "base artifact not found" / hash check failed | wrong or damaged donor | stop, fix the ID |
| "not in template's supported set" | strategy not allowed here | stop; adding it is a code change |
| "names no base_artifact_id" (`ConfigurationError`) | a `finetune` block without a donor | stop; pass `base_artifact_id` |
| changed-template refusal | config differs from the donor's | stop; do not edit the config |
| a refusal about features or fine-tuning settings | our configuration | stop and report |
| a clearly transient failure (network, timeout) | environment | retry once, then escalate |
| `PicklingError` from the data loader | worker processes under Python 3.14 (the shim loads in-process) | stop; check that the effective training loader has `num_workers=0`; if it does, keep the traceback and escalate |
| anything else | unknown | stop and keep the logs |

## Known limits

- Training data loads **in-process** (no worker processes) for the aquacast models. aquacast builds its collate
  function as a local closure, which cannot be pickled for the `forkserver` start method that Python 3.14 uses by
  default, so the vendored `num_workers: 4` made every train or retrain raise `PicklingError` (found on the first
  staging run, 2026-09-29). The shim forces 0 workers; loading is slower, which matters little for a short
  fine-tune. Remove the override once aquacast makes its collate picklable.
- The aim of fine-tuning on this deployment's forcing is to reduce the **climatology**
  mismatch between training data and serving data. Whether it does is a hypothesis to
  evaluate. It does **not** teach the model the forecast model's error behaviour: the
  future leg is reanalysis in training and weather forecasts in serving.
- Which strategy suits which basins is a modelling judgement, made by the modeller.

## OPEN (settled by the first run)

- The training and validation windows for the pilot group.
- `batch_size` and the trainer `lr`.
- The exact command to start the run.
- Expected runtime on the staging host.
