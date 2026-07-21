"""Gate arithmetic — the CI pass/fail contract, shared by every sink.

The full report is the :class:`~comparo.core.report_record.ReportRecord`, built by
:mod:`comparo.core.report_builder`; this module holds only the gate arithmetic the
builder and the CLI both need, kept here so neither has to import the other.

Precedence, locked by the RUN/DIFF Results specs: **FAIL whenever any rule broke
anywhere; ERROR only when errors are the only failure; a run that judged nothing
fails closed.** The same fold over already-computed verdicts is
:func:`comparo.core.outcomes.combine`.
"""

from comparo.core.outcomes import Verdict


def diff_passed(calls: int, drift: int, errors: int) -> bool:
    """Whether a diff run passes its gate.

    A run that compared nothing (``calls == 0``) verified nothing, so it can never
    be a pass — it fails closed, mirroring :attr:`ExecutionResult.passed`.
    """
    return calls > 0 and drift == 0 and errors == 0


def _verdict(broke: int, errors: int, judged: int) -> Verdict:
    if broke:
        return Verdict.FAIL
    if errors:
        return Verdict.ERROR
    return Verdict.PASS if judged else Verdict.FAIL


def diff_gate(calls: int, drift: int, errors: int) -> Verdict:
    """The tri-state gate verdict for a diff run.

    Drift outranks errors: a run with real drift is a FAIL even when some cells
    also errored — ERROR is reserved for runs where nothing broke but part of
    the comparison could not run.
    """
    return _verdict(drift, errors, calls)


def run_gate(failed: int, errors: int, cells: int) -> Verdict:
    """The gate verdict for a one-environment run.

    ``failed`` must count only rules judged against a real response — every rule
    on a response-less cell auto-fails with "no response", but those were never
    evaluated and belong to the cell's ``errors``, not to ``failed``.
    """
    return _verdict(failed, errors, cells)


def execution_gate(drift: int, failed: int, errors: int, cells: int) -> Verdict:
    """The gate verdict for an execution — assertions on both sides plus the diff.

    Any broken dimension (a failed judged assertion on either side, or drift) is
    a FAIL; errors alone grade ERROR; an empty plan fails closed.
    """
    return _verdict(drift + failed, errors, cells)
