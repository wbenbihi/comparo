# Data and execution pipelines

## 1. Source discovery and configuration compilation

```text
entry file/directory
  -> deterministic source catalog
  -> source-located JSON-compatible YAML documents
  -> strict comparo/v1alpha1 resources
  -> version frontend and semantic validation
  -> typed reference linking and Value expansion
  -> directive-free CompiledProject
```

`comparo.configuration.loader` accepts a `comparo.yaml` file or a directory containing it. The
entry Project may discover the declared data folder (`.comparo` by default), explicit project
sources, and caller-supplied additional sources. Files are ordered deterministically, symlinks are
not followed, and source/parser limits are supplied by the caller.

The YAML adapter emits source-located neutral nodes. It rejects aliases, anchors, custom tags,
directives, merge keys, duplicate keys, and non-string mapping keys. The pure
`comparo.core.configuration` frontend then validates the closed resource envelope, links
references, compiles value expressions, validates cross-field semantics, and emits either one
`CompiledProject` or diagnostics—never both.

### Resource kinds

Project files can declare these kinds:

| Kind | Compiles today | Production execution today |
| --- | --- | --- |
| `Project` | Yes | Supplies discovery, value declarations, and defaults |
| `Environment` | Yes | Yes: values/env plus HTTP fields, timeout, TLS, proxy, and SafetyProfile |
| `Request` | Yes | Yes: wire fields, bodies/auth/stream/policies, schemas, rules, and Matrices |
| `Scenario` | Yes | No; rejected by runtime capability preflight |
| `Matrix` | Yes | Yes |
| `ExecutionProfile` | Yes | Yes, within the supported execution subset |
| `Schema` | Yes | Yes: local and policy-authorized remote JSON Schema |
| `AssertionProfile` | Yes | Yes: typed deterministic operator catalog |
| `DiffProfile` | Yes | Yes: selectors, transforms, reducers, pairing, and comparators |
| `GateProfile` | Yes | Yes: typed thresholds and coverage requirements |
| `SafetyProfile` | Yes | Yes: restrictive project/environment safety and response classification |
| `RedactionProfile` | Yes | Yes: compiled rules plus mandatory credential redaction |
| `ArtifactProfile` | Yes | Yes: restrictive capture/persistence configuration |
| `Value` | Yes | Yes, including invocation overlays from reusable Value objects |

`ExecutionPolicy` is not a project resource. It is loaded from a separate operator-controlled
document and compiled by `comparo.policy` / `comparo.core.policy`.

### Value directives

The external language currently compiles these directives in recursively resolvable positions:

| Directive | Runtime source |
| --- | --- |
| `$use` | Reusable `Value` expansion in value position; typed resource link in reference position |
| `$var` | Effective variables namespace |
| `$secret` | Sealed secrets namespace |
| `$param` | Effective parameters namespace |
| `$env` | Explicit Environment env snapshot |
| `$matrix` | Current cell's Matrix binding(s) |
| `$file` | Policy-confined project file provider |
| `$from` | Ordered fallback, advancing only on unavailable input |
| `$optional` | Converts unavailable input to omission |
| `$literal` | Inert payload boundary; nested dollar keys are not directives |

Interpolation supports the `variables`, `secrets`, `parameters`, `env`, `matrix`, and reserved
`steps` namespaces. Conversion is never inferred from the destination field: it requires `as` or
an interpolation `:type` suffix.

The compiler recognizes but rejects reserved future directives such as `$case`, `$generate`,
`$step`, `$select`, `$transform`, `$patch`, and `$provider`. A frontend must not offer them as
currently executable syntax.

### Source revision retention

`LoadedCompilation` retains source-located parsed documents internally and a logical source
catalog with SHA-256 and byte counts. Filesystem paths never enter frontend DTOs. Explorer source
reads reopen the logical source with no-follow semantics, require the catalog fingerprint to
match, and return only a caller-bounded masked or explicitly trusted exact line window.

