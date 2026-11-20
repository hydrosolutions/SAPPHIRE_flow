from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sapphire_flow.config.deployment_profile import select_deployment_profile
from tests.unit.config.profile_support import materialize_offline_fixture
from tools import deployment_install_runtime as runtime
from tools import deployment_install_state as state
from tools.deployment_rehearsal import compose_executable as find_compose

resolve_images = runtime.resolve_images


class DockerFake:
    def __init__(self) -> None:
        self.resources: dict[str, dict[str, dict[str, Any]]] = {
            kind: {} for kind in runtime.COMMANDS
        }
        self.events: list[tuple[str, ...]] = []
        self.recipe: dict[str, Any] = {}
        self.pools_paused = False
        self.jobs_paused = False
        self.executed = False
        self.failure: str | None = None
        self.daemon = "synthetic-daemon"
        self.version = "28.0.0"
        self.rootless = False
        self.elapsed = 0.0

    def clock(self) -> float:
        return self.elapsed

    def sleep(self, seconds: float) -> None:
        self.elapsed += seconds

    def create_service(self, name: str) -> None:
        recipe = self.recipe
        project = recipe["name"]
        for kind, category in (("volume", "volumes"), ("network", "networks")):
            for resource in recipe[category].values():
                identifier = (
                    resource["name"]
                    if kind == "volume"
                    else hashlib.sha256(resource["name"].encode()).hexdigest()
                )
                self.resources[kind].setdefault(
                    identifier,
                    {
                        "Name": resource["name"],
                        "Id": identifier,
                        "Labels": {
                            **resource["labels"],
                            runtime.PROJECT_LABEL: project,
                        },
                        "Sentinel": "retained synthetic data",
                    },
                )
        config = recipe["services"][name]
        container_name = project + "-" + name + "-1"
        identifier = hashlib.sha256(container_name.encode()).hexdigest()
        mounts: list[dict[str, Any]] = []
        for mount in config.get("volumes", []):
            item = {
                "Type": mount["type"],
                "Destination": mount["target"],
                "RW": not mount.get("read_only", False),
            }
            if mount["type"] == "bind":
                item["Source"] = mount["source"].replace("$$", "$")
            elif mount["type"] == "volume":
                item["Name"] = project + "_" + mount["source"]
            mounts.append(item)
        for grant in config.get("secrets", []):
            role = grant["source"]
            mounts.append(
                {
                    "Type": "bind",
                    "Destination": "/run/secrets/" + role,
                    "Source": recipe["secrets"][role]["file"].replace("$$", "$"),
                    "RW": False,
                }
            )
        self.resources["container"][identifier] = {
            "Id": identifier,
            "Name": "/" + container_name,
            "Image": config["image"],
            "Config": {
                "Labels": {
                    **config["labels"],
                    runtime.PROJECT_LABEL: project,
                    "com.docker.compose.service": name,
                },
                "Env": [f"{k}={v}" for k, v in config.get("environment", {}).items()],
                "Cmd": [value.replace("$$", "$") for value in config["command"]]
                if config.get("command") is not None
                else ["image-default-command"],
                "Entrypoint": config.get("entrypoint") or ["image-default-entrypoint"],
            },
            "HostConfig": {
                "RestartPolicy": {"Name": config["restart"]},
                "Privileged": False,
                "ReadonlyRootfs": config.get("read_only", False),
                "CapAdd": config.get("cap_add", []),
                "CapDrop": config.get("cap_drop", []),
                "PortBindings": {
                    "80/tcp": [
                        {
                            "HostIp": "127.0.0.1",
                            "HostPort": config["ports"][0]["published"],
                        }
                    ]
                }
                if name == "caddy"
                else {},
            },
            "NetworkSettings": {
                "Ports": {
                    "80/tcp": [
                        {
                            "HostIp": "127.0.0.1",
                            "HostPort": config["ports"][0]["published"],
                        }
                    ]
                }
                if name == "caddy"
                else {},
                "Networks": {project + "_" + key: {} for key in config["networks"]},
            },
            "Mounts": mounts,
            "State": {
                "Running": name != "init",
                "Status": "exited" if name == "init" else "running",
                "ExitCode": 1 if self.failure == "init" and name == "init" else 0,
                "FinishedAt": "fixed-init-finish",
                "Health": {"Status": "healthy"},
            },
        }
        self.events.append(("up", name))
        if name == "init":
            assert self.pools_paused
        if name in runtime.WORKERS:
            assert self.pools_paused and self.jobs_paused

    def find(self, kind: str, identifier: str) -> dict[str, Any]:
        for item in self.resources[kind].values():
            if identifier in {item["Id"], item["Name"].lstrip("/")}:
                return item
        raise KeyError(identifier)

    def selected(self, kind: str, args: list[str]) -> list[str]:
        result: list[str] = []
        filters = [
            args[index + 1]
            for index, value in enumerate(args[:-1])
            if value == "--filter"
        ]
        for identifier, item in self.resources[kind].items():
            valid = True
            for selection in filters:
                key, value = selection.split("=", 1)
                if key == "name":
                    valid &= re.fullmatch(value, item["Name"].lstrip("/")) is not None
                elif key == "id":
                    valid &= identifier == value
                else:
                    label, wanted = value.split("=", 1)
                    labels = item.get("Config", item).get("Labels", {})
                    valid &= labels.get(label) == wanted
            if valid:
                result.append(identifier)
        return result

    def helper(self, action: str) -> str:
        self.events.append(("helper", action))
        if self.failure == action:
            return json.dumps({"status": "failed", "code": "helper_check_failed"})
        if action == "prepare-pools":
            self.pools_paused = True
        elif action == "pause-jobs":
            self.jobs_paused = True
        elif not self.pools_paused or not self.jobs_paused:
            return json.dumps(
                {"status": "failed", "code": "schedule_pause_not_confirmed"}
            )
        if self.executed:
            return json.dumps({"status": "failed", "code": "unexpected_flow_execution"})
        return json.dumps({"status": "ok", "stage": action})

    def __call__(
        self,
        command: list[str],
        environment: dict[str, str],
        timeout: float,
        stdin: str | None = None,
    ) -> runtime.common.CommandOutput:
        assert "DB_PASSWORD" not in environment
        assert timeout > 0
        try:
            output = self.execute(command, stdin)
            return runtime.common.CommandOutput(stdout=output, stderr="", returncode=0)
        except KeyError:
            return runtime.common.CommandOutput(
                stdout="", stderr="private error", returncode=1
            )

    def execute(self, command: list[str], stdin: str | None) -> str:
        if "--project-directory" in command:
            recipe_path = Path(command[command.index("-f") + 1])
            self.recipe = json.loads(recipe_path.read_text())
            if "config" in command:
                return json.dumps(self.recipe)
            assert "up" in command
            for name in command[command.index("never") + 1 :]:
                self.create_service(name)
                assert "--no-start" in command
                item = self.find("container", self.recipe["name"] + "-" + name + "-1")
                item["State"]["Running"] = False
                item["State"]["Status"] = "created"
            return ""
        assert command[1:3] == ["--host", runtime.SOCKET]
        args = command[3:]
        if args[0] == "info":
            if args[-1] == "{{.ID}}":
                return self.daemon
            return json.dumps(
                {
                    "ID": self.daemon,
                    "OSType": "linux",
                    "Architecture": "amd64",
                    "ServerVersion": self.version,
                    "SecurityOptions": ["rootless"] if self.rootless else [],
                    "Swarm": {"LocalNodeState": "inactive"},
                }
            )
        if args[0] == "ps":
            return "\n".join(self.selected("container", args))
        if args[0] in {"volume", "network", "container"}:
            if args[1] == "ls":
                return "\n".join(self.selected(args[0], args))
            return json.dumps([self.find(args[0], args[-1])])
        if args[:2] == ["image", "inspect"]:
            return json.dumps(
                [
                    {
                        "Id": "sha256:" + "a" * 64,
                        "Os": "linux",
                        "Architecture": "amd64",
                        "Config": {
                            "User": "other" if self.failure == "image_root" else "",
                            "Cmd": ["image-default-command"],
                            "Entrypoint": ["image-default-entrypoint"],
                        },
                    }
                ]
            )
        if args[0] == "rm":
            if self.failure == "probe_cleanup":
                raise runtime.common.RehearsalRuntimeError("command_failed")
            self.resources["container"].pop(args[-1])
            return ""
        if args[0] == "run":
            if self.failure == "probe_cleanup":
                name = args[args.index("--name") + 1]
                identifier = hashlib.sha256(name.encode()).hexdigest()
                self.resources["container"][identifier] = {
                    "Name": "/" + name,
                    "Id": identifier,
                    "State": {"Running": True},
                    "Config": {
                        "Labels": dict([args[args.index("--label") + 1].split("=", 1)])
                    },
                }
            if any("import pwd" in arg for arg in args):
                return "invalid" if self.failure == "image_app" else "valid"
            if "sapphire_flow.cli.deployment_install" in args:
                return self.helper(args[-1])
            if stdin and "authenticated" in stdin:
                self.events.append(("authenticate",))
                return "failed" if self.failure == "authentication" else "authenticated"
            assert args[args.index("--network") + 1] == "none"
            assert args[args.index("--cap-drop") + 1] == "ALL"
            assert "--read-only" in args
            assert args[args.index("--user") + 1] in {"0", "1000:1000"}
            assert all(
                arg.endswith(",readonly")
                for arg in args
                if arg.startswith("type=bind,")
            )
            if self.failure == "unreadable" and "1000:1000" in args:
                raise KeyError("unreadable")
            self.events.append(("read-deliveries",))
            return "readable"
        if args[0] == "exec":
            if "sapphire_flow.cli.deployment_install" in args:
                return self.helper(args[-1])
            assert "psql" in args
            self.events.append(("database-check",))
            return "failed" if self.failure == "database" else "ok"
        if args[0] == "wait":
            return str(self.find("container", args[-1])["State"]["ExitCode"])
        if args[0] in {"start", "stop"}:
            item = self.find("container", args[-1])
            service = item["Config"]["Labels"]["com.docker.compose.service"]
            if args[0] == "start" and service == "init":
                assert ("start", "init") not in self.events
            self.events.append((args[0], service))
            item["State"]["Running"] = args[0] == "start"
            item["State"]["Status"] = "running" if args[0] == "start" else "exited"
            if service == "init":
                item["State"]["Running"] = False
                item["State"]["Status"] = "exited"
            return ""
        raise AssertionError(args)


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, ...]:
    for key in (
        "DB_PASSWORD",
        "ACCESS_TOKEN_PEPPER",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    ):
        monkeypatch.delenv(key, raising=False)
    inputs = tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    profile, installation = materialize_offline_fixture(inputs)
    installation.write_text(
        installation.read_text()
        .replace('SAPPHIRE_DOMAIN = "example.invalid"', 'SAPPHIRE_DOMAIN = ":80"')
        .replace(
            'SAPPHIRE_CORS_ORIGINS = "https://example.invalid"',
            'SAPPHIRE_CORS_ORIGINS = ""',
        )
    )
    delivery_dir = inputs / "secret-delivery"
    delivery_dir.mkdir(mode=0o700)
    for index, role in enumerate(state.ROLES):
        path = delivery_dir / role
        path.write_text(str(index) * 64 + "\n")
        path.chmod(0o444)
    selection = select_deployment_profile(
        profile_file=profile, installation_file=installation, ambient_environment={}
    )
    fake = DockerFake()

    def port_available(port: int) -> None:
        assert port == 48189

    def identity(*args: Any) -> str:
        return "a" * 40

    def compose_executable(selection: Any) -> str:
        return "/compose"

    def host_access(session: runtime.Session) -> None:
        fake.events.append(("host-access", str(session.record.http_port)))

    monkeypatch.setattr(runtime, "check_host_port", port_available)
    monkeypatch.setattr(runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runtime.platform, "machine", lambda: "amd64")
    monkeypatch.setattr(runtime.common, "source_identity", identity)
    monkeypatch.setattr(runtime.common, "compose_executable", compose_executable)
    monkeypatch.setattr(
        runtime,
        "host_access",
        host_access,
    )

    def images(
        session: Any, probes: Any, recipe: Any, environment: Any
    ) -> dict[str, str]:
        return {
            service["image"]: "sha256:"
            + hashlib.sha256(service["image"].encode()).hexdigest()
            for service in recipe["services"].values()
        }

    monkeypatch.setattr(runtime, "resolve_images", images)
    layout = runtime.preview.InstallationLayout(
        state_dir=tmp_path / "state", http_port=48189
    )
    lock = tmp_path / "lock"
    return selection, layout, fake, lock


