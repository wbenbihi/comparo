"""Direct validation and operating-system edge coverage for local value adapters."""

from __future__ import annotations

import asyncio
import errno
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from typing import cast

import msgspec
import pytest

from comparo.adapters.values import EnvironmentInlineValue
from comparo.adapters.values import EnvironmentSnapshotBuildResult
from comparo.adapters.values import EnvironmentSnapshotErrorCode
from comparo.adapters.values import EnvironmentSourceLimits
from comparo.adapters.values import LocalFileLimits
from comparo.adapters.values import LocalFilesystemError
from comparo.adapters.values import LocalFilesystemErrorCode
from comparo.adapters.values import PinnedProjectFiles
from comparo.adapters.values import ProjectFileValueProvider
from comparo.adapters.values import RunEnvironmentSnapshotBuilder
from comparo.adapters.values import SafeYamlValueDecoder
from comparo.adapters.values import YamlValueLimits
from comparo.adapters.values import capture_ambient_environment
from comparo.adapters.values._filesystem import ConfinedReadCode
from comparo.adapters.values._filesystem import ConfinedReadResult
from comparo.adapters.values._filesystem import _path_parts
from comparo.adapters.values._filesystem import _read_error_code
from comparo.adapters.values.environment import _dotenv_value
from comparo.adapters.values.environment import _DotenvAssignment
from comparo.adapters.values.environment import _DotenvResult
from comparo.adapters.values.environment import _parse_dotenv
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.value_references import SymbolPath
from comparo.core.domain.values import JsonString
from comparo.core.policy.models import AmbientEnvironmentPolicy
from comparo.core.policy.models import CompiledExecutionPolicy
from comparo.core.policy.trusted import trusted_local_policy
from comparo.core.resolution.models import EnvironmentSnapshot
from tests.adapters.values.test_runtime_adapters import _definition
from tests.adapters.values.test_runtime_adapters import _file_source
from tests.adapters.values.test_runtime_adapters import _provenance
from tests.adapters.values.test_runtime_adapters import _snapshot_request


def test_local_filesystem_models_reject_invalid_direct_construction() -> None:
    limits = LocalFileLimits()
    with pytest.raises(TypeError, match="byte limit"):
        msgspec.structs.replace(limits, maximum_bytes=cast("ByteCount", object()))
    with pytest.raises(TypeError, match="path limit"):
        msgspec.structs.replace(limits, maximum_path_bytes=cast("ByteCount", object()))
    with pytest.raises(TypeError, match="segment limit"):
        msgspec.structs.replace(limits, maximum_segments=cast("PositiveCount", object()))

    success = ConfinedReadResult(data=b"")
    with pytest.raises(TypeError, match="data"):
        msgspec.structs.replace(success, data=cast("bytes", "bad"))
    with pytest.raises(TypeError, match="code"):
        ConfinedReadResult(code=cast("ConfinedReadCode", object()))
    with pytest.raises(ValueError, match="exactly"):
        ConfinedReadResult()
    with pytest.raises(ValueError, match="exactly"):
        ConfinedReadResult(data=b"x", code=ConfinedReadCode.IO_FAILED)
    with pytest.raises(TypeError, match="typed"):
        LocalFilesystemError(cast("LocalFilesystemErrorCode", object()))
    error = LocalFilesystemError(LocalFilesystemErrorCode.ROOT_UNSAFE)
    assert error.code is LocalFilesystemErrorCode.ROOT_UNSAFE


def test_pinned_project_files_rejects_unsafe_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(TypeError, match="LocalFileLimits"):
        PinnedProjectFiles(tmp_path, limits=cast("LocalFileLimits", object()))
    with pytest.raises(LocalFilesystemError) as relative:
        PinnedProjectFiles(Path("relative"))
    assert relative.value.code is LocalFilesystemErrorCode.ROOT_UNSAFE
    with pytest.raises(LocalFilesystemError) as file_root:
        PinnedProjectFiles(tmp_path / "missing")
    assert file_root.value.code is LocalFilesystemErrorCode.ROOT_UNSAFE

    monkeypatch.setattr("comparo.adapters.values._filesystem._safe_open_supported", lambda: False)
    with pytest.raises(LocalFilesystemError) as unsupported:
        PinnedProjectFiles(tmp_path)
    assert unsupported.value.code is LocalFilesystemErrorCode.UNSUPPORTED_PLATFORM


