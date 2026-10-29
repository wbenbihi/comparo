"""Launch drafts and safe, renderer-independent review copy."""

from dataclasses import dataclass

import msgspec

from comparo.application import ClearProfileSelection
from comparo.application import CompareIntent
from comparo.application import MatrixOverride
from comparo.application import PlanningOptions
from comparo.application import ProfileSelectionOverrides
from comparo.tui4.models.explorer import ExplorerCatalog


@dataclass(frozen=True, slots=True)
class LaunchDraft:
    """One session-only selection; blank trials preserve inherited defaults."""

    intent: CompareIntent = CompareIntent.COMPARISON
    profile: str | None = None
    subjects: tuple[str, ...] = ()
    environments: tuple[str, ...] = ()
    baseline: str | None = None
    candidates: tuple[str, ...] = ()
    trials: str = ""
    approve_mutations: bool = False
    manual_profile: bool = False
    comparison_topology: bool = True
    maximum_attempts: str = ""
    fail_fast: bool | None = None
    matrices: tuple[MatrixOverride, ...] = ()
    assertions: tuple[str, ...] | None = None
    diffs: tuple[str, ...] | None = None
    gate: str | None = None
    selection_problems: tuple[str, ...] = ()

    @property
    def execution_intent(self) -> CompareIntent:
        """Lower a manually configured profile to the matching ad-hoc planning intent."""
        if self.intent is CompareIntent.EXECUTION_PROFILE and not self.manual_profile:
            return self.intent
        if self.intent is CompareIntent.ASSERTION or not self.comparison_topology:
            return CompareIntent.ASSERTION
        return CompareIntent.COMPARISON

    @property
    def problems(self) -> tuple[str, ...]:
        """Explain incomplete selections before invoking the planner."""
        problems = list(self.selection_problems)
        if self.execution_intent is CompareIntent.EXECUTION_PROFILE:
            if self.profile is None:
                problems.append("Choose an execution profile.")
        else:
            if not self.subjects:
                problems.append("Select at least one Request.")
            if self.execution_intent is CompareIntent.ASSERTION and not self.environments:
                problems.append("Select at least one Environment.")
            if self.execution_intent is CompareIntent.COMPARISON:
                if self.baseline is None or not self.candidates:
                    problems.append("Choose a baseline and at least one candidate.")
                if self.baseline in self.candidates:
                    problems.append("The baseline cannot also be a candidate.")
        problems.extend(
            f"{label} must be a positive whole number, or blank to inherit."
            for label, value in (
                ("Trials", self.trials),
                ("Maximum attempts", self.maximum_attempts),
            )
            if value and not _positive_number(value)
        )
        if any(not item.cases for item in self.matrices):
            problems.append("Select at least one case for each Matrix.")
        return tuple(problems)

    def options(self) -> PlanningOptions:
        """Translate only the active mode, without leaking hidden topology selections."""
        if self.problems:
            raise ValueError("launch draft is incomplete")
        if self.execution_intent is CompareIntent.EXECUTION_PROFILE:
            selected = PlanningOptions(execution_profile=self.profile)
        elif self.execution_intent is CompareIntent.ASSERTION:
            selected = PlanningOptions(subjects=self.subjects, environments=self.environments)
        else:
            selected = PlanningOptions(
                subjects=self.subjects, baseline=self.baseline, candidates=self.candidates
            )
        return msgspec.structs.replace(
            selected,
            trials=int(self.trials) if self.trials else None,
            maximum_attempts=int(self.maximum_attempts) if self.maximum_attempts else None,
            fail_fast=self.fail_fast,
            matrix_filters=self.matrices,
            profiles=ProfileSelectionOverrides(
                assertions=self.assertions,
                diffs=self.diffs,
                gate=ClearProfileSelection() if self.gate == "@none" else self.gate,
            ),
        )


def default_subjects(catalog: ExplorerCatalog) -> tuple[str, ...]:
    """Read typed Project-default Request relationships without parsing authored YAML."""
    outline = catalog.project.summary.outline
    if outline is None:
        return ()
    return tuple(
        dict.fromkeys(
            item.target.resource_id
            for item in outline.relationships
            if item.target.kind == "Request" and "subjects" in item.path
        )
    )


def _positive_number(value: str) -> bool:
    return value.isascii() and value.isdecimal() and int(value) > 0
