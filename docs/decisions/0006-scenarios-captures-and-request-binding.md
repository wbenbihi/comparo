# ADR 0006: Scenario boundary, captures, and Request binding

- Status: Accepted
- Date: 2026-07-23
- Owners: Comparo maintainers

## Context

Comparo must anticipate future stateful workflows without creating a second HTTP, assertion, diff,
secret, or sanitization engine. A Scenario needs to reuse Request resources while injecting
declared outputs from earlier steps and occasionally refining request-specific fields.

## Decisions

### Scenario boundary

Scenario is a first-class resource layered above the existing Request execution primitive. Steps
contain either an inline Request or a reference to a reusable Request. The Scenario scheduler uses
the ordinary resolver, transport, observation, assertion, binary diff, and sanitization modules.

Initial authoring is sequential and the compiled form is dependency-graph-ready. There is no
general expression language or arbitrary loop. Scenario-level Matrices expand the whole workflow;
step-local fan-out is deferred until explicit foreach and join semantics exist.

### Declared outputs and capture scope

Steps expose only explicitly declared named outputs. An output has a shared Selector, declared
type, required/optional state, sensitivity, and an explicit conversion when needed. Later steps
access it using `$step` or the equivalent interpolation namespace. Arbitrary traversal of a prior
raw response is not exposed as an implicit value namespace.

Captured outputs are scoped to Scenario cell, trial, Environment, producing step, and output name.
A baseline capture never enters a candidate request implicitly. Run- and cell-scoped generated
inputs remain shared across compared Environments and repeated trials by default.

### Stateful execution and failure

Session state, cookies, and captures are reused between steps in the same Environment, Scenario
cell, and trial and isolated from every other Environment, cell, and trial. A failure blocks only
dependent steps in that Environment. Independent Environments and branches continue. Corresponding
comparisons are incomplete when a required side is unavailable. `finally` steps support cleanup
while remaining subject to effective safety policy.

### Reusable Request parameters

A reusable Request may declare typed parameters and consume them through the existing explicit
parameter namespace:

```yaml
spec:
  parameters:
    userId:
      type: string
      required: true

  request:
    method: GET
    endpoint: /users/${parameters.userId}
```

A Scenario step binds those parameters from literals, Scenario parameters, prior declared outputs,
or any permitted resolvable value. Parameter binding is the preferred mechanism for values that
the reusable Request intentionally expects callers to supply.

### Ephemeral typed overrides

A step referencing a Request may also produce an ephemeral `EffectiveRequest` through typed
overrides. Scalar fields use explicit replacement. Headers, query parameters, and other
duplicate-capable collections use set/add/remove/clear operations. JSON bodies may use an explicit
JSON Merge Patch or JSON Patch operation. There is no generic recursive YAML merge.

Overrides may contain `$step` and other value directives. They cannot alter resource metadata,
Scenario dependencies, environment selection, Matrix fan-out, or safety authority. Assertion and
diff attachment remains an evaluation concern rather than being hidden inside an HTTP override.

This is per-use binding, not Request inheritance: the result has no reusable resource identity,
does not mutate the referenced Request, and retains field-level provenance. Safety validation is
performed against the final effective method, URL, headers, body, and other capabilities, so an
override cannot bypass method or destination restrictions.

## Consequences

- Reusable Requests behave like typed templates with explicit caller contracts.
- Scenario-specific values do not require copying complete Request documents.
- Refactoring a Request body or endpoint cannot silently break a path-based generic deep patch.
- The compiler can derive step dependencies from `$step` references and reject cycles before
  execution.
- Request reuse works for standalone invocation and future Scenarios without introducing a second
  parameter or transport model.

## Implementation-level follow-ups

1. Exact external syntax for the referenced Request binding wrapper.
2. Complete typed override operation set per HTTP field.
3. Parameter-frame rules between RunInvocation, Scenario inputs, and nested Request bindings.
4. Whether all output sensitivity is explicit or some HTTP sources receive safe automatic
   sensitivity defaults.
5. Exact `finally`, future `needs`, and conditional-step syntax.
