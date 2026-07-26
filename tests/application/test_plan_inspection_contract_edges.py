"""Exhaustive edge coverage for value-free Plan inspection contracts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import msgspec
import pytest

from comparo.application.count_presentation import count_label
from comparo.application.inspection import ApplicationDiagnostic
from comparo.application.inspection import SourceCoordinate
from comparo.application.plan_inspection_authority import PlanArtifactAuthorityInspection
from comparo.application.plan_inspection_authority import PlanHttpAuthorityInspection
from comparo.application.plan_inspection_authority import PlanSecretEgressRuleInspection
from comparo.application.plan_inspection_authority import PlanSecretEgressSourceInspection
from comparo.application.plan_inspection_authority import PlanValueAuthorityInspection
from comparo.application.plan_inspection_identity import ResourceCoordinate
from comparo.application.plan_inspection_profiles import PlanGateInspection
from comparo.application.plan_inspection_profiles import PlanGateThresholdInspection
from comparo.application.plan_inspection_profiles import PlanProfileAttachmentInspection
from comparo.application.plan_inspection_profiles import PlanProfilesInspection
from comparo.application.plan_inspection_profiles import PlanValidationInspection
from comparo.application.plan_inspection_topology import BudgetInspection
from comparo.application.plan_inspection_topology import EnvironmentPairInspection
from comparo.application.plan_inspection_topology import InlineSourceCoordinate
from comparo.application.plan_inspection_topology import MatrixAxisInspection
from comparo.application.plan_inspection_topology import PlanCardinalityInspection
from comparo.application.plan_inspection_topology import PlanningSourceInspection
from comparo.application.plan_inspection_topology import PlanSliceInspection
from comparo.application.plan_inspection_topology import ProvenanceInspection
from comparo.application.plan_inspection_topology import _validate_plan_slice_cardinality
from comparo.application.plan_review_checkpoint import PlanReviewInspection
from comparo.application.plan_review_checkpoint import inspect_execution_settings
from comparo.application.plan_review_clipboard import PlanClipboardSummary
from comparo.application.plan_review_limits import RuntimeLimitInspection
from comparo.application.plan_review_limits import RuntimeLimitsInspection
from comparo.application.plan_review_limits import inspect_runtime_limits
from comparo.application.plan_sources import SensitiveSourceInspection
from comparo.application.production_execution import production_execution_defaults_v1
from comparo.application.project_outline import ProjectRelationship
from comparo.application.project_outline import ProjectResourceOutline
from comparo.application.project_outline import ResourceIdentityCoordinate
from comparo.application.project_workspace import ProjectSourceSummary
from comparo.application.project_workspace import ProjectWorkspaceSnapshot
from comparo.application.project_workspace import _with_consumers
from tests.application.test_plan_review import _coordinate
from tests.application.test_plan_review import _plan
from tests.application.test_project_workspace import _resource
from tests.application.test_project_workspace import _snapshot


def _http() -> PlanHttpAuthorityInspection:
    authority = _plan().http_authority
    assert authority is not None
    return authority


def _values() -> PlanValueAuthorityInspection:
    authority = _plan().value_authority
    assert authority is not None
    return authority


def _artifact() -> PlanArtifactAuthorityInspection:
    authority = _plan().artifact_authority
    assert authority is not None
    return authority


def _profile(kind: str = "AssertionProfile") -> PlanProfileAttachmentInspection:
    return PlanProfileAttachmentInspection(
        kind=kind,
        namespace="local",
        resource_id="profile",
        fingerprint="a" * 64,
        inline=False,
    )


def _threshold(severity: str = "blocking") -> PlanGateThresholdInspection:
    return PlanGateThresholdInspection(
        severity=severity,
        violated=0,
        drifted=0,
        unavailable=0,
        incomparable=0,
        incomplete=0,
        errors=0,
    )


def _gate() -> PlanGateInspection:
    return PlanGateInspection(
        profile=_coordinate("GateProfile", "gate", "b"),
        severities=("blocking",),
        environment_roles=("baseline",),
        require_complete_run=False,
        require_assertion_coverage=False,
        require_diff_coverage=False,
        thresholds=(_threshold(),),
    )


def _summary() -> PlanClipboardSummary:
    return PlanClipboardSummary(
        api_version="comparo.plan-summary/v1alpha1",
        review_fingerprint="a" * 64,
        run_id="run",
        plan_fingerprint="b" * 64,
        policy_namespace="local",
        policy_id="ci",
        subjects="1",
        environments="2",
        cells="1",
        trials="1",
        planned_executions="2",
        comparisons="1",
        maximum_attempts="2",
        write_capable_subjects="0",
        mutation_approved=False,
        artifact_persistence="never",
        tool_version="test",
        execution_allowed=True,
        execution_blockers=(),
    )


@pytest.mark.parametrize("value", [-1, 1.5, True])
def test_count_label_rejects_non_counts(value: object) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        count_label(cast("int", value))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"source": ""}, "source text"),
        ({"source": "x" * 1025}, "source text"),
        ({"document": 0}, "positions"),
        ({"line": True}, "positions"),
    ],
)
def test_source_coordinate_rejects_invalid_state(changes: dict[str, object], message: str) -> None:
    values: dict[str, object] = {"source": "source", "document": 1, "line": 1, "column": 1}
    values.update(changes)
    with pytest.raises(ValueError, match=message):
        SourceCoordinate(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"code": "bad token"}, ValueError, "code"),
        ({"stage": ""}, ValueError, "stage"),
        ({"severity": "fatal"}, ValueError, "severity"),
        ({"source": object()}, TypeError, "source"),
        ({"resource": ("local", "Request")}, ValueError, "resource"),
        ({"resource": ("local", "", "ping")}, ValueError, "resource"),
        ({"path": []}, ValueError, "path"),
        ({"path": ("",)}, ValueError, "path"),
        ({"occurrences": -1}, ValueError, "occurrences"),
        ({"occurrences": True}, ValueError, "occurrences"),
    ],
)
def test_application_diagnostic_rejects_invalid_state(
    changes: dict[str, object], error: type[Exception], message: str
) -> None:
    values: dict[str, object] = {"code": "test.code", "severity": "error", "stage": "test"}
    values.update(changes)
    with pytest.raises(error, match=message):
        ApplicationDiagnostic(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["", "x" * 256, 1])
def test_resource_coordinates_reject_invalid_identity(value: object) -> None:
    with pytest.raises(ValueError, match="identity"):
        ResourceCoordinate(
            namespace=cast("str", value), kind="Request", resource_id="ping", fingerprint="a" * 64
        )
    with pytest.raises(ValueError, match="identity"):
        ResourceIdentityCoordinate(
            namespace="local", kind="Request", resource_id=cast("str", value)
        )


def test_resource_coordinate_rejects_non_sha256_fingerprint() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        ResourceCoordinate(
            namespace="local", kind="Request", resource_id="ping", fingerprint="A" * 64
        )


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"allowed_schemes": []}, ValueError, "lists"),
        ({"allowed_methods": ("GET", "GET")}, ValueError, "lists"),
        ({"allowed_ports": [443]}, ValueError, "ports"),
        ({"allowed_ports": (0,)}, ValueError, "ports"),
        ({"allowed_ports": (443, 443)}, ValueError, "unique"),
        ({"allow_any_host": 1}, TypeError, "flags"),
        ({"allowed_schemes": ()}, ValueError, "requires schemes"),
        ({"allowed_methods": ()}, ValueError, "requires schemes"),
        ({"allow_any_host": True}, ValueError, "host wildcard"),
        ({"allow_any_port": True}, ValueError, "port wildcard"),
        ({"allowed_ports": ()}, ValueError, "explicit ports"),
    ],
)
def test_http_authority_rejects_invalid_state(
    changes: dict[str, object], error: type[Exception], message: str
) -> None:
    values = msgspec.to_builtins(_http())
    assert isinstance(values, dict)
    values.update(changes)
    with pytest.raises(error, match=message):
        PlanHttpAuthorityInspection(**values)


def test_secret_egress_source_rejects_invalid_state() -> None:
    with pytest.raises(ValueError, match="kind"):
        PlanSecretEgressSourceInspection(kind="file", name="token")
    with pytest.raises(ValueError, match="name"):
        PlanSecretEgressSourceInspection(kind="secret", name="")


def _egress_rule() -> PlanSecretEgressRuleInspection:
    return PlanSecretEgressRuleInspection(
        sources=(PlanSecretEgressSourceInspection(kind="secret", name="token"),),
        allowed_schemes=("https",),
        allowed_methods=("POST",),
        allowed_hosts=("api.example.test",),
        allowed_ports=(443,),
    )


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"sources": []}, TypeError, "sources"),
        ({"sources": ()}, ValueError, "non-empty"),
        ({"sources": (_egress_rule().sources[0],) * 2}, ValueError, "unique"),
        ({"allowed_schemes": ["https"]}, ValueError, "destinations"),
        ({"allowed_methods": ()}, ValueError, "non-empty"),
        ({"allowed_hosts": ("api", "api")}, ValueError, "unique"),
        ({"allowed_ports": [443]}, ValueError, "ports"),
        ({"allowed_ports": ()}, ValueError, "non-empty"),
        ({"allowed_ports": (443, 443)}, ValueError, "unique"),
    ],
)
def test_secret_egress_rule_rejects_invalid_state(
    changes: dict[str, object], error: type[Exception], message: str
) -> None:
    values = msgspec.to_builtins(_egress_rule())
    assert isinstance(values, dict)
    values["sources"] = _egress_rule().sources
    values.update(changes)
    with pytest.raises(error, match=message):
        PlanSecretEgressRuleInspection(**values)


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"ambient_environment_names": ["TOKEN"]}, ValueError, "names"),
        ({"remote_schema_sources": ("",)}, ValueError, "names"),
        ({"ambient_environment_names": ("TOKEN", "TOKEN")}, ValueError, "unique"),
        ({"allow_project_files": 1}, TypeError, "flags"),
        ({"secret_egress": []}, TypeError, "secret egress authority"),
        ({"secret_egress": (_egress_rule(), _egress_rule())}, ValueError, "rules"),
        (
            {"allow_any_ambient_environment": True, "ambient_environment_names": ("TOKEN",)},
            ValueError,
            "wildcard",
        ),
        (
            {"allow_any_remote_schemas": True, "remote_schema_sources": ("https://example.test",)},
            ValueError,
            "wildcard",
        ),
        (
            {"allow_any_secret_egress": True, "secret_egress": (_egress_rule(),)},
            ValueError,
            "wildcard",
        ),
    ],
)
def test_value_authority_rejects_invalid_state(
    changes: dict[str, object], error: type[Exception], message: str
) -> None:
    values = msgspec.to_builtins(_values())
    assert isinstance(values, dict)
    values.update(changes)
    with pytest.raises(error, match=message):
        PlanValueAuthorityInspection(**values)


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"allow_sanitized_persistence": 1}, TypeError, "permission"),
        ({"root_count": -1}, ValueError, "root count"),
        ({"root_count": 1}, ValueError, "must match"),
        ({"selected_root_present": 1}, TypeError, "booleans"),
        ({"selected_root_authorized": True}, ValueError, "absent"),
        (
            {"selected_root_present": True, "selected_root_authorized": True},
            ValueError,
            "requires persistence",
        ),
    ],
)
def test_artifact_authority_rejects_invalid_state(
    changes: dict[str, object], error: type[Exception], message: str
) -> None:
    values = msgspec.to_builtins(_artifact())
    assert isinstance(values, dict)
    values.update(changes)
    with pytest.raises(error, match=message):
        PlanArtifactAuthorityInspection(**values)


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"kind": "Unknown"}, ValueError, "kind"),
        ({"inline": 1}, TypeError, "marker"),
        ({"namespace": ""}, ValueError, "namespace"),
        ({"resource_id": ""}, ValueError, "resource ID"),
        ({"owner": ("local", "Request")}, ValueError, "owner"),
        ({"fragment": ""}, ValueError, "fragment"),
        ({"inline": True}, ValueError, "inline profile"),
        ({"namespace": None}, ValueError, "named profile"),
        ({"fingerprint": "bad"}, ValueError, "fingerprint"),
    ],
)
def test_profile_attachment_rejects_invalid_state(
    changes: dict[str, object], error: type[Exception], message: str
) -> None:
    values = msgspec.to_builtins(_profile())
    assert isinstance(values, dict)
    values.update(changes)
    with pytest.raises(error, match=message):
        PlanProfileAttachmentInspection(**values)


def test_inline_profile_attachment_is_accepted() -> None:
    profile = PlanProfileAttachmentInspection(
        kind="SafetyProfile",
        namespace=None,
        resource_id=None,
        fingerprint="c" * 64,
        inline=True,
        owner=("local", "Request", "ping"),
        fragment="safety",
    )
    assert profile.inline is True


def test_gate_contracts_reject_invalid_state() -> None:
    with pytest.raises(ValueError, match="severity"):
        msgspec.structs.replace(_threshold(), severity="fatal")
    with pytest.raises(ValueError, match="allowances"):
        msgspec.structs.replace(_threshold(), errors=-1)
    with pytest.raises(TypeError, match="GateProfile"):
        msgspec.structs.replace(_gate(), profile=_coordinate("Request", "ping", "a"))
    with pytest.raises(ValueError, match="selections"):
        msgspec.structs.replace(_gate(), severities=())
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(_gate(), environment_roles=("baseline", "baseline"))
    with pytest.raises(ValueError, match="severity"):
        msgspec.structs.replace(_gate(), severities=("fatal",), thresholds=())
    with pytest.raises(ValueError, match="role"):
        msgspec.structs.replace(_gate(), environment_roles=("invalid",))
    with pytest.raises(TypeError, match="requirements"):
        msgspec.structs.replace(_gate(), require_complete_run=cast("bool", 1))
    with pytest.raises(TypeError, match="thresholds"):
        msgspec.structs.replace(
            _gate(), thresholds=cast("tuple[PlanGateThresholdInspection, ...]", [])
        )
    with pytest.raises(ValueError, match="match selected"):
        msgspec.structs.replace(_gate(), thresholds=())


def test_validation_and_profile_collections_reject_invalid_state() -> None:
    with pytest.raises(ValueError, match="rule counts"):
        PlanValidationInspection(assertion_rules=-1, diff_rules=0, gate=None)
    with pytest.raises(TypeError, match="gate"):
        PlanValidationInspection(
            assertion_rules=0, diff_rules=0, gate=cast("PlanGateInspection", object())
        )
    with pytest.raises(TypeError, match="AssertionProfile"):
        PlanProfilesInspection(assertions=cast("tuple[PlanProfileAttachmentInspection, ...]", []))
    with pytest.raises(TypeError, match="DiffProfile"):
        PlanProfilesInspection(diffs=(_profile("AssertionProfile"),))
    with pytest.raises(TypeError, match="optional"):
        PlanProfilesInspection(gate=_profile("AssertionProfile"))


def test_small_topology_contracts_reject_invalid_state() -> None:
    valid = PlanCardinalityInspection(
        cells=1, trials=1, environment_executions=2, comparisons=1, maximum_attempts=2
    )
    with pytest.raises(ValueError, match="non-negative"):
        msgspec.structs.replace(valid, cells=-1)
    with pytest.raises(ValueError, match="below"):
        msgspec.structs.replace(valid, maximum_attempts=1)
    matrix = MatrixAxisInspection(
        binding="region", matrix=_coordinate("Matrix", "regions", "a"), cases=("eu",)
    )
    with pytest.raises(ValueError, match="binding"):
        msgspec.structs.replace(matrix, binding="")
    with pytest.raises(TypeError, match="Matrix resource"):
        msgspec.structs.replace(matrix, matrix=_coordinate("Request", "ping", "a"))
    with pytest.raises(ValueError, match="non-empty"):
        msgspec.structs.replace(matrix, cases=())
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(matrix, cases=("eu", "eu"))
    staging = _coordinate("Environment", "staging", "a")
    prod = _coordinate("Environment", "prod", "b")
    with pytest.raises(TypeError, match="Environment pair"):
        EnvironmentPairInspection(baseline=staging, candidate=_coordinate("Request", "ping", "c"))
    with pytest.raises(ValueError, match="must differ"):
        EnvironmentPairInspection(baseline=staging, candidate=staging)
    assert EnvironmentPairInspection(baseline=staging, candidate=prod).candidate == prod


def test_budget_inline_source_and_provenance_contracts() -> None:
    budget = BudgetInspection(resource="trials", requested=2, effective=1, cap=1)
    with pytest.raises(ValueError, match="resource"):
        msgspec.structs.replace(budget, resource="")
    with pytest.raises(ValueError, match="non-negative"):
        msgspec.structs.replace(budget, requested=-1)
    with pytest.raises(ValueError, match="cannot exceed"):
        msgspec.structs.replace(budget, effective=2)
    inline = InlineSourceCoordinate(
        owner_namespace="local",
        owner_kind="Request",
        owner_resource_id="ping",
        fragment="profiles.safety",
        fingerprint="a" * 64,
    )
    with pytest.raises(ValueError, match="identity text"):
        msgspec.structs.replace(inline, fragment="")
    with pytest.raises(ValueError, match="SHA-256"):
        msgspec.structs.replace(inline, fingerprint="bad")
    source = PlanningSourceInspection(kind="default")
    with pytest.raises(ValueError, match="kind"):
        msgspec.structs.replace(source, kind="")
    with pytest.raises(ValueError, match="mutually exclusive"):
        PlanningSourceInspection(
            kind="profile",
            definition=_coordinate("Request", "ping", "a"),
            invocation_id="invocation",
        )
    with pytest.raises(TypeError, match="named planning"):
        PlanningSourceInspection(kind="profile", definition=cast("ResourceCoordinate", object()))
    with pytest.raises(TypeError, match="inline planning"):
        PlanningSourceInspection(kind="profile", inline=cast("InlineSourceCoordinate", object()))
    with pytest.raises(ValueError, match="invocation"):
        PlanningSourceInspection(kind="invocation", invocation_id="")
    with pytest.raises(ValueError, match="field"):
        ProvenanceInspection(field="", sources=())
    with pytest.raises(TypeError, match="sources"):
        ProvenanceInspection(
            field="subjects", sources=cast("tuple[PlanningSourceInspection, ...]", [])
        )


def test_sensitive_source_contract_rejects_invalid_state() -> None:
    with pytest.raises(ValueError, match="identities"):
        SensitiveSourceInspection(
            name="",
            source="source",
            environment_namespace="local",
            environment_id="staging",
            mechanisms=("$env:TOKEN",),
        )
    with pytest.raises(ValueError, match="mechanisms"):
        SensitiveSourceInspection(
            name="TOKEN",
            source="source",
            environment_namespace="local",
            environment_id="staging",
            mechanisms=(),
        )


def test_slice_and_plan_contracts_reject_projection_contradictions() -> None:
    plan = _plan()
    slice_ = plan.slices[0]
    with pytest.raises(TypeError, match="profiles"):
        msgspec.structs.replace(slice_, profiles=cast("PlanProfilesInspection", object()))
    unresolved = msgspec.structs.replace(slice_, method=None, unsafe_method=None)
    assert unresolved.method is None
    with pytest.raises(ValueError, match="unresolved"):
        msgspec.structs.replace(slice_, method=None)
    with pytest.raises(ValueError, match="classification"):
        msgspec.structs.replace(slice_, unsafe_method=True)
    with pytest.raises(TypeError, match="subject"):
        msgspec.structs.replace(slice_, subject=_coordinate("Environment", "staging", "a"))
    with pytest.raises(TypeError, match="matrices"):
        msgspec.structs.replace(slice_, matrices=cast("tuple[MatrixAxisInspection, ...]", []))
    with pytest.raises(ValueError, match="Environment coordinates"):
        msgspec.structs.replace(slice_, environments=())
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(slice_, environments=(slice_.environments[0],) * 2)
    with pytest.raises(TypeError, match="comparisons"):
        msgspec.structs.replace(
            slice_, comparisons=cast("tuple[EnvironmentPairInspection, ...]", [])
        )
    with pytest.raises(ValueError, match="Environment pairs"):
        msgspec.structs.replace(slice_, comparisons=(slice_.comparisons[0],) * 2)
    with pytest.raises(TypeError, match="slice cardinality"):
        msgspec.structs.replace(slice_, cardinality=cast("PlanCardinalityInspection", object()))
    with pytest.raises(ValueError, match="trials"):
        msgspec.structs.replace(slice_, trials=0)
    with pytest.raises(ValueError, match="maximum attempts"):
        msgspec.structs.replace(slice_, maximum_attempts_per_execution=0)
    with pytest.raises(ValueError, match="compact recipe"):
        msgspec.structs.replace(
            slice_, cardinality=msgspec.structs.replace(slice_.cardinality, cells=2)
        )
    outside = _coordinate("Environment", "outside", "f")
    with pytest.raises(ValueError, match="admitted"):
        msgspec.structs.replace(
            slice_,
            comparisons=(
                EnvironmentPairInspection(baseline=slice_.environments[0], candidate=outside),
            ),
        )

    with pytest.raises(ValueError, match="API version"):
        msgspec.structs.replace(plan, api_version="v2")
    with pytest.raises(ValueError, match="ExecutionPolicy"):
        msgspec.structs.replace(plan, policy=_coordinate("Request", "ping", "a"))
    with pytest.raises(TypeError, match="slices"):
        msgspec.structs.replace(plan, slices=cast("tuple[PlanSliceInspection, ...]", []))
    with pytest.raises(ValueError, match="at least one"):
        msgspec.structs.replace(plan, slices=())
    with pytest.raises(TypeError, match="cardinality"):
        msgspec.structs.replace(plan, cardinality=cast("PlanCardinalityInspection", object()))
    with pytest.raises(ValueError, match="Environment count"):
        msgspec.structs.replace(plan, environment_count=0)
    with pytest.raises(ValueError, match="write-capable"):
        msgspec.structs.replace(plan, write_capable_subjects=2)
    with pytest.raises(ValueError, match="contradicts slices"):
        msgspec.structs.replace(plan, write_capable_subjects=1)
    with pytest.raises(ValueError, match="Environment count contradicts"):
        msgspec.structs.replace(plan, environment_count=1)
    with pytest.raises(TypeError, match="budgets"):
        msgspec.structs.replace(plan, budgets=cast("tuple[BudgetInspection, ...]", []))
    with pytest.raises(TypeError, match="provenance"):
        msgspec.structs.replace(plan, provenance=cast("tuple[ProvenanceInspection, ...]", []))
    with pytest.raises(TypeError, match="HTTP authority"):
        msgspec.structs.replace(plan, http_authority=cast("PlanHttpAuthorityInspection", object()))
    assert plan.http_authority is not None
    with pytest.raises(ValueError, match="absent from"):
        msgspec.structs.replace(
            plan,
            http_authority=msgspec.structs.replace(plan.http_authority, allowed_methods=("POST",)),
        )
    with pytest.raises(TypeError, match="sensitive sources"):
        msgspec.structs.replace(
            plan, sensitive_sources=cast("tuple[SensitiveSourceInspection, ...]", [])
        )
    with pytest.raises(TypeError, match="value authority"):
        msgspec.structs.replace(
            plan, value_authority=cast("PlanValueAuthorityInspection", object())
        )
    with pytest.raises(TypeError, match="artifact authority"):
        msgspec.structs.replace(
            plan, artifact_authority=cast("PlanArtifactAuthorityInspection", object())
        )
    with pytest.raises(TypeError, match="validation"):
        msgspec.structs.replace(plan, validation=cast("PlanValidationInspection", object()))


def test_gate_attachment_and_compilation_must_match() -> None:
    plan = _plan()
    attached = _profile("GateProfile")
    profiles = PlanProfilesInspection(gate=attached)
    plan_slice = msgspec.structs.replace(plan.slices[0], profiles=profiles)
    with pytest.raises(ValueError, match="absent from compiled"):
        msgspec.structs.replace(plan, slices=(plan_slice,))

    gate = _gate()
    validation = PlanValidationInspection(assertion_rules=0, diff_rules=0, gate=gate)
    with pytest.raises(ValueError, match="differs"):
        msgspec.structs.replace(plan, validation=validation)

    matching_attachment = PlanProfileAttachmentInspection(
        kind="GateProfile",
        namespace=gate.profile.namespace,
        resource_id=gate.profile.resource_id,
        fingerprint=gate.profile.fingerprint,
        inline=False,
    )
    matching_slice = msgspec.structs.replace(
        plan.slices[0],
        profiles=PlanProfilesInspection(gate=matching_attachment),
    )
    matched = msgspec.structs.replace(
        plan,
        slices=(matching_slice,),
        validation=validation,
    )
    assert matched.validation == validation


def test_defensive_matrix_cardinality_checks_reject_unconstructible_shapes() -> None:
    plan_slice = _plan().slices[0]
    empty_axis = SimpleNamespace(binding="empty", cases=())
    empty = SimpleNamespace(
        matrices=(empty_axis,),
        trials=1,
        environments=plan_slice.environments,
        comparisons=plan_slice.comparisons,
        maximum_attempts_per_execution=1,
        cardinality=plan_slice.cardinality,
    )
    with pytest.raises(ValueError, match="at least one case"):
        _validate_plan_slice_cardinality(cast("PlanSliceInspection", empty))

    first = SimpleNamespace(binding="same", cases=("a",))
    second = SimpleNamespace(binding="same", cases=("b",))
    duplicate = SimpleNamespace(
        matrices=(first, second),
        trials=1,
        environments=plan_slice.environments,
        comparisons=plan_slice.comparisons,
        maximum_attempts_per_execution=1,
        cardinality=plan_slice.cardinality,
    )
    with pytest.raises(ValueError, match="bindings"):
        _validate_plan_slice_cardinality(cast("PlanSliceInspection", duplicate))


def test_execution_checkpoint_and_clipboard_contracts(tmp_path: Path) -> None:
    settings = production_execution_defaults_v1()
    execution = inspect_execution_settings(settings, None)
    with pytest.raises(ValueError, match="sensitivity"):
        msgspec.structs.replace(execution, response_body_sensitivity="private")
    with pytest.raises(ValueError, match="unsafe-source"):
        msgspec.structs.replace(execution, artifact_unsafe_source="allow")
    with pytest.raises(ValueError, match="header"):
        msgspec.structs.replace(execution, sensitive_response_headers=("",))
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(execution, sensitive_response_headers=("cookie", "cookie"))
    with pytest.raises(TypeError, match="flags"):
        msgspec.structs.replace(execution, capture_bodies=cast("bool", 1))
    with pytest.raises(ValueError, match="fingerprints"):
        msgspec.structs.replace(execution, fingerprint="bad")
    with pytest.raises(TypeError, match="Path"):
        inspect_execution_settings(settings, cast("Path", object()))
    with pytest.raises(ValueError, match="absolute"):
        inspect_execution_settings(settings, Path("relative"))

    summary = _summary()
    with pytest.raises(ValueError, match="version"):
        msgspec.structs.replace(summary, api_version="v2")
    with pytest.raises(ValueError, match="identities"):
        msgspec.structs.replace(summary, run_id="")
    with pytest.raises(ValueError, match="count labels"):
        msgspec.structs.replace(summary, subjects="")
    with pytest.raises(TypeError, match="mutation approval"):
        msgspec.structs.replace(summary, mutation_approved=cast("bool", 1))
    with pytest.raises(ValueError, match="persistence"):
        msgspec.structs.replace(summary, artifact_persistence="sometimes")
    with pytest.raises(TypeError, match="execution decision"):
        msgspec.structs.replace(summary, execution_allowed=cast("bool", 1))
    with pytest.raises(ValueError, match="blockers"):
        msgspec.structs.replace(summary, execution_allowed=False, execution_blockers=("unknown",))
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(
            summary, execution_allowed=False, execution_blockers=("artifact-root-required",) * 2
        )
    with pytest.raises(ValueError, match="contradicts"):
        msgspec.structs.replace(
            summary, execution_allowed=True, execution_blockers=("artifact-root-required",)
        )


def _replace_limit(
    runtime: RuntimeLimitsInspection, section: str, name: str, value: int
) -> RuntimeLimitsInspection:
    limits = tuple(
        msgspec.structs.replace(item, value=value)
        if (item.section, item.name) == (section, name)
        else item
        for item in runtime.limits
    )
    return msgspec.structs.replace(runtime, limits=limits)


def test_runtime_limit_contracts_reject_invalid_state() -> None:
    settings = production_execution_defaults_v1()
    runtime = inspect_runtime_limits(settings)
    item = runtime.find("files", "bytes")
    with pytest.raises(ValueError, match="identity"):
        msgspec.structs.replace(item, section="")
    with pytest.raises(ValueError, match="non-negative"):
        msgspec.structs.replace(item, value=-1)
    with pytest.raises(ValueError, match="denominator"):
        msgspec.structs.replace(item, denominator=0)
    with pytest.raises(ValueError, match="canonical identity"):
        RuntimeLimitInspection(section="files", name="bytes", value=1, unit="count")
    with pytest.raises(ValueError, match="denominator one"):
        msgspec.structs.replace(item, denominator=2)
    with pytest.raises(TypeError, match="typed immutable"):
        RuntimeLimitsInspection(
            limits=cast("tuple[RuntimeLimitInspection, ...]", []), artifact_persistence="never"
        )
    with pytest.raises(ValueError, match="persistence mode"):
        msgspec.structs.replace(runtime, artifact_persistence="sometimes")
    with pytest.raises(ValueError, match="keepalive"):
        _replace_limit(
            runtime, "http", "keepalive-connections", runtime.find("http", "connections").value + 1
        )
    with pytest.raises(ValueError, match="memory threshold"):
        _replace_limit(
            runtime,
            "body-store",
            "memory-threshold",
            runtime.find("body-store", "transient-bytes").value + 1,
        )
    with pytest.raises(ValueError, match="request body"):
        _replace_limit(
            runtime,
            "request-body",
            "bytes",
            runtime.find("body-store", "transient-bytes").value + 1,
        )
    with pytest.raises(ValueError, match="decoded body"):
        _replace_limit(
            runtime,
            "ordinary",
            "body-bytes",
            runtime.find("capture", "entity-body-bytes").value + 1,
        )
    with pytest.raises(ValueError, match="artifact blob"):
        _replace_limit(
            runtime,
            "artifacts",
            "blob-bytes",
            runtime.find("artifacts", "total-blob-bytes").value + 1,
        )
    with pytest.raises(ValueError, match="schema violations"):
        _replace_limit(
            runtime, "schema", "retained-violations", runtime.find("schema", "violations").value + 1
        )
    with pytest.raises(KeyError):
        runtime.find("missing", "limit")


def test_review_dependency_and_persistence_blocker_contracts(tmp_path: Path) -> None:
    settings = production_execution_defaults_v1()
    runtime = inspect_runtime_limits(settings)
    execution = inspect_execution_settings(settings, None)
    plan = _plan()
    with pytest.raises(TypeError, match="mutation approval"):
        PlanReviewInspection(
            plan=plan,
            runtime=runtime,
            execution=execution,
            mutation_approved=cast("bool", 1),
            tool_version="test",
        )
    with pytest.raises(ValueError, match="tool version"):
        PlanReviewInspection(
            plan=plan,
            runtime=runtime,
            execution=execution,
            mutation_approved=False,
            tool_version="",
        )
    with pytest.raises(ValueError, match="value-source"):
        PlanReviewInspection(
            plan=msgspec.structs.replace(plan, value_authority=None),
            runtime=runtime,
            execution=execution,
            mutation_approved=False,
            tool_version="test",
        )
    with pytest.raises(ValueError, match="artifact policy"):
        PlanReviewInspection(
            plan=msgspec.structs.replace(plan, artifact_authority=None),
            runtime=runtime,
            execution=execution,
            mutation_approved=False,
            tool_version="test",
        )
    with pytest.raises(ValueError, match="evaluation preflight"):
        PlanReviewInspection(
            plan=msgspec.structs.replace(plan, validation=None),
            runtime=runtime,
            execution=execution,
            mutation_approved=False,
            tool_version="test",
        )

    persistent = msgspec.structs.replace(runtime, artifact_persistence="always")
    denied = PlanReviewInspection(
        plan=plan,
        runtime=persistent,
        execution=execution,
        mutation_approved=False,
        tool_version="test",
    )
    assert denied.execution_blockers == ("artifact-persistence-denied",)
    allowed_authority = PlanArtifactAuthorityInspection(
        allow_sanitized_persistence=True,
        root_count=1,
        selected_root_present=False,
        selected_root_authorized=False,
    )
    root_required_plan = msgspec.structs.replace(plan, artifact_authority=allowed_authority)
    required = PlanReviewInspection(
        plan=root_required_plan,
        runtime=persistent,
        execution=execution,
        mutation_approved=False,
        tool_version="test",
    )
    assert required.execution_blockers == ("artifact-root-required",)
    root = tmp_path.resolve()
    selected_execution = inspect_execution_settings(settings, root)
    denied_root_plan = msgspec.structs.replace(
        plan,
        artifact_authority=msgspec.structs.replace(allowed_authority, selected_root_present=True),
    )
    denied_root = PlanReviewInspection(
        plan=denied_root_plan,
        runtime=persistent,
        execution=selected_execution,
        mutation_approved=False,
        tool_version="test",
    )
    assert denied_root.execution_blockers == ("artifact-root-denied",)

    authorized_plan = msgspec.structs.replace(
        plan,
        artifact_authority=msgspec.structs.replace(
            allowed_authority,
            selected_root_present=True,
            selected_root_authorized=True,
        ),
    )
    authorized = PlanReviewInspection(
        plan=authorized_plan,
        runtime=persistent,
        execution=selected_execution,
        mutation_approved=False,
        tool_version="test",
    )
    assert authorized.execution_allowed is True


def test_review_advisory_defenses_cover_absent_authority_and_safe_profiles() -> None:
    settings = production_execution_defaults_v1()
    plan = _plan()
    review = PlanReviewInspection(
        plan=plan,
        runtime=inspect_runtime_limits(settings),
        execution=inspect_execution_settings(settings, None),
        mutation_approved=False,
        tool_version="test",
    )
    safety = _profile("SafetyProfile")
    safe_slice = msgspec.structs.replace(
        plan.slices[0], profiles=PlanProfilesInspection(safety=(safety,))
    )
    safe_plan = msgspec.structs.replace(plan, slices=(safe_slice,))
    safe_review = msgspec.structs.replace(review, plan=safe_plan)
    assert safe_review.policy_advisories == ()

    object.__setattr__(review.plan, "http_authority", None)
    assert review.policy_advisories == ()


def test_workspace_revision_and_relationship_contract_edges() -> None:
    coordinate = _coordinate("Request", "ping", "a")
    with pytest.raises(ValueError, match="digest"):
        ProjectSourceSummary(
            source_id="source", entry=True, resources=(coordinate,), sha256="bad", byte_count=1
        )
    with pytest.raises(ValueError, match="byte count"):
        ProjectSourceSummary(source_id="source", entry=True, byte_count=-1)
    with pytest.raises(ValueError, match="complete"):
        ProjectSourceSummary(source_id="source", entry=True, sha256="a" * 64)

    snapshot = _snapshot()
    foreign = msgspec.structs.replace(
        snapshot.resources[0],
        coordinate=msgspec.structs.replace(snapshot.resources[0].coordinate, namespace="foreign"),
    )
    with pytest.raises(ValueError, match="snapshot namespace"):
        ProjectWorkspaceSnapshot(
            api_version=snapshot.api_version,
            namespace=snapshot.namespace,
            project=snapshot.project,
            resources=(foreign,),
        )

    foreign_relationship = ProjectRelationship(
        path=("request",),
        target=ResourceIdentityCoordinate(
            namespace="foreign", kind="Value", resource_id="credentials"
        ),
        origin="$use",
    )
    project = msgspec.structs.replace(
        snapshot.project,
        outline=ProjectResourceOutline(relationships=(foreign_relationship,)),
    )
    with pytest.raises(ValueError, match="relationships"):
        ProjectWorkspaceSnapshot(
            api_version=snapshot.api_version,
            namespace=snapshot.namespace,
            project=project,
            resources=snapshot.resources,
        )


def test_workspace_inverse_relationships_are_added_once() -> None:
    target = _resource("Value", "credentials")
    relationship = ProjectRelationship(
        path=("request", "body"),
        target=ResourceIdentityCoordinate(
            namespace="local", kind="Value", resource_id="credentials"
        ),
        origin="$use",
    )
    owner = msgspec.structs.replace(
        _resource("Request", "owner"),
        outline=ProjectResourceOutline(relationships=(relationship,)),
    )
    enriched = _with_consumers((owner, target))
    assert enriched[1].outline is not None
    used_by = enriched[1].outline.relationships[0]
    assert used_by.origin == "used-by"
    assert used_by.target.resource_id == "owner"

    target_with_inverse = msgspec.structs.replace(
        target,
        outline=ProjectResourceOutline(relationships=(used_by,)),
    )
    deduplicated = _with_consumers((owner, target_with_inverse))
    assert deduplicated[1].outline is not None
    assert deduplicated[1].outline.relationships == (used_by,)
