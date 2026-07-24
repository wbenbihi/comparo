"""Tests for complete v1alpha1 directive and interpolation compilation."""

from typing import cast

import pytest

from comparo.configuration.yaml_parser import parse_yaml_source
from comparo.core.configuration.directives import compile_inert_value
from comparo.core.configuration.directives import compile_value_expression
from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import ConfigurationNode
from comparo.core.domain.expression_analysis import expression_dependencies
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import ConvertExpr
from comparo.core.domain.expressions import ExpressionSegment
from comparo.core.domain.expressions import FallbackExpr
from comparo.core.domain.expressions import FileExpr
from comparo.core.domain.expressions import InterpolationExpr
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import OptionalExpr
from comparo.core.domain.expressions import SensitiveLiteralExpr
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import SourceId
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString

_SOURCE = SourceId(value="directive-test")
_OWNER = ResourceIdentity(
    namespace=NamespaceId(value="local"),
    kind=ResourceKind.VALUE,
    resource_id=ResourceId(value="subject"),
)


def _node(text: str) -> ConfigurationNode:
    parsed = parse_yaml_source(source_id=_SOURCE, text=text)
    assert parsed.diagnostics == ()
    return parsed.documents[0].root


def _compile(
    text: str,
    *,
    sensitive: bool = False,
    resolver: object | None = None,
) -> tuple[ValueExpression, tuple[str, ...]]:
    diagnostics: list[ConfigurationDiagnostic] = []

    def resolve(reference: str, span: object) -> ValueExpression | None:
        del span
        if resolver is None or reference != "shared":
            return None
        return cast("ValueExpression", resolver)

    expression = compile_value_expression(
        _node(text),
        owner=_OWNER,
        diagnostics=diagnostics,
        resolve_value=resolve,
        sensitive_literals=sensitive,
    )
    return expression, tuple(item.code.value for item in diagnostics)


def test_inert_compilation_preserves_exact_json_types_and_dollar_payloads() -> None:
    value = compile_inert_value(
        _node("nullValue: null\nflag: true\ncount: 2\nnumber: 1.50\ntext: hi\nitems: [1]\n$ref: x")
    )
    assert type(value) is JsonObject
    assert tuple(type(member.value) for member in value.members) == (
        JsonNull,
        JsonBoolean,
        JsonInteger,
        JsonDecimal,
        JsonString,
        JsonArray,
        JsonString,
    )


@pytest.mark.parametrize(
    ("text", "namespace", "converted"),
    [
        ("{$var: NAME}", ValueNamespace.VARIABLES, False),
        ("{$secret: TOKEN}", ValueNamespace.SECRETS, False),
        ("{$param: tenant}", ValueNamespace.PARAMETERS, False),
        ("{$env: PORT}", ValueNamespace.ENV, False),
        ("{$matrix: locale}", ValueNamespace.MATRIX, False),
        ("{$env: {name: PORT, as: integer}}", ValueNamespace.ENV, True),
    ],
)
def test_lookup_directives_use_disjoint_namespaces_and_explicit_conversion(
    text: str,
    namespace: ValueNamespace,
    converted: bool,
) -> None:
    expression, diagnostics = _compile(text)
    assert diagnostics == ()
    assert expression_dependencies(expression)[0].namespace is namespace
    assert (type(expression) is ConvertExpr) is converted


def test_file_fallback_optional_literal_and_value_use_compile_to_closed_nodes() -> None:
    literal, diagnostics = _compile("{$literal: {$env: NOT_A_LOOKUP}}")
    assert type(literal) is LiteralExpr
    assert diagnostics == ()
    assert type(literal.value) is JsonObject
    short_file, diagnostics = _compile("{$file: token.bin}")
    assert type(short_file) is FileExpr
    assert short_file.maximum_bytes is None
    assert diagnostics == ()
    long_file, diagnostics = _compile(
        "{$file: {path: token.txt, as: string, trim: both, maxBytes: 12}}"
    )
    assert type(long_file) is FileExpr
    assert long_file.maximum_bytes is not None
    assert long_file.maximum_bytes.value == 12
    assert diagnostics == ()
    fallback, diagnostics = _compile("{$from: [{$env: FIRST}, {$env: SECOND}]} ")
    assert type(fallback) is FallbackExpr
    assert diagnostics == ()
    optional, diagnostics = _compile("{$optional: {$env: MAYBE}}")
    assert type(optional) is OptionalExpr
    assert diagnostics == ()
    shared = LiteralExpr(value=JsonString(value="shared"), provenance=optional.provenance)
    used, diagnostics = _compile("{$use: shared}", resolver=shared)
    assert used is shared
    assert diagnostics == ()


