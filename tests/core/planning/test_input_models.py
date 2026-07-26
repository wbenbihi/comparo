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
from comparo.core.domain.identity import MatrixCaseId
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
from comparo.core.domain.planning_inputs import InvocationEnvironmentValue
from comparo.core.domain.planning_inputs import InvocationRequestEdit
from comparo.core.domain.planning_inputs import InvocationValueOverlay
from comparo.core.domain.planning_inputs import MatrixSelection
from comparo.core.domain.planning_inputs import PlanningBudgetValue
from comparo.core.domain.planning_profiles import InvocationProfileOverrides
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.values import JsonString
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
    with pytest.raises(TypeError, match="MatrixCaseId tuple"):
        msgspec.structs.replace(selection, cases=cast("tuple[MatrixCaseId, ...]", [object()]))
    case = MatrixCaseId(value="one")
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(selection, cases=(case, case))
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
    with pytest.raises(ValueError, match="not bound"):
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
        ("fail_fast", object(), "boolean overrides"),
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


def test_invocation_environment_values_and_request_edits_are_closed(
    comparison_project: CompiledProject,
) -> None:
    environment_value = InvocationEnvironmentValue(
        environment=ResourceId(value="dev"),
        symbol=SymbolPath(segments=("TOKEN",)),
        value=SensitiveValue(
            JsonString(value="secret"),
            origins=(TaintOriginId(value="test/invocation-environment"),),
        ),
    )
    with pytest.raises(TypeError, match="ResourceId"):
        msgspec.structs.replace(environment_value, environment=object())
    with pytest.raises(ValueError, match="one segment"):
        msgspec.structs.replace(
            environment_value,
            symbol=SymbolPath(segments=("TOKEN", "nested")),
        )
    with pytest.raises(TypeError, match="sealed JsonString"):
        msgspec.structs.replace(environment_value, value=JsonString(value="raw"))

    project_spec = cast("CanonicalObject", comparison_project.project.spec)
    expression = cast(
        "ValueExpression",
        next(member.value for member in project_spec.members if member.name == "variables"),
    )
    request_edit = InvocationRequestEdit(
        subject=ResourceId(value="chat"),
        method=expression,
    )
    with pytest.raises(TypeError, match="subject ResourceId"):
        msgspec.structs.replace(request_edit, subject=object())
    with pytest.raises(ValueError, match="at least one field"):
        InvocationRequestEdit(subject=ResourceId(value="chat"))
    with pytest.raises(TypeError, match="ValueExpression"):
        msgspec.structs.replace(request_edit, method=object())

    valid = invocation()
    with pytest.raises(TypeError, match="profile overrides"):
        msgspec.structs.replace(valid, profiles=object())
    with pytest.raises(TypeError, match="Environment values"):
        msgspec.structs.replace(valid, environment_values=object())
    with pytest.raises(ValueError, match="coordinates must be unique"):
        msgspec.structs.replace(
            valid,
            environment_values=(environment_value, environment_value),
        )
    with pytest.raises(TypeError, match="Request edits"):
        msgspec.structs.replace(valid, requests=object())
    with pytest.raises(ValueError, match="unique subjects"):
        msgspec.structs.replace(valid, requests=(request_edit, request_edit))


def test_invocation_profile_overrides_reject_cross_kind_values() -> None:
    profiles = InvocationProfileOverrides()
    with pytest.raises(TypeError, match="gate profile"):
        msgspec.structs.replace(profiles, gate=SetValue(value=MatrixBindingId(value="wrong")))
    with pytest.raises(TypeError, match="assertion profile selection"):
        msgspec.structs.replace(profiles, assertions=object())
    with pytest.raises(TypeError, match="selection identities"):
        msgspec.structs.replace(
            profiles,
            assertions=CollectionOverridePlan(
                base=ReplaceBase(items=(cast("ResourceId", MatrixBindingId(value="wrong")),))
            ),
        )
    with pytest.raises(TypeError, match="set values"):
        msgspec.structs.replace(
            profiles,
            assertions=CollectionOverridePlan(
                base=KeepBase(),
                set_items=(
                    KeyedSet(
                        identity=ResourceId(value="named"),
                        item=cast("ResourceId", MatrixBindingId(value="wrong")),
                    ),
                ),
            ),
        )


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
    with pytest.raises(ValueError, match="every planning and scheduling"):
        msgspec.structs.replace(valid, ceilings=valid.ceilings[:-1])
