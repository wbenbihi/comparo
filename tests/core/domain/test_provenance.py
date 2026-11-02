"""Tests for value-free provenance chains."""

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import CellId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.identity import SourceId
from comparo.core.domain.identity import StepExecutionId
from comparo.core.domain.identity import TrialId
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ResolutionProvenance
from comparo.core.domain.provenance import ResolutionSource
from comparo.core.domain.provenance import ResolutionStep
from comparo.core.domain.provenance import ResolutionStepOutcome
from comparo.core.domain.provenance import SourceLocation
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.provenance import ValueProviderIdentity
from comparo.core.domain.scopes import AttemptScope
from comparo.core.domain.scopes import CellScope
from comparo.core.domain.scopes import EnvironmentScope
from comparo.core.domain.scopes import ExecutionScope
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import RuntimeScope
from comparo.core.domain.scopes import StepScope
from comparo.core.domain.scopes import TrialScope
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.value_references import namespace_supports_declared_scope
from comparo.core.domain.value_references import namespace_supports_runtime_scope
from comparo.core.domain.value_references import runtime_value_scope_kind

_UUIDS = tuple(UUID(int=value) for value in range(1, 7))


def _location() -> SourceLocation:
    return SourceLocation(
        source_id=SourceId(value="project-config"),
        document=PositiveCount(value=1),
        line=PositiveCount(value=2),
        column=PositiveCount(value=3),
    )


def _runtime_scopes() -> tuple[
    RunScope,
    CellScope,
    TrialScope,
    EnvironmentScope,
    ExecutionScope,
    StepScope,
    AttemptScope,
]:
    run = RunScope(run_id=RunId(value=_UUIDS[0]))
    cell = CellScope(run=run, cell_id=CellId(value=_UUIDS[1]))
    trial = TrialScope(cell=cell, trial_id=TrialId(value=_UUIDS[2]))
    environment = EnvironmentScope(
        run=run,
        environment=ResourceIdentity(
            namespace=NamespaceId(value="local"),
            kind=ResourceKind.ENVIRONMENT,
            resource_id=ResourceId(value="staging"),
        ),
    )
    execution = ExecutionScope(
        trial=trial,
        environment=environment,
        execution_id=EnvironmentExecutionId(value=_UUIDS[3]),
    )
    step = StepScope(execution=execution, step_execution_id=StepExecutionId(value=_UUIDS[4]))
    attempt = AttemptScope(execution=execution, attempt_id=AttemptId(value=_UUIDS[5]))
    return run, cell, trial, environment, execution, step, attempt


def _provider(name: str = "environment") -> ValueProviderIdentity:
    return ValueProviderIdentity(namespace="comparo.core", name=name, version="1")


def _configuration(kind: ProvenanceKind = ProvenanceKind.PROJECT) -> ValueProvenance:
    return ValueProvenance(entries=(ProvenanceEntry(kind=kind),))


def _source(
    *,
    provider: ValueProviderIdentity | None = None,
    namespace: ValueNamespace | None = ValueNamespace.VARIABLES,
    symbol: str = "api-token",
    scope: RuntimeScope | None = None,
) -> ResolutionSource:
    return ResolutionSource(
        namespace=namespace,
        symbol=SymbolPath(segments=(symbol,)),
        provider=provider or _provider(),
        configuration=_configuration(),
        scope=scope or _runtime_scopes()[0],
    )


def _resolution(
    *,
    scope: RuntimeScope,
    namespace: ValueNamespace | None = ValueNamespace.VARIABLES,
    outcome: ResolutionStepOutcome = ResolutionStepOutcome.PRESENT,
) -> ResolutionProvenance:
    source = _source(namespace=namespace, scope=scope)
    return ResolutionProvenance(
        namespace=namespace,
        symbol=source.symbol,
        provider=source.provider,
        scope=scope,
        steps=(ResolutionStep(source=source, outcome=outcome),),
    )


def test_source_location_requires_safe_identifier_and_typed_coordinates() -> None:
    assert _location().line.value == 2
    with pytest.raises(ValueError, match="lowercase alphanumeric slug"):
        SourceId(value="/Users/operator/project/comparo.yaml")
    with pytest.raises(TypeError, match="logical SourceId"):
        SourceLocation(
            source_id=cast("SourceId", "project-config"),
            document=PositiveCount(value=1),
            line=PositiveCount(value=1),
            column=PositiveCount(value=1),
        )
    with pytest.raises(TypeError):
        SourceLocation(
            source_id=SourceId(value="project-config"),
            document=1,  # type: ignore[arg-type]
            line=PositiveCount(value=1),
            column=PositiveCount(value=1),
        )