Masked source inspection uses the same sensitivity classifier as compiler fingerprinting. It
masks complete `Value` payloads, Project secrets, Environment inline env values, auth/cookies,
credential-shaped headers/query fields and URLs, secret interpolations, and YAML comment payloads.
Ordinary structure such as endpoints remains readable. If parsing or compilation cannot provide
trustworthy spans, masking fails closed by replacing every non-whitespace source character.

## 2. Invocation, precedence, and planning

```text
CompiledProject + PlanningOptions + ExecutionPolicy authority
  -> typed RunInvocation
  -> structural/value/safety binding
  -> Matrix recipes and topology
  -> exact cardinality and budget admission
  -> fingerprinted PreparedRun
```

`PlanningOptions` is the frontend-neutral override contract. It supports:

- selecting an `ExecutionProfile`, or selecting ad-hoc subjects;
- assertions-only Environments, or one baseline plus one or more candidates;
- replacing Matrix attachments by binding name and selecting case IDs;
- trials, maximum attempts, fail-fast, and planning-budget overrides;
- global or Environment-specific `parameters`, `variables`, and sealed `secrets` through reusable
  Values or inline expression objects;
- sealed per-Environment `$env` overrides;
- typed Request field edits; and
- assertion/diff/gate/safety/redaction/artifact profile replacement or detachment.

It deliberately does not introduce a second anonymous ExecutionProfile resource language. The
typed controls above compile into the normal `RunInvocation` and preserve ordinary provenance.

### Separate precedence mechanisms

Comparo intentionally has no universal merge function:

1. Structural execution fields use typed field-specific replacement/edit semantics across
   version defaults, Project defaults, an ExecutionProfile, resource attachments, and invocation.
2. Value namespaces retain ordered layers for run-time lookup. Global layers precede matching
   Environment-specific invocation layers.
3. Safety authority composes restrictively with external ExecutionPolicy ceilings.

The planner records provenance for effective fields. It never resolves a secret, dotenv value,
file, interpolation, or provider.

### Matrix behavior

A Matrix uses explicit cases or named axes. Axes support `product` (default), `zip`, and `union`.
`include` appends explicit cases; `exclude` removes exact case IDs. Multiple Matrix attachments on
one Request are supported and combined by Cartesian product. Each attachment has a binding alias.
Unqualified `$matrix` field lookup fails when the same field exists in more than one binding;
qualified lookup selects the binding.

The plan stores compact recipes and exact counts. Cells/trials/executions are generated lazily in
stable order. The baseline executes once per cell/trial and is reused across directed candidate
comparisons.

### Safe plan inspection

`inspect_plan` and `PlanReviewInspection` expose:

- exact cardinalities and plan slices;
- subject, Environment, baseline/candidate, Matrix, trial, and attempt topology;
- assertion, diff, gate, safety, redaction, and artifact definition coordinates;
- effective planning budgets and origins;
- HTTP/value/artifact policy authority;
- mutation approval and execution settings;
- field provenance and safe sensitive-source mechanisms;
- runtime/evaluation admission counts and plan fingerprints.

This projection is unresolved by design and is the correct source for a TUI Plan screen.

## 3. Environment snapshots and value resolution

At run start, `build_environment_snapshots` lowers each selected Environment's `spec.env`:

```yaml
env:
  inheritProcess: false
  files:
    - path: .env.staging
      onConflict: error
  values:
    API_HOST: example.test
```

Env files and optional ambient values populate only the explicit `$env` namespace. They never
mutate `os.environ`, never configure the HTTP client implicitly, and are not used unless an
expression asks for `$env`/`${env...}`. Every selected Environment receives an isolated immutable
snapshot. CLI and interactive execution capture the policy-authorized process subset once at
admission, within entry, per-value, and aggregate byte limits; the runtime never retains a mutable
environment mapping for later traversal.

