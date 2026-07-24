# ADR 0002: Configuration layering and value resolution

- Status: Accepted
- Date: 2026-07-23
- Owners: Comparo maintainers

## Context

Comparo receives effective values from several places:

- Engine defaults.
- Project defaults.
- Reusable object configuration.
- Environment configuration.
- ExecutionProfile configuration.
- A concrete CLI, TUI, or CI invocation.
- Environment files and the ambient process environment.
- Secret providers and fallback chains.
- Generated run, cell, step, or attempt values.
- Operator-owned execution policy.

The current engine resolves some of these with ad hoc precedence rules. The redesigned engine needs
one explicit binding and resolution model while preserving the fact that ordinary configuration,
value lookup, and authority restriction compose differently.

## Decision categories

### Structural configuration precedence

Ordinary execution settings bind from least to most specific:

```text
engine default
  -> Project
  -> Environment or referenced reusable profile, where applicable
  -> ExecutionProfile
  -> RunInvocation from CLI / TUI / CI
  -> EffectiveExecution
```

The exact participating layers depend on the setting. For example, an environment timeout can
participate in request timeout calculation, while environment selection itself is supplied by the
Project, ExecutionProfile, or invocation.

This is not implemented as a recursive dictionary merge. Each typed setting declares whether an
override replaces, appends, removes, clears, or merges by key. Absence, an explicit empty value,
and an explicit clear operation are distinct.

### Value-resolution precedence

Ordinary named variables bind from least to most specific:

```text
Project declaration/default
  -> Environment override
  -> ExecutionProfile global binding
  -> ExecutionProfile environment-specific binding
  -> RunInvocation global override
  -> RunInvocation environment-specific override
```

An explicit environment-source lookup such as `$env: NAME` uses this least-to-most-specific order:

```text
ambient process environment, when inherited
  -> Environment dotenv files
  -> Environment inline env values
  -> ExecutionProfile global env overlay
  -> ExecutionProfile environment-specific env overlay
  -> RunInvocation global env overlay / CLI env file
  -> RunInvocation environment-specific env overlay / CLI env file
```

An empty string found in any source is a real value and stops lookup. Only absence proceeds to the
next source.

A logical declared secret binds from least to most specific:

```text
Project secret binding
  -> Environment secret binding
  -> ExecutionProfile global binding
  -> ExecutionProfile environment-specific binding
  -> RunInvocation or operator-injected global binding
  -> RunInvocation or operator-injected environment-specific binding
```

Secret values do not silently fall back into the ordinary variable namespace.
At a given layer, an environment-specific value wins over a global value. ExecutionPolicy locks,
caps, and forced operator values are authority constraints rather than another ordinary
last-writer-wins layer.

### Environment-scoped dotenv sources

An Environment may populate its `$env` namespace without exporting values into the Comparo
process:

```yaml
spec:
  env:
    inheritProcess: true
    files:
      - path: env/common.env
      - path: env/staging.env
        onConflict: override
    values:
      REGION: eu-west-1
```

These values are visible only through `$env` or `${env.NAME}`. They are not automatically injected
into requests, variables, secrets, parameters, plugins, subprocesses, or the HTTP client's proxy
and TLS behavior. Every compared Environment owns an isolated env namespace, and all sources are
snapshotted at run start.

The dotenv grammar is deterministic: UTF-8 `KEY=value` entries, comments, blank lines, and quoted
values are supported; shell execution, command substitution, and implicit variable expansion are
not. Values begin as strings and require explicit conversion. Duplicate keys within a file are
errors. Across ordered files, `onConflict` is `error` by default and may explicitly be `override`
or `keep`.

Relative paths resolve from the Project root. Canonical paths outside that root require external
ExecutionPolicy authorization. The source path and key may appear in provenance, but file contents
and resolved values do not. Restricted policy may disable ambient inheritance, constrain names and
paths, or lock injected values. HTTP transports must not implicitly honor ambient variables such
as proxy or certificate settings; those capabilities require explicit Comparo configuration.

