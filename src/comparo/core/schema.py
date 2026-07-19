"""The ``comparo/v1`` JSON Schema, generated from the msgspec object models.

One comparo YAML file describes one object — an Environment, Request, Schema,
Instance, Matrix, DiffProfile, AssertionProfile, ExecutionProfile, or Project —
so the schema is the tagged union of every kind, dispatched on ``kind``. Because
it is derived from the same structs the loader validates against, it can never
drift from the real config surface. Editors consume it for autocomplete and
inline validation; agents consume it to author config they can then check with
``comparo validate``.
"""

from __future__ import annotations

from typing import Any

import msgspec

from comparo.core.models import Object
from comparo.core.report_record import ReportRecord

#: Where the published schema lives, for ``$id`` and editor ``$schema`` refs.
SCHEMA_ID = "https://raw.githubusercontent.com/wbenbihi/comparo/main/schema/comparo-v1.schema.json"
#: Where the report-record schema lives — the saved run/diff/execution artifact.
REPORT_SCHEMA_ID = (
    "https://raw.githubusercontent.com/wbenbihi/comparo/main/schema/comparo-report-v1.schema.json"
)
_DRAFT = "https://json-schema.org/draft/2020-12/schema"
_KINDS = (
    "Environment, Request, Schema, Instance, Matrix, DiffProfile, "
    "AssertionProfile, ExecutionProfile, or Project"
)


def json_schema() -> dict[str, Any]:
    """Return the ``comparo/v1`` JSON Schema for a single config object."""
    body = msgspec.json.schema(Object)
    return {
        "$schema": _DRAFT,
        "$id": SCHEMA_ID,
        "title": "comparo/v1",
        "description": f"A comparo/v1 object - one of {_KINDS}.",
        **body,
    }


def report_schema() -> dict[str, Any]:
    """Return the JSON Schema for a saved report record (run/diff/execution)."""
    body = msgspec.json.schema(ReportRecord)
    return {
        "$schema": _DRAFT,
        "$id": REPORT_SCHEMA_ID,
        "title": "comparo report v1",
        "description": "A comparo report record - a saved run, diff, or execution.",
        **body,
    }
