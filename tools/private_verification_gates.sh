#!/usr/bin/env bash
# Invoked only under private_verification.py's private descriptor/environment capture.
set -Eeuo pipefail
umask 022
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0
export GIT_CONFIG_COUNT=2
export GIT_CONFIG_KEY_0="url.https://x-access-token:${RECAP_DG_CLIENT_TOKEN}@github.com/hydrosolutions/recap-dg-client.git.insteadOf"
export GIT_CONFIG_VALUE_0=https://github.com/hydrosolutions/recap-dg-client.git
export GIT_CONFIG_KEY_1="url.https://x-access-token:${AQUACAST_TOKEN}@github.com/hydrosolutions/aquacast.git.insteadOf"
export GIT_CONFIG_VALUE_1=https://github.com/hydrosolutions/aquacast.git
export RUNNER_TEMP="$PRIVATE_WORK/tmp"
mkdir -p "$RUNNER_TEMP" "$PRIVATE_EVIDENCE/coverage"

ensure_env() {
  local name="$1"
  shift
  export UV_PROJECT_ENVIRONMENT="$PRIVATE_WORK/venv-$name"
  if [ ! -f "$PRIVATE_WORK/$name-ready" ]; then
    uv sync --frozen "$@"
    touch "$PRIVATE_WORK/$name-ready"
  fi
}

ensure_system() {
  if [ -f "$PRIVATE_WORK/system-ready" ]; then return; fi
set -euo pipefail
# Bypass azure.archive.ubuntu.com. The runner's sources use a
# mirrorlist (file:/etc/apt/apt-mirrors.txt) pointing at Azure's
# Ubuntu mirror, and on 2026-08-19 that ONE host stalled ~10 times
# while every other repo in the same `apt-get update` — dl.google.com,
# packages.microsoft.com — answered instantly. Retrying could never
# fix it: a fresh process re-resolves the same hostname to the same
# degraded service. Point at Canonical's archive instead. Slightly
# further from the runner than a same-datacentre mirror; a few
# seconds against ten lost runs.
echo "http://archive.ubuntu.com/ubuntu/" | sudo tee /etc/apt/apt-mirrors.txt
APT_OPTS=(-o Acquire::Retries=3
          -o Acquire::http::Timeout=30
          -o Acquire::https::Timeout=30)
for attempt in 1 2 3; do
  if sudo timeout --kill-after=10 90 apt-get "${APT_OPTS[@]}" update; then
    break
  fi
  if [ "$attempt" = 3 ]; then
    echo "::error::apt-get update stalled or failed on 3 separate attempts"
    exit 1
  fi
  echo "::warning::apt-get update attempt $attempt stalled; retrying in a fresh process"
  sleep 5
done
# --no-upgrade: install what is MISSING, never upgrade what the runner
# image already has. On 2026-09-24 two `unit` shards failed here with
# a 404 on libexpat1/libexpat1-dev 2.6.1-2ubuntu0.6 — Ubuntu had
# published a newer security build and pulled the old .deb while the
# index we had just fetched still named it. The five packages we
# actually need (eccodes, geos and their deps) downloaded fine every
# time; apt only tried to UPGRADE libexpat1, which is already present
# on the image, because we name it explicitly. Two different mirror
# IPs served the same missing version.
#
# ⚠️ Two things this knowingly accepts. It can leave us on a library
# older than a Python wheel wants — apt cannot see what `uv`-installed
# packages link against, so if a minimum native version ever matters,
# assert it explicitly rather than relying on "latest available". And
# it skips Expat security updates for this job; that is a CI runner,
# not a deployed artefact, and the Dockerfile's own install is
# unaffected.
#
# ⛔ libexpat1 stays NAMED even though nothing else here depends on it:
# the Dockerfile records it as a rasterio runtime requirement, and
# Python wheels never ask apt for their system deps. Dropping it would
# dodge this trigger by weakening the declaration.
#
# Rejected: `--fix-missing` (holds back explicitly requested packages
# and does not repair the index). Retrying `install` after a fresh
# `update` remains a valid fallback if this recurs for a package we
# genuinely need — it is safe after a *download* failure, unlike the
# mid-unpack kill the comment above rules out.
sudo apt-get "${APT_OPTS[@]}" install -y --no-install-recommends --no-upgrade \
  libeccodes0 libexpat1 libgeos-c1v5

  touch "$PRIVATE_WORK/system-ready"
}

