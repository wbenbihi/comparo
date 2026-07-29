"""Structural effective-request composition inspection tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.application import request_composition
from comparo.application.plan_inspection_identity import ResourceCoordinate
from comparo.application.plan_occurrences import PlanOccurrenceInspection
from comparo.application.planning import PlanningOptions
from comparo.application.planning import RequestOverride
from comparo.application.preflight import PreparedProjectPlan
from comparo.application.preflight import ProjectPlanRequest
from comparo.application.preflight import prepare_project_plan
from comparo.application.request_composition import RequestCompositionInspection
from comparo.application.request_composition import RequestCompositionStrategy
from comparo.application.request_composition import RequestContributionKind
from comparo.application.request_composition import RequestFieldComposition
from comparo.application.request_composition import RequestFieldContribution
from comparo.application.request_composition import inspect_request_composition
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.values import JsonString
from comparo.core.runtime import execution_work_at
from comparo.core.runtime.models import ExecutionWork

_INVOCATION_ID = UUID("10000000-0000-0000-0000-000000000001")
_RUN_ID = UUID("20000000-0000-0000-0000-000000000002")
_FINGERPRINT = "a" * 64


def _literal(value: str) -> LiteralExpr:
    return LiteralExpr(
        value=JsonString(value=value),
        provenance=ValueProvenance(entries=(ProvenanceEntry(kind=ProvenanceKind.INVOCATION),)),
    )


def _write_project(root: Path) -> Path:
    config = root / "comparo.yaml"
    config.write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Project\n"
        "metadata: {id: demo, name: Demo}\n"
        "spec:\n"
        "  data: .comparo\n"
        "  defaults:\n"
        "    subjects: [{$use: Request/ping}]\n"
        "    environments: [{$use: staging}]\n",
        encoding="utf-8",
    )
    data = root / ".comparo"
    data.mkdir()
    (data / "resources.yaml").write_text(
        "apiVersion: comparo/v1alpha1\n"
        "kind: Environment\n"
        "metadata: {id: staging, name: Staging}\n"
        "spec:\n"
        "  baseUrl: https://staging.example.test\n"
        "  headers: {x-env: env}\n"
        "  cookies: {session: env}\n"
        "  auth: {bearer: env}\n"
        "  timeout: 5s\n"
        "  tls: {verifyCertificates: true}\n"
        "  proxy: null\n"
        "---\n"
        "apiVersion: comparo/v1alpha1\n"
        "kind: Request\n"
        "metadata: {id: ping, name: Ping}\n"
        "spec:\n"
        "  request:\n"
        "    method: GET\n"
        "    endpoint: /ping\n"
        "    query: {verbose: 'true'}\n"
        "    headers: {x-request: request}\n"
        "    cookies: {request: cookie}\n"
        "    body: {hello: world}\n"
        "    bodyType: json\n"
        "    auth: {bearer: request}\n"
        "    stream: auto\n"
        "    retries: {maxAttempts: 1, conditions: []}\n"
        "    redirects: {maxHops: 0}\n",
        encoding="utf-8",
    )
    return config


def _project(
    root: Path,
    *,
    edited: bool = True,
    direct_url: bool = False,
) -> PreparedProjectPlan:
    config = _write_project(root)
    if direct_url:
        source = root / ".comparo" / "resources.yaml"
        source.write_text(
            source.read_text(encoding="utf-8").replace(
                "    endpoint: /ping\n",
                "    url: https://direct.example.test/ping\n",
            ),
            encoding="utf-8",
        )
    options = PlanningOptions(
        requests=(
            RequestOverride(
                subject="ping",
                endpoint=None if direct_url else _literal("/edited"),
                url=_literal("https://override.example.test/ping") if direct_url else None,
                timeout=_literal("8s"),
            ),
        )
        if edited
        else ()
    )
    outcome = prepare_project_plan(
        ProjectPlanRequest(
            config=config,
            additional_sources=(),
            policy=None,
            options=options,
            invocation_id=_INVOCATION_ID,
            run_id=_RUN_ID,
        )
    )
    assert outcome.project is not None, outcome.diagnostics
    return outcome.project


def _coordinate(kind: str = "Request") -> ResourceCoordinate:
    return ResourceCoordinate(
        namespace="local",
        kind=kind,
        resource_id="ping",
        fingerprint=_FINGERPRINT,
    )


def _occurrence() -> PlanOccurrenceInspection:
    return PlanOccurrenceInspection(
        ordinal=0,
        execution_id="execution",
        subject=_coordinate(),
        environment=msgspec.structs.replace(
            _coordinate("Environment"),
            resource_id="staging",
        ),
        cell_id="cell",
        trial_id="trial",
        trial_ordinal=0,
    )


def _contribution() -> RequestFieldContribution:
    return RequestFieldContribution(
        kind=RequestContributionKind.REQUEST,
        resource=_coordinate(),
        field_path=("request", "method"),
    )


def test_inspects_exact_structural_field_composition(tmp_path: Path) -> None:
    result = inspect_request_composition(_project(tmp_path), execution_ordinal=0)

    assert result is not None
    assert result.occurrence.ordinal == 0
    fields = {item.field: item for item in result.fields}
    assert tuple(item.kind for item in fields["url"].contributions) == (
        RequestContributionKind.ENVIRONMENT,
        RequestContributionKind.REQUEST,
        RequestContributionKind.INVOCATION,
    )
    assert fields["url"].strategy is RequestCompositionStrategy.URL_ROUTING
    assert tuple(item.kind for item in fields["headers"].contributions) == (
        RequestContributionKind.ENVIRONMENT,
        RequestContributionKind.REQUEST,
    )
    assert fields["headers"].strategy is RequestCompositionStrategy.MERGE_ENVIRONMENT_THEN_REQUEST
    assert tuple(item.kind for item in fields["timeout"].contributions) == (
        RequestContributionKind.VERSIONED_DEFAULT,
        RequestContributionKind.ENVIRONMENT,
        RequestContributionKind.INVOCATION,
    )
    assert (
        fields["timeout"].strategy is RequestCompositionStrategy.FALLBACK_REQUEST_THEN_ENVIRONMENT
    )
    assert tuple(item.kind for item in fields["tls"].contributions) == (
        RequestContributionKind.VERSIONED_DEFAULT,
        RequestContributionKind.ENVIRONMENT,
    )
    assert fields["proxy"].strategy is RequestCompositionStrategy.ENVIRONMENT_ONLY
    assert tuple(item.kind for item in fields["retries"].contributions) == (
        RequestContributionKind.VERSIONED_DEFAULT,
        RequestContributionKind.REQUEST,
    )
    assert (
        msgspec.json.decode(
            msgspec.json.encode(result),
            type=RequestCompositionInspection,
        )
        == result
    )


def test_returns_none_for_missing_execution(tmp_path: Path) -> None:
    assert inspect_request_composition(_project(tmp_path), execution_ordinal=1) is None


def test_inspects_direct_url_and_appended_invocation_fields(tmp_path: Path) -> None:
    result = inspect_request_composition(
        _project(tmp_path, direct_url=True),
        execution_ordinal=0,
    )

    assert result is not None
    url = next(item for item in result.fields if item.field == "url")
    assert tuple(item.field_path[-1] for item in url.contributions) == (
        "baseUrl",
        "url",
        "url",
    )


def test_returns_none_for_future_scenario_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    work = execution_work_at(project.prepared, 0)
    assert work is not None
    scenario_identity = msgspec.structs.replace(
        work.subject.identity,
        kind=ResourceKind.SCENARIO,
        resource_id=ResourceId(value="future"),
    )
    scenario = msgspec.structs.replace(work.subject, identity=scenario_identity)
    monkeypatch.setattr(
        request_composition,
        "execution_work_at",
        lambda _prepared, _ordinal: cast(
            "ExecutionWork",
            SimpleNamespace(subject=scenario),
        ),
    )

    assert inspect_request_composition(project, execution_ordinal=0) is None


@pytest.mark.parametrize("ordinal", [-1, True])
def test_rejects_invalid_execution_ordinal(tmp_path: Path, ordinal: int) -> None:
    with pytest.raises(ValueError, match="ordinal"):
        inspect_request_composition(_project(tmp_path), execution_ordinal=ordinal)


def test_rejects_untyped_project() -> None:
    with pytest.raises(TypeError, match="PreparedProjectPlan"):
        inspect_request_composition(
            cast("PreparedProjectPlan", object()),
            execution_ordinal=0,
        )


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"kind": cast("RequestContributionKind", object())}, TypeError, "kind"),
        ({"field_path": ()}, ValueError, "path"),
        ({"field_path": ("",)}, ValueError, "path"),
        ({"resource": None}, ValueError, "coordinate"),
        (
            {
                "kind": RequestContributionKind.INVOCATION,
                "resource": _coordinate(),
            },
            ValueError,
            "coordinate",
        ),
    ],
)
def test_field_contribution_rejects_invalid_state(
    changes: dict[str, object],
    error: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error, match=message):
        msgspec.structs.replace(_contribution(), **changes)


def test_default_and_invocation_contributions_do_not_require_resources() -> None:
    for kind in (
        RequestContributionKind.VERSIONED_DEFAULT,
        RequestContributionKind.INVOCATION,
    ):
        assert RequestFieldContribution(kind=kind, field_path=("source",)).resource is None


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"field": ""}, ValueError, "field"),
        ({"strategy": cast("RequestCompositionStrategy", object())}, TypeError, "strategy"),
        ({"contributions": []}, TypeError, "contributions"),
        ({"contributions": (object(),)}, TypeError, "contributions"),
        ({"contributions": ()}, ValueError, "at least one"),
    ],
)
def test_field_composition_rejects_invalid_state(
    changes: dict[str, object],
    error: type[Exception],
    message: str,
) -> None:
    value = RequestFieldComposition(
        field="method",
        strategy=RequestCompositionStrategy.REPLACE,
        contributions=(_contribution(),),
    )
    with pytest.raises(error, match=message):
        msgspec.structs.replace(value, **changes)


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"plan_fingerprint": "short"}, ValueError, "SHA-256"),
        ({"plan_fingerprint": "z" * 64}, ValueError, "SHA-256"),
        ({"occurrence": object()}, TypeError, "occurrence"),
        ({"fields": []}, TypeError, "fields"),
        ({"fields": (object(),)}, TypeError, "fields"),
    ],
)
def test_composition_inspection_rejects_invalid_state(
    changes: dict[str, object],
    error: type[Exception],
    message: str,
) -> None:
    field = RequestFieldComposition(
        field="method",
        strategy=RequestCompositionStrategy.REPLACE,
        contributions=(_contribution(),),
    )
    value = RequestCompositionInspection(
        plan_fingerprint=_FINGERPRINT,
        occurrence=_occurrence(),
        fields=(field,),
    )
    with pytest.raises(error, match=message):
        msgspec.structs.replace(value, **changes)


def test_composition_inspection_rejects_duplicate_fields() -> None:
    field = RequestFieldComposition(
        field="method",
        strategy=RequestCompositionStrategy.REPLACE,
        contributions=(_contribution(),),
    )
    with pytest.raises(ValueError, match="unique"):
        RequestCompositionInspection(
            plan_fingerprint=_FINGERPRINT,
            occurrence=_occurrence(),
            fields=(field, field),
        )
