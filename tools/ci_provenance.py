from __future__ import annotations

import contextlib
import hashlib
import http.client
import importlib.util
import io
import ipaddress
import json
import os
import re
import signal
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

API_HOST = "api.github.com"
REPOSITORY = "hydrosolutions/SAPPHIRE_flow"
MAX_METADATA = 2 * 1024 * 1024
MAX_TOTAL_METADATA = 16 * 1024 * 1024
MAX_ARCHIVE = 1024 * 1024
MAX_PROOF = 64 * 1024
MAX_API_CALLS = 64
ADMISSION_SECONDS = 120
IDLE_SECONDS = 10


class ProofError(ValueError):
    """Fixed-code refusal; never include remote data or credentials."""


def parse_object(raw: bytes, maximum: int = MAX_METADATA) -> dict[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ProofError("duplicate_json_field")
            result[key] = value
        return result

    if not 0 < len(raw) <= maximum:
        raise ProofError("metadata_size")

    def nonfinite(value: str) -> object:
        raise ProofError("nonfinite_json_number")

    try:
        result = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
    except (ValueError, UnicodeError) as exc:
        raise ProofError("invalid_json") from exc
    if not isinstance(result, dict):
        raise ProofError("expected_object")
    return cast("dict[str, object]", result)


@dataclass(kw_only=True, slots=True)
class Budget:
    deadline: float
    clock: Callable[[], float] = time.monotonic
    calls: int = 0
    metadata_bytes: int = 0

    def remaining(self) -> float:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise ProofError("admission_deadline")
        return remaining

    def timeout(self) -> float:
        return min(IDLE_SECONDS, self.remaining())

    def api_call(self) -> None:
        self.remaining()
        self.calls += 1
        if self.calls > MAX_API_CALLS:
            raise ProofError("api_call_limit")

    def metadata(self, length: int) -> None:
        self.remaining()
        self.metadata_bytes += length
        if self.metadata_bytes > MAX_TOTAL_METADATA:
            raise ProofError("metadata_total_limit")


@contextlib.contextmanager
def deadline_guard(budget: Budget) -> Iterator[None]:
    # Socket idle limits alone do not bound a slow-drip HTTP header/TLS operation.
    def expired(signum: int, frame: object) -> None:
        raise ProofError("admission_deadline")

    started = time.monotonic()
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    seconds = budget.remaining()
    if previous_timer[0] > 0:
        seconds = min(seconds, previous_timer[0])
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
        budget.remaining()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(
                signal.ITIMER_REAL,
                max(0.000001, previous_timer[0] - (time.monotonic() - started)),
                previous_timer[1],
            )


def public_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if "%" in value:
        raise ProofError("unsupported_address")
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ProofError("unsupported_address") from exc
    if (
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or address.is_loopback
        or address.is_link_local
    ):
        raise ProofError("nonpublic_address")
    if isinstance(address, ipaddress.IPv4Address):
        if any(
            address in ipaddress.ip_network(net)
            for net in ("192.0.0.0/24", "192.88.99.0/24")
        ):
            raise ProofError("special_address")
    elif (
        address not in ipaddress.ip_network("2000::/3")
        or any(
            address in ipaddress.ip_network(net)
            for net in ("2001::/23", "2002::/16", "3fff::/20")
        )
        or address.ipv4_mapped is not None
        or address.sixtofour is not None
        or address.teredo is not None
    ):
        raise ProofError("special_address")
    return address


def storage_location(location: str) -> tuple[str, str]:
    if (
        not location.isascii()
        or len(location.encode()) > 8192
        or any(ord(c) <= 32 or ord(c) == 127 for c in location)
        or "\\" in location
    ):
        raise ProofError("invalid_storage_location")
    try:
        parts = urlsplit(location)
        host = parts.hostname or ""
        port = parts.port
    except ValueError as exc:
        raise ProofError("invalid_storage_location") from exc
    if (
        parts.scheme != "https"
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or "#" in location
        or port not in (None, 443)
        or not 1 <= len(host) <= 253
        or parts.netloc not in (host, host + ":443")
        or any(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
            for label in host.split(".")
        )
    ):
        raise ProofError("invalid_storage_location")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ProofError("ip_literal_location")
    return host, (parts.path or "/") + ("?" + parts.query if parts.query else "")


_RESOLVE = """import json,socket,sys
rows=socket.getaddrinfo(sys.argv[1],443,type=socket.SOCK_STREAM)
assert 0<len(rows)<=32
addresses=list(dict.fromkeys(row[4][0] for row in rows))
print(json.dumps(addresses))
"""


def resolve_public(host: str, budget: Budget) -> tuple[str, ...]:
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", _RESOLVE, host],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={},
        start_new_session=True,
    )
    try:
        raw, _ = child.communicate(timeout=budget.timeout())
    except BaseException as exc:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGKILL)
        if child.stdout is not None:
            with contextlib.suppress(OSError):
                child.stdout.close()
        try:
            child.wait(timeout=1)
        except subprocess.TimeoutExpired as cleanup_error:
            raise ProofError("resolver_cleanup") from cleanup_error
        if isinstance(exc, subprocess.TimeoutExpired):
            raise ProofError("resolver_deadline") from exc
        raise
    budget.remaining()
    if child.returncode != 0 or not 0 < len(raw) <= 4096:
        raise ProofError("resolver_failure")
    try:
        addresses = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ProofError("resolver_failure") from exc
    if (
        not isinstance(addresses, list)
        or not 1 <= len(cast("list[object]", addresses)) <= 32
        or any(not isinstance(a, str) for a in cast("list[object]", addresses))
    ):
        raise ProofError("resolver_failure")
    # Validate ALL answers before selecting one. There is no retry/failover.
    return tuple(str(public_address(a)) for a in cast("list[str]", addresses))


class PinnedConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        address: str,
        budget: Budget,
        *,
        context: ssl.SSLContext | None = None,
        socket_factory: Callable[..., socket.socket] = socket.socket,
    ) -> None:
        self.address = ipaddress.ip_address(address)
        self.budget = budget
        self.socket_factory = socket_factory
        context = context or ssl.create_default_context()
        if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
            raise ProofError("tls_verification_required")
        self.tls_context = context
        super().__init__(host, port=443, timeout=budget.timeout(), context=context)

    def connect(self) -> None:
        family = socket.AF_INET6 if self.address.version == 6 else socket.AF_INET
        raw = self.socket_factory(family, socket.SOCK_STREAM)
        try:
            raw.settimeout(self.budget.timeout())
            target = (
                (str(self.address), 443, 0, 0)
                if family == socket.AF_INET6
                else (str(self.address), 443)
            )
            raw.connect(target)
            if ipaddress.ip_address(raw.getpeername()[0]) != self.address:
                raise ProofError("tls_peer_mismatch")
            raw.settimeout(self.budget.timeout())
            self.sock = self.tls_context.wrap_socket(raw, server_hostname=self.host)
            if ipaddress.ip_address(self.sock.getpeername()[0]) != self.address:
                raise ProofError("tls_peer_mismatch")
            self.budget.remaining()
        except BaseException:
            raw.close()
            if self.sock is not None:
                self.sock.close()
            raise


@dataclass(frozen=True, kw_only=True, slots=True)
class Response:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def single(self, name: str) -> str | None:
        values = [v for k, v in self.headers if k.lower() == name.lower()]
        if len(values) > 1:
            raise ProofError("duplicate_http_header")
        return values[0] if values else None


def canonical_api_path(path: str) -> bool:
    prefix = "/repos/" + REPOSITORY + "/actions/"
    if not path.startswith(prefix) or len(path) > 256:
        return False
    number = r"[1-9][0-9]{0,19}"
    page = r"\?per_page=100&page=[1-9][0-9]?"
    pattern = (
        rf"(?:runs/{number}(?:/attempts/{number}(?:/jobs{page})?|/artifacts{page})?"
        rf"|artifacts/{number}(?:/zip)?|workflows/ci\.yml)"
    )
    return re.fullmatch(pattern, path[len(prefix) :]) is not None


