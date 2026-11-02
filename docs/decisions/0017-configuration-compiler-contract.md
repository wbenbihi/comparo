# ADR 0017: v1alpha1 configuration compiler contract

- Status: Accepted
- Date: 2026-07-24
- Owners: Comparo maintainers

## Context

Step 4 must turn the configuration decisions from ADRs 0001-0016 into a strict external-language
boundary without coupling the pure core to YAML, paths, remote retrieval, runtime value sources,
or user interfaces.

## Decisions

`comparo/v1alpha1` has a version-owned AST and strict per-kind field catalog. Filesystem discovery
and safe YAML composition are adapters. The core frontend consumes source-located neutral nodes;
it registers typed `(kind, id)` identities, links references, validates cross-field semantics and
closed operation aliases, and emits one immutable, version-neutral `CompiledProject`.

The canonical graph contains typed value expressions, exact inert data, linked `DefinitionRef`
values, container-owned inline definitions, and safe remote Schema coordinates. It contains no
YAML nodes, directive mappings, unlinked resource reference strings, filesystem paths, dynamic
operation identities, or unknown operations. Compilation performs no I/O.

Project `defaults` and `ExecutionProfile` share one structured execution-template field catalog.
References within Project defaults are linked identically to profile references. The external
operation registry accepts generated public aliases and exact `comparo.core/<alias>@1` built-in
identities. Third-party names remain unsupported until ADR 0015's extension model is decided.

The checked-in JSON Schema is generated deterministically from resource fields, nested structured
fields, directives, and operation registries. The compiler remains authoritative for semantic and
graph invariants that JSON Schema cannot express.

YAML admission uses explicit caller-overridable byte, document, depth, node, and source-count
limits. Defaults are safe operating inputs rather than immutable product maxima. External source
escape requires caller authority. Remote Schema coordinates are retained only when they are
bounded HTTP(S) URLs without user-info, query, or fragment payloads.

Diagnostics are aggregate, deterministic, source-located, code-based, and value-free. Secret
declaration literals compile to sealed expressions; fingerprints redact their payloads. Any error
suppresses the compiled project.

The prototype `comparo/v1` is recognized only to produce its intentional unsupported diagnostic.
Migration is identity for `comparo/v1alpha1` and fails closed for unknown or prototype sources.

## Consequences

- CLI, TUI, CI, and the future planner consume one compiler result rather than reinterpret YAML.
- New configuration versions can convert independently into the same canonical graph.
- Large repositories can deliberately raise admission inputs without making the default parser
  unbounded.
- Remote schema policy and retrieval remain later authorized effects, not compiler side effects.
- Operation aliases become compatibility-sensitive external syntax once the stable lineage is
  published.
