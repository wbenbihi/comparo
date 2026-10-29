"""The payments-api fixture universe — mockup data for the visual phase.

Content mirrors the validated mockups so screens can be checked against them
one-to-one. Nothing here touches the backend.
"""

from __future__ import annotations

from comparo.tuibis.viewmodels import BodyLine
from comparo.tuibis.viewmodels import EntityKind
from comparo.tuibis.viewmodels import EnvironmentVM
from comparo.tuibis.viewmodels import ListItemVM
from comparo.tuibis.viewmodels import Pill
from comparo.tuibis.viewmodels import PillKind
from comparo.tuibis.viewmodels import ProjectVM
from comparo.tuibis.viewmodels import ProvenancedValue
from comparo.tuibis.viewmodels import RelationVM
from comparo.tuibis.viewmodels import RequestVM
from comparo.tuibis.viewmodels import StyledCell
from comparo.tuibis.viewmodels import StyledLine
from comparo.tuibis.viewmodels import TreeGroupVM
from comparo.tuibis.viewmodels import TreeLeafVM

PRICE_QUOTE = RequestVM(
    name="Price quote",
    method="GET",
    path="/v1/quote",
    description="Quotes a price for a plan and currency.",
    tags=("pricing", "quote"),
    base_url="https://staging.api.payments.local",
    resolved_env="staging",
    resolved_cell="pro·USD",
    query=(
        ProvenancedValue("plan", "pro", "⊞ Plans.plan"),
        ProvenancedValue("currency", "USD", "⊞ Plans.currency"),
        ProvenancedValue("amount", "1000", "authored literal"),
    ),
    headers=(
        ProvenancedValue("accept", "application/json", "authored"),
        ProvenancedValue("x-api-version", "2024-05-01", "⌂ staging.API_VERSION"),
        ProvenancedValue("x-client-id", "payments-api", "$ CLIENT_ID"),
        ProvenancedValue("authorization", "* REDACTED", "$ BEARER_TOKEN sealed", redacted=True),
    ),
    headers_note="6 · 2 from environment",
    body_lines=(),
    matrix_pill=Pill(PillKind.MATRIX, "⊞ Plans · 6 cells"),
    variants_line="basic·USD  basic·CAD  basic·EUR  pro·USD  pro·CAD  pro·EUR",
    expected_line="2xx · application/json · § quote-response strict",
    checks_line="◈ 28 rules · 3 blocking    Δ 12 rules · 4 tolerances",
    history_line="14 saved runs · last x FAILED 74ecaa · today 14:42",
    uses=(
        RelationVM(
            EntityKind.MATRIX,
            "Plans",
            "plan × currency → 6 cells",  # noqa: RUF001
            "matrices/plans.yaml:4",
        ),
        RelationVM(EntityKind.ENVIRONMENT, "staging", "BASE_URL · API_VERSION", ".env.staging"),
        RelationVM(EntityKind.VALUE, "CLIENT_ID", "project variable", "values/common.yaml:9"),
        RelationVM(
            EntityKind.SCHEMA, "quote-response", "strict response contract", "schemas/quote.json"
        ),
    ),
    used_by=(
        RelationVM(
            EntityKind.ASSERTION_PROFILE,
            "order-contract",
            "4 rules target this request",
            "profiles/assert.yaml:12",
        ),
        RelationVM(
            EntityKind.EXECUTION_PROFILE,
            "smoke-run",
            "2 execution profiles include it",
            "profiles/exec.yaml:5",
        ),
    ),
)

