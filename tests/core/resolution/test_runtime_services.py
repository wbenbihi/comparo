"""Concurrency, cancellation, deterministic generation, and sensitive retention services."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.lifecycle import CancellationSource
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.scopes import RuntimeScope
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.value_references import runtime_value_scope_kind
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import ResolvedValue
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution.cache import SingleFlightCache
from comparo.core.resolution.control import RunCancellation
from comparo.core.resolution.generation import DeterministicGenerator
from comparo.core.resolution.models import GenerationOperation
from comparo.core.resolution.models import GenerationRequest
from comparo.core.resolution.models import GenerationSpec
from comparo.core.resolution.models import ProviderBlocked
from comparo.core.resolution.models import ProviderPresent
from comparo.core.resolution.provenance import LITERAL_PROVIDER
from comparo.core.resolution.provenance import present
from comparo.core.resolution.provenance import resolution_source
from comparo.core.resolution.registry import SensitiveValueRegistry
from tests.core.resolution.conftest import configuration_provenance
from tests.core.resolution.test_scopes_and_snapshots import _coordinates


def _run[ResultT](awaitable: Coroutine[Any, Any, ResultT]) -> ResultT:
    return asyncio.run(awaitable)


def _seed(value: bytes = b"seed") -> SensitiveValue[BytesValue]:
    return SensitiveValue(
        BytesValue(value=value),
        origins=(TaintOriginId(value="test/seed"),),
    )


def _generation(scope: RuntimeScope, operation: GenerationOperation) -> GenerationRequest:
    return GenerationRequest(
        spec=GenerationSpec(
            symbol=SymbolPath(segments=("generated",)),
            operation=operation,
            scope_kind=runtime_value_scope_kind(scope),
            configuration=configuration_provenance(),
            length=PositiveCount(value=96 if operation is GenerationOperation.HEX else 32),
        ),
        scope=scope,
        instant=UtcInstant(unix_nanoseconds=1),
    )


def test_single_flight_shields_source_work_and_close_cancels_pending_tasks() -> None:
    async def scenario() -> None:
        cache: SingleFlightCache[str, str] = SingleFlightCache()
        release = asyncio.Event()
        calls = 0

        async def factory() -> str:
            nonlocal calls
            calls += 1
            await release.wait()
            return "value"

        cancelled_waiter = asyncio.create_task(cache.get("same", factory))
        await asyncio.sleep(0)
        cancelled_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_waiter
        surviving_waiter = asyncio.create_task(cache.get("same", factory))
        release.set()
        assert await surviving_waiter == "value"
        assert calls == 1

        pending_started = asyncio.Event()

        async def pending() -> str:
            pending_started.set()
            await asyncio.Event().wait()
            return "unreachable"

        pending_waiter = asyncio.create_task(cache.get("pending", pending))
        await pending_started.wait()
        await cache.close()
        with pytest.raises(asyncio.CancelledError):
            await pending_waiter
        await cache.close()
        with pytest.raises(RuntimeError, match="closed"):
            await cache.get("late", factory)

    _run(scenario())


def test_run_cancellation_is_typed_first_wins_and_waitable() -> None:
    async def scenario() -> None:
        cancellation = RunCancellation()
        assert cancellation.cancelled is False
        assert cancellation.source is None
        waiter = asyncio.create_task(cancellation.wait())
        cancellation.cancel(CancellationSource.USER)
        cancellation.cancel(CancellationSource.DEADLINE)
        assert await waiter is CancellationSource.USER
        invalid_source: Any = object()
        with pytest.raises(TypeError, match="CancellationSource"):
            cancellation.cancel(invalid_source)

    _run(scenario())


def test_deterministic_generator_covers_every_scope_and_secure_rejection(
    prepared_run: PreparedRun,
) -> None:
    run, cell, trial, environment, execution, step, attempt, _ = _coordinates(prepared_run)
    generator = DeterministicGenerator(_seed())
    assert generator.identity.namespace == "comparo"

    async def scenario() -> None:
        values: list[str] = []
        for scope in (run, cell, trial, environment, execution, step, attempt):
            outcome = await generator.generate(_generation(scope, GenerationOperation.HEX))
            assert type(outcome) is ProviderPresent
            values.append(cast("JsonString", outcome.value).value)
        assert len(set(values)) == len(values)
        assert all(len(value) == 96 for value in values)
        uuid_outcome = await generator.generate(_generation(run, GenerationOperation.UUID))
        assert type(uuid_outcome) is ProviderPresent
        assert len(cast("JsonString", uuid_outcome.value).value) == 36
        secure_request = msgspec.structs.replace(
            _generation(run, GenerationOperation.HEX),
            spec=msgspec.structs.replace(
                _generation(run, GenerationOperation.HEX).spec,
                sensitive=True,
                secure=True,
            ),
        )
        assert type(await generator.generate(secure_request)) is ProviderBlocked
        with pytest.raises(TypeError, match="GenerationRequest"):
            await generator.generate(cast("GenerationRequest", object()))
        generator.close()
        with pytest.raises(RuntimeError, match="closed"):
            await generator.generate(_generation(run, GenerationOperation.HEX))

    _run(scenario())
    with pytest.raises(TypeError, match="sealed bytes"):
        DeterministicGenerator(cast("Any", JsonString(value="seed")))
    with pytest.raises(TypeError, match="sealed bytes"):
        DeterministicGenerator(
            cast(
                "Any",
                SensitiveValue(
                    JsonString(value="seed"),
                    origins=(TaintOriginId(value="test/seed"),),
                ),
            )
        )
    with pytest.raises(ValueError, match="must not be empty"):
        DeterministicGenerator(_seed(b""))


def test_sensitive_registry_deduplicates_and_rejects_cross_run_or_closed_access(
    prepared_run: PreparedRun,
) -> None:
    run, _, _, _, execution, _, _, _ = _coordinates(prepared_run)
    registry = SensitiveValueRegistry(run)
    symbol = SymbolPath(segments=("token",))
    source = resolution_source(
        symbol=symbol,
        provider=LITERAL_PROVIDER,
        configuration=configuration_provenance(),
        scope=execution,
        namespace=None,
    )
    sealed = cast("SensitiveValue[ResolvedValue]", _seed())
    sensitive_result = present(
        sealed,
        symbol=symbol,
        scope=execution,
        source=source,
        namespace=None,
    )
    public_result = present(
        JsonString(value="public"),
        symbol=symbol,
        scope=execution,
        source=source,
        namespace=None,
    )

    async def scenario() -> None:
        await registry.register(public_result, symbol=symbol, scope=execution)
        await registry.register(sensitive_result, symbol=symbol, scope=execution)
        await registry.register(sensitive_result, symbol=symbol, scope=execution)
        assert (await registry.snapshot()).revision.value == 1
        with pytest.raises(TypeError, match="SymbolPath"):
            await registry.register(
                sensitive_result,
                symbol=cast("SymbolPath", object()),
                scope=execution,
            )
        other_run = msgspec.structs.replace(
            run,
            run_id=msgspec.structs.replace(run.run_id, value=UUID(int=999)),
        )
        with pytest.raises(ValueError, match="registry run"):
            await registry.register(
                sensitive_result,
                symbol=symbol,
                scope=msgspec.structs.replace(execution.environment, run=other_run),
            )
        await registry.close()
        await registry.close()
        with pytest.raises(RuntimeError, match="closed"):
            await registry.snapshot()
        with pytest.raises(RuntimeError, match="closed"):
            await registry.register(sensitive_result, symbol=symbol, scope=execution)

    _run(scenario())
    with pytest.raises(TypeError, match="RunScope"):
        SensitiveValueRegistry(cast("Any", object()))
