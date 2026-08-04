"""Provider-independent evaluation of the closed value-expression language."""

from __future__ import annotations

from typing import Protocol
from typing import cast

from comparo.core.domain.expression_types import ConversionType
from comparo.core.domain.expression_types import InterpolationMode
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import ConvertExpr
from comparo.core.domain.expressions import ExpressionSegment
from comparo.core.domain.expressions import FallbackExpr
from comparo.core.domain.expressions import FileExpr
from comparo.core.domain.expressions import InterpolationExpr
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import LiteralTextSegment
from comparo.core.domain.expressions import LookupExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import OptionalExpr
from comparo.core.domain.expressions import SensitiveLiteralExpr
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.provenance import ResolutionSource
from comparo.core.domain.provenance import ResolutionStep
from comparo.core.domain.provenance import ValueProviderIdentity
from comparo.core.domain.scopes import RuntimeScope
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.sensitivity import stable_taint_origin_union
from comparo.core.domain.value_egress import ValueEgressSourceKind
from comparo.core.domain.value_egress import normalize_secret_egress_origins
from comparo.core.domain.value_egress import value_egress_origin
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.values import Blocked
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import Error
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import JsonValue
from comparo.core.domain.values import Missing
from comparo.core.domain.values import Present
from comparo.core.domain.values import ResolutionErrorCode
from comparo.core.domain.values import ResolutionResult
from comparo.core.domain.values import ResolvedValue
from comparo.core.domain.values import Unavailable
from comparo.core.domain.values import UnavailableReason
from comparo.core.domain.values import is_json_value
from comparo.core.resolution.conversions import ConversionError
from comparo.core.resolution.conversions import convert
from comparo.core.resolution.conversions import trim_bytes
from comparo.core.resolution.models import ResolutionTarget
from comparo.core.resolution.models import SanitizationPatternDisposition
from comparo.core.resolution.provenance import COMPOSITE_PROVIDER
from comparo.core.resolution.provenance import CONVERSION_PROVIDER
from comparo.core.resolution.provenance import FALLBACK_PROVIDER
from comparo.core.resolution.provenance import INTERPOLATION_PROVIDER
from comparo.core.resolution.provenance import LITERAL_PROVIDER
from comparo.core.resolution.provenance import NAMESPACE_PROVIDER
from comparo.core.resolution.provenance import OPTIONAL_PROVIDER
from comparo.core.resolution.provenance import SENSITIVE_LITERAL_PROVIDER
from comparo.core.resolution.provenance import blocked
from comparo.core.resolution.provenance import error
from comparo.core.resolution.provenance import missing
from comparo.core.resolution.provenance import present
from comparo.core.resolution.provenance import resolution_source
from comparo.core.resolution.provenance import unavailable

type ResolutionResultT = ResolutionResult[ResolvedValue]
type LookupToken = tuple[ValueNamespace, SymbolPath, RuntimeScope]
_ACCESS = SensitiveValueAccess(purpose="expression-resolution")


class ResolutionHost(Protocol):
    """The narrow run-state and effect surface required by expression evaluation."""

    @property
    def resolution_cancelled(self) -> bool:
        """Return whether this run has received cancellation."""
        ...

    async def resolve_lookup(
        self,
        expression: LookupExpr,
        target: ResolutionTarget,
        stack: tuple[LookupToken, ...],
    ) -> ResolutionResultT:
        """Resolve one namespace lookup through immutable run bindings."""
        ...

    async def resolve_file(
        self,
        expression: FileExpr,
        target: ResolutionTarget,
        stack: tuple[LookupToken, ...],
    ) -> ResolutionResultT:
        """Resolve one confined file source through the injected adapter."""
        ...

    async def register_resolution(
        self,
        result: Present[ResolvedValue],
        target: ResolutionTarget,
        pattern_disposition: SanitizationPatternDisposition,
    ) -> None:
        """Retain a sensitive result for the run's sanitization boundary."""
        ...

    async def decode_yaml(
        self,
        result: Present[ResolvedValue],
        expression: ValueExpression,
        target: ResolutionTarget,
    ) -> ResolutionResultT:
        """Delegate one explicit YAML conversion through the run's decoder port."""
        ...


