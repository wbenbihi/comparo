"""Tests for the exact bounded Plan checkpoint consumed by frontends."""

from __future__ import annotations

from pathlib import Path

import msgspec
import pytest

from comparo.application.inspection import EnvironmentPairInspection
from comparo.application.inspection import PlanArtifactAuthorityInspection
from comparo.application.inspection import PlanCardinalityInspection
from comparo.application.inspection import PlanHttpAuthorityInspection
from comparo.application.inspection import PlanInspection
from comparo.application.inspection import PlanningSourceInspection
from comparo.application.inspection import PlanSliceInspection
from comparo.application.inspection import PlanValidationInspection
from comparo.application.inspection import PlanValueAuthorityInspection
from comparo.application.inspection import ProvenanceInspection
from comparo.application.inspection import ResourceCoordinate
from comparo.application.plan_review import PlanReviewInspection
from comparo.application.plan_review import RuntimeLimitsInspection
from comparo.application.plan_review import clipboard_summary
from comparo.application.plan_review import inspect_execution_settings
from comparo.application.plan_review import inspect_runtime_limits
from comparo.application.production_execution import production_execution_defaults_v1


def _coordinate(kind: str, resource_id: str, fingerprint: str) -> ResourceCoordinate:
    return ResourceCoordinate(
        namespace="local",
        kind=kind,
        resource_id=resource_id,
        fingerprint=fingerprint * 64,
    )


def _plan(*, method: str = "GET", require_approval: bool = False) -> PlanInspection:
    staging = _coordinate("Environment", "staging", "1")
    production = _coordinate("Environment", "production", "2")
    cardinality = PlanCardinalityInspection(
        cells=1,
        trials=1,
        environment_executions=2,
        comparisons=1,
        maximum_attempts=2,
    )
    plan_slice = PlanSliceInspection(
        subject=_coordinate("Request", "checkout", "3"),
        matrices=(),
        trials=1,
        environments=(staging, production),
        comparisons=(EnvironmentPairInspection(baseline=staging, candidate=production),),
        maximum_attempts_per_execution=1,
        cardinality=cardinality,
        method=method,
        unsafe_method=method != "GET",
    )
    return PlanInspection(
        api_version="comparo.plan/v1alpha1",
        run_id="run-1",
        invocation_id="invocation-1",
        fingerprint="a" * 64,
        ordering="subject-matrix-trial-environment/v1",
        policy=_coordinate("ExecutionPolicy", "ci", "4"),
        cardinality=cardinality,
        environment_count=2,
        write_capable_subjects=0 if method == "GET" else 1,
        slices=(plan_slice,),
        budgets=(),
        provenance=(),
        validation=PlanValidationInspection(assertion_rules=0, diff_rules=0, gate=None),
        http_authority=PlanHttpAuthorityInspection(
            allowed_schemes=("https",),
            allowed_methods=tuple(dict.fromkeys(("GET", method))),
            allowed_hosts=("api.example.test",),
            allowed_ports=(443,),
            allow_any_host=False,
            allow_any_port=False,
            require_mutation_approval=require_approval,
        ),
        value_authority=PlanValueAuthorityInspection(
            ambient_environment_names=(),
            allow_any_ambient_environment=False,
            allow_project_files=False,
            remote_schema_sources=(),
            allow_any_remote_schemas=False,
            secret_egress=(),
            allow_any_secret_egress=False,
        ),
        artifact_authority=PlanArtifactAuthorityInspection(
            allow_sanitized_persistence=False,
            root_count=0,
            selected_root_present=False,
            selected_root_authorized=False,
        ),
    )


def test_runtime_limit_inspection_projects_every_enforced_ceiling() -> None:
    settings = production_execution_defaults_v1()
    inspected = inspect_runtime_limits(settings)

    assert inspected.find("http", "connections").value == (
        settings.transport.maximum_connections.value
    )
    assert inspected.find("capture", "entity-body-bytes").value == (
        settings.transport.capture.maximum_entity_body_bytes.value
    )
    assert inspected.find("ordinary", "body-bytes").value == (
        settings.ordinary.maximum_body_bytes.value
    )
    assert inspected.find("request-body", "bytes").value == (
        settings.request_body.maximum_body_bytes.value
    )
    assert inspected.find("streams", "records").value == settings.streams.maximum_records.value
    assert inspected.find("artifacts", "total-blob-bytes").value == (
        settings.artifact.limits.maximum_total_blob_bytes.value
    )
    assert len(inspected.limits) == 72
    assert inspected.artifact_persistence == settings.artifact.persist.value


