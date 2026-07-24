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
            self.paired,
            self.compared,
            self.ignored,
            self.not_evaluated,
        )
        if any(type(count) is not Count for count in values):
            raise TypeError("diff coverage must use Count values")
        resolved = self.compared.value + self.ignored.value + self.not_evaluated.value
        if resolved != self.paired.value:
            raise ValueError("every paired unit must be compared, ignored, or not evaluated")