scan_image() {
  local image="$1" variant="$2"
  local report="$PRIVATE_EVIDENCE/$variant-trivy.json"
  # Operational scan errors may retry once; findings are evaluated once below.
  if ! trivy image --scanners vuln,secret --format json --output "$report" --exit-code 0 --ignore-unfixed "$image"; then
    rm -f "$report"
    trivy image --scanners vuln,secret --format json --output "$report" --exit-code 0 --ignore-unfixed "$image"
  fi
  test -s "$report"
  jq -e 'type == "object" and (length > 0) and ((has("SchemaVersion") | not) or ((.SchemaVersion | type) == "number" and (.SchemaVersion | floor) == .SchemaVersion)) and ((has("Results") | not) or (.Results | type) == "array")' "$report"
  trivy convert --format sarif --scanners vuln,secret --severity HIGH,CRITICAL \
    --output "$PRIVATE_EVIDENCE/$variant-trivy.sarif" "$report"
  test -s "$PRIVATE_EVIDENCE/$variant-trivy.sarif"
  trivy convert --format table --scanners vuln,secret --severity HIGH,CRITICAL --exit-code 1 "$report"
}

generate_sbom() {
  local image="$1" variant="$2"
  SYFT_CHECK_FOR_APP_UPDATE=false "$SYFT_BIN" scan "$image" \
    -o "cyclonedx-json=$PRIVATE_EVIDENCE/$variant-sbom.cdx.json"
  test -s "$PRIVATE_EVIDENCE/$variant-sbom.cdx.json"
  jq -e '.bomFormat == "CycloneDX" and (.components | length) > 0' "$PRIVATE_EVIDENCE/$variant-sbom.cdx.json"
}

prove_image() {
  local image="$1" variant="$2"
  python3 tools/image_identity_probe.py --variant "$variant" --image-tag "$image" \
    --expected-version "$EXPECTED_VERSION" --expected-revision "$EXPECTED_REVISION"
  local imports='import sys, sapphire_flow, prefect'
  if [ "$variant" = aquacast ]; then imports+=', aquacast, torch'; fi
  docker run --rm --network none -e DB_PASSWORD=ci-smoke-only "$image" python -I -c \
    "$imports; from pathlib import Path; assert sys.prefix == '/app/.venv'; assert 'include-system-site-packages = false' in Path('/app/.venv/pyvenv.cfg').read_text().lower()"
}

image_component() {
  local variant="$1" name="$2"
  shift 2
  # Do not invoke a function as an if/! condition: that disables errexit inside
  # it. An unconditional subshell retains each component's fail-fast semantics.
  set +e
  (set -Eeuo pipefail; "$@")
  COMPONENT_STATUS=$?
  set -e
  local outcome=failed
  if [ "$COMPONENT_STATUS" = 0 ]; then outcome=passed; fi
  printf '{"outcome":"%s","exit_code":%s}\n' "$outcome" "$COMPONENT_STATUS" \
    > "$PRIVATE_EVIDENCE/$variant-$name-result.json"
}

