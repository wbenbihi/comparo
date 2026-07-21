"""Rendering, formatting, and lookup helpers for the comparo TUI.

Pure helper functions (and two small helper classes) that build Rich
renderables, format detail panes, and derive footer/help content for the
view/modal classes in comparo.tui.app. Nothing here references a view/modal
class or ComparoApp — this module sits between comparo.tui.tokens and
comparo.tui.app in the dependency order (constants <- functions <- views).
"""

import dataclasses
import hashlib
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
from textual.dom import DOMNode
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from comparo import __version__
from comparo.adapters import updates as updates_adapter
from comparo.adapters.userconfig import UserConfig
from comparo.core.assertions import AssertionResult
from comparo.core.compare import CellDiff
from comparo.core.compare import written_identity
from comparo.core.diagnostics import Diagnostic
from comparo.core.diagnostics import LoadError
from comparo.core.diff import FieldDiff
from comparo.core.diff import RuleRef
from comparo.core.diff import State
from comparo.core.execute import Execution
from comparo.core.execution import CellOutcome
from comparo.core.execution import ExecutionProgress
from comparo.core.execution import ExecutionResult
from comparo.core.execution import select_requests
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
from comparo.core.outbound import outbound_diffs
from comparo.core.provenance import Origin
from comparo.core.provenance import Trail
from comparo.core.redaction import binary_is_clean
from comparo.core.redaction import decoded_text
from comparo.core.redaction import mask_credential_header
from comparo.core.refs import ref_id as _ref_id
from comparo.core.report_record import FieldDiffRecord
from comparo.core.report_record import ResponseRecord
from comparo.core.resolve import EnvironmentSelectionError
from comparo.core.resolve import ResolvedRequest
from comparo.core.resolve import Resolver
from comparo.core.resolve import select_environment
from comparo.core.streams import parse_sse
from comparo.tui.components import CheckRow
from comparo.tui.components import ErrorPanelModel
from comparo.tui.components import StatChip
from comparo.tui.components import cell_glyph
from comparo.tui.components import error_panel
from comparo.tui.components import provenance_suffix
from comparo.tui.components import seg_pill
from comparo.tui.components import spec_table
from comparo.tui.components import stat_chips
from comparo.tui.components import verdict_box
from comparo.tui.replay import AssertionSummary
from comparo.tui.replay import ReplayCell as CellRecord
from comparo.tui.replay import ReplayRecord as ReportRecord
from comparo.tui.replay import RequestBreakdown
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
    "_exec_header",
    "_exec_mode",
    "_exec_plan_line",
    "_exec_profile_card",
    "_exec_profiles_hint",
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
    "_outbound_header",
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
    "_running_cell_name",
    "_running_row_from_progress",
    "_running_table",
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
    results: list[AssertionResult],
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

    if (results or execution is not None) and want_meta and state not in ("pending", "running"):
        node = root.add(Text("CHECKS", style=f"bold {_LABEL}"), expand=True)
        for result in results:
            label = redact(result.label or f"{result.target} {result.op}")
            detail = redact(result.detail)
            if result.ok:
                mark, tint = "✓", _SAME
            elif result.severity == "warn":
                # An advisory break: amber ~, never a red ✗ — it cannot fail a gate.
                mark, tint = "~", _WARN
            else:
                mark, tint = "✗", _DRIFT
            row = Text.assemble((f"{mark} {label}  ", tint), (detail, _DIM))
            if result.severity == "warn":
                row.append("  · warn", style=_DIM)
            node.add_leaf(row)
        # ``reachable`` is synthesized per cell — transport, not an engine rule —
        # and always LAST (run-results spec §4); a dead cell shows it alone,
        # because a rule that never ran must never render as a broken row.
        if execution is not None:
            reached = execution.response
            if reached is not None:
                node.add_leaf(Text.assemble(("✓ reachable  ", _SAME), (str(reached.status), _DIM)))
            else:
                detail = redact(execution.error or "no response")
                node.add_leaf(Text.assemble(("✗ reachable  ", _DRIFT), (detail, _DIM)))

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
            shown = redact(mask_credential_header(str(key), str(value)))
            headers.add_leaf(Text.assemble((f"{redact(key)}: ", _DIM), (shown, _TEXT)))
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
        version = response.http_version or "HTTP"
        phrase = f" {redact(response.reason_phrase)}" if response.reason_phrase else ""
        node.add_leaf(Text(f"{version} {response.status}{phrase}", style=_TEXT_HI))
        for key, value in response.headers[:24]:
            shown = redact(mask_credential_header(str(key), str(value)))
            node.add_leaf(Text(f"{redact(str(key))}: {shown}", style=_DIM))
        raw = response.body.decode("utf-8", "replace") if response.body else ""
        body = node.add(Text("body", style=_DIM), expand=True)
        for line in redact(raw).splitlines()[:200] or [""]:
            body.add_leaf(Text(line, style=_TEXT))
    elif execution is not None and execution.error is not None:
        root.add_leaf(Text(redact(execution.error), style=_DRIFT))


@dataclasses.dataclass(frozen=True, slots=True)
class Anchor:
    """A verdict pinned into the evidence tree at a body path.

    ``state``: ``held`` / ``broke`` / ``warn_broke``. ``missing`` plants a red
    synthetic node where the field SHOULD have been — an absent required field
    renders at its site, never as a detached message.
    """

    state: str
    label: str = ""
    missing: bool = False


def anchors_from_assertions(
    results: list[AssertionResult], redact: Callable[[str], str] = str
) -> dict[str, Anchor]:
    """Pin body-targeting assertion results onto their JSON paths.

    ``body:$.quote.currency`` → ``$.quote.currency``; a failed rule whose
    observed value is absent marks the anchor missing. A broken anchor always
    outranks a held one on the same path.
    """
    anchors: dict[str, Anchor] = {}
    for result in results:
        target = result.target
        if not target.startswith("body"):
            continue
        raw = target[5:] if target.startswith("body:") else ""
        path = "$" if not raw else (raw if raw.startswith("$") else f"$.{raw}")
        if result.ok:
            state = "held"
        elif result.severity == "warn":
            state = "warn_broke"
        else:
            state = "broke"
        missing = not result.ok and result.actual is None
        label = redact(result.label or f"{result.target} {result.op}")
        current = anchors.get(path)
        if current is None or (state == "broke" and current.state != "broke"):
            anchors[path] = Anchor(state, label, missing)
    return anchors


_ANCHOR_MARKS: dict[str, tuple[str, str]] = {
    "held": ("✓ ", _SAME),
    "broke": ("✗ ", _DRIFT),
    "warn_broke": ("~ ", _WARN),
}


def _anchor_prefix(anchor: Anchor | None) -> tuple[str, str] | None:
    if anchor is None:
        return None
    return _ANCHOR_MARKS.get(anchor.state)


def _anchored_into(
    node: TreeNode[object],
    value: object,
    redact: Callable[[str], str],
    anchors: dict[str, Anchor],
    path: str = "$",
    registry: list[TreeNode[object]] | None = None,
) -> list[TreeNode[object]]:
    """The evidence tree: the JSON tree with verdicts pinned at their sites.

    Returns the broken-anchor nodes in render order — the ``n``/``p`` registry a
    view scrolls between. Missing required fields render as red synthetic nodes
    inside their parent, labelled with the rule that wanted them.
    """
    if registry is None:
        registry = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            _anchored_child(node, str(key), item, redact, anchors, child_path, registry)
        _plant_missing(node, value, anchors, path, registry)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child_path = f"{path}[{index}]"
            _anchored_child(node, f"[{index}]", item, redact, anchors, child_path, registry)
    else:
        leaf = Text()
        mark = _anchor_prefix(anchors.get(path))
        if mark is not None:
            leaf.append(mark[0], style=f"bold {mark[1]}")
        leaf.append_text(Text.assemble(_scalar(value, redact)))
        added = node.add_leaf(leaf)
        anchor = anchors.get(path)
        if anchor is not None and anchor.state == "broke":
            registry.append(added)
    return registry


