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
  "rules":      { … },          // the rule inventories, referenced by id
  "cells":      [ … ],          // one entry per (request × matrix variant)
  "notRun":     [ … ]           // cells deselected at prepare — the ⊘ roster
}
```

| Field | Type | Notes |
|---|---|---|
| `schemaVersion` | int | The format version. Readers ignore unknown fields; an additive field never breaks an older reader. |
| `kind` | enum | `run` \| `diff` \| `execution`. Drives which optional sections appear. |
| `metadata` | object | Who/when/what produced the report. |
| `invocation` | object | Everything needed to reproduce it. |
| `summary` | object | The precomputed gate and tallies. |
| `rules` | object \| null | `{ "diff": [DiffRule], "assertions": [AssertRule] }` — every effective rule with its cross-cell outcome tallies, stored once; cells reference by `id`. |
| `cells` | array | One entry per executed cell. May be empty (nothing selected). |
| `notRun` | array | `{ "requestId", "name", "variant" }` per deselected cell, so the roster replays. |

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
| `fields` | object \| null | Field-path tally (for `diff`/`execution`): `{ "same", "drift", "skipped" }` — one unit, field paths. |
| `cellVerdicts` | object \| null | Cell tally: `{ "passed", "failed", "errors", "notRun", "advisory" }` — one unit, cells. `advisory` counts passed cells with a broken warn rule; `notRun` counts the roster, so these counts span `cells` + `notRun`. |
| `assertions` | object \| null | Assertion tally (for `run`/`execution`): `{ "passed", "failed", "warned", "notAsserted", "unjudged" }`. `unjudged` counts rows on a response-less side — never judged, never `failed`. |

## `cells[]`

One executed `(request, matrix-variant)` unit.

| Field | Type | Notes |
|---|---|---|
| `requestId` | string | The request's `metadata.id` (redacted). |
| `name` | string | Display name (redacted). |
| `variant` | string | The matrix cell key (redacted); `""` when the request has no matrix. |
| `verdict` | enum | `pass` \| `fail` \| `error` \| `not_run` — one vocabulary for every kind (a drifted diff cell is `fail`; "clean" is display copy). |
| `advisory` | bool | Passed, but at least one warn rule broke — the `~` marker. |
| `error` | string \| null | The cell-level error (redacted): pairing failure, empty matrix, compare error. |
| `sides` | object | `{ "baseline": Side, "candidate": Side \| null }`. |
| `comparison` | object \| null | The diff between the two sides — present for `diff`/`execution`. |
| `requestComparison` | object \| null | The outbound layer: `{ "verdict": "same"\|"drift", "fields": [{ "label", "baseline", "candidate", "source" }] }` — did we send the same request to both sides, and if not, which config surface differed. |

Assertions live **inside each side** (they are evaluated against that side's
response), not on the cell.

### `Side`

```jsonc
{
  "request":  OutboundRequest,            // the resolved outbound — the replay fidelity
  "response": ResponseRecord | null,      // null if the call errored before a response
  "assertions": [AssertionResult] | null, // checks vs this response (run/execution)
  "error": string | null,                 // transport/resolution error (redacted), else null
  "attempts": 1,                          // transport attempts made (1 = no retry fired)
  "retryPolicy": string | null            // e.g. "exponential x3"
}
```

**`OutboundRequest`** — what was sent (resolved, masked): `method`, `url`,
`headers` (`[[name, value]]`), `query`, `body`, `bodyType` (`json`/`form`/`raw`),
`auth` (`{ "scheme": "basic"|"bearer", "value": "••••••" }` — value always
masked), `cookies`, `streaming`, `trail` (`[{ "path", "origin", "detail" }]` —
where each injected value came from: `variable`/`secret`/`instance`/`matrix`/
`file`; the request facet's provenance annotations, redacted).

**`ResponseRecord`** — what came back: `status`, `headers` (`[[name, value]]`),
`latencyMs`, `sizeBytes` (true materialized-body length), `httpVersion` +
`reasonPhrase` (the raw facet's status line), and **exactly one body
representation**: `body` (parsed redacted JSON) ⊕ `events` (ordered parsed
records for a stream) ⊕ `bodyText` (redacted non-JSON text, truncated only
*after* redaction — `bodyTruncated: true` marks a cut) ⊕ a binary digest
(`sha256` of the raw body plus `bodyHead`, hex of at most the first KiB —
dropped entirely when the redactor would touch its text view: hex must never
become a side channel around the mask).

### `comparison`

Present for `diff` and `execution`.

| Field | Type | Notes |
|---|---|---|
| `verdict` | enum | `same` \| `drift` \| `error` — the diff *dimension's* verdict. |
| `same` / `drift` / `skipped` | int | Field-path counts for this cell. |
| `error` | string \| null | The compare/pairing error, verbatim (redacted). |
| `profiles` | array | The composed `DiffProfile` ids, composition order (redacted). |
| `defaultMode` | string \| null | The effective catch-all mode. |
| `fields` | `[FieldDiff]` | **Every** compared field. `same` entries are path-only (no values — recovered by pure lookup into the stored side; re-diffing redacted bodies is unsound, so the stored verdicts are authoritative). |

**`FieldDiff`** — `path` (redacted), `state` (`same`/`drift`/`skip`), `mode`
(`exact`/`ignore`/`shape`/`type`/`tolerance`), `baseline` / `candidate` (redacted
values, present for a drift), `ruleId` (into `rules.diff` — the governing rule,
including the `default` catch-all and synthetics). Paths under `$headers.<name>`
compare the response headers (names case-folded; duplicates joined per RFC 9110,
`set-cookie` kept as a list; credential values masked before comparison);
`$status` is the always-on status check.

**`AssertionResult`** — `target` (redacted), `op`, `ok`, `severity`
(`error`/`warn`), `label` (redacted human form, what the screen showed),
`ruleId` (into `rules.assertions`), `outcome` (`held`/`broke`/`error` — `error`
means the side never responded, so the rule was never judged), `expected` /
`actual` (redacted), `detail` (redacted).

## `rules`

The inventories every cell references by id — stored once, per the
no-repetition rule.

**`rules.diff[]`** — `id`, `path` (declared, redacted), `mode`, `origin`
(`profile`/`inline`/`default`/`synthetic`), `profile` (owning `DiffProfile` id),
`tolerance` / `arrayLength` (the rule's parameters — part of its identity),
`outcomes` (`{ "broke", "held", "silenced", "absent", "error" }` — cells per
outcome; a rule with every count zero matched nothing anywhere: "unused" is
derived, never stored).

**`rules.assertions[]`** — `id`, `target`, `op`, `severity`, `label`, `origin`
(`profile`/`inline`), `profile` \| `request` (the owner), `expected`,
`outcomes` (as above, plus `warnBroke`/`warnHeld` so advisories never read as
gate failures).

## What each `kind` populates

| Path | `run` | `diff` | `execution` |
|---|:--:|:--:|:--:|
| `invocation.environments.candidate` | `null` | ✓ | ✓ |
| `invocation.profile` | `null` | `null` | ✓ |
| `summary.fields` | `null` | ✓ | ✓ |
| `summary.cellVerdicts` | ✓ | ✓ | ✓ |
| `summary.assertions` | ✓ | `null` | ✓ |
| `rules.diff` | `[]` | ✓ | ✓ |
| `rules.assertions` | ✓ | `[]` | ✓ |
| `cell.sides.candidate` | `null` | ✓ | ✓ |
| `cell.sides.*.assertions` | ✓ | `null` | ✓ |
| `cell.comparison` | `null` | ✓ | ✓ |
| `cell.requestComparison` | `null` | ✓ | ✓ |

## Errors & edge cases

- **A side that never got a response** → `side.response = null`,
  `side.error = "<masked message>"`, cell `verdict = "error"`. The gate reads
  `ERROR` only when errored cells are the sole failure — a rule broken anywhere
  else still grades `FAIL`. Rules on an errored cell were never judged and do
  not count as broken.
- **Non-JSON text body** → `response.body = null`; `bodyText` carries the
  redacted text (truncated after redaction when huge). A **binary** body stores
  `sha256` + `bodyHead` instead — and BOTH are dropped when any text view of the
  whole body trips the redactor: never mojibake, never a hex side channel, and
  never a digest oracle over secret-bearing bytes.
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
