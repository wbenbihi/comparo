"""Tests for registry, linking, semantic validation, and canonical normalization."""

from typing import cast

import pytest

from comparo.configuration.yaml_parser import parse_yaml_source
from comparo.core.configuration import compiler
from comparo.core.configuration.canonical import CanonicalArray
from comparo.core.configuration.canonical import CanonicalMember
from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import CompilationResult
from comparo.core.configuration.canonical import CompiledProject
from comparo.core.configuration.canonical import CompiledResource
from comparo.core.configuration.canonical import InlineResource
from comparo.core.configuration.canonical import LinkedResource
from comparo.core.configuration.canonical import RemoteSchema
from comparo.core.configuration.compiler import compile_v1alpha1
from comparo.core.configuration.models import ConfigurationDiagnostic
from comparo.core.configuration.models import MappingNode
from comparo.core.configuration.v1alpha1.ast import V1Alpha1FrontendResult
from comparo.core.configuration.v1alpha1.frontend import parse_v1alpha1_documents
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InlineDefinitionOrigin
from comparo.core.domain.identity import InlineDefinitionRef
from comparo.core.domain.identity import InlineFragment
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import SourceId
from comparo.core.domain.metadata import Metadata
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.values import JsonString

_SOURCE = SourceId(value="entry-config")
_NAMESPACE = NamespaceId(value="local")


def _document(kind: str, resource_id: str, spec: str, *, name: str | None = None) -> str:
    return (
        "apiVersion: comparo/v1alpha1\n"
        f"kind: {kind}\n"
        f"metadata: {{id: {resource_id}, name: {name or resource_id.title()}}}\n"
        f"spec: {spec}\n"
    )


def _frontend(text: str, *, source: SourceId = _SOURCE) -> V1Alpha1FrontendResult:
    parsed = parse_yaml_source(source_id=source, text=text)
    return parse_v1alpha1_documents(parsed.documents)


def _compile(text: str) -> CompilationResult:
    return compile_v1alpha1(
        _frontend(text),
        namespace=_NAMESPACE,
        entry_source=_SOURCE,
    )


def _codes(result: CompilationResult) -> tuple[str, ...]:
    return tuple(diagnostic.code.value for diagnostic in result.diagnostics)


def _valid_project() -> str:
    return _document(
        "Project",
        "demo",
        "{data: .comparo, variables: {region: eu}, secrets: {token: literal-secret}}",
    )


def test_compiler_links_all_reference_shapes_and_normalizes_directives() -> None:
    text = "---\n".join(
        (
            _valid_project(),
            _document("Environment", "prod", "{baseUrl: 'https://prod.example'}"),
            _document("Environment", "staging", "{baseUrl: 'https://staging.example'}"),
            _document("Matrix", "locales", "{cases: [{id: en, values: {locale: en}}]}"),
            _document("Value", "token", "{value: {$env: API_TOKEN}}"),
            _document("Schema", "response", "{type: object, properties: {ok: {type: boolean}}}"),
            _document("AssertionProfile", "http-ok", "{rules: [{operator: equals}]}"),
            _document("DiffProfile", "semantic", "{rules: [{comparator: exact}]}"),
            _document(
                "Request",
                "get-user",
                "{request: {method: GET, endpoint: '/users/${parameters.userId}', "
                "headers: {authorization: {$use: token}}}, matrices: [{$use: locales}], "
                "responseSchema: {$use: response}, assertions: [{$use: http-ok}], "
                "diffs: [{rules: [{comparator: exact}]}]}",
            ),
            _document(
                "ExecutionProfile",
                "release",
                "{subjects: [{$use: Request/get-user}], baseline: {$use: prod}, "
                "candidates: [{$use: staging}]}",
            ),
        )
    )
    result = _compile(text)
    assert result.diagnostics == ()
    assert result.project is not None
    project = result.project
    assert project.find(ResourceKind.REQUEST, ResourceId(value="get-user")) is not None
    assert project.find(ResourceKind.REQUEST, ResourceId(value="missing")) is None
    request = cast(
        "CompiledResource",
        project.find(ResourceKind.REQUEST, ResourceId(value="get-user")),
    )
    spec = cast("CanonicalObject", request.spec)
    values = {member.name: member.value for member in spec.members}
    assert type(values["request"]) is ObjectExpr
    assert type(values["matrices"]) is CanonicalArray
    assert type(values["matrices"].items[0]) is LinkedResource
    assert type(values["diffs"]) is CanonicalArray
    assert type(values["diffs"].items[0]) is InlineResource
    assert type(values["responseSchema"]) is LinkedResource
    environment = cast(
        "CompiledResource",
        project.find(ResourceKind.ENVIRONMENT, ResourceId(value="prod")),
    )
    assert type(cast("CanonicalObject", environment.spec).members[0].value) is LiteralExpr
    assert "literal-secret" not in repr(project.project)


