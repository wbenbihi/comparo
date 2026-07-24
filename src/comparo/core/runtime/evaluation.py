"""Default evaluation preparation for runs without assertion, diff, or gate profiles."""

from __future__ import annotations

from comparo.core.domain.gates.model import CompiledGateProfile
from comparo.core.planning.models import PreparedRun
from comparo.core.runtime.models import ComparisonWork
from comparo.core.runtime.models import ExecutionRecord
from comparo.core.runtime.ports import AssertionCall
from comparo.core.runtime.ports import DiffCall


class NoEvaluationPreparation:
    """Select no rules while preserving the same runtime and gate composition path."""

    @property
    def gate_profile(self) -> CompiledGateProfile | None:
        """Return no requested gate profile."""
        return None

    async def assertions(
        self,
        prepared: PreparedRun,
        record: ExecutionRecord,
    ) -> tuple[AssertionCall, ...]:
        """Return no per-Environment assertion calls."""
        if type(prepared) is not PreparedRun or type(record) is not ExecutionRecord:
            raise TypeError("assertion preparation requires PreparedRun and ExecutionRecord")
        return ()

    async def diffs(
        self,
        prepared: PreparedRun,
        work: ComparisonWork,
        baseline: ExecutionRecord,
        candidate: ExecutionRecord,
    ) -> tuple[DiffCall, ...]:
        """Return no directed diff calls."""
        if type(prepared) is not PreparedRun or type(work) is not ComparisonWork:
            raise TypeError("diff preparation requires PreparedRun and ComparisonWork")
        if type(baseline) is not ExecutionRecord or type(candidate) is not ExecutionRecord:
            raise TypeError("diff preparation requires ExecutionRecord sides")
        return ()
