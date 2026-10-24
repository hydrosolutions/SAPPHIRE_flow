from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tarfile
from contextlib import suppress
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

REPOSITORY = "hydrosolutions/SAPPHIRE_flow"
ENVIRONMENT = "private-verification"
MAX_REPORT_BYTES = 64 * 1024 * 1024


class VerificationError(ValueError):
    pass


class Outcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True, kw_only=True, slots=True)
class Binding:
    repository: str
    event: str
    ref: str
    source_sha: str
    source_tree: str
    base_sha: str
    head_sha: str
    workflow_ref: str
    workflow_sha: str
    run_id: str
    run_attempt: str
    actor: str
    triggering_actor: str
    job: str
    environment: str

    def __post_init__(self) -> None:
        if self.repository != REPOSITORY or self.environment != ENVIRONMENT:
            raise VerificationError("unapproved repository or environment")
        for value in (
            self.source_sha,
            self.source_tree,
            self.head_sha,
            self.workflow_sha,
        ):
            if not re.fullmatch(r"[0-9a-f]{40}", value):
                raise VerificationError("invalid source identity")
        if self.base_sha and not re.fullmatch(r"[0-9a-f]{40}", self.base_sha):
            raise VerificationError("invalid base identity")
        if not all(
            re.fullmatch(r"[1-9][0-9]*", x) for x in (self.run_id, self.run_attempt)
        ):
            raise VerificationError("invalid attempt identity")
        if self.event == "pull_request":
            if not self.base_sha or not re.fullmatch(
                r"refs/pull/[1-9][0-9]*/merge", self.ref
            ):
                raise VerificationError("invalid PR identity")
        elif self.event != "push" or self.ref != "refs/heads/main":
            raise VerificationError("unsupported verification event")
        if (
            not self.actor
            or self.actor != self.triggering_actor
            or self.actor.endswith("[bot]")
        ):
            raise VerificationError("unapproved actor")
        if self.job != "verify" or not self.workflow_ref.startswith(
            REPOSITORY + "/.github/workflows/private-verification.yml@"
        ):
            raise VerificationError("invalid workflow identity")


def require_admission(raw: str, actual: Binding) -> None:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise VerificationError("duplicate admission field")
            result[key] = value
        return result

    try:
        approved = json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, TypeError) as exc:
        raise VerificationError("invalid admission record") from exc
    if approved != asdict(actual):
        raise VerificationError("admission does not match this source and attempt")


@dataclass(frozen=True, kw_only=True, slots=True)
class Gate:
    name: str
    timeout_seconds: int
    needs: tuple[str, ...] = ()


GATES = (
    Gate(name="lint", timeout_seconds=1200),
    *(
        Gate(name="unit-" + shard, timeout_seconds=1800)
        for shard in ("scripts", "services", "adapters-flows", "rest")
    ),
    Gate(
        name="unit-coverage",
        timeout_seconds=900,
        needs=("unit-scripts", "unit-services", "unit-adapters-flows", "unit-rest"),
    ),
    Gate(name="wheel-only-guard", timeout_seconds=900),
    Gate(name="integration-heavy", timeout_seconds=1500),
    Gate(name="integration-rest", timeout_seconds=1500),
    Gate(
        name="integration",
        timeout_seconds=120,
        needs=("integration-heavy", "integration-rest"),
    ),
    Gate(name="build-image-and-scan", timeout_seconds=3600),
    Gate(name="aquacast-release-image-proof", timeout_seconds=1200),
)


CI_JOB_GATES: dict[str, tuple[str, ...]] = {
    "lint": ("lint",),
    "unit": ("unit-scripts", "unit-services", "unit-adapters-flows", "unit-rest"),
    "unit-coverage": ("unit-coverage",),
    "wheel-only-guard": ("wheel-only-guard",),
    "integration-shard": ("integration-heavy", "integration-rest"),
    "integration": ("integration",),
    "build-image-and-scan": ("build-image-and-scan",),
    "aquacast-release-image-proof": ("aquacast-release-image-proof",),
}


def require_complete(results: Mapping[str, Outcome]) -> None:
    if set(results) != {gate.name for gate in GATES} or any(
        outcome != Outcome.PASSED for outcome in results.values()
    ):
        raise VerificationError("required verification gates incomplete")