def test_provenance_entry_validates_typed_optional_components() -> None:
    resource = ResourceIdentity(
        namespace=NamespaceId(value="local"),
        kind=ResourceKind.PROJECT,
        resource_id=ResourceId(value="api"),
    )
    entry = ProvenanceEntry(
        kind=ProvenanceKind.PROJECT,
        resource=resource,
        location=_location(),
    )
    assert entry.resource == resource
    with pytest.raises(TypeError):
        ProvenanceEntry(kind="project")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ProvenanceEntry(kind=ProvenanceKind.PROJECT, resource="Project/api")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ProvenanceEntry(kind=ProvenanceKind.PROJECT, location="line 2")  # type: ignore[arg-type]


def test_value_provenance_is_non_empty_typed_and_immutable() -> None:
    entry = ProvenanceEntry(kind=ProvenanceKind.VERSIONED_DEFAULT)
    assert ValueProvenance(entries=(entry,)).entries == (entry,)
    with pytest.raises(ValueError, match="at least one"):
        ValueProvenance(entries=())
    with pytest.raises(TypeError):
        ValueProvenance(entries=[entry])  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ValueProvenance(entries=("default",))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("namespace", "Bad Provider", "lowercase namespaced token"),
        ("name", "", "lowercase namespaced token"),
        ("version", 1, "ASCII decimal version"),
        ("version", "v1", "ASCII decimal version"),
        ("version", "\N{ARABIC-INDIC DIGIT ONE}", "ASCII decimal version"),
        ("version", "0", "must be positive"),
        ("version", "01", "canonical decimal notation"),
    ],
)
def test_value_provider_identity_is_stable_and_versioned(
    field: str,
    value: object,
    message: str,
) -> None:
    fields: dict[str, object] = {
        "namespace": "comparo.core",
        "name": "dotenv",
        "version": "1",
    }
    fields[field] = value
    with pytest.raises(ValueError, match=message):
        ValueProviderIdentity(**fields)  # type: ignore[arg-type]


def test_resolution_provenance_preserves_order_without_resolved_values() -> None:
    secret = "raw-secret-must-not-appear"
    first = _source(provider=_provider("dotenv"), symbol="primary-token")
    second = _source(
        provider=_provider("ambient-environment"),
        symbol="fallback-token",
    )
    provenance = ResolutionProvenance(
        namespace=ValueNamespace.VARIABLES,
        symbol=SymbolPath(segments=("api-token",)),
        provider=second.provider,
        scope=_runtime_scopes()[0],
        steps=(
            ResolutionStep(source=first, outcome=ResolutionStepOutcome.UNAVAILABLE),
            ResolutionStep(source=second, outcome=ResolutionStepOutcome.PRESENT),
        ),
    )

    assert provenance.terminal_outcome is ResolutionStepOutcome.PRESENT
    assert tuple(step.source.symbol for step in provenance.steps) == (
        first.symbol,
        second.symbol,
    )
    built = msgspec.to_builtins(provenance)
    assert secret not in repr(built)
    assert "value" not in cast("dict[str, object]", built)
    with pytest.raises(AttributeError):
        provenance.steps = ()  # type: ignore[misc]


@pytest.mark.parametrize(
    ("scope_index", "expected"),
    [
        (0, ValueScopeKind.RUN),
        (1, ValueScopeKind.CELL),
        (2, ValueScopeKind.TRIAL),
        (3, ValueScopeKind.ENVIRONMENT),
        (4, ValueScopeKind.EXECUTION),
        (5, ValueScopeKind.STEP),
        (6, ValueScopeKind.ATTEMPT),
    ],
)
def test_resolution_accepts_every_concrete_scope(
    scope_index: int,
    expected: ValueScopeKind,
) -> None:
    scope = _runtime_scopes()[scope_index]
    provenance = _resolution(scope=scope)

    assert provenance.scope == scope
    assert runtime_value_scope_kind(scope) is expected
    assert namespace_supports_runtime_scope(ValueNamespace.VARIABLES, scope)


@pytest.mark.parametrize(
    ("namespace", "scope_index"),
    [
        (ValueNamespace.ENV, 3),
        (ValueNamespace.PARAMETERS, 1),
        (ValueNamespace.PARAMETERS, 5),
        (ValueNamespace.MATRIX, 1),
        (ValueNamespace.STEPS, 5),
        (ValueNamespace.SECRETS, 6),
    ],
)
def test_resolution_namespace_accepts_only_declared_concrete_scopes(
    namespace: ValueNamespace,
    scope_index: int,
) -> None:
    assert (
        _resolution(scope=_runtime_scopes()[scope_index], namespace=namespace).namespace
        is namespace
    )


