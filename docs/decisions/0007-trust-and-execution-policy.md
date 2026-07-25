# ADR 0007: Configuration trust and ExecutionPolicy authority

- Status: Accepted
- Date: 2026-07-23
- Owners: Comparo maintainers

## Context

Comparo may execute project configuration from an untrusted pull request while having access to
HTTP destinations, local files, CI inputs, secret providers, remote Schemas, and future plugins.
Project-owned safety declarations cannot be the sole authority for these capabilities.

## Decisions

### Trust modes

Comparo supports an explicit trusted-local mode and a restricted mode. Local CLI and TUI default to
trusted-local operation; CI integrations default to restricted operation. Trust is selected by the
invoking surface and is never inferred from Environment names or ambient CI variables.

Restricted mode treats the complete Project, including `.comparo`, as attacker-controlled data.
The compiler and runtime must not execute arbitrary YAML constructors, Project code, shell commands,
or Project-selected plugins as a consequence of interpreting configuration.

### External authority

An operator-owned ExecutionPolicy defines the maximum capabilities of a run. It is supplied outside
Project discovery and cannot be selected, replaced, or weakened by a Project resource. A Project or
Environment SafetyProfile expresses requested behavior and further restrictions. Execution-specific
configuration may restrict further.

Effective safety uses typed composition rather than precedence:

- allowlists intersect;
- denylists union;
- maximum budgets take the minimum;
- required safeguards compose with logical OR;
- allowed capabilities compose with logical AND.

Final authorization is evaluated against the immutable effective request, resolved destination,
redirect, value source, remote Schema graph, artifact behavior, and resource plan.

Every final capability grant or denial carries the exact fingerprinted `ExecutionPolicy`
definition that made the decision. A generic provenance marker is not authority. Related approvals
for one outbound operation, such as HTTP execution, mutation, delegated proxy DNS, external
analysis, and destination-bound secret egress, must use that same exact policy authority and bind
the immutable request or plan they authorize; they cannot be replayed under another effective
policy or against another request.

Serializable capability coordinates contain only typed resource identities, bounded logical
aliases, or public HTTP(S) origins/paths without user-info, query, or fragment components. Secret
tokens and signed URLs remain sealed inputs and never become authority metadata. HTTP authority
binds a finalized request only through a materialization proof for the exact EffectiveRequest;
derived byte digests must preserve every sensitive input origin.

### Restricted CI integration

A GitHub Action receives the possibly untrusted Project directory and trusted ExecutionPolicy as
separate inputs. The policy may come from a protected base revision, a centrally maintained
reusable workflow, or an operator integration that constructs the domain object directly. A policy
read from the pull request's `.comparo` tree is not operator-owned.

The action loads policy, compiles the Project, creates and authorizes a secret-free RunPlan, records
its fingerprint and policy digest, then injects explicitly approved secret providers and executes.
Named secrets are passed deliberately; broad ambient environment access and `secrets: inherit` are
not the default design.

### Capability categories

ExecutionPolicy covers at least:

- HTTP schemes, destinations, ports, methods, redirects, address classes, TLS, proxies, and client
  credentials;
- ambient environment names, file roots, secret providers, logical secret names, and explicit
  destination-bound secret egress;
- remote Schema sources and their transitive references;
- any future external-capability identities, versions, integrity, effects, and data egress;
- concurrency, execution, duration, body, decoding, and artifact budgets;
- raw-versus-sanitized persistence capabilities.

Redirects and DNS results are reauthorized. File access is canonicalized and symlink-safe. In
restricted mode, ambient proxy, `.netrc`, CA, and certificate behavior is disabled unless the
operator policy permits it.

### Mutations and approval

Permission to use a mutating HTTP method and approval of a concrete run are separate. A
SafetyProfile may require operator approval even when ExecutionPolicy permits the method. Approval
is supplied outside the Project and bound to the immutable RunPlan fingerprint; changes to methods,
destinations, or expansion invalidate it. Cleanup and Scenario `finally` steps remain subject to
the same authorization.

### Future extensions and invariants

ADR 0015 exposes no public plugin system initially. If one is added, in-process community code is
trusted executable code, not sandboxed data. Restricted mode disables it unless the operator
allowlists and pins it. A Project may request a future capability but cannot authorize one.

Safe parsing, secret-safe diagnostics, terminal control escaping, final-request validation, unknown
directive rejection, and defensive handling of hostile response/decoder input remain engine
invariants in every trust mode. They are not arbitrary project-size product limits.

The initial restricted policy therefore requires a `values.secretEgress` section. Each rule names
the exact logical secret and/or `$env` namespace sources it authorizes and binds them to allowed
HTTP schemes, methods, hosts, and ports. The egress source `environment` covers the isolated
snapshot after precedence, whether the winning value came from an inherited process variable, a
declared dotenv file, or Environment inline values; `values.ambientEnvironment` separately governs
which parent-process names may enter that snapshot. Every sensitive origin carried by a finalized
request must be covered. Unknown origins or destination mismatches are denied before DNS. The
trusted-local policy exposes the same capability explicitly through `allowAny`; ordinary HTTP
destination permission never implies permission to disclose a secret.

## Consequences

- Comparo can intentionally process untrusted PR configuration without treating repository policy
  as authority.
- Local operation remains lightweight while using the same capability model as CI.
- Policy provenance and digest make CI results auditable without persisting secret policy inputs.
- A future isolated-plugin design would be a new security boundary rather than a claim layered over
  in-process Python execution.
