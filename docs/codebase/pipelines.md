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
| `Environment` | Yes | Partial: `baseUrl`, `env`, variables, secrets |
| `Request` | Yes | Partial: body-free request subset |
| `Scenario` | Yes | No; rejected by runtime capability preflight |
| `Matrix` | Yes | Yes |
| `ExecutionProfile` | Yes | Yes, within the supported execution subset |
| `Schema` | Yes | No response-schema validation in production composition |
| `AssertionProfile` | Yes | Partial operator/selector subset |
| `DiffProfile` | Yes | Partial pipeline subset |
| `GateProfile` | Yes | Partial: empty profile selects one fixed strict gate |
| `SafetyProfile` | Yes | Planning handles exist; attached profile is rejected by production runtime |
| `RedactionProfile` | Yes | Attached profile is rejected; fixed mandatory redaction still applies |
| `ArtifactProfile` | Yes | Attached profile is rejected; composition settings control artifacts |
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

### What compilation does not retain

`ProjectSourceSet` temporarily contains `ParsedDocument` objects, but `LoadedCompilation` returns
the compilation and a source catalog only. Exact authored documents and text are discarded. This
is why the existing application API cannot provide a raw YAML view after loading.

## 2. Invocation, precedence, and planning

```text
CompiledProject + PlanningOptions + ExecutionPolicy authority
  -> typed RunInvocation
  -> structural/value/safety binding
  -> Matrix recipes and topology
  -> exact cardinality and budget admission
  -> fingerprinted PreparedRun
```

`PlanningOptions` is the frontend-neutral primitive override contract. It supports:

- selecting an `ExecutionProfile`, or selecting ad-hoc subjects;
- assertions-only Environments, or one baseline plus one or more candidates;
- replacing Matrix attachments by binding name;
- trials, maximum attempts, fail-fast, and planning-budget overrides;
- global or Environment-specific `parameters`, `variables`, and `secrets` supplied through
  reusable `Value` resources.

It does **not** support ephemeral inline resources, arbitrary literal overlay mappings, request
field patches, selecting individual Matrix case IDs, or choosing assertion/diff/gate profiles at
invocation time.

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
snapshot.

`RunContext` resolves expressions at the run, cell, trial, Environment, execution, step, or
attempt scope. Effectful reads are single-flight per exact source/scope key. Results are the
closed union `Present`, `Unavailable`, `Missing`, `Error`, or `Blocked`, with value-free
provenance. Secret and env values are sealed and registered for sanitization.

The `steps` namespace and step scope are internal forward seams; Scenario capture production is
not implemented.

## 4. Offline request preview

`render_prepared_requests` resolves every planned request using pinned project files, isolated env
snapshots, deterministic preview generation, and the same `ConfigurationRequestProvider` used by
production execution. It performs no DNS lookup or network I/O. The result is sanitized before it
crosses the application boundary.

Each preview includes occurrence IDs, subject/environment coordinates, method, sanitized URL,
ordered headers, redacted cookies, and body omission metadata. Current limitations:

- the runtime admits only absent/null bodies, so preview bodies are always `no-body`;
- query fields are represented inside the sanitized URL, not as a separate list;
- exact per-field value provenance is not returned;
- all occurrences are resolved as one atomic operation; one resolution diagnostic prevents a
  partial preview collection;
- there is no application-owned cURL formatter yet.

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
features. The current admitted Request wire fields are method, endpoint/URL, headers, query, and
an absent/null body. The method must be a static literal and allowed by policy. Scenarios,
response schemas, configured bodies, Environment HTTP overlays, and attached Safety,
Redaction, or Artifact profiles are rejected.

The lower-level HTTP domain and adapter support substantially more: typed request bodies,
authentication, redirect/cookie policy, retry, deadlines, TLS, proxying, bounded storage, ordinary
responses, and SSE/record streams. Those features are not project features until configuration
binding and production settings honor them.

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
bounded stream records and decode SSE `data` as JSON according to the stream policy. The current
configuration evaluation binder exposes only whole JSON response body and status selectors, so a
user cannot yet author the full stream-selector/diff pipeline through the production YAML path.

