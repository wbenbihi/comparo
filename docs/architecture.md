# Architecture

> How comparo is put together: one engine, three front-ends, and a boundary the
> build enforces.

This document is for contributors and technically-minded users who want to
understand — or safely change — the codebase. It describes the shape of the code
as it exists, not aspirations. Every claim below is drawn from the source and the
project configuration.

## Table of contents

- [The big picture](#the-big-picture)
- [Package layout](#package-layout)
- [Dependency layers and the enforced contracts](#dependency-layers-and-the-enforced-contracts)
- [The by-reference resolution model](#the-by-reference-resolution-model)
- [The HTTP port](#the-http-port)
- [The `comparo.core` module map](#the-comparocore-module-map)
- [The front-ends](#the-front-ends)
- [Tooling and quality gates](#tooling-and-quality-gates)

## The big picture

comparo is built as a **hexagonal (ports-and-adapters)** application. A single
engine — the `comparo.core` package — contains all the logic: loading and
validating a project, resolving references and secrets, expanding matrices,
sending requests, asserting and diffing responses, running execution profiles, and
building and archiving reports. The engine knows nothing about *which* HTTP library
moves bytes on the wire, and nothing about *how* a human drives it.

Around that engine sit three front-ends — the **TUI**, the **CLI**, and the
**GitHub Action** — and a thin **adapters** layer that plugs a concrete HTTP
library and concrete report formats into the ports the core defines. The arrows
only ever point *inward*, toward the core; the core never imports a front-end or
an adapter.

```
   ┌────────────┐     ┌────────────┐     ┌──────────────────┐
   │    CLI     │     │    TUI     │     │  GitHub Action   │
   │  (typer)   │     │ (textual)  │     │   (action.yml)   │
   └─────┬──────┘     └─────┬──────┘     └────────┬─────────┘
         │                  │             shells out to
         │ import           │ import      `comparo diff`
         │                  │                      │
         └──────────┬───────┴──────────────────────┘
                    │ depend on
                    ▼
          ┌───────────────────────┐        ┌────────────────────────┐
          │     comparo.core      │        │    comparo.adapters    │
          │       (engine)        │◄───────│  HttpxClient           │
          │                       │ implement  built-in Reporters   │
          │  defines the ports:   │  ports │                        │
          │  HttpClient, Reporter │        │  (the only httpx import)│
          └───────────────────────┘        └────────────────────────┘
                    ▲                                  │
                    └────────── depends on ────────────┘
```

The GitHub Action is a *composite* action (`action.yml`): it installs comparo
with `uv` and shells out to `comparo diff`, so it depends on the CLI at the
process level rather than importing any Python. The CLI and TUI depend on the
engine and the adapters by import.

## Package layout

```
src/comparo/
├── core/        the engine — no HTTP library, no front-end knowledge
├── adapters/    implementations of the core's ports (httpx, reporters)
├── cli/         the Typer console front-end
├── tui/         the Textual terminal UI
```

`comparo/__init__.py` states the intent plainly: *"The public surface is the
`comparo.core` engine; the `cli` and `tui` packages are thin front-ends over it
and must never be imported by the core."*

## Dependency layers and the enforced contracts

The boundary above is not a convention that lives only in reviewers' heads — it
is checked mechanically by [import-linter](https://import-linter.readthedocs.io/)
on every push and pull request. Two contracts, declared in `pyproject.toml`,
define it.

**1. The layered contract.** Front-ends may depend on the core; the core depends
on nothing above it. Layers are listed highest-first, and a lower layer may never
import a higher one:

```toml
[[tool.importlinter.contracts]]
name = "Interfaces and adapters may depend on core; core depends on neither"
type = "layers"
layers = [
  "comparo.cli",
  "comparo.tui",
  "comparo.adapters",
  "comparo.core",
]
```

Read top-to-bottom: `cli` sits above `tui`, which sits above `adapters`, which
sits above `core`. So the CLI may import the TUI, the adapters, and the core; the
TUI may import the adapters and the core; the adapters may import the core; and
**the core imports none of them**. (The CLI does in fact import the TUI — its
`tui` command launches `ComparoApp` — which the ordering permits.)

**2. The forbidden contract.** The core must reach the network only through its
own port, never a concrete HTTP library:

```toml
[[tool.importlinter.contracts]]
name = "Core must not import an HTTP library directly"
type = "forbidden"
source_modules = ["comparo.core"]
forbidden_modules = ["httpx"]
```

Both contracts run under `root_package = "comparo"` with
`include_external_packages = true` (so the `httpx` ban can see the external
import). They are enforced in CI by the **Architecture contract (import-linter)**
step in `.github/workflows/ci.yml`, which runs `uv run lint-imports`; a violation
fails the build. Contributors run the same check locally (see
[CONTRIBUTING.md](../CONTRIBUTING.md)):

```console
uv run lint-imports
```

## The by-reference resolution model

comparo configuration is deliberately *unresolved* on disk. A `Request` does not
embed its API key or its shared request body; it references them. Resolution
happens in two clearly separated stages, and secrets are handled by taint rather
than by trust.

### Stage 1 — the loader keeps holes

`core/loader.py` loads every `*.yaml` object under a project root in
diagnostics-collecting passes, so one run surfaces every problem at once:

1. **Parse + envelope validation** — each document is parsed with `ruamel.yaml`
   and decoded into a typed model (`msgspec.convert(..., strict=True)`).
2. **Id indexing** — objects are indexed by `metadata.id`; a duplicate id or a
   second `Project` manifest is a diagnostic.
3. **Reference checking** — every `$ref`/`$val` target in the raw tree is checked
   against the known ids. A dangling reference is a *hard error* with a near-miss
   suggestion (`did you mean '…'?`), never a silent degradation. (A JSON-Schema
   `$ref` — one that starts with `#` or contains `/` — is the user's own payload
   and is left alone.)
4. **Profile-slot validation** — once the tree resolves cleanly, every profile
   attachment slot (a request's `diff`/`assert`, an `ExecutionProfile.profiles`,
   the project default `diff`, an `AssertionProfile.include`) is resolved through
   `refs.resolve_specs`. A slot that names a missing id, points at the wrong kind,
   or holds an invalid inline spec is a *hard error* — never a silently-empty rule
   set, which would pass every gate. A non-empty `spec.plugins` block is rejected
   here too, since the plugin system does not exist yet.

Crucially, the loader **does not fill** `$ref`, `$val`, or `${...}`. It leaves
them as holes in the tree. It only proves they *could* be filled. The result is a
`LoadedProject`: the manifest plus every object indexed by id.

### Stage 2 — the Resolver fills holes for one environment

`core/resolve.py` is the by-reference sink. A `Resolver` is bound to a
`LoadedProject`, one chosen `Environment`, and a `Sink`. Walking a request tree it
fills:

- `${NAME}` interpolation holes (delegated to `core/interpolation.py`), which
  support required (`${NAME}`), optional (`${NAME?}`), default
  (`${NAME | fallback}`), and typed-cast (`${NAME:int}`) forms;
- `$val` — inline a reusable `Instance` value;
- `$literal` — an escape hatch for a literal object;
- `$secret` / `$env` / `$file` — a secret reference.

It also injects matrix cases (see [`matrix.py`](#the-comparocore-module-map)) and
merges environment-level headers with request headers. The output is a
`ResolvedRequest` — concrete method, URL, headers, query, body — plus a
provenance `trail`.

### The two sinks: DISPLAY and EXECUTE

The same walk produces two different results depending on the sink:

| Sink | `mask_secrets` | What secrets become | Used by |
| --- | --- | --- | --- |
| `Sink.DISPLAY` (default) | `True` | the mask `••••••` | Explorer, `render`, `curl` preview, export |
| `Sink.EXECUTE` | `False` | the **real** value, resolved lazily | the HTTP engine (`execute.py`, `health.py`) |

In the **DISPLAY** sink, a secret is replaced by the mask string and its position
is recorded in the provenance trail, so a human can see *that* a secret is there
without ever seeing its value. In the **EXECUTE** sink, `core/secrets.py`'s
`ExecuteSecrets` resolves the real value on demand from its source — `$env`,
`$literal`, `$file`, or an ordered `from` fallback list — and **caches** it. Lazy
resolution means a secret that is declared but unavailable only fails a run if
something actually uses it.

### Taint-based masking

Masking is not a string search bolted on at the end; it is a property carried by
every resolved value. `core/provenance.py` defines an `Origin` enum
(`LITERAL`, `VARIABLE`, `SECRET`, `INSTANCE`, `MATRIX`, `FILE`) and a `tainted`
property: a value whose origin is `SECRET` or `FILE` "must be masked and never
persisted." Two consequences follow:

- **Interpolation is secret-first.** In `interpolation.py`, a `${NAME}` whose
  name is a secret in the environment resolves as a secret *even when written as a
  plain variable* — so a secret can never be surfaced by aliasing it.
- **A string-match backstop scrubs echoes too.** Drift and assertion *details*
  are built from real EXECUTE-sink responses, and a server can echo a secret
  straight back into a body it drifts on. So `core/redaction.py` defines a
  `Redactor` — built over every environment's resolved secret values — that masks
  any known secret value (and, since a server can echo a secret as a JSON *key*, any
  key or field path) anywhere it appears in a string. It is applied at **every sink
  that leaves the process**: the `diff`/`run`/`exec` console output, the built-in
  reporters (`build_report`), `core/export.py`'s JSON run export (`runs/*.json`), and
  the saved `.reports/` archive. `core/export.py` additionally renders request values
  through the DISPLAY sink, so declared secrets arrive already masked. No real secret
  value survives into a report, a saved run, or the screen.

## The HTTP port

The core never imports `httpx` (the second contract forbids it). Instead,
`core/http.py` defines the **port** the engine sends through — a `Protocol`:

```python
class HttpClient(Protocol):
    async def send(self, request: ResolvedRequest, timeout: TimeoutBudget) -> HttpResponse: ...
    async def aclose(self) -> None: ...
```

Alongside it live the wire-level value types the engine speaks in: `HttpResponse`
(status, headers, body bytes, elapsed ms), `TimeoutBudget` (per-phase timeouts,
merged request-over-environment), and `HttpError` (a transport failure that
adapters raise and the engine catches).

The **adapter** that implements the port is `adapters/httpx_client.py`. Its
`HttpxClient` is the single module in the codebase that imports `httpx`: it maps a
`ResolvedRequest` onto an `httpx.AsyncClient` call, materializes the response
back, and translates `httpx.HTTPError` into the core's `HttpError`. It also owns
the wire-format concerns the core stays out of: it splits the body into httpx's
`json` / `data` (form) / `content` (raw) slots by `bodyType`, turns a resolved
`auth` block into `httpx.BasicAuth` or an `Authorization: Bearer` header, and sends
per-request `cookies`. For a `streaming: true` response it reads the stream to
completion and hands the bytes to `core/streams.py`, which parses them into the
ordered `events` list carried on `HttpResponse`; `compare.py` then diffs that event
sequence rather than the raw bytes.

Every engine component that touches the network — `execute.py`, `compare.py`,
`execution.py`, `health.py` — accepts an `HttpClient`, so all of them are exercised
in tests with a hand-built fake client and never open a socket. A diff (and an
execution with a candidate) is given **two** clients, one per environment, so the
baseline and candidate never share a cookie jar — a `Set-Cookie` from one side can
never leak into a request sent to the other.

`core/report.py` mirrors the same pattern for output: it defines a `Reporter`
protocol, and `adapters/reporters.py` supplies the concrete JUnit, SARIF, JSON,
and Markdown reporters. The core stays format-agnostic. Separately, `core/archive.py`
persists a run as a redacted `ReportRecord` under `<data>/.reports/`, so a past diff
or execution can be browsed and replayed later without hitting the network — the
Report screen reads these back. Both the reporters and the archive receive already-
redacted input.

## The `comparo.core` module map

| Module | Role |
| --- | --- |
| `models.py` | Typed `msgspec` structs for every object kind, sharing a Kubernetes-style envelope (`apiVersion` / `kind` / `metadata` / `spec`). Framework fields forbid unknown keys; payload positions are `Any` and validated later. Exposes the tagged union `Object` — now including `AssertionProfile` and `ExecutionProfile`. |
| `loader.py` | Loads a directory of YAML into a `LoadedProject` in diagnostics-collecting passes (parse + envelope, id indexing, reference checking, then profile-slot validation); keeps `$ref`/`$val`/`${...}` as holes; dangling references and unresolvable profile slots are hard errors with near-miss hints. |
| `diagnostics.py` | `Diagnostic` (file, message, line, hint) and `LoadError`, which carries every problem found in one load. |
| `refs.py` | `resolve_specs`: the one resolver for a profile attachment slot — a `$ref`, an inline spec, or a list that composes — shared by the diff and assertion slots. An unresolvable slot is a hard error, never a silent empty rule set. |
| `interpolation.py` | The `${...}` grammar — required / optional / default / typed-cast — resolved *secret-first* against an environment `Context`. |
| `secrets.py` | `ExecuteSecrets`: lazily resolves declared secrets from `$env` / `$literal` / `$file` / `from` sources and caches them, for the EXECUTE sink; `$file` paths are confined to the project root. |
| `resolve.py` | Environment selection and pairing, plus the `Resolver` that fills holes into a `ResolvedRequest` (method, URL, headers, query, body, `bodyType`, `auth`, `cookies`, `streaming`) under the DISPLAY or EXECUTE `Sink`. A matrix whose target is `request.path` fills `${...}` placeholders in the endpoint here. |
| `provenance.py` | `Origin` (with the `tainted` property) and `Trail` — the single fact that drives masking, scrubbing, and diff explanations. |
| `matrix.py` | Expands a request across its referenced matrices into one `MatrixCell` per combination (cartesian product), each with a stable `key`; applies an ExecutionProfile's per-matrix `include` / `exclude` / `override` scope. |
| `streams.py` | Parses a streamed response body back into its ordered records — the full SSE envelope (`id` · `event` · joined `data` · `retry`, per the SSE processing model; a trailing unterminated event is deliberately kept so a timed-out stream still diffs whatever arrived), or the JSON objects of a chunked stream — so a stream is diffed as a sequence, not flattened to bytes. This is THE stream parse: the Run tab's renderer and the diff's per-event comparison consume the same output, so the tabs can never disagree about what an event contains. |
| `http.py` | The `HttpClient` port, the `HttpResponse` (with an optional `events` list for streams) / `TimeoutBudget` / `HttpError` value types. Core's only view of the network. |
| `execute.py` | Resolves a request in the EXECUTE sink, computes its timeout budget, and sends it through an `HttpClient`; failures are captured on the `Execution`, not raised, and runs are bounded by a concurrency semaphore. |
| `diff.py` | The tri-state comparator: every path is `SAME`, `DRIFT`, or `SKIP`, under modes `ignore` / `exact` / `shape` / `type` / `tolerance`, with the most-specific path rule winning. |
| `assertions.py` | Evaluates an `AssertionProfile` (or a request's `status`/`schema` sugar) against one materialized response — targets (`status`, `latency`, headers, JSON-path) and ops (`equals`, `matches`, comparisons, `between`, `oneOf`, `exists`, `contains`, `schema`); `error` rules gate, `warn` rules advise. Composition returns `SourcedAssertion`s — each rule tagged with an `AssertRef` (target/op/severity/label + provenance: owning profile or request, stable within-block index) that evaluation stamps onto every `AssertionResult`, so rule ↔ result ↔ cell traceability is in the data. The Run tab renders these results directly (evaluated once per cell; the screen, the saved run, and the archived report share the same objects). |
| `compare.py` | Runs a diff pair: executes every request-cell against both environments concurrently (each through its own `HttpClient`, so cookie jars stay separate), pairs results by `(request id, cell)`, composes the request/project/override diff profiles, and diffs each — as an event sequence for streamed cells. |
| `execution.py` | Runs an `ExecutionProfile`: resolves it to a plan (which requests, cells, environments), executes each cell against baseline and candidate, asserts both, diffs the pair, and reports a fail-closed gate. Orchestration only — no comparison logic of its own. |
| `report.py` | The structured `RunReport` and the `Reporter` port; `build_report` folds diff results into cells with a pass/fail gate (`diff_passed` / `diff_gate`), redacting drift details as it goes. |
| `archive.py` | The saved-report store under `<data>/.reports/`: `ReportRecord` (gate, counts, assertion roll-ups, per-request breakdown) and `CellRecord` (redacted before/after bodies, response headers, status/latency/bytes for a faithful replay); `record_from_diff` / `record_from_execution` / `record_from_run`, `save_record`, `list_records`. |
| `redaction.py` | The `Redactor` string-match backstop: masks any declared secret *value* (or key/path) found in any string a sink emits, even when it arrived untainted (e.g. echoed back by the server). |
| `triage.py` | Silences a drift by appending an `ignore` rule to the owning `DiffProfile`'s YAML file (round-tripped through ruamel so comments survive) — a reviewable, committed act, never an in-memory hide. |
| `curl.py` | Renders a `ResolvedRequest` as a runnable multi-line `curl`; masked or real depending on the sink it was resolved with. |
| `export.py` | Serializes a run to JSON with every secret masked — DISPLAY-sink values plus string-match redaction of response bodies (keys and values). |
| `health.py` | Probes an environment's declared health checks through the `HttpClient` port and aggregates PASS / PARTIAL / FAIL / UNKNOWN. |

## The front-ends

**CLI** (`cli/app.py`) — a [Typer](https://typer.tiangolo.com/) app whose module
docstring is explicit: *"Only wiring lives here … No engine logic belongs in this
module."* `init` scaffolds a new project; `validate`, `render`, `run`, `exec`,
`diff`, and `tui` load one, call engine functions, construct an `HttpxClient`
adapter, and print or write the results; `help` prints the command reference.
Running `comparo` with no command opens the TUI on `./comparo.yaml`. `run` gates on
each request's `status`/`schema` sugar; `exec` runs an `ExecutionProfile` through
`run_execution` and exits on its gate; `diff` drives the report gate and writes any
requested reporter outputs (appending the Markdown reporter to
`$GITHUB_STEP_SUMMARY` when run inside Actions). Each of these builds a `Redactor`
for the project and threads it through every line it prints.

**TUI** (`tui/app.py`) — a [Textual](https://textual.textualize.io/) application
with Explorer, Run, Diff, Execution, Report, and Settings screens. Despite its size
it is still a front-end: it imports the same engine functions (`load_project`,
`Resolver`, `expand`, `execute_request`, `compare_cell`, `run_execution`,
`check_health`, `evaluate_rules`, `export_run`, `to_curl`) and the `HttpxClient`
adapter, and holds no comparison or resolution logic of its own. The **Execution**
screen launches an `ExecutionProfile` (from the Explorer) and shows the same gate
`comparo exec` computes; the **Report** screen browses the saved-run archive under
`<data>/.reports/` — it saves runs with `archive.save_record` and reads them back
with `list_records`, so a past diff or execution can be replayed from its persisted,
redacted `CellRecord`s without re-executing. The core never depends on any of it.

**GitHub Action** (`action.yml`) — installs comparo and invokes `comparo diff`
with the chosen environments and the `markdown` / `junit` / `sarif` reporters, so
CI gets a step-summary table and machine-readable artifacts, and the job fails
when the gate does.

## Tooling and quality gates

comparo targets **Python 3.13+** and uses [uv](https://docs.astral.sh/uv/) for
environment and dependency management. Runtime dependencies are deliberately few:
`msgspec` (typed decode), `ruamel.yaml` (round-trip YAML with line numbers),
`httpx` (the transport, behind the adapter — `httpx-sse` is also declared, though a
streamed response is currently read chunk-by-chunk and parsed by `core/streams.py`),
`typer` (CLI), `textual` (TUI), and `jsonschema` (schema checks).

Every change must pass the same gates CI runs. From
[CONTRIBUTING.md](../CONTRIBUTING.md):

```console
uv sync                                   # create the venv and install everything
uv run pre-commit install --hook-type pre-commit --hook-type commit-msg

uv run ruff check .          # lint (and import sorting)
uv run ruff format .         # format
uv run mypy                  # strict type checking
uv run lint-imports          # architecture contract (core must not import interfaces)
uv run pytest                # tests
```

The configuration behind these gates (`pyproject.toml`):

- **ruff** — a broad lint select (`E W F I N UP B C4 SIM PTH TID RUF D PT`) at a
  100-column line length, Google-style docstrings required, and one import per
  line (isort `force-single-line`), plus the formatter.
- **mypy** — `strict = true` on `src` and `tests`, with `warn_unreachable`,
  `warn_redundant_casts`, and `warn_unused_ignores`.
- **import-linter** — the two architecture contracts above (`uv run
  lint-imports`).
- **pytest** — with `pytest-cov` for coverage and `syrupy` for snapshot tests;
  CI runs `pytest --cov --cov-report=term-missing`.

The same steps run in `.github/workflows/ci.yml` on every push to `main` and
every pull request. Commits follow the
[Angular Commit Convention](https://github.com/angular/angular/blob/main/CONTRIBUTING.md#commit),
which drives automated semantic releases (`release.yml`) to PyPI via Trusted
Publishing.