def _anchored_child(
    node: TreeNode[object],
    key: str,
    value: object,
    redact: Callable[[str], str],
    anchors: dict[str, Anchor],
    path: str,
    registry: list[TreeNode[object]],
) -> None:
    key = redact(key)
    anchor = anchors.get(path)
    mark = _anchor_prefix(anchor)
    prefix = Text()
    if mark is not None:
        prefix.append(mark[0], style=f"bold {mark[1]}")
    if isinstance(value, dict | list):
        count = f"{{{len(value)}}}" if isinstance(value, dict) else f"[{len(value)}]"
        label = prefix
        label.append(key, style=_AXIS)
        label.append(f"  {count}", style=_DIM)
        if anchor is not None and anchor.state == "broke" and anchor.label:
            label.append(f"  ← {anchor.label}", style=_DRIFT)
        branch = node.add(label, expand=anchor is not None)
        if anchor is not None and anchor.state == "broke":
            registry.append(branch)
        _anchored_into(branch, value, redact, anchors, path, registry)
        return
    label = prefix
    label.append(key, style=_AXIS)
    label.append(": ", style=_DIM)
    label.append_text(Text.assemble(_scalar(value, redact)))
    if anchor is not None:
        if anchor.state == "broke" and anchor.label:
            label.append(f"  ← {anchor.label}", style=_DRIFT)
        elif anchor.state == "warn_broke" and anchor.label:
            label.append(f"  ← {anchor.label} · warn", style=_WARN)
    leaf = node.add_leaf(label)
    if anchor is not None and anchor.state == "broke":
        registry.append(leaf)


def _plant_missing(
    node: TreeNode[object],
    value: dict[str, object],
    anchors: dict[str, Anchor],
    path: str,
    registry: list[TreeNode[object]],
) -> None:
    """Red synthetic nodes for anchored fields absent from this object."""
    prefix = f"{path}."
    for anchor_path, anchor in anchors.items():
        if not anchor.missing or not anchor_path.startswith(prefix):
            continue
        name = anchor_path[len(prefix) :]
        if "." in name or "[" in name or name in value:
            continue  # not a direct child, or actually present
        label = Text("✗ ", style=f"bold {_DRIFT}")
        label.append(name, style=_DRIFT)
        label.append(" — missing", style=f"bold {_DRIFT}")
        if anchor.label:
            label.append(f"  ← {anchor.label}", style=_DIM)
        registry.append(node.add_leaf(label))


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


@dataclasses.dataclass(frozen=True, slots=True)
class BinaryView:
    """Honest bytes, never mojibake — buildable live and from a saved record."""

    content_type: str
    size_bytes: int
    sha256: str | None  # None = withheld (secret-bearing body, fail closed)
    head: bytes | None  # at most the first KiB; None = withheld
    magic: str = ""


_BINARY_HEAD = 1024

