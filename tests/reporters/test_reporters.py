"""Contract, safety, and gate-faithfulness tests for all built-in reporters."""

from __future__ import annotations

from xml.etree import ElementTree

import msgspec
import pytest

from comparo.core.artifacts import ArtifactSummary
from comparo.core.artifacts.common import ArtifactCaptureExtent
from comparo.core.artifacts.common import ArtifactFactOutcome
from comparo.core.artifacts.common import ArtifactGateVerdict
from comparo.core.artifacts.common import ArtifactMetric
from comparo.core.artifacts.common import ArtifactOmissionReason
from comparo.core.artifacts.common import ArtifactSeverity
from comparo.core.artifacts.lifecycle import ArtifactGate
from comparo.core.artifacts.lifecycle import ArtifactGateCounts
from comparo.core.artifacts.model import ArtifactCoverageIssue
from comparo.core.artifacts.model import RunArtifact
from comparo.reporters import REPORT_API_VERSION
from comparo.reporters import ReportError
from comparo.reporters import ReportErrorCode
from comparo.reporters import ReportFormat
from comparo.reporters import render_report
from tests.core.artifacts.test_artifact_contracts import _artifact
from tests.core.artifacts.test_artifact_contracts import _definition


def _metric(name: str, value: int) -> ArtifactMetric:
    return ArtifactMetric(name=name, value=value)


def _failed_artifact(*, controls: bool = False) -> RunArtifact:
    artifact = _artifact()
    assertion = msgspec.structs.replace(
        artifact.executions[0].assertions[0],
        outcome=ArtifactFactOutcome.VIOLATED,
    )
    execution = msgspec.structs.replace(artifact.executions[0], assertions=(assertion,))
    diff = msgspec.structs.replace(
        artifact.comparisons[0].diffs[0],
        outcome=ArtifactFactOutcome.DRIFTED,
    )
    comparison = msgspec.structs.replace(artifact.comparisons[0], diffs=(diff,))
    reason = "threshold-exceeded"
    if controls:
        reason = "threshold\x1b]8;;https://evil.test\x07|<script>"
    names = ("drifted", "violated")
    gate_profile = _definition("GateProfile", "strict")
    gate = ArtifactGate(
        verdict=ArtifactGateVerdict.FAILED,
        profile=gate_profile,
        reasons=(reason,),
        coverage=(_metric("run-complete", 1),),
        assessments=(
            ArtifactGateCounts(
                severity=ArtifactSeverity.BLOCKING,
                actual=tuple(_metric(name, 1) for name in names),
                allowed=tuple(_metric(name, 0) for name in names),
            ),
        ),
    )
    return msgspec.structs.replace(
        artifact,
        gate=gate,
        summary=ArtifactSummary(
            executions=(_metric("observed", 2),),
            comparisons=(_metric("evaluated", 1),),
            assertions=(_metric("violated", 1),),
            diffs=(_metric("drifted", 1),),
        ),
        executions=(execution, artifact.executions[1]),
        comparisons=(comparison,),
        resources=tuple(
            sorted(
                (*artifact.resources, gate_profile),
                key=lambda item: (item.namespace, item.kind, item.resource_id),
            )
        ),
        coverage_issues=(
            ArtifactCoverageIssue(
                source="response.body",
                extent=ArtifactCaptureExtent.OMITTED,
                reason=ArtifactOmissionReason.POLICY,
                execution_id=execution.execution_id,
            ),
        ),
    )


@pytest.mark.parametrize("format", list(ReportFormat))
def test_every_reporter_is_pure_deterministic_and_complete(format: ReportFormat) -> None:
    artifact = _failed_artifact()

    first = render_report(artifact, format, maximum_bytes=1_000_000)
    second = render_report(artifact, format, maximum_bytes=1_000_000)

    assert first == second
    assert first.format is format
    assert first.content
    assert not first.content.endswith(b"\x1b")
    exact = render_report(artifact, format, maximum_bytes=len(first.content))
    assert exact.content == first.content
    with pytest.raises(ReportError) as error:
        render_report(artifact, format, maximum_bytes=len(first.content) - 1)
    assert error.value.code is ReportErrorCode.BUDGET_EXCEEDED


def test_json_report_has_an_independent_envelope_and_the_complete_artifact() -> None:
    artifact = _failed_artifact()
    report = render_report(artifact, ReportFormat.JSON, maximum_bytes=1_000_000)

    document = msgspec.json.decode(report.content)

    assert document["api_version"] == REPORT_API_VERSION
    assert document["format"] == "json"
    assert document["artifact_api_version"] == artifact.api_version
    assert document["artifact"]["metadata"]["artifact_id"] == "artifact-0"
    assert document["artifact"]["coverage_issues"][0]["extent"] == "omitted"


