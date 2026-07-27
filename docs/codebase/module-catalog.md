# Non-TUI module catalog

This catalog covers the production Python source outside `comparo.tui`. `__init__.py` modules
primarily re-export their package's public contracts and are included with their package entry.

## Repository roots

| Path | Responsibility |
| --- | --- |
| `src/comparo/__init__.py` | Package metadata surface |
| `src/comparo/py.typed` | PEP 561 marker for typed consumers |
| `src/comparo/schemas/` | Generated project and ExecutionPolicy JSON Schemas |
| `pyproject.toml` | Packaging, dependencies, Ruff, mypy, pytest/coverage, import-linter contracts |
| `tests/` | Unit, integration, conformance, property, security, architecture, CLI, and example tests |
| `deprecated.ignore.folder/` | Quarantined prototype; excluded from distributions and not a current implementation source |

## `comparo.application`

The application package is the preferred frontend boundary. It composes core contracts and local
adapters, catches unsafe exceptions, and returns immutable renderer-independent products.

| Module | Responsibility |
| --- | --- |
| `application.__init__` | Curated application public exports |
| `artifact_references` | Pure immutable request/response body coordinates shared without artifact I/O dependencies |
| `artifacts` | Artifact-root validation, authenticated inspection/reporting, digest reads, and body windows selected by pure result coordinates |
| `compare` | Bounded one-shot prepare/review/start service with caller-bounded retained plans and concurrent inspection leases |
| `count_presentation` | Shared exact/large count presentation contracts used by safe inspections |
| `curl_export` | Canonical bounded POSIX cURL from authenticated sanitized request captures |
| `diagnostic_catalog` | Static human guidance selected from closed diagnostics without interpolating live values |
| `diagnostics` | Runs offline sensitive-value rendering/serialization containment checks for the doctor command |
| `environment_binding` | Lowers selected Environment env sources and builds isolated run-start snapshots |
| `evaluation_assertions`, `evaluation_diffs`, `evaluation_items`, `evaluation_selectors` | Lower the typed assertion and semantic diff catalogs into pure-kernel inputs |
| `evaluation_binding` | Composes selectors, assertions, semantic diff pipelines, gates, schemas, and budgets into runtime evaluation |
| `execution_session` | Async session, cancellation, bounded occurrence/event progress, and terminal summaries |
| `inspection` | Safe coordinates and projections for diagnostics, plans, resources, policies, and artifacts |
| `plan_inspection` | Top-level safe `PreparedRun` inspection assembly |
| `plan_inspection_authority` | ExecutionPolicy, HTTP, values, filesystem, schema, and artifact authority projections |
| `plan_inspection_identity` | Safe definition/fingerprint/source identity helpers |
| `plan_inspection_profiles` | Assertion/diff/gate/safety/redaction/artifact attachment projections |
| `plan_inspection_topology` | Subject, Environment pair, Matrix, trial, slice, and cardinality projections |
| `plan_review` | Combines plan inspection, runtime limits, execution settings, mutation approval, and tool version |
| `plan_review_checkpoint` | Immutable plan-review checkpoint contracts |
| `plan_review_clipboard` | Safe plan-review copy/export projections; no live value access |
| `plan_review_limits` | Runtime/resource limit inspection helpers |
| `plan_sources` | Safe source/provenance mechanism projections used by plan review |
| `planning` | Translates primitive frontend choices to typed `RunInvocation` and invokes the planner |
| `preflight` | Project compilation, external policy loading, planning, and `PreparedProjectPlan` assembly |
| `production_execution` | Concrete production composition from prepared project through HTTP, evaluation, artifact compilation, and persistence |
| `profile_binding` | Restrictive Safety/Redaction/Artifact profile composition over caller settings |
| `project_outline` | Secret-free compiled resource structure and dependency/used-by graph |
| `project_workspace` | Explorer project compilation and safe resource/source snapshot facade |
| `remote_schemas`, `schema_validation`, `regex_validation`, `digest_validation` | Authorized bounded preparation of external/local evaluation capabilities |
| `render_preview` | Offline run-scoped sanitized request previews, including lazy ordinal windows |
| `resolved_inspection` | Handle-bindable, execution-exact masked expression inspection with bounded fingerprinted node windows and explicit local-source freshness |
| `report_archive` | Authenticated searchable cursor-paged report rows and metadata projections |
| `report_retention` | Reviewable fingerprinted recoverable report pruning and restoration |
| `result_analysis` | Bounded Issues, Requests, Rules, Fields, and Timeline projections |
| `results` | Body-free final artifact projection with stable references, cross-links, and explicit retry/redirect ordering and terminal topology |
| `runtime_capabilities` | Fail-closed admission of the production runtime's currently implemented configuration subset |
| `source_inspection` | Revision-pinned masked/exact YAML windows and bounded effective resource projections |