#: A few load-bearing magics — enough to say what the blob probably is.
_MAGIC_NAMES: list[tuple[bytes, str]] = [
    (b"\x89PNG", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF8", "gif"),
    (b"%PDF", "pdf"),
    (b"PK\x03\x04", "zip"),
    (b"\x1f\x8b", "gzip"),
]


def binary_from_bytes(
    body: bytes, content_type: str, redact: Callable[[str], str] = str
) -> BinaryView:
    """The live binary view — same fail-closed rule as the saved record.

    The digest and the head are withheld together when any text view of the
    whole body trips the redactor (hex is a side channel; a digest of
    secret-bearing bytes is a verification oracle).
    """
    clean = binary_is_clean(body, redact)
    magic = next((name for prefix, name in _MAGIC_NAMES if body.startswith(prefix)), "")
    return BinaryView(
        content_type=content_type,
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest() if clean else None,
        head=body[:_BINARY_HEAD] if clean else None,
        magic=magic,
    )


def binary_from_record(record: ResponseRecord) -> BinaryView:
    """The replayed binary view — straight from the stored digest and hex head."""
    head = bytes.fromhex(record.body_head) if record.body_head else None
    magic = ""
    if head is not None:
        magic = next((name for prefix, name in _MAGIC_NAMES if head.startswith(prefix)), "")
    return BinaryView(
        content_type=_content_type(record.headers),
        size_bytes=record.size_bytes,
        sha256=record.sha256,
        head=head,
        magic=magic,
    )


def _binary_into(node: TreeNode[object], view: BinaryView) -> None:
    """content-type · size · magic · sha256 (the load-bearing line) · hex rows."""
    meta = Text()
    meta.append(view.content_type or "application/octet-stream", style=_TEXT)
    meta.append(f"  ·  {view.size_bytes} bytes", style=_DIM)
    if view.magic:
        meta.append(f"  ·  {view.magic}", style=_AXIS)
    node.add_leaf(meta)
    if view.sha256:
        sha = Text("sha256  ", style=_DIM)
        sha.append(view.sha256, style=f"bold {_TEXT_HI}")
        sha.append("  — compare against earlier saved runs", style=_DIM)
        node.add_leaf(sha)
    else:
        node.add_leaf(Text("digest withheld — the body carries a declared secret", style=_WARN))
    if view.head is None:
        node.add_leaf(Text("bytes withheld — fail closed, never a hex side channel", style=_WARN))
    else:
        rows = node.add(Text("bytes", style=_DIM), expand=view.size_bytes <= 256)
        for offset in range(0, len(view.head), 16):
            chunk = view.head[offset : offset + 16]
            hexes = " ".join(f"{byte:02x}" for byte in chunk)
            ascii_view = "".join(chr(b) if 32 <= b < 127 else "·" for b in chunk)
            row = Text(f"{offset:08x}  ", style=_DIM)
            row.append(f"{hexes:<47}", style=_TEXT)
            row.append(f"  {ascii_view}", style=_AXIS)
            rows.add_leaf(row)
        elided = view.size_bytes - len(view.head)
        if elided > 0:
            rows.add_leaf(
                Text(f"⋯ {elided} bytes elided — full body kept in the saved run", style=_DIM)
            )


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
        outline = _HtmlOutline(node, highlight=None)
        outline.feed(redact(text)[:20000])
        outline.close()
        return
    if decoded_text(body) is None and body:
        # Binary: honest bytes, never mojibake — the same view the record stores.
        _binary_into(node, binary_from_bytes(body, content_type, redact))
        return
    for line in redact(text)[:4000].splitlines()[:200]:
        node.add_leaf(Text(line, style=_TEXT))


def _sse_into(node: TreeNode[object], text: str, redact: Callable[[str], str] = str) -> None:
    """The SSE facet: the FULL envelope per event — id · event · data · retry.

    An unnamed event shows the spec default *message* dimmed; an id-less event
    says so; a ``retry`` field is kept and labeled as the reconnect hint. The
    ``data`` payload parses as JSON when it is JSON, else stays redacted text.
    """
    events = parse_sse(text)
    if not events:
        node.add_leaf(Text("(no events)", style=_DIM))
        return
    for index, event in enumerate(events):
        label = Text.assemble((f"event {index + 1}", _AXIS))
        if event.get("event"):
            label.append(f"  {redact(event['event'])}", style=_ACCENT)
        else:
            label.append("  message", style=_DIM)  # the spec default for unnamed events
        entry = node.add(label, expand=len(events) <= 8)
        if event.get("id"):
            entry.add_leaf(Text.assemble(("id: ", _DIM), (redact(event["id"]), _TEXT)))
        else:
            entry.add_leaf(Text("no id", style=_DIM))
        if event.get("retry"):
            entry.add_leaf(
                Text.assemble(
                    ("retry: ", _DIM),
                    (redact(event["retry"]), _TEXT),
                    ("  · reconnect hint", _DIM),
                )
            )
        data = event.get("data", "")
        try:
            _value_into(entry.add(Text("data", style=_DIM), expand=True), json.loads(data), redact)
        except (ValueError, TypeError):
            # Redact before the 200-char clip so a straddling secret can't leak.
            entry.add_leaf(Text.assemble(("data: ", _DIM), (redact(data)[:200], _TEXT)))


def _event_strip(states: list[str]) -> Text:
    """The per-event verdict strip — ``✓ 1 · ✗ 3`` — over the shared glyph map."""
    strip = Text()
    for index, state in enumerate(states):
        if index:
            strip.append(" · ", style=_DIM)
        glyph, color = cell_glyph(state)
        strip.append(f"{glyph} {index + 1}", style=f"bold {color}" if state == "fail" else color)
    return strip


#: Boilerplate whose content never belongs in an outline.
_HTML_ELIDED = frozenset({"script", "style", "noscript", "template", "svg", "iframe"})
#: Landmarks that earn their own branch in the outline.
_HTML_SECTIONS = frozenset({"header", "nav", "main", "section", "article", "aside", "footer"})


class _HtmlOutline(HTMLParser):
    """An OUTLINE of the document, not tag soup.

    Title, headings, landmark sections, table shapes, and quoted text content —
    boilerplate (scripts, styles, inline SVG) elided with a count. When
    *highlight* is set (a ``contains`` assertion's needle), its first match is
    marked at its site in the outline.
    """

    def __init__(self, root: TreeNode[object], highlight: str | None = None) -> None:
        """Start the outline under *root*, optionally hunting *highlight*."""
        super().__init__(convert_charrefs=True)
        self._stack = [root]
        self._highlight = highlight
        self._highlighted = False
        self._elide_depth = 0
        self._elided = 0
        self._table_depth = 0
        self._table_rows = 0
        self._table_cols = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Open a branch for landmarks, count boilerplate, track tables."""
        if self._elide_depth:
            self._elide_depth += 1
            return
        if tag in _HTML_ELIDED:
            self._elide_depth = 1
            self._elided += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "table":
            self._table_depth += 1
            self._table_rows = self._table_cols = 0
            return
        if self._table_depth:
            if tag == "tr":
                self._table_rows += 1
            elif tag in ("td", "th") and self._table_rows == 1:
                self._table_cols += 1
            return
        if tag in _HTML_SECTIONS:
            label = Text(f"§ {tag}", style=_AXIS)
            self._stack.append(self._stack[-1].add(label, expand=True))

    def handle_endtag(self, tag: str) -> None:
        """Close landmarks, finish table shapes, leave elisions."""
        if self._elide_depth:
            self._elide_depth -= 1
            return
        if tag == "title":
            self._in_title = False
            return
        if tag == "table" and self._table_depth:
            self._table_depth -= 1
            shape = Text("table", style=_ACCENT)
            shape.append(f"  {self._table_rows}x{self._table_cols}", style=_DIM)
            self._stack[-1].add_leaf(shape)
            return
        if tag in _HTML_SECTIONS and len(self._stack) > 1:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        """Title, headings, and meaningful text — with the contains-match marked."""
        if self._elide_depth or self._table_depth:
            return
        content = " ".join(data.split())
        if not content:
            return
        if self._in_title:
            title = Text("⌂ ", style=_ACCENT)
            title.append(content[:120], style=f"bold {_TEXT_HI}")
            self._stack[-1].add_leaf(title)
            return
        heading = self._heading_level()
        if heading:
            label = Text(f"{'#' * heading} ", style=_ACCENT)
            label.append(content[:160], style=f"bold {_TEXT_HI}")
            self._stack[-1].add_leaf(label)
            return
        if len(content) < 3:
            return
        line = Text("“", style=_DIM)
        if self._highlight and not self._highlighted and self._highlight.lower() in content.lower():
            start = content.lower().index(self._highlight.lower())
            line.append(content[:start][:120], style=_TEXT)
            line.append(content[start : start + len(self._highlight)], style=f"bold {_SAME}")
            line.append(content[start + len(self._highlight) :][:120], style=_TEXT)
            line.append("”  ✓ contains", style=_SAME)
            self._highlighted = True
        else:
            line.append(content[:200], style=_TEXT)
            line.append("”", style=_DIM)
        self._stack[-1].add_leaf(line)

    def _heading_level(self) -> int:
        tag = self.lasttag or ""
        if len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
            return int(tag[1])
        return 0

    def close(self) -> None:
        """Finish parsing and note what the outline elided."""
        super().close()
        if self._elided:
            plural = "" if self._elided == 1 else "s"
            self._stack[0].add_leaf(
                Text(f"⋯ {self._elided} script/style block{plural} elided", style=_DIM)
            )


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
    """The pill toggle — delegates to the one shared implementation."""
    return seg_pill(options, active)


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


def _app_redact(node: DOMNode) -> Callable[[str], str]:
    """The project's secret-redactor for a DOM node, or identity if no project loaded.

    Accepts any ``DOMNode`` (a view widget or the ``App`` itself), and is backed by
    ``ComparoApp.redactor`` (built once per project), so the many render sites share
    a single redactor instead of each rebuilding one.
    """
    app = cast("ComparoApp", node.app)
    return app.redactor.text if app.project is not None else str


def _governing_path(field: FieldDiff) -> str | None:
    """The declared path of the USER rule that governed *field*.

    ``None`` for the default catch-all and for synthetic built-ins (the ``$status``
    check, volatile-header ignores) — showing those as rule paths would send the
    user hunting their profiles for a rule that exists nowhere. The skip-group
    fallback label ("volatile") covers the built-ins.
    """
    ref = field.rule
    if ref is None or ref.origin in ("default", "synthetic"):
        return None
    return ref.path


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
        "\nthe same request is replayed against both sides — the OUTBOUND header "
        "above confirms whether the drift is the service's",
        style=_SAME,
    )
    hint = Text("\npress ", style=_DIM)
    hint.append("v", style=f"bold {_ACCENT}")
    hint.append(f" for {'side-by-side' if unified else 'unified'}    ", style=_DIM)
    hint.append("o", style=f"bold {_ACCENT}")
    hint.append(" expand outbound    ", style=_DIM)
    hint.append("i", style=f"bold {_ACCENT}")
    hint.append(" to silence this field", style=_DIM)
    return Group(title, Text(), well, _git_legend(baseline, candidate), insight, hint)


def _headers_well(cell: CellDiff, redact: Callable[[str], str] = str) -> Group | None:
    """The response-headers diff well — git-style over the ``$headers`` fields.

    Drifts as -/+ pairs, silenced names as ╎ with their governing rule, and a
    few identical headers as context so the well reads as the real envelope.
    """
    fields = [f for f in cell.fields if f.path.startswith("$headers")]
    if not fields:
        return None
    drifts = [f for f in fields if f.state is State.DRIFT]
    skips = [f for f in fields if f.state is State.SKIP]
    sames = [f for f in fields if f.state is State.SAME][:6]
    head = Text("RESPONSE HEADERS", style=f"bold {_LABEL}")
    head.append(f"   {len(drifts)} drift · {len(skips)} ignored", style=_DRIFT if drifts else _DIM)
    lines: list[RenderableType] = [head]

    def name_of(field: FieldDiff) -> str:
        return redact(field.path.removeprefix("$headers."))

    for field in sames:
        line = Text("▏  ", style=_SAME)
        line.append(f"{name_of(field)}: ", style=_DIM)
        line.append(_clip(redact(str(field.baseline))), style=_TEXT)
        lines.append(line)
    for field in drifts:
        minus = Text("▌ - ", style=_DRIFT)
        minus.append(f"{name_of(field)}: ", style=_DIM)
        minus.append(_clip(redact(str(field.baseline))), style=_DIM)
        lines.append(minus)
        plus = Text("▌ + ", style=_DRIFT)
        plus.append(f"{name_of(field)}: ", style=_DIM)
        plus.append(_clip(redact(str(field.candidate))), style=f"bold {_DRIFT}")
        lines.append(plus)
    for field in skips:
        rule = _governing_path(field)
        line = Text("╎  ", style=_SKIP)
        line.append(f"{name_of(field)}: ", style=_DIM)
        line.append("⋯ ignored", style=_SKIP)
        line.append(f" · rule {rule}" if rule else " · built-in volatile", style=_DIM)
        lines.append(line)
    return Group(*lines)


def _cell_verdict_rows(cell: CellDiff, redact: Callable[[str], str] = str) -> list[CheckRow]:
    """The diff verdict box's rows — one per BROKEN rule on this cell."""
    rows: list[CheckRow] = []
    for outcome in cell.rule_outcomes:
        if outcome.outcome != "broke":
            continue
        ref = outcome.ref
        evidence = ""
        witness = next(
            (
                field
                for field in cell.drifts
                if field.rule is not None and written_identity(field.rule) == written_identity(ref)
            ),
            None,
        )
        label = redact(ref.path)
        if witness is not None:
            label = f"{redact(ref.path)} · {ref.mode} → {redact(witness.path)}"
            evidence = (
                f"{_clip(redact(json.dumps(witness.baseline, default=str)))} → "
                f"{_clip(redact(json.dumps(witness.candidate, default=str)))}"
            )
        rows.append(
            CheckRow(
                label,
                "broke",
                provenance=provenance_suffix(ref.origin, ref.profile),
                evidence=evidence,
            )
        )
    return rows


def _cell_inspect(
    cell: CellDiff,
    pair: tuple[Environment, Environment] | None,
    *,
    unified: bool,
    outbound_layer: RenderableType | None = None,
    redact: Callable[[str], str] = str,
) -> Group:
    """The whole-request inspect (mockup states 1-5): ledger → verdict → wells.

    Reads in triage order: the call ledger, the verdict box naming which rules
    broke (or the green all-held box; or the error panel for a dead cell), the
    response-headers well, then the body — the git well for JSON, the event
    sequence for a stream, honest before/after notes otherwise. Clean sections
    collapse to one-line stubs instead of disappearing.
    """
    parts: list[RenderableType] = []
    ledger = _live_call_ledger(cell)
    if ledger is not None:
        parts.extend((ledger, Text()))
    if outbound_layer is not None:
        parts.extend((outbound_layer, Text()))
    if cell.error is not None:
        attempts, policy = 1, None
        for side in (cell.candidate, cell.baseline):
            if side is not None and side.error is not None:
                attempts, policy = side.attempts, side.retry_policy
                break
        model = ErrorPanelModel(
            message=redact(cell.error),
            attempts=attempts,
            retry_policy=policy,
            meaning="0 fields compared — the cell counts as error; "
            "no rule was evaluated, so nothing here reads as broken.",
            rerun_hint=f"[{_ACCENT}]x[/] re-runs the diff (all cells)",
        )
        kept: RenderableType | None = None
        if cell.baseline is not None and cell.baseline.response is not None:
            body = cell.baseline.response.body
            preview = redact(body.decode("utf-8", "replace"))[:200] if body else ""
            note = Text("  baseline response kept — single-sided, not a diff\n", style=_DIM)
            note.append(f"  {_clip(preview, 160)}", style=_SKIP)
            kept = note
        parts.append(error_panel(model, kept))
        return Group(*parts)
    broken = _cell_verdict_rows(cell, redact)
    effective = sum(1 for outcome in cell.rule_outcomes if outcome.outcome != "absent")
    if broken:
        parts.append(verdict_box(broken, total=effective))
    else:
        held = sum(1 for outcome in cell.rule_outcomes if outcome.outcome == "held")
        silenced = sum(1 for outcome in cell.rule_outcomes if outcome.outcome == "silenced")
        clean = Text("✓ every rule held on this cell", style=f"bold {_SAME}")
        clean.append(f"  — {held} held · {silenced} silenced", style=_DIM)
        parts.append(clean)
    parts.append(Text())
    headers = _headers_well(cell, redact)
    if headers is not None:
        parts.extend((headers, Text()))
    base_events, cand_events = _cell_events(cell)
    if base_events is not None or cand_events is not None:
        parts.append(_stream_body_view(base_events or [], cand_events or [], redact))
        return Group(*parts)
    body_fields = {
        field.path: field
        for field in cell.fields
        if not field.path.startswith("$headers") and field.path != "$status"
    }
    if cell.baseline_body is None or cell.candidate_body is None:
        if not broken:
            parts.append(Text("body — identical (compared as raw bytes)", style=_DIM))
        else:
            parts.append(Text("body — differs (compared as raw bytes)", style=_DRIFT))
        return Group(*parts)
    if not broken:
        keys = len(cell.baseline_body) if isinstance(cell.baseline_body, dict) else "…"
        parts.append(Text(f"body — identical · {keys} top-level keys", style=_DIM))
        return Group(*parts)
    names = (pair[0].metadata.name, pair[1].metadata.name) if pair is not None else ("a", "b")
    lines = _body_diff_lines(cell.baseline_body, cell.candidate_body, body_fields, redact=redact)
    well: RenderableType = (
        _diff_unified(lines) if unified else _diff_side_by_side(lines, pair, names)
    )
    head = Text("BODY", style=f"bold {_LABEL}")
    parts.extend((head, well, _git_legend(*names)))
    return Group(*parts)


def _rule_record_view(
    ref: RuleRef,
    cells: list[tuple[CellDiff, str]],
    redact: Callable[[str], str] = str,
) -> Group:
    """The rule record (mockup states 6-8): spec · stat chips · per-cell record."""
    counts = {"broke": 0, "held": 0, "silenced": 0, "absent": 0, "error": 0}
    for _, outcome in cells:
        counts[outcome] = counts.get(outcome, 0) + 1
    spec_rows: list[tuple[str, Text | str]] = [
        ("path", Text(redact(ref.path), style=f"bold {_TEXT_HI}")),
        ("mode", Text(ref.mode, style=_MODE.get(ref.mode, _TEXT))),
        (
            "source",
            Text(provenance_suffix(ref.origin, redact(ref.profile) if ref.profile else None)),
        ),
    ]
    if ref.tolerance is not None:
        spec_rows.append(("tolerance", Text(f"±{ref.tolerance}", style=_WARN)))
    chips = [
        StatChip("enforced", sum(counts.values()) - counts["absent"], _TEXT_HI),
        StatChip("✗ broke", counts["broke"], _DRIFT),
        StatChip("✓ held", counts["held"], _SAME),
        StatChip("◌ silenced", counts["silenced"], _SKIP),
        StatChip("— absent", counts["absent"], _DIM),
        StatChip("! error", counts["error"], _WARN),
    ]
    parts: list[RenderableType] = [spec_table(spec_rows), Text(), stat_chips(chips), Text()]
    record_head = Text("RECORD", style=f"bold {_LABEL}")
    record_head.append("  every cell this rule touched", style=_DIM)
    parts.append(record_head)
    marks = {
        "broke": ("✗ broke", _DRIFT),
        "held": ("✓ held", _SAME),
        "silenced": ("◌ silenced", _SKIP),
        "absent": ("— absent", _DIM),
        "error": ("! error", _WARN),
    }
    for cell, outcome in cells:
        word, color = marks.get(outcome, ("?", _DIM))
        line = Text("  ")
        line.append(cell.request.metadata.name, style=_TEXT_HI)
        if cell.cell_key:
            line.append(f" · {redact(cell.cell_key)}", style=_AXIS)
        line.append(f"   {word}", style=f"bold {color}")
        if outcome == "broke":
            witness = next(
                (
                    field
                    for field in cell.drifts
                    if field.rule is not None
                    and written_identity(field.rule) == written_identity(ref)
                ),
                None,
            )
            if witness is not None:
                line.append(f"   {redact(witness.path)}", style=_TEXT)
                line.append(f"  {_clip(redact(witness.detail))}", style=_DIM)
        elif outcome == "silenced":
            silenced = sum(
                1
                for field in cell.fields
                if field.state is State.SKIP
                and field.rule is not None
                and written_identity(field.rule) == written_identity(ref)
            )
            line.append(f"   {silenced} field(s) hidden", style=_DIM)
        parts.append(line)
    return Group(*parts)


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


def _rule_detail(rule: str, mode: str, silenced: list[tuple[str, list[str]]]) -> Group:
    """The rule-detail panel (d-rules) — what a silencing rule is and every field it hid.

    Selecting a rule in the "broken rules" index shows this instead of a body diff:
    the rule's mode and why it exists, then the exact field paths it silenced with
    their source request — so a skip is auditable, never a silent pass.
    """
    parts: list[RenderableType] = []
    head = Text("RULE  ", style=f"bold {_LABEL}")
    head.append(rule, style=_SKIP)
    head.append(f"   {mode}", style=_DIM)
    parts.append(head)
    why = Text("\nwhy  ", style=f"bold {_DIM}")
    if mode == "ignore":
        why.append(
            "volatile — the DiffProfile deliberately ignores this path; a diff here "
            "would be noise, not a regression.",
            style=_DIM,
        )
    else:
        why.append(
            "within tolerance — a small numeric drift on this path is absorbed by design.",
            style=_DIM,
        )
    parts.append(why)
    total_requests = len({request for _, requests in silenced for request in requests})
    heading = Text("\nFields it silenced  ", style=f"bold {_TEXT_HI}")
    heading.append(f"{len(silenced)} · across {total_requests} request(s)", style=_DIM)
    parts.append(heading)
    for path, requests in silenced:
        line = Text("◌ ", style=_SKIP)
        line.append(path, style=_SKIP)
        who = "all requests" if len(requests) > 1 else (requests[0] if requests else "—")
        line.append(f"   · {who}", style=_DIM)
        parts.append(line)
    footer = Text("\ngreen never means full coverage — this is ", style=_DIM)
    footer.append("what the tool chose not to check", style=f"bold {_TEXT_HI}")
    footer.append(", and why.", style=_DIM)
    parts.append(footer)
    return Group(*parts)


def _mode_prose(mode: str) -> str:
    """A one-line explanation of what a diff mode allows — for the field drill."""
    return {
        "exact": "values must match exactly; no tolerance, no shape allowance",
        "shape": "only the structure and types must match, not the values",
        "tolerance": "a small numeric drift on this path is absorbed",
        "ignore": "this path is deliberately not compared",
    }.get(mode, "")


def _field_drill_card(
    path: str, entries: list[tuple[CellDiff, FieldDiff]], redact: Callable[[str], str] = str
) -> Group:
    """The field-drill card (d-drill) — the whole story of one drift on one screen.

    Reached by ``enter`` on a drifted field: its state and the mode that made it a
    drift, baseline→candidate value AND type, and the EXACT ignore-rule ``i`` would
    write — so silencing a diff is never a hidden act.
    """
    field = entries[0][1]
    count = len(entries)
    plural = "" if count == 1 else "s"
    parts: list[RenderableType] = []
    head = Text("field drill  ", style=f"bold {_LABEL}")
    head.append(redact(path), style=f"bold {_DRIFT}")
    head.append("   drift · fails the gate", style=_DRIFT)
    parts.append(head)

    status = Text("\nstate   ", style=_DIM)
    status.append("drift", style=f"bold {_DRIFT}")
    status.append("\nmode    ", style=_DIM)
    status.append(field.mode, style=_MODE.get(field.mode, _AXIS))
    prose = _mode_prose(field.mode)
    if prose:
        status.append(f"   {prose}", style=_DIM)
    status.append("\ndrifts  ", style=_DIM)
    status.append(f"{count} cell{plural}", style=f"bold {_TEXT_HI}")
    variants = ", ".join(redact(cell.cell_key) for cell, _ in entries if cell.cell_key)
    if variants:
        status.append(f"   {variants}", style=_AXIS)
    status.append("\nrule    ", style=_DIM)
    governing = _governing_path(field)
    if governing is not None:
        status.append(redact(governing), style=_SKIP)
    else:
        status.append("none", style=_SKIP)
        status.append("   not silenced by any DiffProfile rule", style=_DIM)
    parts.append(status)

    parts.append(Text("\nBaseline → candidate", style=f"bold {_TEXT_HI}"))
    table = _table()
    table.add_column("", style=_LABEL, no_wrap=True)
    table.add_column("baseline")
    table.add_column("candidate")
    table.add_row(
        Text("value", style=_DIM),
        Text(redact(_sv(field.baseline)), style=_TEXT),
        Text(redact(_sv(field.candidate)), style=f"bold {_TEXT_HI}"),
    )
    table.add_row(
        Text("type", style=_DIM),
        Text(type(field.baseline).__name__, style=_DIM),
        Text(type(field.candidate).__name__, style=_DIM),
    )
    parts.append(table)

    triage = Text(
        "\nTriage — i writes the rule below into the committed DiffProfile",
        style=f"bold {_TEXT_HI}",
    )
    parts.append(triage)
    yaml = Text("\nignore:\n", style=_DIM)
    yaml.append(f"  - {redact(path)}", style=_SKIP)
    yaml.append(f"   # silences all {count} cell{plural} at once", style=_DIM)
    parts.append(yaml)
    foot = Text("\npress ", style=_DIM)
    foot.append("i", style=f"bold {_ACCENT}")
    foot.append(" to ignore    ", style=_DIM)
    foot.append("esc", style=f"bold {_ACCENT}")
    foot.append(" back", style=_DIM)
    parts.append(foot)
    return Group(*parts)


def _outbound_diff_view(
    baseline: ResolvedRequest,
    candidate: ResolvedRequest,
    base_name: str,
    cand_name: str,
    *,
    redact: Callable[[str], str] = str,
) -> Group:
    """Diff the resolved outbound request across the pair (DIFF-27).

    A table of every differing field — baseline → candidate — with the config
    surface each difference came from, so the panel answers the first triage
    question: is the drift the service's, or did we send two different requests?
    Every value is redacted, and masked secrets compare equal, so a hidden token
    can never surface as a false drift.
    """
    parts: list[RenderableType] = []
    head = Text()
    head.append("OUTBOUND REQUEST", style=f"bold {_LABEL}")
    head.append("   the request we sent to each side", style=_DIM)
    parts.append(head)

    diffs = outbound_diffs(baseline, candidate, redact=redact)

    if not diffs:
        verdict = Text("\n✓ identical on both sides", style=f"bold {_SAME}")
        verdict.append(
            "\nWe send the same request to both environments, so any response "
            "drift is the service's — not something we sent differently.",
            style=_DIM,
        )
        parts.append(verdict)
    else:
        heading = Text("\n")
        heading.append(f"differs on {len(diffs)} ", style=f"bold {_DRIFT}")
        heading.append("field" if len(diffs) == 1 else "fields", style=f"bold {_DRIFT}")
        heading.append("  — is the drift the service's, or ours?", style=_DIM)
        parts.append(heading)
        legend = Text("− ", style=f"bold {_DRIFT}")
        legend.append(base_name, style=_DIM)
        legend.append("    + ", style=f"bold {_SAME}")
        legend.append(cand_name, style=_DIM)
        parts.append(legend)
        body = Text()
        for entry in diffs:
            body.append(f"\n{entry.label}", style=_LABEL)
            body.append(f"   ← {entry.source}", style=_DIM)  # where the difference comes from
            body.append("\n  − ", style=f"bold {_DRIFT}")
            body.append(entry.baseline or "—", style=_TEXT)
            if entry.candidate and entry.candidate != "—":
                body.append("\n  + ", style=f"bold {_SAME}")
                body.append(entry.candidate, style=f"bold {_TEXT_HI}")
        parts.append(body)
        verdict = Text("\n⚠ the outbound differs across environments", style=f"bold {_WARN}")
        verdict.append(
            " — some response drift is\nours: we sent a different request (see the source of "
            "each field). Fix is likely config, not the service.",
            style=_DIM,
        )
        parts.append(verdict)
    return Group(*parts)


def _outbound_header(
    baseline: ResolvedRequest,
    candidate: ResolvedRequest,
    base_name: str,
    cand_name: str,
    *,
    expanded: bool,
    redact: Callable[[str], str] = str,
) -> RenderableType:
    """The compare panel's persistent OUTBOUND layer — a summary or the full diff.

    comparo replays the *same* request against both environments, so the outbound
    only differs where env config does (host, auth, an env var). This header sits
    above the response-body diff and answers the first triage question — is the
    drift the service's, or did we send two different requests? — without leaving
    the field view. ``o`` toggles it between the one-line summary and the full
    request diff. Every value is redacted, so a masked secret never leaks or
    surfaces as a false drift.
    """
    if expanded:
        view = _outbound_diff_view(baseline, candidate, base_name, cand_name, redact=redact)
        collapse = Text("\npress ", style=_DIM)
        collapse.append("o", style=f"bold {_ACCENT}")
        collapse.append(" to collapse", style=_DIM)
        return Group(view, collapse)
    diffs = outbound_diffs(baseline, candidate, redact=redact)
    line = Text(no_wrap=True)
    line.append("OUTBOUND  ", style=f"bold {_LABEL}")
    if diffs:
        labels = ", ".join(entry.label for entry in diffs[:3])
        more = "…" if len(diffs) > 3 else ""
        line.append("⚠ ", style=f"bold {_WARN}")
        line.append(f"differs · {len(diffs)} field{'s' if len(diffs) != 1 else ''} ", style=_TEXT)
        line.append(f"({labels}{more})", style=_DIM)
    else:
        line.append("✓ ", style=f"bold {_SAME}")
        line.append("identical on both sides", style=_DIM)
    line.append("   press ", style=_DIM)
    line.append("o", style=f"bold {_ACCENT}")
    line.append(" to expand", style=_DIM)
    return _band(line, _HUNK_BG)


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


def _ref_ids(value: object) -> list[str]:
    """Extract the referenced ids from a free-form ``$ref``/id/list profile value."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        ref = value.get("$ref")
        return [ref] if isinstance(ref, str) else []
    if isinstance(value, list):
        return [rid for item in value for rid in _ref_ids(item)]
    return []


def _exec_setup(
    project: LoadedProject, profile: ExecutionProfile, redact: Callable[[str], str] = str
) -> Group:
    """The launch SETUP panel — a read-only spec sheet for the profile.

    What it asserts, what it diffs, the selection math, and the gate formula, so
    the verdict's composition is legible before you run.
    """
    baseline, candidate = _exec_env_names(project, profile)
    mode = _exec_mode(profile)
    profiles = profile.spec.profiles
    assert_ids = _ref_ids(profiles.assert_) if profiles is not None else []
    diff_ids = _ref_ids(profiles.diff) if profiles is not None else []
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
    if mode in ("assert", "both"):
        asserts = Text("\nasserts    ", style=_DIM)
        asserts.append("on both environments", style=_DIM)
        asserts.append("   status · schema sugar per request", style=_DIM)
        if assert_ids:
            asserts.append("   + ", style=_DIM)
            asserts.append(", ".join(redact(rid) for rid in assert_ids), style=f"bold {_AXIS}")
        parts.append(asserts)
    if mode in ("diff", "both") and candidate is not None:
        diffs = Text("\ndiffs      ", style=_DIM)
        diffs.append(f"{redact(baseline)} ⇄ {redact(candidate)}", style=_TEXT)
        if diff_ids:
            diffs.append("   · profiles ", style=_DIM)
            diffs.append(", ".join(redact(rid) for rid in diff_ids), style=f"bold {_AXIS}")
        parts.append(diffs)
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
    for request in select_requests(project, profile):
        line, count = _exec_plan_line(project, profile, request, redact)
        parts.append(line)
        total += count
    envs = 2 if candidate is not None else 1
    summary = Text("\n  will run ", style=_DIM)
    summary.append(f"{total} cell{'' if total == 1 else 's'}", style=f"bold {_TEXT_HI}")
    summary.append(" × ", style=_DIM)
    summary.append(f"{envs} env{'' if envs == 1 else 's'}", style=f"bold {_TEXT_HI}")
    summary.append(" = ", style=_DIM)
    summary.append(f"{total * envs} calls", style=f"bold {_TEXT_HI}")
    parts.append(summary)
    # The gate formula, stated up front, so the verdict's composition is legible
    # before the run — the same ∧ shown post-run in the gate ledger.
    gate = Text("\ngate = ", style=_DIM)
    factors = []
    if mode in ("assert", "both"):
        factors += ["baseline asserts", "candidate asserts"]
    if mode in ("both", "diff") and candidate is not None:
        factors.append("diff")
    gate.append(" ∧ ".join(factors) or "—", style=_SAME)
    gate.append("   — press ", style=_DIM)
    gate.append("enter", style=f"bold {_ACCENT}")
    gate.append(" to run", style=_DIM)
    parts.append(gate)
    return Group(*parts)


class _RunningRow(NamedTuple):
    """One cell of the live plan — its state and, once finished, both sides' metrics.

    ``variant``/``method_path``/``drift`` are already redacted by the view before the
    row is built, so the running table never handles a raw declared secret.
    """

    request: str
    variant: str = ""
    method_path: str = ""
    state: str = "queued"  # queued | running | done
    baseline_status: int | None = None
    candidate_status: int | None = None
    baseline_ms: int | None = None
    candidate_ms: int | None = None
    base_pass: int = 0
    base_fail: int = 0
    cand_pass: int = 0
    cand_fail: int = 0
    drift: str = ""
    #: The cell's overall verdict — an error or a failed assertion fails it too,
    #: not only a drift (so an errored-but-undrifted cell is not painted green).
    failed: bool = False


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
    state = "done" if event.done else ("running" if event.started else "queued")
    return _RunningRow(
        request=_req_short(event.request_id),
        variant=redact(event.cell_key) if event.cell_key else "",
        method_path=method_path,
        state=state,
        baseline_status=event.status,
        candidate_status=event.candidate_status,
        baseline_ms=event.baseline_ms,
        candidate_ms=event.candidate_ms,
        base_pass=event.baseline_pass,
        base_fail=event.baseline_fail,
        cand_pass=event.candidate_pass,
        cand_fail=event.candidate_fail,
        drift=drift_leaf,
        failed=not event.ok,
    )


def _run_glyph(row: _RunningRow) -> Text:
    """The per-row status glyph: ○ queued · ◐ in flight · ✓/✗ finished."""
    if row.state == "queued":
        return Text("○", style=_DIM)
    if row.state == "running":
        return Text("◐", style=_WARN)
    return Text("✗" if row.failed else "✓", style=_DRIFT if row.failed else _SAME)


def _running_side(
    status: int | None, ms: int | None, passed: int, failed: int, *, exec_mode: bool, state: str
) -> Text:
    """One side of a running row — status · latency, plus the assert tally for exec."""
    if state == "running":
        return Text("…", style=_DIM)  # in flight
    if state == "queued" or (status is None and ms is None):
        return Text("—", style=_DIM)
    text = Text()
    if status is not None:
        text.append(str(status), style=_SAME if 200 <= status < 400 else _DRIFT)
    if ms is not None:
        text.append(f" {ms}ms", style=_DIM)
    if exec_mode and (passed or failed):
        text.append(f" {passed}/{failed}", style=_DRIFT if failed else _SAME)
    return text


def _running_state(row: _RunningRow, exec_mode: bool) -> Text:
    """The row's STATE cell — queued/running, or the finished verdict + reason."""
    if row.state == "queued":
        return Text("queued", style=_DIM)
    if row.state == "running":
        return Text("running", style=_WARN)
    if not row.failed:
        return Text("pass" if exec_mode else "same", style=_SAME)
    if not exec_mode:
        return Text("drift" if row.drift else "error", style=_DRIFT)
    reasons = []
    if row.base_fail or row.cand_fail:
        reasons.append("assert")
    if row.drift:
        reasons.append("diff")
    label = " + ".join(reasons) if reasons else "error"
    return Text(f"{label} ✗", style=_DRIFT)


def _running_table(
    label: str,
    done: int,
    total: int,
    rows: list[_RunningRow],
    *,
    base_name: str = "baseline",
    cand_name: str = "candidate",
    exec_mode: bool = False,
) -> Group:
    """The live run as a per-plan table — every cell a row, filling in per side.

    Shared by the Execution running sub-view and the Diff RUNNING state so both
    render progress *over the plan* (queued → in flight → finished), not a
    spinner. For an execution each side also carries its live assert tally, so a
    dimension can be watched failing before the gate is computed.
    """
    parts: list[RenderableType] = []
    head = Text()
    head.append(label or "run", style=f"bold {_TEXT_HI}")
    head.append("   replaying each cell against both sides…", style=_DIM)
    parts.append(head)
    passed = sum(1 for row in rows if row.state == "done" and not row.failed)
    failed = sum(1 for row in rows if row.state == "done" and row.failed)
    width = 24
    filled = round(width * done / total) if total else 0
    bar = Text("\n")
    bar.append("█" * filled, style=_ACCENT)
    bar.append("░" * (width - filled), style=_DIM)
    bar.append(f"   {done}/{total or '…'} cells", style=_TEXT)
    bar.append("   ", style=_DIM)
    bar.append(f"{passed} ✓", style=_SAME)
    bar.append("  ", style=_DIM)
    bar.append(f"{failed} ✗", style=_DRIFT if failed else _DIM)
    parts.append(bar)
    parts.append(Text())
    table = _table()
    table.add_column("", width=2, no_wrap=True)
    table.add_column("CELL", no_wrap=True)
    table.add_column(base_name, justify="right", no_wrap=True)
    table.add_column(cand_name, justify="right", no_wrap=True)
    table.add_column("STATE", no_wrap=True)
    for row in rows:
        table.add_row(
            _run_glyph(row),
            _running_cell_name(row, hi=(row.state == "running")),
            _running_side(
                row.baseline_status,
                row.baseline_ms,
                row.base_pass,
                row.base_fail,
                exec_mode=exec_mode,
                state=row.state,
            ),
            _running_side(
                row.candidate_status,
                row.candidate_ms,
                row.cand_pass,
                row.cand_fail,
                exec_mode=exec_mode,
                state=row.state,
            ),
            _running_state(row, exec_mode),
        )
    parts.append(table)
    legend = Text("\n○ queued  ◐ in flight  ✓/✗ finished", style=_DIM)
    parts.append(legend)
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
        f"    {len(drifted)} drifted cell(s) · the same compare engine as the Diff tab",
        style=_DIM,
    )
    hint = Text(
        "each cell shows its call ledger, the outbound we sent (is the drift ours?), "
        "then the response body diff — esc returns to where you came from.",
        style=_DIM,
    )
    parts: list[RenderableType] = [head, hint, Text()]
    names = (baseline, candidate or "candidate")
    cand_name = candidate or "candidate"
    for outcome in drifted:
        crumb = Text("▸ ", style=_DRIFT)
        crumb.append(_req_short(outcome.request_id), style=f"bold {_TEXT_HI}")
        if outcome.cell_key:
            crumb.append(f" · {redact(outcome.cell_key)}", style=_AXIS)
        request = outcome.diff.request if outcome.diff is not None else None
        if request is not None:
            crumb.append("    ", style=_DIM)
            crumb.append_text(_method_badge(request.spec.request.method))
            crumb.append(f" {redact(request.spec.request.endpoint)}", style=_DIM)
        parts.append(crumb)
        # Layer 1 — the call ledger (a latency/size regression even when bodies match).
        ledger = _executions_ledger(outcome.baseline, outcome.candidate)
        if ledger is not None:
            parts.append(ledger)
        # Layer 2 — the outbound we sent, so the drift traces to us or the service.
        base_req = outcome.baseline.resolved if outcome.baseline is not None else None
        cand_req = outcome.candidate.resolved if outcome.candidate is not None else None
        if base_req is not None and cand_req is not None:
            parts.append(
                _outbound_diff_view(base_req, cand_req, baseline, cand_name, redact=redact)
            )
        # Layer 3 — the response body diff.
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
    parts.append(_git_legend(baseline, cand_name))
    return Group(*parts)


