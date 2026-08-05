"""Pure projections for Scenario, execution, policy, and profile resources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from comparo.application.profile_capabilities import ProfileFieldSupport
from comparo.application.profile_capabilities import profile_resource_capabilities
from comparo.core.domain.identity import ResourceKind
from comparo.tui4.models.explorer import ResourceEntry
from comparo.tui4.models.resource_presenters import ResourceDependency
from comparo.tui4.models.resource_presenters import ResourceSetting
from comparo.tui4.models.resource_presenters import resource_dependencies
from comparo.tui4.models.resource_presenters import resource_setting
from comparo.tui4.models.resource_presenters import resource_settings

if TYPE_CHECKING:
    from comparo.application.project_outline import ProjectOutlineField


@dataclass(frozen=True)
class ScenarioStep:
    """One authored Scenario step and its ordering references."""

    id: str
    request: str
    after: str
    definition: ResourceSetting


@dataclass(frozen=True)
class ScenarioOverview:
    """Future Scenario declaration without implying current executability."""

    parameters: tuple[ResourceSetting, ...]
    matrices: tuple[ResourceSetting, ...]
    matrix_count: int
    steps: tuple[ScenarioStep, ...]
    evaluation: tuple[ResourceSetting, ...]
    consumers: tuple[ResourceDependency, ...]


@dataclass(frozen=True)
class ExecutionProfileOverview:
    """Authored selection fragments, controls, and attachments for a named run plan."""

    authored_selection: str
    declared_subject_count: int
    declared_environment_count: int
    declared_trials: int | None
    selection: tuple[ResourceSetting, ...]
    controls: tuple[ResourceSetting, ...]
    inputs: tuple[ResourceSetting, ...]
    evaluation: tuple[ResourceSetting, ...]
    consumers: tuple[ResourceDependency, ...]


@dataclass(frozen=True)
class RuleSideSummary:
    """One selector and transform/reducer pipeline for a rule side."""

    role: str
    selector: str
    pipeline: str


@dataclass(frozen=True)
class RuleSummary:
    """One value-safe assertion or asymmetric diff rule summary."""

    id: str
    name_declared: bool
    description_declared: bool
    tags: tuple[str, ...]
    sides: tuple[RuleSideSummary, ...]
    combination: str
    severity: str
    completeness: str
    quantifier: str | None


@dataclass(frozen=True)
class RuleProfileOverview:
    """Rules plus deterministic composition controls for assertion or diff profiles."""

    family: str
    rules: tuple[RuleSummary, ...]
    composition: tuple[ResourceSetting, ...]
    parameters: tuple[ResourceSetting, ...]
    consumers: tuple[ResourceDependency, ...]


@dataclass(frozen=True)
class GateProfileOverview:
    """Gate scope, completeness requirements, and adverse-fact thresholds."""

    scope: tuple[ResourceSetting, ...]
    requirements: tuple[ResourceSetting, ...]
    thresholds: tuple[ResourceSetting, ...]
    consumers: tuple[ResourceDependency, ...]


@dataclass(frozen=True)
class SafetyProfileOverview:
    """Composed project safety restrictions and classification policy."""

    composition: tuple[ResourceSetting, ...]
    schemas: tuple[ResourceSetting, ...]
    classification: tuple[ResourceSetting, ...]
    budgets: tuple[ResourceSetting, ...]
    unsupported: tuple[ResourceSetting, ...]
    unsupported_support: ProfileFieldSupport
    consumers: tuple[ResourceDependency, ...]


@dataclass(frozen=True)
class RedactionProfileOverview:
    """Sanitization selectors, opaque pattern inventory, and backstop controls."""

    rules: tuple[ResourceSetting, ...]
    patterns: tuple[ResourceSetting, ...]
    backstop: tuple[ResourceSetting, ...]
    patterns_support: ProfileFieldSupport
    backstop_support: ProfileFieldSupport
    consumers: tuple[ResourceDependency, ...]


@dataclass(frozen=True)
class ArtifactProfileOverview:
    """Sanitized artifact capture, persistence, and retention controls."""

    capture: tuple[ResourceSetting, ...]
    storage: tuple[ResourceSetting, ...]
    retention: tuple[ResourceSetting, ...]
    retention_support: ProfileFieldSupport
    capture_control_count: int
    storage_control_count: int
    consumers: tuple[ResourceDependency, ...]


@dataclass(frozen=True)
class ExecutionPolicyOverview:
    """Operator-owned execution authority grouped by capability family."""

    planning: tuple[ResourceSetting, ...]
    http: tuple[ResourceSetting, ...]
    values: tuple[ResourceSetting, ...]
    artifacts: tuple[ResourceSetting, ...]


def _outline_fields(entry: ResourceEntry) -> tuple[ProjectOutlineField, ...]:
    outline = entry.summary.outline
    return () if outline is None else outline.fields


def _field_at(entry: ResourceEntry, path: tuple[str, ...]) -> ProjectOutlineField | None:
    return next((field for field in _outline_fields(entry) if field.path == path), None)


def _display(field: ProjectOutlineField | None, fallback: str = "—") -> str:
    if field is None:
        return fallback
    return field.exact_value if field.exact_value is not None else field.value


def _top_settings(
    entry: ResourceEntry,
    names: frozenset[str],
) -> tuple[ResourceSetting, ...]:
    return resource_settings(entry, names=names, structural_exact=True)


def _consumers(entry: ResourceEntry) -> tuple[ResourceDependency, ...]:
    return tuple(edge for edge in resource_dependencies(entry) if edge.direction == "used-by")


def scenario_overview(entry: ResourceEntry) -> ScenarioOverview:
    """Project a Scenario's steps and attachments without claiming runtime support."""
    steps: list[ScenarioStep] = []
    for field in _outline_fields(entry):
        if len(field.path) != 2 or field.path[0] != "steps" or field.category != "container":
            continue
        root = field.path
        step_id = _display(_field_at(entry, (*root, "id")), f"step {root[1]}")
        definition = resource_setting(
            entry,
            root,
            name=step_id,
            structural_exact=True,
        )
        if definition is None:
            continue
        steps.append(
            ScenarioStep(
                id=step_id,
                request=_display(_field_at(entry, (*root, "request"))),
                after=_display(_field_at(entry, (*root, "after")), "start"),
                definition=definition,
            )
        )
    dependencies = resource_dependencies(entry)
    return ScenarioOverview(
        parameters=_top_settings(entry, frozenset({"parameters"})),
        matrices=_top_settings(entry, frozenset({"matrices"})),
        matrix_count=len(
            {
                (edge.target_kind, edge.target_id)
                for edge in dependencies
                if edge.direction == "uses"
                and edge.path[:1] == ("matrices",)
                and edge.target_kind == "Matrix"
            }
        ),
        steps=tuple(steps),
        evaluation=_top_settings(entry, frozenset({"assertions", "diffs", "safety"})),
        consumers=_consumers(entry),
    )


