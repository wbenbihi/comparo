"""Immutable planner products that retain configuration without resolving values."""

from __future__ import annotations

from typing import TypeGuard
from typing import final

import msgspec

from comparo.core.configuration.canonical import CompiledProject
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.expressions import is_value_expression
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InlineDefinitionRef
from comparo.core.domain.identity import InvocationId
from comparo.core.domain.identity import MatrixBindingId
from comparo.core.domain.identity import MatrixCaseId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import TrialId
from comparo.core.domain.planning import EnvironmentPair
from comparo.core.domain.planning import PlannedCell
from comparo.core.domain.planning import RunPlan
from comparo.core.domain.planning_inputs import PlanningAuthority
from comparo.core.domain.planning_inputs import RunInvocation
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.provenance import ProvenanceKind

type DefinitionHandle = DefinitionRef | InlineDefinitionRef


def is_definition_handle(value: object) -> TypeGuard[DefinitionHandle]:
    """Narrow named and anonymous exact definition references."""
    return type(value) in {DefinitionRef, InlineDefinitionRef}


@final
class PlanningSource(msgspec.Struct, frozen=True, kw_only=True):
    """Value-free origin of one effective planning contribution."""

    kind: ProvenanceKind
    definition: DefinitionHandle | None = None
    invocation_id: InvocationId | None = None

    def __post_init__(self) -> None:
        """Require exactly the coordinate appropriate for the origin category."""
        if type(self.kind) is not ProvenanceKind:
            raise TypeError("planning source kind must be a ProvenanceKind")
        if self.definition is not None and not is_definition_handle(self.definition):
            raise TypeError("planning source definition must be an exact definition handle")
        if self.invocation_id is not None and type(self.invocation_id) is not InvocationId:
            raise TypeError("planning source invocation must be an InvocationId")
        if self.kind is ProvenanceKind.INVOCATION:
            valid = self.invocation_id is not None and self.definition is None
        elif self.kind is ProvenanceKind.VERSIONED_DEFAULT:
            valid = self.invocation_id is None and self.definition is None
        else:
            valid = self.invocation_id is None and self.definition is not None
        if not valid:
            raise ValueError("planning source must carry exactly its matching origin coordinate")


@final
class EffectiveFieldProvenance(msgspec.Struct, frozen=True, kw_only=True):
    """Ordered sources that produced one named effective field."""

    field: str
    sources: tuple[PlanningSource, ...]

    def __post_init__(self) -> None:
        """Require a bounded field name and a non-empty immutable origin chain."""
        if type(self.field) is not str or not self.field or len(self.field) > 64:
            raise ValueError("effective field name must be bounded non-empty text")
        if type(self.sources) is not tuple or any(
            type(source) is not PlanningSource for source in self.sources
        ):
            raise TypeError("effective field sources must be a typed immutable tuple")
        if not self.sources:
            raise ValueError("effective field provenance must not be empty")


@final
class MatrixCaseInput(msgspec.Struct, frozen=True, kw_only=True):
    """One stable Matrix case and its still-unresolved named input object."""

    case_id: MatrixCaseId
    values: ValueExpression

    def __post_init__(self) -> None:
        """Keep identity static while allowing runtime-resolvable case values."""
        if type(self.case_id) is not MatrixCaseId:
            raise TypeError("Matrix case input requires a MatrixCaseId")
        if not is_value_expression(self.values):
            raise TypeError("Matrix case values must be a ValueExpression")


@final
class EffectiveMatrix(msgspec.Struct, frozen=True, kw_only=True):
    """One bound Matrix attachment with its compact ordered case inputs."""

    binding: MatrixBindingId
    definition: DefinitionRef
    cases: tuple[MatrixCaseInput, ...]

    def __post_init__(self) -> None:
        """Require a named Matrix definition and non-empty unique cases."""
        if type(self.binding) is not MatrixBindingId:
            raise TypeError("effective Matrix binding must be a MatrixBindingId")
        if type(self.definition) is not DefinitionRef:
            raise TypeError("effective Matrix requires a DefinitionRef")
        if self.definition.identity.kind is not ResourceKind.MATRIX:
            raise ValueError("effective Matrix definition must identify Matrix")
        if type(self.cases) is not tuple or any(
            type(case) is not MatrixCaseInput for case in self.cases
        ):
            raise TypeError("effective Matrix cases must be a typed immutable tuple")
        if not self.cases:
            raise ValueError("effective Matrix requires at least one case")
        identities = tuple(case.case_id for case in self.cases)
        if len(identities) != len(set(identities)):
            raise ValueError("effective Matrix case identities must be unique")