def _gate_dimensions(result: ExecutionResult) -> list[tuple[str, bool]]:
    """The three gate dimensions and whether each holds — the AND factors."""
    base_fail = sum(_assert_tally(o.baseline_assertions)[1] for o in result.outcomes)
    cand_fail = sum(_assert_tally(o.candidate_assertions)[1] for o in result.outcomes)
    return [
        ("baseline assertion", base_fail == 0),
        ("candidate assertion", cand_fail == 0),
        ("diff", result.drift == 0 and result.errors == 0),
    ]


def _exec_header(
    profile: ExecutionProfile, result: ExecutionResult, redact: Callable[[str], str] = str
) -> Group:
    """The results banner — the gate verdict up front, profile/pair/select beneath.

    The verdict is the headline (``✗ GATE FAIL · exit 1 · which dimensions are
    red``); the dim context line names the profile, the pair, and the select
    clause so a leaked secret in a tag/request-id is still masked here.
    """
    passed = result.passed
    dims = _gate_dimensions(result)
    red = [name for name, ok in dims if not ok]
    exit_code = 0 if passed else 1
    verdict_style = f"bold {_SAME if passed else _DANGER}"
    hero = Text()
    hero.append("✓ GATE PASS" if passed else "✗ GATE FAIL", style=verdict_style)
    hero.append(f"   · exit {exit_code} · ", style=_DIM)
    if red:
        noun = "dimension" if len(red) == 1 else "dimensions"
        hero.append(f"{len(red)} of 3 {noun} red", style=_DRIFT)
        hero.append(f" — {' · '.join(red)}", style=_DIM)
    else:
        hero.append("all 3 dimensions green", style=_SAME)

    context = Text("\n")
    context.append("ExecutionProfile ", style=_DIM)
    context.append(redact(profile.metadata.name), style=f"bold {_TEXT_HI}")
    context.append("   baseline ", style=_DIM)
    context.append(redact(result.baseline), style=_TEXT_HI)
    context.append(" ●", style=_SAME)
    if result.candidate is not None:
        context.append(" ⇄ candidate ", style=_DIM)
        context.append(redact(result.candidate), style=_TEXT_HI)
        context.append(" ●", style=_SAME)
    context.append(f"   {len(result.outcomes)} cells", style=_DIM)
    select = profile.spec.select
    if select is not None and (select.tags or select.requests):
        clauses = []
        if select.tags:
            clauses.append("tags " + ", ".join(redact(tag) for tag in select.tags))
        if select.requests:
            clauses.append("requests " + ", ".join(redact(_req_short(r)) for r in select.requests))
        context.append("   select ", style=_DIM)
        context.append(" · ".join(clauses), style=_TEXT_HI)
    return Group(hero, context)


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


