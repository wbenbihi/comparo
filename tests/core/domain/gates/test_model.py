"""Exercise normalized gate units, policy contracts, and verdicts."""

from __future__ import annotations

import pytest

from comparo.core.domain.assertions.results import Satisfied
from comparo.core.domain.gates.model import AssertionGateOutcome
from comparo.core.domain.gates.model import AssertionGateRuleOutcome
from comparo.core.domain.gates.model import AssertionGateSummary
from comparo.core.domain.gates.model import CompiledGateProfile
from comparo.core.domain.gates.model import EnvironmentRole
from comparo.core.domain.gates.model import GateFailureReason
from comparo.core.domain.gates.model import GateResult
from comparo.core.domain.gates.model import GateSeverity
from comparo.core.domain.gates.model import GateThreshold
from comparo.core.domain.gates.model import GateVerdict
from comparo.core.domain.gates.model import NormalizedFactSummary
from comparo.core.domain.gates.model import RunCompletion
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from tests.core.domain.gates.test_normalization import _assertion_fact
from tests.core.domain.gates.test_normalization import _zero_assertion_counts


def _fingerprint() -> Fingerprint:
    return Fingerprint(algorithm="sha256", digest=Sha256Digest(hex_value="3" * 64))


def _resource(kind: ResourceKind, name: str) -> ResourceIdentity:
    return ResourceIdentity(
        namespace=NamespaceId(value="demo"),
        kind=kind,
        resource_id=ResourceId(value=name),
    )


def _gate_definition() -> DefinitionRef:
    return DefinitionRef(
        identity=_resource(ResourceKind.GATE_PROFILE, "release"),
        fingerprint=_fingerprint(),
    )


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


def test_exact_zero_assertion_still_counts_as_evaluated_rule_coverage() -> None:
    fact = _assertion_fact(Satisfied(), _zero_assertion_counts())
    rule = AssertionGateRuleOutcome(fact=fact)
    assertion = AssertionGateSummary(
        role=EnvironmentRole.BASELINE,
        rules=(rule,),
    )
    summary = NormalizedFactSummary(
        completion=RunCompletion.COMPLETE,
        assertions=(assertion,),
    )

    assert summary.assertions[0].rules[0].outcome is AssertionGateOutcome.SATISFIED
    assert summary.assertions[0].rules[0].selected_matches.value == 0


def test_gate_profile_is_reduction_policy_not_evaluation_control() -> None:
    profile = CompiledGateProfile(
        definition=_gate_definition(),
        severities=(GateSeverity.BLOCKING,),
        environment_roles=(EnvironmentRole.BASELINE, EnvironmentRole.CANDIDATE),
        require_complete_run=True,
        require_assertion_coverage=True,
        require_diff_coverage=True,
        thresholds=(_threshold(),),
    )

    assert profile.require_complete_run
    assert not hasattr(profile, "fail_fast")


def test_requested_and_not_requested_verdict_contracts_are_distinct() -> None:
    assert GateResult(verdict=GateVerdict.NOT_REQUESTED, profile=None).reasons == ()
    with pytest.raises(ValueError, match="reason"):
        GateResult(verdict=GateVerdict.FAILED, profile=_gate_definition())

    failed = GateResult(
        verdict=GateVerdict.FAILED,
        profile=_gate_definition(),
        reasons=(GateFailureReason.RUN_INCOMPLETE,),
    )
    assert failed.verdict is GateVerdict.FAILED