def test_compiler_supports_remote_and_inline_schemas_without_fetching() -> None:
    remote = _compile(
        "---\n".join(
            (
                _valid_project(),
                _document(
                    "Request",
                    "remote",
                    "{request: {method: GET, endpoint: /}, "
                    "responseSchema: {$use: 'https://schemas.example/api.json'}}",
                ),
            )
        )
    )
    assert remote.project is not None
    request = cast(
        "CompiledResource",
        remote.project.find(ResourceKind.REQUEST, ResourceId(value="remote")),
    )
    schema = next(
        member.value
        for member in cast("CanonicalObject", request.spec).members
        if member.name == "responseSchema"
    )
    assert schema == RemoteSchema(uri="https://schemas.example/api.json")
    inline = _compile(
        "---\n".join(
            (
                _valid_project(),
                _document(
                    "Request",
                    "inline",
                    "{request: {method: GET, endpoint: /}, responseSchema: true}",
                ),
            )
        )
    )
    assert inline.project is not None

    unsafe = _compile(
        "---\n".join(
            (
                _valid_project(),
                _document(
                    "Request",
                    "unsafe",
                    "{request: {method: GET, endpoint: /}, "
                    "responseSchema: {$use: 'https://user:secret@schemas.example/api.json'}}",
                ),
            )
        )
    )
    assert "configuration.reference.shape" in _codes(unsafe)


def test_project_defaults_are_typed_and_linked_like_execution_profiles() -> None:
    result = _compile(
        "---\n".join(
            (
                _document(
                    "Project",
                    "demo",
                    "{defaults: {baseline: {$use: prod}, candidates: [{$use: staging}], "
                    "assertions: [{$use: healthy}]}}",
                ),
                _document("Environment", "prod", "{baseUrl: 'https://prod.example'}"),
                _document("Environment", "staging", "{baseUrl: 'https://staging.example'}"),
                _document("AssertionProfile", "healthy", "{rules: [{operator: equals}]}"),
            )
        )
    )
    assert result.project is not None
    defaults = next(
        member.value
        for member in cast("CanonicalObject", result.project.project.spec).members
        if member.name == "defaults"
    )
    default_members = {
        member.name: member.value for member in cast("CanonicalObject", defaults).members
    }
    assert type(default_members["baseline"]) is LinkedResource
    assert type(cast("CanonicalArray", default_members["assertions"]).items[0]) is LinkedResource


