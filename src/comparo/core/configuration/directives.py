"""Compilation of the closed v1alpha1 value-directive language."""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal
from typing import cast

from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import ConfigurationNode
from comparo.core.configuration.models import MappingNode
from comparo.core.configuration.models import ScalarNode
from comparo.core.configuration.models import SequenceNode
from comparo.core.configuration.models import SourceSpan
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
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
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.domain.values import json_number

type ValueReferenceResolver = Callable[[str, SourceSpan], ValueExpression | None]

_INTERPOLATION = re.compile(r"(?<!\$)\$\{([^{}]+)\}")
_DIRECTIVES = frozenset(
    {"$use", "$var", "$secret", "$param", "$env", "$file", "$from", "$optional", "$literal"}
)
_FUTURE_DIRECTIVES = frozenset(
    {"$matrix", "$case", "$generate", "$step", "$select", "$transform", "$patch", "$provider"}
)
_LOOKUPS = {
    "$var": (ValueNamespace.VARIABLES, ValueScopeKind.RUN),
    "$secret": (ValueNamespace.SECRETS, ValueScopeKind.RUN),
    "$param": (ValueNamespace.PARAMETERS, ValueScopeKind.CELL),
    "$env": (ValueNamespace.ENV, ValueScopeKind.ENVIRONMENT),
}
_INTERPOLATION_NAMESPACES = {namespace.value: namespace for namespace in ValueNamespace}

_MALFORMED = DiagnosticCode(value="configuration.directive.malformed")
_UNKNOWN = DiagnosticCode(value="configuration.directive.unknown")
_UNSUPPORTED = DiagnosticCode(value="configuration.directive.unsupported")
_INTERPOLATION_INVALID = DiagnosticCode(value="configuration.interpolation.invalid")
_REFERENCE_INVALID = DiagnosticCode(value="configuration.reference.invalid")
_OPTIONAL_POSITION = DiagnosticCode(value="configuration.directive.optional-position")


def _issue(code: DiagnosticCode, span: SourceSpan) -> ConfigurationDiagnostic:
    return ConfigurationDiagnostic(
        code=code,
        severity=DiagnosticSeverity.ERROR,
        primary=span,
    )


def _provenance(owner: ResourceIdentity, span: SourceSpan) -> ValueProvenance:
    return ValueProvenance(
        entries=(
            ProvenanceEntry(
                kind=ProvenanceKind.PROJECT,
                resource=owner,
                location=span.start,
            ),
        )
    )


def _exact_scalar(node: ScalarNode) -> JsonValue:
    if node.value is None:
        return JsonNull()
    if type(node.value) is bool:
        return JsonBoolean(value=node.value)
    if type(node.value) is int:
        return JsonInteger(value=node.value)
    if type(node.value) is Decimal:
        return json_number(node.value)
    return JsonString(value=cast("str", node.value))


def compile_inert_value(node: ConfigurationNode) -> JsonValue:
    """Convert an opaque/static node to exact inert data without directive recognition."""
    if type(node) is ScalarNode:
        return _exact_scalar(node)
    if type(node) is SequenceNode:
        return JsonArray(items=tuple(compile_inert_value(item) for item in node.items))
    return JsonObject(
        members=tuple(
            JsonObjectMember(name=entry.name, value=compile_inert_value(entry.value))
            for entry in node.entries
        )
    )


def _literal_expression(
    node: ConfigurationNode,
    *,
    owner: ResourceIdentity,
    sensitive_literals: bool,
) -> ValueExpression:
    exact = compile_inert_value(node)
    provenance = _provenance(owner, node.span)
    if sensitive_literals:
        return SensitiveLiteralExpr(
            value=SensitiveValue(
                exact,
                origins=(TaintOriginId(value="configuration/secret"),),
            ),
            provenance=provenance,
        )
    return LiteralExpr(value=exact, provenance=provenance)


def _mapping_fields(node: MappingNode) -> dict[str, ConfigurationNode]:
    return {entry.name: entry.value for entry in node.entries}


def _converted(
    source: ValueExpression,
    conversion_name: str | None,
    *,
    owner: ResourceIdentity,
    span: SourceSpan,
    diagnostics: list[ConfigurationDiagnostic],
) -> ValueExpression:
    if conversion_name is None:
        return source
    try:
        target = ConversionType(conversion_name)
    except ValueError:
        diagnostics.append(_issue(_MALFORMED, span))
        return source
    return ConvertExpr(source=source, target=target, provenance=_provenance(owner, span))


