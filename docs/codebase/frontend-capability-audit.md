# Frontend capability audit

This audit answers one question: **can a replacement TUI implement the intended experience using
current core/backend contracts without inventing business logic?**

Status meanings:

- **Supported** — an end-to-end executable capability and safe application boundary exist.
- **Partial** — useful substrate exists, but the full user journey or projection does not.
- **Missing** — the required backend behavior or frontend-neutral contract does not exist.
- **Deferred** — intentionally outside the current product/release scope.

## Executive assessment

A replacement TUI can already provide a strong project navigator, plan/review flow, execution
launcher, coarse progress display, final assertion/diff analysis, report archive, and exports for
the current body-free Request subset. It cannot honestly deliver the complete intended product
yet. The largest blockers are backend/application gaps, not Textual work:

1. there is no raw authored-source inspection service or raw-vs-effective comparison;
2. the executable YAML subset excludes configured request bodies, most Environment transport
   settings, schemas, policy profiles, Scenarios, and advanced evaluation pipelines;
3. ad-hoc invocation cannot express ephemeral inline execution/rule/request overrides or select
   an individual Matrix case;
4. live progress is aggregate and outcome-free;
5. persisted reports omit response bodies by default and body navigation is digest-level only;
6. report archive metadata and paging/search are too small for a mature history experience.

The new TUI should build on the supported contracts and visibly mark unavailable features. It
must not emulate missing core behavior locally.

## Explorer workspace

| Capability | Status | Current contract | Exact limitation / work needed |
| --- | --- | --- | --- |
| Discover project sources | Supported | `ProjectWorkspaceRequest`, `inspect_project_workspace` | Bounded deterministic local discovery is available |
| List all compiled resources by kind | Supported | `ProjectWorkspaceSnapshot` | Includes metadata, fingerprints, source coordinate |
| Show metadata, tags, description | Supported | `ProjectResourceSummary` | Safe exact metadata is available |
| Show source file/document/line/column | Supported | `SourceCoordinate` | No source text reader exists |
| Show effective structural outline | Supported | `ProjectResourceOutline` | It is intentionally value-concealed |
| Show references/dependencies | Supported | `ProjectRelationship` | `$use` and composition links plus inverse `used-by` links |
| Show exact authored YAML (“Raw”) | Missing | None | Loader discards `ParsedDocument` objects; add a bounded source/AST inspection service with explicit secret handling |
| Show compiled/effective object (“Resolved” in old UI terminology) | Partial | Concealed outline only | No complete safe canonical projection; literals, remote URI, and file path are concealed |
| Raw vs effective side-by-side | Missing | None | Needs raw source service plus typed mapping/provenance between authored and canonical paths |
| Show runtime-resolved values | Partial | Request preview only | Resolution exists only in run context; no generic safe per-field resolved-instance/provenance DTO |
| Show “why this value won” | Partial | Plan field provenance; resolution provenance internal | Plan values can be explained structurally, but rendered request fields do not expose a provenance trail |
| Show dependency consumers | Supported | inverse `used-by` relationships | Only compiled resource relationships, not runtime reads |
| Search/filter resource list | Supported in frontend | Snapshot contains bounded full collection | No backend search required at current project limits; frontend may index safe fields |
| View Request method/shape | Partial | Outline exposes exact static method, concealed other strings | Exact endpoint/header/query values are not exposed until sanitized render preview |
| View Schema payload | Partial | Shape/links only | Local schema content is concealed; remote URI is concealed; response validation is not executable |
| View Matrix cases/axes | Partial | Structural outline and plan inspection | Outline conceals literal values; plan exposes selected coordinates/case IDs, not a rich authored Matrix DTO |
| View profiles/rules | Partial | Outline plus Plan profile/rule counts | Exact safe rule presentation is limited; operands may need runtime resolution |
| “Use in Compare” | Supported | resource identity -> `PlanningOptions.subjects` | Works for Request resources in current executable subset |

### Raw-source security requirement

Raw YAML may contain literal project secrets. A source viewer needs an explicit contract such as:

- a bounded source/document handle tied to one inspected workspace revision;
- separate modes for exact trusted-local view and mandatory-redacted view;
- no implicit copying into diagnostics, logs, command palettes, search indexes, or telemetry;
- line/window reads instead of retaining every source in frontend state;
- source fingerprint checks so displayed text matches the compiled revision;
- explicit handling of additional/out-of-root authorized sources.

Returning `ProjectSourceSet` or filesystem paths directly to the TUI would violate the current
boundary.

## Compare: target construction and plan

