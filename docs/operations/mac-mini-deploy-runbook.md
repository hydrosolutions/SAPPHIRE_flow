# Mac-mini deploy runbook

Current procedure for deploying an owner-published release to the mac-mini staging host. Companion to
[`../deployment/mac-mini-staging.md`](../deployment/mac-mini-staging.md) (first-time install) and
[`../standards/cicd.md`](../standards/cicd.md) (the gate + rollback contract).

The 2026-08-20 **0.1.758 → 0.1.775** commands and results below are historical evidence only.
They are not the current deploy template because release images are now consumed from a verified
receipt. Rollback anchor `sapphire-flow:rollback-backup` -> 0.1.758 may still exist on the host.

## What you are actually deploying

**Not two plans — nine PRs and ~3,900 changed lines.** The mini has been on 0.1.758 since
Plan 176; five code-bearing PRs have landed since:

| PR | What it changes at runtime |
|---|---|
| #188 Plan 186 | **Whole-graph ingest** — one LINDAS request per run instead of one per station |
| #193 Plan 189 | Audit publish-lag horizon; **collector cron 18 → 24 runs/hour**; staleness thresholds |
| #192 Plan 151 T5–T7 | **~3,000 lines of new track-resolution/assembly services.** Commit says *dormant* — VERIFY before assuming it is inert |
| #186 Plan 162 T5 | Backup restore-rehearsal fix |
| #187/#191/#185 | CI only — no runtime effect |
| #189/#196/#194 | added 2026-08-19/20: restore-rehearsal `is_called` fix, Plan 151 **T8a** (dormant), ERA5-Land multi-variable safety |

**No DB migrations** (`alembic/versions/` unchanged since v0.1.758) — so this is code-only, and
rollback does not cross a schema boundary. That is the single biggest risk removed.

**Compose changes are one line**: the collector cron. No topology change, no new service.

## Threshold changes that alter alerting behaviour

| Constant | Was | Now |
|---|---|---|
| `_STALE_MEASUREMENT_THRESHOLD` (flow) | 3 h | **30 min** |
| `BAFU_OBS_STALE_THRESHOLD` (watchdog) | 3 h | **15 min** |

Expect **faster, louder** alerting after this deploy. A stall that used to sit silent for hours
now pages in ~15 min. If an alert fires shortly after deploy, check whether it is a real stall
before assuming the deploy broke something — the alert may simply be working for the first time.

## Procedure

Use the **Release identity receipt** procedure below for every current owner-published release.
That is the only current official deploy recipe in this runbook.

The current procedure preserves the rollback anchor, both host overlays, both worker quiescence,
init/migration success before restart, both image variants, and credential boundaries. It does not
rebuild after receipt verification and it does not implicitly pull application images.

## 2026-08 historical deploy evidence (not current procedure)

The following commands were executed for the **0.1.758 → 0.1.775** deploy on 2026-08-20. They are
kept to preserve historical facts and acceptance evidence. Do not copy them for a current release:
they predate mandatory release identity inputs and the verified receipt consumption path.

    ssh sapphire@192.168.1.136
    cd ~/SAPPHIRE_flow
    export PATH=/usr/local/bin:$PATH
    export DOCKER_HOST=unix:///var/run/docker.sock

    # 0. Rollback anchor FIRST (cicd.md step 0) — images are local-only, nothing to pull back
    docker tag sapphire-flow:$(grep '^VERSION' .env | cut -d= -f2) sapphire-flow:rollback-backup

    # 1. Update + set VERSION (both, or the image tag lies about what is running)
    git pull --ff-only origin main
    sed -i "" "s/^VERSION=.*/VERSION=0.1.775/" .env

    # 2. Historical deploy command from 2026-08 only. Not current.
    export RECAP_DG_CLIENT_TOKEN=$(cat secrets/recap_dg_client_token)
    export AQUACAST_TOKEN=$(cat secrets/aquacast_token)
    docker compose -f docker-compose.yml -f docker-compose.macmini.yml up -d --build

