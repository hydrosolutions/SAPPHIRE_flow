from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "staging_release_test", ROOT / "tools/staging_release.py"
)
assert spec is not None and spec.loader is not None
collector = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = collector
spec.loader.exec_module(collector)
transfer = collector.transfer


def binding() -> transfer.ReleaseBinding:
    return transfer.ReleaseBinding(
        version="1.2.3",
        source_sha="a" * 40,
        source_tree="b" * 40,
        tag_object="c" * 40,
        release_state="d" * 40,
        workflow_sha="a" * 40,
        workflow_ref=transfer.REPOSITORY
        + "/.github/workflows/private-verification.yml@refs/tags/v1.2.3",
        run_id="123",
        run_attempt="1",
        actor="owner",
        delivery_mode=transfer.DeliveryMode.DELIVER_ONLY,
    )


def environment(tmp_path: Path) -> dict[str, str]:
    approved = binding()
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"inputs": {"delivery_mode": "deliver-only"}}))
    return {
        "STAGING_RELEASE_ADMISSION": json.dumps(asdict(approved)),
        "GITHUB_REPOSITORY": transfer.REPOSITORY,
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/tags/v1.2.3",
        "GITHUB_SHA": approved.source_sha,
        "GITHUB_WORKFLOW_SHA": approved.workflow_sha,
        "GITHUB_WORKFLOW_REF": approved.workflow_ref,
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_ACTOR": "owner",
        "GITHUB_TRIGGERING_ACTOR": "owner",
        "GITHUB_JOB": "verify",
        "GITHUB_EVENT_PATH": str(event),
        "PRIVATE_PREPARATION": json.dumps(
            dict.fromkeys(
                (
                    "checkout",
                    "uv",
                    "buildx",
                    "trivy",
                    "syft",
                    "route",
                    "engine",
                    "exporter-smoke",
                ),
                "success",
            )
        ),
        "RECAP_DG_CLIENT_TOKEN": "synthetic-source-1",
        "AQUACAST_TOKEN": "synthetic-source-2",
        "STAGING_MAX_ARCHIVE_BYTES": "1000000",
        "STAGING_MAX_EXPANDED_BYTES": "1000000",
        "STAGING_RESERVE_BYTES": "1",
        "STAGING_TRANSFER_TIMEOUT": "1500",
    }


def canonical_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    approved = binding()
    monkeypatch.setattr(collector.release, "validate_destination", lambda: None)
    monkeypatch.setattr(
        collector.release, "ensure_clean_context", lambda source: approved.source_tree
    )
    monkeypatch.setattr(collector.release, "validate_state", lambda *args: None)
    monkeypatch.setattr(collector.release, "ensure_source_on_main", lambda *args: None)
    monkeypatch.setattr(
        collector.release,
        "read_release_target",
        lambda source: collector.release.Version.parse(approved.version),
    )
    monkeypatch.setattr(
        collector.release,
        "fetch_remote_state",
        lambda: (
            approved.source_sha,
            approved.release_state,
            [
                SimpleNamespace(
                    name="v1.2.3",
                    metadata=object(),
                    object_oid=approved.tag_object,
                    peeled_oid=approved.source_sha,
                )
            ],
        ),
    )


class TestCanonicalAdmission:
    def test_exact_canonical_source_is_admitted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        canonical_remote(monkeypatch)
        assert collector.require_release(tmp_path, environment(tmp_path)) == binding()

    @pytest.mark.parametrize(
        "field,value",
        [
            ("GITHUB_EVENT_NAME", "pull_request"),
            ("GITHUB_REF", "refs/heads/main"),
            ("GITHUB_RUN_ATTEMPT", "2"),
            ("GITHUB_WORKFLOW_SHA", "e" * 40),
            ("GITHUB_ACTOR", "other"),
        ],
    )
    def test_changed_event_cannot_admit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: str
    ) -> None:
        canonical_remote(monkeypatch)
        env = environment(tmp_path)
        env[field] = value
        with pytest.raises(transfer.TransferError, match="event"):
            collector.require_release(tmp_path, env)

    @pytest.mark.parametrize("field", ["state", "tag", "source"])
    def test_remote_binding_change_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
    ) -> None:
        canonical_remote(monkeypatch)
        approved = binding()
        monkeypatch.setattr(
            collector.release,
            "fetch_remote_state",
            lambda: (
                approved.source_sha,
                "e" * 40 if field == "state" else approved.release_state,
                [
                    SimpleNamespace(
                        name="v1.2.3",
                        metadata=object(),
                        object_oid="e" * 40 if field == "tag" else approved.tag_object,
                        peeled_oid="e" * 40
                        if field == "source"
                        else approved.source_sha,
                    )
                ],
            ),
        )
        with pytest.raises(transfer.TransferError, match="changed"):
            collector.require_release(tmp_path, environment(tmp_path))

    def test_builder_environment_excludes_transfer_credentials(
        self, tmp_path: Path
    ) -> None:
        env = environment(tmp_path)
        env.update(
            STAGING_ADMIN_SSH_KEY="must-not-reach-build",
            STAGING_KNOWN_HOSTS="not-needed",
        )
        child = collector.private_env(env, binding(), tmp_path)
        assert "STAGING_ADMIN_SSH_KEY" not in child
        assert "STAGING_RELEASE_ADMISSION" not in child
        assert child["EXPECTED_VERSION"] == "1.2.3"
        assert child["RECAP_DG_CLIENT_TOKEN"] == "synthetic-source-1"


