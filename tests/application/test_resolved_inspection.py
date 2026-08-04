"""Masked run-scoped value-inspection application-service tests."""

from __future__ import annotations

import asyncio
import tracemalloc
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Never
from typing import cast

import msgspec
import pytest

from comparo.adapters.values import EnvironmentSourceLimits
from comparo.adapters.values import LocalFileLimits
from comparo.adapters.values import LocalFilesystemError
from comparo.adapters.values import LocalFilesystemErrorCode
from comparo.adapters.values import PinnedProjectFiles
from comparo.adapters.values import YamlValueLimits
from comparo.application import resolved_inspection as inspection_module
from comparo.application.environment_binding import CapturedAmbientEnvironment
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import ResourceCoordinate
from comparo.application.planning import PlanningOptions
from comparo.application.planning import RequestOverride
from comparo.application.preflight import PreparedProjectPlan
from comparo.application.preflight import prepare_project_plan
from comparo.application.resolved_inspection import ResolvedInputTiming
from comparo.application.resolved_inspection import ResolvedInspectionState
from comparo.application.resolved_inspection import ResolvedTaintSource
from comparo.application.resolved_inspection import ResolvedValueFormat
from comparo.application.resolved_inspection import ResolvedValueFreshness
from comparo.application.resolved_inspection import ResolvedValueInspection
from comparo.application.resolved_inspection import ResolvedValueInspectionBatch
from comparo.application.resolved_inspection import ResolvedValueInspectionBatchResult
from comparo.application.resolved_inspection import ResolvedValueInspectionResult
from comparo.application.resolved_inspection import ResolvedValueInspectionTarget
from comparo.application.resolved_inspection import ResolvedValueNode
from comparo.application.resolved_inspection import ResolvedValueProjectionLimits
from comparo.application.resolved_inspection import (
    _bind_resolved_value_target as bind_resolved_value_target,
)
from comparo.application.resolved_inspection import (
    _inspect_resolved_value as inspect_resolved_value,
)
from comparo.application.resolved_inspection import (
    _inspect_resolved_value_batch as inspect_resolved_value_batch,
)
from comparo.application.resolved_inspection import (
    _ResolvedValueInspectionRequest as ResolvedValueInspectionRequest,
)
from comparo.application.resolved_inspection import (
    _ResolvedValueInspectionSettings as ResolvedValueInspectionSettings,
)
from comparo.core.configuration.canonical import CanonicalArray
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import CompiledResource
from comparo.core.configuration.canonical import InlineResource
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import ConversionType
from comparo.core.domain.expressions import ConvertExpr
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ObjectExpressionMember
from comparo.core.domain.identity import InlineDefinitionOrigin
from comparo.core.domain.identity import InlineDefinitionRef
from comparo.core.domain.identity import InlineFragment
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.resolution import RunContext
from comparo.core.runtime import iter_execution_work
from tests.application.test_preflight import _request
from tests.application.test_preflight import _write_policy


def _settings(
    *,
    maximum_page_nodes: int = 8,
    maximum_scalar_bytes: int = 64,
    maximum_path_segments: int = 16,
    maximum_path_segment_bytes: int = 64,
    maximum_traversal_nodes: int = 1_000_000,
    maximum_traversal_bytes: int = 64 * 1024 * 1024,
    maximum_taint_origins: int = 64,
    maximum_taint_origin_bytes: int = 1024,
) -> ResolvedValueInspectionSettings:
    return ResolvedValueInspectionSettings(
        files=LocalFileLimits(),
        environment=EnvironmentSourceLimits(),
        yaml=YamlValueLimits(),
        maximum_page_nodes=PositiveCount(value=maximum_page_nodes),
        maximum_scalar_bytes=ByteCount(value=maximum_scalar_bytes),
        maximum_path_segments=PositiveCount(value=maximum_path_segments),
        maximum_path_segment_bytes=ByteCount(value=maximum_path_segment_bytes),
        maximum_traversal_nodes=PositiveCount(value=maximum_traversal_nodes),
        maximum_traversal_bytes=ByteCount(value=maximum_traversal_bytes),
        maximum_taint_origins=PositiveCount(value=maximum_taint_origins),
        maximum_taint_origin_bytes=ByteCount(value=maximum_taint_origin_bytes),
    )


def _projection_limits(
    *,
    maximum_page_nodes: int = 8,
    maximum_scalar_bytes: int = 64,
    maximum_path_segments: int = 16,
    maximum_path_segment_bytes: int = 64,
    maximum_traversal_nodes: int = 1_000_000,
    maximum_traversal_bytes: int = 64 * 1024 * 1024,
    maximum_taint_origins: int = 64,
    maximum_taint_origin_bytes: int = 1024,
) -> ResolvedValueProjectionLimits:
    return ResolvedValueProjectionLimits(
        maximum_page_nodes=PositiveCount(value=maximum_page_nodes),
        maximum_scalar_bytes=ByteCount(value=maximum_scalar_bytes),
        maximum_path_segments=PositiveCount(value=maximum_path_segments),
        maximum_path_segment_bytes=ByteCount(value=maximum_path_segment_bytes),
        maximum_traversal_nodes=PositiveCount(value=maximum_traversal_nodes),
        maximum_traversal_bytes=ByteCount(value=maximum_traversal_bytes),
        maximum_taint_origins=PositiveCount(value=maximum_taint_origins),
        maximum_taint_origin_bytes=ByteCount(value=maximum_taint_origin_bytes),
    )


def _literal(value: str) -> LiteralExpr:
    return LiteralExpr(
        value=JsonString(value=value),
        provenance=ValueProvenance(entries=(ProvenanceEntry(kind=ProvenanceKind.INVOCATION),)),
    )