## Acceptance — in this order

    # a. Running code is what you think (read from INSIDE the container, not the image tag)
    docker compose exec -T prefect-worker python -c "import sapphire_flow; print(sapphire_flow.__version__)"
    #    expect 0.1.775

    # b. init succeeded
    docker inspect sapphire_flow-init-1 --format "{{.State.ExitCode}}"    # expect 0
    #    init now also creates the tenants declared in config/overlays/mac-mini.toml
    #    ([tenants.chwrr], Plan 513). Before the FIRST init carrying that declaration, read the
    #    existing row: code exactly `chwrr`, name exactly `CHWRR Nepal`, no trailing whitespace.
    #    A different name (or a renamed `sapphire`, when `sapphire` is declared) makes init FAIL, which also blocks the Swiss
    #    workers, API and deployment registration. Fix by owner SQL, never by editing the declaration
    #    to match a wrong row. Init logs `tenants_declared` (count) on every run.

    # c. Collector cron picked up the 24-run list, and both deployments are on the ingest pool
    docker compose exec -T prefect-worker prefect deployment inspect \
      collect-bafu-observations/collect-bafu-observations | grep -A2 cron
    for d in ingest-observations collect-bafu-observations; do
      docker compose exec -T prefect-worker prefect deployment inspect "$d/$d" | grep work_pool_name
    done

    # d. Plan 186's whole-graph ingest: ONE request per run, stations_polled unchanged
    docker compose logs --since 10m prefect-worker-ingest | grep -E "whole_graph_fetch_completed|ingest.starting"

    # e. Plan 189's audit — window ending 30+ MIN IN THE PAST, or trailing slots read as skipped
    docker compose exec -T prefect-worker python -m sapphire_flow.cli.bafu_observation_audit \
      --base-path /data/bafu_observations \
      --start "$(date -u -v-2H +%Y-%m-%dT%H:00:00Z)" --end "$(date -u -v-1H +%Y-%m-%dT%H:00:00Z)" \
      | grep -E "complete|skipped"
    #    ⚠️ THE `Z` IS REQUIRED. A naive timestamp dies with
    #    `ValueError: Naive datetime not allowed` before the audit runs.
    #    expect ~6 slots/hour present, 0 missing, and a skipped_too_recent count of 0 for a past window

    # f. Plan 151 T5-T7 is genuinely dormant — no new flow runs, no new deployments
    docker compose exec -T prefect-worker prefect deployment ls | wc -l   # compare to before

## Rollback (code-only — no schema step)

    docker compose stop prefect-worker prefect-worker-ingest
    sed -i "" "s/^VERSION=.*/VERSION=0.1.758/" .env      # or the rollback-backup tag from step 0
    docker compose -f docker-compose.yml -f docker-compose.macmini.yml up -d
    docker compose run --rm init                          # re-register deployments at the old cron
    # Verify BOTH deployments' pools afterwards — a deployment left on a workerless pool is
    # silently dead and only surfaces later as a stale-heartbeat alert (cicd.md § rollback).

## Known traps, all previously hit on this host

- **Docker engine cannot be started over SSH.** It needs the mini's GUI session. If `docker` is
  unreachable, that is the problem — check `curl -m8 http://192.168.1.136:8000/api/v1/health` first,
  which needs no socket.
- **Both overlays, every time.** A plain `docker compose up` silently drops the mac-mini overlay
  and with it NWP, both BAFU collectors, and the API port binding.
- **`.env` VERSION must be set or the image tag lies.** It sat at 0.1.710 while 0.1.753 ran.
- **`docker compose ps` reports the tag, not the running code.** Always read `__version__` from
  inside the container.


## Release identity receipt

Before any owner-published release image is labelled or built, verify the release source and context first. The operator must not type an arbitrary version/SHA pair and then call `docker compose build` as release evidence.

Required pre-build checks:

1. Start from a clean reviewed `main` checkout at the owner-approved full source SHA.
2. Fetch the canonical remote and verify that `origin/main`, the annotated `v<version>` tag target, and `refs/heads/release-state` all agree with that source and version.
3. Verify the helper's fixed frozen legacy ceiling against the surviving helper-era tag metadata. If the state ref or release tag is missing, stop and escalate. Do not fall back to an older tag.
4. Before first owner bootstrap after retiring the old publisher, verify the read-only numeric GitHub workflow endpoint for the former `.github/workflows/tag-main.yml` workflow and complete unfiltered run pagination. Pre-retirement samples are not quiescence evidence.
5. Verify the expected latest release externally from the owner-approved release notes or coordination channel. The helper can prove consistency of the refs it sees; it cannot prove deleted helper-era evidence or a different expected latest if that evidence no longer exists.
6. Build only from the immutable clean context prepared by the helper receipt path.

For auditable local artifact proof, run the receipt command before deployment:

```bash
uv run python3 tools/release_identity.py build-receipt \
  --version <version> \
  --source <full-sha> \
  --receipt <path>
```

The command verifies the canonical remote, clean source tree, release-state, annotated release tag object, Docker labels, immutable local image IDs, and runtime `sapphire_flow.__version__`. It writes a credential-free JSON receipt and refuses to overwrite an existing receipt. This receipt is not a registry digest and does not deploy anything.

If the command reports that `main`, `release-state`, the frozen ceiling, or the annotated release tag moved, stop. Do not reuse local images as release evidence. Fetch the new remote state, confirm the owner-published source and expected latest release again, and rerun the receipt command with a new receipt path after the release owner resolves the race. There is no automated recovery command. Missing or inconsistent release-state/tag evidence is a refusal, not permission to delete refs or publish from an older tag.

After the receipt passes for an owner-published canonical release, deploy only the already-proved local images. Do not rebuild or pull in the official consumption path: a later build can overwrite the mutable tags with image IDs that the receipt did not prove.