def _lookup(
    directive: str,
    argument: ConfigurationNode,
    *,
    owner: ResourceIdentity,
    diagnostics: list[ConfigurationDiagnostic],
) -> ValueExpression:
    name: str | None = None
    conversion: str | None = None
    if type(argument) is ScalarNode and type(argument.value) is str:
        name = argument.value
    elif type(argument) is MappingNode:
        fields = _mapping_fields(argument)
        if set(fields) <= {"name", "as"}:
            name_node = fields.get("name")
            as_node = fields.get("as")
            if type(name_node) is ScalarNode and type(name_node.value) is str:
                name = name_node.value
            if type(as_node) is ScalarNode and type(as_node.value) is str:
                conversion = as_node.value
            elif as_node is not None:
                name = None
    if name is None:
        diagnostics.append(_issue(_MALFORMED, argument.span))
        return _literal_expression(argument, owner=owner, sensitive_literals=False)
    namespace, scope = _LOOKUPS[directive]
    try:
        dependency = ExpressionDependency(
            namespace=namespace,
            symbol=SymbolPath(segments=tuple(name.split("."))),
            available_at=scope,
        )
    except ValueError:
        diagnostics.append(_issue(_MALFORMED, argument.span))
        return _literal_expression(argument, owner=owner, sensitive_literals=False)
    source = LookupExpr(dependency=dependency, provenance=_provenance(owner, argument.span))
    return _converted(
        source,
        conversion,
        owner=owner,
        span=argument.span,
        diagnostics=diagnostics,
    )


def _file(
    argument: ConfigurationNode,
    *,
    owner: ResourceIdentity,
    diagnostics: list[ConfigurationDiagnostic],
    resolve_value: ValueReferenceResolver,
) -> ValueExpression:
    options: tuple[ConversionType | None, FileTrim, ByteCount | None]
    if type(argument) is ScalarNode and type(argument.value) is str:
        path = _literal_expression(argument, owner=owner, sensitive_literals=False)
        options = (None, FileTrim.NONE, None)
    elif type(argument) is MappingNode:
        fields = _mapping_fields(argument)
        if not set(fields) <= {"path", "as", "trim", "maxBytes"} or "path" not in fields:
            diagnostics.append(_issue(_MALFORMED, argument.span))
            return _literal_expression(argument, owner=owner, sensitive_literals=False)
        path = compile_value_expression(
            fields["path"],
            owner=owner,
            diagnostics=diagnostics,
            resolve_value=resolve_value,
        )
        options = _file_options(fields, argument.span, diagnostics)
    else:
        diagnostics.append(_issue(_MALFORMED, argument.span))
        return _literal_expression(argument, owner=owner, sensitive_literals=False)
    conversion, trim, maximum = options
    try:
        return FileExpr(
            path=path,
            conversion=conversion,
            trim=trim,
            maximum_bytes=maximum,
            provenance=_provenance(owner, argument.span),
        )
    except (TypeError, ValueError):
        diagnostics.append(_issue(_MALFORMED, argument.span))
        return _literal_expression(argument, owner=owner, sensitive_literals=False)


def _file_options(
    fields: dict[str, ConfigurationNode],
    span: SourceSpan,
    diagnostics: list[ConfigurationDiagnostic],
) -> tuple[ConversionType | None, FileTrim, ByteCount | None]:
    conversion: ConversionType | None = None
    trim = FileTrim.NONE
    maximum: ByteCount | None = None
    as_node = fields.get("as")
    trim_node = fields.get("trim")
    maximum_node = fields.get("maxBytes")
    try:
        if type(as_node) is ScalarNode and type(as_node.value) is str:
            conversion = ConversionType(as_node.value)
        elif as_node is not None:
            raise ValueError
        if type(trim_node) is ScalarNode and type(trim_node.value) is str:
            trim = FileTrim(trim_node.value)
        elif trim_node is not None:
            raise ValueError
        if type(maximum_node) is ScalarNode and type(maximum_node.value) is int:
            maximum = ByteCount(value=maximum_node.value)
        elif maximum_node is not None:
            raise ValueError
    except (TypeError, ValueError):
        diagnostics.append(_issue(_MALFORMED, span))
    return conversion, trim, maximum


