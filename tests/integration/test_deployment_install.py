from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

import pytest

from tests.integration.test_deployment_rehearsal import cli_environment, materialize
from tools import deployment_install_runtime as runtime
from tools import deployment_install_state as state
from tools import deployment_rehearsal as common
from tools import qualify_deployment_smoke as sentinel

ROOT = Path(__file__).resolve().parents[2]
Operation = Literal["install", "verify", "stop", "start"]


def snapshot(directory: Path) -> dict[str, tuple[Any, ...]]:
    result: dict[str, tuple[Any, ...]] = {}
    for path in directory.rglob("*"):
        if path.is_file():
            metadata = path.stat()
            result[str(path.relative_to(directory))] = (
                hashlib.sha256(path.read_bytes()).hexdigest(),
                metadata.st_uid,
                metadata.st_gid,
                metadata.st_mode,
                metadata.st_mtime_ns,
            )
    return result


def prepare_case(
    directory: Path, revision: str, tag: str
) -> tuple[Path, Path, tuple[str, ...]]:
    profile, installation = materialize(directory / "profile", revision, tag)
    project = "sapphire-install-test-" + uuid4().hex
    text = installation.read_text()
    for old, new in {
        "synthetic-rehearsal-desired": project,
        'SAPPHIRE_DOMAIN = "example.invalid"': 'SAPPHIRE_DOMAIN = ":80"',
        'SAPPHIRE_CORS_ORIGINS = "https://example.invalid"': (
            'SAPPHIRE_CORS_ORIGINS = ""'
        ),
    }.items():
        assert text.count(old) == 1, "qualification fixture drift"
        text = text.replace(old, new)
    installation.write_text(text)
    delivery = profile.parent / "secret-delivery"
    delivery.mkdir(mode=0o700)
    canaries = tuple(uuid4().hex + uuid4().hex for _ in state.ROLES)
    for role, value in zip(state.ROLES, canaries, strict=True):
        path = delivery / role
        path.write_text(value + "\n")
        path.chmod(0o444)
    return profile, installation, canaries


def session(directory: Path, deadline: float) -> runtime.Session:
    record = state.load_record(directory)
    assert record.project.startswith("sapphire-install-test-"), (
        "not a qualification installation"
    )
    return runtime.Session(
        common.Runtime(directory=directory, deadline=deadline), record
    )


def invoke(
    operation: Operation,
    directory: Path,
    *,
    profile: Path,
    installation: Path,
    canaries: tuple[str, ...],
    deadline: float,
    outcomes: list[dict[str, Any]],
    http_port: int = 48189,
    fault: Literal["none", "kill_init"] = "none",
) -> tuple[int, dict[str, Any]]:
    command = [
        sys.executable,
        str(ROOT / "tools/deployment_install.py"),
        operation,
        "--state-dir",
        str(directory),
        "--format",
        "json",
    ]
    if operation == "install":
        command.extend(
            [
                "--profile-file",
                str(profile),
                "--installation",
                str(installation),
                "--image-identity",
                "ci-development",
                "--http-port",
                str(http_port),
            ]
        )
    if deadline - time.monotonic() <= 240:
        raise AssertionError("qualification budget exhausted before launch")
    operation_deadline = min(deadline - 240, time.monotonic() + 900)
    injected = False
    with (
        tempfile.TemporaryFile(mode="w+t", dir=profile.parent) as stdout,
        tempfile.TemporaryFile(mode="w+t", dir=profile.parent) as stderr,
        subprocess.Popen(
            command,
            cwd=profile.parent,
            env=cli_environment(),
            stdout=stdout,
            stderr=stderr,
            text=True,
        ) as process,
    ):
        try:
            while process.poll() is None:
                if time.monotonic() >= operation_deadline or any(
                    os.fstat(stream.fileno()).st_size > common.LIMIT
                    for stream in (stdout, stderr)
                ):
                    process.send_signal(signal.SIGTERM)
                    break
                if (
                    fault == "kill_init"
                    and not injected
                    and (directory / "installation.json").exists()
                ):
                    progress = state.load_record(directory)
                    if progress.stage == "create_init":
                        owned = session(directory, deadline)
                        identifier: str | None = None
                        try:
                            identifier, item = owned.service("init")
                        except common.RehearsalRuntimeError:
                            item = {}
                        if identifier is not None and item.get("State", {}).get(
                            "Running"
                        ):
                            try:
                                owned.inspect("container", identifier)
                                owned.driver.run(["kill", identifier])
                            except common.RehearsalRuntimeError:
                                raise AssertionError(
                                    "init failure injection failed"
                                ) from None
                            injected = True
                time.sleep(0.1)
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=130)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        stdout.seek(0)
        stderr.seek(0)
        output, errors = stdout.read(common.LIMIT + 1), stderr.read(common.LIMIT + 1)
    if max(len(output), len(errors)) > common.LIMIT:
        raise AssertionError("qualification child output exceeded bound")
    if any(value in output or value in errors for value in canaries):
        raise AssertionError("credential escaped captured CLI output")
    try:
        result = json.loads(output)
        if not isinstance(result, dict):
            raise ValueError
        result = cast("dict[str, Any]", result)
        if result.get("scope") != "persistent_installation":
            raise ValueError
    except ValueError:
        raise AssertionError("invalid installation report") from None
    outcomes.append(
        {
            "operation": operation,
            "exit_code": process.returncode,
            **{
                key: result[key]
                for key in (
                    "status",
                    "code",
                    "stage",
                    "containment",
                    "failed_operation",
                )
                if key in result
            },
        }
    )
    if fault == "kill_init":
        assert injected, "init failure injection did not run"
    return process.returncode, result


