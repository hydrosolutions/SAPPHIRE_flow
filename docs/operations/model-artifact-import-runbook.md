# Importing a model artifact — operator runbook

The procedure for importing an externally-trained model artifact into a SAPPHIRE Flow deployment.
**Identical on every host**; only the staging directory's host path differs. Written for Plan 307;
the mac mini and a Nepali deployment follow the same steps.

Companion: [`../standards/cicd.md`](../standards/cicd.md) § Required mounts — artifact staging.

## Before you start

**You need the staging mount.** A deployment whose overlay does not bind a read-only artifact
staging directory cannot import by path. Confirm it, composing **both** files:

```bash
docker compose -f docker-compose.yml -f docker-compose.macmini.yml config \
  | grep -A2 '/data/incoming'
```

**You need six provenance values, and three of them are easy to get wrong.** Every one must
describe the *real external training*; they are written into an immutable record.

| value | where it comes from | trap |
|---|---|---|
| `trained_at` | the training-completion line in the trainer's log | **Not** the bundle's `created_at` (stamped at the best checkpoint, which may be many epochs earlier) and **not** the file mtime. Three different numbers. ⚠️ The log's wall clock carries no offset — confirm the training machine's timezone and convert to UTC. |
| `training_period_start` / `_end` | the training config's splits, **including per-region overrides** | A config's global split is often a fallback that regional overrides extend. ⚠️ **Must be timezone-aware** — a date-only string is rejected (`ensure_utc`: *Naive datetime not allowed*). |
| `expected_config_hash` | **computed at import time** from the model config in the owner's tree | ⛔ Never copy the constant vendored into this repo — that compares the repo file against itself, a check that can never fail. |
| `expected_artifact_sha256` | `shasum -a 256` of the artifact file | Different from `expected_config_hash`: one digests the checkpoint bytes, the other the model's config. Never derive one from the other. |
| `source_commit` | the training checkout revision | Leave **null** if it cannot be recovered. The package version recorded in the bundle is not necessarily the training source. |
| `group_id` / `station_id` | the target group or station | Exactly one. |

## Procedure

### 1. Stage the artifact on the host

```bash
scp <artifact> sapphire@<host>:/Users/sapphire/sapphire-incoming/<name>.pt
ssh sapphire@<host> 'shasum -a 256 /Users/sapphire/sapphire-incoming/<name>.pt'
```

⚠️ **Compare that digest against the source file before going further.** It is the only check that
the transfer was clean, and it is the value you pass as `expected_artifact_sha256`.

⛔ **Do not `docker cp` into the container.** The rootfs is read-only and the copy is refused.
Staging happens on the host, in the bound directory.

### 2. Compute the config hash from the owner's tree

```bash
shasum -a 256 <owner-model-tree>/config.yaml
```

Compare it to the digest the plan records. **A mismatch stops the import** — it means either the
tree moved or this repo's vendored copy no longer matches it, and both are findings.

### 3. Run the import

```bash
docker exec -i <worker> /entrypoint.sh python - <<'PY'
from prefect.deployments import run_deployment
fr = run_deployment(
    name="import-model-artifact/import-model-artifact",
    parameters={
        "model_id": "<model>",
        "artifact_path": "/data/incoming/<name>.pt",
        "expected_artifact_sha256": "<digest from step 1>",
        "trained_at": "<UTC ISO-8601 with offset>",
        "training_period_start": "<UTC ISO-8601 with offset>",
        "training_period_end": "<UTC ISO-8601 with offset>",
        "expected_config_hash": "<digest from step 2>",
        "group_id": "<uuid>",
        # Recoverable provenance. Pass the real values; use None ONLY when the
        # value genuinely cannot be recovered, never as a shortcut.
        "source_repository": "<repo, or None>",
        "source_commit": "<training checkout revision, or None>",
        "imported_by": "<operator>",
        "operator": "<operator>",
        "notes": "<anything the record should carry — e.g. a training-cut convention we do not serve>",
    },
    timeout=600,
)
print("flow run:", fr.id, "state:", fr.state.type if fr.state else None)
PY
```

🔴 **That prints the FLOW RUN id, not the artifact id.** `run_deployment` returns a `FlowRun`; the
artifact id is the flow's *return value*. The call also returns when the timeout expires, which is
not the same as the run having completed — **require a completed state before verifying anything**,
because a timed-out run may still be writing. *(Clean-room review 2026-09-21: step 4 previously
told you to paste this value into a query against the artifact table, where it matches nothing.)*

⚠️ **The `-i` is required.** `docker exec` does not attach stdin by default, so without it the
heredoc never reaches Python, nothing is submitted, and the command exits silently as though it
had worked.

⚠️ **`source_commit` and `source_repository` default to `None` if you omit them**, and that null
is written to the immutable record. *(Final review 2026-09-21: the table above told you to recover
`source_commit` and leave it null only when unrecoverable, while this command never passed it — so
following the procedure verbatim recorded null every time.)* Pass them, or pass `None`
deliberately.

`artifact_path` may be given relative to the staging root (`<name>.pt`) or absolute
(`/data/incoming/<name>.pt`). ⛔ **It must name a file directly in the staging root** —
subdirectories are refused, because containment cannot be guaranteed across an intermediate
directory that a host writer can move mid-import.

### 4. Verify what was written

Look the row up by the provenance **you supplied**. ⛔ Not "the most recent row", and not the
flow-run id.

