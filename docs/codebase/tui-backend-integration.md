# TUI/backend integration contract

This document is the implementation reference for a replacement Comparo TUI. It describes the
backend information a TUI can display, the application functions it should call, and the lifecycle,
safety, paging, cancellation, and identity contracts it must preserve.

It documents the backend on the `refactor` branch as of **2026-07-29**. It does not describe the
rejected current TUI implementation and does not require its component or state model to be reused.

The companion [application API reference](application-api-reference.md) is the canonical
frontend-neutral callable and lifetime contract. This document adds TUI orchestration and
information-design guidance on top of it.

## 1. Boundary in one sentence

Widgets render immutable application projections; they do not compile configuration, resolve live
values, authorize HTTP, execute requests, evaluate rules, authenticate storage, or infer facts that
the application layer did not project.

The preferred import boundary is `comparo.application`. Detailed result and structural DTOs that
are not re-exported at the package root remain public from their owning application modules:

```python
from comparo.application import CompareService, RunService, inspect_project_workspace
from comparo.application.project_outline import ProjectOutlineField, ProjectRelationship
from comparo.application.results import ResultMetric, ResultResource, result_value_preview
from comparo.reporters import ReportFormat
```

A TUI-specific backend facade may construct application inputs and import the few domain scalar
types required by those constructors. Widgets and presenters should not import `comparo.core`,
configuration adapters, HTTP adapters, artifact adapters, or live runtime types. This isolates a
current ergonomic seam in the Python API—some public request DTOs use `PositiveCount`, `ByteCount`,
`Duration`, `ValueNamespace`, `BudgetResource`, or value-expression types—without spreading that
coupling through presentation code.

## 2. Representation labels are part of the product contract

The TUI must label the representation it displays. “Config” and “resolved” are not precise enough.

| UI label | Backend product | What it means | Disclosure level |
| --- | --- | --- | --- |
| Authored · masked | `SourceWindow` in `MASKED` mode | A revision-pinned YAML line window with sensitive spans and comments masked | Default-safe |
| Authored · exact | `SourceWindow` in `TRUSTED_EXACT` mode | Exact authored YAML, including literals that may be secrets | Explicitly unsafe |
| Effective | `EffectiveResourceWindow` | Linked/compiled structural fields and relationships before runtime resolution | Value-safe |
| Plan | `PlanReviewInspection` | Effective selection, precedence, topology, limits, authority, and start decision | Secret-free |
| Resolved sample · masked | `ResolvedValueInspection` | One field resolved for one admitted execution occurrence through the masked resolver | Irreversibly masked |
| Request preview · sanitized | `RenderedRequestPreview` | Pre-network finalized and sanitized request for one plan occurrence | Safe projection |
| Live progress | `ExecutionProgressSnapshot` | Coalesced operational state for an active run | Value-free; not a verdict source |
| Result manifest | `ResultProjection` | Artifact-owned verdicts, facts, HTTP metadata, topology, and references | Sanitized; body-free |
| Artifact body · sanitized | `ArtifactBodyWindow` | Authenticated bounded bytes from a persisted sanitized request/response capture | Safe per capture policy, still untrusted terminal data |
| Artifact body · structured | `StructuredBodyPage` | Authenticated bounded JSON nodes or SSE events, including nested SSE data JSON | Safe per capture policy, still untrusted terminal data |

The TUI must not present a masked resolution sample as the value that is guaranteed to execute.
The plan and captured ambient process environment are exact, but dotenv files, project files, and
generated inputs may be sampled again. The `ResolvedValueFreshness` fields state those timing
semantics explicitly.

## 3. Recommended TUI-owned backend object

One process-scoped backend owner should contain operational services and immutable launch choices:

```text
TuiBackend
├── ProjectWorkspaceRequest
├── CompareService
├── RunService
├── ProductionExecutionSettings
├── ProjectLoadLimits / PolicyLoadLimits
├── ArtifactReadLimits / ReportArchiveLimits
├── absolute validated artifact root
├── zero or one current ComparePreparationHandle
└── bounded current/recent terminal sessions owned by RunService
```

This is a frontend orchestration object, not a domain service. Its responsibilities are limited to:

- translating typed widget selections into application request DTOs;
- serializing operations over the stateful `CompareService`;
- running synchronous filesystem/compilation work outside the Textual event loop;
- owning cancellation and cleanup for handles, sessions, and workers;
- dropping stale worker responses using frontend generation IDs; and
- returning application DTOs to presenters without reinterpreting them.

The owner must not cache live resolvers, `PreparedProjectPlan`, `SensitiveValue`, wire requests,
observations, blob paths, or adapter exceptions in widget state.

## 4. Function catalog

### 4.1 Functions the TUI should call