## `comparo.configuration` and `comparo.policy`

| Module | Responsibility |
| --- | --- |
| `configuration.__init__` | Public project-loading exports |
| `configuration.loader` | Project validation, deterministic discovery, revision-fingerprinted source catalog, bounded reads, and compiler composition |
| `configuration.yaml_parser` | Safe source-located JSON-compatible YAML subset parser |
| `policy.__init__` | Public operator-policy loading exports |
| `policy.loader` | Reads and parses the separate operator-owned ExecutionPolicy document before pure compilation |

## `comparo.core.configuration`

| Module | Responsibility |
| --- | --- |
| `configuration.__init__` | Compiler public API |
| `canonical` | Version-neutral linked graph: `CompiledProject`, resources, inline definitions, linked resources, and remote schemas |
| `compiler` | Registry construction, reference linking, Value expansion, canonicalization, and deterministic diagnostics |
| `directives` | Value directive compilation, interpolation, explicit conversions, secret sealing, and future-directive rejection |
| `migrations` | Configuration-version classification and migration boundary |
| `models` | Neutral source-located document/node and compilation result contracts |
| `schema_generation` | Generates the public project JSON Schema from version catalogs |
| `v1alpha1.__init__` | v1alpha1 frontend exports |
| `v1alpha1.ast` | Strict version-specific resource envelope/AST records |
| `v1alpha1.frontend` | Parses neutral documents into typed v1alpha1 resources and conversion inputs |
| `v1alpha1.operations` | Closed assertion/diff operation alias catalogs and shape checks |
| `v1alpha1.schema` | Resource field catalogs, inline/reference rules, directive positions, and schema model |
| `v1alpha1.semantic_validation` | Cross-field, resource-specific, and static execution-shape validation |

## `comparo.core.domain`: shared contracts

| Modules | Responsibility |
| --- | --- |
| `domain.__init__`, `enums`, `primitives`, `immutability` | Public domain surface, closed enums, validated scalar wrappers, and immutable-carrier helpers |
| `identity`, `definition_consistency` | Resource/definition/run/cell/trial/execution/comparison identities, fingerprints, and revision consistency |
| `metadata`, `provenance`, `diagnostics` | Resource metadata, value-free source lineage, and closed diagnostics |
| `values`, `value_kinds`, `value_references`, `value_egress` | Exact JSON/bytes values, namespaces/symbols, and controlled value egress |
| `sensitivity`, `credentials` | Sealed taint-carrying values and mandatory sensitive HTTP field catalogs |
| `expressions`, `expression_types`, `expression_analysis` | Typed unresolved expression tree and static dependency/scope analysis |
| `scopes`, `lifecycle` | Run/cell/trial/Environment/execution/step/attempt coordinates and completion/cancellation |
| `overrides`, `planning_inputs`, `planning_profiles`, `planning` | Typed edit plans, frontend invocation and profile overrides, effective topology, plan records, and cardinality |
| `budget_contracts`, `budget_origins`, `budgets` | Budget dimensions, authority/default/configuration origins, ledgers, exhaustion, and usage |
| `capabilities` | Namespaced/versioned capability identity and preparation contracts |
| `analysis`, `analysis_types`, `analysis_results`, `analysis_usage`, `analysis_budget_validation` | Separately authorized external-analysis request/result/usage seam; no plugin host implementation |

## `comparo.core.domain.http`

The HTTP domain describes finalized intent and immutable observations. It does not perform socket,
filesystem, decoding, or serialization I/O.

| Modules | Responsibility |
| --- | --- |
| `http.__init__` | HTTP domain public exports |
| `url`, `fields`, `media_types` | Typed absolute URLs, path/query/header/cookie occurrences, and media types |
| `auth`, `secret_egress` | Typed auth schemes and destination-bound sensitive egress evidence |
| `bodies`, `request_materialization` | Request body specifications and materialized repeatable sources |
| `body_lineage`, `requests` | Body provenance plus prepared/finalized wire request aggregates |
| `authorization`, `route_authority` | Exact request/destination/method/redirect authorization grants |
| `policies` | Redirect, cookie, retry, timeout, TLS, proxy, capture, classification, and related policies |
| `transport`, `pre_send` | Transport request/result and pre-send blocked/unavailable outcomes |
| `response_metadata`, `response_bodies`, `responses` | Response head, body capture/decoded-view handles, and exchange response records |
| `observations` | Attempt, exchange, execution, redirect, retry, and environment observation aggregates |
| `timings` | Explicit measured/unmeasured timing facts |
| `streams`, `stream_attempts`, `stream_items`, `stream_termination` | Record/SSE stream observations, reconnect attempts, items, controls, errors, and termination |
| `views` | Ordinary/stream body view handles used by selector snapshots |
| `attempt_transitions`, `transition_validation`, `lifecycle_validation` | Attempt/exchange state-machine validation |
| `exchange_validation`, `observation_lineage_validation` | Exchange and execution observation ownership/lineage invariants |
| `redirect_validation`, `retry_observation_validation` | Redirect/retry topology and policy evidence validation |
| `retry_timing_validation`, `timing_validation` | Aggregate timing conservation and retry timing invariants |
| `stream_count_validation`, `stream_item_validation` | Stream count/index and item-shape invariants |
| `stream_owner_validation`, `stream_terminal_validation` | Stream ownership and terminal truth invariants |

