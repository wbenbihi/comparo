"""Typed models for comparo configuration objects.

Every object shares a Kubernetes-style envelope (``apiVersion`` / ``kind`` /
``metadata`` / ``spec``) and is decoded with :mod:`msgspec`. Framework structs
forbid unknown fields so a mistyped key is a hard error; payload and freeform
positions (a request body, an instance value, a JSON Schema) are typed ``Any``
and validated later by the milestones that consume them.

Every field carries a ``description`` and ``examples`` via :func:`_f`, so the
generated JSON Schema drives editor autocomplete, hover docs, and inline
validation — and gives an agent enough to author config it can then
``comparo validate``.
"""

from typing import Annotated
from typing import Any
from typing import Literal

import msgspec

API_VERSION = "comparo/v1"


def _f(description: str, *examples: Any, **constraints: Any) -> msgspec.Meta:
    """Field metadata for editors: a description plus one or more schema ``examples``.

    Args:
        description: The one-line field description shown on hover / completion.
        examples: Realistic values surfaced by editors and JSON-Schema tooling.
        constraints: Extra :class:`msgspec.Meta` keywords (``ge``, ``pattern``, …).

    Returns:
        The :class:`msgspec.Meta` to place in the field's ``Annotated`` type.
    """
    return msgspec.Meta(description=description, examples=list(examples) or None, **constraints)


#: A duration written as a readable string, e.g. ``"5s"``, ``"500ms"``, ``"2m"``.
DurationStr = Annotated[str, msgspec.Meta(pattern=r"^[0-9]+(ms|s|m|h)$")]


class Duration(msgspec.Struct, rename="camel", forbid_unknown_fields=True, frozen=True):
    """A split timeout budget; an omitted field falls back to a built-in default."""

    connect: Annotated[
        DurationStr | None, _f("Time allowed to establish the connection.", "5s")
    ] = None
    read: Annotated[DurationStr | None, _f("Time allowed to read the full response.", "30s")] = None
    stream_idle: Annotated[
        DurationStr | None, _f("Max gap allowed between two streamed chunks.", "10s")
    ] = None
    stream_max: Annotated[
        DurationStr | None, _f("Hard cap on the total duration of a streamed response.", "5m")
    ] = None


