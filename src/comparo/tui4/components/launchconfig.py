"""Launch selection composed from the shared TUI4 library; no application effects."""

from typing import override

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widget import Widget

from comparo.application import CompareIntent
from comparo.application import PlanReviewInspection
from comparo.tui4.components.actionbar import Action
from comparo.tui4.components.actionbar import ActionBar
from comparo.tui4.components.choicemenu import ChoiceMenu
from comparo.tui4.components.disclosure import Disclosure
from comparo.tui4.components.disclosure import DisclosureHeader
from comparo.tui4.components.filter import FilterInput
from comparo.tui4.components.menu import MenuItem
from comparo.tui4.components.panel import Panel
from comparo.tui4.components.resourcechoices import ProfileChoices
from comparo.tui4.components.resourcechoices import ResourceChoices
from comparo.tui4.components.resourcelabel import resource_label
from comparo.tui4.components.selectiongroup import SelectionGroup
from comparo.tui4.components.typography import Caption
from comparo.tui4.components.typography import Hint
from comparo.tui4.components.valueinput import ValueInput
from comparo.tui4.components.variantchoices import VariantChoices
from comparo.tui4.models.explorer import ExplorerCatalog
from comparo.tui4.models.explorer import ResourceEntry
from comparo.tui4.models.launch import LaunchDraft
from comparo.tui4.models.launch import default_subjects

MODES = (
    MenuItem("comparison", "Run requests"),
    MenuItem("execution-profile", "Run an execution profile"),
    MenuItem("assertion", "Run assertions"),
)


