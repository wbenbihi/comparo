"""Launch summaries and plan review assembled from shared TUI display components."""

from dataclasses import dataclass
from typing import override
from urllib.parse import urlsplit

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.containers import VerticalScroll
from textual.content import Content
from textual.reactive import reactive

from comparo.application import CompareIntent
from comparo.application import PlanReviewInspection
from comparo.application import RenderPreviewWindowResult
from comparo.tui.components.disclosure import Disclosure
from comparo.tui.components.list import Field
from comparo.tui.components.list import KeyValueList
from comparo.tui.components.panel import Panel
from comparo.tui.components.resourcelabel import resource_label
from comparo.tui.components.reviewsection import ReviewSection
from comparo.tui.components.separator import Separator
from comparo.tui.components.status import duration
from comparo.tui.components.table import Column
from comparo.tui.components.table import Record
from comparo.tui.components.table import Table
from comparo.tui.components.typography import Caption
from comparo.tui.components.typography import Hint
from comparo.tui.models.explorer import ExplorerCatalog
from comparo.tui.models.explorer import ResourceEntry
from comparo.tui.models.launch import LaunchDraft


@dataclass(frozen=True, slots=True)
class SummarySection:
    """One titled group of safe facts with optional explanatory copy."""

    title: str
    fields: tuple[tuple[str, str | Content], ...] = ()
    note: str = ""


def _fields(values: tuple[tuple[str, str | Content], ...]) -> tuple[Field, ...]:
    """Prevent authored names from being interpreted as Rich or Textual markup."""
    return tuple(
        Field(key=key, value=Content(value) if isinstance(value, str) else value)
        for key, value in values
    )


def override_fields(draft: LaunchDraft) -> tuple[tuple[str, str | Content], ...]:
    """List only actual session overrides, including explicit cleared profiles."""
    fields: list[tuple[str, str | Content]] = []
    for label, value in (
        ("Trials / cell", draft.trials),
        ("Attempts / call", draft.maximum_attempts),
    ):
        if value:
            fields.append((label, value))
    if draft.fail_fast is not None:
        fields.append(("Stop early", "Yes" if draft.fail_fast else "No"))
    fields.extend((item.binding, ", ".join(item.cases)) for item in draft.matrices)
    for label, profiles in (("Assertions", draft.assertions), ("Diffs", draft.diffs)):
        if profiles is not None:
            fields.append((label, ", ".join(profiles) or "None"))
    if draft.gate is not None:
        fields.append(("Gate", "None" if draft.gate == "@none" else draft.gate))
    if draft.approve_mutations:
        fields.append(("Mutations", "Approved for this run"))
    return tuple(fields)


def draft_sections(
    draft: LaunchDraft, catalog: ExplorerCatalog | None
) -> tuple[SummarySection, ...]:
    """Keep the narrow scope readable while effective counts are being prepared."""
    del catalog
    saved = draft.execution_intent is CompareIntent.EXECUTION_PROFILE
    fields = (
        (("Profile", draft.profile or "Choose a profile"),)
        if saved
        else (("Requests", str(len(draft.subjects))),)
    )
    return (
        SummarySection(
            "Scope", fields, "Select a valid scope." if draft.problems else "Calculating expansion…"
        ),
        SummarySection(
            "Session overrides",
            override_fields(draft),
            "" if override_fields(draft) else "None · using defaults",
        ),
    )


def preview_sections(
    draft: LaunchDraft, review: PlanReviewInspection
) -> tuple[SummarySection, ...]:
    """Use exact planner totals and short identities in the Target summary."""
    plan = review.plan
    baseline = tuple(
        dict.fromkeys(
            pair.baseline.resource_id for item in plan.slices for pair in item.comparisons
        )
    )
    candidates = tuple(
        dict.fromkeys(
            pair.candidate.resource_id for item in plan.slices for pair in item.comparisons
        )
    )
    topology = (
        (("Baseline", ", ".join(baseline)), ("Candidates", ", ".join(candidates)))
        if baseline
        else (
            (
                "Environments",
                ", ".join(
                    dict.fromkeys(
                        env.resource_id for item in plan.slices for env in item.environments
                    )
                ),
            ),
        )
    )
    return (
        SummarySection(
            "Scope",
            (
                ("Requests", str(len(plan.slices))),
                ("Matrix cells", str(plan.cardinality.cells)),
                *topology,
                ("Planned calls", str(plan.cardinality.environment_executions)),
                ("Comparisons", str(plan.cardinality.comparisons)),
            ),
        ),
        SummarySection(
            "Session overrides",
            override_fields(draft),
            "" if override_fields(draft) else "None · using defaults",
        ),
    )