build_image() {
  local variant="$1" tag="sapphire-flow-private-$1:$GITHUB_SHA"
  local extra=()
  if [ "$variant" = aquacast ]; then
    extra=(--build-arg WITH_AQUACAST=1 --secret "id=aquacast_token,env=AQUACAST_TOKEN")
  fi
  local context="$PRIVATE_WORK/image-context"
  if [ ! -f "$PRIVATE_WORK/context-ready" ]; then
    mkdir "$context"
    (umask 022; git archive "$GITHUB_SHA" | tar -xf - -C "$context")
    touch "$PRIVATE_WORK/context-ready"
  fi
  docker buildx build --builder private-verification --platform linux/amd64 \
    --file "$context/Dockerfile" --tag "$tag" --load --pull \
    --build-arg "SAPPHIRE_RELEASE_VERSION=$EXPECTED_VERSION" \
    --build-arg "SAPPHIRE_SOURCE_REVISION=$EXPECTED_REVISION" \
    --secret id=recap_dg_client_token,env=RECAP_DG_CLIENT_TOKEN "${extra[@]}" "$context"
  local image
  image=$(docker image inspect --format '{{.Id}}' "$tag")
  test "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")" = linux/amd64
  printf '%s\n' "$image" > "$PRIVATE_EVIDENCE/$variant-image-id"
  if [ "$variant" = default ]; then
    local shipped expected script
    shipped=$(docker run --rm --entrypoint sh "$image" -c 'ls /app/scripts' | sort)
    expected=$(printf '%s\n' backfill_era5_land_history.py backfill_meteoswiss_history.py \
      create_station_group.py import_caravan_attributes.py onboard.py validate_forcing_reference.py | sort)
    test "$shipped" = "$expected"
    for script in $expected; do
      docker run --rm --entrypoint sh "$image" -c "python /app/scripts/$script --help"
    done
  fi
  local scan_status sbom_status proof_status=1
  image_component "$variant" scan scan_image "$image" "$variant"
  scan_status=$COMPONENT_STATUS
  image_component "$variant" sbom generate_sbom "$image" "$variant"
  sbom_status=$COMPONENT_STATUS
  # Legacy default proof needs a valid SBOM, not a green Trivy result. The
  # Aquacast proof was independent of SBOM and keeps that independence.
  if [ "$sbom_status" = 0 ] || [ "$variant" = aquacast ]; then
    image_component "$variant" proof prove_image "$image" "$variant"
    proof_status=$COMPONENT_STATUS
  else
    printf '{"outcome":"blocked","reason":"sbom_failed"}\n' \
      > "$PRIVATE_EVIDENCE/$variant-proof-result.json"
  fi
  test "$scan_status" = 0 && test "$sbom_status" = 0 && test "$proof_status" = 0
}

case "${1:?required gate}" in
  lint)
    ensure_env default
    if [ "$GITHUB_EVENT_NAME" = pull_request ]; then
      "$UV_PROJECT_ENVIRONMENT/bin/python" -I tools/release_target.py check \
        --base-sha "$PR_BASE_SHA" --head-sha "$PR_HEAD_SHA" --merge-sha "$PR_MERGE_SHA"
      uv run --no-sync python tools/check_map_contract_version.py --base-ref "$PR_BASE_SHA"
    fi
    uv run --no-sync ruff check src/ tests/
    uv run --no-sync ruff format --check src/ tests/
    shellcheck -x scripts/launchd/start-sapphire.sh scripts/launchd/watchdog.sh scripts/launchd/install-launchd.sh scripts/launchd/run-recap-probe.sh scripts/launchd/prune-docker.sh scripts/launchd/run-nepal-forcing.sh scripts/launchd/docker-endpoint.sh scripts/bootstrap-mac-mini.sh
    uv run --no-sync pyright --outputjson src/ > "$PRIVATE_EVIDENCE/pyright.json" || true
    uv run --no-sync python tools/pyright_ratchet.py "$PRIVATE_EVIDENCE/pyright.json" tools/pyright_baseline.json
    trivy fs --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed --scanners vuln --skip-dirs .venv .
    ;;
  unit-scripts|unit-services|unit-adapters-flows|unit-rest)
    shard="${1#unit-}"
    ensure_system
    ensure_env unit --extra aquacast
    uv run --no-sync pytest 'tests/unit/models/test_aquacast_shim.py::TestRealDiscovery::test_discover_models_returns_the_aquacast_model' -q --color=no | tee "$RUNNER_TEMP/aquacast-shim.txt"
    grep -Fq '1 passed' "$RUNNER_TEMP/aquacast-shim.txt"
    if [ "$shard" = scripts ]; then
set -o pipefail
uv run --no-sync pytest -v \
  tests/unit/cli/test_export_forecast_lab.py \
  tests/unit/flows/test_compute_skills.py \
  tests/unit/scripts/test_backfill_meteoswiss_history_script.py \
  tests/unit/services/skill/test_combined_skill.py \
  | tee "$RUNNER_TEMP/plan201-sequential.txt"
grep -Fq 'tests/unit/services/skill/test_combined_skill.py::TestCoverageLogging::test_coverage_log_message PASSED' "$RUNNER_TEMP/plan201-sequential.txt"
if grep -Eq '^[0-9]+ (failed|error)' "$RUNNER_TEMP/plan201-sequential.txt" ||
   grep -q ' skipped' "$RUNNER_TEMP/plan201-sequential.txt"; then
  exit 1
