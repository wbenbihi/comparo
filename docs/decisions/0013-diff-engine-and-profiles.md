# ADR 0013: Diff engine, derivation, pairing, and profiles

- Status: Accepted
- Date: 2026-07-24
- Owners: Comparo maintainers

## Context

Comparo compares observations from isolated Environments. Environment-owned database identifiers,
timestamps, ordering, and other incidental values commonly differ even when behavior is compatible.
Streaming and generative services add another problem: transport records, SSE events, and text may
differ in count, boundaries, order, or wording while still satisfying the same useful contract.

A diff engine limited to recursive equality plus ignored paths would either report meaningless
drift or require service-specific logic inside the engine. Comparo instead needs a small, typed,
composable evaluation kernel that can derive comparable semantic values without turning comparison
into an unrestricted expression or I/O system.

## Accepted decisions

### Pure directed binary evaluator

The primitive remains a pure directed comparison of one baseline Observation and one candidate
Observation under a compiled DiffProfile and applicable ResolutionFrames. Multi-Environment
topology, HTTP execution, storage, sanitization, reporting, gating, and fail-fast behavior stay
outside the engine.

Each rule has a stable ID, name, optional description and tags, severity, applicability,
completeness requirements, typed pipeline, and versioned operation identities. Static structure is
compiled before execution; only declared compatible operands may resolve in baseline, candidate,
cell, or shared contexts. Facts retain definition, profile-composition, Request/cell/trial,
Environment-pair, selector, transform, reducer, pairing, comparator, and concrete-location
provenance.

Baseline-to-candidate direction is observable. Added and removed are not interchangeable, and a
profile cannot accidentally reverse its operands.

### Rule pipeline

Every comparison rule compiles to the following conceptual pipeline:

```text
select each side
  -> derive each side with typed transforms and reducers
  -> pair the derived values
  -> compare each pair
  -> aggregate structured DiffFacts
```

Selection and derivation happen independently on each side. Pairing happens only after semantic
reconstruction, so wire chunks or records do not have to become comparison identities. Every stage
preserves source locations and sensitivity and has explicit Ready, empty, unavailable, incomplete,
budget-exceeded, invalid-runtime-input, and evaluator-error behavior. There is no implicit value
conversion, text normalization, ordering change, fallback decoder, or missing/null equivalence.

Rules may use a shared selector/pipeline for both sides or explicitly define asymmetric pipelines.
Asymmetry is visible in provenance and is useful for intentional API migrations; it is never
inferred from differing data.

### Projection, exclusion, and ignored data

Profiles support both complementary intentions:

- projection selects only the contractually relevant values; everything outside the projection is
  not evaluated;
- exclusion derives a value by explicitly removing selected locations before comparison, so all
  other current and future fields remain in scope;
- an explicit ignore rule may emit `ignored` facts for locations the report should acknowledge;
- normalization derives a comparable representation while retaining the original locations and
  operation provenance.

For example, two database-backed responses may remove Environment-specific `id` and `createdAt`
fields and compare the remaining object, or project only `name` and `status`. Excluded and
unselected data is never reported as equal. Diff exclusion is unrelated to redaction: ignored raw
values still follow capture, sensitivity, and sanitization policy.

A custom DiffProfile is an allowlist of its effective rules. It does not secretly retain a built-in
raw-body comparison. Built-in profiles are applied only through explicit assignment or inclusion.
An entirely ignored or empty effective evaluation is `notEvaluated`, not equivalent.

### Typed transforms and reducers

The initial transform vocabulary is intentionally finite and versioned. It includes relative
selection, object projection and field removal, collection mapping/filtering with constrained pure
predicates, explicit text normalization, string or byte length, value type, JSON shape, digest,
sorting by a typed stable key, and a future namespaced transform capability.

Reducers initially include first, last, count, concatenate, sum, minimum, maximum, collect, set,
multiset, distinct, group-by, and object-from-entries. Empty-input behavior, duplicate handling,
ordering, separator behavior, accepted types, and completeness requirements are part of each
versioned operation contract rather than global conventions.

Transforms and reducers are deterministic, pure, cancellable, resource-bounded, unable to perform
I/O, and incapable of emitting presentation text containing unsanitized values. Their declared
schemas distinguish static structure from resolvable operands. A future external implementation
would be namespaced, version-pinned, schema-validated, policy-controlled trusted code and preserve
the same fact and budget contracts; ADR 0015 does not yet expose that plugin model.

### Pairing

Pairing consumes derived typed collections. Initial strategies cover statically singular values,
ordered/index pairing, key-based pairing with singular scalar keys, and set/multiset comparison.
Missing values, extra values, duplicate keys, invalid key types, and incomplete collections remain
explicit. Keyed pairing never falls back to position, and ordered pairing never silently sorts.

For raw SSE and JSON record views, ordered record/event pairing remains the default. A profile may
instead map records, reconstruct a semantic value, reduce records into a set or multiset, or pair
by a stable derived key. Thus stream record count, boundaries, and arrival order matter only when
the selected profile says they matter.

