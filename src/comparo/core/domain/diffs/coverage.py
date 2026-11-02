"""Exact diff fact counts and stage coverage."""

from __future__ import annotations

from typing import final

import msgspec

from comparo.core.domain.primitives import Count


@final
class DiffFactCounts(msgspec.Struct, frozen=True, kw_only=True):
    """Exact logical counts independent of bounded evidence."""

    same: Count
    changed: Count
    added: Count
    removed: Count
    ignored: Count
    incomparable: Count

    def __post_init__(self) -> None:
        """Require exact non-negative counts for every concrete outcome."""
        values = (
            self.same,
            self.changed,
            self.added,
            self.removed,
            self.ignored,
            self.incomparable,
        )
        if any(type(count) is not Count for count in values):
            raise TypeError("diff fact counts must use Count values")

    @property
    def evaluated(self) -> int:
        """Return non-ignored concrete fact count."""
        return (
            self.same.value
            + self.changed.value
            + self.added.value
            + self.removed.value
            + self.incomparable.value
        )

    @property
    def total(self) -> int:
        """Return all concrete facts, including acknowledged ignores."""
        return self.evaluated + self.ignored.value


@final
class DiffCoverage(msgspec.Struct, frozen=True, kw_only=True):
    """Exact stage coverage preventing narrow work from masquerading as equality."""

    baseline_selected: Count
    candidate_selected: Count
    baseline_derived: Count
    candidate_derived: Count
    baseline_consumed: Count
    candidate_consumed: Count
    baseline_resolved: Count
    candidate_resolved: Count
    paired: Count
    compared: Count
    ignored: Count
    not_evaluated: Count

    def __post_init__(self) -> None:
        """Require exact counts and consistent pair resolution."""
        values = (
            self.baseline_selected,
            self.candidate_selected,
            self.baseline_derived,
            self.candidate_derived,
            self.baseline_consumed,
            self.candidate_consumed,
            self.baseline_resolved,
            self.candidate_resolved,
            self.paired,
            self.compared,
            self.ignored,
            self.not_evaluated,
        )
        if any(type(count) is not Count for count in values):
            raise TypeError("diff coverage must use Count values")
        if self.baseline_consumed.value > self.baseline_derived.value:
            raise ValueError("baseline consumed values cannot exceed baseline derived values")
        if self.candidate_consumed.value > self.candidate_derived.value:
            raise ValueError("candidate consumed values cannot exceed candidate derived values")
        if self.baseline_resolved.value > self.baseline_consumed.value:
            raise ValueError("baseline resolved values cannot exceed baseline consumed values")
        if self.candidate_resolved.value > self.candidate_consumed.value:
            raise ValueError("candidate resolved values cannot exceed candidate consumed values")
        resolved = self.compared.value + self.ignored.value + self.not_evaluated.value
        if resolved != self.paired.value:
            raise ValueError("every paired unit must be compared, ignored, or not evaluated")
        _require_unit_footprints(
            units=self.paired.value,
            baseline=self.baseline_consumed.value,
            candidate=self.candidate_consumed.value,
            label="paired",
        )
        resolved_units = self.compared.value + self.ignored.value
        _require_unit_footprints(
            units=resolved_units,
            baseline=self.baseline_resolved.value,
            candidate=self.candidate_resolved.value,
            label="resolved",
        )
        _require_unit_footprints(
            units=self.not_evaluated.value,
            baseline=self.baseline_consumed.value - self.baseline_resolved.value,
            candidate=self.candidate_consumed.value - self.candidate_resolved.value,
            label="not-evaluated",
        )


def _require_unit_footprints(
    *,
    units: int,
    baseline: int,
    candidate: int,
    label: str,
) -> None:
    if units < max(baseline, candidate) or units > baseline + candidate:
        raise ValueError(f"{label} units contradict exact per-side value coverage")
