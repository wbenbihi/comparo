# ADR 0014: Resource budgets, admission control, and backpressure

- Status: Accepted
- Date: 2026-07-24
- Owners: Comparo maintainers

## Context

Comparo must support large Projects, Matrices, trials, full response bodies, streams, and rich
evaluation without allowing a configuration or remote server to consume unbounded memory, disk,
time, network, or paid requests. A small immutable application maximum would make legitimate large
Projects impossible, while unbounded work would make the public tool unsafe.

## Accepted decisions

### Layered effective budgets

Comparo has no immutable application-level maximum Project, Matrix, or body size. It has documented
versioned defaults that are ordinary inspectable inputs to effective configuration and may be
overridden where authority permits.

```text
versioned defaults
  -> Project and profile requests
  -> RunInvocation overrides
  -> applicable Environment SafetyProfile ceilings
  -> external ExecutionPolicy ceilings
  = effective budgets
```

Ordinary precedence chooses the requested value. Safety and operator ceilings constrain it; a
lower applicable ceiling wins, and no ordinary override can raise it. Every operation whose work
is controlled by untrusted or multiplicative input requires a finite effective budget. Large
finite values are allowed, subject to available resources and policy. Implementations expose a
capability or resource failure rather than disguising a platform limitation as product policy.

The compiled plan and RunArtifact retain requested values, effective values, default/override/cap
provenance, actual use or high-water marks, and any limiting operation.

### Independent budget dimensions

Budgets are typed by resource and scope rather than collapsed into a generic maximum:

- planning: Requests, Matrix cells, trials, Scenario steps, Environment executions, attempts, and
  binary comparisons;
- scheduling: queued work, total concurrency, per-Environment and per-origin concurrency, and open
  connections;
- HTTP: request/header bytes and counts, encoded and entity-body bytes, decompression ratio,
  redirects, retries, deadlines, and stream reconnects;
- streams: events/records, bytes, duration, idle duration, and connection segments;
- decoding and selection: characters, JSON depth/nodes/tokens, view-index bytes, traversal/filter
  work, matches, and selected-result bytes;
- evaluation: derived values/bytes, transform work, pair count, facts, trial samples, and
  statistical work;
- evidence: retained examples and bytes independently from logical evaluation;
- storage: in-memory thresholds, per-body and per-run transient bytes, and store-specific quotas;
- artifacts: retained body/evidence bytes, blobs, and total bundle bytes;
- future external capabilities: deadlines and input/output/work budgets, with cooperative
  enforcement for trusted in-process code unless an isolated runner provides stronger controls.

Operation schemas define units and scopes. A per-body limit does not substitute for a run-wide
storage limit, and an evidence limit does not change a logically completed result merely because
not every example was retained.

### Admission control and lazy execution

The planner calculates or safely bounds logical cardinalities before network work, including:

```text
cells x trials x selected Environments = Environment executions
cells x trials x directed comparison pairs = comparisons
```

Scenario expansion and maximum attempts are included where applicable. Exceeding an effective plan
budget rejects the run before resolution or network activity. Accepted work is represented and
generated lazily; the runtime never creates a task or large result object for every future work
item at once.

A bounded scheduler separates total work from concurrency, uses backpressure between network,
capture, decoding, evaluation, and artifact stages, and provides fairness across Environments and
cells. A very large accepted plan may therefore execute with small fixed concurrency without one
Environment indefinitely starving another. Cancellation closes producers, consumers, body
handles, connections, and temporary storage deterministically.

### Full capture and bounded storage

The BodyStore memory threshold is a placement threshold, not a truncation limit. Crossing it moves
capture to secure spool or another storage adapter while continuing toward a complete body. The
effective per-body, decoded-expansion, run-wide transient-storage, stream, and execution budgets
remain finite and visible.

Budget exhaustion produces an explicit partial BodyHandle and termination reason; a prefix never
masquerades as complete. Aggregate reservations and accounting prevent many individually permitted
bodies from collectively exhausting the machine. Storage and downstream readers apply
backpressure instead of buffering an unbounded producer.

### Exhaustion semantics

Budget exhaustion is typed and attached to the responsible stage:

- plan exhaustion rejects execution before network activity;
- response, stream, or storage exhaustion produces a partial or unavailable Observation;
- selector, decoder, transform, reducer, pairing, or comparator exhaustion makes the affected
  evaluation incomplete or unavailable rather than unequal;
- evidence exhaustion may retain a logically exact outcome when evaluation can continue, while
  marking evidence truncated and retaining exact counts;
- artifact exhaustion does not rewrite live facts, but makes persisted capture coverage explicitly
  incomplete or rejects the artifact according to ArtifactProfile;
- external-capability exhaustion is distinct from an assertion violation or content drift.

The GateProfile alone decides whether incomplete execution, evaluation, evidence, or artifact
coverage is acceptable. No exhaustion path produces an accidental green result.

### Frontend warnings

CLI and TUI may use configurable UX thresholds to preview cost and request confirmation for an
unusually expensive invocation. Such warnings are not engine budgets or safety authority. CI and
other non-interactive execution never depend on a prompt; their permitted cost is determined by
the effective plan and external ExecutionPolicy.

## Consequences

- Legitimate large Projects can raise requested budgets without modifying Comparo.
- A malicious server cannot rely on full-body defaults to fill memory or disk indefinitely.
- Matrix size, repetition, retries, concurrency, evidence, and persistence are independently
  understandable and enforceable.
- A resource limit cannot be confused with application drift or successful completion.
- Reports can explain both what limit applied and who supplied it.

## Implementation-level follow-ups

1. Exact YAML placement, shorthand, units, and shipped versioned default values.
2. Resource-ledger reservation algorithms and fairness policy.
3. Which local interactive warning thresholds ship in CLI and TUI.
4. OS-specific disk-free-space reservations and cleanup behavior.
5. Strong plugin CPU/memory isolation when an isolated runner exists.