def _entry(catalog: ExplorerCatalog | None, kind: str, identity: str) -> ResourceEntry | None:
    """Resolve a catalog identity without reading authored values or secrets."""
    if catalog is None:
        return None
    return next(
        (
            entry
            for entry in catalog.entries.values()
            if entry.summary.coordinate.kind == kind
            and entry.summary.coordinate.resource_id == identity
        ),
        None,
    )


class SummaryContents(Vertical):
    """Replace summary facts inside a stable scrolling and focus container."""

    sections: reactive[tuple[SummarySection, ...]] = reactive((), recompose=True)
    DEFAULT_CSS = "SummaryContents { height: auto; }"

    def __init__(self, sections: tuple[SummarySection, ...]) -> None:
        """Initialize safe summary facts."""
        super().__init__()
        self.set_reactive(SummaryContents.sections, sections)

    @override
    def compose(self) -> ComposeResult:
        """Use aligned field lists and semantic typography instead of a text dump."""
        for section in self.sections:
            yield Separator(section.title, align="left")
            if section.fields:
                yield KeyValueList(*_fields(section.fields))
            if section.note:
                yield Hint(section.note, markup=False)


class ScopeContents(SummaryContents):
    """Scope rail that changes its information layout when moved below the form."""

    compact: reactive[bool] = reactive(False, recompose=True)

    @override
    def compose(self) -> ComposeResult:
        """Use a tall rail or horizontal summary, without shrinking one into the other."""
        if self.compact:
            yield from self._compact_summary()
            return
        for section in self.sections:
            yield Caption(section.title.upper(), classes="scope-section", markup=False)
            for label, value in section.fields:
                if label:
                    yield Caption(label, classes="scope-label", markup=False)
                yield Caption(value, classes="scope-value", markup=False)
            if section.note:
                yield Hint(section.note, markup=False)

    def _compact_summary(self) -> ComposeResult:
        topology_labels = {"Baseline", "Candidates", "Environments"}
        names = {
            "Matrix cells": "cells",
            "Requests": "requests",
            "Planned calls": "calls",
            "Comparisons": "comparisons",
        }
        for section in self.sections:
            if section.title == "Scope":
                counts = " · ".join(
                    f"{value} {names.get(label, label.lower())}"
                    for label, value in section.fields
                    if label not in topology_labels
                )
                yield Caption(
                    counts or "Scope not selected", classes="scope-inline-counts", markup=False
                )
                topology = dict(section.fields)
                if "Baseline" in topology:
                    yield Caption(
                        f"{topology['Baseline']} → {topology.get('Candidates', '—')}", markup=False
                    )
                elif "Environments" in topology:
                    yield Caption(f"Environments: {topology['Environments']}", markup=False)
                if section.note:
                    yield Hint(section.note, markup=False)
            else:
                overrides = " · ".join(f"{label}: {value}" for label, value in section.fields)
                yield Caption("Overrides: " + (overrides or section.note), markup=False)


class LaunchSummary(Panel):
    """Reusable section composition for draft scope, progress, and final outcome."""

    sections: reactive[tuple[SummarySection, ...]] = reactive(())
    compact: reactive[bool] = reactive(False)

    def __init__(self, *, id: str | None = None) -> None:
        """Initialize the right-hand summary panel."""
        super().__init__(title="Scope summary", id=id)

    @override
    def compose(self) -> ComposeResult:
        """Keep the focus owner mounted when progress updates change its content."""
        with VerticalScroll():
            contents = ScopeContents(self.sections)
            contents.set_reactive(ScopeContents.compact, self.compact)
            yield contents

    def watch_compact(self) -> None:
        """Switch composition while preserving the summary's stable scrolling owner."""
        if self.is_mounted:
            self.query_one(ScopeContents).compact = self.compact

    def watch_sections(self) -> None:
        """Project new facts without resetting scroll or keyboard focus."""
        if self.is_mounted:
            self.query_one(SummaryContents).sections = self.sections

    @override
    def focus(self, scroll_visible: bool = True) -> "LaunchSummary":
        """Keep scroll and workspace bindings available from the summary region."""
        self.query_one(VerticalScroll).focus(scroll_visible)
        return self


