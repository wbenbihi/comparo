# Core refactor roadmap

This document is the implementation guide for rebuilding Comparo's core and backend as a
production-grade, modular engine. The current implementation is treated as an executable
prototype: its useful behavior and fixtures inform the new engine, but its internal abstractions
are not compatibility requirements.

The guiding rule is to work from the pure domain center outward. Configuration syntax, network
libraries, persistence, the CLI, and the TUI are adapters around the core rather than owners of
business behavior.

```text
Architecture decisions
  -> immutable domain model
  -> selector engine
  -> assertion, diff, and gate engines
  -> configuration compiler
  -> deterministic run planner
  -> run-scoped values and secrets
  -> runtime with a fake transport
  -> production HTTP adapter
  -> sanitization, artifacts, and persistence
  -> CLI
  -> TUI
  -> scenarios and optional extensions
```

## Architectural destination

The intended dependency flow is:

```text
YAML / JSON documents
  -> versioned configuration frontend
  -> compiler and semantic linker
  -> immutable CompiledProject
  -> deterministic planner
  -> immutable RunPlan
  -> runtime orchestrator
  -> HTTP transport port
  -> ObservedExchange values
  -> pure assertion and pairwise diff evaluators
  -> gate evaluator
  -> live RunResult
  -> explicit sanitization boundary
  -> versioned RunArtifact
  -> CLI / TUI / CI reporters
```

Safety policy participates in compilation, planning, execution, and artifact creation. A single
run-scoped context owns value snapshots, secrets, deterministic generators, cancellation, and the
secret registry used for sanitization.

Resource budgets use documented versioned defaults, ordinary configuration requests, and
SafetyProfile/ExecutionPolicy ceilings. There is no immutable maximum Project size. Accepted work
is generated lazily under bounded concurrency and explicit memory, disk, network, decoder,
selector, evaluation, evidence, and artifact budgets.

Core modules must not depend on YAML, `httpx`, Textual, CLI frameworks, ambient environment
variables, or filesystem APIs. Those dependencies belong behind ports and adapters.

## Step 0: architecture and product decisions

Status: completed on 2026-07-24. The accepted record and completion boundary are indexed in
[`docs/decisions/README.md`](decisions/README.md).

Resolve the decisions that would otherwise cause foundational modules to be redesigned later:

1. Product boundary and primary user journeys.
2. Configuration versioning and compatibility policy.
3. Shared selector and path language.
4. Pairwise diff and multi-environment execution semantics.
5. Stateless requests versus scenarios and captured values.
6. Configuration trust and threat model.
7. Production and mutating-request safety policy.
8. Value, secret, generator, and capture scopes.
9. Assertion, diff, and gate outcome semantics.
10. Response capture, sanitization, and artifact policy.
11. Extension seams and external-analysis trust boundary.
12. Compatibility and migration strategy for the new configuration lineage.

Record accepted decisions as short architecture decision records. A decision is ready when two
developers could independently implement compatible modules from it.

## Step 1: immutable domain foundation

Define the internal language before parsing external configuration:

- Typed identifiers for environments, requests, matrices, profiles, executions, rules, cells,
  comparisons, runs, and artifacts.
- Immutable HTTP request, body, auth, timeout, retry, TLS, proxy, and redirect models.
- Immutable observation and decoded-view models.
- Selector, assertion rule, diff rule, and gate policy models.
- Environment-pair, plan-cell, run-plan, and resource-budget models.
- Structured error and outcome taxonomy.
- Secret or tainted value types that cannot accidentally enter serializable artifacts.

There should be no parser, filesystem, network, async orchestration, or display code in this
step. Tests construct domain objects directly.

Exit criteria:

- Domain objects are immutable and strongly typed.
- Raw dictionaries remain only for genuine JSON-like payloads.
- Distinct outcomes exist for resolution, policy, transport, assertion, diff, cancellation, and
  incomplete execution.
- Automated dependency contracts prevent adapter imports into the domain.

## Step 2: shared selector engine

Implement one selector abstraction for assertions, diffs, captures, redaction, triage, and future
matrix injection.

```text
select(observation, selector) -> SelectionResult
```

Selection results distinguish one value, multiple values, missing data, an unavailable source, a
parse failure, and an invalid selector. The grammar must support proper escaping and be documented
as a stable language rather than described loosely as JSONPath.

Exit criteria:

- Parser and evaluator are separate pure modules.
- Property and fuzz tests cover path parsing and evaluation.
- No downstream evaluator needs its own path parser.

## Step 3: pure assertion, diff, and gate engines

Assertions consume an observation plus compiled rules and return structured assertion outcomes.
Operators are typed variants whose parameters are validated before evaluation.

