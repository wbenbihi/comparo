"""Request-resolution and conservative materialization edge cases."""

from __future__ import annotations

import asyncio
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import SensitiveLiteralExpr
from comparo.core.domain.http.url import UrlPath
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ResolutionProvenance
from comparo.core.domain.provenance import ResolutionSource
from comparo.core.domain.provenance import ResolutionStep
from comparo.core.domain.provenance import ResolutionStepOutcome
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.provenance import ValueProviderIdentity
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.values import Blocked
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import Error
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import Missing
from comparo.core.domain.values import ResolutionBlockCode
from comparo.core.domain.values import ResolutionErrorCode
from comparo.core.domain.values import Unavailable
from comparo.core.domain.values import UnavailableReason
from comparo.core.planning.models import PreparedRun
from comparo.core.planning.planner import plan_run
from comparo.core.resolution import DeterministicGenerator
from comparo.core.resolution import EnvironmentSnapshot
from comparo.core.resolution import ResolutionTarget
from comparo.core.resolution import ResolvedForWire
from comparo.core.resolution import RunContext
from comparo.core.resolution.snapshots import cell_inputs
from comparo.core.runtime import BlockedExecution
from comparo.core.runtime import CancelledExecution
from comparo.core.runtime import ConfigurationRequestProvider
from comparo.core.runtime import RequestPreparationFailure
from comparo.core.runtime import UnavailableExecution
from comparo.core.runtime import iter_execution_work
from comparo.core.runtime import request_preparation as preparation
from comparo.core.runtime.http import _attempt_scope
from comparo.core.runtime.ports import PreparedRequest
from tests.core.domain.http.factories import definition
from tests.core.planning.conftest import authority
from tests.core.planning.conftest import compile_project
from tests.core.planning.conftest import document
from tests.core.planning.conftest import invocation
from tests.core.runtime.conftest import FixedClock


def _prepared(
    request: str,
    *,
    base_url: str = "'https://api.example.test'",
    project_fields: str = "",
    scenario: bool = False,
) -> PreparedRun:
    subject_kind = "Scenario" if scenario else "Request"
    subject_name = "flow" if scenario else "check"
    project_prefix = f"{project_fields}, " if project_fields else ""
    project = compile_project(
        document(
            "Project",
            "demo",
            f"{{{project_prefix}defaults: {{subjects: [{{$use: {subject_kind}/{subject_name}}}], "
            "environments: [{$use: dev}]}}",
        ),
        document("Environment", "dev", f"{{baseUrl: {base_url}}}"),
        document(subject_kind, subject_name, request),
    )
    result = plan_run(project, invocation(), authority())
    assert result.prepared is not None
    return result.prepared


def _context(prepared: PreparedRun) -> RunContext:
    provider = ValueProviderIdentity(namespace="test", name="environment", version="1")
    snapshots = tuple(
        EnvironmentSnapshot(environment=item, entries=(), provider=provider)
        for item in prepared.effective.environments
    )
    return RunContext(
        prepared=prepared,
        environment_snapshots=snapshots,
        clock=FixedClock(),
        generator=DeterministicGenerator(
            SensitiveValue(
                BytesValue(value=b"request-preparation-seed"),
                origins=(TaintOriginId(value="test/request-seed"),),
            )
        ),
    )


async def _prepare(prepared: PreparedRun) -> PreparedRequest:
    context = _context(prepared)
    provider = ConfigurationRequestProvider(prepared, context)
    work = next(iter_execution_work(prepared))
    try:
        return await provider.prepare(work, _attempt_scope(work, 0))
    finally:
        await provider.close()


def _configuration_provenance() -> ValueProvenance:
    return ValueProvenance(entries=(ProvenanceEntry(kind=ProvenanceKind.PROJECT),))


def _resolution_provenance(outcome: ResolutionStepOutcome) -> ResolutionProvenance:
    provider = ValueProviderIdentity(namespace="test", name="literal", version="1")
    scope = RunScope(run_id=RunId(value=UUID(int=901)))
    source = ResolutionSource(
        namespace=ValueNamespace.VARIABLES,
        symbol=SymbolPath(segments=("value",)),
        provider=provider,
        configuration=_configuration_provenance(),
        scope=scope,
    )
    return ResolutionProvenance(
        namespace=ValueNamespace.VARIABLES,
        symbol=source.symbol,
        provider=provider,
        scope=scope,
        steps=(ResolutionStep(source=source, outcome=outcome),),
    )


