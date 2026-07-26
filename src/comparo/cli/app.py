"""Cyclopts and Rich integration for the Comparo command-line interface."""

from __future__ import annotations

import sys
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version
from pathlib import Path
from typing import Annotated
from typing import BinaryIO
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

from comparo.application import ArtifactReadLimits
from comparo.application import ReportArchiveLimits
from comparo.cli.contracts import DEFAULT_CLI_ARTIFACT_LIMITS_V1
from comparo.cli.contracts import DEFAULT_CLI_ARTIFACT_ROOT_V1
from comparo.cli.contracts import DEFAULT_CLI_BLOB_OUTPUT_BYTES_V1
from comparo.cli.contracts import DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1
from comparo.cli.contracts import DEFAULT_CLI_EXECUTION_LIMITS_V1
from comparo.cli.contracts import DEFAULT_CLI_RENDER_LIMITS_V1
from comparo.cli.contracts import ArtifactBlobRequest
from comparo.cli.contracts import ArtifactReadRequest
from comparo.cli.contracts import CliArtifactLimits
from comparo.cli.contracts import CliColorMode
from comparo.cli.contracts import CliCommandResult
from comparo.cli.contracts import CliConfigLoadLimits
from comparo.cli.contracts import CliExecutionCommandKind
from comparo.cli.contracts import CliExecutionLimits
from comparo.cli.contracts import CliExitCode
from comparo.cli.contracts import CliInvocation
from comparo.cli.contracts import CliMessageLevel
from comparo.cli.contracts import CliOutputMode
from comparo.cli.contracts import CliPayloadKind
from comparo.cli.contracts import CliRenderLimits
from comparo.cli.contracts import CliReportFormat
from comparo.cli.contracts import DoctorRequest
from comparo.cli.contracts import ExecutionRequest
from comparo.cli.contracts import PlanRequest
from comparo.cli.contracts import RenderRequest
from comparo.cli.contracts import ReportRequest
from comparo.cli.contracts import ValidateRequest
from comparo.cli.execution import production_execution_settings
from comparo.cli.loading import policy_load_limits
from comparo.cli.loading import project_load_limits
from comparo.cli.services import CliServices
from comparo.cli.services import DefaultCliServices
from comparo.tui.contracts import DEFAULT_TUI_MAXIMUM_INPUT_CHARACTERS_V1
from comparo.tui.contracts import DEFAULT_TUI_MAXIMUM_INPUT_ITEMS_V1
from comparo.tui.contracts import DEFAULT_TUI_MAXIMUM_NAVIGATION_FRAMES_V1
from comparo.tui.contracts import DEFAULT_TUI_MAXIMUM_OUTPUT_CHARACTERS_V1
from comparo.tui.contracts import DEFAULT_TUI_MAXIMUM_OUTPUT_ITEMS_V1
from comparo.tui.contracts import DEFAULT_TUI_MAXIMUM_OUTSTANDING_PREPARATIONS_V1
from comparo.tui.contracts import DEFAULT_TUI_MAXIMUM_REPORT_ENTRIES_V1
from comparo.tui.contracts import TuiInteractionLimits
from comparo.tui.contracts import TuiLaunchLimits
from comparo.tui.contracts import TuiLaunchRequest

type ColorName = Literal["auto", "always", "never"]
type OutputName = Literal["human", "json"]
type DocumentationFormat = Literal["markdown", "html", "rst"]
type CompletionShell = Literal["bash", "zsh", "fish"]
type ReportFormatName = Literal["json", "junit", "sarif", "markdown"]

ColorOption = Annotated[ColorName, Parameter(name="--color")]
OutputOption = Annotated[OutputName, Parameter(name="--format")]
ConfigOption = Annotated[Path, Parameter(name="--config", alias="-C")]
SourceOption = Annotated[
    tuple[Path, ...],
    Parameter(name="--source", alias="-S", consume_multiple=False),
]
PolicyOption = Annotated[
    Path | None,
    Parameter(
        name="--policy",
        help=(
            "External ExecutionPolicy path; omit to use the permissive built-in "
            "trusted-local authority."
        ),
    ),
]
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
ArtifactRootOption = Annotated[Path, Parameter(name="--root")]
BlobOutputLimitOption = Annotated[int, Parameter(name="--maximum-output-bytes")]
ExecutionArtifactRootOption = Annotated[Path, Parameter(name="--artifact-root")]
TuiArtifactRootOption = Annotated[
    Path,
    Parameter(
        name="--artifact-root",
        help=(
            "Project-relative archive created privately after admitted Start; "
            "absolute operator-managed archives must already exist."
        ),
    ),
]
MaximumReportEntriesOption = Annotated[int, Parameter(name="--maximum-report-entries")]
MaximumPreparationsOption = Annotated[
    int,
    Parameter(name="--maximum-outstanding-preparations"),
]
MaximumInputCharactersOption = Annotated[int, Parameter(name="--maximum-input-characters")]
MaximumInputItemsOption = Annotated[int, Parameter(name="--maximum-input-items")]
MaximumOutputCharactersOption = Annotated[int, Parameter(name="--maximum-output-characters")]
MaximumOutputItemsOption = Annotated[int, Parameter(name="--maximum-output-items")]
MaximumNavigationFramesOption = Annotated[
    int,
    Parameter(name="--maximum-navigation-frames-per-workspace"),
]
TuiPersistenceOption = Annotated[
    bool,
    Parameter(
        name="--persist",
        negative="--no-persist",
        help="Persist sanitized run reports; disable when policy forbids artifact storage.",
    ),
]


class _InvalidTuiLaunchError(Exception):
    """Value-free marker for invalid typed TUI launch limits."""


