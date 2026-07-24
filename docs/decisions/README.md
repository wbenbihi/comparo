# Comparo core architecture decisions

Step 0 of the core refactor roadmap was completed on 2026-07-24. These ADRs define the accepted
product and architecture boundaries for the new configuration lineage beginning at
`comparo/v1alpha1`.

| ADR | Decision area | Status |
| --- | --- | --- |
| [0001](0001-product-boundary.md) | Product promise, surfaces, and protocol boundary | Accepted |
| [0002](0002-layering-and-resolution.md) | Configuration precedence, namespaces, values, and dotenv | Accepted |
| [0003](0003-configuration-resource-model.md) | Resource identity, discovery, kinds, directives, Matrix, and Schema | Accepted |
| [0004](0004-selectors-and-body-views.md) | Selectors, decoded views, SSE, and JSON streams | Accepted |
| [0005](0005-multi-environment-execution.md) | Environment selection and binary comparison topology | Accepted |
| [0006](0006-scenarios-captures-and-request-binding.md) | Stateful Scenario boundary and Request injection | Accepted |
| [0007](0007-trust-and-execution-policy.md) | Trust modes, SafetyProfile, and external ExecutionPolicy | Accepted |
| [0008](0008-value-lifetimes-and-scopes.md) | Run, cell, trial, Environment, execution, step, and attempt scopes | Accepted |
| [0009](0009-outcomes-and-gating.md) | Execution, assertion, diff, completion, and gate outcomes | Accepted |
| [0010](0010-capture-sanitization-and-artifacts.md) | Full capture, mandatory sanitization, and artifact persistence | Accepted |
| [0011](0011-http-exchange-and-transport.md) | HTTP behavior, observations, request construction, and timings | Accepted |
| [0012](0012-assertion-engine-and-profiles.md) | Assertion evaluation, operators, assignments, and composition | Accepted |
| [0013](0013-diff-engine-and-profiles.md) | Select/derive/pair/compare pipeline, exclusions, and trials | Accepted |
| [0014](0014-resource-budgets-and-backpressure.md) | Layered budgets, admission control, and bounded execution | Accepted |
| [0015](0015-extension-seams-and-deferred-plugin-model.md) | Internal capability seams and deliberately deferred plugin model | Accepted |
| [0016](0016-compatibility-versioning-and-migration.md) | New-lineage compatibility, independent versions, and migration | Accepted |

## Meaning of completion

The foundational choices that determine module boundaries, authority, data flow, outcome semantics,
and future compatibility are closed. Implementation may refine algorithms and external syntax only
within these constraints. A change that alters an accepted invariant requires a superseding ADR.

The following are intentionally not Step 0 blockers:

- generated per-resource YAML and JSON Schemas and their ergonomic shorthand inventory;
- concrete immutable domain type definitions and module APIs;
- shipped numeric defaults for documented budget dimensions;
- exact artifact manifest fields, CLI exit codes, and machine-report envelopes;
- implementation algorithms, storage adapters, and performance tuning;
- public plugin packaging, runtime, installation, and isolation, explicitly deferred by ADR 0015;
- features already marked as later scope, including WebSocket, gRPC, browser automation, and
  advanced Scenario fan-out.

Those items belong to subsequent roadmap steps or are deliberate product deferrals. They must not
be implemented by weakening strict validation, pure evaluation, safety authority, sanitization, or
compatibility guarantees accepted here.
