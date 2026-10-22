from __future__ import annotations

import gzip
import importlib.util
import io
import json
import os
import sys
import tarfile
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.private_verification import Binding

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "private_verification", ROOT / "tools/private_verification.py"
)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verifier
SPEC.loader.exec_module(verifier)


@pytest.fixture
def binding() -> Binding:
    return verifier.Binding(
        repository=verifier.REPOSITORY,
        event="pull_request",
        ref="refs/pull/1/merge",
        source_sha="a" * 40,
        source_tree="b" * 40,
        base_sha="c" * 40,
        head_sha="d" * 40,
        workflow_ref=verifier.REPOSITORY
        + "/.github/workflows/private-verification.yml@refs/pull/1/merge",
        workflow_sha="a" * 40,
        run_id="1",
        run_attempt="1",
        actor="mabesa",
        triggering_actor="mabesa",
        job="verify",
        environment=verifier.ENVIRONMENT,
    )


class TestAdmission:
    def test_exact_binding(self, binding: Binding) -> None:
        verifier.require_admission(json.dumps(asdict(binding)), binding)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("run_attempt", "2"),
            ("run_id", "2"),
            ("source_sha", "e" * 40),
            ("head_sha", "e" * 40),
            ("base_sha", "e" * 40),
            ("source_tree", "e" * 40),
            ("workflow_sha", "e" * 40),
            ("ref", "refs/pull/2/merge"),
        ],
    )
    def test_changed_binding_rejected(
        self, binding: Binding, field: str, value: str
    ) -> None:
        with pytest.raises(verifier.VerificationError, match="does not match"):
            verifier.require_admission(
                json.dumps(asdict(binding)), replace(binding, **{field: value})
            )

    @pytest.mark.parametrize("raw", ["", "{}", "null", '{"run_id":"1","run_id":"1"}'])
    def test_missing_or_malformed_admission(self, binding: Binding, raw: str) -> None:
        with pytest.raises(verifier.VerificationError, match="admission"):
            verifier.require_admission(raw, binding)


class TestPrivateCapture:
    @pytest.mark.parametrize("stage", ["dependency", "test", "scan"])
    def test_canary_failure_is_only_in_private_evidence(
        self, tmp_path: Path, capfd: pytest.CaptureFixture[str], stage: str
    ) -> None:
        evidence = tmp_path / stage
        public_summary = tmp_path / "public-summary"
        script = (
            "import os,sys; print('PRIVATE_CANARY'); "
            "print('PRIVATE_CANARY',file=sys.stderr); "
            "open(os.environ['GITHUB_STEP_SUMMARY'],'w').write('PRIVATE_CANARY'); "
            "sys.exit(1)"
        )
        result = verifier.run_private(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            evidence=evidence,
            env={**os.environ, "GITHUB_STEP_SUMMARY": str(public_summary)},
            timeout_seconds=10,
        )
        assert result == verifier.Outcome.FAILED
        assert "PRIVATE_CANARY" in (evidence / "command.log").read_text()
        assert "PRIVATE_CANARY" in (evidence / "github_step_summary").read_text()
        assert not public_summary.exists()
        assert "PRIVATE_CANARY" not in str(capfd.readouterr())


class TestFinish:
    def test_delivery_failure_never_passes(
        self, binding: Binding, tmp_path: Path
    ) -> None:
        def fail_delivery(payload: bytes) -> None:
            raise verifier.VerificationError("delivery failed")

        with pytest.raises(verifier.VerificationError, match="delivery failed"):
            verifier.finish_verification(
                binding=binding,
                admission=json.dumps(asdict(binding)),
                results={g.name: verifier.Outcome.PASSED for g in verifier.GATES},
                evidence=tmp_path,
                secrets=(),
                deliver=fail_delivery,
            )

    @pytest.mark.parametrize("mode", ["missing", "failed", "blocked", "extra"])
    def test_incomplete_gate_is_delivered_but_never_passes(
        self, binding: Binding, tmp_path: Path, mode: str
    ) -> None:
        results = {g.name: verifier.Outcome.PASSED for g in verifier.GATES}
        if mode == "missing":
            results.pop("lint")
        elif mode == "extra":
            results["unknown"] = verifier.Outcome.PASSED
        else:
            results["lint"] = verifier.Outcome(mode)
        payloads: list[bytes] = []
        with pytest.raises(verifier.VerificationError, match="incomplete"):
            verifier.finish_verification(
                binding=binding,
                admission=json.dumps(asdict(binding)),
                results=results,
                evidence=tmp_path,
                secrets=(),
                deliver=payloads.append,
            )
        assert len(payloads) == 1
        payload = payloads[0]
        assert int.from_bytes(payload[:8], "big") == len(payload[8:])

    def test_redacts_credentials_not_private_canary(self, tmp_path: Path) -> None:
        (tmp_path / "log").write_text("secret-token PRIVATE_CANARY")
        data = verifier.pack_report(tmp_path, secrets=(b"secret-token",))
        with tarfile.open(fileobj=io.BytesIO(gzip.decompress(data))) as report:
            member = report.extractfile("log")
            assert member is not None
            assert member.read() == b"[REDACTED] PRIVATE_CANARY"