class LaunchConfiguration(Panel):
    """Translate semantic component choices into one immutable session draft."""

    class Changed(Message):
        """Configuration changed without preparing or executing anything."""

        def __init__(self, configuration: "LaunchConfiguration", draft: LaunchDraft) -> None:
            """Retain the semantic form owner and immutable selection."""
            super().__init__()
            self.configuration = configuration
            self.draft = draft

        @property
        @override
        def control(self) -> "LaunchConfiguration":
            """Return the semantic event owner."""
            return self.configuration

    def __init__(self, *, id: str | None = None) -> None:
        """Create a configuration panel with no initial project."""
        super().__init__(title="Configure run", hint="↑↓ browse · Space / Enter select", id=id)
        self.catalog: ExplorerCatalog | None = None

    @override
    def compose(self) -> ComposeResult:
        """Match Target: starting-point rail beside numbered expandable configuration."""
        with Horizontal(id="launch-target-columns"):
            with VerticalScroll(id="launch-starting-point"):
                yield Caption("STARTING POINT", classes="launch-section-title")
                yield ChoiceMenu(*MODES, selected=("comparison",), id="launch-mode")
                yield Hint(
                    "Tab: sections · ↑↓: navigate\n"
                    "Enter: edit · Esc: return\nAlt+1…5: jump to a section",
                    markup=False,
                )
            with VerticalScroll(id="launch-form"):
                yield Disclosure(
                    "1. Target",
                    Vertical(
                        ChoiceMenu(
                            MenuItem("saved", "Use a saved profile"),
                            MenuItem("manual", "Configure manually"),
                            selected=("saved",),
                            id="launch-profile-source",
                        ),
                        SelectionGroup(
                            "Saved execution profile · choose one",
                            ProfileChoices(id="launch-profile"),
                            Caption("", id="launch-profile-info", markup=False),
                            dependent=True,
                            id="launch-profile-fields",
                        ),
                        id="launch-profile-configuration",
                    ),
                    SelectionGroup(
                        "Requests · select one or more",
                        FilterInput("Search requests…", id="launch-filter"),
                        ResourceChoices(multiple=True, id="launch-requests"),
                        Hint("No matching Requests.", id="launch-no-requests"),
                        id="launch-ad-hoc",
                    ),
                    SelectionGroup(
                        "Requests included by this profile · read-only",
                        Caption("", id="launch-profile-targets", markup=False),
                        dependent=True,
                        id="launch-profile-includes",
                    ),
                    expanded=True,
                    id="launch-target-section",
                )
                yield Disclosure(
                    "2. Environment topology",
                    ChoiceMenu(
                        MenuItem("comparison", "Compare baseline and candidates"),
                        MenuItem("assertion", "Check environments independently"),
                        selected=("comparison",),
                        id="launch-topology-mode",
                    ),
                    SelectionGroup(
                        "Comparison environments",
                        Caption("BASELINE · choose one", classes="launch-section-label"),
                        ResourceChoices(id="launch-baseline"),
                        Caption("CANDIDATES · select one or more", classes="launch-section-label"),
                        ResourceChoices(multiple=True, id="launch-candidates"),
                        dependent=True,
                        id="launch-comparison-fields",
                    ),
                    SelectionGroup(
                        "Assertion environments · select one or more",
                        ResourceChoices(multiple=True, id="launch-environments"),
                        dependent=True,
                        id="launch-assertion-fields",
                    ),
                    SelectionGroup(
                        "Environments inherited from the profile · read-only",
                        Caption("", id="launch-profile-topology", markup=False),
                        dependent=True,
                        id="launch-profile-topology-group",
                    ),
                    id="launch-topology-section",
                )
                yield Disclosure(
                    "3. Variants · all cases / inherited trials",
                    ValueInput(
                        "Trials per cell",
                        placeholder="Inherit project / profile",
                        maximum_length=9,
                        id="launch-trials",
                    ),
                    VariantChoices(id="launch-variants"),
                    id="launch-variants-section",
                )
                yield Disclosure(
                    "4. Validation · inherited profiles",
                    *tuple(
                        self._validation_controls(name, label)
                        for name, label in (
                            ("assertions", "Assertion profiles"),
                            ("diffs", "Diff profiles"),
                            ("gate", "Gate profile"),
                        )
                    ),
                    Hint(
                        "Request response schemas remain active "
                        "when assertion profiles are cleared."
                    ),
                    id="launch-validation-section",
                )
                yield Disclosure(
                    "5. Safety · inherited limits",
                    ValueInput(
                        "Maximum attempts per call",
                        placeholder="Inherit project / profile",
                        maximum_length=9,
                        id="launch-maximum-attempts",
                    ),
                    Caption("Stop after a failure", markup=False),
                    ChoiceMenu(
                        MenuItem("inherit", "Inherit"),
                        MenuItem("yes", "Stop early"),
                        MenuItem("no", "Finish the full scope"),
                        selected=("inherit",),
                        id="launch-fail-fast",
                    ),
                    ChoiceMenu(
                        MenuItem("approved", "Approve mutating requests for this run"),
                        multiple=True,
                        id="launch-mutations",
                    ),
                    Hint("Changes apply to this run only; saved resources stay unchanged."),
                    ActionBar((Action("reset", "Reset overrides"),), id="launch-option-reset"),
                    id="launch-safety-section",
                )

    @staticmethod
    def _validation_controls(name: str, label: str) -> Vertical:
        return SelectionGroup(
            label,
            ChoiceMenu(
                MenuItem("inherit", "Inherit"),
                MenuItem("select", "Choose profiles"),
                MenuItem("none", "None"),
                selected=("inherit",),
                id=f"launch-{name}-mode",
            ),
            SelectionGroup(
                "Choose one" if name == "gate" else "Select one or more",
                ResourceChoices(multiple=name != "gate", id=f"launch-{name}"),
                dependent=True,
                id=f"launch-{name}-selection",
            ),
        )

    def load_preview(self, review: PlanReviewInspection) -> None:
        """Populate case controls from the planner without changing the draft."""
        self.query_one(VariantChoices).load_review(review)
        count = review.plan.cardinality
        self.query_one("#launch-variants-section", Disclosure).title = (
            f"3. Variants · {count.cells} cells · "
            + (
                f"{self.draft().trials} trials per cell"
                if self.draft().trials
                else "inherited trials"
            )
        )

    def on_mount(self) -> None:
        """Keep advanced controls closed until explicitly requested."""
        for section in self.sections:
            section.edit_mode = True
            section._reflect()
        self._reflect()

    @property
    def sections(self) -> list[Disclosure]:
        """The five primary configuration sections in reading order."""
        return list(self.query("#launch-form > Disclosure").results(Disclosure))

    @staticmethod
    def _contains(owner: Widget, focused: Widget | None) -> bool:
        return focused is owner or (focused is not None and owner in focused.ancestors)

    def _fields(self, section: Disclosure) -> list[Widget]:
        """Use the actual visible focus chain, excluding layout scroll containers."""
        return [
            item
            for item in self.screen.focus_chain
            if self._contains(section, item)
            and not isinstance(item, (DisclosureHeader, VerticalScroll))
        ]

    def focus_section(self, index: int, *, edit: bool = False) -> None:
        """Reach any section in one command, without traversing its siblings' controls."""
        section = self.sections[max(0, min(index, len(self.sections) - 1))]
        if edit:
            for other in self.sections:
                other.expanded = other is section
            self.call_after_refresh(self._enter_section, section)
        else:
            section.focus_header()

    def _enter_section(self, section: Disclosure) -> None:
        fields = self._fields(section)
        if fields:
            fields[0].focus()
        else:
            section.focus_header()

    def move_target_focus(self, direction: int) -> bool:
        """Navigate headers directly; traverse controls only after entering a section."""
        focused = self.app.focused
        if self._contains(self.choices("mode"), focused):
            if direction < 0:
                return False
            self.focus_section(0)
            return True
        for index, section in enumerate(self.sections):
            if not self._contains(section, focused):
                continue
            fields = self._fields(section)
            if focused in fields:
                next_index = fields.index(focused) + direction
                if 0 <= next_index < len(fields):
                    fields[next_index].focus()
                    return True
                if direction < 0:
                    section.focus_header()
                    return True
            next_section = index + direction
            if next_section < 0:
                self.focus_default()
                return True
            if next_section < len(self.sections):
                self.focus_section(next_section)
                return True
            return False
        self.focus_default()
        return True

    def return_to_sections(self) -> None:
        """Escape leaves an editor in one step, then returns to the starting choices."""
        focused = self.app.focused
        for section in self.sections:
            if self._contains(section, focused) and not isinstance(focused, DisclosureHeader):
                section.focus_header()
                return
        self.focus_default()

    @on(Disclosure.Opened)
    def _edit_section(self, event: Disclosure.Opened) -> None:
        if event.section in self.sections:
            event.stop()
            self.focus_section(self.sections.index(event.section), edit=True)

    @on(DisclosureHeader.Moved)
    def _move_section(self, event: DisclosureHeader.Moved) -> None:
        focused = self.app.focused
        for index, section in enumerate(self.sections):
            if self._contains(section, focused):
                event.stop()
                self.focus_section(index + event.direction)
                return

    def choices(self, name: str) -> ChoiceMenu:
        """Return a semantic selector for coordinated form updates."""
        return self.query_one(f"#launch-{name}", ChoiceMenu)

    def _entries(self, kind: str) -> tuple[ResourceEntry, ...]:
        if self.catalog is None:
            return ()
        return tuple(
            entry
            for entry in self.catalog.entries.values()
            if entry.summary.coordinate.kind == kind
        )

    def load(self, catalog: ExplorerCatalog) -> None:
        """Populate from safe catalog projections without materializing effective config."""
        self.catalog = catalog
        profiles = self._entries("ExecutionProfile")
        environments = self._entries("Environment")
        self.query_one("#launch-profile", ResourceChoices).load_entries(profiles)
        self.choices("profile").selected = (
            (profiles[0].summary.coordinate.resource_id,) if profiles else ()
        )
        self.choices("mode").selected = ("comparison",)
        for name, kind in (
            ("assertions", "AssertionProfile"),
            ("diffs", "DiffProfile"),
            ("gate", "GateProfile"),
        ):
            self.query_one(f"#launch-{name}", ResourceChoices).load_entries(self._entries(kind))
        for name in ("baseline", "candidates", "environments"):
            self.query_one(f"#launch-{name}", ResourceChoices).load_entries(environments)
            self.choices(name).selected = tuple(
                entry.summary.coordinate.resource_id
                for entry in environments
                if entry.facts.role == ("baseline" if name == "baseline" else "candidate")
                or (name == "environments" and entry.facts.role == "baseline")
            )
        self.choices("requests").selected = default_subjects(catalog)
        self.query_one(FilterInput).value = ""
        self._filter_requests("")
        self.reset_options()

    def draft(self) -> LaunchDraft:
        """Capture active and retained selectors; the model lowers only the active mode."""
        profile = self.choices("profile").selected
        baseline = self.choices("baseline").selected
        return LaunchDraft(
            intent=CompareIntent(self.choices("mode").selected[0]),
            profile=profile[0] if profile else None,
            subjects=self.choices("requests").selected,
            baseline=baseline[0] if baseline else None,
            candidates=self.choices("candidates").selected,
            environments=self.choices("environments").selected,
            trials=self.query_one("#launch-trials", ValueInput).value.strip(),
            manual_profile=self.choices("profile-source").selected == ("manual",),
            comparison_topology=self.choices("topology-mode").selected == ("comparison",),
            maximum_attempts=self.query_one("#launch-maximum-attempts", ValueInput).value.strip(),
            fail_fast={"inherit": None, "yes": True, "no": False}[
                self.choices("fail-fast").selected[0]
            ],
            matrices=self.query_one(VariantChoices).overrides,
            assertions=self._profile_override("assertions"),
            diffs=self._profile_override("diffs"),
            gate=self._gate_override(),
            approve_mutations=bool(self.choices("mutations").selected),
            selection_problems=tuple(
                f"Choose {name} profiles, or explicitly select None."
                for name in ("assertions", "diffs", "gate")
                if self.choices(f"{name}-mode").selected == ("select",)
                and not self.choices(name).selected
            ),
        )

    def _profile_override(self, name: str) -> tuple[str, ...] | None:
        mode = self.choices(f"{name}-mode").selected[0]
        return (
            None
            if mode == "inherit"
            else ()
            if mode == "none"
            else self.choices(name).selected or None
        )

    def _gate_override(self) -> str | None:
        mode = self.choices("gate-mode").selected[0]
        selected = self.choices("gate").selected
        return (
            None
            if mode == "inherit"
            else "@none"
            if mode == "none"
            else selected[0]
            if selected
            else None
        )

    def _reflect(self) -> None:
        draft = self.draft()
        profile_mode = draft.execution_intent is CompareIntent.EXECUTION_PROFILE
        self.query_one("#launch-profile-configuration").display = (
            draft.intent is CompareIntent.EXECUTION_PROFILE
        )
        self.query_one("#launch-profile-fields").display = not draft.manual_profile
        self.query_one("#launch-profile-includes").display = profile_mode
        self.query_one("#launch-profile-topology-group").display = profile_mode
        self.query_one("#launch-ad-hoc").display = not profile_mode
        self.query_one("#launch-ad-hoc").set_class(
            draft.intent is CompareIntent.EXECUTION_PROFILE, "dependent"
        )
        self.query_one("#launch-topology-mode").display = (
            not profile_mode and draft.intent is not CompareIntent.ASSERTION
        )
        self.query_one("#launch-comparison-fields").display = (
            draft.execution_intent is CompareIntent.COMPARISON
        )
        self.query_one("#launch-assertion-fields").display = (
            draft.execution_intent is CompareIntent.ASSERTION
        )
        for name in ("assertions", "diffs", "gate"):
            self.query_one(f"#launch-{name}-selection").display = self.choices(
                f"{name}-mode"
            ).selected == ("select",)
        profile = next(
            (
                entry
                for entry in self._entries("ExecutionProfile")
                if entry.summary.coordinate.resource_id == draft.profile
            ),
            None,
        )
        description = (
            profile.summary.description if profile is not None else "No profiles available."
        )
        self.query_one("#launch-profile-info", Caption).update(description or "")
        self.query_one("#launch-profile-info").display = False
        ids: tuple[str, ...] = ()
        if profile is not None and profile.summary.outline is not None:
            relationships = profile.summary.outline.relationships
            ids = tuple(
                item.target.resource_id for item in relationships if "subjects" in item.path
            )
            targets = tuple(
                entry
                for entry in self._entries("Request")
                if entry.summary.coordinate.resource_id in ids
            )
            self.query_one("#launch-profile-targets", Caption).update(
                Text("\n").join(resource_label(entry) for entry in targets)
            )
            profile_topology = []
            for label, key in (
                ("Baseline", "baseline"),
                ("Candidates", "candidates"),
                ("Environments", "environments"),
            ):
                values = tuple(
                    dict.fromkeys(
                        item.target.resource_id for item in relationships if key in item.path
                    )
                )
                if values:
                    profile_topology.append(f"{label}: {', '.join(values)}")
            self.query_one("#launch-profile-topology", Caption).update(
                "\n".join(profile_topology) or "Inherited from project defaults."
            )
        count = len(ids) if profile_mode else len(draft.subjects)
        self.query_one("#launch-target-section", Disclosure).title = f"1. Target · {count} selected"
        topology = (
            "From execution profile"
            if profile_mode
            else (
                f"{draft.baseline or 'Choose baseline'} → "
                f"{', '.join(draft.candidates) or 'Choose candidate'}"
                if draft.execution_intent is CompareIntent.COMPARISON
                else ", ".join(draft.environments)
            )
        )
        self.query_one(
            "#launch-topology-section", Disclosure
        ).title = f"2. Environments · {topology}"

        validation_overrides = sum(
            self.choices(f"{name}-mode").selected != ("inherit",)
            for name in ("assertions", "diffs", "gate")
        )
        validation_summary = (
            "Selection required"
            if draft.selection_problems
            else f"{validation_overrides} profile overrides"
            if validation_overrides
            else "Inherited profiles"
        )
        self.query_one(
            "#launch-validation-section", Disclosure
        ).title = f"4. Validation · {validation_summary}"
        safety_overrides = sum(
            (bool(draft.maximum_attempts), draft.fail_fast is not None, draft.approve_mutations)
        )
        safety_summary = (
            f"{safety_overrides} session overrides" if safety_overrides else "Inherited limits"
        )
        self.query_one("#launch-safety-section", Disclosure).title = f"5. Safety · {safety_summary}"
        self.post_message(self.Changed(self, draft))

    def set_compact(self, compact: bool) -> None:
        """Keep starting choices legible without changing their semantic identities."""
        self.choices("mode").items = (
            MenuItem("comparison", "Run requests"),
            MenuItem("execution-profile", "Run profile" if compact else "Run an execution profile"),
            MenuItem("assertion", "Run assertions"),
        )

    @on(ChoiceMenu.Changed)
    def _choice_changed(self, event: ChoiceMenu.Changed) -> None:
        event.stop()
        if event.menu.id in {
            "launch-profile",
            "launch-profile-source",
            "launch-requests",
            "launch-mode",
        }:
            self.query_one(VariantChoices).reset()
        self._reflect()

    @on(VariantChoices.Changed)
    def _variants_changed(self, event: VariantChoices.Changed) -> None:
        event.stop()
        self._reflect()

    @on(ValueInput.Changed)
    def _value_changed(self, event: ValueInput.Changed) -> None:
        event.stop()
        self._reflect()

    @on(FilterInput.Changed)
    def _filter_changed(self, event: FilterInput.Changed) -> None:
        event.stop()
        self._filter_requests(event.value)

    def _filter_requests(self, query: str) -> None:
        entries = tuple(
            entry
            for entry in self._entries("Request")
            if query.casefold()
            in f"{entry.summary.name} {entry.summary.coordinate.resource_id} "
            f"{entry.facts.route or ''}".casefold()
        )
        self.query_one("#launch-requests", ResourceChoices).load_entries(entries)
        self.query_one("#launch-no-requests").display = not entries

    @on(FilterInput.Dismissed)
    def _filter_dismissed(self, event: FilterInput.Dismissed) -> None:
        event.stop()
        self.choices("requests").focus()

    def toggle_options(self) -> None:
        """Expand or collapse run options without changing their values."""
        section = self.query_one("#launch-safety-section", Disclosure)
        section.expanded = not section.expanded

    def reset_options(self) -> None:
        """Restore inherited trials and revoke per-run mutation approval."""
        for control in self.query(ValueInput):
            control.value = ""
        self.query_one(VariantChoices).reset()
        self.choices("fail-fast").selected = ("inherit",)
        for name in ("assertions", "diffs", "gate"):
            self.choices(f"{name}-mode").selected = ("inherit",)
            self.choices(name).selected = ()
        self.choices("mutations").selected = ()
        self._reflect()

    @on(ActionBar.Requested)
    def _option_action(self, event: ActionBar.Requested) -> None:
        event.stop()
        if event.action == "options":
            self.toggle_options()
        elif event.action == "reset":
            self.reset_options()

    def focus_default(self) -> None:
        """Focus the shared starting-point menu."""
        self.choices("mode").focus()