```bash
set -euo pipefail
export PATH=/usr/local/bin:$PATH
export DOCKER_HOST=unix:///var/run/docker.sock
export SAPPHIRE_PYTHON="${SAPPHIRE_PYTHON:-$(pwd)/.venv/bin/python}"
if [ "${SAPPHIRE_PYTHON#/}" = "${SAPPHIRE_PYTHON}" ] || [ ! -x "${SAPPHIRE_PYTHON}" ]; then
  echo "SAPPHIRE_PYTHON must be an executable absolute path to Python >=3.12" >&2
  echo "Run 'uv sync' during provisioning or set SAPPHIRE_PYTHON persistently." >&2
  exit 1
fi
"${SAPPHIRE_PYTHON}" - <<'PY'
from __future__ import annotations

import sys

if sys.version_info < (3, 12):
    raise SystemExit(
        "SAPPHIRE_PYTHON must be Python >=3.12; "
        f"got {sys.version_info.major}.{sys.version_info.minor}"
    )
PY
export SAPPHIRE_RELEASE_VERSION=<version>
export VERSION="$SAPPHIRE_RELEASE_VERSION"
export SAPPHIRE_SOURCE_REVISION=<full-sha>
export RELEASE_RECEIPT=<path>
uv run python3 tools/compose_release_identity.py
"${SAPPHIRE_PYTHON}" - <<'PY'
import json
import os
import subprocess
from pathlib import Path

receipt = json.loads(Path(os.environ["RELEASE_RECEIPT"]).read_text())
version = os.environ["VERSION"]
release_version = os.environ["SAPPHIRE_RELEASE_VERSION"]
source_revision = os.environ["SAPPHIRE_SOURCE_REVISION"]
if receipt.get("package_version") != version or release_version != version:
    raise SystemExit("receipt package_version does not match requested VERSION")
if receipt.get("source_revision") != source_revision:
    raise SystemExit("receipt source_revision does not match requested source")
images = receipt.get("images")
if not isinstance(images, list) or len(images) != 2:
    raise SystemExit("receipt images must contain exactly two entries")
by_variant = {}
for item in images:
    if not isinstance(item, dict):
        raise SystemExit("receipt image entry is malformed")
    variant = item.get("variant")
    if variant not in {"default", "aquacast"} or variant in by_variant:
        raise SystemExit("receipt image variants must be unique default/aquacast")
    by_variant[variant] = item
if set(by_variant) != {"default", "aquacast"}:
    raise SystemExit("receipt must contain exactly default and aquacast variants")
expected = {
    f"sapphire-flow:{version}": by_variant["default"].get("image_id"),
    f"sapphire-flow-aquacast:{version}": by_variant["aquacast"].get("image_id"),
}
receipt_tags = {item.get("mutable_tag") for item in by_variant.values()}
if receipt_tags != set(expected):
    raise SystemExit("receipt image tags do not match requested VERSION")
expected_service_images = {
    "api": f"sapphire-flow:{version}",
    "init": f"sapphire-flow:{version}",
    "prefect-worker-backup": f"sapphire-flow:{version}",
    "prefect-worker-ingest": f"sapphire-flow:{version}",
    "prefect-worker": f"sapphire-flow-aquacast:{version}",
}
config_raw = subprocess.check_output(
    [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "docker-compose.macmini.yml",
        "config",
        "--format",
        "json",
    ],
    text=True,
)
config = json.loads(config_raw)
services = config.get("services")
if not isinstance(services, dict):
    raise SystemExit("rendered Compose config is missing services")
selected = {}
for name, expected_image in expected_service_images.items():
    service = services.get(name)
    if not isinstance(service, dict):
        raise SystemExit(f"rendered Compose config is missing service {name}")
    image = service.get("image")
    if image != expected_image:
        raise SystemExit(
            f"rendered Compose service {name} image {image!r} "
            f"does not match expected {expected_image!r}"
        )
    selected[name] = image
for tag, expected_id in expected.items():
    if not isinstance(expected_id, str) or not expected_id.startswith("sha256:"):
        raise SystemExit(f"receipt image ID for {tag} is malformed")
    actual_id = subprocess.check_output(
        ["docker", "image", "inspect", tag, "--format", "{{.Id}}"],
        text=True,
    ).strip()
    if actual_id != expected_id:
        raise SystemExit(
            f"{tag} image ID {actual_id} does not match receipt {expected_id}"
        )
PY
# Identity verification above gates every mutating command below, including rollback-anchor preparation.
# Preserve rollback anchors and the previous public identity before changing .env.
if [ -z "${ROLLBACK_DIR:-}" ]; then
  export ROLLBACK_DIR="$HOME/sapphire-release-rollback/$SAPPHIRE_RELEASE_VERSION"
else
  export ROLLBACK_DIR
fi
"${SAPPHIRE_PYTHON}" - <<'PY'
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from tools.release_env_identity import (
    IDENTITY_KEYS,
    DotenvIdentityError,
    public_identity_snapshot,
    validate_identity_snapshot,
    write_identity_atomic,
    write_json_once,
)

checkout = Path.cwd().resolve()
rollback_dir = Path(os.environ["ROLLBACK_DIR"]).expanduser().resolve()
if rollback_dir == checkout or checkout in rollback_dir.parents:
    raise SystemExit(f"rollback directory must be outside checkout: {rollback_dir}")
rollback_dir.mkdir(parents=True, exist_ok=True)


def write_text_once(path: Path, content: str) -> None:
    if path.exists():
        if path.read_text() != content:
            raise SystemExit(f"existing rollback evidence differs: {path}")
        return
    with path.open("x") as stream:
        stream.write(content)


def verify_config_from_persisted_env(version: str) -> None:
    env = {key: value for key, value in os.environ.items() if key not in IDENTITY_KEYS}
    config_raw = subprocess.check_output(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.macmini.yml",
            "config",
            "--format",
            "json",
        ],
        text=True,
        env=env,
    )
    config = json.loads(config_raw)
    expected = {
        "api": f"sapphire-flow:{version}",
        "init": f"sapphire-flow:{version}",
        "prefect-worker-backup": f"sapphire-flow:{version}",
        "prefect-worker-ingest": f"sapphire-flow:{version}",
        "prefect-worker": f"sapphire-flow-aquacast:{version}",
    }
    for service, image in expected.items():
        actual = config.get("services", {}).get(service, {}).get("image")
        if actual != image:
            raise SystemExit(f"fresh .env Compose verification failed for {service}")


def inspect_image(tag: str) -> str | None:
    existing = subprocess.run(
        ["docker", "image", "inspect", tag, "--format", "{{.Id}}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if existing.returncode == 0:
        return existing.stdout.strip()
    absence = subprocess.run(
        ["docker", "image", "ls", "--quiet", "--no-trunc", tag],
        text=True,
        capture_output=True,
        check=False,
    )
    if absence.returncode != 0:
        raise SystemExit(f"cannot prove rollback tag {tag} is absent")
    if absence.stdout.strip():
        raise SystemExit(f"cannot inspect existing rollback tag {tag}")
    return None

incoming_identity = {key: os.environ[key] for key in IDENTITY_KEYS}
try:
    validate_identity_snapshot(
        {key: {"present": True, "value": value} for key, value in incoming_identity.items()}
    )
except DotenvIdentityError as exc:
    raise SystemExit(str(exc)) from exc

env_path = checkout / ".env"
prior_identity_path = rollback_dir / "prior-public-identity.json"
if prior_identity_path.exists():
    prior_identity = json.loads(prior_identity_path.read_text())
    validate_identity_snapshot(prior_identity) if all(
        item.get("present") is True for item in prior_identity.values() if isinstance(item, dict)
    ) else None
else:
    try:
        prior_identity = public_identity_snapshot(env_path.read_bytes() if env_path.exists() else b"")
    except DotenvIdentityError as exc:
        raise SystemExit(str(exc)) from exc
    write_json_once(prior_identity_path, prior_identity)
write_json_once(
    rollback_dir / "incoming-public-identity.json",
    {key: {"present": True, "value": value} for key, value in incoming_identity.items()},
)

prior_receipt = os.environ.get("PRIOR_RELEASE_RECEIPT")
receipt_path = rollback_dir / "prior-release-receipt.json"
absence_path = rollback_dir / "prior-release-receipt.absent"
if prior_receipt and Path(prior_receipt).is_file():
    if absence_path.exists():
        raise SystemExit("prior receipt supplied but absence marker already exists")
    write_text_once(receipt_path, Path(prior_receipt).read_text())
else:
    if receipt_path.exists():
        raise SystemExit("prior receipt already exists but no PRIOR_RELEASE_RECEIPT was supplied")
    write_text_once(
        absence_path,
        "No PRIOR_RELEASE_RECEIPT was provided or present; this may be the first legacy cutover.\n",
    )

services = {"api": "default", "prefect-worker": "aquacast"}
anchors = {}
for service, variant in services.items():
    container_id = subprocess.check_output(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.macmini.yml",
            "ps",
            "-q",
            service,
        ],
        text=True,
    ).strip()
    if not container_id:
        raise SystemExit(f"cannot prepare rollback: service {service} is not running")
    image_id = subprocess.check_output(
        ["docker", "inspect", container_id, "--format", "{{.Image}}"],
        text=True,
    ).strip()
    if not image_id.startswith("sha256:"):
        raise SystemExit(
            f"cannot prepare rollback: service {service} image ID {image_id!r} is malformed"
        )
    repository = "sapphire-flow" if variant == "default" else "sapphire-flow-aquacast"
    tag = f"{repository}:rollback-{variant}-pre-{os.environ['SAPPHIRE_RELEASE_VERSION']}"
    anchors[variant] = {"service": service, "image_id": image_id, "tag": tag}

anchors_path = rollback_dir / "rollback-anchors.json"
anchors_text = json.dumps(anchors, indent=2, sort_keys=True) + "\n"
anchors_exist = anchors_path.exists()
if anchors_exist and json.loads(anchors_path.read_text()) != anchors:
    raise SystemExit(f"existing rollback anchors differ: {anchors_path}")
for item in anchors.values():
    verified = inspect_image(item["tag"])
    if verified is None:
        if anchors_exist:
            raise SystemExit(f"rollback tag {item['tag']} is absent despite existing anchors")
        subprocess.check_call(["docker", "tag", item["image_id"], item["tag"]])
        verified = subprocess.check_output(
            ["docker", "image", "inspect", item["tag"], "--format", "{{.Id}}"],
            text=True,
        ).strip()
    if verified != item["image_id"]:
        raise SystemExit(
            f"rollback tag {item['tag']} points to {verified}, expected {item['image_id']}"
        )
if not anchors_exist:
    with anchors_path.open("x") as stream:
        stream.write(anchors_text)

try:
    write_identity_atomic(env_path, incoming_identity)
    persisted = validate_identity_snapshot(public_identity_snapshot(env_path.read_bytes()))
except DotenvIdentityError as exc:
    raise SystemExit(str(exc)) from exc
if persisted != incoming_identity:
    raise SystemExit("persisted .env release identity differs from incoming receipt identity")
verify_config_from_persisted_env(persisted["VERSION"])

PY
# Keep BOTH v0 workers quiesced before init/migration reruns.
docker compose -f docker-compose.yml -f docker-compose.macmini.yml stop prefect-worker prefect-worker-ingest
docker compose -f docker-compose.yml -f docker-compose.macmini.yml run --rm --build=false --pull never init
docker compose -f docker-compose.yml -f docker-compose.macmini.yml up -d --no-build --pull never
```