def test_explicit_url_query_headers_ports_and_sensitive_values_materialize() -> None:
    prepared = _prepared(
        "{request: {method: get, url: 'http://127.0.0.1:8080/a/?from=url', "
        "query: {extra: 'yes'}, headers: {X-Test: ok}, body: null}}"
    )
    result = asyncio.run(_prepare(prepared))
    assert not isinstance(result, RequestPreparationFailure)
    assert result.method.value == "GET"
    assert result.url.authority.host.value == "127.0.0.1"
    assert result.url.authority.port is not None
    assert result.url.authority.port.value == 8080
    assert result.url.path.trailing_slash
    assert tuple(item.name.value for item in result.url.query.parameters) == ("from", "extra")
    assert result.headers.fields[0].name.value == "x-test"

    secret = _prepared(
        "{request: {method: GET, endpoint: /private, headers: {X-Token: '${secrets.token}'}}}",
        project_fields="secrets: {token: hidden}",
    )
    secret_result = asyncio.run(_prepare(secret))
    assert not isinstance(secret_result, RequestPreparationFailure)
    assert isinstance(secret_result.headers.fields[0].value, SensitiveValue)
    assert isinstance(
        secret_result.materialization.identity.effective_request_fingerprint,
        SensitiveValue,
    )


@pytest.mark.parametrize(
    ("request_config", "base_url", "project_fields", "expected"),
    [
        (
            "{request: {method: POST, endpoint: /, body: hello}}",
            "'https://api.test'",
            "",
            "body-unsupported",
        ),
        (
            "{request: {method: GET, url: 'ftp://api.test'}}",
            "'https://api.test'",
            "",
            "shape-invalid",
        ),
        (
            "{request: {method: GET, url: 'https://u:p@api.test/'}}",
            "'https://api.test'",
            "",
            "shape-invalid",
        ),
        (
            "{request: {method: GET, url: 'https://api.test/#part'}}",
            "'https://api.test'",
            "",
            "shape-invalid",
        ),
        (
            "{request: {method: GET, endpoint: /, query: nope}}",
            "'https://api.test'",
            "",
            "shape-invalid",
        ),
        (
            "{request: {method: GET, endpoint: /, query: {n: 1}}}",
            "'https://api.test'",
            "",
            "shape-invalid",
        ),
        (
            "{request: {method: GET, endpoint: /, headers: nope}}",
            "'https://api.test'",
            "",
            "shape-invalid",
        ),
        (
            "{request: {method: GET, endpoint: /, headers: {x: 1}}}",
            "'https://api.test'",
            "",
            "shape-invalid",
        ),
        ("{request: {method: GET, endpoint: /}}", "12", "", "shape-invalid"),
        (
            "{request: {method: GET, endpoint: /}}",
            "'${secrets.base}'",
            "secrets: {base: 'https://api.test'}",
            "shape-invalid",
        ),
        (
            "{request: {method: GET, endpoint: /, headers: {x: '${env.MISSING}'}}}",
            "'https://api.test'",
            "",
            "unavailable",
        ),
    ],
)
def test_unsupported_or_malformed_authored_shapes_fail_explicitly(
    request_config: str,
    base_url: str,
    project_fields: str,
    expected: str,
) -> None:
    result = asyncio.run(
        _prepare(
            _prepared(
                request_config,
                base_url=base_url,
                project_fields=project_fields,
            )
        )
    )
    assert type(result) is RequestPreparationFailure
    outcome = cast(UnavailableExecution, result.outcome)
    assert outcome.code.value == f"runtime.request.{expected}"


def test_provider_rejects_invalid_context_calls_and_non_request_subjects() -> None:
    prepared = _prepared("{request: {method: GET, endpoint: /}}")
    context = _context(prepared)
    with pytest.raises(TypeError, match="PreparedRun and RunContext"):
        ConfigurationRequestProvider(object(), context)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="PreparedRun and RunContext"):
        ConfigurationRequestProvider(prepared, object())  # type: ignore[arg-type]
    object.__setattr__(context, "_run", RunScope(run_id=RunId(value=UUID(int=902))))
    with pytest.raises(ValueError, match="another run"):
        ConfigurationRequestProvider(prepared, context)
    asyncio.run(context.close())

    scenario_context = _context(prepared)
    provider = ConfigurationRequestProvider(prepared, scenario_context)
    work = next(iter_execution_work(prepared))
    with pytest.raises(TypeError, match="ExecutionWork and AttemptScope"):
        asyncio.run(provider.prepare(object(), _attempt_scope(work, 0)))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ExecutionWork and AttemptScope"):
        asyncio.run(provider.prepare(work, object()))  # type: ignore[arg-type]
    msgspec.structs.force_setattr(
        work,
        "subject",
        definition(ResourceKind.SCENARIO, "flow"),
    )
    result = asyncio.run(provider.prepare(work, _attempt_scope(work, 0)))
    assert type(result) is RequestPreparationFailure
    asyncio.run(provider.close())


