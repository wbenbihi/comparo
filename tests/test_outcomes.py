"""Tests for the shared verdict/outcome vocabulary — one algebra for every tab."""

import dataclasses

import pytest

from comparo.core.outcomes import CheckTally
from comparo.core.outcomes import Verdict
from comparo.core.outcomes import combine
from comparo.core.outcomes import provenance_label


def test_combine_fail_outranks_error() -> None:
    # The locked precedence: a broken rule anywhere is a FAIL, even next to errors.
    assert combine([Verdict.PASS, Verdict.ERROR, Verdict.FAIL]) is Verdict.FAIL


def test_combine_error_only_when_errors_are_the_only_failure() -> None:
    assert combine([Verdict.PASS, Verdict.ERROR, Verdict.PASS]) is Verdict.ERROR


def test_combine_all_pass() -> None:
    assert combine([Verdict.PASS, Verdict.PASS]) is Verdict.PASS


def test_combine_empty_fails_closed() -> None:
    # Judging nothing is never a pass.
    assert combine([]) is Verdict.FAIL


def test_verdict_is_wire_compatible() -> None:
    # The report Gate literal stores plain strings; the enum must compare equal.
    assert Verdict.PASS == "PASS"
    assert Verdict.FAIL == "FAIL"
    assert Verdict.ERROR == "ERROR"


def test_provenance_label_grammar() -> None:
    assert provenance_label("profile", "diff.pricing") == "profile diff.pricing"
    assert provenance_label("inline", "price-quote") == "inline · price-quote"
    assert provenance_label("default") == "default"
    assert provenance_label("synthetic") == "synthetic"


def test_check_tally_defaults_to_zero_and_is_frozen() -> None:
    tally = CheckTally()
    assert (tally.broke, tally.held, tally.silenced, tally.absent, tally.error) == (0, 0, 0, 0, 0)
    assert (tally.absorbed, tally.warn_broke, tally.warn_held) == (0, 0, 0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        tally.broke = 1  # type: ignore[misc]