@dataclass(frozen=True, kw_only=True)
class _ArtifactLimitArguments:
    """Cyclopts-only flattened form of the version-owned artifact limit contract."""

    maximum_blobs: Annotated[
        int,
        Parameter(name="--maximum-blobs", help="Maximum blobs admitted in one artifact bundle."),
    ] = DEFAULT_CLI_ARTIFACT_LIMITS_V1.maximum_blobs
    maximum_blob_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-blob-bytes",
            help="Maximum bytes admitted for one artifact blob.",
        ),
    ] = DEFAULT_CLI_ARTIFACT_LIMITS_V1.maximum_blob_bytes
    maximum_total_blob_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-total-blob-bytes",
            help="Maximum aggregate blob bytes admitted per artifact bundle.",
        ),
    ] = DEFAULT_CLI_ARTIFACT_LIMITS_V1.maximum_total_blob_bytes
    maximum_manifest_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-manifest-bytes",
            help="Maximum artifact manifest bytes admitted.",
        ),
    ] = DEFAULT_CLI_ARTIFACT_LIMITS_V1.maximum_manifest_bytes
    maximum_report_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-report-bytes",
            help="Maximum bytes produced by one artifact report.",
        ),
    ] = DEFAULT_CLI_ARTIFACT_LIMITS_V1.maximum_report_bytes
    maximum_redaction_patterns: Annotated[
        int,
        Parameter(
            name="--maximum-redaction-patterns",
            help="Maximum sensitive literal patterns admitted for redaction.",
        ),
    ] = DEFAULT_CLI_ARTIFACT_LIMITS_V1.maximum_redaction_patterns
    maximum_redaction_pattern_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-redaction-pattern-bytes",
            help="Maximum aggregate bytes admitted for redaction patterns.",
        ),
    ] = DEFAULT_CLI_ARTIFACT_LIMITS_V1.maximum_redaction_pattern_bytes
    maximum_redaction_value_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-redaction-value-bytes",
            help="Maximum bytes inspected in one value during redaction.",
        ),
    ] = DEFAULT_CLI_ARTIFACT_LIMITS_V1.maximum_redaction_value_bytes
    maximum_redaction_value_nodes: Annotated[
        int,
        Parameter(
            name="--maximum-redaction-value-nodes",
            help="Maximum structured nodes inspected in one value during redaction.",
        ),
    ] = DEFAULT_CLI_ARTIFACT_LIMITS_V1.maximum_redaction_value_nodes


ArtifactLimitsOption = Annotated[_ArtifactLimitArguments, Parameter(name="*")]
_DEFAULT_ARTIFACT_LIMIT_ARGUMENTS = _ArtifactLimitArguments()


@dataclass(frozen=True, kw_only=True)
class _ConfigLoadLimitArguments:
    """Cyclopts-only flattened form of version-owned project-load limits."""

    maximum_sources: Annotated[
        int,
        Parameter(
            name="--maximum-config-sources",
            help="Maximum project YAML sources discovered and admitted.",
        ),
    ] = DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1.maximum_sources
    maximum_discovery_entries: Annotated[
        int,
        Parameter(
            name="--maximum-config-discovery-entries",
            help="Maximum filesystem entries examined during project source discovery.",
        ),
    ] = DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1.maximum_discovery_entries
    maximum_total_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-config-total-bytes",
            help="Maximum aggregate bytes admitted across all project YAML sources.",
        ),
    ] = DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1.maximum_total_bytes
    maximum_total_documents: Annotated[
        int,
        Parameter(
            name="--maximum-config-total-documents",
            help="Maximum aggregate YAML documents admitted across the project.",
        ),
    ] = DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1.maximum_total_documents
    maximum_total_nodes: Annotated[
        int,
        Parameter(
            name="--maximum-config-total-nodes",
            help="Maximum aggregate YAML nodes retained across the project.",
        ),
    ] = DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1.maximum_total_nodes
    maximum_resources: Annotated[
        int,
        Parameter(
            name="--maximum-config-resources",
            help="Maximum compiled resource documents admitted across the project.",
        ),
    ] = DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1.maximum_resources
    maximum_yaml_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-config-yaml-bytes",
            help="Maximum bytes admitted from one project YAML source.",
        ),
    ] = DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1.maximum_yaml_bytes
    maximum_yaml_documents: Annotated[
        int,
        Parameter(
            name="--maximum-config-yaml-documents",
            help="Maximum documents admitted from one project YAML source.",
        ),
    ] = DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1.maximum_yaml_documents
    maximum_yaml_depth: Annotated[
        int,
        Parameter(
            name="--maximum-config-yaml-depth",
            help="Maximum nesting depth admitted in one project YAML source.",
        ),
    ] = DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1.maximum_yaml_depth
    maximum_yaml_nodes: Annotated[
        int,
        Parameter(
            name="--maximum-config-yaml-nodes",
            help="Maximum nodes admitted from one project YAML source.",
        ),
    ] = DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1.maximum_yaml_nodes
    maximum_policy_yaml_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-policy-yaml-bytes",
            help="Maximum bytes admitted from one explicit ExecutionPolicy YAML source.",
        ),
    ] = DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1.maximum_policy_yaml_bytes
    maximum_policy_yaml_depth: Annotated[
        int,
        Parameter(
            name="--maximum-policy-yaml-depth",
            help="Maximum nesting depth admitted in an ExecutionPolicy YAML source.",
        ),
    ] = DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1.maximum_policy_yaml_depth
    maximum_policy_yaml_nodes: Annotated[
        int,
        Parameter(
            name="--maximum-policy-yaml-nodes",
            help="Maximum nodes admitted from one ExecutionPolicy YAML source.",
        ),
    ] = DEFAULT_CLI_CONFIG_LOAD_LIMITS_V1.maximum_policy_yaml_nodes


ConfigLoadLimitsOption = Annotated[_ConfigLoadLimitArguments, Parameter(name="*")]
_DEFAULT_CONFIG_LOAD_LIMIT_ARGUMENTS = _ConfigLoadLimitArguments()


@dataclass(frozen=True, kw_only=True)
class _ExecutionArguments:
    """Cyclopts-only flattened arguments common to every execution journey."""

    config: Annotated[
        Path,
        Parameter(
            name="--config",
            alias="-C",
            help="Project manifest or directory containing comparo.yaml.",
        ),
    ] = Path("comparo.yaml")
    source: Annotated[
        tuple[Path, ...],
        Parameter(
            name="--source",
            alias="-S",
            consume_multiple=False,
            help="Additional local YAML source; repeat to add more.",
        ),
    ] = ()
    policy: Annotated[
        Path | None,
        Parameter(
            name="--policy",
            help=(
                "External ExecutionPolicy path; omit to use the permissive built-in "
                "trusted-local authority."
            ),
        ),
    ] = None
    subject: Annotated[
        tuple[str, ...],
        Parameter(
            name="--subject",
            consume_multiple=False,
            help="Request id; repeat to select multiple executable subjects.",
        ),
    ] = ()
    matrix: Annotated[
        tuple[str, ...],
        Parameter(
            name="--matrix",
            consume_multiple=False,
            help="Matrix override as BINDING=MATRIX; repeat to add bindings.",
        ),
    ] = ()
    trials: Annotated[
        int | None,
        Parameter(name="--trials", help="Positive trial-count override."),
    ] = None
    maximum_attempts: Annotated[
        int | None,
        Parameter(
            name="--maximum-attempts",
            help="Positive maximum-attempt override.",
        ),
    ] = None
    fail_fast: Annotated[
        bool | None,
        Parameter(
            name="--fail-fast",
            negative="--no-fail-fast",
            help="Enable or disable fail-fast execution.",
        ),
    ] = None
    budget: Annotated[
        tuple[str, ...],
        Parameter(
            name="--budget",
            consume_multiple=False,
            help="Budget override as RESOURCE=LIMIT; repeat to add resources.",
        ),
    ] = ()
    parameters_from: Annotated[
        tuple[str, ...],
        Parameter(
            name="--parameters-from",
            consume_multiple=False,
            help="Value resource as [ENV=]VALUE supplying invocation parameters; repeatable.",
        ),
    ] = ()
    variables_from: Annotated[
        tuple[str, ...],
        Parameter(
            name="--variables-from",
            consume_multiple=False,
            help="Value resource as [ENV=]VALUE supplying invocation variables; repeatable.",
        ),
    ] = ()
    secrets_from: Annotated[
        tuple[str, ...],
        Parameter(
            name="--secrets-from",
            consume_multiple=False,
            help="Value resource as [ENV=]VALUE supplying invocation secrets; repeatable.",
        ),
    ] = ()
    approve_mutations: Annotated[
        bool,
        Parameter(
            name="--approve-mutations",
            help="Approve requests classified as mutating by the effective policy.",
        ),
    ] = False
    persist: Annotated[
        bool,
        Parameter(
            name="--persist",
            negative="--no-persist",
            help="Persist a sanitized artifact bundle; disabled by default.",
        ),
    ] = False
    artifact_root: Annotated[
        Path,
        Parameter(
            name="--artifact-root",
            help=("Existing local directory for explicitly persisted artifact bundles."),
        ),
    ] = DEFAULT_CLI_ARTIFACT_ROOT_V1
    output_format: Annotated[
        OutputName,
        Parameter(name="--format", help="Human or deterministic JSON output."),
    ] = "human"
    color: Annotated[
        ColorName,
        Parameter(name="--color", help="Terminal color behavior for human messages."),
    ] = "auto"