class TestReleaseBuilder:
    @pytest.mark.parametrize("failed_variant", [None, "default", "aquacast"])
    def test_each_variant_built_once_and_failures_persist(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        failed_variant: str | None,
    ) -> None:
        evidence = tmp_path / "evidence"
        evidence.mkdir()
        calls = []
        ids = {"default": "sha256:" + "a" * 64, "aquacast": "sha256:" + "b" * 64}
        results = dict.fromkeys(transfer.GATE_NAMES, "blocked")

        def gate(command: list[str], **kwargs: object) -> object:
            name = command[-1]
            variant = next(v for v, g in collector.IMAGE_GATES.items() if g == name)
            calls.append(variant)
            assert kwargs["env"]["RELEASE_IMAGE_CONTEXT"] == str(tmp_path)
            (evidence / (variant + "-image-id")).write_text(ids[variant])
            for component in ("scan", "sbom", "proof"):
                status = (
                    "failed"
                    if variant == failed_variant and component == "scan"
                    else "passed"
                )
                (evidence / (variant + "-" + component + "-result.json")).write_text(
                    json.dumps({"outcome": status})
                )
            (evidence / (variant + "-trivy.sarif")).write_text("private scan evidence")
            return (
                collector.verification.Outcome.FAILED
                if variant == failed_variant
                else collector.verification.Outcome.PASSED
            )

        monkeypatch.setattr(collector.verification, "run_private", gate)
        monkeypatch.setattr(collector, "disk_sample", lambda *args: None)
        monkeypatch.setattr(
            collector.release,
            "run_docker",
            lambda args, **kwargs: ids[
                "aquacast" if "aquacast" in args[-1] else "default"
            ],
        )

        def inspect(raw_id: str, version: str, source: str, *, variant: str) -> object:
            assert raw_id == ids[variant]
            return collector.release.ReceiptImage(
                variant=variant,
                mutable_tag=raw_id,
                image_id=raw_id,
                labels={},
                runtime_import_version=version,
            )

        monkeypatch.setattr(collector.release, "inspect_image", inspect)
        builder = collector.ReleaseBuilder(tmp_path, tmp_path, {}, results)
        if failed_variant:
            with pytest.raises(
                collector.release.ReleaseIdentityError, match="evidence retained"
            ):
                builder.build(tmp_path, "1.2.3", "a" * 40)
            assert (
                json.loads(
                    (evidence / (failed_variant + "-scan-result.json")).read_text()
                )["outcome"]
                == "failed"
            )
            assert (evidence / (failed_variant + "-trivy.sarif")).exists()
            assert results[collector.IMAGE_GATES[failed_variant]] == "failed"
        else:
            images = builder.build(tmp_path, "1.2.3", "a" * 40)
            assert {i.image_id for i in images} == set(ids.values())
        assert calls == ["default", "aquacast"]
        assert all((evidence / (v + "-gate-result.json")).exists() for v in ids)

    def test_tag_drift_cannot_make_receipt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "evidence").mkdir()

        def gate(command: list[str], **kwargs: object) -> object:
            variant = "default" if command[-1] == "build-image-and-scan" else "aquacast"
            (tmp_path / "evidence" / (variant + "-image-id")).write_text(
                "sha256:" + "a" * 64
            )
            return collector.verification.Outcome.PASSED

        monkeypatch.setattr(collector.verification, "run_private", gate)
        monkeypatch.setattr(collector, "disk_sample", lambda *args: None)
        monkeypatch.setattr(
            collector.release,
            "run_docker",
            lambda *args, **kwargs: "sha256:" + "b" * 64,
        )
        results = dict.fromkeys(transfer.GATE_NAMES, "blocked")
        with pytest.raises(
            collector.release.ReleaseIdentityError, match="gates failed"
        ):
            collector.ReleaseBuilder(tmp_path, tmp_path, {}, results).build(
                tmp_path, "1.2.3", "a" * 40
            )
        assert results["build-image-and-scan"] == "failed"
        assert (tmp_path / "evidence/default-identity-failure.log").exists()


