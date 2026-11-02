"""Closed public operation aliases accepted by the v1alpha1 frontend."""

from __future__ import annotations

ASSERTION_OPERATION_ALIASES = frozenset(
    {
        "approximatelyEquals",
        "between",
        "cardinality",
        "conformsTo",
        "contains",
        "containsAll",
        "containsAny",
        "containsToken",
        "decodesAs",
        "digestEquals",
        "endsWith",
        "equals",
        "greater",
        "greaterOrEqual",
        "hasKey",
        "hasKeys",
        "hasOnlyKeys",
        "in",
        "isType",
        "length",
        "less",
        "lessOrEqual",
        "matches",
        "mediaTypeIs",
        "mediaTypeMatches",
        "notContains",
        "notEquals",
        "notIn",
        "notMatches",
        "startsWith",
    }
)

DIFF_TRANSFORM_ALIASES = frozenset(
    {
        "collectionFilter",
        "collectionMap",
        "digest",
        "exclude",
        "jsonShape",
        "length",
        "normalizeText",
        "project",
        "relativeSelect",
        "sortBy",
        "streamProject",
        "valueType",
    }
)

DIFF_REDUCER_ALIASES = frozenset(
    {
        "collect",
        "concatenate",
        "count",
        "distinct",
        "first",
        "groupBy",
        "last",
        "maximum",
        "minimum",
        "multiset",
        "objectFromEntries",
        "set",
        "sum",
    }
)

DIFF_PAIRING_ALIASES = frozenset({"keyed", "multiset", "ordered", "set", "singular"})

DIFF_COMPARATOR_ALIASES = frozenset(
    {
        "exact",
        "ignore",
        "jsonShape",
        "multiset",
        "numericTolerance",
        "ordered",
        "set",
        "subset",
        "superset",
        "textPredicate",
        "type",
    }
)


def supports_operation(name: object, aliases: frozenset[str]) -> bool:
    """Accept a public alias or its explicit built-in versioned identity."""
    if type(name) is not str:
        return False
    if name in aliases:
        return True
    prefix = "comparo.core/"
    suffix = "@1"
    return name.startswith(prefix) and name.endswith(suffix) and name[len(prefix) : -2] in aliases
