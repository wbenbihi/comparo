# Immutable core domain foundation

Comparo's first refactor milestone defines the internal language shared by later compiler,
planner, runtime, evaluator, artifact, CLI, and TUI layers. It deliberately does not parse YAML,
read files or environment variables, perform network I/O, schedule asynchronous work, persist
artifacts, or render output.

The domain describes facts and valid state. Later services create and consume those facts through
explicit ports.

```text
versioned configuration (Step 4)
  -> compiled definitions
  -> deterministic planning (Step 5)
  -> scoped resolution (Step 6)
  -> authorized HTTP execution (Steps 7-8)
  -> immutable observations
  -> selector and evaluator engines (Steps 2-3)
  -> live run result
  -> sanitizer and artifact compiler (Step 9)
```

## Design rules

Domain records are `msgspec.Struct` values that are frozen, keyword-only, and final unless a
private structural base has a documented reason to exist. Collections are immutable tuples. Exact
types replace interchangeable primitives where confusing two values would be dangerous: a
`RunId` is not a `CellId`, a byte count is not a duration, and a definition fingerprint is not an
opaque body-store locator.

Direct construction is a validation boundary. Runtime checks deliberately supplement static
typing because configuration compilers, adapters, tests, and future integrations may construct
objects dynamically. Closed unions use typed variants instead of boolean combinations or dynamic
dictionaries. Raw dictionaries are absent; genuine JSON payloads use a recursive immutable value
model that preserves exact arbitrary-precision numbers and source member order.

Generic carriers do not create runtime-erased payload escape hatches. Resolution outcomes accept
only the closed resolved-value model, including exact JSON values, bytes, durations, and byte
counts. Override carriers instead accept recursively validated `ImmutableDomainValue` graphs so a
field may override a self-contained frozen policy object without reopening arbitrary objects.
`SensitiveValue[T]` is the one deliberately open generic carrier and recursively verifies the same
deep-immutability boundary. Module-level semantic lookup tables are tuples or frozensets so
imported process-global validation rules cannot be mutated in place.

Authored sensitive literals have a dedicated `SensitiveLiteralExpr` variant. A compiler must emit
that variant for `$literal` beneath a secret declaration; it must never unwrap the payload into the
ordinary public literal expression. Structural metadata such as source IDs, taint origins, inline
fragments, capability aliases, TLS material references, and reveal-purpose labels use bounded
machine tokens rather than paths or arbitrary prose.

Every resolution outcome carries a separate `ResolutionProvenance`. It binds a stable logical
symbol, optional value namespace, versioned provider identity, exact concrete runtime scope, and
ordered source attempts. The overall scope is the consumer coordinate; each attempt carries its
own exact production or cache scope and configuration-origin `ValueProvenance`. A source must be
valid in its namespace at that production scope and the coordinate must supply the consumer under
the runtime scope lattice. Only unavailable attempts may precede the terminal attempt. Outcome
constructors reject a terminal category that contradicts the result variant, and a logical or
terminal `secrets` namespace requires a sealed sensitive payload. These records contain identities
and source locations, never resolved or secret payloads. Versioned provider identities are
internal audit coordinates at this milestone and do not establish a plugin API.

Compiled selectors and evaluation operations also carry explicit value-kind and cardinality
contracts. A compiled assertion or diff pipeline must be statically compatible from selection
through transformation, reduction, pairing, and comparison. Operations over a current derived
item use a distinct item-selector contract rather than reinterpreting an observation-rooted
selector at runtime.

Ready selection results enforce the compiled cardinality and the runtime carrier for every
declared `ValueKind`; absence cannot masquerade as an empty successful singular selection. Body
and view handle values must match their concrete location IDs, and location duplication follows
the selector's explicit capability. HTTP media types and field-item collections have dedicated
immutable carriers rather than falling through a generic object channel.

Composition is the default. Inheritance is limited to the private implementation bases used to
create type-distinct slug and UUID identifiers. Domain objects do not own service locators,
registries, global state, clocks, randomness, or hidden defaults.