class TestFailureHandoff:
    def test_failed_receipt_is_diagnostic_only_with_all_gate_outcomes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.setattr(
            collector, "require_release", lambda *args, **kwargs: binding()
        )
        monkeypatch.setattr(collector, "require_local_release", lambda *args: binding())
        monkeypatch.setattr(
            collector.verification,
            "run_private",
            lambda *args, **kwargs: collector.verification.Outcome.PASSED,
        )

        def fail(*args: object, **kwargs: object) -> None:
            raise collector.release.ReleaseIdentityError("synthetic receipt failure")

        monkeypatch.setattr(collector.release, "build_receipt", fail)

        def forbid(*args: object) -> None:
            pytest.fail("failed gates cannot export")

        monkeypatch.setattr(collector, "export_release", forbid)
        with pytest.raises(transfer.TransferError, match="diagnostic ready"):
            collector.verify_release(ROOT, work, environment(tmp_path))
        envelope = transfer.Envelope.parse((work / "envelope.json").read_bytes())
        assert envelope.kind == transfer.PayloadKind.DIAGNOSTIC
        assert set(envelope.files) == {"report.bin"}
        assert set(envelope.gates) == set(transfer.GATE_NAMES)
        assert (work / "evidence/receipt-failure.log").exists()

    def test_stale_binding_cannot_reach_ssh(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def deny(*args: object, **kwargs: object) -> None:
            raise transfer.TransferError("source changed")

        monkeypatch.setattr(collector, "require_release", deny)
        monkeypatch.setattr(
            transfer, "send", lambda *args, **kwargs: pytest.fail("SSH not allowed")
        )
        with pytest.raises(transfer.TransferError, match="source changed"):
            collector.transfer_release(tmp_path, tmp_path, environment(tmp_path))


class TestBootstrapCacheBoundary:
    @pytest.mark.parametrize("cache_location", ["workspace", "configured"])
    def test_real_source_guard_after_bootstrap_cleanup(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        cache_location: Literal["workspace", "configured"],
    ) -> None:
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/private-verification.yml").read_text()
        )
        bootstrap = next(
            step
            for step in workflow["jobs"]["verify"]["steps"]
            if step.get("id") == "trivy"
        )
        inputs = bootstrap["with"]
        assert inputs["cache"] == "false"
        assert inputs["cache-dir"] == (
            "${{ runner.temp }}/private-trivy-bootstrap-cache"
        )
        assert inputs["scan-ref"] == "${{ runner.temp }}/private-trivy-bootstrap"
        workspace, runner = tmp_path / "workspace", tmp_path / "runner"
        workspace.mkdir()
        runner.mkdir()
        cache = Path(inputs["cache-dir"].replace("${{ runner.temp }}", str(runner)))
        target = Path(inputs["scan-ref"].replace("${{ runner.temp }}", str(runner)))
        assert not cache.is_relative_to(workspace)
        assert not cache.is_relative_to(target)
        target.mkdir()
        monkeypatch.chdir(workspace)
        git = collector.release.git
        git(["init", "--quiet"])
        (workspace / ".gitignore").write_text((ROOT / ".gitignore").read_text())
        git(["add", ".gitignore"])
        git(
            [
                "-c",
                "user.name=Bootstrap test",
                "-c",
                "user.email=bootstrap@example.invalid",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--quiet",
                "-m",
                "Synthetic bootstrap source",
            ]
        )
        source = git(["rev-parse", "HEAD"])
        tree = collector.release.ensure_clean_context(source)
        # Model the successful pinned installer's observed transient cleanup.
        installer = workspace / "trivy"
        installer.mkdir()
        installer.rmdir()
        env_file = workspace / "trivy_envs.txt"
        env_file.write_text("synthetic bootstrap settings")
        env_file.unlink()
        if cache_location == "workspace":
            cache = workspace / ".cache" / "trivy"
        database = cache / "db" / "metadata.json"
        database.parent.mkdir(parents=True)
        database.write_text('{"synthetic": true}')
        assert list(target.iterdir()) == []
        if cache_location == "workspace":
            with pytest.raises(
                collector.release.ReleaseIdentityError,
                match="tracked or untracked build-context changes",
            ):
                collector.release.ensure_clean_context(source)
        else:
            assert collector.release.ensure_clean_context(source) == tree

    def test_private_gates_replace_bootstrap_cache(self, tmp_path: Path) -> None:
        bootstrap_cache = tmp_path / "private-trivy-bootstrap-cache"
        work = tmp_path / "staging-release-123-1"
        env = {"TRIVY_CACHE_DIR": str(bootstrap_cache)}
        child = collector.private_env(env, binding(), work)
        assert child["TRIVY_CACHE_DIR"] == str(work / "trivy-cache")
        assert child["TRIVY_CACHE_DIR"] != str(bootstrap_cache)


class TestWorkflow:
    def test_manual_tag_route_has_late_ssh_and_unchanged_package_sources(self) -> None:
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/private-verification.yml").read_text()
        )
        events = workflow.get("on", workflow.get(True))
        assert set(events) == {"workflow_dispatch"}
        assert (
            events["workflow_dispatch"]["inputs"]["delivery_mode"]["default"]
            == "deliver-only"
        )
        job = workflow["jobs"]["verify"]
        assert job["environment"] == transfer.ENVIRONMENT
        steps = job["steps"]
        build = next(s for s in steps if s.get("id") == "verification")
        final = steps[-1]
        assert "SSH" not in json.dumps(build)
        assert (
            build["env"]["RECAP_DG_CLIENT_TOKEN"]
            == "${{ secrets.RECAP_DG_CLIENT_TOKEN }}"
        )
        assert build["env"]["AQUACAST_TOKEN"] == "${{ secrets.AQUACAST_TOKEN }}"
        assert "steps.transfer_admission.outcome == 'success'" in final["if"]
        assert (
            final["env"]["STAGING_ADMIN_SSH_KEY"]
            == "${{ secrets.STAGING_ADMIN_SSH_KEY }}"
        )
        assert sum("STAGING_ADMIN_SSH_KEY" in json.dumps(s) for s in steps) == 1
        checkout = next(s for s in steps if s.get("id") == "checkout")
        assert checkout["with"]["fetch-depth"] == 0
        assert checkout["with"]["persist-credentials"] is False
        assert any(
            s.get("run")
            == "git remote set-url origin https://github.com/hydrosolutions/SAPPHIRE_flow.git"
            for s in steps
        )