def test_resolution_rejects_namespace_scope_mismatches() -> None:
    run = _runtime_scopes()[0]
    source = _source(namespace=None, scope=run)
    with pytest.raises(ValueError, match="namespace cannot reach"):
        ResolutionProvenance(
            namespace=ValueNamespace.ENV,
            symbol=SymbolPath(segments=("anonymous",)),
            provider=source.provider,
            scope=run,
            steps=(ResolutionStep(source=source, outcome=ResolutionStepOutcome.PRESENT),),
        )
    with pytest.raises(ValueError, match="source namespace is unavailable"):
        _source(namespace=ValueNamespace.ENV, scope=run)


def test_independent_matrix_and_environment_sources_compose_at_execution_scope() -> None:
    _, cell, _, environment, execution, _, _ = _runtime_scopes()
    matrix = _source(
        namespace=ValueNamespace.MATRIX,
        symbol="matrix-input",
        provider=_provider("matrix"),
        scope=cell,
    )
    env = _source(
        namespace=ValueNamespace.ENV,
        symbol="environment-fallback",
        provider=_provider("environment"),
        scope=environment,
    )
    provenance = ResolutionProvenance(
        namespace=None,
        symbol=SymbolPath(segments=("request-input",)),
        provider=env.provider,
        scope=execution,
        steps=(
            ResolutionStep(source=matrix, outcome=ResolutionStepOutcome.UNAVAILABLE),
            ResolutionStep(source=env, outcome=ResolutionStepOutcome.PRESENT),
        ),
    )

    assert tuple(step.source.scope for step in provenance.steps) == (cell, environment)


def test_logical_namespace_may_reach_a_downstream_consumer_scope() -> None:
    _, cell, _, _, execution, _, _ = _runtime_scopes()
    source = _source(namespace=ValueNamespace.MATRIX, scope=cell)
    provenance = ResolutionProvenance(
        namespace=ValueNamespace.MATRIX,
        symbol=source.symbol,
        provider=source.provider,
        scope=execution,
        steps=(ResolutionStep(source=source, outcome=ResolutionStepOutcome.PRESENT),),
    )

    assert provenance.scope == execution


def test_step_namespace_reaches_an_attempt_owned_by_the_same_step() -> None:
    _, _, _, _, execution, step, _ = _runtime_scopes()
    attempt = AttemptScope(
        execution=execution,
        attempt_id=AttemptId(value=UUID(int=101)),
        step_execution_id=step.step_execution_id,
    )
    source = _source(namespace=ValueNamespace.STEPS, scope=step)
    provenance = ResolutionProvenance(
        namespace=ValueNamespace.STEPS,
        symbol=source.symbol,
        provider=source.provider,
        scope=attempt,
        steps=(ResolutionStep(source=source, outcome=ResolutionStepOutcome.PRESENT),),
    )

    assert provenance.scope == attempt


def test_resolution_rejects_unrelated_source_coordinates() -> None:
    run, _, _, _, execution, _, _ = _runtime_scopes()
    wrong_cell = CellScope(run=run, cell_id=CellId(value=UUID(int=100)))
    wrong_environment = EnvironmentScope(
        run=run,
        environment=ResourceIdentity(
            namespace=NamespaceId(value="local"),
            kind=ResourceKind.ENVIRONMENT,
            resource_id=ResourceId(value="production"),
        ),
    )
    for source in (
        _source(namespace=ValueNamespace.MATRIX, scope=wrong_cell),
        _source(namespace=ValueNamespace.ENV, scope=wrong_environment),
        _source(
            namespace=ValueNamespace.VARIABLES,
            scope=RunScope(run_id=RunId(value=UUID(int=99))),
        ),
    ):
        with pytest.raises(ValueError, match="source scope cannot supply"):
            ResolutionProvenance(
                symbol=SymbolPath(segments=("request-input",)),
                provider=source.provider,
                scope=execution,
                steps=(ResolutionStep(source=source, outcome=ResolutionStepOutcome.PRESENT),),
            )


def test_namespace_scope_helpers_reject_runtime_type_erasure() -> None:
    with pytest.raises(TypeError, match="ValueNamespace"):
        namespace_supports_declared_scope("env", ValueScopeKind.ENVIRONMENT)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ValueScopeKind"):
        namespace_supports_declared_scope(ValueNamespace.ENV, "environment")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="exact RuntimeScope"):
        runtime_value_scope_kind(cast("RuntimeScope", object()))


