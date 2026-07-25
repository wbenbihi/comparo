"""Cyclopts and Rich integration for the Comparo command-line interface."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version
from pathlib import Path
from typing import Annotated
from typing import Literal
from typing import TextIO

from cyclopts import App
from cyclopts import Parameter
from cyclopts.docs import generate_html_docs
from cyclopts.docs import generate_markdown_docs
from cyclopts.docs import generate_rst_docs
from cyclopts.exceptions import CycloptsError
from rich.console import Console
from rich.text import Text

from comparo.cli.contracts import CliColorMode
from comparo.cli.contracts import CliCommandResult
from comparo.cli.contracts import CliExitCode
from comparo.cli.contracts import CliInvocation
from comparo.cli.contracts import CliMessageLevel
from comparo.cli.contracts import CliOutputMode
from comparo.cli.contracts import CliPayloadKind
from comparo.cli.contracts import DoctorRequest
from comparo.cli.contracts import PlanRequest
from comparo.cli.contracts import ValidateRequest
from comparo.cli.services import CliServices
from comparo.cli.services import DefaultCliServices

type ColorName = Literal["auto", "always", "never"]
type OutputName = Literal["human", "json"]
type DocumentationFormat = Literal["markdown", "html", "rst"]
type CompletionShell = Literal["bash", "zsh", "fish"]

ColorOption = Annotated[ColorName, Parameter(name="--color")]
OutputOption = Annotated[OutputName, Parameter(name="--format")]
ConfigOption = Annotated[Path, Parameter(name="--config", alias="-C")]
SourceOption = Annotated[
    tuple[Path, ...],
    Parameter(name="--source", alias="-S", consume_multiple=False),
]
PolicyOption = Annotated[Path | None, Parameter(name="--policy")]
SubjectOption = Annotated[
    tuple[str, ...],
    Parameter(name="--subject", consume_multiple=False),
]
EnvironmentOption = Annotated[
    tuple[str, ...],
    Parameter(name="--environment", alias="--env", consume_multiple=False),
]
CandidateOption = Annotated[
    tuple[str, ...],
    Parameter(name="--candidate", consume_multiple=False),
]
MatrixOption = Annotated[
    tuple[str, ...],
    Parameter(name="--matrix", consume_multiple=False),
]
BudgetOption = Annotated[
    tuple[str, ...],
    Parameter(name="--budget", consume_multiple=False),
]
FailFastOption = Annotated[
    bool | None,
    Parameter(name="--fail-fast", negative="--no-fail-fast"),
]

_LEVEL_STYLES: dict[CliMessageLevel, str] = {
    CliMessageLevel.SUCCESS: "green",
    CliMessageLevel.INFO: "cyan",
    CliMessageLevel.WARNING: "yellow",
    CliMessageLevel.ERROR: "bold red",
}


def _package_version() -> str:
    """Return installed package metadata without consulting project configuration."""
    try:
        return version("comparo")
    except PackageNotFoundError:
        return "unknown"


def _invocation(result: CliCommandResult, color: ColorName) -> CliInvocation:
    """Pair a service result with its outer presentation preference."""
    return CliInvocation(result=result, color=CliColorMode(color))


def _document_result(payload: str, kind: CliPayloadKind) -> CliCommandResult:
    """Create a complete generated text document result."""
    return CliCommandResult(
        exit_code=CliExitCode.SUCCESS,
        payload_kind=kind,
        payload=payload.encode(),
    )


def _generate_reference(cli: App, output_format: DocumentationFormat) -> tuple[str, CliPayloadKind]:
    """Generate one reference format without erasing generator types."""
    if output_format == "markdown":
        return generate_markdown_docs(cli, usage_name="comparo"), CliPayloadKind.MARKDOWN
    if output_format == "html":
        return generate_html_docs(cli, usage_name="comparo"), CliPayloadKind.HTML
    return generate_rst_docs(cli, usage_name="comparo"), CliPayloadKind.RST


def build_app(services: CliServices) -> App:
    """Build an isolated command graph around injected framework-neutral services."""
    cli = App(
        name="comparo",
        help="Validate and compare HTTP behavior across environments.",
        version=_package_version,
        default_parameter=Parameter(negative=()),
        exit_on_error=False,
        print_error=False,
        result_action="return_value",
        suppress_keyboard_interrupt=False,
    )
    schema = App(name="schema", help="Print versioned public schemas and CLI documentation.")
    cli.command(schema)

    @schema.command(name="config")
    def schema_config(*, color: ColorOption = "auto") -> CliInvocation:
        """Print the comparo/v1alpha1 configuration JSON Schema.

        Args:
            color: Terminal color behavior for human messages.
        """
        return _invocation(services.configuration_schema(), color)

    @schema.command(name="artifact")
    def schema_artifact(*, color: ColorOption = "auto") -> CliInvocation:
        """Print the versioned RunArtifact JSON Schema.

        Args:
            color: Terminal color behavior for human messages.
        """
        return _invocation(services.artifact_schema(), color)

    @schema.command(name="report")
    def schema_report(*, color: ColorOption = "auto") -> CliInvocation:
        """Print the versioned JSON-report envelope schema.

        Args:
            color: Terminal color behavior for human messages.
        """
        return _invocation(services.report_schema(), color)

    @schema.command(name="policy")
    def schema_policy(*, color: ColorOption = "auto") -> CliInvocation:
        """Print the comparo.policy/v1alpha1 ExecutionPolicy JSON Schema.

        Args:
            color: Terminal color behavior for human messages.
        """
        return _invocation(services.policy_schema(), color)

    @schema.command(name="cli")
    def schema_cli(
        output_format: DocumentationFormat = "markdown",
        /,
        *,
        color: ColorOption = "auto",
    ) -> CliInvocation:
        """Print generated documentation for the installed command graph.

        Args:
            output_format: Documentation format to generate.
            color: Terminal color behavior for human messages.
        """
        payload, payload_kind = _generate_reference(build_app(services), output_format)
        return _invocation(
            _document_result(payload, payload_kind),
            color,
        )

    @cli.command(name="validate")
    def validate(
        *,
        config: ConfigOption = Path("comparo.yaml"),
        source: SourceOption = (),
        output_format: OutputOption = "human",
        color: ColorOption = "auto",
    ) -> CliInvocation:
        """Load and compile a project without resolving values or using the network.

        Args:
            config: Project manifest or directory containing comparo.yaml.
            source: Additional local YAML source; repeat the option to add more.
            output_format: Human or deterministic JSON output.
            color: Terminal color behavior for human messages.
        """
        request = ValidateRequest(
            config=config,
            additional_sources=source,
            output=CliOutputMode(output_format),
        )
        return _invocation(services.validate(request), color)

    @cli.command(name="doctor")
    def doctor(
        *,
        output_format: OutputOption = "human",
        color: ColorOption = "auto",
    ) -> CliInvocation:
        """Run deterministic offline checks of the sensitive-value boundary.

        Args:
            output_format: Human or deterministic JSON output.
            color: Terminal color behavior for human messages.
        """
        request = DoctorRequest(output=CliOutputMode(output_format))
        return _invocation(services.doctor(request), color)

    @cli.command(name="plan")
    def plan(
        *,
        config: ConfigOption = Path("comparo.yaml"),
        source: SourceOption = (),
        policy: PolicyOption = None,
        profile: str | None = None,
        subject: SubjectOption = (),
        environment: EnvironmentOption = (),
        baseline: str | None = None,
        candidate: CandidateOption = (),
        matrix: MatrixOption = (),
        trials: int | None = None,
        maximum_attempts: int | None = None,
        fail_fast: FailFastOption = None,
        budget: BudgetOption = (),
        output_format: OutputOption = "human",
        color: ColorOption = "auto",
    ) -> CliInvocation:
        """Compile and inspect a deterministic plan without resolving values or using the network.

        Args:
            config: Project manifest or directory containing comparo.yaml.
            source: Additional local YAML source; repeat the option to add more.
            policy: Explicit operator-owned ExecutionPolicy file; defaults to trusted-local.
            profile: ExecutionProfile id to apply.
            subject: Request or Scenario id; repeat to select more than one.
            environment: Environment id for an assertion run; repeat to select more than one.
            baseline: Baseline Environment id for a comparison run.
            candidate: Candidate Environment id; repeat to select more than one.
            matrix: Matrix override as BINDING=MATRIX; repeat for multiple bindings.
            trials: Positive trial-count override.
            maximum_attempts: Positive per-execution attempt limit.
            fail_fast: Enable fail-fast; use --no-fail-fast to disable it.
            budget: Planning budget as RESOURCE=LIMIT; repeat for multiple resources.
            output_format: Human or deterministic JSON output.
            color: Terminal color behavior for human messages.
        """
        request = PlanRequest(
            config=config,
            additional_sources=source,
            policy=policy,
            execution_profile=profile,
            subjects=subject,
            environments=environment,
            baseline=baseline,
            candidates=candidate,
            matrices=matrix,
            trials=trials,
            maximum_attempts=maximum_attempts,
            fail_fast=fail_fast,
            budgets=budget,
            output=CliOutputMode(output_format),
        )
        return _invocation(services.plan(request), color)

    @cli.command(name="completion")
    def completion(
        shell: CompletionShell,
        /,
        *,
        color: ColorOption = "auto",
    ) -> CliInvocation:
        """Generate a static completion script without modifying shell startup files.

        Args:
            shell: Shell syntax to generate.
            color: Terminal color behavior for human messages.
        """
        payload = cli.generate_completion(shell=shell)
        return _invocation(_document_result(payload, CliPayloadKind.TEXT), color)

    return cli


_DEFAULT_SERVICES = DefaultCliServices()
_DEFAULT_APP = build_app(_DEFAULT_SERVICES)


def generate_cli_reference(output_format: DocumentationFormat = "markdown") -> str:
    """Generate deterministic reference documentation from the command graph."""
    if output_format == "markdown":
        return generate_markdown_docs(_DEFAULT_APP, usage_name="comparo")
    if output_format == "html":
        return generate_html_docs(_DEFAULT_APP, usage_name="comparo")
    return generate_rst_docs(_DEFAULT_APP, usage_name="comparo")


def _console(stream: TextIO, color: CliColorMode) -> Console:
    """Create a terminal console with explicit, testable color behavior."""
    if color is CliColorMode.ALWAYS:
        return Console(
            file=stream,
            force_terminal=True,
            no_color=False,
            highlight=False,
            color_system="standard",
        )
    if color is CliColorMode.NEVER:
        return Console(file=stream, color_system=None, no_color=True, highlight=False)
    return Console(file=stream, highlight=False)


def _requested_color(tokens: tuple[str, ...]) -> CliColorMode:
    """Select help/error console color without exposing or interpreting other tokens."""
    for index, argument in enumerate(tokens):
        if argument.startswith("--color="):
            candidate = argument.partition("=")[2]
        elif argument == "--color" and index + 1 < len(tokens):
            candidate = tokens[index + 1]
        else:
            continue
        if candidate in {mode.value for mode in CliColorMode}:
            return CliColorMode(candidate)
    return CliColorMode.AUTO


def _render_result(result: CliCommandResult, output: Console, error: Console) -> None:
    """Render one complete result without interpreting payload content as markup."""
    if result.payload_kind is not CliPayloadKind.HUMAN:
        output.file.write(result.payload.decode())
        output.file.flush()
        return
    destination = output if result.exit_code is CliExitCode.SUCCESS else error
    for message in result.messages:
        destination.print(Text(message.text, style=_LEVEL_STYLES[message.level]))


def _render_static_error(console: Console, message: str) -> None:
    """Render a fixed adapter-owned error without exception or token text."""
    console.print(Text(message, style="bold red"))


def main(
    argv: Sequence[str] | None = None,
    *,
    services: CliServices | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI and return a stable exit code without raising parser exits."""
    tokens = tuple(sys.argv[1:] if argv is None else argv)
    color = _requested_color(tokens)
    output_console = _console(sys.stdout if stdout is None else stdout, color)
    error_console = _console(sys.stderr if stderr is None else stderr, color)
    current_app = _DEFAULT_APP if services is None else build_app(services)
    try:
        parsed = current_app(
            tokens,
            console=output_console,
            error_console=error_console,
            exit_on_error=False,
            print_error=False,
            result_action="return_value",
        )
    except KeyboardInterrupt:
        _render_static_error(error_console, "Interrupted.")
        return int(CliExitCode.INTERRUPTED)
    except CycloptsError:
        _render_static_error(error_console, "Invalid command invocation. Run with --help.")
        return int(CliExitCode.INVALID_INVOCATION)
    except Exception:  # noqa: BLE001 - the process boundary must fail closed without exception text.
        _render_static_error(error_console, "Comparo could not complete the command safely.")
        return int(CliExitCode.INTERNAL_ERROR)
    if parsed is None:
        return int(CliExitCode.SUCCESS)
    if type(parsed) is not CliInvocation:
        _render_static_error(error_console, "Comparo received an invalid command result.")
        return int(CliExitCode.INTERNAL_ERROR)
    _render_result(parsed.result, output_console, error_console)
    return int(parsed.result.exit_code)


__all__ = ["build_app", "generate_cli_reference", "main"]
