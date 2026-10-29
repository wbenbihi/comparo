"""Literal archive presentation and filters over authenticated report metadata."""

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta

from rich.text import Text

from comparo.application import ReportArchiveEntry
from comparo.application import ReportArchiveResource
from comparo.application import ResultProjection
from comparo.application.result_analysis import ResultProjectionWindow
from comparo.application.result_analysis import project_issues
from comparo.tui4.components.status import duration
from comparo.tui4.components.status import status_text
from comparo.tui4.theme import style


def literal(value: str) -> str:
    """Keep labels literal and suppress terminal and bidi control characters."""
    return "".join(char if char.isprintable() else "�" for char in value)


def resource_label(resource: ReportArchiveResource) -> str:
    """Keep namespaces when they distinguish authored resources."""
    return (
        resource.resource_id
        if resource.namespace == "default"
        else (f"{resource.namespace}/{resource.resource_id}")
    )


def report_status(entry: ReportArchiveEntry) -> str:
    """Display completion separately from an explicitly recorded release gate."""
    return (
        entry.gate
        if entry.completion == "complete" and entry.gate != "not-requested"
        else entry.completion
    )


def profile_label(entry: ReportArchiveEntry) -> str:
    """Identify ad-hoc runs explicitly instead of inventing a saved profile."""
    return resource_label(entry.execution_profile) if entry.execution_profile else "Ad-hoc run"


def report_time(entry: ReportArchiveEntry) -> datetime | None:
    """Convert recorded timestamps to local time without assuming platform date range."""
    try:
        return datetime.fromtimestamp(entry.created_unix_nanoseconds / 1_000_000_000).astimezone()
    except (OverflowError, OSError, ValueError):
        return None


def date_group(entry: ReportArchiveEntry, now: datetime) -> str:
    """Group the archive by local calendar days, newest first."""
    created = report_time(entry)
    if created is None:
        return "Unknown date"
    days = (now.date() - created.date()).days
    if days == 0:
        return "Today · " + created.strftime("%b %d, %Y")
    if days == 1:
        return "Yesterday · " + created.strftime("%b %d, %Y")
    return "Last 7 days" if 1 < days < 7 else created.strftime("%B %Y")


@dataclass(frozen=True)
class ArchiveFilters:
    """One local filter over the bounded, authenticated archive listing."""

    text: str = ""
    status: str = "all"
    profile: str = "all"
    environment: str = "all"
    date: str = "all"
    tag: str = "all"

    def matches(self, entry: ReportArchiveEntry, now: datetime) -> bool:
        """Match metadata only; no run outcomes or body contents are evaluated here."""
        haystack = " ".join(
            (
                entry.run_id,
                entry.bundle_id,
                entry.title or "",
                profile_label(entry),
                *entry.tags,
                *(resource_label(item) for item in entry.subjects),
                *(resource_label(item) for item in entry.environments),
                entry.vcs.revision if entry.vcs else "",
                entry.vcs.branch or "" if entry.vcs else "",
            )
        ).casefold()
        categorical = (
            self.text.casefold() in haystack
            and (self.status == "all" or report_status(entry) == self.status)
            and (self.profile == "all" or profile_label(entry) == self.profile)
            and (
                self.environment == "all"
                or self.environment in {resource_label(item) for item in entry.environments}
            )
            and (self.tag == "all" or self.tag in entry.tags)
        )
        if not categorical or self.date == "all":
            return categorical
        created = report_time(entry)
        days = {"today": 0, "7": 6, "30": 29}[self.date]
        return created is not None and (
            now.date() - timedelta(days=days) <= created.date() <= now.date()
        )


def archive_columns(width: int) -> tuple[tuple[str, int], ...]:
    """Fit the same archive row to its actual available width."""
    if width >= 97:
        return (
            ("Status", 12),
            ("Run", 15),
            ("When", 11),
            ("Profile", width - 85),
            ("Environments", 15),
            ("Calls", 6),
            ("Issues", 7),
            ("Duration", 9),
            ("Commit", 10),
        )
    if width >= 66:
        return (
            ("Status", 12),
            ("Run / profile", width - 47),
            ("When", 11),
            ("Calls", 6),
            ("Issues", 7),
            ("Duration", 11),
        )
    return (("Status", 12), ("Run / profile", max(12, width - 24)), ("When", 12))


