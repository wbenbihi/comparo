"""Single-source field catalog for validation and schema generation."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.identity import ResourceKind


class FieldMode(DomainStrEnum):
    """How one external field is compiled into the canonical model."""

    STATIC = "static"
    STRUCTURED = "structured"
    RESOLVABLE = "resolvable"
    ASSERTION_RULES = "assertion-rules"
    DIFF_RULES = "diff-rules"
    REFERENCE = "reference"
    REFERENCE_LIST = "reference-list"
    REFERENCE_UNION_LIST = "reference-union-list"
    SCHEMA = "schema"


@final
class FieldSchema(msgspec.Struct, frozen=True, kw_only=True):
    """One strict top-level resource field and its compilation behavior."""

    name: str
    mode: FieldMode
    required: bool = False
    reference_kinds: tuple[ResourceKind, ...] = ()
    inline: bool = False
    fields: tuple[FieldSchema, ...] = ()

    def __post_init__(self) -> None:
        """Reject contradictory field definitions in the compiler catalog."""
        if type(self.name) is not str or not self.name:
            raise ValueError("field schema name must be non-empty text")
        if type(self.mode) is not FieldMode:
            raise TypeError("field schema mode must be a FieldMode")
        if type(self.required) is not bool or type(self.inline) is not bool:
            raise TypeError("field schema flags must be booleans")
        if type(self.reference_kinds) is not tuple or any(
            type(kind) is not ResourceKind for kind in self.reference_kinds
        ):
            raise TypeError("field schema reference kinds must be a typed tuple")
        if type(self.fields) is not tuple or any(
            type(field) is not FieldSchema for field in self.fields
        ):
            raise TypeError("structured field members must be a typed tuple")
        reference_mode = self.mode in {
            FieldMode.REFERENCE,
            FieldMode.REFERENCE_LIST,
            FieldMode.REFERENCE_UNION_LIST,
            FieldMode.SCHEMA,
        }
        if reference_mode != bool(self.reference_kinds):
            raise ValueError("reference field modes and kinds must be declared together")
        if self.inline and len(self.reference_kinds) != 1:
            raise ValueError("inline reference fields must declare exactly one resource kind")
        if (self.mode is FieldMode.STRUCTURED) != bool(self.fields):
            raise ValueError("structured field modes and member fields must be declared together")


@final
class ResourceSchema(msgspec.Struct, frozen=True, kw_only=True):
    """The strict field inventory for one versioned resource kind."""

    kind: ResourceKind
    fields: tuple[FieldSchema, ...]
    opaque_spec: bool = False

    def __post_init__(self) -> None:
        """Require one unique field definition and coherent opaque behavior."""
        if type(self.kind) is not ResourceKind:
            raise TypeError("resource schema kind must be a ResourceKind")
        if type(self.fields) is not tuple or any(
            type(field) is not FieldSchema for field in self.fields
        ):
            raise TypeError("resource schema fields must be a typed tuple")
        names = tuple(field.name for field in self.fields)
        if len(names) != len(set(names)):
            raise ValueError("resource schema field names must be unique")
        if type(self.opaque_spec) is not bool:
            raise TypeError("resource schema opaque flag must be a boolean")
        if self.opaque_spec and self.fields:
            raise ValueError("opaque resource schemas cannot declare fields")


def _field(
    name: str,
    mode: FieldMode,
    *,
    required: bool = False,
    kinds: tuple[ResourceKind, ...] = (),
    inline: bool = False,
    fields: tuple[FieldSchema, ...] = (),
) -> FieldSchema:
    return FieldSchema(
        name=name,
        mode=mode,
        required=required,
        reference_kinds=kinds,
        inline=inline,
        fields=fields,
    )


_ASSERTION_PROFILE_COMMON = (
    _field("include", FieldMode.REFERENCE_LIST, kinds=(ResourceKind.ASSERTION_PROFILE,)),
    _field("edits", FieldMode.STATIC),
    _field("rules", FieldMode.ASSERTION_RULES),
    _field("parameters", FieldMode.RESOLVABLE),
)

_EXECUTION_FIELDS = (
    _field(
        "subjects",
        FieldMode.REFERENCE_UNION_LIST,
        kinds=(ResourceKind.REQUEST, ResourceKind.SCENARIO),
    ),
    _field("baseline", FieldMode.REFERENCE, kinds=(ResourceKind.ENVIRONMENT,)),
    _field("candidates", FieldMode.REFERENCE_LIST, kinds=(ResourceKind.ENVIRONMENT,)),
    _field("environments", FieldMode.REFERENCE_LIST, kinds=(ResourceKind.ENVIRONMENT,)),
    _field("matrices", FieldMode.REFERENCE_LIST, kinds=(ResourceKind.MATRIX,)),
    _field("trials", FieldMode.STATIC),
    _field("maximumAttempts", FieldMode.STATIC),
    _field("budgets", FieldMode.STATIC),
    _field("parameters", FieldMode.RESOLVABLE),
    _field("variables", FieldMode.RESOLVABLE),
    _field("secrets", FieldMode.RESOLVABLE),
    _field(
        "assertions",
        FieldMode.REFERENCE_LIST,
        kinds=(ResourceKind.ASSERTION_PROFILE,),
        inline=True,
    ),
    _field("diffs", FieldMode.REFERENCE_LIST, kinds=(ResourceKind.DIFF_PROFILE,), inline=True),
    _field("gate", FieldMode.REFERENCE, kinds=(ResourceKind.GATE_PROFILE,), inline=True),
    _field("safety", FieldMode.REFERENCE, kinds=(ResourceKind.SAFETY_PROFILE,), inline=True),
    _field(
        "redaction",
        FieldMode.REFERENCE,
        kinds=(ResourceKind.REDACTION_PROFILE,),
        inline=True,
    ),
    _field(
        "artifacts",
        FieldMode.REFERENCE,
        kinds=(ResourceKind.ARTIFACT_PROFILE,),
        inline=True,
    ),
)

RESOURCE_SCHEMAS: tuple[ResourceSchema, ...] = (
    ResourceSchema(
        kind=ResourceKind.PROJECT,
        fields=(
            _field("data", FieldMode.STATIC),
            _field("sources", FieldMode.STATIC),
            _field("variables", FieldMode.RESOLVABLE),
            _field("secrets", FieldMode.RESOLVABLE),
            _field("defaults", FieldMode.STRUCTURED, fields=_EXECUTION_FIELDS),
        ),
    ),
    ResourceSchema(
        kind=ResourceKind.ENVIRONMENT,
        fields=(
            _field("baseUrl", FieldMode.RESOLVABLE, required=True),
            _field("envFiles", FieldMode.STATIC),
            _field("variables", FieldMode.RESOLVABLE),
            _field("secrets", FieldMode.RESOLVABLE),
            _field("headers", FieldMode.RESOLVABLE),
            _field("cookies", FieldMode.RESOLVABLE),
            _field("auth", FieldMode.RESOLVABLE),
            _field("timeout", FieldMode.RESOLVABLE),
            _field("tls", FieldMode.STATIC),
            _field("proxy", FieldMode.RESOLVABLE),
            _field(
                "safety", FieldMode.REFERENCE, kinds=(ResourceKind.SAFETY_PROFILE,), inline=True
            ),
            _field(
                "redaction",
                FieldMode.REFERENCE,
                kinds=(ResourceKind.REDACTION_PROFILE,),
                inline=True,
            ),
            _field(
                "artifacts",
                FieldMode.REFERENCE,
                kinds=(ResourceKind.ARTIFACT_PROFILE,),
                inline=True,
            ),
        ),
    ),
    ResourceSchema(
        kind=ResourceKind.REQUEST,
        fields=(
            _field("parameters", FieldMode.RESOLVABLE),
            _field("request", FieldMode.RESOLVABLE, required=True),
            _field("matrices", FieldMode.REFERENCE_LIST, kinds=(ResourceKind.MATRIX,)),
            _field(
                "responseSchema",
                FieldMode.SCHEMA,
                kinds=(ResourceKind.SCHEMA,),
                inline=True,
            ),
            _field(
                "assertions",
                FieldMode.REFERENCE_LIST,
                kinds=(ResourceKind.ASSERTION_PROFILE,),
                inline=True,
            ),
            _field(
                "diffs", FieldMode.REFERENCE_LIST, kinds=(ResourceKind.DIFF_PROFILE,), inline=True
            ),
            _field(
                "safety", FieldMode.REFERENCE, kinds=(ResourceKind.SAFETY_PROFILE,), inline=True
            ),
            _field(
                "redaction",
                FieldMode.REFERENCE,
                kinds=(ResourceKind.REDACTION_PROFILE,),
                inline=True,
            ),
            _field(
                "artifacts",
                FieldMode.REFERENCE,
                kinds=(ResourceKind.ARTIFACT_PROFILE,),
                inline=True,
            ),
        ),
    ),
    ResourceSchema(
        kind=ResourceKind.SCENARIO,
        fields=(
            _field("parameters", FieldMode.RESOLVABLE),
            _field("matrices", FieldMode.REFERENCE_LIST, kinds=(ResourceKind.MATRIX,)),
            _field("steps", FieldMode.RESOLVABLE, required=True),
            _field(
                "assertions",
                FieldMode.REFERENCE_LIST,
                kinds=(ResourceKind.ASSERTION_PROFILE,),
                inline=True,
            ),
            _field(
                "diffs", FieldMode.REFERENCE_LIST, kinds=(ResourceKind.DIFF_PROFILE,), inline=True
            ),
            _field(
                "safety", FieldMode.REFERENCE, kinds=(ResourceKind.SAFETY_PROFILE,), inline=True
            ),
        ),
    ),
    ResourceSchema(
        kind=ResourceKind.MATRIX,
        fields=(
            _field("axes", FieldMode.RESOLVABLE),
            _field("cases", FieldMode.RESOLVABLE),
            _field("include", FieldMode.RESOLVABLE),
            _field("exclude", FieldMode.STATIC),
            _field("combine", FieldMode.STATIC),
        ),
    ),
    ResourceSchema(
        kind=ResourceKind.EXECUTION_PROFILE,
        fields=_EXECUTION_FIELDS,
    ),
    ResourceSchema(kind=ResourceKind.SCHEMA, fields=(), opaque_spec=True),
    ResourceSchema(kind=ResourceKind.ASSERTION_PROFILE, fields=_ASSERTION_PROFILE_COMMON),
    ResourceSchema(
        kind=ResourceKind.DIFF_PROFILE,
        fields=(
            _field("include", FieldMode.REFERENCE_LIST, kinds=(ResourceKind.DIFF_PROFILE,)),
            _field("edits", FieldMode.STATIC),
            _field("rules", FieldMode.DIFF_RULES),
            _field("parameters", FieldMode.RESOLVABLE),
        ),
    ),
    ResourceSchema(
        kind=ResourceKind.GATE_PROFILE,
        fields=(
            _field("assertions", FieldMode.STATIC),
            _field("diffs", FieldMode.STATIC),
            _field("runtime", FieldMode.STATIC),
            _field("coverage", FieldMode.STATIC),
        ),
    ),
    ResourceSchema(
        kind=ResourceKind.SAFETY_PROFILE,
        fields=(
            _field("include", FieldMode.REFERENCE_LIST, kinds=(ResourceKind.SAFETY_PROFILE,)),
            _field("extends", FieldMode.REFERENCE, kinds=(ResourceKind.SAFETY_PROFILE,)),
            _field("network", FieldMode.STATIC),
            _field("filesystem", FieldMode.STATIC),
            _field("environment", FieldMode.STATIC),
            _field("schemas", FieldMode.STATIC),
            _field("budgets", FieldMode.STATIC),
        ),
    ),
    ResourceSchema(
        kind=ResourceKind.REDACTION_PROFILE,
        fields=(
            _field("rules", FieldMode.STATIC),
            _field("patterns", FieldMode.STATIC),
            _field("backstop", FieldMode.STATIC),
        ),
    ),
    ResourceSchema(
        kind=ResourceKind.ARTIFACT_PROFILE,
        fields=(
            _field("capture", FieldMode.STATIC),
            _field("storage", FieldMode.STATIC),
            _field("retention", FieldMode.STATIC),
        ),
    ),
    ResourceSchema(
        kind=ResourceKind.VALUE,
        fields=(_field("value", FieldMode.RESOLVABLE, required=True),),
    ),
)


def schema_for(kind: ResourceKind) -> ResourceSchema:
    """Return the one catalog entry for a project-authored resource kind."""
    if type(kind) is not ResourceKind:
        raise TypeError("schema lookup requires a ResourceKind")
    try:
        return next(schema for schema in RESOURCE_SCHEMAS if schema.kind is kind)
    except StopIteration as error:
        raise ValueError("resource kind has no project schema") from error
