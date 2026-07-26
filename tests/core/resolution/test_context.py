"""End-to-end behavior of the isolated run-scoped value context."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from decimal import Decimal
from typing import Any
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.expression_types import ConversionType
from comparo.core.domain.expression_types import FileTrim
from comparo.core.domain.expression_types import InterpolationMode
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
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Duration
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.provenance import ValueProviderIdentity
from comparo.core.domain.scopes import AttemptScope
from comparo.core.domain.scopes import ExecutionScope
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import Sensitivity
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_egress import CONFIGURATION_SECRET_MARKER
from comparo.core.domain.value_egress import ValueEgressSourceKind
from comparo.core.domain.value_egress import value_egress_origin
from comparo.core.domain.value_kinds import ValueKind
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import Error
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import Present
from comparo.core.domain.values import ResolutionErrorCode
from comparo.core.domain.values import Unavailable
from comparo.core.domain.values import UnavailableReason
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution import DeterministicGenerator
from comparo.core.resolution import ExternalProviderSource
from comparo.core.resolution import GenerationOperation
from comparo.core.resolution import GenerationSpec
from comparo.core.resolution import LiveValueResolver
from comparo.core.resolution import MaskedPresent
from comparo.core.resolution import MaskedSensitiveValue
from comparo.core.resolution import MaskedValueResolver
from comparo.core.resolution import ProviderError
from comparo.core.resolution import ProviderPresent
from comparo.core.resolution import ProviderUnavailable
from comparo.core.resolution import ResolutionTarget
from comparo.core.resolution import ResolvedForWire
from comparo.core.resolution import RunContext
from comparo.core.resolution import mask_resolution
from comparo.core.resolution.models import ExternalValueRequest
from comparo.core.resolution.models import FileReadRequest
from comparo.core.resolution.models import ProviderOutcome
from tests.core.resolution.conftest import configuration_provenance
from tests.core.resolution.conftest import execution_scope
from tests.core.resolution.conftest import matrix_inputs
from tests.core.resolution.conftest import snapshots

_ACCESS = SensitiveValueAccess(purpose="resolution-test")


class FixedClock:
    """A deterministic injected run clock."""

    def now(self) -> UtcInstant:
        """Return a stable test instant."""
        return UtcInstant(unix_nanoseconds=123)


class FakeFileProvider:
    """A counting async raw-byte file adapter."""

    def __init__(self, outcome: ProviderOutcome) -> None:
        """Store one safe outcome and initialize observations."""
        self.outcome = outcome
        self.calls = 0
        self.requests: list[FileReadRequest] = []

    @property
    def identity(self) -> ValueProviderIdentity:
        """Return a stable adapter identity."""
        return ValueProviderIdentity(namespace="test", name="file", version="1")

    async def read(self, request: FileReadRequest) -> ProviderOutcome:
        """Count a source read and yield once to exercise single-flight behavior."""
        self.calls += 1
        self.requests.append(request)
        await asyncio.sleep(0)
        return self.outcome


class RotatingProvider:
    """A counting provider whose values expose accidental cross-scope caching."""

    def __init__(self) -> None:
        """Initialize the read counter."""
        self.calls = 0

    @property
    def identity(self) -> ValueProviderIdentity:
        """Return a stable provider identity."""
        return ValueProviderIdentity(namespace="test", name="vault", version="1")

    async def read(self, request: ExternalValueRequest) -> ProviderOutcome:
        """Return a new sealed-generation candidate for each exact source read."""
        del request
        self.calls += 1
        await asyncio.sleep(0)
        return ProviderPresent(value=JsonString(value=f"rotation-{self.calls}"))


def _seed() -> SensitiveValue[BytesValue]:
    return SensitiveValue(
        BytesValue(value=b"deterministic-test-seed"),
        origins=(TaintOriginId(value="test/seed"),),
    )


def _target(prepared: PreparedRun, environment_index: int = 0) -> ResolutionTarget:
    return ResolutionTarget(
        symbol=SymbolPath(segments=("request", "value")),
        scope=execution_scope(prepared, environment_index),
        cell_inputs=matrix_inputs(prepared),
    )


def _context(
    prepared: PreparedRun,
    *,
    file_provider: FakeFileProvider | None = None,
    external_providers: tuple[RotatingProvider, ...] = (),
) -> RunContext:
    return RunContext(
        prepared=prepared,
        environment_snapshots=snapshots(prepared),
        clock=FixedClock(),
        generator=DeterministicGenerator(_seed()),
        file_provider=file_provider,
        external_providers=external_providers,
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


def _run[ResultT](awaitable: Coroutine[Any, Any, ResultT]) -> ResultT:
    return asyncio.run(awaitable)


def test_layered_namespace_matrix_and_secret_resolution(prepared_run: PreparedRun) -> None:
    async def scenario() -> None:
        async with _context(prepared_run) as context:
            target = _target(prepared_run)
            greeting = await context.resolve(
                _lookup(ValueNamespace.VARIABLES, "greeting", ValueScopeKind.RUN), target
            )
            region = await context.resolve(
                _lookup(ValueNamespace.VARIABLES, "region", ValueScopeKind.ENVIRONMENT), target
            )
            locale = await context.resolve(
                _lookup(ValueNamespace.MATRIX, "locale", ValueScopeKind.CELL), target
            )
            token = await context.resolve(
                _lookup(ValueNamespace.SECRETS, "token", ValueScopeKind.RUN), target
            )
            assert cast("Present[JsonString]", greeting).value == JsonString(value="hello")
            assert cast("Present[JsonString]", region).value == JsonString(value="staging")
            assert cast("Present[JsonString]", locale).value == JsonString(value="en")
            assert type(token) is Present
            assert type(token.value) is SensitiveValue
            assert token.value.reveal(access=_ACCESS) == JsonString(value="hidden")
            assert CONFIGURATION_SECRET_MARKER not in token.value.origins
            assert token.value.origins == (
                value_egress_origin(
                    ValueEgressSourceKind.SECRET,
                    SymbolPath(segments=("token",)),
                ),
            )

            marked_literal = SensitiveLiteralExpr(
                value=SensitiveValue(
                    JsonString(value="marked"),
                    origins=(CONFIGURATION_SECRET_MARKER,),
                ),
                provenance=configuration_provenance(),
            )
            outside_secrets = await context.resolve(marked_literal, target)
            assert type(outside_secrets) is Present
            assert type(outside_secrets.value) is SensitiveValue
            assert outside_secrets.value.origins == (CONFIGURATION_SECRET_MARKER,)
            assert (await context.sanitization_snapshot()).revision.value >= 1

    _run(scenario())


def test_live_and_masked_resolvers_share_resolution_without_secret_egress(
    prepared_run: PreparedRun,
) -> None:
    async def scenario() -> None:
        async with _context(prepared_run) as context:
            expression = _lookup(ValueNamespace.SECRETS, "token", ValueScopeKind.RUN)
            target = _target(prepared_run)

            live = await LiveValueResolver(context).resolve(expression, target)
            masked = await MaskedValueResolver(context).resolve(expression, target)

            assert type(live) is Present
            assert type(live.value) is SensitiveValue
            assert live.value.reveal(access=_ACCESS) == JsonString(value="hidden")
            assert type(masked) is MaskedPresent
            assert type(masked.value) is MaskedSensitiveValue
            assert masked.value.kind.value == "string"
            encoded = msgspec.json.encode(masked)
            assert b"hidden" not in encoded
            assert b"<masked>" in encoded
            assert "hidden" not in repr(masked)

    _run(scenario())


def test_masked_view_contracts_reject_invalid_payloads_and_engines(
    prepared_run: PreparedRun,
) -> None:
    origin = TaintOriginId(value="test/masked")
    valid = MaskedSensitiveValue(kind=ValueKind.STRING, origins=(origin,))
    cases: tuple[dict[str, object], ...] = (
        {"kind": ValueKind.UNKNOWN},
        {"kind": cast("ValueKind", object())},
        {"origins": ()},
        {"origins": cast("tuple[TaintOriginId, ...]", [origin])},
        {"origins": (cast("TaintOriginId", object()),)},
        {"origins": (origin, origin)},
        {"marker": "caller-owned"},
    )
    for changes in cases:
        with pytest.raises(ValueError, match="masked sensitive value"):
            msgspec.structs.replace(valid, **changes)

    async def scenario() -> None:
        async with _context(prepared_run) as context:
            target = _target(prepared_run)
            expression = LiteralExpr(
                value=JsonString(value="public"),
                provenance=configuration_provenance(),
            )
            present = cast("Present[JsonString]", await context.resolve(expression, target))
            public = cast("MaskedPresent", mask_resolution(present))
            assert public.sensitivity is Sensitivity.PUBLIC
            assert public.taint_origins == ()
            with pytest.raises(TypeError, match="resolved or explicitly masked"):
                MaskedPresent(value=cast("Any", object()), provenance=present.provenance)
            with pytest.raises(TypeError, match="ResolutionProvenance"):
                MaskedPresent(value=public.value, provenance=cast("Any", object()))

    _run(scenario())

    for resolver in (LiveValueResolver, MaskedValueResolver):
        with pytest.raises(TypeError, match="requires a ResolutionEngine"):
            resolver(cast("Any", object()))


def test_masking_preserves_public_values_and_closes_every_sensitive_value_kind(
    prepared_run: PreparedRun,
) -> None:
    values = (
        JsonNull(),
        JsonBoolean(value=True),
        JsonInteger(value=1),
        JsonDecimal(value=Decimal("1.5")),
        JsonString(value="secret"),
        JsonArray(items=(JsonString(value="item"),)),
        JsonObject(members=(JsonObjectMember(name="key", value=JsonString(value="value")),)),
        BytesValue(value=b"secret"),
        Duration(nanoseconds=1),
        ByteCount(value=1),
    )
    expected = (
        ValueKind.NULL,
        ValueKind.BOOLEAN,
        ValueKind.INTEGER,
        ValueKind.NUMBER,
        ValueKind.STRING,
        ValueKind.ARRAY,
        ValueKind.OBJECT,
        ValueKind.BYTES,
        ValueKind.DURATION,
        ValueKind.SIZE,
    )
    origin = TaintOriginId(value="test/closed-kind")

    async def scenario() -> None:
        async with _context(prepared_run) as context:
            target = _target(prepared_run)
            template = cast(
                "Present[Any]",
                await context.resolve(
                    LiteralExpr(
                        value=JsonString(value="template"),
                        provenance=configuration_provenance(),
                    ),
                    target,
                ),
            )
            for value, kind in zip(values, expected, strict=True):
                public_result = msgspec.structs.replace(template, value=value)
                public = cast("MaskedPresent", mask_resolution(public_result))
                assert public.value == value
                sensitive_result = msgspec.structs.replace(
                    public_result,
                    value=SensitiveValue(value, origins=(origin,)),
                )
                masked = cast("MaskedPresent", mask_resolution(sensitive_result))
                assert type(masked.value) is MaskedSensitiveValue
                assert masked.value.kind is kind
                assert masked.sensitivity is Sensitivity.SENSITIVE
                assert masked.taint_origins == (origin,)
                assert b"secret" not in msgspec.json.encode(masked)
            unavailable = await context.resolve(
                _lookup(ValueNamespace.VARIABLES, "absent", ValueScopeKind.RUN),
                target,
            )
            assert mask_resolution(unavailable) is unavailable

    _run(scenario())


def test_fallback_optional_composites_interpolation_and_explicit_conversion(
    prepared_run: PreparedRun,
) -> None:
    provenance = configuration_provenance()
    absent = _lookup(ValueNamespace.VARIABLES, "absent", ValueScopeKind.RUN)
    secret = SensitiveLiteralExpr(
        value=SensitiveValue(
            JsonString(value="s3cr3t"),
            origins=(TaintOriginId(value="test/secret"),),
        ),
        provenance=provenance,
    )
    fallback = FallbackExpr(
        sources=(absent, LiteralExpr(value=JsonString(value="42"), provenance=provenance)),
        provenance=provenance,
    )
    body = ObjectExpr(
        members=(
            ObjectExpressionMember(
                name="number",
                expression=ConvertExpr(
                    source=fallback,
                    target=ConversionType.INTEGER,
                    provenance=provenance,
                ),
            ),
            ObjectExpressionMember(
                name="omit",
                expression=OptionalExpr(source=absent, provenance=provenance),
            ),
            ObjectExpressionMember(
                name="message",
                expression=InterpolationExpr(
                    mode=InterpolationMode.EMBEDDED_TEXT,
                    segments=(
                        LiteralTextSegment(value="token="),
                        ExpressionSegment(expression=secret),
                    ),
                    provenance=provenance,
                ),
            ),
        ),
        provenance=provenance,
    )

    async def scenario() -> None:
        context = _context(prepared_run)
        wire = await context.resolve_for_wire(body, _target(prepared_run))
        assert type(wire) is ResolvedForWire
        assert type(wire.result.value) is SensitiveValue
        revealed = wire.result.value.reveal(access=_ACCESS)
        revealed_object = cast("JsonObject", revealed)
        assert tuple(member.name for member in revealed_object.members) == (
            "number",
            "message",
        )
        assert revealed_object.members[0].value == JsonInteger(value=42)
        assert revealed_object.members[1].value == JsonString(value="token=s3cr3t")
        assert wire.sanitization.entries[-1].value is wire.result.value
        await context.close()

    _run(scenario())


def test_file_reads_are_single_flight_converted_and_value_free_on_failure(
    prepared_run: PreparedRun,
) -> None:
    provenance = configuration_provenance()
    provider = FakeFileProvider(ProviderPresent(value=BytesValue(value=b" 123 \n")))
    expression = FileExpr(
        path=LiteralExpr(value=JsonString(value="fixtures/value.txt"), provenance=provenance),
        conversion=ConversionType.INTEGER,
        trim=FileTrim.BOTH,
        provenance=provenance,
    )

    async def scenario() -> None:
        context = _context(prepared_run, file_provider=provider)
        first, second = await asyncio.gather(
            context.resolve(expression, _target(prepared_run)),
            context.resolve(expression, _target(prepared_run)),
        )
        assert first == second
        assert cast("Present[JsonInteger]", first).value == JsonInteger(value=123)
        assert provider.calls == 1
        assert provider.requests[0].path == JsonString(value="fixtures/value.txt")
        failed = await context.resolve(
            msgspec.structs.replace(
                expression,
                path=LiteralExpr(value=JsonString(value="other"), provenance=provenance),
                conversion=ConversionType.BOOLEAN,
            ),
            _target(prepared_run),
        )
        assert type(failed) is Error
        assert failed.code is ResolutionErrorCode.CONVERSION_FAILED
        assert "123" not in repr(failed)
        await context.close()

    _run(scenario())


def test_attempt_scoped_provider_rotates_but_concurrent_reads_share(
    prepared_run: PreparedRun,
) -> None:
    provider = RotatingProvider()
    source = ExternalProviderSource(
        provider=provider.identity,
        symbol=SymbolPath(segments=("token",)),
        scope_kind=ValueScopeKind.ATTEMPT,
        configuration=configuration_provenance(),
        sensitive=True,
    )

    async def scenario() -> None:
        context = _context(prepared_run, external_providers=(provider,))
        base = _target(prepared_run)
        attempt_one = msgspec.structs.replace(
            base,
            scope=AttemptScope(
                execution=cast("ExecutionScope", base.scope),
                attempt_id=AttemptId(value=UUID(int=41)),
            ),
        )
        attempt_two = msgspec.structs.replace(
            attempt_one,
            scope=msgspec.structs.replace(
                cast("AttemptScope", attempt_one.scope),
                attempt_id=AttemptId(value=UUID(int=42)),
            ),
        )
        same_a, same_b = await asyncio.gather(
            context.resolve_external(source, attempt_one),
            context.resolve_external(source, attempt_one),
        )
        rotated = await context.resolve_external(source, attempt_two)
        assert provider.calls == 2
        assert same_a == same_b
        assert cast("SensitiveValue[JsonString]", cast("Present[JsonString]", same_a).value).reveal(
            access=_ACCESS
        ) == JsonString(value="rotation-1")
        assert cast(
            "SensitiveValue[JsonString]", cast("Present[JsonString]", rotated).value
        ).reveal(access=_ACCESS) == JsonString(value="rotation-2")
        assert (await context.sanitization_snapshot()).revision.value == 2
        await context.close()

    _run(scenario())


def test_cell_generation_is_shared_across_environments_and_cancel_is_fail_closed(
    prepared_run: PreparedRun,
) -> None:
    spec = GenerationSpec(
        symbol=SymbolPath(segments=("correlation",)),
        operation=GenerationOperation.UUID,
        scope_kind=ValueScopeKind.CELL,
        configuration=configuration_provenance(),
        length=PositiveCount(value=32),
    )

    async def scenario() -> None:
        context = _context(prepared_run)
        staging = await context.generate(spec, _target(prepared_run, 0))
        production = await context.generate(spec, _target(prepared_run, 1))
        assert type(staging) is Present
        assert type(production) is Present
        assert staging.value == production.value
        assert staging.provenance.steps[-1].source.scope == (
            production.provenance.steps[-1].source.scope
        )
        assert len(cast("JsonString", staging.value).value) == 36
        context.cancel(CancellationSource.USER)
        context.cancel(CancellationSource.DEADLINE)
        cancelled = await context.resolve(
            LiteralExpr(
                value=JsonArray(items=()),
                provenance=configuration_provenance(),
            ),
            _target(prepared_run),
        )
        assert type(cancelled) is Error
        assert cancelled.code is ResolutionErrorCode.CANCELLED
        assert context.cancellation.source is CancellationSource.USER
        await context.close()
        await context.close()
        with pytest.raises(RuntimeError, match="run context is closed"):
            await context.resolve(
                LiteralExpr(
                    value=JsonString(value="late"),
                    provenance=configuration_provenance(),
                ),
                _target(prepared_run),
            )

    _run(scenario())


def test_provider_absence_and_failure_remain_typed(prepared_run: PreparedRun) -> None:
    provenance = configuration_provenance()
    absent_provider = FakeFileProvider(ProviderUnavailable(reason=UnavailableReason.NOT_FOUND))
    failed_provider = FakeFileProvider(ProviderError(code=ResolutionErrorCode.PROVIDER_FAILED))
    expression = FileExpr(
        path=LiteralExpr(value=JsonString(value="missing"), provenance=provenance),
        conversion=None,
        trim=FileTrim.NONE,
        provenance=provenance,
    )

    async def scenario() -> None:
        absent_context = _context(prepared_run, file_provider=absent_provider)
        absent = await absent_context.resolve(expression, _target(prepared_run))
        assert type(absent) is Unavailable
        assert absent.reason is UnavailableReason.NOT_FOUND
        await absent_context.close()
        failed_context = _context(prepared_run, file_provider=failed_provider)
        failed = await failed_context.resolve(expression, _target(prepared_run))
        assert type(failed) is Error
        assert failed.code is ResolutionErrorCode.PROVIDER_FAILED
        await failed_context.close()

    _run(scenario())
