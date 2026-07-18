"""Scaffold a comparo project from an OpenAPI 3.x document.

Parsing an external spec format is an adapter concern (like httpx), so it lives
here rather than in :mod:`comparo.core`. This module reads an OpenAPI 3.0/3.1
document and turns its *mechanical* parts into ``comparo/v1`` objects:

- ``servers`` become :class:`~comparo.core.models.Environment` objects;
- each ``paths`` operation becomes a :class:`~comparo.core.models.Request`;
- ``components.schemas`` become :class:`~comparo.core.models.Schema` objects;
- ``securitySchemes`` become ``$secret``-backed auth stubs on every environment.

It is deliberately a **scaffold, not a finished project**: no ``DiffProfile`` is
generated (which fields are volatile is the user's judgement) and a credential is
*never* written — auth always resolves to a ``$secret`` declared under the
environment and sourced from ``$env`` with a placeholder variable name. The
result loads and validates immediately; the user then refines it.

The adapter only builds objects and serialises them; writing the project tree is
the CLI's job. Every emitted document is validated against the real object models
before it is returned, so a mistyped field is a hard error here, not at load.
"""

from __future__ import annotations

import dataclasses
import io
import json
import re
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

import msgspec
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from comparo.core.models import API_VERSION
from comparo.core.models import Object

#: HTTP methods an OpenAPI path item may declare, in a stable emit order.
_HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")

#: The base URL used when a spec declares no usable server.
_PLACEHOLDER_BASE_URL = "https://example.com"

#: Recursion guard for schema-derived example stubs.
_MAX_EXAMPLE_DEPTH = 6

#: Representative values for a few common ``string`` formats, for nicer stubs.
_STRING_FORMATS = {
    "date-time": "2020-01-01T00:00:00Z",
    "date": "2020-01-01",
    "time": "00:00:00",
    "uuid": "00000000-0000-0000-0000-000000000000",
    "email": "user@example.com",
    "uri": "https://example.com",
    "hostname": "example.com",
    "ipv4": "127.0.0.1",
}


class OpenApiImportError(Exception):
    """Raised when a document is not a usable OpenAPI 3.x specification."""


@dataclasses.dataclass(frozen=True, slots=True)
class ImportedObject:
    """One scaffolded comparo object: its id, kind, and serialisable document."""

    id: str
    kind: str
    document: dict[str, Any]


@dataclasses.dataclass(frozen=True, slots=True)
class ImportResult:
    """Everything an import produced, ready to be written to a project tree."""

    project_name: str
    environments: list[ImportedObject]
    requests: list[ImportedObject]
    schemas: list[ImportedObject]
    #: Placeholder environment-variable names the declared secrets read from.
    secret_env_vars: list[str]

    @property
    def default_environment(self) -> str:
        """The short id-segment of the first environment, for ``environments.default``."""
        return self.environments[0].id.split(".", 1)[-1]


def load_spec(text: str) -> dict[str, Any]:
    """Parse an OpenAPI document supplied as JSON or YAML text.

    JSON is tried first (every JSON document is also valid YAML, but the JSON
    parser is stricter and faster); on failure the project's YAML parser is used.

    Args:
        text: The raw spec document.

    Returns:
        The parsed spec as a mapping.

    Raises:
        OpenApiImportError: If the text parses as neither JSON nor YAML, or the
            top-level value is not a mapping.
    """
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError:
        try:
            parsed = YAML(typ="safe").load(text)
        except YAMLError as error:
            message = f"could not parse the spec as JSON or YAML: {error}"
            raise OpenApiImportError(message) from error
    if not isinstance(parsed, dict):
        raise OpenApiImportError("the spec is not a mapping — expected an OpenAPI document")
    return parsed


def import_openapi(spec: Mapping[str, Any], *, name: str | None = None) -> ImportResult:
    """Turn a parsed OpenAPI 3.x document into scaffolded comparo objects.

    Args:
        spec: A parsed OpenAPI document (see :func:`load_spec`).
        name: An explicit project name; falls back to ``info.title``.

    Returns:
        The environments, requests, schemas, and declared secret names to write.

    Raises:
        OpenApiImportError: If the document is Swagger 2.0, is not OpenAPI 3.x, or
            produces an object that fails to validate against the comparo models.
    """
    version = _require_openapi_3(spec)
    project_name = _project_name(spec, name)

    auth, secrets, headers = _security(spec)
    schemas, schema_ids = _schemas(spec)
    environments = _environments(spec, auth, secrets, headers)
    requests = _requests(spec, schema_ids)

    result = ImportResult(
        project_name=project_name,
        environments=environments,
        requests=requests,
        schemas=schemas,
        secret_env_vars=sorted(secrets),
    )
    for obj in (*environments, *requests, *schemas):
        _validate(obj, version)
    return result


