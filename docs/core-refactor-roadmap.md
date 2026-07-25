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
  -> dedicated CI integration
  -> release qualification
  -> safe request command export
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

Status: completed on 2026-07-24. The implemented boundary and verification contract are recorded
in [Immutable core domain foundation](core-domain-foundation.md).

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

Status: completed on 2026-07-24. The implemented language and runtime boundary are documented in
[Comparo selector language](selector-language.md).

Implement one selector abstraction for assertions, diffs, captures, redaction, triage, and future
matrix injection.

```text
select(prepared_snapshot, compiled_selector, control) -> SelectionEvaluation
```

The prepared snapshot binds an immutable observation to already-resolved body, decoded-view, and
stream values. Control carries cumulative selection budgets and an immutable cancellation
observation. Evaluation returns both the closed result union and updated budgets. Authored parse
failures are compilation outcomes; they never masquerade as missing runtime data.

Selection implements RFC 6901 JSON Pointer and RFC 9535 JSONPath, typed HTTP fields and static
facts, exact attempt/exchange occurrence scope, body views, and SSE/record stream sources. Runtime
results distinguish ready, partial, unavailable, unmeasured, observed-invalid, decode-failed,
incomplete, budget-exhausted, unsupported, and cancelled states.

Exit criteria:

- Parser and evaluator are separate pure modules: complete.
- Property, adversarial, and pinned RFC conformance tests cover path parsing and evaluation:
  complete.
- Evaluation is I/O-free, incrementally budgeted before retention, and protected by an explicit
  dependency allowlist: complete.
- HTTP, body, stream, occurrence, and JSON sources lower to one public selection boundary:
  complete.
- No downstream evaluator needs its own path parser: complete.

## Step 3: pure assertion, diff, and gate engines

Status: completed on 2026-07-24. The implemented evaluator boundaries and verification contract
are documented in [Pure evaluation kernel](core-evaluation-kernel.md).

Assertions consume an observation plus compiled rules and return structured assertion outcomes.
Operators are typed variants whose parameters are validated before evaluation.

Diffs consume two observations plus a compiled profile and remain a binary primitive. Rules use a
typed select, transform/reduce, pair, and compare pipeline so Environment-specific values and
nondeterministic streams can be reduced to meaningful semantic facts before comparison. The
initial operation set covers projection/exclusion, exact/type/shape comparison, absolute and
relative tolerance, ordered/keyed/set/multiset collections, and subset/superset behavior. Future
external or plugin-provided analysis remains behind a separately prepared capability boundary; it
is not built into the Step 3 diff evaluator.

The gate is a separate pure engine over assertion, diff, and runtime outcomes. It decides whether
facts are acceptable without forcing evaluators to know release policy.

Exit criteria:

- Evaluation is pure and I/O-free: complete.
- Every outcome points to a stable rule identifier: complete.
- Invalid regex, missing selection, unavailable response, unequal value, drift, and evaluator
  error are distinct outcomes: complete.
- Empty and entirely skipped evaluations cannot become accidentally green: complete.
- Ignored or unselected values cannot be reported as equal, and comparison coverage is explicit:
  complete.

## Step 4: versioned configuration compiler

Status: complete (2026-07-24).

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
  unsupported-format diagnostic: complete.
- All references are linked to the correct resource kind: complete.
- Cross-field invariants fail before execution: complete.
- Schemas are generated for every supported external version: complete.
- Migration behavior has golden fixtures: complete.

## Step 5: deterministic run planner

Status: completed on 2026-07-24. The implemented boundary, precedence model, admission contract,
and preview guarantees are documented in [Deterministic run planner](run-planner.md).

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

- Planning is deterministic and inspectable: complete.
- Empty, contradictory, unsafe, and excessively large plans fail before network activity:
  complete for pre-resolution structure and plan authority; finalized effects are re-authorized
  after Step 6 materialization.
- Matrix expansion is bounded and does not create an eager unbounded task list: complete.
- A plan preview never resolves secrets: complete.

## Step 6: run-scoped values and secrets

Status: completed on 2026-07-24. The implemented scope lattice, provider boundary, caching,
conversion, generation, and sensitivity lifecycle are documented in
[Run-scoped values and secrets](run-scoped-values.md).

Introduce explicit scopes:

```text
project/environment -> run -> cell -> trial -> execution -> step -> attempt
```

