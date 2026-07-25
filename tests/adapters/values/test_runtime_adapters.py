"""Behavior and trust-boundary tests for production value adapters."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from collections.abc import Coroutine
from datetime import UTC
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.adapters.values import EnvFileConflict
from comparo.adapters.values import EnvironmentFileSource
from comparo.adapters.values import EnvironmentInlineValue
from comparo.adapters.values import EnvironmentSnapshotErrorCode
from comparo.adapters.values import EnvironmentSnapshotRequest
from comparo.adapters.values import EnvironmentSourceLimits
from comparo.adapters.values import PinnedProjectFiles
from comparo.adapters.values import ProjectFileValueProvider
from comparo.adapters.values import RunEnvironmentSnapshotBuilder
from comparo.adapters.values import SafeYamlValueDecoder
from comparo.adapters.values import SecureGenerator
from comparo.adapters.values import SystemRunClock
from comparo.adapters.values import YamlValueLimits
from comparo.core.domain.expression_types import FileTrim
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.identity import RunId
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.primitives import UtcInstant
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.scopes import RunScope
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import SensitiveValueAccess
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.sensitivity import is_sensitive_value
from comparo.core.domain.value_egress import ValueEgressSourceKind
from comparo.core.domain.value_egress import value_egress_origin
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.domain.values import ResolutionBlockCode
from comparo.core.domain.values import ResolutionErrorCode
from comparo.core.domain.values import UnavailableReason
from comparo.core.policy.models import CompiledExecutionPolicy
from comparo.core.policy.trusted import trusted_local_policy
from comparo.core.resolution.models import FileReadRequest
from comparo.core.resolution.models import GenerationOperation
from comparo.core.resolution.models import GenerationRequest
from comparo.core.resolution.models import GenerationSpec
from comparo.core.resolution.models import ProviderBlocked
from comparo.core.resolution.models import ProviderError
from comparo.core.resolution.models import ProviderPresent
from comparo.core.resolution.models import ProviderUnavailable
from comparo.core.resolution.models import YamlDecodeRequest


def _async_test[**P](
    function: Callable[P, Coroutine[object, object, None]],
) -> Callable[P, None]:
    @wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        asyncio.run(function(*args, **kwargs))

    return wrapper


def _fingerprint(value: str = "0") -> Fingerprint:
    return Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value=value * 64),
    )


def _definition(kind: ResourceKind, name: str, digest: str = "0") -> DefinitionRef:
    return DefinitionRef(
        identity=ResourceIdentity(
            namespace=NamespaceId(value="demo"),
            kind=kind,
            resource_id=ResourceId(value=name),
        ),
        fingerprint=_fingerprint(digest),
    )


def _provenance() -> ValueProvenance:
    return ValueProvenance(entries=(ProvenanceEntry(kind=ProvenanceKind.PROJECT),))


def _scope() -> RunScope:
    return RunScope(run_id=RunId(value=UUID(int=1)))


def _file_request(
    policy: CompiledExecutionPolicy,
    path: JsonString | SensitiveValue[JsonString],
    *,
    trim: FileTrim = FileTrim.NONE,
    maximum: ByteCount | None = None,
) -> FileReadRequest:
    return FileReadRequest(
        path=path,
        symbol=SymbolPath(segments=("file",)),
        scope=_scope(),
        configuration=_provenance(),
        policy=policy.definition,
        safety=(),
        trim=trim,
        maximum_bytes=maximum,
    )


def _snapshot_request(
    policy: CompiledExecutionPolicy,
    *,
    inherit_process: bool = False,
    files: tuple[EnvironmentFileSource, ...] = (),
    values: tuple[EnvironmentInlineValue, ...] = (),
) -> EnvironmentSnapshotRequest:
    return EnvironmentSnapshotRequest(
        environment=_definition(ResourceKind.ENVIRONMENT, "staging"),
        configuration=_provenance(),
        policy=policy.definition,
        inherit_process=inherit_process,
        files=files,
        values=values,
    )


def _file_source(
    path: str,
    conflict: EnvFileConflict = EnvFileConflict.ERROR,
) -> EnvironmentFileSource:
    return EnvironmentFileSource(
        path=JsonString(value=path),
        configuration=_provenance(),
        on_conflict=conflict,
    )


@_async_test
async def test_project_file_provider_confines_trims_and_bounds_reads(tmp_path: Path) -> None:
    nested = tmp_path / "data"
    nested.mkdir()
    (nested / "token.txt").write_bytes(b"  value\n")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_bytes(b"outside")
    (tmp_path / "link.txt").symlink_to(outside)
    policy = trusted_local_policy()
    with PinnedProjectFiles(tmp_path) as files:
        provider = ProjectFileValueProvider(files, policy)
        present = await provider.read(
            _file_request(policy, JsonString(value="data/token.txt"), trim=FileTrim.BOTH)
        )
        assert type(present) is ProviderPresent
        assert present.value == BytesValue(value=b"value")
        assert provider.identity.name == "project-file"

        sensitive_path = SensitiveValue(
            JsonString(value="data/token.txt"),
            origins=(TaintOriginId(value="test/path"),),
        )
        bounded = await provider.read(
            _file_request(policy, sensitive_path, maximum=ByteCount(value=2))
        )
        assert bounded == ProviderError(code=ResolutionErrorCode.BUDGET_EXCEEDED)
        missing = await provider.read(_file_request(policy, JsonString(value="missing")))
        assert missing == ProviderUnavailable(reason=UnavailableReason.NOT_FOUND)
        denied = await provider.read(_file_request(policy, JsonString(value="link.txt")))
        assert denied == ProviderBlocked(code=ResolutionBlockCode.PATH_DENIED)


@_async_test
async def test_project_file_provider_enforces_exact_authority(tmp_path: Path) -> None:
    (tmp_path / "value").write_text("ok")
    policy = trusted_local_policy()
    denied_policy = msgspec.structs.replace(
        policy,
        definition=msgspec.structs.replace(policy.definition, fingerprint=_fingerprint("1")),
    )
    denied_files = msgspec.structs.replace(
        policy,
        values=msgspec.structs.replace(policy.values, allow_project_files=False),
    )
    with PinnedProjectFiles(tmp_path) as files:
        provider = ProjectFileValueProvider(files, policy)
        mismatch = await provider.read(_file_request(denied_policy, JsonString(value="value")))
        assert mismatch == ProviderBlocked(code=ResolutionBlockCode.SOURCE_DENIED)
        unavailable = ProjectFileValueProvider(files, denied_files)
        blocked = await unavailable.read(_file_request(denied_files, JsonString(value="value")))
        assert blocked == ProviderBlocked(code=ResolutionBlockCode.CAPABILITY_DENIED)
        safety = (_definition(ResourceKind.SAFETY_PROFILE, "safe"),)
        request = msgspec.structs.replace(
            _file_request(policy, JsonString(value="value")), safety=safety
        )
        assert await provider.read(request) == ProviderBlocked(
            code=ResolutionBlockCode.SOURCE_DENIED
        )
        with pytest.raises(TypeError, match="FileReadRequest"):
            await provider.read(cast("FileReadRequest", object()))

    closed = await provider.read(_file_request(policy, JsonString(value="value")))
    assert closed == ProviderError(code=ResolutionErrorCode.PROVIDER_FAILED)


@_async_test
async def test_environment_snapshot_uses_explicit_precedence_and_no_expansion(
    tmp_path: Path,
) -> None:
    (tmp_path / "one.env").write_text(
        "# comment\nBASE=file\nEMPTY=\nSINGLE='a b'\nDOUBLE=\"line\\nnext\"\nLITERAL=$BASE\n"
    )
    (tmp_path / "two.env").write_text("BASE=second\nOTHER=value\n")
    policy = trusted_local_policy()
    with PinnedProjectFiles(tmp_path) as project_files:
        builder = RunEnvironmentSnapshotBuilder(
            project_files,
            policy,
            ambient={"BASE": "ambient", "AMBIENT": "yes", "BAD.NAME": "ignored"},
        )
        inline = EnvironmentInlineValue(
            symbol=SymbolPath(segments=("OTHER",)),
            value=JsonString(value="inline"),
            configuration=_provenance(),
        )
        result = await builder.build(
            _snapshot_request(
                policy,
                inherit_process=True,
                files=(
                    _file_source("one.env"),
                    _file_source("two.env", EnvFileConflict.OVERRIDE),
                ),
                values=(inline,),
            )
        )
    assert result.snapshot is not None
    access = SensitiveValueAccess(purpose="test.environment-snapshot")
    actual = {}
    for item in result.snapshot.entries:
        assert is_sensitive_value(item.value)
        assert cast("SensitiveValue[JsonString]", item.value).origins == (
            value_egress_origin(ValueEgressSourceKind.ENVIRONMENT, item.symbol),
        )
        value = cast(
            "JsonString",
            item.value.reveal(access=access) if is_sensitive_value(item.value) else item.value,
        )
        actual[item.symbol.segments[0]] = value.value
    assert actual == {
        "AMBIENT": "yes",
        "BASE": "second",
        "DOUBLE": "line\nnext",
        "EMPTY": "",
        "LITERAL": "$BASE",
        "OTHER": "inline",
        "SINGLE": "a b",
    }
    assert result.snapshot.provider.name == "env-snapshot"
    ambient = next(
        item for item in result.snapshot.entries if item.symbol == SymbolPath(segments=("AMBIENT",))
    )
    assert is_sensitive_value(ambient.value)


@_async_test
@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"A=1\nA=2\n", EnvironmentSnapshotErrorCode.DUPLICATE_KEY),
        (b"export A=1\n", EnvironmentSnapshotErrorCode.INVALID_DOTENV),
        (b"A='unterminated\n", EnvironmentSnapshotErrorCode.INVALID_DOTENV),
        (b'A="bad\\x"\n', EnvironmentSnapshotErrorCode.INVALID_DOTENV),
        (b"A=\xff\n", EnvironmentSnapshotErrorCode.INVALID_DOTENV),
        (b"A=x\x00y\n", EnvironmentSnapshotErrorCode.INVALID_DOTENV),
    ],
)
async def test_environment_snapshot_rejects_ambiguous_dotenv(
    tmp_path: Path,
    content: bytes,
    expected: EnvironmentSnapshotErrorCode,
) -> None:
    (tmp_path / "bad.env").write_bytes(content)
    policy = trusted_local_policy()
    with PinnedProjectFiles(tmp_path) as project_files:
        result = await RunEnvironmentSnapshotBuilder(project_files, policy, ambient={}).build(
            _snapshot_request(policy, files=(_file_source("bad.env"),))
        )
    assert result.error is expected


@_async_test
async def test_environment_snapshot_conflict_keep_and_failure_mapping(tmp_path: Path) -> None:
    (tmp_path / "first.env").write_text("A=first\n")
    (tmp_path / "second.env").write_text("A=second\n")
    policy = trusted_local_policy()
    with PinnedProjectFiles(tmp_path) as project_files:
        builder = RunEnvironmentSnapshotBuilder(project_files, policy, ambient={})
        conflict = await builder.build(
            _snapshot_request(
                policy,
                files=(_file_source("first.env"), _file_source("second.env")),
            )
        )
        assert conflict.error is EnvironmentSnapshotErrorCode.CONFLICT
        kept = await builder.build(
            _snapshot_request(
                policy,
                files=(
                    _file_source("first.env"),
                    _file_source("second.env", EnvFileConflict.KEEP),
                ),
            )
        )
        assert kept.snapshot is not None
        kept_value = cast("SensitiveValue[JsonString]", kept.snapshot.entries[0].value)
        assert kept_value.reveal(access=SensitiveValueAccess(purpose="test.dotenv-keep")) == (
            JsonString(value="first")
        )
        missing = await builder.build(
            _snapshot_request(policy, files=(_file_source("missing.env"),))
        )
        assert missing.error is EnvironmentSnapshotErrorCode.NOT_FOUND
        denied = await builder.build(_snapshot_request(policy, files=(_file_source("../outside"),)))
        assert denied.error is EnvironmentSnapshotErrorCode.PATH_DENIED


@_async_test
async def test_environment_snapshot_fails_closed_on_policy_safety_and_budgets(
    tmp_path: Path,
) -> None:
    (tmp_path / "large.env").write_text("A=123456\n")
    policy = trusted_local_policy()
    limits = EnvironmentSourceLimits(
        maximum_files=PositiveCount(value=1),
        maximum_file_bytes=ByteCount(value=4),
        maximum_total_bytes=ByteCount(value=4),
        maximum_entries=PositiveCount(value=1),
        maximum_line_bytes=ByteCount(value=3),
    )
    with PinnedProjectFiles(tmp_path) as project_files:
        builder = RunEnvironmentSnapshotBuilder(
            project_files,
            policy,
            ambient={"A": "1"},
            limits=limits,
        )
        too_many = await builder.build(
            _snapshot_request(policy, files=(_file_source("large.env"), _file_source("large.env")))
        )
        assert too_many.error is EnvironmentSnapshotErrorCode.BUDGET_EXCEEDED
        too_large = await builder.build(
            _snapshot_request(policy, files=(_file_source("large.env"),))
        )
        assert too_large.error is EnvironmentSnapshotErrorCode.BUDGET_EXCEEDED
        mismatch_request = msgspec.structs.replace(
            _snapshot_request(policy),
            policy=msgspec.structs.replace(policy.definition, fingerprint=_fingerprint("1")),
        )
        mismatch = await builder.build(mismatch_request)
        assert mismatch.error is EnvironmentSnapshotErrorCode.POLICY_MISMATCH
        safety_request = msgspec.structs.replace(
            _snapshot_request(policy), safety=(_definition(ResourceKind.SAFETY_PROFILE, "safe"),)
        )
        unsupported = await builder.build(safety_request)
        assert unsupported.error is EnvironmentSnapshotErrorCode.SAFETY_UNSUPPORTED
        with pytest.raises(TypeError, match="EnvironmentSnapshotRequest"):
            await builder.build(cast("EnvironmentSnapshotRequest", object()))

        denied_policy = msgspec.structs.replace(
            policy,
            values=msgspec.structs.replace(policy.values, allow_project_files=False),
        )
        denied_builder = RunEnvironmentSnapshotBuilder(project_files, denied_policy, ambient={})
        denied = await denied_builder.build(
            _snapshot_request(denied_policy, files=(_file_source("large.env"),))
        )
        assert denied.error is EnvironmentSnapshotErrorCode.CAPABILITY_DENIED


@_async_test
async def test_yaml_decoder_is_inert_bounded_and_sensitivity_agnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decoder = SafeYamlValueDecoder(
        YamlValueLimits(maximum_bytes=64, maximum_depth=8, maximum_nodes=16)
    )
    request = YamlDecodeRequest(
        document=JsonString(value="literal: {$env: TOKEN}\nnumber: 2"),
        symbol=SymbolPath(segments=("yaml",)),
        scope=_scope(),
        configuration=_provenance(),
    )
    result = await decoder.decode(request)
    assert type(result) is ProviderPresent
    assert type(result.value) is JsonObject
    assert decoder.identity.name == "yaml"

    sealed = SensitiveValue(
        BytesValue(value=b"key: value"),
        origins=(TaintOriginId(value="test/yaml"),),
    )
    sealed_result = await decoder.decode(msgspec.structs.replace(request, document=sealed))
    assert type(sealed_result) is ProviderPresent
    invalid_utf8 = await decoder.decode(
        msgspec.structs.replace(request, document=BytesValue(value=b"\xff"))
    )
    assert invalid_utf8 == ProviderError(code=ResolutionErrorCode.CONVERSION_FAILED)
    empty = await decoder.decode(msgspec.structs.replace(request, document=JsonString(value="")))
    assert empty == ProviderError(code=ResolutionErrorCode.CONVERSION_FAILED)
    oversized = await decoder.decode(
        msgspec.structs.replace(request, document=JsonString(value="x" * 65))
    )
    assert oversized == ProviderError(code=ResolutionErrorCode.BUDGET_EXCEEDED)
    invalid = await decoder.decode(msgspec.structs.replace(request, document=JsonString(value="[")))
    assert invalid == ProviderError(code=ResolutionErrorCode.CONVERSION_FAILED)

    def broken_parser(**_arguments: object) -> object:
        raise RuntimeError("payload-must-not-escape")

    monkeypatch.setattr("comparo.adapters.values.yaml.parse_yaml_source", broken_parser)
    failed = await decoder.decode(request)
    assert failed == ProviderError(code=ResolutionErrorCode.PROVIDER_FAILED)
    with pytest.raises(TypeError, match="YamlDecodeRequest"):
        await decoder.decode(cast("YamlDecodeRequest", object()))


@_async_test
async def test_secure_generation_supports_uuid_and_exact_hex() -> None:
    generator = SecureGenerator()
    base = GenerationSpec(
        symbol=SymbolPath(segments=("generated",)),
        operation=GenerationOperation.UUID,
        scope_kind=ValueScopeKind.RUN,
        configuration=_provenance(),
        sensitive=True,
        secure=True,
    )
    request = GenerationRequest(
        spec=base,
        scope=_scope(),
        instant=UtcInstant(unix_nanoseconds=0),
    )
    uuid_result = await generator.generate(request)
    assert type(uuid_result) is ProviderPresent
    parsed = UUID(cast("JsonString", uuid_result.value).value)
    assert parsed.version == 4
    assert generator.identity.name == "secure-generator"

    hex_spec = msgspec.structs.replace(
        base,
        operation=GenerationOperation.HEX,
        length=PositiveCount(value=7),
    )
    hex_result = await generator.generate(msgspec.structs.replace(request, spec=hex_spec))
    assert type(hex_result) is ProviderPresent
    value = cast("JsonString", hex_result.value).value
    assert len(value) == 7
    assert re.fullmatch(r"[0-9a-f]+", value)

    public = msgspec.structs.replace(hex_spec, secure=False, sensitive=False)
    blocked = await generator.generate(msgspec.structs.replace(request, spec=public))
    assert blocked == ProviderBlocked(code=ResolutionBlockCode.CAPABILITY_DENIED)
    with pytest.raises(TypeError, match="GenerationRequest"):
        await generator.generate(cast("GenerationRequest", object()))


def test_system_run_clock_returns_current_utc_time() -> None:
    before = UtcInstant.from_datetime(datetime.now(UTC)).unix_nanoseconds
    actual = SystemRunClock().now().unix_nanoseconds
    after = UtcInstant.from_datetime(datetime.now(UTC)).unix_nanoseconds
    assert before <= actual <= after
