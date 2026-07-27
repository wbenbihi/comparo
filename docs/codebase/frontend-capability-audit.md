# Frontend capability audit

This is the implementation contract for a replacement CLI/TUI/CI frontend. It answers whether a
journey can be implemented through current backend contracts without importing live core objects
or reimplementing business rules in presentation code.

Status meanings:

- **Supported** — executable behavior and a safe frontend-neutral boundary both exist.
- **Partial** — the backend is useful, but one explicit limitation remains.
- **Deferred** — intentionally outside the current release scope.

## Executive assessment

The backend now supports the complete core Comparo journey: inspect a project, build or override a
bounded execution, review the exact plan and authority, preview requests lazily, run assertions and
directed environment diffs, follow bounded live progress, inspect final facts and sanitized bodies,
and browse/export/retain historical reports. The TUI does not need to import the current TUI code
or invent missing domain behavior.

The remaining product gaps are deliberate scope decisions, not hidden presentation work:

- stateful `Scenario` execution and step captures;
- WebSocket and gRPC transports;
- browser automation, which is outside Comparo's scope;
- a community plugin host/trust/packaging model and external LLM judge;
- a generic frontend editor for arbitrary project resources.

## Explorer

| Capability | Status | Backend contract | Notes |
| --- | --- | --- | --- |
| Discover sources and resources | Supported | `inspect_project_workspace` | Deterministic, bounded, with metadata, tags, descriptions, fingerprints, and source coordinates |
| Show dependency and consumer graph | Supported | `ProjectRelationship` | Includes links/composition and inverse `used-by` edges |
| Show raw authored YAML | Supported | `inspect_project_source` | Line/byte bounded and content-fingerprint pinned |
| Show YAML without disclosed secrets | Supported | `SourceViewMode.MASKED` | Default mode; uses the compiler's sensitivity classifier, masks comment payloads, and masks all non-whitespace when invalid YAML has no trustworthy spans |
| Show exact authored YAML | Supported, explicit trust | `SourceViewMode.TRUSTED_EXACT` | Output declares `contains_unmasked_values`; do not log, index, or retain it implicitly |
| Detect source changes | Supported | `expected_sha256` | A changed source fails with a closed `source-changed` code |
| Show effective compiled structure | Supported | `inspect_effective_resource` | Fingerprint-exact, value-safe, paged fields and relationships |
| Raw/effective side by side | Supported | source + effective inspection services | Use source/resource fingerprints to label the two representations honestly |
| Show runtime-resolved Request values | Supported | `render_prepared_request_window` | Sanitized outbound request preview, one ordinal window at a time |
| Show arbitrary generic resolved values | Supported | `CompareService.inspect_resolved_value` / `ResolvedValueInspectionTarget` | Exact retained plan, execution ordinal, Environment, Matrix cell, and effective Request overrides; sensitive values are irreversibly masked and public values are fingerprinted, paged, and bounded |
| Explain planning precedence | Supported | plan provenance inspections | Runtime request-field provenance is not yet projected field by field |

“Raw,” “effective,” and “resolved” are distinct. Raw is authored source; effective is linked and
precedence-applied structure. Generic resolved inspection is a masked run-scoped expression view;
resolved request preview is sanitized wire intent. The generic result's machine-readable
`freshness` declares that the retained plan and captured ambient-process input are exact, while
dotenv files, project files, and generated values are inspection samples that execution produces
again.

## Compare preparation

| Capability | Status | Backend contract | Notes |
| --- | --- | --- | --- |
| Assertions across Environments | Supported | `CompareIntent.ASSERTION` | One or many Requests and Environments |
| Baseline-to-many diff | Supported | `CompareIntent.COMPARISON` | Baseline occurrence is shared per cell/trial |
| Execute a named profile | Supported | `CompareIntent.EXECUTION_PROFILE` | Selection remains overrideable through typed invocation controls |
| Multiple Matrices | Supported | `MatrixOverride` | Independent attachments form a Cartesian product |
| Select Matrix cases | Supported | `MatrixOverride.cases` | Explicit case filters preserve binding identity |
| Trials, attempts, fail-fast, budgets | Supported | `PlanningOptions` | Caller requests are capped by operator authority |
| Inline parameters/variables/secrets | Supported | `InlineValueOverride` | Secret literals must already be sealed |
| Override `$env` for one Environment | Supported | `EnvironmentValueOverride` | Values are sealed and scoped; process environment is not mutated |
| Patch Request fields | Supported | `RequestOverride` | Method, URL/endpoint, fields, body/auth/stream, and transport policy expressions |
| Select/detach rule and policy profiles | Supported | `ProfileSelectionOverrides` | Assertions, diffs, gate, safety, redaction, and artifacts |
| Ephemeral full ExecutionProfile resource | Partial | Compose the typed controls above | There is no second anonymous profile language; controls compile into the normal invocation model |
| Preview exact topology and authority | Supported | `PlanReviewInspection` | Includes counts, profiles, limits, provenance, policy, and mutation approval |
| Review/start checkpoint | Supported | `CompareService` one-shot handle | Start rejects controls that differ from the reviewed checkpoint |
| Preview very large runs | Supported | `CompareService.preview_requests` | Resolves only the requested ordinal window from the exact retained plan and reviewed controls; the handle remains usable for later pages or execution |
| Inspect one effective resolved field | Supported | `CompareService.inspect_resolved_value` | Uses the same retained handle and controls; in-flight inspections block start, and discard/close request cancellation while retaining the plan until cleanup drains |

