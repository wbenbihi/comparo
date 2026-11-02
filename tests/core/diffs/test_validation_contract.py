"""Cover the exact public diff call-boundary identity bindings."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import msgspec
import pytest

from comparo.core.diffs import DiffControl
from comparo.core.diffs import PreparedDiffInputs
from comparo.core.diffs.validation import validate_diff_inputs
from comparo.core.domain.diffs.rules import CompiledDiffRule
from comparo.core.domain.diffs.rules import DiffApplication
from comparo.core.domain.identity import ComparisonId
from comparo.core.domain.identity import EffectiveRuleId
from comparo.core.domain.identity import EnvironmentExecutionId
from comparo.core.domain.identity import RuleId
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.selection import SelectionSnapshot
from tests.core.diffs.factories import application
from tests.core.diffs.factories import control
from tests.core.diffs.factories import inputs
from tests.core.diffs.factories import rule
from tests.core.diffs.factories import snapshots


def _boundary() -> tuple[
    SelectionSnapshot,
    SelectionSnapshot,
    DiffApplication,
    CompiledDiffRule,
    PreparedDiffInputs,
    DiffControl,
]:
    baseline, candidate = snapshots()
    compiled = rule()
    bound = application(compiled, baseline, candidate)
    return baseline, candidate, bound, compiled, inputs(compiled), control()


def _fingerprint() -> Fingerprint:
    return Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value="e" * 64),
    )


def _changed_observation(
    snapshot: SelectionSnapshot,
    field: str,
    value: object,
) -> SelectionSnapshot:
    changed = msgspec.structs.replace(snapshot)
    observation = msgspec.structs.replace(snapshot.observation)
    msgspec.structs.force_setattr(observation, field, value)
    msgspec.structs.force_setattr(changed, "observation", observation)
    return changed


def test_valid_diff_boundary_accepts_one_exact_directed_application() -> None:
    validate_diff_inputs(*_boundary())


def test_boundary_requires_two_typed_selection_snapshots() -> None:
    baseline, candidate, bound, compiled, prepared, diff_control = _boundary()
    with pytest.raises(TypeError, match="two SelectionSnapshot"):
        validate_diff_inputs(
            cast("SelectionSnapshot", object()),
            candidate,
            bound,
            compiled,
            prepared,
            diff_control,
        )
    with pytest.raises(TypeError, match="two SelectionSnapshot"):
        validate_diff_inputs(
            baseline,
            cast("SelectionSnapshot", object()),
            bound,
            compiled,
            prepared,
            diff_control,
        )


def test_boundary_requires_typed_application_rule_and_control() -> None:
    baseline, candidate, bound, compiled, prepared, diff_control = _boundary()
    with pytest.raises(TypeError, match="DiffApplication and CompiledDiffRule"):
        validate_diff_inputs(
            baseline,
            candidate,
            cast("DiffApplication", object()),
            compiled,
            prepared,
            diff_control,
        )
    with pytest.raises(TypeError, match="DiffApplication and CompiledDiffRule"):
        validate_diff_inputs(
            baseline,
            candidate,
            bound,
            cast("CompiledDiffRule", object()),
            prepared,
            diff_control,
        )
    with pytest.raises(TypeError, match="DiffControl"):
        validate_diff_inputs(
            baseline,
            candidate,
            bound,
            compiled,
            prepared,
            cast("DiffControl", object()),
        )


def test_application_must_match_definition_effective_id_and_fingerprint() -> None:
    baseline, candidate, bound, compiled, prepared, diff_control = _boundary()
    mismatches = (
        msgspec.structs.replace(
            bound,
            definition=msgspec.structs.replace(
                bound.definition,
                rule_id=RuleId(value="foreign"),
            ),
        ),
        msgspec.structs.replace(
            bound,
            effective_id=EffectiveRuleId(value="foreign"),
        ),
        msgspec.structs.replace(bound, rule_fingerprint=_fingerprint()),
    )
    for mismatch in mismatches:
        with pytest.raises(ValueError, match="does not identify"):
            validate_diff_inputs(
                baseline,
                candidate,
                mismatch,
                compiled,
                prepared,
                diff_control,
            )


def test_snapshots_must_match_each_directed_execution_id() -> None:
    baseline, candidate, bound, compiled, prepared, diff_control = _boundary()
    wrong_baseline = _changed_observation(
        baseline,
        "execution_id",
        EnvironmentExecutionId(value=UUID(int=901)),
    )
    with pytest.raises(ValueError, match="baseline snapshot"):
        validate_diff_inputs(
            wrong_baseline,
            candidate,
            bound,
            compiled,
            prepared,
            diff_control,
        )
    wrong_candidate = _changed_observation(
        candidate,
        "execution_id",
        EnvironmentExecutionId(value=UUID(int=902)),
    )
    with pytest.raises(ValueError, match="candidate snapshot"):
        validate_diff_inputs(
            baseline,
            wrong_candidate,
            bound,
            compiled,
            prepared,
            diff_control,
        )


def test_snapshots_must_match_each_directed_environment() -> None:
    baseline, candidate, bound, compiled, prepared, diff_control = _boundary()
    wrong_baseline = _changed_observation(
        baseline,
        "environment",
        bound.pair.candidate_environment,
    )
    with pytest.raises(ValueError, match="baseline snapshot Environment"):
        validate_diff_inputs(
            wrong_baseline,
            candidate,
            bound,
            compiled,
            prepared,
            diff_control,
        )
    wrong_candidate = _changed_observation(
        candidate,
        "environment",
        bound.pair.baseline_environment,
    )
    with pytest.raises(ValueError, match="candidate snapshot Environment"):
        validate_diff_inputs(
            baseline,
            wrong_candidate,
            bound,
            compiled,
            prepared,
            diff_control,
        )


def test_prepared_inputs_must_match_comparison_and_both_execution_frames() -> None:
    baseline, candidate, bound, compiled, prepared, diff_control = _boundary()
    mismatches: tuple[PreparedDiffInputs, ...] = (
        msgspec.structs.replace(
            prepared,
            comparison_id=ComparisonId(value=UUID(int=903)),
        ),
        msgspec.structs.replace(
            prepared,
            baseline_execution_id=EnvironmentExecutionId(value=UUID(int=904)),
        ),
        msgspec.structs.replace(
            prepared,
            candidate_execution_id=EnvironmentExecutionId(value=UUID(int=905)),
        ),
    )
    for mismatch in mismatches:
        with pytest.raises(ValueError, match="directed pair frames"):
            validate_diff_inputs(
                baseline,
                candidate,
                bound,
                compiled,
                mismatch,
                diff_control,
            )


def test_boundary_delegates_exact_prepared_expression_coverage() -> None:
    baseline, candidate, bound, compiled, prepared, diff_control = _boundary()
    foreign = msgspec.structs.replace(prepared, rule_fingerprint=_fingerprint())
    with pytest.raises(ValueError, match="different compiled rule"):
        validate_diff_inputs(
            baseline,
            candidate,
            bound,
            compiled,
            foreign,
            diff_control,
        )
