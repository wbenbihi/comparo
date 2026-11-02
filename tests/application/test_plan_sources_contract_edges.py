"""Exhaustive dependency and mechanism projection coverage for admitted Plans."""

from __future__ import annotations

from collections import deque
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.application import plan_sources as sources
from comparo.application.plan_sources import SensitiveSourceInspection
from comparo.core.configuration.canonical import CanonicalArray
from comparo.core.configuration.canonical import CanonicalMember
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import InlineResource
from comparo.core.domain.expression_types import ConversionType
from comparo.core.domain.expression_types import FileTrim
from comparo.core.domain.expression_types import InterpolationMode
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import ConvertExpr
from comparo.core.domain.expressions import ExpressionDependency
from comparo.core.domain.expressions import ExpressionSegment
from comparo.core.domain.expressions import FallbackExpr
from comparo.core.domain.expressions import FileExpr
from comparo.core.domain.expressions import InterpolationExpr
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import LiteralTextSegment
from comparo.core.domain.expressions import LookupExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ObjectExpressionMember
from comparo.core.domain.expressions import OptionalExpr
from comparo.core.domain.expressions import SensitiveLiteralExpr
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.identity import InlineDefinitionOrigin
from comparo.core.domain.identity import InlineDefinitionRef
from comparo.core.domain.identity import InlineFragment
from comparo.core.domain.identity import InvocationId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.values import JsonString
from comparo.core.planning.models import PlanningSource
from comparo.core.planning.models import PreparedRun
from comparo.core.planning.models import ValueBindingLayer
from tests.application.test_inspection import _prepared
from tests.application.test_planning import _literal
from tests.application.test_planning_contract_edges import _lookup
from tests.application.test_planning_contract_edges import _sealed
from tests.application.test_project_outline import _fingerprint
from tests.application.test_project_outline import _identity


def _expressions() -> tuple[
    LookupExpr,
    FileExpr,
    FallbackExpr,
    OptionalExpr,
    ConvertExpr,
    ArrayExpr,
    ObjectExpr,
    InterpolationExpr,
    LiteralExpr,
    SensitiveLiteralExpr,
]:
    provenance = _literal("value").provenance
    lookup = _lookup()
    file_expression = FileExpr(
        path=_literal("secret.txt"),
        conversion=ConversionType.STRING,
        trim=FileTrim.NONE,
        maximum_bytes=ByteCount(value=128),
        provenance=provenance,
    )
    fallback = FallbackExpr(sources=(lookup, file_expression), provenance=provenance)
    optional = OptionalExpr(source=lookup, provenance=provenance)
    converted = ConvertExpr(
        source=lookup,
        target=ConversionType.STRING,
        provenance=provenance,
    )
    array = ArrayExpr(items=(lookup,), provenance=provenance)
    object_expression = ObjectExpr(
        members=(ObjectExpressionMember(name="token", expression=lookup),),
        provenance=provenance,
    )
    interpolation = InterpolationExpr(
        mode=InterpolationMode.EMBEDDED_TEXT,
        segments=(LiteralTextSegment(value="Bearer "), ExpressionSegment(expression=lookup)),
        provenance=provenance,
    )
    return (
        lookup,
        file_expression,
        fallback,
        optional,
        converted,
        array,
        object_expression,
        interpolation,
        _literal("literal"),
        _sealed(),
    )


def _inline_resource(expression: ValueExpression) -> InlineResource:
    owner = _identity(ResourceKind.REQUEST, "ping")
    return InlineResource(
        definition=InlineDefinitionRef(
            kind=ResourceKind.SAFETY_PROFILE,
            origin=InlineDefinitionOrigin(
                owner=owner,
                fragment=InlineFragment(value="#safety"),
            ),
            fingerprint=_fingerprint(),
        ),
        spec=CanonicalObject(members=(CanonicalMember(name="value", value=expression),)),
    )


def test_sensitive_source_inspection_rejects_untyped_prepared_run() -> None:
    with pytest.raises(TypeError, match="PreparedRun"):
        sources.inspect_sensitive_sources(cast("PreparedRun", object()))


