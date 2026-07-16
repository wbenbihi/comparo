"""Typed models for comparo configuration objects.

Every object shares a Kubernetes-style envelope (``apiVersion`` / ``kind`` /
``metadata`` / ``spec``) and is decoded with :mod:`msgspec`. Framework structs
forbid unknown fields so a mistyped key is a hard error; payload and freeform
positions (a request body, an instance value, a JSON Schema) are typed ``Any``
and validated later by the milestones that consume them.
"""

from typing import Annotated
from typing import Any
from typing import Literal

import msgspec

API_VERSION = "comparo/v1"

#: A duration written as a readable string, e.g. ``"5s"``, ``"500ms"``, ``"2m"``.
DurationStr = Annotated[str, msgspec.Meta(pattern=r"^[0-9]+(ms|s|m|h)$")]


class Duration(msgspec.Struct, rename="camel", forbid_unknown_fields=True, frozen=True):
    """A split timeout budget; an omitted field falls back to a built-in default."""

    connect: DurationStr | None = None
    read: DurationStr | None = None
    stream_idle: DurationStr | None = None


class Meta(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """Object metadata. Every kind except ``Project`` declares an ``id``."""

    name: str
    id: str | None = None
    description: str | None = None
    tags: list[str] | None = None


class Header(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """A single HTTP header. ``value`` may be a literal or a reference hole."""

    key: str
    value: Any = None
    description: str | None = None
    required: bool = False
    type: str | None = None


class HealthCheck(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """A request used to probe an environment's readiness."""

    method: str
    endpoint: str
    headers: list[Header] | None = None


class EnvironmentSpec(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The body of an ``Environment`` object."""

    base_url: str
    timeout: Duration | None = None
    secrets: dict[str, Any] | None = None
    variables: dict[str, str] | None = None
    headers: list[Header] | None = None
    health: list[HealthCheck] | None = None


class HttpRequest(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The outbound HTTP request a ``Request`` object describes."""

    method: str
    endpoint: str
    query: dict[str, Any] | None = None
    headers: Any = None
    body: Any = None


class Response(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The expected response of a ``Request`` object."""

    status: int | None = None
    schema: Any = None
    diff: Any = None
    streaming: bool | None = None


class RequestSpec(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The body of a ``Request`` object."""

    request: HttpRequest
    response: Response | None = None
    matrix: list[Any] | None = None
    timeout: Duration | None = None


class InstanceSpec(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The body of an ``Instance`` object — a single reusable value."""

    value: Any = None


class MatrixSpec(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The body of a ``Matrix`` object — a list of atomic cases."""

    target: str
    values: list[dict[str, Any]]
    mode: Literal["merge", "replace"] = "merge"
    create_path: bool = False


class DiffRule(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """One path-scoped comparison rule inside a ``DiffProfile``."""

    path: str
    mode: str
    schema: Any = None
    array_length: str | None = None
    tolerance: float | None = None


class DiffProfileSpec(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The body of a ``DiffProfile`` object."""

    default: str
    rules: list[DiffRule] | None = None


class ProjectSpec(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The body of the root ``Project`` manifest."""

    data: str | None = None
    environments: Any = None
    run: Any = None
    diff: Any = None
    selection: Any = None
    report: Any = None
    redaction: Any = None
    plugins: Any = None


class AssertionRule(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """One response assertion: a target, an operator, and an expected value."""

    target: str
    op: str
    value: Any = None
    severity: Literal["error", "warn"] = "error"


class AssertionProfileSpec(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The body of an ``AssertionProfile`` — composable response assertions."""

    rules: list[AssertionRule] | None = None
    include: list[Any] | None = None


class MatrixScope(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """Per-execution matrix customization: which cases to run, add, or drop."""

    include: list[dict[str, Any]] | None = None
    exclude: list[dict[str, Any]] | None = None
    override: list[dict[str, Any]] | None = None


class ExecutionSelect(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """Which requests an execution runs — by tag and/or explicit id."""

    tags: list[str] | None = None
    requests: list[str] | None = None


class ExecutionEnvironments(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The environments to run against; a ``candidate`` enables the diff."""

    baseline: str | None = None
    candidate: str | None = None


class ExecutionCheck(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """Which checks an execution computes."""

    assertions: bool = True
    diff: bool = True


class ExecutionProfileSpec(msgspec.Struct, rename="camel", forbid_unknown_fields=True):
    """The body of an ``ExecutionProfile`` — what to run, where, and which checks."""

    select: ExecutionSelect | None = None
    matrix: dict[str, MatrixScope] | None = None
    environments: ExecutionEnvironments | None = None
    check: ExecutionCheck | None = None
    profiles: Any = None
    report: Any = None


class Environment(
    msgspec.Struct, tag_field="kind", tag="Environment", rename="camel", forbid_unknown_fields=True
):
    """A target environment."""

    api_version: Literal["comparo/v1"]
    metadata: Meta
    spec: EnvironmentSpec


class Request(
    msgspec.Struct, tag_field="kind", tag="Request", rename="camel", forbid_unknown_fields=True
):
    """An HTTP request, optionally matrix-expanded."""

    api_version: Literal["comparo/v1"]
    metadata: Meta
    spec: RequestSpec


class Schema(
    msgspec.Struct, tag_field="kind", tag="Schema", rename="camel", forbid_unknown_fields=True
):
    """A JSON Schema used for structural validation."""

    api_version: Literal["comparo/v1"]
    metadata: Meta
    spec: dict[str, Any]


class Instance(
    msgspec.Struct, tag_field="kind", tag="Instance", rename="camel", forbid_unknown_fields=True
):
    """A reusable value injected by reference."""

    api_version: Literal["comparo/v1"]
    metadata: Meta
    spec: InstanceSpec


class Matrix(
    msgspec.Struct, tag_field="kind", tag="Matrix", rename="camel", forbid_unknown_fields=True
):
    """A set of parameter cases a request runs against."""

    api_version: Literal["comparo/v1"]
    metadata: Meta
    spec: MatrixSpec


class DiffProfile(
    msgspec.Struct, tag_field="kind", tag="DiffProfile", rename="camel", forbid_unknown_fields=True
):
    """How two responses are compared, per JSON path."""

    api_version: Literal["comparo/v1"]
    metadata: Meta
    spec: DiffProfileSpec


class AssertionProfile(
    msgspec.Struct,
    tag_field="kind",
    tag="AssertionProfile",
    rename="camel",
    forbid_unknown_fields=True,
):
    """A composable set of response assertions, attached to a request or execution."""

    api_version: Literal["comparo/v1"]
    metadata: Meta
    spec: AssertionProfileSpec


class ExecutionProfile(
    msgspec.Struct,
    tag_field="kind",
    tag="ExecutionProfile",
    rename="camel",
    forbid_unknown_fields=True,
):
    """A named run plan: what to run, which environments, and which checks."""

    api_version: Literal["comparo/v1"]
    metadata: Meta
    spec: ExecutionProfileSpec


class Project(
    msgspec.Struct, tag_field="kind", tag="Project", rename="camel", forbid_unknown_fields=True
):
    """The root manifest — run-wide defaults."""

    api_version: Literal["comparo/v1"]
    metadata: Meta
    spec: ProjectSpec


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

#: Kinds that must declare ``metadata.id`` (every kind except the root manifest).
REFERENCEABLE_KINDS = (
    "Environment",
    "Request",
    "Schema",
    "Instance",
    "Matrix",
    "DiffProfile",
    "AssertionProfile",
    "ExecutionProfile",
)
