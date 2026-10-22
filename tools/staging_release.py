from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import traceback
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

_spec = importlib.util.spec_from_file_location(
    "staging_transfer", Path(__file__).with_name("staging_release_transfer.py")
)
assert _spec is not None and _spec.loader is not None
transfer = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = transfer
_spec.loader.exec_module(transfer)
release = transfer.tool("release_identity")
verification = transfer.tool("private_verification")
SECRET_NAMES = ("RECAP_DG_CLIENT_TOKEN", "AQUACAST_TOKEN", "STAGING_ADMIN_SSH_KEY")
IMAGE_GATES = {
    "default": "build-image-and-scan",
    "aquacast": "aquacast-release-image-proof",
}

SOURCE_FILES = (
    ".github/workflows/private-verification.yml",
    "tools/staging_release.py",
    "tools/staging_release_transfer.py",
    "tools/private_verification.py",
    "tools/private_verification_gates.sh",
    "tools/release_identity.py",
    "tools/image_identity_probe.py",
    "src/sapphire_flow/ops/image_archive.py",
)


def approved_binding(env: Mapping[str, str]) -> transfer.ReleaseBinding:
    return transfer.ReleaseBinding.parse(
        transfer.parse_json(env["STAGING_RELEASE_ADMISSION"].encode())
    )


def require_local_release(
    root: Path, env: Mapping[str, str]
) -> transfer.ReleaseBinding:
    binding = approved_binding(env)
    event = transfer.parse_json(transfer.metadata_bytes(Path(env["GITHUB_EVENT_PATH"])))
    expected = {
        "GITHUB_REPOSITORY": binding.repository,
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/tags/v" + binding.version,
        "GITHUB_SHA": binding.source_sha,
        "GITHUB_WORKFLOW_SHA": binding.workflow_sha,
        "GITHUB_WORKFLOW_REF": binding.workflow_ref,
        "GITHUB_RUN_ID": binding.run_id,
        "GITHUB_RUN_ATTEMPT": binding.run_attempt,
        "GITHUB_ACTOR": binding.actor,
        "GITHUB_TRIGGERING_ACTOR": binding.actor,
        "GITHUB_JOB": "verify",
    }
    if any(env.get(k) != v for k, v in expected.items()) or event.get("inputs") != {
        "delivery_mode": binding.delivery_mode.value
    }:
        raise transfer.TransferError("event does not match approval")
    release.validate_destination()
    tree = release.ensure_clean_context(binding.source_sha)
    if tree != binding.source_tree:
        raise transfer.TransferError("approved source tree changed")
    return binding


def require_release(root: Path, env: Mapping[str, str]) -> transfer.ReleaseBinding:
    binding = require_local_release(root, env)
    main, state, tags = release.fetch_remote_state()
    if state != binding.release_state:
        raise transfer.TransferError("approved release-state changed")
    release.validate_state(state, tags, main)
    release.ensure_source_on_main(binding.source_sha, main)
    tag = next((t for t in tags if t.name == "v" + binding.version), None)
    if (
        tag is None
        or tag.metadata is None
        or tag.object_oid != binding.tag_object
        or tag.peeled_oid != binding.source_sha
    ):
        raise transfer.TransferError("approved release tag changed")
    target = release.read_release_target(binding.source_sha)
    if target.text != binding.version:
        raise transfer.TransferError("canonical release target mismatch")
    return binding


def private_env(
    env: Mapping[str, str], binding: transfer.ReleaseBinding, work: Path
) -> dict[str, str]:
    result = dict(env)
    for name in (
        "STAGING_ADMIN_SSH_KEY",
        "STAGING_KNOWN_HOSTS",
        "STAGING_RELEASE_ADMISSION",
    ):
        result.pop(name, None)
    result.update(
        PRIVATE_WORK=str(work),
        PRIVATE_EVIDENCE=str(work / "evidence"),
        UV_FROZEN="1",
        UV_CACHE_DIR=str(work / "uv-cache"),
        TRIVY_CACHE_DIR=str(work / "trivy-cache"),
        XDG_CACHE_HOME=str(work / "cache"),
        SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SAPPHIRE_FLOW=binding.version,
        EXPECTED_VERSION=binding.version,
        EXPECTED_REVISION=binding.source_sha,
        GIT_TERMINAL_PROMPT="0",
        DOCKER_BUILD_RECORD_UPLOAD="false",
        DOCKER_BUILD_SUMMARY="false",
    )
    return result


