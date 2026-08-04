"""Immutable inputs and outputs for run-scoped value resolution."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.enums import DomainStrEnum
from comparo.core.domain.expression_types import FileTrim
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.identity import CellId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InlineDefinitionRef
from comparo.core.domain.identity import MatrixBindingId
from comparo.core.domain.identity import MatrixCaseId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.provenance import ResolutionProvenance
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.provenance import ValueProviderIdentity
from comparo.core.domain.scopes import AttemptScope
from comparo.core.domain.scopes import CellScope
from comparo.core.domain.scopes import EnvironmentScope
from comparo.core.domain.scopes import ExecutionScope
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import RuntimeScope
from comparo.core.domain.scopes import StepScope
from comparo.core.domain.scopes import TrialScope
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.scopes import run_scope_of
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.value_references import namespace_supports_declared_scope
from comparo.core.domain.value_references import runtime_value_scope_kind
from comparo.core.domain.values import Blocked
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import Error
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import Missing
from comparo.core.domain.values import Present
from comparo.core.domain.values import ResolutionBlockCode
from comparo.core.domain.values import ResolutionErrorCode
from comparo.core.domain.values import ResolvedValue
from comparo.core.domain.values import Unavailable
from comparo.core.domain.values import UnavailableReason
from comparo.core.domain.values import is_resolved_value
from comparo.core.domain.values import is_resolved_value_type

type DefinitionHandle = DefinitionRef | InlineDefinitionRef


def _is_definition_handle(value: object) -> bool:
    return type(value) in {DefinitionRef, InlineDefinitionRef}


@final
class MatrixInputBinding(msgspec.Struct, frozen=True, kw_only=True):
    """One exact Matrix case and its unresolved object values for a logical cell."""

    binding: MatrixBindingId
    case_id: MatrixCaseId
    values: ObjectExpr

    def __post_init__(self) -> None:
        """Require an exact binding, case identity, and object expression."""
        if type(self.binding) is not MatrixBindingId:
            raise TypeError("Matrix input binding requires a MatrixBindingId")
        if type(self.case_id) is not MatrixCaseId:
            raise TypeError("Matrix input binding requires a MatrixCaseId")
        if type(self.values) is not ObjectExpr:
            raise TypeError("Matrix input binding values must be an ObjectExpr")


@final
class CellInputs(msgspec.Struct, frozen=True, kw_only=True):
    """The complete unresolved Matrix namespace for one lazily planned cell."""

    cell_id: CellId
    bindings: tuple[MatrixInputBinding, ...]

    def __post_init__(self) -> None:
        """Require unique ordered binding names for one exact cell."""
        if type(self.cell_id) is not CellId:
            raise TypeError("cell inputs require a CellId")
        if type(self.bindings) is not tuple or any(
            type(binding) is not MatrixInputBinding for binding in self.bindings
        ):
            raise TypeError("cell input bindings must be a typed immutable tuple")
        identities = tuple(binding.binding for binding in self.bindings)
        if len(identities) != len(set(identities)):
            raise ValueError("cell input binding names must be unique")


@final
class ResolutionTarget(msgspec.Struct, frozen=True, kw_only=True):
    """One logical receiving symbol and exact runtime consumer coordinate."""

    symbol: SymbolPath
    scope: RuntimeScope
    namespace: ValueNamespace | None = None
    cell_inputs: CellInputs | None = None

    def __post_init__(self) -> None:
        """Validate the symbol, concrete scope, and optional matching cell snapshot."""
        if type(self.symbol) is not SymbolPath:
            raise TypeError("resolution target requires a SymbolPath")
        runtime_value_scope_kind(self.scope)
        if self.namespace is not None and type(self.namespace) is not ValueNamespace:
            raise TypeError("resolution target namespace must be a ValueNamespace")
        if self.cell_inputs is not None:
            if type(self.cell_inputs) is not CellInputs:
                raise TypeError("resolution target cell inputs must be CellInputs")
            cell_id = _scope_cell_id(self.scope)
            if cell_id is None or self.cell_inputs.cell_id != cell_id:
                raise ValueError("resolution target cell inputs must match its runtime scope")


def _scope_cell_id(scope: RuntimeScope) -> CellId | None:
    if type(scope) in {RunScope, EnvironmentScope}:
        return None
    if type(scope) is CellScope:
        return scope.cell_id
    if type(scope) is TrialScope:
        return scope.cell.cell_id
    if type(scope) is ExecutionScope:
        return scope.trial.cell.cell_id
    if type(scope) is StepScope:
        return scope.execution.trial.cell.cell_id
    if type(scope) is AttemptScope:
        return scope.execution.trial.cell.cell_id
    raise TypeError("unsupported runtime scope")  # pragma: no cover - closed RuntimeScope


type SnapshotValue = JsonString | SensitiveValue[JsonString]


@final
class EnvironmentSnapshotEntry(msgspec.Struct, frozen=True, kw_only=True):
    """One run-start `$env` value with safe configuration provenance."""

    symbol: SymbolPath
    value: SnapshotValue
    configuration: ValueProvenance

    def __post_init__(self) -> None:
        """Require a text value or sealed text and non-secret provenance."""
        if type(self.symbol) is not SymbolPath:
            raise TypeError("environment snapshot entry requires a SymbolPath")
        if type(self.value) is not JsonString and not (
            is_sensitive_value(self.value) and self.value.payload_type is JsonString
        ):
            raise TypeError("environment snapshot entry must contain text or sealed text")
        if type(self.configuration) is not ValueProvenance:
            raise TypeError("environment snapshot entry requires ValueProvenance")


@final
class EnvironmentSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    """An isolated immutable `$env` namespace captured at run start."""

    environment: DefinitionRef
    entries: tuple[EnvironmentSnapshotEntry, ...]
    provider: ValueProviderIdentity

    def __post_init__(self) -> None:
        """Bind unique entries to one exact selected Environment revision."""
        if type(self.environment) is not DefinitionRef:
            raise TypeError("environment snapshot requires a DefinitionRef")
        if self.environment.identity.kind is not ResourceKind.ENVIRONMENT:
            raise ValueError("environment snapshot definition must identify Environment")
        if type(self.entries) is not tuple or any(
            type(entry) is not EnvironmentSnapshotEntry for entry in self.entries
        ):
            raise TypeError("environment snapshot entries must be a typed immutable tuple")
        symbols = tuple(entry.symbol for entry in self.entries)
        if len(symbols) != len(set(symbols)):
            raise ValueError("environment snapshot symbols must be unique")
        if type(self.provider) is not ValueProviderIdentity:
            raise TypeError("environment snapshot requires a ValueProviderIdentity")


@final
class ProviderPresent(msgspec.Struct, frozen=True, kw_only=True):
    """A provider returned one immutable public or sealed value."""

    value: ResolvedValue | SensitiveValue[ResolvedValue]

    def __post_init__(self) -> None:
        """Require the closed resolved-value union without opening sealed values."""
        if is_sensitive_value(self.value):
            if not is_resolved_value_type(self.value.payload_type):
                raise TypeError("provider sealed payload must be a resolved value")
        elif not is_resolved_value(self.value):
            raise TypeError("provider payload must be a resolved value")


@final
class ProviderUnavailable(msgspec.Struct, frozen=True, kw_only=True):
    """A provider reported expected absence."""

    reason: UnavailableReason

    def __post_init__(self) -> None:
        """Require a typed absence reason."""
        if type(self.reason) is not UnavailableReason:
            raise TypeError("provider unavailable reason must be UnavailableReason")


@final
class ProviderError(msgspec.Struct, frozen=True, kw_only=True):
    """A provider failed without returning exception text or payload data."""

    code: ResolutionErrorCode

    def __post_init__(self) -> None:
        """Require a stable error category."""
        if type(self.code) is not ResolutionErrorCode:
            raise TypeError("provider error code must be ResolutionErrorCode")


@final
class ProviderBlocked(msgspec.Struct, frozen=True, kw_only=True):
    """Operator or safety authority denied a provider read."""

    code: ResolutionBlockCode

    def __post_init__(self) -> None:
        """Require a stable denial category."""
        if type(self.code) is not ResolutionBlockCode:
            raise TypeError("provider block code must be ResolutionBlockCode")


type ProviderOutcome = ProviderPresent | ProviderUnavailable | ProviderError | ProviderBlocked


type StructuredDocument = (
    JsonString | BytesValue | SensitiveValue[JsonString] | SensitiveValue[BytesValue]
)


@final
class YamlDecodeRequest(msgspec.Struct, frozen=True, kw_only=True):
    """One explicit YAML conversion delegated to a safe injected decoder."""

    document: StructuredDocument
    symbol: SymbolPath
    scope: RuntimeScope
    configuration: ValueProvenance

    def __post_init__(self) -> None:
        """Accept only text or bytes, optionally sealed, with value-free coordinates."""
        valid_document = type(self.document) in {JsonString, BytesValue} or (
            is_sensitive_value(self.document)
            and self.document.payload_type in {JsonString, BytesValue}
        )
        if not valid_document:
            raise TypeError("YAML decode document must be text, bytes, or their sealed form")
        if type(self.symbol) is not SymbolPath:
            raise TypeError("YAML decode request requires a SymbolPath")
        runtime_value_scope_kind(self.scope)
        if type(self.configuration) is not ValueProvenance:
            raise TypeError("YAML decode request requires ValueProvenance")


@final
class ExternalProviderSource(msgspec.Struct, frozen=True, kw_only=True):
    """An internally authorized source contract for a registered provider."""

    provider: ValueProviderIdentity
    symbol: SymbolPath
    scope_kind: ValueScopeKind
    configuration: ValueProvenance
    namespace: ValueNamespace | None = None
    sensitive: bool = False

    def __post_init__(self) -> None:
        """Validate a static provider identity and declared production scope."""
        if type(self.provider) is not ValueProviderIdentity:
            raise TypeError("external source requires a ValueProviderIdentity")
        if type(self.symbol) is not SymbolPath:
            raise TypeError("external source requires a SymbolPath")
        if type(self.scope_kind) is not ValueScopeKind:
            raise TypeError("external source scope must be a ValueScopeKind")
        if type(self.configuration) is not ValueProvenance:
            raise TypeError("external source requires ValueProvenance")
        if self.namespace is not None:
            if type(self.namespace) is not ValueNamespace:
                raise TypeError("external source namespace must be a ValueNamespace")
            if not namespace_supports_declared_scope(self.namespace, self.scope_kind):
                raise ValueError("external source namespace is unavailable at its declared scope")
        if type(self.sensitive) is not bool:
            raise TypeError("external source sensitive flag must be boolean")
        if self.namespace is ValueNamespace.SECRETS and not self.sensitive:
            raise ValueError("secret external sources must be declared sensitive")


@final
class ExternalValueRequest(msgspec.Struct, frozen=True, kw_only=True):
    """One policy-bound read passed to an injected external provider."""

    source: ExternalProviderSource
    scope: RuntimeScope
    policy: DefinitionRef
    safety: tuple[DefinitionHandle, ...]

    def __post_init__(self) -> None:
        """Bind the source to its exact concrete scope and authority revisions."""
        if type(self.source) is not ExternalProviderSource:
            raise TypeError("external value request requires an ExternalProviderSource")
        runtime_value_scope_kind(self.scope)
        if runtime_value_scope_kind(self.scope) is not self.source.scope_kind:
            raise ValueError("external value request scope must match its declared source scope")
        _validate_authority(self.policy, self.safety)


@final
class FileReadRequest(msgspec.Struct, frozen=True, kw_only=True):
    """One live path read that an adapter must confine and authorize."""

    path: JsonString | SensitiveValue[JsonString]
    symbol: SymbolPath
    scope: RuntimeScope
    configuration: ValueProvenance
    policy: DefinitionRef
    safety: tuple[DefinitionHandle, ...]
    trim: FileTrim
    maximum_bytes: ByteCount | None = None

    def __post_init__(self) -> None:
        """Require text path containment and exact external authority."""
        if type(self.path) is not JsonString and not (
            is_sensitive_value(self.path) and self.path.payload_type is JsonString
        ):
            raise TypeError("file read path must be text or sealed text")
        if type(self.symbol) is not SymbolPath:
            raise TypeError("file read request requires a SymbolPath")
        runtime_value_scope_kind(self.scope)
        if type(self.configuration) is not ValueProvenance:
            raise TypeError("file read request requires ValueProvenance")
        _validate_authority(self.policy, self.safety)
        if type(self.trim) is not FileTrim:
            raise TypeError("file read trim must be FileTrim")
        if self.maximum_bytes is not None and type(self.maximum_bytes) is not ByteCount:
            raise TypeError("file read maximum bytes must be ByteCount")


def _validate_authority(
    policy: DefinitionRef,
    safety: tuple[DefinitionHandle, ...],
) -> None:
    if type(policy) is not DefinitionRef:
        raise TypeError("value source policy must be a DefinitionRef")
    if policy.identity.kind is not ResourceKind.EXECUTION_POLICY:
        raise ValueError("value source policy must identify ExecutionPolicy")
    if type(safety) is not tuple or any(not _is_definition_handle(item) for item in safety):
        raise TypeError("value source safety must be an immutable definition-handle tuple")
    for item in safety:
        kind = item.identity.kind if type(item) is DefinitionRef else item.kind
        if kind is not ResourceKind.SAFETY_PROFILE:
            raise ValueError("value source safety handles must identify SafetyProfile")


class GenerationOperation(DomainStrEnum):
    """Initial internal deterministic generation operations."""

    UUID = "uuid"
    HEX = "hex"


@final
class GenerationSpec(msgspec.Struct, frozen=True, kw_only=True):
    """One scoped generated logical value independent of external syntax."""

    symbol: SymbolPath
    operation: GenerationOperation
    scope_kind: ValueScopeKind
    configuration: ValueProvenance
    length: PositiveCount = PositiveCount(value=32)
    sensitive: bool = False
    secure: bool = False

    def __post_init__(self) -> None:
        """Require a supported operation and explicit stable generation scope."""
        if type(self.symbol) is not SymbolPath:
            raise TypeError("generation spec requires a SymbolPath")
        if type(self.operation) is not GenerationOperation:
            raise TypeError("generation operation must be a GenerationOperation")
        if type(self.scope_kind) is not ValueScopeKind:
            raise TypeError("generation scope must be a ValueScopeKind")
        if type(self.configuration) is not ValueProvenance:
            raise TypeError("generation spec requires ValueProvenance")
        if type(self.length) is not PositiveCount:
            raise TypeError("generation length must be a PositiveCount")
        if self.operation is GenerationOperation.UUID and self.length.value != 32:
            raise ValueError("UUID generation uses the fixed default length")
        if type(self.sensitive) is not bool or type(self.secure) is not bool:
            raise TypeError("generation security flags must be booleans")
        if self.secure and not self.sensitive:
            raise ValueError("secure generated values must be sensitive")


@final
class GenerationRequest(msgspec.Struct, frozen=True, kw_only=True):
    """One generation spec bound to an exact production scope."""

    spec: GenerationSpec
    scope: RuntimeScope
    instant: UtcInstant

    def __post_init__(self) -> None:
        """Bind generation to its declared concrete scope and observed clock."""
        if type(self.spec) is not GenerationSpec:
            raise TypeError("generation request requires a GenerationSpec")
        runtime_value_scope_kind(self.scope)
        if runtime_value_scope_kind(self.scope) is not self.spec.scope_kind:
            raise ValueError("generation request scope must match the declared generation scope")
        if type(self.instant) is not UtcInstant:
            raise TypeError("generation request requires a UtcInstant")


class SanitizationPatternDisposition(DomainStrEnum):
    """Whether one registered payload contributes new literal redaction patterns."""

    PATTERN_SOURCE = "pattern-source"
    COVERED_COMPOSITE = "covered-composite"


@final
class RegisteredSensitiveValue(msgspec.Struct, frozen=True, kw_only=True):
    """One sealed value retained for later mandatory sanitization."""

    value: SensitiveValue[ResolvedValue]
    symbol: SymbolPath
    scope: RuntimeScope
    provenance: ResolutionProvenance
    pattern_disposition: SanitizationPatternDisposition = (
        SanitizationPatternDisposition.PATTERN_SOURCE
    )

    def __post_init__(self) -> None:
        """Keep only sealed payloads and safe exact coordinates."""
        if not is_sensitive_value(self.value) or not is_resolved_value_type(
            self.value.payload_type
        ):
            raise TypeError("registered sensitive value must contain a sealed resolved value")
        if type(self.symbol) is not SymbolPath:
            raise TypeError("registered sensitive value requires a SymbolPath")
        runtime_value_scope_kind(self.scope)
        if type(self.provenance) is not ResolutionProvenance:
            raise TypeError("registered sensitive value requires ResolutionProvenance")
        if type(self.pattern_disposition) is not SanitizationPatternDisposition:
            raise TypeError("registered sensitive value requires a pattern disposition")
        if self.pattern_disposition is SanitizationPatternDisposition.COVERED_COMPOSITE:
            if self.value.payload_type not in {JsonArray, JsonObject}:
                raise ValueError("covered sanitization values must be JSON composites")
            if self.provenance.namespace is ValueNamespace.SECRETS:
                raise ValueError("secrets-namespace composites must remain pattern sources")


@final
class SanitizationSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    """An immutable same-run view of all sealed values registered so far."""

    run: RunScope
    revision: Count
    entries: tuple[RegisteredSensitiveValue, ...]

    def __post_init__(self) -> None:
        """Require monotonic revision accounting and same-run entries."""
        if type(self.run) is not RunScope:
            raise TypeError("sanitization snapshot requires a RunScope")
        if type(self.revision) is not Count:
            raise TypeError("sanitization snapshot revision must be Count")
        if type(self.entries) is not tuple or any(
            type(entry) is not RegisteredSensitiveValue for entry in self.entries
        ):
            raise TypeError("sanitization snapshot entries must be a typed immutable tuple")
        if self.revision.value != len(self.entries):
            raise ValueError("sanitization snapshot revision must equal its entry count")
        if any(run_scope_of(entry.scope) != self.run for entry in self.entries):
            raise ValueError("sanitization snapshot entries must belong to its run")
        pattern_origins: set[TaintOriginId] = set()
        for entry in self.entries:
            if entry.pattern_disposition is SanitizationPatternDisposition.COVERED_COMPOSITE:
                if not set(entry.value.origins).issubset(pattern_origins):
                    raise ValueError(
                        "covered sanitization composites require earlier pattern sources"
                    )
            else:
                pattern_origins.update(entry.value.origins)


@final
class ResolvedForWire(msgspec.Struct, frozen=True, kw_only=True):
    """A present wire value and the sanitizer snapshot that already contains its taint."""

    result: Present[ResolvedValue]
    sanitization: SanitizationSnapshot

    def __post_init__(self) -> None:
        """Require a present result and a same-run snapshot containing sensitive data."""
        if type(self.result) is not Present:
            raise TypeError("wire resolution requires a Present result")
        if type(self.sanitization) is not SanitizationSnapshot:
            raise TypeError("wire resolution requires a SanitizationSnapshot")
        if run_scope_of(self.result.provenance.scope) != self.sanitization.run:
            raise ValueError("wire value and sanitization snapshot must belong to the same run")
        if is_sensitive_value(self.result.value) and all(
            entry.value is not self.result.value for entry in self.sanitization.entries
        ):
            raise ValueError("sensitive wire value must already be registered for sanitization")


type WireResolution = ResolvedForWire | Unavailable | Missing | Error | Blocked
