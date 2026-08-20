#!/bin/bash
# prune-docker.sh — weekly host-level Docker image and build-cache prune.
#
# Plan 105 D3: Docker image/build-cache accumulation from version-bumped
# deploys fills the host disk silently. This script is the host-level
# (not container-level) solution; running `docker image prune -a` from inside
# a Prefect worker would require mounting the Docker socket — a security
# no-go (container escape surface, violates docs/standards/security.md).
#
# Invoked weekly by launchd (ch.hydrosolutions.sapphire-docker-prune.plist).
# Never run automatically from inside any container.
#
# Stack-up guard: this script removes ALL images not referenced by a
# container, matching `docker image prune -a` semantics. Removing only
# dangling/untagged images would reclaim nothing from old tagged
# `sapphire-flow:0.1.xxx` images — the primary ~15 GB offender. Tags matching
# PROTECT_RE (rollback anchors) are the one exception; see
# `prune_unreferenced_images` below for why that cannot be a docker filter.
# Protection: the running `sapphire-flow:${VERSION}`
# image and its base images remain referenced while the stack is up. If the
# stack is DOWN (docker compose down during maintenance), every image
# including the current version would be removed; `docker compose up -d` alone
# (without --build) would then fail or force an unexpected rebuild. This script
# therefore SKIPS the prune if the stack is not detected as running.
# Operators should always use `docker compose up -d --build` after a version
# upgrade so a pruned image is rebuilt rather than assumed cached.

set -euo pipefail

# Explicit PATH: launchd runs with a minimal environment; docker may not be on
# the default PATH. Mirrors the convention in start-sapphire.sh.
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH}"

# DOCKER_CMD may be set in tests to point at a fake docker stub, bypassing the
# PATH-based resolution (which the script's own `export PATH=...` reorders).
DOCKER="${DOCKER_CMD:-docker}"

log() { printf '[prune-docker] %s\n' "$1"; }

# --- Stack-up guard ---
# Use plain `docker ps` (no Compose project needed; containers are named
# sapphire_flow-*). The `2>/dev/null` swallows stderr so Docker-daemon-down
# errors don't print to the log. The leading `!` makes a non-zero docker-ps
# exit (daemon unreachable) fall into the "not running" branch WITHOUT
# tripping `set -euo pipefail` (a tested `if !` branch is not an uncaught
# failure). Guard-command errors default to SKIP — never prune when the
# running state is unknown.
if ! "${DOCKER}" ps --format '{{.Names}}' 2>/dev/null | grep -Eq '^sapphire_flow-'; then
    log "stack not running or daemon unreachable — skipping prune"
    exit 0
fi

log "sapphire stack is running — checking reclaimable space"

# --- Parse reclaimable space per Type using {{json .}} ---
# docker system df --format '{{.ReclaimableSize}}' exits non-zero (code 1) on
# Docker-Desktop-for-Mac — invalid field. Use `{{json .}}` (one JSON object
# per row) and parse with python3 (host system Python; uv is not guaranteed on
# PATH in the launchd minimal environment).
IMAGES_GB=$("${DOCKER}" system df --format '{{json .}}' | python3 -c "
import sys, json
total = 0.0
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        continue
    if row.get('Type') != 'Images':
        continue
    raw = row.get('Reclaimable', '0B')
    # Strip trailing ' (xx%)' if present, then parse the unit.
    raw = raw.split(' (')[0].strip()
    if raw.endswith('GB'):
        total = float(raw[:-2])
    elif raw.endswith('MB'):
        total = float(raw[:-2]) / 1024.0
    elif raw.endswith('kB') or raw.endswith('KB'):
        total = float(raw[:-2]) / (1024.0 * 1024.0)
    elif raw.endswith('B'):
        total = float(raw[:-1]) / (1024.0 ** 3)
print(total)
" 2>/dev/null || echo "0")

CACHE_GB=$("${DOCKER}" system df --format '{{json .}}' | python3 -c "
import sys, json
total = 0.0
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        continue
    if row.get('Type') != 'Build Cache':
        continue
    raw = row.get('Reclaimable', '0B')
    raw = raw.split(' (')[0].strip()
    if raw.endswith('GB'):
        total = float(raw[:-2])
    elif raw.endswith('MB'):
        total = float(raw[:-2]) / 1024.0
    elif raw.endswith('kB') or raw.endswith('KB'):
        total = float(raw[:-2]) / (1024.0 * 1024.0)
    elif raw.endswith('B'):
        total = float(raw[:-1]) / (1024.0 ** 3)
print(total)
" 2>/dev/null || echo "0")

log "images reclaimable: ${IMAGES_GB} GB  |  build-cache reclaimable: ${CACHE_GB} GB"

# Gate each prune independently on ≥ 1 GB reclaimable.
# Images attached to NO container (running or exited) are removed, including
# old tagged `sapphire-flow:0.1.xxx` images. The deployed stack's images are
# protected because their containers reference them.
PRUNE_THRESHOLD=1

# Rollback anchors must survive the prune. They are unreferenced by design —
# no container runs them — and are the only way to reproduce a prior version
# without a rebuild. `docker image prune -a` cannot express this: its only
# filters are `until=` and `label=`, neither of which matches a tag name. So
# the candidate set is enumerated here and protected tags are skipped.
# Overridable so tests exercise the protection without depending on the
# production naming convention.
PROTECT_RE="${PRUNE_PROTECT_RE:-:rollback}"

prune_unreferenced_images() {
    local in_use candidates img removed=0 protected=0
    # `ps -a`, not `ps`: an exited container still pins its image, which is
    # what `docker image prune -a` itself honours. Using `ps` here would
    # delete the image out from under a stopped container.
    in_use=$("${DOCKER}" ps -a --format '{{.Image}}' 2>/dev/null | sort -u || true)
    candidates=$("${DOCKER}" images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null \
        | grep -v '^<none>:<none>$' | sort -u || true)

    while IFS= read -r img; do
        [ -z "${img}" ] && continue
        if printf '%s\n' "${in_use}" | grep -qxF "${img}"; then
            continue
        fi
        if printf '%s' "${img}" | grep -q "${PROTECT_RE}"; then
            log "protected, not pruned: ${img}"
            protected=$((protected + 1))
            continue
        fi
        if "${DOCKER}" rmi "${img}" >/dev/null 2>&1; then
            log "removed ${img}"
            removed=$((removed + 1))
        else
            log "could not remove ${img} — leaving in place"
        fi
    done <<< "${candidates}"

    # Untagged/dangling layers left behind carry no rollback value and are
    # safe to reclaim wholesale.
    "${DOCKER}" image prune -f >/dev/null 2>&1 || true
    log "image prune complete — ${removed} removed, ${protected} protected"
}

if python3 -c "import sys; sys.exit(0 if float('${IMAGES_GB}') >= ${PRUNE_THRESHOLD} else 1)"; then
    log "pruning images (${IMAGES_GB} GB reclaimable >= ${PRUNE_THRESHOLD} GB threshold)"
    prune_unreferenced_images
else
    log "images reclaimable ${IMAGES_GB} GB < ${PRUNE_THRESHOLD} GB — skipping image prune"
fi

if python3 -c "import sys; sys.exit(0 if float('${CACHE_GB}') >= ${PRUNE_THRESHOLD} else 1)"; then
    log "pruning build cache (${CACHE_GB} GB reclaimable >= ${PRUNE_THRESHOLD} GB threshold)"
    "${DOCKER}" builder prune -f
    log "build-cache prune complete"
else
    log "build cache reclaimable ${CACHE_GB} GB < ${PRUNE_THRESHOLD} GB — skipping builder prune"
fi

log "done"