_EXECUTION_SELECTION = frozenset({"subjects", "baseline", "candidates", "environments", "matrices"})
_EXECUTION_CONTROLS = frozenset({"trials", "maximumAttempts", "failFast", "budgets"})
_EXECUTION_INPUTS = frozenset({"parameters", "variables", "secrets"})
_EXECUTION_EVALUATION = frozenset(
    {"assertions", "diffs", "gate", "safety", "redaction", "artifacts"}
)


def execution_profile_overview(entry: ResourceEntry) -> ExecutionProfileOverview:
    """Project only this profile's authored fragments, never an effective invocation."""
    dependencies = tuple(edge for edge in resource_dependencies(entry) if edge.direction == "uses")
    subject_count = len(
        {
            (edge.target_kind, edge.target_id)
            for edge in dependencies
            if edge.path[:1] == ("subjects",)
        }
    )
    environment_count = len(
        {
            edge.target_id
            for edge in dependencies
            if edge.target_kind == "Environment"
            and edge.path[:1] in {("baseline",), ("candidates",), ("environments",)}
        }
    )
    baseline = _field_at(entry, ("baseline",))
    candidates = _field_at(entry, ("candidates",))
    environments = _field_at(entry, ("environments",))
    if baseline is not None or candidates is not None:
        authored_selection = "comparison fields"
    elif environments is not None:
        authored_selection = "assertion environments"
    else:
        authored_selection = "selection inherited"
    trials_field = _field_at(entry, ("trials",))
    declared_trials = None
    if (
        trials_field is not None
        and trials_field.exact_kind == "integer"
        and trials_field.exact_value is not None
    ):
        candidate = int(trials_field.exact_value)
        if candidate > 0:
            declared_trials = candidate
    return ExecutionProfileOverview(
        authored_selection=authored_selection,
        declared_subject_count=subject_count,
        declared_environment_count=environment_count,
        declared_trials=declared_trials,
        selection=_top_settings(entry, _EXECUTION_SELECTION),
        controls=_top_settings(entry, _EXECUTION_CONTROLS),
        inputs=_top_settings(entry, _EXECUTION_INPUTS),
        evaluation=_top_settings(entry, _EXECUTION_EVALUATION),
        consumers=_consumers(entry),
    )