def sql(owned: runtime.Session, statement: str) -> str:
    identifier, _ = owned.service("postgres")
    return owned.driver.run(
        [
            "exec",
            identifier,
            "psql",
            "-U",
            "sapphire",
            "-d",
            "sapphire",
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
            "-c",
            statement,
        ]
    ).strip()


def persistence_markers(
    owned: runtime.Session, action: Literal["write", "read"]
) -> None:
    if action == "write":
        sql(
            owned,
            "CREATE TABLE installation_qualification (value text PRIMARY KEY); "
            "INSERT INTO installation_qualification VALUES ('retained');",
        )
    assert sql(owned, "SELECT value FROM installation_qualification") == "retained", (
        "database marker lost"
    )
    identifier, _ = owned.service("prefect-worker")
    script = (
        "from pathlib import Path; "
        "p = Path('/data/artifacts/installation-qualification'); "
    )
    if action == "write":
        script += "p.write_text('retained'); "
    script += "assert p.read_text() == 'retained'; print('retained')"
    assert (
        owned.driver.run(
            [
                "exec",
                "--user",
                "1000:1000",
                identifier,
                "/app/.venv/bin/python",
                "-c",
                script,
            ]
        )
        == "retained"
    ), "volume marker lost"


def teardown_case(directory: Path) -> Literal["not_created", "cleaned"]:
    if not (directory / "installation.json").exists():
        return "not_created"
    owned = session(directory, time.monotonic() + 120)
    inventory = owned.inventory()
    # Test-only destruction: every resource is independently rechecked before removal.
    for kind in ("container", "network", "volume"):
        for identifier in inventory[kind]:
            owned.inspect(kind, identifier)
            owned.driver.run(
                [kind, "rm", *(["-f"] if kind == "container" else []), identifier]
            )
    assert not any(owned.inventory().values()), "test installation cleanup incomplete"
    probes = owned.driver.run(
        [
            "ps",
            "-aq",
            "--filter",
            "label=" + runtime.LABEL + "=" + owned.record.installation_id,
        ]
    )
    assert not probes, (
        "temporary installation probe remains; inspect private recovery state"
    )
    return "cleaned"


def assert_private_outputs(
    owned: runtime.Session, canaries: tuple[str, ...], evidence: dict[str, Any]
) -> None:
    contents = [json.dumps(evidence).encode()]
    contents.extend(
        path.read_bytes()
        for path in owned.driver.directory.rglob("*")
        if path.is_file()
    )
    for identifier in owned.inventory()["container"]:
        contents.append(
            json.dumps(
                owned.inspect("container", identifier)["Config"].get("Env", [])
            ).encode()
        )
    if any(value.encode() in content for value in canaries for content in contents):
        raise AssertionError(
            "credential escaped into retained state or public evidence"
        )