def test_sensitive_source_scan_deduplicates_visited_and_retained_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared()
    environment = prepared.effective.environments[0]
    dependency = ExpressionDependency(
        namespace=ValueNamespace.ENV,
        symbol=SymbolPath(segments=("TOKEN",)),
        available_at=ValueScopeKind.ENVIRONMENT,
    )
    expected = SensitiveSourceInspection(
        name="TOKEN",
        source="Environment:local/staging",
        environment_namespace="local",
        environment_id="staging",
        mechanisms=("$env:TOKEN",),
    )
    monkeypatch.setattr(
        sources,
        "_dependency_queue",
        lambda _prepared: deque(((dependency, environment), (dependency, environment))),
    )
    monkeypatch.setattr(
        sources,
        "_trace_dependency",
        lambda *_args: (expected, ()),
    )
    assert sources.inspect_sensitive_sources(prepared) == (expected,)

    monkeypatch.setattr(
        sources,
        "_trace_dependency",
        lambda *_args: (None, ()),
    )
    assert sources.inspect_sensitive_sources(prepared) == ()


def test_dependency_queue_skips_missing_selected_definitions() -> None:
    prepared = _prepared()
    missing = msgspec.structs.replace(
        prepared.effective.environments[0],
        fingerprint=_fingerprint(),
    )
    effective = msgspec.structs.replace(
        prepared.effective,
        environments=(missing,),
    )
    object.__setattr__(prepared, "effective", effective)
    queue = sources._dependency_queue(prepared)
    assert queue
    assert all(item[1] == missing for item in queue)

    prepared = _prepared()
    missing_subject_ref = msgspec.structs.replace(
        prepared.effective.subjects[0].definition,
        fingerprint=_fingerprint(),
    )
    missing_subject = msgspec.structs.replace(
        prepared.effective.subjects[0],
        definition=missing_subject_ref,
    )
    object.__setattr__(
        prepared,
        "effective",
        msgspec.structs.replace(prepared.effective, subjects=(missing_subject,)),
    )
    sources._dependency_queue(prepared)


def test_definition_index_and_root_dependency_dispatch_cover_every_container() -> None:
    prepared = _prepared()
    definitions = sources._definition_specs(prepared)
    assert definitions

    inline_prepared = _prepared()
    inline = _inline_resource(_lookup())
    resource = inline_prepared.project.resources[0]
    object.__setattr__(
        resource,
        "spec",
        CanonicalObject(members=(CanonicalMember(name="inline", value=inline),)),
    )
    assert any(
        type(key) is InlineDefinitionRef for key in sources._definition_specs(inline_prepared)
    )
    object.__setattr__(resource, "spec", JsonString(value="non-object"))
    sources._definition_specs(inline_prepared)

    lookup = _lookup()
    inline = _inline_resource(lookup)
    canonical = CanonicalObject(
        members=(
            CanonicalMember(name="parameters", value=lookup),
            CanonicalMember(
                name="nested",
                value=CanonicalArray(items=(inline, lookup)),
            ),
        )
    )
    dependencies = sources._root_dependencies(canonical)
    assert dependencies == (lookup.dependency, lookup.dependency)
    assert sources._root_dependencies(inline) == (lookup.dependency,)
    assert sources._root_dependencies(JsonString(value="plain")) == ()


def test_recursive_dependencies_dispatch_every_canonical_shape() -> None:
    lookup = _lookup()
    inline = _inline_resource(lookup)
    canonical = CanonicalObject(
        members=(
            CanonicalMember(
                name="array",
                value=CanonicalArray(items=(lookup, JsonString(value="plain"))),
            ),
            CanonicalMember(name="inline", value=inline),
        )
    )
    assert sources._dependencies(lookup) == (lookup.dependency,)
    assert sources._dependencies(canonical) == (lookup.dependency, lookup.dependency)
    assert sources._dependencies(inline) == (lookup.dependency,)
    assert sources._dependencies(JsonString(value="plain")) == ()