class ReleaseBuilder:
    def __init__(
        self, root: Path, work: Path, env: Mapping[str, str], results: dict[str, str]
    ) -> None:
        self.root, self.work, self.env, self.results = root, work, dict(env), results

    def build(self, context: Path, version: str, source: str) -> list:
        images = []
        for variant, gate in IMAGE_GATES.items():
            tag = (
                "sapphire-flow-aquacast:" if variant == "aquacast" else "sapphire-flow:"
            ) + version
            env = {
                **self.env,
                "RELEASE_IMAGE_CONTEXT": str(context),
                "RELEASE_IMAGE_TAG": tag,
            }
            outcome = verification.run_private(
                ["bash", "tools/private_verification_gates.sh", gate],
                cwd=self.root,
                evidence=self.work / "evidence" / gate,
                env=env,
                timeout_seconds=3600 if variant == "default" else 1200,
            )
            self.results[gate] = outcome.value
            try:
                id_file = self.work / "evidence" / (variant + "-image-id")
                if not id_file.exists():
                    transfer.write_once(
                        self.work / "evidence" / (variant + "-build-result.json"),
                        transfer.json_bytes({"outcome": "failed"}),
                    )
                else:
                    raw_id = transfer.image_id(id_file.read_text().strip())
                    transfer.write_once(
                        self.work / "evidence" / (variant + "-build-result.json"),
                        transfer.json_bytes({"outcome": "passed", "image_id": raw_id}),
                    )
                    if outcome == verification.Outcome.PASSED:
                        if (
                            release.run_docker(
                                ["image", "inspect", "--format", "{{.Id}}", tag],
                                cwd=self.root,
                            )
                            != raw_id
                        ):
                            raise transfer.TransferError("mutable image tag changed")
                        image = release.inspect_image(
                            raw_id, version, source, variant=variant
                        )
                        if (
                            image.image_id != raw_id
                            or release.run_docker(
                                ["image", "inspect", "--format", "{{.Id}}", tag],
                                cwd=self.root,
                            )
                            != raw_id
                        ):
                            raise transfer.TransferError(
                                "image identity changed during proof"
                            )
                        images.append(replace(image, mutable_tag=tag))
            except Exception:
                self.results[gate] = "failed"
                transfer.write_once(
                    self.work / "evidence" / (variant + "-identity-failure.log"),
                    traceback.format_exc().encode(),
                )
            for component in ("build", "scan", "sbom", "proof"):
                component_path = (
                    self.work / "evidence" / f"{variant}-{component}-result.json"
                )
                if not component_path.exists():
                    transfer.write_once(
                        component_path,
                        transfer.json_bytes(
                            {"outcome": "blocked", "reason": "image_gate_aborted"}
                        ),
                    )
            transfer.write_once(
                self.work / "evidence" / (variant + "-gate-result.json"),
                transfer.json_bytes({"outcome": self.results[gate]}),
            )
            disk_sample(self.root, self.work, "after-" + gate)
        if any(self.results[g] != "passed" for g in IMAGE_GATES.values()):
            raise release.ReleaseIdentityError(
                "release image gates failed; evidence retained"
            )
        return images


def disk_sample(root: Path, work: Path, boundary: str) -> None:
    verification.record_disk(
        work / "evidence",
        boundary,
        {
            "root": Path("/"),
            "workspace": root,
            "work": work,
            "docker-default-root": Path("/var/lib/docker"),
        },
    )


