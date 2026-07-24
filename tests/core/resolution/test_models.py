"""Validation boundaries for run-scoped resolution inputs and outputs."""

from typing import Any
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.domain.expression_types import FileTrim
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InlineDefinitionOrigin
from comparo.core.domain.identity import InlineDefinitionRef
from comparo.core.domain.identity import InlineFragment
from comparo.core.domain.identity import MatrixBindingId
from comparo.core.domain.identity import MatrixCaseId
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.provenance import ValueProviderIdentity
from comparo.core.domain.scopes import AttemptScope
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.value_references import ValueNamespace
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import ResolutionBlockCode
from comparo.core.domain.values import ResolutionErrorCode
from comparo.core.domain.values import ResolvedValue
from comparo.core.domain.values import UnavailableReason
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution.models import CellInputs
from comparo.core.resolution.models import EnvironmentSnapshot
from comparo.core.resolution.models import EnvironmentSnapshotEntry
from comparo.core.resolution.models import ExternalProviderSource
from comparo.core.resolution.models import ExternalValueRequest
from comparo.core.resolution.models import FileReadRequest
from comparo.core.resolution.models import GenerationOperation
from comparo.core.resolution.models import GenerationRequest
from comparo.core.resolution.models import GenerationSpec
from comparo.core.resolution.models import ProviderBlocked
from comparo.core.resolution.models import ProviderError
from comparo.core.resolution.models import ProviderPresent
from comparo.core.resolution.models import ProviderUnavailable
from comparo.core.resolution.models import RegisteredSensitiveValue
from comparo.core.resolution.models import ResolutionTarget
from comparo.core.resolution.models import ResolvedForWire
from comparo.core.resolution.models import SanitizationSnapshot
from comparo.core.resolution.models import YamlDecodeRequest
from comparo.core.resolution.provenance import LITERAL_PROVIDER
from comparo.core.resolution.provenance import present
from comparo.core.resolution.provenance import resolution_source
from tests.core.planning.conftest import policy_definition
from tests.core.resolution.conftest import configuration_provenance
from tests.core.resolution.conftest import execution_scope
from tests.core.resolution.conftest import matrix_inputs
from tests.core.resolution.conftest import snapshots


def _symbol(name: str = "value") -> SymbolPath:
    return SymbolPath(segments=(name,))


def _provider() -> ValueProviderIdentity:
    return ValueProviderIdentity(namespace="test", name="provider", version="1")


def _sensitive(
    value: JsonString | BytesValue | None = None,
) -> SensitiveValue[JsonString | BytesValue]:
    payload = JsonString(value="secret") if value is None else value
    return SensitiveValue(payload, origins=(TaintOriginId(value="test/origin"),))


def _safety(policy: DefinitionRef) -> DefinitionRef:
    return msgspec.structs.replace(
        policy,
        identity=msgspec.structs.replace(policy.identity, kind=ResourceKind.SAFETY_PROFILE),
    )


def _inline_safety(policy: DefinitionRef) -> InlineDefinitionRef:
    return InlineDefinitionRef(
        kind=ResourceKind.SAFETY_PROFILE,
        origin=InlineDefinitionOrigin(
            owner=policy.identity,
            fragment=InlineFragment(value="#safety"),
        ),
        fingerprint=policy.fingerprint,
    )


def _attempt(prepared: PreparedRun) -> AttemptScope:
    return AttemptScope(
        execution=execution_scope(prepared, 0),
        attempt_id=AttemptId(value=UUID(int=50)),
    )


def test_matrix_and_target_models_reject_ambiguous_coordinates(prepared_run: PreparedRun) -> None:
    inputs = matrix_inputs(prepared_run)
    binding = inputs.bindings[0]
    with pytest.raises(TypeError, match="MatrixBindingId"):
        msgspec.structs.replace(binding, binding=cast("MatrixBindingId", object()))
    with pytest.raises(TypeError, match="MatrixCaseId"):
        msgspec.structs.replace(binding, case_id=cast("MatrixCaseId", object()))
    with pytest.raises(TypeError, match="ObjectExpr"):
        msgspec.structs.replace(binding, values=cast("ObjectExpr", object()))
    with pytest.raises(TypeError, match="CellId"):
        msgspec.structs.replace(inputs, cell_id=cast("Any", object()))
    with pytest.raises(TypeError, match="typed immutable tuple"):
        msgspec.structs.replace(inputs, bindings=cast("Any", [binding]))
    with pytest.raises(TypeError, match="typed immutable tuple"):
        msgspec.structs.replace(inputs, bindings=cast("Any", (object(),)))
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(inputs, bindings=(binding, binding))

    target = ResolutionTarget(
        symbol=_symbol(),
        scope=execution_scope(prepared_run, 0),
        cell_inputs=inputs,
    )
    with pytest.raises(TypeError, match="SymbolPath"):
        msgspec.structs.replace(target, symbol=cast("SymbolPath", object()))
    with pytest.raises(TypeError, match="ValueNamespace"):
        msgspec.structs.replace(target, namespace=cast("ValueNamespace", object()))
    with pytest.raises(TypeError, match="CellInputs"):
        msgspec.structs.replace(target, cell_inputs=cast("CellInputs", object()))
    with pytest.raises(ValueError, match="match"):
        msgspec.structs.replace(
            target,
            cell_inputs=msgspec.structs.replace(
                inputs,
                cell_id=msgspec.structs.replace(inputs.cell_id, value=UUID(int=99)),
            ),
        )
    with pytest.raises(ValueError, match="match"):
        ResolutionTarget(
            symbol=_symbol(),
            scope=RunScope(run_id=prepared_run.invocation.run_id),
            cell_inputs=inputs,
        )