Automated architecture tests enforce these rules. They also reject adapter/framework imports,
I/O, nondeterministic calls, mutation escape hatches, generic `Any` fields, serialization helpers,
mutable nested defaults, and oversized domain modules.

## Module ownership

| Area | Owns | Does not own |
| --- | --- | --- |
| Identity and metadata | Resource identity, runtime occurrence IDs, names, descriptions, tags | Reference parsing or source discovery |
| Values and expressions | Exact JSON/byte values, resolution outcomes, sensitivity, logical value references, value-source expressions | Provider lookup, conversion execution, environment or file access |
| Provenance and overrides | Configuration-origin chains, scoped resolution-attempt chains, and typed replace/append/remove/clear operations | Precedence calculation or resolved payload storage |
| Scopes and lifecycle | Project-to-attempt scope coordinates, cancellation and completion outcomes | Caches, clocks, task scheduling |
| Capabilities and budgets | Effect requests, exact fingerprinted operator authority, dimension contracts, limits and exhaustion evidence | Policy file loading, authorization algorithms, resource accounting |
| Planning records | Fingerprinted Environment pairs, Matrix binding aliases, recipe-bound cells, exact cardinalities and admitted run plans | Expansion, compilation, lazy scheduling |
| HTTP request language | Structured URLs, ordered fields, credentials, bodies, policies, finalized wire intent and authorization evidence | URL parsing, DNS, serialization, sockets, redirects, retries |
| HTTP observation language | Attempts, exchanges, responses, body/view handles, timings, streams and failures | Body stores, decoders, HTTP clients or stream parsers |
| Selectors | Compiled selector identity, capabilities, concrete locations and exhaustive result variants | Selector parsing and evaluation |
| Assertions | Versioned operator configurations, compiled rule identity and structured fact outcomes | Predicate execution |
| Diffs | Typed transform/reduce/pair/compare pipeline descriptions, coverage and fact outcomes | Pipeline execution |
| Gates | Compiled gate policy, normalized value-free inputs and verdict records | Gate evaluation |
| External analysis | Explicitly authorized, budgeted future analysis requests and typed outcomes | Provider SDKs, network access or hidden comparison behavior |

Consumers import the focused leaf modules they depend on. Package facades stay intentionally small
so an internal representation does not accidentally become a broad compatibility promise.

## Security and sensitive data

`SensitiveValue[T]` contains live secret or tainted data together with non-secret origin IDs. It
does not reveal its payload through `str`, `repr`, formatting, copying, pickling, msgspec encoding,
or conversion to built-ins. Code that genuinely needs the live value must hold an explicit access
capability. Receiving domain fields validate the sealed payload's runtime type without revealing
it. Receivers accept only the exact `SensitiveValue` implementation, never a subclass that could
replace redaction, rendering, or payload-type behavior. Domain string enums use one hardened base
that prevents ordinary member, name, value, deletion, and `__dict__` mutation after construction.

Sensitivity propagates structurally through resolved values, credentials, cookies, recognized
request and response credential fields, body sources, selected values, stream items, and
content-derived digests. Opaque body and decoded-view locators are always sealed. Hashes of
sensitive content remain sensitive because a digest may still identify or confirm private data.

Diagnostics and terminal outcomes carry stable codes and typed, value-free context rather than
arbitrary exception prose. Human-readable messages belong to reporters. This prevents a provider,
decoder, server, or malformed input from smuggling raw values into an otherwise serializable
result.

Exact external capability URLs are public authorization coordinates. They are bounded HTTP(S)
URLs without user-info, query, or fragment components; credentials and signed query values cannot
enter serializable authority records. Logical targets use bounded aliases, while resource targets
use exact `ResourceIdentity` values.

Capability grants represent decisions made under one exact, fingerprinted, operator-owned
`ExecutionPolicy`. A provenance label alone is not authority. Project configuration may request or
further restrict a capability, but cannot create authority. An outbound HTTP execution is
represented only after the finalized request, effective policy, resolved route, plan, and any
required mutation or proxy-DNS approval have been bound together. Redirects and retries must
produce newly authorized outbound intents in the runtime; an adapter must not accept an earlier
logical request as permission to send.

