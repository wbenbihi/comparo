"""Narrow injected runtime, HTTP, evaluation-preparation, and progress ports."""

from __future__ import annotations

from typing import Protocol
from typing import final

import msgspec

from comparo.core.assertions.engine import AssertionControl
from comparo.core.assertions.inputs import PreparedAssertionInputs
from comparo.core.diffs.accounting import DiffControl
from comparo.core.diffs.inputs import PreparedDiffInputs
from comparo.core.domain.assertions.rules import AssertionApplication
from comparo.core.domain.assertions.rules import CompiledAssertionRule
from comparo.core.domain.diffs.rules import CompiledDiffRule
from comparo.core.domain.diffs.rules import DiffApplication
from comparo.core.domain.gates.model import CompiledGateProfile
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.authorization import HttpRequestOccurrence
from comparo.core.domain.http.observations import EnvironmentExecutionObservation
from comparo.core.domain.http.observations import RetryMetadata
from comparo.core.domain.http.observations import RetryTrigger
from comparo.core.domain.http.policies import RetryPolicy
from comparo.core.domain.http.pre_send import HttpPreSendOutcome
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.responses import ResponseObservation
from comparo.core.domain.http.timings import HttpExchangeTimings
from comparo.core.domain.http.transport import TransportFailure
from comparo.core.domain.primitives import Count
from comparo.core.domain.scopes import AttemptScope
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution.control import RunCancellation
from comparo.core.runtime.models import ComparisonWork
from comparo.core.runtime.models import ExecutionOutcome
from comparo.core.runtime.models import ExecutionRecord
from comparo.core.runtime.models import ExecutionWork
from comparo.core.runtime.models import ObservedExecution
from comparo.core.runtime.models import RuntimeEvent
from comparo.core.runtime.models import is_execution_outcome
from comparo.core.selection.snapshot import SelectionSnapshot


class RuntimeEventSink(Protocol):
    """Consume value-free progress events without controlling execution semantics."""

    async def emit(self, event: RuntimeEvent) -> None:
        """Accept one already-sequenced event."""
        ...


class ExecutionDriver(Protocol):
    """Execute one scheduler-owned unit and release run-owned resources."""

    async def execute(self, work: ExecutionWork) -> ExecutionOutcome:
        """Return one typed terminal outcome without raising input-bearing errors."""
        ...

    async def close(self) -> None:
        """Release every resource owned by this run-local driver."""
        ...


@final
class HttpTransportResult(msgspec.Struct, frozen=True, kw_only=True):
    """One transport exchange result before runtime occurrence aggregation."""

    response: ResponseObservation | None
    failure: TransportFailure | None
    timings: HttpExchangeTimings

    def __post_init__(self) -> None:
        """Require a terminal response/failure and exact transport timing vector."""
        if self.response is not None and type(self.response) is not ResponseObservation:
            raise TypeError("transport result response must be ResponseObservation")
        if self.failure is not None and type(self.failure) is not TransportFailure:
            raise TypeError("transport result failure must be TransportFailure")
        if self.response is None and self.failure is None:
            raise ValueError("transport result requires a response or failure")
        if type(self.timings) is not HttpExchangeTimings:
            raise TypeError("transport result timings must be HttpExchangeTimings")


class HttpTransport(Protocol):
    """Perform exactly one already-authorized, route-pinned HTTP exchange."""

    async def exchange(self, request: AuthorizedExchangeRequest) -> HttpTransportResult:
        """Execute one exchange; implicit redirects and retries are forbidden."""
        ...

    async def close(self) -> None:
        """Release run-owned connections and transport resources."""
        ...


@final
class RequestPreparationFailure(msgspec.Struct, frozen=True, kw_only=True):
    """A value-free request-resolution failure before HTTP pre-send processing."""

    outcome: ExecutionOutcome

    def __post_init__(self) -> None:
        """Reject observed outcomes at a request-preparation failure boundary."""
        if not is_execution_outcome(self.outcome):
            raise TypeError("request preparation failure requires an ExecutionOutcome")
        if type(self.outcome) is ObservedExecution:
            raise ValueError("request preparation failure cannot contain an observation")