class TestPrivatePhaseCLI:
    @pytest.mark.parametrize("phase", ["verify", "preflight", "transfer"])
    def test_real_descriptor_capture_hides_failures(
        self, tmp_path: Path, phase: str
    ) -> None:
        import os
        import subprocess

        root, runner = tmp_path / "workspace", tmp_path / "runner"
        root.mkdir()
        runner.mkdir()
        work = runner / "staging-release-123-1"
        if phase != "verify":
            work.mkdir(mode=0o700)
        failure = (
            "def fail(*args, **kwargs):\n"
            " print('PRIVATE_PHASE_CANARY')\n"
            " print('PRIVATE_PHASE_CANARY',file=sys.stderr)\n"
            " raise ValueError('PRIVATE_PHASE_CANARY')\n"
        )
        tool_path = str(ROOT / "tools/staging_release.py")
        code = (
            "import importlib.util,sys; "
            "s=importlib.util.spec_from_file_location('cli_test',"
            f"{tool_path!r}); "
            "m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; "
            "s.loader.exec_module(m); "
            f"exec({failure!r}); "
            "m.verify_release=fail; m.preflight=fail; m.transfer_release=fail; "
            f"sys.argv=['staging_release.py',{phase!r}]; sys.exit(m.main())"
        )
        env = {
            **os.environ,
            "GITHUB_WORKSPACE": str(root),
            "RUNNER_TEMP": str(runner),
            "STAGING_RELEASE_ADMISSION": json.dumps(asdict(binding())),
        }
        result = subprocess.run(
            [sys.executable, "-I", "-c", code],
            env=env,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 1
        assert (
            result.stdout
            == (
                f"STAGING_RELEASE_CHECKPOINT {phase.upper()} WORK_SETUP ENTER\n"
                f"STAGING_RELEASE_CHECKPOINT {phase.upper()} WORK_SETUP PASS\n"
                "STAGING_RELEASE_PHASE_FAILED\n"
            ).encode()
        )
        assert result.stderr == b""
        assert "PRIVATE_PHASE_CANARY" in (work / (phase + ".log")).read_text()
        assert work.stat().st_mode & 0o777 == 0o700


class TestRealRuntimeBundle:
    def test_runtime_files_match_actual_committed_objects(self, tmp_path: Path) -> None:
        import subprocess
        import tarfile
        from dataclasses import replace

        source = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        approved = replace(binding(), source_sha=source, workflow_sha=source)
        output = tmp_path / "runtime.tar"
        collector.runtime_bundle(ROOT, approved, output)
        with tarfile.open(output) as archive:
            assert set(archive.getnames()) == set(transfer.RUNTIME_FILES)
            for name in transfer.RUNTIME_FILES:
                expected = subprocess.check_output(
                    ["git", "show", source + ":" + name], cwd=ROOT
                )
                assert archive.extractfile(name).read() == expected
        transfer.validate_runtime_bundle(output)


class TestExportFailureEvidence:
    def test_docker_save_error_enters_private_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import io
        import os
        import tarfile

        binaries = tmp_path / "bin"
        binaries.mkdir()
        docker = binaries / "docker"
        docker.write_text(
            "#!/bin/sh\nprintf 'SYNTHETIC_DOCKER_SAVE_CAUSE\n' >&2\nexit 9\n"
        )
        docker.chmod(0o700)
        monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ["PATH"])
        evidence = tmp_path / "evidence"
        evidence.mkdir(mode=0o700)
        with pytest.raises(transfer.TransferError, match="export failed"):
            collector.archive_images(
                ["sha256:" + "a" * 64],
                tmp_path / "images.tar",
                1024,
                evidence / "export-save.log",
            )
        report = collector.verification.pack_report(evidence, secrets=())
        with tarfile.open(fileobj=io.BytesIO(report), mode="r:gz") as archive:
            assert (
                b"SYNTHETIC_DOCKER_SAVE_CAUSE"
                in archive.extractfile("export-save.log").read()
            )


class TestTransferStepBudget:
    def test_workflow_budget_has_not_been_extended(self) -> None:
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/private-verification.yml").read_text()
        )
        job = workflow["jobs"]["verify"]
        assert job["timeout-minutes"] == 180
        assert (
            job["steps"][-1]["timeout-minutes"] * 60 == transfer.TRANSFER_STEP_SECONDS
        )
        assert (
            transfer.TRANSFER_WORK_SECONDS + transfer.TRANSFER_CLEANUP_SECONDS
            == transfer.TRANSFER_STEP_SECONDS
        )