## 6. Evaluation and gates

The pure assertion and diff packages implement a broad typed kernel. The current
`evaluation_binding` application adapter exposes only this initial external subset:

| Family | Current production binding |
| --- | --- |
| Assertion selectors | `status`, `response.status`, `body`, `response.body` |
| Assertion operators | `equals`, `notEquals` |
| Assertion controls | `severity`, `quantifier`, `completeness` |
| Diff selectors | same status/body selectors |
| Diff pairing | automatic `singular` or `ordered` from selector cardinality |
| Diff comparators | `exact`, `ignore` |
| Diff controls | `severity`, `completeness` |
| Profile composition | `include` and `rules`; other profile fields rejected |
| Gate | one reusable, non-inline, empty profile; fixed blocking zero-tolerance thresholds |

Diff transforms, reducers, explicit pairing, numeric tolerance, keyed/set/multiset comparison,
shape/type comparison, stream projection, and other modeled operations are rejected by this
binder even though the pure diff engine implements them.

Evaluation produces stable occurrence-level facts, structured outcomes, counts, coverage,
bounded evidence, and value-free issues. The gate consumes normalized summaries rather than live
values. It never recomputes assertions or diffs.

## 7. Live progress and cancellation

`ExecutionSession` starts one production task, owns the run cancellation source, and yields
coalesced `ExecutionProgressSnapshot` values. Snapshots expose phase, totals, queued/started/
finished counts, latest runtime sequence, and the latest execution/comparison identity and event
kind. A slow UI retains only the latest snapshot.

Runtime events are deliberately sparse: run started; execution queued, started, or finished;
comparison finished; run finished. They contain identities but no terminal outcome, duration,
assertion/diff status, HTTP phase, or body/stream progress. There is no retained event history.

Final detail becomes available only after `session.result()` returns a `RunArtifact`. A rich live
table/timeline therefore requires a new bounded sanitized application progress projection.

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

`production_execution_defaults_v1` does not persist artifacts and classifies response bodies as
public for in-memory artifact creation. `persisted_production_execution_defaults_v1` enables
persistence but conservatively classifies response bodies as sensitive. Because sensitive bodies
are omitted by the current fixed redaction composition, historical persisted reports do **not**
provide full response bodies by default. This does not yet implement the product decision that
full sanitized response bodies should normally be available.

Attached `RedactionProfile` and `ArtifactProfile` resources are also rejected by production
capability preflight, so the fixed composition cannot currently be customized from project YAML.

## 9. Result, archive, and report projections

`project_results` converts one sanitized `RunArtifact` into a body-free, renderer-independent
model with summary, execution rows, comparison rows, assertion/diff facts, evidence, stable
references, and cross-links. It never opens blobs or changes verdicts.

`result_analysis` derives bounded views:

- Issues groups non-success fact outcomes;
- Requests aggregates execution outcomes by subject;
- Rules aggregates rule applications and outcomes;
- Fields aggregates normalized evidence locations and samples;
- Timeline emits deterministic causal artifact order.

The Fields view is evidence-location aggregation, not a guaranteed JSON-path structural diff.
The Timeline is causal manifest order, not timestamped live HTTP history. Request aggregates do
not incorporate comparison verdicts and must not be presented as “validation passed.”

`list_report_archive` authenticates each bounded candidate's marker, manifest, and results bytes,
then returns newest-first summary rows plus isolated per-entry metadata failures. It validates
blob declarations and budgets but does not read/hash every blob. `verify_artifact` performs that
complete blob authentication before full result use. `read_artifact_blob` authenticates and reads
one explicitly named sanitized digest under an output cap. `render_artifact_report` exports JSON,
JUnit, SARIF, or Markdown from a completely verified artifact.

Current archive/listing gaps include pagination/cursors, server-side search/filtering, duration,
ExecutionProfile identity, VCS metadata, notes/tags/title, and top-issue summaries. The blob API
is digest-oriented; it does not yet resolve a result/exchange/body reference into a decoded,
chunked viewer model.