def success_case(
    directory: Path, revision: str, tag: str, deadline: float, evidence: dict[str, Any]
) -> None:
    profile, installation, canaries = prepare_case(directory, revision, tag)
    original = snapshot(profile.parent)
    retained = directory / "state"
    outcomes: list[dict[str, Any]] = evidence["operations"]

    def run(operation: Operation) -> tuple[int, dict[str, Any]]:
        return invoke(
            operation,
            retained,
            profile=profile,
            installation=installation,
            canaries=canaries,
            deadline=deadline,
            outcomes=outcomes,
        )

    control = directory / "sentinel-control"
    control.mkdir(mode=0o700)
    (control / "docker-config").mkdir(mode=0o700)
    docker = common.Runtime(directory=control, deadline=deadline)
    daemon = docker.daemon()
    identity = uuid4().hex
    sentinel_created = False
    before: dict[str, Any] | None = None
    try:
        evidence["stage"] = "install"
        code, result = run("install")
        assert code == 0 and result["status"] == "INSTALLED_PAUSED", (
            f"install failed: {result.get('code')} at {result.get('stage')}"
        )
        assert result["http_binding"] == "127.0.0.1:48189:80/tcp"
        assert result["host_responses"] == {
            "health": 200,
            "unauthenticated_stations": 401,
        }
        assert result["token_probe"] == "revoked"
        owned = session(retained, deadline)
        initial_ids = owned.record.known
        init = owned.record.init_identity
        persistence_markers(owned, "write")
        token_sql = (
            "SELECT count(*) || ':' || count(disabled_at) FROM access_tokens "
            "WHERE name = 'installation-probe'"
        )
        assert sql(owned, token_sql) == "1:1", "probe token not revoked"
        image = owned.service("api")[1]["Image"]
        sentinel_created = True
        sentinel.create_sentinel(docker, identity, image)
        before = sentinel.sentinel_snapshot(docker, identity)
        evidence["stage"] = "verify"
        frozen = snapshot(retained)
        assert run("verify")[1]["status"] == "VERIFIED_PAUSED"
        assert snapshot(retained) == frozen, "verify changed retained state"
        assert sql(owned, token_sql) == "1:1", "verify minted another token"
        evidence["stage"] = "stop_and_missing_delivery"
        assert run("stop")[1]["status"] == "STOPPED"
        assert run("verify")[1]["status"] == "STOPPED"
        delivery = profile.parent / "secret-delivery" / "sapphire_api_db_password"
        parked = directory / "parked-credential"
        delivery.rename(parked)
        try:
            code, result = run("start")
            assert code == 2 and result["code"] == "credential_delivery_unavailable"
            assert all(
                not owned.inspect("container", identifier)["State"]["Running"]
                for kind, identifier in initial_ids
                if kind == "container"
            ), "missing secret started a service"
        finally:
            parked.rename(delivery)
        evidence["stage"] = "restart"
        code, result = run("start")
        assert code == 0 and result["status"] == "INSTALLED_PAUSED", "restart failed"
        restarted = session(retained, deadline)
        assert set(restarted.record.known) == set(initial_ids), (
            "restart recreated resources"
        )
        assert restarted.record.init_identity == init, "init ran again"
        actual_init = restarted.service("init")[1]
        assert (actual_init["Id"], actual_init["State"]["FinishedAt"]) == init
        persistence_markers(restarted, "read")
        assert sql(restarted, token_sql) == "1:1", "restart minted another token"
        assert run("verify")[1]["status"] == "VERIFIED_PAUSED"
        assert snapshot(profile.parent) == original, (
            "original profile or delivery changed"
        )
        assert sentinel.sentinel_snapshot(docker, identity) == before, (
            "unrelated sentinel changed"
        )
        evidence["persistence_verified"] = True
        assert_private_outputs(restarted, canaries, evidence)
    finally:
        try:
            evidence["installation_cleanup_verified"] = (
                teardown_case(retained) == "cleaned"
            )
        finally:
            if sentinel_created:
                docker.deadline = time.monotonic() + 120
                assert docker.daemon() == daemon, "sentinel daemon changed"
                try:
                    if before is not None:
                        evidence["sentinel_preserved"] = (
                            sentinel.sentinel_snapshot(docker, identity) == before
                        )
                        assert evidence["sentinel_preserved"], (
                            "cleanup changed sentinel"
                        )
                finally:
                    sentinel.cleanup_sentinel(docker, identity)
                    evidence["sentinel_cleanup_verified"] = True


