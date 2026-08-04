"""Deterministic JSON Schema generation from the v1alpha1 field catalog."""

from __future__ import annotations

import json

from comparo.core.configuration.v1alpha1.frontend import API_VERSION
from comparo.core.configuration.v1alpha1.operations import ASSERTION_OPERATION_ALIASES
from comparo.core.configuration.v1alpha1.operations import DIFF_COMPARATOR_ALIASES
from comparo.core.configuration.v1alpha1.operations import DIFF_PAIRING_ALIASES
from comparo.core.configuration.v1alpha1.operations import DIFF_REDUCER_ALIASES
from comparo.core.configuration.v1alpha1.operations import DIFF_TRANSFORM_ALIASES
from comparo.core.configuration.v1alpha1.schema import RESOURCE_SCHEMAS
from comparo.core.configuration.v1alpha1.schema import FieldMode
from comparo.core.configuration.v1alpha1.schema import FieldSchema
from comparo.core.configuration.v1alpha1.schema import ResourceSchema

type JsonSchema = dict[str, object]


def _operation_schema(aliases: frozenset[str]) -> JsonSchema:
    names = sorted([*aliases, *(f"comparo.core/{alias}@1" for alias in aliases)])
    return {
        "oneOf": [
            {"enum": names},
            {
                "type": "object",
                "properties": {"name": {"enum": names}},
                "required": ["name"],
                "additionalProperties": {"$ref": "#/$defs/resolvableValue"},
            },
        ]
    }


def _field_schema(field: FieldSchema) -> JsonSchema:
    if field.mode is FieldMode.STATIC:
        return {"$ref": "#/$defs/jsonValue"}
    if field.mode is FieldMode.RESOLVABLE:
        return {"$ref": "#/$defs/resolvableValue"}
    if field.mode is FieldMode.STRUCTURED:
        return _fields_schema(field.fields)
    if field.mode is FieldMode.ASSERTION_RULES:
        return {"$ref": "#/$defs/assertionRules"}
    if field.mode is FieldMode.DIFF_RULES:
        return {"$ref": "#/$defs/diffRules"}
    if field.mode is FieldMode.SCHEMA:
        return {
            "oneOf": [
                {"$ref": "#/$defs/resourceUse"},
                {"$ref": "#/$defs/jsonValue"},
            ]
        }
    binding: JsonSchema = {"$ref": "#/$defs/resourceUse"}
    if field.inline:
        binding = {
            "oneOf": [
                binding,
                {"type": "object"},
            ]
        }
    if field.mode in {FieldMode.REFERENCE_LIST, FieldMode.REFERENCE_UNION_LIST}:
        result: JsonSchema = {"type": "array", "items": binding}
        if field.unique_items:
            result["uniqueItems"] = True
        return result
    return binding


def _fields_schema(fields: tuple[FieldSchema, ...]) -> JsonSchema:
    return {
        "type": "object",
        "properties": {field.name: _field_schema(field) for field in fields},
        "required": [field.name for field in fields if field.required],
        "additionalProperties": False,
    }


def _resource_schema(schema: ResourceSchema) -> JsonSchema:
    if schema.opaque_spec:
        spec: JsonSchema = {"$ref": "#/$defs/jsonValue"}
    else:
        spec = _fields_schema(schema.fields)
    return {
        "type": "object",
        "properties": {
            "apiVersion": {"const": API_VERSION},
            "kind": {"const": schema.kind.value},
            "metadata": {"$ref": "#/$defs/metadata"},
            "spec": spec,
        },
        "required": ["apiVersion", "kind", "metadata", "spec"],
        "additionalProperties": False,
    }


