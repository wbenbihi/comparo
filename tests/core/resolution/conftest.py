"""Representative admitted run and exact runtime coordinates for resolution tests."""

from uuid import UUID

import pytest

from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import TrialId
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.provenance import ValueProviderIdentity
from comparo.core.domain.scopes import CellScope
from comparo.core.domain.scopes import EnvironmentScope
from comparo.core.domain.scopes import ExecutionScope
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import TrialScope
from comparo.core.planning import iter_plan_cells
from comparo.core.planning import plan_run
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution.models import CellInputs
from comparo.core.resolution.models import EnvironmentSnapshot
from comparo.core.resolution.snapshots import cell_inputs
from tests.core.planning.conftest import authority
from tests.core.planning.conftest import compile_project
from tests.core.planning.conftest import document
from tests.core.planning.conftest import invocation


def configuration_provenance() -> ValueProvenance:
    """Return a safe test-only configuration origin."""
    return ValueProvenance(entries=(ProvenanceEntry(kind=ProvenanceKind.PROJECT),))


@pytest.fixture
def prepared_run() -> PreparedRun:
    """Compile and admit two Environments, one Matrix, and layered values."""
    project = compile_project(
        document(
            "Project",
            "demo",
            "{variables: {greeting: hello, shared: project}, secrets: {token: hidden}, "
            "defaults: {subjects: [{$use: Request/check}], baseline: {$use: staging}, "
            "candidates: [{$use: prod}], matrices: [{$use: cases}]}}",
        ),
        document(
            "Environment",
            "staging",
            "{baseUrl: 'https://staging.example', variables: {region: staging}}",
        ),
        document(
            "Environment",
            "prod",
            "{baseUrl: 'https://prod.example', variables: {region: prod}}",
        ),
        document(
            "Matrix",
            "cases",
            "{cases: [{id: one, values: {locale: en, count: 2}}]}",
        ),
        document(
            "Request",
            "check",
            "{request: {method: GET, endpoint: /check}}",
        ),
    )
    result = plan_run(project, invocation(), authority())
    assert result.prepared is not None
    return result.prepared


def execution_scope(prepared: PreparedRun, environment_index: int) -> ExecutionScope:
    """Build one exact execution coordinate for the first lazily planned cell."""
    planned_cell = next(iter(iter_plan_cells(prepared.plan)))
    run = RunScope(run_id=prepared.invocation.run_id)
    cell = CellScope(run=run, cell_id=planned_cell.cell_id)
    trial = TrialScope(cell=cell, trial_id=TrialId(value=UUID(int=20)))
    environment = prepared.effective.environments[environment_index]
    return ExecutionScope(
        trial=trial,
        environment=EnvironmentScope(run=run, environment=environment.identity),
        execution_id=EnvironmentExecutionId(value=UUID(int=30 + environment_index)),
    )


def snapshots(prepared: PreparedRun) -> tuple[EnvironmentSnapshot, ...]:
    """Create empty run-start snapshots for every selected Environment."""
    provider = ValueProviderIdentity(namespace="test", name="environment", version="1")
    return tuple(
        EnvironmentSnapshot(environment=environment, entries=(), provider=provider)
        for environment in prepared.effective.environments
    )


def matrix_inputs(prepared: PreparedRun) -> CellInputs:
    """Return the exact Matrix snapshot for the first planned cell."""
    return cell_inputs(prepared, next(iter(iter_plan_cells(prepared.plan))))
