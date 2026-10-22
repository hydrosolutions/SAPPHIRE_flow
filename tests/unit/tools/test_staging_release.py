from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

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
            dict.fromkeys(("checkout", "uv", "buildx", "trivy", "syft"), "success")
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
        monkeypatch.setattr(collector, "require_release", lambda *args: binding())
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
        def deny(*args: object) -> None:
            raise transfer.TransferError("source changed")

        monkeypatch.setattr(collector, "require_release", deny)
        monkeypatch.setattr(
            transfer, "send", lambda *args, **kwargs: pytest.fail("SSH not allowed")
        )
        with pytest.raises(transfer.TransferError, match="source changed"):
            collector.transfer_release(tmp_path, tmp_path, environment(tmp_path))


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
            "def fail(*args):\n"
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
        assert result.stdout == b"STAGING_RELEASE_PHASE_FAILED\n"
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

        def preflight(*args: object) -> object:
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