def test_pinned_project_files_validates_opened_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "comparo.adapters.values._filesystem.os.fstat",
        lambda _descriptor: SimpleNamespace(st_mode=0),
    )
    with pytest.raises(LocalFilesystemError) as error:
        PinnedProjectFiles(tmp_path)
    assert error.value.code is LocalFilesystemErrorCode.ROOT_UNSAFE


def test_pinned_project_files_runtime_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "directory").mkdir()
    reader = PinnedProjectFiles(tmp_path)
    directory = asyncio.run(reader.read("directory"))
    assert directory.code is ConfinedReadCode.PATH_DENIED
    with pytest.raises(TypeError, match="path must be"):
        asyncio.run(reader.read(cast("str", object())))
    with pytest.raises(TypeError, match="maximum"):
        asyncio.run(reader.read("directory", maximum_bytes=cast("ByteCount", object())))

    def fail_dup(_descriptor: int) -> int:
        raise OSError

    monkeypatch.setattr("comparo.adapters.values._filesystem.os.dup", fail_dup)
    failed = asyncio.run(reader.read("missing"))
    assert failed.code is ConfinedReadCode.IO_FAILED
    reader.close()
    reader.close()
    with pytest.raises(LocalFilesystemError):
        reader.__enter__()


def test_project_provider_constructor_requires_exact_dependencies(tmp_path: Path) -> None:
    policy = trusted_local_policy()
    with PinnedProjectFiles(tmp_path) as files:
        assert files.limits == LocalFileLimits()
        with pytest.raises(TypeError, match="PinnedProjectFiles"):
            ProjectFileValueProvider(cast("PinnedProjectFiles", object()), policy)
        with pytest.raises(TypeError, match="CompiledExecutionPolicy"):
            ProjectFileValueProvider(files, cast("CompiledExecutionPolicy", object()))


def test_environment_models_reject_invalid_direct_construction() -> None:
    limits = EnvironmentSourceLimits()
    with pytest.raises(TypeError, match="count"):
        msgspec.structs.replace(limits, maximum_files=cast("PositiveCount", object()))
    with pytest.raises(TypeError, match="byte"):
        msgspec.structs.replace(limits, maximum_line_bytes=cast("ByteCount", object()))

    source = _file_source("x.env")
    with pytest.raises(TypeError, match="JsonString"):
        msgspec.structs.replace(source, path=cast("JsonString", object()))
    with pytest.raises(TypeError, match="ValueProvenance"):
        msgspec.structs.replace(source, configuration=cast("ValueProvenance", object()))
    with pytest.raises(TypeError, match="conflict"):
        msgspec.structs.replace(source, on_conflict=cast("Any", object()))

    inline = EnvironmentInlineValue(
        symbol=SymbolPath(segments=("A",)),
        value=JsonString(value="x"),
        configuration=_provenance(),
    )
    with pytest.raises(ValueError, match="one portable"):
        msgspec.structs.replace(inline, symbol=SymbolPath(segments=("A", "B")))
    with pytest.raises(ValueError, match="portable variable"):
        msgspec.structs.replace(inline, symbol=SymbolPath(segments=("A-B",)))
    with pytest.raises(TypeError, match="JsonString"):
        msgspec.structs.replace(inline, value=cast("JsonString", object()))
    with pytest.raises(TypeError, match="ValueProvenance"):
        msgspec.structs.replace(inline, configuration=cast("ValueProvenance", object()))