| Journey | Function or method | Execution shape | TUI rule |
| --- | --- | --- | --- |
| Load Explorer | `inspect_project_workspace(request)` | Synchronous, filesystem/compile | Run in a serialized thread worker |
| Resolve project root | `resolve_project_root(config)` | Synchronous path resolution | Use after successful project selection |
| Initialize default artifact root | `initialize_project_artifact_root(project_root, relative)` | Synchronous filesystem mutation | Bootstrap only from explicit launch behavior |
| Validate artifact root | `validate_project_artifact_root` / `validate_existing_artifact_root` | Synchronous filesystem validation | Validate before constructing execution controls |
| Read authored source | `inspect_project_source(request)` | Synchronous, filesystem/compile | Default to `MASKED`; pin `expected_sha256` |
| Read effective structure | `inspect_effective_resource(request)` | Synchronous, filesystem/compile | Page fields and relationships independently |
| Prepare Compare | `CompareService.prepare(request)` | Synchronous, filesystem/compile | Replace/discard the previous draft handle |
| Preview planned requests | `CompareService.preview_requests(...)` | Async, cancellable | Page by canonical execution ordinal |
| Inspect one resolved field | `CompareService.inspect_resolved_value(...)` | Async, cancellable | Use the same handle and controls as review/start |
| Inspect resolved fields | `CompareService.inspect_resolved_values(...)` | Async, cancellable batch | Keep target diagnostics independent |
| Explain Request composition | `CompareService.inspect_request_composition(...)` | Pure retained-plan projection | Show origin, strategy, and winner; do not infer precedence |
| List plan occurrences/comparisons/rules | `CompareService.list_occurrences/list_comparisons/list_rules` | Pure caller-windowed projection | Use canonical coordinates before and during execution |
| Validate start | `CompareService.validate_start(handle, controls)` | Synchronous, no effects | Optional immediate UI guard; `start` revalidates |
| Start Compare | `CompareService.start(handle, controls)` | Synchronous task creation | Consumes the one-shot handle |
| Register execution | `RunService.register(session)` | Synchronous bounded admission | Registry owns current/recent terminal discovery |
| List/inspect/follow execution | `RunService.list/inspect/after` | Sync snapshots + async revision wait | Render latest state; do not queue every snapshot |
| Cancel execution | `RunService.cancel(handle)` | Synchronous, first-wins | Change UI to cancelling only when it returns `True` |
| Obtain terminal run | `await RunService.result(handle)` | Async, shielded and value-safe | Always inspect terminal success or diagnostics |
| Build result model | `project_results(artifact)` | Pure synchronous | Use for both live Compare and stored Report |
| Page terminal results | `await RunService.result_page(handle, query)` | Async terminal wait + bounded projection | Page executions/assertions/comparisons/diffs independently |
| Build result pivots | `project_issues/requests/rules/fields/timeline(...)` | Pure synchronous | Window output; preserve selected reference |
| Query Report | `query_report_archive(root, limits, query)` | Synchronous, filesystem/authentication | Run outside event loop and reset cursor when filters change |
| Open Report | `verify_artifact(root, bundle_id, limits)` | Synchronous, full authentication | Then call `project_results`; never open files directly |
| Page archived results | `read_archived_result_page(...)` | Synchronous, manifest authentication | Does not read detached body blobs |
| Read a body | `read_artifact_body_window(...)` | Async, authenticated, bounded | Page by `next_offset`; use only a projected body reference |
| Read structured body | `read_artifact_structured_body_page(...)` | Async, authenticated, caller-bounded | JSON Pointer nodes, SSE events, or one event's JSON data |
| Export cURL | `export_artifact_curl(request)` | Async, authenticated, bounded | Display exact warnings/flags before copying |
| Export report | `render_artifact_report(...)` | Synchronous, authenticated, bounded | Run outside event loop; write only after an explicit user action |
| Review retention | `plan_report_retention(...)` | Synchronous, authenticated | Show candidates and fingerprint before applying |
| Apply retention | `apply_report_retention(...)` | Synchronous, recoverable mutation | Explicit confirmation; display partial failures |
| Restore report | `restore_report(...)` | Synchronous, recoverable mutation | Refresh archive after success |
| Present diagnostic | `present_diagnostic(diagnostic)` | Pure synchronous | Render catalog text plus safe structured coordinates |

### 4.2 Lower-level entry points the TUI normally should not call

| Entry point | Intended use | Preferred TUI route |
| --- | --- | --- |
| `prepare_project_plan` | Headless composition retaining internal execution objects | `CompareService.prepare` |
| `inspect_project_plan` | Stateless headless plan inspection | `CompareService.prepare`, which also binds controls and start authority |
| `plan_project` / `build_invocation` | Application and tests that already own a compiled project | Build `PlanningOptions`, then use `CompareService` |
| `render_prepared_requests` / `render_prepared_request_window` | Headless operation over a retained internal plan | `CompareService.preview_requests` |
| `execute_production` / `start_execution_session` | Headless composition | `CompareService.start` |
| `read_artifact_blob` | Generic sanitized blob access | `read_artifact_body_window` with authenticated result coordinates |
| `read_authenticated_artifact_request` | cURL service implementation | `export_artifact_curl` |
| `bind_execution_profiles`, evaluation binders, environment snapshot functions | Production composition internals | Review and execute through `CompareService` |

This distinction matters: direct lower-level calls can make a TUI preview one plan and execute
another, capture ambient inputs at a different time, or bypass the opaque-handle lifecycle.

## 5. Shared identities, diagnostics, and navigation

### 5.1 Resource identities

`ResourceCoordinate` is the revision-exact address of a compiled resource:

| Field | Meaning |
| --- | --- |
| `namespace` | Project namespace |
| `kind` | `Project`, `Request`, `Environment`, `Matrix`, profile kind, and so on |
| `resource_id` | Stable authored resource ID |
| `fingerprint` | Exact compiled definition SHA-256 |

Use the full coordinate—not only `resource_id`—as the Explorer selection key and as the identity
stored in local navigation. A reload can produce the same resource ID with a new fingerprint.

`SourceCoordinate` contains logical `source`, document number, line, and column. The logical source
ID is displayable; it is not a filesystem path and must not be treated as one.

### 5.2 Result identities

`ResultReference(entity, result_id)` is the canonical route key for result navigation. Entities
are `execution`, `assertion`, `comparison`, and `diff`. `ResultCrossLink` provides explicit edges:

- execution → assertion;
- comparison → baseline execution;
- comparison → candidate execution; and
- comparison → diff.

Build lookup dictionaries once per `ResultProjection`. Do not reconstruct relations by parsing
IDs. Store `ResultReference` in the Compare/Report local navigation stack so switching pivots does
not lose the selected fact.

### 5.3 Diagnostics

`ApplicationDiagnostic` contains only:

- `code`, `severity`, and `stage`;
- optional logical `SourceCoordinate`;
- optional `(namespace, kind, resource_id)`;
- a structural path; and
- optional occurrence count.

Use `present_diagnostic` for static title, summary, action, and documentation key. The code and
coordinates may be shown as structured secondary information. Never render `repr(error)`, an
exception traceback, or adapter exception text.

Application APIs have two failure shapes:

1. Success-or-diagnostics DTOs, such as `ProjectWorkspaceResult`, `ComparePreparationResult`,
   `RenderPreviewWindowResult`, `ResolvedValueInspectionResult`, and
   `ProductionExecutionResult`.
2. Closed value-free operation exceptions, such as `SourceInspectionError`,
   `CompareServiceError`, `ArtifactOperationError`, `CurlExportError`, `ReportArchiveError`, and
   `ReportRetentionError`. Render their enum `code`, mapped to trusted UI copy.

Unexpected exceptions must collapse to a fixed internal-error message at the backend/TUI boundary.

## 6. Explorer contract

### 6.1 Load the project catalog

Construct `ProjectWorkspaceRequest` with:

| Field | Purpose |
| --- | --- |
| `config: Path` | User-selected `comparo.yaml` or project directory |
| `additional_sources: tuple[Path, ...]` | Caller-added source files |
| `load_limits: ProjectLoadLimits` | Caller-owned discovery/parser ceilings |

Call `inspect_project_workspace`. `ProjectWorkspaceResult` is disjoint:

- success: `snapshot` plus a complete `sources` catalog and no diagnostics;
- failure: no snapshot, one or more diagnostics, and any safely discovered source summaries.

`ProjectWorkspaceSnapshot` provides `api_version`, `namespace`, one Project summary, and every
non-Project resource summary. Ordering is deterministic.

Each `ProjectResourceSummary` provides:

| Field | Explorer use |
| --- | --- |
| `coordinate` | Stable selection, reload comparison, navigation |
| `name` | Primary friendly label when present |
| `description` | Overview copy |
| `tags` | Filter chips/status metadata |
| `source` | Authored location jump |
| `outline.fields` | Secret-free structural overview rows |
| `outline.relationships` | Outbound and inbound dependencies |

Group resources by `coordinate.kind`; show `name` with `resource_id` as the stable secondary label.
Do not identify two resources only by `name`.

The matching `ProjectSourceSummary` provides both `source_id`, the opaque identifier used for
revision-pinned reads, and `filename`, the safe basename intended for user-facing provenance.
Never render `source_id` as though it were a filesystem name and never reconstruct an absolute
path from either projection.

### 6.2 Structural overview rows

`ProjectOutlineField` contains `path`, `label`, `value`, `depth`, `category`, and the optional
paired fields `exact_value` / `exact_kind`. Current categories include containers, references,
inline definitions, directives, literals, sensitive values, and generic structure. The ordinary
`value` is purposefully conservative:

- sensitive values are `* REDACTED`;
- most strings show their length rather than contents;
- arrays/objects show member counts;
- `$use` and value directives show safe structural meaning;
- remote schema URIs and `$file` paths are concealed; and
- Request methods are an explicitly safe exact scalar.

For Project resources only, `exact_value` can additionally carry a bounded terminal-safe display
of inert compiled null/boolean/number/string leaves and linked-resource identities. Project value
namespaces (`variables`, `secrets`, and their defaults equivalents) never receive this shortcut;
they must use masked runtime resolution. Project data/source paths are reduced to safe basenames,
oversized strings are omitted, and control/non-ASCII code points are escaped. This lets a Project
overview combine exact inert configuration with masked runtime values without parsing authored
YAML or exposing filesystem coordinates in the TUI.

This is an overview/tree model, not a normalized YAML renderer and not a generic project editor.

### 6.3 Dependency view

`ProjectRelationship` provides:

| Field | Meaning |
| --- | --- |
| `path` | Structural location on the owning resource |
| `target` | Namespace/kind/resource ID of the other resource |
| `origin` | `link`, `composition`, or inverse `used-by` |

The dependable primary UI is an inbound/outbound table or tree. A visual graph can be derived from
the same edges, but it must not replace the navigable list. Targets intentionally do not contain a
fingerprint; resolve them against the current workspace snapshot before navigation.

### 6.4 Authored source view

For one selected resource, prefer `AuthoredResourceInspectionRequest`. It pins the exact resource
coordinate and returns only that YAML document through `AuthoredResourceWindow`:

| Field | Rule |
| --- | --- |
| `workspace` | Same request used for the catalog |
| `coordinate` | Exact `ProjectResourceSummary.coordinate`, including fingerprint |
| `limits` | Explicit source bytes, output bytes, and line count |
| `start_resource_line` | One-based paging cursor relative to the resource document |
| `mode` | `MASKED` by default |
| `expected_sha256` | Set from the owning `ProjectSourceSummary` to reject reload races |

The result includes the resource's absolute source span, resource-relative paging state, content,
redaction count, and disclosure marker. It prevents an Explorer detail panel from accidentally
showing adjacent YAML documents in the same source.

Use `SourceInspectionRequest` only for an intentional whole-source browser:

| Field | Rule |
| --- | --- |
| `workspace` | Same request used for the catalog |
| `source_id` | From `ProjectSourceSummary.source_id` |
| `limits` | Explicit source bytes, output bytes, and line count |
| `start_line` | One-based paging cursor |
| `mode` | `MASKED` by default |
| `expected_sha256` | Set from the source catalog to reject reload races |

`SourceWindow` returns revision, total/returned lines, content, `next_line`, whether one line was
truncated by the byte cap, redaction count, disclosure marker, and compilation diagnostics.

Rules for exact mode:

- require a deliberate user action and persistent unsafe-state banner;
- never enable it merely because a resource is selected;
- do not log, telemetry-record, search-index, cache across sessions, or automatically copy it;
- clear it when the project reloads or the facet closes; and
- treat `contains_unmasked_values=True` as authoritative.

### 6.5 Effective resource view

`EffectiveResourceInspectionRequest` selects the exact resource coordinate and independently
pages structural fields and relationships. `EffectiveResourceWindow` returns totals and separate
`next_field` / `next_relationship` cursors. A reload changing the coordinate fingerprint produces
`RESOURCE_NOT_FOUND`; reload the workspace instead of silently opening the new revision.

### 6.6 Resolved view inside Explorer

There is deliberately no context-free “resolve this Request” API. Runtime values depend on:

- the selected Environment;
- Matrix binding/case combination;
- trial and execution occurrence;
- invocation value and Request overrides;
- attached profiles and authority; and
- captured or sampled local inputs.

Explorer can offer a resolved facet by creating an ephemeral single-subject Compare preparation,
letting the user choose the Environment/occurrence context, calling
`CompareService.inspect_resolved_value`, and discarding the handle when the facet closes. It must
display the selected Environment, cell/trial/execution coordinate, and freshness alongside the
value. “Use in Compare” should transfer typed draft selections, not leak or reuse internal plans
without showing their review.

## 7. Compare draft and invocation contract

### 7.1 Intent

`CompareIntent` controls the preparation journey:

| Intent | Required topology |
| --- | --- |
| `ASSERTION` | Ad-hoc subjects plus one or more Environments and no comparisons |
| `COMPARISON` | Ad-hoc subjects plus baseline and one or more candidates |
| `EXECUTION_PROFILE` | A selected named ExecutionProfile; topology may be deliberately overridden |

The application rejects mismatched combinations. The TUI should change visible selectors by
intent, while preserving one Compare workspace and one preparation state machine.

### 7.2 `PlanningOptions`

`PlanningOptions` is the complete frontend override surface:

| Field | UI semantics |
| --- | --- |
| `execution_profile` | Named reusable profile or inherit/no profile |
| `subjects` | Replacement Request/Scenario IDs for ad-hoc execution |
| `environments` | Assertions-only Environment selection |
| `baseline` / `candidates` | Directed comparison selection |
| `matrices` | Matrix resource per binding and optional case filters |
| `trials` | Positive override or `None` to inherit |
| `maximum_attempts` | Positive override or `None` to inherit |
| `fail_fast` | `True`, `False`, or `None` to inherit |
| `budgets` | Explicit caller-requested planning limits |
| `value_resources` | Reusable Value resources for parameters/variables/secrets |
| `inline_values` | Inline expression objects; secret literals must be sealed |
| `environment_values` | Per-Environment sealed `$env` values |
| `requests` | Per-Request field expression edits |
| `profiles` | Assertion/diff/gate/safety/redaction/artifact selection overrides |