| Capability | Status | Current contract | Exact limitation / work needed |
| --- | --- | --- | --- |
| Assertions-only run across Environments | Supported | `CompareIntent.ASSERTION`, `PlanningOptions.environments` | Request subset only |
| Baseline-to-many comparison | Supported | `CompareIntent.COMPARISON`, baseline/candidates | Baseline executes once per cell/trial |
| Run an ExecutionProfile | Supported | `CompareIntent.EXECUTION_PROFILE` | Invocation overrides are limited to `PlanningOptions` |
| Select one or more Requests | Supported | `subjects` | Scenario selection plans but is rejected before production execution |
| Override selected Environments | Supported | environments or baseline/candidates | Exact topology validation is built in |
| Override Matrix attachments | Supported | `MatrixOverride(binding, matrix)` | Replaces attachment mapping; cannot select individual cases |
| Use two or more Matrices | Supported | multiple attachment bindings | Their cases combine by Cartesian product; ambiguous shorthand fields fail |
| Override trials, attempts, fail-fast | Supported | `PlanningOptions` | Typed positive/boolean validation |
| Override planning budgets | Supported | `BudgetOverride` | Effective external policy may cap them |
| Supply scoped values from reusable Value objects | Supported | `ValueResourceOverride` | Parameters/variables/secrets; global or Environment-specific |
| Override the `$env` namespace from the TUI | Missing | None in `PlanningOptions` | Env comes from declared inline/dotenv sources and optional process capture; invocation Value overlays exclude the env namespace |
| Supply an explicit ambient-env snapshot to `CompareService` | Missing | Lower execution API accepts one; Compare start does not | `CompareService.start` passes `None`, which asks the adapter to capture the policy-filtered process environment |
| Supply arbitrary inline values in a session | Missing | None | Current frontend options require a compiled reusable `Value` resource |
| Ephemeral inline ExecutionProfile | Missing | None | Only a compiled named profile can be selected |
| Patch one Request field for this run | Missing | None | No invocation-level method/URL/header/query/body patch contract |
| Inject a previous step value | Deferred | step scope and namespace modeled | Scenario execution/captures are not implemented |
| Select assertion/diff/gate profiles ad hoc | Missing | None in `PlanningOptions` | Attachments come from compiled defaults/profile/subject composition |
| Author/edit a temporary rule inline | Missing | None | Inline definitions inside project YAML can compile; UI-session inline rules cannot be supplied |
| Execute one selected Matrix case | Missing | None | Planner accepts a Matrix definition, not a case filter |
| Preview exact topology/cardinality | Supported | `PlanReviewInspection` | Rich, safe, and frontend-neutral |
| Explain precedence and authority | Supported | plan provenance and authority projections | Runtime rendered-field provenance remains absent |
| Preflight all unsupported runtime/evaluation features | Supported | Compare admission | Fails before network effects with safe diagnostic codes |
| Review and start the exact same controls | Supported | one-shot preparation handle | Control mismatch is rejected |
| Approve mutating methods | Supported | `approve_mutations` + policy | Approval is bound into reviewed start controls |
| Safely preview resolved requests | Partial | `render_prepared_requests` | Body-free only, whole-plan atomic, no per-field provenance |
| Copy safe cURL | Missing | Preview is a useful base | Needs a dedicated canonical serializer, shell dialect, omission/redaction markers, and clipboard policy |

### Meaning of “scoped inline execution”

The current backend supports a **scoped ad-hoc selection of existing resources**: pick Requests,
pick Environments, bind Matrices, set trials/budgets, and overlay reusable Value objects. It does
not support an **ephemeral inline execution definition** created only in the TUI. If the desired
feature includes editing a header, entering a one-off parameter value, changing a rule, selecting
one Matrix case, or injecting a temporary profile without writing YAML, a new application/domain
invocation overlay is required.

That overlay should compile into the same typed planner inputs and provenance model. The TUI must
not mutate `CompiledProject` objects or synthesize `PreparedRun` internals.

## Compare: executable HTTP and evaluation features

| Capability | Status | Notes |
| --- | --- | --- |
| Static HTTP method | Supported | Must be policy-allowed |
| Dynamic/resolved method | Missing | Explicitly rejected by capability preflight |
| URL/endpoint resolution | Supported | With Environment base URL and value expressions |
| Ordered duplicate headers/query | Supported | Domain/adapter and request provider support them |
| Request body | Missing end-to-end | Rich types exist in domain/adapter, but production configuration path rejects non-null body |
| Environment headers/cookies/auth | Missing end-to-end | Schema models them; production capability preflight rejects overlays |
| Environment timeout/TLS/proxy | Missing end-to-end | Adapter supports typed settings; project binding rejects overlays |
| Redirects/retries/deadlines | Partial | Adapter/runtime supports fixed configured behavior; project-level configuration is not bound |
| Ordinary JSON/text/binary response | Supported at transport | Current rule binder primarily exposes status and JSON body |
| SSE transport and bounded stream decoding | Partial | Runtime substrate exists; authored stream selector/diff binding is not exposed |
| JSON Schema response validation | Missing end-to-end | Local/remote Schema compiles; runtime rejects `responseSchema` |
| Remote public Schema fetch | Missing | Policy model exists; no production fetch/cache/validation composition |
| Basic assertions | Supported | equals/notEquals over status or whole JSON body |
| Full assertion operator catalog | Missing end-to-end | Pure kernel models more; application binder rejects it |
| Basic diffs | Supported | exact/ignore over status or whole JSON body |
| Ignore environment-specific field | Partial | Whole selected value can use `ignore`; path selection/exclusion is not bound through current YAML execution path |
| Nondeterministic LLM/stream semantic diff | Missing end-to-end | Pure diff pipeline has transform/reduce/pair seams; binder rejects them; external LLM judge is deferred |
| Custom gate thresholds/policy | Missing end-to-end | Current binder installs fixed strict thresholds from an empty named GateProfile |
| Project Safety/Redaction/Artifact profiles | Missing end-to-end | Compiled/planned handles exist; production preflight rejects attachments |

