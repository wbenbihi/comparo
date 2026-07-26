"""Compact Plan topology, cardinality, provenance, and consistency contracts."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.application.plan_inspection_authority import PlanArtifactAuthorityInspection
from comparo.application.plan_inspection_authority import PlanHttpAuthorityInspection
from comparo.application.plan_inspection_authority import PlanValueAuthorityInspection
from comparo.application.plan_inspection_identity import ResourceCoordinate
from comparo.application.plan_inspection_identity import is_sha256
from comparo.application.plan_inspection_profiles import PlanProfilesInspection
from comparo.application.plan_inspection_profiles import PlanValidationInspection
from comparo.application.plan_sources import SensitiveSourceInspection
from comparo.core.domain.http.fields import HttpMethod
from comparo.core.domain.http.fields import is_read_only_http_method


@final
class PlanCardinalityInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Exact admitted work counts without eagerly expanding cells."""

    cells: int
    trials: int
    environment_executions: int
    comparisons: int
    maximum_attempts: int

    def __post_init__(self) -> None:
        """Require finite non-negative logical work counts."""
        values = (
            self.cells,
            self.trials,
            self.environment_executions,
            self.comparisons,
            self.maximum_attempts,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("plan cardinalities must be non-negative exact integers")
        if self.maximum_attempts < self.environment_executions:
            raise ValueError("maximum attempts cannot be below planned executions")


@final
class MatrixAxisInspection(msgspec.Struct, frozen=True, kw_only=True):
    """One compact Matrix axis and its stable case identities."""

    binding: str
    matrix: ResourceCoordinate
    cases: tuple[str, ...]

    def __post_init__(self) -> None:
        """Require one named Matrix revision and unique non-empty case IDs."""
        if type(self.binding) is not str or not self.binding:
            raise ValueError("Matrix binding must be non-empty text")
        if type(self.matrix) is not ResourceCoordinate or self.matrix.kind != "Matrix":
            raise TypeError("Matrix axis must identify a Matrix resource")
        if (
            type(self.cases) is not tuple
            or not self.cases
            or any(type(item) is not str or not item for item in self.cases)
        ):
            raise ValueError("Matrix cases must be a non-empty typed tuple")
        if len(self.cases) != len(set(self.cases)):
            raise ValueError("Matrix cases must be unique")


@final
class EnvironmentPairInspection(msgspec.Struct, frozen=True, kw_only=True):
    """One directed baseline-to-candidate comparison."""

    baseline: ResourceCoordinate
    candidate: ResourceCoordinate

    def __post_init__(self) -> None:
        """Require two distinct Environment revisions."""
        if any(
            type(item) is not ResourceCoordinate or item.kind != "Environment"
            for item in (self.baseline, self.candidate)
        ):
            raise TypeError("Environment pair must contain Environment coordinates")
        if self.baseline == self.candidate:
            raise ValueError("Environment pair baseline and candidate must differ")


@final
class PlanSliceInspection(msgspec.Struct, frozen=True, kw_only=True):
    """One executable subject's compact, value-unresolved recipe."""

    subject: ResourceCoordinate
    matrices: tuple[MatrixAxisInspection, ...]
    trials: int
    environments: tuple[ResourceCoordinate, ...]
    comparisons: tuple[EnvironmentPairInspection, ...]
    maximum_attempts_per_execution: int
    cardinality: PlanCardinalityInspection
    method: str | None = None
    unsafe_method: bool | None = None
    profiles: PlanProfilesInspection = PlanProfilesInspection()

    def __post_init__(self) -> None:
        """Require the compact slice and every projected cardinality to agree."""
        _validate_plan_slice_shape(self)
        _validate_plan_slice_cardinality(self)
        _validate_plan_slice_topology(self)
        if type(self.profiles) is not PlanProfilesInspection:
            raise TypeError("plan slice profiles must be typed")
        if self.method is None:
            if self.unsafe_method is not None:
                raise ValueError("unresolved plan method cannot carry a safety classification")
            return
        method = HttpMethod(value=self.method)
        expected = not is_read_only_http_method(method)
        if self.unsafe_method is not expected:
            raise ValueError("plan method safety classification is contradictory")


@final
class BudgetInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Requested, effective, and operator-capped planning budget."""

    resource: str
    requested: int
    effective: int
    cap: int

    def __post_init__(self) -> None:
        """Require one coherent non-negative requested/effective/cap triple."""
        if type(self.resource) is not str or not self.resource or len(self.resource) > 127:
            raise ValueError("budget resource must be bounded non-empty text")
        values = (self.requested, self.effective, self.cap)
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("budget values must be non-negative exact integers")
        if self.effective > self.requested or self.effective > self.cap:
            raise ValueError("effective budget cannot exceed request or policy cap")


@final
class InlineSourceCoordinate(msgspec.Struct, frozen=True, kw_only=True):
    """Exact inline-definition revision and stable owning resource identity."""

    owner_namespace: str
    owner_kind: str
    owner_resource_id: str
    fragment: str
    fingerprint: str

    def __post_init__(self) -> None:
        """Require bounded identity text and a complete revision digest."""
        values = (self.owner_namespace, self.owner_kind, self.owner_resource_id, self.fragment)
        if any(type(item) is not str or not item for item in values):
            raise ValueError("inline source coordinate requires non-empty identity text")
        if not is_sha256(self.fingerprint):
            raise ValueError("inline source fingerprint must be a SHA-256 hex digest")


@final
class PlanningSourceInspection(msgspec.Struct, frozen=True, kw_only=True):
    """One exact typed planning source without lossy string formatting."""

    kind: str
    definition: ResourceCoordinate | None = None
    inline: InlineSourceCoordinate | None = None
    invocation_id: str | None = None

    def __post_init__(self) -> None:
        """Keep named, inline, invocation, and versioned-default sources disjoint."""
        if type(self.kind) is not str or not self.kind:
            raise ValueError("planning source kind must be non-empty text")
        populated = sum(
            item is not None for item in (self.definition, self.inline, self.invocation_id)
        )
        if populated > 1:
            raise ValueError("planning source identities are mutually exclusive")
        if self.definition is not None and type(self.definition) is not ResourceCoordinate:
            raise TypeError("named planning source must be a ResourceCoordinate")
        if self.inline is not None and type(self.inline) is not InlineSourceCoordinate:
            raise TypeError("inline planning source must be an InlineSourceCoordinate")
        if self.invocation_id is not None and (
            type(self.invocation_id) is not str or not self.invocation_id
        ):
            raise ValueError("planning invocation source must be non-empty text")


@final
class ProvenanceInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Ordered value-free origins for one effective planning field."""

    field: str
    sources: tuple[PlanningSourceInspection, ...]

    def __post_init__(self) -> None:
        """Require one stable field and immutable typed exact sources."""
        if type(self.field) is not str or not self.field:
            raise ValueError("provenance field must be non-empty text")
        if type(self.sources) is not tuple or any(
            type(item) is not PlanningSourceInspection for item in self.sources
        ):
            raise TypeError("provenance sources must be a typed immutable tuple")


@final
class PlanInspection(msgspec.Struct, frozen=True, kw_only=True):
    """Complete secret-free preview shared by human and machine renderers."""

    api_version: str
    run_id: str
    invocation_id: str
    fingerprint: str
    ordering: str
    policy: ResourceCoordinate
    cardinality: PlanCardinalityInspection
    environment_count: int
    write_capable_subjects: int
    slices: tuple[PlanSliceInspection, ...]
    budgets: tuple[BudgetInspection, ...]
    provenance: tuple[ProvenanceInspection, ...]
    validation: PlanValidationInspection | None = None
    sensitive_sources: tuple[SensitiveSourceInspection, ...] = ()
    http_authority: PlanHttpAuthorityInspection | None = None
    value_authority: PlanValueAuthorityInspection | None = None
    artifact_authority: PlanArtifactAuthorityInspection | None = None

    def __post_init__(self) -> None:
        """Reject a frontend projection that contradicts the admitted compact plan."""
        if self.api_version != "comparo.plan/v1alpha1":
            raise ValueError("plan inspection API version is unsupported")
        if not is_sha256(self.fingerprint):
            raise ValueError("plan fingerprint must be a SHA-256 hex digest")
        if type(self.policy) is not ResourceCoordinate or self.policy.kind != "ExecutionPolicy":
            raise ValueError("plan inspection policy must identify an ExecutionPolicy")
        if type(self.slices) is not tuple or any(
            type(item) is not PlanSliceInspection for item in self.slices
        ):
            raise TypeError("plan inspection slices must be a typed immutable tuple")
        if not self.slices:
            raise ValueError("plan inspection requires at least one admitted slice")
        if type(self.cardinality) is not PlanCardinalityInspection:
            raise TypeError("plan inspection cardinality must be typed")
        _validate_plan_topology_counts(self)
        expected = PlanCardinalityInspection(
            cells=sum(item.cardinality.cells for item in self.slices),
            trials=sum(item.cardinality.trials for item in self.slices),
            environment_executions=sum(
                item.cardinality.environment_executions for item in self.slices
            ),
            comparisons=sum(item.cardinality.comparisons for item in self.slices),
            maximum_attempts=sum(item.cardinality.maximum_attempts for item in self.slices),
        )
        if self.cardinality != expected:
            raise ValueError("plan inspection cardinality contradicts its slices")
        if type(self.budgets) is not tuple or any(
            type(item) is not BudgetInspection for item in self.budgets
        ):
            raise TypeError("plan inspection budgets must be a typed immutable tuple")
        if type(self.provenance) is not tuple or any(
            type(item) is not ProvenanceInspection for item in self.provenance
        ):
            raise TypeError("plan inspection provenance must be a typed immutable tuple")
        _validate_plan_authorities(self)
        _validate_plan_validation(self)
        _validate_rule_attachments(self)


def _validate_plan_topology_counts(value: PlanInspection) -> None:
    if type(value.environment_count) is not int or value.environment_count < 1:
        raise ValueError("plan inspection Environment count must be positive")
    if (
        type(value.write_capable_subjects) is not int
        or value.write_capable_subjects < 0
        or value.write_capable_subjects > len(value.slices)
    ):
        raise ValueError("plan inspection write-capable subject count is invalid")
    if value.write_capable_subjects != sum(item.unsafe_method is True for item in value.slices):
        raise ValueError("plan inspection write-capable subject count contradicts slices")
    environment_identities = {
        (environment.namespace, environment.resource_id)
        for item in value.slices
        for environment in item.environments
    }
    if value.environment_count != len(environment_identities):
        raise ValueError("plan inspection Environment count contradicts slices")


def _validate_plan_authorities(value: PlanInspection) -> None:
    if (
        value.http_authority is not None
        and type(value.http_authority) is not PlanHttpAuthorityInspection
    ):
        raise TypeError("plan HTTP authority must be typed or absent")
    if value.http_authority is not None:
        allowed = set(value.http_authority.allowed_methods)
        if any(item.method is not None and item.method not in allowed for item in value.slices):
            raise ValueError("plan method is absent from ExecutionPolicy HTTP authority")
    if type(value.sensitive_sources) is not tuple or any(
        type(item) is not SensitiveSourceInspection for item in value.sensitive_sources
    ):
        raise TypeError("plan sensitive sources must be a typed immutable tuple")
    if (
        value.value_authority is not None
        and type(value.value_authority) is not PlanValueAuthorityInspection
    ):
        raise TypeError("plan value authority must be typed or absent")
    if (
        value.artifact_authority is not None
        and type(value.artifact_authority) is not PlanArtifactAuthorityInspection
    ):
        raise TypeError("plan artifact authority must be typed or absent")
    if value.validation is not None and type(value.validation) is not PlanValidationInspection:
        raise TypeError("plan validation inspection must be typed or absent")


def _validate_plan_validation(value: PlanInspection) -> None:
    if value.validation is None:
        return
    attached = {
        (
            item.profiles.gate.namespace,
            item.profiles.gate.resource_id,
            item.profiles.gate.fingerprint,
        )
        for item in value.slices
        if item.profiles.gate is not None
    }
    gate = value.validation.gate
    if gate is None:
        if attached:
            raise ValueError("attached GateProfile is absent from compiled validation")
        return
    profile = gate.profile
    expected = {(profile.namespace, profile.resource_id, profile.fingerprint)}
    if attached != expected:
        raise ValueError("compiled GateProfile differs from Plan attachments")


def _validate_rule_attachments(value: PlanInspection) -> None:
    validation = value.validation
    if validation is None:
        return
    if validation.assertion_rules and not any(item.profiles.assertions for item in value.slices):
        raise ValueError("compiled assertion rules require an attached AssertionProfile")
    if validation.diff_rules and not any(item.profiles.diffs for item in value.slices):
        raise ValueError("compiled diff rules require an attached DiffProfile")


def _validate_plan_slice_shape(value: PlanSliceInspection) -> None:
    if type(value.subject) is not ResourceCoordinate or value.subject.kind not in {
        "Request",
        "Scenario",
    }:
        raise TypeError("plan slice subject must identify a Request or Scenario")
    if type(value.matrices) is not tuple or any(
        type(item) is not MatrixAxisInspection for item in value.matrices
    ):
        raise TypeError("plan slice matrices must be a typed immutable tuple")
    if (
        type(value.environments) is not tuple
        or not value.environments
        or any(
            type(item) is not ResourceCoordinate or item.kind != "Environment"
            for item in value.environments
        )
    ):
        raise ValueError("plan slice requires typed Environment coordinates")
    if len(value.environments) != len(set(value.environments)):
        raise ValueError("plan slice Environments must be unique")
    if type(value.comparisons) is not tuple or any(
        type(item) is not EnvironmentPairInspection for item in value.comparisons
    ):
        raise TypeError("plan slice comparisons must be a typed immutable tuple")
    if len(value.comparisons) != len(set(value.comparisons)):
        raise ValueError("plan slice Environment pairs must be unique")
    if type(value.cardinality) is not PlanCardinalityInspection:
        raise TypeError("plan slice cardinality must be typed")
    if type(value.trials) is not int or value.trials < 1:
        raise ValueError("plan slice trials must be a positive exact integer")
    if (
        type(value.maximum_attempts_per_execution) is not int
        or value.maximum_attempts_per_execution < 1
    ):
        raise ValueError("plan slice maximum attempts must be a positive exact integer")


def _validate_plan_slice_cardinality(value: PlanSliceInspection) -> None:
    cells = 1
    bindings: set[str] = set()
    for matrix in value.matrices:
        if not matrix.cases:
            raise ValueError("plan slice Matrix axes require at least one case")
        if matrix.binding in bindings:
            raise ValueError("plan slice Matrix bindings must be unique")
        bindings.add(matrix.binding)
        cells *= len(matrix.cases)
    sampled_trials = cells * value.trials
    expected = PlanCardinalityInspection(
        cells=cells,
        trials=sampled_trials,
        environment_executions=sampled_trials * len(value.environments),
        comparisons=sampled_trials * len(value.comparisons),
        maximum_attempts=(
            sampled_trials * len(value.environments) * value.maximum_attempts_per_execution
        ),
    )
    if value.cardinality != expected:
        raise ValueError("plan slice cardinality contradicts its compact recipe")


def _validate_plan_slice_topology(value: PlanSliceInspection) -> None:
    selected = set(value.environments)
    if any(
        item.baseline not in selected or item.candidate not in selected
        for item in value.comparisons
    ):
        raise ValueError("plan comparison Environments must be admitted by the slice")


__all__ = (
    "BudgetInspection",
    "EnvironmentPairInspection",
    "InlineSourceCoordinate",
    "MatrixAxisInspection",
    "PlanCardinalityInspection",
    "PlanInspection",
    "PlanSliceInspection",
    "PlanningSourceInspection",
    "ProvenanceInspection",
)
