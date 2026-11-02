# ADR 0001: Product boundary

- Status: Accepted
- Date: 2026-07-23
- Owners: Comparo maintainers

## Context

Comparo is currently an alpha-stage terminal application and executable prototype. There are no
known external users and the project does not recommend the current release for production use.
The core and backend will therefore be redesigned without preserving the existing configuration,
CLI, artifact, TUI, or internal Python APIs.

The redesign needs a clear product boundary so the domain, configuration compiler, planner,
runtime, security model, and extension seams can be developed independently.

## Canonical product promise

Given the same logical HTTP request and inputs, Comparo executes it against one or more
environments, validates each response independently with assertion profiles, compares selected
environment pairs with diff profiles, and produces a reliable gate verdict.

The product may gain deeper assertions, comparisons, workflows, and extensions, but those features
must reinforce this promise rather than turn Comparo into a general-purpose automation runtime.

## Accepted decisions

### Product surfaces

Comparo is a lightweight open-source tool. Its supported product surfaces are:

- Local CLI.
- Terminal UI.
- CI execution.

A stable public Python API, daemon, shared team server, and hosted service are not current product
requirements. Internal module contracts should still be clean enough to embed, test, and replace.

### Compatibility

The current implementation is alpha and has no known users. Nothing in the existing
`comparo/v1` configuration format, CLI, artifact format, TUI behavior, or internal Python API needs
to be preserved.

The redesigned lineage starts at `comparo/v1alpha1` and its production configuration will be the
first stable `comparo/v1`, not `comparo/v2`. Backward compatibility begins with the published new
lineage rather than the prototype and follows ADR 0016.

### Execution primitive and future workflows

An independent request cell remains the initial execution primitive. The architecture must,
however, reserve explicit seams for future stateful scenarios or workflows containing dependent
steps, captured values, setup, cleanup, and authentication flows.

Workflow behavior must be layered above the request-cell primitive rather than simulated by
increasingly powerful generic interpolation directives.

### Future extensions

The architecture preserves narrow typed internal capability seams for future functionality such as
an LLM judge. It does not stabilize a public plugin system in the initial format. Packaging,
runtime, and isolation are deliberately deferred under ADR 0015; in-process community code would
be trusted executable code unless Comparo introduced a real isolation boundary.

### Configuration trust and execution authority

Comparo supports two trust modes:

- A local trusted mode for a developer executing their own project.
- A restricted CI mode in which project configuration cannot grant itself additional authority.

An operator-owned `ExecutionPolicy`, supplied outside the project configuration, controls the
maximum hosts, methods, secret inputs, file capabilities, plugins, and resource budgets available
to a run. Project configuration may request or further restrict capabilities, but cannot exceed
the external policy. Restricted mode is the default posture for CI integrations.

### Environment identity

Environment names are arbitrary and do not have hardcoded behavioral meaning. Terms such as
`dev`, `staging`, and `prod` are conventions rather than reserved identities. Safety behavior is
expressed with a typed `SafetyProfile`, not inferred from an environment name or classification.

An environment may attach a reusable `SafetyProfile` object or define an inline safety profile.
The project-owned safety profile describes intended restrictions; it cannot grant permissions
beyond the operator-owned `ExecutionPolicy` in restricted mode.

### Mutating HTTP requests

Comparo must support mutating methods such as `POST`, `PUT`, `PATCH`, and `DELETE`. Production or
otherwise sensitive environments may require explicit opt-in before those methods are executed.
SafetyProfile may require approval, while the external ExecutionPolicy supplies operator authority
and binds approval to the immutable RunPlan as defined in ADR 0007.

### Deterministic compared inputs

Dynamic values generated for a comparison cell are, by default, generated once and reused across
the environments being compared. Value scopes may later expose explicit exceptions, but
environment-local generation must not be the accidental default.

### Configuration defaults and invocation overrides

An `ExecutionProfile` is a reusable execution template, not an immutable final plan. Project
configuration supplies defaults, an execution profile may refine those defaults, and a concrete
CLI or TUI invocation may deliberately override supported values such as the selected baseline and
candidate environments.

Frontends do not mutate the compiled project or profile. They produce a typed `RunInvocation`
containing explicit overrides. The planner binds project defaults, the selected execution profile,
and the invocation into one immutable `EffectiveExecution` before expanding a `RunPlan`.

Every effective setting retains provenance identifying whether it came from an engine default,
the Project, the ExecutionProfile, or the invocation. The plan preview and persisted artifact must
record the effective environment selection and meaningful overrides so a run remains explainable
and reproducible.

Invocation precedence does not grant safety authority. The actual environments selected after
binding are checked against their `SafetyProfile` and the external `ExecutionPolicy`; a CLI or TUI
override cannot bypass either policy.

### Public library contract

A stable public Python API is not a current requirement. Internal APIs may change while the new
kernel is being developed. Stable external contracts are the configuration language, CLI behavior,
and artifact formats once each is explicitly declared production-ready.

### Initial protocol boundary

The first production kernel supports HTTP/1.1, HTTP/2, buffered HTTP responses, Server-Sent
Events, and chunked JSON streams. WebSocket and gRPC are deferred and may later use distinct
transport and observation contracts. Browser automation is explicitly outside Comparo's scope.

## Related decisions

- ADR 0005 defines Environment selection and baseline-to-many expansion into directed binary
  comparisons without a redundant `targets` list.
- ADR 0007 defines untrusted Project execution and external operator authority.
- ADR 0009 separates fact production, exploratory completion, and GateProfile verdicts.
- ADR 0010 defines full capture, mandatory sanitization, and artifact persistence.
- ADR 0014 defines configurable finite budgets, admission control, and bounded lazy execution.
- ADR 0015 defers the public plugin model while retaining extension seams.
- ADR 0016 defines compatibility from the new configuration lineage onward.

## Consequences

- The refactor can introduce a new alpha configuration format and later freeze it as the first
  production `comparo/v1`.
- Core contracts optimize for local and CI execution rather than distributed multi-tenant
  operation.
- Pairwise diff stays simple even when the planner supports several environments.
- Scenario and internal capability seams are preserved, but their optional implementations do not
  block the initial kernel.
- Project `SafetyProfile` objects are reusable declarations of intent, while external
  `ExecutionPolicy` remains the authority boundary in restricted execution.