def test_environment_request_and_result_validate_closed_contracts() -> None:
    policy = trusted_local_policy()
    request = _snapshot_request(policy)
    wrong_environment = _definition(ResourceKind.REQUEST, "request")
    invalid_replacements: tuple[tuple[str, object, type[Exception]], ...] = (
        ("environment", object(), TypeError),
        ("environment", wrong_environment, ValueError),
        ("configuration", object(), TypeError),
        ("policy", object(), TypeError),
        ("policy", _definition(ResourceKind.REQUEST, "request"), ValueError),
        ("inherit_process", 1, TypeError),
        ("files", [], TypeError),
        ("files", (object(),), TypeError),
        ("values", [], TypeError),
        ("values", (object(),), TypeError),
        ("safety", [], TypeError),
        ("safety", (object(),), TypeError),
        ("safety", (wrong_environment,), ValueError),
    )
    for field, value, error in invalid_replacements:
        with pytest.raises(error):
            msgspec.structs.replace(request, **{field: value})

    inline = EnvironmentInlineValue(
        symbol=SymbolPath(segments=("A",)),
        value=JsonString(value="x"),
        configuration=_provenance(),
    )
    with pytest.raises(ValueError, match="unique"):
        msgspec.structs.replace(request, values=(inline, inline))

    failure = EnvironmentSnapshotBuildResult(error=EnvironmentSnapshotErrorCode.IO_FAILED)
    with pytest.raises(TypeError, match="EnvironmentSnapshot"):
        msgspec.structs.replace(failure, snapshot=cast("EnvironmentSnapshot", object()), error=None)
    with pytest.raises(TypeError, match="typed"):
        msgspec.structs.replace(failure, error=cast("EnvironmentSnapshotErrorCode", object()))
    with pytest.raises(ValueError, match="exactly"):
        EnvironmentSnapshotBuildResult()

    assignment = _DotenvAssignment(name="A", value="x")
    with pytest.raises(TypeError, match="assignments"):
        _DotenvResult(assignments=cast("Any", [assignment]))
    with pytest.raises(TypeError, match="dotenv error"):
        _DotenvResult(error=cast("EnvironmentSnapshotErrorCode", object()))
    with pytest.raises(ValueError, match="cannot retain"):
        _DotenvResult(
            assignments=(assignment,),
            error=EnvironmentSnapshotErrorCode.INVALID_DOTENV,
        )


def test_environment_builder_constructor_and_ambient_allowlist(tmp_path: Path) -> None:
    policy = trusted_local_policy()
    with PinnedProjectFiles(tmp_path) as files:
        with pytest.raises(TypeError, match="PinnedProjectFiles"):
            RunEnvironmentSnapshotBuilder(cast("PinnedProjectFiles", object()), policy)
        with pytest.raises(TypeError, match="CompiledExecutionPolicy"):
            RunEnvironmentSnapshotBuilder(files, cast("CompiledExecutionPolicy", object()))
        with pytest.raises(TypeError, match="EnvironmentSourceLimits"):
            RunEnvironmentSnapshotBuilder(
                files,
                policy,
                limits=cast("EnvironmentSourceLimits", object()),
            )
        with pytest.raises(TypeError, match="mapping"):
            RunEnvironmentSnapshotBuilder(files, policy, ambient=cast("Any", []))
        with pytest.raises(TypeError, match="exact strings"):
            RunEnvironmentSnapshotBuilder(files, policy, ambient=cast("Any", {1: "x"}))

        restricted = msgspec.structs.replace(
            policy,
            values=msgspec.structs.replace(
                policy.values,
                ambient_environment=AmbientEnvironmentPolicy(names=("ONLY",), allow_any=False),
            ),
        )
        builder = RunEnvironmentSnapshotBuilder(
            files,
            restricted,
            ambient={"ONLY": "yes", "IGNORED": "no"},
        )
        result = asyncio.run(builder.build(_snapshot_request(restricted, inherit_process=True)))
        assert result.snapshot is not None
        assert tuple(item.symbol.segments[0] for item in result.snapshot.entries) == ("ONLY",)


def test_ambient_capture_is_immutable_direct_and_incrementally_bounded() -> None:
    policy = trusted_local_policy()
    restricted = msgspec.structs.replace(
        policy,
        values=msgspec.structs.replace(
            policy.values,
            ambient_environment=AmbientEnvironmentPolicy(names=("ONLY",), allow_any=False),
        ),
    )
    source = {"ONLY": "yes", "IGNORED": "x" * 100_000}

    assert capture_ambient_environment(source, restricted) == (("ONLY", "yes"),)

    with pytest.raises(ValueError, match="configured limits"):
        capture_ambient_environment(
            {"ONLY": "toolong"},
            restricted,
            limits=EnvironmentSourceLimits(maximum_value_bytes=ByteCount(value=3)),
        )
    with pytest.raises(ValueError, match="configured limits"):
        capture_ambient_environment(
            {"A": "1", "B": "2"},
            policy,
            limits=EnvironmentSourceLimits(maximum_entries=PositiveCount(value=1)),
        )
    with pytest.raises(ValueError, match="configured limits"):
        capture_ambient_environment(
            {"A": "12", "B": "34"},
            policy,
            limits=EnvironmentSourceLimits(maximum_total_bytes=ByteCount(value=5)),
        )