This binds the requested version/source, the rendered Compose application image selection, and the selected local daemon image IDs to the receipt before deployment. It then records the prior public `.env` identity, the incoming public identity, the prior running default/Aquacast image IDs and local rollback tags before stopping workers, and only then atomically persists the verified incoming identity to `.env`. It assumes the operator controls and trusts the local Docker daemon between proof and deploy; if any incoming tag is missing, changed, pulled, rebuilt, or otherwise has a different image ID, stop before rollback preparation and create a fresh verified receipt before deployment. If rollback preparation fails, stop before quiescing workers. The two worker containers are stopped only after identity verification and rollback preparation pass; `init` must succeed before `up -d` restarts services. Owner approval is still required for deployment. Existing schema rollback restrictions and T1d downgrade rules in `docs/standards/cicd.md` still apply; these local anchors do not authorize unsafe database downgrade or raw-lineage access changes.

For developer builds whose package version contains a PEP 440 local segment such as `+g<sha>`, use a separate Docker-safe `VERSION` tag and keep `SAPPHIRE_RELEASE_VERSION` as the runtime package identity. Developer builds are not release evidence.

`.env` identity persistence intentionally supports only a small dotenv grammar: blank lines, comments, simple `KEY=value` bindings, and double-quoted multiline non-identity values. It does not source shell syntax. Unsupported noncomment records such as `export KEY=value`, single-quoted multiline values, unterminated quotes, duplicate public identity keys, or quoted public identity keys fail closed before rollback evidence or `.env` mutation. Keep secrets in supported Compose-compatible forms or update this parser with native Compose-backed tests before relying on a new form.

Host helper snippets require Python >=3.12. By default, launchd/bootstrap/runbook commands use the managed interpreter at `${REPO_ROOT}/.venv/bin/python` (or `$(pwd)/.venv/bin/python` in the copy-paste runbook). Provision it during setup with `uv sync`; launchd startup will only verify and use it, never install or download dependencies. If operators need a different interpreter, set `SAPPHIRE_PYTHON` to an executable absolute path persistently in the launchd wrapper environment or the operator shell before running the receipt procedure. Do not rely on launchd's minimal `PATH` selecting a usable `python3`.