def _project(
    root: Path,
    *,
    options: PlanningOptions | None = None,
    policy: Path | None = None,
) -> PreparedProjectPlan:
    data = root / ".comparo"
    data.mkdir()
    config = root / "comparo.yaml"
    config.write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Project\n"
        "metadata: {id: demo, name: Demo}\n"
        "spec:\n"
        "  data: .comparo\n"
        "  variables:\n"
        "    publicTree:\n"
        "      items: [abcdef, 2, true, null, {deep: value}]\n"
        "      a-very-long-output-key: value\n"
        "    fileValue: {$file: sample.bin}\n"
        "    missingValue: {$env: ABSENT}\n"
        "    optionalValue: {$optional: {$env: ABSENT}}\n"
        "    errorValue: {$env: {name: LABEL, as: integer}}\n"
        "    fallbackValue: {$from: [{$env: ABSENT}, fallback]}\n"
        "    baseValue: hello\n"
        "    interpolatedValue: 'prefix-${variables.baseValue}'\n"
        "  secrets: {token: super-secret-inspection-canary}\n"
        "  defaults:\n"
        "    subjects: [{$use: Request/ping}]\n"
        "    baseline: {$use: staging}\n"
        "    candidates: [{$use: prod}]\n",
        encoding="utf-8",
    )
    (root / "sample.bin").write_bytes(b"\x00\xab\xff")
    (data / "resources.yaml").write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Environment\n"
        "metadata: {id: staging, name: Staging}\n"
        "spec:\n"
        "  baseUrl: https://staging.example.test\n"
        "  env: {values: {LABEL: staging-secret-label}}\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: Environment\n"
        "metadata: {id: prod, name: Prod}\n"
        "spec:\n"
        "  baseUrl: https://prod.example.test\n"
        "  env: {values: {LABEL: prod-secret-label}}\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: Matrix\n"
        "metadata: {id: regions, name: Regions}\n"
        "spec:\n"
        "  cases:\n"
        "    - {id: eu, values: {region: eu}}\n"
        "    - {id: us, values: {region: us}}\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: Value\n"
        "metadata: {id: private-value, name: Private value}\n"
        "spec: {value: literal-value-inspection-canary}\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: Request\n"
        "metadata: {id: ping, name: Ping}\n"
        "spec:\n"
        "  request:\n"
        "    method: GET\n"
        "    endpoint: /original\n"
        "    headers:\n"
        "      X-Region: {$matrix: region}\n"
        "      X-Label: {$env: LABEL}\n"
        "      X-Token: {$secret: token}\n"
        "  matrices: [{$use: regions}]\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: Request\n"
        "metadata: {id: other, name: Other}\n"
        "spec: {request: {method: GET, endpoint: /other}}\n",
        encoding="utf-8",
    )
    outcome = prepare_project_plan(_request(config, options=options, policy=policy))
    assert outcome.project is not None, outcome.diagnostics
    return outcome.project


def _coordinate(resource: CompiledResource) -> ResourceCoordinate:
    definition = resource.definition
    identity = definition.identity
    return ResourceCoordinate(
        namespace=identity.namespace.value,
        kind=identity.kind.value,
        resource_id=identity.resource_id.value,
        fingerprint=definition.fingerprint.digest.hex_value,
    )


def _resource(project: PreparedProjectPlan, kind: ResourceKind, name: str) -> CompiledResource:
    resource = next(
        (
            item
            for item in (project.prepared.project.project, *project.prepared.project.resources)
            if item.definition.identity.kind is kind
            and item.definition.identity.resource_id.value == name
        ),
        None,
    )
    assert resource is not None
    return resource


def _inspection_request(
    project: PreparedProjectPlan,
    *,
    resource: CompiledResource | None = None,
    field_path: tuple[str, ...] = ("request", "headers", "X-Region"),
    ordinal: int = 0,
    environment: ResourceCoordinate | None = None,
    settings: ResolvedValueInspectionSettings | None = None,
    start_node: int = 0,
    maximum_nodes: int = 8,
) -> ResolvedValueInspectionRequest:
    selected = _resource(project, ResourceKind.REQUEST, "ping") if resource is None else resource
    work = next(item for item in iter_execution_work(project.prepared) if item.ordinal.value == 0)
    environment_resource = _resource(
        project,
        ResourceKind.ENVIRONMENT,
        work.environment.identity.resource_id.value,
    )
    effective_settings = _settings() if settings is None else settings
    return ResolvedValueInspectionRequest(
        project=project,
        resource=_coordinate(selected),
        field_path=field_path,
        execution_ordinal=ordinal,
        environment=_coordinate(environment_resource) if environment is None else environment,
        settings=effective_settings,
        start_node=start_node,
        maximum_nodes=maximum_nodes,
        ambient_environment=CapturedAmbientEnvironment(
            (),
            limits=effective_settings.environment,
        ),
    )


def _inspect(request: ResolvedValueInspectionRequest) -> ResolvedValueInspectionResult:
    return asyncio.run(inspect_resolved_value(request))


def test_resolves_matrix_and_environment_in_the_exact_execution_context(tmp_path: Path) -> None:
    project = _project(tmp_path)
    eu = _inspect(_inspection_request(project))
    us = _inspect(_inspection_request(project, ordinal=2))
    env = _inspect(
        _inspection_request(
            project,
            field_path=("request", "headers", "X-Label"),
        )
    )

    assert eu.inspection is not None
    assert us.inspection is not None
    assert eu.inspection.state is ResolvedInspectionState.PRESENT
    assert eu.inspection.nodes[0].preview == "eu"
    assert us.inspection.nodes[0].preview == "us"
    assert eu.inspection.execution.ordinal == 0
    assert eu.inspection.execution.environment.resource_id == "staging"
    assert eu.inspection.provenance.provider_name == "matrix"
    assert env.inspection is not None
    assert env.inspection.masked is True
    assert env.inspection.nodes[0].preview == "<masked>"
    assert "staging-secret-label" not in msgspec.json.encode(env).decode()


def test_resolves_a_whole_effective_request_expression(tmp_path: Path) -> None:
    result = _inspect(
        _inspection_request(
            _project(tmp_path),
            field_path=("request",),
            maximum_nodes=8,
        )
    )

    assert result.inspection is not None
    assert result.inspection.state is ResolvedInspectionState.PRESENT
    assert result.inspection.kind is ValueKind.OBJECT
    assert result.inspection.nodes[0].path == ()
    assert result.inspection.nodes[0].masked is True


def test_secret_and_composed_sensitive_values_are_irreversibly_masked(tmp_path: Path) -> None:
    project = _project(tmp_path)
    secret = _inspect(
        _inspection_request(
            project,
            field_path=("request", "headers", "X-Token"),
        )
    )
    composed = _inspect(
        _inspection_request(
            project,
            field_path=("request", "headers"),
        )
    )

    for result in (secret, composed):
        assert result.inspection is not None
        assert result.inspection.masked is True
        assert result.inspection.kind in {ValueKind.STRING, ValueKind.OBJECT}
        assert result.inspection.nodes[0].masked is True
        assert result.inspection.taint_sources
        encoded = msgspec.json.encode(result).decode()
        assert "super-secret-inspection-canary" not in encoded
        assert "staging-secret-label" not in encoded


