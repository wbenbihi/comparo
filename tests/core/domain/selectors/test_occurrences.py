"""Exercise retry and redirect occurrence scopes on compiled selectors."""

from __future__ import annotations

from uuid import UUID

import pytest

from comparo.core.domain.identity import AttemptId
from comparo.core.domain.identity import ExchangeId
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.selectors.contracts import ValueContract
from comparo.core.domain.selectors.contracts import ValueKind
from comparo.core.domain.selectors.model import CompiledSelector
from comparo.core.domain.selectors.occurrences import AllAttemptsOccurrence
from comparo.core.domain.selectors.occurrences import AllExchangesOccurrence
from comparo.core.domain.selectors.occurrences import AttemptOccurrence
from comparo.core.domain.selectors.occurrences import ExchangeOccurrence
from comparo.core.domain.selectors.occurrences import ObservationOccurrenceSelector
from comparo.core.domain.selectors.occurrences import TerminalOccurrence
from comparo.core.domain.selectors.occurrences import is_observation_occurrence_selector
from comparo.core.domain.selectors.paths import NoPath
from comparo.core.domain.selectors.sources import SelectionAccess
from comparo.core.domain.selectors.sources import SelectorCapabilities
from comparo.core.domain.selectors.sources import SelectorCardinality
from comparo.core.domain.selectors.sources import SelectorSource


def _fingerprint() -> Fingerprint:
    return Fingerprint(algorithm="sha256", digest=Sha256Digest(hex_value="7" * 64))


def _selector(
    source: SelectorSource,
    occurrence: ObservationOccurrenceSelector,
    cardinality: SelectorCardinality,
) -> CompiledSelector:
    return CompiledSelector(
        fingerprint=_fingerprint(),
        source=source,
        path=NoPath(),
        capabilities=SelectorCapabilities(
            cardinality=cardinality,
            access=SelectionAccess.INCREMENTAL,
            may_duplicate=False,
            uses_descendant=False,
            uses_filter=False,
        ),
        output=ValueContract(kinds=(ValueKind.UNKNOWN,), cardinality=cardinality),
        occurrence=occurrence,
    )


def test_occurrence_selector_union_is_closed_and_typed() -> None:
    values = (
        TerminalOccurrence(),
        AttemptOccurrence(attempt_id=AttemptId(value=UUID(int=1))),
        ExchangeOccurrence(exchange_id=ExchangeId(value=UUID(int=2))),
        AllAttemptsOccurrence(),
        AllExchangesOccurrence(),
    )

    assert all(is_observation_occurrence_selector(value) for value in values)
    assert not is_observation_occurrence_selector("terminal")


def test_occurrence_scope_changes_static_cardinality() -> None:
    all_attempts = _selector(
        SelectorSource.ATTEMPT,
        AllAttemptsOccurrence(),
        SelectorCardinality.ZERO_OR_MORE,
    )
    all_exchanges = _selector(
        SelectorSource.EXCHANGE,
        AllExchangesOccurrence(),
        SelectorCardinality.ZERO_OR_MORE,
    )

    assert all_attempts.capabilities.cardinality is SelectorCardinality.ZERO_OR_MORE
    assert all_exchanges.capabilities.cardinality is SelectorCardinality.ZERO_OR_MORE


def test_occurrence_scope_rejects_ambiguous_source_combinations() -> None:
    with pytest.raises(ValueError, match="execution and Scenario"):
        _selector(
            SelectorSource.EXECUTION,
            AllAttemptsOccurrence(),
            SelectorCardinality.ZERO_OR_MORE,
        )
    with pytest.raises(ValueError, match="attempt source"):
        _selector(
            SelectorSource.ATTEMPT,
            ExchangeOccurrence(exchange_id=ExchangeId(value=UUID(int=3))),
            SelectorCardinality.EXACTLY_ONE,
        )
    with pytest.raises(ValueError, match="logical stream"):
        _selector(
            SelectorSource.STREAM_EVENTS,
            AllExchangesOccurrence(),
            SelectorCardinality.ZERO_OR_MORE,
        )
    with pytest.raises(ValueError, match="all-attempt"):
        _selector(
            SelectorSource.RESPONSE_STATUS,
            AllAttemptsOccurrence(),
            SelectorCardinality.ZERO_OR_MORE,
        )
