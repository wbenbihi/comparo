"""Tests for safe static human diagnostic guidance."""

from __future__ import annotations

from comparo.application.diagnostic_catalog import present_diagnostic
from comparo.application.inspection import ApplicationDiagnostic


def test_catalog_selects_specific_static_guidance_without_coordinates() -> None:
    diagnostic = ApplicationDiagnostic(
        code="schema.remote.denied",
        severity="error",
        stage="configuration",
        resource=("secret-namespace", "Schema", "secret-schema"),
        path=("secret-field",),
    )

    presentation = present_diagnostic(diagnostic)
    rendered = " ".join(
        (
            presentation.title,
            presentation.summary,
            presentation.action,
            presentation.documentation_key,
        )
    )

    assert presentation.documentation_key == "schemas.remote"
    assert "secret" not in rendered


def test_unknown_diagnostic_never_interpolates_its_code() -> None:
    diagnostic = ApplicationDiagnostic(
        code="attacker-secret-code",
        severity="warning",
        stage="custom-stage",
    )

    presentation = present_diagnostic(diagnostic)
    rendered = " ".join(
        (
            presentation.title,
            presentation.summary,
            presentation.action,
            presentation.documentation_key,
        )
    )

    assert "attacker-secret-code" not in rendered
    assert presentation.documentation_key == "diagnostics.general"