def to_yaml(document: Mapping[str, Any]) -> str:
    """Serialise one object document to comparo's block-style YAML.

    Args:
        document: A single object document (envelope + spec) as plain builtins.

    Returns:
        The YAML text, without the editor schema modeline (the CLI prepends it).
    """
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    stream = io.StringIO()
    yaml.dump(dict(document), stream)
    return stream.getvalue()


# ── Envelope / validation ────────────────────────────────────────────────────


def _require_openapi_3(spec: Mapping[str, Any]) -> str:
    """Return the OpenAPI version, rejecting Swagger 2.0 and non-3.x documents."""
    if "swagger" in spec:
        raise OpenApiImportError(
            "Swagger 2.0 is not supported — convert it to OpenAPI 3.x first "
            "(e.g. with swagger2openapi) and import that"
        )
    version = spec.get("openapi")
    if not isinstance(version, str) or not version.startswith("3."):
        raise OpenApiImportError(
            "not an OpenAPI 3.x document — expected a top-level `openapi: 3.x` version"
        )
    return version


def _validate(obj: ImportedObject, version: str) -> None:
    """Assert a scaffolded document decodes as a real comparo object."""
    try:
        msgspec.convert(obj.document, type=Object, strict=True)
    except msgspec.ValidationError as error:  # pragma: no cover - defensive
        message = (
            f"failed to build a valid {obj.kind} for '{obj.id}' from the OpenAPI "
            f"{version} spec: {error}"
        )
        raise OpenApiImportError(message) from error


def _project_name(spec: Mapping[str, Any], name: str | None) -> str:
    """Resolve the project name from the flag, then ``info.title``, then a default."""
    if name is not None and name.strip():
        return name.strip()
    info = spec.get("info")
    title = info.get("title") if isinstance(info, dict) else None
    if isinstance(title, str) and title.strip():
        return title.strip()
    return "imported-api"


# ── Environments ─────────────────────────────────────────────────────────────


def _environments(
    spec: Mapping[str, Any],
    auth: Any,
    secrets: dict[str, Any],
    headers: list[dict[str, Any]],
) -> list[ImportedObject]:
    """Build one Environment per server (or a single placeholder when there are none).

    Every environment carries the same auth stub, secrets, and header set so a
    request stays environment-agnostic and a diff pair compares like for like.
    """
    servers = spec.get("servers")
    entries: Sequence[Any] = servers if isinstance(servers, list) and servers else [None]
    result: list[ImportedObject] = []
    used: set[str] = set()
    for index, server in enumerate(entries, start=1):
        if isinstance(server, dict):
            base_url = _server_base_url(server)
            description = server.get("description")
            if isinstance(description, str) and description.strip():
                slug, display = _slug(description), description.strip()
            else:
                slug, display = f"env-{index}", f"Environment {index}"
        else:
            base_url, slug, display = _PLACEHOLDER_BASE_URL, f"env-{index}", f"Environment {index}"
        slug = _unique(slug, used)
        environment_id = f"environment.{slug}"

        body: dict[str, Any] = {"baseUrl": base_url}
        if headers:
            body["headers"] = [dict(header) for header in headers]
        if secrets:
            body["secrets"] = {key: dict(source) for key, source in secrets.items()}
        if auth is not None:
            body["auth"] = auth
        document = {
            "apiVersion": API_VERSION,
            "kind": "Environment",
            "metadata": {"name": display, "id": environment_id},
            "spec": body,
        }
        result.append(ImportedObject(environment_id, "Environment", document))
    return result


def _server_base_url(server: Mapping[str, Any]) -> str:
    """Resolve a server ``url`` to a concrete base URL.

    Template variables (``{region}``) are filled from each variable's ``default``,
    a trailing slash is dropped, and a relative or scheme-less URL is anchored to
    the placeholder host so the environment is still runnable.
    """
    url = str(server.get("url") or "").strip()
    variables = server.get("variables")
    if isinstance(variables, dict):
        for var_name, variable in variables.items():
            if isinstance(variable, dict) and "default" in variable:
                url = url.replace("{" + str(var_name) + "}", str(variable["default"]))
    url = url.rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = _PLACEHOLDER_BASE_URL + url if url.startswith("/") else _PLACEHOLDER_BASE_URL
    return url or _PLACEHOLDER_BASE_URL


