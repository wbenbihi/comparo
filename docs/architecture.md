# Architecture

Comparo is being rebuilt from a pure, immutable domain center outward. The current public source
tree implements **Step 1 only**: the internal language shared by future compiler, planner,
runtime, evaluator, artifact, CLI, and TUI modules. It is not yet a runnable CLI or TUI.

The accepted destination and implementation order are recorded in the
[core refactor roadmap](core-refactor-roadmap.md). The exact Step 1 boundary is recorded in the
[immutable core domain foundation](core-domain-foundation.md), and product decisions live in the
[architecture decision index](decisions/README.md).

## Current package layout

```text
src/comparo/
├── __init__.py
├── py.typed
└── core/
    ├── __init__.py
    └── domain/
        ├── assertions/   typed assertion rules and outcomes
        ├── diffs/        typed binary diff pipelines and outcomes
        ├── gates/        value-free gate inputs and verdict contracts
        ├── http/         finalized request and observation language
        ├── selectors/    compiled selectors, locations, and result variants
        └── *.py          identity, values, scopes, budgets, planning, and policy contracts
```

The previous prototype implementation, tests, and examples are quarantined under
`deprecated.ignore.folder/`. Hatch excludes that tree from distributions. There are currently no
`comparo.adapters`, `comparo.cli`, or `comparo.tui` packages and no executable console entry point.

## Dependency direction

The intended dependency flow is:

```text
versioned configuration frontend
  -> compiler and semantic linker
  -> immutable compiled project
  -> deterministic planner
  -> run-scoped resolution
  -> bounded runtime through explicit ports
  -> immutable HTTP observations
  -> pure assertion and diff evaluators
  -> value-free gate evaluator
  -> sanitization and versioned artifacts
  -> CLI / TUI / CI reporters
```

Only the domain layer exists today. Domain modules cannot import adapter frameworks, HTTP client
libraries, YAML or schema libraries, CLI/TUI frameworks, filesystem APIs, networking APIs,
serialization helpers, ambient environment access, clocks, randomness, or task schedulers.
Architecture tests also enforce acyclic internal imports, frozen/final `msgspec.Struct` records,
closed field types, immutable module constants, secret-safe containers, and cohesive module size.

The current import-linter configuration contains one contract: `comparo.core.domain` must not
import adapter or framework packages. Additional layer contracts will be added when those packages
actually exist; this document does not claim future modules are already implemented.

## Domain responsibilities

The domain describes facts and rejects impossible state at direct construction. It does not
perform work:

- identity and provenance records preserve exact, value-free lineage;
- values and expressions model immutable inputs, taint, and exhaustive resolution outcomes;
- scopes and budgets bind limits and exhaustion evidence to exact runtime coordinates;
- planning records describe admitted immutable work without expanding or scheduling it;
- HTTP records describe finalized wire intent, explicit authority, observations, bodies, decoded
  view handles, retries, redirects, reconnects, streams, and timing facts;
- selectors, assertions, diffs, and gates define compiled contracts and exhaustive outcomes;
- external analysis remains a separately authorized, budgeted port contract rather than hidden
  diff behavior.

Small pure protocol-grammar functions may validate or canonically derive typed HTTP metadata—for
example media-type parameters or a multipart `Content-Type` field. They do not parse project
configuration, decode stored response bodies, serialize an outbound request, read a byte stream,
or render user-facing output. Those operations belong to later compiler, store, transport, and
reporter modules behind explicit ports.

## Security boundary

`SensitiveValue` is a live-process containment type. Receiving domain records accept only its
exact implementation, preserve taint through derived values, and fail closed under ordinary
rendering, copying, pickling, JSON, MessagePack, and built-in conversion. Provenance, diagnostics,
gate summaries, and terminal errors are value-free. This protects against accidental leakage; it
is not a sandbox against hostile Python code executing in the same process.

ExecutionPolicy authority is structural and exact. HTTP and external-analysis grants bind the
complete immutable intent, effective policy authority, plan or runtime coordinates, and any
separate secret-egress, mutation, or delegated-DNS approvals. Canonical resource exhaustion binds
the exact dimension, derivation, effect, and concrete scope.

Live observations are not persistence artifacts. A future explicit sanitization boundary will
compile them into a versioned `RunArtifact`; reporters must consume that safe artifact rather than
serializing live domain graphs.

## Verification

The authoritative local gates are:

```console
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen lint-imports
uv run --frozen python -m pytest --cov --cov-report=term-missing
uv build
uv run --frozen python -m tests.architecture.distribution_members \
  dist/comparo-0.2.0.tar.gz dist/comparo-0.2.0-py3-none-any.whl
```

The suite requires 100% statement and branch coverage for the foundational domain. Distribution
verification uses an exact member allowlist and fails if local untracked package sources or
sensitive/deprecated paths could enter a release artifact.
