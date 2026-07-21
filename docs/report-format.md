# Report format

comparo writes one versioned JSON artifact for every **run**, **diff**, and
**execution**. It captures the whole interaction — the resolved outbound request
*and* the response, per side — so a saved report replays in full detail offline,
feeds the TUI's Report tab, and is the single source the CI reporters project
from.

- **JSON, not YAML.** Config is YAML because humans author it; reports are
  machine-generated and machine-consumed (replay, CI, `jq`, external tools), so
  they are JSON — universal, unambiguous, streamable.
- **One shape, three kinds.** A `run` has one side; a `diff` and an `execution`
  have two (`baseline` + `candidate`). Everything else follows from `kind`.
- **Redaction is a floor.** Every value is masked before the record is written —
  url, headers, query, body, cookies, JSON paths, names, and error messages. An
  `auth` value is *always* the mask glyph. The never-leak invariant is
  unconditional.

Get the machine-readable schema with `comparo schema --report`.

## Envelope

```jsonc
{
  "schemaVersion": 1,            // int — bumped only on a breaking change
  "kind": "diff",               // "run" | "diff" | "execution"
  "metadata":   { … },          // who/when/what produced this
  "invocation": { … },          // what was asked — reproducibility
  "summary":    { … },          // the precomputed verdict + tallies
  "cells":      [ … ]           // one entry per (request × matrix variant)
}
```

| Field | Type | Notes |
|---|---|---|
| `schemaVersion` | int | The format version. Readers ignore unknown fields; an additive field never breaks an older reader. |
| `kind` | enum | `run` \| `diff` \| `execution`. Drives which optional sections appear. |
| `metadata` | object | Who/when/what produced the report. |
| `invocation` | object | Everything needed to reproduce it. |
| `summary` | object | The precomputed gate and tallies. |
| `cells` | array | One entry per executed cell. May be empty (nothing selected). |

## `metadata`

