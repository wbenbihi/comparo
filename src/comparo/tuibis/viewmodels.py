"""Presentation view-models — the only data shape screens and widgets see.

These are deliberately backend-agnostic: when the real projections land, an
adapter maps application structs onto these frozen dataclasses. During the
visual phase they are fed from ``fixtures``.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import Enum


class EntityKind(Enum):
    """EntityKind — see the module docstring."""

    PROJECT = "project"
    REQUEST = "request"
    SCENARIO = "scenario"
    ENVIRONMENT = "environment"
    MATRIX = "matrix"
    SCHEMA = "schema"
    ASSERTION_PROFILE = "assertion-profile"
    DIFF_PROFILE = "diff-profile"
    EXECUTION_PROFILE = "execution-profile"
    VALUE = "value"
    REPORT = "report"


class Status(Enum):
    """Status — see the module docstring."""

    PASSED = "passed"
    ADVISORY = "advisory"
    FAILED = "failed"
    ERROR = "error"
    RUNNING = "running"
    QUEUED = "queued"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    REDACTED = "redacted"
    DRIFT = "drift"
    SAME = "same"
    OUTBOUND = "outbound"
    VERIFIED = "verified"


class PillKind(Enum):
    """PillKind — see the module docstring."""

    MATRIX = "matrix"
    LIVE = "live"
    DEFAULT = "default"
    BASELINE = "baseline"
    CANDIDATE = "candidate"


@dataclass(frozen=True)
class Pill:
    """Pill — see the module docstring."""

    kind: PillKind
    text: str


@dataclass(frozen=True)
class ProvenancedValue:
    """One key/value row with its ← provenance annotation."""

    key: str
    value: str
    provenance: str = ""
    redacted: bool = False


@dataclass(frozen=True)
class BodyLine:
    """BodyLine — see the module docstring."""

    number: int
    text: str
    provenance: str = ""
    redacted: bool = False


@dataclass(frozen=True)
class RelationVM:
    """RelationVM — see the module docstring."""

    kind: EntityKind
    name: str
    summary: str = ""
    source: str = ""


@dataclass(frozen=True)
class RequestVM:
    """RequestVM — see the module docstring."""

    name: str
    method: str
    path: str
    description: str
    tags: tuple[str, ...]
    base_url: str
    resolved_env: str
    resolved_cell: str
    query: tuple[ProvenancedValue, ...]
    headers: tuple[ProvenancedValue, ...]
    headers_note: str
    body_lines: tuple[BodyLine, ...]
    matrix_pill: Pill | None
    variants_line: str
    expected_line: str
    checks_line: str
    history_line: str
    uses: tuple[RelationVM, ...]
    used_by: tuple[RelationVM, ...]
    write_capable: bool = False


@dataclass(frozen=True)
class EnvironmentVM:
    """EnvironmentVM — see the module docstring."""

    name: str
    url: str
    pills: tuple[Pill, ...]
    connection_line: str
    safety_line: str
    values_note: str
    values: tuple[tuple[str, str, str, str], ...]  # name, class, source, used-by
    precedence_line: str
    usage_line: str


@dataclass(frozen=True)
class TreeLeafVM:
    """TreeLeafVM — see the module docstring."""

    key: str
    kind: EntityKind
    name: str
    detail: str = ""
    method: str | None = None
    pills: tuple[Pill, ...] = ()
    dimmed: bool = False


@dataclass(frozen=True)
class TreeGroupVM:
    """TreeGroupVM — see the module docstring."""

    label: str
    count: str
    leaves: tuple[TreeLeafVM, ...]
    collapsed: bool = False
    omitted: str = ""


@dataclass(frozen=True)
class ProjectVM:
    """ProjectVM — see the module docstring."""

    name: str
    environments: str  # "staging ⇄ production" context, pre-rendered parts
    baseline: str
    candidate: str
    groups: tuple[TreeGroupVM, ...]
    requests: dict[str, RequestVM] = field(default_factory=dict)
    envs: dict[str, EnvironmentVM] = field(default_factory=dict)


@dataclass(frozen=True)
class KeyHint:
    """KeyHint — see the module docstring."""

    key: str
    label: str


@dataclass(frozen=True)
class StyledCell:
    """One fixed-width table cell: text, semantic style token, width."""

    text: str
    style: str = "fg"
    width: int = 0


@dataclass(frozen=True)
class StyledLine:
    """One pre-modelled row: a run of styled cells, optional cursor."""

    cells: tuple[StyledCell, ...]
    cursor: bool = False
    indent: int = 1


@dataclass(frozen=True)
class RunStripVM:
    """The slim run header strip."""

    run_id: str
    profile: str
    baseline: str
    candidate: str
    verdict: str
    tallies: str
    right: str
    replay: bool = False


@dataclass(frozen=True)
class StageVM:
    """One launch stage in the breadcrumb: name + state."""

    name: str
    state: str  # done | active | run | fail | todo


@dataclass(frozen=True)
class IndexItemVM:
    """One analyse index chip."""

    key: str
    label: str


@dataclass(frozen=True)
class PanelVM:
    """A titled panel of pre-modelled lines."""

    title: str
    lines: tuple[StyledLine, ...]
    hint: str = ""
    tone: str = "neutral"
    glyph: EntityKind | None = None


@dataclass(frozen=True)
class StageViewVM:
    """One launch stage screen: its panels, arranged left/right or stacked."""

    stage: str
    left: tuple[PanelVM, ...]
    right: tuple[PanelVM, ...]
    full: tuple[PanelVM, ...]
    action_line: tuple[StyledCell, ...] = ()
    hints: tuple[KeyHint, ...] = ()


@dataclass(frozen=True)
class AnalyseIndexVM:
    """One analyse index: list panel + detail panel + inspect body."""

    key: str
    list_panel: PanelVM
    detail_panel: PanelVM
    inspect_facet: str
    inspect_lines: tuple[StyledLine, ...]


@dataclass(frozen=True)
class AnalyseVM:
    """The whole analyse composition."""

    strip: RunStripVM
    indexes: tuple[AnalyseIndexVM, ...]
    deep_dives: dict[str, PanelVM] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkspaceVM:
    """A generic panel-composed workspace (reports, settings)."""

    panels_left: tuple[PanelVM, ...]
    panels_right: tuple[PanelVM, ...]
    hints: tuple[KeyHint, ...]


@dataclass(frozen=True)
class ListItemVM:
    """One interactive list row: a key, its styled line, and behaviors."""

    key: str
    line: StyledLine
    selectable: bool = False
    selected: bool = False
    header: bool = False
    dimmed: bool = False
    search_text: str = ""