ExecutionArgumentsOption = Annotated[_ExecutionArguments, Parameter(name="*")]
_DEFAULT_EXECUTION_ARGUMENTS = _ExecutionArguments()


@dataclass(frozen=True, kw_only=True)
class _RuntimeLimitArguments:
    """Cyclopts-only flattened version-owned runtime execution limits."""

    body_memory_threshold_bytes: Annotated[
        int,
        Parameter(
            name="--body-memory-threshold-bytes",
            help="Per-body byte threshold before transient storage spills to disk.",
        ),
    ] = DEFAULT_CLI_EXECUTION_LIMITS_V1.body_memory_threshold_bytes
    maximum_transient_body_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-transient-body-bytes",
            help="Maximum aggregate bytes retained in the run-local body store.",
        ),
    ] = DEFAULT_CLI_EXECUTION_LIMITS_V1.maximum_transient_body_bytes
    maximum_authorization_occurrences: Annotated[
        int,
        Parameter(
            name="--maximum-authorization-occurrences",
            help="Maximum distinct HTTP authorization occurrences retained per run.",
        ),
    ] = DEFAULT_CLI_EXECUTION_LIMITS_V1.maximum_authorization_occurrences
    maximum_encoded_body_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-encoded-body-bytes",
            help="Maximum encoded response bytes captured per exchange.",
        ),
    ] = DEFAULT_CLI_EXECUTION_LIMITS_V1.maximum_encoded_body_bytes
    maximum_entity_body_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-entity-body-bytes",
            help="Maximum decoded entity bytes captured per exchange.",
        ),
    ] = DEFAULT_CLI_EXECUTION_LIMITS_V1.maximum_entity_body_bytes
    maximum_expansion_ratio: Annotated[
        int,
        Parameter(
            name="--maximum-expansion-ratio",
            help="Maximum decoded-to-encoded response expansion ratio.",
        ),
    ] = DEFAULT_CLI_EXECUTION_LIMITS_V1.maximum_expansion_ratio
    maximum_stream_records: Annotated[
        int,
        Parameter(
            name="--maximum-stream-records",
            help="Maximum data records retained from one streaming response.",
        ),
    ] = DEFAULT_CLI_EXECUTION_LIMITS_V1.maximum_stream_records
    maximum_stream_controls: Annotated[
        int,
        Parameter(
            name="--maximum-stream-controls",
            help="Maximum control records retained from one streaming response.",
        ),
    ] = DEFAULT_CLI_EXECUTION_LIMITS_V1.maximum_stream_controls
    maximum_stream_errors: Annotated[
        int,
        Parameter(
            name="--maximum-stream-errors",
            help="Maximum decoding errors retained from one streaming response.",
        ),
    ] = DEFAULT_CLI_EXECUTION_LIMITS_V1.maximum_stream_errors
    maximum_stream_item_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-stream-item-bytes",
            help="Maximum bytes admitted for one decoded stream item.",
        ),
    ] = DEFAULT_CLI_EXECUTION_LIMITS_V1.maximum_stream_item_bytes
    maximum_decoded_body_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-decoded-body-bytes",
            help="Maximum ordinary response bytes admitted for semantic decoding.",
        ),
    ] = DEFAULT_CLI_EXECUTION_LIMITS_V1.maximum_decoded_body_bytes
    maximum_text_characters: Annotated[
        int,
        Parameter(
            name="--maximum-text-characters",
            help="Maximum text characters decoded from one ordinary response.",
        ),
    ] = DEFAULT_CLI_EXECUTION_LIMITS_V1.maximum_text_characters
    maximum_json_depth: Annotated[
        int,
        Parameter(
            name="--maximum-json-depth",
            help="Maximum nesting depth for an ordinary JSON response.",
        ),
    ] = DEFAULT_CLI_EXECUTION_LIMITS_V1.maximum_json_depth
    maximum_json_nodes: Annotated[
        int,
        Parameter(
            name="--maximum-json-nodes",
            help="Maximum nodes decoded from one ordinary JSON response.",
        ),
    ] = DEFAULT_CLI_EXECUTION_LIMITS_V1.maximum_json_nodes
    maximum_json_tokens: Annotated[
        int,
        Parameter(
            name="--maximum-json-tokens",
            help="Maximum tokens parsed from one ordinary JSON response.",
        ),
    ] = DEFAULT_CLI_EXECUTION_LIMITS_V1.maximum_json_tokens


RuntimeLimitsOption = Annotated[_RuntimeLimitArguments, Parameter(name="*")]
_DEFAULT_RUNTIME_LIMIT_ARGUMENTS = _RuntimeLimitArguments()


