"""ExecutionPolicy compiler, schema, authority, and trusted-local contracts."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import msgspec
import pytest

from comparo.configuration.yaml_parser import parse_yaml_source
from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import ParsedDocument
from comparo.core.domain.budget_contracts import BudgetResource
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.diagnostics import DiagnosticSeverity
from comparo.core.domain.http.url import DnsHost
from comparo.core.domain.http.url import HttpScheme
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import SourceId
from comparo.core.policy import PLANNING_RESOURCES
from comparo.core.policy import POLICY_API_VERSION
from comparo.core.policy import AmbientEnvironmentPolicy
from comparo.core.policy import CompiledExecutionPolicy
from comparo.core.policy import PolicyArtifacts
from comparo.core.policy import PolicyCompilationResult
from comparo.core.policy import PolicyPlanning
from comparo.core.policy import PolicyResourceSelector
from comparo.core.policy import compile_execution_policy
from comparo.core.policy import default_planning_ceilings
from comparo.core.policy import generate_policy_schema
from comparo.core.policy import planning_authority
from comparo.core.policy import render_policy_schema
from comparo.core.policy import trusted_local_policy
from comparo.core.policy.models import is_policy_host

_SOURCE = SourceId(value="policy-test")


def _planning(
    *,
    subjects: str = "[]",
    environments: str = "[]",
    allow_any_subjects: str = "false",
    allow_any_environments: str = "false",
    extra: str = "",
) -> str:
    suffix = "" if not extra else f", {extra}"
    return (
        f"{{subjects: {subjects}, environments: {environments}, "
        f"allowAnySubjects: {allow_any_subjects}, "
        f"allowAnyEnvironments: {allow_any_environments}{suffix}}}"
    )


def _text(
    *,
    planning: str = _planning(
        allow_any_subjects="true",
        allow_any_environments="true",
    ),
    changes: str = "",
) -> str:
    return (
        "apiVersion: comparo.policy/v1alpha1\n"
        "kind: ExecutionPolicy\n"
        "metadata: {id: ci, name: CI, description: Policy, tags: [release]}\n"
        "spec:\n"
        f"  planning: {planning}\n"
        "  http:\n"
        "    allowedSchemes: [https, http]\n"
        "    allowedMethods: [POST, GET]\n"
        "    allowedHosts: [api.example.com, 127.0.0.1, '::1']\n"
        "    allowedPorts: [443, 80]\n"
        "    allowedAddressClasses: [public, loopback]\n"
        "    allowAnyHost: false\n"
        "    allowAnyPort: false\n"
        "    requireMutationApproval: true\n"
        "    allowProxyDns: false\n"
        "  values:\n"
        "    ambientEnvironment: {names: [TOKEN, API_KEY], allowAny: false}\n"
        "    allowProjectFiles: true\n"
        "    remoteSchemas:\n"
        "      sources: [https://schemas.example.com/public.json]\n"
        "      allowAny: false\n"
        "  artifacts:\n"
        "    allowSanitizedPersistence: true\n"
        "    roots: [/var/lib/comparo, /tmp/comparo]\n"
        f"{changes}"
    )


def _replace_block(text: str, start: str, end: str | None, replacement: str) -> str:
    start_index = text.index(start)
    end_index = len(text) if end is None else text.index(end, start_index + len(start))
    return f"{text[:start_index]}{replacement}{text[end_index:]}"


def _compile(text: str) -> PolicyCompilationResult:
    parsed = parse_yaml_source(source_id=_SOURCE, text=text)
    return compile_execution_policy(
        parsed.documents,
        source_id=_SOURCE,
        diagnostics=parsed.diagnostics,
    )


def _policy() -> CompiledExecutionPolicy:
    result = _compile(
        _text(
            planning=_planning(
                subjects="[Scenario/check, Request/get]",
                environments="[Environment/prod]",
                extra="ceilings: {requests: 12, concurrency: 2}",
            )
        )
    )
    assert result.policy is not None
    return result.policy


def test_policy_compiles_canonically_with_defaults_and_exact_authority() -> None:
    policy = _policy()
    assert policy.api_version == POLICY_API_VERSION
    assert policy.definition.identity.kind is ResourceKind.EXECUTION_POLICY
    assert policy.metadata.tags == ("release",)
    assert tuple(item.kind for item in policy.planning.subjects) == (
        ResourceKind.REQUEST,
        ResourceKind.SCENARIO,
    )
    ceilings = {item.resource: item.limit.value for item in policy.planning.ceilings}
    assert tuple(ceilings) == PLANNING_RESOURCES
    assert ceilings[BudgetResource.REQUESTS] == 12
    assert ceilings[BudgetResource.CONCURRENCY] == 2
    assert ceilings[BudgetResource.QUEUED_WORK] == 64
    assert policy.http.allowed_schemes == (HttpScheme.HTTP, HttpScheme.HTTPS)
    assert tuple(item.value for item in policy.http.allowed_methods) == ("GET", "POST")
    assert tuple(item.value for item in policy.http.allowed_ports) == (80, 443)
    assert tuple(item.identifier for item in policy.values.remote_schemas.sources) == (
        "https://schemas.example.com/public.json",
    )
    authority = planning_authority(policy, project_namespace=NamespaceId(value="local"))
    assert authority.policy == policy.definition
    assert authority.allowed_subjects is not None
    assert {item.namespace.value for item in authority.allowed_subjects} == {"local"}
    assert authority.ceilings == policy.planning.ceilings


def test_policy_fingerprint_is_deterministic_and_semantic_lists_are_canonical() -> None:
    first = _compile(_text()).policy
    second = _compile(
        _text().replace("[https, http]", "[http, https]").replace("[POST, GET]", "[GET, POST]")
    ).policy
    assert first is not None
    assert second is not None
    assert first.definition.fingerprint == second.definition.fingerprint
    assert default_planning_ceilings() == default_planning_ceilings()
    assert tuple(item.resource for item in default_planning_ceilings()) == PLANNING_RESOURCES


def test_policy_schema_is_deterministic_strict_and_has_no_raw_persistence() -> None:
    schema = generate_policy_schema()
    assert schema["additionalProperties"] is False
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    rendered = render_policy_schema()
    assert rendered == render_policy_schema()
    assert rendered.endswith("\n")
    assert "rawPersistence" not in rendered
    assert '"allowSanitizedPersistence"' in rendered
    checked_in = (
        Path(__file__).parents[3]
        / "src"
        / "comparo"
        / "schemas"
        / "comparo-policy-v1alpha1.schema.json"
    )
    assert checked_in.read_text(encoding="utf-8") == rendered


def test_trusted_local_policy_is_explicit_stable_and_finitely_bounded() -> None:
    first = trusted_local_policy()
    second = trusted_local_policy()
    assert first == second
    assert first.definition.identity.resource_id == ResourceId(value="trusted-local")
    assert first.http.allow_any_host
    assert first.values.ambient_environment.allow_any
    assert first.values.remote_schemas.allow_any
    assert first.artifacts == PolicyArtifacts(
        allow_sanitized_persistence=True,
        roots=("/",),
    )
    assert tuple(item.resource for item in first.planning.ceilings) == PLANNING_RESOURCES


@pytest.mark.parametrize("root", ["/", "C:/comparo"])
def test_policy_accepts_canonical_cross_platform_artifact_roots(root: str) -> None:
    result = _compile(_text().replace("[/var/lib/comparo, /tmp/comparo]", f"[{root}]"))
    assert result.policy is not None
    assert result.policy.artifacts.roots == (root,)


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        ("comparo.policy/v1alpha1", "comparo.policy/v9", "policy.envelope.api-version"),
        ("ExecutionPolicy", "Project", "policy.envelope.kind"),
        ("id: ci", "id: INVALID", "policy.metadata.id"),
        ("id: ci", "id: 1", "policy.metadata.id"),
        ("name: CI", "name: ''", "policy.metadata.name"),
        ("description: Policy", "description: ''", "policy.metadata.description"),
        ("tags: [release]", "tags: [release, release]", "policy.value.duplicate"),
        ("tags: [release]", "tags: [INVALID]", "policy.metadata.tags"),
        ("tags: [release]", "tags: plain", "policy.metadata.tags"),
        ("[https, http]", "[ftp]", "policy.http.scheme"),
        ("[POST, GET]", "[get]", "policy.http.method"),
        ("[api.example.com, 127.0.0.1, '::1']", "[BAD_host]", "policy.http.host"),
        ("[443, 80]", "[0]", "policy.http.port"),
        ("[public, loopback]", "[unknown]", "policy.http.address-class"),
        ("allowAnyHost: false", "allowAnyHost: nope", "policy.http.flag"),
        ("allowAnyPort: false", "allowAnyPort: nope", "policy.http.flag"),
        ("[TOKEN, API_KEY]", "[BAD-NAME]", "policy.values.environment-name"),
        ("[TOKEN, API_KEY]", f"[{'A' * 256}]", "policy.values.environment-name"),
        ("allowProjectFiles: true", "allowProjectFiles: nope", "policy.values.project-files"),
        (
            "https://schemas.example.com/public.json",
            "file:///secret",
            "policy.values.schema-source",
        ),
        (
            "https://schemas.example.com/public.json",
            "https://",
            "policy.values.schema-source",
        ),
        ("[/var/lib/comparo, /tmp/comparo]", "[relative]", "policy.artifacts.root"),
        ("[/var/lib/comparo, /tmp/comparo]", "['']", "policy.artifacts.root"),
        ("[/var/lib/comparo, /tmp/comparo]", r'["/bad\troot"]', "policy.artifacts.root"),
        ("[/var/lib/comparo, /tmp/comparo]", "[/a/../b]", "policy.artifacts.root"),
        ("[/var/lib/comparo, /tmp/comparo]", "[/a//b]", "policy.artifacts.root"),
        ("[/var/lib/comparo, /tmp/comparo]", "[/a/]", "policy.artifacts.root"),
        ("[/var/lib/comparo, /tmp/comparo]", "[c:/bad]", "policy.artifacts.root"),
    ],
)
def test_invalid_policy_values_are_source_located(old: str, new: str, code: str) -> None:
    result = _compile(_text().replace(old, new))
    assert result.policy is None
    assert code in {item.code.value for item in result.diagnostics}
    assert all(item.primary.start.source_id == _SOURCE for item in result.diagnostics)


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("plain", "policy.envelope.mapping"),
        (_text().replace("kind: ExecutionPolicy\n", ""), "policy.envelope.field-required"),
        (
            _text().replace("kind: ExecutionPolicy", "kind: ExecutionPolicy\nunknown: true"),
            "policy.envelope.field-unknown",
        ),
        (
            _text().replace(
                "metadata: {id: ci, name: CI, description: Policy, tags: [release]}",
                "metadata: plain",
            ),
            "policy.metadata.mapping",
        ),
        (_text().replace("spec:\n", "spec:\n  unknown: true\n"), "policy.spec.field-unknown"),
        (_replace_block(_text(), "spec:\n", None, "spec: plain\n"), "policy.spec.mapping"),
        (
            _replace_block(_text(), "  planning: ", "\n  http:\n", "  planning: plain"),
            "policy.planning.mapping",
        ),
        (_text(planning=_planning(subjects="[invalid]")), "policy.planning.selector"),
        (
            _text(planning=_planning(subjects=f"[Request/{'a' * 64}]")),
            "policy.planning.selector",
        ),
        (
            _text(planning=_planning(subjects="[Environment/prod]")),
            "policy.planning.selector",
        ),
        (
            _text(planning=_planning(environments="[Request/get]")),
            "policy.planning.selector",
        ),
        (
            _text(planning=_planning(extra="ceilings: {requests: 0}")),
            "policy.planning.ceiling",
        ),
        (
            _text(planning=_planning(extra="ceilings: plain")),
            "policy.planning.ceilings.mapping",
        ),
        (
            _text(planning=_planning(extra="ceilings: {unknown: 2}")),
            "policy.planning.ceilings.field-unknown",
        ),
        (
            _text(planning=_planning(subjects="[Request/get]", allow_any_subjects="true")),
            "policy.planning.subject-conflict",
        ),
        (
            _text(
                planning=_planning(
                    environments="[Environment/prod]",
                    allow_any_environments="true",
                )
            ),
            "policy.planning.environment-conflict",
        ),
        (
            _text().replace(
                "    allowedHosts: [api.example.com, 127.0.0.1, '::1']",
                "    allowedHosts: []\n    extra: true",
            ),
            "policy.http.field-unknown",
        ),
        (
            _replace_block(_text(), "  http:\n", "  values:\n", "  http: plain\n"),
            "policy.http.mapping",
        ),
        (
            _text().replace("    allowedSchemes: [https, http]", "    allowedSchemes: []"),
            "policy.http.scheme",
        ),
        (
            _text().replace("    allowedMethods: [POST, GET]", "    allowedMethods: []"),
            "policy.http.method",
        ),
        (
            _text().replace("    allowedPorts: [443, 80]", "    allowedPorts: []"),
            "policy.http.port",
        ),
        (
            _text().replace("    allowedPorts: [443, 80]", "    allowedPorts: plain"),
            "policy.http.port",
        ),
        (
            _text().replace("    allowedPorts: [443, 80]", "    allowedPorts: [443, bad]"),
            "policy.http.port",
        ),
        (
            _text().replace("    allowedPorts: [443, 80]", "    allowedPorts: [443, 443]"),
            "policy.value.duplicate",
        ),
        (
            _text().replace(
                "    allowedAddressClasses: [public, loopback]", "    allowedAddressClasses: []"
            ),
            "policy.http.address-class",
        ),
        (
            _text().replace("    allowAnyHost: false", "    allowAnyHost: true"),
            "policy.http.host-conflict",
        ),
        (
            _text().replace("    allowAnyPort: false", "    allowAnyPort: true"),
            "policy.http.port-conflict",
        ),
        (
            _text().replace(
                "ambientEnvironment: {names: [TOKEN, API_KEY], allowAny: false}",
                "ambientEnvironment: {names: [TOKEN], allowAny: true}",
            ),
            "policy.values.environment-conflict",
        ),
        (
            _replace_block(_text(), "  values:\n", "  artifacts:\n", "  values: plain\n"),
            "policy.values.mapping",
        ),
        (
            _text().replace(
                "ambientEnvironment: {names: [TOKEN, API_KEY], allowAny: false}",
                "ambientEnvironment: plain",
            ),
            "policy.values.ambient-environment.mapping",
        ),
        (
            _text().replace("      allowAny: false", "      allowAny: true"),
            "policy.values.schema-conflict",
        ),
        (
            _replace_block(
                _text(),
                "    remoteSchemas:\n",
                "  artifacts:\n",
                "    remoteSchemas: plain\n",
            ),
            "policy.values.remote-schemas.mapping",
        ),
        (
            _text().replace(
                "    allowSanitizedPersistence: true", "    allowSanitizedPersistence: false"
            ),
            "policy.artifacts.root-conflict",
        ),
        (
            _replace_block(_text(), "  artifacts:\n", None, "  artifacts: plain\n"),
            "policy.artifacts.mapping",
        ),
    ],
)
def test_policy_rejects_unknown_missing_conflicting_and_invalid_shapes(
    text: str,
    code: str,
) -> None:
    result = _compile(text)
    assert result.policy is None
    assert code in {item.code.value for item in result.diagnostics}


def test_policy_document_and_boundary_fail_closed() -> None:
    empty = compile_execution_policy((), source_id=_SOURCE)
    assert empty.diagnostics[0].code.value == "policy.document.exactly-one"
    parsed = parse_yaml_source(source_id=_SOURCE, text=_text()).documents
    multiple = compile_execution_policy((*parsed, *parsed), source_id=_SOURCE)
    assert multiple.diagnostics[0].primary == parsed[0].root.span
    diagnostic = ConfigurationDiagnostic(
        code=DiagnosticCode(value="policy.test.invalid"),
        severity=DiagnosticSeverity.ERROR,
        primary=parsed[0].root.span,
    )
    carried = compile_execution_policy(parsed, source_id=_SOURCE, diagnostics=(diagnostic,))
    assert carried == PolicyCompilationResult(diagnostics=(diagnostic,))
    with pytest.raises(TypeError, match="documents"):
        compile_execution_policy(cast("tuple[ParsedDocument, ...]", [object()]), source_id=_SOURCE)
    with pytest.raises(TypeError, match="namespace"):
        compile_execution_policy(parsed, source_id=cast("SourceId", object()))
    with pytest.raises(TypeError, match="diagnostics"):
        compile_execution_policy(
            parsed,
            source_id=_SOURCE,
            diagnostics=cast("tuple[ConfigurationDiagnostic, ...]", [object()]),
        )


def test_core_policy_models_enforce_direct_construction_invariants() -> None:
    policy = _policy()
    assert is_policy_host(DnsHost(value="example.com"))
    assert not is_policy_host(object())
    with pytest.raises(ValueError, match="planning-selectable"):
        PolicyResourceSelector(kind=ResourceKind.PROJECT, resource_id=ResourceId(value="x"))
    with pytest.raises(ValueError, match="every resource"):
        PolicyPlanning(
            subjects=(),
            environments=(),
            allow_any_subjects=False,
            allow_any_environments=False,
            ceilings=(),
        )
    with pytest.raises(ValueError, match="allowAnyHost"):
        msgspec.structs.replace(policy.http, allow_any_host=True)
    with pytest.raises(ValueError, match="allowAnyPort"):
        msgspec.structs.replace(policy.http, allow_any_port=True)
    assert trusted_local_policy().http.allow_any_port
    with pytest.raises(ValueError, match="portable variable"):
        AmbientEnvironmentPolicy(names=("BAD-NAME",), allow_any=False)
    with pytest.raises(ValueError, match="redundant allowlist"):
        AmbientEnvironmentPolicy(names=("TOKEN",), allow_any=True)
    with pytest.raises(ValueError, match="match configured roots"):
        PolicyArtifacts(allow_sanitized_persistence=True, roots=())
    with pytest.raises(TypeError, match="Project NamespaceId"):
        planning_authority(policy, project_namespace=cast("NamespaceId", object()))
    with pytest.raises(TypeError, match="CompiledExecutionPolicy"):
        planning_authority(
            cast("CompiledExecutionPolicy", object()), project_namespace=NamespaceId(value="x")
        )
    with pytest.raises(ValueError, match="exactly"):
        PolicyCompilationResult()
    with pytest.raises(ValueError, match="exactly"):
        PolicyCompilationResult(policy=policy, diagnostics=(_compile("plain").diagnostics[0],))
