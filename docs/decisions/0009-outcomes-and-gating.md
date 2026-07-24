# ADR 0009: Execution facts, evaluation outcomes, and gating

- Status: Accepted
- Date: 2026-07-23
- Owners: Comparo maintainers

## Context

Comparo must distinguish successful execution that discovers a regression from transport failure,
incomplete work, skipped evaluation, evaluator failure, and the final acceptability decision. Local
exploration and CI gating must use one execution and evaluation engine rather than diverging paths.

## Decisions

### One fact-producing engine

The runtime produces structured executions and observations. Pure assertion and binary diff engines
produce facts without deciding the overall release verdict. An optional pure GateProfile reduces
those facts into a verdict. It performs no I/O, resolution, assertion, diff, or fail-fast behavior.

HTTP non-success status codes are valid observations. Assertion violations do not suppress diff
evaluation, and drift does not suppress remaining assertions. Independent work continues unless an
explicit execution-level fail-fast setting stops it.

### Execution and assertion outcomes

Execution distinguishes observed, partial, unavailable, blocked, and cancelled states with typed
resolution, policy, DNS, connection, TLS, timeout, protocol, resource-budget, and internal failure
categories.

Assertion outcomes are `satisfied`, `violated`, `skipped`, `unavailable`, and `error`. Invalid static
operator configuration fails compilation rather than becoming a runtime result. Assertion severity
is independent and uses `blocking`, `warning`, or `info`.

### Diff and comparison outcomes

Concrete field facts distinguish `same`, `changed`, `added`, `removed`, `ignored`, and
`incomparable`. A binary comparison is `equivalent`, `drifted`, `incomplete`, `error`, or
`notEvaluated`. A missing execution is incomplete rather than being represented as every field
removed. An entirely ignored comparison is not evaluated rather than automatically equivalent.

### Run completion and gate verdict

Run completion and gate verdict are independent dimensions. Completion distinguishes complete,
partial, cancelled, rejected, and internal-error results. Gate verdict is passed, failed, or not
requested. A complete run may legitimately have a failed gate; a partial exploratory run may retain
valuable facts without a requested verdict.

GateProfile can select severities and environment roles, require complete execution and non-empty
coverage, and set allowed violation/drift thresholds. Assertions apply to every selected Environment
by default. Empty selections, entirely skipped assertions, ignored comparisons, and incomplete work
cannot accidentally pass when the GateProfile requires evaluated coverage. The normal fail-closed
release profile requires complete work and zero blocking violations or drifts.

### Artifact reevaluation

Facts retain stable rule, resource, cell, Environment, comparison, selector, and concrete-location
identities. A sufficiently complete sanitized artifact may be evaluated by another compatible
GateProfile without repeating HTTP work.

## Consequences

- TUI exploration and CI gating cannot disagree because they used different evaluators.
- A `500` response can be equivalent across environments while independently violating assertions.
- Warnings remain visible facts even when a GateProfile permits them.
- Partial and empty runs remain diagnosable without being reported as green.
