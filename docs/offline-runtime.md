# Offline runtime

Step 7 is Comparo's first complete backend execution path. It accepts an admitted `PreparedRun`,
resolves each request at its exact attempt scope, executes an authorized exchange through an
injected transport, evaluates the resulting observations, and returns one immutable `RunResult`.
The reference transport is programmable and performs no network I/O.

```text
comparo/v1alpha1 documents
  -> CompiledProject
  -> PreparedRun
  -> bounded execution work
  -> attempt-scoped request resolution
  -> pre-send authorization port
  -> programmable fake transport
  -> immutable observations and selection snapshots
  -> pure assertions and binary diffs
  -> normalized facts and gate
  -> RunResult
```

The runtime is a composition root for existing core contracts. It does not duplicate compiler,
planner, resolution, selector, assertion, diff, or gate rules.

## Public boundary and ownership

`comparo.core.runtime.run_prepared` is the orchestration entry point. Its dependencies are explicit
and run-local:

- `ExecutionDriver` owns execution of one planned Environment occurrence and closes its resources;
- `EvaluationPreparation` converts compiled attachments and resolved operands into calls to the
  pure assertion and diff kernels;
- `RunCancellation` carries a first-wins cancellation source;
- `RunClock` supplies lifecycle time without ambient clock access;
- `RuntimeEventSink` receives sequenced, value-free progress events.

`HttpExecutionDriver` is the HTTP-specific composition of narrower ports. `HttpRequestProvider`
materializes one request, `HttpExchangePreparer` performs route resolution and final authorization,
`HttpTransport` performs exactly one already-authorized exchange, `RetryWaiter` makes retry delay
observable and cancellable, and `SelectionSnapshotProvider` loads any body views selectors need.
No transport can receive an unresolved or merely planned request.

Closing the driver closes its request provider, exchange preparer, snapshot provider, and
transport exactly once. All cleanup operations are attempted even if another cleanup fails. A
cleanup defect becomes the value-free `runtime.cleanup.failed` internal run outcome rather than
adapter exception text.

## Bounded scheduling and accounting

Execution work is enumerated lazily from the compact plan. A fixed worker set consumes a bounded
`asyncio.Queue`; the scheduler never creates one task per planned execution. The two scheduling
budgets are ordinary planning inputs capped by applicable `SafetyProfile` and external
`ExecutionPolicy` ceilings:

| Resource | Versioned request default | Runtime meaning |
| --- | ---: | --- |
| `queuedWork` | 64 | maximum waiting work occurrences |
| `concurrency` | 8 | maximum active execution workers |

The final ledgers retain requested, cap, effective, and complete contribution provenance from the
planner. Runtime adds cumulative consumption, final in-use count, and high-water evidence. A run
cannot produce final ledger snapshots while queue or worker reservations remain active.

The result itself necessarily contains one terminal record per admitted execution and comparison.
Transient queue and task memory is bounded by the effective scheduling budgets. Large-plan tests
exercise 256 executions with a queue of five and concurrency of three, and verify both scheduler
and transport high-water marks.

## Failure, cancellation, and fail-fast

Each execution ends as exactly one of:

- `ObservedExecution`, with its immutable HTTP observation and matching selection snapshot;
- `UnavailableExecution`, with a stage and stable value-free diagnostic code;
- `BlockedExecution`, for an authorization or resolution denial;
- `CancelledExecution`, with its typed cancellation source.

Driver exceptions are contained per execution and become `runtime.execution.internal`. By default,
an unavailable or blocked execution does not prevent unrelated work from running. Effective
`failFast: true` cancels future and active work after the first failed execution using the
`execution-policy` source. External cancellation races each active driver call, unwinds it, drains
all scheduler reservations, and closes the driver before returning.

Attempts are distinct from trials and executions. The HTTP driver creates deterministic attempt
and exchange identities, retains pre-send failures separately from transport failures, evaluates
typed retry conditions, honors both the request policy and admitted maximum-attempt ceiling, and
records retry metadata. The offline waiter derives deterministic backoff facts but performs no
wall-clock sleep; Step 8 supplies the production waiter.

## Determinism and events

Worker completion order may vary. Execution records are keyed by their plan ordinal and sorted
before evaluation. Comparison occurrences are then derived from the stable trial and directed-pair
order, with deterministic identities. Equivalent inputs therefore produce the same `RunResult`
ordering regardless of fake-transport yields or worker interleaving.

Events describe live progress rather than canonical result order. They carry only the run,
execution, or comparison identity and a monotonic run-local sequence. Supported categories are run
started, execution queued, execution started, execution finished, comparison finished, and run
finished. CLI and TUI state is deliberately absent.

## Evaluation integration

An observed execution receives one `SelectionSnapshot`. The exact same observation and snapshot
are reused by assertion preparation, both sides of diff preparation, fact normalization, and gate
evaluation. Runtime never re-executes a request to compare Environments.

Assertion preparation failure is isolated to its execution. Diff preparation failure is isolated
to its directed comparison. A pair with either side unavailable is explicitly
`IncompleteComparison`; it is never reported as equal. Evaluation incompleteness contributes to a
partial completion and is visible to a strict gate.

## Request materialization in Step 7

`ConfigurationRequestProvider` proves the real compiler-to-resolution seam for Request resources.
It resolves the request at `AttemptScope`, supplies planned Matrix cell inputs, resolves the
selected Environment's `baseUrl`, and creates a typed absolute HTTP URL, duplicate-preserving
query sequence, and request headers. Resolved sensitive values stay sealed in the wire model and
the same `RunContext` sanitization registry. Effective request fingerprints include the resolved
target, so otherwise identical authored requests in different Environments retain distinct wire
identities.

This bridge intentionally accepts only the body-free HTTP subset needed to prove the offline
vertical slice. A non-null body and unsupported shapes fail explicitly. Body serializers,
authentication, cookies, redirects, complete request-policy binding, real delays, streaming,
response storage, and network I/O belong to Step 8. The port shapes are already separated so those
capabilities replace adapters without changing scheduler or evaluation semantics.

## Fake transport

`ProgrammableFakeTransport` maps exact `ExchangeId` values to immutable `FakeExchangeAction`
records. An action contains a typed transport result and an optional cooperative yield count used
to permute completion order. The fake:

- accepts only `AuthorizedExchangeRequest`;
- consumes each scripted exchange once;
- fails closed for missing actions or use after close;
- exposes call order, active count, maximum concurrency, and remaining actions;
- releases active accounting on success, failure, or cancellation.

It is both the offline implementation and the contract reference for the production adapter.

## Verification contract

The Step 7 suite covers every runtime statement and branch. It pins:

- the complete configuration-to-gate offline vertical slice;
- attempt-scoped value and secret materialization without payload leakage;
- bounded large-plan scheduling and resource-ledger evidence;
- randomized completion with deterministic result ordering;
- retryable and terminal transport/pre-send outcomes;
- default failure independence, fail-fast, and external cancellation;
- cleanup success and contained cleanup defects;
- real assertion, diff, normalization, and gate integration;
- malformed collaborators and closed-union validation at every port.

Architecture checks forbid runtime imports from configuration adapters, network clients, ambient
environment or filesystem APIs, randomness, YAML, CLI, and TUI packages. Step 8 may add production
adapters outside this core boundary; `comparo.core.runtime` continues to depend on ports.