A `RunContext` owns environment overlays, resolved secret caches, deterministic generated values,
the clock and random providers, cancellation, and the values that the sanitizer must recognize.
Value providers sit behind a port and may include literals, environment variables, confined files,
fallback chains, secret managers, deterministic generators, and later scenario captures.

Exit criteria:

- A source is read at most once per declared scope: complete.
- A value sent on the wire is registered in the same snapshot used for sanitization: complete.
- Cell-scoped generated values are reused across compared environments: complete.
- Resolution errors identify their source location without printing sensitive data: complete.
- Secret-source rotation and concurrent resolution have regression tests: complete.

## Step 7: runtime with a fake transport

Status: completed on 2026-07-25. The bounded scheduler, authorized transport seam, offline
vertical slice, cancellation behavior, and verification contract are documented in
[Offline runtime](offline-runtime.md).

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

- Large fake plans run with bounded tasks and memory: complete.
- Resource ledgers expose requested, effective, consumed, and limiting budgets with provenance:
  complete.
- Cancellation leaves no work behind: complete.
- One cell failure does not abort unrelated cells unless policy requests fail-fast: complete.
- Randomized completion order still produces deterministic results: complete.
- CLI and TUI concerns do not appear in the runtime: complete.

## Step 8: production HTTP adapter

Status: completed on 2026-07-25. Request finalization, route-pinned transport, redirect/retry
orchestration, TLS identity isolation, bounded capture and storage, aggregate deadlines, and
record-stream projection are documented in [Production HTTP adapter](production-http-adapter.md).

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

- Fake and real adapters pass the same transport and runtime contracts: complete.
- Wire behavior is faithful for every request representation: complete.
- Unsafe methods are not retried without an explicit policy: complete.
- Streams distinguish completion, timeout, truncation, cancellation, and failure: complete.
- Local integration tests cover protocol edge cases and cleanup: complete.

## Step 9: sanitization, artifacts, and persistence

Status: completed on 2026-07-25. The live-to-safe lifecycle, independently versioned artifact,
atomic local persistence contract, reporter semantics, and explicit deferred integration are
documented in [Sanitized artifacts, persistence, and reporters](artifacts-and-reporters.md).

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

- Artifact types cannot contain live secret types: complete.
- Persisted values pass through one explicit sanitization boundary: complete.
- Writes are atomic, containment-safe, and use restrictive permissions: complete for the initial
  POSIX local-filesystem adapter.
- Omitted and truncated captures are explicit data: complete.
- JSON, JUnit, SARIF, and Markdown derive from the same artifact: complete.

## Step 10: CLI migration

Status: completed on 2026-07-25. The local Cyclopts/Rich adapter now exposes the shared schema,
validation, doctor, planning, sanitized rendering, run, diff, ExecutionProfile, artifact, report,
and shell-completion journeys documented in [the CLI reference](cli.md). Production commands share
one application planner/runtime composition; caller-owned load and runtime limits, invocation Value
resource overlays, structured exit outcomes, destination-bound secret egress, and once-only HTTP
authorization are enforced before effects at their respective boundaries. At the end of Step 10,
a dedicated CI wrapper and the TUI remained intentionally deferred; the TUI is migrated in Step 11
and CI integration follows in Step 12 below.

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

- Command handlers contain no domain rules: complete.
- Exit codes come from structured outcomes: complete.
- All configuration and report validation occurs before network activity: complete.
- Every execution command uses the same planner and runtime: complete.

## Step 11: TUI migration

Move the TUI last. It consumes compiled projects, run plans, runtime events, run results, and saved
artifacts. It does not resolve values, compose profiles, execute HTTP, evaluate rules, build
artifacts, or reconstruct secrets.

Exit criterion: removing the TUI package would remove no backend capability or business rule.

Status: complete. Explorer consumes safe project summaries; Compare consumes admitted plan
previews, cancellable progress sessions, and sanitized result projections; Report consumes a
bounded authenticated archive; Settings owns terminal-only preferences. The Textual shell and
widgets contain no configuration, resolution, HTTP, evaluation, gate, or persistence logic. Both
bare interactive `comparo` and explicit `comparo tui` launch through caller-owned, versioned load,
execution, artifact, report, and preparation limits.

## Step 12: dedicated CI integration

Status: pending and required before the first public rebuilt release.

