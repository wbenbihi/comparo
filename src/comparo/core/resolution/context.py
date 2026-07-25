"""Run-owned orchestration of pure expressions and injected value providers."""

from __future__ import annotations

import asyncio
from typing import cast

from comparo.core.domain.expressions import FileExpr
from comparo.core.domain.expressions import LookupExpr
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.expressions import is_value_expression
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import RuntimeScope
from comparo.core.domain.scopes import run_scope_of
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import Present
from comparo.core.domain.values import ResolutionErrorCode
from comparo.core.domain.values import ResolvedValue
from comparo.core.domain.values import is_json_value
from comparo.core.planning.models import DefinitionHandle
from comparo.core.planning.models import PreparedRun
from comparo.core.planning.models import is_definition_handle
from comparo.core.resolution.cache import SingleFlightCache
from comparo.core.resolution.control import RunCancellation
from comparo.core.resolution.generation import DeterministicGenerator
from comparo.core.resolution.models import CellInputs
from comparo.core.resolution.models import EnvironmentSnapshot
from comparo.core.resolution.models import ExternalProviderSource
from comparo.core.resolution.models import ExternalValueRequest
from comparo.core.resolution.models import FileReadRequest
from comparo.core.resolution.models import GenerationRequest
from comparo.core.resolution.models import GenerationSpec
from comparo.core.resolution.models import ProviderOutcome
from comparo.core.resolution.models import ResolutionTarget
from comparo.core.resolution.models import ResolvedForWire
from comparo.core.resolution.models import SanitizationSnapshot
from comparo.core.resolution.models import StructuredDocument
from comparo.core.resolution.models import WireResolution
from comparo.core.resolution.models import YamlDecodeRequest
from comparo.core.resolution.ports import ExternalValueProvider
from comparo.core.resolution.ports import FileValueProvider
from comparo.core.resolution.ports import GeneratedValueProvider
from comparo.core.resolution.ports import RunClock
from comparo.core.resolution.ports import YamlValueDecoder
from comparo.core.resolution.provenance import CONVERSION_PROVIDER
from comparo.core.resolution.provenance import ENV_PROVIDER
from comparo.core.resolution.provenance import MATRIX_PROVIDER
from comparo.core.resolution.provenance import NAMESPACE_PROVIDER
from comparo.core.resolution.provenance import resolution_source
from comparo.core.resolution.provider_runtime import ProviderRuntime
from comparo.core.resolution.provider_runtime import reveal
from comparo.core.resolution.provider_runtime import seal
from comparo.core.resolution.provider_runtime import taint_origin
from comparo.core.resolution.registry import SensitiveValueRegistry
from comparo.core.resolution.resolver import ExpressionResolver
from comparo.core.resolution.resolver import LookupToken
from comparo.core.resolution.resolver import ResolutionResultT
from comparo.core.resolution.scopes import concrete_scope
from comparo.core.resolution.scopes import environment_scope
from comparo.core.resolution.snapshots import environment_snapshot
from comparo.core.resolution.snapshots import matrix_binding
from comparo.core.resolution.snapshots import namespace_binding
from comparo.core.resolution.snapshots import validate_environment_snapshots

type _Result = ResolutionResultT
type _LookupToken = LookupToken
type _FileKey = tuple[FileExpr, RuntimeScope]
type _ExternalKey = tuple[ExternalProviderSource, RuntimeScope]
type _GenerationKey = tuple[GenerationSpec, RuntimeScope]
type _DecodeKey = tuple[ValueExpression, RuntimeScope]


