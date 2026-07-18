"""Rendering, formatting, and lookup helpers for the comparo TUI.

Pure helper functions (and two small helper classes) that build Rich
renderables, format detail panes, and derive footer/help content for the
view/modal classes in comparo.tui.app. Nothing here references a view/modal
class or ComparoApp — this module sits between comparo.tui.tokens and
comparo.tui.app in the dependency order (constants <- functions <- views).
"""

import json
import traceback
from collections.abc import Callable
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING
from typing import NamedTuple
from typing import cast
from urllib.parse import urlencode

import msgspec
from rich.box import ROUNDED
from rich.cells import cell_len
from rich.console import Group
from rich.console import RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual.widget import Widget
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from comparo import __version__
from comparo.adapters import updates as updates_adapter
from comparo.adapters.userconfig import UserConfig
from comparo.core.archive import AssertionSummary
from comparo.core.archive import CellRecord
from comparo.core.archive import ReportRecord
from comparo.core.archive import RequestBreakdown
from comparo.core.assertions import AssertionResult
from comparo.core.checks import Check
from comparo.core.compare import CellDiff
from comparo.core.diagnostics import Diagnostic
from comparo.core.diagnostics import LoadError
from comparo.core.diff import FieldDiff
from comparo.core.diff import State
from comparo.core.execute import Execution
from comparo.core.execution import CellOutcome
from comparo.core.execution import ExecutionProgress
from comparo.core.execution import ExecutionResult
from comparo.core.export import RunEntry
from comparo.core.export import export_run
from comparo.core.health import Health
from comparo.core.health import HealthReport
from comparo.core.loader import LoadedProject
from comparo.core.matrix import MatrixCell
from comparo.core.matrix import expand
from comparo.core.models import AssertionProfile
from comparo.core.models import DiffProfile
from comparo.core.models import Environment
from comparo.core.models import ExecutionProfile
from comparo.core.models import Header
from comparo.core.models import Instance
from comparo.core.models import Matrix
from comparo.core.models import Project
from comparo.core.models import Request
from comparo.core.models import Schema
from comparo.core.provenance import Origin
from comparo.core.provenance import Trail
from comparo.core.redaction import Redactor
from comparo.core.refs import ref_id as _ref_id
from comparo.core.resolve import EnvironmentSelectionError
from comparo.core.resolve import ResolvedRequest
from comparo.core.resolve import Resolver
from comparo.core.resolve import select_environment
from comparo.core.streams import parse_sse
from comparo.tui.tokens import _ACCENT
from comparo.tui.tokens import _ADD_BG
from comparo.tui.tokens import _ASSERT_GLYPH
from comparo.tui.tokens import _AXIS
from comparo.tui.tokens import _DANGER
from comparo.tui.tokens import _DEL_BG
from comparo.tui.tokens import _DIFF_BG
from comparo.tui.tokens import _DIM
from comparo.tui.tokens import _DOCS_URL
from comparo.tui.tokens import _DRIFT
from comparo.tui.tokens import _GATE_COLOR
from comparo.tui.tokens import _HEALTH_COLOR
from comparo.tui.tokens import _HELP_ERROR_GLOBAL
from comparo.tui.tokens import _HELP_GLOBAL
from comparo.tui.tokens import _HELP_MODAL_GLOBAL
from comparo.tui.tokens import _HELP_RUNNING_GLOBAL
from comparo.tui.tokens import _HELP_SCREEN
from comparo.tui.tokens import _HELP_TITLE
from comparo.tui.tokens import _HUNK_BG
from comparo.tui.tokens import _INK
from comparo.tui.tokens import _ISSUES_URL
from comparo.tui.tokens import _KIND_COLOR
from comparo.tui.tokens import _LABEL
from comparo.tui.tokens import _METHOD
from comparo.tui.tokens import _MODAL_HELP_SCREENS
from comparo.tui.tokens import _MODE
from comparo.tui.tokens import _REPO_URL
from comparo.tui.tokens import _RUN_GLYPH
from comparo.tui.tokens import _SAME
from comparo.tui.tokens import _SKIP
from comparo.tui.tokens import _SYNTAX_BG
from comparo.tui.tokens import _TEXT
from comparo.tui.tokens import _TEXT_HI
from comparo.tui.tokens import _WARN
from comparo.tui.tokens import _WELL_BORDER

if TYPE_CHECKING:
    from comparo.tui.app import ComparoApp

__all__ = [
    "_HtmlOutline",
    "_RunningRow",
    "_app_env",
    "_app_redact",
    "_assert_count_text",
    "_assert_counts",
    "_assert_lines",
    "_assert_tally",
    "_assertion_profile_detail",
    "_band",
    "_bash",
    "_body_diff_lines",
    "_body_into",
    "_body_summary",
    "_branch",
    "_breakdown_legend",
    "_breakdown_table",
    "_build_report_tree",
    "_cell_for_request",
    "_cell_label",
    "_cell_verdict",
    "_check_result",
    "_clip",
    "_content_type",
    "_crash_report",
    "_default_environment",
    "_default_pair",
    "_description",
    "_diff_body_view",
    "_diff_error_view",
    "_diff_field",
    "_diff_legend",
    "_diff_ready",
    "_diff_side_by_side",
    "_diff_skip_view",
    "_diff_slug",
    "_diff_unified",
    "_diffprofile_detail",
    "_drift_change",
    "_edges",
    "_environment_detail",
    "_environments",
    "_envs_label",
    "_error_report",
    "_exec_assert_body",
    "_exec_assert_rows",
    "_exec_diff_legend",
    "_exec_diff_summary",
    "_exec_drift_fields",
    "_exec_env_names",
    "_exec_foot",
    "_exec_gate_body",
    "_exec_header",
    "_exec_mode",
    "_exec_plan_line",
    "_exec_profile_card",
    "_exec_profiles_hint",
    "_exec_selected_requests",
    "_exec_setup",
    "_exec_skip_paths",
    "_exec_stacked_diff",
    "_execution_profile_detail",
    "_field_skip_count",
    "_fmt_bytes",
    "_gate_banner",
    "_git_legend",
    "_graph",
    "_header_rows",
    "_help_body",
    "_help_row",
    "_hole_str",
    "_hunk_band",
    "_is_remote",
    "_json",
    "_keys_bar",
    "_kind_of",
    "_leaf",
    "_matches",
    "_matrix_head",
    "_matrix_summary",
    "_object_detail",
    "_ok_report",
    "_outbound_diff_view",
    "_p50",
    "_pad_cells",
    "_pair",
    "_project_detail",
    "_project_leaf",
    "_raw_detail_into",
    "_raw_header_pairs",
    "_record_detail",
    "_record_kind",
    "_record_markdown",
    "_rel_dir",
    "_relative_age",
    "_render_provenance",
    "_replay_banner",
    "_replay_compare_path_well",
    "_replay_compare_well",
    "_replay_detail_tree",
    "_replay_diff_cell",
    "_replay_drift_groups",
    "_replay_drift_summary",
    "_replay_path_groups",
    "_replay_run_progress",
    "_replay_skip_groups",
    "_report_reading_pane",
    "_req_short",
    "_request_detail",
    "_request_latencies",
    "_requests",
    "_run_key",
    "_run_label",
    "_running_body",
    "_running_cell_name",
    "_running_row_from_progress",
    "_save_run",
    "_scalar",
    "_seg_toggle",
    "_selfcheck_rows",
    "_settings_about",
    "_settings_appearance",
    "_settings_behavior",
    "_settings_body",
    "_settings_engine",
    "_settings_keybindings",
    "_settings_plugins",
    "_settings_project",
    "_settings_security",
    "_settings_updates",
    "_short",
    "_sigil_refs",
    "_sse_into",
    "_sv",
    "_table",
    "_title",
    "_unified_rows",
    "_value_child",
    "_value_into",
]


def _keys_bar(keys: tuple[tuple[str, str], ...] | list[tuple[str, str]]) -> Text:
    """Render ``(key, action)`` hints as a single no-wrap line of pills."""
    bar = Text(no_wrap=True, overflow="ellipsis")
    for index, (key, action) in enumerate(keys):
        if index:
            bar.append(" ")
        bar.append(f" {key} ", style=f"bold {_INK} on {_ACCENT}")
        bar.append(f" {action}", style=_DIM)
    return bar


def _crash_report(error: Exception, redact: Callable[[str], str]) -> Group:
    """A friendly, secret-redacted crash panel with a prefilled GitHub issue link.

    The traceback is masked with the project's secret values before it is shown or
    put into the issue URL, so a crash can never leak a secret.
    """
    tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    safe_tb = redact(tb)
    title = f"crash: {type(error).__name__}: {redact(str(error))}"
    # GitHub caps issue URLs, so the prefilled body carries only the tail.
    body = (
        "**What I was doing:** \n\n"
        f"**comparo version:** {__version__}\n\n"
        "**Traceback** (secrets already masked):\n\n"
        f"```\n{safe_tb[-3500:]}\n```\n"
    )
    url = f"{_ISSUES_URL}?{urlencode({'title': title[:200], 'body': body})}"
    text = Text()
    text.append("comparo hit an unexpected error and has to stop.\n\n", style=f"bold {_DRIFT}")
    text.append("This is a bug — nothing you did is at fault, and your files were not changed.\n")
    text.append("The traceback below has your secrets masked. Please report it:\n\n", style=_DIM)
    text.append(f"{url}\n", style=_ACCENT)
    body_panel = Text("\n")
    body_panel.append(safe_tb, style=_DIM)
    return Group(text, body_panel)


def _environments(project: LoadedProject) -> list[Environment]:
    """Every environment in the project, sorted by id."""
    envs = [obj for obj in project.objects.values() if isinstance(obj, Environment)]
    return sorted(envs, key=lambda env: env.metadata.id or env.metadata.name)


def _default_pair(project: LoadedProject) -> tuple[Environment, Environment] | None:
    """A baseline ⇄ candidate pair to seed the Diff screen when none is configured.

    Args:
        project: The loaded project.

    Returns:
        The first two environments, the only one twice, or ``None`` if there are none.
    """
    envs = _environments(project)
    if not envs:
        return None
    return (envs[0], envs[1]) if len(envs) > 1 else (envs[0], envs[0])


def _default_environment(project: LoadedProject) -> Environment | None:
    try:
        return select_environment(project, None)
    except EnvironmentSelectionError:
        for obj in project.objects.values():
            if isinstance(obj, Environment):
                return obj
        return None


def _branch(label: str, count: int) -> Text:
    return Text.assemble((f"{label}  ", f"bold {_LABEL}"), (f"{count}", _DIM))


def _leaf(obj: object, *, health: Health = Health.UNKNOWN, default: bool = False) -> Text:
    metadata = getattr(obj, "metadata", None)
    name = str(getattr(metadata, "name", "?"))
    row = Text()
    if isinstance(obj, Environment):
        row.append("● ", style=_HEALTH_COLOR[health])
        row.append(name, style=_TEXT_HI if default else _TEXT)
        if _is_remote(obj):
            row.append("  live", style=f"bold {_DANGER}")
        if default:
            row.append("  default", style=f"bold {_ACCENT}")
    elif isinstance(obj, Matrix):
        row.append(name, style=_AXIS)
        row.append(f"  ×{len(obj.spec.values)}", style=_DIM)
    elif isinstance(obj, Request):
        row.append(name, style=_TEXT)
        if obj.spec.matrix:
            row.append("  matrix", style=_AXIS)
    elif isinstance(obj, ExecutionProfile):
        row.append("▸ ", style=_ACCENT)
        row.append(name, style=_TEXT_HI)
        row.append("  enter to run", style=_DIM)
    elif isinstance(obj, AssertionProfile):
        row.append(name, style=_TEXT)
        count = len(obj.spec.rules or [])
        if count:
            row.append(f"  ×{count}", style=_DIM)
    else:
        row.append(name, style=_TEXT)
    return row


def _project_leaf(manifest: Project) -> Text:
    row = Text()
    row.append("◆ ", style=_ACCENT)
    row.append(str(manifest.metadata.name or "project"), style=f"bold {_TEXT_HI}")
    row.append("  project", style=_DIM)
    return row


def _project_detail(manifest: Project, redact: Callable[[str], str] = str) -> Group:
    spec = manifest.spec
    parts: list[RenderableType] = []
    head = Text()
    if spec.data:
        head.append("data       ", style=_LABEL)
        head.append(f"{redact(str(spec.data))}\n", style=_TEXT)
    environments = spec.environments
    default = environments.default if environments is not None else None
    if isinstance(default, str):
        head.append("default    ", style=_LABEL)
        head.append(f"{redact(default)}\n", style=_ACCENT)
    parts.append(head)
    pairs = environments.diff_pairs if environments is not None else None
    if pairs:
        block = Text("\nDIFF PAIRS", style=_LABEL)
        for pair in pairs:
            block.append(f"\n  {redact(pair.name):<16}", style=_TEXT)
            block.append(f"{redact(pair.baseline)} ⇄ {redact(pair.candidate)}", style=_AXIS)
        parts.append(block)
    sections: tuple[tuple[str, object], ...] = (
        ("run", spec.run),
        ("diff", spec.diff),
        ("selection", spec.selection),
        ("report", spec.report),
        ("redaction", spec.redaction),
        ("plugins", spec.plugins),
    )
    for label, value in sections:
        if value:
            parts.append(Text(f"\n\n{label.upper()}", style=_LABEL))
            # Config interiors are now structs; render them as their plain form.
            plain = msgspec.to_builtins(value) if isinstance(value, msgspec.Struct) else value
            parts.append(_json(plain, redact))
    return Group(*parts)


def _is_remote(environment: Environment) -> bool:
    url = environment.spec.base_url.lower()
    return not any(host in url for host in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]"))


def _matches(obj: object, kind: type, needle: str) -> bool:
    if not needle:
        return True
    metadata = getattr(obj, "metadata", None)
    haystack = [
        str(getattr(metadata, "name", "")),
        str(getattr(metadata, "id", "") or ""),
        kind.__name__.lower(),
    ]
    haystack.extend(getattr(metadata, "tags", None) or [])
    return any(needle in part.lower() for part in haystack)


def _title(obj: object, tag: str) -> Text:
    metadata = getattr(obj, "metadata", None)
    identifier = str(getattr(metadata, "id", "") or getattr(metadata, "name", ""))
    return Text.assemble((identifier, f"bold {_ACCENT}"), ("   ", ""), (tag, _AXIS))


def _description(obj: object) -> Text:
    metadata = getattr(obj, "metadata", None)
    description = getattr(metadata, "description", None)
    if description:
        return Text(str(description), style=_TEXT)
    return Text("no description", style=_DIM)


def _request_detail(
    project: LoadedProject,
    request: Request,
    resolved: ResolvedRequest,
    *,
    raw: bool = False,
    redact: Callable[[str], str] = str,
) -> Group:
    outbound = request.spec.request
    parts: list[RenderableType] = []
    head = Text()
    head.append(
        f" {resolved.method} ", style=f"bold {_INK} on {_METHOD.get(resolved.method, _ACCENT)}"
    )
    head.append("  ")
    head.append(redact(outbound.endpoint if raw else resolved.url), style=_TEXT_HI)
    parts.append(head)
    if request.metadata.description:
        parts.append(Text(f"\n{request.metadata.description}", style=_DIM))
    tags = request.metadata.tags or []
    matrices = _matrix_summary(project, request.spec.matrix)
    meta = Text()
    if tags:
        meta.append("\ntags       ", style=_LABEL)
        meta.append(" · ".join(tags), style=_AXIS)
    if matrices:
        meta.append("\nmatrix     ", style=_LABEL)
        meta.append(matrices, style=_AXIS)
    parts.append(meta)
    headers = Text("\n\nHEADERS", style=_LABEL)
    for key, rendered in _header_rows(outbound.headers, resolved.headers, raw=raw, redact=redact):
        headers.append(f"\n  {key:<18}", style=_DIM)
        headers.append(rendered)
    parts.append(headers)
    query_source = (outbound.query or {}) if raw else resolved.query
    if query_source:
        query = Text("\n\nQUERY", style=_LABEL)
        for key, value in query_source.items():
            shown = _hole_str(value) if raw else str(value)
            query.append(f"\n  {redact(key):<18}", style=_DIM)
            query.append(redact(shown), style=_AXIS)
        parts.append(query)
    body_source = outbound.body if raw else resolved.body
    if body_source is not None:
        parts.append(Text("\n\nBODY", style=_LABEL))
        parts.append(_json(body_source, redact))
    response = request.spec.response
    if response is not None:
        section = Text("\n\nRESPONSE", style=_LABEL)
        if response.status:
            section.append("\n  status   ", style=_DIM)
            section.append(str(response.status), style=_TEXT)
        for name, reference in (("schema", response.schema), ("diff", response.diff)):
            identifier = _ref_id(reference)
            if identifier:
                section.append(f"\n  {name:<9}", style=_DIM)
                section.append(identifier, style=_TEXT)
        parts.append(section)
    return Group(*parts)


