from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[3] / "tools/ci_provenance.py"
spec = importlib.util.spec_from_file_location("ci_provenance_test", TOOL)
assert spec is not None and spec.loader is not None
proof = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = proof
spec.loader.exec_module(proof)

producer_spec = importlib.util.spec_from_file_location(
    "ci_admission_producer_test", TOOL.with_name("ci_attest.py")
)
assert producer_spec is not None and producer_spec.loader is not None
producer = importlib.util.module_from_spec(producer_spec)
sys.modules[producer_spec.name] = producer
producer_spec.loader.exec_module(producer)
EXPECTED_JOBS = frozenset(producer.MANDATORY_STEPS)


class TestProofMetadata:
    def test_duplicate_fields_refuse(self) -> None:
        with pytest.raises(proof.ProofError, match="invalid_json"):
            proof.parse_object(b'{"id":1,"id":2}')

    def test_non_object_refuses(self) -> None:
        with pytest.raises(proof.ProofError, match="expected_object"):
            proof.parse_object(b"[]")


class TestStorageLocation:
    @pytest.mark.parametrize(
        "url",
        [
            "http://public.example/blob",
            "https://public.example:444/blob",
            "https://u:p@public.example/blob",
            "https://Public.example/blob",
            "https://public.example./blob",
            "https://public.example/blob#fragment",
            "https://127.0.0.1/blob",
            "https://[2606:4700::1111]/blob",
            "https://public.example\\evil/blob",
            "https://public.example/blob\n",
            "https://public%2eexample/blob",
            "https://public.example:/blob",
        ],
    )
    def test_ambiguous_or_unapproved_location_refuses(self, url: str) -> None:
        with pytest.raises(proof.ProofError):
            proof.storage_location(url)

    def test_exact_https_host_and_signed_path_remain_separate(self) -> None:
        assert proof.storage_location(
            "https://storage.example:443/blob?sig=synthetic"
        ) == ("storage.example", "/blob?sig=synthetic")


class TestPublicDestination:
    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254",
            "100.64.0.1",
            "192.0.0.9",
            "192.88.99.1",
            "224.0.0.1",
            "240.0.0.1",
            "::1",
            "::ffff:8.8.8.8",
            "64:ff9b::808:808",
            "64:ff9b:1::1",
            "2002:808:808::1",
            "2001::1",
            "2001:db8::1",
            "3fff::1",
            "2606:4700::1111%eth0",
            "fe80::1",
            "fc00::1",
            "ff00::1",
        ],
    )
    def test_nonpublic_and_transition_addresses_refuse(self, address: str) -> None:
        with pytest.raises(proof.ProofError):
            proof.public_address(address)

    @pytest.mark.parametrize("address", ["8.8.8.8", "2606:4700:4700::1111"])
    def test_ordinary_global_unicast_is_supported(self, address: str) -> None:
        assert str(proof.public_address(address)) == address


class TestAdmissionBudget:
    def test_total_deadline_does_not_reset_at_calls(self) -> None:
        now = [100.0]
        budget = proof.Budget(deadline=220.0, clock=lambda: now[0])
        budget.api_call()
        now[0] = 219.5
        assert budget.timeout() == 0.5
        now[0] = 220.0
        with pytest.raises(proof.ProofError, match="admission_deadline"):
            budget.api_call()

    def test_cumulative_metadata_limit(self) -> None:
        budget = proof.Budget(deadline=200.0, clock=lambda: 100.0)
        budget.metadata(proof.MAX_TOTAL_METADATA)
        with pytest.raises(proof.ProofError, match="metadata_total_limit"):
            budget.metadata(1)

    def test_call_limit(self) -> None:
        budget = proof.Budget(deadline=200.0, clock=lambda: 100.0)
        for _ in range(64):
            budget.api_call()
        with pytest.raises(proof.ProofError, match="api_call_limit"):
            budget.api_call()


class TestTransportBoundary:
    def test_mixed_dns_answers_refuse_before_connect(self) -> None:
        budget = proof.Budget(deadline=proof.time.monotonic() + 20)
        connected = []
        reader = proof.GitHubReader(
            "synthetic-token",
            budget,
            resolver=lambda host, budget: ("8.8.8.8", "127.0.0.1"),
            connection=lambda *args: connected.append(args),
        )
        with pytest.raises(proof.ProofError, match="nonpublic_address"):
            reader.metadata("runs/1")
        assert connected == []

    def test_only_canonical_api_can_receive_credential(self) -> None:
        budget = proof.Budget(deadline=proof.time.monotonic() + 20)
        reader = proof.GitHubReader("synthetic-token", budget)
        with pytest.raises(proof.ProofError, match="invalid_api_route"):
            reader.request(
                "untrusted.example",
                "/repos/other/actions/runs/1",
                authenticated=True,
                maximum=100,
            )

    def test_unverified_tls_context_refuses(self) -> None:
        import ssl

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        budget = proof.Budget(deadline=proof.time.monotonic() + 20)
        with pytest.raises(proof.ProofError, match="tls_verification_required"):
            proof.PinnedConnection("api.github.com", "8.8.8.8", budget, context=context)