def test_execution_settings_inspection_binds_capture_classification_and_root_without_path(
    tmp_path: Path,
) -> None:
    settings = production_execution_defaults_v1()
    first_root = (tmp_path / "private-canary-root").resolve()
    second_root = (tmp_path / "other-root").resolve()
    baseline = inspect_execution_settings(settings, first_root)
    capture = msgspec.structs.replace(settings.artifact.capture, bodies=False)
    changed = msgspec.structs.replace(
        settings,
        artifact=msgspec.structs.replace(settings.artifact, capture=capture),
    )

    assert inspect_execution_settings(changed, first_root).fingerprint != baseline.fingerprint
    assert (
        inspect_execution_settings(settings, second_root).artifact_root_fingerprint
        != baseline.artifact_root_fingerprint
    )
    assert baseline.capture_bodies is True
    assert str(first_root).encode() not in msgspec.json.encode(baseline)


def test_clipboard_summary_is_constant_size_and_never_flattens_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = production_execution_defaults_v1()
    runtime = inspect_runtime_limits(settings)
    execution = inspect_execution_settings(settings, None)
    plan = _plan()
    review = PlanReviewInspection(
        plan=plan,
        runtime=runtime,
        execution=execution,
        mutation_approved=False,
        tool_version="test",
    )
    expanded = msgspec.structs.replace(
        plan,
        fingerprint="c" * 64,
        provenance=tuple(
            ProvenanceInspection(
                field=f"field-{index}",
                sources=(PlanningSourceInspection(kind=f"default-{index}"),),
            )
            for index in range(1_000)
        ),
    )

    encode = msgspec.json.encode

    def reject_full_review_encoding(_value: object) -> bytes:
        raise AssertionError("clipboard summary must not encode the complete review")

    monkeypatch.setattr(
        "comparo.application.plan_review_clipboard.msgspec.json.encode",
        reject_full_review_encoding,
    )
    summary = clipboard_summary(review)
    expanded_summary = clipboard_summary(
        PlanReviewInspection(
            plan=expanded,
            runtime=runtime,
            execution=execution,
            mutation_approved=False,
            tool_version="test",
        )
    )

    assert summary.review_fingerprint != expanded_summary.review_fingerprint
    assert summary.plan_fingerprint != expanded_summary.plan_fingerprint
    assert len(encode(summary)) < 1_024
    assert summary.planned_executions == "2"
    assert summary.policy_id == "ci"
    assert summary.tool_version == "test"
    assert summary.execution_allowed is True
    assert summary.execution_blockers == ()


def test_clipboard_summary_bounds_counts_beyond_python_decimal_conversion_limit() -> None:
    plan = _plan()
    huge = 10**5_000
    cardinality = PlanCardinalityInspection(
        cells=1,
        trials=huge,
        environment_executions=huge * 2,
        comparisons=huge,
        maximum_attempts=huge * 2,
    )
    plan_slice = msgspec.structs.replace(
        plan.slices[0],
        trials=huge,
        cardinality=cardinality,
    )
    huge_plan = msgspec.structs.replace(
        plan,
        fingerprint="d" * 64,
        cardinality=cardinality,
        slices=(plan_slice,),
    )
    settings = production_execution_defaults_v1()
    review = PlanReviewInspection(
        plan=huge_plan,
        runtime=inspect_runtime_limits(settings),
        execution=inspect_execution_settings(settings, None),
        mutation_approved=False,
        tool_version="test",
    )

    summary = clipboard_summary(review)
    encoded = msgspec.json.encode(summary)

    assert len(encoded) < 1_024
    assert summary.trials.startswith("≥2^")
    assert "exact value retained" in summary.planned_executions


