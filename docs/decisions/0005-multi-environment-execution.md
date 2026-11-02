# ADR 0005: Multi-environment execution and comparisons

- Status: Accepted
- Date: 2026-07-23
- Owners: Comparo maintainers

## Context

Comparo executes the same logical request cells across selected environments, evaluates each
environment independently, and compares selected environment pairs. The configuration and planner
must support a normal baseline-to-many workflow without maintaining a redundant list of execution
targets, while the internal diff primitive remains binary.

## Decisions

### Configuration topology

An ExecutionProfile expresses the ordinary comparison topology directly:

```yaml
spec:
  baseline: production
  candidates:
    - staging
    - development
```

The selected execution environments are the union of the baseline and candidates. There is no
separate `targets` list. An assertions-only execution uses an `environments` list, which is mutually
exclusive with effective baseline/candidate selection.

Project defaults, ExecutionProfile fields, and RunInvocation overrides use the established typed
precedence model. Candidate collections support explicit inherit, replace, append, remove, and
clear operations. The TUI ordinarily submits a complete replacement without mutating Project or
profile resources.

### Binary comparison primitive

The planner expands baseline-to-many selection into binary directed pairs. The baseline/candidate
direction is retained in results because added and removed values are directional. The internal
RunPlan supports arbitrary binary pairs, but the first stable configuration exposes only the
ordinary baseline-to-many shape. Explicit-pair or all-pairs syntax can be added without changing
the diff engine.

### Execution and evaluation reuse

For each Request/Matrix cell and trial, each selected Environment executes once. Its observation
and per-Environment assertion results are reused for every comparison containing that Environment
in that trial. The baseline is not re-executed for each candidate.

Cell-scoped dynamic inputs are resolved once and reused across all selected environments. The
effective environment still supplies its own base URL, authentication, safety restrictions, and
explicitly environment-scoped configuration.

### Failure independence

Independent work continues by default. A failed or unavailable execution produces incomplete
comparison outcomes for pairs that depend on it but does not prevent other environments,
assertions, or complete pairs from running. Exploratory reporting preserves all available facts;
gate policy normally fails closed on incomplete work. An explicit fail-fast execution policy may
stop unrelated outstanding work.

### Planning and scale

The planner reports Matrix-cell, trial, Environment-execution, and pairwise-comparison
cardinalities before network work. Execution is lazy and bounded. Configuration and operator
policy may impose resource budgets without introducing an arbitrary hard-coded project-size limit.

One RunPlan is compiled against one coherent definition graph. Across all plan slices, a stable
ResourceIdentity maps to exactly one fingerprint for subjects, Matrix definitions, selected
Environments, and comparison endpoints. A run cannot silently combine two revisions under one
logical identity.

## Consequences

- Environment resources never acquire permanent baseline or candidate roles.
- The same observation cannot accidentally differ merely because it participated in two pairs.
- Assertions-only checks and pairwise comparisons share execution and observation machinery.
- Additional comparison topologies remain planner features rather than changes to diff semantics.
- Partial transport failure remains distinct from equality, drift, assertion failure, and policy
  rejection.
