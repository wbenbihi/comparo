# Deterministic run planner

Comparo's planner is the pure boundary between a successfully linked `CompiledProject` and
executable work. It binds one typed `RunInvocation`, applies operator-owned `PlanningAuthority`,
admits the resulting cardinality, and returns an immutable `PreparedRun` containing a compact
`RunPlan`.

```text
CompiledProject + RunInvocation + PlanningAuthority
  -> structural binding and provenance
  -> profile and Matrix attachment composition
  -> exact topology and cardinality
  -> budget and policy admission
  -> stable plan fingerprint
  -> PreparedRun
```

Planning performs no filesystem or network I/O, reads no ambient state, resolves no value source,
and never opens a sealed literal. A failure returns a value-free planning diagnostic and no plan.

## Public boundary

The public entry point is `comparo.core.planning.plan_run`:

```python
result = plan_run(compiled_project, invocation, authority)
if result.prepared is None:
    codes = tuple(diagnostic.code.value for diagnostic in result.diagnostics)
else:
    prepared = result.prepared
    plan = prepared.plan
```

`RunInvocation` carries deliberate CLI, TUI, or CI choices without mutating compiled resources:

- an exact invocation ID and run ID;
- an optional `ExecutionProfile` selection;
- typed Request/Scenario selection edits;
- a complete assertions-only or baseline-to-many topology, or an explicit edit of inherited
  comparison candidates;
- Matrix attachment edits with local binding aliases;
- trial and maximum-attempt overrides;
- a fail-fast override, defaulting to independent execution failures;
- ordinary planning-budget requests;
- unresolved global and Environment-specific value overlays.

Collection edits have a fixed field-owned order: choose the inherited, cleared, or replacement
base; remove by identity; set keyed items; then append. Absence, clear, replacement, removal, and
addition are different operations. A `ComparisonTopologyEdit` can inherit or set the baseline and
can replace, remove, set, or append candidates. It fails if there is no inherited comparison
topology to edit.

`PlanningAuthority` is supplied outside Project discovery. It names one exact fingerprinted
`ExecutionPolicy`, may restrict subject and Environment identities, and must provide a finite
ceiling for every planning admission dimension. The Project cannot select or weaken this object.

## Three different composition mechanisms

Structural precedence, value lookup, and safety authority deliberately do not share a generic
merge algorithm.

Ordinary structural fields bind from versioned defaults through Project defaults, the selected
`ExecutionProfile`, Request-specific attachments where applicable, and finally the invocation.
Each field owns replacement or collection-edit behavior and records the sources that contributed
to its effective value.

Unresolved value namespaces retain ordered layers for Step 6:

```text
Project declarations
  -> Project defaults
  -> selected Environment bindings
  -> ExecutionProfile global bindings
  -> RunInvocation global bindings
  -> RunInvocation Environment-specific bindings
```

Invocation overlays are normalized to global-first and then selected-Environment order regardless
of caller tuple order. An overlay scoped to an Environment outside the effective topology is an
error. The planner preserves expression trees and provenance; it does not perform `$env`, `$file`,
`$secret`, `$from`, interpolation, conversion, or provider resolution.

Safety is restrictive composition. Applicable Project, Request, Environment, and profile safety
attachments are expanded by exact definition identity. Budget ceilings take the minimum across
all applicable `SafetyProfile` definitions and external `ExecutionPolicy` authority. Other HTTP,
filesystem, Schema, and persistence capabilities remain exact handles for their later
request/materialization authorization stages; they never become ordinary last-writer-wins fields.

## Topology and cardinality

A comparison run has one baseline and one or more candidates. The planner executes the selected
Environments once per subject, Matrix cell, and trial, then expands the topology into directed
binary `(baseline, candidate)` pairs. The baseline is not re-executed for each candidate.

An assertions-only run selects one or more Environments and has no comparison pairs. The two
topologies are mutually exclusive. Missing, empty, incomplete, contradictory, ambiguous, denied,
or Scenario-based selections fail before work is admitted. Stateful Scenario execution remains a
later roadmap step.

The plan records exact totals for:

```text
Matrix cells
Matrix cells x trials
Matrix cells x trials x selected Environments
Matrix cells x trials x directed comparison pairs
Environment executions x maximum attempts
```

A Matrix changes declared logical inputs, a trial repeats one logical cell, and an attempt retries
one Environment execution. These identities and counts are never conflated.

