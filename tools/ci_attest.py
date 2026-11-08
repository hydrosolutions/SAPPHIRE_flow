from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import ModuleType


class AttestationError(ValueError):
    pass


MANDATORY_STEPS: dict[str, tuple[str, ...]] = {
    "lint": (
        "Checkout exact source",
        "Set SCM version override for editable root installs",
        "Install frozen dependencies",
        "Enforce Ruff lint",
        "Enforce Ruff formatting",
        "Enforce shellcheck",
        "Enforce pyright ratchet",
        "Trivy filesystem scan",
    ),
    "unit (scripts)": (
        "Checkout exact source",
        "Set SCM version override for editable root installs",
        "Install (aquacast extra)",
        "Prove the aquacast shim test ran",
        "Run unit shard",
        "Plan 201 regression — known ordering leak stays fixed (sequential)",
        "Run fake-store contracts",
    ),
    "unit (services)": (
        "Checkout exact source",
        "Set SCM version override for editable root installs",
        "Install (aquacast extra)",
        "Prove the aquacast shim test ran",
        "Run unit shard",
    ),
    "unit (adapters-flows)": (
        "Checkout exact source",
        "Set SCM version override for editable root installs",
        "Install (aquacast extra)",
        "Prove the aquacast shim test ran",
        "Run unit shard",
    ),
    "unit (rest)": (
        "Checkout exact source",
        "Set SCM version override for editable root installs",
        "Install (aquacast extra)",
        "Prove the aquacast shim test ran",
        "Run unit shard",
    ),
    "unit-coverage": (
        "Checkout exact source",
        "Set SCM version override for editable root installs",
        "Combine the shards into one whole-suite number",
    ),
    "wheel-only-guard": (
        "Checkout exact source",
        "Set SCM version override for editable root installs",
        "Enforce frozen wheel-only guard",
    ),
    "integration (heavy)": (
        "Checkout exact source",
        "Set SCM version override for editable root installs",
        "Run integration tests",
        "Prove integration shard partition",
        "Run Swiss overview performance gate",
    ),
    "integration (rest)": (
        "Checkout exact source",
        "Set SCM version override for editable root installs",
        "Run integration tests",
    ),
    "integration": ("Require all integration shards to pass",),
    "build-image-and-scan": (
        "Checkout exact source",
        "Set SCM version override for editable root installs",
        "Build app image",
        "Smoke-check operator scripts in the built image",
        "Require a Trivy report",
        "Render vulnerability table (gate)",
        "Convert report to SARIF",
        "Upload Trivy SARIF to code scanning",
        "Require a valid SBOM",
        "Upload SBOM artifact",
        "Prove default image release identity",
    ),
    "aquacast-release-image-proof": (
        "Checkout exact source",
        "Assert checkout matches GitHub SHA",
        "Build Aquacast app image",
        "Trivy Aquacast image scan",
        "Prove Aquacast image release identity",
    ),
}

# Reviewed workflow bytes bind names to actual commands, action pins and inputs.
# Update only with the workflow and the producer inventory drift tests.
WORKFLOW_SHA256 = "0d890f982430d208cb16d54b3c56f5af95302b5fd7625dd94586766b4c57da2c"


def require_workflow(root: Path) -> None:
    raw = (root / ".github/workflows/ci.yml").read_bytes()
    if hashlib.sha256(raw).hexdigest() != WORKFLOW_SHA256:
        raise AttestationError("workflow_inventory_drift")


def qualified_jobs(
    jobs: list[dict[str, object]],
    *,
    run_id: int,
    attempt: int,
) -> tuple[dict[str, object], ...] | None:
    selected: dict[str, dict[str, object]] = {}
    identifiers: set[int] = set()
    for job in jobs:
        name = job.get("name")
        identifier = job.get("id")
        if not isinstance(name, str) or type(identifier) is not int:
            raise AttestationError("invalid_job_identity")
        if identifier <= 0 or identifier in identifiers:
            raise AttestationError("duplicate_job_identity")
        identifiers.add(identifier)
        if job.get("run_id") != run_id or type(job.get("run_attempt")) is not int:
            raise AttestationError("unknown_job_attempt")
        if job["run_attempt"] != attempt:
            return None
        if name == "ci-attest":
            continue
        if name not in MANDATORY_STEPS or name in selected:
            raise AttestationError("unexpected_job_inventory")
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            return None
        steps = job.get("steps")
        if not isinstance(steps, list):
            raise AttestationError("missing_job_steps")
        required = MANDATORY_STEPS[name]
        counts: dict[str, int] = dict.fromkeys(required, 0)
        for raw_step in cast("list[object]", steps):
            if not isinstance(raw_step, dict):
                raise AttestationError("invalid_job_step")
            step = cast("dict[str, object]", raw_step)
            step_name = step.get("name")
            if isinstance(step_name, str) and step_name in counts:
                if (
                    step.get("status") != "completed"
                    or step.get("conclusion") != "success"
                ):
                    return None
                counts[step_name] += 1
        if set(counts.values()) != {1}:
            return None
        selected[name] = job
    if set(selected) != set(MANDATORY_STEPS):
        return None
    return tuple(selected[name] for name in MANDATORY_STEPS)