@dataclass(frozen=True, kw_only=True)
class _RenderLimitArguments:
    """Cyclopts-only flattened version-owned offline render limits."""

    maximum_file_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-file-bytes",
            help="Maximum bytes read from one project file value.",
        ),
    ] = DEFAULT_CLI_RENDER_LIMITS_V1.maximum_file_bytes
    maximum_file_path_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-file-path-bytes",
            help="Maximum encoded bytes admitted in one project-relative file path.",
        ),
    ] = DEFAULT_CLI_RENDER_LIMITS_V1.maximum_file_path_bytes
    maximum_file_segments: Annotated[
        int,
        Parameter(
            name="--maximum-file-segments",
            help="Maximum segments admitted in one project-relative file path.",
        ),
    ] = DEFAULT_CLI_RENDER_LIMITS_V1.maximum_file_segments
    maximum_environment_files: Annotated[
        int,
        Parameter(
            name="--maximum-environment-files",
            help="Maximum dotenv files admitted for one Environment.",
        ),
    ] = DEFAULT_CLI_RENDER_LIMITS_V1.maximum_environment_files
    maximum_environment_file_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-environment-file-bytes",
            help="Maximum bytes read from one Environment dotenv file.",
        ),
    ] = DEFAULT_CLI_RENDER_LIMITS_V1.maximum_environment_file_bytes
    maximum_environment_total_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-environment-total-bytes",
            help="Maximum aggregate dotenv bytes admitted for one Environment.",
        ),
    ] = DEFAULT_CLI_RENDER_LIMITS_V1.maximum_environment_total_bytes
    maximum_environment_entries: Annotated[
        int,
        Parameter(
            name="--maximum-environment-entries",
            help="Maximum dotenv entries admitted for one Environment.",
        ),
    ] = DEFAULT_CLI_RENDER_LIMITS_V1.maximum_environment_entries
    maximum_environment_line_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-environment-line-bytes",
            help="Maximum bytes admitted in one dotenv line.",
        ),
    ] = DEFAULT_CLI_RENDER_LIMITS_V1.maximum_environment_line_bytes
    maximum_yaml_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-yaml-bytes",
            help="Maximum bytes admitted for one explicit YAML value conversion.",
        ),
    ] = DEFAULT_CLI_RENDER_LIMITS_V1.maximum_yaml_bytes
    maximum_yaml_depth: Annotated[
        int,
        Parameter(
            name="--maximum-yaml-depth",
            help="Maximum nesting depth for one explicit YAML value conversion.",
        ),
    ] = DEFAULT_CLI_RENDER_LIMITS_V1.maximum_yaml_depth
    maximum_yaml_nodes: Annotated[
        int,
        Parameter(
            name="--maximum-yaml-nodes",
            help="Maximum nodes decoded by one explicit YAML value conversion.",
        ),
    ] = DEFAULT_CLI_RENDER_LIMITS_V1.maximum_yaml_nodes
    maximum_redaction_patterns: Annotated[
        int,
        Parameter(
            name="--maximum-redaction-patterns",
            help="Maximum sensitive literal patterns admitted for preview redaction.",
        ),
    ] = DEFAULT_CLI_RENDER_LIMITS_V1.maximum_redaction_patterns
    maximum_redaction_pattern_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-redaction-pattern-bytes",
            help="Maximum aggregate bytes admitted for preview redaction patterns.",
        ),
    ] = DEFAULT_CLI_RENDER_LIMITS_V1.maximum_redaction_pattern_bytes
    maximum_redaction_value_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-redaction-value-bytes",
            help="Maximum bytes inspected in one preview value during redaction.",
        ),
    ] = DEFAULT_CLI_RENDER_LIMITS_V1.maximum_redaction_value_bytes
    maximum_redaction_value_nodes: Annotated[
        int,
        Parameter(
            name="--maximum-redaction-value-nodes",
            help="Maximum structured nodes inspected in one preview value.",
        ),
    ] = DEFAULT_CLI_RENDER_LIMITS_V1.maximum_redaction_value_nodes
    maximum_output_bytes: Annotated[
        int,
        Parameter(
            name="--maximum-output-bytes",
            help="Maximum bytes emitted by the complete rendered preview.",
        ),
    ] = DEFAULT_CLI_RENDER_LIMITS_V1.maximum_output_bytes


RenderLimitsOption = Annotated[_RenderLimitArguments, Parameter(name="*")]
_DEFAULT_RENDER_LIMIT_ARGUMENTS = _RenderLimitArguments()

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


def _artifact_limits(value: _ArtifactLimitArguments) -> CliArtifactLimits:
    """Project framework-owned parsed arguments to the framework-neutral contract."""
    return CliArtifactLimits(
        maximum_blobs=value.maximum_blobs,
        maximum_blob_bytes=value.maximum_blob_bytes,
        maximum_total_blob_bytes=value.maximum_total_blob_bytes,
        maximum_manifest_bytes=value.maximum_manifest_bytes,
        maximum_report_bytes=value.maximum_report_bytes,
        maximum_redaction_patterns=value.maximum_redaction_patterns,
        maximum_redaction_pattern_bytes=value.maximum_redaction_pattern_bytes,
        maximum_redaction_value_bytes=value.maximum_redaction_value_bytes,
        maximum_redaction_value_nodes=value.maximum_redaction_value_nodes,
    )


def _artifact_read_limits(value: _ArtifactLimitArguments) -> ArtifactReadLimits:
    """Project CLI artifact ceilings into the application report-reader contract."""
    limits = _artifact_limits(value)
    return ArtifactReadLimits(
        maximum_blobs=limits.maximum_blobs,
        maximum_blob_bytes=limits.maximum_blob_bytes,
        maximum_total_blob_bytes=limits.maximum_total_blob_bytes,
        maximum_manifest_bytes=limits.maximum_manifest_bytes,
        maximum_report_bytes=limits.maximum_report_bytes,
        maximum_redaction_patterns=limits.maximum_redaction_patterns,
        maximum_redaction_pattern_bytes=limits.maximum_redaction_pattern_bytes,
        maximum_redaction_value_bytes=limits.maximum_redaction_value_bytes,
        maximum_redaction_value_nodes=limits.maximum_redaction_value_nodes,
    )


def _execution_limits(
    runtime: _RuntimeLimitArguments,
    artifact: _ArtifactLimitArguments,
) -> CliExecutionLimits:
    """Project all caller-visible runtime ceilings into one immutable contract."""
    return CliExecutionLimits(
        body_memory_threshold_bytes=runtime.body_memory_threshold_bytes,
        maximum_transient_body_bytes=runtime.maximum_transient_body_bytes,
        maximum_authorization_occurrences=runtime.maximum_authorization_occurrences,
        maximum_encoded_body_bytes=runtime.maximum_encoded_body_bytes,
        maximum_entity_body_bytes=runtime.maximum_entity_body_bytes,
        maximum_expansion_ratio=runtime.maximum_expansion_ratio,
        maximum_stream_records=runtime.maximum_stream_records,
        maximum_stream_controls=runtime.maximum_stream_controls,
        maximum_stream_errors=runtime.maximum_stream_errors,
        maximum_stream_item_bytes=runtime.maximum_stream_item_bytes,
        maximum_decoded_body_bytes=runtime.maximum_decoded_body_bytes,
        maximum_text_characters=runtime.maximum_text_characters,
        maximum_json_depth=runtime.maximum_json_depth,
        maximum_json_nodes=runtime.maximum_json_nodes,
        maximum_json_tokens=runtime.maximum_json_tokens,
        artifact=_artifact_limits(artifact),
    )


