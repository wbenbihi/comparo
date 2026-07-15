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
from comparo.core.models import Environment
from comparo.core.models import Request
from comparo.core.resolve import Resolver
from comparo.core.resolve import Sink
from comparo.core.secrets import SecretError


@dataclasses.dataclass(frozen=True, slots=True)
class Execution:
    """The outcome of executing one request: a response, or an error."""

    request: Request
    environment: Environment
    response: HttpResponse | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Whether the request produced a response."""
        return self.response is not None


async def execute_request(
    project: LoadedProject, environment: Environment, request: Request, client: HttpClient
) -> Execution:
    """Resolve and send one request, capturing any failure on the result.

    Args:
        project: The loaded project (for reference resolution).
        environment: The environment to execute against.
        request: The request to execute.
        client: The transport to send through.

    Returns:
        The execution outcome, with either a response or an error message.
    """
    try:
        resolved = Resolver(project, environment, Sink.EXECUTE).resolve_request(request)
        timeout = TimeoutBudget.resolve(request.spec.timeout, environment.spec.timeout)
        response = await client.send(resolved, timeout)
    except (SecretError, HttpError, InterpolationError) as error:
        return Execution(request, environment, None, str(error))
    return Execution(request, environment, response, None)


async def execute_all(
    project: LoadedProject,
    environment: Environment,
    requests: list[Request],
    client: HttpClient,
    concurrency: int = 4,
) -> list[Execution]:
    """Execute *requests* against *environment* with bounded concurrency.

    Args:
        project: The loaded project.
        environment: The environment to execute against.
        requests: The requests to execute.
        client: The transport to send through.
        concurrency: The maximum number of in-flight requests.

    Returns:
        One execution outcome per request, in the input order.
    """
    limit = asyncio.Semaphore(concurrency)

    async def _one(request: Request) -> Execution:
        async with limit:
            return await execute_request(project, environment, request, client)

    return await asyncio.gather(*(_one(request) for request in requests))