def gate_environment_ready(env: Mapping[str, str]) -> bool:
    preparation = transfer.parse_json(env["PRIVATE_PREPARATION"].encode())
    return (
        preparation
        == dict.fromkeys(("checkout", "uv", "buildx", "trivy", "syft"), "success")
        and all(env.get(n) for n in SECRET_NAMES[:2])
        and env.get("RUNNER_DEBUG", "0") != "1"
    )


def archive_images(ids: list[str], output: Path, limit: int, errors_path: Path) -> None:
    receiver = transfer.tool("receive_build_report")
    with output.open("xb") as target, errors_path.open("xb") as errors:
        process = subprocess.Popen(
            ["docker", "image", "save", *ids],
            stdout=subprocess.PIPE,
            stderr=errors,
            start_new_session=True,
        )
        try:
            assert process.stdout is not None
            os.set_blocking(process.stdout.fileno(), False)
            stream = receiver.InputStream(
                fd=process.stdout.fileno(),
                deadline=time.monotonic() + 600,
                clock=time.monotonic,
                wait=receiver.wait_ready,
            )
            size = 0
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise transfer.TransferError("export exceeds capacity")
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
            if process.wait(timeout=30):
                raise transfer.TransferError("image export failed")
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def runtime_bundle(root: Path, binding: transfer.ReleaseBinding, output: Path) -> None:
    with tarfile.open(output, "x:") as archive:
        for name in transfer.RUNTIME_FILES:
            # Read the approved Git object, not test-mutated workspace bytes.
            result = subprocess.run(
                ["git", "show", f"{binding.source_sha}:{name}"],
                cwd=root,
                capture_output=True,
                check=False,
            )
            if result.returncode or len(result.stdout) > transfer.MAX_METADATA:
                raise transfer.TransferError("runtime configuration source unavailable")
            import io

            member = tarfile.TarInfo(name)
            member.size = len(result.stdout)
            member.mode = 0o755 if name.endswith(".sh") else 0o644
            archive.addfile(member, io.BytesIO(result.stdout))


def export_release(
    root: Path, work: Path, binding: transfer.ReleaseBinding, env: Mapping[str, str]
) -> tuple:
    payload = work / "payload"
    receipt = transfer.parse_json(transfer.metadata_bytes(payload / "receipt.json"))
    max_expanded = transfer.positive(int(env["STAGING_MAX_EXPANDED_BYTES"]), 2**40)
    max_archive = transfer.positive(int(env["STAGING_MAX_ARCHIVE_BYTES"]), 2**40)
    reserve = transfer.positive(int(env["STAGING_RESERVE_BYTES"]), 2**40)
    if (
        shutil.disk_usage(work).free
        < max_expanded + 2 * max_archive + transfer.MAX_REPORT + reserve
    ):
        raise transfer.TransferError("insufficient runner export capacity")
    raw = payload / "images.tar"
    ids = [transfer.image_id(image["image_id"]) for image in receipt["images"]]
    disk_sample(root, work, "before-export")
    archive_images(ids, raw, max_expanded, work / "evidence" / "export-save.log")
    with tarfile.open(raw, "r:") as saved:
        manifest_file = saved.extractfile("manifest.json")
        if manifest_file is None:
            raise transfer.TransferError("missing export manifest")
        manifest = json.loads(manifest_file.read(transfer.MAX_METADATA + 1))
        if (
            not isinstance(manifest, list)
            or len(manifest) != 2
            or any(
                not isinstance(item, dict) or item.get("RepoTags") not in (None, [])
                for item in manifest
            )
        ):
            raise transfer.TransferError(
                "export must contain exactly two untagged images"
            )
    archive = transfer.tool("image_archive")
    driver = release.run_docker(["info", "--format", "{{.Driver}}"], cwd=root)
    status = release.run_docker(
        ["info", "--format", "{{json .DriverStatus}}"], cwd=root
    )
    mode = transfer.docker_store_mode(driver, status)
    images = []
    for item in receipt["images"]:
        observed = archive.describe_image_archive(raw, item["image_id"])
        images.append(
            transfer.ImageBinding(
                variant=item["variant"],
                raw_id=item["image_id"],
                config_digest=observed.config_digest,
                platform=observed.platform,
                store_mode=mode,
                id_kind="config"
                if observed.config_digest == item["image_id"]
                else "oci-descriptor",
            )
        )
    with (payload / "images.tar.gz").open("xb") as target, raw.open("rb") as source:
        with gzip.GzipFile(
            fileobj=target, mode="wb", filename="", mtime=0
        ) as compressed:
            shutil.copyfileobj(source, compressed, 1024 * 1024)
        target.flush()
        os.fsync(target.fileno())
    if (payload / "images.tar.gz").stat().st_size > max_archive:
        raise transfer.TransferError("compressed archive exceeds capacity")
    expanded_size = raw.stat().st_size
    expanded_hash = transfer.FileDigest.of(raw).sha256
    unpacked_size = archive.unpacked_layer_bytes(raw, max_expanded)
    raw.unlink()
    runtime_bundle(root, binding, payload / "runtime.tar")
    disk_sample(root, work, "after-export")
    return tuple(images), expanded_size, expanded_hash, unpacked_size