class TestDeadlineGuard:
    def test_blocked_socket_is_cancelled_by_total_deadline(self) -> None:
        import socket
        import time

        reader, writer = socket.socketpair()
        started = time.monotonic()
        try:
            with (
                pytest.raises(proof.ProofError, match="admission_deadline"),
                proof.deadline_guard(proof.Budget(deadline=started + 0.05)),
            ):
                reader.recv(1)
        finally:
            reader.close()
            writer.close()
        assert time.monotonic() - started < 2

    def test_nested_guards_restore_outer_timer_and_handler(self) -> None:
        import signal
        import time

        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        assert previous_timer[0] == 0
        with proof.deadline_guard(proof.Budget(deadline=time.monotonic() + 10)):
            outer_handler = signal.getsignal(signal.SIGALRM)
            with proof.deadline_guard(proof.Budget(deadline=time.monotonic() + 20)):
                assert 0 < signal.getitimer(signal.ITIMER_REAL)[0] <= 10
            assert signal.getsignal(signal.SIGALRM) is outer_handler
            assert 0 < signal.getitimer(signal.ITIMER_REAL)[0] <= 10
        assert signal.getsignal(signal.SIGALRM) is previous_handler
        assert signal.getitimer(signal.ITIMER_REAL) == previous_timer


@pytest.fixture
def tls_endpoint(tmp_path: Path):
    import contextlib
    import socket
    import ssl
    import threading
    from datetime import UTC, datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ci-proof-test")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime(2020, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2040, 1, 1, tzinfo=UTC))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("api.github.com"), x509.DNSName("storage.example")]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    certificate_path = tmp_path / "certificate.pem"
    key_path = tmp_path / "key.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certificate_path, key_path)
    client_context = ssl.create_default_context(cafile=str(certificate_path))
    snis = []
    server_context.set_servername_callback(
        lambda connection, name, context: snis.append(name)
    )

    @contextlib.contextmanager
    def serve(
        responses: list[bytes],
        *,
        stall: bool = False,
        raw_peer: str = "8.8.8.8",
        tls_peer: str = "8.8.8.8",
    ):
        class RoutedTlsSocket(ssl.SSLSocket):
            def getpeername(self) -> tuple:
                actual = super().getpeername()
                assert actual[0] == "127.0.0.1"
                return (tls_peer, actual[1])

        client_context.sslsocket_class = RoutedTlsSocket
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        listener.settimeout(3)
        destination = listener.getsockname()
        requests = []
        stopped = threading.Event()
        failures = []

        def worker() -> None:
            try:
                for response in responses:
                    raw, _ = listener.accept()
                    raw.settimeout(3)
                    with server_context.wrap_socket(raw, server_side=True) as secured:
                        incoming = b""
                        while b"\r\n\r\n" not in incoming:
                            chunk = secured.recv(4096)
                            if not chunk:
                                raise OSError("test client closed before headers")
                            incoming += chunk
                            if len(incoming) > 32768:
                                raise AssertionError("test request exceeded limit")
                        requests.append(incoming)
                        secured.sendall(response)
                        if stall:
                            stopped.wait(3)
            except (OSError, ssl.SSLError) as error:
                failures.append(type(error).__name__)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        class RoutedSocket(socket.socket):
            def connect(self, address: tuple) -> None:
                assert address == ("8.8.8.8", 443)
                super().connect(destination)

            def getpeername(self) -> tuple:
                actual = super().getpeername()
                assert actual[0] == "127.0.0.1"
                return (raw_peer, actual[1])

        def connection(host: str, address: str, budget: object):
            assert address == "8.8.8.8"
            return proof.PinnedConnection(
                host,
                address,
                budget,
                context=client_context,
                socket_factory=RoutedSocket,
            )

        try:
            yield connection, requests, snis, failures
        finally:
            stopped.set()
            listener.close()
            thread.join(timeout=5)
            assert not thread.is_alive()

    return serve


