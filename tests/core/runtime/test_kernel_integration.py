"""Runtime integration tests for real assertion, diff, normalization, and gate kernels."""

from __future__ import annotations

import asyncio
from uuid import uuid5

import msgspec

from comparo.core.diffs.inputs import PreparedDiffInputs
from comparo.core.domain.diffs.rules import DirectedEnvironmentPair
from comparo.core.domain.gates.model import CompiledGateProfile
from comparo.core.domain.gates.model import GateThreshold
from comparo.core.domain.gates.model import GateVerdict
from comparo.core.domain.gates.types import EnvironmentRole
from comparo.core.domain.gates.types import GateSeverity
from comparo.core.domain.identity import RuleApplicationId
from comparo.core.domain.primitives import Count
from comparo.core.planning.models import PreparedRun
from comparo.core.resolution.control import RunCancellation
from comparo.core.runtime import AssertionCall
from comparo.core.runtime import DiffCall
from comparo.core.runtime import RecordingEventSink
from comparo.core.runtime import run_prepared
from comparo.core.runtime.models import ComparisonWork
from comparo.core.runtime.models import ExecutionRecord
from comparo.core.runtime.models import ObservedExecution
from tests.core.assertions import factories as assertion_factories
from tests.core.diffs import factories as diff_factories
from tests.core.domain.gates.test_invariants import _definition as gate_definition
from tests.core.runtime.conftest import FixedClock
from tests.core.runtime.test_vertical_runtime import _driver


def _threshold() -> GateThreshold:
    zero = Count(value=0)
    return GateThreshold(
        severity=GateSeverity.BLOCKING,
        violated_rules=zero,
        drifted_rules=zero,
        unavailable_rules=zero,
        incomparable_rules=zero,
        incomplete_rules=zero,
        error_rules=zero,
    )


class KernelPreparation:
    """Prepare one real status assertion and one real status diff for each coordinate."""

    def __init__(self) -> None:
        """Compile reusable test rules and a strict zero-tolerance gate."""
        self.assertion_rule = assertion_factories.rule()
        self.diff_rule = diff_factories.rule()
        self._gate = CompiledGateProfile(
            definition=gate_definition(),
            severities=(GateSeverity.BLOCKING,),
            environment_roles=(EnvironmentRole.BASELINE, EnvironmentRole.CANDIDATE),
            require_complete_run=True,
            require_assertion_coverage=True,
            require_diff_coverage=True,
            thresholds=(_threshold(),),
        )

    @property
    def gate_profile(self) -> CompiledGateProfile:
        """Return the strict compiled gate."""
        return self._gate

    async def assertions(
        self,
        prepared: PreparedRun,
        record: ExecutionRecord,
    ) -> tuple[AssertionCall, ...]:
        """Bind a status-equals-200 assertion to one runtime snapshot."""
        del prepared
        observed = record.outcome
        assert type(observed) is ObservedExecution
        application = assertion_factories.application(self.assertion_rule, observed.snapshot)
        application = msgspec.structs.replace(
            application,
            application_id=RuleApplicationId(
                value=uuid5(record.work.scope.execution_id.value, "assertion:status-200")
            ),
        )
        return (
            AssertionCall(
                application=application,
                rule=self.assertion_rule,
                inputs=assertion_factories.inputs(self.assertion_rule, observed.snapshot),
                control=assertion_factories.control(),
            ),
        )

    async def diffs(
        self,
        prepared: PreparedRun,
        work: ComparisonWork,
        baseline: ExecutionRecord,
        candidate: ExecutionRecord,
    ) -> tuple[DiffCall, ...]:
        """Bind an exact status comparison to one runtime directed pair."""
        del prepared
        left = baseline.outcome
        right = candidate.outcome
        assert type(left) is ObservedExecution
        assert type(right) is ObservedExecution
        application = diff_factories.application(self.diff_rule, left.snapshot, right.snapshot)
        application = msgspec.structs.replace(
            application,
            application_id=RuleApplicationId(
                value=uuid5(work.comparison_id.value, "diff:status-exact")
            ),
            pair=DirectedEnvironmentPair(
                comparison_id=work.comparison_id,
                baseline_environment=work.pair.baseline,
                baseline_execution_id=work.baseline_execution_id,
                candidate_environment=work.pair.candidate,
                candidate_execution_id=work.candidate_execution_id,
            ),
        )
        return (
            DiffCall(
                application=application,
                rule=self.diff_rule,
                inputs=PreparedDiffInputs(
                    rule_fingerprint=self.diff_rule.fingerprint,
                    comparison_id=work.comparison_id,
                    baseline_execution_id=work.baseline_execution_id,
                    candidate_execution_id=work.candidate_execution_id,
                ),
                control=diff_factories.control(),
            ),
        )


def test_runtime_reuses_observations_across_real_assertions_diffs_and_gate(
    prepared_comparison_run: PreparedRun,
) -> None:
    cancellation = RunCancellation()
    driver, transport = _driver(prepared_comparison_run, cancellation)
    result = asyncio.run(
        run_prepared(
            prepared_comparison_run,
            driver=driver,
            evaluation=KernelPreparation(),
            cancellation=cancellation,
            clock=FixedClock(),
            events=RecordingEventSink(),
        )
    )

    assert all(len(item.assertions) == 1 for item in result.executions)
    assert all(
        len(item.outcome.diffs) == 1
        for item in result.comparisons
        if hasattr(item.outcome, "diffs")
    )
    assert result.gate.result.verdict is GateVerdict.PASSED
    assert result.gate.coverage is not None
    assert result.gate.coverage.covered_assertion_rules.value == len(result.executions)
    assert result.gate.coverage.covered_diff_rules.value == len(result.comparisons)
    assert len(transport.calls) == len(result.executions)