def test_environment_snapshot_models_are_exact_and_unique(prepared_run: PreparedRun) -> None:
    environment = prepared_run.effective.environments[0]
    entry = EnvironmentSnapshotEntry(
        symbol=_symbol("TOKEN"),
        value=cast("SensitiveValue[JsonString]", _sensitive()),
        configuration=configuration_provenance(),
    )
    with pytest.raises(TypeError, match="SymbolPath"):
        msgspec.structs.replace(entry, symbol=cast("SymbolPath", object()))
    with pytest.raises(TypeError, match="text or sealed text"):
        msgspec.structs.replace(entry, value=cast("JsonString", JsonInteger(value=1)))
    with pytest.raises(TypeError, match="ValueProvenance"):
        msgspec.structs.replace(entry, configuration=cast("Any", object()))

    snapshot = EnvironmentSnapshot(environment=environment, entries=(entry,), provider=_provider())
    with pytest.raises(TypeError, match="DefinitionRef"):
        msgspec.structs.replace(snapshot, environment=cast("DefinitionRef", object()))
    wrong_kind = msgspec.structs.replace(
        environment,
        identity=msgspec.structs.replace(environment.identity, kind=ResourceKind.REQUEST),
    )
    with pytest.raises(ValueError, match="Environment"):
        msgspec.structs.replace(snapshot, environment=wrong_kind)
    with pytest.raises(TypeError, match="typed immutable tuple"):
        msgspec.structs.replace(snapshot, entries=cast("Any", [entry]))
    with pytest.raises(TypeError, match="typed immutable tuple"):
        msgspec.structs.replace(snapshot, entries=cast("Any", (object(),)))
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(snapshot, entries=(entry, entry))
    with pytest.raises(TypeError, match="ValueProviderIdentity"):
        msgspec.structs.replace(snapshot, provider=cast("ValueProviderIdentity", object()))


def test_provider_outcome_models_reject_untyped_payloads() -> None:
    assert ProviderPresent(value=JsonString(value=""))
    assert ProviderPresent(value=_sensitive())
    with pytest.raises(TypeError, match="sealed payload"):
        ProviderPresent(
            value=cast(
                "Any",
                SensitiveValue(
                    _symbol(),
                    origins=(TaintOriginId(value="test/not-resolved"),),
                ),
            )
        )
    with pytest.raises(TypeError, match="resolved value"):
        ProviderPresent(value=cast("Any", object()))
    with pytest.raises(TypeError, match="UnavailableReason"):
        ProviderUnavailable(reason=cast("UnavailableReason", object()))
    with pytest.raises(TypeError, match="ResolutionErrorCode"):
        ProviderError(code=cast("ResolutionErrorCode", object()))
    with pytest.raises(TypeError, match="ResolutionBlockCode"):
        ProviderBlocked(code=cast("ResolutionBlockCode", object()))
    assert ProviderUnavailable(reason=UnavailableReason.NOT_FOUND)
    assert ProviderError(code=ResolutionErrorCode.PROVIDER_FAILED)
    assert ProviderBlocked(code=ResolutionBlockCode.SOURCE_DENIED)


