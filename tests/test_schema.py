"""Tests for the comparo/v1 JSON Schema."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import msgspec

from comparo.core.loader import load_project
from comparo.core.schema import json_schema

SCHEMA_FILE = Path(__file__).parent.parent / "schema" / "comparo-v1.schema.json"
SAMPLE = Path(__file__).parent.parent / "examples" / "sample-project"

_KINDS = (
    "Environment",
    "Request",
    "Schema",
    "Instance",
    "Matrix",
    "DiffProfile",
    "AssertionProfile",
    "ExecutionProfile",
    "Project",
)


def test_schema_has_every_kind_and_a_discriminator() -> None:
    schema = json_schema()
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["title"] == "comparo/v1"
    assert "discriminator" in schema  # dispatched on `kind`
    for kind in _KINDS:
        assert kind in schema["$defs"], f"{kind} missing from the schema"


def _plain(value: object) -> object:
    # The loader keeps ruamel scalar subclasses (FoldedScalarString, ...) that
    # msgspec treats as foreign; coerce them back to plain builtins for encoding.
    if isinstance(value, str):
        return str(value)
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    raise NotImplementedError(type(value))


def test_schema_validates_every_real_example_object() -> None:
    validator = jsonschema.Draft202012Validator(json_schema())
    project = load_project(SAMPLE)
    checked = 0
    for obj in project.objects.values():
        # to the camelCase document a user writes (ruamel scalars → plain builtins).
        document = msgspec.to_builtins(obj, enc_hook=_plain)
        errors = list(validator.iter_errors(document))
        assert not errors, f"{type(obj).__name__}: {errors[0].message if errors else ''}"
        checked += 1
    assert checked >= 3  # it actually validated something


def test_schema_rejects_an_unknown_kind() -> None:
    validator = jsonschema.Draft202012Validator(json_schema())
    bogus = {"apiVersion": "comparo/v1", "kind": "Nonsense", "metadata": {"id": "x"}, "spec": {}}
    assert list(validator.iter_errors(bogus))  # not a valid kind → rejected


def test_shipped_schema_file_is_up_to_date() -> None:
    # `comparo schema -o schema/comparo-v1.schema.json` must be committed so editors
    # and agents get the current surface. Regenerate the file if this fails.
    on_disk = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert on_disk == json_schema()