def _operation_name(entry: ResourceEntry, root: tuple[str, ...]) -> str | None:
    """Return one safe scalar/object operation identity."""
    control = _field_at(entry, (*root, "name")) or _field_at(entry, root)
    return None if control is None else _display(control)


def _transform_pipeline(entry: ResourceEntry, root: tuple[str, ...]) -> tuple[str, ...]:
    """Return operation identities from one exact transforms array."""
    stages: list[str] = []
    transforms_root = (*root, "transforms")
    for field in _outline_fields(entry):
        if (
            len(field.path) == len(transforms_root) + 1
            and field.path[: len(transforms_root)] == transforms_root
        ):
            stage = _display(field)
            if field.category == "container":
                stage = _display(_field_at(entry, (*field.path, "name")), stage)
            stages.append(stage)
    return tuple(stages)


def _selector_summary(entry: ResourceEntry, root: tuple[str, ...]) -> str:
    """Summarize shorthand or structured selectors without revealing path expressions."""
    field = _field_at(entry, root)
    if field is None:
        return "—"
    if field.category != "container":
        return _display(field)
    parts = [_display(_field_at(entry, (*root, "source")), "source unknown")]
    view = _field_at(entry, (*root, "view"))
    if view is not None:
        parts.append(_display(view))
    path = _field_at(entry, (*root, "path"))
    if path is not None:
        parts.append("path declared")
    field_selector = _field_at(entry, (*root, "field"))
    if field_selector is not None:
        field_name = _field_at(entry, (*root, "field", "name"))
        parts.append(f"field {_display(field_name or field_selector)}")
    occurrence = _field_at(entry, (*root, "occurrence"))
    if occurrence is not None:
        parts.append(_display(occurrence))
    return " · ".join(parts)


def _diff_side(
    entry: ResourceEntry,
    rule_root: tuple[str, ...],
    role: str,
) -> RuleSideSummary:
    """Project one asymmetric side with shared-pipeline fallbacks made explicit."""
    side_root = (*rule_root, role)
    selector_root = (
        (*side_root, "selector")
        if _field_at(entry, (*side_root, "selector")) is not None
        else (*rule_root, "selector")
    )
    transforms_root = (
        side_root if _field_at(entry, (*side_root, "transforms")) is not None else rule_root
    )
    transforms = _transform_pipeline(entry, transforms_root)
    pipeline_root = side_root if _field_at(entry, side_root) is not None else rule_root
    reducer = _operation_name(entry, (*pipeline_root, "reducer"))
    if reducer is None and pipeline_root != rule_root:
        reducer = _operation_name(entry, (*rule_root, "reducer"))
    stages = (*transforms, *((reducer,) if reducer is not None else ()))
    return RuleSideSummary(
        role=role,
        selector=_selector_summary(entry, selector_root),
        pipeline=" → ".join(stages) or "identity",
    )


def _rule_tags(entry: ResourceEntry, root: tuple[str, ...]) -> tuple[str, ...]:
    """Return application-approved structural tags in authored order."""
    return tuple(
        _display(field)
        for field in _outline_fields(entry)
        if len(field.path) == len(root) + 2
        and field.path[: len(root)] == root
        and field.path[len(root)] == "tags"
    )


def rule_profile_overview(entry: ResourceEntry) -> RuleProfileOverview:
    """Project assertion or diff rule identities and pipelines without operands."""
    family = "assertion" if entry.summary.coordinate.kind == "AssertionProfile" else "diff"
    rules: list[RuleSummary] = []
    for field in _outline_fields(entry):
        if len(field.path) != 2 or field.path[0] != "rules" or field.category != "container":
            continue
        root = field.path
        name_field = _field_at(entry, (*root, "name"))
        description_field = _field_at(entry, (*root, "description"))
        sides: tuple[RuleSideSummary, ...]
        if family == "assertion":
            selector = _selector_summary(entry, (*root, "selector"))
            operator = _operation_name(entry, (*root, "operator")) or "—"
            sides = (RuleSideSummary("assertion", selector, operator),)
            quantifier_field = _field_at(entry, (*root, "quantifier"))
            combination = "not declared" if quantifier_field is None else _display(quantifier_field)
            quantifier = None if quantifier_field is None else combination
        else:
            sides = (_diff_side(entry, root, "baseline"), _diff_side(entry, root, "candidate"))
            pairing = _operation_name(entry, (*root, "pairing")) or "derived pairing"
            comparator = _operation_name(entry, (*root, "comparator")) or "missing comparator"
            combination = f"{pairing} → {comparator}"
            quantifier = None
        rules.append(
            RuleSummary(
                id=_display(_field_at(entry, (*root, "id")), f"rule {root[1]}"),
                name_declared=name_field is not None,
                description_declared=description_field is not None,
                tags=_rule_tags(entry, root),
                sides=sides,
                combination=combination,
                severity=_display(_field_at(entry, (*root, "severity")), "default"),
                completeness=_display(
                    _field_at(entry, (*root, "completeness")),
                    "default",
                ),
                quantifier=quantifier,
            )
        )
    return RuleProfileOverview(
        family=family,
        rules=tuple(rules),
        composition=_top_settings(entry, frozenset({"include", "edits"})),
        parameters=_top_settings(entry, frozenset({"parameters"})),
        consumers=_consumers(entry),
    )