def verify_release(root: Path, work: Path, env: Mapping[str, str]) -> None:
    binding = require_release(root, env)
    limits = {
        name: transfer.positive(
            int(env[name]),
            transfer.MAX_SENDER_SECONDS
            if name == "STAGING_TRANSFER_TIMEOUT"
            else 2**40,
        )
        for name in (
            "STAGING_MAX_ARCHIVE_BYTES",
            "STAGING_MAX_EXPANDED_BYTES",
            "STAGING_RESERVE_BYTES",
            "STAGING_TRANSFER_TIMEOUT",
        )
    }
    transfer.sender_timeout(limits["STAGING_TRANSFER_TIMEOUT"])
    evidence, payload = work / "evidence", work / "payload"
    evidence.mkdir(mode=0o700)
    payload.mkdir(mode=0o700)
    transfer.write_once(evidence / "capacity-limits.json", transfer.json_bytes(limits))
    transfer.write_once(
        evidence / "source-files.json",
        transfer.json_bytes(
            {
                name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                for name in SOURCE_FILES
            }
        ),
    )
    results = dict.fromkeys(transfer.GATE_NAMES, "blocked")
    child_env = private_env(env, binding, work)
    ready = gate_environment_ready(env)
    transfer.write_once(
        evidence / "admission.json", transfer.json_bytes(asdict(binding))
    )
    transfer.write_once(
        evidence / "preparation.json", env["PRIVATE_PREPARATION"].encode()
    )
    mapping = {
        "PR release-target/merge-parent/map-base checks": (
            "canonical published source/target/tag/state validation; "
            "PR-only checks remain in legacy CI"
        ),
        "substantive gates": list(transfer.GATE_NAMES),
    }
    transfer.write_once(evidence / "gate-mapping.json", transfer.json_bytes(mapping))
    disk_sample(root, work, "before-gates")
    for gate in verification.GATES:
        if gate.name in IMAGE_GATES.values():
            continue
        require_local_release(root, env)
        if ready and all(results[n] == "passed" for n in gate.needs):
            if gate.name == "integration":
                results[gate.name] = "passed"
            else:
                results[gate.name] = verification.run_private(
                    ["bash", "tools/private_verification_gates.sh", gate.name],
                    cwd=root,
                    evidence=evidence / gate.name,
                    env=child_env,
                    timeout_seconds=gate.timeout_seconds,
                ).value
        disk_sample(root, work, "after-" + gate.name)
    if ready:
        try:
            require_local_release(root, env)
            release.build_receipt(
                argparse.Namespace(
                    version=binding.version,
                    source=binding.source_sha,
                    receipt=str(payload / "receipt.json"),
                ),
                docker_builder=ReleaseBuilder(root, work, child_env, results),
                approved_tag_object=binding.tag_object,
                approved_release_state=binding.release_state,
            )
        except Exception:
            transfer.write_once(
                evidence / "receipt-failure.log", traceback.format_exc().encode()
            )
            # Variant/component outcomes survive receipt failure.
            transfer.write_once(
                evidence / "receipt-failure.json",
                transfer.json_bytes({"outcome": "failed"}),
            )
            if all(results[g] == "passed" for g in IMAGE_GATES.values()):
                results["build-image-and-scan"] = "failed"
    cleanup = verification.run_private(
        ["docker", "buildx", "rm", "private-verification"],
        cwd=root,
        evidence=evidence / "builder-cleanup",
        env=child_env,
        timeout_seconds=60,
    )
    if cleanup != verification.Outcome.PASSED:
        results["build-image-and-scan"] = "failed"
    disk_sample(root, work, "after-builder-cleanup")
    require_release(root, env)
    images, expanded, expanded_hash, unpacked_size = (), 0, None, 0
    kind = transfer.PayloadKind.DIAGNOSTIC
    if set(results.values()) == {"passed"}:
        try:
            images, expanded, expanded_hash, unpacked_size = export_release(
                root, work, binding, env
            )
            kind = transfer.PayloadKind.RELEASE
        except Exception:
            transfer.write_once(
                evidence / "export-failure.log", traceback.format_exc().encode()
            )
            results["build-image-and-scan"] = "failed"
            transfer.write_once(
                evidence / "export-failure.json",
                transfer.json_bytes({"outcome": "failed"}),
            )
    transfer.write_once(
        evidence / "manifest.json",
        transfer.json_bytes(
            {
                "binding": asdict(binding),
                "gates": results,
                "receipt_is_not_independent_custody": True,
                "runtime_activation": "held",
            }
        ),
    )
    report = verification.pack_report(
        evidence, secrets=tuple(env.get(n, "").encode() for n in SECRET_NAMES)
    )
    transfer.write_once(payload / "report.bin", report)
    names = (
        ("report.bin", "receipt.json", "images.tar.gz", "runtime.tar")
        if kind == transfer.PayloadKind.RELEASE
        else ("report.bin",)
    )
    envelope = transfer.Envelope(
        binding=binding,
        kind=kind,
        files={n: transfer.FileDigest.of(payload / n) for n in names},
        gates=results,
        images=images if kind == transfer.PayloadKind.RELEASE else (),
        expanded_archive_bytes=expanded if kind == transfer.PayloadKind.RELEASE else 0,
        expanded_archive_sha256=expanded_hash
        if kind == transfer.PayloadKind.RELEASE
        else None,
        unpacked_layer_bytes=unpacked_size
        if kind == transfer.PayloadKind.RELEASE
        else 0,
    )
    transfer.write_once(work / "envelope.json", envelope.encode())
    if kind == transfer.PayloadKind.DIAGNOSTIC:
        raise transfer.TransferError("verification failed; private diagnostic ready")


