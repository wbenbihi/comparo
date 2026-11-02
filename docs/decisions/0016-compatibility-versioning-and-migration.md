# ADR 0016: Compatibility, versioning, and migration

- Status: Accepted
- Date: 2026-07-24
- Owners: Comparo maintainers

## Context

The executable prototype labels its configuration `comparo/v1`, but it has no known production
users and was never offered as a stable contract. The refactor needs freedom to replace that
format while ensuring configuration authored for the new architecture remains usable by future
Comparo releases. Configuration, artifacts, machine reports, operation semantics, and any future
extension protocol evolve on different timelines.

## Decisions

### New configuration lineage

The prototype `comparo/v1` format is unsupported and does not constrain the new compiler, domain,
CLI, TUI, or artifact model. It receives no execution-compatibility frontend. Known prototype
documents receive a specific diagnostic rather than being misinterpreted.

The new configuration lineage begins at `comparo/v1alpha1`. The identifier `comparo/v1` remains
reserved for the first stable schema in this lineage, not the prototype. Repository examples and
fixtures are translated to the new format rather than defining legacy behavior.

Once a new-lineage API version is published, later Comparo versions continue to accept its valid
documents. A breaking syntax or semantic change introduces another frontend version, such as
`comparo/v1alpha2`; it never silently changes the meaning of `v1alpha1`. The stable release adds
the `comparo/v1` frontend and does not call the new architecture `v2`.

### Versioned frontend and canonical domain

Every supported configuration version has its own strict parser/AST, defaults, aliases, validation,
and conversion into the current immutable domain:

```text
source document
  -> exact apiVersion frontend
  -> version-owned defaults and semantic conversion
  -> canonical compiled domain
```

Old syntax is not carried through the runtime. Version conversion is pure, source-located, and
covered by golden fixtures. Compiled plans and artifacts retain source API versions, compiler
version, applied conversions, default-set version, operation identities, and fingerprints.

Adding an optional field, directive, or resource kind may be backward compatible because it does
not invalidate old documents. Old binaries are not required to understand future syntax. Unknown
fields, directives, kinds, and operation names remain errors; forward compatibility never relies
on silently ignoring configuration.

Defaults that affect execution or evaluation belong to their configuration or operation semantic
version. A new binary does not reinterpret an omitted `v1alpha1` field using a behavior-changing
new default. Operators, decoders, selectors, transforms, reducers, comparators, and other semantic
operations have independently versioned identities so their stable behavior is not coupled to the
outer YAML version.

### Migration

A migration command parses one supported frontend and emits a selected newer external version.
It does not resolve secrets, execute HTTP, install capabilities, or mutate the original files by
default. Source comments and layout are preserved where the chosen YAML tooling can do so; semantic
correctness and explicit diagnostics take precedence over byte-for-byte formatting.

Ambiguous or behavior-changing migrations require an explicit author choice and never guess.
Multi-file Projects remain multi-file unless the user requests another layout. Migration has
idempotent golden fixtures and a validation pass over emitted documents.

### Independent external contracts

Configuration API, RunArtifact schema, machine-report schemas, CLI command contract, and any future
extension protocol are versioned independently. A configuration migration does not rewrite saved
artifacts or imply a plugin protocol change.

New CLI and TUI versions read older new-lineage RunArtifacts through explicit non-destructive
artifact conversion. Machine-readable JSON and CI exit-code meanings become compatibility
contracts when declared stable. Human-readable prose, colors, spacing, and interactive layout are
not stable APIs.

Newer artifacts or configuration need not be readable by older Comparo binaries. Readers diagnose
unsupported future versions rather than partially interpreting them.

### Security and correctness changes

Backward compatibility does not require preserving a vulnerability, unsafe parser behavior, or
policy bypass. A security fix may reject a previously accepted action, tighten mandatory
sanitization, or require operator authorization. The change must produce a structured diagnostic,
be documented, and avoid silently substituting different execution.

Corrections that reveal an implementation never matched its documented versioned semantics restore
the specification and receive regression tests. A deliberate semantic change receives a new
operation or configuration version.

## Consequences

- The prototype creates no permanent legacy burden or reason to call the first production format
  `v2`.
- `v1alpha1` is a real compatibility commitment once published, despite its alpha stability label.
- Multiple parsers can coexist while the runtime operates on one current domain model.
- Strict schemas and backward compatibility coexist without unsafe unknown-field tolerance.
- Historical artifacts and CI integrations have explicit, independent compatibility contracts.

## Implementation-level follow-ups

1. Define the exact publication point after which a generated alpha schema is considered released.
2. Establish the CLI exit-code table and machine-report version envelopes.
3. Select a round-trip YAML implementation and document its comment-preservation limits.
4. Define the artifact conversion registry and minimum fixture corpus.
