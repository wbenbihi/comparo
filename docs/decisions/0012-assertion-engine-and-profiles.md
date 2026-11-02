# ADR 0012: Assertion engine and profiles

- Status: Accepted
- Date: 2026-07-24
- Owners: Comparo maintainers

## Context

Comparo needs reusable per-Environment assertions that operate consistently in local, TUI, and CI
execution. Selector absence, observation failure, predicate violation, evaluator failure, explicit
inapplicability, and gate decisions must remain separate.

## Accepted decisions

### Pure fact evaluator

The assertion engine is a pure function of a compiled rule, immutable Observation, and applicable
ResolutionFrame. It performs no HTTP, storage, reporting, sanitization, gating, or fail-fast work.
It produces stable parent AssertionFacts and concrete MatchFacts. HTTP error statuses remain valid
observations, and facts are reusable across binary comparisons.

Rules have stable ID, name, optional description and tags, static severity, selector, operator,
cardinality/quantifier behavior, optional applicability, completeness requirements, and typed
operator operands. Runtime operands may resolve in cell and Environment context; identity,
selection, operator, severity, and aggregation remain static. Definition identity, compiled
fingerprint, and Request/cell/Environment application identity are all retained.

### Selection, cardinality, and quantifiers

Selection distinguishes a ready ordered collection, including empty, from NoBody, decode failure,
incomplete, budget-exceeded, unsupported, cancelled, and unavailable states. Cardinality-only rules
may require exact or min/max match counts without a dummy predicate.

`one` requires exactly one match and a satisfied predicate. `all` and `any` require a non-empty
selection by default and aggregate every or at least one satisfied predicate. `none` succeeds when
no selected match satisfies the predicate and permits empty selection. Potentially plural
selectors require explicit quantification; statically singleton selectors may default to one.

Each parent retains selected/evaluated/satisfied/violated/error counts and structured child facts
at concrete locations. Evaluation and evidence may stream. Exact counts survive evidence budgets;
violating/counterexample evidence, bounded pass samples, truncation, and optional storage-backed
evidence are separate from the logical outcome and are sanitized before presentation.

### Types, applicability, and outcomes

Operators declare versioned IDs, accepted actual types, operand schema, collection awareness,
completeness requirements, determinism, and structured evidence. There is no implicit conversion
between text, numbers, booleans, null, bytes, missing, or structured values. Integers and decimals
share an exact numeric domain without parsing strings.

Assertion outcomes are satisfied, violated, skipped, unavailable, or error. False predicates and
cardinality mismatches violate. Only an explicit false applicability condition skips. Missing
observations, views, or operands are unavailable. Evaluator/plugin defects and runtime operand type
failures are errors. Invalid static rules fail compilation. Conditions reuse pure typed selectors
and operators with structured all/any composition; there is no general expression language and no
generic disabled flag.

Normal value selectors propagate body/view failure. Dedicated observation-state operators may
assert decodability, completeness, attempt counts, and similar facts so the state itself can be a
violation. Operators specify ordinary completeness; partial-prefix or full-stream requirements are
explicit and retained in evidence.

### Assignment

AssertionProfile definitions are role-neutral. EffectiveExecution assigns profiles to all selected
Environments, current baseline or candidate roles, or explicit Environment IDs. Environment
resources never acquire permanent roles. Assignments are expanded before execution, and identical
rule applications reuse facts rather than reevaluating per comparison.

### Built-in operator registry

YAML aliases resolve to versioned operator identities such as `comparo.core/equals@1`, and artifacts
retain the full identity. Stable semantics never change incompatibly under an existing version.
Operators declare static versus resolvable configuration, accepted actual and operand types,
completeness, purity, and structured evidence.

The accepted equality/type set is equals, notEquals, in, notIn, and isType. Equality is strict and
structural: object order is insignificant, array order remains significant, exact numeric domains
interoperate without parsing strings, and missing never equals null. Ordering provides less/less-
or-equal, greater/greater-or-equal, and inclusive-configurable between for exact numbers, duration,
size, and typed instants; strings have no implicit collation.

ApproximatelyEquals uses explicit nonnegative absolute and/or relative tolerance and accepts when
absolute delta is at most the maximum of absolute tolerance and relative tolerance multiplied by
the greater absolute operand. Evidence retains exact deltas and effective tolerance.

String operators are contains, notContains, startsWith, endsWith, matches, and notMatches, without
implicit trim, normalization, or case folding. Regex uses a versioned linear-time, RE2-compatible
dialect with explicit search/full mode and bounded flags; pattern and compilation options are
static. Containment/shape provides typed contains, containsAll/Any, hasKey(s), hasOnlyKeys, and
length with type-defined units.

