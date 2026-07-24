"""Direct-construction invariants for invocation and operator planning inputs."""

from typing import cast

import msgspec
import pytest

from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import CompiledProject
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import MatrixBindingId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.overrides import Clear
from comparo.core.domain.overrides import CollectionOverridePlan
from comparo.core.domain.overrides import KeepBase
from comparo.core.domain.overrides import KeyedSet
from comparo.core.domain.overrides import ReplaceBase
from comparo.core.domain.overrides import SetValue
from comparo.core.domain.planning_inputs import AssertionTopology
from comparo.core.domain.planning_inputs import ComparisonTopology
from comparo.core.domain.planning_inputs import ComparisonTopologyEdit
from comparo.core.domain.planning_inputs import InvocationValueOverlay
from comparo.core.domain.planning_inputs import MatrixSelection
from comparo.core.domain.planning_inputs import PlanningBudgetValue
from comparo.core.domain.primitives import PositiveCount
from tests.core.planning.conftest import authority
from tests.core.planning.conftest import invocation
from tests.core.planning.conftest import policy_definition


def test_matrix_and_topology_inputs_reject_ambiguous_construction() -> None:
    selection = MatrixSelection(
        binding=MatrixBindingId(value="left"), matrix=ResourceId(value="cases")
    )
    with pytest.raises(TypeError, match="MatrixBindingId"):
        msgspec.structs.replace(selection, binding="left")
    with pytest.raises(TypeError, match="ResourceId"):
        msgspec.structs.replace(selection, matrix="cases")
    with pytest.raises(TypeError, match="baseline"):
        ComparisonTopology(
            baseline=cast("ResourceId", "dev"),
            candidates=(ResourceId(value="prod"),),
        )
    with pytest.raises(TypeError, match="ResourceId tuple"):
        ComparisonTopology(
            baseline=ResourceId(value="dev"),
            candidates=cast("tuple[ResourceId, ...]", [ResourceId(value="prod")]),
        )
    with pytest.raises(ValueError, match="at least one"):
        ComparisonTopology(baseline=ResourceId(value="dev"), candidates=())
    with pytest.raises(ValueError, match="cannot also"):
        ComparisonTopology(
            baseline=ResourceId(value="dev"),
            candidates=(ResourceId(value="dev"),),
        )
    edit = ComparisonTopologyEdit(candidates=CollectionOverridePlan(base=KeepBase()))
    assert edit.baseline.__class__.__name__ == "Inherit"
    with pytest.raises(TypeError, match="baseline edit"):
        msgspec.structs.replace(edit, baseline=object())
    with pytest.raises(TypeError, match="candidate selection"):
        msgspec.structs.replace(edit, candidates=object())
    with pytest.raises(TypeError, match="ResourceId values"):
        msgspec.structs.replace(
            edit,
            candidates=CollectionOverridePlan(
                base=ReplaceBase(items=(cast("ResourceId", MatrixBindingId(value="bad")),))
            ),
        )
    assert ComparisonTopologyEdit(baseline=Clear()).baseline.__class__.__name__ == "Clear"
    duplicate = ResourceId(value="dev")
    with pytest.raises(ValueError, match="unique"):
        AssertionTopology(environments=(duplicate, duplicate))
    with pytest.raises(ValueError, match="at least one"):
        AssertionTopology(environments=())


def test_budget_and_value_overlay_inputs_are_closed() -> None:
    budget = PlanningBudgetValue(
        resource=BudgetResource.TRIALS,
        limit=PositiveCount(value=2),
    )
    with pytest.raises(TypeError, match="BudgetResource"):
        msgspec.structs.replace(budget, resource="trials")
    with pytest.raises(ValueError, match="not admitted"):
        msgspec.structs.replace(budget, resource=BudgetResource.MEMORY_BYTES)
    with pytest.raises(TypeError, match="PositiveCount"):
        msgspec.structs.replace(budget, limit=2)
    with pytest.raises(ValueError, match="at least one"):
        InvocationValueOverlay()
    with pytest.raises(TypeError, match="Environment"):
        InvocationValueOverlay(environment=cast("ResourceId", "dev"), variables=None)
    with pytest.raises(TypeError, match="ValueExpression"):
        InvocationValueOverlay(variables=cast("ValueExpression", "secret"))