@pytest.mark.parametrize(
    ("kind", "rules", "code"),
    [
        ("AssertionProfile", "plain", "configuration.semantic.rule-shape"),
        ("AssertionProfile", "[plain]", "configuration.semantic.rule-shape"),
        ("AssertionProfile", "[{}]", "configuration.semantic.rule-shape"),
        (
            "AssertionProfile",
            "[{operator: [equals]}]",
            "configuration.semantic.operation-static",
        ),
        (
            "AssertionProfile",
            "[{operator: {expected: yes}}]",
            "configuration.semantic.operation-static",
        ),
        (
            "AssertionProfile",
            "[{operator: {name: [equals]}}]",
            "configuration.semantic.operation-static",
        ),
        (
            "AssertionProfile",
            "[{operator: '${env.OPERATOR}'}]",
            "configuration.semantic.operation-static",
        ),
        (
            "AssertionProfile",
            "[{operator: executesCode}]",
            "configuration.semantic.operation-unsupported",
        ),
        ("DiffProfile", "plain", "configuration.semantic.rule-shape"),
        ("DiffProfile", "[plain]", "configuration.semantic.rule-shape"),
        ("DiffProfile", "[{}]", "configuration.semantic.rule-shape"),
        (
            "DiffProfile",
            "[{comparator: unknown}]",
            "configuration.semantic.operation-unsupported",
        ),
        (
            "DiffProfile",
            "[{comparator: exact, transforms: project}]",
            "configuration.semantic.rule-shape",
        ),
        (
            "DiffProfile",
            "[{comparator: exact, transforms: [unknown]}]",
            "configuration.semantic.operation-unsupported",
        ),
        (
            "DiffProfile",
            "[{comparator: exact, reducer: unknown}]",
            "configuration.semantic.operation-unsupported",
        ),
        (
            "DiffProfile",
            "[{comparator: exact, pairing: unknown}]",
            "configuration.semantic.operation-unsupported",
        ),
        (
            "DiffProfile",
            "[{comparator: {name: '${env.OP}'}}]",
            "configuration.semantic.operation-static",
        ),
    ],
)
def test_profile_rules_reject_ambiguous_or_unsupported_operations(
    kind: str,
    rules: str,
    code: str,
) -> None:
    result = _compile(
        _valid_project() + "---\n" + _document(kind, "profile", f"{{rules: {rules}}}")
    )
    assert result.project is None
    assert code in _codes(result)


def test_profile_rules_accept_structured_and_versioned_builtin_operations() -> None:
    result = _compile(
        "---\n".join(
            (
                _valid_project(),
                _document(
                    "AssertionProfile",
                    "assertions",
                    "{rules: [{operator: {name: 'comparo.core/equals@1', expected: "
                    "'${parameters.expected}'}}]}",
                ),
                _document(
                    "DiffProfile",
                    "diffs",
                    "{rules: [{comparator: {name: numericTolerance, absolute: 0.1}, "
                    "transforms: [{name: project, pointers: [/status]}], reducer: count, "
                    "pairing: singular, note: semantic, samples: [one], "
                    "nested: {comparator: exact}, payload: {$literal: {comparator: arbitrary, "
                    "transforms: [arbitrary]}}}]}",
                ),
            )
        )
    )
    assert result.diagnostics == ()
    assert result.project is not None


@pytest.mark.parametrize(
    ("documents", "code"),
    [
        (("Environment", "prod", "{baseUrl: x}"), "configuration.project.exactly-one"),
        (
            ("Project", "demo", "[]"),
            "configuration.spec.mapping-required",
        ),
        (
            ("Project", "demo", "{defaults: []}"),
            "configuration.spec.mapping-required",
        ),
        (
            ("Project", "demo", "{defaults: {candidates: []}}"),
            "configuration.semantic.execution-topology",
        ),
        (
            (
                "Project",
                "demo",
                "{defaults: {baseline: {$use: prod}, environments: []}}",
            ),
            "configuration.semantic.execution-topology",
        ),
        (
            ("Environment", "env", "{baseUrl: x, unknown: true}"),
            "configuration.spec.field-unknown",
        ),
        (
            ("Environment", "env", "{baseUrl: x, envFiles: []}"),
            "configuration.spec.field-unknown",
        ),
        (("Environment", "env", "{}"), "configuration.spec.field-required"),
        (
            ("Environment", "env", "{baseUrl: x, tls: {$env: BAD}}"),
            "configuration.spec.static-dynamic",
        ),
        (("Matrix", "matrix", "{}"), "configuration.semantic.matrix-shape"),
        (
            ("Matrix", "matrix", "{axes: {}, cases: []}"),
            "configuration.semantic.matrix-shape",
        ),
        (
            ("ExecutionProfile", "run", "{baseline: {$use: env}, environments: []}"),
            "configuration.semantic.execution-topology",
        ),
        (
            ("ExecutionProfile", "run", "{candidates: []}"),
            "configuration.semantic.execution-topology",
        ),
        (("Request", "bad", "{request: plain}"), "configuration.semantic.request-shape"),
        (
            ("Request", "bad", "{request: {method: '${env.METHOD}', endpoint: /}}"),
            "configuration.semantic.request-shape",
        ),
        (
            ("Request", "bad", "{request: {method: GET, endpoint: /, url: 'https://x'}}"),
            "configuration.semantic.request-shape",
        ),
        (
            ("Request", "bad", "{request: {method: GET, endpoint: /, extra: true}}"),
            "configuration.semantic.request-shape",
        ),
    ],
)
def test_cross_field_and_schema_errors_fail_before_execution(
    documents: tuple[str, str, str],
    code: str,
) -> None:
    kind, resource_id, spec = documents
    text = _valid_project()
    if code == "configuration.project.exactly-one" or kind == "Project":
        text = _document(kind, resource_id, spec)
    else:
        text += "---\n" + _document(kind, resource_id, spec)
    result = _compile(text)
    assert result.project is None
    assert code in _codes(result)