class Meta(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """Object metadata. Every kind except ``Project`` declares an ``id``."""

    name: Annotated[
        str, _f("Human-readable label shown in the TUI and reports.", "Local", "Get user")
    ]
    id: Annotated[
        str | None,
        _f(
            "Stable, unique identity other objects reference. Omitted only on Project.",
            "environment.local",
            "request.get-user",
        ),
    ] = None
    description: Annotated[
        str | None, _f("Free-text explanation of what this object is for.", "Local dev environment")
    ] = None
    tags: Annotated[
        list[str] | None,
        _f("Labels for the manifest's default selection and matrix filtering.", ["smoke", "auth"]),
    ] = None


class Header(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """A single HTTP header. ``value`` may be a literal or a reference hole."""

    key: Annotated[str, _f("The header name.", "Authorization", "Content-Type")]
    value: Annotated[
        Any,
        _f(
            "The value: a literal, a ${...} string, or a directive hole.",
            "application/json",
            "Bearer ${API_TOKEN}",
        ),
    ] = None
    description: Annotated[
        str | None, _f("Free-text note (kept from an OpenAPI import).", "The bearer token")
    ] = None
    required: Annotated[
        bool, _f("Whether the header is required (documentation, from an import).", True)
    ] = False
    type: Annotated[
        str | None, _f("A hint for the value's type (from an OpenAPI import).", "string")
    ] = None


class HealthCheck(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """A request used to probe an environment's readiness."""

    method: Annotated[str, _f("HTTP method for the readiness probe.", "GET")]
    endpoint: Annotated[str, _f("Path (joined to baseUrl) to probe.", "/health", "/status/200")]
    headers: Annotated[
        list[Header] | None,
        _f(
            "Extra headers to send with the probe.",
            [{"key": "Accept", "value": "application/json"}],
        ),
    ] = None


class EnvironmentSpec(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The body of an ``Environment`` object."""

    base_url: Annotated[
        str,
        _f(
            "Base URL every request endpoint is joined to.",
            "http://localhost:8080",
            "https://api.example.com",
        ),
    ]
    timeout: Annotated[
        Duration | None,
        _f("Per-request timeout budget for this environment.", {"connect": "5s", "read": "30s"}),
    ] = None
    secrets: Annotated[
        dict[str, Any] | None,
        _f(
            "Declared secrets by name; the value is a source ($env/$file/$literal/$from).",
            {"API_TOKEN": {"$env": "COMPARO_TOKEN"}},
        ),
    ] = None
    variables: Annotated[
        dict[str, str] | None,
        _f("Named string values referenced as ${NAME} or {$var: NAME}.", {"region": "us-east-1"}),
    ] = None
    headers: Annotated[
        list[Header] | None,
        _f(
            "Headers applied to every request unless it overrides them.",
            [{"key": "Accept", "value": "application/json"}],
        ),
    ] = None
    health: Annotated[
        list[HealthCheck] | None,
        _f(
            "Readiness probes run by `comparo health` and the TUI.",
            [{"method": "GET", "endpoint": "/health"}],
        ),
    ] = None
    auth: Annotated[
        Any,
        _f(
            "Default Basic/Bearer auth applied to every request unless it sets its own.",
            {"bearer": "${API_TOKEN}"},
        ),
    ] = None


class HttpRequest(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The outbound HTTP request a ``Request`` object describes."""

    method: Annotated[str, _f("The HTTP method.", "GET", "POST")]
    endpoint: Annotated[
        str, _f("Path (joined to baseUrl); may carry ${...} holes.", "/users/${USER_ID}")
    ]
    query: Annotated[
        dict[str, Any] | None,
        _f("Query parameters as a name→value map.", {"page": "1", "limit": "20"}),
    ] = None
    headers: Annotated[
        list[Header] | dict[str, Any] | None,
        _f(
            "A list of Header objects or a {Name: value} map ({$val: id} injects a shared set).",
            {"$val": "instance.default-headers"},
        ),
    ] = None
    body: Annotated[Any, _f("The request body (shape depends on bodyType).", {"name": "Ada"})] = (
        None
    )
    body_type: Annotated[
        str | None, _f("How the body is encoded: json (default), form, or raw.", "json", "form")
    ] = None
    auth: Annotated[
        Any,
        _f(
            "Basic/Bearer auth for this request; overrides the environment default.",
            {"basic": {"username": "u", "password": "${PW}"}},
        ),
    ] = None
    cookies: Annotated[
        dict[str, Any] | None,
        _f("Cookies to send, as a name→value map.", {"session": "${SESSION}"}),
    ] = None


class Response(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The expected response of a ``Request`` object."""

    status: Annotated[int | None, _f("The expected HTTP status code.", 200)] = None
    schema: Annotated[
        Any,
        _f(
            "A Schema to validate the body against ($use: schema.x or inline JSON Schema).",
            {"$use": "schema.user"},
        ),
    ] = None
    diff: Annotated[
        Any,
        _f(
            "A DiffProfile ($use/inline/list) overriding the project default comparison.",
            {"$use": "diff.lenient"},
        ),
    ] = None
    streaming: Annotated[
        bool | None, _f("Whether the response is streamed (SSE / chunked).", True)
    ] = None
    assertions: Annotated[
        Any,
        _f(
            "AssertionProfiles ($use/inline/list) checked on the response — the `assert` key.",
            {"$use": "assert.http-ok"},
        ),
    ] = msgspec.field(default=None, name="assert")


class RequestSpec(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The body of a ``Request`` object."""

    request: Annotated[
        HttpRequest, _f("The outbound HTTP request.", {"method": "GET", "endpoint": "/users"})
    ]
    response: Annotated[
        Response | None,
        _f("The expected response — schema, diff profile, assertions.", {"status": 200}),
    ] = None
    matrix: Annotated[
        list[Any] | None,
        _f(
            "Matrices ({$use: matrix.x}) this request is expanded across.",
            [{"$use": "matrix.locales"}],
        ),
    ] = None
    timeout: Annotated[Duration | None, _f("A per-request timeout override.", {"read": "60s"})] = (
        None
    )


class InstanceSpec(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The body of an ``Instance`` object — a single reusable value."""

    value: Annotated[
        Any,
        _f(
            "The reusable value injected wherever {$val: this-id} appears.",
            {"Accept": "application/json"},
        ),
    ] = None


#: A matrix injects into exactly one of these request positions; a typo like
#: ``request.qeury`` is rejected at load instead of silently no-op'ing.
MatrixTarget = Annotated[str, msgspec.Meta(pattern=r"^(request\.)?(query|body|path)$")]


class MatrixSpec(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The body of a ``Matrix`` object — a list of atomic cases."""

    target: Annotated[
        MatrixTarget,
        _f(
            "Where each case is injected: request.query, request.body, or request.path.",
            "request.query",
        ),
    ]
    values: Annotated[
        list[dict[str, Any]],
        _f(
            "The cases; the cartesian product across matrices is expanded.",
            [{"locale": "en-US"}, {"locale": "fr-FR"}],
        ),
    ]
    mode: Annotated[
        Literal["merge", "replace"],
        _f("merge updates the target container with each case; replace substitutes it.", "merge"),
    ] = "merge"
    create_path: Annotated[
        bool, _f("Create missing intermediate objects along the target path.", True)
    ] = False


#: The comparison modes a DiffProfile understands. Constrained so a typo like
#: ``excat`` is rejected at load instead of silently degrading a path to SAME.
DiffMode = Literal["exact", "ignore", "shape", "type", "tolerance"]


class DiffRule(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """One path-scoped comparison rule inside a ``DiffProfile``."""

    path: Annotated[
        str,
        _f(
            "The JSON path this rule scopes ($ is the body root; $headers.x, $status too).",
            "$.data.items[*].id",
            "$headers.date",
        ),
    ]
    mode: Annotated[DiffMode, _f("How this path is compared.", "ignore", "tolerance")]
    schema: Annotated[
        Any, _f("A JSON Schema for `mode: shape` structural comparison.", {"type": "number"})
    ] = None
    array_length: Annotated[
        Literal["exact", "tolerant"] | None,
        _f("For arrays: compare length exactly or tolerantly.", "exact"),
    ] = None
    tolerance: Annotated[
        float | None, _f("Absolute numeric tolerance for `mode: tolerance`.", 0.01)
    ] = None


class DiffProfileSpec(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The body of a ``DiffProfile`` object."""

    default: Annotated[
        DiffMode, _f("The comparison mode for any path not matched by a rule.", "exact")
    ]
    rules: Annotated[
        list[DiffRule] | None,
        _f(
            "Path-scoped overrides of the default mode.",
            [{"path": "$.timestamp", "mode": "ignore"}],
        ),
    ] = None


class DiffPair(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """A named baseline/candidate pairing the Diff screen and ``--pair`` resolve."""

    name: Annotated[str, _f("The pair's name, selected with --pair.", "prod-vs-staging")]
    baseline: Annotated[str, _f("The baseline environment name or id.", "environment.prod")]
    candidate: Annotated[str, _f("The candidate environment name or id.", "environment.staging")]


class EnvironmentsConfig(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The manifest's ``environments`` block: a default and named diff pairs."""

    default: Annotated[
        str | None, _f("The environment used when none is given.", "environment.local")
    ] = None
    diff_pairs: Annotated[
        list[DiffPair] | None,
        _f(
            "Named baseline/candidate pairs for diffing.",
            [{"name": "canary", "baseline": "stable", "candidate": "canary"}],
        ),
    ] = None


class RetryConfig(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """Retry policy for transport failures."""

    attempts: Annotated[int, _f("Max attempts per request (≥ 1).", 3, ge=1)] | None = None
    backoff: Annotated[
        Literal["constant", "linear", "exponential"] | None,
        _f("How the delay grows between attempts.", "exponential"),
    ] = None


class RunConfig(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """Run-wide execution defaults: concurrency and retry."""

    concurrency: Annotated[int, _f("In-flight request cap (≥ 1).", 8, ge=1)] | None = None
    retry: Annotated[
        RetryConfig | None,
        _f("Retry policy for transport failures.", {"attempts": 3, "backoff": "exponential"}),
    ] = None


class SelectionConfig(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The default request selection for headless ``run``/``diff``."""

    tags: Annotated[
        list[str] | None, _f("Only run requests carrying any of these tags.", ["smoke"])
    ] = None
    requests: Annotated[
        list[str] | None, _f("Only run these request ids.", ["request.get-user"])
    ] = None


class ReportConfig(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """Report defaults: formats to write, the artifact output dir, archive dir."""

    formats: Annotated[
        list[str] | None,
        _f("Reporters to write on a headless run.", ["junit", "sarif", "json", "markdown"]),
    ] = None
    output: Annotated[str | None, _f("Directory for written report artifacts.", "reports")] = None
    dir: Annotated[
        str | None, _f("Directory the replayable report archive is kept in.", ".reports")
    ] = None
    retention: Annotated[
        int | None, _f("How many saved reports to keep (newest first); null keeps all.", 50)
    ] = None


class RedactionConfig(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """Redaction options. The declared-secret display masking is always on."""

    string_match_backstop: Annotated[
        bool | None,
        _f(
            "Forward-compat flag only — the value-match floor is always on and cannot be disabled.",
            True,
        ),
    ] = None


class ProjectSpec(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The body of the root ``Project`` manifest.

    Interiors are strict structs so a mistyped key (``defualt``, ``concurency``)
    is a hard load error, not a silently-ignored setting. ``diff`` stays ``Any``
    because it holds ``$use``/inline profile holes; ``plugins`` is gated by a
    dedicated load error.
    """

    data: Annotated[
        str | None,
        _f("Directory the object YAML lives in, relative to the manifest.", ".comparo", "."),
    ] = None
    environments: Annotated[
        EnvironmentsConfig | None,
        _f("The default environment and named diff pairs.", {"default": "environment.local"}),
    ] = None
    run: Annotated[
        RunConfig | None, _f("Run-wide concurrency and retry defaults.", {"concurrency": 8})
    ] = None
    diff: Annotated[
        Any,
        _f(
            "The project-wide default DiffProfile ($use/inline/list) under `default`.",
            {"default": {"$use": "diff.strict"}},
        ),
    ] = None
    selection: Annotated[
        SelectionConfig | None,
        _f("Default request selection for headless run/diff.", {"tags": ["smoke"]}),
    ] = None
    report: Annotated[
        ReportConfig | None,
        _f("Reporter, output, and archive defaults.", {"formats": ["junit"], "dir": ".reports"}),
    ] = None
    redaction: Annotated[
        RedactionConfig | None,
        _f("Redaction options (the value floor is always on).", {"stringMatchBackstop": True}),
    ] = None
    plugins: Annotated[
        Any, _f("Reserved — configuring it is currently a hard load error.", None)
    ] = None


class AssertionRule(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """One response assertion: a target, an operator, and an expected value."""

    target: Annotated[
        str,
        _f(
            "What to assert on: $status, $headers.x, or a body path $.a.b.",
            "$status",
            "$.data.total",
        ),
    ]
    op: Annotated[
        str,
        _f(
            "The operator: equals, lt/lte/gt/gte, between, oneOf, exists, contains, schema.",
            "equals",
            "lte",
        ),
    ]
    value: Annotated[Any, _f("The expected value for the operator.", 200, 999)] = None
    severity: Annotated[
        Literal["error", "warn"], _f("error fails the gate; warn is advisory.", "error")
    ] = "error"


class AssertionProfileSpec(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The body of an ``AssertionProfile`` — composable response assertions."""

    rules: Annotated[
        list[AssertionRule] | None,
        _f(
            "The assertions this profile checks.",
            [{"target": "$status", "op": "equals", "value": 200}],
        ),
    ] = None
    include: Annotated[
        list[Any] | None,
        _f("Other AssertionProfiles ({$use: id}) composed in first.", [{"$use": "assert.http-ok"}]),
    ] = None


class MatrixScope(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """Per-execution matrix customization: which cases to run, add, or drop."""

    include: Annotated[
        list[dict[str, Any]] | None, _f("Keep only cases matching these.", [{"tier": "free"}])
    ] = None
    exclude: Annotated[
        list[dict[str, Any]] | None, _f("Drop cases matching these.", [{"region": "eu"}])
    ] = None
    override: Annotated[
        list[dict[str, Any]] | None,
        _f("Add extra cases for this execution.", [{"tier": "enterprise"}]),
    ] = None


class ExecutionSelect(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """Which requests an execution runs — by tag and/or explicit id."""

    tags: Annotated[
        list[str] | None, _f("Run requests carrying any of these tags.", ["release"])
    ] = None
    requests: Annotated[list[str] | None, _f("Run these request ids.", ["request.checkout"])] = None


class ExecutionEnvironments(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The environments to run against; a ``candidate`` enables the diff."""

    baseline: Annotated[str | None, _f("The baseline environment name or id.", "stable")] = None
    candidate: Annotated[
        str | None, _f("The candidate environment; its presence enables the diff.", "canary")
    ] = None


class ExecutionCheck(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """Which checks an execution computes."""

    assertions: Annotated[bool, _f("Whether to evaluate response assertions.", True)] = True
    diff: Annotated[bool, _f("Whether to diff baseline vs candidate.", True)] = True


class ExecutionProfiles(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The ``profiles`` block of an ExecutionProfile — its diff / assert overrides.

    Typed (not ``Any``) so a mistyped key like ``asert`` is a hard load error
    rather than a silently-ignored profile.
    """

    diff: Annotated[
        Any,
        _f("A DiffProfile ($use/inline/list) applied for this execution.", {"$use": "diff.strict"}),
    ] = None
    assert_: Annotated[
        Any,
        _f(
            "AssertionProfiles ($use/inline/list) applied for this execution — the `assert` key.",
            {"$use": "assert.release"},
        ),
    ] = msgspec.field(name="assert", default=None)


class ExecutionProfileSpec(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The body of an ``ExecutionProfile`` — what to run, where, and which checks."""

    select: Annotated[
        ExecutionSelect | None,
        _f("Which requests to run (by tag and/or id).", {"tags": ["release"]}),
    ] = None
    matrix: Annotated[
        dict[str, MatrixScope] | None,
        _f(
            "Per-matrix scoping, keyed by matrix id.",
            {"matrix.tiers": {"include": [{"tier": "free"}]}},
        ),
    ] = None
    environments: Annotated[
        ExecutionEnvironments | None,
        _f(
            "Baseline and (optional) candidate environments.",
            {"baseline": "stable", "candidate": "canary"},
        ),
    ] = None
    check: Annotated[
        ExecutionCheck | None,
        _f("Which checks to compute (assertions, diff).", {"assertions": True, "diff": True}),
    ] = None
    profiles: Annotated[
        ExecutionProfiles | None,
        _f("Diff/assert profile overrides for this execution.", {"diff": {"$use": "diff.strict"}}),
    ] = None
    report: Annotated[
        Any,
        _f(
            "Report overrides for this execution (same shape as Project.report).",
            {"formats": ["junit"]},
        ),
    ] = None


class Environment(
    msgspec.Struct, tag_field="kind", tag="Environment", rename="camel", forbid_unknown_fields=True
):
    """A target environment."""

    api_version: Annotated[Literal["comparo/v1"], _f("Always comparo/v1.", "comparo/v1")]
    metadata: Annotated[
        Meta, _f("Name, id, description, tags.", {"name": "Local", "id": "environment.local"})
    ]
    spec: Annotated[
        EnvironmentSpec, _f("The environment body.", {"baseUrl": "http://localhost:8080"})
    ]


class Request(
    msgspec.Struct, tag_field="kind", tag="Request", rename="camel", forbid_unknown_fields=True
):
    """An HTTP request, optionally matrix-expanded."""

    api_version: Annotated[Literal["comparo/v1"], _f("Always comparo/v1.", "comparo/v1")]
    metadata: Annotated[
        Meta, _f("Name, id, description, tags.", {"name": "Get user", "id": "request.get-user"})
    ]
    spec: Annotated[
        RequestSpec, _f("The request body.", {"request": {"method": "GET", "endpoint": "/users"}})
    ]


class Schema(
    msgspec.Struct, tag_field="kind", tag="Schema", rename="camel", forbid_unknown_fields=True
):
    """A JSON Schema used for structural validation."""

    api_version: Annotated[Literal["comparo/v1"], _f("Always comparo/v1.", "comparo/v1")]
    metadata: Annotated[
        Meta, _f("Name, id, description, tags.", {"name": "User", "id": "schema.user"})
    ]
    spec: Annotated[
        dict[str, Any], _f("The JSON Schema body.", {"type": "object", "required": ["id"]})
    ]


class Instance(
    msgspec.Struct, tag_field="kind", tag="Instance", rename="camel", forbid_unknown_fields=True
):
    """A reusable value injected by reference."""

    api_version: Annotated[Literal["comparo/v1"], _f("Always comparo/v1.", "comparo/v1")]
    metadata: Annotated[
        Meta,
        _f(
            "Name, id, description, tags.",
            {"name": "Default headers", "id": "instance.default-headers"},
        ),
    ]
    spec: Annotated[
        InstanceSpec, _f("The reusable value.", {"value": {"Accept": "application/json"}})
    ]


class Matrix(
    msgspec.Struct, tag_field="kind", tag="Matrix", rename="camel", forbid_unknown_fields=True
):
    """A set of parameter cases a request runs against."""

    api_version: Annotated[Literal["comparo/v1"], _f("Always comparo/v1.", "comparo/v1")]
    metadata: Annotated[
        Meta, _f("Name, id, description, tags.", {"name": "Locales", "id": "matrix.locales"})
    ]
    spec: Annotated[
        MatrixSpec,
        _f("The matrix body.", {"target": "request.query", "values": [{"locale": "en-US"}]}),
    ]


class DiffProfile(
    msgspec.Struct, tag_field="kind", tag="DiffProfile", rename="camel", forbid_unknown_fields=True
):
    """How two responses are compared, per JSON path."""

    api_version: Annotated[Literal["comparo/v1"], _f("Always comparo/v1.", "comparo/v1")]
    metadata: Annotated[
        Meta, _f("Name, id, description, tags.", {"name": "Lenient", "id": "diff.lenient"})
    ]
    spec: Annotated[DiffProfileSpec, _f("The diff profile body.", {"default": "exact"})]


class AssertionProfile(
    msgspec.Struct,
    tag_field="kind",
    tag="AssertionProfile",
    rename="camel",
    forbid_unknown_fields=True,
):
    """A composable set of response assertions, attached to a request or execution."""

    api_version: Annotated[Literal["comparo/v1"], _f("Always comparo/v1.", "comparo/v1")]
    metadata: Annotated[
        Meta, _f("Name, id, description, tags.", {"name": "HTTP OK", "id": "assert.http-ok"})
    ]
    spec: Annotated[
        AssertionProfileSpec,
        _f(
            "The assertion profile body.",
            {"rules": [{"target": "$status", "op": "equals", "value": 200}]},
        ),
    ]


class ExecutionProfile(
    msgspec.Struct,
    tag_field="kind",
    tag="ExecutionProfile",
    rename="camel",
    forbid_unknown_fields=True,
):
    """A named run plan: what to run, which environments, and which checks."""

    api_version: Annotated[Literal["comparo/v1"], _f("Always comparo/v1.", "comparo/v1")]
    metadata: Annotated[
        Meta,
        _f(
            "Name, id, description, tags.", {"name": "Release gate", "id": "execution.release-gate"}
        ),
    ]
    spec: Annotated[
        ExecutionProfileSpec,
        _f(
            "The execution plan body.",
            {"environments": {"baseline": "stable", "candidate": "canary"}},
        ),
    ]


class Project(
    msgspec.Struct, tag_field="kind", tag="Project", rename="camel", forbid_unknown_fields=True
):
    """The root manifest — run-wide defaults."""

    api_version: Annotated[Literal["comparo/v1"], _f("Always comparo/v1.", "comparo/v1")]
    metadata: Annotated[
        Meta, _f("Name and optional description/tags (no id on Project).", {"name": "my-api"})
    ]
    spec: Annotated[ProjectSpec, _f("The manifest body — run-wide defaults.", {"data": ".comparo"})]


#: The tagged union of every object kind, dispatched on the ``kind`` field.
Object = (
    Environment
    | Request
    | Schema
    | Instance
    | Matrix
    | DiffProfile
    | AssertionProfile
    | ExecutionProfile
    | Project
)