def run_private(
    command: list[str],
    *,
    cwd: Path,
    evidence: Path,
    env: Mapping[str, str],
    timeout_seconds: int,
) -> Outcome:
    evidence.mkdir(mode=0o700)
    child_env = dict(env)
    # Redirect workflow-command files as well as ordinary stdout/stderr. The
    # interpreter and reviewed candidate remain trusted; this is not a sandbox.
    for name in (
        "GITHUB_STEP_SUMMARY",
        "GITHUB_ENV",
        "GITHUB_OUTPUT",
        "GITHUB_PATH",
        "GITHUB_STATE",
    ):
        child_env[name] = str(evidence / name.lower())
    for name in (
        "ACTIONS_RUNTIME_TOKEN",
        "ACTIONS_CACHE_URL",
        "ACTIONS_RESULTS_URL",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "PRIVATE_REPORT_SSH_KEY",
        "PRIVATE_REPORT_KNOWN_HOSTS",
        "PRIVATE_VERIFICATION_ADMISSION",
    ):
        child_env.pop(name, None)
    with (evidence / "command.log").open("xb") as output:
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError:
            return Outcome.FAILED
        try:
            return (
                Outcome.PASSED
                if process.wait(timeout=timeout_seconds) == 0
                else Outcome.FAILED
            )
        except subprocess.TimeoutExpired:
            return Outcome.FAILED
        finally:
            # Also stop descendants left behind by a timed-out shell command.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def pack_report(evidence: Path, *, secrets: tuple[bytes, ...]) -> bytes:
    output = io.BytesIO()
    total = 0
    with (
        gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w|") as archive,
    ):
        files = sorted(evidence.rglob("*"))
        if len(files) > 256:
            raise VerificationError("too many evidence files")
        for path in files:
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise VerificationError("nonregular evidence")
            total += path.stat().st_size
            if total > MAX_REPORT_BYTES:
                raise VerificationError("evidence exceeds receiver limit")
            data = path.read_bytes()
            for secret in secrets:
                if secret:
                    data = data.replace(secret, b"[REDACTED]")
            info = tarfile.TarInfo(path.relative_to(evidence).as_posix())
            info.mode = 0o600
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    payload = output.getvalue()
    if not payload or len(payload) > MAX_REPORT_BYTES:
        raise VerificationError("report exceeds receiver limit")
    return payload