No frontend may mutate `CompiledProject` or synthesize `PreparedRun` internals. Every ad-hoc edit
must use the typed invocation surface so normal precedence, provenance, policy, and budgets apply.

## HTTP, schemas, and evaluation

| Capability | Status | Notes |
| --- | --- | --- |
| Request method, URL/endpoint, duplicate headers/query, cookies, auth, body | Supported | Request finalization and body materialization use the same run-scoped resolver |
| Environment headers/cookies/auth/timeout/TLS/proxy | Supported | Lowered per Environment; policy remains authoritative |
| Redirects, retries, deadlines | Supported | Typed and bounded; redirects are reauthorized |
| JSON/text/binary response capture | Supported | Full body capture by default within caller-owned storage/capture limits |
| SSE and JSON `data` | Supported | Bounded stream snapshots and configured auto/JSON semantics |
| Local response JSON Schema | Supported | Compiled, prepared, validated, and emitted as ordinary gated assertion facts |
| Remote public JSON Schema | Supported with authority | Fresh route-pinned root-document GET with no redirects; exact Safety/ExecutionPolicy intersection plus document-count, per-document, aggregate-byte, and batch deadlines. External nested references are denied; local fragments work. Cache, integrity pins, and transitive fetching are not implemented. |
| Assertion operation catalog | Supported | Equality, membership, ordering, tolerance, strings, keys, length/cardinality, regex, media, digest, decode, and schema operations |
| Semantic diff pipelines | Supported | Selectors, project/exclude/normalize/shape/map/filter/sort/stream transforms, reducers, singular/ordered/keyed/set/multiset pairing, and typed comparators |
| Environment-specific ignored fields | Supported | Use `exclude` or `project` transforms before comparison |
| Nondeterministic LLM output | Supported without an LLM judge | Compare stable shape/types/subsets, normalize/project fields, reduce streams, and use keyed/set-like pairing |
| External LLM judge | Deferred | Reserved analysis capability seam exists; no plugin host or trust decision yet |
| Safety/Redaction/Artifact profiles | Supported | Bound as project restrictions; they cannot expand caller/operator authority |

## Live execution

| Capability | Status | Backend contract | Notes |
| --- | --- | --- | --- |
| Async start and cancellation | Supported | `ExecutionSession` | One explicit run-owned task and cancellation source |
| Overall progress | Supported | `ExecutionProgressSnapshot` | Phase and exact queued/started/finished totals |
| Per-occurrence state/outcome | Supported | `LiveOccurrenceProgress` | Queued/running/finished plus observed/unavailable/blocked/cancelled/evaluated/incomplete |
| Safe failure and timing | Supported | occurrence fields | Closed stage/code plus observer start/finish/elapsed nanoseconds |
| Recent event timeline | Supported | `recent_events` | Bounded caller-owned window |
| Slow frontend backpressure | Supported | latest-only mailbox | Occurrence/event windows are bounded; omission is explicit |
| Final assertion/diff/gate summary | Supported | `ExecutionFinalSummary` | Projected from the final artifact before terminal close |
| Live body/chunk viewer | Partial by security design | final artifact body service | Body content crosses the frontend boundary only after sanitization; stream progress is not a live raw-payload console |

## Final analysis and bodies

