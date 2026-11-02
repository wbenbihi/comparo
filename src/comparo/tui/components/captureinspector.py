"""Shared capture inspector: paged bytes, JSON paths, SSE events, and reviewed cURL."""

from typing import ClassVar
from typing import override

from textual import on
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.binding import BindingType
from textual.content import Content
from textual.screen import ModalScreen

from comparo.application import ArtifactBodyMessage
from comparo.application import ArtifactBodyReference
from comparo.application.curl_export import CurlExport
from comparo.application.structured_bodies import StructuredBodyFormat
from comparo.application.structured_bodies import StructuredBodyPage
from comparo.application.structured_bodies import StructuredBodyQuery
from comparo.application.structured_bodies import StructuredJsonPage
from comparo.application.structured_bodies import StructuredSseDataFormat
from comparo.application.structured_bodies import StructuredSsePage
from comparo.tui.components.actionbar import Action
from comparo.tui.components.actionbar import ActionBar
from comparo.tui.components.diffview import DiffView
from comparo.tui.components.menu import MenuItem
from comparo.tui.components.panel import Panel
from comparo.tui.components.selectfield import SelectField
from comparo.tui.components.table import Column
from comparo.tui.components.table import Record
from comparo.tui.components.table import Table
from comparo.tui.components.typography import Caption
from comparo.tui.evidence_access import BODY_PAGE_BYTES
from comparo.tui.evidence_access import EvidenceAccess
from comparo.tui.models.diff import build_source_document
from comparo.tui.theme import style


def literal(text: str) -> str:
    """Contain terminal controls while retaining evidence line breaks."""
    return "".join(char if char.isprintable() or char in "\n\t" else "�" for char in text)