class LaunchReview(Panel):
    """Immutable admitted-plan view using the same tables and facts as inspectors."""

    catalog: ExplorerCatalog | None = None
    draft = LaunchDraft()
    destinations: RenderPreviewWindowResult | None = None

    review: reactive[PlanReviewInspection | None] = reactive(None, recompose=True)

    def __init__(self, *, id: str | None = None) -> None:
        """Initialize an empty review checkpoint."""
        super().__init__(title="Review plan", id=id)

    @override
    def compose(self) -> ComposeResult:
        """Use the reference's numbered full-width review rows and concise effective facts."""
        review = self.review
        if review is None:
            return
        plan = review.plan
        count = plan.cardinality
        destinations = tuple(
            dict.fromkeys(env.resource_id for item in plan.slices for env in item.environments)
        )
        baseline = {pair.baseline.resource_id for item in plan.slices for pair in item.comparisons}
        candidates = {
            pair.candidate.resource_id for item in plan.slices for pair in item.comparisons
        }
        with VerticalScroll():
            yield Caption(
                f"{len(plan.slices)} targets  ·  {count.cells} cells  ·  "
                f"{count.environment_executions} executions  ·  {count.comparisons} comparisons",
                classes="launch-plan-strip",
                markup=False,
            )
            destination_table = Table[str](
                columns=(
                    Column("role", "Role", 12),
                    Column("environment", "Environment"),
                    Column("host", "Host (preview)"),
                    Column("methods", "Methods"),
                    Column("calls", "Calls", 6),
                ),
                records=tuple(
                    Record(
                        identity,
                        (
                            "BASELINE"
                            if identity in baseline
                            else "CANDIDATE"
                            if identity in candidates
                            else "ASSERTIONS",
                            Content(identity),
                            self._hosts(identity),
                            Content(
                                ", ".join(
                                    dict.fromkeys(
                                        item.method or "Dynamic"
                                        for item in plan.slices
                                        if any(
                                            env.resource_id == identity for env in item.environments
                                        )
                                    )
                                )
                            ),
                            str(
                                sum(
                                    item.cardinality.environment_executions
                                    // len(item.environments)
                                    for item in plan.slices
                                    if any(env.resource_id == identity for env in item.environments)
                                )
                            ),
                        ),
                    )
                    for identity in destinations
                ),
            )
            destination_table.styles.height = len(destinations) + 1
            self._static_table(destination_table)
            sample = self.destinations
            yield ReviewSection(
                "1. Destinations",
                destination_table,
                Hint(
                    f"Sanitized sample: {len(sample.previews)} / "
                    f"{sample.total_executions} planned calls."
                    + (" Some destinations could not be resolved." if sample.diagnostics else "")
                    if sample is not None
                    else "Destination preview unavailable; policy boundaries are shown below."
                ),
            )
            scope_table = Table[str](
                columns=(
                    Column("request", "Request / method / route"),
                    Column("cells", "Cells", 6),
                    Column("trials", "Trials", 6),
                    Column("calls", "Executions", 10),
                ),
                records=tuple(
                    Record(
                        str(index),
                        (
                            self._request_label(item.subject.resource_id),
                            str(item.cardinality.cells),
                            str(item.trials),
                            str(item.cardinality.environment_executions),
                        ),
                    )
                    for index, item in enumerate(plan.slices)
                ),
                id="launch-plan-table",
            )
            scope_table.styles.height = min(len(plan.slices) + 1, 8)
            self._static_table(scope_table)
            matrix = tuple(
                Caption(
                    f"{item.subject.resource_id}: {axis.binding} ({len(axis.cases)}) → "
                    + ", ".join(axis.cases[:6])
                    + (f" +{len(axis.cases) - 6} more" if len(axis.cases) > 6 else ""),
                    markup=False,
                )
                for item in plan.slices
                for axis in item.matrices
            )
            yield ReviewSection(
                "2. Scope and expansion",
                scope_table,
                *matrix,
                Caption(
                    f"{count.trials} cell-trials across environments → "
                    f"{count.environment_executions} calls · {count.comparisons} comparisons",
                    markup=False,
                ),
                Hint("Each planned call may include retries and redirects."),
            )
            validation = plan.validation
            assertions = (
                ", ".join(
                    dict.fromkeys(
                        profile.resource_id or "Inline"
                        for item in plan.slices
                        for profile in item.profiles.assertions
                    )
                )
                or "None"
            )
            diffs = (
                ", ".join(
                    dict.fromkeys(
                        profile.resource_id or "Inline"
                        for item in plan.slices
                        for profile in item.profiles.diffs
                    )
                )
                or "None"
            )
            fields: list[tuple[str, str | Content]] = [
                ("Assertions", assertions),
                ("Environment diff", diffs),
            ]
            schema_count = sum(item.response_schema for item in plan.slices)
            if schema_count:
                fields.append(
                    ("Response schemas", f"{schema_count} Request contracts remain active")
                )
            if validation is not None:
                fields.extend(
                    (
                        (
                            "Rules",
                            f"{validation.assertion_rules} assertions · "
                            f"{validation.diff_rules} diffs",
                        ),
                        (
                            "Gate",
                            validation.gate.profile.resource_id if validation.gate else "None",
                        ),
                    )
                )
                gate = validation.gate
                if gate is not None:
                    required = []
                    if gate.require_complete_run:
                        required.append("complete execution")
                    if gate.require_assertion_coverage:
                        required.append("assertion coverage")
                    if gate.require_diff_coverage:
                        required.append("diff coverage")
                    fields.append(
                        ("Gate requires", " + ".join(required) or "Configured severity thresholds")
                    )
                    fields.append(("Applies to", ", ".join(gate.environment_roles)))
                    for threshold in gate.thresholds:
                        allowances = (
                            ("violations", threshold.violated),
                            ("drifts", threshold.drifted),
                            ("unavailable", threshold.unavailable),
                            ("incomparable", threshold.incomparable),
                            ("incomplete", threshold.incomplete),
                            ("errors", threshold.errors),
                        )
                        fields.append(
                            (
                                f"Gate · {threshold.severity}",
                                "Allowed: "
                                + ", ".join(f"{value} {name}" for name, value in allowances),
                            )
                        )
            yield ReviewSection("3. Validation", KeyValueList(*_fields(tuple(fields))))
            timeout = review.runtime.find("request-timeout", "execution-total")
            connections = review.runtime.find("http", "connections")
            body = review.runtime.find("ordinary", "body-bytes")
            limit_table = Table[str](
                columns=(
                    Column("timeout", "Execution timeout"),
                    Column("connections", "Connections"),
                    Column("attempts", "Attempts / call"),
                    Column("body", "Response limit"),
                ),
                records=(
                    Record(
                        "limits",
                        (
                            duration(timeout.value),
                            str(connections.value),
                            ", ".join(
                                str(value)
                                for value in dict.fromkeys(
                                    item.maximum_attempts_per_execution for item in plan.slices
                                )
                            ),
                            f"{body.value // 1024} KiB",
                        ),
                    ),
                ),
            )
            limit_table.styles.height = 2
            self._static_table(limit_table)
            yield ReviewSection("4. Limits", limit_table)
            authority = plan.http_authority
            safety: list[tuple[str, str | Content]] = [
                ("Write-capable requests", str(plan.write_capable_subjects)),
                (
                    "Mutation approval",
                    "Granted for this run" if review.mutation_approved else "Not granted",
                ),
                (
                    "Sensitive bindings",
                    ", ".join(dict.fromkeys(item.name for item in plan.sensitive_sources))
                    or "None",
                ),
            ]
            if authority is not None:
                safety.extend(
                    (
                        (
                            "Permitted hosts",
                            "Any host"
                            if authority.allow_any_host
                            else ", ".join(authority.allowed_hosts),
                        ),
                        ("Permitted methods", ", ".join(authority.allowed_methods)),
                    )
                )
            safety.append(
                (
                    "Policy / report",
                    f"{plan.policy.resource_id} · "
                    f"persistence: {review.runtime.artifact_persistence}",
                )
            )
            yield ReviewSection(
                "5. Safety review",
                KeyValueList(*_fields(tuple(safety))),
                Caption(
                    " · ".join(_review_reason(code) for code in review.execution_blockers)
                    or "✓ No execution blockers.",
                    markup=False,
                ),
                Disclosure(
                    "Policy advisories",
                    *(
                        Caption(_review_reason(code), markup=False)
                        for code in review.policy_advisories
                    ),
                )
                if review.policy_advisories
                else Hint("No policy advisories."),
            )
            overrides = override_fields(self.draft)
            yield ReviewSection(
                "6. Session overrides",
                KeyValueList(*_fields(overrides))
                if overrides
                else Caption("None · using inherited settings.", markup=False),
                Disclosure(
                    "View configuration provenance",
                    KeyValueList(
                        *_fields(
                            tuple(
                                (
                                    item.field,
                                    " → ".join(
                                        source.definition.resource_id
                                        if source.definition
                                        else source.inline.owner_resource_id
                                        if source.inline
                                        else "Session override"
                                        if source.invocation_id
                                        else source.kind
                                        for source in item.sources
                                    ),
                                )
                                for item in plan.provenance
                            )
                        )
                    ),
                ),
            )
            yield Disclosure(
                "All planning and runtime limits",
                KeyValueList(
                    *_fields(
                        tuple(
                            (item.resource, f"{item.effective} (ceiling {item.cap})")
                            for item in plan.budgets
                        )
                    )
                ),
                KeyValueList(
                    *_fields(
                        tuple(
                            (f"{item.section}/{item.name}", f"{item.value} {item.unit}")
                            for item in review.runtime.limits
                        )
                    )
                ),
            )

    @staticmethod
    def _static_table(table: Table[str]) -> None:
        table.show_cursor = False
        table.can_focus = False
        table.zebra_stripes = False

    def _hosts(self, identity: str) -> Content:
        if self.destinations is None:
            return Content("Unavailable")
        hosts = []
        for item in self.destinations.previews:
            if item.environment.resource_id == identity:
                parsed = urlsplit(item.url)
                host = parsed.hostname or "Redacted"
                # Drop userinfo, paths, query strings and fragments from the safe preview.
                hosts.append(
                    f"{parsed.scheme}://{host}" + (f":{parsed.port}" if parsed.port else "")
                )
        return Content(", ".join(dict.fromkeys(hosts)) or "Not sampled")

    def _request_label(self, identity: str) -> Content:
        entry = _entry(self.catalog, "Request", identity)
        if entry is None:
            return Content(f"⇄ {identity}")
        label = resource_label(entry)
        if entry.facts.route:
            label.append(f"  {entry.facts.route}")
        return Content.from_rich_text(label)

    @override
    def focus(self, scroll_visible: bool = True) -> "LaunchReview":
        """Focus review scrolling after the reactive checkpoint has mounted."""
        if self.query(VerticalScroll):
            self.query_one(VerticalScroll).focus(scroll_visible)
        return self


def _review_reason(code: str) -> str:
    return {
        "mutation-approval-required": "Approve write-capable requests before running.",
        "artifact-persistence-denied": "Policy does not allow report persistence.",
        "artifact-root-required": "A report storage location is required.",
        "artifact-root-denied": "Policy does not allow this report storage location.",
        "no-safety-profile": "No Safety Profile is attached.",
        "any-host": "Policy allows any destination host.",
        "any-port": "Policy allows any destination port.",
        "proxy-dns": "Proxy DNS is permitted.",
        "any-ambient-environment": "Policy permits any ambient environment variable.",
        "project-files": "Project file reads are permitted.",
        "any-remote-schemas": "Policy permits any remote schema.",
        "any-secret-egress": "Policy permits unrestricted secret egress.",
    }.get(code, code)