ConformsTo performs non-mutating JSON Schema validation, recommended defaulting to 2020-12 when
the Schema omits a dialect. Format remains annotation unless assertion is explicit. Schema and
validation evidence retain bounded instance/schema locations and exact counts. DecodesAs asserts
body-view validity and treats malformed, empty-for-format, invalid-text, and NoBody cases as
violations while operational unavailability remains unavailable.

Parsed HTTP media types use mediaTypeIs with subset/exact/ignore parameter behavior and
mediaTypeMatches for suffix-aware checks. ContainsToken parses applicable HTTP token lists rather
than substring matching. DigestEquals streams bytes and initially permits SHA-256 and SHA-512;
digest evidence inherits source sensitivity.

Observation and stream states use ordinary selectors, equality, membership, and cardinality
instead of opaque pass operators. Application timestamps require an explicit named parse
transform before typed ordering. Operator static fields include regex/schema/algorithm/type/mode;
expected values, numeric bounds/tolerances, and compatible expected metadata may resolve at
runtime.

Operators are deterministic, pure, cancellable, resource-bounded, unable to perform I/O or format
unsanitized output, and return structured evidence. A future external operator adapter would be
namespaced, schema-validated, and policy-controlled under ADR 0015's internal capability seam.
Locale collation, fuzzy similarity, PCRE, XPath, embedded query expressions, statistics, snapshots,
cross-Environment predicates, and arbitrary code are deferred.

### Portable timing facts

Timings are scoped to the Run, Environment execution, attempt, actual HTTP exchange, stream window,
or evaluator. `response.timings.elapsed` is the selected terminal exchange's monotonic start-to-end
interval and excludes scheduler queue, earlier redirects, retries, and backoff.
`execution.timings.elapsed` covers the complete Environment execution including redirects, retries,
and backoff; queue time remains separately addressable. The ambiguous selector
`response.timings.total` is not canonical.

The transport records monotonic milestones and derives only valid intervals. Portable spans include
connection acquisition, DNS, connect, proxy tunnel, TLS, request write, exchange-start to final
headers, sequential server wait when defined, exchange-start to first body byte, body read, and
exchange elapsed. These spans are not assumed additive because pooling, multiplexing, duplex
traffic, and backpressure overlap or omit phases.

Every timing metric is Measured with duration/precision/source, NotPerformed with a reason such as
connection reuse or an empty body, or Unavailable with an instrumentation reason. Missing
instrumentation and reused work never become zero. Failures retain completed and partial timing
facts. All durations use a monotonic clock; wall timestamps are separate correlation metadata and
never used by subtraction.

Streams add time to first record, observation-window elapsed, and per-record received offsets.
Timing is not a default stream or response diff. Explicit performance rules select a metric and
tolerance. ExecutionProfile may choose no connection reuse, Environment-scoped pooling, or
Scenario-scoped reuse, and every exchange records its connection/reuse provenance.

### AssertionProfile composition

An AssertionProfile deterministically expands ordered includes, applies optional include aliases,
applies ordered typed edits, appends local rules, validates the final set, and fingerprints it
before execution. Include expansion is depth-first; cycles are source-located errors. The same
canonical rule reached through a diamond is deduplicated with all provenance paths, while distinct
definitions with the same effective ID conflict. An include alias prefixes effective rule IDs
without changing original definition identity.

Edits are remove, full replace, or typed override. Replace may change identity, selector, operator,
cardinality, or purpose and retains report position. Override may change presentation, tags,
severity, operator operands, applicability, and completeness, but not ID, selector, operator,
quantifier, or cardinality. Operator-specific schemas validate operands. Targets may use an
unambiguous effective ID or qualified defining Profile/rule identity; zero or ambiguous targets
fail compilation. Final effective IDs are unique.

Order is stable include order, replacements in target position, then authored local rules. It is
reporting and artifact order only; evaluation may be concurrent and has no first-wins semantics.
Removed or unassigned rules produce no fact, while an assigned false applicability condition
produces skipped. Every effective rule retains defining origin, include/alias paths, edits,
attachment layer, role/Environment assignment, effective ID, and fingerprint.

`$param` in a profile uses the executable Request or Scenario parameter frame, never a shadow
profile frame. A profile may declare required executable parameters and types so attachment is
checked statically. Inline Request and Scenario-step rules compile identically but have
container-owned diagnostic identities and are not independently referenceable.

Attachments layer from Project defaults through Request/Scenario, ExecutionProfile, and
RunInvocation using explicit inherit/replace/append/remove/clear operations. Role and explicit
Environment assignment remain EffectiveExecution concerns. Identical rule applications arriving
through multiple assignment paths are evaluated once with all provenance; conflicting fingerprints
under one effective ID fail. ExecutionPolicy may lock required CI assignments against removal.

## Implementation-level follow-up

The shared Selector syntax and RFC 9535 JSONPath contract are accepted in ADR 0004. The final
per-kind shorthand inventory will be generated from concrete resource schemas.
