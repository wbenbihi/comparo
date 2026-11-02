"""Selected saved-run preview, separate from the existing full Analyze surface."""

from typing import override

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.containers import VerticalScroll
from textual.content import Content

from comparo.application import ReportArchiveEntry
from comparo.application import ResultProjection
from comparo.tui.components.actionbar import Action
from comparo.tui.components.actionbar import ActionBar
from comparo.tui.components.list import Field
from comparo.tui.components.list import KeyValueList
from comparo.tui.components.separator import Separator
from comparo.tui.components.status import duration
from comparo.tui.components.status import status_text
from comparo.tui.components.typography import Caption
from comparo.tui.models.reports import literal
from comparo.tui.models.reports import profile_label
from comparo.tui.models.reports import report_issues
from comparo.tui.models.reports import report_status
from comparo.tui.models.reports import report_time
from comparo.tui.models.reports import report_topology
from comparo.tui.models.reports import resource_label


class ReportPreview(Vertical):
    """Outcome, causes, provenance and actions for the highlighted archive entry."""

    @override
    def compose(self) -> ComposeResult:
        """Keep actions fixed while long report metadata scrolls as one surface."""
        with VerticalScroll():
            yield Caption("Select a saved run", id="report-outcome", markup=False)
            yield Caption("", id="report-gate", markup=False)
            yield Separator()
            yield Caption("Top issues", classes="report-section")
            yield Caption("", id="report-issues", markup=False)
            yield Separator()
            yield KeyValueList(id="report-metadata")
        yield Caption("Saved results · read-only", id="report-readonly")
        yield ActionBar(id="report-actions")

    def show_entry(
        self,
        entry: ReportArchiveEntry | None,
        projection: ResultProjection | None = None,
        *,
        error: str = "",
    ) -> None:
        """Render authenticated metadata, then the selected manifest's exact issue groups."""
        self.query_one("#report-actions", ActionBar).actions = (
            Action(
                "open",
                "Open analysis (read-only)",
                key="Enter",
                enabled=projection is not None,
                primary=True,
            ),
            Action("export", "Export report", key="e", enabled=projection is not None),
            Action("copy", "Copy report ID", key="c", enabled=entry is not None),
        )
        if entry is None:
            self.query_one("#report-outcome", Caption).update("Select a saved run")
            self.query_one("#report-gate", Caption).update("")
            self.query_one("#report-issues", Caption).update("")
            self.query_one(KeyValueList).entries = ()
            return
        title = status_text(report_status(entry))
        title.append(f" · {entry.executions} calls · {entry.comparisons} comparisons")
        self.query_one("#report-outcome", Caption).update(title)
        gate = projection.summary.gate_profile if projection else None
        self.query_one("#report-gate", Caption).update(
            f"Gate: {entry.gate}" + (f" · {gate.resource_id}" if gate else "")
        )
        self.query_one("#report-issues", Caption).update(
            report_issues(projection) if projection else (error or "Loading recorded issues…")
        )
        created = report_time(entry)
        rows = (
            ("Profile", profile_label(entry)),
            (
                "Topology" if projection else "Environments",
                report_topology(projection)
                if projection
                else " + ".join(resource_label(item) for item in entry.environments),
            ),
            ("Started", created.strftime("%b %d, %Y %H:%M:%S %Z") if created else "Not available"),
            ("Duration", duration(entry.duration_nanoseconds)),
            (
                "Commit",
                f"{entry.vcs.revision[:12]} {entry.vcs.branch or ''}"
                if entry.vcs
                else "Not recorded",
            ),
            ("Title", entry.title or "—"),
            ("Tags", ", ".join(entry.tags) or "—"),
            ("Report ID", entry.bundle_id),
            ("Run ID", entry.run_id),
            ("Captures", f"{entry.blobs} blobs · {entry.blob_bytes / 1024:.1f} KiB"),
            (
                "Integrity",
                "Verification failed"
                if error
                else ("Manifest verified" if projection else "Verifying manifest…"),
            ),
        )
        self.query_one(KeyValueList).entries = tuple(
            Field(key, Content("\n".join(literal(line) for line in value.splitlines())))
            for key, value in rows
        )