External analysis is authorized separately from pure diff evaluation. Every request has exact
input-byte, output-byte, deadline, and work budgets. Sending any sensitive input additionally
requires a destination-bound secret-egress grant issued by the same exact ExecutionPolicy as the
analysis grant. Provider denial, secret-egress denial, timeout, malformed output, provider failure,
and budget exhaustion remain distinct outcomes.

Analysis provider, model, schema, and version metadata use bounded structural value objects, and
input media metadata uses the canonical typed media model. A terminal analysis result is wrapped
in an `AnalysisExecutionFact` bound to the exact authorized request, so a successful or failed
provider result cannot be replayed for another recipe.

Every terminal analysis fact also carries the complete input, output, deadline, and work budget
snapshots for that execution. Authorization begins with unused budgets; terminal usage must retain
the exact authorized contracts, remain monotonic, and support timeout or exhaustion evidence.

These controls protect the configuration and artifact boundaries. They are not a sandbox against
hostile Python code executing inside the Comparo process; a future in-process plugin would be
trusted code, as recorded in ADR 0015. Deliberate low-level mutation through `object.__setattr__`
or direct private-slot access is consequently outside this boundary.

## Bodies, views, and memory

Complete decoded entity bodies are the semantic default, but body bytes do not live inside the
domain graph. An immutable handle carries identity, exact extent, completeness, digest, media
metadata, sensitivity, and a sealed store token. A future `BodyStore` owns memory-to-disk spooling,
bounded reads, cleanup, and concurrent access.

Request bytes use the same containment discipline. At materialization, the exact
`EffectiveRequest` is projected into a value-free fingerprinted identity plus body-kind,
serialization, media, boundary, digest, and taint metadata. `RequestMaterializationProof` binds
that descriptor to the complete finalized byte-source without retaining the original large or
secret payload. Its output taint origins must cover every sensitive input origin; multipart
materialization additionally proves the exact planned boundary and canonical derived Content-Type.
An adapter cannot replace a sealed body with a public digest before HTTP authorization.

Decoded views follow the same pattern. A handle binds the exact source body handle, decoder
identity/version, options, completeness, sensitivity, and sealed store token. A view cannot lower
the source sensitivity or claim more completeness than the captured body. A future
`DecodedViewStore` owns lazy decoding, single-flight caching, eviction, indexes, and streaming
reads. Domain constructors never open either store. Selector and evaluator services receive the
relevant port explicitly.

This separation permits full-body comparison without retaining every body in memory and prevents
storage paths from entering reports. The artifact pipeline remains a separate sanitization
boundary; live domain values are not themselves persistable artifacts.

## HTTP observation truth

Every observed exchange retains the exact authorized request that produced it. Redirect
transitions derive method/body/header and credential changes from the before/after requests;
retries must preserve the authorized request and match the effective condition and delay schedule.
Every hop receives a new authorization, so a prior grant cannot authorize a redirect, retry,
different route, or different policy revision.

Pre-route resolution and route-pinned send authorization are separate typed effects. A DNS failure
retains an authorized resolution request but no fabricated route or send grant. Resolution-policy
denial, DNS failure, and send-policy denial are distinct pre-send attempt outcomes. A retry may
perform a fresh authorized resolution while preserving the exact request, occurrence, plan, and
policy coordinates.

A sealed redirect `Location` cannot be resolved into public path or query data. Its scheme and
authority remain public routing facts, while every non-empty path segment and query value remains
sealed and covers the raw field's taint origins. URL path, query, and complete URL sensitivity are
derived from those components rather than trusted as parallel flags.