⚠️ **This is a candidate lookup, not a unique key.** *(Confirming review 2026-09-21 corrected an
earlier claim that it "uniquely identifies" the import.)* These values describe the **training**,
not a particular import execution — re-importing the same artifact creates a **new** row with a
fresh id and identical provenance. So the row count is itself part of the check.

```sql
SELECT a.id, a.model_id, a.station_id, a.group_id, a.status,
       a.trained_at, a.training_period_start, a.training_period_end, a.imported_at
FROM model_artifacts a
WHERE a.model_id = '<model>'
  AND a.group_id = '<group uuid>'          -- or a.station_id for a station import
  AND a.trained_at = '<the trained_at you supplied>';
```

Check that:

- **how many rows come back.** One, and `imported_at` matches this run — that is your artifact.
  More than one means this artifact has been imported before, so check `imported_at` and `status`
  to tell yours from the earlier ones, and satisfy yourself the older rows are meant to be there.
  None means the import did not write, whatever the flow run appeared to do;
- the **scope** is the station or group you intended, and the other is null;
- `trained_at` and both **training-period bounds** equal what you supplied, to the second — a date
  that was silently reinterpreted shows up right here;
- `imported_at` is the time of this run.

⚠️ **Do not check that the four timestamps differ from each other.** They record different events,
but nothing stops two of them coinciding — a training run finishing on the last day of its own
training period is ordinary. Compare each against what you supplied. *(Clean-room review
2026-09-21: an earlier version demanded they be "distinct", which would send an operator chasing a
non-problem.)*

## Why an import is refused

| refusal | meaning |
|---|---|
| `give artifact_base64 OR artifact_path, not both` | pick one source |
| `one of artifact_base64 or artifact_path is required` | neither was given |
| `expected_artifact_sha256 is required when artifact_path is used` | the staging directory is host-writable; an unverified read is not an import we can vouch for |
| `expected_artifact_sha256 applies to artifact_path only` | you passed a digest with the **base64** route, where the bytes are already in the parameter and a digest protects nothing. It is refused rather than ignored, so you are not quietly told you asked for verification when you did not |
| `artifact_base64 is not valid base64` | the base64 route received something that is not valid base64. Decoding is strict — invalid characters are rejected, never silently discarded |
| `outside the staging root` | the absolute path you gave does not lie inside the staging root |
| `model ... is not discoverable` | the model's entry point is not installed in this worker's environment. Nothing to do with the artifact — check the image, not the file |
| `could not be opened ... without following a symlink` | the guarded open failed. A symlink is what it exists to stop, but the same message also covers **a missing file, a permissions failure, or a file replaced mid-import**. Check the staged file exists and is readable before assuming an attack. *(Clean-room review 2026-09-21: this row previously diagnosed every such failure as a symlink.)* |
| `content does not match expected_artifact_sha256` | the staged bytes are not the ones you verified. **Nothing is written.** The digest read is deliberately not reported — see Known limits |
| `must name a file directly inside the staging root` | the path does not name **exactly one file** directly inside the staging root. An **empty path** and a path with a **directory component** both land here — subdirectories are not supported, because containment cannot be guaranteed across an intermediate directory a host writer can move |
| `contains '..'` | the path uses **traversal**, in either the relative or the absolute form. Checked before any normalisation, so both spellings of the same path answer alike *(confirming review 2026-09-21: the row above previously claimed this case, which now fails earlier with a different message)* |
| `is not a regular file` | the staged path is a FIFO, device or directory. A FIFO would otherwise block the import forever |
| `staging root ... is not available as a real directory` | the root open failed. Usually this deployment's overlay does not bind it, or it is a symlink — but the same handler also catches **permissions failures and any other `OSError`**. Check the mount before assuming either. *(Confirming review 2026-09-21: the twin of the artifact-open row above, corrected one row and not the other.)* |
| `config/artifact mismatch` | `expected_config_hash` disagrees with the model's declared hash. Refused **before any write** |

## Known limits, stated rather than implied

- **A hardlink inside the staging root to a file outside it will be read.** `O_NOFOLLOW` cannot
  distinguish a hardlink from an ordinary file.
  ⚠️ **What the checksum does:** it stops **different bytes** being substituted for the ones you
  verified, because the digest comes from you, computed from the source artifact off-host. It does
  **not** mean a hardlink "cannot be imported" — a hardlink whose content *is* the intended
  artifact imports normally, and harmlessly. *(Confirming review 2026-09-21 caught this section
  claiming the stronger property.)* The residual is a **hash oracle**, which is why the import no
  longer reports the digest it read on a mismatch. Recompute it from the staged file if you need
  it for debugging.
  🔑 **This answer depends on who can write to the staging directory.** Where that is the same
  account that owns the compose files, the secrets and the deploy — as on the mac mini — a
  hardlink grants nothing that account does not already have. **A deployment that lets a LESS
  privileged party stage artifacts must first give the staging root its own filesystem**, so that
  a hardlink to anything outside it is impossible. Decide that before accepting the first
  artifact from such a party, not after.
- **The staging root, its ancestors and the mount topology beneath them are trusted
  configuration.** That is a deployment assumption this procedure relies on, not something the
  import can verify. A deployment where an untrusted party can write to an ancestor of the
  staging root does not satisfy it.

⚠️ **Parameter size.** The older `artifact_base64` route still works, but Prefect refuses flow-run
parameters above **524,288 bytes** serialized — about 390 KB of artifact. Anything larger must go
by path; that ceiling is what Plan 307 exists to route around.
