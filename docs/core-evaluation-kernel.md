# Pure evaluation kernel

Step 3 of the core refactor implements Comparo's first complete evaluation milestone. Hand-built
immutable observations can now pass through the shared selector engine, per-Environment
assertions, directed binary diffs, and a separate release gate without configuration parsing,
network access, storage access, reporting, or UI code entering the evaluation boundary.

The three engines are independently replaceable. They share only narrow domain contracts,
deterministic fact identity, and strict value semantics.

## Public boundaries

### Assertions

```text
evaluate_assertion(
  selection_snapshot,
  assertion_application,
  compiled_rule,
  prepared_inputs,
  assertion_control,
) -> AssertionEvaluation
```

An assertion application binds one stable compiled rule to one exact Environment execution.
Prepared inputs contain the complete static expression set and a bounded cache of pure capability
results. Capability entries are bound to the rule operator, selector, implementation, selected
value, operands, execution, and concrete location. This lets a storage or schema adapter prepare a
digest, regular-expression, or JSON Schema result without giving the pure evaluator I/O access.
Valid entries for deterministic paths that are not reached because of cancellation,
applicability, or quantifier short-circuiting may remain unused; foreign or stale entries are
rejected.

Match facts distinguish satisfied, violated, and evaluator-error predicates. Parent outcomes keep
satisfied, violated, skipped, unavailable, and error separate. Missing selection, decode failure,
operand unavailability, cancellation, incompleteness, and budget exhaustion retain typed reasons
rather than becoming predicate failures.

Assertion evidence is independent of logical counts. It prioritizes errors and counterexamples,
retains exact omission and exhaustion evidence, preserves sensitivity, and can be truncated
without changing a logically completed result. Approximate comparisons retain exact rational
delta and effective tolerance. Schema evidence retains exact violation counts and bounded
instance/schema locations. Digest evidence remains sealed when its source is sensitive.

### Diffs

```text
evaluate_diff(
  baseline_snapshot,
  candidate_snapshot,
  diff_application,
  compiled_rule,
  prepared_inputs,
  diff_control,
) -> DiffEvaluation
```

The application binds an ordered baseline/candidate Environment pair and their exact execution
IDs. Prepared operands are split into baseline, candidate, and genuinely shared frames; stale or
swapped frames are rejected. Shared comparator operands cannot depend on an Environment-,
execution-, step-, or attempt-specific scope.

The engine executes one typed pipeline:

```text
select each side
  -> transform each side
  -> reduce each side
  -> pair derived values
  -> compare pairs
  -> aggregate facts and coverage
```

Initial transforms include relative selection, projection, exclusion, mapping, filtering, text
normalization, length, type, JSON shape, digest, sorting, and stream projection. Reducers include
first, last, count, concatenate, sum, minimum, maximum, collect, set, multiset, distinct,
group-by, and object-from-entries with explicit empty and duplicate behavior. Pairing supports
singular, ordered, keyed, set, and multiset semantics. Comparators cover exact values, numeric
tolerance, text relations, type and JSON-shape compatibility, subset/superset, ordered
collections, sets, multisets, and explicit ignore.

Concrete results are same, changed, added, removed, ignored, or incomparable. Aggregate results
are equivalent, drifted, incomplete, error, or not-evaluated. Baseline-to-candidate direction is
retained throughout. Every fact carries exact selector, concrete-location, transform/reducer,
comparator, and taint provenance without retaining raw comparison values.

Coverage separately conserves selected, derived, consumed, resolved, paired, compared, ignored,
and not-evaluated work on each side. Empty derivations, all-ignored comparisons, incomplete stream
prefixes, and unresolved pairs cannot become equivalent. Projection and exclusion describe only
what was actually evaluated; unselected data is never reported as equal.

### Gates

```text
evaluate_gate(compiled_gate_profile | None, normalized_fact_summary) -> GateEvaluation
```

The gate consumes only normalized, value-free assertion, diff, and runtime summaries. It never
imports either evaluator or inspects selected values. A missing profile returns `not-requested`.
Otherwise the gate applies explicit Environment roles, severities, completeness requirements,
coverage requirements, and assertion/diff thresholds to produce passed, failed, or not-requested.

Known violations or drift remain visible even when the source evaluation is also incomplete or
errored. Empty assertion coverage, empty diffs, and all-ignored diffs cannot pass a profile that
requires evaluated coverage. Verdict and failure reasons are derived and cross-validated so a
caller cannot construct a contradictory false-green result.

## Determinism and resource safety

Fact IDs are UUIDv5 values derived from the stable rule application and structural ordinal. Raw or
sensitive payloads never participate in an identifier. JSON objects compare independently of
member order, arrays remain ordered, and integers and decimals use one exact mathematical number
domain. Numeric tolerance and sum operations are independent of the process-global Decimal
context.

Selection, evaluation work, derived values and bytes, pairs, facts, and evidence examples and
bytes use cumulative typed budgets. Charges happen before data-dependent work or retention where
the stage can predict the cost. Exhaustion returns an incomplete or unavailable outcome with the
exact canonical budget record; evidence-only exhaustion leaves logical counts intact. Partial
derivation, pairing, and comparison progress is conserved. Sensitive-origin traversal and stable
lineage union are metered alongside the values whose provenance they protect.

Sensitive values remain sealed. Selection taint propagates through transformations, reducers,
content-derived digests, and prepared sensitive operands. Diagnostics contain stable codes and
typed provenance, never exception prose or rendered values.

## Dependency boundary

The evaluation packages must not import each other or adapter frameworks. Import contracts enforce
independence among `comparo.core.assertions`, `comparo.core.diffs`, and `comparo.core.gates`, and
forbid HTTP clients, filesystem/configuration frameworks, CLI frameworks, and TUI frameworks from
the pure kernel.

The next configuration-compiler step owns authored syntax, version conversion, reference linking,
static rule construction, and source-located diagnostics. Runtime and storage steps will own
capability preparation, body streaming, scheduling, and persistence. Those layers consume the
kernel contracts; they do not move their behavior into the evaluators.

## Verification contract

The milestone is guarded by:

- strict formatting, linting, and type checking;
- import-linter dependency contracts and focused source-cohesion architecture tests;
- exact statement and branch coverage for the new engine and domain modules;
- hostile global-state, budget-boundary, partial-input, stale-identity, taint, and forged-result
  regression tests;
- full-project tests and release-artifact verification before the milestone is committed.