class GitHubReader:
    def __init__(
        self,
        token: str,
        budget: Budget,
        *,
        resolver: Callable[[str, Budget], tuple[str, ...]] = resolve_public,
        connection: Callable[[str, str, Budget], PinnedConnection] = PinnedConnection,
    ) -> None:
        if not token or any(ord(c) <= 32 or ord(c) == 127 for c in token):
            raise ProofError("missing_api_credential")
        self._token = token
        self.budget = budget
        self.resolver = resolver
        self.connection = connection

    def request(
        self,
        host: str,
        path: str,
        *,
        authenticated: bool,
        maximum: int,
        redirect: bool = False,
    ) -> Response:
        with deadline_guard(self.budget):
            return self._request(
                host,
                path,
                authenticated=authenticated,
                maximum=maximum,
                redirect=redirect,
            )

    def _request(
        self,
        host: str,
        path: str,
        *,
        authenticated: bool,
        maximum: int,
        redirect: bool = False,
    ) -> Response:
        if authenticated:
            if not self._token:
                raise ProofError("credential_retired")
            if host != API_HOST or not canonical_api_path(path):
                raise ProofError("invalid_api_route")
            self.budget.api_call()
        addresses = self.resolver(host, self.budget)
        if not addresses:
            raise ProofError("resolver_failure")
        for address in addresses:
            public_address(address)
        connection = self.connection(host, addresses[0], self.budget)
        headers = {"Host": host, "Accept-Encoding": "identity"}
        if authenticated:
            headers.update(
                {
                    "Authorization": "Bearer " + self._token,
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "sapphire-flow-ci-proof",
                }
            )
        try:
            connection.connect()
            if connection.sock is None:
                raise ProofError("connection_failure")
            connection.sock.settimeout(self.budget.timeout())
            connection.request("GET", path, headers=headers)
            connection.sock.settimeout(self.budget.timeout())
            active_socket = connection.sock
            reply = connection.getresponse()
            response = Response(
                status=reply.status, headers=tuple(reply.getheaders()), body=b""
            )
            if response.single("Content-Encoding") not in (None, "identity"):
                raise ProofError("unsupported_content_encoding")
            if (
                sum(len(key) + len(value) + 4 for key, value in response.headers)
                > 32768
            ):
                raise ProofError("http_header_limit")
            length = response.single("Content-Length")
            transfer = response.single("Transfer-Encoding")
            if transfer not in (None, "chunked") or (
                transfer is not None and length is not None
            ):
                raise ProofError("ambiguous_http_framing")
            if length is not None and (
                re.fullmatch(r"[0-9]+", length) is None or int(length) > maximum
            ):
                raise ProofError("http_size_limit")
            if redirect:
                if reply.status != 302 or response.single("Location") is None:
                    raise ProofError("archive_redirect_required")
                self.budget.remaining()
                return response
            if reply.status != 200:
                raise ProofError("http_status")
            chunks: list[bytes] = []
            size = 0
            while True:
                self.budget.remaining()
                if reply.isclosed():
                    break
                active_socket.settimeout(self.budget.timeout())
                chunk = reply.read1(min(65536, maximum + 1 - size))
                self.budget.remaining()
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum:
                    raise ProofError("http_size_limit")
                chunks.append(chunk)
            if length is not None and size != int(length):
                raise ProofError("http_length_mismatch")
            if authenticated:
                self.budget.metadata(size)
            return Response(
                status=reply.status, headers=response.headers, body=b"".join(chunks)
            )
        except (OSError, http.client.HTTPException) as exc:
            self.budget.remaining()
            raise ProofError("transport_failure") from exc
        finally:
            connection.close()

    def metadata(self, suffix: str) -> dict[str, object]:
        response = self.request(
            API_HOST,
            "/repos/" + REPOSITORY + "/actions/" + suffix,
            authenticated=True,
            maximum=MAX_METADATA,
        )
        return parse_object(response.body)

    def items(self, suffix: str, key: str) -> list[dict[str, object]]:
        if key not in ("jobs", "artifacts"):
            raise ProofError("invalid_collection")
        collected: list[dict[str, object]] = []
        identifiers: set[int] = set()
        expected: int | None = None
        for page in range(1, 5):
            data = self.metadata(suffix + f"?per_page=100&page={page}")
            total = data.get("total_count")
            entries = data.get(key)
            if (
                type(total) is not int
                or not 0 <= total <= 400
                or not isinstance(entries, list)
            ):
                raise ProofError("invalid_collection")
            if expected is None:
                expected = total
            if total != expected:
                raise ProofError("collection_changed")
            page_items = cast("list[object]", entries)
            if len(page_items) > 100:
                raise ProofError("collection_page_limit")
            for entry in page_items:
                if not isinstance(entry, dict):
                    raise ProofError("invalid_collection_item")
                item = cast("dict[str, object]", entry)
                identifier = item.get("id")
                if (
                    type(identifier) is not int
                    or identifier <= 0
                    or identifier in identifiers
                ):
                    raise ProofError("duplicate_collection_item")
                identifiers.add(identifier)
                collected.append(item)
            if len(collected) == expected:
                return collected
            if not page_items or len(collected) > expected:
                raise ProofError("incomplete_collection")
        raise ProofError("collection_page_limit")

    def archive(self, artifact_id: str, expected_digest: str) -> bytes:
        if (
            re.fullmatch(r"[1-9][0-9]*", artifact_id) is None
            or re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None
        ):
            raise ProofError("invalid_artifact_identity")
        response = self.request(
            API_HOST,
            f"/repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip",
            authenticated=True,
            maximum=MAX_METADATA,
            redirect=True,
        )
        location = response.single("Location")
        if location is None:
            raise ProofError("archive_redirect_required")
        host, path = storage_location(location)
        archive = self.request(
            host, path, authenticated=False, maximum=MAX_ARCHIVE
        ).body
        if hashlib.sha256(archive).hexdigest() != expected_digest:
            raise ProofError("archive_digest_mismatch")
        return archive

    def forget_credential(self) -> None:
        self._token = ""


