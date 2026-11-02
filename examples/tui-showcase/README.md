# TUI showcase

This deliberately dense local project exercises the current TUI and its awkward states, not only
the happy path. It contains 45 non-Project resources (46 Explore entries including the Project),
17 Requests, six ExecutionProfiles, all five implemented request-body lowering modes, four
Matrices (two attached to executable Requests and two reserved for the Scenario), local/inline/remote Schemas, reusable values, dotenv and file inputs, profiles, an
intentionally unavailable credential, and one future Scenario.

Start the server and TUI from the repository root:

```console
uv run --frozen python examples/support/demo_server.py
uv run --frozen comparo tui --config examples/tui-showcase/comparo.yaml
```

## Suggested journeys

### Explore

Use `o` for Overview, `s` for masked source, `g` for dependencies, and `r` to switch the Overview
between raw and masked-resolved values. `Space` changes the shared execution Environment while
an Environment is selected, and `h` runs its declared health check when available. Request
Overview uses stable URL, Query, Headers, Cookies, Auth, Body, Runtime, and Contracts tabs; use
left/right or the mouse to move between them. The active tab survives `r` and selection changes.
Select a Request or ExecutionProfile and press `c` to populate Launch. This transfers identity
and context only; preparation and execution remain explicit actions.

All other kinds use continuous documents rather than Request-style tabs. Explore the Scenario,
ExecutionProfile, AssertionProfile, DiffProfile, GateProfile, SafetyProfile, RedactionProfile, and
ArtifactProfile entries to inspect workflow topology, run selection, rule pipelines, gate
thresholds, implemented safety restrictions, unsupported/reserved profile fields, sanitization,
and artifact persistence in one scroll. `structured-response` demonstrates an object selector and
rule metadata; `asymmetric-migration` keeps baseline and candidate pipelines distinct. Scenario is
visibly marked as a future non-executable capability and attaches two Scenario-only Matrices so
its count is meaningful. ExecutionPolicy is deliberately absent because it is operator-owned and
loaded outside project discovery.

The Request catalog is intentionally broad:

| Request | Explorer case |
| --- | --- |
| `catalog` | two attached Matrices, reusable headers/query, local Schema, and Request-local diff |
| `health`, `release` | minimal reads and stable-vs-drifting response values |
| `all-inputs` | ordered headers, query, sensitive cookie, masked bearer auth, phased timeouts, exponential retry, and redirect policy |
| `json-payload` | typed parameters inside a structured JSON body |
| `text-payload` | explicit text lowering and interpolation |
| `form-payload` | form body lowering |
| `multipart-payload` | text and base64 file parts |
| `bytes-payload` | bounded `$file` read with explicit bytes conversion |
| `streaming-events` | protocol auto-detection plus semantic JSON-SSE projection that ignores chunk boundaries |
| `absolute-health` | absolute URL routing instead of an Environment-relative endpoint |
| `ordered-occurrences` | duplicate query/header names, a bare query value, and API-key auth |
| `directive-inputs` | `$env`, explicit integer conversion, `$file`, `$from`, `$optional`, `$param`, and interpolation |
| `inline-contract` | inline response Schema and inline assertion profile |
| `remote-schema` | public remote Schema reference, deliberately not executed automatically |
| `request-local-policies` | schema-supported Request-local safety/redaction/artifact attachments; currently tagged `unsupported-runtime` |
| `unavailable-credential` | intentional required `$env` failure for the resolved error state |

The fake `demoToken` and `releaseToken` values are committed only to exercise secret masking; they
are not credentials. Resolved inspection must display them as `<masked>`. The three `.values` files demonstrate isolated declared
dotenv namespaces and contain public fixtures only. The remote Schema remains unbound because
network acquisition requires an explicit safety decision.

### Launch

Choose **Run an execution profile**. Ctrl+R prepares, Ctrl+X runs and Ctrl+K cancels:

- `matrix-release` creates 8 logical cells, 16 Environment executions, and 8 comparisons;
- `repeated-matrix-release` keeps 8 unique cells and uses two trials: 16 trial occurrences,
  32 Environment executions and 16 comparisons;
- `candidate-smoke` runs assertions without a baseline/candidate comparison;
- `streaming-chunks` compares concatenated semantic text across two versus three SSE records;
- `read-routing` groups relative routing, absolute routing, and an inline response contract;
- `launch-review` combines eight catalog cells with health, release and inline-contract Requests:
  11 logical cells, 22 Environment executions and 11 comparisons.

The `catalog` Request attaches `audiences` and `locales` simultaneously. The planner forms their
Cartesian product, while `$matrix` resolves `region`, `plan`, and `locale` inside a reusable query
Value. Every response is first validated against `Schema/catalog-response`. Only the `eu` + `pro`
audience differs between baseline and candidate, across its two locale cells, so the result
contains both clean and drifted comparisons.

### Reports and evidence

After running both a passing and failing profile, refresh Reports and navigate between the saved
artifacts. This is useful for judging list density, verdict semantics, cross-references, and the
shared result components without executing HTTP again. Analyze opens Issues, Requests, Rules,
Fields and Timeline with Alt+1–5 or bracket cycling. Press `i` for raw pages, JSON/SSE navigation
and sanitized cURL review. Fields contains retained rule evidence; it is not a JSON body browser.
The showcase artifact/redaction policy can omit inline evidence, producing an explicit empty Fields
state; use the capture inspector for retained body structure.

Settings persists theme, density, mouse, startup workspace and main command keys. Theme and mouse
changes apply next launch; density and shortcuts apply when saved.

The equivalent headless command is expected to exit with code `1` because two cells drift:

```console
uv run --frozen comparo exec matrix-release \
  --config examples/tui-showcase/comparo.yaml
```

The POST body-shape Requests are intentionally left out of default profiles. They can be planned
individually, while actual mutation execution still requires Comparo's explicit mutation approval.
