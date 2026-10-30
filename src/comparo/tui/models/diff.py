"""Diff/explorer document model — pure text analysis, no UI."""

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

MAX_DOCUMENT_LINES = 5_000
MIN_FOLDABLE_LINES = 3
CHANGE_CUTOFF = 0.6
MAX_PAIRING_AREA = 2_500
EMPHASIS_COVERAGE_LIMIT = 0.8

_YAML_KEY = re.compile(r"^[\w.-]+:(\s|$)")

type RowKind = Literal["context", "add", "remove", "change"]
type Span = tuple[int, int]
type VisibleEntry = tuple[Literal["row", "fold"], int]


@dataclass(frozen=True)
class DiffRow:
    """One aligned line pair (or single line in explorer mode)."""

    kind: RowKind
    old_lineno: int | None
    new_lineno: int | None
    old_text: str
    new_text: str
    emphasis_old: tuple[Span, ...] = ()
    emphasis_new: tuple[Span, ...] = ()


@dataclass(frozen=True)
class FoldRegion:
    """One collapsible row range."""

    start: int
    end: int
    label: str
    depth: int = 0


@dataclass(frozen=True)
class DiffDocument:
    """Rows plus fold regions; sided=False means single-document explorer."""

    rows: tuple[DiffRow, ...]
    regions: tuple[FoldRegion, ...]
    old_text: str
    new_text: str
    language: str | None
    sided: bool
    first_lineno: int = 1
    truncated: bool = False
    detected: bool = False
    prettified: bool = False


def detect_language(text: str) -> str | None:
    """Conservative content sniffing: json, html, xml, yaml, or None."""
    stripped = text.lstrip()
    if not stripped:
        return None
    if stripped[0] in "{[":
        try:
            json.loads(text)
        except ValueError:
            pass
        else:
            return "json"
    lowered = stripped[:256].lower()
    if lowered.startswith(("<!doctype html", "<html")):
        return "html"
    if lowered.startswith("<?xml"):
        return "xml"
    if stripped.startswith("<"):
        return "html"
    first_line = stripped.splitlines()[0]
    if _YAML_KEY.match(first_line):
        return "yaml"
    return None


def _prettify(text: str, language: str | None) -> tuple[str, bool]:
    """Reformat complete JSON; anything else passes through untouched.

    XML remains verbatim because the standard-library XML formatters are not
    hardened for untrusted HTTP response bodies.
    """
    if language == "json":
        try:
            parsed = json.loads(text)
        except ValueError:
            return text, False
        pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
        return pretty, pretty != text
    return text, False


def document_label(document: DiffDocument) -> str:
    """Disclosure label for panel subtitles: what representation is shown."""
    parts: list[str] = []
    if document.language:
        parts.append(f"{document.language} · detected" if document.detected else document.language)
    if document.prettified:
        parts.append("pretty-printed")
    if document.truncated:
        parts.append("truncated")
    return " · ".join(parts)


def _bounded_lines(text: str) -> tuple[list[str], bool]:
    """Split into lines, capped at the document ceiling."""
    lines = text.splitlines()
    if len(lines) > MAX_DOCUMENT_LINES:
        return lines[:MAX_DOCUMENT_LINES], True
    return lines, False


def _ratio(old_line: str, new_line: str) -> float:
    """Similarity of two lines."""
    return SequenceMatcher(None, old_line, new_line, autojunk=False).ratio()


def _char_spans(old_line: str, new_line: str) -> tuple[tuple[Span, ...], tuple[Span, ...]]:
    """Changed character spans on each side of one changed pair."""
    matcher = SequenceMatcher(None, old_line, new_line, autojunk=False)
    old_spans: list[Span] = []
    new_spans: list[Span] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            if i2 > i1:
                old_spans.append((i1, i2))
            if j2 > j1:
                new_spans.append((j1, j2))
    return tuple(old_spans), tuple(new_spans)


def _bounded_spans(spans: tuple[Span, ...], line: str) -> tuple[Span, ...]:
    """Drop emphasis that covers most of a line — it is noise, not signal."""
    if not line:
        return ()
    covered = sum(end - start for start, end in spans)
    if covered > EMPHASIS_COVERAGE_LIMIT * len(line):
        return ()
    return spans