class TestPreflightBudgetAccounting:
    def test_preflight_cost_is_not_reset_before_sender(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = [0.0]
        monkeypatch.setattr(collector.time, "monotonic", lambda: now[0])

        def preflight(*args: object, **kwargs: object) -> object:
            now[0] = 500.0
            return SimpleNamespace(kind=transfer.PayloadKind.RELEASE)

        def send(*args: object, timeout_seconds: int, deadline: float) -> dict:
            assert deadline == transfer.TRANSFER_WORK_SECONDS
            transfer.remaining_ssh_budget(timeout_seconds, deadline, now[0])
            pytest.fail("slow preflight cannot admit SSH")

        monkeypatch.setattr(collector, "preflight", preflight)
        monkeypatch.setattr(transfer, "send", send)
        with pytest.raises(transfer.TransferError, match="before SSH"):
            collector.transfer_release(
                tmp_path, tmp_path, {"STAGING_TRANSFER_TIMEOUT": "1500"}
            )


def checkpoint_cli(
    tmp_path: Path, phase: str, setup: str
) -> tuple[subprocess.CompletedProcess[bytes], Path]:
    root, runner = tmp_path / "workspace", tmp_path / "runner"
    root.mkdir()
    runner.mkdir()
    work = runner / "staging-release-123-1"
    if phase != "verify":
        work.mkdir(mode=0o700)
    env = {
        **environment(tmp_path),
        "GITHUB_WORKSPACE": str(root),
        "RUNNER_TEMP": str(runner),
    }
    code = (
        "import importlib.util,sys,os\n"
        "from pathlib import Path\n"
        "from types import SimpleNamespace\n"
        "s=importlib.util.spec_from_file_location('checkpoint_cli',"
        f"{str(ROOT / 'tools/staging_release.py')!r})\n"
        "m=importlib.util.module_from_spec(s); sys.modules[s.name]=m\n"
        "s.loader.exec_module(m)\n"
        + setup
        + f"\nsys.argv=['staging_release.py',{phase!r}]; sys.exit(m.main())\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        env=env,
        capture_output=True,
        timeout=10,
        check=False,
    )
    return result, work


class TestFixedCheckpoints:
    def test_complete_literal_allowlist(self, tmp_path: Path) -> None:
        import re

        marker = re.compile(
            rb"STAGING_RELEASE_CHECKPOINT (UNKNOWN|VERIFY|PREFLIGHT|TRANSFER) "
            rb"(WORK_SETUP|ADMISSION_CHECK|LOCAL_SOURCE_CHECK|REMOTE_RELEASE_SNAPSHOT|"
            rb"REMOTE_RELEASE_CHECK|PAYLOAD_PREPARATION|PAYLOAD_CHECK) "
            rb"(ENTER|PASS|FAIL)\n"
        )
        path = tmp_path / "markers"
        with path.open("wb") as stream:
            for phase in collector.Phase:
                progress = collector.Progress(phase=phase, fd=stream.fileno())
                for stage in collector.Checkpoint:
                    for outcome in collector.MarkerOutcome:
                        progress.emit(stage, outcome)
        lines = path.read_bytes().splitlines(keepends=True)
        assert len(set(lines)) == 4 * 7 * 3
        assert all(marker.fullmatch(line) for line in lines)

    @pytest.mark.parametrize(
        "failure", [ValueError("private cause"), KeyboardInterrupt()]
    )
    def test_failure_preserves_original_exception(
        self, tmp_path: Path, failure: BaseException
    ) -> None:
        path = tmp_path / "markers"
        with path.open("wb") as stream:
            progress = collector.Progress(
                phase=collector.Phase.VERIFY, fd=stream.fileno()
            )
            with (
                pytest.raises(type(failure)) as caught,
                collector.checkpoint(progress, collector.Checkpoint.LOCAL_SOURCE_CHECK),
            ):
                raise failure
        assert caught.value is failure
        assert path.read_text().splitlines() == [
            "STAGING_RELEASE_CHECKPOINT VERIFY LOCAL_SOURCE_CHECK ENTER",
            "STAGING_RELEASE_CHECKPOINT VERIFY LOCAL_SOURCE_CHECK FAIL",
        ]

    def test_closed_sink_does_not_change_outcome(self) -> None:
        progress = collector.Progress(phase=collector.Phase.VERIFY, fd=-1)
        with collector.checkpoint(progress, collector.Checkpoint.WORK_SETUP):
            pass
        failure = ValueError("synthetic operation failure")
        with (
            pytest.raises(ValueError, match="synthetic operation failure") as caught,
            collector.checkpoint(progress, collector.Checkpoint.WORK_SETUP),
        ):
            raise failure
        assert caught.value is failure

    def test_disabled_reporter_does_not_write_to_reused_sink(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "markers"
        with path.open("wb") as stream:
            progress = collector.Progress(
                phase=collector.Phase.VERIFY, fd=stream.fileno()
            )
            progress.disable()
            progress.emit(
                collector.Checkpoint.WORK_SETUP, collector.MarkerOutcome.ENTER
            )
        assert path.read_bytes() == b""

    @pytest.mark.parametrize(
        "stage,dependency",
        [
            ("ADMISSION_CHECK", "approved_binding"),
            ("LOCAL_SOURCE_CHECK", "ensure_clean_context"),
            ("REMOTE_RELEASE_SNAPSHOT", "fetch_remote_state"),
            ("REMOTE_RELEASE_CHECK", "validate_state"),
        ],
    )
    def test_actual_admission_boundaries_preserve_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        stage: Literal[
            "ADMISSION_CHECK",
            "LOCAL_SOURCE_CHECK",
            "REMOTE_RELEASE_SNAPSHOT",
            "REMOTE_RELEASE_CHECK",
        ],
        dependency: Literal[
            "approved_binding",
            "ensure_clean_context",
            "fetch_remote_state",
            "validate_state",
        ],
    ) -> None:
        canonical_remote(monkeypatch)
        failure = ValueError("synthetic private failure")

        def fail(*args: object, **kwargs: object) -> None:
            raise failure

        target = collector if dependency == "approved_binding" else collector.release
        monkeypatch.setattr(target, dependency, fail)
        path = tmp_path / "markers"
        with path.open("wb") as stream:
            progress = collector.Progress(
                phase=collector.Phase.PREFLIGHT, fd=stream.fileno()
            )
            with pytest.raises(ValueError, match="synthetic private failure") as caught:
                collector.require_release(
                    tmp_path, environment(tmp_path), progress=progress
                )
        assert caught.value is failure
        assert path.read_text().splitlines()[-2:] == [
            f"STAGING_RELEASE_CHECKPOINT PREFLIGHT {stage} ENTER",
            f"STAGING_RELEASE_CHECKPOINT PREFLIGHT {stage} FAIL",
        ]
        assert "synthetic private failure" not in path.read_text()

    def test_payload_check_reports_only_fixed_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        canonical_remote(monkeypatch)
        path = tmp_path / "markers"
        with path.open("wb") as stream:
            progress = collector.Progress(
                phase=collector.Phase.PREFLIGHT, fd=stream.fileno()
            )
            with pytest.raises(FileNotFoundError, match="source-files.json"):
                collector.preflight(
                    tmp_path, tmp_path, environment(tmp_path), progress=progress
                )
        assert path.read_text().splitlines()[-2:] == [
            "STAGING_RELEASE_CHECKPOINT PREFLIGHT PAYLOAD_CHECK ENTER",
            "STAGING_RELEASE_CHECKPOINT PREFLIGHT PAYLOAD_CHECK FAIL",
        ]
        assert "source-files.json" not in path.read_text()

    @pytest.mark.parametrize(
        "path_kind",
        ["not-ready", "blocked", "receipt-failure", "caught-source-failure"],
    )
    def test_payload_cadence_is_independent_of_private_branches(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        path_kind: Literal[
            "not-ready", "blocked", "receipt-failure", "caught-source-failure"
        ],
    ) -> None:
        canonical_remote(monkeypatch)
        env = environment(tmp_path)
        if path_kind == "not-ready":
            env["AQUACAST_TOKEN"] = ""
        work = tmp_path / "work"
        work.mkdir()
        gate_calls: list[str] = []

        def gate(command: list[str], **kwargs: object) -> object:
            gate_calls.append(command[-1])
            return (
                collector.verification.Outcome.FAILED
                if path_kind == "blocked"
                else collector.verification.Outcome.PASSED
            )

        def receipt(*args: object, **kwargs: object) -> None:
            raise ValueError("synthetic receipt detail")

        clean_calls = 0

        def clean(source: str) -> str:
            nonlocal clean_calls
            clean_calls += 1
            pre_receipt = 2 + sum(
                gate.name not in collector.IMAGE_GATES.values()
                for gate in collector.verification.GATES
            )
            if path_kind == "caught-source-failure" and clean_calls == pre_receipt:
                raise ValueError("synthetic source detail")
            return binding().source_tree

        monkeypatch.setattr(collector.release, "ensure_clean_context", clean)
        monkeypatch.setattr(collector.release, "build_receipt", receipt)
        monkeypatch.setattr(collector.verification, "run_private", gate)
        monkeypatch.setattr(collector, "disk_sample", lambda *args: None)
        path = tmp_path / "markers"
        with path.open("wb") as stream:
            progress = collector.Progress(
                phase=collector.Phase.VERIFY, fd=stream.fileno()
            )
            with pytest.raises(transfer.TransferError, match="diagnostic ready"):
                collector.verify_release(ROOT, work, env, progress=progress)
        expected = [
            f"STAGING_RELEASE_CHECKPOINT VERIFY {stage} {outcome}"
            for stage in (
                "ADMISSION_CHECK",
                "LOCAL_SOURCE_CHECK",
                "REMOTE_RELEASE_SNAPSHOT",
                "REMOTE_RELEASE_CHECK",
            )
            for outcome in ("ENTER", "PASS")
        ] + [
            "STAGING_RELEASE_CHECKPOINT VERIFY PAYLOAD_PREPARATION ENTER",
            "STAGING_RELEASE_CHECKPOINT VERIFY PAYLOAD_PREPARATION FAIL",
        ]
        assert path.read_text().splitlines() == expected
        assert clean_calls > 2
        if path_kind == "not-ready":
            assert gate_calls == ["private-verification"]
        else:
            assert len(gate_calls) > 1
        if path_kind == "caught-source-failure":
            assert (
                "synthetic source detail"
                in (work / "evidence/receipt-failure.log").read_text()
            )


class TestCheckpointCLI:
    @pytest.mark.parametrize(
        "failure", ["unsupported", "log-open", "dup-stdout", "dup-stderr"]
    )
    def test_setup_failure_never_dispatches_or_emits_pass(
        self,
        tmp_path: Path,
        failure: Literal["unsupported", "log-open", "dup-stdout", "dup-stderr"],
    ) -> None:
        phase = "PRIVATE_ARGUMENT_CANARY" if failure == "unsupported" else "preflight"
        setup = (
            "def forbidden(*args, **kwargs):\n"
            " raise AssertionError('PHASE_DISPATCH_CANARY')\n"
            "m.preflight=forbidden\n"
        )
        if failure == "log-open":
            setup += (
                "(Path(os.environ['RUNNER_TEMP']) / 'staging-release-123-1' / "
                "'preflight.log').write_text('existing capture')\n"
            )
        if failure.startswith("dup-"):
            destination = 1 if failure == "dup-stdout" else 2
            setup += (
                "original_dup2=m.os.dup2\n"
                "def fail_once(fd, target):\n"
                f" if target == {destination}:\n"
                "  m.os.dup2=original_dup2\n"
                "  raise OSError('PRIVATE_SETUP_CANARY')\n"
                " return original_dup2(fd, target)\n"
                "m.os.dup2=fail_once\n"
            )
        result, _ = checkpoint_cli(tmp_path, phase, setup)
        expected_phase = "UNKNOWN" if failure == "unsupported" else "PREFLIGHT"
        assert result.returncode == 1
        assert result.stderr == b""
        assert result.stdout.decode().splitlines() == [
            f"STAGING_RELEASE_CHECKPOINT {expected_phase} WORK_SETUP ENTER",
            f"STAGING_RELEASE_CHECKPOINT {expected_phase} WORK_SETUP FAIL",
            "STAGING_RELEASE_PHASE_FAILED",
        ]

    @pytest.mark.parametrize(
        "failure",
        ["none", "admission", "local", "snapshot", "remote", "payload"],
    )
    def test_real_capture_and_top_level_markers(
        self,
        tmp_path: Path,
        failure: Literal["none", "admission", "local", "snapshot", "remote", "payload"],
    ) -> None:
        setup = (
            "approved=m.approved_binding(os.environ)\n"
            "m.release.validate_destination=lambda: None\n"
            "m.release.ensure_clean_context=lambda source: approved.source_tree\n"
            "m.release.fetch_remote_state=lambda: (approved.source_sha, "
            "approved.release_state, [SimpleNamespace(name='v1.2.3', "
            "metadata=object(), "
            "object_oid=approved.tag_object, peeled_oid=approved.source_sha)])\n"
            "m.release.validate_state=lambda *args: None\n"
            "m.release.ensure_source_on_main=lambda *args: None\n"
            "m.release.read_release_target=lambda source: "
            "m.release.Version.parse(approved.version)\n"
            "def private_output(*args):\n"
            " print('PRIVATE_STDOUT_CANARY')\n"
            " print('PRIVATE_STDERR_CANARY', file=sys.stderr)\n"
            "def fail(*args):\n"
            " private_output()\n"
            " raise ValueError('PRIVATE_EXCEPTION_CANARY')\n"
            "m._prepare_payload=private_output\n"
        )
        targets = {
            "admission": "m.transfer.metadata_bytes",
            "local": "m.release.ensure_clean_context",
            "snapshot": "m.release.fetch_remote_state",
            "remote": "m.release.validate_state",
            "payload": "m._prepare_payload",
        }
        if failure != "none":
            setup += targets[failure] + "=fail\n"
        result, work = checkpoint_cli(tmp_path, "verify", setup)
        stages = [
            "WORK_SETUP",
            "ADMISSION_CHECK",
            "LOCAL_SOURCE_CHECK",
            "REMOTE_RELEASE_SNAPSHOT",
            "REMOTE_RELEASE_CHECK",
            "PAYLOAD_PREPARATION",
        ]
        fail_index = {
            "admission": 1,
            "local": 2,
            "snapshot": 3,
            "remote": 4,
            "payload": 5,
        }.get(failure)
        expected = []
        for index, stage in enumerate(stages):
            expected.append(f"STAGING_RELEASE_CHECKPOINT VERIFY {stage} ENTER")
            outcome = "FAIL" if index == fail_index else "PASS"
            expected.append(f"STAGING_RELEASE_CHECKPOINT VERIFY {stage} {outcome}")
            if index == fail_index:
                break
        expected.append(
            "STAGING_RELEASE_PHASE_COMPLETE"
            if failure == "none"
            else "STAGING_RELEASE_PHASE_FAILED"
        )
        assert result.returncode == (0 if failure == "none" else 1)
        assert result.stderr == b""
        assert result.stdout.decode().splitlines() == expected
        private_log = (work / "verify.log").read_text()
        assert "PRIVATE_STDOUT_CANARY" in private_log
        assert "PRIVATE_STDERR_CANARY" in private_log
        if failure != "none":
            assert "PRIVATE_EXCEPTION_CANARY" in private_log

    def test_transfer_failure_after_payload_pass_is_not_delivery_proof(
        self, tmp_path: Path
    ) -> None:
        setup = (
            "def preflight(*args, progress=None):\n"
            " with m.checkpoint(progress, m.Checkpoint.PAYLOAD_CHECK):\n"
            "  return SimpleNamespace(kind=m.transfer.PayloadKind.RELEASE)\n"
            "def send(*args, **kwargs):\n"
            " raise ValueError('PRIVATE_SEND_CANARY')\n"
            "m.preflight=preflight\n"
            "m.transfer.send=send\n"
        )
        result, work = checkpoint_cli(tmp_path, "transfer", setup)
        assert result.returncode == 1
        assert result.stderr == b""
        assert result.stdout.decode().splitlines() == [
            "STAGING_RELEASE_CHECKPOINT TRANSFER WORK_SETUP ENTER",
            "STAGING_RELEASE_CHECKPOINT TRANSFER WORK_SETUP PASS",
            "STAGING_RELEASE_CHECKPOINT TRANSFER PAYLOAD_CHECK ENTER",
            "STAGING_RELEASE_CHECKPOINT TRANSFER PAYLOAD_CHECK PASS",
            "STAGING_RELEASE_PHASE_FAILED",
        ]
        assert "PRIVATE_SEND_CANARY" in (work / "transfer.log").read_text()


class TestSmokeOnlyIsNotDeliveryAdmission:
    def test_smoke_value_cannot_enter_release_binding(self) -> None:
        raw = asdict(binding())
        raw["delivery_mode"] = "exporter-smoke-only"
        with pytest.raises(ValueError, match="not a valid DeliveryMode"):
            transfer.ReleaseBinding.parse(raw)

    @pytest.mark.parametrize(
        "selected", ["exporter-smoke-only", "DELIVER-ONLY", "deliver-only "]
    )
    def test_direct_controller_still_requires_exact_private_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: str
    ) -> None:
        canonical_remote(monkeypatch)
        env = environment(tmp_path)
        Path(env["GITHUB_EVENT_PATH"]).write_text(
            json.dumps({"inputs": {"delivery_mode": selected}})
        )
        with pytest.raises(transfer.TransferError, match="event does not match"):
            collector.require_local_release(tmp_path, env)

    @pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), 601])
    def test_export_timeout_override_cannot_expand_production_budget(
        self, tmp_path: Path, timeout: float
    ) -> None:
        with pytest.raises(transfer.TransferError, match="invalid export timeout"):
            collector.archive_images(
                ["sha256:" + "a" * 64],
                tmp_path / "images.tar",
                1024,
                tmp_path / "save.log",
                timeout_seconds=timeout,
            )
        assert not (tmp_path / "images.tar").exists()