Stream attempts form one exact ordered and complete path across both reconnect and ordinary-retry
edges. Each attempt is represented either by a captured connection segment or by its exact
no-response failure or non-stream response. A reconnect repeats the same finalized request under
the same plan and policy authority, but receives fresh authorization for its newly resolved route.
DNS addresses may refresh; route mode and proxy-DNS ownership may not drift. Reconnect-specific
request mutations, including SSE `Last-Event-ID`, remain fail-closed until represented by a
dedicated typed transition contract.

Response media type and content-coding metadata are derived from the exact ordered response fields
rather than accepted as independent adapter claims. Negotiated TLS facts include the applied
verification policy and must satisfy its minimum version. HTTPS proxy TLS and origin TLS carry
role-distinct policies, trust stores, and client identities; an origin mTLS identity can never be
reported or applied as proxy authority. Attempt and execution timing envelopes
contain their children, body/stream timing reasons match actual extents, and occurrence identities
for attempts, exchanges, bodies, views, and stream owners remain unique and connected.

Each physical streaming connection, including a clean reconnect, has a distinct attempt identity.
A reconnect transition is not an HTTP retry and does not consume the ordinary retry count. It binds
the exact preceding terminal exchange and repeats the same finalized request under the same run
plan and ExecutionPolicy revision. DNS addresses may be refreshed, but direct/proxy routing and
proxy target-resolution ownership cannot change. Step 1 deliberately models no reconnect-specific
request mutation: future SSE `Last-Event-ID` injection requires a typed, validated mutation variant
rather than weaker request equality.

A stream observation contains the owner's complete ordered attempt path. Every captured segment
binds the terminal exchange, body, and decoded view of its exact connection attempt; failed
connection attempts and response-bearing attempts that did not establish the requested stream are
retained as typed non-segment occurrences. Outgoing edges determine segment termination:
reconnection, transport failure, or remote EOF before an ordinary retry. A reconnect starts a new
ordinary-retry group, whose request baseline and retry schedule begin at the reconnect attempt's
initial request. Aggregate byte extents, global item intervals, and record/control/error intervals
are contiguous and exact. Each segment owns a unique execution view and response body, its byte
interval equals that body's retained size, and the source handles preserve digest, sensitivity,
and completeness independently across reconnects. Concrete stored items must fit both their
segment interval and their framing-specific category. SSE raw field locations must be non-empty,
ordered, non-overlapping, line-consistent, and contained within their outer event. Impossible
framing counts, omitted attempt outcomes, and duplicate store-window coordinates fail at direct
construction.

Prefix decoded handles remain limited to byte/text views and prefix-safe streaming framings. This
allows complete records retained before a body-read failure to remain addressable without treating
an incomplete ordinary JSON document as a successful decoded view.

Terminal non-segment outcomes preserve their strongest exact classification. A non-success status
after reconnection is reconnect exhaustion; a successful response that cannot establish the
requested framing is a decode failure; body-read and protocol failures remain transport failures;
content-decoding, resource-budget, storage, cancellation, and policy outcomes remain distinct.
The final exchange and capture derive the terminal segment end and logical extent. Authored
windows, safety stops, framing failures, and manual stops require typed evidence reconciled with
the exact retained count, byte, and timing facts. Body-capture exhaustion separately proves entity
bytes, encoded bytes, or expansion ratio; no generic stop reason may replace those dimensions.

## Outcome taxonomy

Expected absence, failure, policy denial, and cancellation are not exceptions collapsed into one
error string:

- value resolution distinguishes present, unavailable, intentional omission, invalid resolution,
  and policy blocking;
- authorization distinguishes granted and denied external authority;
- transport distinguishes phase-specific failures from valid HTTP statuses and preserves partial
  response facts;
- body and view outcomes distinguish no body, empty or complete content, deliberate extent,
  unexpected incompleteness, budget exhaustion, unsupported behavior, and cancellation;
- selection distinguishes a ready ordered collection from unavailable sources, decode failure,
  incomplete input, budget exhaustion, unsupported capability, and cancellation;
- assertions distinguish satisfied, violated, skipped, unavailable, and evaluator error;
- diffs distinguish concrete same/changed/added/removed/ignored/incomparable facts and aggregate
  equivalent/drifted/incomplete/error/not-evaluated outcomes;