def _header_rows(
    raw_headers: object,
    resolved_headers: list[tuple[str, object]],
    *,
    raw: bool,
    redact: Callable[[str], str] = str,
) -> list[tuple[str, Text]]:
    if raw:
        pairs = _raw_header_pairs(raw_headers)
        return [(redact(key), Text(redact(_hole_str(value)), style=_AXIS)) for key, value in pairs]
    rows: list[tuple[str, Text]] = []
    for key, value in resolved_headers:
        shown = redact(str(value))
        masked = "••••" in shown
        rows.append((redact(key), Text(shown, style=_DRIFT if masked else _TEXT)))
    return rows


def _raw_header_pairs(headers: object) -> list[tuple[str, object]]:
    if isinstance(headers, dict):
        target = headers.get("$val")
        if isinstance(target, str):
            return [("(reference)", {"$val": target})]
        # Mapping form: ``{Header-Name: value}`` (skip any ``$``-sigil hole).
        return [(str(key), value) for key, value in headers.items() if not str(key).startswith("$")]
    pairs: list[tuple[str, object]] = []
    if isinstance(headers, list):
        for item in headers:
            if isinstance(item, Header):
                pairs.append((item.key, item.value))
            elif isinstance(item, dict) and "key" in item:
                pairs.append((str(item["key"]), item.get("value")))
    return pairs


def _hole_str(value: object) -> str:
    if isinstance(value, dict) and len(value) == 1:
        key, target = next(iter(value.items()))
        return f"{key} {target}"
    return str(value)


def _object_detail(obj: object, redact: Callable[[str], str] = str) -> RenderableType:
    if isinstance(obj, Environment):
        return _environment_detail(obj, None, redact)
    if isinstance(obj, Matrix):
        return Group(_matrix_head(obj, redact), _json(obj.spec.values, redact))
    if isinstance(obj, DiffProfile):
        return _diffprofile_detail(obj, redact)
    if isinstance(obj, AssertionProfile):
        return _assertion_profile_detail(obj, redact)
    if isinstance(obj, ExecutionProfile):
        return _execution_profile_detail(obj, redact)
    if isinstance(obj, Schema):
        return _json(obj.spec, redact)
    if isinstance(obj, Instance):
        return _json(obj.spec.value, redact)
    return Text(str(obj), style=_TEXT)


def _assertion_profile_detail(
    profile: AssertionProfile, redact: Callable[[str], str] = str
) -> Group:
    spec = profile.spec
    parts: list[RenderableType] = []
    if profile.metadata.description:
        parts.append(Text(profile.metadata.description, style=_DIM))
    for reference in spec.include or []:
        line = Text("\ninclude    ", style=_LABEL)
        line.append(_ref_id(reference) or _hole_str(reference), style=_ACCENT)
        parts.append(line)
    rules = Text("\n\nRULES", style=_LABEL)
    for rule in spec.rules or []:
        tint = _WARN if rule.severity == "warn" else _TEXT
        # A rule's expected value can equal a declared secret (asserting against a
        # credential); mask it here as the label/detail sinks do.
        rules.append(f"\n  {redact(rule.target):<24}", style=_TEXT_HI)
        rules.append(f"{rule.op:<8}", style=_AXIS)
        if rule.value is not None:
            rules.append(_sv(rule.value, redact), style=tint)
        if rule.severity == "warn":
            rules.append("   warn", style=_WARN)
    parts.append(rules)
    parts.append(Text("\n\nRuns on both environments.", style=_DIM))
    return Group(*parts)


def _execution_profile_detail(
    profile: ExecutionProfile, redact: Callable[[str], str] = str
) -> Group:
    spec = profile.spec
    parts: list[RenderableType] = []
    if profile.metadata.description:
        parts.append(Text(profile.metadata.description, style=_DIM))
    envs = spec.environments
    body = Text()
    if envs is not None:
        body.append("\nbaseline   ", style=_LABEL)
        body.append(f"{redact(envs.baseline or '—')}", style=_SAME)
        if envs.candidate:
            body.append("\ncandidate  ", style=_LABEL)
            body.append(redact(envs.candidate), style=_DRIFT)
    select = spec.select
    if select is not None and (select.tags or select.requests):
        body.append("\nselect     ", style=_LABEL)
        chosen = list(select.tags or []) + list(select.requests or [])
        body.append(" · ".join(redact(item) for item in chosen), style=_AXIS)
    check = spec.check
    body.append("\nchecks     ", style=_LABEL)
    do_assert = check.assertions if check is not None else True
    do_diff = check.diff if check is not None else True
    body.append("assert " + ("on" if do_assert else "off"), style=_SAME if do_assert else _DIM)
    body.append("  ·  ", style=_DIM)
    body.append("diff " + ("on" if do_diff else "off"), style=_SAME if do_diff else _DIM)
    parts.append(body)
    profiles = spec.profiles
    for key, block in (
        ("assert", profiles.assert_ if profiles else None),
        ("diff", profiles.diff if profiles else None),
    ):
        for reference in block if isinstance(block, list) else ([block] if block else []):
            line = Text(f"\n{key:<10} ", style=_LABEL)
            line.append(_ref_id(reference) or "inline", style=_ACCENT)
            parts.append(line)
    if isinstance(spec.matrix, dict) and spec.matrix:
        matrix = Text("\n\nMATRIX SCOPE", style=_LABEL)
        for name, scope in spec.matrix.items():
            matrix.append(f"\n  {name}  ", style=_TEXT_HI)
            for verb, cases in (("+", scope.include), ("−", scope.exclude), ("~", scope.override)):
                for case in cases or []:
                    matrix.append(f"{verb}{_sv(case, redact)} ", style=_DIM)
        parts.append(matrix)
    parts.append(Text("\n\npress enter to run this execution", style=f"bold {_ACCENT}"))
    return Group(*parts)


def _environment_detail(
    env: Environment,
    report: HealthReport | None,
    redact: Callable[[str], str] = str,
    *,
    checked: str | None = None,
) -> Text:
    spec = env.spec
    text = Text()
    remote = _is_remote(env)
    text.append("baseUrl    ", style=_LABEL)
    # base_url can embed a credential (https://user:<secret>@host); a variable's
    # value can equal a declared secret (the untainted vector) — mask both.
    text.append(f"{redact(spec.base_url)}", style=_ACCENT)
    text.append("   live\n" if remote else "   local\n", style=_DANGER if remote else _DIM)
    if spec.timeout is not None:
        text.append("timeout    ", style=_LABEL)
        text.append(f"connect {spec.timeout.connect} · read {spec.timeout.read}\n", style=_TEXT)
    for section, mapping in (("VARIABLES", spec.variables), ("SECRETS", spec.secrets)):
        if mapping:
            text.append(f"\n{section}\n", style=_LABEL)
            for key in mapping:
                text.append(f"  {redact(key):<22}", style=_DIM)
                text.append(
                    "••••••\n" if section == "SECRETS" else f"{redact(str(mapping[key]))}\n",
                    style=_DRIFT if section == "SECRETS" else _TEXT,
                )
    if spec.health:
        text.append("\nHEALTH", style=_LABEL)
        if report is not None:
            text.append(f"   {report.status.value}", style=_HEALTH_COLOR[report.status])
        # EXP-23: health is a point-in-time probe you trigger — never fired on
        # focus, since that would hammer a live env on every cursor move. Show
        # how fresh the last probe is (or that there isn't one) and how to re-run.
        if checked is not None:
            age = _relative_age(checked)
            text.append(
                f"   checked {age} ago · press h to re-check" if age else "   press h to re-check",
                style=_DIM,
            )
        else:
            text.append("   not checked yet · press h", style=_DIM)
        text.append("\n", style=_LABEL)
        outcomes = {result.endpoint: result for result in (report.results if report else [])}
        for check in spec.health:
            result = outcomes.get(check.endpoint)
            if result is None:
                text.append(f"  ○ {check.method} {redact(check.endpoint)}\n", style=_DIM)
            else:
                glyph, colour = ("✓", _SAME) if result.ok else ("✗", _DRIFT)
                text.append(f"  {glyph} {check.method} {redact(check.endpoint)}", style=colour)
                text.append(f"   {redact(result.detail)}\n", style=_DIM)
    return text


def _matrix_head(matrix: Matrix, redact: Callable[[str], str] = str) -> Text:
    spec = matrix.spec
    text = Text()
    text.append("target   ", style=_LABEL)
    text.append(f"{redact(spec.target)}\n", style=_TEXT)
    text.append("mode     ", style=_LABEL)
    text.append(f"{spec.mode}\n", style=_TEXT)
    text.append(f"\nVALUES  ×{len(spec.values)}\n", style=_LABEL)
    return text


def _diffprofile_detail(profile: DiffProfile, redact: Callable[[str], str] = str) -> Text:
    spec = profile.spec
    text = Text()
    text.append("default  ", style=_LABEL)
    text.append(f"{spec.default}\n", style=_MODE.get(spec.default, _TEXT))
    if spec.rules:
        text.append("\nRULES\n", style=_LABEL)
        for rule in spec.rules:
            text.append(f"  {redact(rule.path):<30}", style=_TEXT)
            text.append(f"{rule.mode}\n", style=_MODE.get(rule.mode, _TEXT))
    return text


def _render_provenance(trail: list[Trail], redact: Callable[[str], str] = str) -> Text:
    if not trail:
        return Text("all literal — nothing resolved", style=_DIM)
    text = Text()
    for entry in trail:
        colour = _DRIFT if entry.tainted else _AXIS
        # A MATRIX-origin trail detail is a case_key (``token=<value>``) that can
        # carry a declared secret; the backstop is a no-op on ref-name details.
        text.append(f"{redact(entry.path):<22}", style=_TEXT)
        text.append("← ", style=_DIM)
        text.append(redact(entry.detail), style=colour)
        if entry.tainted:
            text.append("  · masked", style=_DIM)
        elif entry.origin is Origin.VARIABLE:
            text.append("  · variable", style=_DIM)
        elif entry.origin is Origin.INSTANCE:
            text.append("  · instance", style=_DIM)
        text.append("\n")
    return text


def _help_body(screen: str) -> Text:
    text = Text()
    text.append(f"{_HELP_TITLE.get(screen, screen.upper())}\n\n", style=f"bold {_TEXT_HI}")
    # A screen key can be a combined token like "esc / bksp / q"; collect every
    # sub-token so a global row that repeats one of them is suppressed.
    shown = {part.strip() for key, _ in _HELP_SCREEN.get(screen, ()) for part in key.split("/")}
    for key, description in _HELP_SCREEN.get(screen, ()):
        _help_row(text, key, description)
    text.append("\nEVERYWHERE\n", style=f"bold {_LABEL}")
    globals_: tuple[tuple[str, str], ...]
    if screen == "error":
        globals_ = _HELP_ERROR_GLOBAL
    elif screen == "execution-running":
        globals_ = _HELP_RUNNING_GLOBAL
    elif screen in _MODAL_HELP_SCREENS:
        globals_ = _HELP_MODAL_GLOBAL
    else:
        globals_ = _HELP_GLOBAL
    for key, description in globals_:
        # Don't repeat a key the screen block already documented with a specific
        # meaning (e.g. matrix/filter 'esc' apply/clear vs the generic 'close').
        if key not in shown:
            _help_row(text, key, description)
    return text


def _help_row(text: Text, key: str, description: str) -> None:
    text.append(f"  {key:<8}", style=f"bold {_ACCENT}")
    text.append(f"  {description}\n", style=_TEXT)


def _json(value: object, redact: Callable[[str], str] = str) -> Syntax:
    # redact is the string-match backstop: a value equal to a declared secret can
    # arrive untainted (a plain literal or a non-secret variable), so the DISPLAY
    # sink alone would not mask it — mask the rendered text before it is shown.
    rendered = redact(json.dumps(value, indent=2, ensure_ascii=False))
    return Syntax(rendered, "json", theme="one-dark", background_color=_SYNTAX_BG, word_wrap=True)


def _bash(command: str) -> Syntax:
    return Syntax(command, "bash", theme="one-dark", background_color=_SYNTAX_BG, word_wrap=True)


def _matrix_summary(project: LoadedProject, matrix: list[object] | None) -> str:
    parts: list[str] = []
    for reference in matrix or []:
        identifier = _ref_id(reference)
        obj = project.objects.get(identifier) if identifier else None
        if isinstance(obj, Matrix):
            parts.append(f"{(identifier or '').split('.')[-1]} ×{len(obj.spec.values)}")
    return " · ".join(parts)


def _sigil_refs(spec: object, sigil: str) -> set[str]:
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            target = node.get(sigil)
            if isinstance(target, str):
                found.add(target)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(msgspec.to_builtins(spec))
    return found


def _edges(project: LoadedProject) -> list[tuple[str, str, str]]:
    """Return ``(request_id, relation, target_id)`` links out of every request."""
    edges: list[tuple[str, str, str]] = []
    for obj in project.objects.values():
        if not isinstance(obj, Request):
            continue
        source = obj.metadata.id or obj.metadata.name
        for reference in obj.spec.matrix or []:
            target = _ref_id(reference)
            if target:
                edges.append((source, "matrix", target))
        response = obj.spec.response
        if response is not None:
            for relation, reference in (("schema", response.schema), ("diff", response.diff)):
                target = _ref_id(reference)
                if target:
                    edges.append((source, relation, target))
        for target in sorted(_sigil_refs(obj.spec, "$val")):
            edges.append((source, "value", target))
    return edges


def _short(project: LoadedProject, identifier: str) -> str:
    obj = project.objects.get(identifier)
    metadata = getattr(obj, "metadata", None)
    name = getattr(metadata, "name", None)
    return str(name) if name else identifier.split(".")[-1]


def _kind_of(project: LoadedProject, identifier: str) -> tuple[str, str]:
    obj = project.objects.get(identifier)
    if obj is None:
        return "?", _DIM
    return type(obj).__name__, _KIND_COLOR.get(type(obj), _TEXT)


def _graph(project: LoadedProject) -> Text:
    edges = _edges(project)
    if not edges:
        return Text("no references between objects yet", style=_DIM)
    outgoing: dict[str, list[tuple[str, str]]] = {}
    incoming: dict[str, set[str]] = {}
    for source, relation, target in edges:
        outgoing.setdefault(source, []).append((relation, target))
        incoming.setdefault(target, set()).add(source)

    text = Text()
    text.append("REQUESTS", style=f"bold {_LABEL}")
    text.append("   what each request links to\n\n", style=_DIM)
    for source in sorted(outgoing):
        text.append("● ", style=_ACCENT)
        text.append(f"{_short(project, source)}\n", style=_TEXT_HI)
        links = outgoing[source]
        for index, (relation, target) in enumerate(links):
            connector = "└─" if index == len(links) - 1 else "├─"
            kind_name, colour = _kind_of(project, target)
            text.append(f"  {connector} {relation:<7}→ ", style=_DIM)
            text.append(_short(project, target), style=colour)
            text.append(f"  {kind_name.lower()}\n", style=_DIM)
        text.append("\n")

    text.append("SHARED OBJECTS", style=f"bold {_LABEL}")
    text.append("   what references them\n\n", style=_DIM)
    for target in sorted(incoming):
        kind_name, colour = _kind_of(project, target)
        sources = sorted(_short(project, source) for source in incoming[target])
        text.append(f"{_short(project, target):<22}", style=colour)
        text.append(f"{kind_name.lower():<12}", style=_DIM)
        text.append("← ", style=_DIM)
        text.append(", ".join(sources) + "\n", style=_TEXT)
    return text


def _error_report(error: LoadError) -> Text:
    grouped: dict[str, list[Diagnostic]] = {}
    for diagnostic in error.diagnostics:
        try:
            location = str(diagnostic.file.relative_to(error.root))
        except ValueError:
            location = str(diagnostic.file)
        grouped.setdefault(location, []).append(diagnostic)

    text = Text()
    for location, diagnostics in grouped.items():
        text.append(f"▌ {location}\n", style=f"bold {_DRIFT}")
        for diagnostic in diagnostics:
            text.append("  • ", style=_DRIFT)
            if diagnostic.line is not None:
                text.append(f"line {diagnostic.line}  ", style=_WARN)
            text.append(f"{diagnostic.message}\n", style=_TEXT_HI)
            if diagnostic.hint is not None:
                text.append("    ✎ fix  ", style=f"bold {_SAME}")
                text.append(f"{diagnostic.hint}\n", style=_SAME)
        text.append("\n")
    text.append("fix the files above and press ", style=_DIM)
    text.append("r", style=f"bold {_ACCENT}")
    text.append(" to re-check.", style=_DIM)
    return text