def test_junit_uses_gate_dimensions_and_never_reinterprets_rule_facts() -> None:
    failed = _failed_artifact()
    report = render_report(failed, ReportFormat.JUNIT, maximum_bytes=1_000_000)
    suite = ElementTree.fromstring(report.content)  # noqa: S314 - parse trusted local output
    names = tuple(item.attrib["name"] for item in suite.findall("testcase"))

    assert suite.attrib["failures"] == "3"
    assert names == ("gate-verdict", "blocking.drifted", "blocking.violated")
    assert not any("rule" in name for name in names)

    not_requested = ElementTree.fromstring(  # noqa: S314 - parse trusted local output
        render_report(_artifact(), ReportFormat.JUNIT, maximum_bytes=1_000_000).content
    )
    assert not_requested.attrib["failures"] == "0"
    assert not_requested.attrib["skipped"] == "1"

    failed_assessment = failed.gate.assessments[0]
    passed_gate = msgspec.structs.replace(
        failed.gate,
        verdict=ArtifactGateVerdict.PASSED,
        reasons=(),
        assessments=(
            msgspec.structs.replace(
                failed_assessment,
                allowed=failed_assessment.actual,
            ),
        ),
    )
    passed_with_adverse_facts = msgspec.structs.replace(failed, gate=passed_gate)
    passed = ElementTree.fromstring(  # noqa: S314 - parse trusted local output
        render_report(
            passed_with_adverse_facts,
            ReportFormat.JUNIT,
            maximum_bytes=1_000_000,
        ).content
    )
    assert passed.attrib["failures"] == "0"
    assert passed.attrib["skipped"] == "0"


def test_sarif_contains_only_adverse_facts_and_coverage_findings() -> None:
    document = msgspec.json.decode(
        render_report(
            _failed_artifact(),
            ReportFormat.SARIF,
            maximum_bytes=1_000_000,
        ).content
    )
    run = document["runs"][0]
    results = run["results"]
    rules = run["tool"]["driver"]["rules"]

    assert document["version"] == "2.1.0"
    assert [item["id"] for item in rules] == sorted(item["id"] for item in rules)
    adverse_rule_ids = {
        item["ruleId"] for item in results if item["ruleId"] != "comparo/artifact-coverage"
    }
    assert len(adverse_rule_ids) == 2
    assert {item["properties"]["outcome"] for item in results if "properties" in item} == {
        "violated",
        "drifted",
    }
    assert any(item["ruleId"] == "comparo/artifact-coverage" for item in results)
    assert run["invocations"][0]["executionSuccessful"] is True

    positive = msgspec.json.decode(
        render_report(_artifact(), ReportFormat.SARIF, maximum_bytes=1_000_000).content
    )
    assert positive["runs"][0]["results"] == []


def test_markdown_has_fixed_sections_and_escapes_table_and_html_injection() -> None:
    content = render_report(
        _failed_artifact(controls=True),
        ReportFormat.MARKDOWN,
        maximum_bytes=1_000_000,
    ).content.decode()

    for section in (
        "## Overview",
        "## Gate",
        "## Coverage",
        "## Executions",
        "## Comparisons",
        "## Findings",
        "## Budgets",
    ):
        assert section in content
    assert "\x1b" not in content
    assert "<script>" not in content
    assert "\\|&lt;script&gt;" in content


@pytest.mark.parametrize("format", list(ReportFormat))
def test_presentation_outputs_never_contain_raw_terminal_controls(format: ReportFormat) -> None:
    content = render_report(
        _failed_artifact(controls=True),
        format,
        maximum_bytes=1_000_000,
    ).content

    assert b"\x1b" not in content
    assert b"\x07" not in content


def test_report_dispatch_rejects_untyped_format_and_limit() -> None:
    with pytest.raises(ReportError) as bad_format:
        render_report(_artifact(), "json", maximum_bytes=1_000_000)  # type: ignore[arg-type]
    assert bad_format.value.code is ReportErrorCode.INVALID_INPUT

    with pytest.raises(ReportError) as bad_limit:
        render_report(_artifact(), ReportFormat.JSON, maximum_bytes=True)
    assert bad_limit.value.code is ReportErrorCode.INVALID_INPUT