def test_resolution_components_require_exact_immutable_types() -> None:
    source = _source()
    step = ResolutionStep(source=source, outcome=ResolutionStepOutcome.PRESENT)
    run = _runtime_scopes()[0]

    with pytest.raises(TypeError, match="source symbol"):
        ResolutionSource(
            symbol=cast("SymbolPath", "value"),
            provider=source.provider,
            configuration=source.configuration,
            scope=source.scope,
        )
    with pytest.raises(TypeError, match="source provider"):
        ResolutionSource(
            symbol=source.symbol,
            provider=cast("ValueProviderIdentity", object()),
            configuration=source.configuration,
            scope=source.scope,
        )
    with pytest.raises(TypeError, match="source configuration"):
        ResolutionSource(
            symbol=source.symbol,
            provider=source.provider,
            configuration=cast("ValueProvenance", object()),
            scope=source.scope,
        )
    with pytest.raises(TypeError, match="source namespace"):
        ResolutionSource(
            namespace=cast("ValueNamespace", "variables"),
            symbol=source.symbol,
            provider=source.provider,
            configuration=source.configuration,
            scope=source.scope,
        )
    with pytest.raises(TypeError, match="exact RuntimeScope"):
        ResolutionSource(
            symbol=source.symbol,
            provider=source.provider,
            configuration=source.configuration,
            scope=cast("RuntimeScope", object()),
        )
    with pytest.raises(TypeError, match="step source"):
        ResolutionStep(
            source=cast("ResolutionSource", object()),
            outcome=ResolutionStepOutcome.PRESENT,
        )
    with pytest.raises(TypeError, match="step outcome"):
        ResolutionStep(source=source, outcome=cast("ResolutionStepOutcome", "present"))
    with pytest.raises(TypeError, match="resolution symbol"):
        ResolutionProvenance(
            symbol=cast("SymbolPath", "value"),
            provider=source.provider,
            scope=run,
            steps=(step,),
        )
    with pytest.raises(TypeError, match="resolution provider"):
        ResolutionProvenance(
            symbol=source.symbol,
            provider=cast("ValueProviderIdentity", object()),
            scope=run,
            steps=(step,),
        )
    with pytest.raises(TypeError, match="resolution namespace"):
        ResolutionProvenance(
            namespace=cast("ValueNamespace", "variables"),
            symbol=source.symbol,
            provider=source.provider,
            scope=run,
            steps=(step,),
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        ResolutionProvenance(
            symbol=source.symbol,
            provider=source.provider,
            scope=run,
            steps=[step],  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="at least one step"):
        ResolutionProvenance(
            symbol=source.symbol,
            provider=source.provider,
            scope=run,
            steps=(),
        )
    with pytest.raises(TypeError, match="ResolutionStep values"):
        ResolutionProvenance(
            symbol=source.symbol,
            provider=source.provider,
            scope=run,
            steps=(cast("ResolutionStep", object()),),
        )


def test_resolution_terminal_provider_must_match_declared_provider() -> None:
    source = _source(provider=_provider("dotenv"))
    with pytest.raises(ValueError, match="terminal source provider"):
        ResolutionProvenance(
            symbol=source.symbol,
            provider=_provider("ambient-environment"),
            scope=_runtime_scopes()[0],
            steps=(ResolutionStep(source=source, outcome=ResolutionStepOutcome.PRESENT),),
        )


@pytest.mark.parametrize(
    "nonterminal",
    [
        ResolutionStepOutcome.PRESENT,
        ResolutionStepOutcome.MISSING,
        ResolutionStepOutcome.ERROR,
        ResolutionStepOutcome.BLOCKED,
    ],
)
def test_resolution_chain_rejects_terminal_outcomes_before_the_last_step(
    nonterminal: ResolutionStepOutcome,
) -> None:
    first = _source(provider=_provider("dotenv"), symbol="primary")
    second = _source(provider=_provider("ambient-environment"), symbol="fallback")
    with pytest.raises(ValueError, match="nonterminal resolution steps must be unavailable"):
        ResolutionProvenance(
            symbol=SymbolPath(segments=("api-token",)),
            provider=second.provider,
            scope=_runtime_scopes()[0],
            steps=(
                ResolutionStep(source=first, outcome=nonterminal),
                ResolutionStep(source=second, outcome=ResolutionStepOutcome.PRESENT),
            ),
        )