def _exec_triplet(outcome: CellOutcome, label: Text) -> tuple[Text, Text, Text, Text, Text]:
    """One execution cell as a per-cell row: cell · baseline · candidate · diff · verdict.

    Shown for EVERY cell (not just the drifted ones), so the results table is a full
    per-cell overview — a passing cell is as visible as a failing one, and the
    verdict column names the failing dimension so no drilldown is needed.
    """

    def side(results: list[AssertionResult]) -> Text:
        passed, failed, _ = _assert_tally(results)
        text = Text(f"{passed}✓", style=_SAME if not failed else _DIM)
        if failed:
            text.append(f" {failed}✗", style=_DRIFT)
        return text

    base_assert = side(outcome.baseline_assertions)
    cand_assert = side(outcome.candidate_assertions)

    drifted = outcome.diff is not None and outcome.diff.drifted
    if outcome.error is not None:
        diff = Text("error", style=_WARN)
    elif drifted:
        diff = Text(f"{len(outcome.diff.drifts)} drift", style=_DRIFT)  # type: ignore[union-attr]
    elif outcome.diff is not None:
        diff = Text("same", style=_SAME)
    else:
        diff = Text("—", style=_DIM)

    if outcome.error is not None:
        verdict = Text("✗ FAIL", style=_DRIFT)
        verdict.append(" (error)", style=_DIM)
    elif outcome.ok:
        verdict = Text("✓ pass", style=_SAME)
    else:
        assert_fail = (
            _assert_tally(outcome.baseline_assertions)[1] > 0
            or _assert_tally(outcome.candidate_assertions)[1] > 0
        )
        reasons = [r for r, on in (("assert", assert_fail), ("diff", drifted)) if on]
        verdict = Text("✗ FAIL", style=_DRIFT)
        verdict.append(f" ({' + '.join(reasons)})", style=_DIM)
    return label, base_assert, cand_assert, diff, verdict