def preflight(root: Path, work: Path, env: Mapping[str, str]) -> transfer.Envelope:
    binding = require_release(root, env)
    recorded = transfer.parse_json(
        transfer.metadata_bytes(work / "evidence/source-files.json")
    )
    if set(recorded) != set(SOURCE_FILES):
        raise transfer.TransferError("source fingerprint set changed")
    for name, expected_hash in recorded.items():
        if (
            not isinstance(name, str)
            or name.startswith("/")
            or ".." in Path(name).parts
        ):
            raise transfer.TransferError("invalid source fingerprint path")
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != transfer.digest(
            expected_hash
        ):
            raise transfer.TransferError("executed source changed")
    envelope = transfer.Envelope.parse(transfer.metadata_bytes(work / "envelope.json"))
    if envelope.binding != binding:
        raise transfer.TransferError("handoff binding mismatch")
    for name, expected in envelope.files.items():
        if transfer.FileDigest.of(work / "payload" / name) != expected:
            raise transfer.TransferError("handoff payload changed")
    if envelope.kind == transfer.PayloadKind.RELEASE:
        transfer.verify_receipt(envelope, work / "payload" / "receipt.json")
    return envelope


def publish_owner_receipt(result: dict, env: Mapping[str, str]) -> None:
    # Callers supply only Envelope.owner_receipt's fixed typed projection.
    with Path(env["GITHUB_STEP_SUMMARY"]).open("a") as output:
        output.write("```json\n" + transfer.json_bytes(result).decode() + "```\n")