### Comparators and default representation behavior

Initial built-in comparators cover exact typed structural equality, explicit ignore, type and JSON
shape compatibility, absolute and relative numeric tolerance, text predicates, subset/superset,
ordered collections, sets, multisets, and keyed object collections. Equality preserves the
assertion engine's strict value model: object member order is insignificant, array order is
significant, numbers are exact, and strings are not implicitly normalized.

The ordinary body rule uses ADR 0004's versioned `auto` view. Different selected representations
are explicit representation drift. Decode failure, NoBody versus EmptyBody, incomplete capture,
and unavailable input remain distinct from content inequality. A profile may force views or
derive values explicitly but no comparator performs hidden decoding or coercion.

A built-in semantic HTTP profile may compare status, parsed media metadata, and the selected body
view. Timing, transport diagnostics, headers, and raw stream controls are compared only when the
effective profile contains applicable rules. All operation identities and versions are retained
in artifacts.

Concrete facts are `same`, `changed`, `added`, `removed`, `ignored`, or `incomparable`; comparison
aggregation remains `equivalent`, `drifted`, `incomplete`, `error`, or `notEvaluated` as defined in
ADR 0009. A failed per-Environment assertion does not suppress cross-Environment diff evaluation.

### Nondeterministic and generative services

Chunk concatenation, event projection, shape extraction, and final-state reduction cover
deterministic semantic properties of nondeterministic streams. A future pinned pure capability may,
for example, reconstruct a canonical chat completion or tool-call collection from provider-
specific deltas before core pairing and comparison.

Semantic equivalence based on embeddings, model judges, or other external services is not hidden
inside a pure comparator. A future evaluator that performs network I/O is a separate explicitly
authorized analysis execution with declared data egress, credentials, cost, timeout, caching,
model/version, nondeterminism, and artifact provenance. Its output may become an observation for
pure assertions or diffs.

One stochastic sample cannot establish distributional compatibility. ExecutionProfile may request
repeated trials of the same logical Matrix cell. A trial changes neither Matrix inputs nor the
logical comparison topology; it repeats the same input and creates new Environment executions.
Trial-scoped observations first produce ordinary assertion and pairwise diff facts. A later
trial-aggregation stage may derive rates, distributions, quantiles, confidence-aware metrics, and
statistical comparison facts under versioned, budgeted rules.

`trial` is therefore an explicit runtime coordinate between a cell and each Environment execution.
Values shared across trials or regenerated per trial use declared scopes; retries remain attempts
within one trial and never masquerade as additional samples. Trial counts, aggregation coverage,
failed/missing samples, seeds where non-sensitive, and stopping policy are artifact provenance.

### Profile composition and coverage

DiffProfile composition follows the same deterministic include, alias, ordered edit, attachment,
identity, conflict, and provenance principles as AssertionProfile. Diff-specific typed overrides
may change presentation, severity, compatible operands, tolerances, and completeness, but cannot
silently change selection, transformation, pairing, comparator identity, or rule purpose. Such
changes require full replacement.

GateProfile decides whether drift, incomparable data, incomplete trials, errors, or absent coverage
are acceptable. Evaluation rules never decide the release verdict. Coverage records what was
selected, derived, compared, ignored, or not evaluated so a narrow profile cannot accidentally be
presented as whole-response equivalence.

Coverage separately conserves each side's derived, consumed, and resolved counts. Every concrete
fact declares whether it consumes the baseline side, candidate side, or both; added, removed,
ignored, and incomparable facts cannot borrow the wrong footprint. A complete `equivalent`,
`drifted`, or all-ignored result must account for every derived value on both sides. Gate
normalization preserves those exact counts rather than reconstructing them from aggregate labels.

Assertion gate roles are derived from exact Environment-execution assignments and the invocation's
directed comparison pairs. One execution may be baseline in one pair and candidate in another;
`OTHER` is exclusive and cannot relabel an execution that appears in a pair.

## Consequences

- Environment-owned identifiers and timestamps can be excluded without suppressing the rest of a
  response comparison.
- New fields are automatically compared after exclusion but intentionally outside an allowlist
  projection; profile authors choose that compatibility posture.
- LLM and streaming services can compare semantic products rather than transport segmentation.
- Deterministic transforms remain testable independently of pairing and comparison.
- External semantic judges and statistical tests can be added without giving the core evaluator
  network access.
- Reports can distinguish equality from data that was ignored or never evaluated.

## Implementation-level follow-ups

1. Exact YAML shorthand and structured forms for pipelines, asymmetric sides, and typed operation
   arguments.
2. The minimal first-release transform/reducer/comparator subset and per-operation schemas.
3. Trial aggregation and statistical comparator catalog, which may ship after ordinary single-run
   comparison while retaining the domain coordinate now.
4. Evidence sampling and storage strategy for very large derived collections.
5. Contracts for explicitly authorized external analysis executions.