class CaptureInspector(ModalScreen[None]):
    """Inspect the selected capture without losing its parent Analyze navigation."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Back to Analyze"),
        Binding("n", "next_page", "Next page"),
        Binding("p", "previous_page", "Previous page"),
        Binding("b", "up", "Parent"),
    ]
    DEFAULT_CSS = """
    CaptureInspector { align: center middle; background: $background 90%; }
    CaptureInspector > Panel { width: 98%; height: 96%; }
    CaptureInspector Caption { height: auto; }
    CaptureInspector SelectField { height: 3; }
    #capture-content, #capture-structure { height: 1fr; }
    #capture-context { text-style: bold; margin: 1 0; }
    #capture-status { margin: 1 0; }
    """

    def __init__(
        self, access: EvidenceAccess, captures: tuple[tuple[str, ArtifactBodyReference], ...]
    ) -> None:
        """Freeze the exact selected message coordinates and archive authority."""
        super().__init__()
        self.access = access
        self.captures = captures
        self.index = next(
            (
                i
                for i, (_, ref) in enumerate(captures)
                if ref.message is ArtifactBodyMessage.RESPONSE
            ),
            0,
        )
        self.mode = "raw"
        self.start = 0
        self.next_start: int | None = None
        self.pointer = ""
        self.event_index: int | None = None
        self.parents: list[tuple[str, int | None, int]] = []
        self.page: StructuredBodyPage | None = None
        self.curl_export: CurlExport | None = None
        self.busy = False
        self.generation = 0

    @override
    def compose(self) -> ComposeResult:
        yield Panel(
            Caption("Stored HTTP evidence", id="capture-context"),
            SelectField(
                "Message",
                tuple(
                    MenuItem(str(index), label) for index, (label, _) in enumerate(self.captures)
                ),
                value=str(self.index),
                id="capture-message",
            ),
            ActionBar(
                (
                    Action("raw", "Raw text"),
                    Action("json", "JSON paths"),
                    Action("sse", "SSE events"),
                    Action("curl", "Review request cURL"),
                ),
                id="capture-modes",
            ),
            Caption("", id="capture-location", markup=False),
            DiffView(id="capture-content"),
            Table(
                columns=(
                    Column("path", "Path / event", 32),
                    Column("type", "Type", 12),
                    Column("value", "Value / data", 64),
                ),
                id="capture-structure",
            ),
            Caption("", id="capture-status", markup=False),
            ActionBar(id="capture-actions"),
            title="CAPTURE INSPECTOR",
        )

    def on_mount(self) -> None:
        """Read only after the user explicitly opens the inspector."""
        self._reload()

    def _status(self, text: str) -> None:
        self.query_one("#capture-status", Caption).update(text)

    def _actions(self) -> None:
        paging = (
            Action("previous", "Previous", key="p", enabled=not self.busy and self.start > 0),
            Action("next", "Next", key="n", enabled=not self.busy and self.next_start is not None),
            Action("up", "Parent", key="b", enabled=not self.busy and bool(self.parents)),
        )
        copying = (
            Action(
                "copy",
                "Copy sanitized cURL",
                enabled=not self.busy and self.curl_export is not None,
            ),
        )
        self.query_one("#capture-actions", ActionBar).actions = (
            *(copying if self.mode == "curl" else paging),
            Action("close", "Back to Analyze", key="Esc"),
        )
        self.query_one("#capture-modes", ActionBar).actions = tuple(
            Action(key, title, primary=self.mode == key)
            for key, title in (
                ("raw", "Raw text"),
                ("json", "JSON paths"),
                ("sse", "SSE events"),
                ("curl", "Review request cURL"),
            )
        )

    def _reload(self) -> None:
        self.generation += 1
        self.busy = True
        self.next_start = None
        self.page = None
        self.curl_export = None
        self.query_one(DiffView).document = None
        self.query_one(Table).records = ()
        self.query_one(DiffView).display = self.mode in {"raw", "curl"}
        self.query_one(Table).display = self.mode in {"json", "sse"}
        self.query_one("#capture-location", Caption).update(
            f"{literal(self.captures[self.index][0])} · {self.mode.upper()}"
            + (
                f" · {self.pointer or '/'}"
                if self.mode == "json" or self.event_index is not None
                else ""
            )
            + (f" · event {self.event_index + 1}" if self.event_index is not None else "")
        )
        self._status("Loading authenticated evidence…")
        self._actions()
        self._load(self.generation)

    @work(exclusive=True, group="capture-read")
    async def _load(self, generation: int) -> None:
        reference = self.captures[self.index][1]
        try:
            if self.mode == "raw":
                window = await self.access.body(reference, self.start)
                if generation != self.generation or not self.app.is_running:
                    return
                self.next_start = window.next_offset
                self.query_one(DiffView).document = build_source_document(
                    literal(window.content.decode("utf-8", errors="replace"))
                )
                self._status(
                    f"Bytes {self.start}-{self.start + len(window.content)} "
                    f"of {window.total_bytes} · "
                    f"Capture: {window.source_state} · body: {window.extent} · "
                    f"{window.redaction_count} redactions"
                    + (f" · {window.reason}" if window.reason else "")
                )
            elif self.mode == "curl":
                exported = await self.access.curl(reference)
                if generation != self.generation or not self.app.is_running:
                    return
                self.curl_export = exported
                self.query_one(DiffView).document = build_source_document(literal(exported.command))
                warnings = ", ".join(w.value.replace("-", " ") for w in exported.warnings) or "none"
                replay = (
                    "Executable command" if exported.executable else "Incomplete request template"
                )
                fidelity = (
                    "Faithful to the sanitized capture"
                    if exported.exact_for_sanitized_capture
                    else "Replay differs from the sanitized capture"
                )
                self._status(
                    f"{replay} · {fidelity}\n"
                    f"Warnings: {warnings} · {exported.redaction_count} redactions. "
                    "Review before copying: clipboard may contain sanitized business data. "
                    "This preview expires after 60 seconds."
                )
                self.set_timer(60, lambda: self._expire(generation))
            else:
                page = await self.access.structure(
                    reference,
                    StructuredBodyQuery(
                        format=StructuredBodyFormat(self.mode),
                        start=self.start,
                        maximum_items=50,
                        pointer=self.pointer,
                        sse_event_index=self.event_index,
                    ),
                )
                if generation != self.generation or not self.app.is_running:
                    return
                self.page = page
                self.next_start = page.next_start
                self._show_page(page)
        except Exception:  # noqa: BLE001 - body, filesystem, and parser payloads never become errors
            if generation == self.generation and self.app.is_running:
                self._status(
                    "Evidence unavailable, unsupported format, or read limit reached. "
                    "Raw text supports byte paging; structured views accept captures up to 4 MiB."
                )
        finally:
            if generation == self.generation and self.is_mounted and self.app.is_running:
                self.busy = False
                self._actions()

    def _show_page(self, page: StructuredBodyPage) -> None:
        records: list[Record[str]] = []
        if isinstance(page, StructuredJsonPage):
            records = [
                Record(
                    str(node.ordinal),
                    (
                        Content(literal(node.pointer or "/")),
                        Content.styled(node.kind.value, style("muted")),
                        Content(literal(node.preview) + (" …" if node.preview_truncated else "")),
                    ),
                )
                for node in page.nodes
            ]
            total = page.total_nodes
            suffix = "Enter an object/array to inspect its subtree; b returns to its parent."
        else:
            records = [
                Record(
                    str(event.ordinal),
                    (
                        Content(
                            f"{event.ordinal + 1}. {literal(event.event_type)} · "
                            f"id {literal(event.event_id)}"
                        ),
                        Content.styled(event.data_format.value, style("muted")),
                        Content(
                            literal(event.data_preview) + (" …" if event.preview_truncated else "")
                        ),
                    ),
                )
                for event in page.events
            ]
            total = page.total_events
            suffix = (
                "Incomplete trailing event omitted. " if page.trailing_incomplete_event else ""
            ) + "Enter a JSON event to inspect its fields; b returns to the event list."
        self.query_one(Table).records = tuple(records)
        self.query_one(Table).fit_proportions((0.35, 0.13, 0.52))
        source = page.source
        self._status(
            f"Items {self.start + 1 if total else 0}-{self.start + len(records)} of {total} · "
            f"Capture: {source.source_state} · body: {source.extent} · "
            f"{source.redaction_count} redactions\n{suffix}"
        )

    @on(SelectField.Changed, "#capture-message")
    def _message(self, event: SelectField.Changed) -> None:
        event.stop()
        self.index = int(event.value)
        self.start, self.pointer, self.event_index = 0, "", None
        self.parents.clear()
        self._reload()

    @on(Table.RowSelected)
    def _drill(self, event: Table.RowSelected[str]) -> None:
        event.stop()
        if self.busy or self.page is None:
            return
        selected = int(event.row)
        if isinstance(self.page, StructuredJsonPage):
            node = next(node for node in self.page.nodes if node.ordinal == selected)
            if not node.child_count or node.pointer == self.pointer:
                return
            self.parents.append((self.pointer, self.event_index, self.start))
            self.pointer, self.start = node.pointer, 0
        elif isinstance(self.page, StructuredSsePage):
            item = next(item for item in self.page.events if item.ordinal == selected)
            if item.data_format is not StructuredSseDataFormat.JSON:
                return
            self.parents.append((self.pointer, self.event_index, self.start))
            self.event_index, self.pointer, self.start = item.ordinal, "", 0
        self._reload()

    @on(ActionBar.Requested)
    def _action(self, event: ActionBar.Requested) -> None:
        event.stop()
        if event.action in {"raw", "json", "sse", "curl"}:
            self.mode = event.action
            self.start, self.pointer, self.event_index = 0, "", None
            self.parents.clear()
            self._reload()
        elif event.action == "next":
            self.action_next_page()
        elif event.action == "previous":
            self.action_previous_page()
        elif event.action == "up":
            self.action_up()
        elif event.action == "close":
            self.action_close()
        elif event.action == "copy" and self.curl_export is not None:
            self.app.copy_to_clipboard(self.curl_export.command)
            self._status("Sanitized cURL copied. The system clipboard remains under your control.")

    def _expire(self, generation: int) -> None:
        if generation == self.generation and self.is_mounted and self.mode == "curl":
            self.curl_export = None
            self.query_one(DiffView).document = None
            self._status("cURL preview expired. Select Review request cURL to regenerate it.")
            self._actions()

    def action_next_page(self) -> None:
        """Replace the current bounded window with its authenticated continuation."""
        if not self.busy and self.next_start is not None:
            self.start = self.next_start
            self._reload()

    def action_previous_page(self) -> None:
        """Read the preceding window without retaining old payloads."""
        if not self.busy and self.start:
            self.start = max(0, self.start - (BODY_PAGE_BYTES if self.mode == "raw" else 50))
            self._reload()

    def action_up(self) -> None:
        """Restore the exact parent page of a JSON subtree or SSE event."""
        if not self.busy and self.parents:
            self.pointer, self.event_index, self.start = self.parents.pop()
            self._reload()

    def action_close(self) -> None:
        """Clear transient command text and restore the unchanged Analyze view."""
        self.generation += 1
        self.curl_export = None
        self.dismiss(None)