`RunContext` resolves expressions at the run, cell, trial, Environment, execution, step, or
attempt scope. Effectful reads are single-flight per exact source/scope key. Results are the
closed union `Present`, `Unavailable`, `Missing`, `Error`, or `Blocked`, with value-free
provenance. Secret and env values are sealed and registered for sanitization.

`LiveValueResolver` and `MaskedValueResolver` are two egress views over the same
`ResolutionEngine`. The live view is used only by trusted execution internals. The masked view
preserves result shape, value kind, provenance, and taint origins while irreversibly replacing
sensitive payloads with a version-owned marker.

The `steps` namespace and step scope are internal forward seams; Scenario capture production is
not implemented.

### Generic masked value inspection

`CompareService.inspect_resolved_value` accepts a frontend-safe target containing only a resource
coordinate, expression field path, execution ordinal, Environment coordinate, continuation
fingerprint, and projection limits. The service binds it internally to the exact retained plan,
reviewed controls, and frozen ambient-environment snapshot. A prepared plan, resolver, local path,
or live value never crosses that handle boundary.

The selection uses the execution's exact Matrix cell, Environment, trial, and attempt-zero scope.
For Request resources it applies the same typed `RequestOverride` expression replacement used by
production request preparation. It creates one inspection-owned `RunContext` and resolves through
`MaskedValueResolver`, which shares the production resolution engine. A sensitive result,
including a sensitive composite, collapses to `<masked>`, its value kind, bounded opaque source
categories, and an omitted-origin count; no source-derived origin identifier, sensitive child
value, or sensitive digest is returned. This collapse is deliberately fail-closed. A frontend that
needs to show public siblings inside a sensitive composite inspects the known effective child
field paths independently and never attempts to expand the collapsed aggregate.

Public results are emitted as pre-order scalar/container nodes with caller-owned page, scalar,
path-depth, path-segment, complete-traversal node/byte, and taint-lineage limits. Continuation pages
require the previous `projection_fingerprint`, so a changed public projection fails closed instead
of combining inconsistent pages. For masked results that fingerprint covers only disclosed masked
metadata; it intentionally makes no claim about undisclosed secret-content identity. A single
cancellable traversal computes the public fingerprint and page, with iterator-frame auxiliary
memory proportional to nesting depth rather than sibling count. Traversal ceilings bound the work
needed even for a deep page; source/resolution budgets separately bound value construction.

The returned `freshness` is explicit: the plan checkpoint and captured ambient-process input are
exact. Dotenv files, project files, and generated values are sampled for inspection; production
execution reads or produces them again. Frontends must not present this advisory value as a
guarantee that a mutable or generated input will remain unchanged before execution.

## 4. Offline request preview

`render_prepared_requests` and `render_prepared_request_window` resolve planned requests using
pinned project files, isolated env snapshots, deterministic preview generation, and the same
request provider used by production. They perform no DNS lookup or network I/O. Results are
sanitized before crossing the application boundary; the windowed service skips directly to a
caller-selected canonical ordinal range.

Each preview includes occurrence IDs, subject/environment coordinates, method, sanitized URL,
ordered headers, redacted cookies, and body omission metadata. Query fields are represented inside
the URL. Exact per-field runtime provenance is not yet returned. Body-bearing request cURL export
therefore uses the authenticated post-run sanitized capture instead of a live preview.

## 5. Production HTTP execution

```text
PreparedProjectPlan
  -> runtime capability preflight
  -> Environment snapshots + RunContext
  -> evaluation binding
  -> ConfigurationRequestProvider
  -> policy-aware exchange preparation
  -> httpcore transport + transient body store
  -> ordinary/SSE snapshot decoding
  -> assertions/diffs/gate
  -> RunResult
```

