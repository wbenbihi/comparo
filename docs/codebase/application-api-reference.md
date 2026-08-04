# Application API reference

This is the golden frontend-neutral access contract for Comparo. A CLI, TUI, CI adapter, test
harness, or future remote frontend should obtain project and run data through these APIs instead
of importing core runtime objects, artifact-store internals, or presentation code.

The public imports are curated by `comparo.application`. Concrete DTO definitions remain in the
modules named below so contributors can trace validation and ownership without guessing.

## Boundary rules

Every application product follows the same rules:

1. DTOs are frozen, keyword-only `msgspec.Struct` values or closed enums. They can be retained by
   a frontend without retaining live providers, secrets, paths, sockets, or mutable core state.
2. A success-or-diagnostics product never contains both. Diagnostics contain closed codes and
   safe coordinates; arbitrary adapter exception text never crosses this boundary.
3. Collection, byte, decode, work, and retention limits belong to the caller or operator policy.
   Versioned defaults are replaceable defaults, not hidden application-wide maxima.
4. Frontends never resolve expressions, recompute assertions/diffs/gates, or open artifact files
   themselves. They request an application projection of the authoritative compiled plan or
   sanitized artifact.
5. Opaque handles are capabilities, not identifiers to synthesize. Use the service that issued a
   handle and release it through that service.
6. `RunArtifact` facts are terminal truth. Live snapshots are bounded operational observations;
   they do not constitute a second verdict engine.

## Availability model

Data is intentionally accessed through different owners at different lifetimes.

| Lifetime | Owner | Read API | Contains |
| --- | --- | --- | --- |
| Project catalog | caller | `inspect_project_workspace` | Safe resource/source metadata and dependency outlines |
| Prepared comparison | `CompareService` | review, occurrence/rule/composition/resolved/preview inspections | Exact retained admitted plan, no effects yet |
| Current run | `RunService` → `ExecutionSession` | `list`, `inspect`, `after` | Coalesced counters, typed occurrence coordinates, recent events, bounded live facts |
| Retained terminal run | `RunService` | `result`, `result_page` | Safe production outcome and independently paged artifact fact collections |
| Archived run | local artifact store | `query_report_archive`, `read_archived_result_page` | Searchable rows and authenticated manifest pages |
| Persisted body | local artifact store | body byte/structure APIs | Sanitized authenticated request/response capture selected by exact result coordinates |

The run registry is process-local and bounded by `RunService.maximum_runs`. The archive is durable.
A terminal run can be removed from the registry with `discard` after its caller has retained the
needed safe products; persisted artifacts remain independently discoverable.

Sanitized body bytes are durable only when artifact persistence succeeded. A non-persisted
terminal artifact still exposes capture metadata and facts, but its temporary sanitized spool is
destroyed during execution cleanup. This is deliberate: keeping unbounded body bytes in the run
registry would silently turn it into a second storage engine. Interactive callers that require
later body inspection must review and select persisted artifact settings and an authorized root.

## Project and Explorer access

### Catalog

```python
inspect_project_workspace(request: ProjectWorkspaceRequest) -> ProjectWorkspaceResult
```

`ProjectWorkspaceRequest` owns the entry config, additional sources, and `ProjectLoadLimits`.
Compilation must build the complete bounded linked graph so identities, links, cycles, and
consumer edges are correct. `ProjectWorkspaceSnapshot` therefore returns the complete catalog
within those caller-owned load limits rather than pretending that slicing after compilation
reduces compiler memory.

On success:

- `snapshot.project` is the Project root;
- `snapshot.resources` contains every non-Project resource in deterministic source order;
- summaries expose namespace/kind/ID/fingerprint, name, description, tags, source coordinate, and
  a secret-free outline;
- Project outline fields may expose a separately typed, bounded `exact_value` for safe inert
  configuration and linked identities; runtime value namespaces remain excluded and require the
  masked resolver;
- linked outline fields additionally expose `reference: ResourceIdentityCoordinate`, preserving
  ordered/repeated namespace-kind-ID identities without requiring a frontend to parse display
  text or reconstruct execution selections from the deduplicated relationship graph;
- outlines expose both authored relationships and inverse `used-by` relationships;
- `sources` exposes logical source IDs, safe basenames for display, entry membership, resource
  membership, and safe source revision metadata without local paths.

On failure, `snapshot` is absent and diagnostics are value-free. A partially discovered source
catalog may still be present.

### Raw authored source

```python
inspect_project_source(request: SourceInspectionRequest) -> SourceWindow
```

Use `SourceViewMode.MASKED` by default. `TRUSTED_EXACT` is the only API intentionally able to
return authored secret literals; its result declares `contains_unmasked_values`. Windows are
bounded and revision-pinned with `expected_sha256` so navigation never silently switches to a
new file revision.

### Effective compiled resource

```python
inspect_effective_resource(
    request: EffectiveResourceInspectionRequest,
) -> EffectiveResourceWindow
```

The request selects the exact resource fingerprint, a field offset, and caller limits. The result
contains value-safe typed fields and relationships from the linked compiled graph. It is not a
resolved runtime value.