The TUI should preserve three states where the contract does: inherit, explicitly clear, and
replace. For scalar gate/redaction/artifact profiles, `ClearProfileSelection()` means detach;
`None` means inherit. For assertion/diff/safety collections, `None` means inherit and an empty tuple
means replace with none.

`MatrixOverride(binding, matrix, cases)` supports multiple matrices without a separate combine
operator. Attachments form the planner's Cartesian product. Empty `cases` means use the Matrix's
admitted cases; a non-empty tuple filters by exact case ID.

The workspace catalog exposes Matrix identities but not a universal typed case catalog. The plan
review exposes the admitted `MatrixAxisInspection.cases`. A practical UI may select Matrix
resources first, prepare, then offer the admitted case IDs for an optional narrower re-prepare.

Use `environment_value_override(environment, name, value)` to seal a typed string immediately.
Use `inline_value_override(...)` for inline namespace expressions so secret literals are sealed
before they enter `PlanningOptions`. Do not retain raw secret text in widget models after the DTO
has been constructed.

`RequestOverride` can replace method, URL/endpoint, query, headers, cookies, body/body type, auth,
stream, timeout, retry, and redirect expressions for one subject. Widgets should emit these through
a dedicated typed form adapter; they must not construct configuration AST nodes ad hoc across
screens.

### 7.3 Execution controls

`CompareExecutionControls` binds every effect-relevant choice to the preparation checkpoint:

- exact `ProductionExecutionSettings`;
- absolute artifact root or no root;
- per-run mutation approval;
- tool version.

Controls use structural equality. Passing different controls to preview, resolved inspection, or
start raises `PREPARATION_MISMATCH`. Any UI edit to controls must discard the old handle and
prepare again.

Use `production_execution_defaults_v1()` for non-persisted headless behavior and
`persisted_production_execution_defaults_v1()` when the TUI intentionally makes runs available in
Report. These are replaceable defaults, not hard product limits. Effective settings and limits are
shown by the review DTO; Settings should not mutate them as a side effect of changing theme or
density.

`artifact_root=None` means no root is selected; it does not silently choose a directory. For the
TUI's project-local default, resolve the successfully loaded project root and call
`initialize_project_artifact_root(project_root, Path(".comparo/artifacts"))`. That API accepts only
a relative contained path and creates private directories without following symlinks. For an
operator-selected absolute archive, require it to exist and use `validate_existing_artifact_root`.
Do not create or validate roots inside widgets.

`CompareExecutionControls` currently has no `ProductionRunAnnotations` field. An interactive
Compare run therefore cannot set title, notes, tags, VCS, or CI context through this service. Add
an application-level control and bind it into both review and start before designing editable run
annotation controls.

### 7.4 `ProjectPlanRequest`

One request contains config/additional source paths, optional external ExecutionPolicy path,
`PlanningOptions`, fresh invocation/run UUIDs, and project/policy load limits. Generate new IDs for
each new preparation intent; do not reuse a run ID for an edited draft.

`policy=None` selects Comparo's explicit trusted-local development policy; it does not mean that no
policy exists. The resulting authority is visible in `PlanReviewInspection` and must still be
shown like an externally loaded policy.

## 8. Compare preparation and review contract

Create one `CompareService` with caller-owned ceilings:

```python
service = CompareService(
    maximum_outstanding=2,
    maximum_inspections_per_preparation=1,
)
```

`prepare(ComparePrepareRequest(...))` returns either:

- `handle` + `PlanReviewInspection`; or
- diagnostics and no retained handle.

The handle is opaque, bounded, process-local, and one-shot. Never serialize it, show it, log it,
or treat its string as a plan ID.

### 8.1 Review information

`PlanReviewInspection` contains `plan`, `runtime`, `execution`, mutation approval, and tool version.
Its computed properties are authoritative:

- `execution_allowed`;
- `execution_blockers`; and
- `policy_advisories`.

Current blockers are mutation approval required, artifact persistence denied, artifact root
required, and artifact root denied. Advisories identify broad authority such as any host/port,
proxy DNS, any ambient environment variable, project file access, any remote schema, any secret
egress, or no SafetyProfile.

Do not convert an advisory into a blocker or ignore an application blocker because a button was
enabled locally.

### 8.2 Plan header and topology

`PlanInspection` provides:

- API version, run ID, invocation ID, ordering, and fingerprint;
- exact policy `ResourceCoordinate`;
- total cardinality;
- Environment count and number of write-capable subjects;
- one compact `PlanSliceInspection` per subject;
- effective planning budgets and provenance;
- compiled evaluation/profile information;
- sensitive-source mechanisms; and
- HTTP, value-source, secret-egress, remote-schema, and artifact authority.

`PlanCardinalityInspection` exposes cells, sampled trials, Environment executions, directed
comparisons, and maximum attempts. These numbers are exact and should drive the primary action
label, not estimates recomputed from visible selectors.

Each `PlanSliceInspection` exposes:

- subject revision;
- Matrix binding/resource/case axes;
- trials;
- selected Environments;
- directed baseline/candidate pairs;
- maximum attempts per execution;
- slice cardinality;
- resolved static HTTP method when known and `unsafe_method`; and
- attached assertion/diff/gate/safety/redaction/artifact profiles.

### 8.3 Limits and authority

`RuntimeLimitsInspection.limits` is a flat typed list of section/name/value/unit/denominator rows.
The review UI can group by section and format ratios when `denominator != 1`. Never silently apply
different settings after this list was displayed.

The authority projections answer design-relevant questions:

| Projection | Displayable facts |
| --- | --- |
| `PlanHttpAuthorityInspection` | Schemes, methods, hosts, ports, address classes, proxy DNS, mutation approval |
| `PlanValueAuthorityInspection` | Ambient names, project files, remote-schema sources, secret-egress rules |
| `PlanArtifactAuthorityInspection` | Whether persistence is allowed and selected root is present/authorized |
| `PlanValidationInspection` | Assertion/diff rule counts and exact gate requirements/thresholds |
| `SensitiveSourceInspection` | Safe mechanism categories and owning Environment, never values |

`clipboard_summary(review)` produces a constant-size, secret-free summary with a fingerprint and
human-ready count labels. Use it for “Copy plan summary”; do not serialize the arbitrary-sized
review object to the clipboard.

Plan validation exposes rule counts and gate details, not a pre-run list of effective assertion or
diff rule IDs/evidence locations. The complete rule-centric UI becomes available from terminal
`ResultFact` objects. A rich pre-run Rules inspector would need another bounded application
projection.

### 8.4 Handle lifecycle

| Event | Required action |
| --- | --- |
| Draft or controls change | `discard(old_handle)`, then prepare again |
| Preview/inspection starts | Keep handle; disable start or show busy state |
| Start succeeds | Handle is consumed; store the returned session |
| User abandons ready screen | `discard(handle)` |
| Workspace/backend closes | `close()`, then `await aclose(timeout=...)` |