@pytest.mark.parametrize(
    ("spec", "code"),
    [
        (
            "{request: {method: GET, endpoint: /}, matrices: {$use: matrix}}",
            "configuration.reference.shape",
        ),
        (
            "{request: {method: GET, endpoint: /}, matrices: [{$use: missing}]}",
            "configuration.reference.missing",
        ),
        (
            "{request: {method: GET, endpoint: /}, matrices: [{$use: Environment/env}]}",
            "configuration.reference.kind",
        ),
        (
            "{request: {method: GET, endpoint: /}, matrices: [{cases: []}]}",
            "configuration.reference.inline-forbidden",
        ),
    ],
)
def test_reference_shape_kind_existence_and_inline_policy_are_strict(spec: str, code: str) -> None:
    result = _compile(_valid_project() + "---\n" + _document("Request", "request", spec))
    assert code in _codes(result)


def test_union_references_require_qualified_kind_and_duplicate_ids_report_both_spans() -> None:
    unqualified = _compile(
        _valid_project()
        + "---\n"
        + _document("Request", "request", "{request: {method: GET, endpoint: /}}")
        + "---\n"
        + _document("ExecutionProfile", "run", "{subjects: [{$use: request}], environments: []}")
    )
    assert "configuration.reference.qualification" in _codes(unqualified)
    duplicate = _compile(
        _valid_project()
        + "---\n"
        + _document("Environment", "same", "{baseUrl: one}")
        + "---\n"
        + _document("Environment", "same", "{baseUrl: two}")
    )
    issue = next(
        diagnostic
        for diagnostic in duplicate.diagnostics
        if diagnostic.code.value == "configuration.resource.duplicate"
    )
    assert len(issue.related) == 1


@pytest.mark.parametrize(
    ("reference", "code"),
    [
        ("BadKind/value", "configuration.reference.shape"),
        ("Matrix/Bad_ID", "configuration.reference.shape"),
        ("Bad_ID", "configuration.reference.shape"),
    ],
)
def test_reference_identifiers_are_strict_and_never_search_across_kinds(
    reference: str,
    code: str,
) -> None:
    result = _compile(
        _valid_project()
        + "---\n"
        + _document(
            "Request",
            "request",
            f"{{request: {{method: GET, endpoint: /}}, matrices: [{{$use: '{reference}'}}]}}",
        )
    )
    assert code in _codes(result)


def test_value_expansion_handles_cache_missing_and_malformed_value_resources() -> None:
    cached = _compile(
        _valid_project()
        + "---\n"
        + _document("Value", "shared", "{value: public}")
        + "---\n"
        + _document(
            "Request",
            "request",
            "{request: {method: GET, endpoint: /, "
            "headers: {one: {$use: shared}, two: {$use: shared}}}}",
        )
    )
    assert cached.project is not None
    missing = _compile(
        _valid_project()
        + "---\n"
        + _document(
            "Request",
            "request",
            "{request: {method: GET, endpoint: /, headers: {x: {$use: missing}}}}",
        )
    )
    assert "configuration.reference.missing" in _codes(missing)
    invalid_reference = _compile(
        _valid_project()
        + "---\n"
        + _document(
            "Request",
            "request",
            "{request: {method: GET, endpoint: /, headers: {x: {$use: 'Value/Bad_ID'}}}}",
        )
    )
    assert "configuration.reference.shape" in _codes(invalid_reference)
    for malformed_spec in ("[]", "{}"):
        malformed = _compile(
            _valid_project()
            + "---\n"
            + _document("Value", "bad", malformed_spec)
            + "---\n"
            + _document(
                "Request",
                "request",
                "{request: {method: GET, endpoint: /, headers: {x: {$use: bad}}}}",
            )
        )
        assert malformed.project is None