## Compare preparation and inspection

`CompareService` is a bounded owner of admitted plans. Its normal lifecycle is:

```text
prepare → inspect/review repeatedly → start exactly once
                                └──→ discard without execution
```

`prepare(ComparePrepareRequest)` compiles, plans, applies operator authority, captures the allowed
ambient-process snapshot, performs runtime/evaluation admission, and returns a
`ComparePreparationHandle` plus `PlanReviewInspection`. `CompareExecutionControls`—settings,
artifact destination, mutation approval, tool version, and run annotations—are part of the
reviewed checkpoint. `start` rejects different controls.

The retained handle supports these non-effectful reads before start:

| API | Product | Purpose |
| --- | --- | --- |
| `preview_requests(handle, controls, start_ordinal=..., maximum_previews=...)` | `RenderPreviewWindowResult` | Finalized sanitized request occurrences by ordinal window |
| `inspect_resolved_value(handle, controls, target)` | `ResolvedValueInspectionResult` | One masked/public resolved value tree; target owns projection limits and page |
| `inspect_resolved_values(handle, controls, batch)` | `ResolvedValueInspectionBatchResult` | Caller-bounded independent targets for one detail surface |
| `inspect_request_composition(handle, controls, execution_ordinal=...)` | `RequestCompositionInspection` | Per-field layers, origin, strategy, and winner |
| `list_occurrences(handle, controls, start_ordinal=..., maximum_occurrences=...)` | `PlanOccurrenceWindow` | Execution coordinates without materializing wire values |
| `list_comparisons(handle, controls, start_ordinal=..., maximum_comparisons=...)` | `PlanComparisonWindow` | Directed baseline/candidate occurrence coordinates |
| `list_rules(handle, controls, start_ordinal=..., maximum_rules=...)` | `PlanRuleWindow` | Compiled assertion/diff rule identities, selectors, operations, and origins |

Resolved inspection targets bind the plan fingerprint, execution ordinal, Environment, Matrix
cell/case inputs, effective Request overrides, and a value/field path. Sensitive values are
irreversibly masked. Public scalar/container values use bounded nodes and a projection
fingerprint. Batch results keep each target's success/diagnostics independent.

Request composition is structural provenance, not value resolution. It covers method, URL or
endpoint, headers, query, cookies, auth, body, stream, request policy, retry, redirects, timeout,
TLS, proxy, and other canonical Request fields. A presentation can explain a winner without
reimplementing precedence.

Starting consumes the preparation and returns an `ExecutionSession`. `discard` releases an unused
preparation. `close` requests cancellation of service-owned inspections; `aclose` waits within a
caller timeout. A frontend should close the service on project replacement or process shutdown.

## Current and terminal run access

`RunService` is the process-local registry. Register the `ExecutionSession` returned by Compare:

```python
handle = runs.register(session)
```

Then use:

| API | Contract |
| --- | --- |
| `list(RunQuery)` | Stable registration-order `RunWindow`, optionally filtered by phase |
| `inspect(RunHandle)` | Latest `RunInspection` and `ExecutionProgressSnapshot` |
| `after(handle, revision)` | Await one newer coalesced snapshot; `None` means terminal stream closure |
| `cancel(handle)` | First-wins cancellation request; returns whether this call won |
| `result(handle)` | Await `RunResultAccess`, containing exactly production result or diagnostics |
| `result_page(handle, ResultPageQuery)` | Await one independently bounded terminal fact collection |
| `discard(handle)` | Remove one terminal retained run; active removal is rejected |
| `close()` | Cancel active runs, await cleanup, and release the registry |

`ExecutionProgressSnapshot` is latest-only and revisioned. It exposes:

- phase and exact queued/started/finished execution/comparison counters;
- bounded `LiveOccurrenceProgress` rows with execution or comparison coordinates, state, outcome,
  safe failure, and observer timestamps;
- bounded recent runtime events;
- bounded `LiveRuleFactProgress` assertion/diff facts with owner, rule/effective-rule identity,
  severity, outcome, and aggregate count metrics;
- `omitted_occurrences`, `omitted_facts`, and `incomplete_fact_scopes`, which must be shown when
  nonzero rather than silently implying completeness;
- terminal `ExecutionFinalSummary` once artifact compilation completes.

Live facts intentionally omit evidence values and body content. The final artifact provides those
sanitized products. Gate is terminal-only because reducing a partial fact stream would produce a
misleading provisional verdict.

## Completed result access

### Eager projection

```python
project_results(artifact: RunArtifact) -> ResultProjection
```

Use the eager projection when the authenticated manifest is already within the frontend's chosen
memory budget. It returns summary/gate, executions, comparisons, assertions, diffs, cross-links,
sanitized HTTP topology, timings, body references, coverage information, and
`ResultRunAnnotations` (title, notes, tags, VCS, CI, and redaction count). It never loads detached
body blobs or recomputes a verdict.

### Paged projection

```python
project_result_page(artifact, ResultPageQuery(...)) -> ResultPage
await RunService.result_page(handle, query) -> RunResultPageAccess
read_archived_result_page(root, bundle_id, read_limits, query) -> ResultPage
```

