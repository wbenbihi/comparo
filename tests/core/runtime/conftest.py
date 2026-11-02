"""Factories for complete authorized fake-transport runtime tests."""

from __future__ import annotations

import msgspec
import pytest

from comparo.core.domain.capabilities import ExecutionPolicyAuthority
from comparo.core.domain.http.authorization import AuthorizedExchangeRequest
from comparo.core.domain.http.authorization import HttpAuthorizationGrant
from comparo.core.domain.http.authorization import HttpAuthorizationIntent
from comparo.core.domain.http.authorization import HttpRequestOccurrence
from comparo.core.domain.http.requests import FinalizedWireRequest
from comparo.core.domain.http.url import DirectResolvedRoute
from comparo.core.domain.http.url import effective_port
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.scopes import AttemptScope
from comparo.core.planning.models import PreparedRun
from comparo.core.planning.planner import plan_run
from comparo.core.runtime.models import ExecutionWork
from tests.core.domain.http.factories import authority
from tests.core.domain.http.factories import destination
from tests.core.domain.http.factories import fingerprint
from tests.core.domain.http.observation_factories import finalized_wire_request
from tests.core.domain.http.observation_factories import request
from tests.core.planning.conftest import authority as planning_authority
from tests.core.planning.conftest import compile_project
from tests.core.planning.conftest import document
from tests.core.planning.conftest import invocation


@pytest.fixture
def prepared_comparison_run() -> PreparedRun:
    """Compile and admit a two-Environment, two-cell, two-trial offline run."""
    project = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/chat}], baseline: {$use: staging}, "
            "candidates: [{$use: prod}], trials: 2, maximumAttempts: 2, "
            "matrices: [{$use: locales}]}}",
        ),
        document("Environment", "staging", "{baseUrl: 'https://staging.example'}"),
        document("Environment", "prod", "{baseUrl: 'https://prod.example'}"),
        document(
            "Matrix",
            "locales",
            "{cases: [{id: en, values: {locale: en}}, {id: fr, values: {locale: fr}}]}",
        ),
        document("Request", "chat", "{request: {method: GET, endpoint: /chat}}"),
    )
    result = plan_run(project, invocation(), planning_authority())
    assert result.prepared is not None
    return result.prepared


class FixedClock:
    """Return one stable completion instant."""

    def now(self) -> UtcInstant:
        """Return the fixed test timestamp."""
        return UtcInstant(unix_nanoseconds=1_767_225_600_000_000_000)


class StaticRequestProvider:
    """Materialize one ordinary body-free request per execution attempt."""

    def __init__(self, *, attempts: int = 1) -> None:
        """Set the retry policy maximum used by every generated request."""
        self._attempts = attempts

    async def prepare(
        self,
        work: ExecutionWork,
        attempt: AttemptScope,
    ) -> FinalizedWireRequest:
        """Build an Environment-bound request without ambient state."""
        del attempt
        effective = request(
            environment_id=work.environment.identity.resource_id.value,
            attempts=self._attempts,
        )
        effective = msgspec.structs.replace(effective, environment=work.environment)
        return finalized_wire_request(effective)

    async def close(self) -> None:
        """Release no state from this stateless provider."""


class AllowingExchangePreparer:
    """Grant the exact route-pinned exchange through a fixed test authority."""

    def __init__(self, policy_authority: ExecutionPolicyAuthority | None = None) -> None:
        """Retain one explicit operator authority."""
        self._authority = policy_authority or authority()
        self.requests: list[FinalizedWireRequest] = []

    async def prepare(
        self,
        request_value: FinalizedWireRequest,
        occurrence: HttpRequestOccurrence,
    ) -> AuthorizedExchangeRequest:
        """Authorize one direct route bound to the supplied occurrence."""
        self.requests.append(request_value)
        intent = HttpAuthorizationIntent(
            request=request_value,
            route=DirectResolvedRoute(
                destination=destination(
                    host=request_value.url.authority.host.value,
                    scheme=request_value.url.scheme,
                    port=effective_port(request_value.url).value,
                )
            ),
            occurrence=occurrence,
            plan_fingerprint=fingerprint("f"),
            effective_policy_fingerprint=fingerprint("e"),
        )
        return AuthorizedExchangeRequest(
            authorization=HttpAuthorizationGrant(intent=intent, authority=self._authority)
        )

    async def close(self) -> None:
        """Release no state from this static authority adapter."""