class ExpressionResolver:
    """Evaluate expressions while delegating run state and effects to a narrow host."""

    __slots__ = ("_host",)

    def __init__(self, host: ResolutionHost) -> None:
        """Bind one resolver to the owning run context."""
        self._host = host

    async def resolve(  # noqa: C901 - exhaustive dispatch over the closed expression union
        self,
        expression: ValueExpression,
        target: ResolutionTarget,
        stack: tuple[LookupToken, ...] = (),
    ) -> ResolutionResultT:
        """Evaluate the closed expression union and register every sensitive result."""
        result: ResolutionResultT
        if self._host.resolution_cancelled:
            result = self.error(
                expression,
                target,
                ResolutionErrorCode.CANCELLED,
                NAMESPACE_PROVIDER,
            )
        elif type(expression) is LiteralExpr:
            result = self.present(expression, target, expression.value, LITERAL_PROVIDER)
        elif type(expression) is SensitiveLiteralExpr:
            result = self.present(
                expression,
                target,
                cast("SensitiveValue[ResolvedValue]", expression.value),
                SENSITIVE_LITERAL_PROVIDER,
            )
        elif type(expression) is LookupExpr:
            result = await self._host.resolve_lookup(expression, target, stack)
        elif type(expression) is FileExpr:
            result = await self._host.resolve_file(expression, target, stack)
        elif type(expression) is FallbackExpr:
            result = await self._fallback(expression, target, stack)
        elif type(expression) is OptionalExpr:
            result = await self._optional(expression, target, stack)
        elif type(expression) is ConvertExpr:
            result = await self._conversion(expression, target, stack)
        elif type(expression) is ArrayExpr:
            result = await self._array(expression, target, stack)
        elif type(expression) is ObjectExpr:
            result = await self._object(expression, target, stack)
        else:
            result = await self._interpolation(expression, target, stack)
        if type(result) is Present:
            covered_composite = type(expression) in {ArrayExpr, ObjectExpr} and (
                target.namespace is not ValueNamespace.SECRETS
            )
            await self._host.register_resolution(
                result,
                target,
                (
                    SanitizationPatternDisposition.COVERED_COMPOSITE
                    if covered_composite
                    else SanitizationPatternDisposition.PATTERN_SOURCE
                ),
            )
        return result

    async def _fallback(
        self,
        expression: FallbackExpr,
        target: ResolutionTarget,
        stack: tuple[LookupToken, ...],
    ) -> ResolutionResultT:
        attempts: list[ResolutionStep] = []
        for source_expression in expression.sources:
            result = await self.resolve(source_expression, target, stack)
            if type(result) is Unavailable:
                attempts.extend(result.provenance.steps)
                continue
            return _prepend_unavailable(result, tuple(attempts), target)
        source = self._source(expression, target, FALLBACK_PROVIDER)
        return unavailable(
            UnavailableReason.NOT_PROVIDED,
            symbol=target.symbol,
            scope=target.scope,
            source=source,
            namespace=target.namespace,
            unavailable_steps=tuple(attempts),
        )

    async def _optional(
        self,
        expression: OptionalExpr,
        target: ResolutionTarget,
        stack: tuple[LookupToken, ...],
    ) -> ResolutionResultT:
        result = await self.resolve(expression.source, target, stack)
        if type(result) is not Unavailable:
            return result
        return missing(
            symbol=target.symbol,
            scope=target.scope,
            source=self._source(expression, target, OPTIONAL_PROVIDER),
            namespace=target.namespace,
            unavailable_steps=result.provenance.steps,
        )

    async def _conversion(
        self,
        expression: ConvertExpr,
        target: ResolutionTarget,
        stack: tuple[LookupToken, ...],
    ) -> ResolutionResultT:
        result = await self.resolve(expression.source, target, stack)
        if type(result) is not Present:
            return result
        return await self.convert_result(
            result,
            expression.target,
            expression,
            target,
            CONVERSION_PROVIDER,
        )

    async def _array(
        self,
        expression: ArrayExpr,
        target: ResolutionTarget,
        stack: tuple[LookupToken, ...],
    ) -> ResolutionResultT:
        values: list[JsonValue] = []
        origins: tuple[TaintOriginId, ...] = ()
        for index, item in enumerate(expression.items):
            child = _child_target(target, f"item-{index}")
            result = await self.resolve(item, child, stack)
            if type(result) is not Present:
                return result
            value, item_origins = _revealed(result.value)
            if not is_json_value(value):
                return self.error(
                    expression,
                    target,
                    ResolutionErrorCode.CONVERSION_FAILED,
                    COMPOSITE_PROVIDER,
                )
            values.append(value)
            origins = stable_taint_origin_union(origins, item_origins)
        array = JsonArray(items=tuple(values))
        return self.present(
            expression,
            target,
            _with_origins(array, origins),
            COMPOSITE_PROVIDER,
        )

    async def _object(
        self,
        expression: ObjectExpr,
        target: ResolutionTarget,
        stack: tuple[LookupToken, ...],
    ) -> ResolutionResultT:
        members: list[JsonObjectMember] = []
        origins: tuple[TaintOriginId, ...] = ()
        for member in expression.members:
            child = _child_target(target, member.name)
            result = await self.resolve(member.expression, child, stack)
            if type(result) is Missing:
                continue
            if type(result) is not Present:
                return result
            value, item_origins = _revealed(result.value)
            if not is_json_value(value):
                return self.error(
                    expression,
                    target,
                    ResolutionErrorCode.CONVERSION_FAILED,
                    COMPOSITE_PROVIDER,
                )
            members.append(JsonObjectMember(name=member.name, value=value))
            origins = stable_taint_origin_union(origins, item_origins)
        value = JsonObject(members=tuple(members))
        return self.present(
            expression,
            target,
            _with_origins(value, origins),
            COMPOSITE_PROVIDER,
        )

    async def _interpolation(
        self,
        expression: InterpolationExpr,
        target: ResolutionTarget,
        stack: tuple[LookupToken, ...],
    ) -> ResolutionResultT:
        if expression.mode is InterpolationMode.WHOLE_VALUE:
            whole_segment = cast("ExpressionSegment", expression.segments[0])
            result = await self.resolve(whole_segment.expression, target, stack)
            if type(result) is not Present:
                return result
            return self.present(expression, target, result.value, INTERPOLATION_PROVIDER)
        parts: list[str] = []
        origins: tuple[TaintOriginId, ...] = ()
        for current_segment in expression.segments:
            if type(current_segment) is LiteralTextSegment:
                parts.append(current_segment.value)
                continue
            result = await self.resolve(current_segment.expression, target, stack)
            if type(result) is not Present:
                return result
            value, item_origins = _revealed(result.value)
            try:
                rendered = convert(value, ConversionType.STRING)
            except ConversionError:
                return self.error(
                    expression,
                    target,
                    ResolutionErrorCode.CONVERSION_FAILED,
                    INTERPOLATION_PROVIDER,
                )
            parts.append(cast("JsonString", rendered).value)
            origins = stable_taint_origin_union(origins, item_origins)
        text = JsonString(value="".join(parts))
        return self.present(
            expression,
            target,
            _with_origins(text, origins),
            INTERPOLATION_PROVIDER,
        )

    async def convert_result(
        self,
        result: Present[ResolvedValue],
        target_type: ConversionType,
        expression: ValueExpression,
        target: ResolutionTarget,
        provider: ValueProviderIdentity,
    ) -> ResolutionResultT:
        """Convert one present value while preserving its sensitivity lineage."""
        if target_type is ConversionType.YAML:
            return await self._host.decode_yaml(result, expression, target)
        value, origins = _revealed(result.value)
        if type(expression) is FileExpr and type(value) is BytesValue:
            value = trim_bytes(value, expression.trim)
        try:
            converted = convert(value, target_type)
        except ConversionError:
            return self.error(
                expression,
                target,
                ResolutionErrorCode.CONVERSION_FAILED,
                provider,
            )
        return self.present(
            expression,
            target,
            _with_origins(converted, origins),
            provider,
        )

    def present(
        self,
        expression: ValueExpression,
        target: ResolutionTarget,
        value: ResolvedValue | SensitiveValue[ResolvedValue],
        provider: ValueProviderIdentity,
        production_scope: RuntimeScope | None = None,
    ) -> Present[ResolvedValue]:
        """Create a present expression result with secret-namespace fail-closed tainting."""
        if target.namespace is ValueNamespace.SECRETS:
            secret_origin = _secret_origin(target.symbol)
            if is_sensitive_value(value):
                value = _with_origins(
                    cast("ResolvedValue", value.reveal(access=_ACCESS)),
                    normalize_secret_egress_origins(value.origins, target.symbol),
                )
            else:
                value = SensitiveValue(
                    cast("ResolvedValue", value),
                    origins=(secret_origin,),
                )
        return present(
            value,
            symbol=target.symbol,
            scope=target.scope,
            source=self._source(expression, target, provider, production_scope),
            namespace=target.namespace,
        )

    def unavailable(
        self,
        expression: ValueExpression,
        target: ResolutionTarget,
        provider: ValueProviderIdentity,
    ) -> Unavailable:
        """Create an unavailable expression result."""
        return unavailable(
            UnavailableReason.NOT_DEFINED,
            symbol=target.symbol,
            scope=target.scope,
            source=self._source(expression, target, provider),
            namespace=target.namespace,
        )

    def error(
        self,
        expression: ValueExpression,
        target: ResolutionTarget,
        code: ResolutionErrorCode,
        provider: ValueProviderIdentity,
    ) -> Error:
        """Create a value-free expression failure."""
        return error(
            code,
            symbol=target.symbol,
            scope=target.scope,
            source=self._source(expression, target, provider),
            namespace=target.namespace,
        )

    @staticmethod
    def _source(
        expression: ValueExpression,
        target: ResolutionTarget,
        provider: ValueProviderIdentity,
        production_scope: RuntimeScope | None = None,
    ) -> ResolutionSource:
        return resolution_source(
            symbol=target.symbol,
            provider=provider,
            configuration=expression.provenance,
            scope=production_scope or target.scope,
            namespace=target.namespace,
        )