class _DiffBuilder:
    """Accumulates rows and fold regions for one two-sided diff."""

    def __init__(self, old_lines: list[str], new_lines: list[str]) -> None:
        self.old_lines = old_lines
        self.new_lines = new_lines
        self.rows: list[DiffRow] = []
        self.regions: list[FoldRegion] = []

    def equal(self, i1: int, i2: int, j1: int) -> None:
        """Unchanged run; foldable when long enough."""
        start = len(self.rows)
        for offset in range(i2 - i1):
            self.rows.append(
                DiffRow(
                    "context",
                    i1 + offset + 1,
                    j1 + offset + 1,
                    self.old_lines[i1 + offset],
                    self.new_lines[j1 + offset],
                )
            )
        if i2 - i1 >= MIN_FOLDABLE_LINES:
            self.regions.append(FoldRegion(start, len(self.rows) - 1, f"{i2 - i1} unchanged lines"))

    def removes(self, i1: int, i2: int) -> None:
        """Pure removals."""
        for index in range(i1, i2):
            self.rows.append(DiffRow("remove", index + 1, None, self.old_lines[index], ""))

    def adds(self, j1: int, j2: int) -> None:
        """Pure additions."""
        for index in range(j1, j2):
            self.rows.append(DiffRow("add", None, index + 1, "", self.new_lines[index]))

    def change(self, old_index: int, new_index: int) -> None:
        """One genuinely similar pair, with bounded intra-line emphasis."""
        old_line = self.old_lines[old_index]
        new_line = self.new_lines[new_index]
        emphasis_old, emphasis_new = _char_spans(old_line, new_line)
        self.rows.append(
            DiffRow(
                "change",
                old_index + 1,
                new_index + 1,
                old_line,
                new_line,
                _bounded_spans(emphasis_old, old_line),
                _bounded_spans(emphasis_new, new_line),
            )
        )

    def replace(self, i1: int, i2: int, j1: int, j2: int) -> None:
        """Pair only similar lines inside a replace block (Differ-style)."""
        if i1 >= i2:
            self.adds(j1, j2)
            return
        if j1 >= j2:
            self.removes(i1, i2)
            return
        if (i2 - i1) * (j2 - j1) > MAX_PAIRING_AREA:
            self._replace_positional(i1, i2, j1, j2)
            return
        best_ratio, best_i, best_j = CHANGE_CUTOFF, None, None
        for i in range(i1, i2):
            for j in range(j1, j2):
                ratio = _ratio(self.old_lines[i], self.new_lines[j])
                if ratio > best_ratio:
                    best_ratio, best_i, best_j = ratio, i, j
        if best_i is None or best_j is None:
            self.removes(i1, i2)
            self.adds(j1, j2)
            return
        self.replace(i1, best_i, j1, best_j)
        self.change(best_i, best_j)
        self.replace(best_i + 1, i2, best_j + 1, j2)

    def _replace_positional(self, i1: int, i2: int, j1: int, j2: int) -> None:
        """Fallback for oversized blocks: positional pairing, gated by similarity."""
        paired = min(i2 - i1, j2 - j1)
        for offset in range(paired):
            i, j = i1 + offset, j1 + offset
            if _ratio(self.old_lines[i], self.new_lines[j]) >= CHANGE_CUTOFF:
                self.change(i, j)
            else:
                self.removes(i, i + 1)
                self.adds(j, j + 1)
        self.removes(i1 + paired, i2)
        self.adds(j1 + paired, j2)


