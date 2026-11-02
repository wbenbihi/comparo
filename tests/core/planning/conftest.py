"""Shared compiled-project and authority fixtures for planner tests."""

from uuid import UUID

import pytest

from comparo.configuration.yaml_parser import parse_yaml_source
from comparo.core.configuration.canonical import CompiledProject
from comparo.core.configuration.compiler import compile_v1alpha1
from comparo.core.configuration.v1alpha1.frontend import parse_v1alpha1_documents
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InvocationId
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.identity import SourceId
from comparo.core.domain.planning_inputs import PlanningAuthority
from comparo.core.domain.planning_inputs import PlanningBudgetValue
from comparo.core.domain.planning_inputs import RunInvocation
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Sha256Digest

_SOURCE = SourceId(value="planning-config")
_NAMESPACE = NamespaceId(value="local")


def document(kind: str, resource_id: str, spec: str) -> str:
    """Build one concise v1alpha1 resource document."""
    return (
        "apiVersion: comparo/v1alpha1\n"
        f"kind: {kind}\n"
        f"metadata: {{id: {resource_id}, name: {resource_id.title()}}}\n"
        f"spec: {spec}\n"
    )


def compile_project(*documents: str) -> CompiledProject:
    """Compile one in-memory project and require a successful fixture."""
    parsed = parse_yaml_source(source_id=_SOURCE, text="---\n".join(documents))
    frontend = parse_v1alpha1_documents(parsed.documents)
    result = compile_v1alpha1(frontend, namespace=_NAMESPACE, entry_source=_SOURCE)
    assert result.diagnostics == ()
    assert result.project is not None
    return result.project


def policy_definition() -> DefinitionRef:
    """Build one exact external policy authority."""
    return DefinitionRef(
        identity=ResourceIdentity(
            namespace=NamespaceId(value="operator"),
            kind=ResourceKind.EXECUTION_POLICY,
            resource_id=ResourceId(value="ci"),
        ),
        fingerprint=Fingerprint(
            algorithm="sha256",
            digest=Sha256Digest(hex_value="f" * 64),
        ),
    )


def authority(limit: int = 100_000) -> PlanningAuthority:
    """Build a finite operator ceiling for every executable planning dimension."""
    resources = (
        BudgetResource.REQUESTS,
        BudgetResource.MATRIX_CELLS,
        BudgetResource.TRIALS,
        BudgetResource.ENVIRONMENT_EXECUTIONS,
        BudgetResource.ATTEMPTS,
        BudgetResource.COMPARISONS,
        BudgetResource.QUEUED_WORK,
        BudgetResource.CONCURRENCY,
    )
    return PlanningAuthority(
        policy=policy_definition(),
        ceilings=tuple(
            PlanningBudgetValue(resource=resource, limit=PositiveCount(value=limit))
            for resource in resources
        ),
    )


def invocation(**changes: object) -> RunInvocation:
    """Build a stable invocation and apply typed test-only field replacements."""
    values: dict[str, object] = {
        "invocation_id": InvocationId(value=UUID("10000000-0000-0000-0000-000000000001")),
        "run_id": RunId(value=UUID("20000000-0000-0000-0000-000000000002")),
    }
    values.update(changes)
    return RunInvocation(**values)  # type: ignore[arg-type]


@pytest.fixture
def comparison_project() -> CompiledProject:
    """Return a representative two-Environment project with Matrix and profiles."""
    return compile_project(
        document(
            "Project",
            "demo",
            "{variables: {project: one}, defaults: {subjects: [{$use: Request/chat}], "
            "baseline: {$use: staging}, candidates: [{$use: prod}], trials: 2, "
            "maximumAttempts: 2, matrices: [{$use: locales}], "
            "assertions: [{$use: common-assertions}], safety: {$use: project-safety}}}",
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
            "locales",
            "{cases: [{id: en, values: {locale: en}}, {id: fr, values: {locale: fr}}]}",
        ),
        document(
            "AssertionProfile",
            "base-assertions",
            "{rules: [{operator: equals}]}",
        ),
        document(
            "AssertionProfile",
            "common-assertions",
            "{include: [{$use: base-assertions}], rules: [{operator: equals}]}",
        ),
        document(
            "DiffProfile",
            "semantic",
            "{rules: [{comparator: exact}]}",
        ),
        document(
            "SafetyProfile",
            "base-safety",
            "{budgets: {matrix-cells: 50}}",
        ),
        document(
            "SafetyProfile",
            "project-safety",
            "{extends: {$use: base-safety}, budgets: {trials: 20}}",
        ),
        document(
            "Request",
            "chat",
            "{request: {method: POST, endpoint: /chat, body: {prompt: hello}}, "
            "diffs: [{$use: semantic}]}",
        ),
    )