@dataclass(frozen=True, kw_only=True, slots=True)
class Instant:
    seconds: Decimal

    def __post_init__(self) -> None:
        if not self.seconds.is_finite():
            raise ProofError("invalid_timestamp")

    @classmethod
    def parse(cls, value: object) -> Instant:
        if not isinstance(value, str):
            raise ProofError("missing_timestamp")
        match = re.fullmatch(
            r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})"
            r"(?:\.([0-9]{1,9}))?(Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])",
            value,
        )
        if match is None or match[3] == "-00:00":
            raise ProofError("ambiguous_timestamp")
        try:
            base = datetime.fromisoformat(match[1] + match[3].replace("Z", "+00:00"))
            delta = base - datetime(1970, 1, 1, tzinfo=UTC)
        except ValueError as exc:
            raise ProofError("invalid_timestamp") from exc
        whole = delta.days * 86400 + delta.seconds
        return cls(seconds=Decimal(whole) + Decimal("0." + (match[2] or "0")))

    def require_age_at(self, admitted: Instant) -> None:
        age = admitted.seconds - self.seconds
        if not Decimal(0) <= age <= Decimal(30 * 86400):
            raise ProofError("proof_age")


def archive_object(raw: bytes) -> dict[str, object]:
    if not 0 < len(raw) <= MAX_ARCHIVE:
        raise ProofError("archive_size")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if len(members) != 1:
                raise ProofError("archive_members")
            member = members[0]
            if (
                member.filename != "full-ci-proof.json"
                or member.header_offset != 0
                or member.flag_bits & 1
                or member.compress_type
                not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                or stat.S_IFMT(member.external_attr >> 16) not in (0, stat.S_IFREG)
                or not 0 < member.file_size <= MAX_PROOF
                or member.compress_size <= 0
                or member.file_size > member.compress_size * 100
            ):
                raise ProofError("archive_member_policy")
            data = archive.read(member)
            if len(data) != member.file_size:
                raise ProofError("archive_member_size")
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        raise ProofError("invalid_archive") from exc
    return parse_object(data, maximum=MAX_PROOF)


@dataclass(frozen=True, kw_only=True, slots=True)
class ApprovedProof:
    run_id: str
    run_attempt: str
    artifact_id: str
    artifact_archive_sha256: str
    source_sha: str
    source_tree: str

    def __post_init__(self) -> None:
        for value in (self.run_id, self.run_attempt, self.artifact_id):
            if re.fullmatch(r"[1-9][0-9]{0,19}", value) is None:
                raise ProofError("invalid_approved_identity")
        for value in (self.source_sha, self.source_tree):
            if re.fullmatch(r"[0-9a-f]{40}", value) is None:
                raise ProofError("invalid_approved_source")
        if re.fullmatch(r"[0-9a-f]{64}", self.artifact_archive_sha256) is None:
            raise ProofError("invalid_approved_digest")


@dataclass(frozen=True, kw_only=True, slots=True)
class CheckoutFacts:
    head: str
    tree: str
    parents: tuple[str, ...]
    workflow_blob: str

    def __post_init__(self) -> None:
        if not 1 <= len(self.parents) <= 32 or any(
            re.fullmatch(r"[0-9a-f]{40}", value) is None
            for value in (self.head, self.tree, self.workflow_blob, *self.parents)
        ):
            raise ProofError("invalid_checkout_facts")


