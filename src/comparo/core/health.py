"""Probe an environment's readiness by running its declared health checks.

Like the rest of the engine this speaks to the network only through the
:class:`~comparo.core.http.HttpClient` port, so it stays testable with a fake
client and free of any HTTP-library import.
"""

import dataclasses
import enum

from comparo.core.http import HttpClient
from comparo.core.http import HttpError
from comparo.core.http import TimeoutBudget
from comparo.core.interpolation import Context
from comparo.core.interpolation import InterpolationError
from comparo.core.interpolation import interpolate
from comparo.core.loader import LoadedProject
from comparo.core.models import Environment
from comparo.core.models import HealthCheck
from comparo.core.resolve import ResolvedRequest
from comparo.core.secrets import ExecuteSecrets
from comparo.core.secrets import SecretError


class Health(enum.Enum):
    """The aggregate readiness of an environment."""

    UNKNOWN = "unknown"
    PASS = "pass"
    PARTIAL = "partial"
    FAIL = "fail"


@dataclasses.dataclass(frozen=True, slots=True)
class CheckResult:
    """The outcome of a single health check."""

    method: str
    endpoint: str
    ok: bool
    detail: str


@dataclasses.dataclass(frozen=True, slots=True)
class HealthReport:
    """Every check's result plus the aggregate status."""

    status: Health
    results: list[CheckResult]


async def check_health(
    project: LoadedProject, environment: Environment, client: HttpClient
) -> HealthReport:
    """Run every declared health check for *environment* and aggregate the result.

    A check passes on any non-error status below 400. The aggregate is
    :attr:`Health.PASS` when every check passes, :attr:`Health.FAIL` when none
    do, and :attr:`Health.PARTIAL` in between. An environment with no declared
    checks reports :attr:`Health.UNKNOWN`.

    Args:
        project: The loaded project, used to resolve secret sources.
        environment: The environment to probe.
        client: The transport the probes are sent through.

    Returns:
        The per-check results and the aggregate status.
    """
    checks = environment.spec.health or []
    if not checks:
        return HealthReport(Health.UNKNOWN, [])
    context = _context(project, environment)
    base = environment.spec.base_url.rstrip("/")
    budget = TimeoutBudget.resolve(None, environment.spec.timeout)
    results: list[CheckResult] = []
    for check in checks:
        url = f"{base}/{check.endpoint.lstrip('/')}"
        try:
            # Header interpolation can raise if a $secret is unresolvable; a failed
            # probe must degrade this check, not crash the whole health run.
            headers = _headers(environment, check, context)
            request = ResolvedRequest(check.method, url, headers, {}, None, [])
            response = await client.send(request, budget)
        except (HttpError, SecretError, InterpolationError) as error:
            results.append(CheckResult(check.method, check.endpoint, ok=False, detail=str(error)))
            continue
        ok = 200 <= response.status < 400
        results.append(
            CheckResult(check.method, check.endpoint, ok=ok, detail=str(response.status))
        )
    return HealthReport(_aggregate(results), results)


def _aggregate(results: list[CheckResult]) -> Health:
    passed = sum(1 for result in results if result.ok)
    if passed == len(results):
        return Health.PASS
    if passed == 0:
        return Health.FAIL
    return Health.PARTIAL


def _context(project: LoadedProject, environment: Environment) -> Context:
    sources = environment.spec.secrets or {}
    return Context(
        variables=dict(environment.spec.variables or {}),
        secret_names=frozenset(sources),
        mask_secrets=False,
        secret_values=ExecuteSecrets(dict(sources), project.root),
    )


def _headers(
    environment: Environment, check: HealthCheck, context: Context
) -> list[tuple[str, object]]:
    merged: dict[str, tuple[str, object]] = {}
    for header in environment.spec.headers or []:
        merged[header.key.lower()] = (header.key, header.value)
    for header in check.headers or []:
        merged[header.key.lower()] = (header.key, header.value)
    return [(key, _interpolate(value, context)) for key, value in merged.values()]


def _interpolate(value: object, context: Context) -> object:
    if isinstance(value, str):
        return interpolate(value, context).value
    return value