def _ok_report() -> Text:
    text = Text()
    text.append("✓ ", style=f"bold {_SAME}")
    text.append("Every object now parses, indexes, and resolves.\n\n", style=_TEXT_HI)
    text.append("Relaunch ", style=_DIM)
    text.append("comparo tui", style=_ACCENT)
    text.append(" to explore the project.", style=_DIM)
    return text


def _app_env(widget: object) -> Environment | None:
    app = getattr(widget, "app", None)
    return getattr(app, "environment", None)


def _requests(project: LoadedProject) -> list[Request]:
    return sorted(
        (obj for obj in project.objects.values() if isinstance(obj, Request)),
        key=lambda request: request.metadata.id or "",
    )


def _table() -> Table:
    return Table(box=None, expand=True, pad_edge=False, show_edge=False)


def _run_key(request: Request, cell: MatrixCell) -> tuple[str, str]:
    return (request.metadata.id or request.metadata.name, cell.key)


def _pair(node: TreeNode[object] | None) -> tuple[Request | None, MatrixCell | None]:
    data = getattr(node, "data", None)
    if isinstance(data, tuple) and len(data) == 2 and isinstance(data[0], Request):
        cell = data[1] if isinstance(data[1], MatrixCell) else None
        return data[0], cell
    return None, None


def _check_result(check: Check) -> AssertionResult:
    """Adapt a run ``Check`` to an ``AssertionResult`` for the report roll-up."""
    return AssertionResult(check.name, "", check.ok, "error", check.detail, check.name)


def _save_run(
    project: LoadedProject, environment: Environment, run_id: str, entries: list[RunEntry]
) -> Path:
    document = export_run(project, environment, entries)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    destination = project.root / "runs" / f"{run_id}-{stamp}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8")
    return destination


def _build_report_tree(
    tree: Tree[object],
    project: LoadedProject,
    environment: Environment | None,
    request: Request,
    cell: MatrixCell,
    execution: Execution | None,
    state: str,
    checks: list[Check],
    redact: Callable[[str], str] = str,
    *,
    focus: str = "all",
) -> None:
    tree.clear()
    root = tree.root
    resolved = (
        Resolver(project, environment).resolve_request(request, cell)
        if environment is not None
        else None
    )
    method = resolved.method if resolved else request.spec.request.method
    head = Text()
    head.append(f" {method} ", style=f"bold {_INK} on {_METHOD.get(method, _ACCENT)}")
    head.append("  ")
    head.append(redact(resolved.url if resolved else request.spec.request.endpoint), style=_TEXT_HI)
    root.add_leaf(head)
    if cell.key:
        root.add_leaf(Text.assemble(("case    ", _LABEL), (redact(cell.key), _AXIS)))
    glyph, colour = _RUN_GLYPH[state]
    status = Text.assemble(("status  ", _LABEL), (f"{glyph} {state}", colour))
    if execution is not None and execution.response is not None:
        response = execution.response
        status.append(f"   {response.status} · {response.elapsed_ms:.0f}ms", style=_TEXT)
    root.add_leaf(status)

    # RUN-27: the detail is switchable — Request · Response · Headers · Raw (and
    # the default "all" overview). Each mode carves the tree to one facet; the
    # RAW view dumps the unparsed request line and response body verbatim.
    if focus == "raw":
        _raw_detail_into(root, resolved, execution, redact)
        return
    want_request = focus in ("all", "request", "headers")
    want_response = focus in ("all", "response", "headers")
    want_meta = focus in ("all", "response")  # checks + metrics ride with the response
    headers_only = focus == "headers"

    if checks and want_meta:
        node = root.add(Text("CHECKS", style=f"bold {_LABEL}"), expand=True)
        for check in checks:
            mark, tint = ("✓", _SAME) if check.ok else ("✗", _DRIFT)
            detail = redact(check.detail)
            node.add_leaf(Text.assemble((f"{mark} {check.name}  ", tint), (detail, _DIM)))

    if execution is not None and execution.response is not None and want_meta:
        response = execution.response
        node = root.add(Text("METRICS", style=f"bold {_LABEL}"), expand=True)
        node.add_leaf(Text.assemble(("duration  ", _DIM), (f"{response.elapsed_ms:.0f} ms", _TEXT)))
        node.add_leaf(Text.assemble(("size      ", _DIM), (f"{len(response.body)} bytes", _TEXT)))

    if resolved is not None and want_request:
        node = root.add(Text("REQUEST", style=f"bold {_LABEL}"), expand=focus != "all")
        headers = node.add(Text("headers", style=_DIM), expand=headers_only)
        for key, value in resolved.headers:
            # The DISPLAY sink masks $secret refs; the string-match redactor is the
            # backstop for a hardcoded-literal secret (and a secret used as a name).
            shown = redact(str(value))
            masked = "••••" in shown
            headers.add_leaf(
                Text.assemble((f"{redact(key)}: ", _DIM), (shown, _DRIFT if masked else _TEXT))
            )
        if resolved.body is not None and not headers_only:
            _value_into(node.add(Text("body", style=_DIM), expand=False), resolved.body, redact)

    if execution is not None and execution.response is not None and want_response:
        response = execution.response
        node = root.add(Text("RESPONSE", style=f"bold {_LABEL}"), expand=True)
        headers = node.add(Text("headers", style=_DIM), expand=headers_only)
        for key, value in response.headers[:24]:
            headers.add_leaf(Text.assemble((f"{redact(key)}: ", _DIM), (redact(str(value)), _TEXT)))
        if not headers_only:
            body = node.add(Text("body", style=_DIM), expand=len(response.body) < 800)
            _body_into(body, response.body, _content_type(response.headers), redact)
    elif execution is not None and execution.error is not None and focus in ("all", "response"):
        root.add_leaf(Text(redact(execution.error), style=_DRIFT))
    elif state == "pending" and focus in ("all", "request", "response"):
        root.add_leaf(Text("not run — press x to execute", style=_DIM))


def _raw_detail_into(
    root: TreeNode[object],
    resolved: ResolvedRequest | None,
    execution: Execution | None,
    redact: Callable[[str], str] = str,
) -> None:
    """Render the RAW detail view (RUN-27) — request line and response verbatim.

    The outbound request line and the response body are shown unparsed. The
    decoded body passes through ``redact`` so a secret a server echoes back is
    masked here too.
    """
    if resolved is not None:
        node = root.add(Text("RAW REQUEST", style=f"bold {_LABEL}"), expand=True)
        node.add_leaf(Text(f"{resolved.method} {redact(resolved.url)}", style=_TEXT_HI))
        for key, value in resolved.headers:
            node.add_leaf(Text(f"{redact(str(key))}: {redact(str(value))}", style=_DIM))
    if execution is not None and execution.response is not None:
        response = execution.response
        node = root.add(Text("RAW RESPONSE", style=f"bold {_LABEL}"), expand=True)
        node.add_leaf(Text(f"HTTP {response.status}", style=_TEXT_HI))
        for key, value in response.headers[:24]:
            node.add_leaf(Text(f"{redact(str(key))}: {redact(str(value))}", style=_DIM))
        raw = response.body.decode("utf-8", "replace") if response.body else ""
        body = node.add(Text("body", style=_DIM), expand=True)
        for line in redact(raw).splitlines()[:200] or [""]:
            body.add_leaf(Text(line, style=_TEXT))
    elif execution is not None and execution.error is not None:
        root.add_leaf(Text(redact(execution.error), style=_DRIFT))


