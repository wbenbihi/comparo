"""Closed edge contracts for frontend-neutral invocation construction."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.application.planning import ClearProfileSelection
from comparo.application.planning import EnvironmentValueOverride
from comparo.application.planning import InlineValueOverride
from comparo.application.planning import InvocationBuildError
from comparo.application.planning import InvocationBuildErrorCode
from comparo.application.planning import MatrixOverride
from comparo.application.planning import PlanningOptions
from comparo.application.planning import ProfileSelectionOverrides
from comparo.application.planning import RequestOverride
from comparo.application.planning import ValueResourceOverride
from comparo.application.planning import _profile_collection
from comparo.application.planning import _profile_scalar
from comparo.application.planning import _sealed_secret_expression
from comparo.application.planning import _value_overlays
from comparo.application.planning import environment_value_override
from comparo.application.planning import inline_value_override
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
from comparo.core.domain.overrides import Clear
from comparo.core.domain.overrides import ClearBase
from comparo.core.domain.overrides import SetValue
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.values import JsonString
from tests.application.test_planning import _literal
from tests.application.test_planning import _object
from tests.core.planning.conftest import compile_project
from tests.core.planning.conftest import document


def _lookup(namespace: ValueNamespace = ValueNamespace.ENV) -> LookupExpr:
    return LookupExpr(
        dependency=ExpressionDependency(
            namespace=namespace,
            symbol=SymbolPath(segments=("TOKEN",)),
            available_at=(
                ValueScopeKind.ENVIRONMENT
                if namespace is ValueNamespace.ENV
                else ValueScopeKind.RUN
            ),
        ),
        provenance=_literal("value").provenance,
    )


def _sealed() -> SensitiveLiteralExpr:
    secured = inline_value_override(ValueNamespace.SECRETS, _object(token="secret"))
    expression = secured.expression.members[0].expression
    assert type(expression) is SensitiveLiteralExpr
    return expression


def test_matrix_override_rejects_untyped_and_duplicate_state() -> None:
    with pytest.raises(TypeError, match="identities"):
        MatrixOverride(binding=cast("str", object()), matrix="regions")
    with pytest.raises(TypeError, match="cases"):
        MatrixOverride(binding="region", matrix="regions", cases=cast("tuple[str, ...]", []))
    with pytest.raises(InvocationBuildError) as caught:
        MatrixOverride(binding="region", matrix="regions", cases=("eu", "eu"))
    assert caught.value.code is InvocationBuildErrorCode.DUPLICATE_SELECTION


def test_inline_value_override_rejects_invalid_direct_state() -> None:
    with pytest.raises(InvocationBuildError) as namespace:
        InlineValueOverride(
            namespace=ValueNamespace.ENV,
            expression=_object(value="x"),
        )
    assert namespace.value.code is InvocationBuildErrorCode.INVALID_VALUE_NAMESPACE
    with pytest.raises(TypeError, match="ObjectExpr"):
        InlineValueOverride(
            namespace=ValueNamespace.VARIABLES,
            expression=cast("ObjectExpr", object()),
        )
    with pytest.raises(TypeError, match="Environment"):
        InlineValueOverride(
            namespace=ValueNamespace.VARIABLES,
            expression=_object(value="x"),
            environment=cast("str", object()),
        )
    with pytest.raises(InvocationBuildError) as environment:
        InlineValueOverride(
            namespace=ValueNamespace.VARIABLES,
            expression=_object(value="x"),
            environment="NOT VALID",
        )
    assert environment.value.code is InvocationBuildErrorCode.INVALID_VALUE_RESOURCE
    with pytest.raises(InvocationBuildError) as secret:
        InlineValueOverride(
            namespace=ValueNamespace.SECRETS,
            expression=_object(token="raw"),
        )
    assert secret.value.code is InvocationBuildErrorCode.SECRET_VALUE_UNSEALED
    with pytest.raises(TypeError, match="typed inputs"):
        inline_value_override(cast("ValueNamespace", object()), _object(value="x"))


def test_environment_value_override_rejects_invalid_state() -> None:
    valid = environment_value_override("staging", "TOKEN", "secret")
    with pytest.raises(TypeError, match="identities"):
        msgspec.structs.replace(valid, name=cast("str", object()))
    with pytest.raises(InvocationBuildError) as caught:
        msgspec.structs.replace(valid, name="NOT VALID")
    assert caught.value.code is InvocationBuildErrorCode.INVALID_IDENTIFIER
    with pytest.raises(TypeError, match="sealed JsonString"):
        EnvironmentValueOverride(
            environment="staging",
            name="TOKEN",
            value=cast("SensitiveValue[JsonString]", object()),
        )
    with pytest.raises(TypeError, match="exact strings"):
        environment_value_override("staging", "TOKEN", cast("str", object()))


def test_request_override_rejects_invalid_state() -> None:
    with pytest.raises(TypeError, match="subject"):
        RequestOverride(subject=cast("str", object()), endpoint=_literal("/ping"))
    with pytest.raises(InvocationBuildError) as subject:
        RequestOverride(subject="NOT VALID", endpoint=_literal("/ping"))
    assert subject.value.code is InvocationBuildErrorCode.INVALID_IDENTIFIER
    with pytest.raises(InvocationBuildError) as empty:
        RequestOverride(subject="ping")
    assert empty.value.code is InvocationBuildErrorCode.INVALID_VALUE_RESOURCE
    with pytest.raises(TypeError, match="ValueExpression"):
        RequestOverride(subject="ping", endpoint=cast("LiteralExpr", object()))


def test_profile_and_options_collections_reject_invalid_state() -> None:
    with pytest.raises(InvocationBuildError) as collection:
        ProfileSelectionOverrides(assertions=cast("tuple[str, ...]", []))
    assert collection.value.code is InvocationBuildErrorCode.DUPLICATE_SELECTION
    with pytest.raises(TypeError, match="single profile"):
        ProfileSelectionOverrides(gate=cast("str", object()))
    with pytest.raises(TypeError, match="profile overrides"):
        PlanningOptions(profiles=cast("ProfileSelectionOverrides", object()))

    inline = inline_value_override(ValueNamespace.VARIABLES, _object(value="x"))
    environment = environment_value_override("staging", "TOKEN", "secret")
    request = RequestOverride(subject="ping", endpoint=_literal("/ping"))
    invalid_collections: tuple[tuple[str, dict[str, object]], ...] = (
        ("inline Value", {"inline_values": [inline]}),
        ("Environment value", {"environment_values": [environment]}),
        ("Request overrides", {"requests": [request]}),
    )
    for message, changes in invalid_collections:
        with pytest.raises(TypeError, match=message):
            PlanningOptions(**changes)  # type: ignore[arg-type]

    with pytest.raises(InvocationBuildError) as duplicate_inline:
        PlanningOptions(inline_values=(inline, inline))
    assert duplicate_inline.value.code is InvocationBuildErrorCode.DUPLICATE_INLINE_VALUE
    with pytest.raises(InvocationBuildError) as duplicate_environment:
        PlanningOptions(environment_values=(environment, environment))
    assert duplicate_environment.value.code is InvocationBuildErrorCode.DUPLICATE_ENVIRONMENT_VALUE
    with pytest.raises(InvocationBuildError) as duplicate_request:
        PlanningOptions(requests=(request, request))
    assert duplicate_request.value.code is InvocationBuildErrorCode.DUPLICATE_REQUEST_EDIT

    resource_scope = ValueResourceOverride(
        namespace=ValueNamespace.VARIABLES,
        environment=None,
        resource="values",
    )
    project = compile_project(
        document("Project", "demo", "{}"),
        document("Value", "values", "{value: {tenant: demo}}"),
    )
    with pytest.raises(InvocationBuildError) as overlap:
        # The lower-level merge also protects callers if separately valid collections overlap.
        _value_overlays(project, (resource_scope,), (inline,))
    assert overlap.value.code is InvocationBuildErrorCode.DUPLICATE_INLINE_VALUE


def test_profile_override_lowering_covers_clear_forms() -> None:
    assert type(_profile_collection(()).base) is ClearBase
    assert type(_profile_scalar(ClearProfileSelection())) is Clear
    selected = _profile_scalar("strict")
    assert type(selected) is SetValue
    assert selected.value.value == "strict"


def test_secret_expression_sealing_recurses_across_every_expression_shape() -> None:
    provenance = _literal("value").provenance
    sealed = _sealed()
    lookup = _lookup()
    raw = _literal("raw")
    file_expression = FileExpr(
        path=lookup,
        conversion=None,
        trim=FileTrim.NONE,
        provenance=provenance,
    )
    fallback = FallbackExpr(sources=(lookup, sealed), provenance=provenance)
    optional = OptionalExpr(source=lookup, provenance=provenance)
    converted = ConvertExpr(
        source=lookup,
        target=ConversionType.STRING,
        provenance=provenance,
    )
    array = ArrayExpr(items=(lookup, sealed), provenance=provenance)
    object_expression = ObjectExpr(
        members=(ObjectExpressionMember(name="token", expression=lookup),),
        provenance=provenance,
    )
    interpolation = InterpolationExpr(
        mode=InterpolationMode.WHOLE_VALUE,
        segments=(ExpressionSegment(expression=lookup),),
        provenance=provenance,
    )
    text_interpolation = InterpolationExpr(
        mode=InterpolationMode.EMBEDDED_TEXT,
        segments=(LiteralTextSegment(value="Bearer "), ExpressionSegment(expression=lookup)),
        provenance=provenance,
    )

    for expression in (
        sealed,
        lookup,
        file_expression,
        fallback,
        optional,
        converted,
        array,
        object_expression,
        interpolation,
    ):
        assert _sealed_secret_expression(expression)
    assert not _sealed_secret_expression(raw)
    assert not _sealed_secret_expression(FallbackExpr(sources=(lookup, raw), provenance=provenance))
    assert not _sealed_secret_expression(ArrayExpr(items=(lookup, raw), provenance=provenance))
    assert not _sealed_secret_expression(
        ObjectExpr(
            members=(ObjectExpressionMember(name="raw", expression=raw),),
            provenance=provenance,
        )
    )
    assert not _sealed_secret_expression(text_interpolation)
    assert not _sealed_secret_expression(cast("LiteralExpr", object()))


def test_sensitive_value_fixture_retains_expected_payload_type() -> None:
    value = SensitiveValue(
        JsonString(value="secret"),
        origins=(TaintOriginId(value="test/secret"),),
    )
    assert value.payload_type is JsonString