### Value directives and namespaces

Value directives are a location-independent value language. `$env`, `$file`, `$from`, `$literal`,
`$var`, `$secret`, and future value directives may be used anywhere the schema declares a
resolvable value. A secret declaration is one such location, not a special directive language:

```yaml
secrets:
  API_TOKEN:
    $from:
      - $env: PROD_API_TOKEN
      - $file: .secrets/prod-token
```

The same fallback form can appear directly in a request value or any other resolvable value:

```yaml
query:
  tenant:
    $from:
      - $var: DEFAULT_TENANT
      - $env: TENANT_ID
      - $literal: public
```

`$from` accepts the full value-source grammar and tries entries in declared order. It advances only
when a source is unavailable; malformed, forbidden, or failed sources are errors rather than
reasons to silently try a later fallback.

Namespaces solve a different problem: they disambiguate which logical symbol table a named lookup
uses. They do not restrict where directives are available. Interpolation uses plural namespace
names matching their declaration sections:

```yaml
region: ${variables.REGION}
authorization: Bearer ${secrets.API_TOKEN}
build: ${env.BUILD_ID}
tenant: ${parameters.TENANT_ID}
locale: ${matrix.locale}
token: ${steps.login.token}
```

Whole-value directive forms remain available:

```yaml
region: { $var: REGION }
token: { $secret: API_TOKEN }
build: { $env: BUILD_ID }
tenant: { $param: TENANT_ID }
locale: { $matrix: locale }
token: { $step: login.token }
```

There is no secret-first cross-namespace lookup. A variable and secret may have the same logical
name without one silently changing the meaning of the other.

Not every YAML node is resolvable: structural fields needed to compile and plan safely, such as
`apiVersion`, `kind`, object identifiers, reference identifiers, selector syntax, operator names,
and value scopes remain static. URLs, request values, assertion expectations, thresholds, timeout
values, and other runtime data may use the resolvable-value grammar where their evaluation scope is
well-defined.

This structural boundary is accepted: value directives are available consistently throughout
runtime-resolvable data, but configuration identity and compilation-control fields remain static.

### Execution parameters

An executable Request or Scenario declares its typed public input contract rather than implicitly
overriding Environment or Project variables:

```yaml
parameters:
  tenantId:
    type: string
    required: true
  limit:
    type: integer
    default: 50
  testIdentity:
    type: string
    required: true
    sensitive: true
```

ExecutionProfiles provide reusable bindings and RunInvocation supplies CLI flags, a TUI form, or
CI action inputs. A Scenario binds a referenced Request's parameters explicitly through step
`with`; step bindings do not create same-kind Request inheritance. Defaults are resolvable values.
Missing required parameters fail before planning or network activity. Parameters occupy their own
namespace and do not implicitly replace variables.

The initial generic types are `string`, `integer`, `number`, `boolean`, `bytes`, and `json`.
Structured constraints use a Schema rather than a second embedded type language. Parameters are
non-nullable unless explicitly declared nullable. The least-to-most-specific parameter chain is
declared default, ExecutionProfile binding, RunInvocation binding, and Scenario step `with` inside
that step.

### Explicit conversion and interpolation

Native YAML scalars keep the type the author wrote. External text and bytes are never converted
because of the receiving field. The explicit conversion vocabulary is `string`, `bytes`,
`integer`, `number`, `boolean`, `json`, `yaml`, `duration`, and `size`. Conversions are strict:
integers are base ten, numbers are finite, booleans are exactly `true` or `false`, and no whitespace
is trimmed implicitly. A file short form is lossless bytes; text or structured parsing must be
explicit.

JSON or YAML produced by conversion is inert data and is never rescanned for Comparo directives.
An interpolation occupying the entire scalar retains its native value; embedded interpolation
requires a scalar string representation and never silently serializes objects, arrays, or bytes.
`$${...}` escapes interpolation, while `$literal` disables interpolation and directive recognition
recursively for its payload.

### Exact directive result model