def transfer_release(root: Path, work: Path, env: Mapping[str, str]) -> None:
    deadline = time.monotonic() + transfer.TRANSFER_WORK_SECONDS

    def expired(signum: int, frame: object) -> None:
        raise transfer.TransferError("transfer phase deadline exceeded")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(transfer.TRANSFER_WORK_SECONDS)
    try:
        envelope = preflight(root, work, env)
        timeout = transfer.sender_timeout(int(env["STAGING_TRANSFER_TIMEOUT"]))
        result = transfer.send(
            envelope,
            work / "payload",
            work,
            env,
            timeout_seconds=timeout,
            deadline=deadline,
        )
        transfer.write_once(
            work / "accepted-owner-receipt.json", transfer.json_bytes(result)
        )
        publish_owner_receipt(result, env)
        if envelope.kind == transfer.PayloadKind.DIAGNOSTIC:
            raise transfer.TransferError(
                "diagnostic received; verification remains failed"
            )
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def main() -> int:
    env = dict(os.environ)
    for name in (
        *SECRET_NAMES,
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "ACTIONS_RUNTIME_TOKEN",
        "ACTIONS_CACHE_URL",
        "ACTIONS_RESULTS_URL",
    ):
        os.environ.pop(name, None)
    saved_stdout, saved_stderr = os.dup(1), os.dup(2)
    status = 1
    captured = False
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in ("verify", "preflight", "transfer"):
            raise transfer.TransferError("unsupported phase")
        phase = sys.argv[1]
        binding = approved_binding(env)
        root = Path(env["GITHUB_WORKSPACE"]).resolve()
        os.chdir(root)
        runner_temp = Path(env["RUNNER_TEMP"])
        if not runner_temp.is_absolute():
            raise transfer.TransferError("runner temporary path must be absolute")
        work = (
            runner_temp.resolve()
            / f"staging-release-{binding.run_id}-{binding.run_attempt}"
        )
        if work == root or root in work.parents or work in root.parents:
            raise transfer.TransferError("output must be outside checkout")
        os.umask(0o077)
        if phase == "verify":
            work.mkdir(mode=0o700)
        if (
            work.is_symlink()
            or not work.is_dir()
            or work.stat().st_uid != os.getuid()
            or work.stat().st_mode & 0o777 != 0o700
        ):
            raise transfer.TransferError("private work directory missing or unsafe")
        with (work / (phase + ".log")).open("xb") as log:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            captured = True
            if phase == "verify":
                verify_release(root, work, env)
            elif phase == "preflight":
                envelope = preflight(root, work, env)
                if envelope.kind == transfer.PayloadKind.RELEASE:
                    publish_owner_receipt(envelope.owner_receipt("produced"), env)
            else:
                transfer_release(root, work, env)
        status = 0
    except (Exception, KeyboardInterrupt):
        if captured:
            traceback.print_exc()
        status = 1
    finally:
        with contextlib.suppress(OSError, ValueError):
            sys.stdout.flush()
            sys.stderr.flush()
        os.dup2(saved_stdout, 1)
        os.dup2(saved_stderr, 2)
        os.close(saved_stdout)
        os.close(saved_stderr)
    with contextlib.suppress(OSError):
        os.write(
            1,
            b"STAGING_RELEASE_PHASE_COMPLETE\n"
            if status == 0
            else b"STAGING_RELEASE_PHASE_FAILED\n",
        )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
