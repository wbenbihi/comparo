# ADR 0008: Value lifetimes, snapshots, and scopes

- Status: Accepted
- Date: 2026-07-23
- Owners: Comparo maintainers

## Context

Variables, environment sources, files, secrets, generated inputs, provider results, Matrix values,
and Scenario captures must remain stable over the correct portion of a run. Compared environments
normally receive the same logical generated inputs, while authentication and captured state may be
environment-local. Concurrent scheduling and retries must not change generated values.

## Decisions

### Runtime scope vocabulary

Runtime values use these scopes:

- `run`: one value for the entire run;
- `cell`: one value for a logical Request/Matrix or Scenario/Matrix cell, shared across compared
  environments;
- `trial`: one value for a repeated sample of a logical cell, shared across the compared
  Environments in that sample;
- `environment`: one value for an Environment across all cells in the run;
- `execution`: one value for one trial of one cell in one Environment;
- `step`: one value for a Scenario step in one Environment, cell, and trial;
- `attempt`: one value for a concrete HTTP attempt.

Cell/trial and Environment are independent coordinates that meet at execution scope; the model is
a scope lattice rather than a misleading linear parent chain. Trial exists even when its only
value is the implicit first sample. A value may depend only on values available at the same or
broader coordinates. The compiler rejects broader-scope expressions that depend on narrower
Matrix, trial, Environment, step, or attempt state.

### Defaults and snapshots

`$generate` defaults to cell scope so compared environments receive equivalent generated input.
It therefore also remains stable across repeated trials unless trial-scoped generation is
explicitly requested. Other sharing behavior is explicit. Project secret bindings resolve once per
run and Environment secret bindings once per Environment per run unless their validated provider
contract explicitly declares another permitted scope.

Ambient process environment and invocation overlays are snapshotted at run start. Permitted files
are snapshotted on first use and remain stable for the run. `$from` selection and both successful
and failed provider resolution remain stable within the consumer scope unless an explicit provider
retry contract applies. No resolved value is cached across separate runs by default.

Scenario `$step` outputs have a fixed Environment-local step identity and cannot be widened by
configuration into cross-environment values.

### Deterministic and secure generation

Ordinary generated test data derives from a root run seed, stable directive identity, and scope
coordinates. It does not consume a scheduling-order-dependent global random stream. Non-sensitive
seeds may be recorded for reproducibility.

Secure generation is a distinct sensitive category using cryptographic randomness. Its seed and
value are not persisted and are non-reproducible by default. Sensitivity propagates through value
selection, interpolation, transformation, patching, and provider composition.

### Sanitization lifecycle

Every sensitive value used on the wire is registered with the RunContext sanitizer from resolution
until the context closes. Attempt-scoped rotations remain registered even after later attempts
replace them. Artifacts retain source identity, scope, provider, and sensitivity provenance rather
than resolved secret contents.

## Consequences

- Worker concurrency cannot change generated request data.
- Matrix cases vary declared inputs, while trials repeat one case and attempts retry one
  Environment execution; the three cannot be conflated.
- Idempotency keys can remain stable over retries while attempt IDs deliberately vary.
- Long-running TUI sessions do not accidentally reuse values or secrets from an earlier run.
- A baseline capture cannot enter a candidate request through a scope override.
- Source failures do not trigger inconsistent fallback choices across uses in the same scope.

## Implementation status

Implemented in Step 6 on 2026-07-24. `RunContext`, the scope projector, single-flight provider
caches, deterministic generator, explicit conversion service, and live sensitive-value registry
enforce this decision. The public `$generate` syntax and Scenario `$step` capture provider remain
deferred; their internal scope and provider seams are present without freezing an external plugin
contract. See [Run-scoped values and secrets](../run-scoped-values.md).
