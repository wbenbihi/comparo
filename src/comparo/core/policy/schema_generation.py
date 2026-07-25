"""Deterministic JSON Schema for the independent ExecutionPolicy API."""

from __future__ import annotations

import json

from comparo.core.domain.http.url import HttpScheme
from comparo.core.policy.models import PLANNING_RESOURCES
from comparo.core.policy.models import POLICY_API_VERSION
from comparo.core.policy.models import NetworkAddressClass

type JsonSchema = dict[str, object]


def _strict_object(
    properties: dict[str, object],
    *,
    required: tuple[str, ...],
) -> JsonSchema:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _string_list(*, pattern: str, maximum: int | None = None) -> JsonSchema:
    item: JsonSchema = {"type": "string", "minLength": 1, "pattern": pattern}
    if maximum is not None:
        item["maxLength"] = maximum
    return {"type": "array", "items": item, "uniqueItems": True}


def generate_policy_schema() -> JsonSchema:
    """Generate the complete strict comparo.policy/v1alpha1 JSON Schema."""
    metadata = _strict_object(
        {
            "id": {
                "type": "string",
                "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$",
                "maxLength": 63,
            },
            "name": {"type": "string", "minLength": 1},
            "description": {"type": "string", "minLength": 1},
            "tags": _string_list(pattern="^[a-z0-9]+(?:-[a-z0-9]+)*$"),
        },
        required=("id", "name"),
    )
    planning = _strict_object(
        {
            "subjects": _string_list(
                pattern="^(?:Request|Scenario)/[a-z0-9]+(?:-[a-z0-9]+)*$",
                maximum=72,
            ),
            "environments": _string_list(
                pattern="^Environment/[a-z0-9]+(?:-[a-z0-9]+)*$",
                maximum=75,
            ),
            "allowAnySubjects": {"type": "boolean"},
            "allowAnyEnvironments": {"type": "boolean"},
            "ceilings": _strict_object(
                {
                    resource.value: {"type": "integer", "minimum": 1}
                    for resource in PLANNING_RESOURCES
                },
                required=(),
            ),
        },
        required=(
            "subjects",
            "environments",
            "allowAnySubjects",
            "allowAnyEnvironments",
        ),
    )
    http_fields = (
        "allowedSchemes",
        "allowedMethods",
        "allowedHosts",
        "allowedPorts",
        "allowedAddressClasses",
        "allowAnyHost",
        "requireMutationApproval",
        "allowProxyDns",
    )
    http = _strict_object(
        {
            "allowedSchemes": {
                "type": "array",
                "items": {"enum": [item.value for item in HttpScheme]},
                "minItems": 1,
                "uniqueItems": True,
            },
            "allowedMethods": {
                "type": "array",
                "items": {
                    "type": "string",
                    "pattern": "^[!#$%&'*+.^_`|~0-9A-Z-]+$",
                },
                "minItems": 1,
                "uniqueItems": True,
            },
            "allowedHosts": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 253},
                "uniqueItems": True,
            },
            "allowedPorts": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1, "maximum": 65_535},
                "minItems": 1,
                "uniqueItems": True,
            },
            "allowedAddressClasses": {
                "type": "array",
                "items": {"enum": [item.value for item in NetworkAddressClass]},
                "minItems": 1,
                "uniqueItems": True,
            },
            "allowAnyHost": {"type": "boolean"},
            "requireMutationApproval": {"type": "boolean"},
            "allowProxyDns": {"type": "boolean"},
        },
        required=http_fields,
    )
    allowlist = _strict_object(
        {
            "names": _string_list(pattern="^[A-Za-z_][A-Za-z0-9_]*$", maximum=255),
            "allowAny": {"type": "boolean"},
        },
        required=("names", "allowAny"),
    )
    remote_schemas = _strict_object(
        {
            "sources": {
                "type": "array",
                "items": {
                    "type": "string",
                    "pattern": "^https?://",
                    "maxLength": 2048,
                },
                "uniqueItems": True,
            },
            "allowAny": {"type": "boolean"},
        },
        required=("sources", "allowAny"),
    )
    values = _strict_object(
        {
            "ambientEnvironment": allowlist,
            "allowProjectFiles": {"type": "boolean"},
            "remoteSchemas": remote_schemas,
        },
        required=("ambientEnvironment", "allowProjectFiles", "remoteSchemas"),
    )
    artifacts = _strict_object(
        {
            "allowSanitizedPersistence": {"type": "boolean"},
            "roots": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 4096},
                "uniqueItems": True,
            },
        },
        required=("allowSanitizedPersistence", "roots"),
    )
    spec = _strict_object(
        {
            "planning": planning,
            "http": http,
            "values": values,
            "artifacts": artifacts,
        },
        required=("planning", "http", "values", "artifacts"),
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://comparo.dev/schemas/comparo-policy-v1alpha1.schema.json",
        "title": "Comparo comparo.policy/v1alpha1 ExecutionPolicy",
        **_strict_object(
            {
                "apiVersion": {"const": POLICY_API_VERSION},
                "kind": {"const": "ExecutionPolicy"},
                "metadata": metadata,
                "spec": spec,
            },
            required=("apiVersion", "kind", "metadata", "spec"),
        ),
    }


def render_policy_schema() -> str:
    """Render canonical checked-in-ready schema text with a trailing newline."""
    return json.dumps(generate_policy_schema(), indent=2, sort_keys=True) + "\n"


__all__ = ("generate_policy_schema", "render_policy_schema")
