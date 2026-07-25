"""Integration tests for live capture and the single RunResult-to-artifact boundary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from comparo.adapters.artifacts.memory import InMemorySanitizedBlobStore
from comparo.core.artifacts.capture import ArtifactCapturingDriver
from comparo.core.artifacts.compiler import ArtifactCompilationResult
from comparo.core.artifacts.compiler import ArtifactCompilationStatus
from comparo.core.artifacts.compiler import compile_run_artifact
from comparo.core.artifacts.policy import ArtifactCapturePolicy
from comparo.core.artifacts.policy import ArtifactLimits
from comparo.core.artifacts.policy import ArtifactPersistMode
from comparo.core.artifacts.policy import ArtifactPolicy
from comparo.core.artifacts.policy import RedactionPolicy
from comparo.core.artifacts.policy import UnsafeSourceBehavior
from comparo.core.domain.http.bodies import RepeatableByteSource
from comparo.core.domain.http.response_bodies import BodyHandle
from comparo.core.domain.identity import RunId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.scopes import RunScope
from comparo.core.planning.models import PreparedRun
from comparo.core.planning.planner import plan_run
from comparo.core.resolution.control import RunCancellation
from comparo.core.resolution.models import SanitizationSnapshot
from comparo.core.runtime import RecordingEventSink
from comparo.core.runtime import run_prepared
from tests.core.planning.conftest import authority as planning_authority
from tests.core.planning.conftest import compile_project
from tests.core.planning.conftest import document
from tests.core.planning.conftest import invocation
from tests.core.runtime.conftest import FixedClock
from tests.core.runtime.test_kernel_integration import KernelPreparation
from tests.core.runtime.test_vertical_runtime import _driver


def _prepared() -> PreparedRun:
    project = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/chat}], baseline: {$use: staging}, "
            "candidates: [{$use: prod}], trials: 1, maximumAttempts: 1}}",
        ),
        document("Environment", "staging", "{baseUrl: 'https://staging.example'}"),
        document("Environment", "prod", "{baseUrl: 'https://prod.example'}"),
        document("Request", "chat", "{request: {method: GET, endpoint: /chat}}"),
    )
    planned = plan_run(project, invocation(), planning_authority())
    assert planned.prepared is not None
    return planned.prepared


def _limits() -> ArtifactLimits:
    return ArtifactLimits(
        maximum_blobs=PositiveCount(value=16),
        maximum_blob_bytes=ByteCount(value=1024),
        maximum_total_blob_bytes=ByteCount(value=4096),
        maximum_manifest_bytes=ByteCount(value=1_000_000),
        maximum_report_bytes=ByteCount(value=1_000_000),
    )


def _policy(unsafe: UnsafeSourceBehavior = UnsafeSourceBehavior.OMIT) -> ArtifactPolicy:
    return ArtifactPolicy(
        persist=ArtifactPersistMode.ALWAYS,
        unsafe_source=unsafe,
        capture=ArtifactCapturePolicy(),
        limits=_limits(),
    )


class _EmptySnapshotProvider:
    def __init__(self, run_id: RunId) -> None:
        self._run = RunScope(run_id=run_id)

    async def sanitization_snapshot(self) -> SanitizationSnapshot:
        return SanitizationSnapshot(run=self._run, revision=Count(value=0), entries=())


class _EmptyBodies:
    def read(self, source: RepeatableByteSource | BodyHandle) -> AsyncIterator[bytes]:
        del source
        return _EmptyIterator()


class _FailingBodies:
    def read(self, source: RepeatableByteSource | BodyHandle) -> AsyncIterator[bytes]:
        del source
        return _FailingIterator()


class _EmptyIterator:
    def __aiter__(self) -> _EmptyIterator:
        return self

    async def __anext__(self) -> bytes:
        raise StopAsyncIteration


class _FailingIterator:
    def __aiter__(self) -> _FailingIterator:
        return self

    async def __anext__(self) -> bytes:
        raise OSError


def test_capture_decorator_compiles_one_safe_complete_artifact() -> None:
    compilation, blobs = asyncio.run(_run(_policy(), _EmptyBodies()))

    assert compilation.status is ArtifactCompilationStatus.CREATED
    artifact = compilation.artifact
    assert artifact is not None
    assert artifact.gate.verdict.value == "passed"
    assert len(artifact.executions) == 2
    assert len(artifact.comparisons) == 1
    assert len(artifact.blobs) == 1
    assert artifact.blobs[0].byte_count == 0
    assert not artifact.coverage_issues
    assert artifact.executions[0].assertions
    assert artifact.comparisons[0].diffs
    assert blobs is not None


def test_unsafe_body_can_be_omitted_with_explicit_coverage() -> None:
    compilation, _blobs = asyncio.run(_run(_policy(), _FailingBodies()))

    assert compilation.status is ArtifactCompilationStatus.CREATED
    artifact = compilation.artifact
    assert artifact is not None
    assert artifact.coverage_issues
    assert all(item.reason.value == "storage-failed" for item in artifact.coverage_issues)
    assert not artifact.blobs


def test_unsafe_body_rejects_artifact_when_policy_requires_it() -> None:
    compilation, _blobs = asyncio.run(
        _run(_policy(UnsafeSourceBehavior.REJECT_ARTIFACT), _FailingBodies())
    )

    assert compilation.status is ArtifactCompilationStatus.REJECTED
    assert compilation.artifact is None
    assert compilation.failure is not None
    assert compilation.failure.code == "unsafe-source"


async def _run(
    policy: ArtifactPolicy,
    bodies: _EmptyBodies | _FailingBodies,
) -> tuple[ArtifactCompilationResult, InMemorySanitizedBlobStore]:
    prepared = _prepared()
    cancellation = RunCancellation()
    inner, _transport = _driver(prepared, cancellation)
    blobs = InMemorySanitizedBlobStore(policy.limits)
    capture = ArtifactCapturingDriver(
        driver=inner,
        snapshots=_EmptySnapshotProvider(prepared.plan.run_id),
        bodies=bodies,
        blobs=blobs,
        policy=policy,
        redaction=RedactionPolicy(),
    )
    result = await run_prepared(
        prepared,
        driver=capture,
        evaluation=KernelPreparation(),
        cancellation=cancellation,
        clock=FixedClock(),
        events=RecordingEventSink(),
    )
    compilation = compile_run_artifact(
        prepared,
        result,
        capture,
        policy=policy,
        artifact_id="artifact-1",
        created_unix_nanoseconds=1,
        tool_version="0.2.0",
    )
    return compilation, blobs