# ── Security → auth stubs + secrets ──────────────────────────────────────────


def _security(spec: Mapping[str, Any]) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
    """Translate ``securitySchemes`` into an auth stub, secrets, and headers.

    Returns a ``(auth, secrets, headers)`` triple. ``auth`` is the first
    Basic/Bearer (or OAuth/OIDC) scheme mapped to comparo's first-class ``auth``
    block; an ``apiKey`` header scheme becomes an environment header. Every
    credential is a ``$secret`` reference; the secret is declared under the
    environment and sourced from ``$env`` with a placeholder variable name — no
    real value is ever emitted.
    """
    schemes = _components(spec).get("securitySchemes")
    auth: Any = None
    secrets: dict[str, Any] = {}
    headers: list[dict[str, Any]] = []
    if not isinstance(schemes, dict):
        return auth, secrets, headers
    for scheme in schemes.values():
        if not isinstance(scheme, dict):
            continue
        scheme_type = str(scheme.get("type", "")).lower()
        if scheme_type == "http" and str(scheme.get("scheme", "")).lower() == "basic":
            secrets.setdefault("API_PASSWORD", {"$env": "API_PASSWORD"})
            if auth is None:
                auth = {"basic": {"username": "${API_USERNAME}", "password": "${API_PASSWORD}"}}
        elif scheme_type in ("http", "oauth2", "openidconnect"):
            secrets.setdefault("API_TOKEN", {"$env": "API_TOKEN"})
            if auth is None:
                auth = {"bearer": "${API_TOKEN}"}
        elif scheme_type == "apikey":
            secrets.setdefault("API_KEY", {"$env": "API_KEY"})
            if str(scheme.get("in", "")).lower() == "header":
                header_name = str(scheme.get("name") or "X-API-Key")
                headers.append({"key": header_name, "value": "${API_KEY}"})
    return auth, secrets, headers


# ── Schemas ──────────────────────────────────────────────────────────────────


def _schemas(spec: Mapping[str, Any]) -> tuple[list[ImportedObject], dict[str, str]]:
    """Wrap each ``components.schemas`` entry in a comparo Schema envelope.

    Internal JSON-Schema ``$ref``s are kept intact — comparo leaves ``#/…``
    pointers untouched. Returns the objects plus a map from OpenAPI component
    name to comparo schema id, so a response can reference the right Schema.
    """
    schemas = _components(spec).get("schemas")
    result: list[ImportedObject] = []
    ids: dict[str, str] = {}
    used: set[str] = set()
    if not isinstance(schemas, dict):
        return result, ids
    for component_name, schema in schemas.items():
        slug = _unique(_slug(str(component_name)), used)
        schema_id = f"schema.{slug}"
        ids[str(component_name)] = schema_id
        document = {
            "apiVersion": API_VERSION,
            "kind": "Schema",
            "metadata": {"name": str(component_name), "id": schema_id},
            "spec": schema if isinstance(schema, dict) else {},
        }
        result.append(ImportedObject(schema_id, "Schema", document))
    return result, ids


# ── Requests ─────────────────────────────────────────────────────────────────


def _requests(spec: Mapping[str, Any], schema_ids: Mapping[str, str]) -> list[ImportedObject]:
    """Build one Request per method+operation across every path."""
    paths = spec.get("paths")
    result: list[ImportedObject] = []
    used: set[str] = set()
    if not isinstance(paths, dict):
        return result
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        shared = item.get("parameters")
        shared_params = shared if isinstance(shared, list) else []
        for method in _HTTP_METHODS:
            operation = item.get(method)
            if isinstance(operation, dict):
                result.append(
                    _operation(method, str(path), operation, shared_params, spec, schema_ids, used)
                )
    return result


