"""Secret-free structural plan fingerprint coverage."""

from decimal import Decimal
from typing import cast

import msgspec

from comparo.core.configuration.canonical import CanonicalObject
from comparo.core.configuration.canonical import CompiledProject
from comparo.core.domain.budgets import BudgetResource
from comparo.core.domain.expressions import ArrayExpr
from comparo.core.domain.expressions import ConversionType
from comparo.core.domain.expressions import ConvertExpr
from comparo.core.domain.expressions import ExpressionDependency
from comparo.core.domain.expressions import ExpressionSegment
from comparo.core.domain.expressions import FallbackExpr
from comparo.core.domain.expressions import FileExpr
from comparo.core.domain.expressions import FileTrim
from comparo.core.domain.expressions import InterpolationExpr
from comparo.core.domain.expressions import InterpolationMode
from comparo.core.domain.expressions import LiteralExpr
from comparo.core.domain.expressions import LiteralTextSegment
from comparo.core.domain.expressions import LookupExpr
from comparo.core.domain.expressions import ObjectExpr
from comparo.core.domain.expressions import ObjectExpressionMember
from comparo.core.domain.expressions import OptionalExpr
from comparo.core.domain.expressions import SensitiveLiteralExpr
from comparo.core.domain.expressions import SymbolPath
from comparo.core.domain.expressions import ValueExpression
from comparo.core.domain.expressions import ValueNamespace
from comparo.core.domain.identity import NamespaceId
from comparo.core.domain.identity import ResourceId
from comparo.core.domain.identity import ResourceIdentity
from comparo.core.domain.identity import ResourceKind
from comparo.core.domain.planning_inputs import InvocationValueOverlay
from comparo.core.domain.primitives import ByteCount
from comparo.core.domain.primitives import PositiveCount
from comparo.core.domain.provenance import ProvenanceEntry
from comparo.core.domain.provenance import ProvenanceKind
from comparo.core.domain.provenance import ValueProvenance
from comparo.core.domain.scopes import ValueScopeKind
from comparo.core.domain.sensitivity import SensitiveValue
from comparo.core.domain.sensitivity import TaintOriginId
from comparo.core.domain.values import BytesValue
from comparo.core.domain.values import ExactValue
from comparo.core.domain.values import JsonArray
from comparo.core.domain.values import JsonBoolean
from comparo.core.domain.values import JsonDecimal
from comparo.core.domain.values import JsonInteger
from comparo.core.domain.values import JsonNull
from comparo.core.domain.values import JsonObject
from comparo.core.domain.values import JsonObjectMember
from comparo.core.domain.values import JsonString
from comparo.core.planning import plan_run
from comparo.core.planning.fingerprints import _expression
from comparo.core.planning.fingerprints import _identities
from tests.core.planning.conftest import authority
from tests.core.planning.conftest import invocation


def _provenance() -> ValueProvenance:
    return ValueProvenance(entries=(ProvenanceEntry(kind=ProvenanceKind.INVOCATION),))


def _literal(value: ExactValue) -> LiteralExpr:
    return LiteralExpr(value=value, provenance=_provenance())


def _lookup() -> LookupExpr:
    return LookupExpr(
        dependency=ExpressionDependency(
            namespace=ValueNamespace.ENV,
            symbol=SymbolPath(segments=("TOKEN",)),
            available_at=ValueScopeKind.ENVIRONMENT,
        ),
        provenance=_provenance(),
    )


def test_expression_fingerprinting_is_exhaustive_and_never_opens_secrets() -> None:
    public_values: tuple[ExactValue, ...] = (
        JsonNull(),
        JsonBoolean(value=True),
        JsonInteger(value=7),
        JsonDecimal(value=Decimal("1.25")),
        JsonString(value="public"),
        JsonArray(items=(JsonInteger(value=1),)),
        JsonObject(members=(JsonObjectMember(name="key", value=JsonString(value="value")),)),
        BytesValue(value=b"bytes"),
    )
    lookup = _lookup()
    raw_file = FileExpr(
        path=_literal(JsonString(value="fixture")),
        conversion=None,
        trim=FileTrim.NONE,
        provenance=_provenance(),
        maximum_bytes=ByteCount(value=10),
    )
    converted_file = FileExpr(
        path=_literal(JsonString(value="fixture")),
        conversion=ConversionType.STRING,
        trim=FileTrim.NONE,
        provenance=_provenance(),
    )
    expressions: tuple[ValueExpression, ...] = (
        *(_literal(value) for value in public_values),
        SensitiveLiteralExpr(
            value=SensitiveValue(
                JsonString(value="literal-secret-sentinel"),
                origins=(TaintOriginId(value="secret/literal"),),
            ),
            provenance=_provenance(),
        ),
        lookup,
        raw_file,
        converted_file,
        FallbackExpr(sources=(lookup, raw_file), provenance=_provenance()),
        OptionalExpr(source=lookup, provenance=_provenance()),
        ConvertExpr(
            source=lookup,
            target=ConversionType.STRING,
            provenance=_provenance(),
        ),
        ArrayExpr(items=(lookup,), provenance=_provenance()),
        ObjectExpr(
            members=(ObjectExpressionMember(name="token", expression=lookup),),
            provenance=_provenance(),
        ),
        InterpolationExpr(
            mode=InterpolationMode.EMBEDDED_TEXT,
            segments=(
                LiteralTextSegment(value="Bearer "),
                ExpressionSegment(expression=lookup),
            ),
            provenance=_provenance(),
        ),
    )
    shapes = tuple(_expression(expression) for expression in expressions)
    assert len(shapes) == len(expressions)
    assert "literal-secret-sentinel" not in repr(shapes)
    assert "secret/literal" in repr(shapes)


def test_plan_fingerprint_binds_values_budgets_and_authority_projection(
    comparison_project: CompiledProject,
) -> None:
    project_spec = cast("CanonicalObject", comparison_project.project.spec)
    variables = cast(
        "ValueExpression",
        next(member.value for member in project_spec.members if member.name == "variables"),
    )
    plain = plan_run(comparison_project, invocation(), authority()).prepared
    repeated = plan_run(comparison_project, invocation(), authority()).prepared
    assert plain is not None
    assert repeated is not None
    assert plain.plan.fingerprint == repeated.plan.fingerprint

    variable_overlay = plan_run(
        comparison_project,
        invocation(values=(InvocationValueOverlay(variables=variables),)),
        authority(),
    ).prepared
    secret_overlay = plan_run(
        comparison_project,
        invocation(values=(InvocationValueOverlay(secrets=variables),)),
        authority(),
    ).prepared
    assert variable_overlay is not None
    assert secret_overlay is not None
    assert variable_overlay.plan.fingerprint != secret_overlay.plan.fingerprint

    changed_authority = msgspec.structs.replace(
        authority(),
        ceilings=tuple(
            msgspec.structs.replace(item, limit=PositiveCount(value=99_999))
            if item.resource is BudgetResource.MATRIX_CELLS
            else item
            for item in authority().ceilings
        ),
    )
    changed = plan_run(comparison_project, invocation(), changed_authority).prepared
    assert changed is not None
    assert changed.plan.fingerprint != plain.plan.fingerprint


def test_authority_identity_encoding_distinguishes_unbounded_and_empty_allowlists() -> None:
    identity = ResourceIdentity(
        namespace=NamespaceId(value="local"),
        kind=ResourceKind.REQUEST,
        resource_id=ResourceId(value="check"),
    )
    assert _identities(None) is None
    assert _identities(()) == []
    assert _identities((identity,)) == [["local", "Request", "check"]]