`start` fails with `PREPARATION_BUSY` while an inspection is active,
`PREPARATION_BLOCKED` when review blockers remain, and `PREPARATION_MISMATCH` if controls changed.
Cancellation of a preview/inspection may retain its lease until cleanup finishes; the handle can
remain busy briefly after the caller task is cancelled.

## 9. Resolved value inspection

### 9.1 Target

`ResolvedValueInspectionTarget` contains:

- exact resource coordinate and non-empty structural `field_path`;
- canonical execution ordinal and expected Environment coordinate;
- projection limits;
- `start_node`, `maximum_nodes`; and
- optional `expected_projection_fingerprint` for continuation pages.

The execution ordinal comes from the admitted plan's canonical order. The service verifies that
the ordinal, subject, Environment, and resource revision agree; the TUI must not guess or combine
coordinates from different reviews.

### 9.2 Result states

`ResolvedValueInspectionResult` is either an inspection or diagnostics. An inspection has one of:

| State | UI meaning |
| --- | --- |
| `present` | A public node window or one collapsed masked node is available |
| `missing` | The selected optional value does not exist; no error code |
| `unavailable` | An authorized source did not provide a value; closed code present |
| `blocked` | Policy/authority prevented resolution; closed code present |
| `error` | Resolution could not complete; closed code present |

For present values, show `kind`, provenance, freshness, and paging state. `ResolvedValueNode`
contains pre-order ordinal/path/kind, presentation format, bounded scalar preview, original byte
count when meaningful, child count, truncation, and masking flag.

Sensitive aggregates collapse rather than exposing a navigable child tree. The top-level
`taint_sources` contains only bounded categories such as environment, file, generated, provider,
runtime, secret, or YAML. `omitted_taint_origin_count` reports origins not classified within the
caller budget. It is not a secret count.

Continuation rules:

1. First page uses `start_node=0` and no expected fingerprint.
2. Subsequent page uses `start_node=previous.next_node` and the exact previous
   `projection_fingerprint`.
3. A `resolution.inspect.projection-changed` diagnostic means discard every accumulated page and
   restart; it does not prove that a secret changed.

## 10. Sanitized request preview

Use `CompareService.preview_requests` with `start_ordinal` and `maximum_previews`. The result
contains:

- successful previews in increasing canonical ordinal order;
- total planned Environment executions;
- a `next_ordinal` cursor; and
- per-occurrence or whole-operation diagnostics.

Each `RenderedRequestPreview` provides execution/trial/cell IDs, subject and Environment revisions,
method, sanitized URL (including query), ordered headers with redaction flags, always-redacted
cookies, and body omission metadata.

Timeout, retry, redirect, TLS, proxy, and other transport-policy values are reviewed through plan
and runtime settings; they are not repeated on each request preview DTO.

Important current limitation: the offline preview composition does not inject the production body
materializer/authentication finalizer. Body-bearing or typed-auth requests can therefore return
per-occurrence preparation diagnostics instead of a full preview. The production execution path
does support bodies and authentication. The UI must call this “sanitized request preview,” show
partial diagnostics, and not promise exact wire bytes for every request. Authenticated post-run
request metadata and cURL come from the artifact.

The preview performs no DNS or network I/O, but it can read authorized dotenv/project files and
generate inspection values within limits. It must therefore be cancellable and labelled with the
same freshness caveat as resolved inspection.

## 11. Live execution contract

`CompareService.start` returns `ExecutionSession` and consumes the preparation. Register that
session with `RunService`; the session owns one production task, one cancellation source, and one
latest-value mailbox, while the registry owns bounded discovery of current/recent terminal runs.

### 11.1 Snapshot fields

`ExecutionProgressSnapshot` provides:

- monotonic frontend `revision` and stable run ID;
- phase: preparing, running, finalizing, cancelling, finished, or cancelled;
- total executions and comparisons;
- queued, started, and finished execution counts;
- finished comparison count and `active_executions`;
- last runtime sequence/kind/occurrence IDs;
- bounded occurrence rows, recent events, and assertion/diff facts;
- counts of omitted occurrences/facts and incomplete fact scopes; and
- terminal `ExecutionFinalSummary` when an artifact exists.

`LiveOccurrenceProgress` provides occurrence kind/ID, queued/running/finished state, terminal
outcome, closed failure stage/code, observer timestamps, computed elapsed nanoseconds, and the
typed `PlanOccurrenceInspection` or `PlanComparisonInspection` coordinate. A running table can
therefore label Request, Environment, Matrix case/cell, trial, and baseline/candidate sides without
joining against presentation fixtures.

`LiveRuleFactProgress` provides bounded value-free assertion/diff facts: owner, engine, authored
and effective rule IDs, severity, outcome, and aggregate metric counts. It intentionally excludes
evidence values. Gate verdict remains terminal-only in `final_summary`; a TUI must not reduce a
partial fact window into a provisional gate.

`recent_events` is a bounded recent window, not an audit log. Snapshots are coalesced: a slow UI
can skip intermediate revisions by design. The TUI should update rows by `(kind, occurrence_id)`
and use aggregate counters as authoritative.

### 11.2 Consumption pattern

```python
session = compare.start(handle, controls)
run = runs.register(session)
snapshot = runs.inspect(run).snapshot

while (newer := await runs.after(run, snapshot.revision)) is not None:
    snapshot = newer
    render_latest(snapshot)

access = await runs.result(run)
if access.result is not None and access.result.artifact is not None:
    results = project_results(access.result.artifact)
else:
    render_diagnostics(access.diagnostics)
```

`after()` returning `None` means no more progress, not necessarily success. Always inspect the
terminal `RunResultAccess`. Live progress never replaces artifact-owned assertion, diff, or gate
facts.

On successful artifact compilation, `ProductionExecutionResult.artifact` is available even when
persistence failed. Its optional persistence outcome separately reports `persisted`, `failed`,
`interrupted`, or `commit-uncertain`; only `persisted` has a bundle ID. Show result analysis from
the artifact regardless, refresh Report only after confirmed persistence, and treat
`commit-uncertain` as an explicit storage warning rather than assuming either success or failure.

`RunService.cancel()` is first-wins and returns whether cancellation was newly accepted.
`result()` is shielded from accidental caller cancellation. `RunService.close()` cancels active
runs, waits for cleanup, and releases the registry during backend shutdown.

## 12. Final result contract

Call `project_results` for both a live run's sanitized artifact and a verified stored artifact.
`ResultProjection` is the sole shared model for Compare results and Report details.