def finish_verification(
    *,
    binding: Binding,
    admission: str,
    results: Mapping[str, Outcome],
    evidence: Path,
    secrets: tuple[bytes, ...],
    deliver: Callable[[bytes], None],
) -> None:
    require_admission(admission, binding)
    manifest = {
        "binding": asdict(binding),
        "gates": dict(results),
        "receiver_ack_is_not_accepted_custody": True,
    }
    (evidence / "manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    # Failed/incomplete results are still delivered for private diagnosis.
    # Neither successful delivery nor a missing gate can turn them green.
    payload = pack_report(evidence, secrets=secrets)
    deliver(len(payload).to_bytes(8, "big") + payload)
    require_complete(results)


def git_value(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    if result.returncode:
        raise VerificationError("source validation failed")
    return result.stdout.decode("ascii").strip()


def current_binding(root: Path, env: Mapping[str, str]) -> Binding:
    event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text())
    source = git_value(root, "rev-parse", "HEAD")
    if source != env["GITHUB_SHA"] or git_value(root, "diff", "--name-only", "HEAD"):
        raise VerificationError("checkout identity changed")
    base = ""
    head = source
    if env["GITHUB_EVENT_NAME"] == "pull_request":
        pr = event["pull_request"]
        if (
            pr["head"]["repo"]["full_name"] != REPOSITORY
            or pr["base"]["repo"]["full_name"] != REPOSITORY
            or pr["base"]["ref"] != "main"
        ):
            raise VerificationError("noncanonical PR")
        base, head = pr["base"]["sha"], pr["head"]["sha"]
        if git_value(root, "show", "-s", "--format=%P", "HEAD").split() != [base, head]:
            raise VerificationError("merge parent mismatch")
        if env["GITHUB_REF"] != f"refs/pull/{event['number']}/merge":
            raise VerificationError("PR ref mismatch")
    return Binding(
        repository=env["GITHUB_REPOSITORY"],
        event=env["GITHUB_EVENT_NAME"],
        ref=env["GITHUB_REF"],
        source_sha=source,
        source_tree=git_value(root, "rev-parse", "HEAD^{tree}"),
        base_sha=base,
        head_sha=head,
        workflow_ref=env["GITHUB_WORKFLOW_REF"],
        workflow_sha=env["GITHUB_WORKFLOW_SHA"],
        run_id=env["GITHUB_RUN_ID"],
        run_attempt=env["GITHUB_RUN_ATTEMPT"],
        actor=env["GITHUB_ACTOR"],
        triggering_actor=env["GITHUB_TRIGGERING_ACTOR"],
        job=env["GITHUB_JOB"],
        environment=ENVIRONMENT,
    )


def deliver_ssh(frame: bytes, *, work: Path, env: Mapping[str, str]) -> None:
    target = env["PRIVATE_REPORT_TARGET"]
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*@[a-zA-Z0-9][a-zA-Z0-9.-]*", target):
        raise VerificationError("invalid report target")
    key, hosts = work / "report-key", work / "known-hosts"
    if (
        not env["PRIVATE_REPORT_SSH_KEY"].strip()
        or not env["PRIVATE_REPORT_KNOWN_HOSTS"].strip()
    ):
        raise VerificationError("missing report credentials")
    command = [
        "ssh",
        "-F",
        "/dev/null",
        "-T",
        "-i",
        str(key),
        "-oBatchMode=yes",
        "-oIdentitiesOnly=yes",
        "-oStrictHostKeyChecking=yes",
        "-oUserKnownHostsFile=" + str(hosts),
        "-oGlobalKnownHostsFile=/dev/null",
        "-oClearAllForwardings=yes",
        "-oPermitLocalCommand=no",
        "-oProxyCommand=none",
        "-oProxyJump=none",
        "-oConnectTimeout=10",
        "-oIdentityAgent=none",
        "-oPasswordAuthentication=no",
        "-oKbdInteractiveAuthentication=no",
        "-oServerAliveInterval=10",
        "-oServerAliveCountMax=2",
        target,
        "upload-build-report",
    ]
    created: list[Path] = []
    try:
        for path, value in (
            (key, env["PRIVATE_REPORT_SSH_KEY"]),
            (hosts, env["PRIVATE_REPORT_KNOWN_HOSTS"]),
        ):
            with path.open("x") as output:
                created.append(path)
                path.chmod(0o600)
                output.write(value + "\n")
        response = subprocess.run(
            command,
            input=frame,
            capture_output=True,
            timeout=120,
            check=False,
            env={"PATH": env.get("PATH", os.defpath), "HOME": str(work), "LC_ALL": "C"},
        )
        if response.returncode != 0 or response.stdout != b"REPORT_RECEIVED\n":
            raise VerificationError("private report delivery not acknowledged")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerificationError("private report delivery failed") from exc
    finally:
        for path in created:
            path.unlink(missing_ok=True)


def record_disk(evidence: Path, boundary: str, paths: Mapping[str, Path]) -> None:
    filesystems: dict[str, dict[str, str | int | None]] = {}
    for label, path in paths.items():
        observation: dict[str, str | int | None] = {
            "path": str(path),
            "device": None,
            "total": None,
            "used": None,
            "free": None,
        }
        try:
            usage = shutil.disk_usage(path)
            device = path.stat().st_dev
        except OSError:
            pass  # Explicit null measurements mean unavailable, never zero capacity.
        else:
            observation.update(
                device=device, total=usage.total, used=usage.used, free=usage.free
            )
        filesystems[label] = observation
    with (evidence / "disk-snapshots.jsonl").open("a") as output:
        output.write(
            json.dumps(
                {
                    "boundary": boundary,
                    "measurement": "sampled-not-peak",
                    "filesystems": filesystems,
                },
                sort_keys=True,
            )
            + "\n"
        )


def execute(root: Path, work: Path, env: Mapping[str, str]) -> None:
    binding = current_binding(root, env)
    admission = env["PRIVATE_VERIFICATION_ADMISSION"]
    require_admission(admission, binding)
    os.umask(0o077)
    work.mkdir(mode=0o700)  # unique per invocation; never reuse an old attempt
    evidence = work / "evidence"
    evidence.mkdir(mode=0o700)
    source_hashes = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in (
            ".github/workflows/ci.yml",
            ".github/workflows/private-verification.yml",
            "tools/private_verification.py",
            "tools/private_verification_gates.sh",
        )
    }
    (evidence / "source-files.json").write_text(json.dumps(source_hashes) + "\n")
    results: dict[str, Outcome] = {}
    child_env = dict(env)
    child_env.update(
        {
            "PRIVATE_WORK": str(work),
            "PRIVATE_EVIDENCE": str(evidence),
            "UV_FROZEN": "1",
            "UV_CACHE_DIR": str(work / "uv-cache"),
            "TRIVY_CACHE_DIR": str(work / "trivy-cache"),
            "XDG_CACHE_HOME": str(work / "cache"),
            "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SAPPHIRE_FLOW": "0.dev0+g"
            + binding.source_sha,
            "EXPECTED_VERSION": "0.dev0+g" + binding.source_sha,
            "EXPECTED_REVISION": binding.source_sha,
            "PR_BASE_SHA": binding.base_sha,
            "PR_HEAD_SHA": binding.head_sha,
            "PR_MERGE_SHA": binding.source_sha,
            "GIT_TERMINAL_PROMPT": "0",
            "DOCKER_BUILD_RECORD_UPLOAD": "false",
            "DOCKER_BUILD_SUMMARY": "false",
        }
    )
    preparation = json.loads(env["PRIVATE_PREPARATION"])
    ready = (
        preparation
        == dict.fromkeys(("checkout", "uv", "buildx", "trivy", "syft"), "success")
        and all(env.get(key) for key in ("RECAP_DG_CLIENT_TOKEN", "AQUACAST_TOKEN"))
        and env.get("RUNNER_DEBUG", "0") != "1"
    )
    (evidence / "preparation.json").write_text(json.dumps(preparation) + "\n")
    disk_paths = {
        "root": Path("/"),
        "workspace": root,
        "work": work,
        "docker-default-root": Path("/var/lib/docker"),
    }
    record_disk(evidence, "before-gates", disk_paths)
    for gate in GATES:
        require_admission(admission, current_binding(root, env))
        if not ready or any(results.get(name) != Outcome.PASSED for name in gate.needs):
            results[gate.name] = Outcome.BLOCKED
        elif gate.name == "integration":
            # This is the existing aggregate assertion, not a skipped subprocess.
            results[gate.name] = Outcome.PASSED
        else:
            results[gate.name] = run_private(
                ["bash", "tools/private_verification_gates.sh", gate.name],
                cwd=root,
                evidence=evidence / gate.name,
                env=child_env,
                timeout_seconds=gate.timeout_seconds,
            )
        record_disk(evidence, "after-" + gate.name, disk_paths)
    require_admission(admission, current_binding(root, env))
    # setup-buildx's public post-job cleanup is disabled. Clean up privately.
    cleanup = run_private(
        ["docker", "buildx", "rm", "private-verification"],
        cwd=root,
        evidence=evidence / "builder-cleanup",
        env=child_env,
        timeout_seconds=60,
    )
    record_disk(evidence, "after-builder-cleanup", disk_paths)
    if cleanup != Outcome.PASSED:
        results["build-image-and-scan"] = Outcome.FAILED
    finish_verification(
        binding=binding,
        admission=admission,
        results=results,
        evidence=evidence,
        secrets=tuple(
            env.get(name, "").encode()
            for name in (
                "RECAP_DG_CLIENT_TOKEN",
                "AQUACAST_TOKEN",
                "PRIVATE_REPORT_SSH_KEY",
            )
        ),
        deliver=lambda frame: deliver_ssh(frame, work=work, env=env),
    )
    require_admission(admission, current_binding(root, env))


def main() -> int:
    # A stdlib bootstrap status, not an application logger: no diagnostics or
    # exception text may reach the Actions command parser or public summary.
    try:
        root = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
        work = Path(os.environ["RUNNER_TEMP"]) / (
            "private-verification-"
            + os.environ["GITHUB_RUN_ID"]
            + "-"
            + os.environ["GITHUB_RUN_ATTEMPT"]
        )
        execute(root, work, os.environ)
    except (
        Exception,
        KeyboardInterrupt,
    ):  # fixed fail-closed boundary, no raw traceback
        with suppress(OSError):
            os.write(1, b"PRIVATE_VERIFICATION_FAILED\n")
        return 1
    with suppress(OSError):
        os.write(1, b"PRIVATE_VERIFICATION_COMPLETE\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