def archive_row(entry: ReportArchiveEntry, width: int) -> Text:
    """Render two aligned literal lines with the shared status/color grammar."""
    created = report_time(entry)
    issues = entry.adverse_assertions + entry.adverse_diffs
    cells: dict[str, tuple[Text, Text]] = {
        "Status": (status_text(report_status(entry)), Text("")),
        "Run": (Text(entry.run_id[:12], style("bright")), Text(entry.bundle_id[:13])),
        "Run / profile": (
            Text(literal(entry.title or entry.run_id[:12]), style("bright")),
            Text(literal(profile_label(entry))),
        ),
        "When": (
            Text(created.strftime("%b %d") if created else "Unknown"),
            Text(created.strftime("%H:%M:%S") if created else ""),
        ),
        "Profile": (Text(literal(profile_label(entry))), Text(literal(entry.title or ""))),
        "Environments": (
            Text(" + ".join(resource_label(item) for item in entry.environments[:2])),
            Text(f"+{len(entry.environments) - 2} more" if len(entry.environments) > 2 else ""),
        ),
        "Calls": (Text(str(entry.executions)), Text("")),
        "Issues": (Text(str(issues), style("err" if issues else "ok")), Text("facts")),
        "Duration": (Text(duration(entry.duration_nanoseconds)), Text("")),
        "Commit": (
            Text(literal(entry.vcs.revision[:8]) if entry.vcs else "—"),
            Text(literal(entry.vcs.branch or "") if entry.vcs else ""),
        ),
    }
    result = Text()
    for line in range(2):
        if line:
            result.append("\n")
        for name, size in archive_columns(width):
            cell = cells[name][line].copy()
            cell.truncate(size - 1, overflow="ellipsis", pad=True)
            if line:
                cell.stylize(style("muted"))
            result.append_text(cell)
            result.append(" ")
    return result


def report_issues(projection: ResultProjection) -> Text:
    """Preview recorded issue groups and operational failures, preserving exact counts."""
    result = Text()
    issues = project_issues(projection, ResultProjectionWindow(maximum_items=3))
    for index, item in enumerate(issues.items, 1):
        result.append(f"{index}. ", style("muted"))
        heading = {
            "drifted": "Responses differ",
            "violated": "Assertion failed",
            "error": "Evaluation error",
        }.get(item.outcome)
        if heading:
            result.append(heading + "\n   ", style("bright"))
        result.append(literal(item.rule_id), style("bright"))
        result.append("\n   ")
        result.append_text(status_text(item.outcome))
        result.append(
            f" · {item.severity}\n   {item.occurrences} occurrences"
            f" · {item.affected_subjects} requests"
            f" · {item.affected_cells} cases\n\n",
            style("muted"),
        )
    if issues.omitted:
        result.append(f"+{issues.omitted} more issue groups in Analyze\n", style("muted"))
    failures = tuple(row for row in projection.executions if row.failure is not None)
    if failures:
        result.append(f"! {len(failures)} execution failures\n", style("error-op"))
        for row in failures[:2]:
            if row.failure is not None:
                result.append(
                    f"  {row.subject.resource_id} · {row.environment.resource_id}: "
                    f"{row.failure.code}\n"
                )
    if not issues.items and not failures:
        result.append("No retained issues.\n", style("ok"))
    result.append(f"{projection.summary.coverage_issues} coverage issues", style("muted"))
    return result


def report_topology(projection: ResultProjection) -> str:
    """Use explicit comparison links; environment sort order is never a topology."""
    executions = {row.reference: row for row in projection.executions}
    pairs = dict.fromkeys(
        f"{executions[row.baseline].environment.resource_id} → "
        f"{executions[row.candidate].environment.resource_id}"
        for row in projection.comparisons
        if row.baseline in executions and row.candidate in executions
    )
    if pairs:
        return "\n".join(pairs)
    return "Assertions only · " + ", ".join(
        dict.fromkeys(row.environment.resource_id for row in projection.executions)
    )