@final
class EffectiveSubject(msgspec.Struct, frozen=True, kw_only=True):
    """One executable subject and all Matrix attachments that shape its cells."""

    definition: DefinitionRef
    matrices: tuple[EffectiveMatrix, ...]
    profiles: EffectiveProfiles

    def __post_init__(self) -> None:
        """Require a Request/Scenario and unambiguous ordered Matrix bindings."""
        if type(self.definition) is not DefinitionRef:
            raise TypeError("effective subject requires a DefinitionRef")
        if self.definition.identity.kind not in {ResourceKind.REQUEST, ResourceKind.SCENARIO}:
            raise ValueError("effective subject must identify Request or Scenario")
        if type(self.matrices) is not tuple or any(
            type(matrix) is not EffectiveMatrix for matrix in self.matrices
        ):
            raise TypeError("effective subject matrices must be a typed immutable tuple")
        bindings = tuple(matrix.binding for matrix in self.matrices)
        if len(bindings) != len(set(bindings)):
            raise ValueError("effective subject Matrix bindings must be unique")
        if type(self.profiles) is not EffectiveProfiles:
            raise TypeError("effective subject profiles must be EffectiveProfiles")


@final
class EffectiveProfiles(msgspec.Struct, frozen=True, kw_only=True):
    """Typed profile attachments after deterministic include expansion."""

    assertions: tuple[DefinitionHandle, ...] = ()
    diffs: tuple[DefinitionHandle, ...] = ()
    gate: DefinitionHandle | None = None
    safety: tuple[DefinitionHandle, ...] = ()
    redaction: DefinitionHandle | None = None
    artifacts: DefinitionHandle | None = None

    def __post_init__(self) -> None:
        """Require each attachment to have the exact resource kind owned by its field."""
        _validate_handles(self.assertions, ResourceKind.ASSERTION_PROFILE, "assertion")
        _validate_handles(self.diffs, ResourceKind.DIFF_PROFILE, "diff")
        _validate_optional_handle(self.gate, ResourceKind.GATE_PROFILE, "gate")
        _validate_handles(self.safety, ResourceKind.SAFETY_PROFILE, "safety")
        _validate_optional_handle(self.redaction, ResourceKind.REDACTION_PROFILE, "redaction")
        _validate_optional_handle(self.artifacts, ResourceKind.ARTIFACT_PROFILE, "artifact")


@final
class ValueBindingLayer(msgspec.Struct, frozen=True, kw_only=True):
    """One ordered unresolved namespace layer, optionally Environment-specific."""

    source: PlanningSource
    environment: DefinitionRef | None = None
    parameters: ValueExpression | None = None
    variables: ValueExpression | None = None
    secrets: ValueExpression | None = None

    def __post_init__(self) -> None:
        """Retain expressions and safe coordinates only."""
        if type(self.source) is not PlanningSource:
            raise TypeError("value binding layer requires a PlanningSource")
        if self.environment is not None and (
            type(self.environment) is not DefinitionRef
            or self.environment.identity.kind is not ResourceKind.ENVIRONMENT
        ):
            raise ValueError("value binding layer Environment must identify Environment")
        for expression in (self.parameters, self.variables, self.secrets):
            if expression is not None and not is_value_expression(expression):
                raise TypeError("value binding layer values must be ValueExpression values")
        if self.parameters is self.variables is self.secrets is None:
            raise ValueError("value binding layer must define at least one namespace")


