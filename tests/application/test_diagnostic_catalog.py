"""Tests for safe static human diagnostic guidance."""

from __future__ import annotations

from typing import cast

import msgspec
import pytest

from comparo.application.diagnostic_catalog import DiagnosticPresentation
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


@pytest.mark.parametrize(
    "replacement",
    [cast("str", object()), "", "x" * 513],
)
def test_presentation_rejects_untyped_empty_or_unbounded_catalog_copy(
    replacement: str,
) -> None:
    valid = DiagnosticPresentation(
        title="Title",
        summary="Summary",
        action="Action",
        documentation_key="docs.key",
    )

    for field in ("title", "summary", "action", "documentation_key"):
        with pytest.raises(ValueError, match="bounded non-empty text"):
            msgspec.structs.replace(valid, **{field: replacement})


def test_catalog_requires_a_typed_value_free_diagnostic() -> None:
    with pytest.raises(TypeError, match="requires ApplicationDiagnostic"):
        present_diagnostic(cast("ApplicationDiagnostic", object()))


@pytest.mark.parametrize(
    ("code", "documentation_key"),
    [
        ("schema.invalid", "schemas.validation"),
        ("planning.invalid", "planning.admission"),
        ("resolution.missing", "values.resolution"),
        ("policy.denied", "policy.authority"),
        ("artifact.incomplete", "artifacts.capture"),
        ("execution.failed", "execution.failures"),
        ("render.unavailable", "requests.preview"),
    ],
)
def test_every_catalog_family_selects_static_guidance(
    code: str,
    documentation_key: str,
) -> None:
    diagnostic = ApplicationDiagnostic(code=code, severity="error", stage="test")

    assert present_diagnostic(diagnostic).documentation_key == documentation_key