`project_results` eagerly contains every manifest-resident execution, comparison, and fact for
callers that have admitted that manifest size. For large runs, use `ResultPageQuery` through
`RunService.result_page` for retained terminal sessions or `read_archived_result_page` for stored
manifests. Executions, assertions, comparisons, and diffs have independent offsets and tagged page
types. Derived analysis lenses remain separately windowed over an eager `ResultProjection`.

### 12.1 Summary

`ResultSummary` includes:

- artifact/run/invocation/project identity and creation time;
- completion kind, instant, reasons, and optional failure stage/code;
- artifact-owned gate verdict/profile/reasons/coverage/threshold assessments;
- exact execution, comparison, assertion, and diff metrics; and
- artifact capture coverage-issue count.

Do not derive the gate badge from visible facts; omitted evidence or facts may exist and the gate
has independent completeness semantics.

### 12.2 Execution and comparison rows

`ExecutionResultRow` provides canonical ordinal, subject, Environment, Matrix coordinates,
cell/trial IDs, outcome/failure, aggregate timings, assertion/comparison references, retry attempts,
exchanges, and retained HTTP message references.

`ComparisonResultRow` provides one directed baseline/candidate comparison with its outcome,
unavailable side references, and diff references. Baseline and candidate are execution references;
follow them through the projection rather than pairing rows by list position.

### 12.3 Facts and evidence

`ResultFact` provides engine, rule revision, authored/effective rule IDs, severity, artifact-owned
outcome, exact counts/coverage, bounded evidence, omitted-evidence count, and structured issues.
Evidence contains a normalized location plus sanitized named `ArtifactValue` instances.

Use `result_value_preview(value, maximum_characters=...)` for bounded scalar/structural evidence
labels. Do not JSON-encode arbitrary evidence into a table cell or use Rich markup on its content.

### 12.4 Result pivots

The five analysis functions consume the same projection and a positive
`ResultProjectionWindow(maximum_items=...)`:

| Function | Product | Exact meaning |
| --- | --- | --- |
| `project_issues` | `IssueAggregate` | Groups adverse fact outcomes by rule/severity/outcome |
| `project_requests` | `RequestAggregate` | Groups execution outcomes by Request |
| `project_rules` | `RuleAggregate` | Groups assertion/diff applications by effective rule |
| `project_fields` | `FieldPathAggregate` | Groups retained evidence locations and bounded samples |
| `project_timeline` | `TimelineEvent` | Deterministic causal artifact order |

Each returns `AnalysisProjection(items, total, omitted)`. Passing `selected_reference` ensures an
off-window selected item is retained by replacing the last visible item. This is selection
preservation, not cursor paging.

Design limitations that must be labelled honestly:

- Requests aggregates do not include comparison verdicts and do not mean “validation passed.”
- Fields are evidence-location aggregates, not a structural JSON diff tree.
- Timeline is causal manifest order, not wall-clock or network timing order.
- Issues intentionally exclude successful/equivalent/skipped facts.

### 12.5 HTTP topology and timings

An execution contains canonical attempts; an attempt contains canonical redirect exchanges. The
last attempt/exchange is marked `terminal`. A terminal pre-send failure can have request metadata
without an exchange and is explicitly marked `pre_send_terminal`; never infer failure from the
last visible response row.

Request and response slots have closed states:

- `retained`: sanitized metadata exists;
- `omitted`: metadata was deliberately unavailable with a typed reason; and
- response-only `not-produced`: transport failed before a response existed.

Request metadata exposes method, sanitized URL, ordered headers/cookies, exact URL/header/cookie
redaction counts, and body-capture presence. Response metadata exposes status, protocol, ordered
headers/trailers, and body-capture presence. Marker text must never be counted to infer redaction;
the typed counts and flags are authoritative.

Timing entries are exact `name` plus either nanoseconds or a not-measured reason. Display attempt
and exchange timing groups as recorded. Do not sum phases into an invented total: spans can overlap,
connection phases may not run on reuse, and explicit elapsed measurements already exist.

## 13. Body inspection and cURL

### 13.1 Body coordinates

Each retained exchange request/response projects an `ArtifactBodyReference` containing:

- artifact API version and ID;
- SHA-256 of the exact canonical artifact result document;
- execution, attempt, and exchange IDs; and
- request or response side.

The body service checks every coordinate and the results digest against the authenticated bundle.
A reference from an older same-ID artifact cannot read a replacement artifact.

### 13.2 Body windows

Call `read_artifact_body_window` with absolute root, bundle ID, reference, read limits, byte offset,
and positive output-byte cap. `ArtifactBodyWindow` provides:

- source state and capture extent;
- omission/partial reason;
- media type and total bytes when a blob exists;
- returned offset/content and `next_offset`; and
- body redaction count.

Render capture state even when content is empty. Empty content may mean an empty complete body, an
omitted capture, or a zero-length page at the end; the metadata distinguishes them.

The byte API can drive hex/text views and bounded syntax highlighting, but a TUI must not parse a
partial window as a complete JSON document. Use `read_artifact_structured_body_page` for complete
structured access. It authenticates the same reference, caps the full document, then applies
caller-owned JSON-node/depth/pointer/preview and SSE-event/line limits. `StructuredJsonPage`
provides pre-order RFC 6901 nodes and subtrees. `StructuredSsePage` provides dispatched events;
supplying `sse_event_index` opens that event's JSON `data` as its own JSON page. Event alignment is
semantic and never assumes equal transport chunk counts.

Only call the body service when the projected message says `has_body_capture=True`. A retained
message reference can exist solely for metadata; without capture metadata the body service
correctly returns `BODY_NOT_FOUND`. A side-by-side body view can coordinate two independent
windows, but there is no backend full-body textual diff projection and the TUI must not present a
locally invented text diff as an evaluated Comparo fact.

Although body bytes passed the artifact sanitizer, they remain untrusted terminal content and may
contain controls, bidirectional characters, markup, or large lines. Apply terminal-control
containment, disable Rich markup, keep only bounded visible windows, and never put body content in
logs or crash reports. “Public” classification means the configured policy allowed capture; it is
not a universal assertion that the payload contains no personal or business-sensitive data.

### 13.3 cURL

`export_artifact_curl` accepts only an authenticated request reference. `CurlExport` provides:

| Field | UI behavior |
| --- | --- |
| `command` | Display and copy exactly this string |
| `executable` | Whether it can execute without replacing a placeholder |
| `exact_for_sanitized_capture` | Whether it reproduces the sanitized captured request |
| `warnings` | Values redacted, incomplete body, body file required, or non-text body |
| `redaction_count` | Typed producer-owned total; never scan marker text |

Label it “sanitized cURL,” show warnings before copy, and assign a discoverable key to the copy
button. Copying must be an explicit action. The command can contain non-secret URLs and business
payloads and can cause mutations if executed; Comparo should not execute it from this TUI contract.