CHECKOUT = RequestVM(
    name="Checkout",
    method="POST",
    path="/v1/checkout",
    description="Creates an order from the selected quote and customer.",
    tags=("checkout", "write"),
    base_url="https://staging.api.payments.local",
    resolved_env="staging",
    resolved_cell="pro·USD",
    query=(),
    headers=(
        ProvenancedValue("content-type", "application/json", "authored"),
        ProvenancedValue("authorization", "* REDACTED", "$ BEARER_TOKEN sealed", redacted=True),
    ),
    headers_note="4 · 1 from environment",
    body_lines=(
        BodyLine(1, "{"),
        BodyLine(2, '  "quote_id": "qt_01J4Z8R8NQ",', "$use Value quote_id · once per cell"),
        BodyLine(
            3, '  "customer": { "id": "cust_2f9b8d6a" },', "$file customers.json · path concealed"
        ),
        BodyLine(4, '  "items": [ { "sku": "plan_pro_monthly" } ],', "⊞ Plans.pro.sku"),
        BodyLine(5, '  "currency": "USD",', "⊞ Plans.currency"),
        BodyLine(
            6, '  "payment": { "token": "* REDACTED" },', "$secret CHECKOUT_TOKEN", redacted=True
        ),
        BodyLine(7, '  "metadata": { "source": "comparo/1.0" }'),
        BodyLine(8, "}"),
    ),
    matrix_pill=Pill(PillKind.MATRIX, "⊞ Plans · 6 cells"),
    variants_line="basic·USD  basic·CAD  basic·EUR  pro·USD  pro·CAD  pro·EUR",
    expected_line="2xx · application/json · § checkout-response strict",
    checks_line="◈ 12 rules · 2 blocking    Δ 8 rules · 2 tolerances",
    history_line="9 saved runs · last x FAILED 74ecaa · today 14:42",
    uses=(
        RelationVM(
            EntityKind.MATRIX,
            "Plans",
            "plan × currency → 6 cells",  # noqa: RUF001
            "matrices/plans.yaml:4",
        ),
        RelationVM(EntityKind.VALUE, "CHECKOUT_TOKEN", "sealed secret", "checkout.yaml:31"),
    ),
    used_by=(
        RelationVM(
            EntityKind.EXECUTION_PROFILE,
            "release-gate",
            "the full pre-release gate",
            "profiles/exec.yaml:1",
        ),
    ),
    write_capable=True,
)

PRICE_QUOTE_AUTHORED = RequestVM(
    name="Price quote",
    method="GET",
    path="/v1/quote",
    description="Authored source - directives shown before any resolution.",
    tags=("pricing", "quote"),
    base_url="$env BASE_URL",
    resolved_env="staging",
    resolved_cell="pro·USD",
    query=(
        ProvenancedValue("plan", "{{ matrix.plan }}", "⊞ Plans axis plan"),
        ProvenancedValue("currency", "{{ matrix.currency }}", "⊞ Plans axis currency"),
        ProvenancedValue("amount", "1000", "authored literal"),
    ),
    headers=(
        ProvenancedValue("accept", "application/json", "authored"),
        ProvenancedValue("x-api-version", "$env API_VERSION", "resolved at run time"),
        ProvenancedValue("x-client-id", "$var CLIENT_ID", "values/common.yaml:9"),
        ProvenancedValue("authorization", "$secret BEARER_TOKEN", "sealed", redacted=True),
    ),
    headers_note="6 · authored form",
    body_lines=(),
    matrix_pill=Pill(PillKind.MATRIX, "⊞ Plans · unexpanded"),
    variants_line="{{ matrix.plan }} x {{ matrix.currency }} → 6 cells at run time",
    expected_line="2xx · application/json · § quote-response strict",
    checks_line="◈ 28 rules · 3 blocking    Δ 12 rules · 4 tolerances",
    history_line="requests/quote.yaml:1 · authored 2026-07-12",
    uses=PRICE_QUOTE.uses,
    used_by=PRICE_QUOTE.used_by,
)

