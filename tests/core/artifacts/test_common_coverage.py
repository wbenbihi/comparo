"""Remaining branch coverage for shared artifact contracts and provenance."""

from __future__ import annotations

import asyncio
from typing import cast

import msgspec
import pytest

from comparo.core.artifacts import common
from comparo.core.artifacts import compiler
from comparo.core.artifacts.capture import ArtifactCaptureUnavailableError
from comparo.core.artifacts.common import ArtifactCiProvenance
from comparo.core.artifacts.common import ArtifactRunAnnotations
from comparo.core.artifacts.common import ArtifactVcsProvenance
from comparo.core.domain.identity import ResourceId
from comparo.core.planning.planner import plan_run
from tests.core.artifacts.test_artifact_contracts import _artifact
from tests.core.artifacts.test_capture_compiler import _policy
from tests.core.artifacts.test_compiler_coverage import _state
from tests.core.planning.conftest import authority
from tests.core.planning.conftest import compile_project
from tests.core.planning.conftest import document
from tests.core.planning.conftest import invocation


def test_stable_token_defends_its_internal_exact_string_invariant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the defensive assertion executable if the prerequisite ever regresses."""
    monkeypatch.setattr(common, "require_text", lambda *_args, **_kwargs: None)

    with pytest.raises(AssertionError, match="exact string"):
        common.require_stable_token(object(), label="token")


def test_vcs_provenance_accepts_bounded_required_and_optional_coordinates() -> None:
    minimal = ArtifactVcsProvenance(
        provider="git",
        repository="https://example.test/project.git",
        revision="deadbeef",
    )
    complete = ArtifactVcsProvenance(
        provider="git",
        repository="https://example.test/project.git",
        revision="deadbeef",
        branch="main",
    )

    assert minimal.branch is None
    assert complete.branch == "main"


@pytest.mark.parametrize(
    ("changes", "label"),
    [
        ({"provider": ""}, "VCS provider"),
        ({"repository": ""}, "VCS repository"),
        ({"revision": ""}, "VCS revision"),
        ({"branch": ""}, "VCS branch"),
    ],
)
def test_vcs_provenance_rejects_empty_coordinates(
    changes: dict[str, object],
    label: str,
) -> None:
    values: dict[str, object] = {
        "provider": "git",
        "repository": "https://example.test/project.git",
        "revision": "deadbeef",
    }
    values.update(changes)

    with pytest.raises(ValueError, match=label):
        ArtifactVcsProvenance(**values)  # type: ignore[arg-type]


def test_ci_provenance_accepts_minimal_and_complete_safe_coordinates() -> None:
    minimal = ArtifactCiProvenance(provider="github", run_id="123")
    complete = ArtifactCiProvenance(
        provider="github",
        run_id="123",
        job="test",
        url="https://ci.example.test/runs/123",
    )

    assert minimal.job is None
    assert minimal.url is None
    assert complete.job == "test"
    assert complete.url == "https://ci.example.test/runs/123"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://ci.example.test/runs/123",
        "https:///runs/123",
        "https://user@ci.example.test/runs/123",
        "https://ci.example.test/runs/123?token=secret",
        "https://ci.example.test/runs/123#fragment",
    ],
)
def test_ci_provenance_rejects_unsafe_or_non_http_urls(url: str) -> None:
    with pytest.raises(ValueError, match="credential- and query-free"):
        ArtifactCiProvenance(provider="github", run_id="123", url=url)


def test_ci_provenance_checks_password_independently_of_username(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Parsed:
        scheme = "https"
        hostname = "ci.example.test"
        username = None
        password = "secret"
        query = ""
        fragment = ""

    monkeypatch.setattr(common, "urlsplit", lambda _url: Parsed())

    with pytest.raises(ValueError, match="credential- and query-free"):
        ArtifactCiProvenance(
            provider="github",
            run_id="123",
            url="https://ci.example.test/runs/123",
        )


@pytest.mark.parametrize(
    ("changes", "label"),
    [
        ({"provider": ""}, "CI provider"),
        ({"run_id": ""}, "CI run ID"),
        ({"job": ""}, "CI job"),
        ({"url": ""}, "CI URL"),
    ],
)
def test_ci_provenance_rejects_empty_coordinates(
    changes: dict[str, object],
    label: str,
) -> None:
    values: dict[str, object] = {"provider": "github", "run_id": "123"}
    values.update(changes)

    with pytest.raises(ValueError, match=label):
        ArtifactCiProvenance(**values)  # type: ignore[arg-type]


def test_run_annotations_accept_complete_sanitized_context() -> None:
    vcs = ArtifactVcsProvenance(
        provider="git",
        repository="https://example.test/project.git",
        revision="deadbeef",
    )
    ci = ArtifactCiProvenance(provider="github", run_id="123")

    annotations = ArtifactRunAnnotations(
        title="Release comparison",
        notes="Expected canary verification.",
        tags=("release", "canary"),
        vcs=vcs,
        ci=ci,
        redaction_count=2,
    )

    assert annotations.vcs is vcs
    assert annotations.ci is ci
    assert annotations.redaction_count == 2


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"title": ""}, ValueError, "run title"),
        ({"notes": ""}, ValueError, "run notes"),
        ({"tags": cast("tuple[str, ...]", ["tag"])}, ValueError, "immutable bounded"),
        ({"tags": (cast("str", 1),)}, ValueError, "immutable bounded"),
        ({"tags": ("",)}, ValueError, "immutable bounded"),
        ({"tags": ("x" * 128,)}, ValueError, "immutable bounded"),
        ({"tags": tuple(f"tag-{index}" for index in range(129))}, ValueError, "bounded"),
        ({"tags": ("same", "same")}, ValueError, "unique"),
        ({"vcs": cast("ArtifactVcsProvenance", object())}, TypeError, "VCS provenance"),
        ({"ci": cast("ArtifactCiProvenance", object())}, TypeError, "CI provenance"),
        ({"redaction_count": -1}, ValueError, "redaction count"),
    ],
)
def test_run_annotations_reject_every_malformed_context(
    changes: dict[str, object],
    error: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error, match=message):
        ArtifactRunAnnotations(**changes)  # type: ignore[arg-type]


def test_run_artifact_rejects_untyped_annotations() -> None:
    with pytest.raises(TypeError, match="annotations must be typed"):
        msgspec.structs.replace(
            _artifact(),
            annotations=cast("ArtifactRunAnnotations", object()),
        )


def test_compiler_releases_capture_when_annotations_are_untyped() -> None:
    prepared, result, capture = asyncio.run(_state())

    with pytest.raises(TypeError, match="annotations must be typed"):
        compiler.compile_run_artifact(
            prepared,
            result,
            capture,
            policy=_policy(),
            artifact_id="artifact-test",
            created_unix_nanoseconds=1,
            tool_version="0.2.0",
            annotations=cast("ArtifactRunAnnotations", object()),
        )

    with pytest.raises(ArtifactCaptureUnavailableError, match="boundary is unavailable"):
        _ = capture.boundary


def test_execution_profile_provenance_projects_the_selected_resource() -> None:
    project = compile_project(
        document(
            "Project",
            "demo",
            "{defaults: {subjects: [{$use: Request/chat}], environments: [{$use: staging}]}}",
        ),
        document("Environment", "staging", "{baseUrl: 'https://staging.example'}"),
        document("Request", "chat", "{request: {method: GET, endpoint: /chat}}"),
        document(
            "ExecutionProfile",
            "release",
            "{subjects: [{$use: Request/chat}], environments: [{$use: staging}]}",
        ),
    )
    planned = plan_run(
        project,
        invocation(execution_profile=ResourceId(value="release")),
        authority(),
    )
    assert planned.prepared is not None

    profile = compiler._execution_profile(planned.prepared)

    assert profile is not None
    assert profile.kind == "ExecutionProfile"
    assert profile.resource_id == "release"