## Live run workspace

| Capability | Status | Current contract | Exact limitation / work needed |
| --- | --- | --- | --- |
| Start asynchronously | Supported | `ExecutionSession` | One active session object per run |
| Cancel safely | Supported | `session.cancel()` | First-wins run cancellation and cleanup |
| Overall phase and progress bars | Supported | coalesced `ExecutionProgressSnapshot` | Preparing/running/finalizing/cancelling/terminal plus counts |
| Show latest occurrence identity | Supported | latest execution/comparison IDs | No label lookup is included in snapshot; UI may map plan IDs |
| Per-execution queued/running state | Partial | raw events contain IDs, but session coalesces | Session does not expose event stream/history or occurrence map |
| Per-execution terminal outcome | Missing live | Available only in final artifact | Runtime event has no outcome/failure |
| Live assertion/diff verdict | Missing live | Available only in final artifact | No safe incremental fact event |
| Live duration/timing | Missing live | Available only in final artifact | No elapsed/start/finish data in progress DTO |
| Live request/response inspection | Missing | Only final sanitized artifact | Live observations/body handles must not cross frontend boundary |
| Live HTTP/SSE chunk timeline | Missing | Internal stream observations exist | No bounded sanitized application event projection |
| Bounded backpressure for slow UI | Supported | latest-only mailbox | A richer event model must preserve this property |
| Final result transition | Supported | `session.result()` | Returns sanitized `RunArtifact` or safe failure |

### Required live projection

A production-quality run screen needs an application-owned read model, not richer presentation
access to live domain objects. It should provide bounded coalesced occurrence rows keyed by stable
IDs, safe terminal outcome/failure codes, start/finish/elapsed timing, assertion/diff summary
counts, and a bounded recent-event window. Body content should remain unavailable until sanitized
capture exists. Slow consumers must not create an unbounded event queue.

## Final results and analysis

| Capability | Status | Current contract | Exact limitation / work needed |
| --- | --- | --- | --- |
| Run summary and gate | Supported | `ResultSummary` | Artifact-owned facts, no recomputation |
| Execution rows with env/cell/trial/outcome | Supported | `ExecutionResultRow` | Includes timing, attempt/exchange counts |
| Assertion facts and evidence | Supported | `ResultFact` | Bounded sanitized evidence from manifest |
| Comparison rows with baseline/candidate links | Supported | `ComparisonResultRow` | Directed binary comparison semantics retained |
| Diff facts and evidence | Supported | `ResultFact` | Advanced authored diff pipeline not yet executable |
| Navigate cross-links | Supported | `ResultReference` / `ResultCrossLink` | Stable frontend routes |
| Issues lens | Supported | `project_issues` | Groups non-success facts |
| Requests lens | Partial semantic scope | `project_requests` | Execution outcomes only; do not label as overall validation verdict |
| Rules lens | Supported | `project_rules` | Aggregated applications/outcomes/evidence |
| Fields lens | Partial semantic scope | `project_fields` | Evidence locations, not guaranteed structural JSON diff paths |
| Timeline lens | Partial semantic scope | `project_timeline` | Causal artifact order, not timestamped network timeline |
| Select a row and keep it in a bounded window | Supported | selected-reference-aware analysis windows | Caller owns maximum items |
| Request/response metadata detail | Supported in artifact | Requires a small application projection or careful artifact navigation |
| Full sanitized response body | Partial | `read_artifact_blob` by digest | Persistence defaults omit it; no result-reference-to-body viewer service |
| Baseline/candidate body side-by-side | Missing application service | Low-level blobs may exist | Need capture lookup, media type, decode/chunk, truncation, and pairing DTO |

## Report workspace