CHECKOUT_AUTHORED = RequestVM(
    name="Checkout",
    method="POST",
    path="/v1/checkout",
    description="Authored source - directives shown before any resolution.",
    tags=("checkout", "write"),
    base_url="$env BASE_URL",
    resolved_env="staging",
    resolved_cell="pro·USD",
    query=(),
    headers=(
        ProvenancedValue("content-type", "application/json", "authored"),
        ProvenancedValue("authorization", "$secret BEARER_TOKEN", "sealed", redacted=True),
    ),
    headers_note="4 · authored form",
    body_lines=(
        BodyLine(1, "{"),
        BodyLine(2, '  "quote_id": "$use quote_id",', "Value directive · captured upstream"),
        BodyLine(3, '  "customer": "$file customers.json#/rows/*",', "file fan-out"),
        BodyLine(4, '  "items": [ { "sku": "{{ matrix.plan.sku }}" } ],', "⊞ Plans axis plan"),
        BodyLine(5, '  "currency": "{{ matrix.currency }}",', "⊞ Plans axis currency"),
        BodyLine(6, '  "payment": { "token": "$secret CHECKOUT_TOKEN" },', "sealed", redacted=True),
        BodyLine(7, '  "metadata": { "source": "comparo/1.0" }'),
        BodyLine(8, "}"),
    ),
    matrix_pill=Pill(PillKind.MATRIX, "⊞ Plans · unexpanded"),
    variants_line="{{ matrix.plan }} x {{ matrix.currency }} → 6 cells at run time",
    expected_line="2xx · application/json · § checkout-response strict",
    checks_line="◈ 12 rules · 2 blocking    Δ 8 rules · 2 tolerances",
    history_line="requests/checkout.yaml:1 · authored 2026-07-12",
    uses=CHECKOUT.uses,
    used_by=CHECKOUT.used_by,
    write_capable=True,
)


def _explain(path: str, directive: str, note: str) -> tuple[StyledLine, ...]:
    """Build the two focused-path explainer rows for one body line."""
    return (
        StyledLine(
            (
                StyledCell("FOCUS  ", "label"),
                StyledCell(path, "bright"),
                StyledCell("  ·  ", "dim"),
                StyledCell(directive, "baseline"),
            )
        ),
        StyledLine((StyledCell("       ", "fg"), StyledCell(note, "muted"))),
    )


BODY_EXPLAINERS: dict[str, dict[str, tuple[StyledLine, ...]]] = {
    "req:checkout": {
        "body:2": _explain(
            "$.quote_id",
            "$use Value quote_id",
            "resolved once per cell · captured from Price quote",
        ),
        "body:3": _explain(
            "$.customer.id", "$file customers.json", "one row per cell · path concealed"
        ),
        "body:4": _explain(
            "$.items[0].sku", "⊞ Plans.plan.sku", "axis plan · pro → plan_pro_monthly"
        ),
        "body:5": _explain("$.currency", "⊞ Plans.currency", "axis currency · pro·USD cell"),
        "body:6": _explain(
            "$.payment.token",
            "$secret CHECKOUT_TOKEN",
            "never displayed · sealed at rest and in reports",
        ),
        "body:7": _explain("$.metadata.source", "authored literal", "constant across all 6 cells"),
    },
}

_PLANS_CASES: tuple[tuple[str, str, str, str], ...] = (
    ("cell:basic-USD", "basic", "USD", "plan_basic_monthly"),
    ("cell:basic-CAD", "basic", "CAD", "plan_basic_monthly"),
    ("cell:basic-EUR", "basic", "EUR", "plan_basic_monthly"),
    ("cell:pro-USD", "pro", "USD", "plan_pro_monthly"),
    ("cell:pro-CAD", "pro", "CAD", "plan_pro_monthly"),
    ("cell:pro-EUR", "pro", "EUR", "plan_pro_monthly"),
)


def matrix_axes() -> tuple[StyledLine, ...]:
    """Axes rows for the Plans matrix inspector."""
    return (
        StyledLine(
            (
                StyledCell("AXIS", "label", width=10),
                StyledCell("VALUES", "label", width=16),
                StyledCell("INJECTS", "label"),
            )
        ),
        StyledLine(
            (
                StyledCell("plan", "fg", width=10),
                StyledCell("basic, pro", "muted", width=16),
                StyledCell("query.plan · body.items[0].sku", "dim"),
            )
        ),
        StyledLine(
            (
                StyledCell("currency", "fg", width=10),
                StyledCell("USD, CAD, EUR", "muted", width=16),
                StyledCell("query.currency · body.currency", "dim"),
            )
        ),
    )


def matrix_cases() -> tuple[ListItemVM, ...]:
    """Selectable case rows for the Plans matrix inspector."""
    items: list[ListItemVM] = [
        ListItemVM(
            "cases:header",
            StyledLine(
                (
                    StyledCell("CASE", "label", width=14),
                    StyledCell("PLAN", "label", width=8),
                    StyledCell("CURRENCY", "label", width=10),
                    StyledCell("SKU", "label"),
                )
            ),
            header=True,
        )
    ]
    items.extend(
        ListItemVM(
            key,
            StyledLine(
                (
                    StyledCell(f"{plan}·{currency}", "fg", width=10),
                    StyledCell(plan, "muted", width=8),
                    StyledCell(currency, "muted", width=10),
                    StyledCell(sku, "dim"),
                )
            ),
            selectable=True,
            selected=True,
            search_text=f"{plan} {currency} {sku}",
        )
        for key, plan, currency, sku in _PLANS_CASES
    )
    return tuple(items)