`ResultPageKind` selects `executions`, `assertions`, `comparisons`, or `diffs`. Each collection has
its own offset and positive maximum. The result is one of four tagged concrete structs:

- `ExecutionResultPage`;
- `AssertionResultPage`;
- `ComparisonResultPage`;
- `DiffResultPage`.

All share the authenticated canonical-results SHA-256, run summary, annotations, `start`, total,
items, and truthful `next_start`. The variants are tagged because an untagged union of structurally
similar `msgspec` rows cannot be decoded unambiguously. Consumers may decode the public
`ResultPage` union directly.

`read_archived_result_page` authenticates the manifest and completion marker without reading body
blobs. Use it when a report view needs facts but not bodies.

### Derived analysis lenses

The `result_analysis` functions project caller-windowed Issues, Requests, Rules, Fields, and
Timeline views from a `ResultProjection`. These are navigation/index lenses only. They preserve
artifact outcomes and references and never reevaluate facts.

## Archive and durable reports

```python
query_report_archive(root, limits, query) -> ReportArchivePage
```

The archive query authenticates candidate bundles, applies safe metadata filters, and returns a
stable cursor page. Per-entry failures are isolated in the page rather than aborting the entire
archive. Use `verify_artifact` for a full artifact+blob verification and `render_artifact_report`
for bounded JSON/JUnit/SARIF/Markdown output.

Retention is a reviewed two-step workflow:

```text
plan_report_retention → inspect fingerprinted candidates → apply_report_retention
```

Removal moves bundles to a private trash namespace. `restore_report` reauthenticates before making
a bundle visible again.

## Body access

A body can be selected only with an `ArtifactBodyReference` emitted by a result projection. The
reference binds artifact API version, artifact ID, canonical-results SHA-256, execution, attempt,
exchange, and request/response side. Callers must not manufacture coordinates from display text.

### Raw sanitized byte window

```python
await read_artifact_body_window(
    root,
    bundle_id,
    reference,
    read_limits,
    offset=...,
    maximum_output_bytes=...,
) -> ArtifactBodyWindow
```

The service authenticates the bundle and exact reference, then returns capture state, extent,
omission reason, media type, total bytes, content, redaction count, and truthful `next_offset`.
An omitted body is represented explicitly; it is never converted to an empty successful body.

### Structured JSON and SSE page

```python
await read_artifact_structured_body_page(
    root,
    bundle_id,
    reference,
    read_limits,
    StructuredBodyLimits(...),
    StructuredBodyQuery(...),
) -> StructuredBodyPage
```

`StructuredBodyLimits` requires explicit maximum document bytes, JSON nodes, depth, preview
characters, pointer bytes, SSE events, and SSE line bytes. The reader first authenticates and caps
the complete sanitized capture. Decode amplification can therefore never bypass the byte cap.

`format=auto` selects JSON for `application/json` or `+json`, and SSE for
`text/event-stream`. Explicit `json` or `sse` remains available when the caller deliberately
overrides missing/incorrect media metadata.

`StructuredJsonPage` is a pre-order RFC 6901 tree page. Each node has an ordinal, absolute pointer,
parent, name/index, relative depth, type, child count, bounded preview, and truncation marker. A
query pointer roots projection at one subtree without returning siblings.

`StructuredSsePage` contains dispatched events with ID, type, retry, data-line/character counts,
bounded preview, and `empty`/`text`/`json` classification. It also declares a trailing incomplete
event. Supplying `sse_event_index` opens that event's data as a `StructuredJsonPage`; its normal
JSON Pointer and page fields then apply. Event boundaries never depend on chunk counts.

Failures are closed `ArtifactOperationErrorCode` values: unavailable/not-found bodies, unsupported
format, decode failure, missing subtree/event, output cap, or structural projection cap. No parser
exception or body fragment appears in the error.

## Error and serialization handling

- Treat `ApplicationDiagnostic.code` and closed service error enums as machine keys. Resolve user
  copy with `present_diagnostic`; never display `repr(exception)`.
- Treat `next_start`, `next_offset`, archive cursor, snapshot revision, fingerprints, and omission
  counts as protocol fields, not decoration.
- Tagged unions (`ResultPage`, `StructuredBodyPage`) can be encoded/decoded with `msgspec` using
  the public union type. Do not discard the tag and guess from fields.
- `artifact_results_sha256`, plan fingerprint, source SHA-256, and projection fingerprint bind
  different representations. Never substitute one for another.
- A changed source, preparation mismatch, stale artifact reference, or projection mismatch must
  trigger reload/reselection. Silently showing data from the new revision is incorrect.

## Deliberately deferred capabilities

These are not presentation gaps to work around:

- stateful Scenario/step execution and captures;
- WebSocket and gRPC transports;
- browser automation, which is outside Comparo's scope;
- community plugin loading/trust/packaging and an external LLM judge;
- reconstruction of every original live input for “run this historical report again.”

Future capabilities must enter through typed, authorized, caller-bounded application contracts.
They must not make a frontend import resolver, runtime, transport, or storage internals.