## `comparo.core.domain.selectors`

| Modules | Responsibility |
| --- | --- |
| `selectors.__init__`, `model`, `contracts` | Compiled selector aggregate, value/cardinality contract, and capabilities |
| `sources`, `static_fields`, `http_fields` | Request/response/body/stream/timing/static source vocabulary |
| `paths`, `jsonpath`, `jsonpath_derivation` | No-path, JSON Pointer, RFC 9535 JSONPath models and static derivation |
| `occurrences`, `items`, `locations` | Attempt/exchange/stream occurrence scope, selected items, and evidence locations |
| `values`, `result_values`, `results` | Selected value carriers and exhaustive selection outcome union |
| `body_view_semantics`, `selector_semantics` | Pure source/path/body-view compatibility and capability derivation |

## Assertion domain and engine

| Modules | Responsibility |
| --- | --- |
| `domain.assertions.__init__`, `model`, `rules`, `contracts` | Compiled assertion rules, profile/application contracts, severity, quantifier, and completeness |
| `domain.assertions.operators`, `extended_operators`, `regex` | Equality and broader typed operator specifications |
| `domain.assertions.match_results`, `results` | Per-match and aggregate assertion outcomes |
| `domain.assertions.evidence` | Bounded structured assertion evidence contracts |
| `assertions.__init__`, `engine`, `work`, `inputs`, `bindings` | Pure rule admission, prepared inputs, evaluation orchestration, and rule application |
| `assertions.predicates`, `numeric`, `digest` | Operator dispatch, exact numeric semantics, and digest helpers |
| `assertions.applicability`, `selection_outcomes` | Maps selection/completeness state to rule applicability/outcomes |
| `assertions.aggregation`, `accounting` | Match-to-rule reduction and operation/resource accounting |
| `assertions.evidence`, `evidence_size` | Evidence construction and byte/node budgeting |

## Diff domain and engine

| Modules | Responsibility |
| --- | --- |
| `domain.diffs.__init__`, `model`, `rules`, `contracts` | Compiled binary diff profile, rule, pipeline, and application contracts |
| `domain.diffs.operations`, `strategies` | Transform/reducer/pairing/comparator identities and typed built-in strategies |
| `domain.diffs.results`, `result_validation` | Per-pair/rule outcomes and validation |
| `domain.diffs.coverage`, `side_coverage_validation`, `stream_validation` | Side/pipeline completeness and stream coverage semantics |
| `domain.diffs.evidence` | Structured diff evidence contracts |
| `diffs.__init__`, `engine`, `runtime`, `admission`, `validation` | Pure pipeline admission, execution, cancellation/budgets, and result assembly |
| `diffs.inputs`, `operands`, `item_selection` | Prepared baseline/candidate operands and item extraction |
| `diffs.transforms`, `json_transforms`, `reducers` | Scalar/collection/JSON/stream transformations and reductions |
| `diffs.pairing`, `comparators`, `exact_numbers` | Singular/ordered/keyed/set-like pairing and comparison semantics |
| `diffs.aggregation`, `accounting`, `evidence`, `value_support` | Outcome reduction, budgets, evidence, and admitted value kinds |

## Gate domain and engine

| Modules | Responsibility |
| --- | --- |
| `domain.gates.__init__`, `model`, `types` | Gate profile, threshold, assessment, verdict, and reason contracts |
| `domain.gates.normalization`, `summaries` | Value-free normalization of assertion/diff/runtime results |
| `domain.gates.coverage_validation`, `outcome_validation`, `summary_validation` | Gate input/result invariants and lifecycle completion checks |
| `gates.__init__`, `gates.engine` | Pure threshold and coverage evaluation over normalized summaries |

## Shared evaluation