def test_external_source_and_request_bind_scope_and_authority(prepared_run: PreparedRun) -> None:
    source = ExternalProviderSource(
        provider=_provider(),
        symbol=_symbol(),
        scope_kind=ValueScopeKind.ATTEMPT,
        configuration=configuration_provenance(),
        sensitive=True,
    )
    for field, value, match in (
        ("provider", object(), "ValueProviderIdentity"),
        ("symbol", object(), "SymbolPath"),
        ("scope_kind", object(), "ValueScopeKind"),
        ("configuration", object(), "ValueProvenance"),
        ("namespace", object(), "ValueNamespace"),
        ("sensitive", 1, "boolean"),
    ):
        with pytest.raises(TypeError, match=match):
            msgspec.structs.replace(source, **{field: value})
    with pytest.raises(ValueError, match="unavailable"):
        msgspec.structs.replace(
            source,
            namespace=ValueNamespace.ENV,
            scope_kind=ValueScopeKind.ATTEMPT,
        )
    with pytest.raises(ValueError, match="declared sensitive"):
        msgspec.structs.replace(source, namespace=ValueNamespace.SECRETS, sensitive=False)

    policy = policy_definition()
    request = ExternalValueRequest(
        source=source,
        scope=_attempt(prepared_run),
        policy=policy,
        safety=(_safety(policy), _inline_safety(policy)),
    )
    with pytest.raises(TypeError, match="ExternalProviderSource"):
        msgspec.structs.replace(request, source=cast("ExternalProviderSource", object()))
    with pytest.raises(ValueError, match="declared source scope"):
        msgspec.structs.replace(request, scope=execution_scope(prepared_run, 0))
    with pytest.raises(TypeError, match="DefinitionRef"):
        msgspec.structs.replace(request, policy=cast("DefinitionRef", object()))
    with pytest.raises(ValueError, match="ExecutionPolicy"):
        msgspec.structs.replace(request, policy=_safety(policy))
    with pytest.raises(TypeError, match="immutable definition-handle tuple"):
        msgspec.structs.replace(request, safety=cast("Any", [_safety(policy)]))
    with pytest.raises(TypeError, match="immutable definition-handle tuple"):
        msgspec.structs.replace(request, safety=cast("Any", (object(),)))
    wrong_safety = msgspec.structs.replace(
        policy,
        identity=msgspec.structs.replace(policy.identity, kind=ResourceKind.REQUEST),
    )
    with pytest.raises(ValueError, match="SafetyProfile"):
        msgspec.structs.replace(request, safety=(wrong_safety,))


def test_file_request_generation_and_secure_flags_are_validated(prepared_run: PreparedRun) -> None:
    policy = policy_definition()
    request = FileReadRequest(
        path=cast("SensitiveValue[JsonString]", _sensitive()),
        symbol=_symbol(),
        scope=_attempt(prepared_run),
        configuration=configuration_provenance(),
        policy=policy,
        safety=(),
        trim=FileTrim.NONE,
        maximum_bytes=ByteCount(value=12),
    )
    for field, value, match in (
        ("path", JsonInteger(value=1), "text or sealed text"),
        ("symbol", object(), "SymbolPath"),
        ("configuration", object(), "ValueProvenance"),
        ("trim", object(), "FileTrim"),
        ("maximum_bytes", object(), "ByteCount"),
    ):
        with pytest.raises(TypeError, match=match):
            msgspec.structs.replace(request, **{field: value})

    spec = GenerationSpec(
        symbol=_symbol(),
        operation=GenerationOperation.HEX,
        scope_kind=ValueScopeKind.ATTEMPT,
        configuration=configuration_provenance(),
        length=PositiveCount(value=3),
        sensitive=True,
        secure=True,
    )
    for field, value, match in (
        ("symbol", object(), "SymbolPath"),
        ("operation", object(), "GenerationOperation"),
        ("scope_kind", object(), "ValueScopeKind"),
        ("configuration", object(), "ValueProvenance"),
        ("length", object(), "PositiveCount"),
        ("sensitive", 1, "booleans"),
        ("secure", 1, "booleans"),
    ):
        with pytest.raises(TypeError, match=match):
            msgspec.structs.replace(spec, **{field: value})
    with pytest.raises(ValueError, match="fixed default"):
        GenerationSpec(
            symbol=_symbol(),
            operation=GenerationOperation.UUID,
            scope_kind=ValueScopeKind.RUN,
            configuration=configuration_provenance(),
            length=PositiveCount(value=2),
        )
    with pytest.raises(ValueError, match="must be sensitive"):
        msgspec.structs.replace(spec, sensitive=False)

    generation = GenerationRequest(
        spec=spec,
        scope=_attempt(prepared_run),
        instant=UtcInstant(unix_nanoseconds=1),
    )
    with pytest.raises(TypeError, match="GenerationSpec"):
        msgspec.structs.replace(generation, spec=cast("GenerationSpec", object()))
    with pytest.raises(ValueError, match="declared generation scope"):
        msgspec.structs.replace(generation, scope=execution_scope(prepared_run, 0))
    with pytest.raises(TypeError, match="UtcInstant"):
        msgspec.structs.replace(generation, instant=cast("UtcInstant", object()))


