"""Exhaustive defensive, escaping, and variant coverage for report projections."""

from __future__ import annotations

from collections.abc import Callable
from xml.etree import ElementTree

import msgspec
import pytest

from comparo.core.artifacts import ArtifactAmount
from comparo.core.artifacts import ArtifactAmountUnit
from comparo.core.artifacts import ArtifactBudget
from comparo.core.artifacts import ArtifactCaptureExtent
from comparo.core.artifacts import ArtifactCoverageIssue
from comparo.core.artifacts import ArtifactFactOutcome
from comparo.core.artifacts import ArtifactGate
from comparo.core.artifacts import ArtifactGateCounts
from comparo.core.artifacts import ArtifactGateVerdict
from comparo.core.artifacts import ArtifactMetric
from comparo.core.artifacts import ArtifactOmissionReason
from comparo.core.artifacts import ArtifactSeverity
from comparo.core.artifacts import RunArtifact
from comparo.reporters import RenderedReport
from comparo.reporters import ReportError
from comparo.reporters import ReportErrorCode
from comparo.reporters import ReportFormat
from comparo.reporters import junit as junit_reporter
from comparo.reporters import markdown as markdown_reporter
from comparo.reporters import render_json_report
from comparo.reporters import render_junit_report
from comparo.reporters import render_markdown_report
from comparo.reporters import render_sarif_report
from comparo.reporters import sarif as sarif_reporter
from comparo.reporters._presentation import markdown_cell
from comparo.reporters._presentation import safe_plain
from comparo.reporters.contracts import complete_report
from tests.core.artifacts.test_artifact_contracts import _artifact


def test_report_error_and_rendered_report_reject_invalid_contract_values() -> None:
    with pytest.raises(TypeError, match="report error code must be typed"):
        ReportError("invalid-input")  # type: ignore[arg-type]

    invalid_reports: tuple[tuple[dict[str, object], type[Exception], str], ...] = (
        ({"format": "json"}, TypeError, "format must be typed"),
        ({"media_type": ""}, ValueError, "media type must be non-empty"),
        ({"media_type": 1}, ValueError, "media type must be non-empty"),
        ({"extension": "json"}, ValueError, "extension must begin"),
        ({"extension": 1}, ValueError, "extension must begin"),
        ({"content": bytearray()}, TypeError, "content must be bytes"),
    )
    defaults: dict[str, object] = {
        "format": ReportFormat.JSON,
        "media_type": "application/json",
        "extension": ".json",
        "content": b"{}",
    }
    for replacement, exception, message in invalid_reports:
        values = defaults | replacement
        with pytest.raises(exception, match=message):
            RenderedReport(**values)  # type: ignore[arg-type]


def test_complete_report_rejects_invalid_limits_and_non_byte_content() -> None:
    for maximum in (-1, True, 1.5):
        with pytest.raises(ReportError) as invalid_limit:
            complete_report(
                format=ReportFormat.JSON,
                media_type="application/json",
                extension=".json",
                content=b"{}",
                maximum_bytes=maximum,  # type: ignore[arg-type]
            )
        assert invalid_limit.value.code is ReportErrorCode.INVALID_INPUT

    with pytest.raises(ReportError) as invalid_content:
        complete_report(
            format=ReportFormat.JSON,
            media_type="application/json",
            extension=".json",
            content=bytearray(),  # type: ignore[arg-type]
            maximum_bytes=1,
        )
    assert invalid_content.value.code is ReportErrorCode.RENDER_FAILED


@pytest.mark.parametrize(
    ("renderer", "expected_format", "media_type", "extension"),
    [
        (render_json_report, ReportFormat.JSON, "application/vnd.comparo.report+json", ".json"),
        (render_junit_report, ReportFormat.JUNIT, "application/junit+xml", ".xml"),
        (render_sarif_report, ReportFormat.SARIF, "application/sarif+json", ".sarif"),
        (
            render_markdown_report,
            ReportFormat.MARKDOWN,
            "text/markdown; charset=utf-8",
            ".md",
        ),
    ],
)
def test_direct_renderers_reject_invalid_artifacts_and_expose_stable_metadata(
    renderer: Callable[..., RenderedReport],
    expected_format: ReportFormat,
    media_type: str,
    extension: str,
) -> None:
    with pytest.raises(ReportError) as invalid:
        renderer(object(), maximum_bytes=1_000_000)
    assert invalid.value.code is ReportErrorCode.INVALID_INPUT

    report = renderer(_artifact(), maximum_bytes=1_000_000)
    assert (report.format, report.media_type, report.extension) == (
        expected_format,
        media_type,
        extension,
    )