| Module | Responsibility |
| --- | --- |
| `evaluation.__init__` | Shared evaluator exports |
| `evaluation.fact_ids` | Deterministic assertion/diff fact identity derivation |
| `evaluation.value_semantics` | Strict cross-engine equality/type/number semantics |

## `comparo.core.selection`

| Modules | Responsibility |
| --- | --- |
| `selection.__init__`, `parser`, `compile` | Public selection API, selector syntax parsing, and compilation |
| `pointer`, `jsonpath_evaluator`, `json_runtime` | JSON Pointer/JSONPath execution primitives |
| `engine`, `source_evaluator` | Top-level selector evaluation and source dispatch |
| `request_evaluator`, `response_evaluator`, `http_evaluator`, `http_field_evaluator` | Typed HTTP request/response/status/header/query/cookie/trailer selection |
| `body_evaluator`, `json_evaluator`, `stream_evaluator` | Ordinary body, JSON, and stream/SSE selection |
| `snapshot` | I/O-free immutable observation/value snapshot consumed by selection |
| `results`, `semantics`, `errors` | Runtime results, source/path semantics, and closed failures |
| `accounting`, `match_accounting`, `limits`, `value_size` | Cumulative work/match/byte budgets and retained-value sizing |
| `timing_values`, `render` | Timing value conversion and safe canonical selected-value rendering |

## `comparo.core.planning`

| Module | Responsibility |
| --- | --- |
| `planning.__init__` | Planner public exports |
| `_canonical` | Strict access helpers over trusted canonical resources |
| `_errors` | Internal value-free planning problem carrier |
| `binding` | Structural precedence, topology, profile attachments, and value-layer binding |
| `matrices` | Axis/case compilation, include/exclude, product/zip/union, ambiguity checks |
| `budgeting` | Requested/capped/effective planning budget construction and admission |
| `fingerprints` | Stable secret-safe plan fingerprinting |
| `iteration` | Lazy canonical plan slice/cell/trial enumeration and occurrence IDs |
| `models` | Effective execution, Matrix recipes, plan slices, `PreparedRun`, and result contracts |
| `planner` | Pure orchestration from `CompiledProject` + invocation + authority to admitted plan |

## `comparo.core.policy`

| Module | Responsibility |
| --- | --- |
| `policy.__init__` | ExecutionPolicy public exports |
| `policy.models` | Immutable operator authority for planning, HTTP, values, filesystem, schemas, artifacts, and analysis |
| `policy.compiler` | Strict source document to typed policy compilation and diagnostics |
| `policy.schema_generation` | Generates ExecutionPolicy JSON Schema |
| `policy.trusted` | Explicit trusted-local default policy construction for local development paths |

## `comparo.core.resolution`

| Module | Responsibility |
| --- | --- |
| `resolution.__init__` | Resolution public exports |
| `models` | Environment snapshots, resolution targets/results, present-for-wire, and sanitizer snapshot contracts |
| `context` | Run-owned composition and lifetime of snapshots, providers, caches, registry, and cancellation |
| `resolver` | Pure recursive expression semantics, namespace lookup, fallback, optional, interpolation, and composite values |
| `provider_runtime` | Contains provider exceptions, validates output, adds taint, and maps canonical results |
| `ports` | File, external value, generation, YAML decode, and clock protocols |
| `cache` | Shielded single-flight effect cache by exact source/scope key |
| `scopes` | Declared lifetime to concrete consumer-coordinate projection |
| `snapshots` | Effective value layers, Matrix cell inputs, and Environment snapshot lookup |
| `conversions` | Explicit string/bytes/JSON/YAML/integer/number/boolean/duration/size conversions |
| `generation` | Deterministic and secure generation request semantics |
| `registry` | Run-owned sensitive-value registry and immutable sanitization snapshots |
| `provenance` | Value-free resolution source/attempt/configuration provenance |
| `control` | Run cancellation and resolution-use guards |
| `views` | `LiveValueResolver` and irreversible `MaskedValueResolver` over one shared engine |

## `comparo.core.runtime`

| Module | Responsibility |
| --- | --- |
| `runtime.__init__` | Runtime public exports |
| `models` | Lazy execution/comparison work, outcomes, records, progress events, and final `RunResult` |
| `ports` | HTTP driver, evaluation preparation, event sink, clock, and request provider protocols |
| `scheduling` | Bounded queue/workers, concurrency ledgers, fail-fast, cancellation, and deterministic retention |
| `request_preparation` | Lowers the current compiled Request subset with run-scoped resolution |
| `http` | HTTP execution driver: prepare, authorize, attempt, retry, snapshot, and normalize outcomes |
| `evaluation` | Applies prepared assertion/diff evaluators and gate in runtime order |
| `engine` | Top-level prepared-run lifecycle and event emission |
| `fake` | Programmable offline driver used for deterministic runtime testing |

