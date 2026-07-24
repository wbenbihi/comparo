# ADR 0015: Extension seams and deferred plugin model

- Status: Accepted
- Date: 2026-07-24
- Owners: Comparo maintainers

## Context

Comparo should eventually support capabilities outside its core scope, such as an LLM judge that
assesses semantic similarity. The packaging ecosystem, language boundary, process model,
installation workflow, sandboxing model, and public plugin API are not understood well enough to
stabilize now. Prematurely exposing one would create a compatibility and security commitment that
could distort the core architecture.

## Decisions

### No public plugin system is selected

`comparo/v1alpha1` does not commit to Python entry points, subprocess RPC, WebAssembly, a plugin
manifest, a lockfile, automatic discovery, package installation, or a marketplace. It does not add
a generic extension resource, arbitrary YAML escape hatch, executable expression, lifecycle hook,
or opaque configuration bag merely to anticipate plugins.

Future additive configuration can introduce extension resources and fields without breaking old
configuration. Backward compatibility requires future Comparo versions to continue accepting old
documents; it does not require current versions to understand future plugin syntax. Strict unknown
field/directive rejection therefore remains intact.

### Preserve internal capability seams

Core modules depend on narrow typed ports and versioned operation identities rather than concrete
registries, package loaders, or framework imports. Built-in decoders, value providers, assertion
operators, diff transforms/reducers/pairers/comparators, analysis executors, and artifact sinks are
implemented behind those ports. Construction and dependency injection happen in the application
layer.

The ports define typed inputs, typed outputs, structured failures, cancellation, resource-budget
participation, determinism/effect metadata, sensitivity propagation, and provenance. They do not
define how a future third-party implementation is installed or invoked. This allows a future
plugin adapter to supply capabilities without changing selector, assertion, diff, gate, planner,
or artifact semantics.

Capabilities use namespaced, versioned internal identities from the beginning. Artifacts retain
the exact implementation and semantic-operation versions used. Core aliases resolve to core
identities; this does not make arbitrary external identities valid configuration before an
extension frontend exists.

### Effectful analysis remains outside pure comparison

Networked, paid, nondeterministic, or data-egressing analysis is not hidden inside a pure diff
comparator. The comparison pipeline has a seam between pure preparation and pure aggregation:

```text
Observations
  -> pure select / derive / pair
  -> typed ComparisonCase
  -> authorized analysis executor port
  -> typed AnalysisResult with provenance
  -> pure DiffFact aggregation and GateProfile
```

Core comparators satisfy the executor contract locally without I/O. A future LLM judge adapter may
consume an explicitly selected ComparisonCase and return a structured verdict, score, diagnostics,
and evidence. The runtime, not the diff evaluator, owns authorization, credentials, network, cost,
timeout, cancellation, caching, and retries.

Such an analysis declares its provider/model and versions, input selection, data destination,
sensitive-data behavior, nondeterminism, sampling, cost and resource bounds, and raw/derived
evidence policy. Its absence, denial, timeout, malformed result, or provider failure remains
distinct from judged semantic drift. Artifacts can retain sanitized inputs or their permitted
digests plus the complete execution provenance needed to explain or replay the analysis where
policy allows.

### Future plugins are executable authority

If third-party plugins are introduced, Comparo will state their actual trust boundary. Installed
in-process code is fully trusted executable code. A subprocess is fault isolation, not a security
sandbox, unless filesystem, network, process, environment, and secret access are genuinely
enforced. Project configuration cannot silently install executable code, and external
ExecutionPolicy remains the ultimate operator authority.

These are safety invariants for a future plugin design, not a decision about its packaging or
runtime.

## Consequences

- Comparo can add an LLM judge without making the pure diff kernel perform network I/O.
- The initial configuration does not acquire a vague extension mechanism that bypasses validation.
- Internal built-ins exercise the same narrow seams future adapters would use.
- Plugin packaging and isolation can be decided from real use cases later without rewriting core
  evaluation facts.
- No claim is made that community code is safe to execute.

## Explicitly deferred

1. Plugin languages, packaging, discovery, installation, and lockfiles.
2. In-process, subprocess, WebAssembly, or sandbox execution.
3. Public manifest and compatibility protocol.
4. The exact configuration/resource model for external analysis such as an LLM judge.
5. Distribution, signing, provenance verification, and marketplaces.
6. Which internal capability ports become public extension points.