def _raise_report_error(_artifact_value: object) -> tuple[object, ...]:
    raise ReportError(ReportErrorCode.BUDGET_EXCEEDED)


def _raise_runtime_error(*_args: object, **_kwargs: object) -> bytes:
    raise RuntimeError("unsafe payload that must not cross the reporter boundary")


def test_json_translates_encoder_failures_without_retaining_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(msgspec.json, "encode", _raise_runtime_error)

    with pytest.raises(ReportError) as failed:
        render_json_report(_artifact(), maximum_bytes=1_000_000)

    assert failed.value.code is ReportErrorCode.RENDER_FAILED
    assert str(failed.value) == "render-failed"
    assert type(failed.value.__cause__) is RuntimeError


@pytest.mark.parametrize(
    ("module", "helper", "renderer"),
    [
        (junit_reporter, "_gate_cases", render_junit_report),
        (markdown_reporter, "_gate_lines", render_markdown_report),
        (sarif_reporter, "_adverse_facts", render_sarif_report),
    ],
)
def test_structured_report_errors_pass_through_renderer_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    helper: str,
    renderer: Callable[..., RenderedReport],
) -> None:
    monkeypatch.setattr(module, helper, _raise_report_error)

    with pytest.raises(ReportError) as failed:
        renderer(_artifact(), maximum_bytes=1_000_000)

    assert failed.value.code is ReportErrorCode.BUDGET_EXCEEDED
    assert failed.value.__cause__ is None


@pytest.mark.parametrize(
    ("module", "helper", "renderer"),
    [
        (junit_reporter, "_gate_cases", render_junit_report),
        (markdown_reporter, "_gate_lines", render_markdown_report),
        (sarif_reporter, "_adverse_facts", render_sarif_report),
    ],
)
def test_unexpected_renderer_failures_are_replaced_by_safe_codes(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    helper: str,
    renderer: Callable[..., RenderedReport],
) -> None:
    monkeypatch.setattr(module, helper, _raise_runtime_error)

    with pytest.raises(ReportError) as failed:
        renderer(_artifact(), maximum_bytes=1_000_000)

    assert failed.value.code is ReportErrorCode.RENDER_FAILED
    assert "unsafe payload" not in str(failed.value)
    assert type(failed.value.__cause__) is RuntimeError


def test_junit_uses_fallback_reason_for_failed_gate_without_reason_text() -> None:
    artifact = _artifact()
    failed_gate = ArtifactGate(
        verdict=ArtifactGateVerdict.FAILED,
        profile=artifact.provenance.execution_policy,
        reasons=(),
        coverage=(),
        assessments=(
            ArtifactGateCounts(
                severity=ArtifactSeverity.BLOCKING,
                actual=(ArtifactMetric(name="violated", value=1),),
                allowed=(ArtifactMetric(name="violated", value=0),),
            ),
        ),
    )
    failed = msgspec.structs.replace(artifact, gate=failed_gate)

    root = ElementTree.fromstring(  # noqa: S314 - parse trusted generated XML
        render_junit_report(failed, maximum_bytes=1_000_000).content
    )

    failure = root.find("testcase/failure")
    assert failure is not None
    assert failure.attrib["message"] == "gate failed"
    assert root.attrib == {
        "name": "comparo.gate",
        "tests": "2",
        "failures": "2",
        "errors": "0",
        "skipped": "0",
    }


def test_empty_markdown_variants_and_exact_fractional_budgets_are_explicit() -> None:
    artifact = _artifact()
    empty = msgspec.structs.replace(
        artifact,
        executions=(),
        comparisons=(),
        blobs=(),
        coverage_issues=(),
        budgets=(),
    )
    empty_text = render_markdown_report(empty, maximum_bytes=1_000_000).content.decode()

    assert "gate not requested or no count thresholds" in empty_text
    assert "all retained sources" in empty_text
    assert "no adverse findings" in empty_text
    assert "| — | — | — | — | no |" in empty_text

    exhausted_ratio = ArtifactBudget(
        resource="decompression-ratio",
        scope="run",
        limit=ArtifactAmount(unit=ArtifactAmountUnit.RATIO, numerator=3, denominator=2),
        used=ArtifactAmount(unit=ArtifactAmountUnit.RATIO, numerator=3, denominator=2),
        exhausted=True,
    )
    ratio_artifact = msgspec.structs.replace(artifact, budgets=(exhausted_ratio,))
    ratio_text = render_markdown_report(
        ratio_artifact,
        maximum_bytes=1_000_000,
    ).content.decode()

    assert "| decompression-ratio | run | 3/2 ratio | 3/2 ratio | yes |" in ratio_text


