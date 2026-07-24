"""Programmable fake transport validation and lifecycle edge cases."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.primitives import Count
from comparo.core.runtime import FakeExchangeAction
from comparo.core.runtime import HttpTransportResult
from comparo.core.runtime import ProgrammableFakeTransport
from tests.core.domain.http.observation_factories import authorized_request
from tests.core.domain.http.observation_factories import exchange_timings
from tests.core.domain.http.observation_factories import occurrence
from tests.core.domain.http.observation_factories import request
from tests.core.domain.http.observation_factories import response


def _result() -> HttpTransportResult:
    return HttpTransportResult(
        response=response(),
        failure=None,
        timings=exchange_timings(),
    )


def _authorized(exchange_id: ExchangeId) -> AuthorizedExchangeRequest:
    return authorized_request(
        request(),
        request_occurrence=occurrence(exchange=exchange_id.value.int),
    )


def test_fake_contracts_reject_invalid_scripts_and_calls() -> None:
    exchange_id = ExchangeId(value=UUID(int=801))
    with pytest.raises(TypeError, match="HttpTransportResult"):
        FakeExchangeAction(result=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Count"):
        FakeExchangeAction(result=_result(), yields=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Mapping"):
        ProgrammableFakeTransport([])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="unsupported entry"):
        ProgrammableFakeTransport({object(): FakeExchangeAction(result=_result())})  # type: ignore[dict-item]
    with pytest.raises(TypeError, match="unsupported entry"):
        ProgrammableFakeTransport({exchange_id: object()})  # type: ignore[dict-item]

    transport = ProgrammableFakeTransport({})
    with pytest.raises(TypeError, match="AuthorizedExchangeRequest"):
        asyncio.run(transport.exchange(object()))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="no action"):
        asyncio.run(transport.exchange(_authorized(exchange_id)))
    asyncio.run(transport.close())
    assert transport.closed
    with pytest.raises(RuntimeError, match="closed"):
        asyncio.run(transport.exchange(_authorized(exchange_id)))


def test_fake_cannot_close_until_a_cancelled_exchange_unwinds() -> None:
    async def scenario() -> ProgrammableFakeTransport:
        exchange_id = ExchangeId(value=UUID(int=802))
        transport = ProgrammableFakeTransport(
            {
                exchange_id: FakeExchangeAction(
                    result=_result(),
                    yields=Count(value=100),
                )
            }
        )
        task = asyncio.create_task(transport.exchange(_authorized(exchange_id)))
        await asyncio.sleep(0)
        assert transport.active.value == 1
        with pytest.raises(RuntimeError, match="active exchanges"):
            await transport.close()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert transport.active.value == 0
        await transport.close()
        return transport

    transport = asyncio.run(scenario())
    assert transport.calls == (ExchangeId(value=UUID(int=802)),)
    assert transport.maximum_active.value == 1
    assert not transport.remaining_exchange_ids
