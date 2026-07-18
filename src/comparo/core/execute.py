"""Execute resolved requests against an :class:`HttpClient`.

The engine resolves a request in the execute sink (real secret values), computes
its timeout budget, and sends it. Failures — a missing secret or a transport
error — are captured on the result rather than raised, so one bad request never
aborts a run of many.
"""

import asyncio
import dataclasses

from comparo.core.http import HttpClient
from comparo.core.http import HttpError
from comparo.core.http import HttpResponse
from comparo.core.http import HttpTimeoutError
from comparo.core.http import TimeoutBudget
from comparo.core.interpolation import InterpolationError
from comparo.core.loader import LoadedProject
from comparo.core.matrix import MatrixCell
from comparo.core.matrix import expand
from comparo.core.models import Environment
from comparo.core.models import Request
from comparo.core.models import RetryConfig
from comparo.core.resolve import Resolver
from comparo.core.resolve import Sink
from comparo.core.secrets import SecretError

#: Base delay for retry backoff, in seconds; scaled by the chosen strategy.
_RETRY_BASE = 0.5


def run_settings(project: LoadedProject) -> tuple[int, RetryConfig | None]:
    """The effective ``(concurrency, retry)`` for a run, from ``spec.run``."""
    run = project.project.spec.run if project.project is not None else None
    concurrency = run.concurrency if run is not None and run.concurrency else 4
    retry = run.retry if run is not None else None
    return concurrency, retry


def _backoff_delay(retry: RetryConfig, attempt: int) -> float:
    strategy = retry.backoff or "exponential"
    if strategy == "constant":
        return _RETRY_BASE
    if strategy == "linear":
        return _RETRY_BASE * (attempt + 1)
    return _RETRY_BASE * (2.0**attempt)


async def _send_with_retry(
    client: HttpClient,
    resolved: object,
    timeout: TimeoutBudget,
    retry: RetryConfig | None,
) -> HttpResponse:
    attempts = max(retry.attempts, 1) if retry is not None and retry.attempts else 1
    last: HttpError | None = None
    for attempt in range(attempts):
        try:
            return await client.send(resolved, timeout)  # type: ignore[arg-type]
        except HttpTimeoutError:
            # A deadline timeout is never retried — retrying would multiply the
            # wall-clock bound the deadline exists to give.
            raise
        except HttpError as error:
            last = error
            if attempt + 1 < attempts and retry is not None:
                await asyncio.sleep(_backoff_delay(retry, attempt))
    assert last is not None  # a failed loop always recorded the last error
    raise last


@dataclasses.dataclass(frozen=True, slots=True)
class Execution:
    """The outcome of executing one request cell: a response, or an error."""

    request: Request
    environment: Environment
    cell_key: str
    response: HttpResponse | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Whether the request produced a response."""
        return self.response is not None


async def execute_request(
    project: LoadedProject,
    environment: Environment,
    request: Request,
    client: HttpClient,
    cell: MatrixCell | None = None,
    retry: RetryConfig | None = None,
) -> Execution:
    """Resolve and send one request cell, capturing any failure on the result.

    Args:
        project: The loaded project (for reference resolution).
        environment: The environment to execute against.
        request: The request to execute.
        client: The transport to send through.
        cell: The matrix cell to inject, or ``None`` for the base request.
        retry: The retry policy for transport failures, or ``None`` for one try.

    Returns:
        The execution outcome, with either a response or an error message.
    """
    key = cell.key if cell is not None else ""
    try:
        # Resolution failures (a missing secret, a bad interpolation) are config
        # errors, not transient — never retried.
        resolved = Resolver(project, environment, Sink.EXECUTE).resolve_request(request, cell)
        timeout = TimeoutBudget.resolve(request.spec.timeout, environment.spec.timeout)
    except (SecretError, InterpolationError) as error:
        return Execution(request, environment, key, None, str(error))
    try:
        response = await _send_with_retry(client, resolved, timeout, retry)
    except HttpError as error:
        return Execution(request, environment, key, None, str(error))
    return Execution(request, environment, key, response, None)


async def execute_all(
    project: LoadedProject,
    environment: Environment,
    requests: list[Request],
    client: HttpClient,
    concurrency: int | None = None,
    retry: RetryConfig | None = None,
) -> list[Execution]:
    """Execute every request, expanded across its matrices, with bounded concurrency.

    Args:
        project: The loaded project.
        environment: The environment to execute against.
        requests: The requests to execute.
        client: The transport to send through.
        concurrency: The maximum number of in-flight requests; ``None`` reads
            ``spec.run.concurrency`` (default 4).
        retry: The retry policy for transport failures; ``None`` reads
            ``spec.run.retry``.

    Returns:
        One execution outcome per request cell.
    """
    if concurrency is None or retry is None:
        default_concurrency, default_retry = run_settings(project)
        concurrency = concurrency if concurrency is not None else default_concurrency
        retry = retry if retry is not None else default_retry
    limit = asyncio.Semaphore(concurrency)

    async def _one(request: Request, cell: MatrixCell) -> Execution:
        async with limit:
            return await execute_request(project, environment, request, client, cell, retry)

    coroutines = [_one(request, cell) for request in requests for cell in expand(project, request)]
    return await asyncio.gather(*coroutines)