def test_value_resource_payloads_remain_masked_during_resolved_inspection(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    value = _resource(project, ResourceKind.VALUE, "private-value")

    result = _inspect(
        _inspection_request(
            project,
            resource=value,
            field_path=("value",),
        )
    )

    assert result.inspection is not None
    assert result.inspection.state is ResolvedInspectionState.PRESENT
    assert result.inspection.kind is ValueKind.STRING
    assert result.inspection.masked
    assert result.inspection.nodes[0].preview == "<masked>"
    assert result.inspection.taint_sources == (ResolvedTaintSource.CONFIGURATION,)
    assert "literal-value-inspection-canary" not in msgspec.json.encode(result).decode()


def test_project_expression_windows_are_bounded_serializable_and_paginated(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    manifest = project.prepared.project.project
    settings = _settings(maximum_page_nodes=2, maximum_scalar_bytes=3)
    first = _inspect(
        _inspection_request(
            project,
            resource=manifest,
            field_path=("variables", "publicTree"),
            settings=settings,
            maximum_nodes=2,
        )
    )
    assert first.inspection is not None
    assert first.inspection.kind is ValueKind.OBJECT
    assert tuple(item.ordinal for item in first.inspection.nodes) == (0, 1)
    assert first.inspection.next_node == 2
    second = _inspect(
        _inspection_request(
            project,
            resource=manifest,
            field_path=("variables", "publicTree"),
            settings=settings,
            start_node=2,
            maximum_nodes=2,
        )
    )
    assert second.inspection is not None
    assert tuple(item.ordinal for item in second.inspection.nodes) == (2, 3)
    assert second.inspection.nodes[0].preview == "abc"
    assert second.inspection.nodes[0].truncated is True
    assert second.inspection.next_node == 4
    tail = _inspect(
        _inspection_request(
            project,
            resource=manifest,
            field_path=("variables", "publicTree"),
            settings=settings,
            start_node=20,
            maximum_nodes=2,
        )
    )
    assert tail.inspection is not None
    assert tail.inspection.nodes == ()
    assert tail.inspection.next_node is None
    msgspec.json.decode(msgspec.json.encode(second))


def test_policy_controlled_project_file_read_returns_only_a_bounded_public_projection(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    result = _inspect(
        _inspection_request(
            project,
            resource=project.prepared.project.project,
            field_path=("variables", "fileValue"),
            settings=_settings(maximum_scalar_bytes=4),
        )
    )

    assert result.inspection is not None
    node = result.inspection.nodes[0]
    assert node.kind is ValueKind.BYTES
    assert node.format is ResolvedValueFormat.HEX
    assert node.preview == "00ab"
    assert node.byte_count == 3
    assert node.truncated is True


def test_typed_request_override_is_the_effective_expression_being_inspected(
    tmp_path: Path,
) -> None:
    project = _project(
        tmp_path,
        options=PlanningOptions(
            requests=(RequestOverride(subject="ping", endpoint=_literal("/overridden")),)
        ),
    )
    result = _inspect(
        _inspection_request(
            project,
            field_path=("request", "endpoint"),
        )
    )

    assert result.inspection is not None
    assert result.inspection.nodes[0].preview == "/overridden"
    assert result.inspection.provenance.terminal_outcome == "present"


def test_request_nested_field_uses_the_production_top_level_resolution_symbol(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    header = _inspection_request(
        project,
        field_path=("request", "headers", "X-Region"),
    )
    endpoint = _inspection_request(
        project,
        field_path=("request", "endpoint"),
    )
    generic = _inspection_request(
        project,
        resource=project.prepared.project.project,
        field_path=("variables", "publicTree"),
    )

    header_selection = inspection_module._select(header)
    endpoint_selection = inspection_module._select(endpoint)
    generic_selection = inspection_module._select(generic)
    assert isinstance(header_selection, inspection_module._Selection)
    assert isinstance(endpoint_selection, inspection_module._Selection)
    assert isinstance(generic_selection, inspection_module._Selection)
    assert header_selection.symbol.segments == ("request", "headers", "X-Region")
    assert endpoint_selection.symbol.segments == ("request", "endpoint")
    assert generic_selection.symbol.segments == ("variables", "publicTree")


def test_non_present_resolution_outcomes_are_value_free_and_closed(tmp_path: Path) -> None:
    project = _project(tmp_path)
    manifest = project.prepared.project.project
    expected = (
        ("missingValue", ResolvedInspectionState.UNAVAILABLE, "not-defined"),
        ("optionalValue", ResolvedInspectionState.MISSING, None),
        ("errorValue", ResolvedInspectionState.ERROR, "conversion-failed"),
    )
    for name, state, code in expected:
        result = _inspect(
            _inspection_request(
                project,
                resource=manifest,
                field_path=("variables", name),
            )
        )
        assert result.inspection is not None
        assert result.inspection.state is state
        assert result.inspection.code == code
        assert result.inspection.nodes == ()
        assert result.inspection.kind is None

    denied_root = tmp_path / "denied"
    denied_root.mkdir()
    denied = _project(denied_root, policy=_write_policy(denied_root))
    blocked = _inspect(
        _inspection_request(
            denied,
            resource=denied.prepared.project.project,
            field_path=("variables", "fileValue"),
        )
    )
    assert blocked.inspection is not None
    assert blocked.inspection.state is ResolvedInspectionState.BLOCKED
    assert blocked.inspection.code == "capability-denied"


def test_nested_expression_paths_share_the_compiler_outline_vocabulary(tmp_path: Path) -> None:
    project = _project(tmp_path)
    manifest = project.prepared.project.project
    paths = (
        ("variables", "fileValue", "path"),
        ("variables", "optionalValue", "source"),
        ("variables", "errorValue", "source"),
        ("variables", "fallbackValue", "1"),
        ("variables", "interpolatedValue", "expression-2"),
        ("variables", "publicTree", "items", "0"),
    )
    for path in paths:
        result = _inspect(_inspection_request(project, resource=manifest, field_path=path))
        assert result.inspection is not None
        assert result.inspection.state in {
            ResolvedInspectionState.PRESENT,
            ResolvedInspectionState.UNAVAILABLE,
        }

    canonical_array = _inspect(_inspection_request(project, field_path=("matrices", "0")))
    assert canonical_array.diagnostics[0].code == "resolution.inspect.expression-not-found"


def test_value_fingerprint_guards_multi_page_consistency(tmp_path: Path) -> None:
    project = _project(tmp_path)
    manifest = project.prepared.project.project
    first = _inspect(
        _inspection_request(
            project,
            resource=manifest,
            field_path=("variables", "publicTree"),
            maximum_nodes=2,
        )
    )
    assert first.inspection is not None
    fingerprint = first.inspection.projection_fingerprint
    continued = _inspect(
        msgspec.structs.replace(
            _inspection_request(
                project,
                resource=manifest,
                field_path=("variables", "publicTree"),
                start_node=2,
                maximum_nodes=2,
            ),
            expected_projection_fingerprint=fingerprint,
        )
    )
    assert continued.inspection is not None
    assert continued.inspection.projection_fingerprint == fingerprint
    changed = _inspect(
        msgspec.structs.replace(
            _inspection_request(
                project,
                resource=manifest,
                field_path=("variables", "publicTree"),
            ),
            expected_projection_fingerprint="0" * 64,
        )
    )
    assert changed.diagnostics[0].code == "resolution.inspect.projection-changed"


@pytest.mark.parametrize(
    "settings",
    [
        _settings(maximum_path_segments=2),
        _settings(maximum_path_segment_bytes=10),
    ],
)
def test_resolved_output_paths_fail_closed_at_caller_owned_limits(
    tmp_path: Path,
    settings: ResolvedValueInspectionSettings,
) -> None:
    project = _project(tmp_path)
    result = _inspect(
        _inspection_request(
            project,
            resource=project.prepared.project.project,
            field_path=("variables", "publicTree"),
            settings=settings,
        )
    )
    assert result.diagnostics[0].code == "resolution.inspect.projection-budget-exceeded"


def test_private_projection_is_exhaustive_without_unbounded_scalar_copies() -> None:
    values = (
        JsonNull(),
        JsonBoolean(value=False),
        JsonInteger(value=-(10**200)),
        JsonDecimal(value=Decimal("1.25")),
        JsonDecimal(value=Decimal("0.000001")),
        JsonString(value="\ud800" + "x" * 100),
        JsonArray(items=()),
        JsonObject(members=()),
        BytesValue(value=b"\x00\xff"),
        Duration(nanoseconds=10**200),
        ByteCount(value=0),
    )
    for value in values:
        projection = asyncio.run(
            inspection_module._project_public_value(
                value,
                _settings(),
                start=0,
                maximum=1,
            )
        )
        assert projection.fingerprint
        node = inspection_module._node(0, (), value, 4)
        assert node.ordinal == 0
    assert inspection_module._maximum_decimal_length(0) == 1
    assert inspection_module._maximum_decimal_length(-10) >= 3
    assert inspection_module._bounded_decimal(Decimal("1.25"), 4) == ("1.25", 4, False)
    assert inspection_module._bounded_decimal(Decimal("0.000001"), 2) == (None, None, True)
    assert inspection_module._decimal_length(Decimal("1E+3")) == 4
    assert inspection_module._utf8_exceeds("é", 1) is True
    assert inspection_module._utf8_exceeds("é", 2) is False


def test_wide_value_traversal_uses_depth_bounded_auxiliary_memory() -> None:
    value = JsonArray(items=(JsonNull(),) * 200_000)

    tracemalloc.start()
    projection = asyncio.run(
        inspection_module._project_public_value(
            value,
            _settings(),
            start=0,
            maximum=2,
        )
    )
    _current, projection_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert projection.nodes[0].ordinal == 0
    assert projection.nodes[1].path == ("0",)
    assert projection_peak < 1_000_000


def test_deep_single_child_traversal_memory_scales_linearly_with_depth() -> None:
    def peak_at_depth(depth: int) -> int:
        value: JsonArray | JsonNull = JsonNull()
        for _ in range(depth):
            value = JsonArray(items=(value,))
        settings = _settings(
            maximum_path_segments=depth + 1,
            maximum_traversal_nodes=depth + 2,
        )

        tracemalloc.start()
        projection = asyncio.run(
            inspection_module._project_public_value(value, settings, start=0, maximum=1)
        )
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert projection.nodes[0].path == ()
        return peak

    shallow_peak = peak_at_depth(1_000)
    deep_peak = peak_at_depth(4_000)

    assert deep_peak < shallow_peak * 6
    assert deep_peak < 1_500_000


def test_deep_projection_preserves_preorder_paths_and_fingerprint() -> None:
    value = JsonObject(
        members=(
            JsonObjectMember(
                name="alpha",
                value=JsonArray(items=(JsonNull(), JsonString(value="x"))),
            ),
            JsonObjectMember(
                name="beta",
                value=JsonObject(
                    members=(
                        JsonObjectMember(name="n", value=JsonInteger(value=7)),
                        JsonObjectMember(name="ok", value=JsonBoolean(value=True)),
                    )
                ),
            ),
        )
    )

    projection = asyncio.run(
        inspection_module._project_public_value(
            value,
            _settings(maximum_page_nodes=16),
            start=0,
            maximum=16,
        )
    )

    assert projection.fingerprint == (
        "751351de429c77b4efcd2be85b8c1198f0be68ec392d5011dc30ff7ad7825279"
    )
    assert tuple(node.path for node in projection.nodes) == (
        (),
        ("alpha",),
        ("alpha", "0"),
        ("alpha", "1"),
        ("beta",),
        ("beta", "n"),
        ("beta", "ok"),
    )
    assert tuple(node.ordinal for node in projection.nodes) == tuple(range(7))


def test_projection_validates_each_path_segment_once_when_descending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked: list[str] = []
    original = inspection_module._utf8_exceeds

    def record_check(value: str, maximum_bytes: int) -> bool:
        checked.append(value)
        return original(value, maximum_bytes)

    monkeypatch.setattr(inspection_module, "_utf8_exceeds", record_check)
    value = JsonObject(
        members=(
            JsonObjectMember(
                name="a",
                value=JsonObject(
                    members=(
                        JsonObjectMember(
                            name="b",
                            value=JsonObject(
                                members=(JsonObjectMember(name="c", value=JsonNull()),)
                            ),
                        ),
                    )
                ),
            ),
        )
    )

    projection = asyncio.run(
        inspection_module._project_public_value(
            value,
            _settings(),
            start=0,
            maximum=1,
        )
    )

    assert projection.fingerprint
    assert checked == ["a", "b", "c"]


@pytest.mark.parametrize(
    ("settings", "value"),
    [
        (
            _settings(maximum_path_segments=2),
            JsonArray(items=(JsonArray(items=(JsonArray(items=(JsonNull(),)),)),)),
        ),
        (
            _settings(maximum_path_segment_bytes=1),
            JsonObject(members=(JsonObjectMember(name="é", value=JsonNull()),)),
        ),
    ],
)
def test_projection_rejects_depth_and_utf8_segment_path_budgets(
    settings: ResolvedValueInspectionSettings,
    value: JsonArray | JsonObject,
) -> None:
    with pytest.raises(inspection_module._ProjectionBudgetExceededError):
        asyncio.run(inspection_module._project_public_value(value, settings, start=0, maximum=1))


def test_wide_flat_siblings_keep_independent_paths() -> None:
    array = JsonArray(items=(JsonNull(),) * 18)
    object_value = JsonObject(
        members=tuple(JsonObjectMember(name=f"k{index}", value=JsonNull()) for index in range(18))
    )

    settings = _settings(maximum_page_nodes=32)
    array_nodes = asyncio.run(
        inspection_module._project_public_value(array, settings, start=0, maximum=32)
    ).nodes
    object_nodes = asyncio.run(
        inspection_module._project_public_value(object_value, settings, start=0, maximum=32)
    ).nodes

    assert array_nodes[-1].path == ("17",)
    assert object_nodes[-1].path == ("k17",)
    assert all(len(node.path) <= 1 for node in (*array_nodes, *object_nodes))


def test_projection_fingerprint_uses_collision_safe_variable_width_framing() -> None:
    first = JsonArray(
        items=(JsonString(value="astring\0b"), JsonString(value="")),
    )
    second = JsonArray(
        items=(JsonString(value="a"), JsonString(value="bstring\0")),
    )

    first_projection = asyncio.run(
        inspection_module._project_public_value(first, _settings(), start=0, maximum=1)
    )
    second_projection = asyncio.run(
        inspection_module._project_public_value(second, _settings(), start=0, maximum=1)
    )

    assert first_projection.fingerprint != second_projection.fingerprint


def test_projection_rejects_caller_owned_traversal_node_and_byte_budgets() -> None:
    with pytest.raises(inspection_module._ProjectionBudgetExceededError):
        asyncio.run(
            inspection_module._project_public_value(
                JsonArray(items=(JsonNull(),) * 20),
                _settings(maximum_traversal_nodes=10),
                start=0,
                maximum=1,
            )
        )
    with pytest.raises(inspection_module._ProjectionBudgetExceededError):
        asyncio.run(
            inspection_module._project_public_value(
                JsonString(value="x" * 20),
                _settings(maximum_traversal_bytes=10),
                start=0,
                maximum=1,
            )
        )


def test_projection_traversal_is_cooperatively_cancellable() -> None:
    async def scenario() -> None:
        task = asyncio.create_task(
            inspection_module._project_public_value(
                JsonArray(items=(JsonNull(),) * 200_000),
                _settings(),
                start=199_999,
                maximum=1,
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_nested_consumer_symbol_matches_object_array_and_wrapper_rules() -> None:
    provenance = _literal("x").provenance
    expression = ObjectExpr(
        members=(
            ObjectExpressionMember(
                name="headers",
                expression=ArrayExpr(
                    items=(
                        ConvertExpr(
                            source=_literal("1"),
                            target=ConversionType.INTEGER,
                            provenance=provenance,
                        ),
                    ),
                    provenance=provenance,
                ),
            ),
        ),
        provenance=provenance,
    )

    selected = inspection_module._expression_and_symbol_at(
        expression,
        ("headers", "0", "source"),
        SymbolPath(segments=("request",)),
    )

    assert selected is not None
    assert selected[1].segments == ("request", "headers", "item-0")


def test_taint_lineage_is_bounded_and_discloses_only_opaque_categories() -> None:
    origins = (
        TaintOriginId(value="secret/aaaaaaaa"),
        TaintOriginId(value="env/bbbbbbbb"),
        TaintOriginId(value="file/cccccccc"),
        TaintOriginId(value="provider/dddddddd"),
    )
    sources, omitted = asyncio.run(
        inspection_module._bounded_taint_sources(
            origins,
            _settings(maximum_taint_origins=2, maximum_taint_origin_bytes=32),
        )
    )

    assert sources == (ResolvedTaintSource.SECRET, ResolvedTaintSource.ENVIRONMENT)
    assert omitted == 2
    encoded = msgspec.json.encode({"sources": sources, "omitted": omitted}).decode()
    assert "aaaaaaaa" not in encoded
    assert "bbbbbbbb" not in encoded


def test_taint_lineage_work_and_byte_egress_are_independently_bounded() -> None:
    repeated_category = tuple(TaintOriginId(value=f"secret/source-{index}") for index in range(300))
    sources, omitted = asyncio.run(
        inspection_module._bounded_taint_sources(
            repeated_category,
            _settings(maximum_taint_origins=300, maximum_taint_origin_bytes=32),
        )
    )
    assert sources == (ResolvedTaintSource.SECRET,)
    assert omitted == 0

    denied, denied_count = asyncio.run(
        inspection_module._bounded_taint_sources(
            (
                TaintOriginId(value="secret/one"),
                TaintOriginId(value="environment/two"),
            ),
            _settings(maximum_taint_origins=2, maximum_taint_origin_bytes=1),
        )
    )
    assert denied == ()
    assert denied_count == 2


def test_private_node_window_skips_prefix_and_detects_continuation() -> None:
    nodes = tuple(
        inspection_module._node(
            ordinal,
            (str(ordinal),),
            JsonNull(),
            8,
        )
        for ordinal in range(3)
    )

    page, next_node = inspection_module._window(iter(nodes), start=1, maximum=1)

    assert tuple(item.ordinal for item in page) == (1,)
    assert next_node == 2


def test_masked_projection_fingerprint_does_not_claim_secret_content_identity() -> None:
    first = inspection_module._masked_projection_fingerprint(
        ValueKind.STRING,
        (ResolvedTaintSource.SECRET,),
        0,
    )
    second = inspection_module._masked_projection_fingerprint(
        ValueKind.STRING,
        (ResolvedTaintSource.SECRET,),
        0,
    )
    assert first == second


def test_private_expression_traversal_handles_inline_and_invalid_indexes(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    resource = _resource(project, ResourceKind.REQUEST, "ping")
    expression = _literal("value")
    canonical = CanonicalArray(items=(expression,))
    assert inspection_module._child(canonical, "0") is expression
    assert inspection_module._child(canonical, "01") is None
    assert inspection_module._child(canonical, "-1") is None
    assert inspection_module._child(expression, "absent") is None
    inline = InlineResource(
        definition=InlineDefinitionRef(
            kind=ResourceKind.REQUEST,
            origin=InlineDefinitionOrigin(
                owner=resource.definition.identity,
                fragment=InlineFragment(value="#inline"),
            ),
            fingerprint=resource.definition.fingerprint,
        ),
        spec=CanonicalObject(members=()),
    )
    assert inspection_module._child(inline, "spec") is inline.spec
    assert inspection_module._child(inline, "other") is None
    interpolation = inspection_module._expression_at(
        project.prepared.project.project.spec,
        ("variables", "interpolatedValue"),
    )
    assert interpolation is not None
    assert inspection_module._child(interpolation, "absent") is None


def test_resource_cleanup_contains_each_independent_failure() -> None:
    class BrokenContext:
        async def close(self) -> None:
            raise RuntimeError("context-canary")

    class BrokenFiles:
        def close(self) -> None:
            raise RuntimeError("files-canary")

    resources = inspection_module._InspectionResources()
    resources.context = cast("RunContext", BrokenContext())
    resources.files = cast("PinnedProjectFiles", BrokenFiles())
    assert asyncio.run(resources.close()) is True


def test_resource_cleanup_closes_files_under_repeated_cancellation() -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        class SlowContext:
            async def close(self) -> None:
                entered.set()
                await release.wait()

        class Files:
            closed = False

            def close(self) -> None:
                self.closed = True

        resources = inspection_module._InspectionResources()
        files = Files()
        resources.context = cast("RunContext", SlowContext())
        resources.files = cast("PinnedProjectFiles", files)
        task = asyncio.create_task(resources.close())
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert files.closed

    asyncio.run(scenario())


def test_environment_binding_diagnostics_cross_only_as_safe_application_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    diagnostic = ApplicationDiagnostic(
        code="environment.snapshot.not-found",
        severity="error",
        stage="resolution",
    )

    async def rejected(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(diagnostics=(object(),))

    monkeypatch.setattr(inspection_module, "build_environment_snapshots", rejected)
    monkeypatch.setattr(inspection_module, "inspect_diagnostic", lambda _item: diagnostic)
    result = _inspect(_inspection_request(project))

    assert result.diagnostics == (diagnostic,)


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ("resource", "resolution.inspect.resource-not-found"),
        ("path", "resolution.inspect.expression-not-found"),
        ("execution", "resolution.inspect.execution-not-found"),
        ("environment", "resolution.inspect.environment-mismatch"),
        ("subject", "resolution.inspect.subject-mismatch"),
    ],
)
def test_exact_selection_rejects_stale_or_mismatched_coordinates(
    tmp_path: Path,
    change: str,
    code: str,
) -> None:
    project = _project(tmp_path)
    request = _inspection_request(project)
    if change == "resource":
        request = msgspec.structs.replace(
            request,
            resource=msgspec.structs.replace(request.resource, fingerprint="0" * 64),
        )
    elif change == "path":
        request = msgspec.structs.replace(request, field_path=("request", "absent"))
    elif change == "execution":
        request = msgspec.structs.replace(request, execution_ordinal=100)
    elif change == "environment":
        prod = _coordinate(_resource(project, ResourceKind.ENVIRONMENT, "prod"))
        request = msgspec.structs.replace(request, environment=prod)
    else:
        request = msgspec.structs.replace(
            request,
            resource=_coordinate(_resource(project, ResourceKind.REQUEST, "other")),
        )

    result = _inspect(request)

    assert result.inspection is None
    assert result.diagnostics[0].code == code
    assert result.diagnostics[0].path == request.field_path


def test_deep_execution_selection_delegates_to_direct_ordinal_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    deep_ordinal = 1_000_000_000
    work = next(iter_execution_work(project.prepared))
    deep_work = msgspec.structs.replace(work, ordinal=Count(value=deep_ordinal))
    calls: list[int] = []

    def direct_lookup(_prepared: object, ordinal: int) -> object:
        calls.append(ordinal)
        return deep_work

    monkeypatch.setattr(inspection_module, "execution_work_at", direct_lookup)
    request = msgspec.structs.replace(
        _inspection_request(project),
        execution_ordinal=deep_ordinal,
    )

    selected = inspection_module._select(request)

    assert isinstance(selected, inspection_module._Selection)
    assert selected.work is deep_work
    assert calls == [deep_ordinal]


def test_setup_internal_cleanup_and_capability_failures_are_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    request = _inspection_request(project)

    class BrokenFiles:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise LocalFilesystemError(LocalFilesystemErrorCode.ROOT_UNSAFE)

    monkeypatch.setattr(inspection_module, "PinnedProjectFiles", BrokenFiles)
    unavailable = _inspect(request)
    assert unavailable.diagnostics[0].code == "resolution.inspect.project-root-unavailable"
    monkeypatch.undo()

    monkeypatch.setattr(inspection_module, "_inspect_with_resources", _raise_internal)
    internal = _inspect(request)
    assert internal.diagnostics[0].code == "resolution.inspect.internal"
    monkeypatch.undo()

    async def failed_close(_self: object) -> bool:
        return True

    monkeypatch.setattr(inspection_module._InspectionResources, "close", failed_close)
    cleanup = _inspect(request)
    assert cleanup.diagnostics[0].code == "resolution.inspect.cleanup-failed"
    monkeypatch.undo()

    scenario = msgspec.structs.replace(
        project.prepared.effective.subjects[0].definition,
        identity=msgspec.structs.replace(
            project.prepared.effective.subjects[0].definition.identity,
            kind=ResourceKind.SCENARIO,
        ),
    )
    unsupported_subject = msgspec.structs.replace(
        project.prepared.effective.subjects[0],
        definition=scenario,
    )
    unsupported = msgspec.structs.replace(
        project,
        prepared=msgspec.structs.replace(
            project.prepared,
            effective=msgspec.structs.replace(
                project.prepared.effective,
                subjects=(unsupported_subject,),
            ),
        ),
    )
    capabilities = _inspect(msgspec.structs.replace(request, project=unsupported))
    assert capabilities.diagnostics


async def _raise_internal(*_args: object, **_kwargs: object) -> Never:
    raise RuntimeError("unsafe-canary")


def test_cancellation_propagates_after_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)

    async def cancel(*_args: object, **_kwargs: object) -> Never:
        raise asyncio.CancelledError

    monkeypatch.setattr(inspection_module, "_inspect_with_resources", cancel)
    with pytest.raises(asyncio.CancelledError):
        _inspect(_inspection_request(project))


def test_public_contracts_reject_ambiguous_and_unbounded_shapes(tmp_path: Path) -> None:
    project = _project(tmp_path)
    settings = _settings()
    request = _inspection_request(project)
    coordinate = request.resource
    environment = request.environment

    for field in (
        "files",
        "environment",
        "yaml",
        "maximum_page_nodes",
        "maximum_scalar_bytes",
        "maximum_path_segments",
        "maximum_path_segment_bytes",
        "maximum_traversal_nodes",
        "maximum_traversal_bytes",
        "maximum_taint_origins",
        "maximum_taint_origin_bytes",
    ):
        with pytest.raises(TypeError, match="untyped limit"):
            msgspec.structs.replace(settings, **{field: object()})
    for field in (
        "maximum_scalar_bytes",
        "maximum_path_segment_bytes",
        "maximum_traversal_bytes",
        "maximum_taint_origin_bytes",
    ):
        with pytest.raises(ValueError, match="positive"):
            msgspec.structs.replace(settings, **{field: ByteCount(value=0)})
    with pytest.raises(TypeError, match="PreparedProjectPlan"):
        msgspec.structs.replace(request, project=cast("PreparedProjectPlan", object()))
    with pytest.raises(TypeError, match="resource"):
        msgspec.structs.replace(request, resource=cast("ResourceCoordinate", object()))
    with pytest.raises(TypeError, match="Environment"):
        msgspec.structs.replace(request, environment=cast("ResourceCoordinate", object()))
    with pytest.raises(ValueError, match="identify Environment"):
        msgspec.structs.replace(request, environment=coordinate)
    with pytest.raises(TypeError, match="typed settings"):
        msgspec.structs.replace(
            request,
            settings=cast("ResolvedValueInspectionSettings", object()),
        )
    with pytest.raises(TypeError, match="field path"):
        msgspec.structs.replace(request, field_path=cast("tuple[str, ...]", ("",)))
    with pytest.raises(ValueError, match="field path exceeds"):
        msgspec.structs.replace(
            request,
            field_path=("a", "b"),
            settings=_settings(maximum_path_segments=1),
        )
    with pytest.raises(ValueError, match="field path exceeds"):
        msgspec.structs.replace(
            request,
            field_path=("too-long",),
            settings=_settings(maximum_path_segment_bytes=2),
        )
    for field in ("execution_ordinal", "start_node"):
        with pytest.raises(ValueError, match="non-negative"):
            msgspec.structs.replace(request, **{field: -1})
    with pytest.raises(ValueError, match="cursor exceeds"):
        msgspec.structs.replace(
            request,
            start_node=10,
            settings=_settings(maximum_traversal_nodes=10),
        )
    for maximum in (0, settings.maximum_page_nodes.value + 1):
        with pytest.raises(ValueError, match="page size"):
            msgspec.structs.replace(request, maximum_nodes=maximum)
    with pytest.raises(TypeError, match="must be sealed"):
        msgspec.structs.replace(
            request,
            ambient_environment=cast("CapturedAmbientEnvironment", object()),
        )
    with pytest.raises(ValueError, match="expected fingerprint"):
        msgspec.structs.replace(request, expected_projection_fingerprint="invalid")
    with pytest.raises(TypeError, match="internal bound request"):
        asyncio.run(inspect_resolved_value(cast("ResolvedValueInspectionRequest", object())))
    assert environment.kind == "Environment"


def test_frontend_target_binds_only_inside_the_retained_plan_lifetime(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    request = _inspection_request(project)
    limits = _projection_limits()
    target = ResolvedValueInspectionTarget(
        resource=request.resource,
        field_path=request.field_path,
        execution_ordinal=request.execution_ordinal,
        environment=request.environment,
        limits=limits,
        start_node=request.start_node,
        maximum_nodes=request.maximum_nodes,
    )

    bound = bind_resolved_value_target(
        project,
        target,
        files=LocalFileLimits(),
        environment=EnvironmentSourceLimits(),
        yaml=YamlValueLimits(),
        ambient_environment=CapturedAmbientEnvironment(
            (),
            limits=EnvironmentSourceLimits(),
        ),
    )

    assert bound.project is project
    assert bound.resource == target.resource
    assert bound.settings.maximum_page_nodes == limits.maximum_page_nodes
    assert repr(bound.ambient_environment) == "<captured-ambient-environment>"

    for field in (
        "maximum_page_nodes",
        "maximum_scalar_bytes",
        "maximum_path_segments",
        "maximum_path_segment_bytes",
        "maximum_traversal_nodes",
        "maximum_traversal_bytes",
        "maximum_taint_origins",
        "maximum_taint_origin_bytes",
    ):
        with pytest.raises(TypeError, match="untyped limit"):
            msgspec.structs.replace(limits, **{field: object()})
    for field in (
        "maximum_scalar_bytes",
        "maximum_path_segment_bytes",
        "maximum_traversal_bytes",
        "maximum_taint_origin_bytes",
    ):
        with pytest.raises(ValueError, match="positive"):
            msgspec.structs.replace(limits, **{field: ByteCount(value=0)})
    with pytest.raises(TypeError, match="target resource"):
        msgspec.structs.replace(target, resource=cast("ResourceCoordinate", object()))
    with pytest.raises(TypeError, match="target Environment"):
        msgspec.structs.replace(target, environment=cast("ResourceCoordinate", object()))
    with pytest.raises(ValueError, match="identify Environment"):
        msgspec.structs.replace(target, environment=target.resource)
    with pytest.raises(TypeError, match="target limits"):
        msgspec.structs.replace(
            target,
            limits=cast("ResolvedValueProjectionLimits", object()),
        )
    with pytest.raises(ValueError, match="non-negative"):
        msgspec.structs.replace(target, execution_ordinal=-1)
    with pytest.raises(TypeError, match="PreparedProjectPlan"):
        bind_resolved_value_target(
            cast("PreparedProjectPlan", object()),
            target,
            files=LocalFileLimits(),
            environment=EnvironmentSourceLimits(),
            yaml=YamlValueLimits(),
            ambient_environment=CapturedAmbientEnvironment(
                (),
                limits=EnvironmentSourceLimits(),
            ),
        )
    with pytest.raises(TypeError, match="ResolvedValueInspectionTarget"):
        bind_resolved_value_target(
            project,
            cast("ResolvedValueInspectionTarget", object()),
            files=LocalFileLimits(),
            environment=EnvironmentSourceLimits(),
            yaml=YamlValueLimits(),
            ambient_environment=CapturedAmbientEnvironment(
                (),
                limits=EnvironmentSourceLimits(),
            ),
        )


def test_resolved_value_batches_preserve_target_order_and_caller_limits(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    requests = (
        _inspection_request(project, field_path=("request", "headers", "X-Region")),
        _inspection_request(project, field_path=("request", "headers", "X-Label")),
    )
    targets = tuple(
        ResolvedValueInspectionTarget(
            resource=request.resource,
            field_path=request.field_path,
            execution_ordinal=request.execution_ordinal,
            environment=request.environment,
            limits=_projection_limits(),
            start_node=request.start_node,
            maximum_nodes=request.maximum_nodes,
        )
        for request in requests
    )
    batch = ResolvedValueInspectionBatch(targets=targets, maximum_targets=2)

    result = asyncio.run(inspect_resolved_value_batch(requests))

    assert type(result) is ResolvedValueInspectionBatchResult
    assert tuple(item.inspection.field_path for item in result.results if item.inspection) == (
        targets[0].field_path,
        targets[1].field_path,
    )
    with pytest.raises(ValueError, match="positive integer"):
        msgspec.structs.replace(batch, maximum_targets=0)
    with pytest.raises(TypeError, match="targets"):
        msgspec.structs.replace(
            batch,
            targets=cast("tuple[ResolvedValueInspectionTarget, ...]", (object(),)),
        )
    with pytest.raises(ValueError, match="caller-owned"):
        msgspec.structs.replace(batch, maximum_targets=1)
    with pytest.raises(ValueError, match="caller-owned"):
        ResolvedValueInspectionBatch(targets=(), maximum_targets=1)
    with pytest.raises(TypeError, match="batch results"):
        ResolvedValueInspectionBatchResult(results=())
    with pytest.raises(TypeError, match="batch results"):
        ResolvedValueInspectionBatchResult(
            results=cast("tuple[ResolvedValueInspectionResult, ...]", (object(),))
        )
    with pytest.raises(TypeError, match="bound requests"):
        asyncio.run(inspect_resolved_value_batch(()))
    with pytest.raises(TypeError, match="bound requests"):
        asyncio.run(
            inspect_resolved_value_batch(
                cast("tuple[ResolvedValueInspectionRequest, ...]", (object(),))
            )
        )


def test_output_contracts_are_closed_and_serializable(tmp_path: Path) -> None:
    project = _project(tmp_path)
    actual = _inspect(_inspection_request(project)).inspection
    assert actual is not None
    execution = actual.execution
    provenance = actual.provenance
    node = actual.nodes[0]
    diagnostic = ApplicationDiagnostic(code="test", severity="error", stage="resolution")
    with pytest.raises(ValueError, match="checkpoint"):
        ResolvedValueFreshness(plan_checkpoint_exact=False)
    with pytest.raises(ValueError, match="source timing"):
        ResolvedValueFreshness(ambient_environment=ResolvedInputTiming.INSPECTION_TIME)
    with pytest.raises(ValueError, match="source timing"):
        ResolvedValueFreshness(generated_values=ResolvedInputTiming.FROZEN_REQUEST)
    with pytest.raises(ValueError, match="source timing"):
        ResolvedValueFreshness(
            execution_generated_values=ResolvedInputTiming.INSPECTION_TIME,
        )

    execution_changes: tuple[dict[str, object], ...] = (
        {"ordinal": -1},
        {"execution_id": ""},
        {"subject": object()},
    )
    for kwargs in execution_changes:
        with pytest.raises((TypeError, ValueError)):
            msgspec.structs.replace(execution, **kwargs)
    provenance_changes: tuple[dict[str, object], ...] = (
        {"provider_name": ""},
        {"namespace": ""},
        {"source_attempts": 0},
    )
    for kwargs in provenance_changes:
        with pytest.raises(ValueError, match="resolution provenance"):
            msgspec.structs.replace(provenance, **kwargs)
    node_changes: tuple[dict[str, object], ...] = (
        {"ordinal": -1},
        {"path": object()},
        {"kind": ValueKind.UNKNOWN},
        {"format": object()},
        {"preview": object()},
        {"byte_count": -1},
        {"child_count": -1},
        {"truncated": object()},
        {"masked": True},
        {"kind": ValueKind.ARRAY, "format": ResolvedValueFormat.TEXT},
        {
            "kind": ValueKind.ARRAY,
            "format": ResolvedValueFormat.NONE,
            "preview": "invalid",
        },
        {"kind": ValueKind.STRING, "child_count": 1},
    )
    for kwargs in node_changes:
        with pytest.raises((TypeError, ValueError)):
            msgspec.structs.replace(node, **kwargs)
    masked = ResolvedValueNode(
        ordinal=0,
        path=(),
        kind=ValueKind.ARRAY,
        format=ResolvedValueFormat.MASKED,
        preview="<masked>",
        byte_count=None,
        child_count=0,
        truncated=False,
        masked=True,
    )
    with pytest.raises(ValueError, match="version-owned"):
        msgspec.structs.replace(masked, preview="secret")

    inspection_changes: tuple[dict[str, object], ...] = (
        {"resource": object()},
        {"field_path": ("",)},
        {"execution": object()},
        {"state": object()},
        {"provenance": object()},
        {"projection_fingerprint": "invalid"},
        {"freshness": object()},
        {"code": ""},
        {"kind": ValueKind.UNKNOWN},
        {"masked": object()},
        {"taint_sources": object()},
        {"taint_sources": (object(),)},
        {"taint_sources": (ResolvedTaintSource.SECRET, ResolvedTaintSource.SECRET)},
        {"omitted_taint_origin_count": -1},
        {"start_node": -1},
        {"nodes": object()},
        {"nodes": (msgspec.structs.replace(node, ordinal=2),)},
        {"next_node": 2},
        {"code": "bad"},
        {"state": ResolvedInspectionState.ERROR},
        {"masked": True, "taint_sources": (), "omitted_taint_origin_count": 0},
    )
    for kwargs in inspection_changes:
        with pytest.raises((TypeError, ValueError)):
            msgspec.structs.replace(actual, **kwargs)
    coordinate = actual.resource
    missing = ResolvedValueInspection(
        resource=coordinate,
        field_path=actual.field_path,
        execution=execution,
        state=ResolvedInspectionState.MISSING,
        provenance=provenance,
        projection_fingerprint=actual.projection_fingerprint,
    )
    with pytest.raises(ValueError, match="failure code"):
        msgspec.structs.replace(missing, code="not-allowed")
    error = msgspec.structs.replace(
        missing,
        state=ResolvedInspectionState.ERROR,
        code="provider-failed",
    )
    assert error.code == "provider-failed"

    success = ResolvedValueInspectionResult(inspection=actual)
    with pytest.raises(TypeError, match="inspection"):
        ResolvedValueInspectionResult(inspection=cast("ResolvedValueInspection", object()))
    with pytest.raises(TypeError, match="diagnostics"):
        ResolvedValueInspectionResult(diagnostics=cast("tuple[ApplicationDiagnostic, ...]", (1,)))
    with pytest.raises(ValueError, match="exactly"):
        ResolvedValueInspectionResult(inspection=actual, diagnostics=(diagnostic,))
    with pytest.raises(ValueError, match="exactly"):
        ResolvedValueInspectionResult()
    assert coordinate == success.inspection.resource if success.inspection is not None else False
