"""End-to-end offline runtime tests over an admitted compiled configuration."""

from __future__ import annotations

import asyncio
from typing import cast
from uuid import UUID
from uuid import uuid5

import msgspec

from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.gates.model import GateVerdict
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.url import DecodedPathSegment
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.lifecycle import RunComplete
from comparo.core.domain.overrides import SetValue
from comparo.core.domain.planning_inputs import PlanningBudgetValue
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.provenance import ValueProviderIdentity
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import BytesValue
from comparo.core.planning.models import PreparedRun
from comparo.core.planning.planner import plan_run
from comparo.core.resolution import DeterministicGenerator
from comparo.core.resolution import EnvironmentSnapshot
from comparo.core.resolution import RunContext
from comparo.core.resolution.control import RunCancellation
from comparo.core.runtime import ConfigurationRequestProvider
from comparo.core.runtime import FakeExchangeAction
from comparo.core.runtime import HttpExecutionDriver
from comparo.core.runtime import HttpTransportResult
from comparo.core.runtime import ImmediateRetryWaiter
from comparo.core.runtime import NoEvaluationPreparation
from comparo.core.runtime import ObservationOnlySnapshotProvider
from comparo.core.runtime import ProgrammableFakeTransport
from comparo.core.runtime import RecordingEventSink
from comparo.core.runtime import RuntimeEventKind
from comparo.core.runtime import iter_execution_work
from comparo.core.runtime import run_prepared
from tests.core.domain.http.observation_factories import exchange_timings
from tests.core.domain.http.observation_factories import response
from tests.core.planning.conftest import authority as planning_authority
from tests.core.planning.conftest import invocation
from tests.core.runtime.conftest import AllowingExchangePreparer
from tests.core.runtime.conftest import FixedClock
from tests.core.runtime.conftest import StaticRequestProvider


def _exchange_id(work_id: UUID, attempt_index: int = 0) -> ExchangeId:
    attempt_id = uuid5(work_id, f"attempt:{attempt_index}")
    return ExchangeId(value=uuid5(attempt_id, "exchange:0"))


def _response_for_host(host: str) -> ResponseObservation:
    value = response()
    destination = msgspec.structs.replace(
        value.endpoint.destination,
        host=DnsHost(value=host),
    )
    return msgspec.structs.replace(
        value,
        endpoint=msgspec.structs.replace(value.endpoint, destination=destination),
    )


def _driver(
    prepared: PreparedRun,
    cancellation: RunCancellation,
) -> tuple[HttpExecutionDriver, ProgrammableFakeTransport]:
    work = tuple(iter_execution_work(prepared))
    actions = {
        _exchange_id(item.scope.execution_id.value): FakeExchangeAction(
            result=HttpTransportResult(
                response=response(),
                failure=None,
                timings=exchange_timings(),
            ),
            yields=Count(value=(len(work) - item.ordinal.value) % 5),
        )
        for item in work
    }
    transport = ProgrammableFakeTransport(actions)
    return (
        HttpExecutionDriver(
            request_provider=StaticRequestProvider(),
            exchange_preparer=AllowingExchangePreparer(),
            transport=transport,
            snapshot_provider=ObservationOnlySnapshotProvider(),
            retry_waiter=ImmediateRetryWaiter(),
            cancellation=cancellation,
            maximum_attempts=prepared.effective.maximum_attempts,
        ),
        transport,
    )


def test_compiled_plan_runs_offline_with_bounded_work_and_deterministic_results(
    prepared_comparison_run: PreparedRun,
) -> None:
    cancellation = RunCancellation()
    driver, transport = _driver(prepared_comparison_run, cancellation)
    events = RecordingEventSink()

    result = asyncio.run(
        run_prepared(
            prepared_comparison_run,
            driver=driver,
            evaluation=NoEvaluationPreparation(),
            cancellation=cancellation,
            clock=FixedClock(),
            events=events,
        )
    )
    assert type(result.completion) is RunComplete
    assert len(result.executions) == (
        prepared_comparison_run.plan.cardinality.environment_executions.value
    )
    assert len(result.comparisons) == prepared_comparison_run.plan.cardinality.comparisons.value
    assert [item.work.ordinal.value for item in result.executions] == list(
        range(len(result.executions))
    )
    assert [item.work.ordinal.value for item in result.comparisons] == list(
        range(len(result.comparisons))
    )
    assert result.gate.result.verdict is GateVerdict.NOT_REQUESTED
    ledgers = {item.resource: item for item in result.ledgers}
    queue = ledgers[BudgetResource.QUEUED_WORK]
    concurrency = ledgers[BudgetResource.CONCURRENCY]
    assert cast(Count, queue.usage.consumed).value == len(result.executions)
    assert cast(Count, queue.usage.high_water).value <= cast(Count, queue.effective).value
    assert cast(Count, concurrency.usage.consumed).value == len(result.executions)
    assert transport.maximum_active.value <= cast(Count, concurrency.effective).value
    assert transport.active.value == 0
    assert transport.closed
    assert not transport.remaining_exchange_ids
    assert events.events[0].kind is RuntimeEventKind.RUN_STARTED
    assert events.events[-1].kind is RuntimeEventKind.RUN_FINISHED
    assert tuple(item.sequence.value for item in events.events) == tuple(range(len(events.events)))


