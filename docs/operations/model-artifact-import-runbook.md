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
        "imported_by": "<operator>",
        "operator": "<operator>",
        "notes": "<anything the record should carry — e.g. a training-cut convention we do not serve>",
    },
    timeout=600,
)
print(fr.id, fr.state.type if fr.state else None)
PY
```

⚠️ **The `-i` is required.** `docker exec` does not attach stdin by default, so without it the
heredoc never reaches Python, nothing is submitted, and the command exits silently as though it
had worked.

`artifact_path` may be given relative to the staging root (`<name>.pt`) or absolute
(`/data/incoming/<name>.pt`). ⛔ **It must name a file directly in the staging root** —
subdirectories are refused, because containment cannot be guaranteed across an intermediate
directory that a host writer can move mid-import.

### 4. Verify what was written

```sql
SELECT a.id, m.id AS model, a.group_id, a.status, a.trained_at, a.imported_at
FROM model_artifacts a JOIN models m ON m.id = a.model_id
WHERE m.id = '<model>' ORDER BY a.imported_at DESC LIMIT 1;
```

Check that the three timestamps — `trained_at`, the training period, and `imported_at` — are
**distinct and un-conflated**. They describe three different events and none is derived from
another.

## Why an import is refused

| refusal | meaning |
|---|---|
| `give artifact_base64 OR artifact_path, not both` | pick one source |
| `one of artifact_base64 or artifact_path is required` | neither was given |
| `expected_artifact_sha256 is required when artifact_path is used` | the staging directory is host-writable; an unverified read is not an import we can vouch for |
| `outside the staging root` / `plain path inside the staging root` | the path escapes the mount |
| `without following a symlink` | a component of the path is a symlink. Containment is enforced by the open itself, so this fires **before the file is read** |
| `content does not match expected_artifact_sha256` | the staged bytes are not the ones you verified. **Nothing is written** |
| `must name a file directly inside the staging root` | the path has a directory component, or escapes with `..` |
| `is not a regular file` | the staged path is a FIFO, device or directory. A FIFO would otherwise block the import forever |
| `staging root ... is not available as a real directory` | this deployment's overlay does not bind it, or it is a symlink — see cicd.md § Required mounts |
| `config/artifact mismatch` | `expected_config_hash` disagrees with the model's declared hash. Refused **before any write** |

## Known limits, stated rather than implied

- **A hardlink inside the staging root to a file outside it will be read.** `O_NOFOLLOW` cannot
  distinguish a hardlink from an ordinary file; only a filesystem boundary could. The required
  checksum is what keeps such a read from becoming an import.
- **The staging root path is trusted configuration.** Anyone who can change the deployment's
  environment, or write to the root's parent directory, controls the deployment already.

⚠️ **Parameter size.** The older `artifact_base64` route still works, but Prefect refuses flow-run
parameters above **524,288 bytes** serialized — about 390 KB of artifact. Anything larger must go
by path; that ceiling is what Plan 307 exists to route around.