def test_empty_frontend_and_multiple_projects_fail_with_synthetic_safe_locations() -> None:
    empty = compile_v1alpha1(
        V1Alpha1FrontendResult(),
        namespace=_NAMESPACE,
        entry_source=_SOURCE,
    )
    assert _codes(empty) == ("configuration.project.exactly-one",)
    assert empty.diagnostics[0].primary.start.source_id == _SOURCE
    multiple = _compile(_valid_project() + "---\n" + _document("Project", "other", "{}"))
    assert "configuration.project.exactly-one" in _codes(multiple)


def test_fingerprints_are_canonical_and_secret_values_are_redacted() -> None:
    first = _compile(
        _document(
            "Project",
            "demo",
            "{defaults: {trials: 1.20}, secrets: {token: first-secret}}",
        )
    )
    second = _compile(
        _document(
            "Project",
            "demo",
            "{secrets: {token: second-secret}, defaults: {trials: 1.2}}",
        )
    )
    assert first.project is not None
    assert second.project is not None
    assert (
        first.project.project.definition.fingerprint
        == second.project.project.definition.fingerprint
    )


def test_fingerprints_redact_contextual_credentials_but_track_public_changes() -> None:
    def request_fingerprint(spec: str) -> Fingerprint:
        result = _compile(_valid_project() + "---\n" + _document("Request", "secured", spec))
        assert result.project is not None
        request = result.project.find(ResourceKind.REQUEST, ResourceId(value="secured"))
        assert request is not None
        return request.definition.fingerprint

    first = request_fingerprint(
        "{request: {method: GET, endpoint: '/?api_key=first-secret', "
        "headers: {authorization: first-secret, x-public: one}, "
        "cookies: {SessionID: first-secret}, auth: {token: first-secret}}}"
    )
    second = request_fingerprint(
        "{request: {method: GET, endpoint: '/?api_key=second-secret', "
        "headers: {authorization: second-secret, x-public: one}, "
        "cookies: {SessionID: second-secret}, auth: {token: second-secret}}}"
    )
    public_change = request_fingerprint(
        "{request: {method: GET, endpoint: '/?api_key=second-secret', "
        "headers: {authorization: second-secret, x-public: two}, "
        "cookies: {SessionID: second-secret}, auth: {token: second-secret}}}"
    )

    assert first == second
    assert second != public_change


def test_fingerprint_redacts_structured_credential_entries() -> None:
    parsed = parse_yaml_source(
        source_id=_SOURCE,
        text=(
            "headers:\n"
            "  - name: authorization\n"
            "    value: Bearer LEAK-ME\n"
            "  - name: x-public\n"
            "    value: visible\n"
            "query:\n"
            "  api_key: LEAK-ME-TOO\n"
            "  public: visible\n"
        ),
    )
    root = cast("MappingNode", parsed.documents[0].root)

    assert compiler._fingerprint_node(root) == {
        "headers": [
            {"name": "authorization", "value": {"sensitive": True}},
            {"name": "x-public", "value": "visible"},
        ],
        "query": {"api_key": {"sensitive": True}, "public": "visible"},
    }
    assert compiler._redact_url_credentials("https://example.test/?token") == (
        "https://example.test/?token"
    )


def test_static_dynamic_detection_recurses_through_sequences() -> None:
    result = _compile(
        _valid_project()
        + "---\n"
        + _document("Environment", "env", "{baseUrl: x, tls: ['${env.BAD}']}")
    )
    assert "configuration.spec.static-dynamic" in _codes(result)


def test_environment_value_fingerprint_redaction_preserves_only_type_and_shape() -> None:
    parsed = parse_yaml_source(
        source_id=_SOURCE,
        text="values: {TOKEN: [one, {nested: null}]}",
    )
    root = cast("MappingNode", parsed.documents[0].root)

    assert compiler._fingerprint_node(root, context="environment-env") == {
        "values": {
            "sensitive": True,
            "type": "mapping",
            "entries": {
                "TOKEN": {
                    "sensitive": True,
                    "type": "sequence",
                    "items": [
                        {"sensitive": True, "type": "str"},
                        {
                            "sensitive": True,
                            "type": "mapping",
                            "entries": {
                                "nested": {"sensitive": True, "type": "null"},
                            },
                        },
                    ],
                }
            },
        }
    }