Value resolution returns one of five distinct results: `Present` with type, sensitivity, and
provenance; `Unavailable` with an absence reason; intentional `Missing`; `Error`; or policy
`Blocked`. Empty strings, `null`, `false`, and zero are present values. A required receiving field
promotes an unhandled unavailable value to a resolution failure.

`$var`, `$secret`, and `$param` resolve only their named namespaces. `$secret` always taints its
result, and a sensitive parameter does the same. `$env` reads the effective environment-specific
snapshot. Their long forms may request explicit conversion. Missing names are unavailable; lookup
and Value cycles are errors with a complete source-located chain.

`$file` returns exact bytes in short form. Its long form permits an explicitly selected conversion,
trim behavior, and a narrowing byte budget. Paths are Project-root-relative by default and may be
runtime-resolvable. They never represent globs, directories, or URLs. The canonical opened target
must pass SafetyProfile and ExecutionPolicy checks, including symlink-safe root confinement.
Missing files are unavailable; permission, decoding, budget, and policy failures are not.

`$from` is a non-empty ordered list of complete value sources. It advances only on `Unavailable`,
short-circuits on the first present value, and returns unavailable if all sources are unavailable.
Conversion errors, provider failures, malformed sources, and policy denials stop the chain. This
prevents an inaccessible production source from silently falling back to a weaker credential.

`$optional` wraps a complete source or `$from` chain and converts only unavailable into intentional
Missing. Errors and blocked results propagate. Omission is allowed only in receiving containers
whose schema defines it, initially headers, query entries, cookies, form entries, and JSON object
members. Required fields reject Missing, and list-element omission is not initially supported.

`$literal` disables both directive and interpolation recognition recursively. In an ordinary
resolvable field, an object consisting of exactly one registered directive key is a directive;
known directives with siblings are malformed, and an unknown single `$`-prefixed key requires
`$literal`. Multi-key application data may contain unknown `$`-prefixed keys. Opaque Schema
payloads and externally parsed JSON/YAML are never interpreted as Comparo directives.

`$use` is a static, receiving-field-typed resource reference. Its identifier cannot be
interpolated, a generic value slot accepts only a Value, and it accepts no override siblings.
Cycles are compile errors; JSON Schema recursion through `$ref` is separate. A Value is evaluated
at the receiving runtime scope, so environment and future generated values retain their declared
semantics.

Future built-in directive names such as `$matrix`, `$generate`, `$step`, `$select`, `$transform`,
`$patch`, and `$provider` fail as unsupported capabilities until implemented rather than passing
as data. A future configuration version may define `$provider` dispatch with a namespaced,
versioned ID, but ADR 0015 defines no public provider/plugin contract yet. ExecutionPolicy controls
any such future availability. Shell execution, a general expression language, implicit network
retrieval, and unregistered global directives are outside the value language.

Generated values use an explicit scope. A run or cell value is created once and reused wherever
that scope applies, including across compared Environments and repeated trials. Trial-,
Environment-, execution-, step-, and attempt-scoped generation deliberately narrows that sharing.
An invocation may supply a deterministic seed without changing the declared scope.

### Safety and authority composition

Safety is not last-writer-wins precedence. Effective authority is the intersection of:

```text
operator-owned ExecutionPolicy
  ∩ project or environment SafetyProfile
  ∩ execution-specific restrictions
```

An invocation may select a different environment or request set, after which safety is recomputed
for the effective selection. It cannot override or clear restrictions.

## Binding and snapshot lifecycle

The intended lifecycle is:

```text
CompiledProject
  + selected ExecutionProfile
  + typed RunInvocation
  -> EffectiveExecution with configuration provenance
  -> safety authorization
  -> immutable RunPlan
  -> run-scoped value snapshot
  -> resolved requests
```

The `RunInvocation` and compiled configuration describe sources and overrides but do not eagerly
read secrets or external values. At run start, a `RunContext` snapshots permitted overlays and
owns provider caches, generated values, clocks, randomness, cancellation, and the secret registry
used by sanitization.

