# Architecture

Comparo is being rebuilt from a pure, immutable domain center outward. The current public source
tree implements **Steps 1 through 6**: the internal language, shared selector and evaluation
kernels, versioned configuration compiler, deterministic admitted run planner, and run-scoped
value/secrets boundary. It is not yet a runnable CLI or TUI.

The accepted destination and implementation order are recorded in the
[core refactor roadmap](core-refactor-roadmap.md). The exact Step 1 boundary is recorded in the
[immutable core domain foundation](core-domain-foundation.md), the selector contract in the
[selector language](selector-language.md), and the evaluator boundary in the
[pure evaluation kernel](core-evaluation-kernel.md). The external-language boundary is documented
in the [versioned configuration compiler](configuration-compiler.md), and the planning boundary in
the [deterministic run planner](run-planner.md), and the live value boundary in
[run-scoped values and secrets](run-scoped-values.md). Product decisions live in the
[architecture decision index](decisions/README.md).

## Current package layout

```text
src/comparo/
├── __init__.py
├── py.typed
├── configuration/          filesystem discovery and source-located YAML adapter
├── schemas/                generated public comparo/v1alpha1 JSON Schema
└── core/
    ├── __init__.py
    ├── configuration/      pure version frontend, linker, and canonical compiler
    ├── domain/
    │   ├── assertions/   typed assertion rules and outcomes
    │   ├── diffs/        typed binary diff pipelines and outcomes
    │   ├── gates/        value-free gate inputs and verdict contracts
    │   ├── http/         finalized request and observation language
    │   ├── selectors/    compiled selectors, locations, and result variants
    │   └── *.py          identity, values, scopes, budgets, planning, and policy contracts
    ├── assertions/       pure per-Environment assertion evaluator
    ├── diffs/            pure directed binary semantic diff evaluator
    ├── evaluation/       shared deterministic fact identity and strict value semantics
    ├── gates/            pure value-free release-policy reducer
    ├── planning/         pure precedence binding, Matrix planning, and admission
    ├── resolution/       run-owned scopes, provider ports, caches, taint, and conversion
    └── selection/        pure parser, compiler, snapshot, evaluator, and accounting kernel
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

The domain, pure evaluation kernels, versioned compiler, planner, and run-scoped resolver exist
today. Domain modules
cannot import adapter frameworks, HTTP client libraries, YAML or schema libraries, CLI/TUI
frameworks, filesystem APIs,
networking APIs, serialization helpers, ambient environment access, clocks, randomness, or task
schedulers. Architecture tests also enforce acyclic internal imports, frozen/final
`msgspec.Struct` records, closed field types, immutable module constants, secret-safe containers,
and cohesive module size. The core compiler cannot import the filesystem/YAML adapter, version
frontends remain independent of canonical compiler stages, and planning cannot import adapters,
ambient state, schedulers, clocks, or randomness. Resolution may coordinate async work, but it
uses injected file, external-value, generation, YAML-decoder, and clock ports; its dependency
allowlist rejects ambient environment, filesystem, network, parser, and adapter imports.

Selection accepts a closed immutable snapshot containing an observation and values already
resolved by storage adapters. It performs no I/O or decoding. Its explicit import allowlist,
module-size limit, cumulative budgets, and closed result union protect that boundary from gradual
coupling to adapters.

Assertions and diffs consume selector results and prepared immutable inputs; they cannot perform
I/O or call each other. Gates consume only normalized value-free summaries and cannot inspect
evaluator payloads. The import-linter configuration enforces three contracts: the domain cannot
import adapter frameworks, assertion/diff/gate engines remain mutually independent, and all pure
evaluation packages reject adapter-framework imports. Selector, assertion, and diff packages add
stricter AST-enforced dependency allowlists and cohesion limits.

The compiler consumes neutral source-located documents and emits an exact `CompiledProject` with
linked references and unresolved typed expressions. The planner consumes that graph plus typed
invocation and external authority, then emits a compact `RunPlan` with exact cardinality, budget
provenance, stable identity, and a secret-free fingerprint. Resolution consumes the prepared run,
immutable Environment/Matrix snapshots, and injected effects. It evaluates expressions without
reinterpreting YAML or recomputing precedence, caches exact source/scope reads, and returns closed
typed outcomes with value-free provenance. The later runtime must consume these products rather
than rebuild them.

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

The run-scoped sensitivity registry retains every sealed resolution result, and
`resolve_for_wire` pairs a value with a sanitizer snapshot that already contains its taint. Live
observations are not persistence artifacts. Step 9's capture decorator consumes the final
same-run snapshot and live body streams before runtime cleanup, then compiles an independently
versioned `RunArtifact`. Atomic stores and reporters accept only that safe artifact and sanitized
blob ports; architecture contracts prevent them from serializing or importing live domain graphs.

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

The suite requires 100% statement and branch coverage across the current package. Distribution
verification uses an exact member allowlist and fails if local untracked package sources or
sensitive/deprecated paths could enter a release artifact.