class TestReportSafety:
    def test_symlink_evidence_fails(self, tmp_path: Path) -> None:
        (tmp_path / "link").symlink_to("elsewhere")
        with pytest.raises(verifier.VerificationError, match="nonregular"):
            verifier.pack_report(tmp_path, secrets=())

    def test_oversize_is_not_truncated_into_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(verifier, "MAX_REPORT_BYTES", 5)
        (tmp_path / "log").write_bytes(b"123456")
        with pytest.raises(verifier.VerificationError, match="exceeds"):
            verifier.pack_report(tmp_path, secrets=())

    def test_failed_admission_never_calls_delivery(
        self, binding: Binding, tmp_path: Path
    ) -> None:
        frames: list[bytes] = []
        with pytest.raises(verifier.VerificationError, match="admission"):
            verifier.finish_verification(
                binding=binding,
                admission="{}",
                results={},
                evidence=tmp_path,
                secrets=(),
                deliver=frames.append,
            )
        assert frames == []


class TestSSHDelivery:
    @pytest.mark.parametrize(
        "status,ack",
        [
            (0, b"REPORT_RECEIVED\n"),
            (1, b"REPORT_RECEIVED\n"),
            (0, b"REPORT_RECEIVED"),
            (0, b"PRIVATE_CANARY"),
            (2, b"REPORT_BUSY\n"),
        ],
    )
    def test_only_exact_acknowledgement_succeeds(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capfd: pytest.CaptureFixture[str],
        status: int,
        ack: bytes,
    ) -> None:
        import subprocess

        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_run(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            calls.append((command, kwargs))
            assert (tmp_path / "report-key").stat().st_mode & 0o777 == 0o600
            return subprocess.CompletedProcess(command, status, ack, b"PRIVATE_CANARY")

        monkeypatch.setattr(verifier.subprocess, "run", fake_run)
        env = {
            "PRIVATE_REPORT_TARGET": "report@example.invalid",
            "PRIVATE_REPORT_SSH_KEY": "synthetic-key",
            "PRIVATE_REPORT_KNOWN_HOSTS": "synthetic-pinned-host",
            "PATH": os.defpath,
        }
        if status == 0 and ack == b"REPORT_RECEIVED\n":
            verifier.deliver_ssh(b"framed-report", work=tmp_path, env=env)
        else:
            with pytest.raises(verifier.VerificationError, match="not acknowledged"):
                verifier.deliver_ssh(b"framed-report", work=tmp_path, env=env)
        command, kwargs = calls[0]
        assert command[-1] == "upload-build-report"
        assert "-oStrictHostKeyChecking=yes" in command
        assert "-oIdentityAgent=none" in command
        assert kwargs["input"] == b"framed-report"
        assert not (tmp_path / "report-key").exists()
        assert not (tmp_path / "known-hosts").exists()
        assert "PRIVATE_CANARY" not in str(capfd.readouterr())

    def test_invalid_target_fails_before_transport(self, tmp_path: Path) -> None:
        with pytest.raises(verifier.VerificationError, match="invalid report target"):
            verifier.deliver_ssh(
                b"data",
                work=tmp_path,
                env={"PRIVATE_REPORT_TARGET": "-oProxyCommand=bad"},
            )

    def test_existing_key_is_not_overwritten_or_deleted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def no_transport(*args: object, **kwargs: object) -> None:
            pytest.fail("existing-key refusal must precede transport")

        monkeypatch.setattr(verifier.subprocess, "run", no_transport)
        key = tmp_path / "report-key"
        key.write_text("preserve-existing")
        with pytest.raises(verifier.VerificationError, match="delivery failed"):
            verifier.deliver_ssh(
                b"data",
                work=tmp_path,
                env={
                    "PRIVATE_REPORT_TARGET": "report@example.invalid",
                    "PRIVATE_REPORT_SSH_KEY": "synthetic-key",
                    "PRIVATE_REPORT_KNOWN_HOSTS": "synthetic-host",
                },
            )
        assert key.read_text() == "preserve-existing"


class TestRetainedWorkflow:
    def test_every_existing_ci_job_has_complete_mapping(self) -> None:
        import yaml

        legacy = yaml.load(
            (ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
        )
        assert set(verifier.CI_JOB_GATES) == set(legacy["jobs"])
        assert set(verifier.CI_JOB_GATES["unit"]) == {
            "unit-" + shard
            for shard in legacy["jobs"]["unit"]["strategy"]["matrix"]["shard"]
        }
        assert set(verifier.CI_JOB_GATES["integration-shard"]) == {
            "integration-" + shard
            for shard in legacy["jobs"]["integration-shard"]["strategy"]["matrix"][
                "shard"
            ]
        }
        assert {name for gates in verifier.CI_JOB_GATES.values() for name in gates} == {
            gate.name for gate in verifier.GATES
        }

    def test_workflow_has_only_manual_release_route_and_gated_credentials(
        self,
    ) -> None:
        import yaml

        workflow = yaml.load(
            (ROOT / ".github/workflows/private-verification.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        assert set(workflow["on"]) == {"workflow_dispatch"}
        assert workflow["permissions"] == {"contents": "read"}
        job = workflow["jobs"]["verify"]
        assert job["environment"] == "staging-release"
        assert "env" not in workflow
        uses = [step["uses"] for step in job["steps"] if "uses" in step]
        assert all(
            "upload-artifact" not in use
            and "upload-sarif" not in use
            and "actions/cache" not in use
            for use in uses
        )
        assert all(len(use.split("@")[1]) == 40 for use in uses)
        uv = next(step for step in job["steps"] if step.get("id") == "uv")
        assert uv["with"]["enable-cache"] == "false"
        buildx = next(step for step in job["steps"] if step.get("id") == "buildx")
        assert buildx["with"]["cache-binary"] == "false"
        assert buildx["with"]["cleanup"] == "false"
        trivy = next(step for step in job["steps"] if step.get("id") == "trivy")
        assert trivy["with"]["cache"] == "false"
        assert "private-trivy-bootstrap" in trivy["with"]["scan-ref"]
        credential_steps = [step for step in job["steps"] if "secrets." in str(step)]
        assert [step["run"] for step in credential_steps] == [
            "python3 -I tools/staging_release.py verify",
            "python3 -I tools/staging_release.py transfer",
        ]
        assert "STAGING_ADMIN_SSH_KEY" not in credential_steps[0]["env"]

    def test_gate_script_retains_image_identity_scan_and_suite_contracts(self) -> None:
        shell = (ROOT / "tools/private_verification_gates.sh").read_text()
        for required in (
            "--no-build --no-cache --no-install-project",
            (
                "--no-install-package forecastinterface "
                "--no-install-package recap-dg-client"
            ),
            "tools/pyright_ratchet.py",
            "tools/release_target.py check",
            "tools/check_map_contract_version.py",
            "--check-partition",
            "--extra aquacast",
            "test_discover_models_returns_the_aquacast_model",
            "test_coverage_log_message PASSED",
            "COVERAGE_CORE=sysmon",
            "-n auto --cov=src/sapphire_flow --cov-report=",
            "coverage combine --keep",
            "--ignore=tests/integration/live -v -m 'not slow'",
            "--platform linux/amd64",
            "tools/image_identity_probe.py",
            "--severity HIGH,CRITICAL --exit-code 1",
            "--ignore-unfixed",
            "cyclonedx-json=",
            "--network none",
            "include-system-site-packages = false",
        ):
            assert required in shell
        assert "--push" not in shell
        assert "cache-to" not in shell
        assert "cache-from" not in shell


@pytest.fixture
def cli_fixture(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    import subprocess

    root = tmp_path / "source"
    (root / "tools").mkdir(parents=True)
    (root / ".github/workflows").mkdir(parents=True)
    (root / "tools/private_verification.py").write_bytes(
        (ROOT / "tools/private_verification.py").read_bytes()
    )
    (root / "tools/private_verification_gates.sh").write_text(
        'printf "PRIVATE_CANARY %s\n" "$1"\n'
        'printf "PRIVATE_CANARY summary\n" >> "$GITHUB_STEP_SUMMARY"\n'
        '[ "$1" != "${PRIVATE_TEST_FAIL_GATE:-none}" ]\n'
    )
    for name in ("ci.yml", "private-verification.yml"):
        (root / ".github/workflows" / name).write_text("synthetic fixture\n")
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
    }

    def git(*args: str) -> str:
        return subprocess.check_output(
            [
                "git",
                "-c",
                "user.name=Synthetic",
                "-c",
                "user.email=synthetic@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.hooksPath=/dev/null",
                *args,
            ],
            cwd=root,
            env=env,
            stderr=subprocess.PIPE,
            text=True,
        ).strip()

    executable = root / "mode-executable"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    git("init", "-b", "main")
    git("add", ".")
    git("commit", "-m", "synthetic base")
    base = git("rev-parse", "HEAD")
    git("checkout", "-b", "feature")
    (root / "change").write_text("synthetic head")
    git("add", ".")
    git("commit", "-m", "synthetic head")
    head = git("rev-parse", "HEAD")
    git("checkout", "main")
    git("merge", "--no-ff", "feature", "-m", "synthetic merge")
    merge = git("rev-parse", "HEAD")
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "number": 1,
                "pull_request": {
                    "head": {"sha": head, "repo": {"full_name": verifier.REPOSITORY}},
                    "base": {
                        "sha": base,
                        "ref": "main",
                        "repo": {"full_name": verifier.REPOSITORY},
                    },
                },
            }
        )
    )
    runtime = tmp_path / "runner"
    runtime.mkdir()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    received = tmp_path / "received-frame"
    (binaries / "ssh").write_text(
        f"#!{sys.executable}\nimport pathlib,sys\n"
        f"pathlib.Path({str(received)!r}).write_bytes(sys.stdin.buffer.read())\n"
        "sys.stdout.write('REPORT_RECEIVED\\n')\n"
    )
    (binaries / "docker").write_text("#!/bin/sh\nexit 0\n")
    for binary in binaries.iterdir():
        binary.chmod(0o700)
    env.update(
        {
            "PATH": str(binaries) + os.pathsep + env["PATH"],
            "GITHUB_WORKSPACE": str(root),
            "RUNNER_TEMP": str(runtime),
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_SHA": merge,
            "GITHUB_REF": "refs/pull/1/merge",
            "GITHUB_REPOSITORY": verifier.REPOSITORY,
            "GITHUB_WORKFLOW_REF": verifier.REPOSITORY
            + "/.github/workflows/private-verification.yml@refs/pull/1/merge",
            "GITHUB_WORKFLOW_SHA": merge,
            "GITHUB_RUN_ID": "1",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_ACTOR": "mabesa",
            "GITHUB_TRIGGERING_ACTOR": "mabesa",
            "GITHUB_JOB": "verify",
            "PRIVATE_PREPARATION": json.dumps(
                dict.fromkeys(("checkout", "uv", "buildx", "trivy", "syft"), "success")
            ),
            "RECAP_DG_CLIENT_TOKEN": "synthetic-recap",
            "AQUACAST_TOKEN": "synthetic-aquacast",
            "PRIVATE_REPORT_SSH_KEY": "synthetic-key",
            "PRIVATE_REPORT_KNOWN_HOSTS": "synthetic-host",
            "PRIVATE_REPORT_TARGET": "report@example.invalid",
            "GITHUB_STEP_SUMMARY": str(tmp_path / "public-summary"),
        }
    )
    env["PRIVATE_VERIFICATION_ADMISSION"] = json.dumps(
        asdict(verifier.current_binding(root, env))
    )
    return root, env, received


class TestCLI:
    @pytest.mark.parametrize(
        "failure", ["none", "lint", "unit-rest", "build-image-and-scan"]
    )
    def test_synthetic_pipeline_preserves_private_failure_evidence(
        self,
        cli_fixture: tuple[Path, dict[str, str], Path],
        failure: str,
    ) -> None:
        import subprocess

        root, env, received = cli_fixture
        work = Path(env["RUNNER_TEMP"]) / (
            f"private-verification-{env['GITHUB_RUN_ID']}-{env['GITHUB_RUN_ATTEMPT']}"
        )
        evidence = work / "evidence"
        assert not work.exists()
        assert not evidence.exists()
        env["PRIVATE_TEST_FAIL_GATE"] = failure
        result = subprocess.run(
            [sys.executable, "-I", str(root / "tools/private_verification.py")],
            env=env,
            capture_output=True,
            check=False,
            timeout=30,
        )
        expected = (
            b"PRIVATE_VERIFICATION_COMPLETE\n"
            if failure == "none"
            else b"PRIVATE_VERIFICATION_FAILED\n"
        )
        assert result.stdout == expected
        assert result.returncode == (0 if failure == "none" else 1)
        assert result.stderr == b""
        assert work.stat().st_mode & 0o777 == 0o700
        assert evidence.stat().st_mode & 0o777 == 0o700
        assert not Path(env["GITHUB_STEP_SUMMARY"]).exists()
        frame = received.read_bytes()
        assert len(frame[8:]) == int.from_bytes(frame[:8], "big")
        contents = gzip.decompress(frame[8:])
        assert b"PRIVATE_CANARY" in contents
        with tarfile.open(fileobj=io.BytesIO(contents)) as archive:
            manifest_file = archive.extractfile("manifest.json")
            assert manifest_file is not None
            manifest = json.load(manifest_file)
            disk_file = archive.extractfile("disk-snapshots.jsonl")
            assert disk_file is not None
            snapshots = [json.loads(line) for line in disk_file]
        assert [sample["boundary"] for sample in snapshots] == [
            "before-gates",
            *("after-" + gate.name for gate in verifier.GATES),
            "after-builder-cleanup",
        ]
        assert all(sample["measurement"] == "sampled-not-peak" for sample in snapshots)
        assert all(
            set(sample["filesystems"])
            == {"root", "workspace", "work", "docker-default-root"}
            for sample in snapshots
        )
        assert b"disk-snapshots" not in result.stdout
        assert str(work).encode() not in result.stdout
        if failure == "unit-rest":
            assert manifest["gates"]["unit-coverage"] == "blocked"
        assert manifest["binding"] == json.loads(env["PRIVATE_VERIFICATION_ADMISSION"])
        if failure != "none":
            assert manifest["gates"][failure] == "failed"

    def test_stale_attempt_fails_before_any_gate_or_upload(
        self,
        cli_fixture: tuple[Path, dict[str, str], Path],
    ) -> None:
        import subprocess

        root, env, received = cli_fixture
        env["GITHUB_RUN_ATTEMPT"] = "2"
        result = subprocess.run(
            [sys.executable, "-I", str(root / "tools/private_verification.py")],
            env=env,
            capture_output=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == 1
        assert result.stdout == b"PRIVATE_VERIFICATION_FAILED\n"
        assert result.stderr == b""
        assert not received.exists()

    def test_changed_checkout_is_rejected(
        self,
        cli_fixture: tuple[Path, dict[str, str], Path],
    ) -> None:
        root, env, _ = cli_fixture
        (root / "tools/private_verification_gates.sh").write_text("changed source")
        with pytest.raises(
            verifier.VerificationError, match="checkout identity changed"
        ):
            verifier.current_binding(root, env)


class TestActualGateCapture:
    @pytest.mark.parametrize(
        "scenario,gate",
        [
            ("dependency", "lint"),
            ("test", "unit-rest"),
            ("scan", "build-image-and-scan"),
        ],
    )
    def test_real_gate_script_captures_tool_failures(
        self,
        cli_fixture: tuple[Path, dict[str, str], Path],
        tmp_path: Path,
        capfd: pytest.CaptureFixture[str],
        scenario: str,
        gate: str,
    ) -> None:
        root, env, _ = cli_fixture
        binaries = tmp_path / "bin"
        fake_tool = f"""#!{sys.executable}
import os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
scenario = os.environ['PRIVATE_TEST_SCENARIO']
fail = ((name == 'uv' and scenario == 'dependency' and args[0] == 'sync') or
        (name == 'uv' and scenario == 'test' and 'pytest' in args) or
        (name == 'trivy' and scenario == 'scan'))
if fail:
    pathlib.Path(os.environ['PRIVATE_WORK'], 'project-created').write_text('mode')
    print('::error::PRIVATE_CANARY_' + scenario)
    print('PRIVATE_CANARY_' + scenario, file=sys.stderr)
    summary = pathlib.Path(os.environ['GITHUB_STEP_SUMMARY'])
    summary.write_text('PRIVATE_CANARY_' + scenario)
    sys.exit(1)
if name == 'docker' and args[:2] == ['image', 'inspect']:
    print('linux/amd64' if '.Architecture' in ' '.join(args) else 'sha256:' + 'a' * 64)
elif name == 'docker' and 'ls /app/scripts' in args:
    print('backfill_era5_land_history.py\\nbackfill_meteoswiss_history.py\\ncreate_station_group.py\\nimport_caravan_attributes.py\\nonboard.py\\nvalidate_forcing_reference.py')
"""
        for name in ("uv", "sudo", "docker", "trivy", "syft"):
            binary = binaries / name
            binary.write_text(fake_tool)
            binary.chmod(0o700)
        work = tmp_path / "private-work"
        evidence = work / "evidence"
        work.mkdir(mode=0o700)
        evidence.mkdir(mode=0o700)
        env.update(
            {
                "PRIVATE_WORK": str(work),
                "PRIVATE_EVIDENCE": str(evidence),
                "PRIVATE_TEST_SCENARIO": scenario,
                "PR_BASE_SHA": "c" * 40,
                "PR_HEAD_SHA": "d" * 40,
                "PR_MERGE_SHA": env["GITHUB_SHA"],
                "EXPECTED_VERSION": "0.dev0+g" + env["GITHUB_SHA"],
                "EXPECTED_REVISION": env["GITHUB_SHA"],
                "SYFT_BIN": str(binaries / "syft"),
            }
        )
        result = verifier.run_private(
            ["bash", str(ROOT / "tools/private_verification_gates.sh"), gate],
            cwd=root,
            evidence=evidence / gate,
            env=env,
            timeout_seconds=30,
        )
        assert result == verifier.Outcome.FAILED
        marker = "PRIVATE_CANARY_" + scenario
        assert marker in (evidence / gate / "command.log").read_text()
        assert marker in (evidence / gate / "github_step_summary").read_text()
        assert not Path(env["GITHUB_STEP_SUMMARY"]).exists()
        assert marker not in str(capfd.readouterr())

        assert (work / "project-created").stat().st_mode & 0o777 == 0o644
        assert work.stat().st_mode & 0o777 == 0o700
        assert (evidence / gate).stat().st_mode & 0o777 == 0o700
        if scenario == "scan":
            assert (work / "image-context/change").stat().st_mode & 0o777 == 0o644
            assert (
                work / "image-context/mode-executable"
            ).stat().st_mode & 0o777 == 0o755


class TestImageFailureEvidence:
    @pytest.mark.parametrize("variant", ["default", "aquacast"])
    @pytest.mark.parametrize(
        "failure",
        ["finding", "operational", "malformed", "sarif", "sbom", "proof", "none"],
    )
    @pytest.mark.parametrize("source_kind", ["legacy", "release"])
    def test_image_components_retain_evidence_and_aggregate_failures(
        self,
        cli_fixture: tuple[Path, dict[str, str], Path],
        tmp_path: Path,
        capfd: pytest.CaptureFixture[str],
        variant: str,
        failure: str,
        source_kind: str,
    ) -> None:
        root, env, _ = cli_fixture
        binaries = tmp_path / "bin"
        tool = (
            f"#!{sys.executable}\n"
            + r"""
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
evidence = pathlib.Path(os.environ['PRIVATE_EVIDENCE'])
failure = os.environ['PRIVATE_SCAN_FAILURE']
with (evidence / 'argv.jsonl').open('a') as stream:
    stream.write(json.dumps([name, args]) + '\n')
def mark(label):
    with (evidence / 'tool-calls').open('a') as stream:
        stream.write(label + '\n')
def canary():
    print('::error::PRIVATE_FINDING_CANARY')
    print('PRIVATE_FINDING_CANARY', file=sys.stderr)
    pathlib.Path(os.environ['GITHUB_STEP_SUMMARY']).write_text('PRIVATE_FINDING_CANARY')
if name == 'docker':
    if args[:2] == ['image', 'inspect']:
        architecture = '.Architecture' in ' '.join(args)
        print('linux/amd64' if architecture else 'sha256:' + 'a' * 64)
    elif 'ls /app/scripts' in args:
        print('backfill_era5_land_history.py\nbackfill_meteoswiss_history.py\ncreate_station_group.py\nimport_caravan_attributes.py\nonboard.py\nvalidate_forcing_reference.py')
    elif '-I' in args:
        mark('entrypoint')
elif name == 'trivy':
    if args[0] == 'image':
        if failure == 'operational':
            canary()
            sys.exit(2)
        report = {'SchemaVersion': 2, 'Results': []}
        if failure == 'malformed':
            canary()
            report = {'SchemaVersion': 'PRIVATE_FINDING_CANARY', 'Results': []}
        pathlib.Path(args[args.index('--output') + 1]).write_text(json.dumps(report))
    elif 'sarif' in args:
        if failure == 'sarif':
            canary()
            sys.exit(2)
        pathlib.Path(args[args.index('--output') + 1]).write_text('{}')
    elif failure == 'finding':
        canary()
        sys.exit(1)
elif name == 'syft':
    mark('sbom-generated')
    path = pathlib.Path(args[args.index('-o') + 1].split('=', 1)[1])
    bom = {'bomFormat': 'CycloneDX', 'components': [{'name': 'synthetic'}]}
    if failure == 'sbom':
        canary()
        bom['components'] = []
    path.write_text(json.dumps(bom))
elif name == 'jq':
    value = json.loads(pathlib.Path(args[-1]).read_text())
    if 'bomFormat' in args[-2]:
        assert value['bomFormat'] == 'CycloneDX' and value['components']
        mark('sbom-validated')
    else:
        valid = value.get('SchemaVersion') == 2
        sys.exit(0 if valid and isinstance(value.get('Results'), list) else 1)
elif name == 'python3':
    assert args[0] == 'tools/image_identity_probe.py'
    mark('identity-proof')
    if failure == 'proof':
        canary()
        sys.exit(3)
else:
    raise AssertionError(name)
"""
        )
        for name in ("docker", "trivy", "syft", "jq", "python3"):
            binary = binaries / name
            binary.write_text(tool)
            binary.chmod(0o700)
        work = tmp_path / "private-work"
        work.mkdir(mode=0o700)
        evidence = work / "evidence"
        evidence.mkdir(mode=0o700)
        env.update(
            {
                "PRIVATE_WORK": str(work),
                "PRIVATE_EVIDENCE": str(evidence),
                "PRIVATE_SCAN_FAILURE": failure,
                "EXPECTED_VERSION": "0.dev0+g" + env["GITHUB_SHA"],
                "EXPECTED_REVISION": env["GITHUB_SHA"],
                "SYFT_BIN": str(binaries / "syft"),
            }
        )
        context = work / "image-context"
        if source_kind == "release":
            import io
            import subprocess
            import tarfile

            context = work / "approved-context"
            context.mkdir()
            archived = subprocess.check_output(
                ["git", "archive", env["GITHUB_SHA"]], cwd=root
            )
            with tarfile.open(fileobj=io.BytesIO(archived)) as source:
                source.extractall(context, filter="data")
            env["RELEASE_IMAGE_CONTEXT"] = str(context)
            env["RELEASE_IMAGE_TAG"] = (
                "sapphire-flow"
                + ("-aquacast" if variant == "aquacast" else "")
                + ":1.2.3"
            )
            env["EXPECTED_VERSION"] = "1.2.3"
            env["GITHUB_EVENT_NAME"] = "workflow_dispatch"
            for name in ("PR_BASE_SHA", "PR_HEAD_SHA", "PR_MERGE_SHA"):
                env.pop(name, None)
        gate = (
            "build-image-and-scan"
            if variant == "default"
            else "aquacast-release-image-proof"
        )
        result = verifier.run_private(
            ["bash", str(ROOT / "tools/private_verification_gates.sh"), gate],
            cwd=root,
            evidence=evidence / gate,
            env=env,
            timeout_seconds=30,
        )
        expected = (
            verifier.Outcome.PASSED if failure == "none" else verifier.Outcome.FAILED
        )
        assert result == expected
        expected_calls = ["sbom-generated"]
        if failure != "sbom":
            expected_calls.append("sbom-validated")
        proof_blocked = failure == "sbom" and variant == "default"
        if not proof_blocked:
            expected_calls.append("identity-proof")
            if failure != "proof":
                expected_calls.append("entrypoint")
        assert (evidence / "tool-calls").read_text().splitlines() == expected_calls
        sbom = json.loads((evidence / f"{variant}-sbom.cdx.json").read_text())
        assert sbom["bomFormat"] == "CycloneDX"
        sbom_result = json.loads((evidence / f"{variant}-sbom-result.json").read_text())
        assert sbom_result["outcome"] == ("failed" if failure == "sbom" else "passed")
        proof_result = json.loads(
            (evidence / f"{variant}-proof-result.json").read_text()
        )
        if proof_blocked:
            assert proof_result == {"outcome": "blocked", "reason": "sbom_failed"}
        else:
            assert proof_result["outcome"] == (
                "failed" if failure == "proof" else "passed"
            )
        scan = json.loads((evidence / f"{variant}-scan-result.json").read_text())
        scan_failed = failure in {"finding", "operational", "malformed", "sarif"}
        assert scan["outcome"] == ("failed" if scan_failed else "passed")
        assert (scan["exit_code"] != 0) == scan_failed
        assert (context / "change").stat().st_mode & 0o777 == 0o644
        assert (context / "mode-executable").stat().st_mode & 0o777 == 0o755
        assert not (evidence / f"{variant}-build-result.json").exists()
        assert not (evidence / f"{variant}-gate-result.json").exists()
        if source_kind == "release":
            calls = [
                json.loads(line)
                for line in (evidence / "argv.jsonl").read_text().splitlines()
            ]
            build = next(
                args
                for name, args in calls
                if name == "docker" and args[:2] == ["buildx", "build"]
            )
            assert build[build.index("--tag") + 1] == env["RELEASE_IMAGE_TAG"]
            assert build[-1] == str(context)
            captured_id = (evidence / f"{variant}-image-id").read_text().strip()
            for name, args in calls:
                if name == "trivy" and args[0] == "image":
                    assert args[-1] == captured_id
                if name == "syft":
                    assert args[1] == captured_id
                if name == "python3":
                    assert args[args.index("--image-tag") + 1] == captured_id
        assert work.stat().st_mode & 0o777 == 0o700
        assert (evidence / gate).stat().st_mode & 0o777 == 0o700
        if failure != "none":
            assert (
                "PRIVATE_FINDING_CANARY"
                in (evidence / gate / "command.log").read_text()
            )
            assert (
                "PRIVATE_FINDING_CANARY"
                in (evidence / gate / "github_step_summary").read_text()
            )
        assert not Path(env["GITHUB_STEP_SUMMARY"]).exists()
        assert "PRIVATE_FINDING_CANARY" not in str(capfd.readouterr())


class TestDiskSnapshots:
    def test_appends_labelled_samples_privately(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capfd: pytest.CaptureFixture[str],
    ) -> None:
        from types import SimpleNamespace

        def usage(path: Path) -> SimpleNamespace:
            return SimpleNamespace(total=1000, used=400, free=600)

        monkeypatch.setattr(verifier.shutil, "disk_usage", usage)
        private_path = tmp_path / "PRIVATE_PATH_CANARY"
        private_path.mkdir()
        for boundary in ("before-gates", "after-example"):
            verifier.record_disk(tmp_path, boundary, {"workspace": private_path})
        samples = [
            json.loads(line)
            for line in (tmp_path / "disk-snapshots.jsonl").read_text().splitlines()
        ]
        assert [sample["boundary"] for sample in samples] == [
            "before-gates",
            "after-example",
        ]
        for sample in samples:
            assert sample["measurement"] == "sampled-not-peak"
            assert sample["filesystems"] == {
                "workspace": {
                    "path": str(private_path),
                    "device": private_path.stat().st_dev,
                    "total": 1000,
                    "used": 400,
                    "free": 600,
                }
            }
        assert capfd.readouterr() == ("", "")

    def test_unavailable_filesystem_records_null_not_zero(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing"
        verifier.record_disk(tmp_path, "before-gates", {"docker-default-root": missing})
        sample = json.loads((tmp_path / "disk-snapshots.jsonl").read_text())
        assert sample["filesystems"]["docker-default-root"] == {
            "path": str(missing),
            "device": None,
            "total": None,
            "used": None,
            "free": None,
        }


class TestDispatchShellIntegration:
    def test_lint_dispatch_without_pr_variables_keeps_checkout_and_git_config_clean(
        self, cli_fixture: tuple[Path, dict[str, str], Path], tmp_path: Path
    ) -> None:
        import subprocess

        root, env, _ = cli_fixture
        work = tmp_path / "dispatch-work"
        evidence = work / "evidence"
        evidence.mkdir(parents=True, mode=0o700)
        binaries = tmp_path / "bin"
        script = (
            f"#!{sys.executable}\n"
            + r"""
import json, os, pathlib, sys
args = sys.argv[1:]
assert os.environ['GIT_CONFIG_GLOBAL'] == '/dev/null'
assert os.environ['GIT_CONFIG_COUNT'] == '2'
pr_names = ('PR_BASE_SHA', 'PR_HEAD_SHA', 'PR_MERGE_SHA')
assert not any(name in os.environ for name in pr_names)
record = pathlib.Path(os.environ['PRIVATE_EVIDENCE']) / 'lint-tools.jsonl'
with record.open('a') as output:
    output.write(json.dumps(args) + '\n')
if 'pyright' in args:
    print('{"generalDiagnostics": []}')
"""
        )
        for name in ("uv", "shellcheck", "trivy"):
            path = binaries / name
            path.write_text(script)
            path.chmod(0o700)
        env.update(
            PRIVATE_WORK=str(work),
            PRIVATE_EVIDENCE=str(evidence),
            GITHUB_EVENT_NAME="workflow_dispatch",
        )
        for name in ("PR_BASE_SHA", "PR_HEAD_SHA", "PR_MERGE_SHA"):
            env.pop(name, None)
        before = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"], cwd=root
        )
        result = verifier.run_private(
            ["bash", str(ROOT / "tools/private_verification_gates.sh"), "lint"],
            cwd=root,
            evidence=evidence / "lint",
            env=env,
            timeout_seconds=30,
        )
        assert result == verifier.Outcome.PASSED
        calls = (evidence / "lint-tools.jsonl").read_text()
        assert "release_target.py" not in calls
        assert "check_map_contract_version.py" not in calls
        assert before == subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"], cwd=root
        )
        assert not (tmp_path / ".gitconfig").exists()
