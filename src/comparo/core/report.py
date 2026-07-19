"""The diff-gate verdict — the CI pass/fail contract, shared by every sink.

The full report is the :class:`~comparo.core.report_record.ReportRecord`, built by
:mod:`comparo.core.report_builder`; this module holds only the gate arithmetic the
builder and the CLI both need, kept here so neither has to import the other.
"""


def diff_passed(calls: int, drift: int, errors: int) -> bool:
    """Whether a diff run passes its gate.

    A run that compared nothing (``calls == 0``) verified nothing, so it can never
    be a pass — it fails closed, mirroring :attr:`ExecutionResult.passed`.
    """
    return calls > 0 and drift == 0 and errors == 0


def diff_gate(calls: int, drift: int, errors: int) -> str:
    """The tri-state gate verdict (``PASS`` / ``FAIL`` / ``ERROR``) for a diff run."""
    if errors:
        return "ERROR"
    return "PASS" if diff_passed(calls, drift, errors) else "FAIL"