def test_trace_dependency_covers_environment_missing_nonsecret_and_secret_bindings() -> None:
    prepared = _prepared()
    environment = prepared.effective.environments[0]
    env_dependency = _lookup().dependency
    inspected, dependencies = sources._trace_dependency(
        sources._BindingIndex(global_bindings={}, environment_bindings={}),
        env_dependency,
        environment,
    )
    assert inspected is not None
    assert dependencies == ()

    variable_dependency = ExpressionDependency(
        namespace=ValueNamespace.VARIABLES,
        symbol=SymbolPath(segments=("TOKEN",)),
        available_at=ValueScopeKind.RUN,
    )
    empty = sources._BindingIndex(global_bindings={}, environment_bindings={})
    assert sources._trace_dependency(empty, variable_dependency, environment) == (None, ())

    default_source = PlanningSource(kind=ProvenanceKind.VERSIONED_DEFAULT)
    lookup = _lookup()
    variables = ObjectExpr(
        members=(ObjectExpressionMember(name="TOKEN", expression=lookup),),
        provenance=lookup.provenance,
    )
    variable_index = sources._binding_index(
        (ValueBindingLayer(source=default_source, variables=variables),)
    )
    inspected, dependencies = sources._trace_dependency(
        variable_index,
        variable_dependency,
        environment,
    )
    assert inspected is None
    assert dependencies == (lookup.dependency,)

    secret_dependency = ExpressionDependency(
        namespace=ValueNamespace.SECRETS,
        symbol=SymbolPath(segments=("TOKEN",)),
        available_at=ValueScopeKind.RUN,
    )
    secrets = ObjectExpr(
        members=(ObjectExpressionMember(name="TOKEN", expression=lookup),),
        provenance=lookup.provenance,
    )
    secret_index = sources._binding_index(
        (ValueBindingLayer(source=default_source, secrets=secrets),)
    )
    inspected, dependencies = sources._trace_dependency(
        secret_index,
        secret_dependency,
        environment,
    )
    assert inspected is not None
    assert inspected.mechanisms == ("$env:TOKEN",)
    assert dependencies == (lookup.dependency,)


def test_mechanism_projection_covers_every_expression_shape() -> None:
    expressions = _expressions()
    (
        lookup,
        file_expression,
        fallback,
        optional,
        converted,
        array,
        object_expression,
        interpolation,
        literal,
        sealed,
    ) = expressions
    assert sources._expression_mechanisms(lookup) == ("$env:TOKEN",)
    assert sources._expression_mechanisms(file_expression) == ("$file",)
    assert sources._expression_mechanisms(fallback) == ("$env:TOKEN", "$file")
    assert sources._expression_mechanisms(optional) == ("$env:TOKEN",)
    assert sources._expression_mechanisms(converted) == ("$env:TOKEN",)
    assert sources._expression_mechanisms(array) == ("$env:TOKEN",)
    assert sources._expression_mechanisms(object_expression) == ("$env:TOKEN",)
    assert sources._expression_mechanisms(interpolation) == ("$env:TOKEN",)
    assert sources._expression_mechanisms(literal) == ()
    assert sources._expression_mechanisms(sealed) == ()
    assert sources._mechanisms(literal) == ("sealed inline literal",)
    with pytest.raises(TypeError, match="unsupported"):
        sources._expression_mechanisms(cast("ValueExpression", object()))


def test_planning_source_projection_covers_named_inline_invocation_and_default() -> None:
    prepared = _prepared()
    named = PlanningSource(
        kind=ProvenanceKind.PROJECT,
        definition=prepared.project.project.definition,
    )
    assert sources._planning_source(named).startswith("project:local/Project/demo@")

    inline = _inline_resource(_lookup()).definition
    inline_source = PlanningSource(kind=ProvenanceKind.PROFILE, definition=inline)
    projected = sources._planning_source(inline_source)
    assert "#safety@" in projected

    invocation_source = PlanningSource(
        kind=ProvenanceKind.INVOCATION,
        invocation_id=InvocationId(value=UUID(int=42)),
    )
    assert sources._planning_source(invocation_source).startswith("invocation:")
    default = PlanningSource(kind=ProvenanceKind.VERSIONED_DEFAULT)
    assert sources._planning_source(default) == "versioned-default"


def test_binding_index_prefers_latest_environment_specific_layer() -> None:
    prepared = _prepared()
    environment = prepared.effective.environments[0]
    source = PlanningSource(kind=ProvenanceKind.VERSIONED_DEFAULT)
    global_layer = ValueBindingLayer(source=source, variables=_object_expression("global"))
    environment_layer = ValueBindingLayer(
        source=source,
        environment=environment,
        variables=_object_expression("environment"),
    )
    index = sources._binding_index((global_layer, environment_layer))
    found = index.find(ValueNamespace.VARIABLES, "TOKEN", environment)
    assert found is not None
    assert type(environment_layer.variables) is ObjectExpr
    assert found[1] == environment_layer.variables.members[0].expression


def _object_expression(value: str) -> ObjectExpr:
    literal = _literal(value)
    return ObjectExpr(
        members=(ObjectExpressionMember(name="TOKEN", expression=literal),),
        provenance=literal.provenance,
    )