def test_environment_builder_maps_closed_and_entry_budgets(tmp_path: Path) -> None:
    policy = trusted_local_policy()
    limits = EnvironmentSourceLimits(maximum_entries=PositiveCount(value=1))
    files = PinnedProjectFiles(tmp_path)
    builder = RunEnvironmentSnapshotBuilder(files, policy, ambient={"A": "one"}, limits=limits)
    files.close()
    closed = asyncio.run(builder.build(_snapshot_request(policy, files=(_file_source("missing"),))))
    assert closed.error is EnvironmentSnapshotErrorCode.IO_FAILED

    with PinnedProjectFiles(tmp_path) as live_files:
        inline_builder = RunEnvironmentSnapshotBuilder(
            live_files,
            policy,
            ambient={},
            limits=limits,
        )
        values = tuple(
            EnvironmentInlineValue(
                symbol=SymbolPath(segments=(name,)),
                value=JsonString(value=name),
                configuration=_provenance(),
            )
            for name in ("A", "B")
        )
        inline_result = asyncio.run(inline_builder.build(_snapshot_request(policy, values=values)))
        assert inline_result.error is EnvironmentSnapshotErrorCode.BUDGET_EXCEEDED

        (tmp_path / "one.env").write_text("B=file\n")
        ambient_result = asyncio.run(
            RunEnvironmentSnapshotBuilder(
                live_files,
                policy,
                ambient={"A": "ambient"},
                limits=limits,
            ).build(
                _snapshot_request(
                    policy,
                    inherit_process=True,
                    files=(_file_source("one.env"),),
                )
            )
        )
        assert ambient_result.error is EnvironmentSnapshotErrorCode.BUDGET_EXCEEDED


def test_dotenv_limits_and_escape_edges() -> None:
    line_limits = EnvironmentSourceLimits(maximum_line_bytes=ByteCount(value=3))
    line_result = _parse_dotenv(b"A=long", line_limits)
    assert line_result.error is EnvironmentSnapshotErrorCode.BUDGET_EXCEEDED
    entry_limits = EnvironmentSourceLimits(maximum_entries=PositiveCount(value=1))
    entry_result = _parse_dotenv(b"A=1\nB=2", entry_limits)
    assert entry_result.error is EnvironmentSnapshotErrorCode.BUDGET_EXCEEDED
    assert _dotenv_value('"unfinished\\"') is None
    assert _dotenv_value('"') is None
    assert _dotenv_value('"\\\\\\"\\r\\t"') == '\\"\r\t'


def test_path_and_error_classification_edges() -> None:
    limits = LocalFileLimits(
        maximum_path_bytes=ByteCount(value=4),
        maximum_segments=PositiveCount(value=1),
    )
    for path in (
        "",
        "a\x00b",
        "a\\b",
        "/a",
        "abcde",
        "a/b",
        ".",
        "..",
        "a//b",
        "\ud800",
    ):
        assert _path_parts(path, limits) is None
    assert _path_parts("file", limits) == ("file",)
    assert _read_error_code(errno.ENOENT) is ConfinedReadCode.NOT_FOUND
    denied = (
        errno.EACCES,
        errno.EINVAL,
        errno.EISDIR,
        errno.ELOOP,
        errno.ENAMETOOLONG,
        errno.ENOTDIR,
        errno.EPERM,
    )
    for number in denied:
        assert _read_error_code(number) is ConfinedReadCode.PATH_DENIED
    assert _read_error_code(errno.EIO) is ConfinedReadCode.IO_FAILED


def test_yaml_adapter_validates_limits_and_dependency_type() -> None:
    limits = YamlValueLimits()
    for field, value in (
        ("maximum_bytes", 0),
        ("maximum_depth", True),
        ("maximum_nodes", 0),
    ):
        with pytest.raises(ValueError, match="positive"):
            msgspec.structs.replace(limits, **{field: value})
    with pytest.raises(TypeError, match="YamlValueLimits"):
        SafeYamlValueDecoder(cast("YamlValueLimits", object()))