def _config_load_limits(value: _ConfigLoadLimitArguments) -> CliConfigLoadLimits:
    """Project every caller-visible project-load ceiling into the CLI contract."""
    return CliConfigLoadLimits(
        maximum_sources=value.maximum_sources,
        maximum_discovery_entries=value.maximum_discovery_entries,
        maximum_total_bytes=value.maximum_total_bytes,
        maximum_total_documents=value.maximum_total_documents,
        maximum_total_nodes=value.maximum_total_nodes,
        maximum_resources=value.maximum_resources,
        maximum_yaml_bytes=value.maximum_yaml_bytes,
        maximum_yaml_documents=value.maximum_yaml_documents,
        maximum_yaml_depth=value.maximum_yaml_depth,
        maximum_yaml_nodes=value.maximum_yaml_nodes,
        maximum_policy_yaml_bytes=value.maximum_policy_yaml_bytes,
        maximum_policy_yaml_depth=value.maximum_policy_yaml_depth,
        maximum_policy_yaml_nodes=value.maximum_policy_yaml_nodes,
    )


def _execution_request(
    command: CliExecutionCommandKind,
    arguments: _ExecutionArguments,
    runtime_limits: _RuntimeLimitArguments,
    artifact_limits: _ArtifactLimitArguments,
    config_load_limits: _ConfigLoadLimitArguments,
    *,
    execution_profile: str | None,
    environments: tuple[str, ...],
    baseline: str | None,
    candidates: tuple[str, ...],
) -> ExecutionRequest:
    """Build the one shared service request without interpreting topology in the adapter."""
    plan = PlanRequest(
        config=arguments.config,
        additional_sources=arguments.source,
        policy=arguments.policy,
        execution_profile=execution_profile,
        subjects=arguments.subject,
        environments=environments,
        baseline=baseline,
        candidates=candidates,
        matrices=arguments.matrix,
        trials=arguments.trials,
        maximum_attempts=arguments.maximum_attempts,
        fail_fast=arguments.fail_fast,
        budgets=arguments.budget,
        output=CliOutputMode(arguments.output_format),
        load_limits=_config_load_limits(config_load_limits),
        parameters_from=arguments.parameters_from,
        variables_from=arguments.variables_from,
        secrets_from=arguments.secrets_from,
    )
    return ExecutionRequest(
        command=command,
        plan=plan,
        approve_mutations=arguments.approve_mutations,
        persist=arguments.persist,
        artifact_root=arguments.artifact_root,
        limits=_execution_limits(runtime_limits, artifact_limits),
        tool_version=_package_version(),
    )


def _register_execution_commands(cli: App, services: CliServices) -> None:
    """Attach all effectful journeys as thin adapters over one service operation."""

    @cli.command(name="run")
    def run(
        *,
        environment: EnvironmentOption = (),
        arguments: ExecutionArgumentsOption = _DEFAULT_EXECUTION_ARGUMENTS,
        config_load_limits: ConfigLoadLimitsOption = _DEFAULT_CONFIG_LOAD_LIMIT_ARGUMENTS,
        runtime_limits: RuntimeLimitsOption = _DEFAULT_RUNTIME_LIMIT_ARGUMENTS,
        artifact_limits: ArtifactLimitsOption = _DEFAULT_ARTIFACT_LIMIT_ARGUMENTS,
    ) -> CliInvocation:
        """Execute assertions against one or more Environments.

        Args:
            environment: Environment id; repeat to override project/profile topology.
            arguments: Shared planning, policy, persistence, and output controls.
            config_load_limits: Version-owned project-source and YAML admission ceilings.
            runtime_limits: Version-owned storage, capture, stream, and decode ceilings.
            artifact_limits: Version-owned artifact and sanitization ceilings.
        """
        request = _execution_request(
            CliExecutionCommandKind.RUN,
            arguments,
            runtime_limits,
            artifact_limits,
            config_load_limits,
            execution_profile=None,
            environments=environment,
            baseline=None,
            candidates=(),
        )
        return _invocation(services.execute(request), arguments.color)

    @cli.command(name="diff")
    def diff(
        *,
        baseline: str | None = None,
        candidate: CandidateOption = (),
        arguments: ExecutionArgumentsOption = _DEFAULT_EXECUTION_ARGUMENTS,
        config_load_limits: ConfigLoadLimitsOption = _DEFAULT_CONFIG_LOAD_LIMIT_ARGUMENTS,
        runtime_limits: RuntimeLimitsOption = _DEFAULT_RUNTIME_LIMIT_ARGUMENTS,
        artifact_limits: ArtifactLimitsOption = _DEFAULT_ARTIFACT_LIMIT_ARGUMENTS,
    ) -> CliInvocation:
        """Execute and compare a baseline Environment with one or more candidates.

        Args:
            baseline: Baseline Environment id overriding project/profile topology.
            candidate: Candidate Environment id; repeat to add candidates.
            arguments: Shared planning, policy, persistence, and output controls.
            config_load_limits: Version-owned project-source and YAML admission ceilings.
            runtime_limits: Version-owned storage, capture, stream, and decode ceilings.
            artifact_limits: Version-owned artifact and sanitization ceilings.
        """
        request = _execution_request(
            CliExecutionCommandKind.DIFF,
            arguments,
            runtime_limits,
            artifact_limits,
            config_load_limits,
            execution_profile=None,
            environments=(),
            baseline=baseline,
            candidates=candidate,
        )
        return _invocation(services.execute(request), arguments.color)

    @cli.command(name="exec")
    def execute_profile(
        profile: str,
        /,
        *,
        environment: EnvironmentOption = (),
        baseline: str | None = None,
        candidate: CandidateOption = (),
        arguments: ExecutionArgumentsOption = _DEFAULT_EXECUTION_ARGUMENTS,
        config_load_limits: ConfigLoadLimitsOption = _DEFAULT_CONFIG_LOAD_LIMIT_ARGUMENTS,
        runtime_limits: RuntimeLimitsOption = _DEFAULT_RUNTIME_LIMIT_ARGUMENTS,
        artifact_limits: ArtifactLimitsOption = _DEFAULT_ARTIFACT_LIMIT_ARGUMENTS,
    ) -> CliInvocation:
        """Execute an ExecutionProfile with optional assertion or comparison overrides.

        Args:
            profile: ExecutionProfile id selected from project configuration.
            environment: Environment id; repeat to select assertion topology.
            baseline: Baseline Environment id selecting comparison topology.
            candidate: Candidate Environment id; repeat to add candidates.
            arguments: Shared planning, policy, persistence, and output controls.
            config_load_limits: Version-owned project-source and YAML admission ceilings.
            runtime_limits: Version-owned storage, capture, stream, and decode ceilings.
            artifact_limits: Version-owned artifact and sanitization ceilings.
        """
        request = _execution_request(
            CliExecutionCommandKind.EXEC,
            arguments,
            runtime_limits,
            artifact_limits,
            config_load_limits,
            execution_profile=profile,
            environments=environment,
            baseline=baseline,
            candidates=candidate,
        )
        return _invocation(services.execute(request), arguments.color)