def _adverse_with(
    artifact: RunArtifact,
    *,
    outcome: ArtifactFactOutcome,
    severity: ArtifactSeverity,
    evidence: bool,
) -> RunArtifact:
    assertion = msgspec.structs.replace(
        artifact.executions[0].assertions[0],
        outcome=outcome,
        severity=severity,
        evidence=(artifact.executions[0].assertions[0].evidence if evidence else ()),
    )
    execution = msgspec.structs.replace(artifact.executions[0], assertions=(assertion,))
    return msgspec.structs.replace(artifact, executions=(execution,))


@pytest.mark.parametrize(
    ("severity", "level"),
    [
        (ArtifactSeverity.BLOCKING, "error"),
        (ArtifactSeverity.WARNING, "warning"),
        (ArtifactSeverity.INFO, "note"),
    ],
)
def test_sarif_maps_every_severity_and_optional_evidence(
    severity: ArtifactSeverity,
    level: str,
) -> None:
    artifact = _adverse_with(
        _artifact(),
        outcome=ArtifactFactOutcome.ERROR,
        severity=severity,
        evidence=severity is not ArtifactSeverity.INFO,
    )
    run = msgspec.json.decode(render_sarif_report(artifact, maximum_bytes=1_000_000).content)[
        "runs"
    ][0]
    result = run["results"][0]

    assert result["level"] == level
    assert ("locations" in result) is (severity is not ArtifactSeverity.INFO)


@pytest.mark.parametrize(
    "outcome",
    [
        ArtifactFactOutcome.SATISFIED,
        ArtifactFactOutcome.EQUIVALENT,
        ArtifactFactOutcome.SKIPPED,
        ArtifactFactOutcome.NOT_EVALUATED,
    ],
)
def test_sarif_excludes_every_non_adverse_outcome(outcome: ArtifactFactOutcome) -> None:
    artifact = _artifact()
    assertion = msgspec.structs.replace(
        artifact.executions[0].assertions[0],
        outcome=outcome,
    )
    execution = msgspec.structs.replace(artifact.executions[0], assertions=(assertion,))
    without_diffs = msgspec.structs.replace(artifact.comparisons[0], diffs=())
    candidate = msgspec.structs.replace(
        artifact,
        executions=(execution,),
        comparisons=(without_diffs,),
    )

    run = msgspec.json.decode(render_sarif_report(candidate, maximum_bytes=1_000_000).content)[
        "runs"
    ][0]

    assert run["results"] == []
    assert run["tool"]["driver"]["rules"] == []


def test_coverage_without_execution_uses_run_fingerprint_and_escaped_source() -> None:
    artifact = _artifact()
    issue = ArtifactCoverageIssue(
        source="response|body\x1b]8;;https://evil.test\x07",
        extent=ArtifactCaptureExtent.PARTIAL,
        reason=ArtifactOmissionReason.REDACTION_FAILED,
        execution_id=None,
    )
    candidate = msgspec.structs.replace(artifact, coverage_issues=(issue,))

    run = msgspec.json.decode(render_sarif_report(candidate, maximum_bytes=1_000_000).content)[
        "runs"
    ][0]
    result = run["results"][0]

    assert (
        result["partialFingerprints"]["coverageSource"]
        == "run:response|body�]8;;https://evil.test�"
    )
    assert "\x1b" not in result["message"]["text"]
    assert run["invocations"][0]["executionSuccessful"] is True


def test_safe_plain_and_markdown_cell_cover_all_character_classes() -> None:
    hostile = "A\t\n\r\x00\u202e\ud800\ue000\U00010000\ufffd"
    plain = safe_plain(hostile)

    assert plain == "A\t\n\r���\ue000\U00010000\ufffd"
    assert markdown_cell("<&>\\|`\r\n\x1b") == "&lt;&amp;&gt;\\\\\\|\\`  �"
    with pytest.raises(TypeError, match="presentation values must be text"):
        safe_plain(1)  # type: ignore[arg-type]


def test_json_encoder_and_all_human_formats_are_byte_deterministic() -> None:
    artifact = _artifact()
    renderers: tuple[Callable[..., RenderedReport], ...] = (
        render_json_report,
        render_junit_report,
        render_sarif_report,
        render_markdown_report,
    )

    for renderer in renderers:
        first = renderer(artifact, maximum_bytes=1_000_000)
        second = renderer(artifact, maximum_bytes=1_000_000)
        assert first.content == second.content
        assert type(first.content) is bytes