## Matrix semantics

A Matrix may provide explicit `{id, values}` cases or named axes. Axes support `product`, `zip`,
and `union`; `product` is the default. `include` appends exact cases and `exclude` removes exact
case IDs. Empty cases, dynamic or duplicate IDs, incompatible zip lengths, invalid combinations,
and expansion beyond the effective cell budget are diagnostics.

Attaching multiple Matrices to one Request takes their Cartesian product. Each attachment has a
binding name and retains its Matrix definition and case ID in the cell coordinate. The same Matrix
can be attached more than once only through distinct aliases. Unqualified Matrix field shorthand
is rejected when two attached bindings expose the same field name.

Matrix case compilation is bounded before rows are retained. The admitted `RunPlan` stores compact
axis recipes rather than an eager task per execution. `iter_plan_cells` and `iter_plan_trials`
enumerate stable cell and trial occurrences lazily; each plan slice separately retains the stable
Environment order consumed by the runtime.

## Profile attachment composition

Assertion and diff attachments use deterministic include-first expansion and exact-definition
deduplication. Safety attachments expand both `extends` and `include`. Gate, redaction, and artifact
attachments use the most specific exact definition. Request-specific attachments remain attached
to their own effective subject.

This stage composes the definition graph and retains exact inline or reusable handles. It does not
resolve profile parameters or rule operands. Those canonical expressions stay in the retained
`CompiledProject` for run-scoped preparation after values are available.

## Budgets and admission

Planning has no immutable application maximum. The versioned `comparo.planning/v1` defaults are
ordinary requested limits:

| Dimension | Default |
| --- | ---: |
| selected Requests | 100 |
| Matrix cells | 10,000 |
| trials | 10,000 |
| Environment executions | 50,000 |
| maximum attempts | 150,000 |
| binary comparisons | 50,000 |
| queued work | 64 |
| concurrency | 8 |

Project or `ExecutionProfile` requests and invocation overrides may raise or lower these values.
Applicable safety ceilings and the mandatory external policy ceiling cap them. Every resulting
`ResourceBudget` records the requested, cap, effective value, contribution kind, and exact
value-free origin. A large finite run is valid when its operator authority and available resources
permit it; exceeding any effective dimension fails before value resolution or network activity.
The runtime later replaces the zero-valued usage placeholders on queued-work and concurrency
budgets with consumed, in-use, and high-water evidence.

## Identity, determinism, and preview safety

Matrix case IDs are authored static identities. Cell and trial occurrence IDs are deterministic
UUIDv5 values derived under the invocation's `RunId` from exact subject revisions, Matrix
coordinates, and zero-based trial ordinals. Enumeration order never depends on scheduler timing.

The SHA-256 plan fingerprint binds the run and invocation IDs, exact policy and configuration
definition fingerprints, selected subjects and Environments, comparison pairs, profile and Matrix
attachments, effective counts, budget requests and caps, authority projections, provenance, and
the semantic shape of unresolved value overlays. Sensitive literals contribute only their sealed
payload type and non-secret taint origins; their contents are never accessed. Bytes contribute a
digest rather than raw content. The same complete typed inputs produce the same plan and
fingerprint.

A compact `RunPlan` contains no request body, resolved token, dotenv value, file content, or
provider result. `PreparedRun` retains the compiled templates and unresolved layers for the next
stage, but all sealed containers continue to fail closed under ordinary rendering and
serialization. A CLI or TUI can therefore preview topology, identities, cardinality, budgets, and
diagnostic codes without resolving secrets.

## Deliberate next-stage boundaries

Step 5 does not materialize HTTP requests, resolve values, execute Scenarios, schedule tasks,
authorize a resolved destination or redirect, fetch remote Schemas, perform transport I/O, or
create artifacts. Step 6 owns scoped values and secrets. Step 7 consumes the scheduling budgets
and effective fail-fast choice through an injected transport boundary. Finalized effects still
require authorization under the same exact `ExecutionPolicy`; an admitted plan is not a reusable
network capability.

Architecture contracts forbid adapter, HTTP client, filesystem, ambient environment, clock,
randomness, scheduler, CLI, and TUI imports from `comparo.core.planning`. The planner suite covers
every statement and branch, including malformed canonical graphs constructed outside the normal
compiler path.