## 14. Report contract

### 14.1 Archive query

`ReportArchiveQuery` supports page size, opaque cursor, bounded text search, completion, gate,
subject, and Environment filters. `query_report_archive` authenticates a caller-bounded archive,
sorts newest first, and returns:

- valid `ReportArchiveEntry` rows;
- per-entry authentication failures;
- total matched valid entries; and
- opaque `next_cursor`.

Reset the cursor whenever a filter or page size changes. Do not decode it or create one locally.

Each query currently enumerates and authenticates up to `ReportArchiveLimits.maximum_entries`
before filtering and returning a page. The page bounds output, not the physical scan work. Debounce
filter input, use one exclusive archive worker, and show a loading state; cancelling a thread
worker can make its result stale but cannot interrupt an already running synchronous filesystem
scan.

An archive row can show bundle/run/invocation IDs, creation time, completion/gate, subjects,
Environments, counts, total detached blob bytes, coverage issues, duration, ExecutionProfile,
adverse assertion/diff counts, title/tags, and sanitized VCS/CI coordinates. Notes intentionally
require opening an authenticated result rather than appearing in compact archive rows.

Per-entry failures must remain visible as “N reports could not be authenticated” with optional
closed-code detail; a corrupt entry must not hide valid siblings.

Archive rows expose title, tags, VCS, and CI coordinates when another frontend supplied them.
After open, `ResultProjection.annotations` and every `ResultPage` expose sanitized title, notes,
tags, VCS, CI, and annotation redaction count. The compact archive DTO still omits notes so report
search pages do not duplicate free-form content.

### 14.2 Open and share results

Opening a row performs `verify_artifact`, then `project_results`. Report mounts the same result
components, pivot projections, body service, and cURL service as completed Compare. Read-only is a
frontend capability flag; it does not create a different interpretation of the artifact.

### 14.3 Export

`render_artifact_report` supports JSON, JUnit, SARIF, and Markdown. It verifies the complete
artifact and returns bounded bytes, media type, and extension. The save location is selected by the
frontend after rendering; the reporter does not authorize arbitrary filesystem writes for the UI.

### 14.4 Retention and restore

Retention is a two-step reviewed mutation:

1. `plan_report_retention` returns exact candidates, skipped corrupt entries, and fingerprint.
2. After explicit confirmation, `apply_report_retention` re-verifies the plan and moves candidates
   to a private trash namespace.

The outcome can partially succeed. Show trashed IDs and failures separately, then refresh the
archive. Trash is recoverable and does not reclaim disk space. `restore_report` authenticates the
restored bundle and rolls it back to trash if verification fails. Permanent purge is not available.

## 15. Screen-level information map

### 15.1 Explorer

| Surface | Primary contract | Information available |
| --- | --- | --- |
| Resource tree | `ProjectWorkspaceSnapshot` | Kinds, identities, names, descriptions, tags, source locations, counts |
| Overview | `ProjectResourceSummary` + outline | Metadata and secret-free kind-aware structure |
| Authored | `SourceWindow` | Masked/exact revision-pinned YAML with line paging |
| Effective | `EffectiveResourceWindow` | Compiled fields, links/composition, consumers |
| Resolved | ephemeral Compare handle + `ResolvedValueInspection` | One field, Environment/occurrence context, safe value nodes, provenance, freshness |
| Dependencies | `ProjectRelationship` | Inbound/outbound targets, paths, origins |
| Use in Compare | new `PlanningOptions` draft | Resource identity transferred without hidden execution |

### 15.2 Compare · prepare

| Surface | Primary contract | Information available |
| --- | --- | --- |
| Intent selectors | workspace catalog + `PlanningOptions` | Subjects, Environments, profile, baseline/candidates, Matrix resources |
| Advanced controls | planning DTOs | Cases, trials, attempts, fail-fast, budgets, values, Request/profile overrides |
| Readiness summary | `PlanReviewInspection` | Exact calls/cells/trials/pairs, methods, profiles, limits, authority, blockers/advisories |
| Request preview | `RenderPreviewWindowResult` | Sanitized per-occurrence method/URL/fields and diagnostics |
| Resolved inspector | `ResolvedValueInspectionResult` | Masked field values, provenance, source timing |
| Composition inspector | `RequestCompositionInspection` | Per-field layers, origin, merge/replace strategy, and winner |
| Rules | `PlanRuleWindow` | Compiled pre-run assertion/diff rules, selectors, operations, and origin |
| Copy summary | `PlanClipboardSummary` | Constant-size checkpoint identity and counts |

### 15.3 Compare · running

| Surface | Primary contract | Information available |
| --- | --- | --- |
| Summary strip | `ExecutionProgressSnapshot` | Phase and exact aggregate counters |
| Occurrence table | `LiveOccurrenceProgress` | State, subject/Environment/Matrix/trial or comparison coordinates, outcome, safe failure, and elapsed observer time |
| Recent activity | `LiveProgressEvent` | Bounded latest runtime events |
| Live facts | `LiveRuleFactProgress` | Bounded assertion/diff owner, rule, severity, outcome, and aggregate metrics with omission/incompleteness counts |
| Cancellation | `ExecutionSession.cancel` | First-wins request and cancelling phase |

There is no safe live raw request/response body or stream-chunk console. Keep those facets disabled
until a final sanitized artifact exists. Live rule facts are operational previews without evidence;
the gate and authoritative facts still come only from the terminal artifact.

### 15.4 Compare · results and Report · detail

| Facet/pivot | Primary contract |
| --- | --- |
| Summary | `ResultSummary` |
| Issues | `project_issues` |
| Requests | `project_requests` + execution rows |
| Rules | `project_rules` + facts |
| Fields | `project_fields` |
| Timeline | `project_timeline` |
| Request/response | attempt/exchange slots and metadata |
| Headers/cookies/trailers | ordered `ResultHttpField` / `ResultHttpCookie` tuples |
| Body bytes/text | message reference + `read_artifact_body_window` |
| JSON/SSE structure | message reference + `read_artifact_structured_body_page` |
| Evidence | `ResultFact.evidence` + `result_value_preview` |
| Related entities | `ResultCrossLink` / owner references |
| cURL | request body reference + `export_artifact_curl` |

### 15.5 Settings

Settings owns theme, density, default workspace, mouse preferences, and future key remapping.
Execution policy, project YAML, selected Environments, safety classification, capture limits, and
artifact persistence are execution/project concerns and must not be edited implicitly from this
workspace. Plan review can expose them read-only.

## 16. Concurrency, backpressure, and stale-response rules

### 16.1 Blocking versus async operations