def _interpolation_lookup(
    body: str,
    *,
    owner: ResourceIdentity,
    span: SourceSpan,
    diagnostics: list[ConfigurationDiagnostic],
) -> ValueExpression | None:
    symbol_text, separator, conversion = body.rpartition(":")
    if not separator:
        symbol_text = body
        conversion = ""
    namespace_name, dot, path = symbol_text.partition(".")
    namespace = _INTERPOLATION_NAMESPACES.get(namespace_name)
    if not dot or namespace is None:
        diagnostics.append(_issue(_INTERPOLATION_INVALID, span))
        return None
    scope = {
        ValueNamespace.VARIABLES: ValueScopeKind.RUN,
        ValueNamespace.SECRETS: ValueScopeKind.RUN,
        ValueNamespace.ENV: ValueScopeKind.ENVIRONMENT,
        ValueNamespace.PARAMETERS: ValueScopeKind.CELL,
        ValueNamespace.MATRIX: ValueScopeKind.CELL,
        ValueNamespace.STEPS: ValueScopeKind.STEP,
    }[namespace]
    try:
        lookup = LookupExpr(
            dependency=ExpressionDependency(
                namespace=namespace,
                symbol=SymbolPath(segments=tuple(path.split("."))),
                available_at=scope,
            ),
            provenance=_provenance(owner, span),
        )
    except ValueError:
        diagnostics.append(_issue(_INTERPOLATION_INVALID, span))
        return None
    return _converted(
        lookup,
        conversion or None,
        owner=owner,
        span=span,
        diagnostics=diagnostics,
    )


def _interpolation(
    node: ScalarNode,
    *,
    owner: ResourceIdentity,
    diagnostics: list[ConfigurationDiagnostic],
) -> ValueExpression | None:
    text = cast("str", node.value)
    matches = tuple(_INTERPOLATION.finditer(text))
    if not matches:
        return None
    expressions = tuple(
        _interpolation_lookup(
            match.group(1),
            owner=owner,
            span=node.span,
            diagnostics=diagnostics,
        )
        for match in matches
    )
    if any(expression is None for expression in expressions):
        return _literal_expression(node, owner=owner, sensitive_literals=False)
    typed = tuple(expression for expression in expressions if expression is not None)
    if len(matches) == 1 and matches[0].span() == (0, len(text)):
        return InterpolationExpr(
            mode=InterpolationMode.WHOLE_VALUE,
            segments=(ExpressionSegment(expression=typed[0]),),
            provenance=_provenance(owner, node.span),
        )
    segments: list[LiteralTextSegment | ExpressionSegment] = []
    cursor = 0
    for match, expression in zip(matches, typed, strict=True):
        if match.start() > cursor:
            segments.append(
                LiteralTextSegment(value=text[cursor : match.start()].replace("$${", "${"))
            )
        segments.append(ExpressionSegment(expression=expression))
        cursor = match.end()
    if cursor < len(text):
        segments.append(LiteralTextSegment(value=text[cursor:].replace("$${", "${")))
    try:
        return InterpolationExpr(
            mode=InterpolationMode.EMBEDDED_TEXT,
            segments=tuple(segments),
            provenance=_provenance(owner, node.span),
        )
    except ValueError:
        diagnostics.append(_issue(_INTERPOLATION_INVALID, node.span))
        return _literal_expression(node, owner=owner, sensitive_literals=False)