class TestActualTlsTransport:
    def test_archive_uses_original_tls_hosts_but_strips_all_credentials(
        self, tls_endpoint, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import hashlib
        import time

        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("https_proxy", "http://127.0.0.1:1")
        monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
        body = b"synthetic-archive"
        redirect = (
            b"HTTP/1.1 302 Found\r\nContent-Length: 0\r\n"
            b"Location: https://storage.example/blob?sig=synthetic-secret\r\n"
            b"Set-Cookie: secret=cookie\r\n\r\n"
        )
        success = (
            b"HTTP/1.1 200 OK\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\n\r\n"
            + body
        )
        with tls_endpoint([redirect, success]) as (
            connection,
            requests,
            snis,
            failures,
        ):
            reader = proof.GitHubReader(
                "synthetic-token",
                proof.Budget(deadline=time.monotonic() + 5),
                resolver=lambda host, budget: ("8.8.8.8",),
                connection=connection,
            )
            assert reader.archive("1", hashlib.sha256(body).hexdigest()) == body
        assert snis == ["api.github.com", "storage.example"]
        assert b"Host: api.github.com" in requests[0]
        assert b"Authorization: Bearer synthetic-token" in requests[0]
        assert b"Host: storage.example" in requests[1]
        assert b"sig=synthetic-secret" in requests[1]
        assert b"Authorization:" not in requests[1]
        assert b"Cookie:" not in requests[1]
        assert b"Proxy-Authorization:" not in requests[1]
        assert failures == []

    def test_partial_header_cannot_outlive_total_deadline(self, tls_endpoint) -> None:
        import time

        with tls_endpoint([b"HTTP/1.1 200 OK\r\nX-Drip: "], stall=True) as (
            connection,
            requests,
            snis,
            failures,
        ):
            reader = proof.GitHubReader(
                "synthetic-token",
                proof.Budget(deadline=time.monotonic() + 0.5),
                resolver=lambda host, budget: ("8.8.8.8",),
                connection=connection,
            )
            with pytest.raises(proof.ProofError, match="admission_deadline"):
                reader.metadata("runs/1")
        assert len(requests) == 1

    def test_hostname_mismatch_is_not_accepted(self, tls_endpoint) -> None:
        import time

        with tls_endpoint([b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"]) as (
            connection,
            requests,
            snis,
            failures,
        ):
            reader = proof.GitHubReader(
                "synthetic-token",
                proof.Budget(deadline=time.monotonic() + 5),
                resolver=lambda host, budget: ("8.8.8.8",),
                connection=connection,
            )
            with pytest.raises(proof.ProofError, match="transport_failure"):
                reader.request(
                    "wrong.example", "/blob", authenticated=False, maximum=100
                )
        assert requests == []
        assert snis == ["wrong.example"]


class TestResolverProcess:
    def test_blocked_resolver_is_killed_and_reaped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time

        children = []
        original = proof.subprocess.Popen

        def spawn(*args, **kwargs):
            assert kwargs["env"] == {}
            child = original(*args, **kwargs)
            children.append(child)
            return child

        monkeypatch.setattr(proof, "_RESOLVE", "import signal; signal.pause()")
        monkeypatch.setattr(proof.subprocess, "Popen", spawn)
        with pytest.raises(proof.ProofError, match="resolver_deadline"):
            proof.resolve_public(
                "synthetic.example", proof.Budget(deadline=time.monotonic() + 0.2)
            )
        assert len(children) == 1
        assert children[0].returncode is not None

    def test_one_private_answer_refuses_all_answers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time

        monkeypatch.setattr(
            proof,
            "_RESOLVE",
            'import json; print(json.dumps(["8.8.8.8", "127.0.0.1"]))',
        )
        with pytest.raises(proof.ProofError, match="nonpublic_address"):
            proof.resolve_public(
                "synthetic.example", proof.Budget(deadline=time.monotonic() + 5)
            )


class TestCanonicalApiPath:
    @pytest.mark.parametrize(
        "suffix",
        [
            "runs/1/../../other",
            "runs/1?url=https://untrusted.example",
            "runs/1/artifacts?per_page=100&page=0",
            "runs/1/attempts/1/jobs",
            "runs/1/attempts/1/jobs?per_page=100&page=1&next=untrusted",
            "workflows/private-verification.yml",
            "runs/1#fragment",
        ],
    )
    def test_only_fixed_pagination_and_routes_are_allowed(self, suffix: str) -> None:
        assert not proof.canonical_api_path(
            "/repos/hydrosolutions/SAPPHIRE_flow/actions/" + suffix
        )


class TestProofClock:
    def test_inclusive_thirty_day_boundary_preserves_nanoseconds(self) -> None:
        started = proof.Instant.parse("2026-10-01T00:00:00.000000001Z")
        started.require_age_at(proof.Instant.parse("2026-10-31T00:00:00.000000001Z"))
        with pytest.raises(proof.ProofError, match="proof_age"):
            started.require_age_at(
                proof.Instant.parse("2026-10-31T00:00:00.000000002Z")
            )

    @pytest.mark.parametrize(
        "timestamp",
        [
            None,
            1,
            "2026-10-01T00:00:00",
            "2026-10-01T00:00:00-00:00",
            "2026-10-01T00:00:00.0000000001Z",
            "2026-10-99T00:00:00Z",
        ],
    )
    def test_missing_or_ambiguous_time_refuses(self, timestamp: object) -> None:
        with pytest.raises(proof.ProofError):
            proof.Instant.parse(timestamp)

    def test_future_execution_refuses(self) -> None:
        with pytest.raises(proof.ProofError, match="proof_age"):
            proof.Instant.parse("2026-10-01T00:00:01Z").require_age_at(
                proof.Instant.parse("2026-10-01T00:00:00Z")
            )


class TestProofArchive:
    @pytest.mark.parametrize(
        "name", ["../full-ci-proof.json", "/full-ci-proof.json", "other.json"]
    )
    def test_only_one_exact_member_name_is_accepted(self, name: str) -> None:
        import io
        import zipfile

        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr(name, b"{}")
        with pytest.raises(proof.ProofError, match="invalid_archive"):
            proof.archive_object(output.getvalue())

    def test_exact_json_member_is_read_without_filesystem_extraction(self) -> None:
        import io
        import zipfile

        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("full-ci-proof.json", b'{"schema_version":1}')
        assert proof.archive_object(output.getvalue()) == {"schema_version": 1}


class TestExactProofBounds:
    def test_compressed_member_above_ratio_limit_refuses(self) -> None:
        import io
        import zipfile

        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("full-ci-proof.json", b" " * (64 * 1024))
        with pytest.raises(proof.ProofError, match="invalid_archive"):
            proof.archive_object(raw.getvalue())

    def test_member_above_64_kib_refuses(self) -> None:
        import io
        import zipfile

        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as archive:
            archive.writestr("full-ci-proof.json", b" " * (64 * 1024 + 1))
        with pytest.raises(proof.ProofError, match="invalid_archive"):
            proof.archive_object(raw.getvalue())

    def test_collection_above_400_entries_refuses(self) -> None:
        import time

        class Reader(proof.GitHubReader):
            def metadata(self, suffix: str) -> dict[str, object]:
                return {"total_count": 401, "jobs": []}

        reader = Reader("synthetic-token", proof.Budget(deadline=time.monotonic() + 5))
        with pytest.raises(proof.ProofError, match="invalid_collection"):
            reader.items("runs/1/attempts/1/jobs", "jobs")

    def test_pages_are_constructed_without_following_external_links(self) -> None:
        import time

        routes = []

        class Reader(proof.GitHubReader):
            def metadata(self, suffix: str) -> dict[str, object]:
                routes.append(suffix)
                return {"total_count": 2, "jobs": [{"id": len(routes)}]}

        reader = Reader("synthetic-token", proof.Budget(deadline=time.monotonic() + 5))
        assert reader.items("runs/1/attempts/2/jobs", "jobs") == [{"id": 1}, {"id": 2}]
        assert routes == [
            "runs/1/attempts/2/jobs?per_page=100&page=1",
            "runs/1/attempts/2/jobs?per_page=100&page=2",
        ]


@pytest.fixture
def admission_case():
    import hashlib
    import io
    import json
    import time
    import zipfile
    from datetime import UTC, datetime

    facts = proof.CheckoutFacts(
        head="a" * 40, tree="b" * 40, parents=("c" * 40,), workflow_blob="d" * 40
    )
    job = {
        "id": 10,
        "name": "lint",
        "run_id": 1,
        "run_attempt": 2,
        "status": "completed",
        "conclusion": "success",
        "started_at": "2026-10-01T00:00:00Z",
        "completed_at": "2026-10-01T00:00:30Z",
    }
    jobs = [
        dict(job, id=index, name=name)
        for index, name in enumerate(sorted(EXPECTED_JOBS), start=1)
    ]
    terminal = dict(
        job,
        id=100,
        name="ci-attest",
        started_at="2026-10-01T00:01:00Z",
        completed_at="2026-10-01T00:01:02Z",
    )
    document = {
        "schema_version": 1,
        "qualification": "FULL",
        "execution_kind": "full",
        "run_id": 1,
        "run_attempt": 2,
        "checkout": {
            "head": facts.head,
            "tree": facts.tree,
            "parents": list(facts.parents),
            "workflow_blob": facts.workflow_blob,
        },
        "mandatory_jobs": [
            {k: item[k] for k in ("id", "name", "started_at", "completed_at")}
            for item in jobs
        ],
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(
            zipfile.ZipInfo("full-ci-proof.json", (2026, 10, 1, 0, 0, 0)),
            json.dumps(document),
        )
    raw = archive.getvalue()
    approved = proof.ApprovedProof(
        run_id="1",
        run_attempt="2",
        artifact_id="3",
        artifact_archive_sha256=hashlib.sha256(raw).hexdigest(),
        source_sha=facts.head,
        source_tree=facts.tree,
    )
    run = {
        "id": 1,
        "run_attempt": 2,
        "workflow_id": 4,
        "repository": {"id": 5, "full_name": proof.REPOSITORY},
        "head_sha": facts.head,
        "event": "push",
        "head_branch": "main",
        "status": "completed",
        "conclusion": "success",
        "run_started_at": "2026-10-01T00:00:00Z",
    }
    artifact = {
        "id": 3,
        "name": "full-ci-proof-1-2",
        "expired": False,
        "digest": "sha256:" + approved.artifact_archive_sha256,
        "size_in_bytes": len(raw),
        "created_at": "2026-10-01T00:01:01Z",
        "workflow_run": {
            "id": 1,
            "repository_id": 5,
            "head_repository_id": 5,
            "head_sha": facts.head,
            "head_branch": "main",
        },
    }

    class Reader:
        def __init__(self) -> None:
            self.budget = proof.Budget(deadline=time.monotonic() + 10)
            self.calls = []
            self.run = run
            self.final = dict(run)
            self.attempt = dict(run)
            self.artifact = artifact
            self.artifacts = [dict(artifact)]
            self.jobs = [*jobs, terminal]
            self.document = document
            self.workflow = {
                "id": 4,
                "path": ".github/workflows/ci.yml",
                "state": "active",
            }
            self.latest_samples = 0
            self.credential_retired = False

        def forget_credential(self) -> None:
            self.credential_retired = True

        def metadata(self, suffix: str) -> dict[str, object]:
            self.budget.api_call()
            self.calls.append(suffix)
            if suffix == "runs/1":
                self.latest_samples += 1
                return self.run if self.latest_samples == 1 else self.final
            return {
                "workflows/ci.yml": self.workflow,
                "runs/1/attempts/2": self.attempt,
                "artifacts/3": self.artifact,
            }[suffix]

        def items(self, suffix: str, key: str) -> list[dict[str, object]]:
            self.calls.append(suffix)
            return self.jobs if key == "jobs" else self.artifacts

        def archive(self, artifact_id: str, expected_digest: str) -> bytes:
            self.calls.append("archive")
            assert artifact_id == "3"
            assert expected_digest == approved.artifact_archive_sha256
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as zipped:
                zipped.writestr(
                    zipfile.ZipInfo("full-ci-proof.json", (2026, 10, 1, 0, 0, 0)),
                    json.dumps(self.document),
                )
            return output.getvalue()

    return approved, facts, Reader(), lambda: datetime(2026, 10, 31, tzinfo=UTC)


class TestProofAdmission:
    def test_freezes_once_only_after_final_latest_attempt_sample(
        self, admission_case, tmp_path: Path
    ) -> None:
        approved, facts, reader, clock = admission_case
        destination = tmp_path / "ci-proof.json"
        raw = proof.admit(
            approved,
            facts,
            reader,
            clock=clock,
            expected_jobs=EXPECTED_JOBS,
            destination=destination,
        )
        assert destination.read_bytes() == raw
        assert reader.calls[0] == reader.calls[-1] == "runs/1"
        assert reader.latest_samples == 2
        assert destination.stat().st_nlink == 1
        assert list(tmp_path.glob(".ci-proof-*")) == []
        assert b"release-version" not in raw
        assert b"https:" not in raw

    @pytest.mark.parametrize(
        "status,conclusion",
        [
            ("queued", None),
            ("completed", "failure"),
            ("completed", "cancelled"),
            ("completed", "success"),
        ],
    )
    def test_newer_attempt_refuses_old_green_artifact(
        self, admission_case, tmp_path: Path, status: str, conclusion: str | None
    ) -> None:
        approved, facts, reader, clock = admission_case
        reader.final.update(run_attempt=3, status=status, conclusion=conclusion)
        destination = tmp_path / "ci-proof.json"
        with pytest.raises(proof.ProofError, match="ineligible_run"):
            proof.admit(
                approved,
                facts,
                reader,
                clock=clock,
                expected_jobs=EXPECTED_JOBS,
                destination=destination,
            )
        assert not destination.exists()
        assert list(tmp_path.glob(".ci-proof-*")) == []

    def test_missing_attempt_start_has_no_job_time_fallback(
        self, admission_case, tmp_path: Path
    ) -> None:
        approved, facts, reader, clock = admission_case
        del reader.attempt["run_started_at"]
        with pytest.raises(proof.ProofError, match="missing_timestamp"):
            proof.admit(
                approved,
                facts,
                reader,
                clock=clock,
                expected_jobs=EXPECTED_JOBS,
                destination=tmp_path / "ci-proof.json",
            )
        assert not (tmp_path / "ci-proof.json").exists()

    def test_artifact_created_outside_terminal_window_refuses(
        self, admission_case, tmp_path: Path
    ) -> None:
        approved, facts, reader, clock = admission_case
        reader.artifact["created_at"] = "2026-10-01T00:00:59Z"
        reader.artifacts[0]["created_at"] = reader.artifact["created_at"]
        with pytest.raises(proof.ProofError, match="artifact_attempt_window"):
            proof.admit(
                approved,
                facts,
                reader,
                clock=clock,
                expected_jobs=EXPECTED_JOBS,
                destination=tmp_path / "ci-proof.json",
            )
        assert not (tmp_path / "ci-proof.json").exists()

    def test_duplicate_named_artifact_refuses_before_download(
        self, admission_case, tmp_path: Path
    ) -> None:
        approved, facts, reader, clock = admission_case
        reader.artifacts.append(dict(reader.artifact, id=99))
        with pytest.raises(proof.ProofError, match="artifact_not_unique"):
            proof.admit(
                approved,
                facts,
                reader,
                clock=clock,
                expected_jobs=EXPECTED_JOBS,
                destination=tmp_path / "ci-proof.json",
            )
        assert "archive" not in reader.calls


class TestAtomicProofFailure:
    def test_deadline_after_link_removes_only_newly_published_proof(
        self, admission_case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        approved, facts, reader, clock = admission_case
        destination = tmp_path / "ci-proof.json"
        original = proof.os.link

        def link_then_expire(source: str, target: Path) -> None:
            original(source, target)
            reader.budget.deadline = 0

        monkeypatch.setattr(proof.os, "link", link_then_expire)
        with pytest.raises(proof.ProofError, match="admission_deadline"):
            proof.admit(
                approved,
                facts,
                reader,
                clock=clock,
                expected_jobs=EXPECTED_JOBS,
                destination=destination,
            )
        assert not destination.exists()
        assert reader.credential_retired
        assert list(tmp_path.glob(".ci-proof-*")) == []

    def test_existing_proof_is_never_overwritten_or_deleted(
        self, admission_case, tmp_path: Path
    ) -> None:
        approved, facts, reader, clock = admission_case
        destination = tmp_path / "ci-proof.json"
        destination.write_bytes(b"existing immutable evidence")
        with pytest.raises(FileExistsError):
            proof.admit(
                approved,
                facts,
                reader,
                clock=clock,
                expected_jobs=EXPECTED_JOBS,
                destination=destination,
            )
        assert destination.read_bytes() == b"existing immutable evidence"
        assert reader.credential_retired


class TestTransportCompletion:
    @pytest.mark.parametrize("framing", [b"Content-Length: 2\r\n", b""])
    def test_connection_close_returns_complete_body(
        self, tls_endpoint, framing: bytes
    ) -> None:
        import time

        response = b"HTTP/1.1 200 OK\r\nConnection: close\r\n" + framing + b"\r\n{}"
        with tls_endpoint([response]) as (connection, requests, snis, failures):
            reader = proof.GitHubReader(
                "synthetic-token",
                proof.Budget(deadline=time.monotonic() + 5),
                resolver=lambda host, budget: ("8.8.8.8",),
                connection=connection,
            )
            assert reader.metadata("runs/1") == {}
        assert len(requests) == 1
        assert failures == []

    @pytest.mark.parametrize("peer", ["raw_peer", "tls_peer"])
    def test_peer_mismatch_never_sends_headers(self, tls_endpoint, peer: str) -> None:
        import time

        with tls_endpoint([b""], **{peer: "8.8.4.4"}) as (
            connection,
            requests,
            snis,
            failures,
        ):
            reader = proof.GitHubReader(
                "synthetic-token",
                proof.Budget(deadline=time.monotonic() + 5),
                resolver=lambda host, budget: ("8.8.8.8",),
                connection=connection,
            )
            with pytest.raises(proof.ProofError, match="peer"):
                reader.metadata("runs/1")
        assert requests == []


class TestStorageRefusals:
    @pytest.mark.parametrize("status", [b"302 Found", b"403 Forbidden"])
    def test_storage_redirect_or_failure_has_no_retry(
        self, tls_endpoint, status: bytes, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import time

        redirect = b"HTTP/1.1 302 Found\r\nContent-Length: 0\r\nLocation: https://storage.example/blob?sig=synthetic-secret\r\n\r\n"
        refused = (
            b"HTTP/1.1 "
            + status
            + b"\r\nContent-Length: 0\r\nLocation: https://other.example/never\r\n\r\n"
        )
        with tls_endpoint([redirect, refused]) as (
            connection,
            requests,
            snis,
            failures,
        ):
            reader = proof.GitHubReader(
                "synthetic-token",
                proof.Budget(deadline=time.monotonic() + 5),
                resolver=lambda host, budget: ("8.8.8.8",),
                connection=connection,
            )
            with pytest.raises(proof.ProofError, match="http_status"):
                reader.archive("1", "e" * 64)
        assert len(requests) == 2
        assert snis == ["api.github.com", "storage.example"]
        assert b"Authorization:" not in requests[1]
        captured = capsys.readouterr()
        assert captured.out == captured.err == ""


class TestAdmissionIdentityRefusals:
    @pytest.mark.parametrize("sample", ["run", "attempt", "final"])
    @pytest.mark.parametrize(
        "field,value",
        [
            ("event", "pull_request"),
            ("head_branch", "feature"),
            ("head_sha", "9" * 40),
            ("workflow_id", 99),
            ("repository", {"id": 5, "full_name": "other/repository"}),
            ("status", "in_progress"),
            ("conclusion", "failure"),
        ],
    )
    def test_run_identity_and_success_required_at_each_sample(
        self, admission_case, tmp_path: Path, sample: str, field: str, value: object
    ) -> None:
        approved, facts, reader, clock = admission_case
        getattr(reader, sample)[field] = value
        destination = tmp_path / "ci-proof.json"
        with pytest.raises(proof.ProofError, match="ineligible_run"):
            proof.admit(
                approved,
                facts,
                reader,
                clock=clock,
                expected_jobs=EXPECTED_JOBS,
                destination=destination,
            )
        assert not destination.exists()
        assert reader.credential_retired
        if sample != "final":
            assert "archive" not in reader.calls

    @pytest.mark.parametrize("field,value", [("head", "9" * 40), ("tree", "9" * 40)])
    def test_local_source_and_tree_must_match_approval(
        self, admission_case, tmp_path: Path, field: str, value: str
    ) -> None:
        from dataclasses import replace

        approved, facts, reader, clock = admission_case
        destination = tmp_path / "ci-proof.json"
        with pytest.raises(proof.ProofError, match="checkout_source_mismatch"):
            proof.admit(
                approved,
                replace(facts, **{field: value}),
                reader,
                clock=clock,
                expected_jobs=EXPECTED_JOBS,
                destination=destination,
            )
        assert not destination.exists()
        assert reader.calls == []
        assert reader.credential_retired

    @pytest.mark.parametrize(
        "field,value",
        [("path", ".github/workflows/other.yml"), ("state", "disabled_manually")],
    )
    def test_workflow_identity_refuses(
        self, admission_case, tmp_path: Path, field: str, value: str
    ) -> None:
        approved, facts, reader, clock = admission_case
        reader.workflow[field] = value
        with pytest.raises(proof.ProofError, match="workflow_identity"):
            proof.admit(
                approved,
                facts,
                reader,
                clock=clock,
                expected_jobs=EXPECTED_JOBS,
                destination=tmp_path / "ci-proof.json",
            )
        assert not (tmp_path / "ci-proof.json").exists()
        assert "archive" not in reader.calls
        assert reader.credential_retired

    @pytest.mark.parametrize("field", ["head", "tree", "workflow_blob", "parents"])
    def test_producer_checkout_mismatch_refuses(
        self, admission_case, tmp_path: Path, field: str
    ) -> None:
        approved, facts, reader, clock = admission_case
        reader.document["checkout"][field] = (
            ["9" * 40] if field == "parents" else "9" * 40
        )
        with pytest.raises(proof.ProofError, match="invalid_producer_proof"):
            proof.admit(
                approved,
                facts,
                reader,
                clock=clock,
                expected_jobs=EXPECTED_JOBS,
                destination=tmp_path / "ci-proof.json",
            )
        assert not (tmp_path / "ci-proof.json").exists()
        assert reader.credential_retired

    @pytest.mark.parametrize(
        "field,value",
        [
            ("id", 99),
            ("name", "wrong"),
            ("digest", "sha256:" + "9" * 64),
            ("expired", True),
            ("expired", None),
            ("expired", 0),
            ("size_in_bytes", 0),
            ("size_in_bytes", 1024 * 1024 + 1),
        ],
    )
    def test_artifact_metadata_refuses(
        self, admission_case, tmp_path: Path, field: str, value: object
    ) -> None:
        approved, facts, reader, clock = admission_case
        reader.artifact[field] = value
        reader.artifacts[0][field] = value
        with pytest.raises(
            proof.ProofError, match="ineligible_artifact|artifact_not_unique"
        ):
            proof.admit(
                approved,
                facts,
                reader,
                clock=clock,
                expected_jobs=EXPECTED_JOBS,
                destination=tmp_path / "ci-proof.json",
            )
        assert not (tmp_path / "ci-proof.json").exists()
        assert "archive" not in reader.calls
        assert reader.credential_retired

    @pytest.mark.parametrize(
        "field,value",
        [
            ("id", 99),
            ("repository_id", 99),
            ("head_repository_id", 99),
            ("head_sha", "9" * 40),
            ("head_branch", "feature"),
        ],
    )
    def test_artifact_run_association_refuses(
        self, admission_case, tmp_path: Path, field: str, value: object
    ) -> None:
        approved, facts, reader, clock = admission_case
        reader.artifact["workflow_run"][field] = value
        with pytest.raises(proof.ProofError, match="ineligible_artifact"):
            proof.admit(
                approved,
                facts,
                reader,
                clock=clock,
                expected_jobs=EXPECTED_JOBS,
                destination=tmp_path / "ci-proof.json",
            )
        assert not (tmp_path / "ci-proof.json").exists()
        assert "archive" not in reader.calls
        assert reader.credential_retired

    @pytest.mark.parametrize("mutation", ["missing", "duplicate", "extra"])
    def test_summary_inventory_must_be_exact(
        self, admission_case, tmp_path: Path, mutation: str
    ) -> None:
        approved, facts, reader, clock = admission_case
        summaries = reader.document["mandatory_jobs"]
        if mutation == "missing":
            summaries.pop()
        elif mutation == "duplicate":
            summaries[-1]["name"] = summaries[0]["name"]
        else:
            job = dict(reader.jobs[0], id=200, name="unapproved-job")
            reader.jobs.append(job)
            summaries.append(
                {key: job[key] for key in ("id", "name", "started_at", "completed_at")}
            )
        with pytest.raises(proof.ProofError, match="producer_job_inventory"):
            proof.admit(
                approved,
                facts,
                reader,
                clock=clock,
                expected_jobs=EXPECTED_JOBS,
                destination=tmp_path / "ci-proof.json",
            )
        assert not (tmp_path / "ci-proof.json").exists()
        assert reader.credential_retired


class TestHostileHttpBodies:
    @pytest.mark.parametrize(
        "headers,body,error",
        [
            (
                b"Content-Encoding: gzip\r\nContent-Length: 2\r\n",
                b"{}",
                "unsupported_content_encoding",
            ),
            (
                b"Transfer-Encoding: chunked\r\nContent-Length: 2\r\n",
                b"{}",
                "ambiguous_http_framing",
            ),
            (b"Transfer-Encoding: gzip\r\n", b"{}", "ambiguous_http_framing"),
            (
                b"Content-Length: 2\r\nContent-Length: 2\r\n",
                b"{}",
                "duplicate_http_header",
            ),
            (b"Content-Length: invalid\r\n", b"{}", "http_size_limit"),
            (b"Content-Length: 9\r\n", b"123456789", "http_size_limit"),
            (b"Content-Length: 5\r\n", b"{}", "http_length_mismatch|transport_failure"),
            (b"", b"123456789", "http_size_limit"),
            (
                b"Transfer-Encoding: chunked\r\n",
                b"9\r\n123456789\r\n0\r\n\r\n",
                "http_size_limit",
            ),
            (b"X-Large: " + b"x" * 33000 + b"\r\n", b"{}", "http_header_limit"),
        ],
    )
    def test_hostile_framing_encoding_and_overflow_refuse(
        self,
        tls_endpoint,
        headers: bytes,
        body: bytes,
        error: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import time

        response = (
            b"HTTP/1.1 200 OK\r\nConnection: close\r\n" + headers + b"\r\n" + body
        )
        with tls_endpoint([response]) as (connection, requests, snis, failures):
            reader = proof.GitHubReader(
                "synthetic-token",
                proof.Budget(deadline=time.monotonic() + 5),
                resolver=lambda host, budget: ("8.8.8.8",),
                connection=connection,
            )
            with pytest.raises(proof.ProofError, match=error):
                reader.request(
                    "api.github.com",
                    f"/repos/{proof.REPOSITORY}/actions/runs/1",
                    authenticated=True,
                    maximum=8,
                )
        assert len(requests) == 1
        captured = capsys.readouterr()
        assert captured.out == captured.err == ""

    def test_valid_chunked_body_remains_supported(self, tls_endpoint) -> None:
        import time

        response = (
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"
            b"Connection: close\r\n\r\n2\r\n{}\r\n0\r\n\r\n"
        )
        with tls_endpoint([response]) as (connection, requests, snis, failures):
            reader = proof.GitHubReader(
                "synthetic-token",
                proof.Budget(deadline=time.monotonic() + 5),
                resolver=lambda host, budget: ("8.8.8.8",),
                connection=connection,
            )
            assert reader.metadata("runs/1") == {}

    def test_body_stall_after_complete_headers_is_bounded(self, tls_endpoint) -> None:
        import time

        response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{"
        with tls_endpoint([response], stall=True) as (
            connection,
            requests,
            snis,
            failures,
        ):
            started = time.monotonic()
            reader = proof.GitHubReader(
                "synthetic-token",
                proof.Budget(deadline=started + 0.5),
                resolver=lambda host, budget: ("8.8.8.8",),
                connection=connection,
            )
            with pytest.raises(
                proof.ProofError, match="admission_deadline|transport_failure"
            ):
                reader.metadata("runs/1")
            assert time.monotonic() - started < 2
        assert len(requests) == 1

    def test_archive_digest_mismatch_refuses_downloaded_bytes(
        self, tls_endpoint, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import time

        redirect = b"HTTP/1.1 302 Found\r\nContent-Length: 0\r\nLocation: https://storage.example/blob?sig=synthetic-secret\r\n\r\n"
        response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"
        with tls_endpoint([redirect, response]) as (
            connection,
            requests,
            snis,
            failures,
        ):
            reader = proof.GitHubReader(
                "synthetic-token",
                proof.Budget(deadline=time.monotonic() + 5),
                resolver=lambda host, budget: ("8.8.8.8",),
                connection=connection,
            )
            with pytest.raises(proof.ProofError, match="archive_digest_mismatch"):
                reader.archive("1", "e" * 64)
        assert len(requests) == 2
        captured = capsys.readouterr()
        assert captured.out == captured.err == ""


@pytest.fixture
def admission_cli_case(admission_case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import importlib.machinery
    import os

    approved, facts, reader, clock = admission_case
    spec = importlib.util.spec_from_file_location(
        "admission_cli_stage_fixture", TOOL.with_name("staging_release.py")
    )
    assert spec is not None and spec.loader is not None
    stage = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = stage
    spec.loader.exec_module(stage)
    binding = stage.transfer.ReuseBinding(
        version="1.2.3",
        source_sha=facts.head,
        source_tree=facts.tree,
        tag_object="c" * 40,
        release_state="d" * 40,
        workflow_sha=facts.head,
        workflow_ref=stage.transfer.REPOSITORY
        + "/.github/workflows/private-verification.yml@refs/tags/v1.2.3",
        run_id="123",
        run_attempt="1",
        actor="owner",
        delivery_mode=stage.transfer.DeliveryMode.DELIVER_ONLY,
        ci=stage.transfer.CIIdentity(
            run_id=approved.run_id,
            run_attempt=approved.run_attempt,
            artifact_id=approved.artifact_id,
            artifact_archive_sha256=approved.artifact_archive_sha256,
        ),
    )
    root, temporary = tmp_path / "repository", tmp_path / "runner"
    root.mkdir()
    temporary.mkdir()
    output = tmp_path / "outputs"
    monkeypatch.chdir(root)
    for name in (*stage.API_BEARER_NAMES, *stage.SECRET_NAMES):
        monkeypatch.delenv(name, raising=False)
    for name in stage.API_BEARER_NAMES:
        monkeypatch.setenv(name, "synthetic-private-bearer")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(root))
    monkeypatch.setenv("RUNNER_TEMP", str(temporary))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    calls = []

    def local_release(root: Path, env: dict) -> object:
        assert not set(stage.API_BEARER_NAMES) & set(env)
        assert not set(stage.API_BEARER_NAMES) & set(os.environ)
        calls.append("source")
        return binding

    def commit_facts(*args: object) -> dict:
        import subprocess

        assert not set(stage.API_BEARER_NAMES) & set(os.environ)
        subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                "import os,subprocess; assert not "
                + repr(set(stage.API_BEARER_NAMES))
                + " & set(os.environ); "
                "subprocess.run(['git','--version'],check=True,capture_output=True)",
            ],
            check=True,
            capture_output=True,
            timeout=5,
        )
        calls.append("git")
        return {
            "head": facts.head,
            "tree": facts.tree,
            "parents": facts.parents,
            "workflow_blob": facts.workflow_blob,
        }

    producer_tool = stage.transfer.tool("ci_attest")
    monkeypatch.setattr(producer_tool, "commit_facts", commit_facts)
    monkeypatch.setattr(stage, "require_local_release", local_release)
    original_spec = importlib.util.spec_from_file_location

    class Loader:
        def create_module(self, spec: object) -> object:
            return stage

        def exec_module(self, module: object) -> None:
            pass

    def route_spec(name: str, location: object, **kwargs: object) -> object:
        if name == "ci_admission_stage":
            return importlib.machinery.ModuleSpec(name, Loader())
        return original_spec(name, location, **kwargs)

    class FixedDatetime(proof.datetime):
        @classmethod
        def now(cls, tz: object = None) -> object:
            return clock()

    def make_reader(token: str, budget: object) -> object:
        assert token == "synthetic-private-bearer"
        assert not set(stage.API_BEARER_NAMES) & set(os.environ)
        calls.append("reader")
        return reader

    monkeypatch.setattr(proof.importlib.util, "spec_from_file_location", route_spec)
    monkeypatch.setattr(proof, "GitHubReader", make_reader)
    monkeypatch.setattr(proof, "datetime", FixedDatetime)
    work = stage.transfer.proof_directory(temporary, binding)
    original_umask = os.umask(0o077)
    try:
        yield stage, binding, reader, work, output, calls
    finally:
        os.umask(original_umask)


class TestAdmissionCLI:
    def test_success_executes_admit_and_hands_off_readonly_hash(
        self, admission_cli_case, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import hashlib
        import os

        stage, binding, reader, work, output, calls = admission_cli_case
        assert proof.main() == 0
        raw = (work / "ci-proof.json").read_bytes()
        assert (
            output.read_text()
            == "proof_sha256=" + hashlib.sha256(raw).hexdigest() + "\n"
        )
        assert (work / "ci-proof.json").stat().st_mode & 0o777 == 0o400
        assert reader.credential_retired
        assert reader.calls[-1] == "runs/1"
        assert not set(stage.API_BEARER_NAMES) & set(os.environ)
        assert calls == ["source", "git", "reader"]
        captured = capsys.readouterr()
        assert captured.out == captured.err == ""

    @pytest.mark.parametrize("failure", ["admission", "output"])
    def test_failure_is_fixed_private_evidence_not_remote_text(
        self,
        admission_cli_case,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        failure: str,
    ) -> None:
        stage, binding, reader, work, output, calls = admission_cli_case
        if failure == "output":
            output.mkdir()
        else:

            def fail(suffix: str) -> dict:
                raise proof.ProofError("PRIVATE_REMOTE_CANARY?sig=synthetic-secret")

            monkeypatch.setattr(reader, "metadata", fail)
        assert proof.main() == 1
        assert (
            work / "failure.json"
        ).read_bytes() == b'{"outcome":"blocked","reason":"proof_admission_failed"}\n'
        assert reader.credential_retired
        if failure == "admission":
            assert not (work / "ci-proof.json").exists()
            assert not output.exists()
        captured = capsys.readouterr()
        assert captured.out == captured.err == ""

    def test_full_profile_never_runs_proof_git_or_api(
        self, admission_cli_case, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dataclasses import asdict

        stage, binding, reader, work, output, calls = admission_cli_case
        fields = asdict(binding)
        del fields["ci"]
        del fields["verification_profile"]
        ordinary = stage.transfer.ReleaseBinding(**fields)
        monkeypatch.setattr(stage, "require_local_release", lambda *args: ordinary)
        assert proof.main() == 0
        assert calls == []
        assert not work.exists()
        assert not output.exists()

    def test_inside_checkout_destination_is_rejected_before_reader(
        self, admission_cli_case, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        stage, binding, reader, work, output, calls = admission_cli_case
        monkeypatch.setenv("RUNNER_TEMP", os.environ["GITHUB_WORKSPACE"])
        assert proof.main() == 1
        assert calls == ["source"]
        assert not output.exists()