def object_field(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProofError("missing_object")
    return cast("dict[str, object]", value)


def api_id(value: object) -> str:
    if type(value) is not int or not 0 < value < 10**20:
        raise ProofError("invalid_api_identity")
    return str(value)


def require_run(
    data: dict[str, object], approved: ApprovedProof, workflow_id: str
) -> None:
    repository = object_field(data.get("repository"))
    if (
        api_id(data.get("id")) != approved.run_id
        or api_id(data.get("run_attempt")) != approved.run_attempt
        or api_id(data.get("workflow_id")) != workflow_id
        or repository.get("full_name") != REPOSITORY
        or data.get("head_sha") != approved.source_sha
        or data.get("event") != "push"
        or data.get("head_branch") != "main"
        or data.get("status") != "completed"
        or data.get("conclusion") != "success"
    ):
        raise ProofError("ineligible_run")


def require_artifact(
    data: dict[str, object], approved: ApprovedProof, repository_id: str
) -> None:
    association = object_field(data.get("workflow_run"))
    size = data.get("size_in_bytes")
    if (
        api_id(data.get("id")) != approved.artifact_id
        or data.get("name") != f"full-ci-proof-{approved.run_id}-{approved.run_attempt}"
        or data.get("expired") is not False
        or data.get("digest") != "sha256:" + approved.artifact_archive_sha256
        or type(size) is not int
        or not 0 < size <= MAX_ARCHIVE
        or api_id(association.get("id")) != approved.run_id
        or api_id(association.get("repository_id")) != repository_id
        or api_id(association.get("head_repository_id")) != repository_id
        or association.get("head_sha") != approved.source_sha
        or association.get("head_branch") != "main"
    ):
        raise ProofError("ineligible_artifact")


def validate_producer(
    document: dict[str, object],
    approved: ApprovedProof,
    facts: CheckoutFacts,
    jobs: list[dict[str, object]],
    started: Instant,
    artifact_created: Instant,
    sampled_now: Instant,
    expected_jobs: frozenset[str],
) -> None:
    expected_fields = {
        "schema_version",
        "qualification",
        "execution_kind",
        "run_id",
        "run_attempt",
        "checkout",
        "mandatory_jobs",
    }
    if (
        set(document) != expected_fields
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["qualification"] != "FULL"
        or document["execution_kind"] != "full"
        or api_id(document["run_id"]) != approved.run_id
        or api_id(document["run_attempt"]) != approved.run_attempt
        or document["checkout"]
        != {
            "head": facts.head,
            "tree": facts.tree,
            "parents": list(facts.parents),
            "workflow_blob": facts.workflow_blob,
        }
    ):
        raise ProofError("invalid_producer_proof")
    terminal = [job for job in jobs if job.get("name") == "ci-attest"]
    if len(terminal) != 1:
        raise ProofError("missing_terminal_producer")
    attest = terminal[0]
    if (
        attest.get("status") != "completed"
        or attest.get("conclusion") != "success"
        or api_id(attest.get("run_attempt")) != approved.run_attempt
        or api_id(attest.get("run_id")) != approved.run_id
    ):
        raise ProofError("unsuccessful_terminal_producer")
    terminal_start = Instant.parse(attest.get("started_at"))
    terminal_end = Instant.parse(attest.get("completed_at"))
    if (
        not started.seconds
        <= terminal_start.seconds
        <= artifact_created.seconds
        <= terminal_end.seconds
        <= sampled_now.seconds
    ):
        raise ProofError("artifact_attempt_window")
    summaries = document["mandatory_jobs"]
    if (
        not isinstance(summaries, list)
        or not 1 <= len(cast("list[object]", summaries)) <= 400
    ):
        raise ProofError("missing_producer_jobs")
    by_id = {api_id(job.get("id")): job for job in jobs}
    if len(by_id) != len(jobs):
        raise ProofError("duplicate_job_identity")
    seen: set[str] = set()
    seen_names: set[str] = set()
    for raw in cast("list[object]", summaries):
        summary = object_field(raw)
        if set(summary) != {"id", "name", "started_at", "completed_at"}:
            raise ProofError("invalid_producer_job")
        identifier = api_id(summary["id"])
        if identifier in seen or identifier not in by_id:
            raise ProofError("invalid_producer_job")
        seen.add(identifier)
        name = summary.get("name")
        if not isinstance(name, str) or name in seen_names:
            raise ProofError("producer_job_inventory")
        seen_names.add(name)
        job = by_id[identifier]
        if (
            summary != {key: job.get(key) for key in summary}
            or job.get("name") == "ci-attest"
            or job.get("status") != "completed"
            or job.get("conclusion") != "success"
            or api_id(job.get("run_id")) != approved.run_id
            or api_id(job.get("run_attempt")) != approved.run_attempt
        ):
            raise ProofError("producer_job_attempt")
        first = Instant.parse(summary["started_at"])
        last = Instant.parse(summary["completed_at"])
        if (
            not started.seconds
            <= first.seconds
            <= last.seconds
            <= terminal_start.seconds
        ):
            raise ProofError("producer_job_time_order")
    if frozenset(seen_names) != expected_jobs:
        raise ProofError("producer_job_inventory")


def admit(
    approved: ApprovedProof,
    facts: CheckoutFacts,
    reader: GitHubReader,
    *,
    clock: Callable[[], datetime],
    expected_jobs: frozenset[str],
    destination: Path,
    decision: Callable[[dict[str, object]], None] | None = None,
) -> bytes:
    staged_identity: tuple[int, int] | None = None
    try:
        if (facts.head, facts.tree) != (approved.source_sha, approved.source_tree):
            raise ProofError("checkout_source_mismatch")
        with deadline_guard(reader.budget):
            entry_sample = reader.budget.clock()
            entry = reader.metadata(f"runs/{approved.run_id}")
            workflow = reader.metadata("workflows/ci.yml")
            if (
                workflow.get("path") != ".github/workflows/ci.yml"
                or workflow.get("state") != "active"
            ):
                raise ProofError("workflow_identity")
            workflow_id = api_id(workflow.get("id"))
            require_run(entry, approved, workflow_id)
            repository_id = api_id(object_field(entry.get("repository")).get("id"))
            attempt = reader.metadata(
                f"runs/{approved.run_id}/attempts/{approved.run_attempt}"
            )
            require_run(attempt, approved, workflow_id)
            started = Instant.parse(attempt.get("run_started_at"))
            jobs = reader.items(
                f"runs/{approved.run_id}/attempts/{approved.run_attempt}/jobs", "jobs"
            )
            artifacts = reader.items(f"runs/{approved.run_id}/artifacts", "artifacts")
            candidates = [
                item
                for item in artifacts
                if item.get("name")
                == f"full-ci-proof-{approved.run_id}-{approved.run_attempt}"
            ]
            if len(candidates) != 1:
                raise ProofError("artifact_not_unique")
            require_artifact(candidates[0], approved, repository_id)
            artifact = reader.metadata(f"artifacts/{approved.artifact_id}")
            require_artifact(artifact, approved, repository_id)
            if artifact.get("created_at") != candidates[0].get("created_at"):
                raise ProofError("artifact_metadata_changed")
            document = archive_object(
                reader.archive(approved.artifact_id, approved.artifact_archive_sha256)
            )
            sampled = clock()
            if sampled.tzinfo is None or sampled.utcoffset() is None:
                raise ProofError("naive_admission_clock")
            now = Instant.parse(sampled.isoformat())
            started.require_age_at(now)
            validate_producer(
                document,
                approved,
                facts,
                jobs,
                started,
                Instant.parse(artifact.get("created_at")),
                now,
                expected_jobs,
            )
            normalized = {
                "schema_version": 1,
                "repository": REPOSITORY,
                "workflow_path": ".github/workflows/ci.yml",
                "workflow_blob": facts.workflow_blob,
                "event": "push",
                "head_branch": "main",
                "run_id": approved.run_id,
                "run_attempt": approved.run_attempt,
                "artifact_id": approved.artifact_id,
                "artifact_archive_sha256": approved.artifact_archive_sha256,
                "source_sha": approved.source_sha,
                "source_tree": approved.source_tree,
                "checkout_sha": facts.head,
                "checkout_tree": facts.tree,
                "checkout_parents": list(facts.parents),
                "execution_kind": "full",
                "qualification": "complete",
                "producer": "ci-attest",
            }
            raw = (
                json.dumps(normalized, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            if len(raw) > MAX_PROOF:
                raise ProofError("normalized_proof_size")
            # Stage privately. The last API operation is the latest-attempt check.
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, prefix=".ci-proof-"
            ) as staged:
                info = os.fstat(staged.fileno())
                staged_identity = (info.st_dev, info.st_ino)
                staged.write(raw)
                staged.flush()
                os.fsync(staged.fileno())
                os.fchmod(staged.fileno(), 0o400)
                final = reader.metadata(f"runs/{approved.run_id}")
                require_run(final, approved, workflow_id)
                if decision is not None:
                    decision(
                        {
                            "age_outcome": "eligible",
                            "bound_run_id": approved.run_id,
                            "bound_attempt": approved.run_attempt,
                            "entry_latest_attempt": api_id(entry.get("run_attempt")),
                            "final_latest_attempt": api_id(final.get("run_attempt")),
                            "entry_sample_monotonic": entry_sample,
                            "final_sample_monotonic": reader.budget.clock(),
                            "attempt_started_at": attempt["run_started_at"],
                            "sampled_now": sampled.isoformat(),
                            "maximum_age_seconds": 30 * 86400,
                            "proof_sha256": hashlib.sha256(raw).hexdigest(),
                        }
                    )
                reader.budget.remaining()
                os.link(staged.name, destination)
            return raw
    except BaseException:
        if staged_identity is not None:
            with contextlib.suppress(FileNotFoundError):
                current = destination.lstat()
                if (current.st_dev, current.st_ino) == staged_identity:
                    destination.unlink()
        raise
    finally:
        reader.forget_credential()


def main() -> int:
    # The credential never enters a generic environment snapshot or child env.
    token = os.environ.pop("GH_TOKEN", "")
    for name in (
        "GITHUB_TOKEN",
        "ACTIONS_RUNTIME_TOKEN",
        "ACTIONS_CACHE_URL",
        "ACTIONS_RESULTS_URL",
    ):
        os.environ.pop(name, None)
    reader: GitHubReader | None = None
    work: Path | None = None
    try:
        spec = importlib.util.spec_from_file_location(
            "ci_admission_stage", Path(__file__).with_name("staging_release.py")
        )
        if spec is None or spec.loader is None:
            raise ProofError("controller_unavailable")
        stage = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = stage
        spec.loader.exec_module(stage)
        if any(os.environ.get(name) for name in stage.SECRET_NAMES):
            raise ProofError("private_credential_in_proof_step")
        env = dict(os.environ)
        root = Path(env["GITHUB_WORKSPACE"]).resolve()
        os.chdir(root)
        binding = stage.require_local_release(root, env)
        if not isinstance(binding, stage.transfer.ReuseBinding):
            return 0
        proof_work = Path(
            stage.transfer.proof_directory(Path(env["RUNNER_TEMP"]), binding)
        )
        work = proof_work
        if (
            proof_work == root
            or root in proof_work.parents
            or proof_work in root.parents
        ):
            raise ProofError("proof_directory_inside_checkout")
        os.umask(0o077)
        work.mkdir(mode=0o700)
        approved = ApprovedProof(
            **asdict(binding.ci),
            source_sha=binding.source_sha,
            source_tree=binding.source_tree,
        )
        budget = Budget(deadline=time.monotonic() + ADMISSION_SECONDS)
        producer = stage.transfer.tool("ci_attest")
        local = producer.commit_facts(root, binding.source_sha, budget.timeout())
        facts = CheckoutFacts(
            head=local["head"],
            tree=local["tree"],
            parents=tuple(local["parents"]),
            workflow_blob=local["workflow_blob"],
        )
        reader = GitHubReader(token, budget)
        token = ""

        def record(value: dict[str, object]) -> None:
            stage.transfer.write_once(
                proof_work / "age-decision.json", stage.transfer.json_bytes(value)
            )

        raw = admit(
            approved,
            facts,
            reader,
            clock=lambda: datetime.now(UTC),
            expected_jobs=frozenset(producer.MANDATORY_STEPS),
            destination=work / "ci-proof.json",
            decision=record,
        )
        with Path(env["GITHUB_OUTPUT"]).open("a") as output:
            output.write("proof_sha256=" + hashlib.sha256(raw).hexdigest() + "\n")
        return 0
    except (Exception, KeyboardInterrupt):
        if work is not None and work.is_dir() and not work.is_symlink():
            with (
                contextlib.suppress(OSError),
                (work / "failure.json").open("xb") as failure,
            ):
                failure.write(
                    b'{"outcome":"blocked","reason":"proof_admission_failed"}\n'
                )
        return 1
    finally:
        token = ""
        if reader is not None:
            reader.forget_credential()


if __name__ == "__main__":
    raise SystemExit(main())