def _gate_dim_panel(label: str, env: str, ok: bool, tally: str, detail: str) -> Panel:
    """One dimension of the gate ledger — a bordered panel with its tally + verdict."""
    header = Text(label, style=_LABEL)
    header.append(f" · {env}", style=_DIM)
    body = Text()
    body.append(f"{'✓' if ok else '✗'} {tally}", style=f"bold {_SAME if ok else _DRIFT}")
    body.append(f"\n{detail}", style=_DIM)
    body.append(f"\n{'PASS' if ok else 'FAIL'}", style=f"bold {_SAME if ok else _DRIFT}")
    return Panel(
        Group(header, Text(), body),
        box=ROUNDED,
        padding=(0, 1),
        border_style=_SAME if ok else _DANGER,
    )


def _gate_composition(result: ExecutionResult, redact: Callable[[str], str] = str) -> Group:
    """The gate as three side-by-side dimensions rolled up with ∧ → one verdict.

    baseline assertions ∧ candidate assertions ∧ diff. Shown at a glance so it
    reads which factor blocks the run — a run can fail on an assertion with no
    drift at all, which a single gate glyph hides.
    """

    def side_tally(getter: Callable[[CellOutcome], list[AssertionResult]]) -> tuple[int, int]:
        passed = failed = 0
        for outcome in result.outcomes:
            p, f, _ = _assert_tally(getter(outcome))
            passed, failed = passed + p, failed + f
        return passed, failed

    base_pass, base_fail = side_tally(lambda o: o.baseline_assertions)
    cand_pass, cand_fail = side_tally(lambda o: o.candidate_assertions)
    same = len(result.outcomes) - result.drift - result.errors
    diff_detail = f"{same} same · {result.drift} drift"
    if result.errors:
        diff_detail += f" · {result.errors} error"
    base_env = redact(result.baseline)
    cand_env = redact(result.candidate) if result.candidate is not None else "—"

    panels = (
        _gate_dim_panel(
            "baseline assertions",
            base_env,
            base_fail == 0,
            f"{base_pass}/{base_fail}",
            f"{base_pass} pass · {base_fail} fail",
        ),
        _gate_dim_panel(
            "candidate assertions",
            cand_env,
            cand_fail == 0,
            f"{cand_pass}/{cand_fail}",
            f"{cand_pass} pass · {cand_fail} fail",
        ),
        _gate_dim_panel(
            "diff",
            f"{base_env} ⇄ {cand_env}",
            result.drift == 0 and result.errors == 0,
            f"{same} · {result.drift}",
            diff_detail,
        ),
    )
    row = Table(box=None, expand=True, show_header=False, padding=0)
    for _ in panels:
        row.add_column(ratio=1)
    row.add_row(*panels)

    passed = result.passed
    rollup = Text("∧ gate  ", style=_DIM)
    rollup.append(
        "GATE PASS" if passed else "GATE FAIL", style=f"bold {_SAME if passed else _DANGER}"
    )
    return Group(row, Text(), rollup)


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