## `comparo.core.artifacts`

| Module | Responsibility |
| --- | --- |
| `artifacts.__init__` | Artifact public exports |
| `model` | Independently versioned top-level sanitized `RunArtifact` |
| `common`, `values` | Stable artifact identities, failures, metrics, blobs, and serializable sanitized values |
| `http` | Artifact request/response/body capture, attempts, exchanges, and timings |
| `facts` | Artifact executions, comparisons, assertion/diff facts, evidence, and coordinates |
| `lifecycle` | Artifact completion, gate, summary, provenance, and coverage records |
| `policy` | Capture/persist/unsafe-source modes, limits, and redaction rules |
| `ports` | Body readers, sanitized blob spool/writer, persistence sink, and store outcome protocols |
| `sanitization` | Mandatory literal/header/query/cookie/body sanitization boundary and budgets |
| `capture` | Live HTTP driver decorator that creates sanitized capture batches and coverage issues |
| `fact_compiler` | Converts live evaluator facts/evidence to safe artifact facts |
| `compiler` | Combines prepared plan, `RunResult`, and captures into one validated artifact |
| `codec` | Deterministic bounded artifact encoding/decoding and schema-version validation |

## `comparo.adapters.values`

| Module | Responsibility |
| --- | --- |
| `values.__init__` | Value adapter public exports |
| `_filesystem` | Shared no-follow, containment, ownership, size, and descriptor helpers |
| `environment` | Dotenv/ambient/inline precedence and immutable Environment snapshot builder |
| `files` | Pinned project root and confined `$file` provider |
| `clock` | System run clock adapter |
| `generation` | Deterministic and secure generation providers |
| `yaml` | Safe inert YAML value decoder under explicit limits |

## `comparo.adapters.http`

| Module | Responsibility |
| --- | --- |
| `http.__init__` | HTTP adapter public exports |
| `preparation` | Policy-aware final destination/method/secret-egress authorization before transport |
| `finalization`, `materialization`, `wire` | Convert typed request intent to exact duplicate-preserving wire method/URL/headers/body |
| `network` | DNS resolution, IP classification, route pinning, and connection authority |
| `tls` | Explicit TLS context and identity isolation |
| `transport` | `httpcore` connection-pool adapter and aggregate exchange orchestration |
| `capture` | Bounded encoded/entity response capture and classification |
| `storage` | Run-owned memory-to-disk transient body store and repeatable reads |
| `ordinary` | Bounded ordinary text/JSON/body snapshot decoding |
| `streaming` | Bounded SSE/record decoding, JSON `data` policy, controls/errors, and termination |
| `redirect` | Redirect policy, method/body transition, cookie isolation, and reauthorization |
| `retry` | Idempotency-aware retry decisions and waiter |
| `timing` | Monotonic phase/deadline timing accumulation |

## `comparo.adapters.artifacts`

| Module | Responsibility |
| --- | --- |
| `artifacts.__init__` | Artifact adapter public exports |
| `spool` | POSIX temporary content-addressed sanitized blob spool with aggregate limits |
| `local` | Private atomic persistence, authenticated opening/listing, blob reading, and recoverable trash/restore moves |
| `memory` | In-memory sanitized blob target for tests/small compositions |
| `transient` | Reads live transient body handles for artifact capture before run cleanup |

## `comparo.reporters`

| Module | Responsibility |
| --- | --- |
| `reporters.__init__` | Unified report-format dispatch exports |
| `contracts` | `ReportFormat`, bounded rendered report, and closed reporter errors |
| `_presentation` | Shared deterministic safe artifact-to-presentation helpers |
| `json` | Canonical JSON artifact report |
| `junit` | CI-oriented JUnit XML projection |
| `sarif` | SARIF result projection |
| `markdown` | Human-readable Markdown summary |

## `comparo.cli`

The CLI is an adapter over application services. It is useful as an integration example but is
not the architectural source of truth for a replacement TUI.

| Module | Responsibility |
| --- | --- |
| `cli.__init__` | Public `main` entry point |
| `app` | Cyclopts command tree and lazy TUI launch dispatch |
| `contracts` | CLI-specific immutable parsed option/result contracts and exit categories |
| `loading` | Paths, policy/default settings, artifact roots, and project request assembly |
| `execution` | Headless prepare/execute/session orchestration over application services |
| `services` | Artifact/report/archive and other application service adapters |
| `rendering` | Rich terminal rendering of safe diagnostics, plans, results, and reports |