def test_randomized_completion_schedule_does_not_change_final_order(
    prepared_comparison_run: PreparedRun,
) -> None:
    async def execute_once() -> tuple[tuple[object, ...], tuple[object, ...]]:
        cancellation = RunCancellation()
        driver, _transport = _driver(prepared_comparison_run, cancellation)
        result = await run_prepared(
            prepared_comparison_run,
            driver=driver,
            evaluation=NoEvaluationPreparation(),
            cancellation=cancellation,
            clock=FixedClock(),
            events=RecordingEventSink(),
        )
        return (
            tuple(item.work.scope.execution_id for item in result.executions),
            tuple(item.work.comparison_id for item in result.comparisons),
        )

    assert asyncio.run(execute_once()) == asyncio.run(execute_once())


def test_large_fake_plan_keeps_transient_queue_and_transport_work_bounded(
    prepared_comparison_run: PreparedRun,
) -> None:
    planned = plan_run(
        prepared_comparison_run.project,
        invocation(
            trials=SetValue(value=PositiveCount(value=64)),
            budgets=(
                PlanningBudgetValue(
                    resource=BudgetResource.QUEUED_WORK,
                    limit=PositiveCount(value=5),
                ),
                PlanningBudgetValue(
                    resource=BudgetResource.CONCURRENCY,
                    limit=PositiveCount(value=3),
                ),
            ),
        ),
        planning_authority(),
    )
    assert planned.prepared is not None
    cancellation = RunCancellation()
    driver, transport = _driver(planned.prepared, cancellation)
    result = asyncio.run(
        run_prepared(
            planned.prepared,
            driver=driver,
            evaluation=NoEvaluationPreparation(),
            cancellation=cancellation,
            clock=FixedClock(),
            events=RecordingEventSink(),
        )
    )

    assert len(result.executions) == 256
    ledgers = {item.resource: item for item in result.ledgers}
    queue = ledgers[BudgetResource.QUEUED_WORK]
    concurrency = ledgers[BudgetResource.CONCURRENCY]
    assert cast(Count, queue.requested).value == 5
    assert cast(Count, queue.effective).value == 5
    assert cast(Count, queue.usage.high_water).value <= 5
    assert cast(Count, concurrency.requested).value == 3
    assert cast(Count, concurrency.effective).value == 3
    assert transport.maximum_active.value <= 3
    assert queue.cap is not None
    assert concurrency.cap is not None
    assert queue.contributions
    assert concurrency.contributions


def test_compiled_request_values_resolve_per_environment_before_fake_transport(
    prepared_comparison_run: PreparedRun,
) -> None:
    provider_identity = ValueProviderIdentity(
        namespace="test",
        name="environment",
        version="1",
    )
    snapshots = tuple(
        EnvironmentSnapshot(environment=item, entries=(), provider=provider_identity)
        for item in prepared_comparison_run.effective.environments
    )
    context = RunContext(
        prepared=prepared_comparison_run,
        environment_snapshots=snapshots,
        clock=FixedClock(),
        generator=DeterministicGenerator(
            SensitiveValue(
                BytesValue(value=b"runtime-resolution-seed"),
                origins=(TaintOriginId(value="test/runtime-seed"),),
            )
        ),
    )
    cancellation = context.cancellation
    work = tuple(iter_execution_work(prepared_comparison_run))
    transport = ProgrammableFakeTransport(
        {
            _exchange_id(item.scope.execution_id.value): FakeExchangeAction(
                result=HttpTransportResult(
                    response=_response_for_host(
                        f"{item.environment.identity.resource_id.value}.example"
                    ),
                    failure=None,
                    timings=exchange_timings(),
                )
            )
            for item in work
        }
    )
    exchange_preparer = AllowingExchangePreparer()
    driver = HttpExecutionDriver(
        request_provider=ConfigurationRequestProvider(prepared_comparison_run, context),
        exchange_preparer=exchange_preparer,
        transport=transport,
        snapshot_provider=ObservationOnlySnapshotProvider(),
        retry_waiter=ImmediateRetryWaiter(),
        cancellation=cancellation,
        maximum_attempts=prepared_comparison_run.effective.maximum_attempts,
    )
    result = asyncio.run(
        run_prepared(
            prepared_comparison_run,
            driver=driver,
            evaluation=NoEvaluationPreparation(),
            cancellation=cancellation,
            clock=FixedClock(),
            events=RecordingEventSink(),
        )
    )
    asyncio.run(context.close())

    assert type(result.completion) is RunComplete
    assert len(exchange_preparer.requests) == len(work)
    assert {item.method.value for item in exchange_preparer.requests} == {"GET"}
    assert {item.url.authority.host.value for item in exchange_preparer.requests} == {
        "staging.example",
        "prod.example",
    }
    fingerprints_by_environment = {
        item.environment.identity.resource_id.value: (
            item.materialization.identity.effective_request_fingerprint
        )
        for item in exchange_preparer.requests
    }
    assert fingerprints_by_environment["staging"] != fingerprints_by_environment["prod"]
    assert {
        tuple(cast(DecodedPathSegment, segment).value for segment in item.url.path.segments)
        for item in exchange_preparer.requests
    } == {("chat",)}
    assert transport.closed
