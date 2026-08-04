# TUI showcase

This deliberately dense local project exercises the current TUI and its awkward states, not only
the happy path. It contains 40 non-Project resources (41 Explorer entries including the Project),
17 Requests, all five implemented request-body lowering modes, two independently attached
Matrices, local/inline/remote Schemas, reusable values, dotenv and file inputs, profiles, an
intentionally unavailable credential, and one future Scenario.

Start the server and TUI from the repository root:

```console
uv run --frozen python examples/support/demo_server.py
uv run --frozen comparo tui --config examples/tui-showcase/comparo.yaml
```

## Suggested journeys

### Explorer

Use `o` for Overview, `s` for masked source, `g` for dependencies, and `r` to switch the Overview
between raw and masked-resolved values. `Space` changes the shared execution Environment while
an Environment is selected, and `h` runs its declared health check when available. Request
Overview uses stable URL, Query, Headers, Cookies, Auth, Body, Runtime, and Contracts tabs; use
left/right or the mouse to move between them. The active tab survives `r` and selection changes.
Request selection and composition begin in Compare rather than through Explorer actions.

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

The fake `demoToken` is committed only to exercise secret masking; it is not a credential. Resolved
inspection must display it as `<masked>`. The three `.values` files demonstrate isolated declared
dotenv namespaces and contain public fixtures only. The remote Schema remains unbound because
network acquisition requires an explicit safety decision.

### Compare

Use Execution profile mode:

- `matrix-release` creates 8 logical cells, 16 Environment executions, and 8 comparisons;
- `repeated-matrix-release` doubles those counts with two trials;
- `candidate-smoke` runs assertions without a baseline/candidate comparison.
- `streaming-chunks` compares concatenated semantic text across two versus three SSE records;
- `read-routing` groups relative routing, absolute routing, and an inline response contract.

The `catalog` Request attaches `audiences` and `locales` simultaneously. The planner forms their
Cartesian product, while `$matrix` resolves `region`, `plan`, and `locale` inside a reusable query
Value. Every response is first validated against `Schema/catalog-response`. Only the `eu` + `pro`
audience differs between baseline and candidate, across its two locale cells, so the result
contains both clean and drifted comparisons.

### Report

After running both a passing and failing profile, refresh Report and navigate between the saved
artifacts. This is useful for judging list density, verdict semantics, cross-references, and the
shared result components without executing HTTP again.

The equivalent headless command is expected to exit with code `1` because two cells drift:

```console
uv run --frozen comparo exec matrix-release \
  --config examples/tui-showcase/comparo.yaml
```

The POST body-shape Requests are intentionally left out of default profiles. They can be planned
individually, while actual mutation execution still requires Comparo's explicit mutation approval.