def _operation(
    method: str,
    path: str,
    operation: Mapping[str, Any],
    shared_params: Sequence[Any],
    spec: Mapping[str, Any],
    schema_ids: Mapping[str, str],
    used: set[str],
) -> ImportedObject:
    """Build a single Request from one path operation."""
    operation_id = operation.get("operationId")
    if isinstance(operation_id, str) and operation_id.strip():
        slug, operation_name = _slug(operation_id), operation_id.strip()
    else:
        slug, operation_name = _slug(f"{method}-{path}"), None
    slug = _unique(slug, used)
    request_id = f"request.{slug}"

    summary = operation.get("summary")
    if isinstance(summary, str) and summary.strip():
        name = summary.strip()
    elif operation_name is not None:
        name = operation_name
    else:
        name = f"{method.upper()} {path}"

    metadata: dict[str, Any] = {"name": name, "id": request_id}
    description = operation.get("description")
    if isinstance(description, str) and description.strip():
        metadata["description"] = description.strip()
    tags = operation.get("tags")
    if isinstance(tags, list):
        clean = [str(tag) for tag in tags if isinstance(tag, str)]
        if clean:
            metadata["tags"] = clean

    request: dict[str, Any] = {"method": method.upper(), "endpoint": path}
    query = _query(shared_params, operation.get("parameters"), spec)
    if query:
        request["query"] = query
    body = _request_body(operation.get("requestBody"), spec)
    if body is not None:
        request["body"] = body

    spec_body: dict[str, Any] = {"request": request}
    response = _response(operation.get("responses"), spec, schema_ids)
    if response:
        spec_body["response"] = response

    document = {
        "apiVersion": API_VERSION,
        "kind": "Request",
        "metadata": metadata,
        "spec": spec_body,
    }
    return ImportedObject(request_id, "Request", document)


def _query(
    shared_params: Sequence[Any], operation_params: Any, spec: Mapping[str, Any]
) -> dict[str, Any]:
    """Collect ``in: query`` parameters into a ``name -> example`` map."""
    query: dict[str, Any] = {}
    for param in _merged_params(shared_params, operation_params, spec):
        if str(param.get("in", "")).lower() != "query":
            continue
        name = param.get("name")
        if isinstance(name, str) and name:
            query[name] = _query_value(param, spec)
    return query