def test_sanitization_and_wire_models_require_same_run_registered_values(
    prepared_run: PreparedRun,
) -> None:
    scope = _attempt(prepared_run)
    sealed = cast("SensitiveValue[ResolvedValue]", _sensitive())
    source = resolution_source(
        symbol=_symbol(),
        provider=LITERAL_PROVIDER,
        configuration=configuration_provenance(),
        scope=scope,
        namespace=None,
    )
    result = present(
        sealed,
        symbol=_symbol(),
        scope=scope,
        source=source,
        namespace=None,
    )
    registered = RegisteredSensitiveValue(
        value=sealed,
        symbol=_symbol(),
        scope=scope,
        provenance=result.provenance,
    )
    with pytest.raises(TypeError, match="sealed resolved value"):
        msgspec.structs.replace(
            registered,
            value=cast("SensitiveValue[ResolvedValue]", JsonString(value="x")),
        )
    with pytest.raises(TypeError, match="SymbolPath"):
        msgspec.structs.replace(registered, symbol=cast("SymbolPath", object()))
    with pytest.raises(TypeError, match="ResolutionProvenance"):
        msgspec.structs.replace(registered, provenance=cast("Any", object()))

    run = RunScope(run_id=prepared_run.invocation.run_id)
    snapshot = SanitizationSnapshot(
        run=run,
        revision=Count(value=1),
        entries=(registered,),
    )
    with pytest.raises(TypeError, match="RunScope"):
        msgspec.structs.replace(snapshot, run=cast("RunScope", object()))
    with pytest.raises(TypeError, match="Count"):
        msgspec.structs.replace(snapshot, revision=cast("Count", object()))
    with pytest.raises(TypeError, match="typed immutable tuple"):
        msgspec.structs.replace(snapshot, entries=cast("Any", [registered]))
    with pytest.raises(TypeError, match="typed immutable tuple"):
        msgspec.structs.replace(snapshot, entries=cast("Any", (object(),)))
    with pytest.raises(ValueError, match="entry count"):
        msgspec.structs.replace(snapshot, revision=Count(value=0))
    other_scope = msgspec.structs.replace(
        scope,
        execution=msgspec.structs.replace(
            scope.execution,
            trial=msgspec.structs.replace(
                scope.execution.trial,
                cell=msgspec.structs.replace(
                    scope.execution.trial.cell,
                    run=RunScope(
                        run_id=msgspec.structs.replace(
                            prepared_run.invocation.run_id,
                            value=UUID(int=999),
                        )
                    ),
                ),
            ),
            environment=msgspec.structs.replace(
                scope.execution.environment,
                run=RunScope(
                    run_id=msgspec.structs.replace(
                        prepared_run.invocation.run_id,
                        value=UUID(int=999),
                    )
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="belong"):
        msgspec.structs.replace(
            snapshot,
            entries=(msgspec.structs.replace(registered, scope=other_scope),),
        )

    assert ResolvedForWire(result=result, sanitization=snapshot)
    with pytest.raises(TypeError, match="Present"):
        ResolvedForWire(result=cast("Any", object()), sanitization=snapshot)
    with pytest.raises(TypeError, match="SanitizationSnapshot"):
        ResolvedForWire(result=result, sanitization=cast("Any", object()))
    other_source = resolution_source(
        symbol=_symbol(),
        provider=LITERAL_PROVIDER,
        configuration=configuration_provenance(),
        scope=other_scope,
        namespace=None,
    )
    other_result = present(
        sealed,
        symbol=_symbol(),
        scope=other_scope,
        source=other_source,
        namespace=None,
    )
    with pytest.raises(ValueError, match="same run"):
        ResolvedForWire(result=other_result, sanitization=snapshot)
    with pytest.raises(ValueError, match="already be registered"):
        ResolvedForWire(
            result=result,
            sanitization=SanitizationSnapshot(run=run, revision=Count(value=0), entries=()),
        )


def test_empty_snapshot_fixture_still_covers_all_selected_environments(
    prepared_run: PreparedRun,
) -> None:
    assert len(snapshots(prepared_run)) == 2


def test_yaml_decode_request_accepts_only_structured_documents(prepared_run: PreparedRun) -> None:
    request = YamlDecodeRequest(
        document=cast("SensitiveValue[JsonString]", _sensitive()),
        symbol=_symbol(),
        scope=_attempt(prepared_run),
        configuration=configuration_provenance(),
    )
    assert YamlDecodeRequest(
        document=BytesValue(value=b"value"),
        symbol=request.symbol,
        scope=request.scope,
        configuration=request.configuration,
    )
    assert YamlDecodeRequest(
        document=cast("SensitiveValue[BytesValue]", _sensitive(BytesValue(value=b"value"))),
        symbol=request.symbol,
        scope=request.scope,
        configuration=request.configuration,
    )
    with pytest.raises(TypeError, match="text, bytes"):
        msgspec.structs.replace(request, document=cast("Any", JsonInteger(value=1)))
    with pytest.raises(TypeError, match="SymbolPath"):
        msgspec.structs.replace(request, symbol=cast("SymbolPath", object()))
    with pytest.raises(TypeError, match="ValueProvenance"):
        msgspec.structs.replace(request, configuration=cast("Any", object()))
