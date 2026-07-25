"""Closed semantic slots for bounded terminal background operations."""

from enum import StrEnum


class TuiOperationSlot(StrEnum):
    """Finite independently coalesced background-operation categories."""

    STARTUP = "startup"
    CLEANUP = "cleanup"
    PROJECT = "project"
    REPORT = "report"
    COMPARE = "compare"
    EXECUTION = "execution"


__all__ = ("TuiOperationSlot",)