def gate_profile_overview(entry: ResourceEntry) -> GateProfileOverview:
    """Project one GateProfile into scope, coverage, and threshold groups."""
    return GateProfileOverview(
        scope=_top_settings(entry, frozenset({"severities", "environmentRoles"})),
        requirements=_top_settings(
            entry,
            frozenset({"requireCompleteRun", "requireAssertionCoverage", "requireDiffCoverage"}),
        ),
        thresholds=_top_settings(entry, frozenset({"thresholds"})),
        consumers=_consumers(entry),
    )


def safety_profile_overview(entry: ResourceEntry) -> SafetyProfileOverview:
    """Separate enforced SafetyProfile fields from schema-reserved unsupported fields."""
    capabilities = profile_resource_capabilities(ResourceKind.SAFETY_PROFILE)
    return SafetyProfileOverview(
        composition=_top_settings(entry, frozenset({"include", "extends"})),
        schemas=_top_settings(entry, frozenset({"schemas"})),
        classification=_top_settings(entry, frozenset({"classification"})),
        budgets=_top_settings(entry, frozenset({"budgets"})),
        unsupported=_top_settings(
            entry,
            frozenset(capabilities.unsupported_fields),
        ),
        unsupported_support=capabilities.support("network"),
        consumers=_consumers(entry),
    )


def redaction_profile_overview(entry: ResourceEntry) -> RedactionProfileOverview:
    """Project sanitization structure while leaving authored patterns concealed."""
    capabilities = profile_resource_capabilities(ResourceKind.REDACTION_PROFILE)
    return RedactionProfileOverview(
        rules=_top_settings(entry, frozenset({"rules"})),
        patterns=_top_settings(entry, frozenset({"patterns"})),
        backstop=_top_settings(entry, frozenset({"backstop"})),
        patterns_support=capabilities.support("patterns"),
        backstop_support=capabilities.support("backstop"),
        consumers=_consumers(entry),
    )


def artifact_profile_overview(entry: ResourceEntry) -> ArtifactProfileOverview:
    """Project active capture/storage and explicitly reserved retention declarations."""
    capture = _top_settings(entry, frozenset({"capture"}))
    storage = _top_settings(entry, frozenset({"storage"}))
    capabilities = profile_resource_capabilities(ResourceKind.ARTIFACT_PROFILE)
    return ArtifactProfileOverview(
        capture=capture,
        storage=storage,
        retention=_top_settings(entry, frozenset({"retention"})),
        retention_support=capabilities.support("retention"),
        capture_control_count=_direct_detail_count(capture),
        storage_control_count=_direct_detail_count(storage),
        consumers=_consumers(entry),
    )


def _direct_detail_count(settings: tuple[ResourceSetting, ...]) -> int:
    """Count direct declared controls rather than their single top-level group."""
    return sum(1 for setting in settings for detail in setting.details if len(detail.path) == 1)


def execution_policy_overview(entry: ResourceEntry) -> ExecutionPolicyOverview:
    """Project an operator policy if a non-project catalog supplies one in the future."""
    return ExecutionPolicyOverview(
        planning=_top_settings(entry, frozenset({"planning"})),
        http=_top_settings(entry, frozenset({"http"})),
        values=_top_settings(entry, frozenset({"values"})),
        artifacts=_top_settings(entry, frozenset({"artifacts"})),
    )


__all__ = [
    "ArtifactProfileOverview",
    "ExecutionPolicyOverview",
    "ExecutionProfileOverview",
    "GateProfileOverview",
    "RedactionProfileOverview",
    "RuleProfileOverview",
    "RuleSideSummary",
    "RuleSummary",
    "SafetyProfileOverview",
    "ScenarioOverview",
    "ScenarioStep",
    "artifact_profile_overview",
    "execution_policy_overview",
    "execution_profile_overview",
    "gate_profile_overview",
    "redaction_profile_overview",
    "rule_profile_overview",
    "safety_profile_overview",
    "scenario_overview",
]