def _directive(
    directive: str,
    argument: ConfigurationNode,
    *,
    owner: ResourceIdentity,
    diagnostics: list[ConfigurationDiagnostic],
    resolve_value: ValueReferenceResolver,
    sensitive_literals: bool,
) -> ValueExpression:
    if directive in _LOOKUPS:
        return _lookup(directive, argument, owner=owner, diagnostics=diagnostics)
    if directive == "$literal":
        return _literal_expression(
            argument,
            owner=owner,
            sensitive_literals=sensitive_literals,
        )
    if directive == "$file":
        return _file(
            argument,
            owner=owner,
            diagnostics=diagnostics,
            resolve_value=resolve_value,
        )
    if directive == "$from":
        if type(argument) is not SequenceNode or not argument.items:
            diagnostics.append(_issue(_MALFORMED, argument.span))
            return _literal_expression(argument, owner=owner, sensitive_literals=False)
        return FallbackExpr(
            sources=tuple(
                compile_value_expression(
                    item,
                    owner=owner,
                    diagnostics=diagnostics,
                    resolve_value=resolve_value,
                    sensitive_literals=sensitive_literals,
                )
                for item in argument.items
            ),
            provenance=_provenance(owner, argument.span),
        )
    if directive == "$optional":
        return OptionalExpr(
            source=compile_value_expression(
                argument,
                owner=owner,
                diagnostics=diagnostics,
                resolve_value=resolve_value,
                sensitive_literals=sensitive_literals,
            ),
            provenance=_provenance(owner, argument.span),
        )
    if directive == "$use":
        if type(argument) is not ScalarNode or type(argument.value) is not str:
            diagnostics.append(_issue(_REFERENCE_INVALID, argument.span))
            return _literal_expression(argument, owner=owner, sensitive_literals=False)
        resolved = resolve_value(argument.value, argument.span)
        if resolved is None:
            diagnostics.append(_issue(_REFERENCE_INVALID, argument.span))
            return _literal_expression(argument, owner=owner, sensitive_literals=False)
        return resolved
    diagnostics.append(
        _issue(_UNSUPPORTED if directive in _FUTURE_DIRECTIVES else _UNKNOWN, argument.span)
    )
    return _literal_expression(argument, owner=owner, sensitive_literals=False)


def compile_value_expression(
    node: ConfigurationNode,
    *,
    owner: ResourceIdentity,
    diagnostics: list[ConfigurationDiagnostic],
    resolve_value: ValueReferenceResolver,
    sensitive_literals: bool = False,
) -> ValueExpression:
    """Compile one recursively resolvable node into the canonical expression tree."""
    if type(node) is ScalarNode:
        if type(node.value) is str:
            interpolated = _interpolation(node, owner=owner, diagnostics=diagnostics)
            if interpolated is not None:
                return interpolated
            if "$${" in node.value:
                escaped = ScalarNode(value=node.value.replace("$${", "${"), span=node.span)
                return _literal_expression(
                    escaped,
                    owner=owner,
                    sensitive_literals=sensitive_literals,
                )
        return _literal_expression(
            node,
            owner=owner,
            sensitive_literals=sensitive_literals,
        )
    if type(node) is SequenceNode:
        items = tuple(
            compile_value_expression(
                item,
                owner=owner,
                diagnostics=diagnostics,
                resolve_value=resolve_value,
                sensitive_literals=sensitive_literals,
            )
            for item in node.items
        )
        if any(type(item) is OptionalExpr for item in items):
            diagnostics.append(_issue(_OPTIONAL_POSITION, node.span))
            items = tuple(
                _literal_expression(node.items[index], owner=owner, sensitive_literals=False)
                if type(item) is OptionalExpr
                else item
                for index, item in enumerate(items)
            )
        return ArrayExpr(items=items, provenance=_provenance(owner, node.span))
    directive_entries = tuple(entry for entry in node.entries if entry.name.startswith("$"))
    if len(node.entries) == 1 and directive_entries:
        entry = directive_entries[0]
        return _directive(
            entry.name,
            entry.value,
            owner=owner,
            diagnostics=diagnostics,
            resolve_value=resolve_value,
            sensitive_literals=sensitive_literals,
        )
    if directive_entries:
        if any(entry.name in _DIRECTIVES for entry in directive_entries):
            diagnostics.append(_issue(_MALFORMED, node.span))
        else:
            diagnostics.extend(
                _issue(
                    _UNSUPPORTED if entry.name in _FUTURE_DIRECTIVES else _UNKNOWN,
                    entry.name_span,
                )
                for entry in directive_entries
            )
    return ObjectExpr(
        members=tuple(
            ObjectExpressionMember(
                name=entry.name,
                expression=compile_value_expression(
                    entry.value,
                    owner=owner,
                    diagnostics=diagnostics,
                    resolve_value=resolve_value,
                    sensitive_literals=sensitive_literals,
                ),
            )
            for entry in node.entries
        ),
        provenance=_provenance(owner, node.span),
    )