def build_diff_document(
    old_text: str,
    new_text: str,
    language: str | None = None,
    *,
    detect: bool = True,
    pretty: bool = True,
) -> DiffDocument:
    """Two-sided diff over (optionally prettified) texts."""
    detected = False
    if language is None and detect:
        language = detect_language(new_text) or detect_language(old_text)
        detected = language is not None
    old_display, old_pretty = _prettify(old_text, language) if pretty else (old_text, False)
    new_display, new_pretty = _prettify(new_text, language) if pretty else (new_text, False)
    old_lines, old_cut = _bounded_lines(old_display)
    new_lines, new_cut = _bounded_lines(new_display)
    builder = _DiffBuilder(old_lines, new_lines)
    matcher = SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            builder.equal(i1, i2, j1)
        elif tag == "delete":
            builder.removes(i1, i2)
        elif tag == "insert":
            builder.adds(j1, j2)
        else:
            builder.replace(i1, i2, j1, j2)
    return DiffDocument(
        rows=tuple(builder.rows),
        regions=tuple(builder.regions),
        old_text=old_display,
        new_text=new_display,
        language=language,
        sided=True,
        truncated=old_cut or new_cut,
        detected=detected,
        prettified=old_pretty or new_pretty,
    )


def build_source_document(
    text: str,
    language: str | None = None,
    first_lineno: int = 1,
    *,
    detect: bool = True,
    pretty: bool = True,
) -> DiffDocument:
    """Single-document explorer over (optionally prettified) text.

    Pass pretty=False for revision-pinned sources whose line numbers are
    contractual (authored source windows).
    """
    detected = False
    if language is None and detect:
        language = detect_language(text)
        detected = language is not None
    display, prettified = _prettify(text, language) if pretty else (text, False)
    if prettified:
        first_lineno = 1  # original numbering is meaningless after reformatting
    lines, truncated = _bounded_lines(display)
    rows = tuple(
        DiffRow("context", None, first_lineno + index, "", line) for index, line in enumerate(lines)
    )
    return DiffDocument(
        rows=rows,
        regions=_indent_regions(lines),
        old_text="",
        new_text=display,
        language=language,
        sided=False,
        first_lineno=first_lineno,
        truncated=truncated,
        detected=detected,
        prettified=prettified,
    )


def _indent_regions(lines: list[str]) -> tuple[FoldRegion, ...]:
    """Nested fold regions from indentation structure."""
    meaningful = [
        (index, len(line) - len(line.lstrip())) for index, line in enumerate(lines) if line.strip()
    ]
    regions: list[FoldRegion] = []
    stack: list[tuple[int, int]] = []
    for position, (index, indent) in enumerate(meaningful):
        while stack and indent <= stack[-1][1]:
            start, _ = stack.pop()
            end = meaningful[position - 1][0]
            if end - start >= MIN_FOLDABLE_LINES - 1:
                regions.append(FoldRegion(start, end, f"{end - start} lines", depth=len(stack)))
        nxt = meaningful[position + 1] if position + 1 < len(meaningful) else None
        if nxt is not None and nxt[1] > indent:
            stack.append((index, indent))
    while stack:
        start, _ = stack.pop()
        end = meaningful[-1][0]
        if end - start >= MIN_FOLDABLE_LINES - 1:
            regions.append(FoldRegion(start, end, f"{end - start} lines", depth=len(stack)))
    return tuple(sorted(regions, key=lambda region: (region.start, -region.end)))


def project_visible(
    row_count: int, regions: tuple[FoldRegion, ...], folds: frozenset[int]
) -> tuple[VisibleEntry, ...]:
    """Visible entries with outermost-wins folding."""
    closed_by_start: dict[int, list[int]] = {}
    for index, region in enumerate(regions):
        if index in folds:
            closed_by_start.setdefault(region.start, []).append(index)
    entries: list[VisibleEntry] = []
    row = 0
    while row < row_count:
        closed = closed_by_start.get(row)
        if closed:
            outermost = max(closed, key=lambda index: regions[index].end)
            entries.append(("fold", outermost))
            row = regions[outermost].end + 1
        else:
            entries.append(("row", row))
            row += 1
    return tuple(entries)


def region_at(regions: tuple[FoldRegion, ...], row: int) -> int | None:
    """Region starting at a row, else the innermost region containing it."""
    starting = [i for i, r in enumerate(regions) if r.start == row]
    if starting:
        return max(starting, key=lambda i: regions[i].end)
    containing = [i for i, r in enumerate(regions) if r.start <= row <= r.end]
    if containing:
        return max(containing, key=lambda i: regions[i].start)
    return None