Before acquiring network effects, `preflight_runtime_capabilities` rejects unsupported compiled
features. The admitted Request path includes method, endpoint/URL, duplicate-preserving fields,
cookies, auth, typed bodies, stream mode, timeout, redirects, and retries. Environments can add
headers/cookies/auth/timeout/TLS/proxy settings. Response schemas and Safety/Redaction/Artifact
profiles are bound before network effects. `Scenario` remains the principal selected-resource
rejection.

Remote response Schemas are separate preparation effects. Every root URI must pass the exact
intersection of `SafetyProfile.schemas` and operator `ExecutionPolicy` source authority, and its
GET route must pass scheme, method, host, port, DNS-address-class, and route-pinning checks. One
load batch is bounded by document count, per-document bytes, aggregate retained bytes, and a batch
deadline. Documents are fetched fresh without redirects. Local JSON Schema fragment references
work inside a fetched document; every external `$ref`, `$dynamicRef`, or `$recursiveRef` is denied.
There is currently no transitive schema loader, cache, or integrity-pin contract.

Shipped production defaults currently include:

- 20 maximum connections and 10 keep-alive connections;
- 64 MiB encoded/entity response capture ceilings;
- a 1 MiB in-memory body threshold and 512 MiB run transient-store ceiling;
- 8 MiB ordinary response decode limit;
- 100,000 stream records, 10,000 controls/errors, and 1 MiB per stream item;
- finite selection/evaluation/evidence limits;
- 64 MiB per artifact blob and 512 MiB total artifact blobs.

These are replaceable typed settings, not hard-coded schema limits.

### Timing model

Timing is hierarchical and spans are not assumed to be additive. An Environment execution records
queue time and end-to-end elapsed time. Each retry/redirect-containing attempt records elapsed
time. Each concrete exchange records connection acquisition, DNS, connect, proxy tunnel, TLS,
request write, response headers, server wait, first body byte, body read, and exchange elapsed.
Every field is explicitly measured, not performed (for example a reused connection or no body),
or unavailable with a reason. Artifacts retain those names and exact nanoseconds/reasons. There is
no single ambiguous `response.timings.total` field.

### SSE and JSON data

When selected rules require a stream view, the composition installs
`StoredStreamSnapshotProvider` with `SseDataJsonPolicy`. The HTTP/selection substrate can preserve
bounded stream records and decode SSE `data` as JSON according to the stream policy. Stream
selectors and `streamProject` participate in the same semantic diff pipeline as ordinary values;
chunk count and arrival ordering need not become comparison criteria.

## 6. Evaluation and gates

The application binder exposes the deterministic typed kernel rather than maintaining a second
special-case evaluator:

| Family | Current production binding |
| --- | --- |
| Assertion selectors | Request/response fields, status, timings, ordinary bodies, JSON paths, and stream sources |
| Assertion operators | Equality, membership, ordering, tolerance/range, text/collection/key, length/cardinality, type, regex, digest, media, decode, and schema |
| Assertion controls | `severity`, `quantifier`, `completeness` |
| Diff selectors | Same selector compiler, with independent baseline/candidate side selectors |
| Diff transforms | Projection/exclusion, normalization, shape/type/length/digest, relative select, map/filter/sort, and stream projection |
| Diff reducers | Scalar, collection, set/multiset, grouping, and object construction reducers |
| Diff pairing | `singular`, `ordered`, `keyed`, `set`, or `multiset` |
| Diff comparators | exact/ignore, type/shape/subset/superset, numeric tolerance, ordered/set/multiset, and text predicate |
| Diff controls | `severity`, `completeness` |
| Profile composition | Reusable/inline rules, includes, metadata, and typed controls |
| Gate | Configured severity thresholds and coverage requirements |

Evaluation produces stable occurrence-level facts, structured outcomes, counts, coverage,
bounded evidence, and value-free issues. The gate consumes normalized summaries rather than live
values. It never recomputes assertions or diffs.

## 7. Live progress and cancellation