def test_profile_and_value_reference_cycles_are_source_located_errors() -> None:
    profiles = _compile(
        _valid_project()
        + "---\n"
        + _document("AssertionProfile", "one", "{include: [{$use: two}]} ")
        + "---\n"
        + _document("AssertionProfile", "two", "{include: [{$use: one}]} ")
    )
    assert "configuration.reference.cycle" in _codes(profiles)
    values = _compile(
        _valid_project()
        + "---\n"
        + _document("Value", "one", "{value: {$use: two}}")
        + "---\n"
        + _document("Value", "two", "{value: {$use: one}}")
        + "---\n"
        + _document(
            "Request",
            "request",
            "{request: {method: GET, endpoint: /, headers: {x: {$use: one}}}}",
        )
    )
    assert "configuration.reference.cycle" in _codes(values)


def test_project_must_be_first_in_entry_and_never_come_from_discovered_source() -> None:
    later = _compile(_document("Environment", "env", "{baseUrl: x}") + "---\n" + _valid_project())
    assert "configuration.project.location" in _codes(later)
    discovered_source = SourceId(value="discovered")
    frontend = _frontend(_valid_project(), source=discovered_source)
    result = compile_v1alpha1(frontend, namespace=_NAMESPACE, entry_source=_SOURCE)
    assert "configuration.project.location" in _codes(result)


def test_compiler_validates_public_boundary_types() -> None:
    frontend = _frontend(_valid_project())
    with pytest.raises(TypeError, match="frontend"):
        compile_v1alpha1(
            cast("V1Alpha1FrontendResult", object()),
            namespace=_NAMESPACE,
            entry_source=_SOURCE,
        )
    with pytest.raises(TypeError, match="namespace"):
        compile_v1alpha1(
            frontend,
            namespace=cast("NamespaceId", "local"),
            entry_source=_SOURCE,
        )
    with pytest.raises(TypeError, match="entry source"):
        compile_v1alpha1(
            frontend,
            namespace=_NAMESPACE,
            entry_source=cast("SourceId", "entry"),
        )


