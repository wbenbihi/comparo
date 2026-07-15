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
from comparo.core.http import TimeoutBudget
from comparo.core.interpolation import InterpolationError
from comparo.core.loader import LoadedProject
from comparo.core.matrix import MatrixCell
from comparo.core.matrix import expand
from comparo.core.models import Environment
from comparo.core.models import Request
from comparo.core.resolve import Resolver
from comparo.core.resolve import Sink
from comparo.core.secrets import SecretError


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
) -> Execution:
    """Resolve and send one request cell, capturing any failure on the result.

    Args:
        project: The loaded project (for reference resolution).
        environment: The environment to execute against.
        request: The request to execute.
        client: The transport to send through.
        cell: The matrix cell to inject, or ``None`` for the base request.

    Returns:
        The execution outcome, with either a response or an error message.
    """
    key = cell.key if cell is not None else ""
    try:
        resolved = Resolver(project, environment, Sink.EXECUTE).resolve_request(request, cell)
        timeout = TimeoutBudget.resolve(request.spec.timeout, environment.spec.timeout)
        response = await client.send(resolved, timeout)
    except (SecretError, HttpError, InterpolationError) as error:
        return Execution(request, environment, key, None, str(error))
    return Execution(request, environment, key, response, None)


async def execute_all(
    project: LoadedProject,
    environment: Environment,
    requests: list[Request],
    client: HttpClient,
    concurrency: int = 4,
) -> list[Execution]:
    """Execute every request, expanded across its matrices, with bounded concurrency.

    Args:
        project: The loaded project.
        environment: The environment to execute against.
        requests: The requests to execute.
        client: The transport to send through.
        concurrency: The maximum number of in-flight requests.

    Returns:
        One execution outcome per request cell.
    """
    limit = asyncio.Semaphore(concurrency)

    async def _one(request: Request, cell: MatrixCell) -> Execution:
        async with limit:
            return await execute_request(project, environment, request, client, cell)

    coroutines = [_one(request, cell) for request in requests for cell in expand(project, request)]
    return await asyncio.gather(*coroutines)
