"""Pinned, bounded JSON Schema preparation outside the pure assertion engine."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol
from typing import final

import jsonschema  # type: ignore[import-untyped]
import msgspec
from referencing import Registry
from referencing.exceptions import Unresolvable

from comparo.core.assertions.bindings import capability_implementation
from comparo.core.assertions.bindings import operands_fingerprint
from comparo.core.assertions.bindings import operation_fingerprint
from comparo.core.assertions.bindings import selected_fingerprint
from comparo.core.assertions.inputs import PreparedCapabilityKind
from comparo.core.assertions.inputs import PreparedPredicate
from comparo.core.assertions.inputs import PreparedPredicateOutcome
from comparo.core.configuration.canonical import CanonicalValue
from comparo.core.configuration.canonical import CompiledProject
from comparo.core.configuration.canonical import LinkedResource
from comparo.core.configuration.canonical import RemoteSchema
from comparo.core.domain.assertions.evidence import SchemaValidationEvidence
from comparo.core.domain.assertions.evidence import SchemaViolationLocation
from comparo.core.domain.assertions.extended_operators import CompiledSchema
from comparo.core.domain.assertions.extended_operators import SchemaOperator
from comparo.core.domain.capabilities import CapabilityTargetKind
from comparo.core.domain.capabilities import NamedCapabilityTarget
from comparo.core.domain.diagnostics import DiagnosticCode
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import InlineDefinitionOrigin
from comparo.core.domain.identity import InlineDefinitionRef
from comparo.core.domain.identity import InlineFragment
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.selectors.paths import JsonPointer
from comparo.core.domain.selectors.results import SelectionMatch
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString

_ACCESS = SensitiveValueAccess(purpose="schema.validation.prepare")
_DEFAULT_DIALECT = NamedCapabilityTarget(
    kind=CapabilityTargetKind.SCHEMA_SOURCE,
    identifier="https://json-schema.org/draft/2020-12/schema",
)
_SCHEMA_INVALID = DiagnosticCode(value="schema.document.invalid")
_SCHEMA_INSTANCE_LIMIT = DiagnosticCode(value="schema.instance.limit-exceeded")
_SCHEMA_VIOLATION_LIMIT = DiagnosticCode(value="schema.violation.limit-exceeded")
_SCHEMA_IMPLEMENTATION = NamedCapabilityTarget(
    kind=CapabilityTargetKind.PROVIDER,
    identifier="comparo.core.jsonschema@1",
)


@final
class SchemaValidationLimits(msgspec.Struct, frozen=True, kw_only=True):
    """Caller-owned structural and evidence limits for JSON Schema validation."""

    maximum_schema_depth: int
    maximum_schema_nodes: int
    maximum_instance_depth: int
    maximum_instance_nodes: int
    maximum_violations: int
    retained_violations: int

    def __post_init__(self) -> None:
        """Reject ambient, boolean, absent, and non-positive ceilings."""
        values = (
            self.maximum_schema_depth,
            self.maximum_schema_nodes,
            self.maximum_instance_depth,
            self.maximum_instance_nodes,
            self.maximum_violations,
            self.retained_violations,
        )
        if any(type(value) is not int or value < 1 for value in values):
            raise ValueError("schema validation limits must be positive exact integers")
        if self.retained_violations > self.maximum_violations:
            raise ValueError("retained schema violations cannot exceed the validation limit")


DEFAULT_SCHEMA_VALIDATION_LIMITS_V1 = SchemaValidationLimits(
    maximum_schema_depth=128,
    maximum_schema_nodes=100_000,
    maximum_instance_depth=256,
    maximum_instance_nodes=1_000_000,
    maximum_violations=10_000,
    retained_violations=1_000,
)


class _ValidationError(Protocol):
    """The value-free coordinates consumed from jsonschema validation errors."""

    @property
    def absolute_path(self) -> Sequence[str | int]:
        """Return the instance coordinate."""

    @property
    def absolute_schema_path(self) -> Sequence[str | int]:
        """Return the schema coordinate."""


class _Validator(Protocol):
    """Minimal pinned validator behavior needed by the preparation adapter."""

    def iter_errors(self, instance: object) -> Iterable[_ValidationError]:
        """Yield violations without rendering payload-bearing messages."""


@final
class PreparedSchema(msgspec.Struct, frozen=True, kw_only=True):
    """One compiled schema identity paired with a process-local validator adapter."""

    compiled: CompiledSchema
    validator: object

    def __post_init__(self) -> None:
        """Require an exact compiled identity and non-null private validator object."""
        if type(self.compiled) is not CompiledSchema:
            raise TypeError("prepared schema requires a CompiledSchema")
        if self.validator is None:
            raise TypeError("prepared schema requires a validator")


class PreparedSchemaCatalog:
    """Immutable lookup of validated schema documents for one prepared run."""

    __slots__ = ("_entries", "_limits")

    def __init__(
        self,
        entries: tuple[PreparedSchema, ...],
        limits: SchemaValidationLimits,
    ) -> None:
        """Retain unique schema identities and their caller-owned validation limits."""
        if type(entries) is not tuple or any(type(item) is not PreparedSchema for item in entries):
            raise TypeError("prepared schema entries must be an immutable typed tuple")
        if type(limits) is not SchemaValidationLimits:
            raise TypeError("prepared schema catalog requires typed limits")
        identities = tuple(item.compiled for item in entries)
        if len(set(identities)) != len(identities):
            raise ValueError("prepared schema identities must be unique")
        self._entries = entries
        self._limits = limits

    @property
    def entries(self) -> tuple[PreparedSchema, ...]:
        """Return immutable prepared schema entries for exact binding."""
        return self._entries

    def predicate(
        self,
        operator: SchemaOperator,
        match: SelectionMatch,
        execution_id: EnvironmentExecutionId,
        selector_fingerprint: Fingerprint,
    ) -> PreparedPredicate:
        """Validate one selected JSON value and bind a value-free prepared predicate."""
        if type(operator) is not SchemaOperator or type(match) is not SelectionMatch:
            raise TypeError("schema predicate preparation requires operator and selection match")
        if type(execution_id) is not EnvironmentExecutionId:
            raise TypeError("schema predicate preparation requires an execution identity")
        if type(selector_fingerprint) is not Fingerprint:
            raise TypeError("schema predicate preparation requires a selector fingerprint")
        entry = next(
            (item for item in self._entries if item.compiled == operator.schema),
            None,
        )
        selected = selected_fingerprint(match)
        operands = operands_fingerprint(())
        if entry is None:
            return _predicate(
                operator,
                match,
                execution_id,
                selector_fingerprint,
                selected,
                operands,
                PreparedPredicateOutcome.ERROR,
                code=_SCHEMA_INVALID,
            )
        value = (
            match.value.reveal(access=_ACCESS) if is_sensitive_value(match.value) else match.value
        )
        try:
            instance = _to_builtin(
                value,
                maximum_depth=self._limits.maximum_instance_depth,
                maximum_nodes=self._limits.maximum_instance_nodes,
            )
        except ValueError:
            return _predicate(
                operator,
                match,
                execution_id,
                selector_fingerprint,
                selected,
                operands,
                PreparedPredicateOutcome.ERROR,
                code=_SCHEMA_INSTANCE_LIMIT,
            )
        try:
            violations = _violations(entry.validator, instance, self._limits)
        except Unresolvable:
            return _predicate(
                operator,
                match,
                execution_id,
                selector_fingerprint,
                selected,
                operands,
                PreparedPredicateOutcome.ERROR,
                code=_SCHEMA_INVALID,
            )
        if violations is None:
            return _predicate(
                operator,
                match,
                execution_id,
                selector_fingerprint,
                selected,
                operands,
                PreparedPredicateOutcome.ERROR,
                code=_SCHEMA_VIOLATION_LIMIT,
            )
        evidence = SchemaValidationEvidence(
            violation_count=Count(value=violations[0]),
            retained=violations[1],
            omitted=Count(value=violations[0] - len(violations[1])),
        )
        sealed_evidence: SchemaValidationEvidence | SensitiveValue[SchemaValidationEvidence]
        if is_sensitive_value(selected):
            sealed_evidence = SensitiveValue(evidence, origins=selected.origins)
        else:
            sealed_evidence = evidence
        return _predicate(
            operator,
            match,
            execution_id,
            selector_fingerprint,
            selected,
            operands,
            (
                PreparedPredicateOutcome.SATISFIED
                if violations[0] == 0
                else PreparedPredicateOutcome.VIOLATED
            ),
            evidence=sealed_evidence,
        )


@final
class RemoteSchemaDocument(msgspec.Struct, frozen=True, kw_only=True):
    """One policy-authorized remote schema payload retained only for run preparation."""

    uri: str
    payload: bytes

    def __post_init__(self) -> None:
        """Require an exact safe URI and immutable non-empty bytes."""
        NamedCapabilityTarget(kind=CapabilityTargetKind.SCHEMA_SOURCE, identifier=self.uri)
        if type(self.payload) is not bytes or not self.payload:
            raise ValueError("remote schema document must contain immutable bytes")


@final
class RemoteSchemaDocuments(msgspec.Struct, frozen=True, kw_only=True):
    """Unique immutable remote schema payloads fetched for one run."""

    entries: tuple[RemoteSchemaDocument, ...]

    def __post_init__(self) -> None:
        """Require unique canonical URI ordering."""
        if type(self.entries) is not tuple or any(
            type(item) is not RemoteSchemaDocument for item in self.entries
        ):
            raise TypeError("remote schema documents must be an immutable typed tuple")
        uris = tuple(item.uri for item in self.entries)
        if uris != tuple(sorted(set(uris))):
            raise ValueError("remote schema documents must be unique and canonical")

    def get(self, uri: str) -> bytes | None:
        """Return one exact fetched payload without any fallback lookup."""
        found = next((item for item in self.entries if item.uri == uri), None)
        return None if found is None else found.payload


def prepare_local_schema(
    project: CompiledProject,
    owner: DefinitionRef,
    binding: CanonicalValue,
    limits: SchemaValidationLimits,
) -> PreparedSchema:
    """Compile one local reusable or inline response schema without performing I/O."""
    if type(project) is not CompiledProject or type(owner) is not DefinitionRef:
        raise TypeError("local schema preparation requires project and owner definitions")
    if type(limits) is not SchemaValidationLimits:
        raise TypeError("local schema preparation requires typed limits")
    resource: DefinitionRef | InlineDefinitionRef
    document: CanonicalValue
    if type(binding) is LinkedResource:
        found = project.find(binding.target.identity.kind, binding.target.identity.resource_id)
        if found is None or found.definition != binding.target:
            raise ValueError("linked response schema is absent from the compiled project")
        resource = found.definition
        document = found.spec
        fingerprint = found.definition.fingerprint
    elif type(binding) is RemoteSchema:
        raise ValueError("remote schema requires the effectful remote-schema loader")
    else:
        document = binding
        builtin = _to_builtin(
            document,
            maximum_depth=limits.maximum_schema_depth,
            maximum_nodes=limits.maximum_schema_nodes,
        )
        fingerprint = _fingerprint(msgspec.json.encode(builtin, order="deterministic"))
        resource = InlineDefinitionRef(
            kind=ResourceKind.SCHEMA,
            origin=InlineDefinitionOrigin(
                owner=owner.identity,
                fragment=InlineFragment(value="#response-schema"),
            ),
            fingerprint=fingerprint,
        )
    schema = _to_builtin(
        document,
        maximum_depth=limits.maximum_schema_depth,
        maximum_nodes=limits.maximum_schema_nodes,
    )
    _reject_remote_references(schema)
    validator = _validator(schema)
    return PreparedSchema(
        compiled=CompiledSchema(fingerprint=fingerprint, resource=resource),
        validator=validator,
    )


def prepare_remote_schema(
    binding: RemoteSchema,
    payload: bytes,
    limits: SchemaValidationLimits,
) -> PreparedSchema:
    """Parse and compile one already-authorized, bounded remote JSON Schema payload."""
    if type(binding) is not RemoteSchema or type(payload) is not bytes:
        raise TypeError("remote schema preparation requires a binding and immutable bytes")
    if type(limits) is not SchemaValidationLimits:
        raise TypeError("remote schema preparation requires typed limits")
    try:
        document = json.loads(
            payload.decode("utf-8"),
            parse_float=Decimal,
            parse_int=int,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("remote schema is not valid UTF-8 JSON") from error
    schema = _to_builtin(
        document,
        maximum_depth=limits.maximum_schema_depth,
        maximum_nodes=limits.maximum_schema_nodes,
    )
    _reject_remote_references(schema)
    target = NamedCapabilityTarget(
        kind=CapabilityTargetKind.SCHEMA_SOURCE,
        identifier=binding.uri,
    )
    return PreparedSchema(
        compiled=CompiledSchema(
            fingerprint=_fingerprint(payload),
            remote_uri=target,
        ),
        validator=_validator(schema),
    )


def schema_operator(prepared: PreparedSchema, *, assert_formats: bool = False) -> SchemaOperator:
    """Create the exact pure-kernel operator identity for one prepared schema."""
    if type(prepared) is not PreparedSchema or type(assert_formats) is not bool:
        raise TypeError("schema operator requires a prepared schema and bool format mode")
    return SchemaOperator(
        schema=prepared.compiled,
        default_dialect=_DEFAULT_DIALECT,
        validator=_SCHEMA_IMPLEMENTATION,
        assert_formats=assert_formats,
    )


def _validator(schema: object) -> object:
    try:
        validator_type = jsonschema.validators.validator_for(
            schema,
            default=jsonschema.Draft202012Validator,
        )
        validator_type.check_schema(schema)
        return validator_type(schema, registry=Registry())
    except jsonschema.exceptions.SchemaError as error:
        raise ValueError("JSON Schema document is invalid") from error


def _violations(
    validator: object,
    instance: object,
    limits: SchemaValidationLimits,
) -> tuple[int, tuple[SchemaViolationLocation, ...]] | None:
    if not hasattr(validator, "iter_errors"):
        return None
    typed = validator
    count = 0
    retained: list[SchemaViolationLocation] = []
    for error in typed.iter_errors(instance):
        count += 1
        if count > limits.maximum_violations:
            return None
        if len(retained) < limits.retained_violations:
            retained.append(
                SchemaViolationLocation(
                    instance=JsonPointer(value=_pointer(error.absolute_path)),
                    schema=JsonPointer(value=_pointer(error.absolute_schema_path)),
                )
            )
    return count, tuple(retained)


def _predicate(
    operator: SchemaOperator,
    match: SelectionMatch,
    execution_id: EnvironmentExecutionId,
    selector_fingerprint: Fingerprint,
    selected: Fingerprint | SensitiveValue[Fingerprint],
    operands: Fingerprint | SensitiveValue[Fingerprint],
    outcome: PreparedPredicateOutcome,
    *,
    code: DiagnosticCode | None = None,
    evidence: SchemaValidationEvidence | SensitiveValue[SchemaValidationEvidence] | None = None,
) -> PreparedPredicate:
    return PreparedPredicate(
        kind=PreparedCapabilityKind.JSON_SCHEMA,
        selector_fingerprint=selector_fingerprint,
        operation_fingerprint=operation_fingerprint(operator),
        implementation=capability_implementation(operator),
        selected_fingerprint=selected,
        operands_fingerprint=operands,
        environment_execution_id=execution_id,
        location=match.location,
        outcome=outcome,
        code=code,
        evidence=evidence,
    )


def _to_builtin(  # noqa: C901 -- closed JSON carrier dispatch is intentionally explicit
    value: object,
    *,
    maximum_depth: int,
    maximum_nodes: int,
) -> object:
    count = 0

    def visit(current: object, depth: int) -> object:  # noqa: C901
        nonlocal count
        count += 1
        if count > maximum_nodes or depth > maximum_depth:
            raise ValueError("JSON value exceeds configured structural limits")
        if type(current) is JsonNull:
            return None
        if type(current) is JsonBoolean:
            return current.value
        if type(current) is JsonInteger:
            return current.value
        if type(current) is JsonDecimal:
            return current.value
        if type(current) is JsonString:
            return current.value
        if type(current) is JsonArray:
            return [visit(item, depth + 1) for item in current.items]
        if type(current) is JsonObject:
            return {member.name: visit(member.value, depth + 1) for member in current.members}
        if current is None or type(current) in {bool, int, Decimal, str}:
            return current
        if type(current) is list:
            return [visit(item, depth + 1) for item in current]
        if type(current) is dict and all(type(key) is str for key in current):
            return {key: visit(item, depth + 1) for key, item in current.items()}
        raise ValueError("schema validation requires JSON-compatible values")

    return visit(value, 0)


def _reject_remote_references(schema: object) -> None:
    stack = [schema]
    while stack:
        current = stack.pop()
        if isinstance(current, Mapping):
            for keyword in ("$ref", "$dynamicRef", "$recursiveRef"):
                reference = current.get(keyword)
                if type(reference) is str and not reference.startswith("#"):
                    raise ValueError("external schema references require an explicit loader")
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


def _pointer(segments: Sequence[str | int]) -> str:
    return "".join(f"/{str(segment).replace('~', '~0').replace('/', '~1')}" for segment in segments)


def _fingerprint(payload: bytes) -> Fingerprint:
    return Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value=hashlib.sha256(payload).hexdigest()),
    )


__all__ = (
    "DEFAULT_SCHEMA_VALIDATION_LIMITS_V1",
    "PreparedSchema",
    "PreparedSchemaCatalog",
    "RemoteSchemaDocument",
    "RemoteSchemaDocuments",
    "SchemaValidationLimits",
    "prepare_local_schema",
    "prepare_remote_schema",
    "schema_operator",
)