def matrix_case_details() -> dict[str, StyledLine]:
    """SELECTED-line preview per matrix case key."""
    return {
        key: StyledLine(
            (
                StyledCell(" SELECTED  ", "label"),
                StyledCell(f"{plan}·{currency}", "bright"),
                StyledCell("  →  ", "dim"),
                StyledCell(f"query.plan {plan} · query.currency {currency} · sku {sku}", "fg"),
            )
        )
        for key, plan, currency, sku in _PLANS_CASES
    }


def _graph_group(key: str, label: str) -> ListItemVM:
    """Non-navigable section header row for the dependency graph."""
    return ListItemVM(key, StyledLine((StyledCell(label, "label"),)), header=True)


def _graph_node(key: str, connector: str, name: str, note: str) -> ListItemVM:
    """One navigable dependency node row with its box-drawing connector."""
    return ListItemVM(
        key,
        StyledLine(
            (
                StyledCell(f"{connector} ", "dim"),
                StyledCell(f"{name:<22}", "fg"),
                StyledCell(note, "muted"),
            )
        ),
        search_text=name,
    )


def _graph_sub(key: str, text: str) -> ListItemVM:
    """Nested, non-navigable inject/$ref detail row under a graph node."""
    return ListItemVM(key, StyledLine((StyledCell(text, "dim"),)), dimmed=True)


def _quote_graph() -> tuple[ListItemVM, ...]:
    """Dependency graph rows for the Price quote request."""
    return (
        _graph_group("hdr:uses:quote", "USES"),
        _graph_node("mat:plans", "├─", "⊞ Plans", "matrix · 2 axes · 6 cells"),
        _graph_sub("sub:quote:inject", "│    inject → query.plan · query.currency"),
        _graph_node("env:staging", "├─", "⌂ staging", "environment · BASE_URL · API_VERSION"),
        _graph_sub("sub:quote:ref", "│    $ref → .env.staging"),
        _graph_node("env:production", "├─", "⌂ production", "environment · candidate lane"),
        _graph_node(
            "val:client-id", "├─", "$ CLIENT_ID", "project variable · values/common.yaml:9"
        ),
        _graph_node("sch:quote", "└─", "§ quote-response", "strict response contract"),
        _graph_group("hdr:usedby:quote", "USED BY"),
        _graph_node("prof:order-contract", "├─", "◈ order-contract", "4 rules target this request"),
        _graph_node("prof:release-gate", "├─", "▶ release-gate", "the full pre-release gate"),
        _graph_node("prof:smoke-run", "├─", "▶ smoke-run", "2 execution profiles include it"),
        _graph_node("runs:quote", "└─", "▤ 14 saved runs", "reports keep full evidence"),
        _graph_group(
            "hdr:impact:quote", "IMPACT  a change here touches 6 cells · 3 profiles · 14 saved runs"
        ),
    )


def _checkout_graph() -> tuple[ListItemVM, ...]:
    """Dependency graph rows for the Checkout request."""
    return (
        _graph_group("hdr:uses:checkout", "USES"),
        _graph_node("mat:plans", "├─", "⊞ Plans", "matrix · 2 axes · 6 cells"),
        _graph_sub("sub:checkout:inject", "│    inject → body.items[0].sku · body.currency"),
        _graph_node("env:staging", "├─", "⌂ staging", "environment · BASE_URL · BEARER_TOKEN"),
        _graph_sub("sub:checkout:ref", "│    $ref → .env.staging"),
        _graph_node("val:checkout-token", "└─", "$ CHECKOUT_TOKEN", "sealed secret · never shown"),
        _graph_group("hdr:usedby:checkout", "USED BY"),
        _graph_node("prof:release-gate", "├─", "▶ release-gate", "the full pre-release gate"),
        _graph_node("runs:checkout", "└─", "▤ 9 saved runs", "reports keep full evidence"),
        _graph_group(
            "hdr:impact:checkout",
            "IMPACT  a change here touches 6 cells · 1 profile · 9 saved runs",
        ),
    )