def test_nested_arrays_objects_sensitive_literals_and_optional_positions_are_safe() -> None:
    expression, diagnostics = _compile(
        "{token: literal-secret, nested: [1, {$env: PORT}]}", sensitive=True
    )
    assert type(expression) is ObjectExpr
    assert all(
        type(member.expression) in {SensitiveLiteralExpr, ArrayExpr}
        for member in expression.members
    )
    assert "literal-secret" not in repr(expression)
    assert diagnostics == ()
    array, diagnostics = _compile("[{$optional: {$env: MAYBE}}]")
    assert type(array) is ArrayExpr
    assert diagnostics == ("configuration.directive.optional-position",)


def test_interpolation_is_namespaced_typed_escaped_and_mode_aware() -> None:
    whole, diagnostics = _compile("'${env.PORT:integer}'")
    assert type(whole) is InterpolationExpr
    assert type(whole.segments[0]) is ExpressionSegment
    assert type(whole.segments[0].expression) is ConvertExpr
    assert diagnostics == ()
    embedded, diagnostics = _compile("'Bearer ${secrets.TOKEN}'")
    assert type(embedded) is InterpolationExpr
    assert diagnostics == ()
    escaped, diagnostics = _compile("'$${env.PORT}'")
    assert type(escaped) is LiteralExpr
    assert escaped.value == JsonString(value="${env.PORT}")
    assert diagnostics == ()
    mixed, diagnostics = _compile("'x=$${raw} ${variables.NAME} tail'")
    assert type(mixed) is InterpolationExpr
    assert diagnostics == ()
    suffix, diagnostics = _compile("'${variables.NAME}tail'")
    assert type(suffix) is InterpolationExpr
    assert diagnostics == ()


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("{$env: {name: PORT, extra: true}}", "configuration.directive.malformed"),
        ("{$env: {name: 2}}", "configuration.directive.malformed"),
        ("{$env: [PORT]}", "configuration.directive.malformed"),
        ("{$env: BAD..NAME}", "configuration.directive.malformed"),
        ("{$env: {name: PORT, as: unknown}}", "configuration.directive.malformed"),
        ("{$env: {name: PORT, as: 2}}", "configuration.directive.malformed"),
        ("{$file: 2}", "configuration.directive.malformed"),
        ("{$file: {as: string}}", "configuration.directive.malformed"),
        ("{$file: {path: 2}}", "configuration.directive.malformed"),
        ("{$file: {path: x, as: nope}}", "configuration.directive.malformed"),
        ("{$file: {path: x, as: 2}}", "configuration.directive.malformed"),
        ("{$file: {path: x, trim: nope}}", "configuration.directive.malformed"),
        ("{$file: {path: x, trim: 2}}", "configuration.directive.malformed"),
        ("{$file: {path: x, maxBytes: -1}}", "configuration.directive.malformed"),
        ("{$file: {path: x, maxBytes: bad}}", "configuration.directive.malformed"),
        ("{$from: []}", "configuration.directive.malformed"),
        ("{$optional: {$unknown: x}}", "configuration.directive.unknown"),
        ("{$use: missing}", "configuration.reference.invalid"),
        ("{$use: 2}", "configuration.reference.invalid"),
        ("{$matrix: field, other: true}", "configuration.directive.malformed"),
        ("{$unknown: field}", "configuration.directive.unknown"),
        ("{$unknown: field, other: true}", "configuration.directive.unknown"),
        ("{$env: PORT, other: true}", "configuration.directive.malformed"),
        ("'${PORT}'", "configuration.interpolation.invalid"),
        ("'${unknown.PORT}'", "configuration.interpolation.invalid"),
        ("'${env.BAD..NAME}'", "configuration.interpolation.invalid"),
        ("'${env.BAD.NAME:unknown}'", "configuration.directive.malformed"),
        ("'prefix ${env.VALUE:json}'", "configuration.interpolation.invalid"),
    ],
)
def test_malformed_and_future_directives_fail_explicitly(text: str, code: str) -> None:
    _, diagnostics = _compile(text)
    assert code in diagnostics