- run completion, gate verdict, and persistence policy remain independent dimensions.

In particular, ignored or unselected data is never reported as equal, an empty or entirely skipped
evaluation is never implicitly green, and a failed assertion does not suppress an applicable
cross-environment diff.

Gate reduction consumes a deliberately narrower model than assertion and diff evaluation. The
normalization boundary projects only exact fact, rule, and application lineage; Environment
execution or directed-pair coordinates; severity; closed outcomes; and conserved counts or
coverage. It never retains compiled rules, selected values, match or diff evidence, or evaluator
facts. These immutable projections are safe to serialize for CI and reporting even when their
source facts contain live sensitive values. Empty assertion coverage remains explicit, and an
empty or all-ignored diff remains `not-evaluated` rather than becoming equivalent.

Assertion gate roles come from exact Environment-execution assignments rather than a caller-owned
enum. One execution may hold both baseline and candidate roles across arbitrary directed pairs;
`OTHER` is exclusive. Comparison IDs and Environment-execution IDs map to one exact coordinate
throughout the summary. Run completeness is derived from the actual lifecycle outcome rather than
a second caller-owned flag. Diff coverage separately accounts for values consumed and resolved on
each side, so an equivalent outcome cannot silently omit unmatched derived values.

## Planning and budget admission

Every budget dimension has an exact amount type, permitted scope and stage placements, accounting
mode, and stage-specific exhaustion effects. Derivation evidence must correlate versioned defaults,
ordinary requests, and operator or safety ceilings with their actual provenance. Concurrent usage
cannot exceed its high-water mark, and no usage snapshot may contradict the effective limit.
Every addressable runtime, body, stream, comparison, artifact, or external-capability budget also
carries its exact typed coordinate; only pre-runtime plan scope is intentionally unaddressed.
Capture and decoded-view budget outcomes embed the canonical `BudgetExceeded` record, including
derivation, usage, attempted amount, scope, stage, and effect. Local capture/view labels are derived
from its resource and cannot become a second, weaker source of truth.

An executable `RunPlan` contains unique, unused budget snapshots for every required planning
dimension and proves its exact request, Matrix-cell, trial, Environment-execution, attempt, and
comparison cardinalities fit those limits before any external effect. Matrix cases retain both a
binding alias and their fingerprinted Matrix definition, so attaching the same Matrix twice is
unambiguous and a planned cell cannot select an undeclared case. Scenario recipes remain
representable for future compilation, but Step 1 rejects them from executable plans until Scenario
step cardinality and attempt semantics are defined.

Across every plan slice, one stable `ResourceIdentity` maps to exactly one content fingerprint.
Subjects, Matrix definitions, selected Environments, and comparison endpoints cannot refer to two
different effective definitions under the same identity within one run.

## Extension rules

New domain capabilities should be introduced as closed typed variants with stable, versioned
identities. Static structure is validated before runtime. An operation that needs I/O belongs
behind a port and produces ordinary typed observations or outcomes for the pure core to consume.
Plugin discovery, loading, trust, and isolation are intentionally deferred; no generic extension
dictionary or arbitrary callback exists in the foundation.

Before adding a field or module, check that:

1. it expresses a domain invariant rather than adapter state;
2. its data is immutable, exact, and safe at direct construction boundaries;
3. sensitive inputs remain sealed through every derivative;
4. unavailable, incomplete, denied, cancelled, and failed states stay distinct;
5. the type can be tested without filesystem, network, clock, randomness, or framework access;
6. it does not implement work assigned to a later roadmap step.

## Verification

The required local gates are:

```console
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen lint-imports
uv run --frozen python -m pytest --cov --cov-report=term-missing
uv build
```

The test suite combines direct-construction examples, architecture contracts, property tests, and
security regressions. Branch coverage is enforced at 100% for this small foundational language;
lowering that threshold requires an explicit engineering decision rather than a local exception.
