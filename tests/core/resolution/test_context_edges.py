"""Failure paths and closed-union branches of the run-scoped resolver."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any
from typing import cast
from uuid import UUID

import msgspec
import pytest

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
from comparo.core.domain.expressions import LookupExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ObjectExpressionMember
from comparo.core.domain.expressions import OptionalExpr
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.provenance import ValueProviderIdentity
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.values import Blocked
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import Error
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import Missing
from comparo.core.domain.values import Present
from comparo.core.domain.values import ResolutionBlockCode
from comparo.core.domain.values import ResolutionErrorCode
from comparo.core.domain.values import Unavailable
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution.context import RunContext
from comparo.core.resolution.generation import DeterministicGenerator
from comparo.core.resolution.models import EnvironmentSnapshotEntry
from comparo.core.resolution.models import ExternalProviderSource
from comparo.core.resolution.models import ExternalValueRequest
from comparo.core.resolution.models import FileReadRequest
from comparo.core.resolution.models import GenerationOperation
from comparo.core.resolution.models import GenerationRequest
from comparo.core.resolution.models import GenerationSpec
from comparo.core.resolution.models import ProviderBlocked
from comparo.core.resolution.models import ProviderOutcome
from comparo.core.resolution.models import ProviderPresent
from comparo.core.resolution.models import ResolutionTarget
from comparo.core.resolution.models import YamlDecodeRequest
from tests.core.planning.conftest import policy_definition
from tests.core.resolution.conftest import configuration_provenance
from tests.core.resolution.conftest import execution_scope
from tests.core.resolution.conftest import matrix_inputs
from tests.core.resolution.conftest import snapshots
from tests.core.resolution.test_context import FixedClock
from tests.core.resolution.test_context import _seed
from tests.core.resolution.test_scopes_and_snapshots import _coordinates


def _run[ResultT](awaitable: Coroutine[Any, Any, ResultT]) -> ResultT:
    return asyncio.run(awaitable)


class ConfigurableProvider:
    """One provider fake usable through file, external, and generator ports."""

    def __init__(self, outcome: object, *, name: str = "configurable") -> None:
        """Store an outcome, exception, or cancellation sentinel."""
        self.outcome = outcome
        self.name = name
        self.calls = 0

    @property
    def identity(self) -> ValueProviderIdentity:
        """Return a stable identity chosen per fake."""
        return ValueProviderIdentity(namespace="test", name=self.name, version="1")

    async def _outcome(self) -> ProviderOutcome:
        self.calls += 1
        if type(self.outcome) is RuntimeError:
            raise self.outcome
        if self.outcome == "cancel":
            raise asyncio.CancelledError
        await asyncio.sleep(0)
        return cast("ProviderOutcome", self.outcome)

    async def read(self, request: FileReadRequest | ExternalValueRequest) -> ProviderOutcome:
        """Return the configured read result."""
        del request
        return await self._outcome()

    async def generate(self, request: GenerationRequest) -> ProviderOutcome:
        """Return the configured generation result."""
        del request
        return await self._outcome()

    async def decode(self, request: YamlDecodeRequest) -> ProviderOutcome:
        """Return the configured YAML decoding result."""
        del request
        return await self._outcome()


class ExplodingClock:
    """A clock adapter that fails without exposing its exception."""

    def now(self) -> UtcInstant:
        """Raise at the adapter boundary."""
        raise RuntimeError("payload-like clock detail")


def _target(prepared: PreparedRun) -> ResolutionTarget:
    return ResolutionTarget(
        symbol=SymbolPath(segments=("value",)),
        scope=execution_scope(prepared, 0),
        cell_inputs=matrix_inputs(prepared),
    )


def _context(
    prepared: PreparedRun,
    *,
    clock: FixedClock | ExplodingClock | None = None,
    generator: DeterministicGenerator | ConfigurableProvider | None = None,
    secure_generator: ConfigurableProvider | None = None,
    file_provider: ConfigurableProvider | None = None,
    external: tuple[ConfigurableProvider, ...] = (),
    safety: tuple[DefinitionRef, ...] = (),
    yaml_decoder: ConfigurableProvider | None = None,
) -> RunContext:
    return RunContext(
        prepared=prepared,
        environment_snapshots=snapshots(prepared),
        clock=clock or FixedClock(),
        generator=generator or DeterministicGenerator(_seed()),
        secure_generator=secure_generator,
        file_provider=file_provider,
        external_providers=external,
        safety=safety,
        yaml_decoder=yaml_decoder,
    )


def _literal(value: JsonString | BytesValue) -> LiteralExpr:
    return LiteralExpr(value=value, provenance=configuration_provenance())


def _duration() -> ConvertExpr:
    return ConvertExpr(
        source=_literal(JsonString(value="1ns")),
        target=ConversionType.DURATION,
        provenance=configuration_provenance(),
    )


def _lookup(namespace: ValueNamespace, name: str, scope: ValueScopeKind) -> LookupExpr:
    return LookupExpr(
        dependency=ExpressionDependency(
            namespace=namespace,
            symbol=SymbolPath(segments=(name,)),
            available_at=scope,
        ),
        provenance=configuration_provenance(),
    )


def _file(path: object = JsonString(value="value")) -> FileExpr:
    return FileExpr(
        path=_literal(cast("JsonString", path)),
        conversion=None,
        trim=FileTrim.NONE,
        provenance=configuration_provenance(),
    )


def _with_binding(
    prepared: PreparedRun,
    *,
    namespace: ValueNamespace,
    name: str,
    expression: object,
) -> PreparedRun:
    object_expression = ObjectExpr(
        members=(
            ObjectExpressionMember(
                name=name,
                expression=cast("Any", expression),
            ),
        ),
        provenance=configuration_provenance(),
    )
    layer = prepared.effective.value_layers[0]
    changes: dict[str, object] = {"variables": None, "secrets": None, "parameters": None}
    changes[namespace.value] = object_expression
    replacement = msgspec.structs.replace(layer, **changes)
    return msgspec.structs.replace(
        prepared,
        effective=msgspec.structs.replace(prepared.effective, value_layers=(replacement,)),
    )


def test_context_constructor_and_call_boundaries_fail_closed(prepared_run: PreparedRun) -> None:
    provider = ConfigurableProvider(ProviderPresent(value=JsonString(value="x")))
    with pytest.raises(TypeError, match="PreparedRun"):
        RunContext(
            prepared=cast("PreparedRun", object()),
            environment_snapshots=(),
            clock=FixedClock(),
            generator=provider,
        )
    with pytest.raises(TypeError, match="safety"):
        RunContext(
            prepared=prepared_run,
            environment_snapshots=snapshots(prepared_run),
            clock=FixedClock(),
            generator=provider,
            safety=cast("Any", [policy_definition()]),
        )
    with pytest.raises(ValueError, match="SafetyProfile"):
        RunContext(
            prepared=prepared_run,
            environment_snapshots=snapshots(prepared_run),
            clock=FixedClock(),
            generator=provider,
            safety=(policy_definition(),),
        )
    with pytest.raises(TypeError, match="immutable tuple"):
        RunContext(
            prepared=prepared_run,
            environment_snapshots=snapshots(prepared_run),
            clock=FixedClock(),
            generator=provider,
            external_providers=cast("Any", [provider]),
        )
    with pytest.raises(ValueError, match="unique"):
        _context(prepared_run, external=(provider, provider))

    async def scenario() -> None:
        context = _context(prepared_run, generator=provider)
        assert context.run.run_id == prepared_run.invocation.run_id
        with pytest.raises(TypeError, match="ValueExpression"):
            await context.resolve(cast("Any", object()), _target(prepared_run))
        with pytest.raises(TypeError, match="ResolutionTarget"):
            await context.resolve(_literal(JsonString(value="x")), cast("Any", object()))
        other_target = msgspec.structs.replace(
            _target(prepared_run),
            scope=msgspec.structs.replace(
                execution_scope(prepared_run, 0).environment,
                run=RunScope(
                    run_id=msgspec.structs.replace(
                        prepared_run.invocation.run_id,
                        value=UUID(int=999),
                    )
                ),
            ),
            cell_inputs=None,
        )
        with pytest.raises(ValueError, match="another run"):
            await context.resolve(_literal(JsonString(value="x")), other_target)
        missing = await context.resolve_for_wire(
            _lookup(ValueNamespace.VARIABLES, "absent", ValueScopeKind.RUN),
            _target(prepared_run),
        )
        assert type(missing) is Unavailable
        await context.close()
        with pytest.raises(RuntimeError, match="closed"):
            await context.sanitization_snapshot()
        with pytest.raises(RuntimeError, match="closed"):
            await context.__aenter__()

    _run(scenario())


def test_environment_matrix_steps_cycle_and_nonpresent_binding_paths(
    prepared_run: PreparedRun,
) -> None:
    selected = snapshots(prepared_run)
    entry = EnvironmentSnapshotEntry(
        symbol=SymbolPath(segments=("TOKEN",)),
        value=JsonString(value="snapshot"),
        configuration=configuration_provenance(),
    )
    selected = (msgspec.structs.replace(selected[0], entries=(entry,)), selected[1])
    context = RunContext(
        prepared=prepared_run,
        environment_snapshots=selected,
        clock=FixedClock(),
        generator=DeterministicGenerator(_seed()),
    )

    async def scenario() -> None:
        found = await context.resolve(
            _lookup(ValueNamespace.ENV, "TOKEN", ValueScopeKind.ENVIRONMENT),
            _target(prepared_run),
        )
        assert type(found) is Present
        absent = await context.resolve(
            _lookup(ValueNamespace.ENV, "MISSING", ValueScopeKind.ENVIRONMENT),
            _target(prepared_run),
        )
        assert type(absent) is Unavailable
        _, cell, _, _, execution, step, _, _ = _coordinates(prepared_run)
        cell_target = ResolutionTarget(symbol=SymbolPath(segments=("value",)), scope=cell)
        invalid_env = await context.resolve(
            _lookup(ValueNamespace.ENV, "TOKEN", ValueScopeKind.ENVIRONMENT),
            cell_target,
        )
        assert type(invalid_env) is Error
        missing_matrix = await context.resolve(
            _lookup(ValueNamespace.MATRIX, "missing", ValueScopeKind.CELL),
            _target(prepared_run),
        )
        assert type(missing_matrix) is Unavailable
        step_target = ResolutionTarget(
            symbol=SymbolPath(segments=("value",)),
            scope=step,
            cell_inputs=matrix_inputs(prepared_run),
        )
        future_capture = await context.resolve(
            _lookup(ValueNamespace.STEPS, "capture", ValueScopeKind.STEP),
            step_target,
        )
        assert type(future_capture) is Unavailable
        del execution
        await context.close()

        cycle_expression = _lookup(ValueNamespace.VARIABLES, "cycle", ValueScopeKind.RUN)
        cyclic = _with_binding(
            prepared_run,
            namespace=ValueNamespace.VARIABLES,
            name="cycle",
            expression=cycle_expression,
        )
        cyclic_context = _context(cyclic)
        cycle = await cyclic_context.resolve(cycle_expression, _target(cyclic))
        assert type(cycle) is Error
        assert cycle.code is ResolutionErrorCode.CYCLE
        await cyclic_context.close()

        forwarding = _with_binding(
            prepared_run,
            namespace=ValueNamespace.VARIABLES,
            name="forward",
            expression=_lookup(ValueNamespace.VARIABLES, "absent", ValueScopeKind.RUN),
        )
        forwarding_context = _context(forwarding)
        forwarded = await forwarding_context.resolve(
            _lookup(ValueNamespace.VARIABLES, "forward", ValueScopeKind.RUN),
            _target(forwarding),
        )
        assert type(forwarded) is Unavailable
        await forwarding_context.close()

    _run(scenario())


def test_file_path_provider_and_composite_failures_are_typed(prepared_run: PreparedRun) -> None:
    absent = _lookup(ValueNamespace.VARIABLES, "absent", ValueScopeKind.RUN)
    no_provider = _context(prepared_run)

    async def scenario() -> None:
        unavailable_path = FileExpr(
            path=absent,
            conversion=None,
            trim=FileTrim.NONE,
            provenance=configuration_provenance(),
        )
        assert (
            type(await no_provider.resolve(unavailable_path, _target(prepared_run))) is Unavailable
        )
        assert type(await no_provider.resolve(_file(), _target(prepared_run))) is Error
        await no_provider.close()

        bytes_binding = _with_binding(
            prepared_run,
            namespace=ValueNamespace.VARIABLES,
            name="path",
            expression=_literal(BytesValue(value=b"not-text")),
        )
        bytes_context = _context(bytes_binding)
        invalid_path = msgspec.structs.replace(
            _file(),
            path=_lookup(ValueNamespace.VARIABLES, "path", ValueScopeKind.RUN),
        )
        assert type(await bytes_context.resolve(invalid_path, _target(bytes_binding))) is Error
        await bytes_context.close()

        wrong_sealed = _with_binding(
            prepared_run,
            namespace=ValueNamespace.SECRETS,
            name="path",
            expression=_literal(BytesValue(value=b"not-text")),
        )
        sealed_context = _context(wrong_sealed)
        secret_path = msgspec.structs.replace(
            _file(),
            path=_lookup(ValueNamespace.SECRETS, "path", ValueScopeKind.RUN),
        )
        assert type(await sealed_context.resolve(secret_path, _target(wrong_sealed))) is Error
        await sealed_context.close()

        invalid_array = ArrayExpr(
            items=(_duration(),),
            provenance=configuration_provenance(),
        )
        valid_array = msgspec.structs.replace(
            invalid_array,
            items=(_literal(JsonString(value="value")),),
        )
        invalid_object = ObjectExpr(
            members=(
                ObjectExpressionMember(
                    name="duration",
                    expression=_duration(),
                ),
            ),
            provenance=configuration_provenance(),
        )
        composite_context = _context(prepared_run)
        assert type(await composite_context.resolve(valid_array, _target(prepared_run))) is Present
        for expression in (invalid_array, invalid_object):
            result = await composite_context.resolve(expression, _target(prepared_run))
            assert type(result) is Error
            assert result.code is ResolutionErrorCode.CONVERSION_FAILED
        unavailable_array = msgspec.structs.replace(invalid_array, items=(absent,))
        unavailable_object = msgspec.structs.replace(
            invalid_object,
            members=(ObjectExpressionMember(name="absent", expression=absent),),
        )
        assert (
            type(await composite_context.resolve(unavailable_array, _target(prepared_run)))
            is Unavailable
        )
        assert (
            type(await composite_context.resolve(unavailable_object, _target(prepared_run)))
            is Unavailable
        )
        await composite_context.close()

    _run(scenario())


def test_fallback_reframes_every_terminal_outcome_and_optional_present(
    prepared_run: PreparedRun,
) -> None:
    absent = _lookup(ValueNamespace.VARIABLES, "absent", ValueScopeKind.RUN)
    invalid = ConvertExpr(
        source=_literal(JsonString(value="not-a-number")),
        target=ConversionType.INTEGER,
        provenance=configuration_provenance(),
    )
    optional_missing = OptionalExpr(source=absent, provenance=configuration_provenance())
    optional_present = OptionalExpr(
        source=_literal(JsonString(value="present")),
        provenance=configuration_provenance(),
    )
    blocked_provider = ConfigurableProvider(
        ProviderBlocked(code=ResolutionBlockCode.PATH_DENIED),
        name="blocked-file",
    )

    async def scenario() -> None:
        context = _context(prepared_run, file_provider=blocked_provider)
        cases = (
            (
                FallbackExpr(
                    sources=(_literal(JsonString(value="first")),),
                    provenance=configuration_provenance(),
                ),
                Present,
            ),
            (
                FallbackExpr(
                    sources=(absent, optional_missing),
                    provenance=configuration_provenance(),
                ),
                Missing,
            ),
            (
                FallbackExpr(sources=(absent, invalid), provenance=configuration_provenance()),
                Error,
            ),
            (
                FallbackExpr(sources=(absent, _file()), provenance=configuration_provenance()),
                Blocked,
            ),
            (
                FallbackExpr(sources=(absent,), provenance=configuration_provenance()),
                Unavailable,
            ),
        )
        for expression, expected in cases:
            result = await context.resolve(expression, _target(prepared_run))
            assert type(result) is expected
            assert result.provenance.steps
        present_result = await context.resolve(optional_present, _target(prepared_run))
        assert type(present_result) is Present
        conversion_unavailable = ConvertExpr(
            source=absent,
            target=ConversionType.STRING,
            provenance=configuration_provenance(),
        )
        assert (
            type(await context.resolve(conversion_unavailable, _target(prepared_run)))
            is Unavailable
        )
        await context.close()

    _run(scenario())


def test_interpolation_whole_value_and_invalid_dynamic_text(prepared_run: PreparedRun) -> None:
    whole = InterpolationExpr(
        mode=InterpolationMode.WHOLE_VALUE,
        segments=(ExpressionSegment(expression=_literal(JsonString(value="whole"))),),
        provenance=configuration_provenance(),
    )
    absent_whole = msgspec.structs.replace(
        whole,
        segments=(
            ExpressionSegment(
                expression=_lookup(ValueNamespace.VARIABLES, "absent", ValueScopeKind.RUN)
            ),
        ),
    )
    invalid_bytes = _with_binding(
        prepared_run,
        namespace=ValueNamespace.VARIABLES,
        name="bytes",
        expression=_literal(BytesValue(value=b"\xff")),
    )
    embedded = InterpolationExpr(
        mode=InterpolationMode.EMBEDDED_TEXT,
        segments=(
            ExpressionSegment(
                expression=_lookup(ValueNamespace.VARIABLES, "bytes", ValueScopeKind.RUN)
            ),
        ),
        provenance=configuration_provenance(),
    )
    unavailable_embedded = msgspec.structs.replace(
        embedded,
        segments=(
            ExpressionSegment(
                expression=_lookup(ValueNamespace.VARIABLES, "absent", ValueScopeKind.RUN)
            ),
        ),
    )

    async def scenario() -> None:
        context = _context(prepared_run)
        assert type(await context.resolve(whole, _target(prepared_run))) is Present
        assert type(await context.resolve(absent_whole, _target(prepared_run))) is Unavailable
        assert (
            type(await context.resolve(unavailable_embedded, _target(prepared_run))) is Unavailable
        )
        await context.close()
        invalid_context = _context(invalid_bytes)
        result = await invalid_context.resolve(embedded, _target(invalid_bytes))
        assert type(result) is Error
        assert result.code is ResolutionErrorCode.CONVERSION_FAILED
        await invalid_context.close()

    _run(scenario())


def test_external_generation_and_provider_adapter_failures(prepared_run: PreparedRun) -> None:
    missing_source = ExternalProviderSource(
        provider=ValueProviderIdentity(namespace="test", name="missing", version="1"),
        symbol=SymbolPath(segments=("token",)),
        scope_kind=ValueScopeKind.ATTEMPT,
        configuration=configuration_provenance(),
    )
    _, cell, _, _, _, _, attempt, _ = _coordinates(prepared_run)
    attempt_target = msgspec.structs.replace(_target(prepared_run), scope=attempt)
    cell_target = ResolutionTarget(symbol=SymbolPath(segments=("value",)), scope=cell)
    exploding = ConfigurableProvider(RuntimeError("secret exception"), name="exploding")
    invalid = ConfigurableProvider(object(), name="invalid")
    cancelled = ConfigurableProvider("cancel", name="cancelled")
    blocked = ConfigurableProvider(
        ProviderBlocked(code=ResolutionBlockCode.SOURCE_DENIED), name="blocked"
    )

    async def scenario() -> None:
        context = _context(prepared_run, external=(exploding, invalid, cancelled, blocked))
        with pytest.raises(TypeError, match="ExternalProviderSource"):
            await context.resolve_external(cast("Any", object()), attempt_target)
        invalid_scope = await context.resolve_external(missing_source, cell_target)
        assert type(invalid_scope) is Error
        missing = await context.resolve_external(missing_source, attempt_target)
        assert type(missing) is Error
        for provider in (exploding, invalid, blocked):
            source = msgspec.structs.replace(missing_source, provider=provider.identity)
            result = await context.resolve_external(source, attempt_target)
            if provider is blocked:
                assert type(result) is Blocked
            else:
                assert type(result) is Error
                assert "secret exception" not in repr(result)
        cancelled_source = msgspec.structs.replace(missing_source, provider=cancelled.identity)
        with pytest.raises(asyncio.CancelledError):
            await context.resolve_external(cancelled_source, attempt_target)

        context.cancel(CancellationSource.USER)
        cancelled_result = await context.resolve_external(
            msgspec.structs.replace(missing_source, provider=exploding.identity),
            attempt_target,
        )
        assert type(cancelled_result) is Error
        assert cancelled_result.code is ResolutionErrorCode.CANCELLED
        await context.close()

        closed_cache_context = _context(prepared_run, external=(exploding,))
        await closed_cache_context._external_cache.close()
        closed_cache_result = await closed_cache_context.resolve_external(
            msgspec.structs.replace(missing_source, provider=exploding.identity),
            attempt_target,
        )
        assert type(closed_cache_result) is Error
        assert closed_cache_result.code is ResolutionErrorCode.CANCELLED
        await closed_cache_context.close()

    _run(scenario())


def test_generation_invalid_scopes_secure_provider_clock_and_sensitive_cache(
    prepared_run: PreparedRun,
) -> None:
    spec = GenerationSpec(
        symbol=SymbolPath(segments=("generated",)),
        operation=GenerationOperation.HEX,
        scope_kind=ValueScopeKind.CELL,
        configuration=configuration_provenance(),
        length=PositiveCount(value=8),
    )

    async def scenario() -> None:
        context = _context(prepared_run)
        with pytest.raises(TypeError, match="GenerationSpec"):
            await context.generate(cast("Any", object()), _target(prepared_run))
        run_target = ResolutionTarget(
            symbol=SymbolPath(segments=("value",)),
            scope=RunScope(run_id=prepared_run.invocation.run_id),
        )
        invalid_scope = await context.generate(spec, run_target)
        assert type(invalid_scope) is Error
        secure = msgspec.structs.replace(spec, sensitive=True, secure=True)
        no_secure = await context.generate(secure, _target(prepared_run))
        assert type(no_secure) is Error
        context.cancel(CancellationSource.USER)
        cancelled = await context.generate(spec, _target(prepared_run))
        assert type(cancelled) is Error
        assert cancelled.code is ResolutionErrorCode.CANCELLED
        await context.close()

        clock_context = _context(prepared_run, clock=ExplodingClock())
        clock_failure = await clock_context.generate(spec, _target(prepared_run))
        assert type(clock_failure) is Error
        assert clock_failure.code is ResolutionErrorCode.PROVIDER_FAILED
        assert "payload-like" not in repr(clock_failure)
        await clock_context.close()

        secure_provider = ConfigurableProvider(
            ProviderPresent(value=JsonString(value="secure")), name="secure-generator"
        )
        secure_context = _context(prepared_run, secure_generator=secure_provider)
        generated = await secure_context.generate(secure, _target(prepared_run))
        assert type(generated) is Present
        assert type(generated.value) is SensitiveValue
        await secure_context.close()

    _run(scenario())


def test_public_secret_binding_is_tainted_once_and_safety_is_forwarded(
    prepared_run: PreparedRun,
) -> None:
    secret_prepared = _with_binding(
        prepared_run,
        namespace=ValueNamespace.SECRETS,
        name="token",
        expression=_literal(JsonString(value="secret")),
    )
    policy = policy_definition()
    safety = msgspec.structs.replace(
        policy,
        identity=msgspec.structs.replace(policy.identity, kind=ResourceKind.SAFETY_PROFILE),
    )

    async def scenario() -> None:
        file_provider = ConfigurableProvider(
            ProviderPresent(value=BytesValue(value=b"file")), name="secret-file"
        )
        context = _context(secret_prepared, safety=(safety,), file_provider=file_provider)
        result = await context.resolve(
            _lookup(ValueNamespace.SECRETS, "token", ValueScopeKind.RUN),
            _target(secret_prepared),
        )
        assert type(result) is Present
        assert type(result.value) is SensitiveValue
        assert len(result.value.origins) == 1
        secret_file = FileExpr(
            path=_lookup(ValueNamespace.SECRETS, "token", ValueScopeKind.RUN),
            conversion=None,
            trim=FileTrim.NONE,
            provenance=configuration_provenance(),
        )
        file_result = await context.resolve(secret_file, _target(secret_prepared))
        repeated = await context.resolve(secret_file, _target(secret_prepared))
        assert type(file_result) is Present
        assert type(repeated) is Present
        assert type(file_result.value) is SensitiveValue
        assert file_provider.calls == 1
        await context.close()

    _run(scenario())


def test_yaml_conversion_uses_injected_decoder_cache_and_preserves_taint(
    prepared_run: PreparedRun,
) -> None:
    decoder = ConfigurableProvider(
        ProviderPresent(value=JsonString(value="decoded")),
        name="safe-yaml",
    )
    expression = ConvertExpr(
        source=_literal(JsonString(value="key: value")),
        target=ConversionType.YAML,
        provenance=configuration_provenance(),
    )

    async def scenario() -> None:
        no_decoder = _context(prepared_run)
        missing = await no_decoder.resolve(expression, _target(prepared_run))
        assert type(missing) is Error
        assert missing.code is ResolutionErrorCode.CONVERSION_FAILED
        await no_decoder.close()

        context = _context(prepared_run, yaml_decoder=decoder)
        first = await context.resolve(expression, _target(prepared_run))
        second = await context.resolve(expression, _target(prepared_run))
        assert type(first) is Present
        assert first == second
        assert decoder.calls == 1
        invalid_input = ConvertExpr(
            source=_duration(),
            target=ConversionType.YAML,
            provenance=configuration_provenance(),
        )
        assert type(await context.resolve(invalid_input, _target(prepared_run))) is Error
        await context.close()

        invalid_decoder = ConfigurableProvider(
            ProviderPresent(value=Duration(nanoseconds=1)),
            name="invalid-yaml",
        )
        invalid_context = _context(prepared_run, yaml_decoder=invalid_decoder)
        assert type(await invalid_context.resolve(expression, _target(prepared_run))) is Error
        await invalid_context.close()

        blocked_decoder = ConfigurableProvider(
            ProviderBlocked(code=ResolutionBlockCode.SOURCE_DENIED),
            name="blocked-yaml",
        )
        blocked_context = _context(prepared_run, yaml_decoder=blocked_decoder)
        assert type(await blocked_context.resolve(expression, _target(prepared_run))) is Blocked
        await blocked_context.close()

        secret_prepared = _with_binding(
            prepared_run,
            namespace=ValueNamespace.SECRETS,
            name="yaml",
            expression=_literal(JsonString(value="key: secret")),
        )
        secret_expression = ConvertExpr(
            source=_lookup(ValueNamespace.SECRETS, "yaml", ValueScopeKind.RUN),
            target=ConversionType.YAML,
            provenance=configuration_provenance(),
        )
        secret_decoder = ConfigurableProvider(
            ProviderPresent(value=JsonString(value="decoded")),
            name="secret-yaml",
        )
        secret_context = _context(secret_prepared, yaml_decoder=secret_decoder)
        secret = await secret_context.resolve(secret_expression, _target(secret_prepared))
        assert type(secret) is Present
        assert type(secret.value) is SensitiveValue
        await secret_context.close()

        secret_duration = _with_binding(
            prepared_run,
            namespace=ValueNamespace.SECRETS,
            name="yaml",
            expression=_duration(),
        )
        secret_duration_context = _context(secret_duration, yaml_decoder=secret_decoder)
        invalid_secret_document = await secret_duration_context.resolve(
            secret_expression,
            _target(secret_duration),
        )
        assert type(invalid_secret_document) is Error
        await secret_duration_context.close()

    _run(scenario())