type PreparedRequest = FinalizedWireRequest | RequestPreparationFailure


class HttpRequestProvider(Protocol):
    """Resolve and materialize a request for one exact attempt scope."""

    async def prepare(self, work: ExecutionWork, attempt: AttemptScope) -> PreparedRequest:
        """Produce a wire request or a typed value-free preparation failure."""
        ...

    async def close(self) -> None:
        """Release request-resolution caches and materialization resources."""
        ...


type ExchangePreparation = AuthorizedExchangeRequest | HttpPreSendOutcome


class HttpExchangePreparer(Protocol):
    """Apply two-phase resolution and send authorization to a finalized request."""

    async def prepare(
        self,
        request: FinalizedWireRequest,
        occurrence: HttpRequestOccurrence,
    ) -> ExchangePreparation:
        """Return send authority or the exact pre-send terminal observation."""
        ...

    async def close(self) -> None:
        """Release route-resolution and authorization resources."""
        ...


class RetryWaiter(Protocol):
    """Apply one observable retry delay without hiding ambient sleeping or randomness."""

    async def wait(
        self,
        policy: RetryPolicy,
        retry_index: Count,
        trigger: RetryTrigger,
        cancellation: RunCancellation,
    ) -> RetryMetadata:
        """Wait cooperatively and return the exact backoff/jitter observation."""
        ...


class SelectionSnapshotProvider(Protocol):
    """Load body/view data required by selectors through injected storage adapters."""

    async def snapshot(
        self,
        observation: EnvironmentExecutionObservation,
    ) -> SelectionSnapshot:
        """Return an I/O-free snapshot bound to the supplied observation."""
        ...

    async def close(self) -> None:
        """Release body/view readers and transient snapshot resources."""
        ...


@final
class AssertionCall(msgspec.Struct, frozen=True, kw_only=True):
    """One fully prepared pure assertion-engine invocation."""

    application: AssertionApplication
    rule: CompiledAssertionRule
    inputs: PreparedAssertionInputs
    control: AssertionControl

    def __post_init__(self) -> None:
        """Require exact engine input types."""
        if type(self.application) is not AssertionApplication:
            raise TypeError("assertion call requires AssertionApplication")
        if type(self.rule) is not CompiledAssertionRule:
            raise TypeError("assertion call requires CompiledAssertionRule")
        if type(self.inputs) is not PreparedAssertionInputs:
            raise TypeError("assertion call requires PreparedAssertionInputs")
        if type(self.control) is not AssertionControl:
            raise TypeError("assertion call requires AssertionControl")


@final
class DiffCall(msgspec.Struct, frozen=True, kw_only=True):
    """One fully prepared pure diff-engine invocation."""

    application: DiffApplication
    rule: CompiledDiffRule
    inputs: PreparedDiffInputs
    control: DiffControl

    def __post_init__(self) -> None:
        """Require exact engine input types."""
        if type(self.application) is not DiffApplication:
            raise TypeError("diff call requires DiffApplication")
        if type(self.rule) is not CompiledDiffRule:
            raise TypeError("diff call requires CompiledDiffRule")
        if type(self.inputs) is not PreparedDiffInputs:
            raise TypeError("diff call requires PreparedDiffInputs")
        if type(self.control) is not DiffControl:
            raise TypeError("diff call requires DiffControl")


class EvaluationPreparation(Protocol):
    """Bridge compiled profile binding and scoped operands into the pure kernels."""

    @property
    def gate_profile(self) -> CompiledGateProfile | None:
        """Return the optional already-compiled gate policy."""
        ...

    async def assertions(
        self,
        prepared: PreparedRun,
        record: ExecutionRecord,
    ) -> tuple[AssertionCall, ...]:
        """Prepare assertion calls for one observed execution."""
        ...

    async def diffs(
        self,
        prepared: PreparedRun,
        work: ComparisonWork,
        baseline: ExecutionRecord,
        candidate: ExecutionRecord,
    ) -> tuple[DiffCall, ...]:
        """Prepare diff calls for one fully observed directed pair."""
        ...