def _ledger_table(
    base_status: int | None,
    cand_status: int | None,
    base_ms: int | None,
    cand_ms: int | None,
    base_size: int | None,
    cand_size: int | None,
) -> Table | None:
    """The CALL LEDGER — baseline vs candidate status / latency / size, and Δ.

    A latency or size regression stays visible even when the two bodies match.
    ``None`` when there is no candidate side (a run), where a two-column ledger
    has nothing to say.
    """
    if cand_status is None and cand_ms is None:
        return None

    def ms(value: int | None) -> str:
        return f"{value}ms" if value is not None else "—"

    def signed(value: int) -> str:
        return f"+{value}" if value >= 0 else str(value)

    table = _table()
    table.add_column("CALL", style=_LABEL, no_wrap=True)
    table.add_column("baseline", justify="right")
    table.add_column("candidate", justify="right")
    table.add_column("Δ", justify="right")

    base_ok = base_status is not None and 200 <= base_status < 400
    cand_ok = cand_status is not None and 200 <= cand_status < 400
    same_status = base_status == cand_status
    table.add_row(
        Text("status", style=_DIM),
        Text("—" if base_status is None else str(base_status), style=_SAME if base_ok else _DRIFT),
        Text(
            "—" if cand_status is None else str(cand_status),
            style=_SAME if cand_ok else _DRIFT,
        ),
        Text("=" if same_status else "≠", style=_DIM if same_status else _DRIFT),
    )
    latency_delta = (
        signed(cand_ms - base_ms) + "ms" if base_ms is not None and cand_ms is not None else ""
    )
    slow = base_ms is not None and cand_ms is not None and cand_ms > base_ms
    table.add_row(
        Text("latency", style=_DIM),
        Text(ms(base_ms), style=_TEXT),
        Text(ms(cand_ms), style=_TEXT),
        Text(latency_delta, style=_WARN if slow else _DIM),
    )
    size_delta = (
        signed(cand_size - base_size) + " B"
        if base_size is not None and cand_size is not None
        else ""
    )
    table.add_row(
        Text("size", style=_DIM),
        Text(_fmt_bytes(base_size), style=_TEXT),
        Text(_fmt_bytes(cand_size), style=_TEXT),
        Text(size_delta, style=_DIM),
    )
    return table