| Capability | Status | Current contract | Exact limitation / work needed |
| --- | --- | --- | --- |
| Persist safe local reports | Partial defaults | Local atomic artifact store | Persistence requires allowed root; persisted defaults omit response bodies |
| List report archive | Supported | `list_report_archive` | Bounded and newest first; marker/manifest/results metadata is authenticated and per-entry failures are isolated, but blob bytes are not read yet |
| Show run/gate/subject/env/count summary | Supported | `ReportArchiveEntry` | Enough for a basic archive table |
| Open/authenticate a report | Supported | `verify_artifact` / `inspect_artifact` | Complete blob authentication before use |
| Reuse final analysis screens | Supported | `project_results` + analysis projections | Same artifact model as immediate run result |
| Export JSON/JUnit/SARIF/Markdown | Supported | `render_artifact_report` | Bounded output |
| Read one sanitized blob | Supported low-level | `read_artifact_blob` | Entire blob returned as bytes under cap; no streaming viewer DTO |
| Search/filter reports | Partial frontend only | Full bounded listing | Only available fields; no backend query/pagination |
| Pagination/continuation | Missing | None | Listing has one maximum entry limit |
| Show duration | Missing archive metadata | Timings exist per execution/exchange inside artifact | No archive-level duration field |
| Show ExecutionProfile used | Missing | Not retained in artifact metadata | Plan provenance does not project this into archive |
| Show Git branch/commit/CI link | Missing | Not retained | Add optional invocation provenance |
| Notes/title/tags | Missing | Not retained | Requires artifact-compatible metadata evolution |
| Top issues in archive row | Missing | Can be derived only after opening full artifact | Add bounded summary fields or a separate index |
| Detect corrupt blob while listing | Missing by design today | Full verification occurs on open | Listing validates declarations/budgets but does not hash blob content |
| Run the same historical intent again | Missing | Artifact lacks the complete exact invocation inputs | A deliberate “run again” action needs a stored safe intent/reference contract; it is not traffic replay |
| Delete/prune artifacts | Missing application service | Store internals exist only for persistence/read | Needs explicit recoverability/retention authority |

## Settings workspace

TUI theme, density, keymap, layout, and accessibility settings are presentation concerns and do
not need core changes. Project or ExecutionPolicy editing is not a TUI Settings responsibility.
If a future UI edits project configuration, it should do so through a separate validated source
editing workflow, not mutate compiled objects.

## Recommended backend work before a feature-complete replacement TUI

### P0 — unblock truthful core journeys

1. **Source inspection contract:** bounded raw/redacted source windows, workspace revision handles,
   and authored-to-compiled path/provenance mapping.
2. **General request lowering:** bind configured request bodies and the intended Environment HTTP
   overlays into production request/transport settings.
3. **Evaluation binding expansion:** expose the accepted selector/assertion/diff/gate contracts,
   especially path exclusion/projection, reducers, pairing, and semantic comparisons.
4. **Inline invocation overlays:** typed ephemeral values, profile/rule attachments, request
   patches, and Matrix case filters with normal compile/plan provenance and policy checks.
5. **Rich safe live projection:** bounded per-occurrence state/outcomes/timing and recent events.
6. **Artifact capture configuration:** connect Redaction/Artifact profiles and implement the
   accepted default of available full sanitized response bodies within budgets.
7. **Evidence/body service:** navigate from result references to request/response captures and
   bounded decoded/chunked body views.

### P1 — make history and diagnostics production-grade

1. Add paginated/indexed archive queries and useful run metadata: duration, execution profile,
   optional VCS/CI provenance, and bounded issue summaries.
2. Add safe human-facing diagnostic/message catalog outside core error values.
3. Add partial/lazy request preview so a large plan can render one occurrence/window without
   resolving the entire plan atomically.
4. Add canonical redacted cURL export after general request-body support is defined.
5. Complete remote/local response-schema authorization, loading, caching, and evaluation.

### Deferred — do not block the next TUI

- stateful Scenario execution and step captures;
- external/plugin capability packaging and isolation, including an LLM judge;
- WebSocket and gRPC;
- browser automation, which is outside Comparo's scope;
- community plugin UX before the capability trust model is decided.

## Contract for the next TUI implementation

The replacement TUI should:

- depend on `comparo.application` DTOs/services and `comparo.reporters`, not current TUI code;
- label compiled structure, plan-effective values, sanitized request preview, and persisted
  observations precisely instead of calling all of them “resolved”;
- derive availability from capability/preflight results and never show a control that can only be
  implemented by bypassing policy;
- keep secrets, raw observations, and adapter exceptions outside widget state;
- use stable resource/result references for navigation and selection;
- request bounded windows for derived collections and body content;
- treat the final `RunArtifact` as the single source of truth for verdicts and reports;
- preserve one reviewed-controls-to-one-start semantics;
- visibly explain partial/omitted capture and incomplete analysis rather than presenting it as
  equality or success.