Diffs consume two observations plus a compiled profile and remain a binary primitive. Rules use a
typed select, transform/reduce, pair, and compare pipeline so Environment-specific values and
nondeterministic streams can be reduced to meaningful semantic facts before comparison. Initial
operations should cover projection/exclusion, exact/type/shape comparison, absolute and relative
tolerance, ordered/keyed/set/multiset collections, subset/superset behavior, and bounded pure
future external capability operations.

The gate is a separate pure engine over assertion, diff, and runtime outcomes. It decides whether
facts are acceptable without forcing evaluators to know release policy.

Exit criteria:

- Evaluation is pure and I/O-free.
- Every outcome points to a stable rule identifier.
- Invalid regex, missing selection, unavailable response, unequal value, drift, and evaluator
  error are distinct outcomes.
- Empty and entirely skipped evaluations cannot become accidentally green.
- Ignored or unselected values cannot be reported as equal, and comparison coverage is explicit.

## Step 4: versioned configuration compiler

Treat configuration as an external language:

```text
documents
  -> version-specific AST
  -> version conversion
  -> reference linking
  -> semantic validation
  -> normalization
  -> CompiledProject
```

Implement the new `comparo/v1alpha1` frontend against the internal domain. The prototype format
that previously used the `comparo/v1` label is not an execution compatibility target and receives
a specific diagnostic. Later published frontends convert into the same canonical domain as
required by ADR 0016. External documents preserve source spans for aggregated diagnostics.
Compiled values contain no directives, raw reference strings, YAML objects, ambiguous profile
attachments, or unsupported operator names.

Exit criteria:

- Repository examples use `comparo/v1alpha1`; known prototype documents receive an intentional
  unsupported-format diagnostic.
- All references are linked to the correct resource kind.
- Cross-field invariants fail before execution.
- Schemas are generated for every supported external version.
- Migration behavior has golden fixtures.

## Step 5: deterministic run planner

The planner consumes a `CompiledProject` and produces an immutable `RunPlan` without reading
secrets or performing I/O.

Project and execution configuration are templates. A typed `RunInvocation` from the CLI, TUI, or
CI supplies deliberate overrides; the planner first binds all layers into an immutable effective
execution and records the provenance of each effective value. Frontends never mutate compiled
configuration objects.

Ordinary configuration precedence, value-source lookup, and safety authority are separate
mechanisms. Typed configuration fields define replace/append/remove/clear behavior; value
namespaces define provider lookup order; safety is the intersection of project restrictions and
operator authority rather than a last-writer-wins override.

It owns request selection, environment selection, baseline-to-many pair expansion, matrix case
generation, trial expansion, profile composition, effective policy calculation, resource-limit
validation, stable cell/trial identifiers, and plan fingerprinting. A Matrix changes declared
inputs; a trial repeats one logical input; an attempt retries one Environment execution.
Multi-environment execution expands to binary comparisons rather than making the diff engine
multi-sided.

Exit criteria:

- Planning is deterministic and inspectable.
- Empty, contradictory, unsafe, and excessively large plans fail before network activity.
- Matrix expansion is bounded and does not create an eager unbounded task list.
- A plan preview never resolves secrets.

## Step 6: run-scoped values and secrets

Introduce explicit scopes:

```text
project/environment -> run -> cell -> trial -> execution -> step -> attempt
```

A `RunContext` owns environment overlays, resolved secret caches, deterministic generated values,
the clock and random providers, cancellation, and the values that the sanitizer must recognize.
Value providers sit behind a port and may include literals, environment variables, confined files,
fallback chains, secret managers, deterministic generators, and later scenario captures.

Exit criteria:

- A source is read at most once per declared scope.
- A value sent on the wire is registered in the same snapshot used for sanitization.
- Cell-scoped generated values are reused across compared environments.
- Resolution errors identify their source location without printing sensitive data.
- Secret-source rotation and concurrent resolution have regression tests.

## Step 7: runtime with a fake transport

Define the HTTP transport port and first implement a programmable fake. Build the runtime around a
bounded worker queue, explicit state transitions, structured progress events, cancellation,
fail-fast policy, attempt handling, observation normalization, deterministic final ordering, and
resource cleanup.

This step creates the first complete offline vertical slice:

```text
comparo/v1alpha1 configuration
  -> compiler
  -> plan
  -> resolution
  -> fake transport
  -> observations
  -> assertions and diffs
  -> gate
  -> RunResult
```

Exit criteria:

- Large fake plans run with bounded tasks and memory.
- Resource ledgers expose requested, effective, consumed, and limiting budgets with provenance.
- Cancellation leaves no work behind.
- One cell failure does not abort unrelated cells unless policy requests fail-fast.
- Randomized completion order still produces deterministic results.
- CLI and TUI concerns do not appear in the runtime.

## Step 8: production HTTP adapter

Implement the real adapter only after its port is stable. Add capabilities in this order:

1. Ordinary request and response handling.
2. Duplicate-preserving query parameters and headers.
3. Typed JSON, text, binary, URL-encoded, multipart, and file bodies.
4. Typed authentication.
5. Redirect and cookie policy with environment isolation.
6. Connect, read, idle, and total deadlines.
7. Idempotency-aware retries.
8. Request, response, and stream size limits.
9. Streaming completion and truncation states.
10. TLS, proxy, and client-certificate configuration.
11. Cancellation and cleanup on every path.

Exit criteria:

- Fake and real adapters pass the same contract suite.
- Wire behavior is faithful for every request representation.
- Unsafe methods are not retried without an explicit policy.
- Streams distinguish completion, timeout, truncation, cancellation, and failure.
- Local integration tests cover protocol edge cases and cleanup.

## Step 9: sanitization, artifacts, and persistence

Separate live results from persistable artifacts:

```text
live RunResult
  -> capture policy
  -> sanitizer using the run snapshot
  -> versioned RunArtifact
  -> atomic artifact store
```

Reporters are projections of `RunArtifact`, never consumers of live objects. Artifact schema
versioning is independent of configuration schema versioning.

Exit criteria:

- Artifact types cannot contain live secret types.
- Persisted values pass through one explicit sanitization boundary.
- Writes are atomic, containment-safe, and use restrictive permissions.
- Omitted and truncated captures are explicit data.
- JSON, JUnit, SARIF, and Markdown derive from the same artifact.

## Step 10: CLI migration

Move commands in increasing order of side effects:

1. Schema generation.
2. Validation.
3. Plan and render preview.
4. Diagnostics.
5. Single-environment run.
6. Multi-environment diff.
7. Execution profiles.
8. Artifact and report commands.

Do not execute live HTTP twice to compare prototype and new behavior. Preserve only deliberately
selected behavior through translated fixtures and local fake transports.

Exit criteria:

- Command handlers contain no domain rules.
- Exit codes come from structured outcomes.
- All configuration and report validation occurs before network activity.
- Every execution command uses the same planner and runtime.

## Step 11: TUI migration

Move the TUI last. It consumes compiled projects, run plans, runtime events, run results, and saved
artifacts. It does not resolve values, compose profiles, execute HTTP, evaluate rules, build
artifacts, or reconstruct secrets.

Exit criterion: removing the TUI package would remove no backend capability or business rule.

## Step 12: scenarios and extensions

Only after the kernel and its ports are stable, consider scenario steps, captures, OAuth flows,
external secret providers, custom operators and comparators, effectful analysis such as an LLM
judge, additional transports, environment inheritance, or distributed execution.

Do not stabilize a public plugin system as part of the initial refactor. Built-in capabilities
exercise narrow typed internal ports with versioned identities, structured failures, cancellation,
budgets, effect metadata, sensitivity propagation, and provenance. Packaging, language, discovery,
and isolation remain deferred until concrete extensions justify them. Effectful analysis runs
through an authorized runtime port between pure comparison preparation and pure fact aggregation;
it is not hidden inside a comparator. In-process code is fully trusted and a subprocess is not
called a sandbox without enforced isolation.

## Delivery milestones

1. **Evaluation kernel:** hand-built observations run through selectors, assertions, diffs, and
   gates.
2. **Compile and plan:** `comparo/v1alpha1` compiles into a deterministic, inspectable plan.
3. **Offline complete engine:** compiled projects execute end-to-end through the fake transport.
4. **Production HTTP engine:** bounded, cancellable execution with run-scoped secret snapshots.
5. **Product integration:** sanitized artifacts, CLI cutover, and finally TUI cutover.

## Parallel work boundaries

After the shared domain contracts stabilize:

- Assertion and diff evaluators can proceed independently.
- Versioned configuration frontend, schema generation, and migration tooling can proceed behind
  the canonical compiler boundary.
- Selection and matrix planning can proceed independently.
- Value providers can be implemented independently behind their port.
- HTTP features can be implemented independently against adapter contract tests.
- Reporters can proceed independently after `RunArtifact` stabilizes.
- CLI and TUI work can overlap after runtime events stabilize.

Parallel implementation should not begin before the shared domain, selector, error, and outcome
contracts are settled; otherwise each module will invent incompatible versions of the same ideas.

## Definition of done for a core module

A module is production-grade only when:

- Its public inputs, outputs, invariants, and error states are explicit.
- It depends only on lower-level contracts allowed by the architecture.
- External I/O is behind a port.
- Time, random values, environment values, and cancellation are injectable where relevant.
- Every user-controlled collection, recursion, byte stream, and task set is bounded.
- Unit and contract tests cover successful, erroneous, cancelled, and boundary behavior.
- Property, fuzz, performance, or security tests exist where the input space demands them.
- Errors are structured and do not leak sensitive values.
- Compatibility expectations are documented.
- The CLI and TUI do not need to reinterpret its results.
