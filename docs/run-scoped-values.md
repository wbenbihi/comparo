# Run-scoped values and secrets

Step 6 implements Comparo's value-resolution boundary. It turns the unresolved expressions in a
`PreparedRun` into exact immutable values without reading ambient process state from core code.
Every effect is injected, scoped, cached, provenance-bearing, and closed with its owning run.

```text
PreparedRun + EnvironmentSnapshot + CellInputs + injected ports
  -> RunContext
       -> ExpressionResolver          pure expression semantics
       -> scope projection            declared lifetime -> concrete coordinate
       -> single-flight source caches one read per exact source/scope
       -> ProviderRuntime             exception containment, taint, typed outcomes
       -> SensitiveValueRegistry      live values retained until close
  -> Present | Unavailable | Missing | Error | Blocked
  -> ResolvedForWire(value, SanitizationSnapshot)
```

This is a core service, not a filesystem, dotenv, secret-manager, or YAML implementation. The
composition root supplies already admitted snapshots and narrow adapters. Step 7 will wire those
ports into the first complete offline runtime.

## Responsibilities

`RunContext` owns one run's mutable lifetime and nothing broader:

- the immutable `PreparedRun` and exact selected-Environment snapshots;
- the run coordinate, cancellation signal, and injected clock;
- source-specific single-flight caches;
- deterministic and optional secure generation providers;
- registered external, file, and inert-YAML provider ports;
- every sealed value the later sanitizer must recognize.

`ExpressionResolver` owns the provider-independent language: literals, namespace lookups,
fallback, optional omission, arrays, objects, interpolation, and explicit conversion. It can
observe cancellation and call only the narrow `ResolutionHost` protocol. It cannot read a file,
environment variable, clock, random source, or provider registry directly.

`ProviderRuntime` is the shared effect boundary. It contains provider exceptions, rejects invalid
adapter output, adds source taint inside the shared source task, and maps outcomes into canonical
resolution provenance. This keeps failure and sensitivity behavior identical across file,
external, generation, and YAML providers.

## Immutable inputs and precedence

The planner has already reduced Project, Environment, ExecutionProfile, and RunInvocation inputs
to ordered `ValueBindingLayer` records. Resolution walks those records from least to most specific
and retains the last applicable binding. It does not merge YAML or recompute configuration
precedence.

The namespaces remain independent:

| Namespace | Binding source | Runtime production coordinate |
| --- | --- | --- |
| `variables` | effective value layers | run or matching Environment |
| `secrets` | effective value layers, always sealed | run or matching Environment |
| `parameters` | effective executable bindings | cell or matching execution |
| `env` | injected immutable Environment snapshot | matching Environment |
| `matrix` | the planned cell's Matrix case bindings | matching cell |
| `steps` | reserved for Scenario captures | deferred |

`$env` never reads `os.environ`. Before a context is created, an adapter applies the accepted
ambient/dotenv/inline/profile/invocation precedence and creates one isolated `EnvironmentSnapshot`
for every selected Environment. An empty string is present. Missing names are unavailable. Env
files therefore populate only the explicit `env` namespace and never mutate the Comparo process
or implicitly configure an HTTP client. Every winning snapshot value is sealed with the canonical
Environment-symbol origin, regardless of which input layer supplied it; changing precedence cannot
evade destination-bound egress authorization.

Multiple Matrix bindings can participate in one request. A qualified symbol such as
`auth.identity` selects the Matrix bound as `auth`; an unqualified symbol is accepted only when
exactly one attached Matrix defines it. Ambiguity is unavailable rather than dependent on binding
order.

## Scope lattice and cache identity

Runtime lifetimes are not a simple parent chain. Cell/trial and Environment are independent axes
that meet at execution:

```text
                       run
                  /           \
             cell -> trial   Environment
                  \           /
                    execution
                        |
                       step
                        |
                     attempt
```

| Declared scope | Shared by |
| --- | --- |
| `run` | every consumer in one run |
| `cell` | all compared Environments and trials for one logical input cell |
| `trial` | all compared Environments in one repeated sample |
| `environment` | one selected Environment across the run |
| `execution` | one cell/trial/Environment join |
| `step` | one Scenario step execution |
| `attempt` | one concrete HTTP attempt |

Scope projection fails when the consumer does not contain the requested coordinate. A cell value
cannot accidentally become Environment-local, and an Environment or step value cannot be widened
to a baseline/candidate-shared value.

Effectful sources use exact immutable cache keys:

| Source | Single-flight key |
| --- | --- |
| external provider or future secret manager | source contract + projected production scope |
| deterministic or secure generation | generation specification + projected production scope |
| confined file | compiled file expression + consumer scope |
| inert YAML decode | conversion expression + consumer scope |
| `$env` | immutable run-start Environment snapshot; no live read |

Pure expressions may be evaluated repeatedly, but an effectful source behind them is read once for
its key. Both successful and failed provider outcomes remain stable. Concurrent callers await the
same shielded task, so cancelling one waiter does not cancel or duplicate the source operation.
Closing the context cancels unfinished shared tasks and discards every cache.

## Resolution semantics

The result union is exhaustive:

- `Present` contains an exact resolved value, possibly sealed;
- `Unavailable` means expected absence and is the only result that advances `$from`;
- `Missing` is intentional omission created by `$optional` from `Unavailable` only;
- `Error` is a typed resolution, conversion, provider, cycle, or cancellation failure;
- `Blocked` is an authority denial and never degrades to fallback.

Every variant carries value-free `ResolutionProvenance`: logical symbol, optional namespace,
provider identity, concrete consumer scope, source production scope, configuration-origin chain,
and coarse attempt outcomes. Provider exception text and authored or resolved payloads are never
copied into errors. An invalid provider result becomes `PROVIDER_FAILED` at the boundary.

