"""Frontend-safe pre-run rule catalog tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.application import rule_inspection as inspection_module
from comparo.application.evaluation_binding import compile_configuration_rule_catalog
from comparo.application.inspection import ResourceCoordinate
from comparo.application.preflight import PreparedProjectPlan
from comparo.application.rule_inspection import DiffSideInspection
from comparo.application.rule_inspection import PlanRuleInspection
from comparo.application.rule_inspection import RuleOperationInspection
from comparo.application.rule_inspection import RuleOriginInspection
from comparo.application.rule_inspection import RuleSelectorInspection
from comparo.application.rule_inspection import RuleSelectorPathInspection
from comparo.application.rule_inspection import inspect_plan_rule_window
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.primitives import Count
from comparo.core.domain.selectors.http_fields import HttpFieldPath
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.occurrences import AllAttemptsOccurrence
from comparo.core.domain.selectors.occurrences import AllExchangesOccurrence
from comparo.core.domain.selectors.occurrences import AttemptOccurrence
from comparo.core.domain.selectors.occurrences import ExchangeOccurrence
from comparo.core.domain.selectors.paths import JsonPathExpression
from comparo.core.domain.selectors.paths import StaticFieldPath
from comparo.core.policy import trusted_local_policy
from comparo.core.selection import PathCompileReady
from comparo.core.selection import try_compile_jsonpath
from tests.application.test_evaluation_binding import _prepared


def _project(tmp_path: Path, *, inline: bool = False) -> PreparedProjectPlan:
    prepared = _prepared(
        assertion=(
            "[{id: status-ok, name: Status, tags: [http], selector: status, "
            "operator: {name: equals, expected: 200}}]"
        ),
        diff=(
            "[{id: body-diff, baseline: {selector: {source: response.body, view: json, "
            "path: /legacy}, transforms: [{name: normalizeText, trim: both}]}, "
            "candidate: {selector: {source: response.body, view: json, path: /current}}, "
            "pairing: singular, comparator: exact}]"
        ),
        inline=inline,
    )
    policy = trusted_local_policy()
    prepared = msgspec.structs.replace(
        prepared,
        authority=msgspec.structs.replace(prepared.authority, policy=policy.definition),
    )
    return PreparedProjectPlan(
        prepared=prepared,
        policy=policy,
        project_root=tmp_path.resolve(),
    )


def test_rule_catalog_pages_exact_compiled_assertion_and_diff_pipelines(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)

    first = inspect_plan_rule_window(project, start_ordinal=0, maximum_rules=1)
    second = inspect_plan_rule_window(project, start_ordinal=1, maximum_rules=5)

    assert first.plan_fingerprint == project.prepared.plan.fingerprint.digest.hex_value
    assert first.total_rules == 2
    assert first.next_ordinal == 1
    assertion = first.rules[0]
    assert assertion.family == "assertion"
    assert assertion.rule_id == "status-ok"
    assert assertion.name == "Status"
    assert assertion.tags == ("http",)
    assert assertion.selector is not None
    assert assertion.selector.source == "response.status"
    assert assertion.selector.path.dialect == "none"
    assert assertion.selector.occurrence == "terminal"
    assert assertion.operator == RuleOperationInspection(
        namespace="comparo.core",
        family="assertion-operator",
        name="equality",
        version="1",
    )
    assert assertion.quantifier == "one"
    assert assertion.origin.resource is not None
    assert assertion.origin.resource.kind == "AssertionProfile"

    assert second.next_ordinal is None
    diff = second.rules[0]
    assert diff.family == "diff"
    assert diff.rule_id == "body-diff"
    assert diff.baseline is not None
    assert diff.baseline.selector.path == RuleSelectorPathInspection(
        dialect="rfc6901",
        expression="/legacy",
    )
    assert tuple(item.name for item in diff.baseline.transforms) == ("normalize-text",)
    assert diff.candidate is not None
    assert diff.candidate.selector.path.expression == "/current"
    assert diff.pairing is not None
    assert diff.pairing.name == "singular"
    assert diff.comparator is not None
    assert diff.comparator.name == "exact"


def test_rule_catalog_preserves_inline_rule_origins(tmp_path: Path) -> None:
    rule = inspect_plan_rule_window(
        _project(tmp_path, inline=True),
        start_ordinal=0,
        maximum_rules=1,
    ).rules[0]

    assert rule.origin.resource is None
    assert rule.origin.inline_owner == ("local", "Project", "demo")
    assert rule.origin.fragment is not None


def test_compare_rule_window_contracts_reject_invalid_shapes(tmp_path: Path) -> None:
    project = _project(tmp_path)
    valid = inspect_plan_rule_window(project, start_ordinal=0, maximum_rules=1)
    rule = valid.rules[0]

    for cursor in (-1, cast("int", True)):
        with pytest.raises(ValueError, match="cursor"):
            inspect_plan_rule_window(project, start_ordinal=cursor, maximum_rules=1)
    for maximum in (0, cast("int", True)):
        with pytest.raises(ValueError, match="page size"):
            inspect_plan_rule_window(project, start_ordinal=0, maximum_rules=maximum)
    with pytest.raises(TypeError, match="PreparedProjectPlan"):
        inspect_plan_rule_window(
            cast("PreparedProjectPlan", object()),
            start_ordinal=0,
            maximum_rules=1,
        )
    with pytest.raises(ValueError, match="cursor"):
        msgspec.structs.replace(valid, start_ordinal=-1)
    with pytest.raises(ValueError, match="total"):
        msgspec.structs.replace(valid, total_rules=-1)
    with pytest.raises(TypeError, match="immutable"):
        msgspec.structs.replace(
            valid,
            rules=cast("tuple[PlanRuleInspection, ...]", (object(),)),
        )
    with pytest.raises(ValueError, match="contiguous"):
        msgspec.structs.replace(valid, rules=(msgspec.structs.replace(rule, ordinal=2),))
    with pytest.raises(ValueError, match="continuation"):
        msgspec.structs.replace(valid, next_ordinal=None)


def test_rule_window_rejects_a_prepared_plan_with_invalid_evaluation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    rejected = compile_configuration_rule_catalog(_prepared(assertion="[]"))
    monkeypatch.setattr(
        inspection_module,
        "compile_configuration_rule_catalog",
        lambda *_args, **_kwargs: rejected,
    )

    with pytest.raises(ValueError, match="no longer admits"):
        inspect_plan_rule_window(project, start_ordinal=0, maximum_rules=1)


def test_rule_projection_component_contracts_are_closed() -> None:
    path = RuleSelectorPathInspection(dialect="none", expression="")
    selector = RuleSelectorInspection(
        source="response.status",
        path=path,
        body_view=None,
        occurrence="terminal",
        cardinality="exactly-one",
        value_kinds=("integer",),
        access="incremental",
        may_duplicate=False,
        uses_descendant=False,
        uses_filter=False,
        function_dependencies=(),
    )
    operation = RuleOperationInspection(
        namespace="comparo.core",
        family="assertion-operator",
        name="equality",
        version="1",
    )
    origin = RuleOriginInspection(
        inline_owner=("default", "Request", "check"),
        fragment="#assertions/0",
    )
    assertion = PlanRuleInspection(
        ordinal=0,
        subject=_subject_coordinate(),
        family="assertion",
        origin=origin,
        rule_id="status",
        effective_rule_id="status",
        fingerprint="f" * 64,
        name=None,
        description=None,
        tags=(),
        severity="blocking",
        completeness="complete",
        selector=selector,
        operator=operation,
        quantifier="one",
    )
    assert assertion.family == "assertion"

    with pytest.raises(ValueError, match="exactly named or inline"):
        RuleOriginInspection()
    with pytest.raises(ValueError, match="exactly named or inline"):
        msgspec.structs.replace(origin, resource=_subject_coordinate())
    with pytest.raises(TypeError, match="named rule origin"):
        RuleOriginInspection(resource=cast("ResourceCoordinate", object()))
    with pytest.raises(ValueError, match="inline rule owner"):
        msgspec.structs.replace(origin, inline_owner=("default", "Request", ""))
    with pytest.raises(ValueError, match="inline rule fragment"):
        msgspec.structs.replace(origin, fragment="")
    with pytest.raises(ValueError, match="operation identity"):
        msgspec.structs.replace(operation, name="")
    with pytest.raises(ValueError, match="dialect"):
        msgspec.structs.replace(path, dialect="")
    with pytest.raises(TypeError, match="expression"):
        msgspec.structs.replace(path, expression=cast("str", object()))
    with pytest.raises(ValueError, match="occurrence"):
        msgspec.structs.replace(path, field_occurrence=-1)
    with pytest.raises(ValueError, match="projection"):
        msgspec.structs.replace(path, field_projection="")
    with pytest.raises(TypeError, match="selector path"):
        msgspec.structs.replace(selector, path=cast("RuleSelectorPathInspection", object()))
    with pytest.raises(ValueError, match="labels"):
        msgspec.structs.replace(selector, source="")
    with pytest.raises(ValueError, match="body view"):
        msgspec.structs.replace(selector, body_view="")
    with pytest.raises(TypeError, match="collections"):
        msgspec.structs.replace(selector, value_kinds=cast("tuple[str, ...]", (1,)))
    with pytest.raises(TypeError, match="markers"):
        msgspec.structs.replace(selector, may_duplicate=cast("bool", 1))

    side = DiffSideInspection(selector=selector, transforms=(operation,), reducer=None)
    with pytest.raises(TypeError, match="side selector"):
        msgspec.structs.replace(side, selector=cast("RuleSelectorInspection", object()))
    with pytest.raises(TypeError, match="transforms"):
        msgspec.structs.replace(
            side,
            transforms=cast("tuple[RuleOperationInspection, ...]", (object(),)),
        )
    with pytest.raises(TypeError, match="reducer"):
        msgspec.structs.replace(side, reducer=cast("RuleOperationInspection", object()))

    with pytest.raises(ValueError, match="ordinal"):
        msgspec.structs.replace(assertion, ordinal=-1)
    with pytest.raises(TypeError, match="subject"):
        msgspec.structs.replace(assertion, subject=cast("ResourceCoordinate", object()))
    with pytest.raises(TypeError, match="origin"):
        msgspec.structs.replace(assertion, origin=cast("RuleOriginInspection", object()))
    with pytest.raises(ValueError, match="identity"):
        msgspec.structs.replace(assertion, rule_id="")
    with pytest.raises(TypeError, match="optional"):
        msgspec.structs.replace(assertion, name=cast("str", object()))
    with pytest.raises(TypeError, match="tags"):
        msgspec.structs.replace(assertion, tags=cast("tuple[str, ...]", (1,)))
    with pytest.raises(ValueError, match="family"):
        msgspec.structs.replace(assertion, family="other")
    with pytest.raises(ValueError, match="contradictory"):
        msgspec.structs.replace(assertion, selector=None)


def test_diff_rule_projection_rejects_assertion_fields_and_quantifier(tmp_path: Path) -> None:
    rule = inspect_plan_rule_window(
        _project(tmp_path),
        start_ordinal=1,
        maximum_rules=1,
    ).rules[0]

    with pytest.raises(ValueError, match="diff rule inspection"):
        msgspec.structs.replace(rule, comparator=None)
    with pytest.raises(ValueError, match="assertion quantifier"):
        msgspec.structs.replace(rule, quantifier="one")


def test_selector_projection_supports_every_path_and_occurrence_variant(
    tmp_path: Path,
) -> None:
    selector = (
        inspect_plan_rule_window(
            _project(tmp_path),
            start_ordinal=0,
            maximum_rules=1,
        )
        .rules[0]
        .selector
    )
    assert selector is not None

    compiled_jsonpath = try_compile_jsonpath("$.items[*]")
    assert type(compiled_jsonpath) is PathCompileReady
    paths = (
        (
            StaticFieldPath(segments=("response", "status")),
            RuleSelectorPathInspection(
                dialect="comparo.static-fields",
                expression="response.status",
            ),
        ),
        (
            cast("JsonPathExpression", compiled_jsonpath.path),
            RuleSelectorPathInspection(dialect="rfc9535", expression='$["items"][*]'),
        ),
        (
            HttpFieldPath(name="X-Request-Id", occurrence=Count(value=1)),
            RuleSelectorPathInspection(
                dialect="comparo.http-field",
                expression="X-Request-Id",
                field_occurrence=1,
                field_projection="raw-value",
            ),
        ),
    )
    for path, expected in paths:
        projected = inspection_module._path(cast("CompiledSelector", SimpleNamespace(path=path)))
        assert projected == expected

    occurrences = (
        (AllAttemptsOccurrence(), "all-attempts"),
        (AllExchangesOccurrence(), "all-exchanges"),
        (
            AttemptOccurrence(
                attempt_id=AttemptId(value=UUID("10000000-0000-0000-0000-000000000001"))
            ),
            "attempt:10000000-0000-0000-0000-000000000001",
        ),
        (
            ExchangeOccurrence(
                exchange_id=ExchangeId(value=UUID("20000000-0000-0000-0000-000000000002"))
            ),
            "exchange:20000000-0000-0000-0000-000000000002",
        ),
    )
    for occurrence, expected_occurrence in occurrences:
        occurrence_projection = inspection_module._occurrence(
            cast("CompiledSelector", SimpleNamespace(occurrence=occurrence))
        )
        assert occurrence_projection == expected_occurrence


def _subject_coordinate() -> ResourceCoordinate:
    """Return a real coordinate while keeping this fixture local to contract tests."""
    project = _prepared()
    definition = project.effective.subjects[0].definition
    identity = definition.identity
    return ResourceCoordinate(
        namespace=identity.namespace.value,
        kind=identity.kind.value,
        resource_id=identity.resource_id.value,
        fingerprint=definition.fingerprint.digest.hex_value,
    )