@final
class EffectiveExecution(msgspec.Struct, frozen=True, kw_only=True):
    """Fully bound structural execution that still contains unresolved expressions."""

    subjects: tuple[EffectiveSubject, ...]
    environments: tuple[DefinitionRef, ...]
    comparisons: tuple[EnvironmentPair, ...]
    trials: PositiveCount
    maximum_attempts: PositiveCount
    value_layers: tuple[ValueBindingLayer, ...]
    provenance: tuple[EffectiveFieldProvenance, ...]

    def __post_init__(self) -> None:
        """Reject empty or contradictory effective execution structure."""
        if type(self.subjects) is not tuple or any(
            type(subject) is not EffectiveSubject for subject in self.subjects
        ):
            raise TypeError("effective subjects must be a typed immutable tuple")
        if not self.subjects:
            raise ValueError("effective execution requires at least one subject")
        if type(self.environments) is not tuple or any(
            type(environment) is not DefinitionRef
            or environment.identity.kind is not ResourceKind.ENVIRONMENT
            for environment in self.environments
        ):
            raise TypeError("effective Environments must be a typed immutable tuple")
        if not self.environments:
            raise ValueError("effective execution requires at least one Environment")
        if type(self.comparisons) is not tuple or any(
            type(pair) is not EnvironmentPair for pair in self.comparisons
        ):
            raise TypeError("effective comparisons must be a typed immutable tuple")
        if (
            type(self.trials) is not PositiveCount
            or type(self.maximum_attempts) is not PositiveCount
        ):
            raise TypeError("effective repetition settings must be PositiveCount values")
        if type(self.value_layers) is not tuple or any(
            type(layer) is not ValueBindingLayer for layer in self.value_layers
        ):
            raise TypeError("effective value layers must be a typed immutable tuple")
        if type(self.provenance) is not tuple or any(
            type(entry) is not EffectiveFieldProvenance for entry in self.provenance
        ):
            raise TypeError("effective provenance must be a typed immutable tuple")


@final
class PreparedRun(msgspec.Struct, frozen=True, kw_only=True):
    """A compact admitted plan paired with inputs required by later resolution."""

    project: CompiledProject
    invocation: RunInvocation
    authority: PlanningAuthority
    effective: EffectiveExecution
    plan: RunPlan

    def __post_init__(self) -> None:
        """Require one coherent typed planning product."""
        if type(self.project) is not CompiledProject:
            raise TypeError("prepared run project must be a CompiledProject")
        if type(self.invocation) is not RunInvocation:
            raise TypeError("prepared run invocation must be a RunInvocation")
        if type(self.authority) is not PlanningAuthority:
            raise TypeError("prepared run authority must be PlanningAuthority")
        if type(self.effective) is not EffectiveExecution:
            raise TypeError("prepared run effective settings must be EffectiveExecution")
        if type(self.plan) is not RunPlan:
            raise TypeError("prepared run plan must be a RunPlan")
        if self.plan.run_id != self.invocation.run_id:
            raise ValueError("prepared run plan and invocation must share a RunId")


@final
class PlannedTrial(msgspec.Struct, frozen=True, kw_only=True):
    """One deterministic trial occurrence over one lazily generated plan cell."""

    trial_id: TrialId
    cell: PlannedCell
    ordinal: Count

    def __post_init__(self) -> None:
        """Require a stable identity, a PlannedCell, and a zero-based ordinal."""
        if type(self.trial_id) is not TrialId:
            raise TypeError("planned trial requires a TrialId")
        if type(self.cell) is not PlannedCell:
            raise TypeError("planned trial cell must be a PlannedCell")
        if type(self.ordinal) is not Count:
            raise TypeError("planned trial ordinal must be a Count")


@final
class PlanningResult(msgspec.Struct, frozen=True, kw_only=True):
    """Fail-closed planning outcome with safe structured diagnostics."""

    prepared: PreparedRun | None = None
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        """Return exactly one successful product or one or more diagnostics."""
        if self.prepared is not None and type(self.prepared) is not PreparedRun:
            raise TypeError("planning result product must be a PreparedRun")
        if type(self.diagnostics) is not tuple or any(
            type(diagnostic) is not Diagnostic for diagnostic in self.diagnostics
        ):
            raise TypeError("planning result diagnostics must be a typed immutable tuple")
        if (self.prepared is None) == (not self.diagnostics):
            raise ValueError("planning result must contain exactly a product or diagnostics")


def _handle_kind(handle: DefinitionHandle) -> ResourceKind:
    return handle.identity.kind if type(handle) is DefinitionRef else handle.kind


def _validate_handles(
    handles: tuple[DefinitionHandle, ...],
    kind: ResourceKind,
    label: str,
) -> None:
    if type(handles) is not tuple or any(not is_definition_handle(item) for item in handles):
        raise TypeError(f"effective {label} profiles must be exact definition handles")
    if any(_handle_kind(item) is not kind for item in handles):
        raise ValueError(f"effective {label} profile has the wrong resource kind")
    if len(handles) != len(set(handles)):
        raise ValueError(f"effective {label} profiles must be unique")


def _validate_optional_handle(
    handle: DefinitionHandle | None,
    kind: ResourceKind,
    label: str,
) -> None:
    if handle is None:
        return
    if not is_definition_handle(handle):
        raise TypeError(f"effective {label} profile must be an exact definition handle")
    if _handle_kind(handle) is not kind:
        raise ValueError(f"effective {label} profile has the wrong resource kind")