def _call_ledger(cell: CellRecord) -> Table | None:
    """The CALL LEDGER for a saved record cell — metrics come straight off the record."""
    return _ledger_table(
        cell.status,
        cell.candidate_status,
        cell.latency_ms,
        cell.candidate_latency_ms,
        cell.size_bytes,
        cell.candidate_size_bytes,
    )


def _executions_ledger(base: Execution | None, cand: Execution | None) -> Table | None:
    """The CALL LEDGER for a live pair of executions — metrics read off each response.

    This is the same ledger the saved-report replay shows, wired into the live
    compare and cell-detail panels so a latency/size regression is visible the
    moment a run finishes, not only when the report is reopened.
    """
    b = base.response if base is not None else None
    c = cand.response if cand is not None else None
    return _ledger_table(
        b.status if b is not None else None,
        c.status if c is not None else None,
        round(b.elapsed_ms) if b is not None else None,
        round(c.elapsed_ms) if c is not None else None,
        len(b.body) if b is not None else None,
        len(c.body) if c is not None else None,
    )


def _live_call_ledger(cell: CellDiff) -> Table | None:
    """The CALL LEDGER for a live diff cell — reads the executions carried on the cell."""
    return _executions_ledger(cell.baseline, cell.candidate)


def _event_sequence(
    baseline: list[object], candidate: list[object], redact: Callable[[str], str]
) -> Table:
    """A streamed response as a numbered event sequence — per event, ✓ same or ✗ drifted.

    Each row aligns event *n* of the two sides so the eye lands on exactly which
    event in the sequence diverged (a length change shows as a ``—`` on the short side).
    """
    table = _table()
    table.add_column("#", style=_DIM, justify="right", no_wrap=True)
    table.add_column("", width=2, no_wrap=True)
    table.add_column("baseline", ratio=1)
    table.add_column("candidate", ratio=1)

    def one(event: object | None) -> str:
        if event is None:
            return "—"
        return redact(json.dumps(event, ensure_ascii=False, default=str))

    for index in range(max(len(baseline), len(candidate))):
        left = baseline[index] if index < len(baseline) else None
        right = candidate[index] if index < len(candidate) else None
        same = left == right and left is not None
        table.add_row(
            str(index + 1),
            Text("✓" if same else "✗", style=_SAME if same else _DRIFT),
            Text(one(left), style=_DIM if same else _DRIFT, no_wrap=False),
            Text(one(right), style=_DIM if same else _SAME, no_wrap=False),
        )
    return table


def _stream_body_view(
    baseline: list[object], candidate: list[object], redact: Callable[[str], str] = str
) -> Group:
    """A streamed response as an event SEQUENCE, not one assembled blob (d-stream).

    A per-event ✓/✗ strip so the eye lands on which event diverged, then the
    aligned per-event table. This is what the mockup asks for when the response
    is chunked/SSE — the diff runs over events, not a single concatenated body.
    """
    count = max(len(baseline), len(candidate))
    drifts = 0
    strip = Text("event sequence  ", style=f"bold {_LABEL}")
    for index in range(count):
        left = baseline[index] if index < len(baseline) else None
        right = candidate[index] if index < len(candidate) else None
        same = left == right and left is not None
        if not same:
            drifts += 1
        if index:
            strip.append(" · ", style=_DIM)
        strip.append(f"{'✓' if same else '✗'}{index + 1}", style=_SAME if same else _DRIFT)
    strip.append(f"   — {drifts} of {count} event{'' if count == 1 else 's'} drift", style=_DIM)
    return Group(strip, Text(), _event_sequence(baseline, candidate, redact))


def _cell_events(cell: CellDiff) -> tuple[list[object] | None, list[object] | None]:
    """The two sides' streamed event lists, or ``(None, None)`` for a normal response."""
    base = (
        cell.baseline.response.events
        if cell.baseline is not None and cell.baseline.response is not None
        else None
    )
    cand = (
        cell.candidate.response.events
        if cell.candidate is not None and cell.candidate.response is not None
        else None
    )
    return base, cand


def _field_from_record(field: FieldDiffRecord) -> FieldDiff:
    """Reconstruct a live FieldDiff from a saved record's field — real state and mode."""
    if field.state == "drift":
        state = State.DRIFT
    elif field.state == "same":
        state = State.SAME
    else:
        state = State.SKIP
    # The record references the rule inventory by id; reconstructing the full ref
    # is the replay adapters' job (Results rework). Until then a replayed field
    # carries no ref — display falls back to "not silenced by any rule".
    rule: RuleRef | None = None
    return FieldDiff(
        field.path,
        state,
        field.mode,
        baseline=field.baseline,
        candidate=field.candidate,
        rule=rule,
    )


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
    # The saved record carries the REAL per-field decision (state + the profile mode
    # that governed it), so the replay renders the true modes — never a fabricated
    # ``exact`` (M-6). Reconstruct a FieldDiff per path from the saved FieldDiffRecord.
    states = {field.path: _field_from_record(field) for field in cell.fields}
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
    ledger = _call_ledger(cell)
    parts: list[RenderableType] = [title, Text(), well]
    if cell.baseline_events is not None or cell.candidate_events is not None:
        parts += [
            Text("\nevent sequence", style=f"bold {_DIM}"),
            _event_sequence(cell.baseline_events or [], cell.candidate_events or [], redact),
        ]
    if ledger is not None:
        parts += [Text("\ncall ledger", style=f"bold {_DIM}"), ledger]
    parts += [legend, note]
    return Group(*parts)


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


def _method_badge(method: str) -> Text:
    """The HTTP method as a coloured badge, per the shared method palette."""
    return Text(f" {method} ", style=f"bold {_INK} on {_METHOD.get(method, _ACCENT)}")


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
        head.append_text(_method_badge(cell.method))
        head.append(f" {cell.path}", style=_TEXT)
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
        req_line = _method_badge(cell.method)
        req_line.append(f" {cell.path}", style=_TEXT)
        request_node.add_leaf(req_line)
        if cell.request_headers:
            headers_node = request_node.add(
                Text.assemble(("headers ", _DIM), (f"({len(cell.request_headers)})", _DIM)),
                expand=True,
            )
            for name, value in list(cell.request_headers.items())[:4]:
                headers_node.add_leaf(Text.assemble((f"{name}: ", _DIM), (value, _TEXT)))
        if cell.request_body is not None:
            request_node.add_leaf(
                Text.assemble(("body ", _DIM), (_body_summary(cell.request_body), _TEXT))
            )
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
    # Scope the checks to THIS request's cell — the record-wide roll-up would leak
    # every other request's assertions into this request's detail.
    lines = cell.assertions if cell is not None else []
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
    redact: Callable[[str], str],
) -> RenderableType:
    """Render one settings section - the master/detail right pane."""
    if key == "about":
        return _settings_about()
    if key == "project":
        return _settings_project(project, redact)
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


def _settings_project(project: LoadedProject, redact: Callable[[str], str]) -> Text:
    def count(kind: type | tuple[type, ...]) -> int:
        return sum(1 for obj in project.objects.values() if isinstance(obj, kind))

    # An env or project NAME can equal a declared secret value (the untainted
    # vector) — this is a display sink, so mask through the project's redactor.
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