def test_run_invocation_rejects_untyped_and_duplicate_override_surfaces() -> None:
    valid = invocation()
    replacements = (
        ("invocation_id", object(), "InvocationId"),
        ("run_id", object(), "RunId"),
        ("execution_profile", object(), "profile"),
        ("subjects", object(), "subject selection"),
        ("topology", object(), "topology"),
        ("matrices", object(), "Matrix selection"),
        ("trials", object(), "count overrides"),
        ("budgets", cast("tuple[PlanningBudgetValue, ...]", []), "budgets"),
        ("values", cast("tuple[InvocationValueOverlay, ...]", []), "values"),
    )
    for field, value, message in replacements:
        with pytest.raises(TypeError, match=message):
            msgspec.structs.replace(valid, **{field: value})

    budget = PlanningBudgetValue(resource=BudgetResource.TRIALS, limit=PositiveCount(value=2))
    with pytest.raises(ValueError, match="budget resources"):
        msgspec.structs.replace(valid, budgets=(budget, budget))


def test_run_invocation_validates_edit_item_types_and_duplicate_value_scopes(
    comparison_project: CompiledProject,
) -> None:
    valid = invocation()
    wrong_subject_edit: CollectionOverridePlan[ResourceId, ResourceId] = CollectionOverridePlan(
        base=ReplaceBase(items=(cast("ResourceId", MatrixBindingId(value="wrong")),))
    )
    with pytest.raises(TypeError, match="ResourceId values"):
        msgspec.structs.replace(valid, subjects=wrong_subject_edit)
    wrong_subject_set = CollectionOverridePlan(
        base=KeepBase(),
        set_items=(
            KeyedSet(
                identity=ResourceId(value="chat"),
                item=cast("ResourceId", MatrixBindingId(value="wrong")),
            ),
        ),
    )
    with pytest.raises(TypeError, match="set values"):
        msgspec.structs.replace(valid, subjects=wrong_subject_set)
    wrong_matrix_edit: CollectionOverridePlan[MatrixBindingId, MatrixSelection] = (
        CollectionOverridePlan(
            base=KeepBase(),
            remove=(cast("MatrixBindingId", ResourceId(value="wrong")),),
        )
    )
    with pytest.raises(TypeError, match="MatrixBindingId"):
        msgspec.structs.replace(valid, matrices=wrong_matrix_edit)
    wrong_matrix_item: CollectionOverridePlan[MatrixBindingId, MatrixSelection] = (
        CollectionOverridePlan(
            base=ReplaceBase(items=(cast("MatrixSelection", ResourceId(value="wrong")),))
        )
    )
    with pytest.raises(TypeError, match="MatrixSelection"):
        msgspec.structs.replace(valid, matrices=wrong_matrix_item)
    with pytest.raises(TypeError, match="PositiveCount"):
        msgspec.structs.replace(valid, trials=SetValue(value=ResourceId(value="wrong")))
    project_spec = cast("CanonicalObject", comparison_project.project.spec)
    variables = cast(
        "ValueExpression",
        next(member.value for member in project_spec.members if member.name == "variables"),
    )
    overlay = InvocationValueOverlay(variables=variables)
    with pytest.raises(ValueError, match="unique Environment"):
        msgspec.structs.replace(valid, values=(overlay, overlay))


def test_planning_authority_requires_exact_complete_nonduplicated_authority() -> None:
    valid = authority()
    with pytest.raises(TypeError, match="DefinitionRef"):
        msgspec.structs.replace(valid, policy=cast("DefinitionRef", object()))
    wrong_policy = msgspec.structs.replace(
        policy_definition(),
        identity=msgspec.structs.replace(
            policy_definition().identity,
            kind=ResourceKind.PROJECT,
        ),
    )
    with pytest.raises(ValueError, match="ExecutionPolicy"):
        msgspec.structs.replace(valid, policy=wrong_policy)
    with pytest.raises(TypeError, match="allowlists"):
        msgspec.structs.replace(valid, allowed_subjects=cast("tuple[ResourceIdentity, ...]", []))
    request_identity = ResourceIdentity(
        namespace=policy_definition().identity.namespace,
        kind=ResourceKind.REQUEST,
        resource_id=ResourceId(value="chat"),
    )
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(valid, allowed_subjects=(request_identity, request_identity))
    with pytest.raises(ValueError, match="invalid resource kind"):
        msgspec.structs.replace(valid, allowed_environments=(request_identity,))
    with pytest.raises(TypeError, match="ceilings"):
        msgspec.structs.replace(valid, ceilings=cast("tuple[PlanningBudgetValue, ...]", []))
    with pytest.raises(ValueError, match="resources must be unique"):
        msgspec.structs.replace(valid, ceilings=(*valid.ceilings, valid.ceilings[0]))
    with pytest.raises(ValueError, match="every RunPlan"):
        msgspec.structs.replace(valid, ceilings=valid.ceilings[:-1])
