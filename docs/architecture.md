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
sending requests, diffing responses, and building reports. The engine knows
nothing about *which* HTTP library moves bytes on the wire, and nothing about
*how* a human drives it.

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
└── plugins/     registry for custom comparators, reporters, auth, generators
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

`core/loader.py` loads every `*.yaml` object under a project root in three
diagnostics-collecting passes, so one run surfaces every problem at once:

1. **Parse + envelope validation** — each document is parsed with `ruamel.yaml`
   and decoded into a typed model (`msgspec.convert(..., strict=True)`).
2. **Id indexing** — objects are indexed by `metadata.id`; a duplicate id or a
   second `Project` manifest is a diagnostic.
3. **Reference checking** — every `$ref`/`$val` target in the raw tree is checked
   against the known ids. A dangling reference is a *hard error* with a near-miss
   suggestion (`did you mean '…'?`), never a silent degradation.

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
- **Reports scrub echoes too.** `core/export.py` renders request values through
  the DISPLAY sink (so declared secrets arrive already masked) and *additionally*
  redacts response bodies by string-match against the real secret values — so a
  secret the server echoes back is masked as well. No real secret value survives
  into a saved report.

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
back, and translates `httpx.HTTPError` into the core's `HttpError`. Every engine
component that touches the network — `execute.py`, `compare.py`, `health.py` —
accepts an `HttpClient`, so all of them are exercised in tests with a hand-built
fake client and never open a socket.

`core/report.py` mirrors the same pattern for output: it defines a `Reporter`
protocol, and `adapters/reporters.py` supplies the concrete JUnit, SARIF, JSON,
and Markdown reporters. The core stays format-agnostic.

## The `comparo.core` module map

| Module | Role |
| --- | --- |
| `models.py` | Typed `msgspec` structs for every object kind, sharing a Kubernetes-style envelope (`apiVersion` / `kind` / `metadata` / `spec`). Framework fields forbid unknown keys; payload positions are `Any` and validated later. Exposes the tagged union `Object`. |
| `loader.py` | Loads a directory of YAML into a `LoadedProject` in three diagnostics-collecting passes; keeps `$ref`/`$val`/`${...}` as holes; dangling references are hard errors with near-miss hints. |
| `diagnostics.py` | `Diagnostic` (file, message, line, hint) and `LoadError`, which carries every problem found in one load. |
| `interpolation.py` | The `${...}` grammar — required / optional / default / typed-cast — resolved *secret-first* against an environment `Context`. |
| `secrets.py` | `ExecuteSecrets`: lazily resolves declared secrets from `$env` / `$literal` / `$file` / `from` sources and caches them, for the EXECUTE sink. |
| `resolve.py` | Environment selection and pairing, plus the `Resolver` that fills holes into a `ResolvedRequest` under the DISPLAY or EXECUTE `Sink`. |
| `provenance.py` | `Origin` (with the `tainted` property) and `Trail` — the single fact that drives masking, scrubbing, and diff explanations. |
| `matrix.py` | Expands a request across its referenced matrices into one `MatrixCell` per combination (cartesian product), each with a stable `key`. |
| `http.py` | The `HttpClient` port, the `HttpResponse` / `TimeoutBudget` / `HttpError` value types. Core's only view of the network. |
| `execute.py` | Resolves a request in the EXECUTE sink, computes its timeout budget, and sends it through an `HttpClient`; failures are captured on the `Execution`, not raised, and runs are bounded by a concurrency semaphore. |
| `diff.py` | The tri-state comparator: every path is `SAME`, `DRIFT`, or `SKIP`, under modes `ignore` / `exact` / `shape` / `type` / `tolerance`, with the most-specific path rule winning. |
| `compare.py` | Runs a diff pair: executes every request-cell against both environments concurrently, pairs results by `(request id, cell)`, and diffs each under its profile. |
| `report.py` | The structured `RunReport` and the `Reporter` port; `build_report` folds diff results into cells with a pass/fail gate. |
| `checks.py` | Pure validations over a materialized response — `reachable`, expected `status`, and JSON `schema` (via `jsonschema`). |
| `curl.py` | Renders a `ResolvedRequest` as a runnable multi-line `curl`; masked or real depending on the sink it was resolved with. |
| `export.py` | Serializes a run to JSON with every secret masked — DISPLAY-sink values plus string-match redaction of response bodies. |
| `health.py` | Probes an environment's declared health checks through the `HttpClient` port and aggregates PASS / PARTIAL / FAIL / UNKNOWN. |

## The front-ends

**CLI** (`cli/app.py`) — a [Typer](https://typer.tiangolo.com/) app whose module
docstring is explicit: *"Only wiring lives here … No engine logic belongs in this
module."* Its commands (`validate`, `render`, `run`, `diff`, `tui`) load a
project, call engine functions, construct an `HttpxClient` adapter, and print or
write the results. The `diff` command drives the report gate and writes any
requested reporter outputs (appending the Markdown reporter to
`$GITHUB_STEP_SUMMARY` when run inside Actions).

**TUI** (`tui/app.py`) — a [Textual](https://textual.textualize.io/) application
with Explorer, Run, Diff, Report, and Settings screens. Despite its size it is
still a front-end: it imports the same engine functions (`load_project`,
`Resolver`, `expand`, `execute_request`, `diff_run`, `check_health`,
`run_checks`, `export_run`, `to_curl`) and the `HttpxClient` adapter, and holds
no comparison or resolution logic of its own. The core never depends on it.

**GitHub Action** (`action.yml`) — installs comparo and invokes `comparo diff`
with the chosen environments and the `markdown` / `junit` / `sarif` reporters, so
CI gets a step-summary table and machine-readable artifacts, and the job fails
when the gate does.

## Tooling and quality gates

comparo targets **Python 3.13+** and uses [uv](https://docs.astral.sh/uv/) for
environment and dependency management. Runtime dependencies are deliberately few:
`msgspec` (typed decode), `ruamel.yaml` (round-trip YAML with line numbers),
`httpx` + `httpx-sse` (the transport, behind the adapter), `typer` (CLI),
`textual` (TUI), and `jsonschema` (schema checks).

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
