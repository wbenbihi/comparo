"""Production execution composition, ordering, and cleanup security regressions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import msgspec
import pytest

import comparo.application as application
from comparo.adapters.artifacts import LocalArtifactBundleSink
from comparo.adapters.artifacts import LocalArtifactStoreError
from comparo.adapters.artifacts import PosixSanitizedBlobSpool
from comparo.adapters.http import TransientBodyStore
from comparo.adapters.values import LocalFileLimits
from comparo.adapters.values import PinnedProjectFiles
from comparo.application import production_execution as production
from comparo.application.artifacts import ArtifactReadLimits
from comparo.application.artifacts import read_artifact_blob
from comparo.application.artifacts import render_artifact_report
from comparo.application.evaluation_binding import bind_configuration_evaluation
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.preflight import PreparedProjectPlan
from comparo.core.artifacts.capture import ArtifactCapturingDriver
from comparo.core.artifacts.common import ArtifactCaptureExtent
from comparo.core.artifacts.common import ArtifactExecutionOutcome
from comparo.core.artifacts.common import ArtifactFactOutcome
from comparo.core.artifacts.common import ArtifactFailure
from comparo.core.artifacts.common import ArtifactGateVerdict
from comparo.core.artifacts.compiler import ArtifactCompilationStatus
from comparo.core.artifacts.model import RunArtifact
from comparo.core.artifacts.policy import ArtifactPersistMode
from comparo.core.artifacts.ports import ArtifactPersistenceOutcome
from comparo.core.artifacts.ports import ArtifactPersistenceStatus
from comparo.core.artifacts.ports import ArtifactStoreErrorCode
from comparo.core.artifacts.ports import ArtifactStoreFailure
from comparo.core.domain.primitives import ByteCount
from comparo.core.planning import plan_run
from comparo.core.policy import PolicyArtifacts
from comparo.core.policy import planning_authority
from comparo.core.policy import trusted_local_policy
from comparo.core.resolution import RunCancellation
from comparo.core.resolution import RunContext
from comparo.core.runtime import RecordingEventSink
from comparo.core.runtime.ports import EvaluationPreparation
from comparo.core.selection import SelectionSnapshotRequirements
from comparo.reporters import ReportFormat
from tests.core.artifacts.test_artifact_contracts import _artifact
from tests.core.planning.conftest import compile_project
from tests.core.planning.conftest import document
from tests.core.planning.conftest import invocation


def _project(root: Path, *, base_url: str = "https://staging.example") -> PreparedProjectPlan:
    compiled = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/check}], baseline: {$use: staging}, "
            "candidates: [{$use: prod}]}}",
        ),
        document("Environment", "staging", f"{{baseUrl: '{base_url}'}}"),
        document("Environment", "prod", f"{{baseUrl: '{base_url}'}}"),
        document("Request", "check", "{request: {method: GET, endpoint: /check}}"),
    )
    policy = trusted_local_policy()
    planned = plan_run(
        compiled,
        invocation(),
        planning_authority(policy, project_namespace=compiled.namespace),
    )
    assert planned.prepared is not None
    return PreparedProjectPlan(
        prepared=planned.prepared,
        policy=policy,
        project_root=root,
    )


def _request(
    root: Path,
    *,
    persist: ArtifactPersistMode = ArtifactPersistMode.NEVER,
    base_url: str = "https://staging.example",
) -> production.ProductionExecutionRequest:
    settings = production.production_execution_defaults_v1()
    settings = msgspec.structs.replace(
        settings,
        artifact=msgspec.structs.replace(settings.artifact, persist=persist),
    )
    return production.ProductionExecutionRequest(
        project=_project(root, base_url=base_url),
        settings=settings,
        ambient_environment=None,
        artifact_root=root / "artifacts",
        approve_mutations=False,
        tool_version="test",
    )


def _evaluated_project(
    root: Path,
    *,
    base_url: str,
    response_schema: str | None = None,
) -> PreparedProjectPlan:
    schema = "" if response_schema is None else f", responseSchema: {response_schema}"
    compiled = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/check}], baseline: {$use: staging}, "
            "candidates: [{$use: prod}], assertions: [{$use: status-assertions}], "
            "diffs: [{$use: status-diffs}], gate: {$use: strict}}}",
        ),
        document("Environment", "staging", f"{{baseUrl: '{base_url}'}}"),
        document("Environment", "prod", f"{{baseUrl: '{base_url}'}}"),
        document(
            "Request",
            "check",
            f"{{request: {{method: GET, endpoint: /check}}{schema}}}",
        ),
        document(
            "AssertionProfile",
            "status-assertions",
            "{rules: [{id: status-ok, selector: response.status, "
            "operator: {name: comparo.core/equals@1, expected: 200}, severity: blocking, "
            "quantifier: one, completeness: complete}, "
            "{id: body-present, selector: response.body, "
            "operator: {name: comparo.core/notEquals@1, expected: null}, severity: blocking, "
            "quantifier: one, completeness: complete}]}",
        ),
        document(
            "DiffProfile",
            "status-diffs",
            "{rules: [{id: status-exact, selector: status, comparator: {name: exact}, "
            "severity: blocking, completeness: complete}, "
            "{id: body-exact, selector: response.body, comparator: {name: exact}, "
            "severity: blocking, completeness: complete}]}",
        ),
        document("GateProfile", "strict", "{}"),
    )
    policy = trusted_local_policy()
    planned = plan_run(
        compiled,
        invocation(),
        planning_authority(policy, project_namespace=compiled.namespace),
    )
    assert planned.prepared is not None
    return PreparedProjectPlan(
        prepared=planned.prepared,
        policy=policy,
        project_root=root,
    )


def _safety_project(
    root: Path,
    *,
    base_url: str,
    safety: str,
    response_schema: str | None = None,
) -> PreparedProjectPlan:
    schema = "" if response_schema is None else f", responseSchema: {response_schema}"
    compiled = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/check}], baseline: {$use: staging}, "
            "candidates: [{$use: prod}]}}",
        ),
        document(
            "Environment",
            "staging",
            f"{{baseUrl: '{base_url}', safety: {{$use: SafetyProfile/strict}}}}",
        ),
        document(
            "Environment",
            "prod",
            f"{{baseUrl: '{base_url}', safety: {{$use: SafetyProfile/strict}}}}",
        ),
        document(
            "Request",
            "check",
            f"{{request: {{method: GET, endpoint: /check}}{schema}}}",
        ),
        document("SafetyProfile", "strict", safety),
    )
    policy = trusted_local_policy()
    planned = plan_run(
        compiled,
        invocation(),
        planning_authority(policy, project_namespace=compiled.namespace),
    )
    assert planned.prepared is not None
    return PreparedProjectPlan(
        prepared=planned.prepared,
        policy=policy,
        project_root=root,
    )


def _secret_assertion_project(
    root: Path,
    *,
    base_url: str,
    canary: str,
) -> PreparedProjectPlan:
    compiled = compile_project(
        document(
            "Project",
            "demo",
            f"{{secrets: {{token: '{canary}'}}, defaults: "
            "{subjects: [{$use: Request/check}], baseline: {$use: staging}, "
            "candidates: [{$use: prod}], assertions: [{$use: echo-assertions}]}}",
        ),
        document("Environment", "staging", f"{{baseUrl: '{base_url}'}}"),
        document("Environment", "prod", f"{{baseUrl: '{base_url}'}}"),
        document("Request", "check", "{request: {method: GET, endpoint: /check}}"),
        document(
            "AssertionProfile",
            "echo-assertions",
            "{rules: [{id: echo-safe, selector: body, "
            "operator: {name: comparo.core/equals@1, expected: {$secret: token}}, "
            "severity: blocking, quantifier: one, completeness: complete}]}",
        ),
    )
    policy = trusted_local_policy()
    planned = plan_run(
        compiled,
        invocation(),
        planning_authority(policy, project_namespace=compiled.namespace),
    )
    assert planned.prepared is not None
    return PreparedProjectPlan(
        prepared=planned.prepared,
        policy=policy,
        project_root=root,
    )


def _body_and_auth_project(root: Path, *, base_url: str, token: str) -> PreparedProjectPlan:
    compiled = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/check}], baseline: {$use: staging}, "
            "candidates: [{$use: prod}]}}",
        ),
        document(
            "Environment",
            "staging",
            f"{{baseUrl: '{base_url}', headers: {{X-Environment: staging}}, "
            f"auth: {{bearer: '{token}'}}}}",
        ),
        document(
            "Environment",
            "prod",
            f"{{baseUrl: '{base_url}', headers: {{X-Environment: prod}}, "
            f"auth: {{bearer: '{token}'}}}}",
        ),
        document(
            "Request",
            "check",
            "{request: {method: POST, endpoint: /check, "
            "headers: {X-Request: comparo}, cookies: {session: cookie-secret}, "
            "body: {message: hello}, bodyType: json, timeout: 5s}}",
        ),
    )
    policy = trusted_local_policy()
    planned = plan_run(
        compiled,
        invocation(),
        planning_authority(policy, project_namespace=compiled.namespace),
    )
    assert planned.prepared is not None
    return PreparedProjectPlan(
        prepared=planned.prepared,
        policy=policy,
        project_root=root,
    )


def _artifact_read_limits(
    settings: production.ProductionExecutionSettings,
) -> ArtifactReadLimits:
    limits = settings.artifact.limits
    return ArtifactReadLimits(
        maximum_blobs=limits.maximum_blobs.value,
        maximum_blob_bytes=limits.maximum_blob_bytes.value,
        maximum_total_blob_bytes=limits.maximum_total_blob_bytes.value,
        maximum_manifest_bytes=limits.maximum_manifest_bytes.value,
        maximum_report_bytes=limits.maximum_report_bytes.value,
        maximum_redaction_patterns=limits.sanitization.maximum_patterns.value,
        maximum_redaction_pattern_bytes=limits.sanitization.maximum_pattern_bytes.value,
        maximum_redaction_value_bytes=limits.sanitization.maximum_value_bytes.value,
        maximum_redaction_value_nodes=limits.sanitization.maximum_value_nodes.value,
    )


@dataclass(slots=True)
class _LoopbackServer:
    server: asyncio.AbstractServer
    requests: list[bytes]
    port: int

    async def close(self) -> None:
        self.server.close()
        await self.server.wait_closed()


async def _start_loopback_server(response: bytes) -> _LoopbackServer:
    requests: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                try:
                    head = await reader.readuntil(b"\r\n\r\n")
                except (asyncio.IncompleteReadError, ConnectionError):
                    return
                content_length = 0
                for line in head.split(b"\r\n")[1:]:
                    if line.lower().startswith(b"content-length:"):
                        content_length = int(line.split(b":", 1)[1].strip())
                body = await reader.readexactly(content_length)
                requests.append(head + body)
                writer.write(response)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, host="127.0.0.1", port=0)
    sockets = server.sockets
    assert sockets is not None
    return _LoopbackServer(
        server=server,
        requests=requests,
        port=int(sockets[0].getsockname()[1]),
    )


def test_production_root_executes_http_evaluation_and_transient_artifact(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        entity = b'{"message":"hello"}'
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(entity)}\r\n".encode("ascii")
            + b"Connection: keep-alive\r\n\r\n"
            + entity
        )
        server = await _start_loopback_server(response)
        artifact_root = tmp_path / "artifacts"
        events = RecordingEventSink()
        request = production.ProductionExecutionRequest(
            project=_evaluated_project(
                tmp_path,
                base_url=f"http://127.0.0.1:{server.port}",
            ),
            settings=production.production_execution_defaults_v1(),
            ambient_environment=None,
            artifact_root=artifact_root,
            approve_mutations=False,
            tool_version="test",
            events=events,
        )
        try:
            result = await production.execute_production(request)
        finally:
            await server.close()

        assert result.diagnostics == ()
        assert result.persistence is None
        assert not artifact_root.exists()
        assert len(server.requests) == 2
        assert events.events[0].kind.value == "run-started"
        assert events.events[-1].kind.value == "run-finished"
        assert all(
            request_bytes.startswith(b"GET /check HTTP/1.1\r\n")
            for request_bytes in server.requests
        )

        artifact = result.artifact
        assert artifact is not None
        assert artifact.gate.verdict is ArtifactGateVerdict.PASSED
        assert len(artifact.executions) == 2
        assert all(
            execution.outcome is ArtifactExecutionOutcome.OBSERVED
            and len(execution.assertions) == 2
            and all(
                assertion.outcome is ArtifactFactOutcome.SATISFIED
                for assertion in execution.assertions
            )
            for execution in artifact.executions
        )
        assert len(artifact.comparisons) == 1
        assert len(artifact.comparisons[0].diffs) == 2
        assert all(
            diff.outcome is ArtifactFactOutcome.EQUIVALENT for diff in artifact.comparisons[0].diffs
        )
        responses = tuple(
            exchange.response
            for execution in artifact.executions
            for attempt in execution.attempts
            for exchange in attempt.exchanges
        )
        assert all(response_value is not None for response_value in responses)
        assert all(response_value.status == 200 for response_value in responses if response_value)
        assert all(
            response_value.body is not None
            and response_value.body.extent is ArtifactCaptureExtent.COMPLETE
            for response_value in responses
            if response_value is not None
        )
        assert len(artifact.blobs) == 1

    asyncio.run(scenario())


def test_response_schema_is_validated_as_an_ordinary_gated_assertion(tmp_path: Path) -> None:
    async def scenario() -> None:
        entity = b'{"ok":"not-a-boolean","message":"hello"}'
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(entity)}\r\n".encode("ascii")
            + b"Connection: keep-alive\r\n\r\n"
            + entity
        )
        server = await _start_loopback_server(response)
        request = production.ProductionExecutionRequest(
            project=_evaluated_project(
                tmp_path,
                base_url=f"http://127.0.0.1:{server.port}",
                response_schema=(
                    "{type: object, required: [ok], properties: {ok: {type: boolean}}}"
                ),
            ),
            settings=production.production_execution_defaults_v1(),
            ambient_environment=None,
            artifact_root=None,
            approve_mutations=False,
            tool_version="test",
        )
        try:
            result = await production.execute_production(request)
        finally:
            await server.close()

        assert result.diagnostics == ()
        artifact = result.artifact
        assert artifact is not None
        assert artifact.gate.verdict is ArtifactGateVerdict.FAILED
        assert all(len(execution.assertions) == 3 for execution in artifact.executions)
        schema_facts = tuple(
            assertion
            for execution in artifact.executions
            for assertion in execution.assertions
            if assertion.effective_rule_id == "comparo-response-schema"
        )
        assert len(schema_facts) == 2
        assert all(fact.outcome is ArtifactFactOutcome.VIOLATED for fact in schema_facts)

    asyncio.run(scenario())


def test_safety_profile_denies_remote_schema_before_dns_or_http(tmp_path: Path) -> None:
    request = production.ProductionExecutionRequest(
        project=_safety_project(
            tmp_path,
            base_url="http://127.0.0.1:9",
            safety="{schemas: {}}",
            response_schema="{$use: 'https://schemas.example.test/response.json'}",
        ),
        settings=production.production_execution_defaults_v1(),
        ambient_environment=None,
        artifact_root=None,
        approve_mutations=False,
        tool_version="test",
    )

    result = asyncio.run(production.execute_production(request))

    assert result.artifact is None
    assert result.failure is not None
    assert result.failure.kind is production.ProductionExecutionFailureKind.INVALID
    assert tuple(item.code for item in result.diagnostics) == ("schema.remote.safety-denied",)


def test_safety_profile_response_classification_reaches_artifact_capture(tmp_path: Path) -> None:
    async def scenario() -> None:
        entity = b'{"private":"classified"}'
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(entity)}\r\n".encode("ascii")
            + b"Connection: keep-alive\r\n\r\n"
            + entity
        )
        server = await _start_loopback_server(response)
        request = production.ProductionExecutionRequest(
            project=_safety_project(
                tmp_path,
                base_url=f"http://127.0.0.1:{server.port}",
                safety="{classification: {responseBody: sensitive}}",
            ),
            settings=production.production_execution_defaults_v1(),
            ambient_environment=None,
            artifact_root=None,
            approve_mutations=False,
            tool_version="test",
        )
        try:
            result = await production.execute_production(request)
        finally:
            await server.close()

        assert result.diagnostics == ()
        assert result.artifact is not None
        assert len(server.requests) == 2
        captures = tuple(
            exchange.response.body
            for execution in result.artifact.executions
            for attempt in execution.attempts
            for exchange in attempt.exchanges
            if exchange.response is not None
        )
        assert len(captures) == 2
        assert all(capture is not None for capture in captures)
        assert all(
            capture.extent is ArtifactCaptureExtent.OMITTED
            and capture.blob is None
            and capture.source_byte_count is None
            for capture in captures
            if capture is not None
        )
        assert result.artifact.blobs == ()

    asyncio.run(scenario())


def test_production_root_materializes_body_auth_cookies_and_environment_defaults(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        response = b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: keep-alive\r\n\r\n"
        server = await _start_loopback_server(response)
        token = "wire-auth-canary"
        request = production.ProductionExecutionRequest(
            project=_body_and_auth_project(
                tmp_path,
                base_url=f"http://127.0.0.1:{server.port}",
                token=token,
            ),
            settings=production.production_execution_defaults_v1(),
            ambient_environment=None,
            artifact_root=None,
            approve_mutations=True,
            tool_version="test",
        )
        try:
            result = await production.execute_production(request)
        finally:
            await server.close()

        assert result.diagnostics == ()
        assert result.artifact is not None
        assert len(server.requests) == 2
        assert all(item.startswith(b"POST /check HTTP/1.1\r\n") for item in server.requests)
        assert all(
            b"authorization: Bearer wire-auth-canary\r\n" in item for item in server.requests
        )
        assert all(b"cookie: session=cookie-secret\r\n" in item.lower() for item in server.requests)
        assert all(b"x-request: comparo\r\n" in item.lower() for item in server.requests)
        assert {b"x-environment: staging\r\n", b"x-environment: prod\r\n"} == {
            next(
                line + b"\r\n"
                for line in item.lower().split(b"\r\n")
                if line.startswith(b"x-environment:")
            )
            for item in server.requests
        }
        assert all(item.endswith(b'{"message":"hello"}') for item in server.requests)

    asyncio.run(scenario())


def test_assertion_only_secret_is_sanitized_before_spool_and_persistence(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        canary = "assertion-only-canary-9f31d26a"
        canary_bytes = canary.encode()
        entity = msgspec.json.encode(canary)
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(entity)}\r\n".encode("ascii")
            + b"Connection: keep-alive\r\n\r\n"
            + entity
        )
        server = await _start_loopback_server(response)
        artifact_root = tmp_path / "artifacts"
        artifact_root.mkdir()
        settings = production.production_execution_defaults_v1()
        settings = msgspec.structs.replace(
            settings,
            artifact=msgspec.structs.replace(
                settings.artifact,
                persist=ArtifactPersistMode.ALWAYS,
            ),
        )
        request = production.ProductionExecutionRequest(
            project=_secret_assertion_project(
                tmp_path,
                base_url=f"http://127.0.0.1:{server.port}",
                canary=canary,
            ),
            settings=settings,
            ambient_environment=None,
            artifact_root=artifact_root,
            approve_mutations=False,
            tool_version="test",
        )
        resources = production._ExecutionResources()
        try:
            result = await production._execute_with_resources(request, resources)
            artifact = result.artifact
            spool = resources.spool
            assert artifact is not None
            assert spool is not None
            spool_bodies = tuple(
                [b"".join([chunk async for chunk in spool.read(blob)]) for blob in artifact.blobs]
            )
        finally:
            cleanup_failed = await resources.close()
            await server.close()

        assert not cleanup_failed
        assert result.diagnostics == ()
        assert result.persistence is not None
        assert result.persistence.status is ArtifactPersistenceStatus.PERSISTED
        assert result.persistence.bundle_id is not None
        assert len(server.requests) == 2
        assert all(canary_bytes not in request_bytes for request_bytes in server.requests)
        assert all(
            len(execution.assertions) == 1
            and execution.assertions[0].outcome is ArtifactFactOutcome.SATISFIED
            for execution in artifact.executions
        )
        assert all(canary_bytes not in payload for payload in spool_bodies)
        assert all(b"<redacted>" in payload for payload in spool_bodies)

        artifact_payload = msgspec.json.encode(artifact, order="deterministic")
        assert canary_bytes not in artifact_payload
        assert all(canary not in blob.sha256 for blob in artifact.blobs)
        assert all(blob.sha256 != sha256(entity).hexdigest() for blob in artifact.blobs)
        assert all(
            blob.sha256 == sha256(payload).hexdigest()
            for blob, payload in zip(artifact.blobs, spool_bodies, strict=True)
        )

        bundle = artifact_root / result.persistence.bundle_id
        bundle_payloads = tuple(
            path.read_bytes() for path in sorted(bundle.rglob("*")) if path.is_file()
        )
        assert bundle_payloads
        assert all(canary_bytes not in payload for payload in bundle_payloads)

        read_limits = _artifact_read_limits(settings)
        report = render_artifact_report(
            artifact_root,
            result.persistence.bundle_id,
            read_limits,
            ReportFormat.JSON,
        )
        assert canary_bytes not in report.content
        persisted_bodies = tuple(
            [
                await read_artifact_blob(
                    artifact_root,
                    result.persistence.bundle_id,
                    blob.sha256,
                    read_limits,
                    maximum_output_bytes=blob.byte_count,
                )
                for blob in artifact.blobs
            ]
        )
        assert persisted_bodies == spool_bodies
        assert all(canary_bytes not in payload for payload in persisted_bodies)

    asyncio.run(scenario())


def test_real_driver_composition_closes_without_network_or_leaking_resources(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        request = _request(tmp_path)
        resources = production._ExecutionResources()
        context, diagnostics = await production._prepare_context(request, resources)
        assert diagnostics == ()
        assert context is not None
        resources.spool = PosixSanitizedBlobSpool(
            request.settings.artifact.limits,
            io_chunk_size=request.settings.artifact_io_chunk_size,
        )
        capture = production._capture_driver(
            request,
            context,
            resources.spool,
            SelectionSnapshotRequirements(),
            resources,
        )

        await capture.close()

        assert resources.body_store is not None
        assert not await resources.close()

    asyncio.run(scenario())


async def _stop_before_network(
    _request_value: object,
    _resources: object,
    _context: object,
    _capture: object,
    _evaluation: object,
) -> production.ProductionExecutionResult:
    return production._failure_code(
        production.ProductionExecutionFailureKind.INCOMPLETE,
        "test.stopped-before-network",
    )


def _capture_without_driver(
    _request_value: object,
    _context: object,
    _spool: object,
    _requirements: object,
    _resources: object,
) -> ArtifactCapturingDriver:
    return cast("ArtifactCapturingDriver", _Release([]))


def test_transient_capture_is_default_and_does_not_create_artifact_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    monkeypatch.setattr(production, "_capture_driver", _capture_without_driver)
    monkeypatch.setattr(production, "_run_compile_persist", _stop_before_network)

    result = asyncio.run(production.execute_production(request))

    assert request.settings.artifact.persist is ArtifactPersistMode.NEVER
    assert result.diagnostics[0].code == "test.stopped-before-network"
    assert not cast(Path, request.artifact_root).exists()


def test_explicit_persistence_requires_a_preexisting_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path, persist=ArtifactPersistMode.ALWAYS)
    root = cast(Path, request.artifact_root)
    monkeypatch.setattr(production, "_capture_driver", _capture_without_driver)
    monkeypatch.setattr(production, "_run_compile_persist", _stop_before_network)

    result = asyncio.run(production.execute_production(request))

    assert result.diagnostics[0].code == "artifact.root-unavailable"
    assert not root.exists()

    root.mkdir()
    result = asyncio.run(production.execute_production(request))
    assert result.diagnostics[0].code == "test.stopped-before-network"


def test_offline_environment_failure_leaves_no_artifact_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path, persist=ArtifactPersistMode.ALWAYS)
    root = cast(Path, request.artifact_root)

    async def failed_preparation(
        _request_value: object,
        _resources: object,
    ) -> tuple[None, tuple[ApplicationDiagnostic, ...]]:
        return None, production._diagnostics("environment.source.invalid")

    monkeypatch.setattr(production, "_prepare_context", failed_preparation)

    result = asyncio.run(production.execute_production(request))

    assert result.diagnostics[0].code == "environment.source.invalid"
    assert not root.exists()


class _AsyncClose:
    def __init__(self, events: list[str], name: str, error: BaseException | None = None) -> None:
        self._events = events
        self._name = name
        self._error = error

    async def close(self) -> None:
        self._events.append(self._name)
        if self._error is not None:
            raise self._error


class _SyncClose:
    def __init__(self, events: list[str], name: str, *, fail: bool = False) -> None:
        self._events = events
        self._name = name
        self._fail = fail

    def close(self) -> None:
        self._events.append(self._name)
        if self._fail:
            raise RuntimeError("private cleanup detail")


class _Release:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self._events = events
        self._fail = fail

    def release(self) -> None:
        self._events.append("capture")
        if self._fail:
            raise RuntimeError("private cleanup detail")


def _failing_resources(events: list[str]) -> production._ExecutionResources:
    resources = production._ExecutionResources()
    resources.capture = cast("ArtifactCapturingDriver", _Release(events, fail=True))
    resources.spool = cast(
        "PosixSanitizedBlobSpool",
        _AsyncClose(events, "spool", RuntimeError("private cleanup detail")),
    )
    resources.body_store = cast(
        "TransientBodyStore",
        _AsyncClose(events, "body"),
    )
    resources.context = cast("RunContext", _AsyncClose(events, "context"))
    resources.files = cast(
        "PinnedProjectFiles",
        _SyncClose(events, "files", fail=True),
    )
    resources.sink = cast(
        "LocalArtifactBundleSink",
        _SyncClose(events, "sink"),
    )
    return resources


def test_cleanup_attempts_every_owned_resource_and_returns_only_failure_signal() -> None:
    events: list[str] = []

    failed = asyncio.run(_failing_resources(events).close())

    assert failed
    assert events == ["capture", "spool", "body", "context", "files", "sink"]


def test_cleanup_preserves_cancellation_after_attempting_later_resources() -> None:
    events: list[str] = []
    resources = _failing_resources(events)
    resources.spool = cast(
        "PosixSanitizedBlobSpool",
        _AsyncClose(events, "spool", asyncio.CancelledError()),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(resources.close())

    assert events == ["capture", "spool", "body", "context", "files", "sink"]


def test_cleanup_finishes_every_resource_under_repeated_cancellation() -> None:
    async def scenario() -> None:
        events: list[str] = []
        entered = asyncio.Event()
        release = asyncio.Event()

        class SlowClose:
            async def close(self) -> None:
                events.append("spool")
                entered.set()
                await release.wait()

        resources = production._ExecutionResources()
        resources.spool = cast("PosixSanitizedBlobSpool", SlowClose())
        resources.body_store = cast("TransientBodyStore", _AsyncClose(events, "body"))
        resources.context = cast("RunContext", _AsyncClose(events, "context"))
        resources.files = cast("PinnedProjectFiles", _SyncClose(events, "files"))
        resources.sink = cast("LocalArtifactBundleSink", _SyncClose(events, "sink"))

        task = asyncio.create_task(resources.close())
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert events == ["spool", "body", "context", "files", "sink"]

    asyncio.run(scenario())


def test_cleanup_removes_real_transient_store_under_repeated_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        config = msgspec.structs.replace(
            production.production_execution_defaults_v1().body_store,
            root=tmp_path,
        )
        store = TransientBodyStore(config)
        directory = store._directory
        entered = asyncio.Event()
        release = asyncio.Event()
        original_finish = TransientBodyStore._finish_close

        async def controlled_finish(
            close_store: TransientBodyStore,
            entries: tuple[object, ...],
        ) -> None:
            entered.set()
            await release.wait()
            await original_finish(close_store, entries)  # type: ignore[arg-type]

        monkeypatch.setattr(TransientBodyStore, "_finish_close", controlled_finish)
        resources = production._ExecutionResources()
        resources.body_store = store
        task = asyncio.create_task(resources.close())
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert store._closed
        assert not directory.exists()

    asyncio.run(scenario())


def test_execute_finishes_cleanup_when_cancellation_is_repeated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        events: list[str] = []
        executing = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()

        class SlowClose:
            async def close(self) -> None:
                events.append("spool")
                cleaning.set()
                await release.wait()

        async def execute(
            _request_value: object,
            resources: production._ExecutionResources,
        ) -> production.ProductionExecutionResult:
            resources.spool = cast("PosixSanitizedBlobSpool", SlowClose())
            resources.body_store = cast("TransientBodyStore", _AsyncClose(events, "body"))
            resources.context = cast("RunContext", _AsyncClose(events, "context"))
            resources.files = cast("PinnedProjectFiles", _SyncClose(events, "files"))
            resources.sink = cast("LocalArtifactBundleSink", _SyncClose(events, "sink"))
            executing.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        monkeypatch.setattr(production, "_execute_with_resources", execute)
        task = asyncio.create_task(production.execute_production(_request(tmp_path)))
        await executing.wait()
        task.cancel()
        await cleaning.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert events == ["spool", "body", "context", "files", "sink"]

    asyncio.run(scenario())


def test_partial_driver_construction_keeps_body_store_owned_for_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    resources = production._ExecutionResources()

    def fail_stream_setup(**_arguments: object) -> object:
        raise ValueError("private stream setup detail")

    monkeypatch.setattr(production, "StoredStreamSnapshotProvider", fail_stream_setup)
    with pytest.raises(ValueError, match="private stream setup detail"):
        production._capture_driver(
            request,
            cast("RunContext", object()),
            cast("PosixSanitizedBlobSpool", object()),
            SelectionSnapshotRequirements(stream=True),
            resources,
        )

    assert resources.body_store is not None
    assert not asyncio.run(resources.close())


def test_cleanup_failure_replaces_safe_result_with_fixed_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)

    async def safe_result(
        _request_value: object,
        _resources: object,
    ) -> production.ProductionExecutionResult:
        return production._failure_code(
            production.ProductionExecutionFailureKind.INCOMPLETE,
            "test.safe-failure",
        )

    async def failed_cleanup(_resources: object) -> bool:
        return True

    monkeypatch.setattr(production, "_execute_with_resources", safe_result)
    monkeypatch.setattr(production._ExecutionResources, "close", failed_cleanup)

    result = asyncio.run(production.execute_production(request))

    assert result.diagnostics[0].code == "execution.cleanup-failed"


def test_public_composition_contracts_reject_invalid_shapes(tmp_path: Path) -> None:
    assert application.ProductionExecutionRequest is production.ProductionExecutionRequest
    assert application.ProductionExecutionResult is production.ProductionExecutionResult
    assert application.ProductionExecutionSettings is production.ProductionExecutionSettings
    assert application.execute_production is production.execute_production
    assert (
        application.production_execution_defaults_v1 is production.production_execution_defaults_v1
    )

    settings = production.production_execution_defaults_v1()
    with pytest.raises(TypeError, match="untyped section"):
        msgspec.structs.replace(
            settings,
            files=cast("LocalFileLimits", object()),
        )
    with pytest.raises(ValueError, match="transient-store"):
        msgspec.structs.replace(
            settings,
            body_store=msgspec.structs.replace(
                settings.body_store,
                maximum_transient_bytes=ByteCount(value=1024 * 1024),
            ),
        )
    with pytest.raises(ValueError, match="captured entity"):
        msgspec.structs.replace(
            settings,
            ordinary=msgspec.structs.replace(
                settings.ordinary,
                maximum_body_bytes=ByteCount(
                    value=settings.transport.capture.maximum_entity_body_bytes.value + 1
                ),
            ),
        )

    request = _request(tmp_path)
    invalid_request_fields: tuple[tuple[str, object, type[Exception]], ...] = (
        ("project", object(), TypeError),
        ("settings", object(), TypeError),
        ("ambient_environment", [], TypeError),
        ("ambient_environment", {"A": 1}, TypeError),
        ("artifact_root", "artifacts", TypeError),
        ("approve_mutations", 1, TypeError),
        ("tool_version", "", ValueError),
        ("events", object(), TypeError),
        ("cancellation", object(), TypeError),
    )
    for field, value, error in invalid_request_fields:
        with pytest.raises(error):
            msgspec.structs.replace(request, **{field: value})
    events = RecordingEventSink()
    cancellation = RunCancellation()
    interactive = msgspec.structs.replace(
        request,
        events=events,
        cancellation=cancellation,
    )
    assert interactive.events is events
    assert interactive.cancellation is cancellation

    persisted = production.persisted_production_execution_defaults_v1()
    assert persisted.artifact.persist is ArtifactPersistMode.ALWAYS
    regular = production.production_execution_defaults_v1()
    assert regular.artifact.persist is ArtifactPersistMode.NEVER

    artifact = _artifact()
    failure = ArtifactPersistenceOutcome(
        status=ArtifactPersistenceStatus.FAILED,
        failure=ArtifactStoreFailure(code=ArtifactStoreErrorCode.IO_FAILED),
    )
    with pytest.raises(TypeError):
        production.ProductionExecutionResult(artifact=cast("RunArtifact", object()))
    with pytest.raises(TypeError):
        production.ProductionExecutionResult(
            failure=production.ProductionExecutionFailure(
                kind=production.ProductionExecutionFailureKind.INVALID,
                diagnostics=production._diagnostics("x"),
            ),
            persistence=cast("ArtifactPersistenceOutcome", object()),
        )
    with pytest.raises(TypeError):
        production.ProductionExecutionFailure(
            kind=production.ProductionExecutionFailureKind.INVALID,
            diagnostics=cast(
                "tuple[ApplicationDiagnostic, ...]",
                [ApplicationDiagnostic(code="x", severity="error", stage="x")],
            ),
        )
    with pytest.raises(TypeError, match="failure kind"):
        production.ProductionExecutionFailure(
            kind=cast("production.ProductionExecutionFailureKind", object()),
            diagnostics=production._diagnostics("x"),
        )
    with pytest.raises(ValueError, match="exactly artifact or failure"):
        production.ProductionExecutionResult()
    typed_failure = production.ProductionExecutionFailure(
        kind=production.ProductionExecutionFailureKind.INVALID,
        diagnostics=production._diagnostics("x"),
    )
    with pytest.raises(ValueError, match="exactly artifact or failure"):
        production.ProductionExecutionResult(
            artifact=artifact,
            failure=typed_failure,
        )
    with pytest.raises(ValueError, match="cannot retain persistence"):
        production.ProductionExecutionResult(
            failure=typed_failure,
            persistence=failure,
        )
    with pytest.raises(TypeError, match="result failure"):
        production.ProductionExecutionResult(
            failure=cast("production.ProductionExecutionFailure", object()),
        )


def test_execute_contains_preflight_runtime_and_cancellation_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    with pytest.raises(TypeError, match="ProductionExecutionRequest"):
        asyncio.run(production.execute_production(object()))  # type: ignore[arg-type]

    async def internal_failure(_request_value: object, _resources: object) -> object:
        raise RuntimeError("private runtime detail")

    monkeypatch.setattr(production, "_execute_with_resources", internal_failure)
    contained = asyncio.run(production.execute_production(request))
    assert contained.diagnostics[0].code == "execution.internal-failed"

    async def cancelled(_request_value: object, _resources: object) -> object:
        raise asyncio.CancelledError

    monkeypatch.setattr(production, "_execute_with_resources", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(production.execute_production(request))


def test_artifact_sink_authority_and_value_free_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path, persist=ArtifactPersistMode.ALWAYS)
    policy = request.settings.artifact
    assert production._artifact_sink(request.project, None, policy) == (
        None,
        "artifact.root-required",
    )
    denied_policy = msgspec.structs.replace(
        request.project.policy,
        artifacts=PolicyArtifacts(allow_sanitized_persistence=False, roots=()),
    )
    denied_project = msgspec.structs.replace(request.project, policy=denied_policy)
    assert production._artifact_sink(denied_project, tmp_path / "denied", policy) == (
        None,
        "artifact.root-denied",
    )
    assert not production._artifact_root_allowed(denied_project, tmp_path)

    def failed_resolve(_path: Path, *, strict: bool = False) -> Path:
        del strict
        raise OSError

    monkeypatch.setattr(Path, "resolve", failed_resolve)
    assert production._artifact_sink(request.project, tmp_path / "broken", policy) == (
        None,
        "artifact.root-unavailable",
    )


def test_artifact_sink_requires_existing_root_and_maps_unsafe_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path, persist=ArtifactPersistMode.ALWAYS)
    policy = request.settings.artifact
    selected = tmp_path / "artifacts"

    assert production._artifact_sink(request.project, selected, policy) == (
        None,
        "artifact.root-unavailable",
    )
    assert not selected.exists()

    selected.mkdir()

    decisions = iter((True, False))
    monkeypatch.setattr(production, "_artifact_root_allowed", lambda *_args: next(decisions))
    assert production._artifact_sink(request.project, selected, policy) == (
        None,
        "artifact.root-denied",
    )
    monkeypatch.undo()

    monkeypatch.setattr(
        production,
        "LocalArtifactBundleSink",
        lambda _root: (_ for _ in ()).throw(
            LocalArtifactStoreError(ArtifactStoreErrorCode.ROOT_UNSAFE)
        ),
    )
    assert production._artifact_sink(request.project, selected, policy) == (
        None,
        "artifact.root-unsafe",
    )


class _RecordingSink:
    def __init__(self, outcome: ArtifactPersistenceOutcome) -> None:
        self.outcome = outcome
        self.calls = 0

    async def persist(self, *_arguments: object) -> ArtifactPersistenceOutcome:
        self.calls += 1
        return self.outcome


def test_persistence_modes_and_fixed_failure_codes(tmp_path: Path) -> None:
    artifact = _artifact()
    settings = production.production_execution_defaults_v1()
    spool = cast("PosixSanitizedBlobSpool", object())
    persisted = ArtifactPersistenceOutcome(
        status=ArtifactPersistenceStatus.PERSISTED,
        bundle_id="bundle",
    )
    sink = _RecordingSink(persisted)
    sink_port = cast("LocalArtifactBundleSink", sink)

    assert asyncio.run(production._persist(None, artifact, spool, settings.artifact)) is None
    on_failure = msgspec.structs.replace(
        settings.artifact,
        persist=ArtifactPersistMode.ON_GATE_FAILURE,
    )
    assert asyncio.run(production._persist(sink_port, artifact, spool, on_failure)) is None
    failed_gate = _artifact()
    msgspec.structs.force_setattr(failed_gate.gate, "verdict", ArtifactGateVerdict.FAILED)
    assert asyncio.run(production._persist(sink_port, failed_gate, spool, on_failure)) == persisted
    assert sink.calls == 1

    failed = ArtifactPersistenceOutcome(
        status=ArtifactPersistenceStatus.FAILED,
        failure=ArtifactStoreFailure(code=ArtifactStoreErrorCode.DIGEST_MISMATCH),
    )
    assert production._persistence_code(failed) == "artifact.persistence.digest-mismatch"
    msgspec.structs.force_setattr(failed, "failure", None)
    assert production._persistence_code(failed) == "artifact.persistence.io-failed"
    assert production._artifact_id(_project(tmp_path).prepared).startswith("run-")


def test_preflight_evaluation_sink_and_setup_fail_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    safe = ApplicationDiagnostic(code="offline.invalid", severity="error", stage="offline")

    with monkeypatch.context() as patch:
        patch.setattr(
            production,
            "preflight_runtime_capabilities",
            lambda _project_value: SimpleNamespace(diagnostics=(object(),)),
        )
        patch.setattr(production, "inspect_diagnostic", lambda _diagnostic: safe)
        result = asyncio.run(production.execute_production(request))
        assert result.diagnostics == (safe,)

    async def branch_scenario() -> None:
        with monkeypatch.context() as patch:
            patch.setattr(
                production,
                "bind_configuration_evaluation",
                lambda *_arguments, **_keywords: SimpleNamespace(
                    diagnostics=(object(),),
                    preparation=None,
                ),
            )
            patch.setattr(production, "inspect_diagnostic", lambda _diagnostic: safe)
            resources = production._ExecutionResources()
            result = await production._execute_with_resources(request, resources)
            assert result.diagnostics == (safe,)
            assert not await resources.close()

        with monkeypatch.context() as patch:
            patch.setattr(
                production,
                "_artifact_sink",
                lambda *_arguments: (None, "artifact.test-denied"),
            )
            resources = production._ExecutionResources()
            result = await production._execute_with_resources(request, resources)
            assert result.diagnostics[0].code == "artifact.test-denied"
            assert not await resources.close()

        with monkeypatch.context() as patch:
            patch.setattr(
                production,
                "PosixSanitizedBlobSpool",
                lambda *_arguments, **_keywords: (_ for _ in ()).throw(OSError()),
            )
            resources = production._ExecutionResources()
            result = await production._execute_with_resources(request, resources)
            assert result.diagnostics[0].code == "execution.runtime-setup-failed"
            assert not await resources.close()

    asyncio.run(branch_scenario())


def test_context_preparation_maps_project_and_environment_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    missing_project = msgspec.structs.replace(
        request,
        project=msgspec.structs.replace(
            request.project,
            project_root=tmp_path / "missing-project",
        ),
    )
    resources = production._ExecutionResources()
    context, diagnostics = asyncio.run(production._prepare_context(missing_project, resources))
    assert context is None
    assert diagnostics[0].code == "execution.project-root-unavailable"

    safe = ApplicationDiagnostic(code="environment.invalid", severity="error", stage="offline")

    async def failed_snapshots(*_arguments: object, **_keywords: object) -> object:
        return SimpleNamespace(diagnostics=(object(),))

    monkeypatch.setattr(production, "build_environment_snapshots", failed_snapshots)
    monkeypatch.setattr(production, "inspect_diagnostic", lambda _diagnostic: safe)
    resources = production._ExecutionResources()
    context, diagnostics = asyncio.run(production._prepare_context(request, resources))
    assert context is None
    assert diagnostics == (safe,)
    assert not asyncio.run(resources.close())


def test_run_compile_persist_maps_compilation_and_storage_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        request = _request(tmp_path)
        resources = production._ExecutionResources()
        context, diagnostics = await production._prepare_context(request, resources)
        assert context is not None
        assert diagnostics == ()
        binding = bind_configuration_evaluation(
            request.project.prepared,
            context,
            request.settings.evaluation,
        )
        assert binding.preparation is not None
        evaluation = cast("EvaluationPreparation", binding.preparation)
        capture = cast("ArtifactCapturingDriver", object())
        resources.spool = PosixSanitizedBlobSpool(
            request.settings.artifact.limits,
            io_chunk_size=request.settings.artifact_io_chunk_size,
        )

        async def fake_run(*_arguments: object, **_keywords: object) -> object:
            return object()

        monkeypatch.setattr(production, "run_prepared", fake_run)
        compilation: object = SimpleNamespace(
            status=ArtifactCompilationStatus.REJECTED,
            artifact=None,
            failure=None,
        )
        monkeypatch.setattr(
            production,
            "compile_run_artifact",
            lambda *_arguments, **_keywords: compilation,
        )
        rejected = await production._run_compile_persist(
            request,
            resources,
            context,
            capture,
            evaluation,
        )
        assert rejected.diagnostics[0].code == "artifact.compilation-rejected"

        compilation = SimpleNamespace(
            status=ArtifactCompilationStatus.REJECTED,
            artifact=None,
            failure=ArtifactFailure(stage="test", code="unsafe"),
        )
        rejected = await production._run_compile_persist(
            request,
            resources,
            context,
            capture,
            evaluation,
        )
        assert rejected.diagnostics[0].code == "artifact.unsafe"

        artifact = _artifact()
        compilation = SimpleNamespace(
            status=ArtifactCompilationStatus.CREATED,
            artifact=artifact,
            failure=None,
        )
        created = await production._run_compile_persist(
            request,
            resources,
            context,
            capture,
            evaluation,
        )
        assert created.artifact == artifact
        assert created.persistence is None

        failed_persistence = ArtifactPersistenceOutcome(
            status=ArtifactPersistenceStatus.FAILED,
            failure=ArtifactStoreFailure(code=ArtifactStoreErrorCode.IO_FAILED),
        )

        async def fail_persist(*_arguments: object) -> ArtifactPersistenceOutcome:
            return failed_persistence

        monkeypatch.setattr(production, "_persist", fail_persist)
        failed = await production._run_compile_persist(
            request,
            resources,
            context,
            capture,
            evaluation,
        )
        assert failed.diagnostics[0].code == "artifact.persistence.io-failed"
        assert not await resources.close()

    asyncio.run(scenario())