fi

    fi
    export COVERAGE_CORE=sysmon
    export COVERAGE_FILE="$PRIVATE_EVIDENCE/coverage/shard.$shard.coverage"
set -euo pipefail
mkdir -p "$(dirname "$COVERAGE_FILE")"
uv run --no-sync python - <<'PY'
import coverage

cov = coverage.Coverage(source=["src/sapphire_flow"])
cov.load()
incompatible = {
    "branch": cov.config.branch,
    "dynamic_context": cov.config.dynamic_context,
    "concurrency": cov.config.concurrency,
}
if any(incompatible.values()):
    raise SystemExit(
        "COVERAGE_CORE=sysmon is only enabled for statement coverage "
        f"without dynamic contexts or concurrency plugins: {incompatible!r}"
    )
cov.start()
core = repr(cov._collector.core)  # noqa: SLF001 - coverage has no public active-core API.
cov.stop()
if "SysMonitor" not in core:
    raise SystemExit(f"COVERAGE_CORE=sysmon did not activate sysmon: {core}")
print(f"coverage measurement core active: {core}")
PY
args="$(uv run --no-sync python tools/unit_shards.py --pytest-args "$shard")"
echo "shard ${shard}: pytest $args"
started=$SECONDS
# Word-splitting $args is deliberate — it is an argument list.
# shellcheck disable=SC2086
uv run --no-sync pytest $args -n auto --cov=src/sapphire_flow --cov-report= --junitxml "$PRIVATE_EVIDENCE/junit-unit-${shard}.xml"
# Plan 319 D1 balanced the shards on COLLECTED COUNTS, which are a
# proxy for duration and not a good one. This line is how that proxy
# gets checked against reality; if the legs come out lopsided, the
# answer is a duration-based split (D1 option b).
echo "::notice title=unit shard ${shard}::wall-clock $((SECONDS - started))s"

    ;;
  unit-coverage)
    ensure_env unit --extra aquacast
    for shard in scripts services adapters-flows rest; do
      test -s "$PRIVATE_EVIDENCE/coverage/shard.$shard.coverage"
    done
    export COVERAGE_FILE="$PRIVATE_EVIDENCE/coverage/combined.coverage"
    uv run --no-sync coverage combine --keep "$PRIVATE_EVIDENCE"/coverage/shard.*.coverage
    uv run --no-sync coverage report --show-missing
    ;;
  wheel-only-guard)
    export UV_PROJECT_ENVIRONMENT="$PRIVATE_WORK/venv-wheel"
    uv sync --frozen --no-build --no-cache --no-install-project --no-install-package forecastinterface --no-install-package recap-dg-client
    uv sync --frozen --no-cache --no-install-project --reinstall-package forecastinterface --reinstall-package recap-dg-client
    ;;
  integration-heavy|integration-rest)
    shard="${1#integration-}"
    ensure_system
    ensure_env default
    container="private-verification-postgres-$shard"
    trap 'docker rm -f "$container"' EXIT
    docker run -d --name "$container" -p 127.0.0.1:5432:5432 \
      -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=sapphire_test \
      --health-cmd pg_isready --health-interval 10s --health-timeout 5s --health-retries 5 \
      postgis/postgis:16-3.4@sha256:44126d872ac91993766c341e369c539e8196614321765d36a6f1bab0419a5fa5
    # pg_isready's exit status is the readiness check; bounded by this gate timeout.
    # shellcheck disable=SC2016 # $1 belongs to the inner readiness shell.
    timeout 90 bash -c 'until docker exec "$1" pg_isready -U test -d sapphire_test; do sleep 1; done' -- "$container"
    export DATABASE_URL=postgresql://test:test@localhost:5432/sapphire_test
    if [ "$shard" = heavy ]; then
      uv run --no-sync python tools/integration_shards.py --check-partition
    fi
    args=$(uv run --no-sync python tools/integration_shards.py --pytest-args "$shard")
    # shellcheck disable=SC2086
    uv run --no-sync pytest $args --ignore=tests/integration/live -v -m 'not slow' \
      --junitxml "$PRIVATE_EVIDENCE/junit-integration-$shard.xml"
    ;;
  build-image-and-scan) build_image default ;;
  aquacast-release-image-proof) build_image aquacast ;;
  *) exit 2 ;;
esac