Fallback stops on present, missing, error, or blocked. Lookup cycles are detected using namespace,
symbol, and concrete consumer scope. Arrays require every retained child to be JSON data. JSON
object members may omit `Missing`; arrays cannot. Whole-value interpolation retains the native
value, while embedded interpolation produces text and carries the union of child taint origins.

## Explicit conversions

Comparo never converts because of the receiving field. Only an authored `as`/`:type` or compiled
`ConvertExpr` requests conversion.

| Target | Accepted behavior |
| --- | --- |
| `string` | canonical scalar/unit text or compact JSON; bytes must be UTF-8 |
| `bytes` | text encoded as UTF-8, or unchanged bytes |
| `integer` | canonical base-ten text with no implicit trim |
| `number` | finite canonical decimal text; no float intermediary |
| `boolean` | exactly `true` or `false` |
| `json` | strict JSON with duplicate object names rejected |
| `yaml` | text/bytes delegated to an injected safe decoder |
| `duration` | explicit supported unit resolving to whole nanoseconds |
| `size` | non-negative integer plus explicit decimal or binary unit |

File trimming is an explicit `FileTrim` operation applied before a requested conversion. YAML is
deliberately not parsed by core. A decoder receives an immutable `YamlDecodeRequest`, must return
only exact JSON values or typed failures, and its result is inert data: it is never rescanned for
Comparo directives. If no decoder is installed, YAML conversion fails explicitly.

## Generation

The internal generation contract supports `uuid` and fixed-length hexadecimal data at any exact
runtime scope. `DeterministicGenerator` derives output with HMAC from a sealed per-run seed, the
operation, logical symbol, length, and full scope coordinate. It never consumes a global random
stream, so scheduler order cannot affect results.

A cell-scoped specification projects both baseline and candidate consumers to the same
`CellScope`; the single-flight key therefore returns the same generated input to both. Different
trials, Environments, executions, steps, or attempts diverge only when that narrower scope is
declared. Secure generation is a separate injected provider, must be marked sensitive, and fails
closed when no secure provider is available. The deterministic seed reference is dropped when
the context closes.

The public `comparo/v1alpha1` language does not expose `$generate` yet. The internal seam proves
the lifetime and provider design without prematurely freezing external syntax or a plugin model.

## Sensitivity and sanitization

Secret namespace results and every `$env` snapshot result fail closed to `SensitiveValue`, even if
a provider incorrectly returns a public value. Taint propagates through conversion, interpolation,
arrays, objects, external providers, sensitive file paths, YAML decoding, and generated values.
Stable taint identifiers derive from logical symbols, never secret content. At a named secret
boundary, an authored-secret marker is replaced by that canonical secret symbol while genuine
provider origins are retained; outside that boundary, unknown authored-secret origins remain
unknown and restricted egress rejects them.

Tainting occurs inside the shared provider task. Concurrent callers therefore receive the same
sealed object identity, and the registry retains it once. The registry keeps all distinct sealed
values—including rotated attempt-scoped values—until context close.

`resolve_for_wire` resolves the value, registers all resulting taint, then returns a
`ResolvedForWire` containing both the present value and a same-run `SanitizationSnapshot` that
already includes it. The runtime must carry this pair forward; it must not resolve a second time
for sanitization. Step 9 will consume the snapshot at the explicit live-result-to-artifact
boundary.

This containment prevents accidental serialization and logging. It is not a sandbox against
hostile Python code executing in the same process.

## Cancellation and lifetime

Cancellation is explicit, run-local, and first-wins. The stored reason is a typed
`CancellationSource`, never exception prose. New resolutions fail closed once cancellation is
observed. Context close is idempotent: it rejects later use, cancels unfinished source tasks,
awaits cleanup, clears source caches and the sensitivity registry, and releases the deterministic
seed.

## Ports and deferred capabilities

The implemented core ports are:

- `FileValueProvider` for confined, authorized reads;
- `ExternalValueProvider` for registered secret managers or later value sources;
- `GeneratedValueProvider` for deterministic or secure generation;
- `YamlValueDecoder` for inert explicit YAML conversion;
- `RunClock` for exact injected time.

Every file or external request carries the effective ExecutionPolicy revision and attached
SafetyProfile revisions. Adapters remain responsible for symlink-safe confinement, live
authorization, byte limits, secure randomness, and provider-specific I/O. Provider identities
must be namespaced, versioned, and unique within a context.

The external `comparo/v1alpha1` language currently supports literal/composite values, Value
expansion, `$var`, `$secret`, `$param`, `$env`, `$matrix`, `$file`, `$from`, `$optional`, explicit
conversion, and interpolation. `$generate`, `$step`, `$select`, `$transform`, `$patch`, and
`$provider` remain unsupported externally. Scenario captures and community capabilities can later
use the scoped provider boundary, but no public plugin contract or dynamic dispatch is frozen by
Step 6.

## Verification contract

Regression tests pin the properties most likely to fail under concurrency or future adapters:

- exact namespace and Environment isolation;
- multiple Matrix binding lookup and ambiguity;
- scope projection across every lattice coordinate;
- source rotation across attempts with one read for concurrent same-attempt callers;
- shared cell generation across compared Environments and scheduler-independent generation;
- source failure, fallback, optional, cycle, cancellation, and close semantics;
- strict conversions, file trimming, inert YAML decoding, and taint preservation;
- same-snapshot wire registration and retention of every rotated secret;
- value-free provider exceptions and resolution failures;
- architecture import allowlists and module-cohesion limits.

The package maintains 100% statement and branch coverage. Import contracts forbid ambient,
filesystem, network, YAML-parser, CLI/TUI, and adapter-framework dependencies in
`comparo.core.resolution`.