Package the existing headless execution path as a deliberately restricted CI journey. The CI
adapter does not receive its own planner, runtime, evaluator, reporter, or policy semantics. It
selects the same application services and versioned machine contracts as the CLI while making the
untrusted-automation posture difficult to misconfigure.

The first adapter targets GitHub Actions without making the backend GitHub-specific. It accepts an
operator-controlled `ExecutionPolicy`, configuration sources, execution selection and explicit
limits; runs non-interactively; preserves cancellation and exit-code semantics; publishes bounded
sanitized reports and artifacts; and can write a concise job summary. Secrets are supplied through
declared value sources and never embedded in generated command lines, workflow output, annotations,
cache keys, or artifact names.

Exit criteria:

- The wrapper delegates to the same execution composition used by `comparo run`, `diff`, and
  `exec`; deleting it removes no backend behavior.
- Restricted authority is the CI default and a project cannot grant itself additional authority.
- GitHub annotations, summaries, reports, and uploaded bundles are projections of sanitized
  artifacts or value-free diagnostics.
- Success, detected drift, invalid invocation, blocked execution, runtime failure, cancellation,
  and incomplete execution retain documented machine-stable outcomes.
- A pinned example workflow and local contract tests cover pull-request and release-gate journeys.

## Step 13: release qualification and publication

Status: pending and required before the first public rebuilt release.

Freeze and verify the first published new-lineage contract only after every required product
surface is present. This step adds no domain behavior. It independently audits the assembled
system, closes documentation and packaging gaps, and proves that the artifacts built by release
automation are the artifacts that were reviewed.

The publication boundary must explicitly identify the first released `comparo/v1alpha1`
configuration schema, artifact schema, machine-report envelopes, CLI exit codes, supported Python
and operating-system matrix, and executable limitations. From that point, the compatibility rules
in ADR 0016 apply to those published contracts.

Exit criteria:

- A fresh architecture, code-quality, security, privacy, secret-egress, dependency, and
  documentation audit has no unresolved release-blocking finding.
- End-to-end tests cover CLI, TUI, and CI journeys against local controlled servers, including
  denied effects, cancellation, malformed input, bounded large input, redaction, persistence, and
  report reopening.
- Schema, artifact, report, and exit-code compatibility fixtures are frozen at the documented
  publication point.
- The wheel and source distribution contain only reviewed members, install in clean environments,
  and pass installed-package smoke tests on the declared support matrix.
- Examples and the quick start exercise only implemented capabilities; every material limitation
  and security assumption is discoverable before a user executes a request.
- The release workflow is exercised as a non-publishing rehearsal before the first tag and PyPI
  publication.

## Step 14: safe request inspection and cURL export

Status: future; not required for the first public rebuilt release.

Add a reusable request-inspection capability for concrete planned or observed request occurrences.
Its first command renderer emits a POSIX cURL representation through an application-layer safe
projection consumed by both CLI and TUI adapters. The renderer is diagnostic convenience, not a
second request-materialization or execution path, and it does not reproduce Comparo assertions,
diffs, gates, retries, authorization, or other runtime semantics.

The displayed and copied command must be byte-for-byte identical. Comparo never displays a
redacted command while placing a live credential in the clipboard. Sensitive fields use explicit
redaction markers or symbolic runtime placeholders; an incomplete command reports why it is not
directly executable. Clipboard access is a replaceable TUI adapter with explicit unavailable and
denied outcomes, and copied content is never written to logs, diagnostics, reports, or TUI history.

Exit criteria:

- Rendering consumes a sanitized request projection and cannot accept `SensitiveValue`, live run
  context, or an authorized transport request.
- One central serializer builds argument tokens before applying tested POSIX shell quoting; TUI
  widgets never concatenate commands.
- The selected environment, cell, trial, attempt, and exchange occurrence are unambiguous.
- Credential headers, cookies, sensitive query values, bodies, control characters, binary data,
  and caller-owned output limits have adversarial tests.
- Omitted, truncated, redacted, or unsupported request content makes completeness explicit.
- Normal HTTP, structured bodies, multipart/file bodies, and SSE-specific presentation are added
  only as their safe projection contracts become representable.

## Step 15: scenarios and extensions

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
6. **Public rebuilt release:** restricted CI integration followed by independent release
   qualification and publication.

Safe cURL export and Scenario/extension work are subsequent product milestones; neither is part of
the first rebuilt release's definition of done.

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