def provenance_tool() -> ModuleType:
    name = "sapphire_ci_provenance"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name("ci_provenance.py")
    )
    if spec is None or spec.loader is None:
        raise AttestationError("transport_unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def positive_id(value: object) -> int:
    if not isinstance(value, str) or re.fullmatch(r"[1-9][0-9]{0,19}", value) is None:
        raise AttestationError("invalid_attempt_identity")
    return int(value)


def commit_facts(root: Path, source: str, timeout: float) -> dict[str, object]:
    result = subprocess.run(
        ["git", "show", "--no-patch", "--format=%H%n%T%n%P", source],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=True,
    )
    lines = result.stdout.decode("ascii").splitlines()
    if len(lines) != 3 or lines[0] != source:
        raise AttestationError("checkout_identity")
    parents = lines[2].split()
    if not parents or any(
        re.fullmatch(r"[0-9a-f]{40}", value) is None for value in [lines[1], *parents]
    ):
        raise AttestationError("checkout_parents")
    blob = (
        subprocess.run(
            ["git", "rev-parse", source + ":.github/workflows/ci.yml"],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=True,
        )
        .stdout.decode("ascii")
        .strip()
    )
    if re.fullmatch(r"[0-9a-f]{40}", blob) is None:
        raise AttestationError("workflow_blob")
    return {"head": source, "tree": lines[1], "parents": parents, "workflow_blob": blob}


def produce(
    root: Path, output: Path, environment: Mapping[str, str], token: str
) -> bool:
    if (
        environment.get("GITHUB_EVENT_NAME") != "push"
        or environment.get("GITHUB_REF") != "refs/heads/main"
    ):
        return False
    require_workflow(root)
    run_id = positive_id(environment.get("GITHUB_RUN_ID"))
    attempt = positive_id(environment.get("GITHUB_RUN_ATTEMPT"))
    source = environment.get("GITHUB_SHA", "")
    if (
        environment.get("GITHUB_REPOSITORY") != "hydrosolutions/SAPPHIRE_flow"
        or re.fullmatch(r"[0-9a-f]{40}", source) is None
    ):
        raise AttestationError("producer_source")
    tool = provenance_tool()
    needs = tool.parse_object(environment.get("CI_NEEDS", "").encode())
    expected = {
        "lint",
        "unit",
        "unit-coverage",
        "wheel-only-guard",
        "integration-shard",
        "integration",
        "build-image-and-scan",
        "aquacast-release-image-proof",
    }
    if set(needs) != expected:
        raise AttestationError("producer_needs")
    if any(
        not isinstance(value, dict)
        or cast("dict[str, object]", value).get("result") != "success"
        for value in needs.values()
    ):
        return False
    budget = tool.Budget(deadline=time.monotonic() + tool.ADMISSION_SECONDS)
    reader = tool.GitHubReader(token, budget)
    try:
        with tool.deadline_guard(budget):
            actual_head = (
                subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=root,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=budget.timeout(),
                    check=True,
                )
                .stdout.decode("ascii")
                .strip()
            )
            if actual_head != source:
                raise AttestationError("checkout_identity")
            facts = commit_facts(root, source, budget.timeout())
            raw_attempt = reader.metadata(f"runs/{run_id}/attempts/{attempt}")
            if (
                raw_attempt.get("id") != run_id
                or raw_attempt.get("run_attempt") != attempt
                or raw_attempt.get("head_sha") != source
            ):
                raise AttestationError("attempt_identity")
            started = tool.Instant.parse(raw_attempt.get("run_started_at"))
            jobs = qualified_jobs(
                reader.items(f"runs/{run_id}/attempts/{attempt}/jobs", "jobs"),
                run_id=run_id,
                attempt=attempt,
            )
            if jobs is None:
                return False
            summaries: list[dict[str, object]] = []
            for job in jobs:
                first = tool.Instant.parse(job.get("started_at"))
                last = tool.Instant.parse(job.get("completed_at"))
                if not started.seconds <= first.seconds <= last.seconds:
                    raise AttestationError("job_time_order")
                summaries.append(
                    {
                        key: job[key]
                        for key in ("id", "name", "started_at", "completed_at")
                    }
                )
            document = {
                "schema_version": 1,
                "qualification": "FULL",
                "execution_kind": "full",
                "run_id": run_id,
                "run_attempt": attempt,
                "checkout": facts,
                "mandatory_jobs": summaries,
            }
            raw = (
                json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            if len(raw) > tool.MAX_PROOF:
                raise AttestationError("proof_size")
            budget.remaining()
            output.parent.mkdir(mode=0o700)
            with output.open("xb") as stream:
                stream.write(raw)
    finally:
        reader.forget_credential()
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    token = os.environ.pop("GH_TOKEN", "")
    try:
        qualified = produce(
            Path(__file__).resolve().parents[1], args.output, dict(os.environ), token
        )
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
            output.write("qualified=" + ("true" if qualified else "false") + "\n")
    except (OSError, ValueError, subprocess.SubprocessError, KeyError):
        sys.stderr.write("FULL CI attestation failed.\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
