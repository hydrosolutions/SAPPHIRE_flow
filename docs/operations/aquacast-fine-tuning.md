# Aquacast fine-tuning configuration

Checked 23 November 2026 against the pinned Aquacast revision
`cea602191534c37615c069c1c5416e2c99149668` (operational configuration)
and upstream `fc772226703940f26d29d8e62a5c39175851b2f8` (fine-tuning engine
and examples). No Aquacast change or dependency upgrade is required to discover
these capabilities.

## What exists

Aquacast's engine supports strategies including `full_model`, `last_layer`, `static_only`, `lora`,
`lora_ensemble` and `svd_ensemble`. Its operational integration exposes
`model.retrain` through ForecastInterface, with model-owned `trainer` and
`finetune` configuration. Each model template must explicitly allow a strategy.
SAPPHIRE's CMAL-small wrapper allows `static_only`, `last_layer` and `lora`.
The local LoRA qualification below covers execution, not hydrological skill.

References: [operational schema at our pin](https://github.com/hydrosolutions/aquacast/blob/cea602191534c37615c069c1c5416e2c99149668/aquacast/operational/config.py),
[strategy examples](https://github.com/hydrosolutions/aquacast/blob/fc772226703940f26d29d8e62a5c39175851b2f8/docs/examples/finetune_sweep/README.md),
[feature compatibility checks](https://github.com/hydrosolutions/aquacast/blob/fc772226703940f26d29d8e62a5c39175851b2f8/aquacast/finetuning/compat.py).

## Reusable configuration

CMAL-small is the initial workflow qualification model; CMAL is the eventual
operational target. Reuse the configuration envelope and admin-review workflow
for both, while keeping donor identity and model requirements explicit.

Use [fine-tuning.toml](../../config/examples/fine-tuning.toml) as the single
submission file. Copy it for each experiment; change deployment/tenant/group,
approved donor, weather source, dates and model parameters. No configuration
inheritance engine or second training pipeline is needed. The existing configured
workflow performs training, candidate/donor evaluation and explicit admin review;
see [model onboarding](model-onboarding.md#configured-fine-tuning-issue-538).

The generic envelope is shared across models and deployments. Only
`training.parameters` follows the selected model's schema. For Aquacast:

| Research YAML setting | Operational TOML setting or owner |
|---|---|
| `seed` | `training.seed` |
| Basin files and region mappings | Assigned group and SAPPHIRE source adapters; FI inputs are already canonical |
| MeteoSwiss region YAML | `data.weather_source = "hybrid"`; record actual products/versions, including preliminary precipitation |
| `data.splits.train`, `.val` | `training.parameters.trainer.train`, `.val` (quoted date pairs) |
| Test period | Separate `data.evaluation_start/end`; do not pass external evaluation data into fitting |
| `data.batch_size` | `training.parameters.trainer.batch_size` |
| Epochs, optimizer, learning rate, weight decay | `training.parameters.trainer.max_epochs`, `.optimizer`, `.lr`, `.weight_decay` |
| Early-stopping patience | `training.parameters.trainer.early_stopping.patience`; enabled/disabled remains template-owned |
| `finetuning` | `training.parameters.finetune` |
| Architecture, windows, ordered features, loss, precision, augmentation, checkpoint selection and strides | Bound model template; not operational run-config overrides |
| Worker count | CMAL wrapper uses zero data workers; do not copy the research file's four workers |

Trainer train/validation ranges must fit within the outer fitting input range.
Validation is part of model selection, so external evaluation must follow it.
The example's 1981–2015 training, 2016 validation and 2017–2020 evaluation are
research dates, not a claim of complete data or independence from donor pretraining.
Keep dates quoted in TOML so model parameters serialize as JSON strings.
Choose a smaller measured epoch/batch budget for the first Mac qualification;
Batch size 512 / 20 epochs are Sandro's research settings, not approved host limits.

## Sandro's reference and the CMAL-small differences

[ch_lora_r8_meteoswiss.reference.yaml](../../config/examples/ch_lora_r8_meteoswiss.reference.yaml)
is the unchanged owner-supplied example from 23 November. It is a research
reference, **not a runnable SAPPHIRE deployment configuration**. Its relative data,
basin and region paths refer to the research workspace and are not bundled here.

| Setting | Supplied reference | Current CMAL-small template |
|---|---|---|
| Hidden size | 512 | 256 |
| Lookback / horizon | 210 / 15 days | 30 / 10 days |
| Future weather | Precipitation, mean temperature, thermal and solar net radiation | Precipitation and mean temperature |
| Fine-tuning | LoRA, rank 8, alpha 16, all targets | Static-only, last-layer and LoRA allowed |
| Declared early stopping / checkpoint.load | Enabled, patience 5 / best | Disabled / last |
| Training / inference stride | 1 / 1 | 7 / 3 |

Do not replace the CMAL-small template with this YAML: feature order and network
shape must match the donor, and template contents participate in artifact identity.
The reference describes a larger model; it does not prove which checkpoint Sandro
used. Aquacast's current fine-tuner reconstructs architecture from the donor bundle
and replaces the supplied model section, so inspect the actual donor metadata.
The same static-feature vocabulary does not make two donors interchangeable.
`normalization = "pretrained"` retains donor normalization while fitting on Swiss
weather; it does not mean ERA5 is being fetched as the experiment's forcing.

For a compatible, LoRA-enabled model, Sandro's block translates directly to:

```toml
[training.parameters.finetune]
strategy = "lora"
lr = 0.001
rank = 8
alpha = 16.0
lora_target = "all"
normalization = "pretrained"
```

This block is accepted by CMAL-small's strategy allowlist following the local
execution qualification below. No architecture change is implied.
If the intention is to reproduce the larger research model exactly,
onboard its matching donor and requirements through the existing model-onboarding
process instead. Do not silently substitute last-layer fitting for LoRA.

Early-stopping enablement, checkpoint.load and stride overrides cannot be expressed
by the current operational run schema. The inspected upstream FineTuner returns
best.pt; the YAML's best/last values alone do not establish which epoch's weights
are returned. Verify the actual pinned fine-tuner path before claiming checkpoint
equivalence. Faithful reproduction needs upstream agreement on unsupported
early-stopping and stride controls; retain them as explicit gaps, not ignored
TOML fields. SAPPHIRE's external hindcast issue schedule is separate
from the research inference stride and must retain actual issue/valid timestamps.

## Local LoRA execution qualification — 23 November 2026

Using Aquacast 0.1.378 at the pinned revision, FI 0.1.20 and PyTorch 2.10.0,
the deployed CMAL-small donor was copied read-only and verified against SHA-256
`84f1a4ef5099b2a9b76783413d250e9016be4d0adea99e14277b61ae2f788a26`.
A local experimental subclass enabled LoRA without changing the production wrapper
during the check. Through the public shim methods, retraining, serialization,
deserialization and a 10-day prediction succeeded. Seven weight matrices changed
(static tokenizer, LSTM and CMAL head); saved/reloaded tensors matched exactly and
all candidate tensors were finite. The donor file stayed unchanged.

The bounded run used one synthetic station, 180 daily rows, train 2010-01-01 through
2010-04-10, validation 2010-04-11 through 2010-06-29, one epoch, batch size 8,
AdamW, learning rate 0.001, weight decay 0.001, seed 42 and the rank-8/alpha-16/all
LoRA block above. Two CPU threads took about 2.54 seconds; Aquacast explicitly
fell back from bf16-mixed to float32 on CPU. Synthetic statics and weather were
test inputs only. These timing and output checks do not establish real Swiss
coverage, training cost, improved skill or evaluation independence. No database
candidate, production training job, model activation or Aquacast modification
was made. The bounded Swiss pilot and admin review remain required.