GRAPHS: dict[str, tuple[ListItemVM, ...]] = {
    "req:quote": _quote_graph(),
    "req:checkout": _checkout_graph(),
}

STAGING = EnvironmentVM(
    name="staging",
    url="https://staging.api.payments.local",
    pills=(
        Pill(PillKind.LIVE, "LIVE"),
        Pill(PillKind.DEFAULT, "DEFAULT"),
        Pill(PillKind.BASELINE, "▌ BASELINE of last run"),
    ),
    connection_line="timeout 30s   tls on   redirects same-origin",
    safety_line=(
        "safety non-production-shared   writes with confirmation   "
        "hosts 2 permitted   remote schemas 1 trusted"
    ),
    values_note="18 names · 4 sealed · 0 unresolved",
    values=(
        ("BASE_URL", "standard", ".env.staging:1", "14 requests"),
        ("API_VERSION", "standard", ".env.staging:2", "14 requests"),
        ("BEARER_TOKEN", "* REDACTED", ".env.staging:3", "12 requests"),
        ("TENANT_ID", "* REDACTED", "services/payments.env:7", "2 requests"),
        ("PORT", "session ovr", "this session only · 8443", "all"),
    ),
    precedence_line=("process env → .env → .env.staging → services/payments.env → session (1)"),
    usage_line="used by 14 requests · 3 profiles · 2 schemas",
)


def project() -> ProjectVM:
    """Build the payments-api project fixture."""
    matrix6 = Pill(PillKind.MATRIX, "⊞ 6")
    matrix3 = Pill(PillKind.MATRIX, "⊞ 3")
    groups = (
        TreeGroupVM(
            "ENVIRONMENTS",
            "2",
            (
                TreeLeafVM(
                    "env:staging",
                    EntityKind.ENVIRONMENT,
                    "staging",
                    pills=(Pill(PillKind.LIVE, "LIVE"), Pill(PillKind.DEFAULT, "DEFAULT")),
                ),
                TreeLeafVM(
                    "env:production",
                    EntityKind.ENVIRONMENT,
                    "production",
                    pills=(Pill(PillKind.LIVE, "LIVE"),),
                ),
            ),
        ),
        TreeGroupVM(
            "REQUESTS",
            "21",
            (
                TreeLeafVM(
                    "req:quote", EntityKind.REQUEST, "Price quote", method="GET", pills=(matrix6,)
                ),
                TreeLeafVM(
                    "req:checkout", EntityKind.REQUEST, "Checkout", method="POST", pills=(matrix6,)
                ),
                TreeLeafVM("req:product", EntityKind.REQUEST, "Get product", method="GET"),
                TreeLeafVM(
                    "req:orders", EntityKind.REQUEST, "List orders", method="GET", pills=(matrix3,)
                ),
                TreeLeafVM("req:create", EntityKind.REQUEST, "Create order", method="POST"),
            ),
            omitted="… 16 more · / filter",
        ),
        TreeGroupVM(
            "MATRICES",
            "1",
            (TreeLeafVM("mat:plans", EntityKind.MATRIX, "Plans", detail="2 axes · 6"),),
        ),
        TreeGroupVM(
            "SCHEMAS",
            "2",
            (TreeLeafVM("sch:quote", EntityKind.SCHEMA, "quote-response", detail="strict"),),
        ),
        TreeGroupVM("PROFILES", "◈ 2 · Δ 1 · ▶ 1", (), collapsed=True),
        TreeGroupVM("VALUES", "3", (), collapsed=True),
    )
    return ProjectVM(
        name="payments-api",
        environments="staging ⇄ production",
        baseline="staging",
        candidate="production",
        groups=groups,
        requests={
            "req:quote": PRICE_QUOTE,
            "req:quote:authored": PRICE_QUOTE_AUTHORED,
            "req:checkout": CHECKOUT,
            "req:checkout:authored": CHECKOUT_AUTHORED,
        },
        envs={"env:staging": STAGING},
    )