def _render_limits(value: _RenderLimitArguments) -> CliRenderLimits:
    """Project every caller-visible render ceiling into one immutable contract."""
    return CliRenderLimits(
        maximum_file_bytes=value.maximum_file_bytes,
        maximum_file_path_bytes=value.maximum_file_path_bytes,
        maximum_file_segments=value.maximum_file_segments,
        maximum_environment_files=value.maximum_environment_files,
        maximum_environment_file_bytes=value.maximum_environment_file_bytes,
        maximum_environment_total_bytes=value.maximum_environment_total_bytes,
        maximum_environment_entries=value.maximum_environment_entries,
        maximum_environment_line_bytes=value.maximum_environment_line_bytes,
        maximum_yaml_bytes=value.maximum_yaml_bytes,
        maximum_yaml_depth=value.maximum_yaml_depth,
        maximum_yaml_nodes=value.maximum_yaml_nodes,
        maximum_redaction_patterns=value.maximum_redaction_patterns,
        maximum_redaction_pattern_bytes=value.maximum_redaction_pattern_bytes,
        maximum_redaction_value_bytes=value.maximum_redaction_value_bytes,
        maximum_redaction_value_nodes=value.maximum_redaction_value_nodes,
        maximum_output_bytes=value.maximum_output_bytes,
    )


def _register_render_command(cli: App, services: CliServices) -> None:
    """Attach the offline request-preview journey to the command graph."""

    @cli.command(name="render")
    def render(
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
        parameters_from: Annotated[
            tuple[str, ...],
            Parameter(name="--parameters-from", consume_multiple=False),
        ] = (),
        variables_from: Annotated[
            tuple[str, ...],
            Parameter(name="--variables-from", consume_multiple=False),
        ] = (),
        secrets_from: Annotated[
            tuple[str, ...],
            Parameter(name="--secrets-from", consume_multiple=False),
        ] = (),
        config_load_limits: ConfigLoadLimitsOption = _DEFAULT_CONFIG_LOAD_LIMIT_ARGUMENTS,
        limits: RenderLimitsOption = _DEFAULT_RENDER_LIMIT_ARGUMENTS,
        output_format: OutputOption = "human",
        color: ColorOption = "auto",
    ) -> CliInvocation:
        """Resolve and sanitize planned requests without DNS or network activity.

        Args:
            config: Project manifest or directory containing comparo.yaml.
            source: Additional local YAML source; repeat the option to add more.
            policy: External ExecutionPolicy path; omission uses permissive trusted-local authority.
            profile: ExecutionProfile id overriding project defaults.
            subject: Request id; repeat to select multiple executable subjects.
            environment: Environment id; repeat to select assertion topology.
            baseline: Baseline Environment id selecting comparison topology.
            candidate: Candidate Environment id; repeat to add candidates.
            matrix: Matrix override as BINDING=MATRIX; repeat to add bindings.
            trials: Positive trial-count override.
            maximum_attempts: Positive maximum-attempt override.
            fail_fast: Enable or disable fail-fast planning.
            budget: Budget override as RESOURCE=LIMIT; repeat to add resources.
            parameters_from: Value resource as [ENV=]VALUE supplying invocation parameters.
            variables_from: Value resource as [ENV=]VALUE supplying invocation variables.
            secrets_from: Value resource as [ENV=]VALUE supplying invocation secrets.
            config_load_limits: Version-owned project-source and YAML admission ceilings.
            limits: Version-owned local-source, sanitization, and output ceilings.
            output_format: Human text or deterministic JSON output.
            color: Terminal color behavior for failures.
        """
        request = RenderRequest(
            plan=PlanRequest(
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
                load_limits=_config_load_limits(config_load_limits),
                parameters_from=parameters_from,
                variables_from=variables_from,
                secrets_from=secrets_from,
            ),
            limits=_render_limits(limits),
        )
        return _invocation(services.render(request), color)


def _register_artifact_commands(cli: App, services: CliServices) -> None:
    """Attach artifact readers without adding their concerns to the main command graph."""
    artifact = App(name="artifact", help="Read authenticated sanitized artifact bundles.")
    cli.command(artifact)

    @artifact.command(name="inspect")
    def artifact_inspect(
        bundle_id: str,
        /,
        *,
        root: ArtifactRootOption = DEFAULT_CLI_ARTIFACT_ROOT_V1,
        limits: ArtifactLimitsOption = _DEFAULT_ARTIFACT_LIMIT_ARGUMENTS,
        output_format: OutputOption = "human",
        color: ColorOption = "auto",
    ) -> CliInvocation:
        """Authenticate a bundle and print a body-free result summary.

        Args:
            bundle_id: Artifact bundle id under the selected root.
            root: Local artifact-store root.
            limits: Version-owned finite read limits, individually overridable.
            output_format: Human or deterministic JSON output.
            color: Terminal color behavior for human messages.
        """
        request = ArtifactReadRequest(
            root=root,
            bundle_id=bundle_id,
            limits=_artifact_limits(limits),
            output=CliOutputMode(output_format),
        )
        return _invocation(services.artifact_inspect(request), color)

    @artifact.command(name="verify")
    def artifact_verify(
        bundle_id: str,
        /,
        *,
        root: ArtifactRootOption = DEFAULT_CLI_ARTIFACT_ROOT_V1,
        limits: ArtifactLimitsOption = _DEFAULT_ARTIFACT_LIMIT_ARGUMENTS,
        output_format: OutputOption = "human",
        color: ColorOption = "auto",
    ) -> CliInvocation:
        """Authenticate a bundle manifest and every declared sanitized blob.

        Args:
            bundle_id: Artifact bundle id under the selected root.
            root: Local artifact-store root.
            limits: Version-owned finite read limits, individually overridable.
            output_format: Human or deterministic JSON output.
            color: Terminal color behavior for human messages.
        """
        request = ArtifactReadRequest(
            root=root,
            bundle_id=bundle_id,
            limits=_artifact_limits(limits),
            output=CliOutputMode(output_format),
        )
        return _invocation(services.artifact_verify(request), color)

    @artifact.command(name="blob")
    def artifact_blob(
        bundle_id: str,
        sha256: str,
        /,
        *,
        root: ArtifactRootOption = DEFAULT_CLI_ARTIFACT_ROOT_V1,
        limits: ArtifactLimitsOption = _DEFAULT_ARTIFACT_LIMIT_ARGUMENTS,
        maximum_output_bytes: BlobOutputLimitOption = DEFAULT_CLI_BLOB_OUTPUT_BYTES_V1,
        color: ColorOption = "auto",
    ) -> CliInvocation:
        """Write one authenticated sanitized blob as exact raw bytes to redirected stdout.

        Args:
            bundle_id: Artifact bundle id under the selected root.
            sha256: Declared SHA-256 blob identity.
            root: Local artifact-store root.
            limits: Version-owned finite read limits, individually overridable.
            maximum_output_bytes: Independent maximum bytes written to redirected stdout.
            color: Terminal color behavior for failures.
        """
        request = ArtifactBlobRequest(
            root=root,
            bundle_id=bundle_id,
            sha256=sha256,
            limits=_artifact_limits(limits),
            maximum_output_bytes=maximum_output_bytes,
        )
        return _invocation(services.artifact_blob(request), color)

    @cli.command(name="report")
    def report(
        bundle_id: str,
        /,
        *,
        root: ArtifactRootOption = DEFAULT_CLI_ARTIFACT_ROOT_V1,
        limits: ArtifactLimitsOption = _DEFAULT_ARTIFACT_LIMIT_ARGUMENTS,
        report_format: Annotated[ReportFormatName, Parameter(name="--format")] = "json",
        color: ColorOption = "auto",
    ) -> CliInvocation:
        """Render an authenticated artifact as JSON, JUnit, SARIF, or Markdown to stdout.

        Args:
            bundle_id: Artifact bundle id under the selected root.
            root: Local artifact-store root.
            limits: Version-owned finite read and report-output limits, individually overridable.
            report_format: Built-in report format.
            color: Terminal color behavior for failures.
        """
        request = ReportRequest(
            root=root,
            bundle_id=bundle_id,
            limits=_artifact_limits(limits),
            format=CliReportFormat(report_format),
        )
        return _invocation(services.report(request), color)