Synchronous compilation, source inspection, artifact verification/reporting, archive queries, and
retention perform bounded local I/O and can still block a Textual event loop. Run them in thread
workers. Pure projection functions are safe on the UI loop for admitted bounded artifacts, though
large result projections may still be scheduled as CPU workers for responsiveness.

Cancelling a Textual thread worker does not forcibly stop the synchronous application call. Mark
its generation stale, keep one bounded exclusive slot per operation, and let the bounded call
finish. Never accumulate a new compilation or full archive authentication task for every
keystroke.

Async Compare inspection, body reads, cURL generation, and execution must run on the owning event
loop so cancellation and cleanup semantics remain intact.

`CompareService` is stateful and not documented as thread-safe. Serialize every method through one
frontend backend owner; never call `prepare`, `discard`, `start`, or `close` concurrently from
uncoordinated workers.

### 16.2 Recommended operation groups

Use one latest-wins worker slot for each semantic operation:

- project reload;
- source/effective resource page;
- Compare preparation;
- request preview page;
- resolved-value page;
- report query;
- report open;
- terminal/archive result page;
- selected body byte/structure page; and
- selected cURL export.

Execution is different: it is one durable session that survives workspace navigation. Do not make
it latest-wins merely because the user changed tabs.

### 16.3 Frontend generations

Application fingerprints protect source, plan, resolution pages, and artifact body references.
The TUI still needs a monotonically increasing generation per selection/filter operation. Before
applying a worker result, verify that its generation and selected coordinate still match. This
prevents a slow Request A response from replacing the visible Request B inspector.

### 16.4 Shutdown order

1. Stop accepting commands and cancel presentation workers.
2. Request cancellation of an unfinished `ExecutionSession` and await `close()`.
3. Discard any unused preparation handle.
4. Call `CompareService.close()` and `await CompareService.aclose(timeout=...)`.
5. Clear exact-source/body/cURL display buffers.

Cleanup timeout diagnostics are value-free and should be shown or logged by code only. Do not
force-close internal descriptors from presentation code.

## 17. Security rules for every component

- Render only application DTO fields documented for that component.
- Never store raw secret entry text after creating a sealed override.
- Never render `SensitiveValue`, live resolver output, wire requests, observations, runtime
  contexts, local storage paths, or arbitrary exception messages.
- Treat exact authored source as a separate unsafe mode, not a toggle that changes every view.
- Treat masked source, masked resolution, sanitized preview, sanitized artifact metadata, body
  windows, and cURL as independent projections with independent guarantees.
- Disable Rich markup for data-bearing text and contain terminal/bidirectional controls.
- Do not infer redaction from `<redacted>` or any marker literal; use typed flags/counts.
- Do not put exact source, bodies, cURL, evidence values, URLs, headers, or cookies into telemetry,
  debug logs, snapshot names, worker names, or exception text.
- Do not automatically copy anything to the clipboard. Copy plan summary, sanitized cURL, source,
  evidence, or bodies only from explicit user actions with appropriate disclosure labels.
- Keep caller-owned limits visible and stable across review/start. UI virtualization is not a
  substitute for backend paging and budgets.
- A sanitized/public response can still contain PII or business data. The TUI may display it by
  policy, but should not retain it beyond the needed view or advertise it as non-sensitive.

## 18. Facts the TUI must not infer

| Available fact | Forbidden inference |
| --- | --- |
| Masked `projection_fingerprint` | Whether undisclosed secret content changed |
| Request aggregate outcome counts | Overall assertion/diff/gate success |
| Timeline order | Wall-clock event time or network phase duration |
| Recent live events | Complete execution audit log |
| Missing response metadata | Response was empty; it may be omitted or not produced |
| `has_body_capture=False` | Body was empty; capture may be disabled or unavailable |
| Redaction marker text | A real redaction occurred |
| Visible evidence only | Complete evidence set; consult `evidence_omitted` |
| Visible analysis page | Complete pivot; consult `total` and `omitted` |
| Sum of timing phases | End-to-end response time |
| Prepared plan | Network authorization; final destinations and redirects are reauthorized |
| `CurlExport.command` | Exact original secret-bearing request |
| Stored run intent | A replayable invocation; Report is evidence, not traffic replay |

## 19. Current backend limitations that affect design

These are intentional or explicit current boundaries, not work for a widget to reconstruct:

- no stateful Scenario/step execution or capture production;
- no WebSocket, gRPC, browser automation, plugin host, or external LLM judge;
- no live response-body or stream-chunk viewer;
- live rule facts intentionally omit evidence and the gate is terminal-only;
- no generic project resource editor;
- no context-free whole-resource resolved view;
- no authoritative full-body visual text-diff projection;
- Fields analysis depends on retained evidence and is not a full response diff tree;
- eager `project_results` and its derived pivots still require a fully admitted manifest, while
  primary execution/assertion/comparison/diff collections also have authenticated page APIs;
- archive query paging still authenticates the bounded physical archive on each query;
- report history cannot reconstruct every live input for “run again”;
- remote schemas have no cache, integrity pin, or transitive external reference loader;
- local artifact persistence is POSIX-specific; and
- trash/restore is recoverable removal, not disk-space-reclaiming purge.

When a desired interaction crosses one of these boundaries, add a bounded application use case
first. Do not open core objects or artifact files from a widget as a shortcut.

## 20. Minimal end-to-end orchestration blueprint

```text
launch
  validate/initialize artifact root
  create ProjectWorkspaceRequest + CompareService + RunService
  inspect_project_workspace

Explorer highlight
  render ProjectResourceSummary immediately
  cancel stale detail worker
  lazily inspect effective/source page

Compare draft change
  discard prior handle
  construct PlanningOptions + controls + fresh UUIDs
  prepare
  render diagnostics OR retain handle + review

Compare inspect
  preview/list rules/occurrences/composition and/or inspect resolved values through retained handle
  maintain fingerprinted page state

Compare start
  start consumes handle and register session in RunService
  consume latest registry progress snapshots with typed occurrence coordinates and live facts
  allow first-wins cancellation
  await terminal ProductionExecutionResult
  page terminal results or project_results from artifact

Report refresh/open
  query_report_archive in bounded pages
  page selected authenticated manifest or verify full artifact
  project_results when derived analysis lenses are required

Shared result detail
  navigate with ResultReference and ResultCrossLink
  derive bounded pivots
  lazily read authenticated body byte or structured JSON/SSE pages
  lazily generate sanitized cURL

shutdown
  close RunService (cancels and drains active sessions)
  discard prepared handle
  drain CompareService
```

This blueprint keeps the four workspaces visually independent while ensuring Explorer resolved
inspection, Compare execution, and Report analysis all use the same reviewed backend mechanics and
the same safe result vocabulary.