def _value_into(node: TreeNode[object], value: object, redact: Callable[[str], str] = str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _value_child(node, str(key), item, redact)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _value_child(node, f"[{index}]", item, redact)
    else:
        node.add_leaf(Text.assemble(_scalar(value, redact)))


def _value_child(
    node: TreeNode[object], key: str, value: object, redact: Callable[[str], str] = str
) -> None:
    key = redact(key)  # a server can echo a secret as a JSON key, not just a value
    if isinstance(value, dict):
        label = Text.assemble((key, _AXIS), (f"  {{{len(value)}}}", _DIM))
        _value_into(node.add(label, expand=False), value, redact)
    elif isinstance(value, list):
        label = Text.assemble((key, _AXIS), (f"  [{len(value)}]", _DIM))
        _value_into(node.add(label, expand=False), value, redact)
    else:
        node.add_leaf(Text.assemble((key, _AXIS), (": ", _DIM), _scalar(value, redact)))


def _scalar(value: object, redact: Callable[[str], str] = str) -> tuple[str, str]:
    if value is None:
        return "null", _AXIS
    if isinstance(value, bool):
        return str(value).lower(), _WARN
    if isinstance(value, int | float):
        return str(value), _WARN
    return redact(f'"{value}"'), _SAME


def _content_type(headers: list[tuple[str, str]]) -> str:
    for key, value in headers:
        if key.lower() == "content-type":
            return value.lower()
    return ""


def _body_into(
    node: TreeNode[object], body: bytes, content_type: str, redact: Callable[[str], str] = str
) -> None:
    text = body.decode("utf-8", "replace")
    if "event-stream" in content_type or text.startswith(("data:", "event:", "id:", "retry:")):
        _sse_into(node, text, redact)
        return
    if "json" in content_type or text[:1] in "{[":
        try:
            _value_into(node, json.loads(body), redact)
            return
        except (ValueError, TypeError):
            pass
    if "html" in content_type or text.lstrip()[:1] == "<":
        # Redact the whole body BEFORE truncating, so a secret straddling the cut
        # can never leak its prefix (the same rule _sv follows).
        _HtmlOutline(node).feed(redact(text)[:20000])
        return
    for line in redact(text)[:4000].splitlines()[:200]:
        node.add_leaf(Text(line, style=_TEXT))


def _sse_into(node: TreeNode[object], text: str, redact: Callable[[str], str] = str) -> None:
    events = parse_sse(text)
    if not events:
        node.add_leaf(Text("(no events)", style=_DIM))
        return
    for index, event in enumerate(events):
        label = Text.assemble((f"event {index}", _AXIS))
        if event.get("event"):
            label.append(f"  {redact(event['event'])}", style=_ACCENT)
        entry = node.add(label, expand=len(events) <= 8)
        if event.get("id"):
            entry.add_leaf(Text.assemble(("id: ", _DIM), (redact(event["id"]), _TEXT)))
        data = event.get("data", "")
        try:
            _value_into(entry.add(Text("data", style=_DIM), expand=True), json.loads(data), redact)
        except (ValueError, TypeError):
            # Redact before the 200-char clip so a straddling secret can't leak.
            entry.add_leaf(Text.assemble(("data: ", _DIM), (redact(data)[:200], _TEXT)))


class _HtmlOutline(HTMLParser):
    """Streams parsed HTML into a collapsible tag tree under a node."""

    def __init__(self, root: TreeNode[object]) -> None:
        """Start the outline under *root*."""
        super().__init__(convert_charrefs=True)
        self._stack = [root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Open a collapsible node for the tag."""
        label = Text.assemble((f"<{tag}>", _ACCENT))
        rendered = " ".join(f"{key}={value}" for key, value in attrs if value)
        if rendered:
            label.append(f"  {rendered}", style=_DIM)
        self._stack.append(self._stack[-1].add(label, expand=False))

    def handle_endtag(self, tag: str) -> None:
        """Close the current tag."""
        if len(self._stack) > 1:
            self._stack.pop()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Add a self-closing tag as a leaf."""
        self._stack[-1].add_leaf(Text(f"<{tag}/>", style=_ACCENT))

    def handle_data(self, data: str) -> None:
        """Add non-empty text content as a leaf."""
        text = data.strip()
        if text:
            self._stack[-1].add_leaf(Text(text[:200], style=_TEXT))


def _diff_ready(cells: list[CellDiff], pair: tuple[Environment, Environment] | None) -> Group:
    parts: list[RenderableType] = []
    if pair is None:
        text = Text("No diff pair configured.\n\n", style=f"bold {_WARN}")
        text.append("Add one to the project manifest:\n\n", style=_DIM)
        text.append(
            "  environments:\n    diffPairs:\n      - name: local-vs-prod\n"
            "        baseline: local\n        candidate: prod",
            style=_TEXT,
        )
        return Group(text)
    baseline, candidate = pair
    if cells:
        parts.append(Text("✓ every compared field is identical — gate PASS", style=f"bold {_SAME}"))
    else:
        head = Text(style=_TEXT_HI)
        head.append(f"Ready to diff {baseline.metadata.name} ⇄ {candidate.metadata.name}.\n\n")
        head.append("Press ", style=_DIM)
        head.append("x", style=f"bold {_ACCENT}")
        head.append(" to diff the selected requests against both.", style=_DIM)
        parts.append(head)
    parts.append(_diff_legend())
    return Group(*parts)


def _seg_toggle(options: tuple[str, ...], active: str) -> Text:
    """A mockup-style pill toggle: the active segment reversed, the rest dim."""
    text = Text()
    for index, option in enumerate(options):
        on = option == active
        if index:
            text.append(" ", style=_DIM)
        text.append(
            f" {option} ", style=f"bold {_INK} on {_ACCENT}" if on else f"{_DIM} on #1b2230"
        )
    return text


def _diff_legend() -> Text:
    text = Text("\n")
    text.append("▏", style=_SAME)
    text.append(" identical   ", style=_DIM)
    text.append("▌", style=_DRIFT)
    text.append(" ", style=_DIM)
    text.append("drift", style=_DRIFT)
    text.append("   ╎", style=_SKIP)
    text.append(" not compared", style=_DIM)
    return text


def _replay_drift_summary(record: ReportRecord) -> Text:
    """``one field · N cells · one bug, not N`` — the matrix-grouping takeaway."""
    fields = len(_replay_drift_groups(record))
    cells = sum(row.drift for row in record.requests)
    field_word = "one field" if fields == 1 else f"{fields} fields"
    text = Text(f"{field_word} · {cells} cell{'' if cells == 1 else 's'}", style=_DIM)
    if fields == 1 and cells > 1:
        text.append(f" · one bug, not {cells}", style=_DIM)
    return text


def _diff_field(
    group: tuple[str, list[tuple[CellDiff, FieldDiff]]],
    pair: tuple[Environment, Environment] | None,
    redact: Callable[[str], str] = str,
) -> Group:
    path, entries = group
    baseline = pair[0].metadata.name if pair else "A"
    candidate = pair[1].metadata.name if pair else "B"
    parts: list[RenderableType] = []
    header = Text(redact(path), style=f"bold {_DRIFT}")
    header.append(f"   drifts on {len(entries)} cell{'' if len(entries) == 1 else 's'}", style=_DIM)
    parts.append(header)
    for cell, field in entries:
        block = Text("\n")
        block.append(f"{redact(cell.cell_key) or cell.request.metadata.name}", style=_AXIS)
        block.append(f"   {field.mode}\n", style=_MODE.get(field.mode, _DIM))
        detail = redact(field.detail)  # mask a secret echoed into the drifted value
        before, sep, after = detail.partition(" → ")
        if sep:
            block.append("  ▌ ", style=_DRIFT)
            block.append(f"{baseline:<10}", style=_DIM)
            block.append(_clip(before), style=_SAME)
            block.append("\n  ▌ ", style=_DRIFT)
            block.append(f"{candidate:<10}", style=_DIM)
            block.append(_clip(after), style=_DRIFT)
            block.append("\n")
        else:
            block.append("  ▌ ", style=_DRIFT)
            block.append(_clip(detail) or "differs", style=_TEXT)
            block.append("\n")
        parts.append(block)
    parts.append(_diff_legend())
    hint = Text("\npress ", style=_DIM)
    hint.append("i", style=f"bold {_ACCENT}")
    hint.append(" to silence this field — writes an ignore rule to the profile", style=_DIM)
    parts.append(hint)
    return Group(*parts)


def _sv(value: object, redact: Callable[[str], str] = str) -> str:
    # Redact BEFORE truncating, so a long secret's prefix can never survive the
    # 60-char clip on its way to the screen.
    rendered = redact(json.dumps(value, ensure_ascii=False))
    return rendered if len(rendered) <= 60 else f"{rendered[:57]}..."


def _clip(text: str, limit: int = 80) -> str:
    """Truncate an already-redacted string for compact display."""
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _pad_cells(text: str, width: int) -> str:
    """Left-justify *text* to a fixed terminal-*cell* width, clipping if too wide.

    Uses ``rich.cells.cell_len`` (display width) rather than ``len`` so wide
    Unicode names still align a following column, and clips with an ellipsis so an
    over-long name never pushes the column out of alignment.
    """
    if cell_len(text) > width:
        clipped = text
        while clipped and cell_len(clipped) > width - 1:
            clipped = clipped[:-1]
        return f"{clipped}…"
    return text + " " * (width - cell_len(text))


def _app_redact(widget: Widget) -> Callable[[str], str]:
    """The project's secret-redactor for a widget, or identity if no project is loaded."""
    project = cast("ComparoApp", widget.app).project
    return Redactor.for_project(project).text if project is not None else str


def _body_diff_lines(
    base: object,
    cand: object,
    states: dict[str, FieldDiff],
    path: str = "$",
    depth: int = 0,
    key: str | None = None,
    trailing: str = "",
    redact: Callable[[str], str] = str,
) -> list[tuple[int, str, str, str, str]]:
    """Walk both response trees, yielding (depth, left, right, state, note) rows.

    ``state`` is ``same`` / ``drift`` / ``skip`` from the profile's FieldDiff at
    that path (``context`` for structural braces); ``note`` carries the skip mode.
    ``redact`` masks secret values echoed into the response before they render.
    """
    label = f'"{redact(key)}": ' if key is not None else ""
    decided = states.get(path)
    if (
        decided is not None
        and decided.state.value in ("skip", "drift")
        and isinstance(base, (dict, list))
    ):
        # The profile decided this whole node at once (e.g. an ignored $.headers,
        # or a type/length drift) — collapse it rather than recursing in.
        if isinstance(base, dict):
            placeholder = f"{{ … {len(base)} keys … }}"
        else:
            placeholder = f"[ … {len(base)} items … ]"
        note = f"{decided.mode}  {redact(path)}" if decided.state.value == "skip" else ""
        line = f"{label}{placeholder}{trailing}"
        return [(depth, line, line, decided.state.value, note)]
    if isinstance(base, dict) and isinstance(cand, dict):
        rows: list[tuple[int, str, str, str, str]] = [
            (depth, f"{label}{{", f"{label}{{", "context", "")
        ]
        names = sorted(set(base) | set(cand))
        for index, name in enumerate(names):
            child = f"{path}.{name}"
            tail = "," if index < len(names) - 1 else ""
            if name in base and name in cand:
                rows += _body_diff_lines(
                    base[name], cand[name], states, child, depth + 1, name, tail, redact
                )
            elif name in base:
                left = f'"{redact(name)}": {_sv(base[name], redact)}{tail}'
                rows.append((depth + 1, left, "", "drift", ""))
            else:
                right = f'"{redact(name)}": {_sv(cand[name], redact)}{tail}'
                rows.append((depth + 1, "", right, "drift", ""))
        rows.append((depth, f"}}{trailing}", f"}}{trailing}", "context", ""))
        return rows
    if isinstance(base, list) and isinstance(cand, list):
        rows = [(depth, f"{label}[", f"{label}[", "context", "")]
        size = max(len(base), len(cand))
        for index in range(size):
            child = f"{path}[{index}]"
            tail = "," if index < size - 1 else ""
            if index < len(base) and index < len(cand):
                rows += _body_diff_lines(
                    base[index], cand[index], states, child, depth + 1, None, tail, redact
                )
            elif index < len(base):
                rows.append((depth + 1, f"{_sv(base[index], redact)}{tail}", "", "drift", ""))
            else:
                rows.append((depth + 1, "", f"{_sv(cand[index], redact)}{tail}", "drift", ""))
        rows.append((depth, f"]{trailing}", f"]{trailing}", "context", ""))
        return rows
    field = states.get(path)
    state = field.state.value if field is not None else "same"
    note = f"{field.mode}  {redact(path)}" if field is not None and state == "skip" else ""
    left = f"{label}{_sv(base, redact)}{trailing}"
    right = f"{label}{_sv(cand, redact)}{trailing}"
    return [(depth, left, right, state, note)]


def _band(content: RenderableType, bg: str, *, expand: bool = True) -> Table:
    """A single full-width row whose background *bg* fills the whole cell.

    Rich fills a cell's padding with the *row* style, not the cell renderable's
    style, so a one-row ``expand`` table is the reliable primitive for a band that
    spans the full width at any panel size — in both the unified and the
    side-by-side view.
    """
    table = Table(expand=expand, box=None, show_header=False, padding=(0, 1))
    table.add_column(ratio=1)
    table.add_row(content, style=f"on {bg}")
    return table


def _hunk_band(hunk_text: str) -> Table:
    """The purple ``@@ … @@`` header row that opens the diff well."""
    return _band(Text(hunk_text, style=f"bold {_AXIS}", no_wrap=True), _HUNK_BG)


def _diff_unified(lines: list[tuple[int, str, str, str, str]]) -> Group:
    """A git-style unified diff: one full-width band per line.

    Deleted (baseline) lines carry a muted-red band, added (candidate) lines a
    muted-green band, unchanged/skip lines the recessed well — each filling the
    whole width so the well reads as one contiguous block.
    """
    ink = {_DEL_BG: _DRIFT, _ADD_BG: _SAME, _DIFF_BG: _DIM}
    rows: list[RenderableType] = []
    for sign, body, bg in _unified_rows(lines):
        fg = ink[bg]
        line = Text(no_wrap=True)
        line.append(f"{sign} ", style=f"bold {fg}")
        line.append(body, style=fg)
        rows.append(_band(line, bg))
    return Group(*rows)


def _unified_rows(lines: list[tuple[int, str, str, str, str]]) -> list[tuple[str, str, str]]:
    """(sign, body, band-bg) per rendered diff line."""
    rendered: list[tuple[str, str, str]] = []
    for depth, left, right, state, note in lines:
        pad = "  " * depth
        if state == "drift":
            if left:
                rendered.append(("-", f"{pad}{left}", _DEL_BG))
            if right:
                rendered.append(("+", f"{pad}{right}", _ADD_BG))
        elif state == "skip":
            rendered.append(("⋯", f"{pad}{left}   skipped · {note}", _DIFF_BG))
        else:
            rendered.append((" ", f"{pad}{left}", _DIFF_BG))
    return rendered


def _diff_side_by_side(
    lines: list[tuple[int, str, str, str, str]],
    pair: tuple[Environment, Environment] | None,
    names: tuple[str, str] | None = None,
) -> Table:
    """A two-pane diff with the SAME full-width bands as the unified view.

    Each pane is a stack of banded cells (red on the baseline side, green on the
    candidate side, well-dark for context) so both views share one visual style.
    """
    baseline = names[0] if names else (pair[0].metadata.name if pair else "baseline")
    candidate = names[1] if names else (pair[1].metadata.name if pair else "candidate")
    left_col: list[RenderableType] = [_band(Text(baseline, style=f"bold {_DIM}"), _DIFF_BG)]
    right_col: list[RenderableType] = [_band(Text(candidate, style=f"bold {_DIM}"), _DIFF_BG)]
    for depth, left, right, state, note in lines:
        pad = "  " * depth
        if state == "drift":
            left_col.append(_band(Text(f"{pad}{left}", style=_DRIFT, no_wrap=True), _DEL_BG))
            right_col.append(_band(Text(f"{pad}{right}", style=_SAME, no_wrap=True), _ADD_BG))
        elif state == "skip":
            left_col.append(
                _band(Text(f"{pad}{left}  ⋯ {note}", style=_SKIP, no_wrap=True), _DIFF_BG)
            )
            right_col.append(_band(Text(f"{pad}{right}", style=_SKIP, no_wrap=True), _DIFF_BG))
        else:
            left_col.append(_band(Text(f"{pad}{left}", style=_DIM, no_wrap=True), _DIFF_BG))
            right_col.append(_band(Text(f"{pad}{right}", style=_DIM, no_wrap=True), _DIFF_BG))
    table = Table(expand=True, box=None, show_header=False, padding=0)
    table.add_column(ratio=1)
    table.add_column(ratio=1)
    table.add_row(Group(*left_col), Group(*right_col))
    return table


def _diff_slug(name: str) -> str:
    """A git-path-friendly slug of a request name (``Price quote`` → ``price-quote``)."""
    lowered = "".join(char if char.isalnum() else "-" for char in name.lower())
    return "-".join(filter(None, lowered.split("-"))) or "response"


def _diff_body_view(
    group: tuple[str, list[tuple[CellDiff, FieldDiff]]],
    pair: tuple[Environment, Environment] | None,
    *,
    unified: bool,
    names: tuple[str, str] | None = None,
    redact: Callable[[str], str] = str,
    chrome: bool = True,
) -> Group:
    path, entries = group
    if not entries:
        return _diff_ready([], pair)
    cell = entries[0][0]
    if cell.baseline_body is None or cell.candidate_body is None:
        return _diff_field(group, pair, redact)  # non-JSON / error cell — fall back
    baseline = names[0] if names else (pair[0].metadata.name if pair else "a")
    candidate = names[1] if names else (pair[1].metadata.name if pair else "b")
    slug = _diff_slug(cell.request.metadata.name)
    outbound = cell.request.spec.request
    parent = path.rsplit(".", 1)[0] if "." in path.lstrip("$.") else path
    states = {field.path: field for field in cell.fields}
    lines = _body_diff_lines(cell.baseline_body, cell.candidate_body, states, redact=redact)
    adds = sum(1 for _, _, right, state, _ in lines if state == "drift" and right)
    dels = sum(1 for _, left, _, state, _ in lines if state == "drift" and left)
    # .difftitle — git command on the left, hunk/stat count right-aligned.
    title = Table(box=None, expand=True, show_header=False, padding=0)
    title.add_column(justify="left")
    title.add_column(justify="right")
    cmd = Text("diff ", style=_DIM)
    for marker, value in (("a/", baseline.lower()), ("b/", candidate.lower())):
        cmd.append(marker, style=f"bold {_ACCENT}")
        cmd.append(value, style=_DIM)
        cmd.append("/", style=f"bold {_ACCENT}")
        cmd.append(f"{slug}.json ", style=_DIM)
    stat = Text("1 hunk · ", style=_DIM)
    stat.append(f"+{adds}", style=_SAME)
    stat.append(" ", style=_DIM)
    stat.append(f"−{dels}", style=_DRIFT)
    title.add_row(cmd, stat)
    # .hunk — the request, the containing path, the cell case, the HTTP call.
    # The hunk header sits on its own muted-purple band, matching the mockup .hunk;
    # it pads to the same width as the body bands so the well reads as one block.
    case = f"  {redact(cell.cell_key)}" if cell.cell_key else ""
    hunk_text = (
        f"@@ {cell.request.metadata.name} · {redact(parent)} @@{case}  "
        f"{outbound.method} {redact(outbound.endpoint)}"
    )
    body = _diff_unified(lines) if unified else _diff_side_by_side(lines, pair, names)
    # The whole well — the purple hunk band and the banded body — sits inside one
    # rounded outline that fills the panel width, so it reads as a single unit.
    well = Panel(
        Group(_hunk_band(hunk_text), body),
        box=ROUNDED,
        expand=True,
        padding=0,
        border_style=_WELL_BORDER,
    )
    if not chrome:
        # Stacked (in-flow) diff: one legend is rendered once at the bottom, and the
        # per-cell insight/silence hints are suppressed to keep the stack readable.
        return Group(title, Text(), well)
    insight = Text(
        "\nthe same request is replayed against both sides — press o to diff the "
        "outbound and confirm the drift is the service's",
        style=_SAME,
    )
    hint = Text("\npress ", style=_DIM)
    hint.append("v", style=f"bold {_ACCENT}")
    hint.append(f" for {'side-by-side' if unified else 'unified'}    ", style=_DIM)
    hint.append("o", style=f"bold {_ACCENT}")
    hint.append(" outbound    ", style=_DIM)
    hint.append("i", style=f"bold {_ACCENT}")
    hint.append(" to silence this field", style=_DIM)
    return Group(title, Text(), well, _git_legend(baseline, candidate), insight, hint)


def _git_legend(baseline: str, candidate: str) -> Text:
    text = Text("\n")
    text.append("− ", style=f"bold {_DRIFT}")
    text.append("baseline ", style=_DIM)
    text.append(baseline, style=_TEXT)
    text.append("    + ", style=f"bold {_SAME}")
    text.append("candidate ", style=_DIM)
    text.append(candidate, style=_TEXT)
    text.append("    ⋯ ", style=_SKIP)
    text.append("skipped by profile", style=_DIM)
    text.append("    dim ", style=_DIM)
    text.append("= unchanged", style=_DIM)
    return text


def _diff_skip_view(
    path: str,
    group: tuple[str, list[tuple[CellDiff, FieldDiff]]] | None,
    redact: Callable[[str], str] = str,
) -> Group:
    """Explain a path the DiffProfile skips — the tri-state's third leg."""
    parts: list[RenderableType] = []
    head = Text()
    head.append("◐ ", style=_SKIP)
    head.append(redact(path), style=f"bold {_SKIP}")
    head.append("   skipped by the diff profile", style=_DIM)
    parts.append(head)
    if group is not None:
        _, entries = group
        mode = entries[0][1].mode
        requests = sorted({cell.request.metadata.name for cell, _ in entries})
        info = Text("\nmode ", style=_DIM)
        info.append(mode, style=_MODE.get(mode, _SKIP))
        info.append(
            f"   ·   {len(entries)} cell{'' if len(entries) == 1 else 's'}   ·   ", style=_DIM
        )
        info.append(", ".join(requests), style=_AXIS)
        parts.append(info)
    body = Text("\n\nThis path is deliberately not compared", style=_SKIP)
    body.append(
        " — a volatile field (a\ntimestamp, an echoed request, a generated id) whose value\n"
        "legitimately differs between environments. It is neither\nsame nor drift.",
        style=_DIM,
    )
    parts.append(body)
    note = Text("\n\nskip stays visible: ", style=_DIM)
    note.append("green never means full coverage.", style=_SKIP)
    parts.append(note)
    return Group(*parts)


def _diff_error_view(
    cell: CellDiff,
    pair: tuple[Environment, Environment] | None,
    *,
    names: tuple[str, str] | None = None,
    redact: Callable[[str], str] = str,
) -> Group:
    """Explain a cell that failed to execute — which request, which env, the message."""
    if names is not None:
        baseline, candidate = names
    else:
        baseline = pair[0].metadata.name if pair else "baseline"
        candidate = pair[1].metadata.name if pair else "candidate"
    parts: list[RenderableType] = []
    head = Text()
    head.append("! ", style=f"bold {_WARN}")
    head.append(cell.request.metadata.name, style=f"bold {_TEXT_HI}")
    identifier = cell.request.metadata.id
    if identifier:
        head.append(f"   {identifier}", style=_DIM)
    parts.append(head)
    sub = Text("\n")
    sub.append(f"{baseline} ⇄ {candidate}", style=_DIM)
    if cell.cell_key:
        sub.append(f"   {redact(cell.cell_key)}", style=_AXIS)
    parts.append(sub)
    which, _, message = redact(cell.error or "unknown error").partition(": ")
    box = Text("\n\n")
    if which in ("baseline", "candidate"):
        box.append(f"the {which} request failed\n", style=f"bold {_WARN}")
        box.append(message or which, style=_DRIFT)
    else:
        box.append("the request failed\n", style=f"bold {_WARN}")
        box.append(redact(cell.error or "unknown error"), style=_DRIFT)
    parts.append(box)
    hint = Text("\n\nNo response to compare against. ", style=_DIM)
    hint.append("Confirm the environment is reachable, then press ", style=_DIM)
    hint.append("x", style=f"bold {_ACCENT}")
    hint.append(" to replay.", style=_DIM)
    parts.append(hint)
    return Group(*parts)


def _outbound_diff_view(
    baseline: ResolvedRequest,
    candidate: ResolvedRequest,
    base_env: Environment,
    cand_env: Environment,
    *,
    redact: Callable[[str], str] = str,
) -> Group:
    """Diff the resolved outbound request across the pair (DIFF-27).

    The same request is replayed against both environments, so the outbound only
    differs where env config does — a different base URL, a per-env auth token,
    an env-specific header or variable. Every value is redacted, and masked
    secrets compare equal, so a hidden token can never surface as a false drift.
    """
    parts: list[RenderableType] = []
    head = Text()
    head.append("OUTBOUND REQUEST", style=f"bold {_LABEL}")
    parts.append(head)
    legend = Text("\n")
    legend.append("− ", style=f"bold {_DRIFT}")
    legend.append(base_env.metadata.name, style=_DIM)
    legend.append("    + ", style=f"bold {_SAME}")
    legend.append(cand_env.metadata.name, style=_DIM)
    parts.append(legend)

    diffs: list[tuple[str, str, str]] = []

    def scalar(label: str, a: object, b: object) -> None:
        sa, sb = redact(str(a)), redact(str(b))
        if sa != sb:
            diffs.append((label, sa, sb))

    def mapping(
        prefix: str,
        a: list[tuple[str, object]] | dict[str, object],
        b: list[tuple[str, object]] | dict[str, object],
    ) -> None:
        am = a if isinstance(a, dict) else dict(a)
        bm = b if isinstance(b, dict) else dict(b)
        ad = {redact(str(k)): redact(str(v)) for k, v in am.items()}
        bd = {redact(str(k)): redact(str(v)) for k, v in bm.items()}
        for key in sorted(set(ad) | set(bd)):
            av, bv = ad.get(key, "—"), bd.get(key, "—")
            if av != bv:
                diffs.append((f"{prefix} {key}", av, bv))

    scalar("method", baseline.method, candidate.method)
    scalar("url", baseline.url, candidate.url)
    mapping("header", baseline.headers, candidate.headers)
    mapping("query", baseline.query, candidate.query)
    if baseline.body != candidate.body:
        diffs.append(("body", "differs — an env value is injected into the body", ""))

    body = Text()
    if not diffs:
        body.append("\n\n✓ identical on both sides", style=f"bold {_SAME}")
        body.append(
            "\n\nThe request we send is the same for both environments, so any"
            "\nresponse drift is the service's — not something we sent differently.",
            style=_DIM,
        )
    else:
        for label, a, b in diffs:
            body.append(f"\n\n{label}", style=_LABEL)
            body.append("\n  − ", style=f"bold {_DRIFT}")
            body.append(a or "—", style=_TEXT)
            if b:
                body.append("\n  + ", style=f"bold {_SAME}")
                body.append(b, style=_TEXT)
        body.append("\n\n⚠ the outbound differs across environments", style=f"bold {_WARN}")
        body.append(
            " — some response drift\nmay be explained by what you send (host, auth, vars), "
            "not the service.",
            style=_DIM,
        )
    parts.append(body)
    return Group(*parts)


def _req_short(request_id: str) -> str:
    return request_id.split(".", 1)[-1]


def _run_label(run_id: str | None) -> str:
    """Display a run id with the ``run-`` prefix used across the UI (``run-7f3a``)."""
    if not run_id:
        return "run"
    return run_id if run_id.startswith("run-") else f"run-{run_id}"


def _assert_tally(results: list[AssertionResult]) -> tuple[int, int, int]:
    passed = failed = warned = 0
    for result in results:
        if result.ok:
            passed += 1
        elif result.severity == "warn":
            warned += 1
        else:
            failed += 1
    return passed, failed, warned


def _assert_count_text(tally: tuple[int, int, int]) -> Text:
    passed, failed, warned = tally
    text = Text()
    text.append(f"{passed} ✓", style=_SAME)
    text.append(" · ", style=_DIM)
    text.append(f"{failed} ✗", style=_DRIFT if failed else _DIM)
    text.append(" · ", style=_DIM)
    text.append(f"{warned} !", style=_WARN if warned else _DIM)
    return text


def _exec_assert_rows(
    outcomes: list[CellOutcome], side: str
) -> tuple[tuple[int, int, int], list[tuple[str, AssertionResult]]]:
    rows: list[tuple[str, AssertionResult]] = []
    flat: list[AssertionResult] = []
    for outcome in outcomes:
        results = (
            outcome.baseline_assertions if side == "baseline" else outcome.candidate_assertions
        )
        for result in results:
            rows.append((outcome.request_id, result))
            flat.append(result)
    return _assert_tally(flat), rows


def _exec_assert_body(
    rows: list[tuple[str, AssertionResult]], redact: Callable[[str], str] = str
) -> Text:
    if not rows:
        return Text("no assertions on this side", style=_DIM)
    text = Text()
    for index, (request_id, result) in enumerate(rows):
        if result.ok:
            glyph, style = "✓", _SAME
        elif result.severity == "warn":
            glyph, style = "!", _WARN
        else:
            glyph, style = "✗", _DRIFT
        if index:
            text.append("\n")
        text.append(f"{glyph} ", style=style)
        text.append(f"{_req_short(request_id):<11}", style=f"bold {_TEXT_HI}")
        text.append(redact(result.label), style=style if not result.ok else _TEXT)
        text.append(f"   {_clip(redact(result.detail))}", style=_DIM)
    return text


def _drift_change(outcome: CellOutcome, redact: Callable[[str], str] = str) -> Text:
    diff = outcome.diff
    if diff is None or not diff.drifts:
        return Text("drift", style=_DRIFT)
    field = diff.drifts[0]
    text = Text(redact(field.path), style=_DRIFT)
    text.append(f"  {field.mode}", style=_DIM)
    detail = _clip(redact(field.detail))
    if detail:
        text.append(f"  {detail}", style=_DIM)
    if len(diff.drifts) > 1:
        text.append(f"  +{len(diff.drifts) - 1}", style=_DIM)
    return text


def _exec_drift_fields(
    outcomes: list[CellOutcome], redact: Callable[[str], str] = str
) -> list[tuple[str, str, str, int, str]]:
    order: list[str] = []
    mode: dict[str, str] = {}
    detail: dict[str, str] = {}
    count: dict[str, int] = {}
    requests: dict[str, list[str]] = {}
    for outcome in outcomes:
        if outcome.diff is None:
            continue
        for field in outcome.diff.drifts:
            if field.path not in count:
                order.append(field.path)
                mode[field.path] = field.mode
                detail[field.path] = _clip(redact(field.detail)) or "differs"
                count[field.path] = 0
                requests[field.path] = []
            count[field.path] += 1
            name = _req_short(outcome.request_id)
            if name not in requests[field.path]:
                requests[field.path].append(name)
    return [
        (redact(path), mode[path], detail[path], count[path], ", ".join(requests[path]))
        for path in order
    ]


def _exec_skip_paths(outcomes: list[CellOutcome]) -> list[str]:
    seen: list[str] = []
    for outcome in outcomes:
        if outcome.diff is None:
            continue
        for field in outcome.diff.fields:
            if field.state is State.SKIP and field.path not in seen:
                seen.append(field.path)
    return seen


def _field_skip_count(diff: CellDiff | None) -> int:
    if diff is None:
        return 0
    return sum(1 for field in diff.fields if field.state is State.SKIP)


def _exec_selected_requests(project: LoadedProject, profile: ExecutionProfile) -> list[Request]:
    """The requests an ExecutionProfile selects — its ``select`` tags / ids, or all."""
    requests = sorted(
        (obj for obj in project.objects.values() if isinstance(obj, Request)),
        key=lambda request: request.metadata.id or "",
    )
    select = profile.spec.select
    ids = set(select.requests or []) if select is not None else set()
    tags = set(select.tags or []) if select is not None else set()
    if not ids and not tags:
        return requests
    return [
        request
        for request in requests
        if request.metadata.id in ids or (tags & set(request.metadata.tags or []))
    ]


def _exec_env_names(project: LoadedProject, profile: ExecutionProfile) -> tuple[str, str | None]:
    """Resolve a profile's baseline / candidate environment *names* for display.

    Falls back to the raw refs when an environment cannot be resolved, so the
    launch preview never crashes on a mis-referenced profile.
    """
    envs = profile.spec.environments
    base_ref = envs.baseline if envs is not None else None
    cand_ref = envs.candidate if envs is not None else None
    try:
        baseline = select_environment(project, base_ref).metadata.name
    except EnvironmentSelectionError:
        baseline = base_ref or "—"
    candidate: str | None = None
    if cand_ref is not None:
        try:
            candidate = select_environment(project, cand_ref).metadata.name
        except EnvironmentSelectionError:
            candidate = cand_ref
    return baseline, candidate


def _exec_mode(profile: ExecutionProfile) -> str:
    """The check mode a profile runs: ``both`` / ``assert`` / ``diff``."""
    check = profile.spec.check
    do_assert = check.assertions if check is not None else True
    do_diff = check.diff if check is not None else True
    envs = profile.spec.environments
    do_diff = do_diff and (envs is not None and envs.candidate is not None)
    if do_assert and do_diff:
        return "both"
    return "assert" if do_assert else "diff"


def _exec_profiles_hint() -> Text:
    """The dim ``run an ExecutionProfile`` header line atop the PROFILES panel."""
    hint = Text("run an ", style=_DIM)
    hint.append("ExecutionProfile", style=f"bold {_AXIS}")
    return hint


def _exec_profile_card(
    project: LoadedProject,
    profile: ExecutionProfile,
    redact: Callable[[str], str] = str,
    caret: bool = False,
) -> Text:
    """A three-line profile card for the launch picker — name, id, envs · mode.

    The highlighted card is prefixed with an accent ``▸`` caret; the others align
    under it with a blank gutter.
    """
    baseline, candidate = _exec_env_names(project, profile)
    card = Text()
    card.append("▸ " if caret else "  ", style=_ACCENT)
    card.append(redact(profile.metadata.name), style=f"bold {_TEXT_HI}")
    card.append(f"\n  {profile.metadata.id or ''}", style=_DIM)
    card.append("\n  ", style=_DIM)
    card.append(redact(baseline), style=_DIM)
    if candidate is not None:
        card.append(" ⇄ ", style=_DIM)
        card.append(redact(candidate), style=_DIM)
    card.append(f"  {_exec_mode(profile)}", style=_ACCENT)
    return card


def _exec_plan_line(
    project: LoadedProject,
    profile: ExecutionProfile,
    request: Request,
    redact: Callable[[str], str],
) -> tuple[Text, int]:
    """One plan-preview row: the request, its call, its matrix scope, and the cell count."""
    scopes = profile.spec.matrix or {}
    cells = expand(project, request, scopes)
    method = request.spec.request.method
    endpoint = redact(request.spec.request.endpoint)
    row = Text("  ")
    row.append("● ", style=_SAME)
    row.append(f"{_clip(request.metadata.name, 13):<14}", style=_TEXT)
    row.append(f"{method} {endpoint} ", style=_DIM)
    targets = {inj.target for cell in cells for inj in cell.injections}
    if targets:
        matrix_ids = []
        cases: list[str] = []
        for target in sorted(targets):
            matrix = next(
                (
                    obj
                    for obj in project.objects.values()
                    if isinstance(obj, Matrix) and obj.spec.target == target
                ),
                None,
            )
            if matrix is not None:
                matrix_ids.append(matrix.metadata.id or matrix.metadata.name)
        seen = [redact(cell.key) for cell in cells if cell.key]
        for key in seen:
            if key not in cases:
                cases.append(key)
        row.append(f"  {', '.join(matrix_ids)}", style=_AXIS)
        row.append(f" → {_clip(', '.join(cases), 24)}", style=_DIM)
    else:
        row.append("  no matrix", style=_DIM)
    row.append(f"   ×{len(cells)}", style=_DIM)
    return row, len(cells)


def _exec_setup(
    project: LoadedProject, profile: ExecutionProfile, redact: Callable[[str], str] = str
) -> Group:
    """The launch SETUP panel — pair, mode, selection, and the counted plan preview."""
    baseline, candidate = _exec_env_names(project, profile)
    mode = _exec_mode(profile)
    parts: list[RenderableType] = []
    head = Text()
    head.append(redact(profile.metadata.name), style=f"bold {_TEXT_HI}")
    if profile.metadata.description:
        head.append(f"   {_clip(redact(profile.metadata.description), 44)}", style=_DIM)
    parts.append(head)
    pair = Text("\nbaseline   ", style=_DIM)
    pair.append(f" {redact(baseline)} ", style=f"bold {_TEXT_HI} on {_SYNTAX_BG}")
    pair.append("  ⇄   ", style=_SAME)
    pair.append("candidate  ", style=_DIM)
    pair.append(
        f" {redact(candidate) if candidate else '—'} ", style=f"bold {_TEXT_HI} on {_SYNTAX_BG}"
    )
    parts.append(pair)
    mode_line = Text("\nmode       ", style=_DIM)
    for option in ("assert", "diff", "both"):
        on = option == mode
        mode_line.append(
            f" {option} ", style=f"bold {_INK} on {_ACCENT}" if on else f"{_DIM} on {_SYNTAX_BG}"
        )
        mode_line.append(" ", style=_DIM)
    mode_line.append("  both = assert ∧ diff", style=_DIM)
    parts.append(mode_line)
    select = profile.spec.select
    sel = Text("\nselect     ", style=_DIM)
    if select is not None and select.tags:
        sel.append("tags ", style=_DIM)
        sel.append(", ".join(redact(tag) for tag in select.tags), style=f"bold {_AXIS}")
        sel.append(" ✓", style=_SAME)
    if select is not None and select.requests:
        sel.append("   requests ", style=_DIM)
        sel.append(", ".join(redact(_req_short(r)) for r in select.requests), style=_TEXT)
    elif select is not None and select.tags:
        # A tag-based select runs every request carrying the tag.
        sel.append("   requests ", style=_DIM)
        sel.append("all in tag", style=_TEXT)
    if select is None or (not select.tags and not select.requests):
        sel.append("all requests", style=_TEXT)
    parts.append(sel)
    parts.append(Text("\nplan preview", style=f"bold {_DIM}"))
    total = 0
    for request in _exec_selected_requests(project, profile):
        line, count = _exec_plan_line(project, profile, request, redact)
        parts.append(line)
        total += count
    summary = Text("\n  will run ", style=_DIM)
    summary.append(f"{total} cell{'' if total == 1 else 's'}", style=f"bold {_TEXT_HI}")
    if mode == "both":
        summary.append(" · assertions on both sides · diff the pair", style=_DIM)
    elif mode == "assert":
        summary.append(" · assertions on both sides", style=_DIM)
    else:
        summary.append(" · diff the pair", style=_DIM)
    parts.append(summary)
    return Group(*parts)


class _RunningRow(NamedTuple):
    """One cell in the live running transition — the in-flight cell or a finished one.

    ``variant``/``method_path``/``drift`` are already redacted by the view before the
    row is built, so ``_running_body`` never handles a raw declared secret.
    """

    request: str
    variant: str = ""
    method_path: str = ""
    status: int | None = None
    baseline_ms: int | None = None
    candidate_ms: int | None = None
    drift: str = ""


def _running_cell_name(row: _RunningRow, hi: bool = True) -> Text:
    """``Price quote · free`` — request name, matrix variant axis-purple.

    ``hi`` is True for the in-flight cell (bold+bright) and False for the finished
    log (normal weight) so the eye separates the cell in flight from the log.
    """
    name = Text(row.request, style=f"bold {_TEXT_HI}" if hi else _TEXT)
    if row.variant:
        name.append(f" · {row.variant}", style=_AXIS)
    return name


def _running_row_from_progress(
    event: ExecutionProgress, redact: Callable[[str], str] = str
) -> _RunningRow:
    """Build a redacted live row from an engine tick — no raw secret is stored."""
    method_path = f"{event.method} {redact(event.path)}" if event.method else ""
    drift_leaf = redact(event.drift).rsplit(".", 1)[-1] if event.drift else ""
    return _RunningRow(
        request=_req_short(event.request_id),
        variant=redact(event.cell_key) if event.cell_key else "",
        method_path=method_path,
        status=event.status,
        baseline_ms=event.baseline_ms,
        candidate_ms=event.candidate_ms,
        drift=drift_leaf,
    )


def _running_body(
    label: str,
    done: int,
    total: int,
    current: _RunningRow | None,
    recent: list[_RunningRow],
    glyphs: list[str],
) -> Group:
    """The live running transition — progress bar, cell in flight, finished cells.

    Shared by the Execution running sub-view and the Diff RUNNING state so both
    speak the same visual language.
    """
    parts: list[RenderableType] = []
    head = Text()
    head.append(label or "run", style=f"bold {_TEXT_HI}")
    head.append("   executing the plan…", style=_DIM)
    parts.append(head)
    width = 24
    filled = round(width * done / total) if total else 0
    # Real tallies from the per-cell glyphs the caller maintains (✓ pass, ✗ fail),
    # never a hardcoded count.
    passed = sum(1 for glyph in glyphs if glyph == "✓")
    failed = sum(1 for glyph in glyphs if glyph == "✗")
    bar = Text("\n")
    bar.append("█" * filled, style=_ACCENT)
    bar.append("░" * (width - filled), style=_DIM)
    bar.append(f"   {done}/{total or '…'} cells", style=_TEXT)
    bar.append("   ", style=_DIM)
    bar.append(f"{passed} ✓", style=_SAME)
    bar.append("  ", style=_DIM)
    bar.append(f"{failed} ✗", style=_DRIFT if failed else _DIM)
    parts.append(bar)
    cur = Text("\n▸ ", style=_ACCENT)
    if (done < total or not total) and current is not None:
        cur.append("running ", style=_DIM)
        cur.append_text(_running_cell_name(current))
        if current.method_path:
            cur.append(f"     {current.method_path}", style=_DIM)
        cur.append("     baseline ", style=_DIM)
        cur.append("◐", style=_WARN)
        cur.append("  candidate ", style=_DIM)
        cur.append("◐", style=_WARN)
    elif done < total or not total:
        cur.append("running …", style=_DIM)
    else:
        cur.append("finishing…", style=_DIM)
    parts.append(cur)
    if recent:
        parts.append(Text("\nrecently finished", style=f"bold {_DIM}"))
        log = Text()
        for index, row in enumerate(recent[-6:]):
            if index:
                log.append("\n")
            # A finished row that drifted is a failure — don't paint it green.
            if row.drift:
                log.append("✗ ", style=_DRIFT)
            else:
                log.append("✓ ", style=_SAME)
            log.append_text(_running_cell_name(row, hi=False))
            if row.method_path:
                log.append(f"    {row.method_path}", style=_DIM)
            if row.status is not None:
                log.append(f"    {row.status}", style=_SAME)
            if row.baseline_ms is not None:
                log.append(f"  {row.baseline_ms}ms base", style=_DIM)
            if row.candidate_ms is not None:
                log.append(f"  {row.candidate_ms}ms cand", style=_DIM)
            if row.drift:
                log.append(f"   ✗ {row.drift} drift", style=_DRIFT)
        parts.append(log)
    plan = Text("\nlive plan   ", style=f"bold {_DIM}")
    for glyph in glyphs:
        colour = _SAME if glyph == "●" else _WARN if glyph == "◐" else _DIM
        plan.append(glyph, style=colour)
    plan.append("   each glyph = one cell, updating as the engine ticks", style=_DIM)
    parts.append(plan)
    return Group(*parts)


def _exec_stacked_diff(
    drifted: list[CellOutcome],
    baseline: str,
    candidate: str | None,
    *,
    unified: bool,
    redact: Callable[[str], str] = str,
) -> Group:
    """The run's scoped body diff — every drifted cell stacked as a git-style well."""
    head = Text()
    head.append(baseline, style=_TEXT_HI)
    head.append(" ● ⇄ ", style=_SAME)
    head.append(candidate or "—", style=f"bold {_TEXT_HI}")
    head.append(
        f"    {len(drifted)} drifted cell(s) · one field, grouped across the matrix",
        style=_DIM,
    )
    parts: list[RenderableType] = [head, Text()]
    names = (baseline, candidate or "candidate")
    for outcome in drifted:
        crumb = Text("▸ ", style=_DRIFT)
        crumb.append(_req_short(outcome.request_id), style=f"bold {_TEXT_HI}")
        if outcome.cell_key:
            crumb.append(f" · {redact(outcome.cell_key)}", style=_AXIS)
        request = outcome.diff.request if outcome.diff is not None else None
        if request is not None:
            crumb.append(
                f"    {request.spec.request.method} {redact(request.spec.request.endpoint)}",
                style=_DIM,
            )
        parts.append(crumb)
        if outcome.error is not None and outcome.diff is not None:
            parts.append(_diff_error_view(outcome.diff, None, names=names, redact=redact))
        elif outcome.diff is not None:
            entries = [(outcome.diff, field) for field in outcome.diff.drifts]
            path = entries[0][1].path if entries else "$"
            parts.append(
                _diff_body_view(
                    (path, entries),
                    None,
                    unified=unified,
                    names=names,
                    redact=redact,
                    chrome=False,
                )
            )
        else:
            parts.append(Text("no diff computed for this cell", style=_DIM))
        parts.append(Text())
    # One shared git legend at the bottom of the stack — not repeated per cell.
    parts.append(_git_legend(baseline, candidate or "candidate"))
    return Group(*parts)


def _exec_header(
    profile: ExecutionProfile, result: ExecutionResult, redact: Callable[[str], str] = str
) -> Group:
    """The results EXECUTION panel — two rows so nothing clips at 104 cols.

    Row 1: the profile + its id + the baseline/candidate pair.
    Row 2: the mode, the select clause, and the counted plan (``req x2 · req x1``).
    """
    line1 = Text()
    line1.append("ExecutionProfile ", style=_DIM)
    line1.append(profile.metadata.name, style=f"bold {_TEXT_HI}")
    line1.append(f"  {profile.metadata.id or ''}", style=_DIM)
    line1.append("    baseline ", style=_DIM)
    line1.append(redact(result.baseline), style=_TEXT_HI)
    line1.append(" ●", style=_SAME)
    if result.candidate is not None:
        line1.append(" · candidate ", style=_DIM)
        line1.append(redact(result.candidate), style=_TEXT_HI)
        line1.append(" ●", style=_SAME)
    if result.checked_assertions and result.checked_diff:
        mode, detail = "both", " assertions + diff"
    elif result.checked_assertions:
        mode, detail = "assert", ""
    else:
        mode, detail = "diff", ""
    line2 = Text()
    line2.append("mode ", style=_DIM)
    line2.append(mode, style=f"bold {_ACCENT}")
    line2.append(detail, style=_DIM)
    select = profile.spec.select
    if select is not None and (select.tags or select.requests):
        clauses = []
        if select.tags:
            clauses.append("tags " + ", ".join(redact(tag) for tag in select.tags))
        if select.requests:
            clauses.append("requests " + ", ".join(redact(_req_short(r)) for r in select.requests))
        line2.append("    select ", style=_DIM)
        line2.append(" · ".join(clauses), style=_TEXT_HI)
    # plan — per-request cell counts, in first-seen order (Price quote x2 · Checkout x1)
    order: list[str] = []
    counts: dict[str, int] = {}
    for outcome in result.outcomes:
        name = _req_short(outcome.request_id)
        if name not in counts:
            order.append(name)
            counts[name] = 0
        counts[name] += 1
    if order:
        line2.append("    plan ", style=_DIM)
        line2.append(" · ".join(f"{redact(name)} ×{counts[name]}" for name in order), style=_TEXT)
    return Group(line1, line2)


def _exec_diff_summary(result: ExecutionResult, redact: Callable[[str], str] = str) -> Text:
    outcomes = result.outcomes
    calls = len(outcomes)
    drift, errors = result.drift, result.errors
    same = calls - drift - errors
    skipped = sum(_field_skip_count(outcome.diff) for outcome in outcomes)
    text = Text()
    text.append(f"{same} same", style=f"bold {_SAME}")
    text.append(" · ", style=_DIM)
    text.append(f"{drift} drift", style=f"bold {_DRIFT}" if drift else _DRIFT)
    text.append(" · ", style=_DIM)
    text.append(f"{errors} error", style=f"bold {_WARN}" if errors else _WARN)
    text.append(" · ", style=_DIM)
    text.append(f"{skipped} skipped", style=_SKIP)
    for path, mode, detail, count, requests in _exec_drift_fields(outcomes, redact):
        text.append("\n✗ ", style=_DRIFT)
        text.append(path, style=_DRIFT)
        text.append(f"  {mode} · ", style=_DIM)
        text.append(detail, style=_DRIFT)
        text.append(f" · {requests} ×{count}", style=_DIM)
    return text


def _exec_diff_legend(result: ExecutionResult, redact: Callable[[str], str] = str) -> Text:
    skips = _exec_skip_paths(result.outcomes)
    text = Text()
    if skips:
        for index, path in enumerate(skips):
            if index:
                text.append(" · ", style=_DIM)
            text.append(f"◐ {redact(path)}", style=_SKIP)
        text.append(" skipped by the diff profile (volatile) — ", style=_DIM)
        text.append("⏎", style=f"bold {_TEXT_HI}")
        text.append(" drills in.", style=_DIM)
    else:
        text.append("no paths skipped — ", style=_DIM)
        text.append("⏎", style=f"bold {_TEXT_HI}")
        text.append(" on a drifted cell drills in.", style=_DIM)
    return text


def _exec_gate_body(result: ExecutionResult) -> Group:
    parts: list[RenderableType] = []
    if result.passed:
        parts.append(Text("✓ gate: PASS", style=f"bold {_SAME}"))
    else:
        parts.append(Text("✗ gate: FAIL", style=f"bold {_DANGER}"))
    base_fail = sum(_assert_tally(o.baseline_assertions)[1] for o in result.outcomes)
    cand_fail = sum(_assert_tally(o.candidate_assertions)[1] for o in result.outcomes)
    warns = sum(
        _assert_tally(o.baseline_assertions)[2] + _assert_tally(o.candidate_assertions)[2]
        for o in result.outcomes
    )
    untriaged = result.drift + result.errors
    assert_failed = base_fail + cand_fail
    narrative = Text("\n")
    if result.passed:
        narrative.append("assertions hold on both sides and nothing drifted.", style=_DIM)
    elif assert_failed > 0:
        # Do not paint a red run green: name the failing assertions as a blocker.
        narrative.append("assertions ", style=_DIM)
        narrative.append("FAIL", style=_DRIFT)
        narrative.append(
            f" (baseline {base_fail} ✗ · candidate {cand_fail} ✗ · {warns} warn)", style=_DIM
        )
        if untriaged:
            noun = "drift" if untriaged == 1 else "drifts"
            narrative.append(" and ", style=_DIM)
            narrative.append(f"{untriaged} untriaged {noun}", style=_DRIFT)
        narrative.append(" block the run.", style=_DIM)
    else:
        narrative.append("assertions ", style=_DIM)
        narrative.append("pass", style=_SAME)
        narrative.append(f" on both sides ({warns} warn) — but ", style=_DIM)
        noun = "drift" if untriaged == 1 else "drifts"
        narrative.append(f"{untriaged} untriaged {noun}", style=_DRIFT)
        narrative.append(" block the run. Triage (", style=_DIM)
        narrative.append("i", style=f"bold {_ACCENT}")
        narrative.append("/", style=_DIM)
        narrative.append("x", style=f"bold {_ACCENT}")
        narrative.append(").", style=_DIM)
    parts.append(narrative)
    exit_code = 0 if result.passed else 1
    parity = Text(f"\nexit code {exit_code}", style=_SAME if result.passed else _DRIFT)
    parity.append(" — matches headless ", style=_DIM)
    parity.append(f"comparo exec {result.profile_id}", style=_ACCENT)
    parts.append(parity)
    return Group(*parts)


def _exec_foot(result: ExecutionResult) -> Table:
    table = Table(box=None, expand=True, show_header=False, padding=0)
    table.add_column(justify="left")
    table.add_column(justify="right")
    has_drift = any(
        outcome.error is not None or (outcome.diff is not None and outcome.diff.drifted)
        for outcome in result.outcomes
    )
    # ↑↓ section and ⏎ cell only do something when there are drifted cells to
    # navigate; on a clean pass the drift table is empty, so drop them.
    hints: tuple[tuple[str, str], ...] = (
        (("↑↓", "section"), ("⏎", "cell")) if has_drift else ()
    ) + (
        ("d", "diff"),
        ("e", "report"),
        ("r", "re-run"),
        ("?", "help"),
        ("esc/⌫", "close"),
        ("q", "quit"),
    )
    keys = Text()
    for key, label in hints:
        keys.append(f"{key} ", style=f"bold {_TEXT_HI}")
        keys.append(f"{label}   ", style=_DIM)
    exit_code = 0 if result.passed else 1
    table.add_row(keys, Text(f"{result.profile_id} · exit {exit_code}", style=_DIM))
    return table


def _cell_verdict(outcome: CellOutcome, redact: Callable[[str], str] = str) -> Group:
    parts: list[RenderableType] = []
    _, base_fail, base_warn = _assert_tally(outcome.baseline_assertions)
    _, cand_fail, cand_warn = _assert_tally(outcome.candidate_assertions)
    line = Text()
    if base_fail == 0 and cand_fail == 0:
        line.append("assertions pass", style=_SAME)
        warns = base_warn + cand_warn
        line.append(
            f"   both sides · {warns} warn (non-blocking)" if warns else "   both sides",
            style=_DIM,
        )
    else:
        line.append("assertions fail", style=_DRIFT)
        line.append(f"   baseline {base_fail} ✗ · candidate {cand_fail} ✗", style=_DIM)
    parts.append(line)
    second = Text("\n")
    if outcome.error is not None:
        second.append("error", style=_WARN)
        second.append(f"   {redact(outcome.error)}", style=_DIM)
    elif outcome.diff is not None and outcome.diff.drifted:
        field = outcome.diff.drifts[0]
        second.append("diff drift", style=_DRIFT)
        second.append(f"   {redact(field.path)} · {field.mode}", style=_DIM)
    else:
        second.append("no drift", style=_SAME)
    parts.append(second)
    if outcome.error is None and outcome.diff is not None and outcome.diff.drifted:
        third = Text("\nuntriaged — press ", style=_DIM)
        third.append("i", style=f"bold {_ACCENT}")
        third.append(" to ignore", style=_DIM)
        parts.append(third)
    return Group(*parts)


def _relative_age(created: str) -> str:
    """A compact age like ``12m`` / ``2h`` / ``1d`` from an ISO timestamp."""
    try:
        when = datetime.fromisoformat(created)
    except (ValueError, TypeError):
        return ""
    # A record written by CI (or hand-edited) may carry a tz-aware timestamp;
    # compare in the same awareness to avoid a naive-vs-aware TypeError.
    now = datetime.now(when.tzinfo) if when.tzinfo is not None else datetime.now()
    seconds = int((now - when).total_seconds())
    if seconds < 60:
        return "now" if seconds < 5 else f"{max(seconds, 0)}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"


def _envs_label(record: ReportRecord) -> str:
    if record.candidate is None:
        return record.baseline
    return f"{record.baseline}⇄{record.candidate}"


def _rel_dir(project: LoadedProject, path: Path) -> str:
    try:
        return str(path.relative_to(project.root))
    except ValueError:
        return str(path)


def _gate_banner(record: ReportRecord) -> Table:
    table = Table(box=None, expand=True, show_header=False, padding=0)
    table.add_column(justify="left")
    table.add_column(justify="right")
    glyph = "✓" if record.gate == "PASS" else ("!" if record.gate == "ERROR" else "✗")
    colour = _GATE_COLOR.get(record.gate, _DIM)
    left = Text(f"{glyph} gate: {record.gate}", style=f"bold {colour}")
    # Mockup: "<execution> · run-<id> · saved <age> ago" — profile name first, then
    # the run id, then the age; the envs live in the list's ENVS column, not here.
    meta = Text()
    if record.execution:
        meta.append(record.execution, style=_TEXT_HI)
        meta.append(" · ", style=_DIM)
    meta.append(_run_label(record.id), style=_TEXT_HI)
    age = _relative_age(record.created)
    meta.append(f" · saved {age} ago" if age else f" · {record.created}", style=_DIM)
    table.add_row(left, meta)
    return table


def _assert_counts(summary: AssertionSummary) -> Text:
    text = Text()
    text.append(f"{summary.passed} ✓", style=_SAME)
    text.append(" · ", style=_DIM)
    text.append(f"{summary.failed} ✗", style=_DRIFT if summary.failed else _DIM)
    text.append(" · ", style=_DIM)
    text.append(f"{summary.warned} !", style=_WARN if summary.warned else _DIM)
    return text


def _assert_lines(summary: AssertionSummary) -> RenderableType:
    if not summary.lines:
        return Text("no assertions — ad-hoc diff run", style=_DIM)
    text = Text()
    for index, line in enumerate(summary.lines):
        glyph, colour = _ASSERT_GLYPH.get(line.state, ("·", _DIM))
        if index:
            text.append("\n")
        text.append(f"{glyph} ", style=colour)
        text.append(line.label, style=colour)
        text.append(f"   {line.detail}", style=_DIM)
    return text


def _breakdown_legend(record: ReportRecord) -> Text:
    drifted = [row for row in record.requests if row.drift]
    text = Text()
    if drifted:
        for row in drifted:
            text.append(f"{row.request}", style=_DRIFT)
            if row.drift_paths:
                text.append(f" drifted on {', '.join(row.drift_paths)}\n", style=_DIM)
            else:
                # A legacy/foreign archive can carry a count without the paths.
                text.append(
                    f" drifted on {row.drift} field(s) · paths not recorded, re-run to name them\n",
                    style=_DIM,
                )
        text.append("⏎", style=f"bold {_TEXT_HI}")
        text.append(" deep-dives here — every drifted field, in place. ", style=_DIM)
    else:
        text.append("no drift under the compared paths. ", style=_DIM)
    text.append("skip stays visible: ", style=_DIM)
    text.append("green never means full coverage.", style=_SKIP)
    return text


def _breakdown_table(record: ReportRecord) -> Table:
    table = _table()
    table.add_column("REQUEST", style=_TEXT_HI, no_wrap=True)
    table.add_column("SAME", justify="right", width=6)
    table.add_column("DRIFT", justify="right", width=6)
    table.add_column("SKIP", justify="right", width=6)
    table.add_column("VERDICT", justify="right", width=9)
    for row in record.requests:
        if row.verdict == "error":
            verdict = Text("! error", style=_WARN)
        elif row.verdict == "drift":
            verdict = Text("✗ drift", style=_DRIFT)
        elif row.verdict == "fail":
            verdict = Text("✗ fail", style=_DRIFT)
        else:
            verdict = Text("✓ pass", style=_SAME)
        table.add_row(
            Text(row.request, style=_TEXT_HI),
            Text(str(row.same), style=_SAME if row.same else _DIM),
            Text(str(row.drift), style=_DRIFT if row.drift else _DIM),
            Text(str(row.skip), style=_SKIP if row.skip else _DIM),
            verdict,
        )
    return table


def _record_kind(record: ReportRecord) -> str:
    """Classify a saved record: ``execution`` (named), ``run`` (no candidate), else ``diff``."""
    if record.execution is not None:
        return "execution"
    if record.candidate is None:
        return "run"
    return "diff"


def _report_reading_pane(record: ReportRecord) -> Group:
    """The step-1 reading pane: gate line, stat pills, assertion roll-ups, breakdown."""
    parts: list[RenderableType] = [_gate_banner(record), Text()]
    pills = Text()
    for label, value, colour in (
        ("calls", record.calls, _TEXT_HI),
        ("same", record.same, _SAME),
        ("drift", record.drift, _DRIFT if record.drift else _DIM),
        ("error", record.error, _WARN if record.error else _DIM),
        ("skipped", record.skipped, _SKIP),
    ):
        pills.append(f" {value} ", style=f"bold {colour} on {_SYNTAX_BG}")
        pills.append(f" {label} ", style=_DIM)
    parts += [pills, Text()]
    for title, env, summary in (
        ("Assertions · ", record.baseline, record.baseline_assertions),
        ("Assertions · ", record.candidate or "—", record.candidate_assertions),
    ):
        line = Text(title, style=_DIM)
        line.append(f"{env}   ", style=f"bold {_TEXT_HI}")
        line.append_text(_assert_counts(summary))
        parts.append(line)
    parts.append(Text())
    parts.append(Text.from_markup(f"[bold {_DIM}]DIFF BREAKDOWN[/] [{_DIM}]· per request[/]"))
    # Pad the request name to a fixed *cell* width (not len()) so the same/drift/skip
    # bars line up in a column regardless of name length or wide Unicode glyphs.
    for row in record.requests:
        line = Text("  ")
        line.append(_pad_cells(row.request, 14), style=_TEXT_HI)
        line.append("same ", style=_DIM)
        line.append(
            "██" if row.same and not row.drift else "░░",
            style=_SAME if row.same and not row.drift else _DIM,
        )
        line.append(" drift ", style=_DIM)
        line.append("██" if row.drift else "░░", style=_DRIFT if row.drift else _DIM)
        line.append(" skip ", style=_DIM)
        line.append("▓▓" if row.skip else "░░", style=_SKIP if row.skip else _DIM)
        cells = row.same + row.drift + row.skip
        line.append(f"   {cells} cell{'' if cells == 1 else 's'}", style=_DIM)
        parts.append(line)
    parts.append(Text())
    hint = Text("press ", style=_DIM)
    hint.append("⏎", style=f"bold {_TEXT_HI}")
    hint.append(" opens the full analysis\n", style=_ACCENT)
    hint.append("the Diff/Run panels, read-only, in this tab", style=_ACCENT)
    parts.append(hint)
    return Group(*parts)


def _replay_banner(record: ReportRecord, kind: str) -> Text:
    """The purple 'analyzing a saved diff/run' banner atop an in-tab replay."""
    noun = "diff" if kind == "diff" else "run"
    tail = (
        "read-only replay — no requests re-sent"
        if kind == "diff"
        else "read-only replay of the Run screen"
    )
    banner = Text()
    banner.append(f" ▸ analyzing a saved {noun} ", style=f"bold {_INK} on {_AXIS}")
    meta = f"  {record.id}"
    if record.execution:
        meta += f" · {record.execution}"
    age = _relative_age(record.created)
    meta += f" · saved {age + ' ago' if age else record.created}  "
    banner.append(meta, style=_DIM)
    banner.append(tail, style=_AXIS)
    return banner


def _cell_label(cell: "CellRecord") -> str:
    """``Price quote · free`` — the request and its matrix variant, if any."""
    return cell.request + (f" · {cell.variant}" if cell.variant else "")


def _replay_path_groups(
    record: ReportRecord, pick: "Callable[[CellRecord], list[str]]"
) -> list[tuple[str, list[str]]]:
    """Group saved paths across cells: one field → the ``request · variant`` cells it hit."""
    order: list[str] = []
    hits: dict[str, list[str]] = {}
    for cell in record.cells:
        label = _cell_label(cell)
        for path in pick(cell):
            if path not in hits:
                order.append(path)
                hits[path] = []
            if label not in hits[path]:
                hits[path].append(label)
    return [(path, hits[path]) for path in order]


def _replay_drift_groups(record: ReportRecord) -> list[tuple[str, list[str]]]:
    """Group the saved drift paths across cells: one field → the cells it hit."""
    if record.cells:
        return _replay_path_groups(record, lambda cell: cell.drift_paths)
    # Older records without cell detail fall back to the per-request breakdown.
    order: list[str] = []
    hits: dict[str, list[str]] = {}
    for row in record.requests:
        for path in row.drift_paths:
            if path not in hits:
                order.append(path)
                hits[path] = []
            if row.request not in hits[path]:
                hits[path].append(row.request)
    return [(path, hits[path]) for path in order]


def _replay_skip_groups(record: ReportRecord) -> list[tuple[str, list[str]]]:
    """Group the saved skipped paths across cells: one field → the cells it hit."""
    return _replay_path_groups(record, lambda cell: cell.skip_paths)


def _replay_diff_cell(record: ReportRecord) -> "CellRecord | None":
    """The first saved cell with a drift and both bodies — the well to replay."""
    for cell in record.cells:
        if cell.drift_paths and cell.baseline_body is not None and cell.candidate_body is not None:
            return cell
    return None


def _replay_compare_well(
    record: ReportRecord, unified: bool, redact: Callable[[str], str] = str
) -> Group:
    """The read-only COMPARE well for a saved diff, replayed from the archive.

    When the record stores per-cell bodies, the well reconstructs the REAL unified
    body diff (context lines plus git delete/add bands over the drifted fields), the
    same shape as the live Diff tab — sourced from disk, nothing re-sent.
    """
    cell = _replay_diff_cell(record)
    if cell is None:
        return _replay_compare_path_well(record, redact)
    baseline = record.baseline
    candidate = record.candidate or "b"
    slug = _diff_slug(cell.request)
    drift_path = cell.drift_paths[0]
    parent = drift_path.rsplit(".", 1)[0] if "." in drift_path.lstrip("$.") else drift_path
    title = Table(box=None, expand=True, show_header=False, padding=0)
    title.add_column(justify="left")
    cmd = Text("diff ", style=_DIM)
    for marker, value in (("a/", baseline.lower()), ("b/", candidate.lower())):
        cmd.append(marker, style=f"bold {_ACCENT}")
        cmd.append(value, style=_DIM)
        cmd.append("/", style=f"bold {_ACCENT}")
        cmd.append(f"{slug}.json ", style=_DIM)
    title.add_row(cmd)
    # Rebuild the profile decision (drift / skip) per path from the saved paths.
    states: dict[str, FieldDiff] = {p: FieldDiff(p, State.DRIFT, "exact") for p in cell.drift_paths}
    for path in cell.skip_paths:
        states.setdefault(path, FieldDiff(path, State.SKIP, "ignore"))
    lines = _body_diff_lines(cell.baseline_body, cell.candidate_body, states, redact=redact)
    body = (
        _diff_unified(lines) if unified else _diff_side_by_side(lines, None, (baseline, candidate))
    )
    case = f"  {cell.variant}" if cell.variant else ""
    call = f"   {cell.method} {cell.path}" if cell.method else ""
    hunk = f"@@ {cell.request} · {redact(parent)} @@{case}{call}"
    well = Panel(
        Group(_hunk_band(hunk), body),
        box=ROUNDED,
        expand=True,
        padding=0,
        border_style=_WELL_BORDER,
    )
    legend = Text("\n")
    legend.append("− ", style=f"bold {_DRIFT}")
    legend.append("baseline ", style=_DIM)
    legend.append(baseline, style=_DIM)
    legend.append("    + ", style=f"bold {_SAME}")
    legend.append("candidate ", style=_DIM)
    legend.append(record.candidate or "—", style=_DIM)
    note = Text(f"\nreplayed from reports/{record.id}.json", style=_AXIS)
    return Group(title, Text(), well, legend, note)


def _replay_compare_path_well(record: ReportRecord, redact: Callable[[str], str] = str) -> Group:
    """Fallback COMPARE well for older records with drift paths but no saved bodies."""
    groups = _replay_drift_groups(record)
    if not groups:
        return Group(Text("no drift under the compared paths — nothing to replay", style=_DIM))
    request = (
        groups[0][1][0]
        if groups[0][1]
        else (record.requests[0].request if record.requests else "response")
    )
    parent = groups[0][0].rsplit(".", 1)[0] if "." in groups[0][0].lstrip("$.") else groups[0][0]
    slug = _diff_slug(request)
    title = Table(box=None, expand=True, show_header=False, padding=0)
    title.add_column(justify="left")
    cmd = Text("diff ", style=_DIM)
    for marker, value in (
        ("a/", record.baseline.lower()),
        ("b/", (record.candidate or "b").lower()),
    ):
        cmd.append(marker, style=f"bold {_ACCENT}")
        cmd.append(value, style=_DIM)
        cmd.append("/", style=f"bold {_ACCENT}")
        cmd.append(f"{slug}.json ", style=_DIM)
    title.add_row(cmd)
    hunk = f"@@ {request} · {redact(parent)} @@  saved replay"
    rows: list[RenderableType] = [_hunk_band(hunk)]
    for path, requests in groups:
        drift_line = Text(no_wrap=True)
        drift_line.append("✗ ", style=f"bold {_DRIFT}")
        drift_line.append(f"{redact(path)}", style=_DRIFT)
        drift_line.append(f"   drifted · {', '.join(requests)}", style=_DRIFT)
        rows.append(_band(drift_line, _DEL_BG))
        add_line = Text(no_wrap=True)
        add_line.append("+ ", style=f"bold {_SAME}")
        add_line.append(f"{redact(path)}", style=_SAME)
        add_line.append("   candidate value differs (see live diff for the body)", style=_SAME)
        rows.append(_band(add_line, _ADD_BG))
    well = Panel(Group(*rows), box=ROUNDED, expand=True, padding=0, border_style=_WELL_BORDER)
    legend = Text("\n")
    legend.append("− ", style=f"bold {_DRIFT}")
    legend.append("baseline ", style=_DIM)
    legend.append(record.baseline, style=_TEXT)
    legend.append("    + ", style=f"bold {_SAME}")
    legend.append("candidate ", style=_DIM)
    legend.append(record.candidate or "—", style=_TEXT)
    note = Text(f"\nreplayed from reports/{record.id}.json", style=_AXIS)
    return Group(title, Text(), well, legend, note)


def _replay_run_progress(record: ReportRecord) -> Text:
    """The saved-run replay progress line — an archived, greyed-out Run bar."""
    text = Text("  run ", style=_DIM)
    text.append(record.id, style=f"bold {_ACCENT}")
    text.append(f"   {record.baseline}", style=_TEXT_HI)
    text.append("   ", style=_DIM)
    width = 24
    text.append("━" * width, style=_SAME)
    passed = record.baseline_assertions.passed
    failed = record.baseline_assertions.failed
    total = record.calls or (passed + failed)
    text.append(f"   {total}/{total}", style=f"bold {_TEXT_HI}")
    text.append("  ·  ", style=_DIM)
    text.append(f"{passed} ✓", style=_SAME)
    text.append("  ", style=_DIM)
    text.append(f"{failed} ✗", style=_DRIFT if failed else _DIM)
    text.append("      archived · not re-sent", style=_DIM)
    return text


def _request_latencies(record: ReportRecord) -> dict[str, list[int]]:
    """Per-request cell latencies from the saved cells, for the P50 column."""
    out: dict[str, list[int]] = {}
    for cell in record.cells:
        if cell.latency_ms is not None:
            out.setdefault(cell.request, []).append(cell.latency_ms)
    return out


def _p50(values: list[int]) -> int | None:
    """The median (lower) of a small latency sample, or ``None`` when empty."""
    if not values:
        return None
    return sorted(values)[(len(values) - 1) // 2]


def _cell_for_request(record: ReportRecord, request: str) -> "CellRecord | None":
    """The first saved cell belonging to *request*, for its detail tree."""
    return next((cell for cell in record.cells if cell.request == request), None)


def _fmt_bytes(size: int | None) -> str:
    """A compact byte count — ``1.2 kB`` / ``840 B`` / ``—``."""
    if size is None:
        return "—"
    return f"{size} B" if size < 1000 else f"{size / 1000:.1f} kB"


def _body_summary(body: object) -> str:
    """A one-line shape of a parsed body — ``{ args, headers, url }`` / ``[ 3 items ]``."""
    if isinstance(body, dict):
        keys = ", ".join(str(key) for key in list(body)[:6])
        return f"{{ {keys} }}" if keys else "{ }"
    if isinstance(body, list):
        return f"[ {len(body)} item{'' if len(body) == 1 else 's'} ]"
    return _sv(body)


def _replay_detail_tree(
    tree: Tree[object], record: ReportRecord, row: "RequestBreakdown | None"
) -> None:
    """Rebuild the Run detail tree for a saved request from the archived cell.

    When the record carries the cell's response (method/path/status/latency/bytes,
    headers and body), the tree rebuilds the real ▾ request / ▾ response subtrees
    and metrics — the same shape as the live Run tab, replayed from disk.
    """
    tree.clear()
    root = tree.root
    if row is None:
        root.add_leaf(Text("select a request", style=_DIM))
        return
    cell = _cell_for_request(record, row.request)
    head = Text()
    head.append(row.request, style=f"bold {_TEXT_HI}")
    head.append("  ·  base  ·  ", style=_DIM)
    if cell is not None and cell.method:
        head.append(f"{cell.method} {cell.path}", style=_SAME)
        if cell.status is not None:
            ok = 200 <= cell.status < 400
            head.append(f"  {cell.status}", style=_SAME if ok else _DRIFT)
    else:
        verdict = "✓ pass" if row.verdict == "pass" else "✗ " + row.verdict
        head.append(verdict, style=_SAME if row.verdict == "pass" else _DRIFT)
    root.add_leaf(head)
    metrics = root.add(Text("metrics", style=f"bold {_AXIS}"), expand=True)
    if cell is not None and cell.status is not None:
        latency = f"{cell.latency_ms}ms" if cell.latency_ms is not None else "—"
        metrics.add_leaf(
            Text.assemble(
                ("status ", _DIM),
                (str(cell.status), _SAME),
                ("   latency ", _DIM),
                (latency, _TEXT),
                ("   bytes ", _DIM),
                (_fmt_bytes(cell.size_bytes), _TEXT),
            )
        )
    else:
        metrics.add_leaf(
            Text.assemble(
                ("same ", _DIM),
                (str(row.same), _SAME),
                ("   drift ", _DIM),
                (str(row.drift), _DRIFT if row.drift else _DIM),
                ("   skip ", _DIM),
                (str(row.skip), _SKIP),
            )
        )
    if cell is not None and cell.method:
        request_node = root.add(Text("request", style=f"bold {_AXIS}"), expand=True)
        request_node.add_leaf(Text.assemble((f"{cell.method} ", _SAME), (cell.path, _TEXT)))
    if cell is not None and (cell.response_headers or cell.baseline_body is not None):
        response_node = root.add(Text("response", style=f"bold {_AXIS}"), expand=True)
        if cell.response_headers:
            headers_node = response_node.add(
                Text.assemble(("headers ", _DIM), (f"({len(cell.response_headers)})", _DIM)),
                expand=True,
            )
            for name, value in list(cell.response_headers.items())[:4]:
                headers_node.add_leaf(Text.assemble((f"{name}: ", _DIM), (value, _TEXT)))
        if cell.baseline_body is not None:
            response_node.add_leaf(
                Text.assemble(("body ", _DIM), (_body_summary(cell.baseline_body), _TEXT))
            )
    checks = root.add(Text("checks", style=f"bold {_AXIS}"), expand=True)
    lines = record.baseline_assertions.lines
    if lines:
        for line in lines:
            glyph, colour = _ASSERT_GLYPH.get(line.state, ("·", _DIM))
            checks.add_leaf(Text.assemble((f"{glyph} {line.label}  ", colour), (line.detail, _DIM)))
    else:
        checks.add_leaf(Text("no assertions recorded (ad-hoc diff)", style=_DIM))
    if row.drift_paths:
        drift = root.add(Text("drift", style=f"bold {_DRIFT}"), expand=True)
        for path in row.drift_paths:
            drift.add_leaf(Text(f"✗ {path}", style=_DRIFT))


def _record_detail(record: ReportRecord) -> Group:
    """The full in-place deep-dive for a saved run.

    Deeper than the reading pane: it names every drifted field per request (from
    the archive's ``drift_paths``) so a user can tell exactly what to investigate
    without leaving the Report screen.
    """
    parts: list[RenderableType] = [_gate_banner(record), Text()]
    stats = Text()
    for label, value, colour in (
        ("calls", record.calls, _TEXT_HI),
        ("same", record.same, _SAME),
        ("drift", record.drift, _DRIFT if record.drift else _DIM),
        ("error", record.error, _WARN if record.error else _DIM),
        ("skipped", record.skipped, _SKIP),
    ):
        stats.append(f"{value} ", style=f"bold {colour}")
        stats.append(f"{label}    ", style=_DIM)
    parts += [stats, Text()]
    for title, env, summary in (
        ("Assertions · baseline", record.baseline, record.baseline_assertions),
        ("Assertions · candidate", record.candidate or "—", record.candidate_assertions),
    ):
        header = Text(f"{title} ", style=f"bold {_TEXT_HI}")
        header.append(env, style=_DIM)
        header.append("   ", style=_DIM)
        header.append_text(_assert_counts(summary))
        parts += [header, _assert_lines(summary), Text()]
    parts.append(Text("Per-request drift", style=f"bold {_TEXT_HI}"))
    parts.append(_breakdown_table(record))
    # Show every drifting request — naming its fields, or an explicit notice when a
    # legacy/foreign archive recorded a count without the paths (never a bare number).
    drifted = [row for row in record.requests if row.drift]
    if drifted:
        parts.append(Text())
        for row in drifted:
            head = Text("▸ ", style=_DRIFT)
            head.append(row.request, style=f"bold {_TEXT_HI}")
            count = len(row.drift_paths) if row.drift_paths else row.drift
            head.append(f"  {count} drifted field(s)", style=_DIM)
            parts.append(head)
            if row.drift_paths:
                for path in row.drift_paths:
                    line = Text("    ↳ ", style=_DIM)
                    line.append(path, style=_DRIFT)
                    parts.append(line)
            else:
                parts.append(
                    Text("    ↳ field paths not recorded — re-run to name them", style=_DIM)
                )
    return Group(*parts)


def _record_markdown(record: ReportRecord) -> str:
    lines = [
        f"# comparo report {record.id}",
        "",
        f"- **gate**: {record.gate}",
        f"- **environments**: {_envs_label(record)}",
        f"- **when**: {record.created}",
    ]
    if record.execution:
        lines.append(f"- **execution**: {record.execution}")
    lines += [
        "",
        "| calls | same | drift | error | skipped |",
        "|------:|-----:|------:|------:|--------:|",
        f"| {record.calls} | {record.same} | {record.drift} | {record.error} | {record.skipped} |",
        "",
        "## Diff breakdown",
        "",
        "| request | same | drift | skip | verdict |",
        "|---------|-----:|------:|-----:|---------|",
    ]
    lines += [
        f"| {row.request} | {row.same} | {row.drift} | {row.skip} | {row.verdict} |"
        for row in record.requests
    ]
    return "\n".join(lines) + "\n"


def _settings_body(
    project: LoadedProject,
    config: UserConfig,
    key: str,
    selfcheck: list[tuple[str, str, bool]] | None,
    checking: bool,
) -> RenderableType:
    """Render one settings section - the master/detail right pane."""
    if key == "about":
        return _settings_about()
    if key == "project":
        return _settings_project(project)
    if key == "security":
        return _settings_security(selfcheck, checking)
    if key == "appearance":
        return _settings_appearance(config)
    if key == "keybindings":
        return _settings_keybindings()
    if key == "updates":
        return _settings_updates(config)
    if key == "plugins":
        return _settings_plugins()
    if key == "engine":
        return _settings_engine()
    return _settings_behavior(config)


def _settings_about() -> Text:
    text = Text()
    text.append("comparo ", style=f"bold {_ACCENT}")
    text.append(f"{__version__}", style=f"bold {_TEXT_HI}")
    text.append("   alpha\n", style=_AXIS)
    text.append(
        "HTTP regression & diff testing across environments — TUI, CLI, and CI\n\n", style=_DIM
    )
    for label, value, style in (
        ("author", "Walid Benbihi", _TEXT_HI),
        ("license", "MIT", _TEXT),
        ("repo", _REPO_URL, _ACCENT),
        ("docs", _DOCS_URL, _ACCENT),
    ):
        text.append(f"{label:<9}", style=_LABEL)
        text.append(f"{value}\n", style=style)
    text.append("\nFree & open source. Built in the open.", style=_DIM)
    return text


def _settings_project(project: LoadedProject) -> Text:
    def count(kind: type | tuple[type, ...]) -> int:
        return sum(1 for obj in project.objects.values() if isinstance(obj, kind))

    # An env or project NAME can equal a declared secret value (the untainted
    # vector) — this is a display sink, so mask through the project's redactor.
    redact = Redactor.for_project(project).text
    manifest = project.project
    spec = manifest.spec if manifest else None
    default = _default_environment(project)
    text = Text()
    stats = (
        (count(Environment), "environments"),
        (count(Request), "requests"),
        (count(Schema), "schemas"),
        (count(Matrix), "matrices"),
        (count((DiffProfile, AssertionProfile, ExecutionProfile)), "profiles"),
    )
    for number, noun in stats:
        text.append(f"{number} ", style=f"bold {_TEXT_HI}")
        text.append(f"{noun}   ", style=_DIM)
    text.append("\n\n")
    report_dir = spec.report.output if spec is not None and spec.report is not None else None
    concurrency = spec.run.concurrency if spec is not None and spec.run is not None else None
    project_line = manifest.metadata.name if manifest else "—"
    if manifest and manifest.metadata.description:
        project_line = f"{manifest.metadata.name} · {manifest.metadata.description}"
    rows = [
        ("manifest", redact(f"{project.root.name}/comparo.yaml"), _TEXT_HI),
        ("project", redact(project_line), _TEXT_HI),
        ("default env", redact(default.metadata.name) if default else "—", _ACCENT),
        ("concurrency", str(concurrency or "—"), _TEXT),
        ("reporting dir", redact(str(report_dir or ".reports/")), _TEXT),
    ]
    for label, value, style in rows:
        text.append(f"{label:<14}", style=_LABEL)
        text.append(f"{value}\n", style=style)
    text.append(
        "\nEdit the YAML in your editor; the TUI reads. A summary, not an editor.", style=_DIM
    )
    return text


def _settings_security(selfcheck: list[tuple[str, str, bool]] | None, checking: bool) -> Text:
    text = Text()
    text.append("Never-leak guarantee.  ", style=f"bold {_SAME}")
    text.append(
        "A resolved secret is masked in every sink that\nleaves the process — the safe path is "
        "the only path.\n\n",
        style=_DIM,
    )
    if checking:
        text.append("running self-check…\n", style=_WARN)
    elif selfcheck is None:
        text.append("press ", style=_DIM)
        text.append("t", style=f"bold {_ACCENT}")
        text.append(" to run a canary secret through every sink\n", style=_DIM)
        from comparo.adapters.doctor import SINK_LABELS

        rows: tuple[tuple[str, str, bool], ...] = tuple((n, d, True) for n, d in SINK_LABELS)
        _selfcheck_rows(text, rows, muted=True)
    else:
        passed = sum(1 for _, _, ok in selfcheck if ok)
        total = len(selfcheck)
        good = passed == total
        text.append(
            f"{'✓' if good else '✗'} {passed}/{total} sinks masked the canary",
            style=f"bold {_SAME if good else _DRIFT}",
        )
        text.append("   canary ", style=_DIM)
        text.append("••••••", style=_SKIP)
        text.append(" (s3cr…-CANARY → masked everywhere)\n\n", style=_DIM)
        _selfcheck_rows(text, tuple(selfcheck), muted=False)
    text.append("\npress ", style=_DIM)
    text.append("t", style=f"bold {_ACCENT}")
    text.append(" to re-run   ·   also headless: ", style=_DIM)
    text.append("comparo doctor", style=f"bold {_ACCENT}")
    return text


def _selfcheck_rows(text: Text, rows: tuple[tuple[str, str, bool], ...], *, muted: bool) -> None:
    for name, detail, ok in rows:
        glyph, tint = ("✓", _SAME) if ok else ("✗", _DRIFT)
        text.append(f"{glyph} ", style=_DIM if muted else tint)
        text.append(f"{name:<18}", style=_DIM if muted else (_TEXT_HI if ok else _DRIFT))
        text.append(f"— {detail}\n", style=_DIM)


def _settings_appearance(config: UserConfig) -> Text:
    text = Text()
    text.append("THEME\n", style=_LABEL)
    text.append("● ", style=_SAME)
    text.append("comparo-ink   ", style=_TEXT_HI)
    text.append("deep-ink dark   accent #6d9eff\n", style=_DIM)
    text.append("○ more themes — swappable, post-alpha (row reserved)\n\n", style=_DIM)
    text.append("comparo-ink is 13 meaning-named tokens (theme.py):\n", style=_DIM)
    for token, style in (
        ("same", _SAME),
        ("drift", _DRIFT),
        ("skip", _SKIP),
        ("accent", _ACCENT),
        ("axis", _AXIS),
        ("warn", _WARN),
    ):
        text.append(token, style=style)
        text.append(" · ", style=_DIM)
    text.append("danger · border · cursor · footer…\n\n", style=_DIM)
    text.append("DEFAULT BODY-DIFF LAYOUT\n", style=_LABEL)
    text.append(_seg_toggle(("unified", "side-by-side"), config.diff_view))
    text.append("   how bodies render in Diff by default\n", style=_DIM)
    text.append("\npress ", style=_DIM)
    text.append("enter", style=f"bold {_ACCENT}")
    text.append(" to switch the default layout", style=_DIM)
    return text


def _settings_keybindings() -> RenderableType:
    globals_table = Table(box=None, show_header=False, padding=(0, 2, 0, 0))
    globals_table.add_column(style=_ACCENT, no_wrap=True)
    globals_table.add_column()
    for combo, action in (
        ("1–6", "switch tab — Explorer · Run · Diff · Execution · Report · Settings"),
        ("tab", "next panel"),
        ("q", "quit — everywhere"),
        ("esc / ⌫", "back"),
        ("/", "filter"),
        ("?", "help"),
    ):
        globals_table.add_row(combo, Text(action, style=_TEXT))
    per_tab = Table(box=None, show_header=False, padding=(0, 2, 0, 0))
    per_tab.add_column(style=f"bold {_ACCENT}", no_wrap=True)
    per_tab.add_column()
    for tab, verbs in (
        ("Explorer", "↑↓ select · enter default · h health · r raw · p curl · g graph"),
        ("Run", "↑↓ / h·l move · f fails · t views · z max · s save"),
        ("Diff", "↑↓ move · v unified · o outbound · i ignore · s save"),
        ("Execution", "↑↓ cells · enter open · v unified · s save"),
        ("Report", "↑↓ list · enter analyze · r reload · o export · d delete"),
        ("Settings", "↑↓ sections · enter/t activate"),
    ):
        per_tab.add_row(tab, Text(verbs, style=_DIM))
    group = Group(
        Text("GLOBAL", style=_LABEL),
        globals_table,
        Text("\nPER TAB", style=_LABEL),
        per_tab,
        Text("\nq always quits — it is never back/close. esc and ⌫ are back.", style=_DIM),
    )
    return group


def _settings_updates(config: UserConfig) -> Text:
    box = "[x]" if config.update_check else "[ ]"
    text = Text()
    text.append(f"{box} ", style=f"bold {_ACCENT}")
    text.append("check for updates on startup", style=_TEXT_HI)
    text.append("  — off (opt-in)" if not config.update_check else "  — on", style=_DIM)
    text.append("\n\nWhen on, comparo fetches PyPI's public version JSON\n", style=_DIM)
    text.append("(pypi.org/pypi/comparo/json) once at launch — a version\n", style=_DIM)
    text.append("string, nothing more.\n", style=_DIM)
    text.append("✓ no telemetry", style=_SAME)
    text.append(" · no account · nothing else leaves your machine\n\n", style=_DIM)
    text.append("status  ", style=_LABEL)
    seen = config.update_latest_seen
    if seen and updates_adapter.is_newer(seen, __version__):
        text.append(f"update available → {seen}", style=f"bold {_WARN}")
        text.append(f"  · you have {__version__}", style=_DIM)
    elif config.update_last_checked:
        text.append("✓ up to date", style=_SAME)
        text.append(f"  · {__version__} · last checked {config.update_last_checked}", style=_DIM)
    else:
        text.append("not checked yet", style=_DIM)
    text.append("\n\nWhen a newer version exists a one-time toast appears at launch.", style=_DIM)
    text.append("\npress ", style=_DIM)
    text.append("enter", style=f"bold {_ACCENT}")
    text.append(" to toggle the check", style=_DIM)
    return text


def _settings_plugins() -> Text:
    text = Text()
    text.append("○ ", style=_SKIP)
    text.append("no plugins installed\n\n", style=_DIM)
    text.append(
        "Plugins are a post-alpha extension point — reporters, auth\n"
        "providers, generators, comparators that plug into comparo.core\n"
        "without touching it. comparo is domain-agnostic by default.",
        style=_DIM,
    )
    return text


def _settings_engine() -> Text:
    text = Text()
    text.append("comparo.core", style=f"bold {_ACCENT}")
    text.append(" is the whole engine. The TUI, CLI, and GitHub\n", style=_TEXT)
    text.append(
        "Action are thin front-ends over it — and never leak back into it.\n\n", style=_TEXT
    )
    text.append("CONTRACTS", style=_LABEL)
    text.append("   enforced by import-linter in CI\n", style=_DIM)
    for contract in (
        "Interfaces and adapters may depend on core; core depends on neither",
        "Core must not import an HTTP library directly",
    ):
        text.append("✓ ", style=_SAME)
        text.append(f"{contract}\n", style=_TEXT)
    text.append("\nconfig API   ", style=_LABEL)
    text.append("comparo/v1\n", style=_ACCENT)
    text.append("docs         ", style=_LABEL)
    text.append(f"{_DOCS_URL}", style=_ACCENT)
    return text


def _settings_behavior(config: UserConfig) -> Text:
    text = Text()
    box = "[x]" if config.confirm_quit else "[ ]"
    text.append(f"{box} ", style=f"bold {_ACCENT}")
    text.append("confirm on quit", style=_TEXT_HI)
    text.append("  — ask before q closes the app\n\n", style=_DIM)
    text.append("default tab on launch   ", style=_LABEL)
    text.append(config.default_tab, style=_TEXT_HI)
    text.append("  · set in config.toml\n", style=_DIM)
    text.append("default diff layout     ", style=_LABEL)
    text.append(config.diff_view, style=_TEXT_HI)
    text.append("  · set in Appearance\n", style=_DIM)
    text.append("\npress ", style=_DIM)
    text.append("enter", style=f"bold {_ACCENT}")
    text.append(" to toggle confirm-on-quit", style=_DIM)
    return text
