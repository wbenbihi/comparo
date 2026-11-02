# ADR 0003: Configuration resource model

- Status: Accepted
- Date: 2026-07-23
- Owners: Comparo maintainers

## Context

Comparo needs a modular YAML resource language that supports large multi-file projects, inline
configuration, reusable objects, future plugins, and strict compilation. The current alpha format
is not a compatibility constraint.

## Accepted decisions

### Version lifecycle

The redesigned format uses `comparo/v1alpha1` during development. The first production contract
will be `comparo/v1`; future Comparo releases remain backward compatible with published valid
documents in the new lineage beginning at `v1alpha1`, as defined in ADR 0016. The prototype format
that also used the `comparo/v1` label is not part of that lineage.

### Project layout and discovery

The conventional entry manifest is `comparo.yaml`, while `--config` may select another manifest or
a directory containing one. A valid project contains exactly one `Project`. It must be the first
non-empty YAML document in the entry manifest, and a `Project` document is forbidden in every
discovered or additional source. Empty YAML documents are ignored.

The Project manifest directory is the project root. `spec.data` selects the automatically
discovered data tree and supports a shorthand path, normally `.comparo`, or a normalized long form:

```yaml
spec:
  data:
    root: .comparo
    include: ["**/*.yaml", "**/*.yml"]
    exclude: []
```

Discovery is recursive, deterministic, limited to `.yaml` and `.yml`, and never follows directory
symlinks. If the data root includes the entry manifest, it is recognized by canonical path and is
not loaded twice. `spec.sources` may explicitly add local files or directories for unusual layouts;
a missing explicit source is an error. Sources whose canonical paths escape the project root are
permitted only when the external ExecutionPolicy authorizes their roots. Automatically discovered
and explicitly declared local sources all contribute to the same namespace.

One YAML file may contain multiple documents separated by `---`; each non-empty document contains
exactly one Comparo resource. Remote resource imports are not part of the initial format. A future
`imports` facility is reserved for explicitly aliased, namespaced packages rather than changing
local source precedence.

Every resource uses the strict common envelope `apiVersion`, `kind`, `metadata`, and `spec`; unknown
envelope fields are rejected. `metadata.id` and `metadata.name` are required, while `description`
and `tags` are available consistently on every kind. Tags are unique lowercase slugs. A
display-specific `displayName` field is not used.

Comparo accepts a documented YAML 1.2-compatible subset. Duplicate mapping keys, custom YAML tags,
merge keys, anchors, and aliases are rejected so the compiled graph has one portable,
source-locatable interpretation. Unknown fields inside each versioned resource schema are also
rejected unless a field is explicitly defined as an opaque payload boundary.

### Inline and reusable definitions

Resource-valued and runtime-resolvable fields should support both inline definitions and reusable
references wherever the field's semantics permit it. Both forms compile into the same domain type
and receive identical validation. Primitive compiler-control fields remain static rather than
becoming arbitrary resource references.

### Reusable arbitrary values

The current `Instance` kind and `$val` directive are replaced by a `Value` resource. `$use` has one
conceptual meaning: resolve a typed resource. In a profile slot it resolves the expected profile
kind; in an arbitrary value slot it resolves a `Value`. The compiler validates the expanded value
against the receiving field's expected type.

### Explicit conversions

Comparo never infers conversion of externally sourced text. A short directive returns its native
default representation, normally a string:

```yaml
port:
  $env: PORT
```

Conversion is explicit in a long directive form:

```yaml
port:
  $env:
    name: PORT
    as: integer
```

or an interpolation expression:

```yaml
port: ${secrets.PORT:integer}
```

If the receiving field expects another type, the unconverted short form fails type validation; the
field schema never silently coerces it. Conversion errors are resolution errors and are not
treated as an unavailable source by `$from`.

## Accepted resource identity

Within the local project namespace, resource identity is the pair `(kind, id)`, with
uniqueness enforced within a kind:

```text
Environment/prod
SafetyProfile/prod
Value/prod
```

The ordinary form of a reference is context-typed:

```yaml
environment:
  $use: prod
```

The receiving field tells the compiler that `prod` must be an Environment. A qualified form such
as `$use: Environment/prod` may be used for clarity or when a field accepts more than one resource
kind. Resource IDs are stable lowercase slugs and do not encode filesystem paths or object
hierarchy. A separate human `metadata.name` may change without breaking references.