| Capability | Status | Backend contract | Notes |
| --- | --- | --- | --- |
| Summary, gate, executions, comparisons | Supported | `project_results` | Artifact facts are the verdict source of truth |
| Issues/Requests/Rules/Fields/Timeline | Supported | `application.result_analysis` | Bounded derived lenses; no verdict recomputation |
| Stable cross-navigation | Supported | `ResultReference` / `ResultCrossLink` | Suitable for screen routes and selection restoration |
| Request/response metadata | Supported | `ResultHttpRequestSlot` / `ResultHttpResponseSlot` inside `ResultHttpAttempt` and `ResultHttpExchange` | Sanitized wire values only; URL/header/cookie redaction counts and attempt/exchange timings are exact, retained/omitted/not-produced states are explicit, and terminal pre-send requests remain visible |
| Full sanitized persisted bodies | Supported | `read_artifact_body_window` | Authenticated result-coordinate lookup is bound to the exact canonical-results SHA-256, then exposes media/capture metadata, byte windows, and truthful continuation; stale replacement-report references fail closed and live transient bodies are outside this contract |
| Baseline/candidate body split view | Supported by composition | two body references/windows | Frontend aligns the two independent captures; chunk counts need not match |
| Safe cURL | Supported | `export_artifact_curl` | Generated only from authenticated sanitized request captures; warnings/counts include URL, header, cookie, and body redactions |

Complete UTF-8 request captures may be embedded in cURL. Binary, oversized, partial, or omitted
bodies produce a typed warning and non-executable file placeholder rather than false equivalence.

## Reports

| Capability | Status | Backend contract | Notes |
| --- | --- | --- | --- |
| Persist sanitized artifacts | Supported | local artifact sink | Private atomic POSIX bundles with independent artifact schema |
| List and search history | Supported | `query_report_archive` | Authenticated safe filters, stable cursor, page size, total matches, per-entry failures |
| Useful archive metadata | Supported | `ReportArchiveEntry` | Duration, profile, subjects/envs, counts, adverse facts, title/tags, VCS and CI context |
| Notes | Supported in full artifact | `RunArtifact.annotations.notes` | Kept out of compact rows by design |
| Open and verify | Supported | `verify_artifact` | Hashes every declared blob before full use |
| Export JSON/JUnit/SARIF/Markdown | Supported | `render_artifact_report` | Caller-bounded output |
| Retention/archive removal | Supported, recoverable | `plan_report_retention` / `apply_report_retention` | Reviewed fingerprinted plan, full preflight before mutation, re-verification, private trash namespace. Moving to trash does not reclaim filesystem space. |
| Restore | Supported | `restore_report` | Restored bundle is authenticated; failed verification rolls it back to trash |
| Run historical intent again | Deferred | artifacts retain safe provenance, not every live input | Comparo is not traffic replay; a future “run again” must deliberately reconstruct a new invocation |

## Diagnostic and security contract

- Frontends consume `ApplicationDiagnostic` and `present_diagnostic`; arbitrary exception text is
  never a user-facing message source.
- Live `SensitiveValue`, `RunContext`, wire requests, observations, body handles, adapter
  exceptions, and filesystem paths do not enter widget state.
- Project limits are caller-owned settings and policy ceilings. Versioned defaults are
  replaceable operational defaults, not schema-wide maximums.
- Parent-process values are copied into one bounded sealed carrier with a value-free debug
  representation before asynchronous execution, and only when a selected Environment declares
  `inheritProcess`. A request carrying `None` or an empty carrier does not perform a later ambient
  read.
- Exact source mode is the only intentional authored-secret disclosure surface. Masked source,
  masked resolution, request preview, artifacts, archive rows, cURL, and reports are separate safe
  projections.
- Generic resolved inspection never returns a live resolver, prepared plan, local path, provider
  exception, source-derived taint identifier, or sensitive payload. Caller-owned page and complete
  traversal limits bound egress and inspection work; source/resolution budgets bound value
  construction. Aggregate-sensitive values collapse, while frontends may inspect known effective
  child paths independently to display public siblings.
- `projection_fingerprint` identifies only the disclosed inspection projection. In particular, a
  masked projection fingerprints its public kind and bounded taint categories, never secret
  content. A paging mismatch therefore reports `resolution.inspect.projection-changed`; it must
  not be presented as proof that a masked payload did or did not change.
- The final `RunArtifact` is authoritative for results. Live progress is operational state, not a
  second verdict engine.

## Deferred extension boundary

`Scenario`/step execution, community plugins, and an LLM judge must enter through typed capability
contracts and explicit trust/limits. They must not add conditionals to the assertion/diff kernels
or let presentation code invoke arbitrary Python. The existing external-analysis capability seam
is intentionally an integration point, not a finalized plugin system.
