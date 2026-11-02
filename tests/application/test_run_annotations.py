"""Tests for sanitized optional run, VCS, and CI artifact annotations."""

from __future__ import annotations

import pytest

from comparo.application.production_execution import ProductionCiProvenance
from comparo.application.production_execution import ProductionRunAnnotations
from comparo.application.production_execution import ProductionVcsProvenance
from comparo.application.production_execution import _artifact_annotations
from comparo.core.artifacts.sanitization import SanitizationBoundary
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from tests.core.artifacts.test_sanitization import _limits


def test_annotations_cross_the_same_sanitization_boundary_as_artifact_values() -> None:
    boundary = SanitizationBoundary(_limits())
    boundary.register(
        SensitiveValue(
            "annotation-secret",
            origins=(TaintOriginId(value="secret/run/annotation"),),
        )
    )
    boundary.seal()
    supplied = ProductionRunAnnotations(
        title="Release annotation-secret",
        notes="Safe notes with annotation-secret",
        tags=("release", "annotation-secret"),
        vcs=ProductionVcsProvenance(
            provider="git",
            repository="org/annotation-secret",
            revision="abc123",
            branch="main",
        ),
        ci=ProductionCiProvenance(
            provider="github",
            run_id="annotation-secret",
            job="compare",
            url="https://ci.example.test/runs/annotation-secret",
        ),
    )

    artifact = _artifact_annotations(supplied, boundary)
    boundary.close()

    assert artifact is not None
    assert "annotation-secret" not in repr(artifact)
    assert artifact.redaction_count >= 6
    assert artifact.ci is not None
    assert artifact.ci.url is None


def test_ci_links_reject_credentials_queries_and_fragments() -> None:
    for url in (
        "https://user:password@ci.example.test/run",
        "https://ci.example.test/run?token=secret",
        "https://ci.example.test/run#secret",
    ):
        with pytest.raises(ValueError, match="must not contain"):
            ProductionCiProvenance(provider="ci", run_id="1", url=url)