| Field | Type | Notes |
|---|---|---|
| `id` | string | Short unique id (e.g. `8c3e11`); the filename stem. |
| `created` | string | ISO 8601 UTC, e.g. `2026-07-18T15:11:22Z`. |
| `tool` | string | `comparo <version>` that wrote it. |
| `project` | string \| null | Project name (redacted). |
| `title` | string \| null | Optional human label (e.g. an execution profile's name). |

## `invocation`

| Field | Type | Notes |
|---|---|---|
| `command` | string | The equivalent headless command (redacted). |
| `environments` | object | `{ "baseline": EnvRef, "candidate": EnvRef \| null }`. `candidate` is `null` for a `run`. |
| `selection` | object \| null | `{ "tags": [string] \| null, "requests": [string] \| null }` — which requests ran. |
| `concurrency` | int | In-flight cap used. |
| `profile` | string \| null | The `ExecutionProfile` id (for `kind = execution`); else `null`. |

**`EnvRef`** — `{ "name": string, "baseUrl": string /*redacted*/, "id": string \| null }`

## `summary`

The verdict, precomputed so a reader never recomputes from `cells`.

| Field | Type | Notes |
|---|---|---|
| `gate` | enum | `PASS` \| `FAIL` \| `ERROR` — the CI exit contract. Precedence: `FAIL` whenever any rule broke anywhere; `ERROR` only when errors are the only failure; a run that judged nothing fails closed. |
| `calls` | int | Total HTTP calls made (cells × sides that executed). |
| `cells` | int | Number of cells. |
| `diff` | object \| null | Drift tally (for `diff`/`execution`): `{ "same", "drift", "error", "skipped" }`. |
| `assertions` | object \| null | Assertion tally (for `run`/`execution`): `{ "passed", "failed", "warned", "notAsserted" }`. |

## `cells[]`

One executed `(request, matrix-variant)` unit.

| Field | Type | Notes |
|---|---|---|
| `requestId` | string | The request's `metadata.id` (redacted). |
| `name` | string | Display name (redacted). |
| `variant` | string | The matrix cell key (redacted); `""` when the request has no matrix. |
| `verdict` | enum | `same` \| `drift` \| `error` (diff side), or `pass` \| `fail` \| `error` (assert side). |
| `sides` | object | `{ "baseline": Side, "candidate": Side \| null }`. |
| `comparison` | object \| null | The diff between the two sides — present for `diff`/`execution`. |

Assertions live **inside each side** (they are evaluated against that side's
response), not on the cell.

### `Side`

```jsonc
{
  "request":  OutboundRequest,            // the resolved outbound — the replay fidelity
  "response": ResponseRecord | null,      // null if the call errored before a response
  "assertions": [AssertionResult] | null, // checks vs this response (run/execution)
  "error": string | null                  // transport/resolution error (redacted), else null
}
```

**`OutboundRequest`** — what was sent (resolved, masked): `method`, `url`,
`headers` (`[[name, value]]`), `query`, `body`, `bodyType` (`json`/`form`/`raw`),
`auth` (`{ "scheme": "basic"|"bearer", "value": "••••••" }` — value always
masked), `cookies`, `streaming`.

**`ResponseRecord`** — what came back: `status`, `headers` (`[[name, value]]`),
`latencyMs`, `sizeBytes` (materialized-body length), `body` (parsed redacted JSON,
or `null` for non-JSON), `events` (ordered parsed records for a stream, else
`null`), `bodyText` (optional raw redacted text for a non-JSON body).

### `comparison`

Present for `diff` and `execution`.

| Field | Type | Notes |
|---|---|---|
| `verdict` | enum | `same` \| `drift` \| `error`. |
| `same` / `drift` / `skipped` | int | Field-path counts. |
| `fields` | `[FieldDiff]` | The **non-same** fields (drift + skip). Same-valued fields are omitted — they are derivable from the two `response.body` blobs, which keeps the record compact. |

**`FieldDiff`** — `path` (redacted), `state` (`drift`/`skip`), `mode`
(`exact`/`ignore`/`shape`/`type`/`tolerance`), `baseline` / `candidate` (redacted
values, present for a drift), `rule` (the declared path of the rule that governed
it, e.g. why a `skip` was skipped). `rule` may be a **synthetic built-in** path —
`$status` (the always-on status check) or a `$headers.<name>` volatile-header
ignore — as well as a `DiffProfile` rule path; `null` still means the profile's
default mode governed. Paths under `$headers.<name>` compare the response headers
(names case-folded; duplicates joined per RFC 9110, `set-cookie` kept as a list;
credential values masked before comparison).

**`AssertionResult`** — `target` (redacted), `op`, `expected` / `actual`
(redacted), `ok`, `severity` (`error`/`warn`), `detail` (redacted).

## What each `kind` populates

| Path | `run` | `diff` | `execution` |
|---|:--:|:--:|:--:|
| `invocation.environments.candidate` | `null` | ✓ | ✓ |
| `invocation.profile` | `null` | `null` | ✓ |
| `summary.diff` | `null` | ✓ | ✓ |
| `summary.assertions` | ✓ | `null` | ✓ |
| `cell.sides.candidate` | `null` | ✓ | ✓ |
| `cell.sides.*.assertions` | ✓ | `null` | ✓ |
| `cell.comparison` | `null` | ✓ | ✓ |

## Errors & edge cases

- **A side that never got a response** → `side.response = null`,
  `side.error = "<masked message>"`, cell `verdict = "error"`. The gate reads
  `ERROR` only when errored cells are the sole failure — a rule broken anywhere
  else still grades `FAIL`. Rules on an errored cell were never judged and do
  not count as broken.
- **Non-JSON body** → `response.body = null`; `bodyText` may carry the redacted raw
  text.
- **Streamed body** → `response.events` holds the ordered records; the diff runs
  over `events`.
- **Empty selection** → `cells: []`, `summary.calls: 0`, `gate: FAIL` — a run
  that judged nothing fails closed, for every kind.

## Versioning

- `schemaVersion` is a single integer, bumped **only** on a breaking change (a
  field removed or renamed, or a type changed).
- Readers ignore unknown fields, so additive fields and new `kind` values never
  bump the version.
- A reader that sees a newer `schemaVersion` reads what it can; it never crashes.