def generate_v1alpha1_schema() -> JsonSchema:
    """Generate the complete public JSON Schema for the supported external version."""
    resource_names = [schema.kind.value for schema in RESOURCE_SCHEMAS]
    definitions: dict[str, object] = {
        "metadata": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$"},
                "name": {"type": "string", "minLength": 1},
                "description": {"type": "string", "minLength": 1},
                "tags": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$",
                    },
                    "uniqueItems": True,
                },
            },
            "required": ["id", "name"],
            "additionalProperties": False,
        },
        "jsonValue": {
            "oneOf": [
                {"type": "null"},
                {"type": "boolean"},
                {"type": "number"},
                {"type": "string"},
                {"type": "array", "items": {"$ref": "#/$defs/jsonValue"}},
                {
                    "type": "object",
                    "additionalProperties": {"$ref": "#/$defs/jsonValue"},
                },
            ]
        },
        "resourceUse": {
            "type": "object",
            "properties": {"$use": {"type": "string", "minLength": 1}},
            "required": ["$use"],
            "additionalProperties": False,
        },
        "lookupArgument": {
            "oneOf": [
                {"type": "string", "minLength": 1},
                {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "as": {
                            "enum": [
                                "boolean",
                                "bytes",
                                "duration",
                                "integer",
                                "json",
                                "number",
                                "size",
                                "string",
                                "yaml",
                            ]
                        },
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            ]
        },
        "valueDirective": {
            "oneOf": [
                {"$ref": "#/$defs/resourceUse"},
                *(
                    {
                        "type": "object",
                        "properties": {name: {"$ref": "#/$defs/lookupArgument"}},
                        "required": [name],
                        "additionalProperties": False,
                    }
                    for name in ("$var", "$secret", "$param", "$env")
                ),
                {
                    "type": "object",
                    "properties": {"$literal": {"$ref": "#/$defs/jsonValue"}},
                    "required": ["$literal"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "$from": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"$ref": "#/$defs/resolvableValue"},
                        }
                    },
                    "required": ["$from"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "$optional": {"$ref": "#/$defs/resolvableValue"},
                    },
                    "required": ["$optional"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "$file": {
                            "oneOf": [
                                {"type": "string", "minLength": 1},
                                {
                                    "type": "object",
                                    "properties": {
                                        "path": {"$ref": "#/$defs/resolvableValue"},
                                        "as": {
                                            "enum": [
                                                "boolean",
                                                "bytes",
                                                "duration",
                                                "integer",
                                                "json",
                                                "number",
                                                "size",
                                                "string",
                                                "yaml",
                                            ]
                                        },
                                        "trim": {"enum": ["none", "start", "end", "both"]},
                                        "maxBytes": {"type": "integer", "minimum": 0},
                                    },
                                    "required": ["path"],
                                    "additionalProperties": False,
                                },
                            ]
                        }
                    },
                    "required": ["$file"],
                    "additionalProperties": False,
                },
            ]
        },
        "applicationObject": {
            "type": "object",
            "propertyNames": {"not": {"pattern": "^\\$"}},
            "additionalProperties": {"$ref": "#/$defs/resolvableValue"},
        },
        "resolvableValue": {
            "oneOf": [
                {"type": "null"},
                {"type": "boolean"},
                {"type": "number"},
                {"type": "string"},
                {"type": "array", "items": {"$ref": "#/$defs/resolvableValue"}},
                {"$ref": "#/$defs/valueDirective"},
                {"$ref": "#/$defs/applicationObject"},
            ]
        },
        "assertionOperation": _operation_schema(ASSERTION_OPERATION_ALIASES),
        "assertionRules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "operator": {"$ref": "#/$defs/assertionOperation"},
                },
                "required": ["operator"],
                "additionalProperties": {"$ref": "#/$defs/resolvableValue"},
            },
        },
        "diffComparator": _operation_schema(DIFF_COMPARATOR_ALIASES),
        "diffPairing": _operation_schema(DIFF_PAIRING_ALIASES),
        "diffReducer": _operation_schema(DIFF_REDUCER_ALIASES),
        "diffTransform": _operation_schema(DIFF_TRANSFORM_ALIASES),
        "diffRules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "comparator": {"$ref": "#/$defs/diffComparator"},
                    "pairing": {"$ref": "#/$defs/diffPairing"},
                    "reducer": {"$ref": "#/$defs/diffReducer"},
                    "transforms": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/diffTransform"},
                    },
                },
                "required": ["comparator"],
                "additionalProperties": {"$ref": "#/$defs/resolvableValue"},
            },
        },
    }
    definitions.update({schema.kind.value: _resource_schema(schema) for schema in RESOURCE_SCHEMAS})
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://comparo.dev/schemas/comparo-v1alpha1.schema.json",
        "title": "Comparo comparo/v1alpha1 resource",
        "oneOf": [{"$ref": f"#/$defs/{name}"} for name in resource_names],
        "$defs": definitions,
    }


def render_v1alpha1_schema() -> str:
    """Render canonical checked-in schema text with a stable trailing newline."""
    return json.dumps(generate_v1alpha1_schema(), indent=2, sort_keys=True) + "\n"
