"""Effect-free Plan projection edge coverage."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import msgspec
import pytest

from comparo.application.evaluation_binding import EvaluationPreflightResult
from comparo.application.evaluation_binding import preflight_configuration_evaluation
from comparo.application.plan_inspection import _artifact_authority
from comparo.application.plan_inspection import _canonical_string
from comparo.application.plan_inspection import _inspect_planning_source
from comparo.application.plan_inspection import _inspect_profile_handle
from comparo.application.plan_inspection import _profiles
from comparo.application.plan_inspection import _unsafe_method
from comparo.application.plan_inspection import _validation
from comparo.application.plan_inspection import inspect_plan
from comparo.application.plan_inspection_profiles import PlanProfilesInspection
from comparo.application.plan_inspection_topology import InlineSourceCoordinate
from comparo.core.configuration.canonical import CanonicalMember
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.domain.diagnostics import Diagnostic
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.diagnostics import DiagnosticStage
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ObjectExpressionMember
from comparo.core.domain.identity import InlineDefinitionRef
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonString
from comparo.core.planning.models import PlanningSource
from comparo.core.planning.models import PreparedRun
from comparo.core.policy import CompiledExecutionPolicy
from comparo.core.policy import trusted_local_policy
from tests.application.test_evaluation_binding import _prepared as _evaluation_prepared
from tests.application.test_inspection import _prepared
from tests.application.test_plan_sources_contract_edges import _inline_resource
from tests.application.test_planning import _literal


def _diagnostic() -> Diagnostic:
    return Diagnostic(
        code=DiagnosticCode(value="evaluation.test.rejected"),
        severity=DiagnosticSeverity.ERROR,
        stage=DiagnosticStage.EVALUATION,
    )


def test_inspect_plan_rejects_invalid_optional_inputs() -> None:
    prepared = _prepared()
    with pytest.raises(TypeError, match="compiled"):
        inspect_plan(prepared, policy=cast("CompiledExecutionPolicy", object()))
    with pytest.raises(ValueError, match="differs"):
        inspect_plan(prepared, policy=trusted_local_policy())
    with pytest.raises(TypeError, match="evaluation preflight"):
        inspect_plan(prepared, evaluation=cast("EvaluationPreflightResult", object()))
    rejected = EvaluationPreflightResult(
        diagnostics=(_diagnostic(),),
        rejected=(),
        assertion_rules=0,
        diff_rules=0,
        gate=None,
    )
    with pytest.raises(ValueError, match="rejected"):
        inspect_plan(prepared, evaluation=rejected)
    with pytest.raises(TypeError, match="artifact root"):
        inspect_plan(prepared, artifact_root=cast("Path", object()))
    with pytest.raises(ValueError, match="absolute"):
        inspect_plan(prepared, artifact_root=Path("relative"))


def test_inspect_plan_projects_matching_policy_and_empty_evaluation() -> None:
    prepared = _prepared()
    policy = msgspec.structs.replace(
        trusted_local_policy(),
        definition=prepared.authority.policy,
    )
    evaluation = EvaluationPreflightResult(
        diagnostics=(),
        rejected=(),
        assertion_rules=0,
        diff_rules=0,
        gate=None,
    )
    inspected = inspect_plan(prepared, policy=policy, evaluation=evaluation)
    assert inspected.http_authority is not None
    assert inspected.value_authority is not None
    assert inspected.artifact_authority is not None
    assert inspected.validation is not None
    assert inspected.validation.gate is None


def test_gate_validation_projection_retains_exact_compiled_formula() -> None:
    prepared = _evaluation_prepared(gate="{}")
    evaluation = preflight_configuration_evaluation(
        prepared,
        policy=trusted_local_policy(),
    )
    assert evaluation.supported
    assert evaluation.gate is not None
    inspected = _validation(evaluation)
    assert inspected.gate is not None
    assert inspected.gate.profile.kind == "GateProfile"
    assert inspected.gate.thresholds


def test_artifact_authority_projects_selected_authorized_and_resolution_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = trusted_local_policy()
    selected = _artifact_authority(policy, tmp_path.resolve())
    assert selected.selected_root_present is True
    assert selected.selected_root_authorized is True

    def fail_resolution(_self: Path, *, strict: bool = False) -> Path:
        del strict
        raise OSError

    monkeypatch.setattr(Path, "resolve", fail_resolution)
    failed = _artifact_authority(policy, tmp_path)
    assert failed.selected_root_present is True
    assert failed.selected_root_authorized is False


def test_inline_planning_source_and_profile_handle_projection() -> None:
    inline = _inline_resource(_literal("value")).definition
    assert type(inline) is InlineDefinitionRef
    source = PlanningSource(kind=ProvenanceKind.PROFILE, definition=inline)
    inspected_source = _inspect_planning_source(source)
    assert type(inspected_source.inline) is InlineSourceCoordinate
    assert inspected_source.inline.fragment == "#safety"

    attachment = _inspect_profile_handle(inline)
    assert attachment.inline is True
    assert attachment.owner == ("local", "Request", "ping")
    with pytest.raises(TypeError, match="definition handle"):
        _inspect_profile_handle(object())
    with pytest.raises(TypeError, match="EffectiveProfiles"):
        _profiles(object())
    assert type(_profiles(_prepared().effective.subjects[0].profiles)) is PlanProfilesInspection


def test_method_classification_is_non_throwing_for_unresolved_and_invalid_methods() -> None:
    assert _unsafe_method(None) is None
    assert _unsafe_method("NOT A METHOD") is None
    assert _unsafe_method("GET") is False
    assert _unsafe_method("POST") is True


def test_canonical_string_lookup_covers_missing_and_non_string_shapes() -> None:
    provenance = _literal("value").provenance
    literal = _literal("GET")
    expression = ObjectExpr(
        members=(ObjectExpressionMember(name="method", expression=literal),),
        provenance=provenance,
    )
    canonical = CanonicalObject(members=(CanonicalMember(name="request", value=expression),))
    assert _canonical_string(canonical, ("request", "method")) == "GET"
    assert _canonical_string(canonical, ("missing",)) is None
    assert _canonical_string(canonical, ("request", "missing")) is None
    assert _canonical_string(JsonString(value="leaf"), ("nested",)) is None
    assert _canonical_string(JsonString(value="leaf"), ()) == "leaf"

    integer_literal = LiteralExpr(
        value=JsonInteger(value=1),
        provenance=provenance,
    )
    non_string = CanonicalObject(members=(CanonicalMember(name="value", value=integer_literal),))
    assert _canonical_string(non_string, ("value",)) is None


def test_projection_helpers_reject_untyped_roots() -> None:
    with pytest.raises(TypeError, match="PreparedRun"):
        inspect_plan(cast("PreparedRun", object()))