def install(setup: tuple[Any, ...]) -> tuple[int, dict[str, Any]]:
    selection, layout, fake, lock = setup
    return runtime.install(
        selection,
        layout,
        executor=fake,
        clock=fake.clock,
        sleep=fake.sleep,
        lock_path=lock,
    )


def lifecycle(setup: tuple[Any, ...], command: Any) -> tuple[int, dict[str, Any]]:
    _, layout, fake, lock = setup
    return runtime.lifecycle(
        command,
        layout.state_dir,
        executor=fake,
        clock=fake.clock,
        sleep=fake.sleep,
        lock_path=lock,
    )


class TestPersistentLifecycle:
    def test_frozen_recipe_round_trips_real_compose_without_daemon(
        self, setup: tuple[Any, ...]
    ) -> None:
        selection, layout, _, _ = setup
        assert install(setup)[0] == 0
        record = state.load_record(layout.state_dir)
        driver = runtime.common.Runtime(
            directory=layout.state_dir, deadline=runtime.time.monotonic() + 60
        )
        session = runtime.Session(driver, record)
        runtime.verify_render(
            session,
            find_compose(selection),
            state.load_recipe(layout.state_dir, record),
        )

    def test_install_verify_stop_start_retains_data_and_never_restarts_init(
        self, setup: tuple[Any, ...]
    ) -> None:
        _, layout, fake, _ = setup
        code, report = install(setup)
        assert (code, report["status"]) == (0, "INSTALLED_PAUSED"), report
        volumes = copy.deepcopy(fake.resources["volume"])
        init = copy.deepcopy(fake.find("container", fake.recipe["name"] + "-init-1"))
        before = len(fake.events)
        state_bytes = {
            path.name: path.read_bytes()
            for path in layout.state_dir.iterdir()
            if path.is_file()
        }
        assert lifecycle(setup, "verify")[1]["status"] == "VERIFIED_PAUSED"
        assert {
            path.name: path.read_bytes()
            for path in layout.state_dir.iterdir()
            if path.is_file()
        } == state_bytes
        assert ("helper", "api") not in fake.events[before:]
        assert lifecycle(setup, "stop")[1]["status"] == "STOPPED"
        assert lifecycle(setup, "verify")[1]["status"] == "STOPPED"
        before = len(fake.events)
        assert lifecycle(setup, "start")[1]["status"] == "INSTALLED_PAUSED"
        events = fake.events[before:]
        assert events.index(("helper", "verify-state")) < events.index(
            ("start", "prefect-worker")
        )
        assert not any(event[0] == "up" for event in events)
        assert fake.find("container", fake.recipe["name"] + "-init-1") == init
        assert fake.resources["volume"] == volumes

    @pytest.mark.parametrize(
        "failure",
        [
            "prepare-pools",
            "init",
            "database",
            "authentication",
            "pause-jobs",
            "workers",
            "api",
        ],
    )
    def test_failed_install_stops_owned_services_and_keeps_data(
        self, setup: tuple[Any, ...], failure: str
    ) -> None:
        selection, layout, fake, _ = setup
        before = state.read_deliveries(dict(selection.installation.secret_bindings))
        fake.failure = failure
        code, report = install(setup)
        assert code == 1
        assert report["containment"] == "stopped_data_retained", report
        assert (
            state.read_deliveries(dict(selection.installation.secret_bindings))
            == before
        )
        assert fake.resources["volume"]
        assert all(
            not item["State"]["Running"]
            for item in fake.resources["container"].values()
        )
        assert (layout.state_dir / "installation.json").exists()
        with pytest.raises(runtime.common.ProfileError, match="profile-installation"):
            install(setup)
        assert lifecycle(setup, "stop")[0] == 0
        with pytest.raises(state.InstallError, match="partial_installation_refused"):
            lifecycle(setup, "start")

    def test_changed_credential_refuses_before_start_but_does_not_block_stop(
        self, setup: tuple[Any, ...]
    ) -> None:
        selection, _, fake, _ = setup
        assert install(setup)[0] == 0
        pepper = dict(selection.installation.secret_bindings)["access_token_pepper"]
        pepper.chmod(0o600)
        pepper.write_text("changed")
        assert lifecycle(setup, "stop")[0] == 0
        before = len(fake.events)
        with pytest.raises(state.InstallError, match="credential_changed"):
            lifecycle(setup, "start")
        assert fake.events[before:] == []

    def test_unpaused_jobs_refuse_restart_and_contain_started_infrastructure(
        self, setup: tuple[Any, ...]
    ) -> None:
        _, _, fake, _ = setup
        assert install(setup)[0] == 0
        assert lifecycle(setup, "stop")[0] == 0
        fake.jobs_paused = False
        before = len(fake.events)
        code, report = lifecycle(setup, "start")
        assert code == 2 and report["containment"] == "stopped_data_retained"
        assert not any(
            event == ("start", worker)
            for event in fake.events[before:]
            for worker in runtime.WORKERS
        )

    def test_foreign_daemon_refuses_without_mutation(
        self, setup: tuple[Any, ...]
    ) -> None:
        _, _, fake, _ = setup
        assert install(setup)[0] == 0
        fake.daemon = "another-daemon"
        before = len(fake.events)
        with pytest.raises(state.InstallError, match="daemon_changed"):
            lifecycle(setup, "stop")
        assert fake.events[before:] == []

    def test_actual_secret_mount_must_be_read_only(
        self, setup: tuple[Any, ...]
    ) -> None:
        _, _, fake, _ = setup
        assert install(setup)[0] == 0
        api = fake.find("container", fake.recipe["name"] + "-api-1")
        for mount in api["Mounts"]:
            if mount["Destination"] == "/run/secrets/access_token_pepper":
                mount["RW"] = True
        with pytest.raises(state.InstallError, match="credential_mount_invalid"):
            lifecycle(setup, "verify")

    def test_foreign_label_refuses_stop_without_touching_containers(
        self, setup: tuple[Any, ...]
    ) -> None:
        _, _, fake, _ = setup
        assert install(setup)[0] == 0
        api = fake.find("container", fake.recipe["name"] + "-api-1")
        api["Config"]["Labels"][runtime.LABEL] = "foreign"
        before = len(fake.events)
        with pytest.raises(state.InstallError, match="foreign_resource_refused"):
            lifecycle(setup, "stop")
        assert fake.events[before:] == []

    def test_missing_credential_refuses_restart_without_fallback(
        self, setup: tuple[Any, ...]
    ) -> None:
        selection, _, fake, _ = setup
        assert install(setup)[0] == 0
        assert lifecycle(setup, "stop")[0] == 0
        dict(selection.installation.secret_bindings)[
            "sapphire_api_db_password"
        ].unlink()
        before = len(fake.events)
        with pytest.raises(state.InstallError, match="credential_delivery_unavailable"):
            lifecycle(setup, "start")
        assert fake.events[before:] == []

    def test_credential_permission_probe_failure_uses_no_persistent_state(
        self, setup: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, layout, fake, _ = setup

        def refuse(*args: Any) -> None:
            raise state.InstallError("credential_readability_failed")

        monkeypatch.setattr(runtime, "probe_deliveries", refuse)
        code, result = install(setup)
        assert code == 2 and result["code"] == "credential_readability_failed"
        assert not layout.state_dir.exists()
        assert not any(fake.resources.values())

    @pytest.mark.parametrize("case", ["old_engine", "rootless"])
    def test_host_preflight_refusal_leaves_target_unused(
        self, setup: tuple[Any, ...], case: str
    ) -> None:
        _, layout, fake, _ = setup
        if case == "old_engine":
            fake.version = "27.5.0"
        else:
            fake.rootless = True
        code, report = install(setup)
        assert code == 2 and report["status"] == "REFUSED"
        assert not layout.state_dir.exists()
        assert not any(fake.resources.values())

    def test_writable_delivery_refuses_before_container_entrypoint_runs(
        self, setup: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, _, fake, _ = setup
        create = fake.create_service

        def writable_mount(name: str) -> None:
            create(name)
            item = fake.find("container", fake.recipe["name"] + "-" + name + "-1")
            for mount in item["Mounts"]:
                if mount["Destination"].startswith("/run/secrets/"):
                    mount["RW"] = True

        monkeypatch.setattr(fake, "create_service", writable_mount)
        code, result = install(setup)
        assert code == 1
        assert result["code"] == "readonly_delivery_mount_not_confirmed"
        assert result["containment"] == "stopped_data_retained"
        assert not any(event[0] == "start" for event in fake.events)

    def test_interruption_stops_services_and_retains_data(
        self, setup: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, layout, fake, _ = setup
        original = fake.helper

        def interrupted(action: str) -> str:
            if action == "pause-jobs":
                raise KeyboardInterrupt
            return original(action)

        monkeypatch.setattr(fake, "helper", interrupted)
        code, result = install(setup)
        assert code == 1 and result["code"] == "interrupted"
        assert result["containment"] == "stopped_data_retained"
        assert fake.resources["volume"] and layout.state_dir.is_dir()
        assert all(
            not item["State"]["Running"]
            for item in fake.resources["container"].values()
        )

    def test_verify_unhealthy_service_refuses_without_repair(
        self, setup: tuple[Any, ...]
    ) -> None:
        _, _, fake, _ = setup
        assert install(setup)[0] == 0
        postgres = fake.find("container", fake.recipe["name"] + "-postgres-1")
        postgres["State"]["Health"]["Status"] = "unhealthy"
        before = len(fake.events)
        with pytest.raises(state.InstallError, match="service_unhealthy"):
            lifecycle(setup, "verify")
        assert fake.events[before:] == []

    def test_wildcard_publication_refuses_verification_without_repair(
        self, setup: tuple[Any, ...]
    ) -> None:
        _, _, fake, _ = setup
        assert install(setup)[0] == 0
        caddy = fake.find("container", fake.recipe["name"] + "-caddy-1")
        caddy["HostConfig"]["PortBindings"]["80/tcp"][0]["HostIp"] = "0.0.0.0"
        before = len(fake.events)
        with pytest.raises(state.InstallError, match="port_publication_changed"):
            lifecycle(setup, "verify")
        assert fake.events[before:] == []

    def test_original_deliveries_survive_entire_lifecycle(
        self, setup: tuple[Any, ...]
    ) -> None:
        selection, _, _, _ = setup
        before = state.read_deliveries(dict(selection.installation.secret_bindings))
        assert install(setup)[0] == 0
        assert lifecycle(setup, "stop")[0] == 0
        assert lifecycle(setup, "start")[0] == 0
        assert lifecycle(setup, "verify")[0] == 0
        assert (
            state.read_deliveries(dict(selection.installation.secret_bindings))
            == before
        )

    def test_stop_failure_reports_incomplete_and_retains_resources(
        self, setup: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, layout, fake, _ = setup
        assert install(setup)[0] == 0
        execute = fake.execute

        def fail_stop(command: list[str], stdin: str | None) -> str:
            if command[3:4] == ["stop"]:
                raise runtime.common.RehearsalRuntimeError("command_timeout")
            return execute(command, stdin)

        monkeypatch.setattr(fake, "execute", fail_stop)
        code, result = lifecycle(setup, "stop")
        assert code == 1 and result["containment"] == "incomplete"
        assert result["code"] == "command_timeout"
        assert layout.state_dir.exists() and fake.resources["volume"]
        assert state.load_record(layout.state_dir).containment == "incomplete"

    @pytest.mark.parametrize("failure", ["image_root", "image_app"])
    def test_image_reader_drift_refuses_before_persistent_mutation(
        self, setup: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch, failure: str
    ) -> None:
        _, layout, fake, _ = setup
        fake.failure = failure
        monkeypatch.setattr(runtime, "resolve_images", resolve_images)

        def identity(*args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(image_id="sha256:" + "a" * 64)

        monkeypatch.setattr(runtime.common.image_probe, "probe_image", identity)
        code, result = install(setup)
        assert code == 2 and result["code"] == "image_consumer_user_invalid"
        assert not layout.state_dir.exists()
        assert not any(fake.resources.values())

    def test_non_root_credential_reader_failure_refuses_before_state(
        self, setup: tuple[Any, ...]
    ) -> None:
        _, layout, fake, _ = setup
        fake.failure = "unreadable"
        code, result = install(setup)
        assert code == 2 and result["code"] == "command_failed"
        assert not layout.state_dir.exists()
        assert not any(fake.resources.values())

    def test_actual_publications_must_match_configured_loopback(
        self, setup: tuple[Any, ...]
    ) -> None:
        _, _, fake, _ = setup
        assert install(setup)[0] == 0
        api = fake.find("container", fake.recipe["name"] + "-api-1")
        api["NetworkSettings"]["Ports"] = {
            "8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8000"}]
        }
        with pytest.raises(state.InstallError, match="actual_publication_changed"):
            lifecycle(setup, "verify")

    def test_reports_and_private_records_never_store_raw_credentials(
        self, setup: tuple[Any, ...]
    ) -> None:
        selection, layout, _, _ = setup
        deliveries = state.read_deliveries(dict(selection.installation.secret_bindings))
        _, result = install(setup)
        content = json.dumps(result).encode() + b"".join(
            path.read_bytes() for path in layout.state_dir.rglob("*") if path.is_file()
        )
        assert all(delivery.effective not in content for delivery in deliveries)

    def test_resolved_manager_symlink_is_a_supported_delivery(
        self, setup: tuple[Any, ...]
    ) -> None:
        selection, _, _, _ = setup
        delivery = dict(selection.installation.secret_bindings)["db_password"]
        target = delivery.with_name("delivered-owner")
        delivery.rename(target)
        delivery.symlink_to(target)
        selected = select_deployment_profile(
            profile_file=selection.profile.manifest_path,
            installation_file=selection.installation.source_path,
            ambient_environment={},
        )
        assert selected.installation is not None
        assert (
            dict(selected.installation.secret_bindings)["db_password"]
            == target.resolve()
        )
        updated = (selected, *setup[1:])
        assert install(updated)[0] == 0

    def test_probe_cleanup_failure_retains_recovery_record(
        self, setup: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, layout, fake, _ = setup
        recovery = layout.state_dir.parent / "preflight-recovery"

        def temporary(*args: Any, **kwargs: Any) -> str:
            recovery.mkdir(mode=0o700)
            return str(recovery)

        monkeypatch.setattr(runtime.tempfile, "mkdtemp", temporary)
        fake.failure = "probe_cleanup"
        code, result = install(setup)
        assert code == 1 and result["code"] == "probe_cleanup_incomplete"
        assert result["containment"] == "incomplete"
        assert not layout.state_dir.exists()
        assert list(recovery.glob("probes-*.json"))
        assert fake.resources["container"]

    def test_restart_attempts_use_distinct_probe_names_and_records(
        self, setup: tuple[Any, ...]
    ) -> None:
        _, layout, _, _ = setup
        assert install(setup)[0] == 0
        for _ in range(2):
            assert lifecycle(setup, "stop")[0] == 0
            assert lifecycle(setup, "start")[0] == 0
        records = list(layout.state_dir.glob("probes-*.json"))
        assert len(records) == 2
        first, second = [set(json.loads(path.read_text())["names"]) for path in records]
        assert first and second and first.isdisjoint(second)

    @pytest.mark.parametrize("name", ["DB_PASSWORD", "ACCESS_TOKEN_PEPPER"])
    def test_secret_environment_fallback_is_refused(
        self, setup: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch, name: str
    ) -> None:
        _, layout, fake, _ = setup
        monkeypatch.setenv(name, "PRIVATE_AMBIENT_CANARY")
        with pytest.raises(
            state.InstallError, match="ambient_runtime_override_refused"
        ):
            install(setup)
        assert not layout.state_dir.exists() and not fake.events

    def test_group_writable_state_parent_is_refused(
        self, setup: tuple[Any, ...]
    ) -> None:
        _, layout, fake, _ = setup
        layout.state_dir.parent.chmod(0o770)
        with pytest.raises(state.InstallError, match="state_parent_unsafe"):
            install(setup)
        assert not layout.state_dir.exists() and not fake.events

    def test_engine_command_decodes_compose_dollars_but_still_rejects_drift(
        self, setup: tuple[Any, ...]
    ) -> None:
        _, _, fake, _ = setup
        code, result = install(setup)
        assert code == 0, result["code"]
        prefect = fake.find("container", fake.recipe["name"] + "-prefect-server-1")
        assert "$(cat" in prefect["Config"]["Cmd"][-1]
        assert "$$(cat" not in prefect["Config"]["Cmd"][-1]
        prefect["Config"]["Cmd"] = ["sh", "-c", "unexpected"]
        with pytest.raises(state.InstallError, match="container_configuration_changed"):
            lifecycle(setup, "verify")

    def test_inherited_image_entrypoint_is_checked_not_ignored(
        self, setup: tuple[Any, ...]
    ) -> None:
        _, _, fake, _ = setup
        assert install(setup)[0] == 0
        postgres = fake.find("container", fake.recipe["name"] + "-postgres-1")
        postgres["Config"]["Entrypoint"] = ["unexpected"]
        with pytest.raises(state.InstallError, match="container_configuration_changed"):
            lifecycle(setup, "verify")