def test_canonical_models_reject_mutable_ambiguous_and_incoherent_states() -> None:
    result = _compile(_valid_project())
    assert result.project is not None
    project = result.project
    compiled = project.project
    definition = compiled.definition
    member = CanonicalMember(name="field", value=JsonString(value="value"))
    assert CanonicalObject(members=(member,)).members == (member,)
    with pytest.raises(TypeError, match="name must be text"):
        CanonicalMember(name=cast("str", 1), value=member.value)
    with pytest.raises(TypeError, match="canonical value"):
        CanonicalMember(name="field", value=cast("JsonString", object()))
    with pytest.raises(TypeError, match="typed tuple"):
        CanonicalObject(members=cast("tuple[CanonicalMember, ...]", [member]))
    with pytest.raises(ValueError, match="unique"):
        CanonicalObject(members=(member, member))
    with pytest.raises(TypeError, match="tuple of values"):
        CanonicalArray(items=cast("tuple[JsonString, ...]", [member.value]))
    with pytest.raises(TypeError, match="tuple of values"):
        CanonicalArray(items=cast("tuple[JsonString, ...]", (object(),)))
    with pytest.raises(ValueError, match=r"HTTP\(S\)"):
        RemoteSchema(uri=cast("str", 2))
    with pytest.raises(ValueError, match="non-payload"):
        RemoteSchema(uri="https://example.com/" + "x" * 4096)
    with pytest.raises(ValueError, match="non-payload"):
        RemoteSchema(uri="https://schemas.example/schema.json?token=secret")
    with pytest.raises(ValueError, match=r"HTTP\(S\)"):
        RemoteSchema(uri="file:///tmp/schema")
    assert RemoteSchema(uri="http://schemas.example/test").uri.startswith("http://")
    with pytest.raises(TypeError, match="DefinitionRef"):
        LinkedResource(target=cast("DefinitionRef", object()))
    inline = InlineResource(
        definition=InlineDefinitionRef(
            kind=ResourceKind.VALUE,
            origin=InlineDefinitionOrigin(
                owner=definition.identity,
                fragment=InlineFragment(value="#value"),
            ),
            fingerprint=definition.fingerprint,
        ),
        spec=CanonicalObject(members=()),
    )
    assert inline.kind is ResourceKind.VALUE
    assert inline.origin.fragment.value == "#value"
    with pytest.raises(TypeError, match="InlineDefinitionRef"):
        InlineResource(
            definition=cast("InlineDefinitionRef", object()),
            spec=inline.spec,
        )
    with pytest.raises(TypeError, match="CanonicalObject"):
        InlineResource(
            definition=inline.definition,
            spec=cast("CanonicalObject", object()),
        )
    with pytest.raises(TypeError, match="definition"):
        CompiledResource(
            definition=cast("DefinitionRef", object()),
            metadata=compiled.metadata,
            spec=compiled.spec,
            location=compiled.location,
        )
    with pytest.raises(TypeError, match="Metadata"):
        CompiledResource(
            definition=definition,
            metadata=cast("Metadata", object()),
            spec=compiled.spec,
            location=compiled.location,
        )
    with pytest.raises(TypeError, match="canonical"):
        CompiledResource(
            definition=definition,
            metadata=compiled.metadata,
            spec=cast("JsonString", object()),
            location=compiled.location,
        )
    with pytest.raises(TypeError, match="SourceLocation"):
        CompiledResource(
            definition=definition,
            metadata=compiled.metadata,
            spec=compiled.spec,
            location=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="API version"):
        CompiledProject(
            api_version="comparo/v2",
            namespace=project.namespace,
            project=compiled,
            resources=(),
        )
    with pytest.raises(TypeError, match="NamespaceId"):
        CompiledProject(
            api_version="comparo/v1alpha1",
            namespace=cast("NamespaceId", "local"),
            project=compiled,
            resources=(),
        )
    with pytest.raises(TypeError, match="CompiledResource"):
        CompiledProject(
            api_version="comparo/v1alpha1",
            namespace=project.namespace,
            project=cast("CompiledResource", object()),
            resources=(),
        )
    environment_result = _compile(
        _valid_project()
        + "---\n"
        + _document("Environment", "a", "{baseUrl: a}")
        + "---\n"
        + _document("Environment", "b", "{baseUrl: b}")
    )
    assert environment_result.project is not None
    first, second = environment_result.project.resources
    with pytest.raises(ValueError, match="Project kind"):
        CompiledProject(
            api_version="comparo/v1alpha1",
            namespace=project.namespace,
            project=first,
            resources=(),
        )
    with pytest.raises(TypeError, match="immutable typed tuple"):
        CompiledProject(
            api_version="comparo/v1alpha1",
            namespace=project.namespace,
            project=compiled,
            resources=cast("tuple[CompiledResource, ...]", [first]),
        )
    with pytest.raises(ValueError, match="another Project"):
        CompiledProject(
            api_version="comparo/v1alpha1",
            namespace=project.namespace,
            project=compiled,
            resources=(compiled,),
        )
    with pytest.raises(ValueError, match="unique"):
        CompiledProject(
            api_version="comparo/v1alpha1",
            namespace=project.namespace,
            project=compiled,
            resources=(first, first),
        )
    with pytest.raises(ValueError, match="canonical identity order"):
        CompiledProject(
            api_version="comparo/v1alpha1",
            namespace=project.namespace,
            project=compiled,
            resources=(second, first),
        )
    with pytest.raises(TypeError, match="typed kind"):
        project.find(cast("ResourceKind", "Request"), ResourceId(value="request"))
    with pytest.raises(TypeError, match="CompiledProject"):
        CompilationResult(project=cast("CompiledProject", object()))
    invalid = _compile(_document("Environment", "env", "{baseUrl: x}"))
    with pytest.raises(TypeError, match="diagnostics"):
        CompilationResult(
            diagnostics=cast("tuple[ConfigurationDiagnostic, ...]", [invalid.diagnostics[0]])
        )
    with pytest.raises(TypeError, match="diagnostics"):
        CompilationResult(diagnostics=cast("tuple[ConfigurationDiagnostic, ...]", (object(),)))
    with pytest.raises(ValueError, match="alongside diagnostics"):
        CompilationResult(project=project, diagnostics=(invalid.diagnostics[0],))
