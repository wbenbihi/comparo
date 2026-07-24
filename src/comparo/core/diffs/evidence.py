"""Bounded adverse-first diff evidence selection and safe structural sizing."""

from __future__ import annotations

from collections.abc import Iterator
from enum import Enum
from typing import final
from uuid import UUID

import msgspec

from comparo.core.diffs.accounting import DiffMeter
from comparo.core.diffs.value_support import require_sort_work
from comparo.core.diffs.value_support import require_work
from comparo.core.domain.budgets import BudgetExceeded
from comparo.core.domain.diffs.results import DiffFact
from comparo.core.domain.diffs.results import DiffFactOutcome


@final
class EvidenceCandidate(msgspec.Struct, frozen=True, kw_only=True):
    """One bounded payload-free fact candidate for adverse-first evidence."""

    fact: DiffFact
    size: int
    ordinal: int


def consider_evidence(
    candidates: list[EvidenceCandidate],
    fact: DiffFact,
    ordinal: int,
    meter: DiffMeter,
) -> int | None:
    """Keep a bounded adverse-first reservoir and return one evicted footprint."""
    example_limit, byte_limit = meter.evidence_capacity()
    if example_limit <= 0 or byte_limit <= 0:
        return 1
    candidate = EvidenceCandidate(
        fact=fact,
        size=evidence_footprint(fact, meter, maximum=byte_limit),
        ordinal=ordinal,
    )
    require_sort_work(meter, len(candidates) + 1)
    ordered = sorted(
        (*candidates, candidate),
        key=lambda item: (-evidence_priority(item.fact.outcome), item.ordinal),
    )
    retained: list[EvidenceCandidate] = []
    retained_bytes = 0
    dropped: list[EvidenceCandidate] = []
    for item in ordered:
        if len(retained) < example_limit and retained_bytes + item.size <= byte_limit:
            retained.append(item)
            retained_bytes += item.size
        else:
            dropped.append(item)
    candidates[:] = retained
    return dropped[0].size if dropped else None


def finalize_evidence(
    candidates: list[EvidenceCandidate],
    omitted_probe_size: int | None,
    fact_count: int,
    meter: DiffMeter,
) -> tuple[tuple[DiffFact, ...], BudgetExceeded | None]:
    """Charge only bounded retained evidence and preserve exact exhaustion provenance."""
    retained: list[EvidenceCandidate] = []
    exhaustion: BudgetExceeded | None = None
    # ``consider_evidence`` prepays a dominating sort bound on every insertion.
    # Finalization must remain total after evaluation work is exhausted so the
    # caller can freeze and report the exact progress accumulated so far.
    for candidate in sorted(candidates, key=lambda item: item.ordinal):
        failure = meter.charge_evidence(candidate.size)
        if failure is None:
            retained.append(candidate)
        elif exhaustion is None:
            exhaustion = failure
    if len(retained) < fact_count and exhaustion is None:
        exhaustion = meter.evidence_exhaustion(omitted_probe_size or 1)
    return tuple(item.fact for item in retained), exhaustion


def evidence_priority(outcome: DiffFactOutcome) -> int:
    """Rank adverse evidence ahead of passing and ignored samples."""
    return {
        DiffFactOutcome.INCOMPARABLE: 4,
        DiffFactOutcome.CHANGED: 3,
        DiffFactOutcome.ADDED: 3,
        DiffFactOutcome.REMOVED: 3,
        DiffFactOutcome.SAME: 1,
        DiffFactOutcome.IGNORED: 0,
    }[outcome]


def evidence_footprint(  # noqa: C901
    root: DiffFact,
    meter: DiffMeter,
    *,
    maximum: int | None = None,
) -> int:
    """Measure payload-free structure, stopping immediately beyond a byte ceiling."""
    if maximum is not None and (type(maximum) is not int or maximum < 0):
        raise ValueError("evidence footprint ceiling must be a non-negative integer")
    total = 0
    stack: list[Iterator[object]] = [iter((root,))]
    while stack:
        require_work(meter)
        try:
            value = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        if value is None or type(value) is bool:
            total += 1
        elif type(value) is int:
            total += 8
        elif type(value) is str:
            for character in value:
                require_work(meter)
                total += len(character.encode("utf-8"))
                if maximum is not None and total > maximum:
                    return maximum + 1
        elif type(value) is bytes:
            total += len(value)
        elif type(value) is UUID:
            total += 16
        elif isinstance(value, Enum):
            stack.append(iter((value.value,)))
        elif type(value) is tuple:  # noqa: SIM114 -- preserves mypy narrowing
            total += 8
            stack.append(iter(value))
        elif type(value) is list:  # noqa: SIM114 -- preserves mypy narrowing
            total += 8
            stack.append(iter(value))
        elif type(value) is frozenset:
            total += 8
            stack.append(iter(value))
        elif isinstance(value, msgspec.Struct):
            total += 8
            fields = msgspec.structs.fields(type(value))
            for field in fields:
                require_work(meter, max(1, len(field.name)))
                total += len(field.name)
            stack.append(_struct_values(value, fields))
        else:
            total += len(type(value).__qualname__)
        if maximum is not None and total > maximum:
            return maximum + 1
    return max(1, total)


def _struct_values(
    value: msgspec.Struct,
    fields: tuple[msgspec.structs.FieldInfo, ...],
) -> Iterator[object]:
    for field in fields:
        yield object.__getattribute__(value, field.name)


__all__ = (
    "EvidenceCandidate",
    "consider_evidence",
    "evidence_footprint",
    "evidence_priority",
    "finalize_evidence",
)