The artifact and plan identities include the qualified resource identity and a compiled content
fingerprint, so moving a resource between YAML files does not change its identity while changing
its effective configuration remains traceable.

All automatically discovered and explicitly declared local sources contribute to one project
namespace. Declaring the same `(kind, id)` twice is a compilation error with both source spans;
source order never implements last-one-wins overriding. Internally the compiler carries
`(namespace, kind, id)`, where the local namespace is implicit in author-facing references. If
external packages are introduced later, they receive an explicit import alias that fills this
namespace component rather than changing the meaning of local IDs.

Only declared resources have referenceable identities. An inline definition is anonymous and is
owned by its containing resource. The compiler assigns it a diagnostic origin such as
`ExecutionProfile/release#safety`, but that origin is not a public reference target. Promoting an
inline definition to a reusable resource is therefore an explicit authoring operation.

Reference resolution never searches across kinds. A typed field determines exactly one expected
kind; an ordinary runtime value field expects `Value`. Qualified references are required where a
field intentionally accepts several resource kinds. Renaming an ID is a breaking configuration
change; changing `metadata.name` or moving the document is not.

## Resource kind catalog

The accepted design currently contains fourteen project-authored resource kinds discovered through
the Project configuration and its declared data sources:

1. `Project`
2. `Environment`
3. `Request`
4. `Scenario`
5. `Matrix`
6. `ExecutionProfile`
7. `Schema`
8. `AssertionProfile`
9. `DiffProfile`
10. `GateProfile`
11. `SafetyProfile`
12. `RedactionProfile`
13. `ArtifactProfile`
14. `Value`

`ExecutionPolicy` is a separate operator-authored document kind under the policy API, never a
project-discovered resource. This makes fifteen accepted authored kinds across both authority
domains. A future generated `RunArtifact` manifest may use the common envelope under an artifact
API, but its exact document shape and `kind` have not yet been frozen and it is not counted as a
project-authored resource.

Parameters, variables, secrets, selectors, assertion rules, diff rules, request bodies, Scenario
steps and outputs, Matrix cases, safety capabilities, and storage handles are typed nested objects,
not independent resource kinds. `Instance` has been removed in favor of `Value`. No `AuthProfile`,
`RetryProfile`, `CaptureProfile`, `StorageProfile`, or plugin-defined core kind has been accepted.

## Accepted composition boundary

Composition is typed per resource kind rather than implemented as generic recursive YAML merge.
There is no universal `extends` mechanism. `Value` uses explicit `$patch`; Schema uses JSON Schema
composition; Matrix uses its case and combination algebra. Request, Environment, and
ExecutionProfile inheritance are deferred rather than included in the initial language. Shared
components and the established Project/Profile/RunInvocation layers handle initial reuse.

### Profiles

AssertionProfile and DiffProfile use ordered includes. SafetyProfile may use typed `extends` or
ordered includes. Duplicate or conflicting rule identifiers are compilation errors unless an
explicit override operation is declared.

### Matrices

A Matrix is modeled as data that produces named rows/cells, not as a request mutation template.
The canonical authoring form is an explicit list of cases. Each case has a stable public ID and a
map of named runtime values. Matrix values are read through `$matrix` or interpolation and can
therefore be placed in any runtime-resolvable request field.

An axis (or dimension) is only an optional compact generator: it is one independent list of named
values. Combining a `plan` axis containing three values with a `locale` axis containing two values
using `product` generates six cases. The explicit case form remains sufficient and is preferred
when combinations are irregular or need carefully chosen case IDs.

Attaching a plain list of matrices to a Request has one defined shorthand meaning: their Cartesian
`product`. A three-case Matrix and a two-case Matrix therefore produce six comparison cells without
requiring a `combine` field. Explicit `zip`, `union`, and a potential future keyed `join` are used
only when product is not the intended behavior. This algebra is composition, not inheritance.

The resulting cell retains each source Matrix's binding name and case ID for provenance. An
unqualified `$matrix: field` shorthand is valid only when that field is unique across all attached
matrices. Qualified lookup identifies both binding and field. A binding alias is required when the
same Matrix is attached more than once. Duplicate unqualified field names are compilation errors,
not order-dependent overrides.

