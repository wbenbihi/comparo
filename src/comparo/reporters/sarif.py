"""SARIF 2.1.0 projection of adverse facts and incomplete artifact coverage."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Final

import msgspec

from comparo.core.artifacts.common import ArtifactCompletionKind
from comparo.core.artifacts.common import ArtifactDefinition
from comparo.core.artifacts.common import ArtifactFactOutcome
from comparo.core.artifacts.common import ArtifactSeverity
from comparo.core.artifacts.facts import ArtifactCoordinate
from comparo.core.artifacts.facts import ArtifactFact
from comparo.core.artifacts.model import ArtifactCoverageIssue
from comparo.core.artifacts.model import RunArtifact
from comparo.reporters._presentation import safe_plain
from comparo.reporters.contracts import RenderedReport
from comparo.reporters.contracts import ReportError
from comparo.reporters.contracts import ReportErrorCode
from comparo.reporters.contracts import ReportFormat
from comparo.reporters.contracts import complete_report

_SARIF_SCHEMA: Final = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/Schemata/sarif-schema-2.1.0.json"
)
_NON_ADVERSE: Final = frozenset(
    {
        ArtifactFactOutcome.SATISFIED,
        ArtifactFactOutcome.EQUIVALENT,
        ArtifactFactOutcome.SKIPPED,
        ArtifactFactOutcome.NOT_EVALUATED,
    }
)
_UUID: Final = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
)


@dataclass(frozen=True, slots=True)
class _Finding:
    fact: ArtifactFact
    owner: str
    owner_ordinal: int
    fact_ordinal: int


def render_sarif_report(artifact: RunArtifact, *, maximum_bytes: int) -> RenderedReport:
    """Render adverse artifact facts without inventing gate or source-code semantics."""
    if type(artifact) is not RunArtifact:
        raise ReportError(ReportErrorCode.INVALID_INPUT)
    content = b""
    failed = False
    try:
        findings = _adverse_facts(artifact)
        rules_by_id = {_sarif_rule_id(item.fact): _rule(item.fact) for item in findings}
        results = [_result(item, artifact) for item in findings]
        if artifact.coverage_issues:
            rules_by_id["comparo/artifact-coverage"] = {
                "id": "comparo/artifact-coverage",
                "name": "Incomplete artifact coverage",
                "shortDescription": {"text": "Artifact capture was partial or omitted"},
            }
            results.extend(
                {
                    "ruleId": "comparo/artifact-coverage",
                    "level": "warning",
                    "message": {
                        "text": safe_plain(
                            f"{item.extent.value}: {item.source} ({item.reason.value})"
                        )
                    },
                    "partialFingerprints": {
                        "coverageSource": _coverage_fingerprint(artifact, item, issue_ordinal)
                    },
                }
                for issue_ordinal, item in enumerate(artifact.coverage_issues)
            )
        document: dict[str, object] = {
            "$schema": _SARIF_SCHEMA,
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": safe_plain(artifact.metadata.tool_name),
                            "version": safe_plain(artifact.metadata.tool_version),
                            "informationUri": "https://comparo.dev",
                            "rules": [rules_by_id[key] for key in sorted(rules_by_id)],
                        }
                    },
                    "automationDetails": {"id": _automation_id(artifact)},
                    "invocations": [
                        {
                            "executionSuccessful": (
                                artifact.completion.kind
                                not in {
                                    ArtifactCompletionKind.CANCELLED,
                                    ArtifactCompletionKind.REJECTED,
                                    ArtifactCompletionKind.INTERNAL_ERROR,
                                }
                            ),
                            "properties": {
                                "artifactId": safe_plain(artifact.metadata.artifact_id),
                                "artifactApiVersion": artifact.api_version,
                                "completion": artifact.completion.kind.value,
                                "gateVerdict": artifact.gate.verdict.value,
                                "runId": safe_plain(artifact.metadata.run_id),
                            },
                        }
                    ],
                    "results": results,
                }
            ],
        }
        content = msgspec.json.encode(document, order="deterministic")
    except ReportError:
        raise
    except Exception:  # noqa: BLE001 - reporter boundary exposes only a closed safe code
        failed = True
    if failed:
        raise ReportError(ReportErrorCode.RENDER_FAILED)
    return complete_report(
        format=ReportFormat.SARIF,
        media_type="application/sarif+json",
        extension=".sarif",
        content=content,
        maximum_bytes=maximum_bytes,
    )


def _adverse_facts(artifact: RunArtifact) -> tuple[_Finding, ...]:
    findings = (
        *(
            _Finding(
                fact=fact,
                owner="execution",
                owner_ordinal=execution.ordinal,
                fact_ordinal=fact_ordinal,
            )
            for execution in artifact.executions
            for fact_ordinal, fact in enumerate(execution.assertions)
        ),
        *(
            _Finding(
                fact=fact,
                owner="comparison",
                owner_ordinal=comparison.ordinal,
                fact_ordinal=fact_ordinal,
            )
            for comparison in artifact.comparisons
            for fact_ordinal, fact in enumerate(comparison.diffs)
        ),
    )
    return tuple(item for item in findings if item.fact.outcome not in _NON_ADVERSE)


def _sarif_rule_id(fact: ArtifactFact) -> str:
    definition = fact.rule
    identity = "\0".join(
        (
            definition.namespace,
            definition.kind,
            definition.resource_id,
            definition.fingerprint,
            fact.effective_rule_id,
        )
    )
    suffix = sha256(identity.encode("utf-8")).hexdigest()
    return safe_plain(f"comparo/{fact.engine.value}/{fact.effective_rule_id}/{suffix}")


def _rule(fact: ArtifactFact) -> dict[str, object]:
    return {
        "id": _sarif_rule_id(fact),
        "name": safe_plain(fact.rule_id),
        "shortDescription": {
            "text": safe_plain(f"Comparo {fact.engine.value} rule {fact.rule_id}")
        },
        "properties": {
            "engine": fact.engine.value,
            "severity": fact.severity.value,
            "ruleFingerprint": fact.rule.fingerprint,
        },
    }


def _result(finding: _Finding, artifact: RunArtifact) -> dict[str, object]:
    fact = finding.fact
    value: dict[str, object] = {
        "ruleId": _sarif_rule_id(fact),
        "level": _sarif_level(fact.severity),
        "message": {"text": safe_plain(f"{fact.engine.value} outcome: {fact.outcome.value}")},
        "partialFingerprints": {
            "comparoFinding": _finding_fingerprint(artifact, finding),
        },
        "properties": {
            "applicationId": safe_plain(fact.application_id),
            "factId": safe_plain(fact.fact_id),
            "outcome": fact.outcome.value,
            "evidenceOmitted": fact.evidence_omitted,
        },
    }
    if fact.evidence:
        value["locations"] = [
            {
                "logicalLocations": [
                    {
                        "name": safe_plain(item.location),
                        "fullyQualifiedName": safe_plain(item.location),
                    }
                ]
            }
            for item in fact.evidence
        ]
    return value


def _automation_id(artifact: RunArtifact) -> str:
    project = artifact.provenance.project
    policy = artifact.provenance.execution_policy
    return safe_plain(
        f"{project.namespace}/{project.resource_id}/{policy.namespace}/{policy.resource_id}"
    )


def _finding_fingerprint(artifact: RunArtifact, finding: _Finding) -> str:
    fact = finding.fact
    owner_key, owner_facts = _finding_owner(artifact, finding)
    signature = (fact.engine.value, fact.rule.fingerprint, fact.effective_rule_id)
    occurrence = sum(
        (item.engine.value, item.rule.fingerprint, item.effective_rule_id) == signature
        for item in owner_facts[: finding.fact_ordinal]
    )
    payload = "\0".join(
        (
            owner_key,
            *signature,
            str(occurrence),
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _finding_owner(
    artifact: RunArtifact,
    finding: _Finding,
) -> tuple[str, tuple[ArtifactFact, ...]]:
    if finding.owner == "execution":
        execution = artifact.executions[finding.owner_ordinal]
        return _execution_correlation_key(artifact, execution.ordinal), execution.assertions
    comparison = artifact.comparisons[finding.owner_ordinal]
    return _comparison_correlation_key(artifact, comparison.ordinal), comparison.diffs


def _coverage_fingerprint(
    artifact: RunArtifact,
    issue: ArtifactCoverageIssue,
    issue_ordinal: int,
) -> str:
    ordinals = {item.execution_id: item.ordinal for item in artifact.executions}
    owner = "run"
    if issue.execution_id is not None:
        owner = _execution_correlation_key(artifact, ordinals[issue.execution_id])
    source = _UUID.sub("<occurrence>", issue.source)
    signature = (owner, source, issue.extent.value, issue.reason.value)
    occurrence = sum(
        _coverage_signature(artifact, previous) == signature
        for previous in artifact.coverage_issues[:issue_ordinal]
    )
    payload = "\0".join(
        (
            *signature,
            str(occurrence),
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _coverage_signature(
    artifact: RunArtifact,
    issue: ArtifactCoverageIssue,
) -> tuple[str, str, str, str]:
    if issue.execution_id is None:
        owner = "run"
    else:
        ordinals = {item.execution_id: item.ordinal for item in artifact.executions}
        owner = _execution_correlation_key(artifact, ordinals[issue.execution_id])
    return (
        owner,
        _UUID.sub("<occurrence>", issue.source),
        issue.extent.value,
        issue.reason.value,
    )


def _execution_correlation_key(artifact: RunArtifact, ordinal: int) -> str:
    execution = artifact.executions[ordinal]
    base = "\0".join(
        (
            "execution",
            _definition_key(execution.subject),
            _definition_key(execution.environment),
            _coordinate_key(execution.coordinates),
        )
    )
    occurrence = sum(
        "\0".join(
            (
                "execution",
                _definition_key(item.subject),
                _definition_key(item.environment),
                _coordinate_key(item.coordinates),
            )
        )
        == base
        for item in artifact.executions[:ordinal]
    )
    return f"{base}\0{occurrence}"


def _comparison_correlation_key(artifact: RunArtifact, ordinal: int) -> str:
    comparison = artifact.comparisons[ordinal]
    executions = {item.execution_id: item for item in artifact.executions}
    baseline = executions[comparison.baseline_execution_id]
    candidate = executions[comparison.candidate_execution_id]
    base = "\0".join(
        (
            "comparison",
            _definition_key(comparison.subject),
            _definition_key(baseline.environment),
            _definition_key(candidate.environment),
            _coordinate_key(baseline.coordinates),
        )
    )
    occurrence = 0
    for previous in artifact.comparisons[:ordinal]:
        previous_baseline = executions[previous.baseline_execution_id]
        previous_candidate = executions[previous.candidate_execution_id]
        previous_key = "\0".join(
            (
                "comparison",
                _definition_key(previous.subject),
                _definition_key(previous_baseline.environment),
                _definition_key(previous_candidate.environment),
                _coordinate_key(previous_baseline.coordinates),
            )
        )
        occurrence += previous_key == base
    return f"{base}\0{occurrence}"


def _definition_key(definition: ArtifactDefinition) -> str:
    return "\0".join((definition.namespace, definition.kind, definition.resource_id))


def _coordinate_key(coordinates: tuple[ArtifactCoordinate, ...]) -> str:
    encoded = msgspec.json.encode(coordinates, order="deterministic")
    return sha256(encoded).hexdigest()


def _sarif_level(severity: ArtifactSeverity) -> str:
    return {
        ArtifactSeverity.BLOCKING: "error",
        ArtifactSeverity.WARNING: "warning",
        ArtifactSeverity.INFO: "note",
    }[severity]