def test_plan_review_surfaces_broad_policy_advisories_for_read_only_work() -> None:
    plan = _plan()
    assert plan.http_authority is not None
    assert plan.value_authority is not None
    permissive = msgspec.structs.replace(
        plan,
        http_authority=msgspec.structs.replace(
            plan.http_authority,
            allowed_hosts=(),
            allowed_ports=(),
            allow_any_host=True,
            allow_any_port=True,
            allow_proxy_dns=True,
        ),
        value_authority=msgspec.structs.replace(
            plan.value_authority,
            allow_any_ambient_environment=True,
            allow_project_files=True,
            allow_any_remote_schemas=True,
            allow_any_secret_egress=True,
        ),
    )
    settings = production_execution_defaults_v1()
    review = PlanReviewInspection(
        plan=permissive,
        runtime=inspect_runtime_limits(settings),
        execution=inspect_execution_settings(settings, None),
        mutation_approved=False,
        tool_version="test",
    )

    assert review.execution_allowed is True
    assert review.policy_advisories == (
        "no-safety-profile",
        "any-host",
        "any-port",
        "proxy-dns",
        "any-ambient-environment",
        "project-files",
        "any-remote-schemas",
        "any-secret-egress",
    )


def test_plan_review_keeps_unapproved_write_checkpoint_visible_but_not_executable() -> None:
    settings = production_execution_defaults_v1()
    runtime = inspect_runtime_limits(settings)
    execution = inspect_execution_settings(settings, None)
    plan = _plan(method="POST", require_approval=True)

    unapproved = PlanReviewInspection(
        plan=plan,
        runtime=runtime,
        execution=execution,
        mutation_approved=False,
        tool_version="test",
    )
    approved = PlanReviewInspection(
        plan=plan,
        runtime=runtime,
        execution=execution,
        mutation_approved=True,
        tool_version="test",
    )
    assert unapproved.execution_allowed is False
    assert approved.execution_allowed is True
    assert clipboard_summary(approved).write_capable_subjects == "1"


def test_plan_projection_rejects_contradictory_cardinality_and_fingerprint() -> None:
    plan = _plan()
    with pytest.raises(ValueError, match="contradicts its slices"):
        msgspec.structs.replace(
            plan,
            cardinality=msgspec.structs.replace(plan.cardinality, comparisons=0),
        )
    with pytest.raises(ValueError, match="SHA-256"):
        msgspec.structs.replace(plan, fingerprint="not-a-digest")
    with pytest.raises(ValueError, match="attached AssertionProfile"):
        msgspec.structs.replace(
            plan,
            validation=PlanValidationInspection(assertion_rules=1, diff_rules=0, gate=None),
        )
    with pytest.raises(ValueError, match="attached DiffProfile"):
        msgspec.structs.replace(
            plan,
            validation=PlanValidationInspection(assertion_rules=0, diff_rules=1, gate=None),
        )


def test_plan_review_rejects_untyped_inputs() -> None:
    settings = production_execution_defaults_v1()
    runtime = inspect_runtime_limits(settings)
    execution = inspect_execution_settings(settings, None)
    plan = _plan()
    with pytest.raises(TypeError, match="ProductionExecutionSettings"):
        inspect_runtime_limits(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ProductionExecutionSettings"):
        inspect_execution_settings(object(), None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="PlanReviewInspection"):
        clipboard_summary(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="PlanInspection"):
        PlanReviewInspection(
            plan=object(),  # type: ignore[arg-type]
            runtime=runtime,
            execution=execution,
            mutation_approved=False,
            tool_version="test",
        )

    with pytest.raises(ValueError, match="canonical v1 ceiling set"):
        RuntimeLimitsInspection(limits=(), artifact_persistence="never")
    with pytest.raises(ValueError, match="HTTP policy authority"):
        PlanReviewInspection(
            plan=msgspec.structs.replace(plan, http_authority=None),
            runtime=runtime,
            execution=execution,
            mutation_approved=False,
            tool_version="test",
        )
    with pytest.raises(ValueError, match="artifact-root selection"):
        PlanReviewInspection(
            plan=plan,
            runtime=runtime,
            execution=inspect_execution_settings(settings, Path.cwd().resolve()),
            mutation_approved=False,
            tool_version="test",
        )
    with pytest.raises(TypeError, match="RuntimeLimitsInspection"):
        PlanReviewInspection(
            plan=plan,
            runtime=object(),  # type: ignore[arg-type]
            execution=execution,
            mutation_approved=False,
            tool_version="test",
        )
    with pytest.raises(TypeError, match="ExecutionSettingsInspection"):
        PlanReviewInspection(
            plan=plan,
            runtime=runtime,
            execution=object(),  # type: ignore[arg-type]
            mutation_approved=False,
            tool_version="test",
        )