def _child_target(target: ResolutionTarget, segment: str) -> ResolutionTarget:
    return ResolutionTarget(
        symbol=SymbolPath(segments=(*target.symbol.segments, segment)),
        scope=target.scope,
        namespace=target.namespace,
        cell_inputs=target.cell_inputs,
    )


def _revealed(
    value: ResolvedValue | SensitiveValue[ResolvedValue],
) -> tuple[ResolvedValue, tuple[TaintOriginId, ...]]:
    if is_sensitive_value(value):
        return cast("ResolvedValue", value.reveal(access=_ACCESS)), value.origins
    return cast("ResolvedValue", value), ()


def _with_origins(
    value: ResolvedValue,
    origins: tuple[TaintOriginId, ...],
) -> ResolvedValue | SensitiveValue[ResolvedValue]:
    if not origins:
        return value
    return SensitiveValue(value, origins=origins)


def _secret_origin(symbol: SymbolPath) -> TaintOriginId:
    return value_egress_origin(ValueEgressSourceKind.SECRET, symbol)


def _prepend_unavailable(
    result: Present[ResolvedValue] | Missing | Error | Blocked,
    steps: tuple[ResolutionStep, ...],
    target: ResolutionTarget,
) -> Present[ResolvedValue] | Missing | Error | Blocked:
    if not steps:
        return result
    terminal = result.provenance.steps[-1].source
    namespace = target.namespace
    if type(result) is Present:
        return present(
            result.value,
            symbol=target.symbol,
            scope=target.scope,
            source=terminal,
            namespace=namespace,
            unavailable_steps=steps,
        )
    if type(result) is Missing:
        return missing(
            symbol=target.symbol,
            scope=target.scope,
            source=terminal,
            namespace=namespace,
            unavailable_steps=steps,
        )
    if type(result) is Error:
        return error(
            result.code,
            symbol=target.symbol,
            scope=target.scope,
            source=terminal,
            namespace=namespace,
            unavailable_steps=steps,
        )
    return blocked(
        result.code,
        symbol=target.symbol,
        scope=target.scope,
        source=terminal,
        namespace=namespace,
        unavailable_steps=steps,
    )