Every resolved non-literal value records provenance without exposing the value:

```text
logical name
namespace (variable / environment source / secret / generated)
configuration layer
concrete provider
scope
sensitivity
```

The same snapshot that resolves a value for the wire is used by the sanitization boundary. Reports
and frontends never reconstruct source values after execution.

## Typed domain concepts

```text
RunInvocation
EffectiveExecution
ConfigurationProvenance
ResolutionContext
ValueNamespace
ValueSource
ResolvedValue
SecretValue
ValueScope
ExecutionPolicy
SafetyProfile
```

An override uses explicit states such as `inherit`, `set`, and `clear`. Collection overrides use
explicit operations such as `replace`, `append`, and `remove`; no caller depends on an implicit
deep-merge algorithm.

## Accepted field phases and typed overlays

Every schema field is classified by the schema author as static, bindable, resolvable with an
explicit dependency scope, or opaque. Static identity, graph, operator, selector, method, scope,
provider-ID, and authority fields reject value directives. Bindable execution choices are changed
only through ExecutionProfile or typed RunInvocation data. Runtime-resolvable request, assertion,
and Scenario data compiles to a typed expression tree. JSON Schema and explicit application-data
boundaries remain opaque.

A resolvable field also declares its permitted namespaces and latest resolution scope. Matrix
values are shared logical cell input and cannot depend on an Environment, secret, prior Scenario
step, or attempt. Assertions may use their Environment context. Diff rules have no implicit
single-side Environment context, and gates are run-scoped. Scenario step bindings may read only
declared outputs from completed predecessors in the same Environment.

Overrides use typed overlay schemas, never recursive YAML merge. Scalar operations are `inherit`,
explicit `set`, and explicit `clear`; `null`, empty values, and absence remain distinct. Maps use
the domain-appropriate subset of `replace`, `remove`, `set`, `add`, and `clear`. Lists use explicit
`replace`, `append`, ID-based `remove`, and `clear`; stable-ID insertion operations may be added
where ordered collections require them. Replacement and clear operations are exclusive, and
multi-operation order is fixed by the receiving field.

Scenario steps use a constrained RequestOverlay. It may change URL components, headers, query,
cookies, authentication binding, body, and timeout, but not method, identity, parameter contract,
Matrix structure, or safety requirements. Request bodies are initially replace-only; future
structured `$patch` provides explicit patch standards. Every final effective request is
re-authorized after binding, overlay application, and resolution and before network activity.

## Consequences

- CLI and TUI selections are temporary invocation data rather than mutations to YAML objects.
- Plan previews can explain where every effective setting came from.
- A CLI env file can intentionally override an environment's configured source while remaining
  subject to external policy.
- Secret source rotation cannot cause execution and sanitization to observe different values.
- Adding future Scenario captures or external value-provider adapters does not require changing generic
  precedence rules; each provider declares its namespace and scope.
- Configuration syntax can remain ergonomic because the compiler lowers it into explicit typed
  operations.
- Project and Environment objects may both declare variables and secrets; environment values take
  precedence over Project defaults within the `variables` and `secrets` namespaces.
- Invocation environment overlays support both a common overlay and per-environment overlays.
- Trusted local execution permits explicit `$env` access to the ambient process environment by
  default, while restricted execution may deny or allowlist it.
- Interpolation uses explicit namespaces and does not perform secret-first cross-namespace lookup.
- Requests and Scenarios declare typed parameters; ExecutionProfiles and RunInvocations bind them,
  and Scenario steps may bind referenced Request parameters explicitly.
- Environment dotenv files populate isolated `$env` namespaces without mutating process state.

## Implementation-level follow-ups

1. Which ambient process environment names restricted CI permits; explicit `$env` access is
   enabled by default in trusted local mode.
2. The final per-kind field inventory generated from the concrete `comparo/v1alpha1` schemas.
3. The exact ExecutionPolicy lock-path syntax and which bindable fields ship locked by default in
   the restricted CI policy.
