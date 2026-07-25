"""Tests for framework-neutral CLI contracts."""

from pathlib import Path
from typing import cast

import pytest

from comparo.cli.contracts import CliColorMode
from comparo.cli.contracts import CliCommandResult
from comparo.cli.contracts import CliExitCode
from comparo.cli.contracts import CliInvocation
from comparo.cli.contracts import CliMessage
from comparo.cli.contracts import CliMessageLevel
from comparo.cli.contracts import CliOutputMode
from comparo.cli.contracts import CliPayloadKind
from comparo.cli.contracts import DoctorRequest
from comparo.cli.contracts import ValidateRequest


def _message() -> CliMessage:
    return CliMessage(level=CliMessageLevel.INFO, text="safe")


def _human_result() -> CliCommandResult:
    return CliCommandResult(
        exit_code=CliExitCode.SUCCESS,
        payload_kind=CliPayloadKind.HUMAN,
        messages=(_message(),),
    )


def test_exit_codes_and_valid_contracts_are_stable() -> None:
    assert tuple(int(item) for item in CliExitCode) == (0, 1, 2, 3, 4, 130)
    human = _human_result()
    document = CliCommandResult(
        exit_code=CliExitCode.SUCCESS,
        payload_kind=CliPayloadKind.JSON,
        payload=b"{}\n",
    )
    request = ValidateRequest(
        config=Path("comparo.yaml"),
        additional_sources=(Path("extra.yaml"),),
        output=CliOutputMode.JSON,
    )
    doctor = DoctorRequest(output=CliOutputMode.HUMAN)
    invocation = CliInvocation(result=human, color=CliColorMode.AUTO)
    assert document.payload == b"{}\n"
    assert request.additional_sources == (Path("extra.yaml"),)
    assert doctor.output is CliOutputMode.HUMAN
    assert invocation.result is human


@pytest.mark.parametrize("text", ["", "x" * 4097, cast("str", 1)])
def test_message_rejects_invalid_text(text: str) -> None:
    with pytest.raises(ValueError, match="bounded"):
        CliMessage(level=CliMessageLevel.INFO, text=text)


def test_message_rejects_invalid_level() -> None:
    with pytest.raises(TypeError, match="level"):
        CliMessage(level=cast("CliMessageLevel", object()), text="safe")


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"exit_code": object()}, TypeError),
        ({"payload_kind": object()}, TypeError),
        ({"messages": []}, TypeError),
        ({"messages": (object(),)}, TypeError),
        ({"payload": "text"}, TypeError),
        ({"messages": (), "payload": b""}, ValueError),
        ({"payload": b"unexpected"}, ValueError),
        ({"payload_kind": CliPayloadKind.JSON, "payload": b""}, ValueError),
        (
            {
                "payload_kind": CliPayloadKind.JSON,
                "messages": (_message(),),
                "payload": b"{}",
            },
            ValueError,
        ),
    ],
)
def test_result_rejects_contradictory_representations(
    changes: dict[str, object],
    error: type[Exception],
) -> None:
    values: dict[str, object] = {
        "exit_code": CliExitCode.SUCCESS,
        "payload_kind": CliPayloadKind.HUMAN,
        "messages": (_message(),),
        "payload": b"",
    }
    values.update(changes)
    with pytest.raises(error):
        CliCommandResult(**values)  # type: ignore[arg-type]


def test_validate_request_rejects_untyped_fields() -> None:
    with pytest.raises(TypeError, match="config"):
        ValidateRequest(
            config=cast("Path", object()),
            additional_sources=(),
            output=CliOutputMode.HUMAN,
        )
    with pytest.raises(TypeError, match="sources"):
        ValidateRequest(
            config=Path("config.yaml"),
            additional_sources=cast("tuple[Path, ...]", []),
            output=CliOutputMode.HUMAN,
        )
    with pytest.raises(TypeError, match="sources"):
        ValidateRequest(
            config=Path("config.yaml"),
            additional_sources=(cast("Path", object()),),
            output=CliOutputMode.HUMAN,
        )
    with pytest.raises(TypeError, match="output"):
        ValidateRequest(
            config=Path("config.yaml"),
            additional_sources=(),
            output=cast("CliOutputMode", object()),
        )


def test_doctor_and_invocation_reject_untyped_fields() -> None:
    with pytest.raises(TypeError, match="output"):
        DoctorRequest(output=cast("CliOutputMode", object()))
    with pytest.raises(TypeError, match="result"):
        CliInvocation(
            result=cast("CliCommandResult", object()),
            color=CliColorMode.AUTO,
        )
    with pytest.raises(TypeError, match="color"):
        CliInvocation(result=_human_result(), color=cast("CliColorMode", object()))
