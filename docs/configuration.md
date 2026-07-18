# Configuration reference

> The `comparo/v1` object model — every `kind`, field, sigil, and rule.

A comparo project is a `comparo.yaml` **manifest** plus a set of version-controlled YAML
objects it references. Each object shares a Kubernetes-style envelope (`apiVersion` / `kind` /
`metadata` / `spec`), is decoded strictly, and is indexed by its `metadata.id` so other
objects can reference it. This document is the authoritative reference for that format.

Every example below is drawn from or is consistent with the runnable
[`examples/sample-project`](../examples/sample-project).

## Table of contents

- [Project layout](#project-layout)
- [The object envelope](#the-object-envelope)
  - [`apiVersion`](#apiversion)
  - [`kind`](#kind)
  - [`metadata`](#metadata)
  - [`spec`](#spec)
  - [How objects are loaded](#how-objects-are-loaded)
- [Shared types](#shared-types)
  - [Duration](#duration)
  - [Header](#header)
- [Object kinds](#object-kinds)
  - [Project](#project)
  - [Environment](#environment)
  - [Request](#request)
  - [Schema](#schema)
  - [Instance](#instance)
  - [Matrix](#matrix)
  - [DiffProfile](#diffprofile)
  - [AssertionProfile](#assertionprofile)
  - [ExecutionProfile](#executionprofile)
- [Interpolation: `${...}`](#interpolation-)
  - [Grammar](#grammar)
  - [Casts](#casts)
  - [Whole-value vs embedded](#whole-value-vs-embedded)
  - [The secret-priority rule](#the-secret-priority-rule)
- [References and sigils](#references-and-sigils)
  - [`$ref` — object reference](#ref--object-reference)
  - [`$val` — instance value injection](#val--instance-value-injection)
  - [`$secret`, `$env`, `$file`, `$literal` — value sigils](#secret-env-file-literal--value-sigils)
- [Secrets and masking](#secrets-and-masking)
  - [Declaring secrets](#declaring-secrets)
  - [Secret sources](#secret-sources)
  - [Lazy resolution and the two sinks](#lazy-resolution-and-the-two-sinks)
- [Matrices](#matrices)
- [Diff profiles](#diff-profiles)
  - [Modes](#modes)
  - [Rule fields](#rule-fields)
  - [Path matching and precedence](#path-matching-and-precedence)
- [Saved reports: the archive](#saved-reports-the-archive)

---

## Project layout

A project has two parts: the **manifest** — a single `Project` object, by convention named
`comparo.yaml` — and the **objects** it references (environments, requests, matrices, schemas,
instances, diff profiles). The manifest's `spec.data` field points at the directory those
objects live in, **relative to the manifest**:

```yaml
# comparo.yaml
apiVersion: comparo/v1
kind: Project
metadata:
  name: my-api
spec:
  data: .comparo   # objects live in ./.comparo/, relative to this file
```

Two conventions are in use:

- **User projects** — what [`comparo init`](cli.md#comparo-init) scaffolds — set
  `data: .comparo`, a hidden directory that never collides with the rest of your repository.
  `init` writes the manifest plus a starter `.comparo/environments/` and `.comparo/requests/`,
  so the project validates and runs immediately.
- **Self-contained projects** — the bundled [examples](../examples) — set `data: .`, so the
  object files sit right beside the manifest.

You do not have to build this layout by hand: `comparo init` creates it for you (see the
[CLI reference](cli.md#comparo-init)). Loading either a manifest file or a plain directory is
supported — see [How objects are loaded](#how-objects-are-loaded).

## The object envelope

Every object is a single YAML document with the same four top-level keys:

```yaml
apiVersion: comparo/v1
kind: Environment
metadata:
  name: Local
  id: environment.local
spec:
  baseUrl: http://localhost:8080
```

> **YAML keys are camelCase.** Internally the models use snake_case, but the on-disk format
> is camelCase — for example `baseUrl`, `streamIdle`, `createPath`, `arrayLength`. This
> reference lists the YAML spelling you actually write.
>
> **Unknown keys are hard errors.** Every framework struct forbids unknown fields, so a
> mistyped key (`baseURL` instead of `baseUrl`) fails the load rather than being silently
> ignored. The freeform positions that accept arbitrary content — a request `body`, an
> `Instance` value, a `Schema` body — are the deliberate exceptions.

### `apiVersion`

Always the string `comparo/v1`. Any other value is rejected.

### `kind`

Selects the object type and therefore the shape of `spec`. One of:

| `kind`             | Purpose                                                                        |
| ------------------ | ------------------------------------------------------------------------------ |
| `Project`          | the root manifest — run-wide defaults (environments, concurrency, reporting)   |
| `Environment`      | a target: base URL, timeout, secrets, variables, headers, health checks, auth  |
| `Request`          | an HTTP request, optionally matrix-expanded, with a response schema and diff   |
| `Schema`           | a JSON Schema used for structural response validation                          |
| `Instance`         | a reusable value injected by reference to avoid duplication                    |
| `Matrix`           | a set of parameter cases a request is run against                              |
| `DiffProfile`      | how two responses are compared, per JSON path                                  |
| `AssertionProfile` | a composable set of response assertions checked on both environments           |
| `ExecutionProfile` | a named run plan — what to run, which environments, and which checks           |

### `metadata`

Common to every kind. `Project` is the only kind that does **not** declare an `id`.

| Field         | YAML key      | Type            | Required                    | Description                                                       |
| ------------- | ------------- | --------------- | --------------------------- | ---------------------------------------------------------------- |
| name          | `name`        | string          | yes                         | Human-readable label shown in the TUI and reports.               |
| id            | `id`          | string          | yes, except on `Project`    | Stable identity other objects reference. Must be unique.         |
| description   | `description` | string          | no                          | Free-text explanation.                                           |
| tags          | `tags`        | list of strings | no                          | Labels used by headless selection (e.g. `--tags smoke`).         |

By convention an `id` is namespaced by kind: `environment.local`, `request.get-json`,
`schema.slideshow`, `instance.auth-headers`, `matrix.locales`, `diff.lenient`. Reference
resolution also accepts the short segment after the prefix in some positions (for example an
environment may be selected as `prod` as well as `environment.prod`).

### `spec`

The kind-specific body. Each kind is documented under [Object kinds](#object-kinds).

### How objects are loaded

- Given a **manifest file** (e.g. `comparo.yaml`), the loader reads the objects under its
  `spec.data` directory (see [Project layout](#project-layout)); given a **directory**, it
  reads every `*.yaml` beneath it. Either way it is **one object per file**.
- Objects are indexed by `metadata.id`. A missing `id` (on a non-`Project` kind), a
  **duplicate `id`**, or a **second `Project` manifest** is an error.
- Every `$ref` and `$val` target is checked against the index. A dangling reference is a hard
  error and the loader offers a **near-miss suggestion** — either the closest known id
  (`did you mean 'diff.lenient'?`) or an id with the *same segments in a different order*. A
  `$ref` that is clearly a JSON-Schema pointer (it starts with `#` or contains `/`, e.g.
  `#/$defs/Money`) is your own payload, not a comparo id, and is left untouched.
- Once the tree resolves cleanly, a final pass validates every **profile attachment slot** — a
  request's `diff` / `assert`, an ExecutionProfile's `profiles`, the project default `diff`, and
  each AssertionProfile `include`. A slot that names a missing id, points at the wrong kind, or
  holds an invalid inline spec is a hard error — never a silently-empty rule set (which would
  pass every gate). A non-empty `spec.plugins` block is rejected here too.
- These passes are diagnostics-collecting: one run surfaces every problem at once, each
  reported with its file and, where available, line number.

---

## Shared types

### Duration

A split timeout budget. Every field is optional; an omitted field falls back to a built-in
default. Each value is a string matching `^[0-9]+(ms|s|m|h)$` — an integer followed by a unit
of `ms`, `s`, `m`, or `h`.

| Field       | YAML key      | Type          | Description                              |
| ----------- | ------------- | ------------- | ---------------------------------------- |
| connect     | `connect`     | duration      | Connection-establishment budget.         |
| read        | `read`        | duration      | Response-read budget.                    |
| stream_idle | `streamIdle`  | duration      | For a streaming read, end the stream gracefully after this long with no new data — so an *idle* Server-Sent Events feed terminates instead of hanging. |
| stream_max  | `streamMax`   | duration      | A *total* cap on a streaming read: stop and diff what arrived after this long, regardless of activity — so a *steady* SSE feed (one that emits on a timer and never idles) still terminates. |

```yaml
timeout:
  connect: 5s
  read: 30s
```

> Only `connect` and `read` currently reach the transport (as httpx connect/read
> timeouts). `streamIdle` is a valid key but not yet applied.

Used by `Environment.spec.timeout` and `Request.spec.timeout`.

### Header

A single HTTP header. Appears in `Environment.spec.headers` and `HealthCheck.headers`.

| Field       | YAML key      | Type    | Required | Description                                                  |
| ----------- | ------------- | ------- | -------- | ------------------------------------------------------------ |
| key         | `key`         | string  | yes      | Header name.                                                 |
| value       | `value`       | any     | no       | Literal value, or a reference/interpolation hole.            |
| description | `description` | string  | no       | Free-text explanation.                                       |
| required    | `required`    | boolean | no       | Documentation flag; defaults to `false`.                     |
| type        | `type`        | string  | no       | Optional type annotation for the value.                      |

> Request-level headers are supplied differently — see [Request](#request). They are usually
> an `Instance` reference (`$val`) whose value is a list of `{key, value}` header entries.

---

## Object kinds

### Project

The root manifest — run-wide defaults. Exactly one `Project` object per tree, and the only
kind with **no** `metadata.id`.

`spec` accepts the following top-level keys. Their inner shapes are consumed by the run
engine, the reporters, and the front-ends rather than validated by the envelope, so treat the
sub-fields below as the conventional structure the sample project uses.

| Field        | YAML key       | Description                                                                       |
| ------------ | -------------- | --------------------------------------------------------------------------------- |
| data         | `data`         | Path to the object tree, relative to this file (e.g. `.comparo` or `.`).           |
| environments | `environments` | Default environment and named diff pairs — see below.                             |
| run          | `run`          | Execution defaults such as `concurrency` and `retry`.                             |
| diff         | `diff`         | Global default comparison profile (a `$ref` / inline / list under `default`).     |
| selection    | `selection`    | Default headless selection, e.g. `tags`.                                          |
| report       | `report`       | Saved-report `dir` and Markdown-export `output` directory — see below.            |
| redaction    | `redaction`    | Redaction options, e.g. `stringMatchBackstop`.                                    |
| plugins      | `plugins`      | Reserved. The plugin system does not exist yet, so a non-empty `plugins` block is a **hard load error** (`spec.plugins is not supported yet`). |

**`environments`** drives environment selection:

- `default` — the environment used when none is named on the command line.
- `diffPairs` — a list of `{name, baseline, candidate}` entries. `comparo diff --pair <name>`
  replays every request against `baseline` and `candidate` and diffs the results.

**`report`** configures where saved output lands. It is a free-form block; two keys are read:

- `dir` — the directory the TUI writes saved run reports (the [archive](#saved-reports-the-archive))
  to, resolved under `spec.data`. Defaults to `.reports`.
- `output` — the directory the TUI's Markdown export writes to. Defaults to `reports`.

The reporter *formats* a `comparo diff` writes are chosen with the CLI `--report` flag (see the
[CLI reference](cli.md#report-formats)), not from the manifest — a `report.formats` key is not consumed.

```yaml
apiVersion: comparo/v1
kind: Project
metadata:
  name: httpbin-showcase
  description: A runnable comparo project that diffs httpbin responses across environments.
spec:
  # Where the object tree lives, relative to this file.
  data: .

  environments:
    default: prod
    # A/B live comparison: replay every request against both and diff the responses.
    diffPairs:
      - name: local-vs-prod
        baseline: local
        candidate: prod

  run:
    concurrency: 4
    retry:
      attempts: 3
      backoff: exponential

  # Global default comparison profile; a request may override it with its own `diff:`.
  diff:
    default:
      $ref: diff.lenient

  # Headless default selection.
  selection:
    tags:
      - smoke

  report:
    # Where the TUI saves run reports (the browsable archive), under spec.data.
    dir: .reports
    # Where the TUI's Markdown export writes.
    output: reports

  redaction:
    # Mask anything equal to a known secret value, even if it arrives untainted.
    stringMatchBackstop: true
```

### Environment

A target the requests run against.

| Field     | YAML key     | Type                          | Required | Description                                                             |
| --------- | ------------ | ----------------------------- | -------- | ---------------------------------------------------------------------- |
| base_url  | `baseUrl`    | string                        | yes      | Base URL; each request's `endpoint` is joined onto it.                 |
| timeout   | `timeout`    | [Duration](#duration)         | no       | Per-request timeout budget.                                            |
| secrets   | `secrets`    | map of name → source          | no       | Declared secrets — see [Secrets and masking](#secrets-and-masking).    |
| variables | `variables`  | map of string → string        | no       | Values available to `${...}` interpolation.                            |
| headers   | `headers`    | list of [Header](#header)     | no       | Headers merged into every request (request headers override by name).  |
| health    | `health`     | list of health checks         | no       | Readiness probes (`method`, `endpoint`, optional `headers`).           |
| auth      | `auth`       | [Auth](#auth)                 | no       | Default Basic/Bearer auth applied to every request unless it sets its own. |

```yaml
apiVersion: comparo/v1
kind: Environment
metadata:
  name: Production
  id: environment.prod
  description: The public httpbin instance.
spec:
  baseUrl: https://httpbin.org
  timeout:
    connect: 5s
    read: 30s
  secrets:
    # Demo bearer token. Locally supplied via the environment; masked everywhere.
    API_TOKEN:
      $env: COMPARO_DEMO_TOKEN
  variables:
    DEFAULT_LOCALE: en-US
  health:
    - method: GET
      endpoint: /status/200
```

A **health check** entry has `method` (required), `endpoint` (required), and an optional
`headers` list.

### Request

An HTTP request, optionally expanded across one or more matrices.

| Field    | YAML key    | Type                                        | Required | Description                                       |
| -------- | ----------- | ------------------------------------------- | -------- | ------------------------------------------------- |
| request  | `request`   | [outbound request](#the-outbound-request)   | yes      | The HTTP request to send.                         |
| response | `response`  | [expected response](#the-expected-response) | no       | The response expectations to check.               |
| matrix   | `matrix`    | list of `$ref` to `Matrix`                  | no       | Matrices this request is expanded across.         |
| timeout  | `timeout`   | [Duration](#duration)                       | no       | Overrides the environment timeout for this request. |

#### The outbound request

`spec.request`:

| Field     | YAML key    | Type                    | Required | Description                                                         |
| --------- | ----------- | ----------------------- | -------- | ------------------------------------------------------------------ |
| method    | `method`    | string                  | yes      | HTTP method (`GET`, `POST`, …).                                     |
| endpoint  | `endpoint`  | string                  | yes      | Path joined onto the environment `baseUrl`; matrix-injectable.     |
| query     | `query`     | map of string → any     | no       | Query parameters; values may be holes and are matrix-injectable.   |
| headers   | `headers`   | any                     | no       | Headers — typically a `$val` to a header `Instance`.               |
| body      | `body`      | any                     | no       | Request body; may contain `${...}`, `$val`, and matrix injections. |
| body_type | `bodyType`  | string                  | no       | Body encoding: `json` (default), `form` (URL-encoded form), or `raw` (sent verbatim). |
| auth      | `auth`      | [Auth](#auth)           | no       | Basic/Bearer auth for this request; overrides the environment default. |
| cookies   | `cookies`   | map of string → any     | no       | Cookies to send, as name → value (values may be holes/secrets).    |

#### Auth

`auth` (on a request, or as an environment-wide default) is one of:

```yaml
auth:
  basic:
    username: ${API_USER}
    password: ${API_PASSWORD}   # a secret hole — masked in every display
# — or —
auth:
  bearer: ${API_TOKEN}          # sent as "Authorization: Bearer <token>"
```

A request's `auth` overrides the environment's default `auth`. Secret values are masked in
displays and injected only at execution, like every other hole.

#### Body encodings

`bodyType` selects how `body` is put on the wire:

| `bodyType`      | Encoding                                                                          |
| --------------- | --------------------------------------------------------------------------------- |
| `json` (default) | Serialized as a JSON body with a JSON content type.                              |
| `form`          | Sent as a URL-encoded form (`application/x-www-form-urlencoded`); `body` must be a map. |
| `raw`           | Sent verbatim — a string/bytes body is passed through untouched; anything else is JSON-encoded first. |

```yaml
request:
  method: POST
  endpoint: /login
  bodyType: form
  body:
    user: bob
    remember: "1"
```

#### Cookies

`cookies` is a `name → value` map sent with the request; values may be `${...}` holes or
secrets, resolved like anywhere else. Cookies are per request — nothing is carried over from a
previous request. When a diff runs, the baseline and candidate use **separate cookie jars**, so
a `Set-Cookie` from one environment can never leak into a request sent to the other.

#### Streaming

Setting `response.streaming: true` tells the engine the response is delivered as a *sequence* —
Server-Sent Events (a `text/event-stream` content type) or a chunked run of JSON objects — rather
than one payload. The adapter reads the stream to completion and parses it back into its ordered
records: SSE events become field maps (`{event, data, id, …}`); a JSON stream becomes the list of
its objects; anything else stays a single text record. The diff then compares that **event
sequence** element-by-element under the request's `DiffProfile` (each event indexed as `$[0]`,
`$[1]`, …), instead of flattening the stream to bytes. A non-streamed response is compared as its
parsed JSON body (or raw bytes when it is not JSON).

#### The expected response

`spec.response`:

| Field      | YAML key    | Type    | Required | Description                                                          |
| ---------- | ----------- | ------- | -------- | ------------------------------------------------------------------- |
| status     | `status`    | integer | no       | Expected HTTP status; checked when present.                         |
| schema     | `schema`    | any     | no       | A `$ref` to a `Schema` object; the body is JSON-Schema-validated.   |
| diff       | `diff`      | any     | no       | A `DiffProfile` as a `$ref`, inline spec, or a list (composed); overrides the project default. |
| streaming  | `streaming` | boolean | no       | Whether the response is streamed (SSE / JSON stream), diffed as its ordered event sequence. |
| assertions | `assert`    | any     | no       | One or more `AssertionProfile`s (`$ref`, inline, or a list) to check. |

```yaml
apiVersion: comparo/v1
kind: Request
metadata:
  name: Echo Anything
  id: request.echo-anything
  description: POST an order to /anything once per locale.
  tags:
    - smoke
    - matrix
spec:
  # Run once per locale; each case is merged into request.query (see matrix.locales).
  matrix:
    - $ref: matrix.locales
  request:
    method: POST
    endpoint: /anything
    headers:
      $val: instance.auth-headers
    body:
      order:
        sku: WIDGET-1
        quantity: 3
        note: Locale is ${DEFAULT_LOCALE}
  response:
    status: 200
    schema:
      $ref: schema.anything-echo
    diff:
      $ref: diff.lenient
```

### Schema

A JSON Schema used to validate a response body structurally. `spec` is the raw JSON Schema
document (a mapping) — it is passed to the validator as-is, so any valid JSON Schema is
accepted here.

```yaml
apiVersion: comparo/v1
kind: Schema
metadata:
  name: Anything Echo
  id: schema.anything-echo
  description: The structure httpbin's /anything endpoint echoes back.
spec:
  type: object
  required:
    - method
    - url
    - headers
    - json
  properties:
    method:
      type: string
    url:
      type: string
    origin:
      type: string
    headers:
      type: object
    args:
      type: object
    json:
      type:
        - object
        - "null"
```

A request references a schema via `response.schema: { $ref: schema.<id> }`. The response body
is parsed as JSON and validated against `spec`; a non-JSON body or a validation failure is
reported as a failed `schema` check.

### Instance

A single reusable value, injected elsewhere by [`$val`](#val--instance-value-injection) to
avoid duplication. `spec.value` is freeform — a scalar, a mapping, or (commonly) a list of
header entries.

| Field | YAML key | Type | Required | Description                     |
| ----- | -------- | ---- | -------- | ------------------------------- |
| value | `value`  | any  | no       | The value to inject by reference. |

```yaml
apiVersion: comparo/v1
kind: Instance
metadata:
  name: Auth Headers
  id: instance.auth-headers
  description: Default headers plus a bearer token sourced from a secret.
spec:
  value:
    - key: accept
      value: application/json
    - key: user-agent
      value: comparo-showcase
    - key: authorization
      # ${API_TOKEN} resolves to the secret (secret-priority), so the value is
      # tainted: masked in the TUI and scrubbed from reports and snapshots.
      value: Bearer ${API_TOKEN}
```

When injected, the instance's value is resolved in place — its `${...}` holes and nested
sigils are filled exactly as if they had been written inline at the injection site.

### Matrix

A list of atomic cases a request is expanded across. See [Matrices](#matrices) for how cases
combine into cells.

| Field       | YAML key     | Type                       | Required | Description                                                          |
| ----------- | ------------ | -------------------------- | -------- | ------------------------------------------------------------------- |
| target      | `target`     | string                     | yes      | Dotted injection path — `request.query`, `request.body`, or `request.path`. |
| values      | `values`     | list of maps               | yes      | The cases; each is a `key: value` map merged (or substituted) in.   |
| mode        | `mode`       | `merge` \| `replace`       | no       | How a case is applied at `target`; defaults to `merge`.             |
| create_path | `createPath` | boolean                    | no       | Create missing intermediate objects along `target`; defaults `false`. |

```yaml
apiVersion: comparo/v1
kind: Matrix
metadata:
  name: Locales
  id: matrix.locales
  description: Run a request once per locale, injecting the pair into the query string.
spec:
  # Injection target — each case is merged into the request's query map.
  target: request.query
  mode: merge
  values:
    - locale: en-US
      currency: USD
    - locale: fr-FR
      currency: EUR
    - locale: ja-JP
      currency: JPY
```

A **path matrix** fills `${...}` placeholders in the request's `endpoint` instead of merging
into a container:

```yaml
apiVersion: comparo/v1
kind: Matrix
metadata:
  name: Status codes
  id: matrix.status-codes
spec:
  target: request.path        # substitute each case into the endpoint template
  values:
    - code: "200"
    - code: "404"
# A request with endpoint: /status/${code} then runs against /status/200 and /status/404.
```

### DiffProfile

How two responses are compared, per JSON path. A `default` mode applies to every path, and
optional `rules` override it for specific subtrees.

| Field   | YAML key  | Type                       | Required | Description                                        |
| ------- | --------- | -------------------------- | -------- | -------------------------------------------------- |
| default | `default` | [mode](#modes)             | yes      | The mode for any path no rule matches.             |
| rules   | `rules`   | list of [rules](#rule-fields) | no    | Path-scoped overrides.                             |

```yaml
apiVersion: comparo/v1
kind: DiffProfile
metadata:
  name: Lenient
  id: diff.lenient
  description: Ignore volatile fields; compare the parts we control by shape and value.
spec:
  # Unlisted paths: same shape (keys + types), values ignored.
  default: shape
  rules:
    - path: $.uuid
      mode: ignore          # /uuid returns a fresh value every call
    - path: $.origin
      mode: ignore          # caller IP address
    - path: $.url
      mode: ignore          # host differs between local and prod
    - path: $.headers
      mode: ignore          # Host, X-Amzn-Trace-Id, Authorization all vary
    - path: $.args
      mode: exact           # the query args we injected must round-trip exactly
    - path: $.json
      mode: exact           # the request body we sent must echo back identically
```

A profile can be as small as a single default:

```yaml
apiVersion: comparo/v1
kind: DiffProfile
metadata:
  name: Strict
  id: diff.strict
spec:
  default: exact
```

#### Attaching a profile: `$ref`, inline, or a composing list

Every profile slot — a request's `response.diff` / `response.assert`, the project default
`diff.default`, and an ExecutionProfile's `profiles.diff` / `profiles.assert` — accepts the
**same three shapes**:

```yaml
# 1. a $ref to a standalone object
diff:
  $ref: diff.lenient

# 2. an inline spec, written in place (no separate object needed)
diff:
  default: shape
  rules:
    - path: $.uuid
      mode: ignore

# 3. a list that composes — later entries add to earlier ones
assert:
  - $ref: assert.http-ok      # the shared contract
  - rules:                    # plus a couple of inline rules
      - target: body:$.args.currency
        op: equals
        value: USD
```

A list of diff profiles concatenates their `rules`, and the **last** entry's `default` wins.
Assertion profiles concatenate their rules (and everything they `include`). A slot that fails to
resolve — a missing `$ref`, a wrong-kind target, or an invalid inline spec — is a **hard load
error**, never a silently-empty profile: an empty rule set passes every gate, so swallowing it
would be a false green.

### AssertionProfile

A composable set of **response assertions** — environment-agnostic checks that must hold on
*both* environments (they never reference an environment). Attach one to a request's
`response.assert`, or to an ExecutionProfile. Assertions with `severity: warn` are non-blocking.

| Field   | YAML key  | Type                        | Required | Description                                  |
| ------- | --------- | --------------------------- | -------- | -------------------------------------------- |
| rules   | `rules`   | list of assertion rules     | no       | The checks (see below).                      |
| include | `include` | list of `$ref`              | no       | Other AssertionProfiles to compose in first. |

Each rule is `{target, op, value, severity}`:

- **target** — what to read from the response:
  - `status` — the HTTP status code.
  - `latency` — the elapsed time in **milliseconds** (a numeric `value` is milliseconds; a
    string like `800ms` / `1s` is also accepted).
  - `contentType` — the `Content-Type` header.
  - `header:<Name>` — a named response header (case-insensitive).
  - `bodyRaw` — the response body as text.
  - `body` — the parsed JSON body; `body:$.<jsonpath>` addresses a node inside it (dotted keys
    and `[index]` elements, e.g. `body:$.items[0].id`).
- **op** — `equals`, `matches` (a regex searched in the value), `lt` / `lte` / `gt` / `gte`,
  `between` (`value: [min, max]`), `oneOf` (`value` is a list), `exists`, `contains`, and
  `schema` (validate the target against a `Schema` `$ref` or an inline JSON Schema).
- **severity** — `error` (default, blocks the gate) or `warn` (advisory; counted but never
  fails the gate).

Every assertion is evaluated against **both** the baseline and the candidate response, so an
AssertionProfile expresses a contract that must hold on each environment independently.

```yaml
apiVersion: comparo/v1
kind: AssertionProfile
metadata:
  name: HTTP OK
  id: assert.http-ok
spec:
  rules:
    - target: status
      op: equals
      value: 200
    - target: contentType
      op: matches
      value: application/json
    - target: latency
      op: lt
      value: 5000
      severity: warn
---
apiVersion: comparo/v1
kind: AssertionProfile
metadata:
  name: Pricing contract
  id: assert.pricing
spec:
  include:
    - $ref: assert.http-ok        # compose the baseline contract
  rules:
    - target: body:$.args.currency
      op: equals
      value: USD
```

`response.status` and `response.schema` on a request are sugar that compile into `status`/`schema`
assertion rules automatically.

### ExecutionProfile

A named run plan: **what** to run, **which** environments, and **which** checks. Unlike an
Assertion/Diff profile, the ExecutionProfile is the object that *owns* the environments — via
explicit `baseline` / `candidate` keys. Launch it from the Explorer (`enter`) or run it headless
with [`comparo exec <id>`](cli.md#comparo-exec).

| Field        | YAML key       | Type                                     | Description                                             |
| ------------ | -------------- | ---------------------------------------- | ------------------------------------------------------ |
| select       | `select`       | `{tags: [...], requests: [...]}`         | Which requests to run (by tag and/or id). Empty = all. |
| matrix       | `matrix`       | `{<matrix.id>: {include/exclude/override}}` | Per-matrix case scoping for this run.               |
| environments | `environments` | `{baseline, candidate}`                  | The env(s); a `candidate` enables the diff.            |
| check        | `check`        | `{assertions: bool, diff: bool}`         | Which checks to compute (both default true).           |
| profiles     | `profiles`     | `{assert: <ref/inline>, diff: <ref/inline>}` | Execution-scoped assertion / diff profiles.        |
| report       | `report`       | any                                      | Report options for this run.                           |

```yaml
apiVersion: comparo/v1
kind: ExecutionProfile
metadata:
  name: Release gate
  id: execution.release-gate
spec:
  select:
    tags: [pricing]
  environments:
    baseline: stable          # this profile owns the env pair — no "X-vs-Y"
    candidate: canary
  check:
    assertions: true
    diff: true
```

The diff only runs when a `candidate` is set (with just a `baseline`, an ExecutionProfile is an
assertions-only run). The gate passes when **every** cell passes — no execution error, every
`error`-severity assertion holds on both environments, and nothing drifted — and it **fails
closed** on an empty run (a `select` / matrix scope that matched nothing verified nothing). That
verdict is exactly the exit code of headless [`comparo exec <id>`](cli.md#comparo-exec).

---

## Interpolation: `${...}`

String values are interpolated against the selected environment's `variables` (and its
secrets — see [the secret-priority rule](#the-secret-priority-rule)). Interpolation happens
inside any string in a resolved value tree: header values, query parameters, body fields, and
instance values.

### Grammar

Everything lives inside a single `${...}`:

| Form                  | Meaning                                                                 |
| --------------------- | ----------------------------------------------------------------------- |
| `${NAME}`             | **Required** — the run aborts if `NAME` is unset.                       |
| `${NAME?}`            | **Optional** — resolves to nothing (empty when embedded) if unset.      |
| `${NAME \| default}`  | **Default** — uses `default` only when `NAME` is unset.                 |
| `${NAME:cast}`        | **Cast** — see [Casts](#casts).                                         |

The parts compose. Whitespace around each part is trimmed, so all of these are valid:

```yaml
note: Locale is ${DEFAULT_LOCALE}         # required
region: ${REGION | us-east-1}             # default when unset
retries: ${MAX_RETRIES:int | 3}           # cast, with a default
trace: ${TRACE_ID?}                       # optional
```

A **required** variable that is unset, and an unparseable cast, both raise a hard error.

### Casts

A cast converts the resolved text to a typed value. Three casts are supported:

| Cast      | Result                                                                 |
| --------- | --------------------------------------------------------------------- |
| `:int`    | Parsed as an integer (fails if the text is not an integer).           |
| `:number` | Parsed as a floating-point number.                                    |
| `:bool`   | `true` if the text is `true`, `1`, or `yes` (case-insensitive), else `false`. |

Any other cast name (e.g. `:date`) is a hard error.

### Whole-value vs embedded

- A string that is **exactly one** `${...}` yields a **typed** value: casts apply, so
  `${MAX_RETRIES:int}` becomes the integer `3`, not the string `"3"`.
- A `${...}` **embedded** in surrounding text is substituted into a string. The result is
  always a string, and an optional-but-unset reference contributes an empty string. For
  example `Bearer ${API_TOKEN}` produces one string with the token spliced in.

### The secret-priority rule

Resolution is **secret-first**. If a name is declared as a secret in the environment, then
`${NAME}` resolves to the *secret* — masked in the display sink, the real value in the execute
sink — **even though it is written as a plain variable**. A secret can never be surfaced by
writing it as an ordinary `${...}`, and a variable can never shadow a secret of the same name.

In the sample project, `authorization: Bearer ${API_TOKEN}` is tainted because `API_TOKEN` is
a declared secret: the whole header is masked in the TUI and scrubbed from reports.

---

## References and sigils

A **sigil** is a single-key mapping whose key begins with `$`. Two families exist: structural
references (`$ref`, `$val`) that point at another object, and value sigils (`$secret`, `$env`,
`$file`, `$literal`) that produce a value in place.

Both `$ref` and `$val` targets are validated at load time against the object index, with
near-miss suggestions on failure.

### `$ref` — object reference

Points at another object by its `metadata.id`. Used in structural positions rather than as a
value hole:

- `Request.spec.matrix` entries — `- $ref: matrix.locales`
- `Request.spec.response.schema` — `$ref: schema.anything-echo`
- `Request.spec.response.diff` — `$ref: diff.lenient`
- `Project.spec.diff.default` — `$ref: diff.lenient`

The referenced object is looked up and consumed by the relevant subsystem (matrix expansion,
schema validation, diffing).

### `$val` — instance value injection

Injects an [`Instance`](#instance)'s `spec.value` at this position, then resolves it in place.
Used to share a value — most often a header list — across requests:

```yaml
request:
  headers:
    $val: instance.default-headers
```

Injected values carry an **instance** provenance and their inner `${...}` / sigils are
resolved as if written inline.

### `$secret`, `$env`, `$file`, `$literal` — value sigils

These produce a value directly. Their behavior depends on where they appear — inside an
[`Environment.spec.secrets`](#secret-sources) source definition, or inline in a request /
instance value tree:

| Sigil      | Inline in a value tree                                                        | Masked in display? |
| ---------- | ----------------------------------------------------------------------------- | ------------------ |
| `$secret`  | The value of a declared secret by name (`$secret: API_TOKEN`).                | Yes                |
| `$env`     | Sources a secret from an OS environment variable; treated as tainted.         | Yes                |
| `$file`    | Sources a secret from a file; treated as tainted.                             | Yes                |
| `$literal` | The value verbatim — **not** interpolated and **not** masked.                 | No                 |

`$literal` is the escape hatch for a value that must be passed through untouched (for example a
string that literally contains `${...}`). `$secret`, `$env`, and `$file` are all tainted: in
the display sink they render as the mask (`••••••`); in the execute sink `$secret` resolves the
declared secret's real value.

---

## Secrets and masking

### Declaring secrets

Secrets are declared per environment under `spec.secrets`, keyed by name:

```yaml
spec:
  secrets:
    API_TOKEN:
      $env: COMPARO_DEMO_TOKEN
```

The **key set** — the names — is what defines which `${...}` references are treated as
secrets. Keeping the same names across environments (even if their sources differ) keeps
requests environment-agnostic and stops a value's taint from flipping per environment. The
sample project does exactly this: `local` uses a `$literal` dummy while `prod` uses `$env`, so
`API_TOKEN` is a secret in both.

```yaml
# environments/local.yaml
secrets:
  API_TOKEN:
    $literal: local-demo-token
```

### Secret sources

A secret's value is a source definition — a single-key mapping choosing where the value comes
from:

| Source            | Resolves to                                                                        |
| ----------------- | ---------------------------------------------------------------------------------- |
| `$env: VAR`       | The OS environment variable `VAR`. Errors if it is unset.                          |
| `$literal: value` | A constant string. Useful for dummy/demo values.                                   |
| `$file: path`     | The contents of a file (relative to the project root), whitespace-stripped. The path is **confined to the project root** — one that escapes it (`../…`) is an error. |
| `from: [ ... ]`   | A list of source definitions tried in order; the first that resolves wins.         |

```yaml
secrets:
  API_TOKEN:
    from:
      - $env: COMPARO_DEMO_TOKEN
      - $file: .secrets/token
      - $literal: local-demo-token
```

### Lazy resolution and the two sinks

Secrets are resolved **lazily and cached**: a secret that is declared but whose source is
unavailable only fails a run if something actually uses it. An unused, unresolvable secret is
never an error.

Resolution runs in one of two sinks:

- **Display sink** (default — the TUI, snapshots, reports): every secret renders as the mask,
  `••••••`. Real values never leave the process.
- **Execute sink** (sending the request): `$secret` and `${SECRET_NAME}` resolve to the real
  value so the request can be sent.

Values remember their **origin** — `literal`, `variable`, `secret`, `instance`, `matrix`, or
`file` — and `secret` and `file` origins are *tainted*: masked in display and never persisted.
The project-level `redaction.stringMatchBackstop` option adds a safety net, masking any string
equal to a known secret value even if it arrived through an untainted path.

---

## Matrices

A `Matrix` turns one request into many. A request references zero or more matrices in
`spec.matrix`; each matrix is a list of atomic **cases**.

- **Expansion.** The cartesian product of all referenced matrices' `values` produces one
  **cell** per combination. A request with no matrices runs as a single cell.
- **Cell keys.** Each case renders as a deterministic, sorted `key=value` string
  (`currency=USD, locale=en-US`); across multiple matrices the per-matrix keys are joined with
  ` · `. Reports and diffs use this key to name exactly which combination they refer to.
- **Injection.** Each case is applied at the matrix's `target`:
  - `target: request.query` merges (or replaces) the case into the request's query map.
  - `target: request.body` merges (or replaces) the case into the body.
  - `target: request.path` substitutes each case key into a `${key}` placeholder in the
    request's `endpoint` — so `endpoint: /status/${code}` matrixed over `code: 200` and
    `code: 404` runs against `/status/200` and `/status/404`. Path matrices fill the template
    rather than merging into a container, so `mode` / `createPath` do not apply to them.
  - The leading `request.` prefix is optional.
- **`mode`.** `merge` (default) updates the container at `target` with the case's keys;
  `replace` substitutes the case for whatever was there.
- **`createPath`.** When `true`, missing intermediate objects along `target` are created so a
  case can be injected into a path that does not yet exist; when `false`, injection into a
  missing path is a no-op.

Given `matrix.locales` above, `request.echo-anything` runs three times — once per locale — each
run merging `{locale, currency}` into the query string, with cell keys
`currency=USD, locale=en-US`, `currency=EUR, locale=fr-FR`, and `currency=JPY, locale=ja-JP`.

---

## Diff profiles

Comparison is **tri-state**: every path resolves to one of three outcomes.

| State   | Meaning                                                         |
| ------- | -------------------------------------------------------------- |
| `same`  | Compared and identical.                                        |
| `drift` | Compared and different — a regression.                         |
| `skip`  | Deliberately not compared. A skipped field is never `same`.    |

A skipped path is reported explicitly: the tool says out loud what it chose not to check.

### Modes

| Mode        | Behavior                                                                                                        |
| ----------- | -------------------------------------------------------------------------------------------------------------- |
| `exact`     | Recurse. Leaf values must be equal; arrays must be the same length. A type mismatch is drift.                  |
| `shape`     | Recurse. Structure (keys and types) must match, but leaf values are ignored; array length is tolerant.        |
| `type`      | The two values must share the same JSON type (`object`, `array`, `string`, `number`, `boolean`, `null`). No recursion. |
| `tolerance` | Numbers are equal if within `± tolerance`; non-numbers fall back to exact equality.                            |
| `ignore`    | Skip the subtree entirely — outcome `skip`.                                                                    |

Both `exact` and `shape` recurse, so a more specific `ignore` rule can carve a hole out of an
otherwise-exact tree. A missing key on one side, or a type mismatch, is always drift.

### Rule fields

Each entry in `rules` scopes a mode to a JSON path.

| Field        | YAML key      | Type    | Required | Description                                                                    |
| ------------ | ------------- | ------- | -------- | ------------------------------------------------------------------------------ |
| path         | `path`        | string  | yes      | JSON path this rule applies to (see below).                                    |
| mode         | `mode`        | string  | yes      | One of the [modes](#modes).                                                     |
| array_length | `arrayLength` | string  | no       | `exact` forces strict length checking even under a `shape` rule; `tolerant` is the default (lengths may differ). |
| tolerance    | `tolerance`   | number  | no       | The `± limit` for `tolerance` mode (defaults to `0`).                           |
| schema       | `schema`      | any     | no       | Reserved for schema-scoped comparison; accepted by the model but not yet consumed by the differ. |

### Path matching and precedence

- **Path syntax.** A leading `$` and `.` are optional; `$.headers`, `.headers`, and `headers`
  are equivalent. Array indices are written `[0]`, and two wildcards match any single segment:
  `*` (a key) and `[*]` (an array element) — e.g. `$.items[*].id`.
- **Prefix scope.** A rule applies to its path and everything beneath it. `$.headers` covers
  the whole `headers` subtree.
- **Precedence.** The **most specific** matching rule wins — the one whose path has the most
  segments and is a prefix of the current path. Any path no rule matches falls to `default`.

When no profile applies at all, comparison falls back to `exact` with no rules — the strictest
possible comparison.

---

## Saved reports: the archive

The TUI can **save a run** so you can browse and replay it later without re-executing. Saved
runs live in the archive directory — `<data>/.reports/` by default, overridable with
[`spec.report.dir`](#project) — one JSON file per run, named by a short id (`b5210e.json`).

A saved run is a `ReportRecord`: the gate verdict (`PASS` / `FAIL` / `ERROR`), the counts
(`calls` / `same` / `drift` / `error` / `skipped`), a per-environment assertion roll-up, a
per-request drift breakdown, and — for a faithful replay — a `cells` list. Each cell persists
enough to redraw the run offline: the request name, matrix variant, method and path, the drifted
and skipped field paths, the **before/after response bodies**, the response headers, and the
status / latency / byte size.

Every string a record persists — request names, paths, cell keys, headers, and the body trees
(keys *and* values) — is passed through the [redactor](#lazy-resolution-and-the-two-sinks) first,
so a declared secret the server echoed back is masked before anything is written to disk. A
saved report can therefore be committed alongside the project. Three shapes are recorded:

- a **diff** run — baseline vs candidate, with the body diff per cell (no assertions);
- an **execution** — an ExecutionProfile's assertions on both environments plus the diff;
- a single-environment **run** — the assertions roll-up for one environment, no diff.