`ExecutionSession` starts one production task, owns cancellation, and yields coalesced
`ExecutionProgressSnapshot` values. Snapshots contain exact aggregate counts, caller-bounded
occurrence rows, terminal outcomes/failure codes, observer timing, a bounded recent-event window,
explicit omitted-row counts, and the final artifact summary. A slow UI retains only the latest
snapshot and cannot grow an unbounded queue. Raw HTTP/body content remains unavailable until the
sanitized artifact exists.

## 8. Artifact capture and persistence

`ArtifactCapturingDriver` wraps the HTTP driver, obtains the final same-run sanitization snapshot,
captures request/response metadata and bodies according to policy, records explicit completeness
or omission, and writes sanitized blobs to a content-addressed spool. `compile_run_artifact`
combines those captures with the final `RunResult`.

The artifact manifest contains:

- artifact, run, invocation, project, policy, and configuration provenance;
- completion and gate facts;
- summary tallies and budget ledgers;
- executions with Matrix coordinates, outcomes, failures, timings, attempts, exchanges, and
  assertions;
- comparisons with exact baseline/candidate links and diff facts;
- sanitized wire request/response metadata and optional body captures;
- blob descriptors and capture-coverage issues.

Every body capture says whether it is complete, partial, or omitted and why. Bodies are detached
sanitized blobs, not embedded manifest strings.

### Important default distinction

`production_execution_defaults_v1` does not persist artifacts;
`persisted_production_execution_defaults_v1` enables persistence. Both classify response bodies as
public by default so complete bodies can be sanitized into detached blobs without accumulating in
memory. `SafetyProfile.classification` can make a shared or Environment-specific body sensitive,
and sensitive bodies are then omitted by the fail-closed capture boundary.

Attached `RedactionProfile` and `ArtifactProfile` resources are composed as restrictions over the
caller-owned settings before any value or network effect. The capture driver receives the compiled
redaction rules and Environment-specific response classification provider.

## 9. Result, archive, and report projections

`project_results` converts one sanitized `RunArtifact` into a body-free, renderer-independent
model with summary, execution rows, comparison rows, assertion/diff facts, evidence, stable
references, and cross-links. Execution rows expose canonical attempt/exchange topology and closed
HTTP message slots: retained sanitized metadata, a typed omission reason, or a response that was
not produced because transport failed. Retained exchange messages also expose immutable body
coordinates and capture-presence flags. Terminal pre-send request metadata remains inspectable
without fabricating an exchange. The projection contains no body bytes, blob descriptors, storage
locators, or live runtime objects; it never opens blobs or changes verdicts.

`result_analysis` derives bounded views:

- Issues groups non-success fact outcomes;
- Requests aggregates execution outcomes by subject;
- Rules aggregates rule applications and outcomes;
- Fields aggregates normalized evidence locations and samples;
- Timeline emits deterministic causal artifact order.

The Fields view is evidence-location aggregation, not a guaranteed JSON-path structural diff.
The Timeline is causal manifest order, not timestamped live HTTP history. Request aggregates do
not incorporate comparison verdicts and must not be presented as “validation passed.”

`query_report_archive` authenticates bounded candidates and returns stable cursor pages with safe
filters, duration, ExecutionProfile, adverse counts, title/tags, and optional sanitized VCS/CI
context. Per-entry metadata failures remain isolated. Notes stay in the full artifact rather than
compact rows. `verify_artifact` authenticates every blob before full use;
`read_artifact_body_window` resolves result coordinates to a truthful bounded body window.

`export_artifact_curl` serializes only authenticated sanitized request captures. Report retention
is a two-phase reviewed plan/application workflow; application re-verifies coordinates and moves
bundles into private recoverable trash. `restore_report` authenticates a restored bundle and rolls
it back if verification fails. Trash is deliberately recoverable archive removal, not permanent
purging: it does not reclaim disk space until a future explicit destructive purge capability is
designed.