def _register_tui_command(cli: App) -> None:
    """Attach the lazy terminal frontend launch contract."""

    @cli.command(name="tui")
    def tui(
        *,
        config: ConfigOption = Path("comparo.yaml"),
        source: SourceOption = (),
        policy: PolicyOption = None,
        artifact_root: TuiArtifactRootOption = Path(".comparo/artifacts"),
        persist: TuiPersistenceOption = True,
        config_load_limits: ConfigLoadLimitsOption = _DEFAULT_CONFIG_LOAD_LIMIT_ARGUMENTS,
        runtime_limits: RuntimeLimitsOption = _DEFAULT_RUNTIME_LIMIT_ARGUMENTS,
        artifact_limits: ArtifactLimitsOption = _DEFAULT_ARTIFACT_LIMIT_ARGUMENTS,
        maximum_report_entries: MaximumReportEntriesOption = (
            DEFAULT_TUI_MAXIMUM_REPORT_ENTRIES_V1
        ),
        maximum_outstanding_preparations: MaximumPreparationsOption = (
            DEFAULT_TUI_MAXIMUM_OUTSTANDING_PREPARATIONS_V1
        ),
        maximum_input_characters: MaximumInputCharactersOption = (
            DEFAULT_TUI_MAXIMUM_INPUT_CHARACTERS_V1
        ),
        maximum_input_items: MaximumInputItemsOption = DEFAULT_TUI_MAXIMUM_INPUT_ITEMS_V1,
        maximum_output_characters: MaximumOutputCharactersOption = (
            DEFAULT_TUI_MAXIMUM_OUTPUT_CHARACTERS_V1
        ),
        maximum_output_items: MaximumOutputItemsOption = DEFAULT_TUI_MAXIMUM_OUTPUT_ITEMS_V1,
        maximum_navigation_frames_per_workspace: MaximumNavigationFramesOption = (
            DEFAULT_TUI_MAXIMUM_NAVIGATION_FRAMES_V1
        ),
    ) -> TuiLaunchRequest:
        """Open the interactive terminal interface.

        Args:
            config: Project manifest or directory containing comparo.yaml.
            source: Additional local YAML source; repeat the option to add more.
            policy: External ExecutionPolicy path; omission uses trusted-local authority.
            artifact_root: Authenticated sanitized report archive root.
            persist: Persist sanitized run reports; disable for a no-storage execution policy.
            config_load_limits: Caller-owned project and policy YAML admission ceilings.
            runtime_limits: Caller-owned HTTP capture and decoding ceilings.
            artifact_limits: Caller-owned artifact persistence and read ceilings.
            maximum_report_entries: Maximum archived report entries admitted per refresh.
            maximum_outstanding_preparations: Maximum retained Compare preparations.
            maximum_input_characters: Maximum characters admitted by one Compare input.
            maximum_input_items: Maximum comma-separated items admitted by one Compare input.
            maximum_output_characters: Maximum characters mounted by one bounded TUI projection.
            maximum_output_items: Maximum rows/items mounted by one bounded TUI projection.
            maximum_navigation_frames_per_workspace: Maximum retained local routes per workspace.
        """
        try:
            parsed_config_limits = _config_load_limits(config_load_limits)
            parsed_execution_limits = _execution_limits(runtime_limits, artifact_limits)
            limits = TuiLaunchLimits(
                project_load=project_load_limits(parsed_config_limits),
                policy_load=policy_load_limits(parsed_config_limits),
                execution_settings=production_execution_settings(
                    parsed_execution_limits,
                    persist=persist,
                ),
                report_archive=ReportArchiveLimits(
                    maximum_entries=maximum_report_entries,
                    artifact=_artifact_read_limits(artifact_limits),
                ),
                interaction=TuiInteractionLimits(
                    maximum_input_characters=maximum_input_characters,
                    maximum_input_items=maximum_input_items,
                    maximum_output_characters=maximum_output_characters,
                    maximum_output_items=maximum_output_items,
                    maximum_navigation_frames_per_workspace=(
                        maximum_navigation_frames_per_workspace
                    ),
                ),
                maximum_outstanding_preparations=maximum_outstanding_preparations,
            )
            return TuiLaunchRequest(
                config=config,
                additional_sources=source,
                policy=policy,
                artifact_root=artifact_root,
                limits=limits,
            )
        except Exception as error:
            raise _InvalidTuiLaunchError from error


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
    _register_artifact_commands(cli, services)
    _register_execution_commands(cli, services)
    _register_render_command(cli, services)
    _register_tui_command(cli)

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
        config_load_limits: ConfigLoadLimitsOption = _DEFAULT_CONFIG_LOAD_LIMIT_ARGUMENTS,
        output_format: OutputOption = "human",
        color: ColorOption = "auto",
    ) -> CliInvocation:
        """Load and compile a project without resolving values or using the network.

        Args:
            config: Project manifest or directory containing comparo.yaml.
            source: Additional local YAML source; repeat the option to add more.
            config_load_limits: Version-owned project-source and YAML admission ceilings.
            output_format: Human or deterministic JSON output.
            color: Terminal color behavior for human messages.
        """
        request = ValidateRequest(
            config=config,
            additional_sources=source,
            output=CliOutputMode(output_format),
            load_limits=_config_load_limits(config_load_limits),
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
        parameters_from: Annotated[
            tuple[str, ...],
            Parameter(name="--parameters-from", consume_multiple=False),
        ] = (),
        variables_from: Annotated[
            tuple[str, ...],
            Parameter(name="--variables-from", consume_multiple=False),
        ] = (),
        secrets_from: Annotated[
            tuple[str, ...],
            Parameter(name="--secrets-from", consume_multiple=False),
        ] = (),
        config_load_limits: ConfigLoadLimitsOption = _DEFAULT_CONFIG_LOAD_LIMIT_ARGUMENTS,
        output_format: OutputOption = "human",
        color: ColorOption = "auto",
    ) -> CliInvocation:
        """Compile and inspect a deterministic plan without resolving values or using the network.

        Args:
            config: Project manifest or directory containing comparo.yaml.
            source: Additional local YAML source; repeat the option to add more.
            policy: Explicit operator-owned policy; omission uses permissive
                trusted-local authority.
            profile: ExecutionProfile id to apply.
            subject: Request id; repeat to select more than one executable subject.
            environment: Environment id for an assertion run; repeat to select more than one.
            baseline: Baseline Environment id for a comparison run.
            candidate: Candidate Environment id; repeat to select more than one.
            matrix: Matrix override as BINDING=MATRIX; repeat for multiple bindings.
            trials: Positive trial-count override.
            maximum_attempts: Positive per-execution attempt limit.
            fail_fast: Enable fail-fast; use --no-fail-fast to disable it.
            budget: Planning budget as RESOURCE=LIMIT; repeat for multiple resources.
            parameters_from: Value resource as [ENV=]VALUE supplying invocation parameters.
            variables_from: Value resource as [ENV=]VALUE supplying invocation variables.
            secrets_from: Value resource as [ENV=]VALUE supplying invocation secrets.
            config_load_limits: Version-owned project-source and YAML admission ceilings.
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
            load_limits=_config_load_limits(config_load_limits),
            parameters_from=parameters_from,
            variables_from=variables_from,
            secrets_from=secrets_from,
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


def _render_result(
    result: CliCommandResult,
    output: Console,
    error: Console,
    binary_output: BinaryIO | None,
) -> bool:
    """Render one complete result without interpreting payload content as markup."""
    if result.payload_kind is CliPayloadKind.BINARY:
        if binary_output is None or binary_output.isatty():
            return False
        binary_output.write(result.payload)
        binary_output.flush()
        return True
    if result.payload_kind is not CliPayloadKind.HUMAN:
        output.file.write(result.payload.decode())
        output.file.flush()
        return True
    destination = output if result.exit_code is CliExitCode.SUCCESS else error
    for message in result.messages:
        destination.print(Text(message.text, style=_LEVEL_STYLES[message.level]))
    return True


def _render_static_error(console: Console, message: str) -> None:
    """Render a fixed adapter-owned error without exception or token text."""
    console.print(Text(message, style="bold red"))


def main(
    argv: Sequence[str] | None = None,
    *,
    services: CliServices | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    binary_stdout: BinaryIO | None = None,
    stdin: TextIO | None = None,
    tui_launcher: Callable[[TuiLaunchRequest], int] | None = None,
) -> int:
    """Run the CLI and return a stable exit code without raising parser exits."""
    tokens = tuple(sys.argv[1:] if argv is None else argv)
    color = _requested_color(tokens)
    output_console = _console(sys.stdout if stdout is None else stdout, color)
    error_console = _console(sys.stderr if stderr is None else stderr, color)
    if not tokens and _is_interactive_terminal(stdin, stdout, stderr):
        return _launch_tui(TuiLaunchRequest(), tui_launcher, error_console)
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
    except (CycloptsError, _InvalidTuiLaunchError):
        _render_static_error(error_console, "Invalid command invocation. Run with --help.")
        return int(CliExitCode.INVALID_INVOCATION)
    except Exception:  # noqa: BLE001 - the process boundary must fail closed without exception text.
        _render_static_error(error_console, "Comparo could not complete the command safely.")
        return int(CliExitCode.INTERNAL_ERROR)
    if parsed is None:
        return int(CliExitCode.SUCCESS)
    if type(parsed) is TuiLaunchRequest:
        return _launch_tui(parsed, tui_launcher, error_console)
    if type(parsed) is not CliInvocation:
        _render_static_error(error_console, "Comparo received an invalid command result.")
        return int(CliExitCode.INTERNAL_ERROR)
    raw_output = sys.stdout.buffer if binary_stdout is None and stdout is None else binary_stdout
    if not _render_result(parsed.result, output_console, error_console, raw_output):
        _render_static_error(error_console, "Comparo could not write binary output safely.")
        return int(CliExitCode.INTERNAL_ERROR)
    return int(parsed.result.exit_code)


def _is_interactive_terminal(
    stdin: TextIO | None,
    stdout: TextIO | None,
    stderr: TextIO | None,
) -> bool:
    """Open the implicit TUI only when all process streams are real terminals."""
    streams = (
        sys.stdin if stdin is None else stdin,
        sys.stdout if stdout is None else stdout,
        sys.stderr if stderr is None else stderr,
    )
    return all(stream.isatty() for stream in streams)


def _launch_tui(
    request: TuiLaunchRequest,
    launcher: Callable[[TuiLaunchRequest], int] | None,
    error_console: Console,
) -> int:
    """Invoke the lazy terminal adapter and contain its process-boundary failures."""
    selected = _default_tui_launcher if launcher is None else launcher
    try:
        code = selected(request)
    except KeyboardInterrupt:
        _render_static_error(error_console, "Interrupted.")
        return int(CliExitCode.INTERRUPTED)
    except Exception:  # noqa: BLE001 - never expose terminal/bootstrap exception text
        _render_static_error(
            error_console,
            "Comparo could not start the terminal interface safely.",
        )
        return int(CliExitCode.INTERNAL_ERROR)
    if type(code) is not int or code < 0 or code > 255:
        _render_static_error(error_console, "Comparo received an invalid terminal exit status.")
        return int(CliExitCode.INTERNAL_ERROR)
    return code


def _default_tui_launcher(request: TuiLaunchRequest) -> int:
    """Import Textual only after the user deliberately enters the TUI journey."""
    from comparo.tui.launch import run_tui

    return run_tui(request)


__all__ = ["build_app", "generate_cli_reference", "main"]