class TestSaveObserver:
    def install_fake_save(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[object, list[tuple], list[object]]:
        receiver = collector.transfer.tool("receive_build_report")
        killed: list[tuple] = []
        waits: list[object] = []

        class Pipe:
            def fileno(self) -> int:
                return 42

        class Process:
            pid = 424242
            stdout = Pipe()

            def wait(self, timeout: float | None = None) -> int:
                waits.append(timeout)
                return 0

        def spawn(args: list[str], **kwargs: object) -> object:
            assert args == ["docker", "image", "save", "sha256:" + "a" * 64]
            assert kwargs["start_new_session"] is True
            kwargs["stderr"].write(b"SYNTHETIC_SAVE_STDERR\n")
            return Process()

        monkeypatch.setattr(collector.subprocess, "Popen", spawn)
        monkeypatch.setattr(collector.os, "set_blocking", lambda *args: None)
        monkeypatch.setattr(collector.os, "killpg", lambda *args: killed.append(args))
        return receiver, killed, waits

    def test_default_wait_and_deadline_are_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        receiver, killed, waits = self.install_fake_save(monkeypatch)
        observed = {}

        class Stream:
            def __init__(self, **kwargs: object) -> None:
                observed.update(kwargs)
                self.parts = iter((b"bytes", b""))

            def read(self, size: int) -> bytes:
                return next(self.parts)

        monkeypatch.setattr(receiver, "InputStream", Stream)
        monkeypatch.setattr(collector.time, "monotonic", lambda: 100.0)
        collector.archive_images(
            ["sha256:" + "a" * 64], tmp_path / "images.tar", 5, tmp_path / "save.log"
        )
        assert observed["wait"] is receiver.wait_ready
        assert observed["deadline"] == 700.0
        assert waits == [30, None]
        assert killed == [(424242, collector.signal.SIGKILL)]
        assert (tmp_path / "images.tar").read_bytes() == b"bytes"

    def test_stalled_output_observer_aborts_and_preserves_capture(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        receiver, killed, waits = self.install_fake_save(monkeypatch)
        now = [100.0]
        calls: list[float] = []

        def wait(fd: int, event: int, seconds: float) -> bool:
            assert seconds <= 2
            now[0] += seconds
            return False

        def observe() -> None:
            calls.append(now[0])
            if len(calls) == 2:
                raise ValueError("synthetic observed growth stop")

        monkeypatch.setattr(receiver, "wait_ready", wait)
        monkeypatch.setattr(collector.time, "monotonic", lambda: now[0])
        with pytest.raises(ValueError, match="observed growth stop"):
            collector.archive_images(
                ["sha256:" + "a" * 64],
                tmp_path / "images.tar",
                10,
                tmp_path / "save.log",
                timeout_seconds=10,
                observer=observe,
            )
        assert calls == [100.0, 102.0]
        assert killed == [(424242, collector.signal.SIGKILL)]
        assert waits == [None]
        assert (tmp_path / "save.log").read_bytes() == b"SYNTHETIC_SAVE_STDERR\n"

    def test_observer_cannot_extend_deadline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        receiver, killed, waits = self.install_fake_save(monkeypatch)
        now = [100.0]

        def observe() -> None:
            now[0] += 3

        monkeypatch.setattr(collector.time, "monotonic", lambda: now[0])
        monkeypatch.setattr(
            receiver,
            "wait_ready",
            lambda *args: pytest.fail("wait after expired deadline"),
        )
        with pytest.raises(receiver.ReportRejectedError, match="deadline exceeded"):
            collector.archive_images(
                ["sha256:" + "a" * 64],
                tmp_path / "images.tar",
                10,
                tmp_path / "save.log",
                timeout_seconds=2,
                observer=observe,
            )
        assert killed == [(424242, collector.signal.SIGKILL)]
        assert waits == [None]

    def test_observer_runs_during_sustained_output_and_byte_cap_still_holds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        receiver, killed, _ = self.install_fake_save(monkeypatch)
        calls: list[str] = []
        monkeypatch.setattr(receiver, "wait_ready", lambda *args: True)
        monkeypatch.setattr(collector.time, "monotonic", lambda: 100.0)
        chunks = iter((b"aaa", b"bbb", b""))
        monkeypatch.setattr(collector.os, "read", lambda *args: next(chunks))
        with pytest.raises(transfer.TransferError, match="export exceeds capacity"):
            collector.archive_images(
                ["sha256:" + "a" * 64],
                tmp_path / "images.tar",
                5,
                tmp_path / "save.log",
                observer=lambda: calls.append("sample"),
            )
        assert calls == ["sample", "sample"]
        assert (tmp_path / "images.tar").read_bytes() == b"aaa"
        assert killed == [(424242, collector.signal.SIGKILL)]