The planner calculates and reports the product cardinality before execution. Configurable
ExecutionProfile and operator ExecutionPolicy budgets may reject excessive expansion; the product
has no arbitrary hard-coded project-size limit. An empty input produces no cells and is surfaced as
an explicit no-work diagnostic rather than silently executing the unexpanded request.

A Matrix may contain named axes or explicit cases. Axis values and cases have stable, non-secret
IDs used in selection and reports; runtime values are never embedded in identity. `include` and
`exclude` add or filter exact cases. Duplicate case IDs are errors unless an explicit `replace`
operation names the target. General deep merge of Matrix specifications is not proposed.

### Schemas

Schema composition uses JSON Schema's own `$ref`, `$defs`, `allOf`, `anyOf`, and `oneOf` semantics,
not Comparo `extends` or `$use` inside the JSON Schema document. Every Comparo Schema resource is
registered under a deterministic local URI derived from its resource identity, allowing one Schema
to `$ref` another without automatic network or filesystem retrieval. Remote references are subject
to the effective safety configuration and may require content-integrity pinning there.

The Schema payload is an opaque JSON Schema dialect boundary. At the field that accepts a Schema,
Comparo permits either an inline schema or `$use` of a Schema resource. Once inside the schema
payload, `$ref`, `$defs`, and other `$` keywords belong to JSON Schema and are not interpreted as
Comparo value directives. This prevents the two languages from corrupting each other's semantics.

A field expecting a Schema may also use `$use` with an absolute HTTP(S) URI. The URI is interpreted
as an external Schema reference only because the receiving field is Schema-typed. Remote schema
loading is controlled by the effective SafetyProfile, including allowed schemes and hosts,
redirect behavior, byte/time budgets, caching, and optional content-integrity requirements. Every
transitive remote `$ref` is checked by the same policy. Operator-owned ExecutionPolicy still caps
what a project-controlled SafetyProfile may authorize, preserving the previously accepted rule
that repository content cannot grant itself new network authority in restricted execution.

### Environments, requests, and executions

Environment, Request, and ExecutionProfile resources do not inherit from resources of the same
kind in the initial language. Common configuration is expressed through Project defaults and
reusable typed components or Values. If same-kind composition is added later, it must have typed
field-specific behavior and explicit replace/append/remove operations rather than generic deep
merge.

## Directive capability roadmap

The initial directive set is `$use`, `$var`, `$secret`, `$param`, `$env`, `$file`, `$from`, and
`$literal`.

The language and compiler registry should anticipate these future capability categories without
implementing or silently accepting them in `v1alpha1`:

- Deterministic generation (`$generate`) with explicit run/cell/trial/environment/execution/step/
  attempt scope, type, format, and seed behavior.
- Matrix-cell lookup (`$matrix` or `$case`) so a named axis value can be referenced at several
  request locations without target-specific injection.
- Scenario-step output (`$step`) for future captured values; capture declaration itself belongs to
  the Scenario model rather than a generic value directive.
- Typed transformations (`$transform`) such as Base64, URL encoding, hashing, or a future
  namespaced capability, expressed as a pipeline over another resolvable value.
- Structured patching (`$patch`) over a reusable Value, using a named standard such as JSON Merge
  Patch or JSON Patch rather than an ambiguous generic `$merge`.
- Structured selection (`$select`) using a standard path such as JSON Pointer over another
  resolvable value, avoiding a general expression language for common extraction needs.
- Explicit optionality (`$optional` or an equivalent long-form source option) that produces a
  typed `Missing` result so a containing header, query parameter, or object property can be omitted
  without confusing missing, `null`, and the empty string.
- External provider dispatch (`$provider`) for a future secret or value-provider adapter, with a
  namespaced provider ID and validated configuration.

Arbitrary command execution, embedded Python/JavaScript, network-fetch directives, and a general
expression language are not proposed. HTTP calls belong to request or future scenario steps, not
value resolution.

Unknown directives are compilation errors and no unknown directive silently passes through as
data. Future configuration versions may add typed directives or namespaced provider references,
but ADR 0015 deliberately defines no public plugin registry in `v1alpha1`. `$literal` remains the
escape for payload objects whose single key would otherwise be interpreted as a directive.

## Implementation-level follow-ups

1. The deterministic local URI syntax used for cross-resource JSON Schema `$ref`.
2. The exact schema locations and long forms for the accepted and anticipated directives.