def _merged_params(
    shared_params: Sequence[Any], operation_params: Any, spec: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Merge path-item and operation parameters; an operation param wins by (name, in)."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    operation_list = operation_params if isinstance(operation_params, list) else []
    for param in (*shared_params, *operation_list):
        resolved = _deref(param, spec)
        if not isinstance(resolved, dict):
            continue
        key = (str(resolved.get("name", "")), str(resolved.get("in", "")))
        merged[key] = resolved
    return list(merged.values())


def _query_value(param: Mapping[str, Any], spec: Mapping[str, Any]) -> Any:
    """Pick an example value for a query parameter."""
    if "example" in param:
        return param["example"]
    schema = _deref(param.get("schema"), spec)
    return _example(schema if isinstance(schema, dict) else {}, spec, frozenset(), 0)


def _request_body(request_body: Any, spec: Mapping[str, Any]) -> Any:
    """Derive a JSON request-body stub from an example or the body schema."""
    resolved = _deref(request_body, spec)
    if not isinstance(resolved, dict):
        return None
    media = _json_media(resolved.get("content"))
    if media is None:
        return None
    if "example" in media:
        return media["example"]
    examples = media.get("examples")
    if isinstance(examples, dict):
        for example in examples.values():
            if isinstance(example, dict) and "value" in example:
                return example["value"]
    schema = media.get("schema")
    if isinstance(schema, dict):
        return _example(schema, spec, frozenset(), 0)
    return None


def _response(
    responses: Any, spec: Mapping[str, Any], schema_ids: Mapping[str, str]
) -> dict[str, Any]:
    """Build the expected-response block from the first 2xx response."""
    if not isinstance(responses, dict):
        return {}
    status, response = _first_success(responses)
    body: dict[str, Any] = {}
    if status is not None:
        body["status"] = status
    resolved = _deref(response, spec)
    if isinstance(resolved, dict):
        schema_id = _response_schema_id(resolved, schema_ids)
        if schema_id is not None:
            body["schema"] = {"$ref": schema_id}
    return body


def _first_success(responses: Mapping[str, Any]) -> tuple[int | None, Any]:
    """Return the lowest 2xx ``(status, response)`` pair, or ``(None, None)``.

    Status keys are matched by numeric value, so a YAML spec whose ``200:`` key
    parsed to an integer is handled the same as a JSON string key.
    """
    best_status: int | None = None
    best_key: Any = None
    for code in responses:
        try:
            value = int(str(code))
        except ValueError:
            continue
        if 200 <= value < 300 and (best_status is None or value < best_status):
            best_status, best_key = value, code
    if best_key is None:
        return None, None
    return best_status, responses[best_key]


def _response_schema_id(response: Mapping[str, Any], schema_ids: Mapping[str, str]) -> str | None:
    """Map a 2xx response whose JSON body is a component ``$ref`` to a Schema id."""
    media = _json_media(response.get("content"))
    if not isinstance(media, dict):
        return None
    schema = media.get("schema")
    if not (isinstance(schema, dict) and set(schema) == {"$ref"}):
        return None
    component = _component_name(schema["$ref"], "schemas")
    return schema_ids.get(component) if component is not None else None


# ── Schema-derived example stubs ─────────────────────────────────────────────


def _example(
    schema: Mapping[str, Any], spec: Mapping[str, Any], seen: frozenset[str], depth: int
) -> Any:
    """Build a representative example value from a JSON Schema.

    Recursion is bounded by ``_MAX_EXAMPLE_DEPTH`` and by a ``seen`` set of
    followed ``$ref``s, so a self-referential schema terminates.
    """
    if not isinstance(schema, dict) or depth > _MAX_EXAMPLE_DEPTH:
        return None
    if "example" in schema:
        return schema["example"]
    ref = schema.get("$ref")
    if isinstance(ref, str):
        if ref in seen:
            return None
        target = _resolve_pointer(spec, ref)
        return _example(target, spec, seen | {ref}, depth + 1) if isinstance(target, dict) else None
    if "default" in schema:
        return schema["default"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    for combiner in ("allOf", "oneOf", "anyOf"):
        options = schema.get(combiner)
        if isinstance(options, list) and options and isinstance(options[0], dict):
            return _example(options[0], spec, seen, depth + 1)
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((item for item in schema_type if item != "null"), None)
    if schema_type == "object" or "properties" in schema:
        properties = schema.get("properties")
        result: dict[str, Any] = {}
        if isinstance(properties, dict):
            for prop_name, prop_schema in properties.items():
                child = prop_schema if isinstance(prop_schema, dict) else {}
                result[str(prop_name)] = _example(child, spec, seen, depth + 1)
        return result
    if schema_type == "array":
        items = schema.get("items")
        return [_example(items, spec, seen, depth + 1)] if isinstance(items, dict) else []
    if schema_type == "string":
        fmt = schema.get("format")
        return _STRING_FORMATS.get(fmt, "string") if isinstance(fmt, str) else "string"
    if schema_type == "integer":
        return 0
    if schema_type == "number":
        return 0
    if schema_type == "boolean":
        return True
    return None


# ── Small helpers ────────────────────────────────────────────────────────────


def _components(spec: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return ``components`` as a mapping (empty when absent)."""
    components = spec.get("components")
    return components if isinstance(components, dict) else {}


def _json_media(content: Any) -> dict[str, Any] | None:
    """Pick the JSON media-type object from a ``content`` map."""
    if not isinstance(content, dict):
        return None
    for media_type, media in content.items():
        name = str(media_type).lower()
        if isinstance(media, dict) and (name == "application/json" or name.endswith("+json")):
            return media
    for media_type, media in content.items():
        if isinstance(media, dict) and "json" in str(media_type).lower():
            return media
    return None


def _deref(node: Any, spec: Mapping[str, Any]) -> Any:
    """Follow a single local ``{$ref: '#/...'}`` one level; pass anything else through."""
    if isinstance(node, dict) and set(node) == {"$ref"} and isinstance(node["$ref"], str):
        target = _resolve_pointer(spec, node["$ref"])
        if isinstance(target, dict):
            return target
    return node


def _resolve_pointer(spec: Mapping[str, Any], ref: str) -> Any:
    """Resolve a local JSON pointer (``#/a/b/c``); return ``None`` if it dangles."""
    if not ref.startswith("#/"):
        return None
    node: Any = spec
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, Mapping) and token in node:
            node = node[token]
        else:
            return None
    return node


def _component_name(ref: Any, kind: str) -> str | None:
    """Extract ``X`` from ``#/components/<kind>/X``."""
    if not isinstance(ref, str):
        return None
    prefix = f"#/components/{kind}/"
    return ref[len(prefix) :] if ref.startswith(prefix) else None


def _slug(text: str) -> str:
    """Lowercase *text* into a URL/id-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "item"


def _unique(slug: str, used: set[str]) -> str:
    """Return *slug*, suffixing ``-2``, ``-3``… until it is unused, and reserve it."""
    candidate = slug
    counter = 2
    while candidate in used:
        candidate = f"{slug}-{counter}"
        counter += 1
    used.add(candidate)
    return candidate
