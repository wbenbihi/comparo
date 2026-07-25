"""Secret-free canonical fingerprinting for admitted compact run plans."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import cast

from comparo.core.domain.budgets import ResourceBudget
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import ConvertExpr
from comparo.core.domain.expressions import FallbackExpr
from comparo.core.domain.expressions import FileExpr
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import LiteralTextSegment
from comparo.core.domain.expressions import LookupExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import OptionalExpr
from comparo.core.domain.expressions import SensitiveLiteralExpr
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.identity import DefinitionRef
from comparo.core.domain.identity import InlineDefinitionRef
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.planning import PlanSlice
from comparo.core.domain.planning_inputs import PlanningAuthority
from comparo.core.domain.planning_inputs import RunInvocation
from comparo.core.domain.primitives import Count
from comparo.core.domain.primitives import Fingerprint
from comparo.core.domain.primitives import Sha256Digest
from comparo.core.domain.values import ExactValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonString
from comparo.core.planning.models import EffectiveExecution
from comparo.core.planning.models import EffectiveProfiles
from comparo.core.planning.models import PlanningSource


def plan_fingerprint(
    effective: EffectiveExecution,
    invocation: RunInvocation,
    authority: PlanningAuthority,
    slices: tuple[PlanSlice, ...],
    budgets: tuple[ResourceBudget, ...],
) -> Fingerprint:
    """Hash only stable structure, exact definitions, occurrence IDs, and finite limits."""
    content = {
        "algorithm": "comparo.run-plan/v1",
        "run": str(invocation.run_id.value),
        "invocation": str(invocation.invocation_id.value),
        "policy": _definition(authority.policy),
        "authority": {
            "allowedSubjects": _identities(authority.allowed_subjects),
            "allowedEnvironments": _identities(authority.allowed_environments),
            "ceilings": [
                [ceiling.resource.value, ceiling.limit.value] for ceiling in authority.ceilings
            ],
        },
        "subjects": [
            {
                "definition": _definition(subject.definition),
                "matrices": [
                    {
                        "binding": matrix.binding.value,
                        "definition": _definition(matrix.definition),
                        "cases": [case.case_id.value for case in matrix.cases],
                    }
                    for matrix in subject.matrices
                ],
                "profiles": _profiles(subject.profiles),
            }
            for subject in effective.subjects
        ],
        "environments": [_definition(item) for item in effective.environments],
        "comparisons": [
            [_definition(pair.baseline), _definition(pair.candidate)]
            for pair in effective.comparisons
        ],
        "trials": effective.trials.value,
        "maximumAttempts": effective.maximum_attempts.value,
        "failFast": effective.fail_fast,
        "valueLayers": [
            {
                "source": _source(layer.source),
                "environment": (
                    None if layer.environment is None else _definition(layer.environment)
                ),
                "parameters": _optional_expression(layer.parameters),
                "variables": _optional_expression(layer.variables),
                "secrets": _optional_secret_expression(layer.secrets),
            }
            for layer in effective.value_layers
        ],
        "provenance": [
            {
                "field": entry.field,
                "sources": [_source(source) for source in entry.sources],
            }
            for entry in effective.provenance
        ],
        "budgets": [
            {
                "resource": budget.resource.value,
                "requested": cast("Count", budget.requested).value,
                "effective": cast("Count", budget.effective).value,
                "cap": cast("Count", budget.cap).value,
            }
            for budget in budgets
        ],
        "cardinalities": [
            {
                "cells": item.cardinality.cells.value,
                "trials": item.cardinality.trials.value,
                "environmentExecutions": item.cardinality.environment_executions.value,
                "comparisons": item.cardinality.comparisons.value,
                "maximumAttempts": item.cardinality.maximum_attempts.value,
            }
            for item in slices
        ],
    }
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    return Fingerprint(
        algorithm="sha256",
        digest=Sha256Digest(hex_value=hashlib.sha256(encoded).hexdigest()),
    )


def _definition(reference: DefinitionRef | InlineDefinitionRef) -> dict[str, str]:
    if type(reference) is DefinitionRef:
        identity = reference.identity
        return {
            "namespace": identity.namespace.value,
            "kind": identity.kind.value,
            "id": identity.resource_id.value,
            "fingerprint": reference.fingerprint.digest.hex_value,
        }
    return {
        "namespace": reference.origin.owner.namespace.value,
        "kind": reference.kind.value,
        "id": reference.origin.fragment.value,
        "fingerprint": reference.fingerprint.digest.hex_value,
    }


def _profiles(profiles: EffectiveProfiles) -> dict[str, object]:
    return {
        "assertions": [_definition(item) for item in profiles.assertions],
        "diffs": [_definition(item) for item in profiles.diffs],
        "gate": None if profiles.gate is None else _definition(profiles.gate),
        "safety": [_definition(item) for item in profiles.safety],
        "redaction": None if profiles.redaction is None else _definition(profiles.redaction),
        "artifacts": None if profiles.artifacts is None else _definition(profiles.artifacts),
    }


def _identities(identities: tuple[ResourceIdentity, ...] | None) -> list[list[str]] | None:
    if identities is None:
        return None
    return [
        [identity.namespace.value, identity.kind.value, identity.resource_id.value]
        for identity in identities
    ]


def _source(source: PlanningSource) -> dict[str, object]:
    return {
        "kind": source.kind.value,
        "definition": (None if source.definition is None else _definition(source.definition)),
        "invocation": (None if source.invocation_id is None else str(source.invocation_id.value)),
    }


def _optional_expression(expression: ValueExpression | None) -> object:
    return None if expression is None else _expression(expression)


def _optional_secret_expression(expression: ValueExpression | None) -> object:
    return None if expression is None else _expression(expression, sensitive_context=True)


def _expression(expression: ValueExpression, *, sensitive_context: bool = False) -> object:
    """Return a stable semantic shape without ever opening a sensitive literal."""
    if type(expression) is LiteralExpr:
        return _literal_expression(expression, sensitive_context=sensitive_context)
    if type(expression) is SensitiveLiteralExpr:
        return [
            "sensitive-literal",
            expression.value.payload_type.__name__,
            [origin.value for origin in expression.value.origins],
        ]
    if type(expression) is LookupExpr:
        dependency = expression.dependency
        return [
            "lookup",
            dependency.namespace.value,
            list(dependency.symbol.segments),
            dependency.available_at.value,
        ]
    if type(expression) is FileExpr:
        return [
            "file",
            # The path is a public control input, not output data from the secret expression.
            _expression(expression.path),
            None if expression.conversion is None else expression.conversion.value,
            expression.trim.value,
            None if expression.maximum_bytes is None else expression.maximum_bytes.value,
        ]
    if type(expression) is FallbackExpr:
        return [
            "fallback",
            [
                _expression(source, sensitive_context=sensitive_context)
                for source in expression.sources
            ],
        ]
    if type(expression) is OptionalExpr:
        return [
            "optional",
            _expression(expression.source, sensitive_context=sensitive_context),
        ]
    if type(expression) is ConvertExpr:
        return [
            "convert",
            expression.target.value,
            _expression(expression.source, sensitive_context=sensitive_context),
        ]
    if type(expression) is ArrayExpr:
        return [
            "array",
            [_expression(item, sensitive_context=sensitive_context) for item in expression.items],
        ]
    if type(expression) is ObjectExpr:
        return [
            "object",
            [
                [
                    member.name,
                    _expression(member.expression, sensitive_context=sensitive_context),
                ]
                for member in expression.members
            ],
        ]
    return [
        "interpolation",
        expression.mode.value,
        [
            ["sensitive-text"]
            if type(segment) is LiteralTextSegment and sensitive_context
            else ["text", segment.value]
            if type(segment) is LiteralTextSegment
            else [
                "expression",
                _expression(segment.expression, sensitive_context=sensitive_context),
            ]
            for segment in expression.segments
        ],
    ]


def _literal_expression(expression: LiteralExpr, *, sensitive_context: bool) -> object:
    if sensitive_context:
        return ["context-sensitive-literal", type(expression.value).__name__]
    return ["literal", _exact_value(expression.value)]


def _exact_value(value: ExactValue) -> object:
    if type(value) is JsonNull:
        return ["null"]
    if type(value) is JsonBoolean:
        return ["boolean", value.value]
    if type(value) is JsonInteger:
        return ["integer", value.value]
    if type(value) is JsonDecimal:
        return ["number", _decimal_text(value.value)]
    if type(value) is JsonString:
        return ["string", value.value]
    if type(value) is JsonArray:
        return ["array", [_exact_value(item) for item in value.items]]
    if type(value) is JsonObject:
        return [
            "object",
            [[member.name, _exact_value(member.value)] for member in value.members],
        ]
    return [
        "bytes",
        len(value.value),
        hashlib.sha256(value.value).hexdigest(),
    ]


def _decimal_text(value: Decimal) -> str:
    sign, digits, exponent = value.as_tuple()
    coefficient = "".join(str(digit) for digit in digits)
    return f"{sign}:{coefficient}:{exponent}"
