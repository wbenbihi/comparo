"""SARIF 2.1.0 projection of adverse facts and incomplete artifact coverage."""

from __future__ import annotations

from typing import Final

import msgspec

from comparo.core.artifacts.common import ArtifactFactOutcome
from comparo.core.artifacts.common import ArtifactGateVerdict
from comparo.core.artifacts.common import ArtifactSeverity
from comparo.core.artifacts.facts import ArtifactFact
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


def render_sarif_report(artifact: RunArtifact, *, maximum_bytes: int) -> RenderedReport:
    """Render adverse artifact facts without inventing gate or source-code semantics."""
    if type(artifact) is not RunArtifact:
        raise ReportError(ReportErrorCode.INVALID_INPUT)
    try:
        facts = tuple(_adverse_facts(artifact))
        rules_by_id = {_sarif_rule_id(item): _rule(item) for item in facts}
        results = [_result(item) for item in facts]
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
                        "coverageSource": safe_plain(f"{item.execution_id or 'run'}:{item.source}")
                    },
                }
                for item in artifact.coverage_issues
            )
        document: dict[str, object] = {
            "$schema": _SARIF_SCHEMA,
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": artifact.metadata.tool_name,
                            "semanticVersion": artifact.metadata.tool_version,
                            "informationUri": "https://comparo.dev",
                            "rules": [rules_by_id[key] for key in sorted(rules_by_id)],
                        }
                    },
                    "automationDetails": {"id": safe_plain(artifact.metadata.artifact_id)},
                    "invocations": [
                        {
                            "executionSuccessful": (
                                artifact.gate.verdict is not ArtifactGateVerdict.FAILED
                            ),
                            "properties": {
                                "artifactApiVersion": artifact.api_version,
                                "completion": artifact.completion.kind.value,
                                "gateVerdict": artifact.gate.verdict.value,
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
    except Exception as error:
        raise ReportError(ReportErrorCode.RENDER_FAILED) from error
    return complete_report(
        format=ReportFormat.SARIF,
        media_type="application/sarif+json",
        extension=".sarif",
        content=content,
        maximum_bytes=maximum_bytes,
    )


def _adverse_facts(artifact: RunArtifact) -> tuple[ArtifactFact, ...]:
    assertions = (fact for execution in artifact.executions for fact in execution.assertions)
    diffs = (fact for comparison in artifact.comparisons for fact in comparison.diffs)
    return tuple(fact for fact in (*assertions, *diffs) if fact.outcome not in _NON_ADVERSE)


def _sarif_rule_id(fact: ArtifactFact) -> str:
    return safe_plain(f"comparo/{fact.engine.value}/{fact.effective_rule_id}")


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


def _result(fact: ArtifactFact) -> dict[str, object]:
    value: dict[str, object] = {
        "ruleId": _sarif_rule_id(fact),
        "level": _sarif_level(fact.severity),
        "message": {"text": safe_plain(f"{fact.engine.value} outcome: {fact.outcome.value}")},
        "partialFingerprints": {
            "factId": safe_plain(fact.fact_id),
            "applicationId": safe_plain(fact.application_id),
        },
        "properties": {
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


def _sarif_level(severity: ArtifactSeverity) -> str:
    return {
        ArtifactSeverity.BLOCKING: "error",
        ArtifactSeverity.WARNING: "warning",
        ArtifactSeverity.INFO: "note",
    }[severity]