def failure_case(
    directory: Path, revision: str, tag: str, deadline: float, evidence: dict[str, Any]
) -> None:
    profile, installation, canaries = prepare_case(directory, revision, tag)
    original = snapshot(profile.parent)
    retained = directory / "state"
    outcomes: list[dict[str, Any]] = evidence["operations"]
    control = directory / "sentinel-control"
    control.mkdir(mode=0o700)
    (control / "docker-config").mkdir(mode=0o700)
    docker = common.Runtime(directory=control, deadline=deadline)
    daemon = docker.daemon()
    identity = uuid4().hex
    sentinel_created = False
    sentinel_before: dict[str, Any] | None = None
    try:
        evidence["stage"] = "injected_init_failure"
        code, result = invoke(
            "install",
            retained,
            profile=profile,
            installation=installation,
            canaries=canaries,
            deadline=deadline,
            outcomes=outcomes,
            http_port=48190,
            fault="kill_init",
        )
        assert code == 1 and result["code"] == "init_failed", (
            "init failure not reported"
        )
        assert result["containment"] == "stopped_data_retained", (
            "failure containment incomplete"
        )
        owned = session(retained, deadline)
        inventory = owned.inventory()
        assert inventory["volume"], "failure removed storage"
        assert all(
            not owned.inspect("container", identifier)["State"]["Running"]
            for identifier in inventory["container"]
        ), "failure left services running"
        image = json.loads(docker.run(["image", "inspect", "sapphire-flow:" + tag]))[0][
            "Id"
        ]
        sentinel_created = True
        sentinel.create_sentinel(docker, identity, image)
        sentinel_before = sentinel.sentinel_snapshot(docker, identity)
        before = inventory
        code, result = invoke(
            "install",
            retained,
            profile=profile,
            installation=installation,
            canaries=canaries,
            deadline=deadline,
            outcomes=outcomes,
            http_port=48190,
        )
        assert (
            code == 2
            and result["status"] == "REFUSED"
            and result["code"] == "STATE_PATH_OCCUPIED"
        )
        assert owned.inventory() == before, "repeated install mutated failed state"
        code, result = invoke(
            "stop",
            retained,
            profile=profile,
            installation=installation,
            canaries=canaries,
            deadline=deadline,
            outcomes=outcomes,
            http_port=48190,
        )
        assert code == 0 and result["status"] == "STOPPED"
        assert owned.inventory() == before, "guarded stop removed data"
        assert snapshot(profile.parent) == original, (
            "failed install changed original inputs"
        )
        code, result = invoke(
            "start",
            retained,
            profile=profile,
            installation=installation,
            canaries=canaries,
            deadline=deadline,
            outcomes=outcomes,
            http_port=48190,
        )
        assert code == 2 and result["code"] == "partial_installation_refused"
        assert sentinel.sentinel_snapshot(docker, identity) == sentinel_before
        assert_private_outputs(owned, canaries, evidence)
        evidence["failure_retention_verified"] = True
    finally:
        try:
            evidence["failure_cleanup_verified"] = teardown_case(retained) == "cleaned"
        finally:
            if sentinel_created:
                docker.deadline = time.monotonic() + 120
                assert docker.daemon() == daemon, "failure sentinel daemon changed"
                try:
                    if sentinel_before is not None:
                        evidence["failure_sentinel_preserved"] = (
                            sentinel.sentinel_snapshot(docker, identity)
                            == sentinel_before
                        )
                        assert evidence["failure_sentinel_preserved"], (
                            "failure cleanup changed sentinel"
                        )
                finally:
                    sentinel.cleanup_sentinel(docker, identity)
                    evidence["failure_sentinel_cleanup_verified"] = True


@pytest.mark.deployment
class TestPersistentInstallation:
    def test_lifecycle_failure_and_owned_cleanup(self, tmp_path: Path) -> None:
        if os.environ.get("SAPPHIRE_INSTALL_ACCEPTANCE") != "dedicated-test-daemon":
            pytest.skip(
                "requires explicit dedicated-daemon opt-in and matching prepared images"
            )
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        tag = os.environ["SAPPHIRE_INSTALL_IMAGE_TAG"]
        report = Path(
            os.environ.get(
                "SAPPHIRE_INSTALL_QUALIFICATION_REPORT",
                str(tmp_path / "qualification.json"),
            )
        )
        report.parent.mkdir(mode=0o700, exist_ok=True)
        common.private_directory(report.parent)
        evidence: dict[str, Any] = {
            "schema_version": 1,
            "scope": "hosted_development_installation_qualification",
            "passed": False,
            "stage": "preparation",
            "operations": [],
            "persistence_verified": False,
            "failure_retention_verified": False,
            "sentinel_preserved": False,
            "failure_sentinel_preserved": False,
            "failure_sentinel_cleanup_verified": False,
            "installation_cleanup_verified": False,
            "failure_cleanup_verified": False,
            "sentinel_cleanup_verified": False,
            "host_reboot_verified": False,
        }
        deadline = time.monotonic() + 2700
        try:
            for name, check in (("success", success_case), ("failure", failure_case)):
                directory = tmp_path / name
                directory.mkdir(mode=0o700)
                check(directory, revision, tag, deadline, evidence)
            assert all(
                evidence[key]
                for key in (
                    "installation_cleanup_verified",
                    "failure_cleanup_verified",
                    "sentinel_cleanup_verified",
                    "failure_sentinel_cleanup_verified",
                )
            ), "qualification cleanup unverified"
            evidence["stage"] = "complete"
            evidence["passed"] = True
        finally:
            common.durable_json(report, evidence)