def test_resolution_failures_map_to_value_free_execution_outcomes() -> None:
    unavailable = Unavailable(
        reason=UnavailableReason.NOT_FOUND,
        provenance=_resolution_provenance(ResolutionStepOutcome.UNAVAILABLE),
    )
    missing = Missing(provenance=_resolution_provenance(ResolutionStepOutcome.MISSING))
    failed = Error(
        code=ResolutionErrorCode.PROVIDER_FAILED,
        provenance=_resolution_provenance(ResolutionStepOutcome.ERROR),
    )
    cancelled = Error(
        code=ResolutionErrorCode.CANCELLED,
        provenance=_resolution_provenance(ResolutionStepOutcome.ERROR),
    )
    blocked = Blocked(
        code=ResolutionBlockCode.SOURCE_DENIED,
        provenance=_resolution_provenance(ResolutionStepOutcome.BLOCKED),
    )

    for result in (unavailable, missing):
        mapped = preparation._resolution_failure(result)
        assert mapped is not None
        assert type(mapped.outcome) is UnavailableExecution
        assert mapped.outcome.code.value == "runtime.request.unavailable"
    mapped_failure = preparation._resolution_failure(failed)
    assert mapped_failure is not None
    assert type(mapped_failure.outcome) is UnavailableExecution
    assert mapped_failure.outcome.code.value == "runtime.request.resolution-error"
    mapped_cancelled = preparation._resolution_failure(cancelled)
    assert mapped_cancelled is not None
    assert mapped_cancelled.outcome == CancelledExecution(source=CancellationSource.PARENT_SCOPE)
    mapped_blocked = preparation._resolution_failure(blocked)
    assert mapped_blocked is not None
    assert type(mapped_blocked.outcome) is BlockedExecution
    assert preparation._resolution_failure(object()) is None
    assert type(preparation._resolved_object(object())) is RequestPreparationFailure


def test_sensitive_wire_object_is_revealed_only_inside_materialization_boundary(
    prepared_comparison_run: PreparedRun,
) -> None:
    async def scenario() -> tuple[JsonObject, tuple[TaintOriginId, ...]]:
        context = _context(prepared_comparison_run)
        work = next(iter_execution_work(prepared_comparison_run))
        attempt = _attempt_scope(work, 0)
        payload = JsonObject(
            members=(JsonObjectMember(name="method", value=JsonString(value="GET")),)
        )
        expression = SensitiveLiteralExpr(
            value=SensitiveValue(
                payload,
                origins=(TaintOriginId(value="test/request-secret"),),
            ),
            provenance=_configuration_provenance(),
        )
        result = await context.resolve_for_wire(
            expression,
            ResolutionTarget(
                symbol=SymbolPath(segments=("request",)),
                scope=attempt,
                cell_inputs=cell_inputs(prepared_comparison_run, work.trial.cell),
            ),
        )
        assert type(result) is ResolvedForWire
        resolved = preparation._resolved_object(result)
        assert type(resolved) is tuple
        text_result = await context.resolve_for_wire(
            SensitiveLiteralExpr(
                value=SensitiveValue(
                    JsonString(value="not-object"),
                    origins=(TaintOriginId(value="test/request-text-secret"),),
                ),
                provenance=_configuration_provenance(),
            ),
            ResolutionTarget(
                symbol=SymbolPath(segments=("requestText",)),
                scope=attempt,
                cell_inputs=cell_inputs(prepared_comparison_run, work.trial.cell),
            ),
        )
        assert type(text_result) is ResolvedForWire
        assert type(preparation._resolved_object(text_result)) is RequestPreparationFailure
        public_text_result = await context.resolve_for_wire(
            LiteralExpr(
                value=JsonString(value="not-object"),
                provenance=_configuration_provenance(),
            ),
            ResolutionTarget(
                symbol=SymbolPath(segments=("requestPublicText",)),
                scope=attempt,
                cell_inputs=cell_inputs(prepared_comparison_run, work.trial.cell),
            ),
        )
        assert type(public_text_result) is ResolvedForWire
        assert type(preparation._resolved_object(public_text_result)) is RequestPreparationFailure
        await context.close()
        return resolved

    payload, origins = asyncio.run(scenario())
    assert payload.members[0].value == JsonString(value="GET")
    assert origins == (TaintOriginId(value="test/request-secret"),)


def test_url_and_header_helpers_cover_root_ipv6_and_invalid_shapes() -> None:
    provenance = _configuration_provenance()
    empty = JsonObject(members=())
    assert preparation._path("/", ()) == UrlPath()
    assert preparation._host("2001:db8::1").value == "2001:db8::1"
    assert preparation._host("Example.TEST").value == "example.test"
    assert preparation._join_url("https://api.test/", "/v1") == "https://api.test/v1"
    with pytest.raises(ValueError, match="absolute HTTP"):
        preparation._absolute_url("relative", empty, provenance, ())
    with pytest.raises(ValueError, match="user information"):
        preparation._absolute_url("https://u@api.test", empty, provenance, ())
    with pytest.raises(TypeError, match="headers must be an object"):
        preparation._headers(
            JsonObject(members=(JsonObjectMember(name="headers", value=JsonString(value="x")),)),
            provenance,
            (),
        )
    with pytest.raises(TypeError, match="header values"):
        preparation._headers(
            JsonObject(
                members=(
                    JsonObjectMember(
                        name="headers",
                        value=JsonObject(
                            members=(JsonObjectMember(name="x", value=JsonInteger(value=1)),)
                        ),
                    ),
                )
            ),
            provenance,
            (),
        )
