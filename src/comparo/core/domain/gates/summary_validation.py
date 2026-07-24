"""Shared structural validation for value-free gate summaries."""

from __future__ import annotations

from comparo.core.domain.gates.types import EnvironmentRole
from comparo.core.domain.gates.types import GateRuleLineage
from comparo.core.domain.identity import ComparisonId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.lifecycle import RunCancelled
from comparo.core.domain.lifecycle import RunComplete
from comparo.core.domain.lifecycle import RunCompletion as LifecycleRunCompletion
from comparo.core.domain.lifecycle import RunInternalError
from comparo.core.domain.lifecycle import RunPartial
from comparo.core.domain.lifecycle import RunRejected


def require_lifecycle_completion(completion: LifecycleRunCompletion) -> None:
    """Require one exact lifecycle outcome instead of a duplicate gate claim."""
    if type(completion) not in {
        RunComplete,
        RunPartial,
        RunCancelled,
        RunRejected,
        RunInternalError,
    }:
        raise TypeError("normalized fact completion must be an exact lifecycle outcome")


def is_lifecycle_complete(completion: LifecycleRunCompletion) -> bool:
    """Return whether an exact lifecycle outcome proves complete execution."""
    return type(completion) is RunComplete


def require_environment(environment: DefinitionRef, context: str) -> None:
    """Require an exact Environment definition coordinate."""
    if type(environment) is not DefinitionRef:
        raise TypeError(f"{context} environment must be a DefinitionRef")
    if environment.identity.kind is not ResourceKind.ENVIRONMENT:
        raise ValueError(f"{context} must address an Environment")


def require_rule_tuple(
    rules: tuple[object, ...],
    rule_type: type[object],
    label: str,
) -> None:
    """Require one non-empty immutable closed rule collection."""
    if type(rules) is not tuple:
        raise TypeError(f"{label} gate rules must be an immutable tuple")
    if not rules:
        raise ValueError(f"{label} gate rules must be a non-empty immutable tuple")
    if any(type(rule) is not rule_type for rule in rules):
        raise TypeError(f"{label} gate summary contains an unsupported rule outcome")


def require_unique_lineages(
    lineages: tuple[GateRuleLineage, ...],
    label: str,
) -> None:
    """Require each normalized rule lineage exactly once per summary."""
    if len(set(lineages)) != len(lineages):
        raise ValueError(f"{label} gate rule lineages must be unique")


def require_globally_unique_lineages(lineages: tuple[GateRuleLineage, ...]) -> None:
    """Reject duplicate source facts or applications across normalized summaries."""
    fact_ids = tuple(lineage.source_fact_id for lineage in lineages)
    application_ids = tuple(lineage.application_id for lineage in lineages)
    if len(set(fact_ids)) != len(fact_ids):
        raise ValueError("normalized source fact IDs must be globally unique")
    if len(set(application_ids)) != len(application_ids):
        raise ValueError("normalized rule applications must be globally unique")


type AssertionExecutionCoordinate = tuple[
    DefinitionRef, EnvironmentExecutionId, tuple[EnvironmentRole, ...]
]
type DirectedComparisonCoordinate = tuple[
    ComparisonId,
    DefinitionRef,
    EnvironmentExecutionId,
    DefinitionRef,
    EnvironmentExecutionId,
]


def require_normalized_coordinates(
    assertions: tuple[AssertionExecutionCoordinate, ...],
    pairs: tuple[DirectedComparisonCoordinate, ...],
) -> None:
    """Require globally stable comparison and Environment-execution coordinates."""
    assertion_ids = tuple(execution_id for _, execution_id, _ in assertions)
    if len(set(assertion_ids)) != len(assertion_ids):
        raise ValueError("normalized assertion summary coordinates must be unique")
    if len(set(pairs)) != len(pairs):
        raise ValueError("normalized diff summary coordinates must be unique")
    _require_comparison_id_consistency(pairs)
    _require_environment_execution_consistency(assertions, pairs)
    _require_assertion_roles(assertions, pairs)


def _require_comparison_id_consistency(pairs: tuple[DirectedComparisonCoordinate, ...]) -> None:
    coordinates: dict[
        ComparisonId,
        tuple[DefinitionRef, EnvironmentExecutionId, DefinitionRef, EnvironmentExecutionId],
    ] = {}
    for (
        comparison_id,
        baseline_environment,
        baseline_id,
        candidate_environment,
        candidate_id,
    ) in pairs:
        coordinate = (
            baseline_environment,
            baseline_id,
            candidate_environment,
            candidate_id,
        )
        previous = coordinates.setdefault(comparison_id, coordinate)
        if previous != coordinate:
            raise ValueError("one ComparisonId cannot identify multiple directed pairs")


def _require_environment_execution_consistency(
    assertions: tuple[AssertionExecutionCoordinate, ...],
    pairs: tuple[DirectedComparisonCoordinate, ...],
) -> None:
    environments: dict[EnvironmentExecutionId, DefinitionRef] = {}
    coordinates = tuple((execution_id, environment) for environment, execution_id, _ in assertions)
    pair_coordinates = tuple(
        coordinate
        for _, baseline_environment, baseline_id, candidate_environment, candidate_id in pairs
        for coordinate in (
            (baseline_id, baseline_environment),
            (candidate_id, candidate_environment),
        )
    )
    for execution_id, environment in (*coordinates, *pair_coordinates):
        previous = environments.setdefault(execution_id, environment)
        if previous != environment:
            raise ValueError(
                "one EnvironmentExecutionId cannot identify multiple Environment definitions"
            )


def _require_assertion_roles(
    assertions: tuple[AssertionExecutionCoordinate, ...],
    pairs: tuple[DirectedComparisonCoordinate, ...],
) -> None:
    baseline_ids = frozenset(pair[2] for pair in pairs)
    candidate_ids = frozenset(pair[4] for pair in pairs)
    for _, execution_id, roles in assertions:
        proven = (
            *((EnvironmentRole.BASELINE,) if execution_id in baseline_ids else ()),
            *((EnvironmentRole.CANDIDATE,) if execution_id in candidate_ids else ()),
        )
        if proven and roles != proven:
            raise ValueError(
                "assertion execution roles contradict directed pairs in the same summary"
            )