class RunContext:
    """Own all live value state, caches, effects, cancellation, and sanitization for one run."""

    __slots__ = (
        "_cancellation",
        "_clock",
        "_close_task",
        "_closed",
        "_decode_cache",
        "_external_cache",
        "_external_providers",
        "_file_cache",
        "_file_provider",
        "_generation_cache",
        "_generator",
        "_prepared",
        "_providers",
        "_registry",
        "_resolver",
        "_run",
        "_safety",
        "_secure_generator",
        "_snapshots",
        "_yaml_decoder",
    )

    def __init__(
        self,
        *,
        prepared: PreparedRun,
        environment_snapshots: tuple[EnvironmentSnapshot, ...],
        clock: RunClock,
        generator: GeneratedValueProvider,
        file_provider: FileValueProvider | None = None,
        external_providers: tuple[ExternalValueProvider, ...] = (),
        secure_generator: GeneratedValueProvider | None = None,
        safety: tuple[DefinitionHandle, ...] = (),
        yaml_decoder: YamlValueDecoder | None = None,
    ) -> None:
        """Create one isolated context from already admitted and snapshotted inputs."""
        if type(prepared) is not PreparedRun:
            raise TypeError("run context requires a PreparedRun")
        validate_environment_snapshots(prepared, environment_snapshots)
        _validate_provider_collection(external_providers)
        if type(safety) is not tuple or any(not is_definition_handle(item) for item in safety):
            raise TypeError("run context safety must be an immutable definition-handle tuple")
        if any(
            (item.identity.kind if type(item) is DefinitionRef else item.kind)
            is not ResourceKind.SAFETY_PROFILE
            for item in safety
        ):
            raise ValueError("run context safety handles must identify SafetyProfile")
        self._prepared = prepared
        self._run = RunScope(run_id=prepared.invocation.run_id)
        self._snapshots = environment_snapshots
        self._clock = clock
        self._generator = generator
        self._secure_generator = secure_generator
        self._file_provider = file_provider
        self._external_providers = external_providers
        self._safety = safety
        self._yaml_decoder = yaml_decoder
        self._cancellation = RunCancellation()
        self._registry = SensitiveValueRegistry(self._run)
        self._providers = ProviderRuntime(self._cancellation, self._registry)
        self._resolver = ExpressionResolver(self)
        self._file_cache: SingleFlightCache[_FileKey, ProviderOutcome] = SingleFlightCache()
        self._external_cache: SingleFlightCache[_ExternalKey, ProviderOutcome] = SingleFlightCache()
        self._generation_cache: SingleFlightCache[_GenerationKey, ProviderOutcome] = (
            SingleFlightCache()
        )
        self._decode_cache: SingleFlightCache[_DecodeKey, ProviderOutcome] = SingleFlightCache()
        self._close_task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def run(self) -> RunScope:
        """Return this context's exact run coordinate."""
        return self._run

    @property
    def cancellation(self) -> RunCancellation:
        """Return the explicit run-owned cancellation signal."""
        return self._cancellation

    async def resolve(self, expression: ValueExpression, target: ResolutionTarget) -> _Result:
        """Resolve an expression without permitting ambient state or inferred conversion."""
        self._validate_call(expression, target)
        return await self._resolver.resolve(expression, target)

    async def resolve_for_wire(
        self,
        expression: ValueExpression,
        target: ResolutionTarget,
    ) -> WireResolution:
        """Resolve a wire value and atomically pair it with a sanitizer-ready snapshot."""
        result = await self.resolve(expression, target)
        if type(result) is not Present:
            return result
        snapshot = await self._registry.snapshot()
        return ResolvedForWire(result=result, sanitization=snapshot)

    async def resolve_external(
        self,
        source: ExternalProviderSource,
        target: ResolutionTarget,
    ) -> _Result:
        """Resolve an internally registered future provider source at its declared scope."""
        self._validate_open_target(target)
        if type(source) is not ExternalProviderSource:
            raise TypeError("external resolution requires an ExternalProviderSource")
        production_scope = concrete_scope(source.scope_kind, target.scope)
        if production_scope is None:
            return self._providers.error(
                source=resolution_source(
                    symbol=source.symbol,
                    provider=source.provider,
                    configuration=source.configuration,
                    scope=target.scope,
                    namespace=source.namespace,
                ),
                target=target,
                code=ResolutionErrorCode.INVALID_SOURCE,
            )
        provider = next(
            (
                candidate
                for candidate in self._external_providers
                if candidate.identity == source.provider
            ),
            None,
        )
        runtime_source = resolution_source(
            symbol=source.symbol,
            provider=source.provider,
            configuration=source.configuration,
            scope=production_scope,
            namespace=source.namespace,
        )
        if provider is None:
            return self._providers.error(
                source=runtime_source,
                target=target,
                code=ResolutionErrorCode.INVALID_SOURCE,
            )
        request = ExternalValueRequest(
            source=source,
            scope=production_scope,
            policy=self._prepared.authority.policy,
            safety=self._safety,
        )
        outcome = await self._providers.call(
            self._external_cache,
            (source, production_scope),
            lambda: provider.read(request),
            sensitive_origin=(
                taint_origin("provider", source.symbol) if source.sensitive else None
            ),
        )
        return await self._providers.result(
            outcome,
            source=runtime_source,
            target=target,
        )

    async def generate(self, spec: GenerationSpec, target: ResolutionTarget) -> _Result:
        """Generate once per declared scope, including one shared value per comparison cell."""
        self._validate_open_target(target)
        if type(spec) is not GenerationSpec:
            raise TypeError("generation requires a GenerationSpec")
        production_scope = concrete_scope(spec.scope_kind, target.scope)
        provider = self._secure_generator if spec.secure else self._generator
        runtime_source = resolution_source(
            symbol=spec.symbol,
            provider=provider.identity if provider is not None else self._generator.identity,
            configuration=spec.configuration,
            scope=production_scope or target.scope,
            namespace=target.namespace,
        )
        if production_scope is None or provider is None:
            return self._providers.error(
                source=runtime_source,
                target=target,
                code=ResolutionErrorCode.INVALID_SOURCE,
            )
        if self._cancellation.cancelled:
            return self._providers.error(
                source=runtime_source,
                target=target,
                code=ResolutionErrorCode.CANCELLED,
            )

        async def generate_once() -> ProviderOutcome:
            request = GenerationRequest(
                spec=spec,
                scope=production_scope,
                instant=self._clock.now(),
            )
            return await provider.generate(request)

        outcome = await self._providers.call(
            self._generation_cache,
            (spec, production_scope),
            generate_once,
            sensitive_origin=(taint_origin("generated", spec.symbol) if spec.sensitive else None),
        )
        return await self._providers.result(
            outcome,
            source=runtime_source,
            target=target,
        )

    def cancel(self, source: CancellationSource) -> None:
        """Request first-wins run cancellation without cancelling arbitrary callers."""
        self._cancellation.cancel(source)

    async def sanitization_snapshot(self) -> SanitizationSnapshot:
        """Return all sensitive values retained so far without revealing them."""
        if self._closed:
            raise RuntimeError("run context is closed")
        return await self._registry.snapshot()

    async def close(self) -> None:
        """Finish one shared cleanup before propagating cancellation to any caller."""
        task = self._close_task
        if task is None:
            self._closed = True
            task = asyncio.create_task(self._finish_close())
            self._close_task = task

        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True

        task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _finish_close(self) -> None:
        """Drop all run-owned live values in an independently scheduled cleanup task."""
        await asyncio.gather(
            self._file_cache.close(),
            self._external_cache.close(),
            self._generation_cache.close(),
            self._decode_cache.close(),
        )
        await self._registry.close()
        if type(self._generator) is DeterministicGenerator:
            self._generator.close()

    async def __aenter__(self) -> RunContext:
        """Enter the run-owned async lifetime."""
        if self._closed:
            raise RuntimeError("run context is closed")
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close all run-owned state regardless of terminal outcome."""
        del exc_info
        await self.close()

    @property
    def resolution_cancelled(self) -> bool:
        """Expose cancellation to the provider-independent expression resolver."""
        return self._cancellation.cancelled

    async def register_resolution(
        self,
        result: Present[ResolvedValue],
        target: ResolutionTarget,
    ) -> None:
        """Retain one resolved sensitive value until this run closes."""
        await self._registry.register(result, symbol=target.symbol, scope=target.scope)

    async def resolve_lookup(
        self,
        expression: LookupExpr,
        target: ResolutionTarget,
        stack: tuple[_LookupToken, ...],
    ) -> _Result:
        """Resolve one namespace dependency against this run's immutable snapshots."""
        dependency = expression.dependency
        token = (dependency.namespace, dependency.symbol, target.scope)
        provider = NAMESPACE_PROVIDER
        if token in stack:
            return self._resolver.error(expression, target, ResolutionErrorCode.CYCLE, provider)
        if dependency.namespace is ValueNamespace.ENV:
            return await self._environment_lookup(expression, target)
        if dependency.namespace is ValueNamespace.MATRIX:
            provider = MATRIX_PROVIDER
            found = matrix_binding(target.cell_inputs, dependency.symbol)
            production_scope = concrete_scope(dependency.available_at, target.scope)
            if found is None or production_scope is None:
                return self._resolver.unavailable(expression, target, provider)
            bound_expression, _ = found
        elif dependency.namespace in {
            ValueNamespace.VARIABLES,
            ValueNamespace.SECRETS,
            ValueNamespace.PARAMETERS,
        }:
            found_binding = namespace_binding(
                self._prepared,
                dependency.namespace,
                dependency.symbol,
                target.scope,
            )
            if found_binding is None:
                return self._resolver.unavailable(expression, target, provider)
            bound_expression = found_binding.expression
            production_scope = found_binding.production_scope
        else:
            return self._resolver.unavailable(expression, target, provider)
        source_target = ResolutionTarget(
            symbol=dependency.symbol,
            scope=production_scope,
            namespace=dependency.namespace,
            cell_inputs=_scope_cell_inputs(target, production_scope),
        )
        resolved = await self._resolver.resolve(bound_expression, source_target, (*stack, token))
        if type(resolved) is Present:
            value = resolved.value
            if dependency.namespace is ValueNamespace.SECRETS:
                value = seal(value, taint_origin("secret", dependency.symbol))
            return self._resolver.present(expression, target, value, provider)
        return resolved

    async def _environment_lookup(
        self,
        expression: LookupExpr,
        target: ResolutionTarget,
    ) -> _Result:
        environment = environment_scope(target.scope)
        if environment is None:
            return self._resolver.error(
                expression,
                target,
                ResolutionErrorCode.INVALID_SOURCE,
                ENV_PROVIDER,
            )
        snapshot = environment_snapshot(self._snapshots, environment)
        entry = next(
            (item for item in snapshot.entries if item.symbol == expression.dependency.symbol),
            None,
        )
        if entry is None:
            return self._resolver.unavailable(expression, target, snapshot.provider)
        return self._resolver.present(
            expression,
            target,
            entry.value,
            snapshot.provider,
            environment,
        )

    async def resolve_file(
        self,
        expression: FileExpr,
        target: ResolutionTarget,
        stack: tuple[_LookupToken, ...],
    ) -> _Result:
        """Resolve one policy-bound file expression through the injected adapter."""
        path_result = await self._resolver.resolve(expression.path, target, stack)
        if type(path_result) is not Present:
            return path_result
        path = path_result.value
        if is_sensitive_value(path):
            if path.payload_type is not JsonString:
                return self._resolver.error(
                    expression, target, ResolutionErrorCode.INVALID_SOURCE, NAMESPACE_PROVIDER
                )
            typed_path: JsonString | SensitiveValue[JsonString] = cast(
                "SensitiveValue[JsonString]", path
            )
        elif type(path) is JsonString:
            typed_path = path
        else:
            return self._resolver.error(
                expression, target, ResolutionErrorCode.INVALID_SOURCE, NAMESPACE_PROVIDER
            )
        provider = self._file_provider
        if provider is None:
            return self._resolver.error(
                expression, target, ResolutionErrorCode.INVALID_SOURCE, NAMESPACE_PROVIDER
            )
        request = FileReadRequest(
            path=typed_path,
            symbol=target.symbol,
            scope=target.scope,
            configuration=expression.provenance,
            policy=self._prepared.authority.policy,
            safety=self._safety,
            trim=expression.trim,
            maximum_bytes=expression.maximum_bytes,
        )
        outcome = await self._providers.call(
            self._file_cache,
            (expression, target.scope),
            lambda: provider.read(request),
            sensitive_origin=(
                taint_origin("file", target.symbol) if is_sensitive_value(typed_path) else None
            ),
        )
        source = resolution_source(
            symbol=target.symbol,
            provider=provider.identity,
            configuration=expression.provenance,
            scope=target.scope,
            namespace=target.namespace,
        )
        result = await self._providers.result(
            outcome,
            source=source,
            target=target,
        )
        if type(result) is not Present or expression.conversion is None:
            return result
        return await self._resolver.convert_result(
            result,
            expression.conversion,
            expression,
            target,
            provider.identity,
        )

    async def decode_yaml(
        self,
        result: Present[ResolvedValue],
        expression: ValueExpression,
        target: ResolutionTarget,
    ) -> _Result:
        """Delegate explicit YAML conversion without importing a parser into core."""
        decoder = self._yaml_decoder
        if decoder is None:
            return self._resolver.error(
                expression,
                target,
                ResolutionErrorCode.CONVERSION_FAILED,
                CONVERSION_PROVIDER,
            )
        document = result.value
        if is_sensitive_value(document):
            if document.payload_type not in {JsonString, BytesValue}:
                return self._resolver.error(
                    expression,
                    target,
                    ResolutionErrorCode.CONVERSION_FAILED,
                    decoder.identity,
                )
        elif type(document) not in {JsonString, BytesValue}:
            return self._resolver.error(
                expression,
                target,
                ResolutionErrorCode.CONVERSION_FAILED,
                decoder.identity,
            )
        request = YamlDecodeRequest(
            document=cast("StructuredDocument", document),
            symbol=target.symbol,
            scope=target.scope,
            configuration=expression.provenance,
        )
        outcome = await self._providers.call(
            self._decode_cache,
            (expression, target.scope),
            lambda: decoder.decode(request),
            sensitive_origin=(
                taint_origin("yaml", target.symbol) if is_sensitive_value(document) else None
            ),
        )
        source = resolution_source(
            symbol=target.symbol,
            provider=decoder.identity,
            configuration=expression.provenance,
            scope=target.scope,
            namespace=target.namespace,
        )
        decoded = await self._providers.result(outcome, source=source, target=target)
        if type(decoded) is not Present:
            return decoded
        value = decoded.value
        payload = (
            reveal(cast("SensitiveValue[ResolvedValue]", value))
            if is_sensitive_value(value)
            else value
        )
        if not is_json_value(payload):
            return self._resolver.error(
                expression,
                target,
                ResolutionErrorCode.CONVERSION_FAILED,
                decoder.identity,
            )
        return decoded

    def _validate_call(self, expression: object, target: ResolutionTarget) -> None:
        if not is_value_expression(expression):
            raise TypeError("resolution requires a ValueExpression")
        self._validate_open_target(target)

    def _validate_open_target(self, target: ResolutionTarget) -> None:
        if self._closed:
            raise RuntimeError("run context is closed")
        if type(target) is not ResolutionTarget:
            raise TypeError("resolution requires a ResolutionTarget")
        if run_scope_of(target.scope) != self._run:
            raise ValueError("resolution target belongs to another run")


def _validate_provider_collection(providers: tuple[ExternalValueProvider, ...]) -> None:
    if type(providers) is not tuple:
        raise TypeError("external providers must be an immutable tuple")
    identities = tuple(provider.identity for provider in providers)
    if len(identities) != len(set(identities)):
        raise ValueError("external provider identities must be unique")


def _scope_cell_inputs(
    target: ResolutionTarget,
    scope: RuntimeScope,
) -> CellInputs | None:
    """Retain Matrix inputs only for scopes carrying the same cell coordinate."""
    try:
        return ResolutionTarget(
            symbol=target.symbol,
            scope=scope,
            cell_inputs=target.cell_inputs,
        ).cell_inputs
    except ValueError:
        return None
