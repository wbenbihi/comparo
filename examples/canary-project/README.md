# Canary project — the signature diff, and a tour of every feature

A **runnable** project built around the **Diff screen**, and the widest tour of comparo in the
repo. It runs against [postman-echo](https://postman-echo.com) — a public request-echo service —
because it lets us control exactly what each environment "returns", so drift is surgical instead
of noisy. Every request, matrix, schema, secret, and diff mode here works against the live host.

> httpbin is often overloaded (503s). This project deliberately uses a different host so the
> examples stay runnable when httpbin is down. postman-echo can rate-limit rapid calls with a
> transient 503 — retry a few seconds apart.

## The scenario

Two environments front the same echo API. They are **identical except two variables**:

| Variable | `stable` (baseline) | `canary` (candidate) | Effect on the diff |
| --- | --- | --- | --- |
| `TAX_RATE` | `0.20` | `0.25` | the accidental regression → **DRIFT** |
| `SAMPLE_SIZE` | `1000` | `1004` | a tweak inside the ±5 budget → **SAME** |
| `API_VERSION` | `2` | `2` | identical → **SAME** |
| `DEFAULT_LOCALE` | `en-US` | `en-US` | identical → **SAME** |

Each request echoes its inputs back, so the response *is* what we sent. `quote` draws `taxRate`
from the environment, so `stable` sends `0.20`, `canary` sends `0.25`, and that one field drifts.
`checkout` draws `sampleSize`, which also differs (`1000` → `1004`) — but its diff profile compares
it with a **± tolerance**, so an in-budget nudge is not flagged as a regression.

## What the diff shows

Run the pair (`comparo diff … --pair stable-vs-canary`, or press `x` on the Diff screen):

```
diff · Stable ⇄ Canary
  ✓ request.catalog                              same  (2 skipped)
  ✓ request.checkout                             same  (2 skipped)
  ✗ request.quote [plan=free]                    drift
      $.args.taxRate  "0.20" → "0.25"
  ✗ request.quote [plan=pro]                     drift
      $.args.taxRate  "0.20" → "0.25"
  ✓ request.search [currency=USD, locale=en-US · tier=free] same  (2 skipped)
  …
  ✓ request.stream                               same  (2 skipped)

summary: 22 same · 2 drift · 0 error · 33 fields skipped
gate: FAIL
```

Every request but `quote` compares **SAME**; `quote` drifts on `taxRate` in both plan cells, which
the Diff screen collapses into a single grouped row (`$.args.taxRate ×2`) — one regressed field is
one bug, not two. Three tri-state outcomes are on show at once:

- **SAME** — `currency`, `apiVersion`, `sampleSize` (within tolerance), and the whole `checkout`,
  `search`, and utility fleet compare equal.
- **DRIFT** — `taxRate` differs (`0.20 → 0.25`); the gate fails on it.
- **SKIP** — volatile envelope fields (`$.headers`, `$.url`) are ignored, and the tool says
  *skipped* — never pretends they matched.

## What it demonstrates

| Area | Where | Feature |
| --- | --- | --- |
| **Diff pair** | `comparo.yaml` | `stable-vs-canary` replays every request against both environments |
| **Diff: `exact`** | `diff/echo-args.yaml` · `diff/strict.yaml` | `$.args` round-trips exactly; `/time/object` compared byte-for-byte |
| **Diff: `shape`** | `diff/envelope.yaml` | the project default — keys + types must match, values ignored |
| **Diff: `type`** | `diff/pricing.yaml` | `$.json.tier` need only keep its JSON type |
| **Diff: `tolerance`** | `diff/pricing.yaml` | `$.json.sampleSize` within ±5 is SAME (`1000` → `1004`) |
| **Diff: `ignore`** | every profile | `$.headers` and `$.url` are skipped, not compared |
| **Single matrix** | `matrices/plans.yaml` → `quote` | one request per plan; the drift lands on both cells |
| **Multi-matrix** | `matrices/{locales,tiers}.yaml` → `search` | cartesian `3 × 2 = 6` cells |
| **Schemas (pass)** | `schemas/echo-get.yaml`, `echo-write.yaml`, `time-object.yaml`, `authenticated.yaml` | JSON-Schema validation of responses |
| **Schema (fail)** | `schemas/echo-strict.yaml` → `schema-mismatch` | requires a `json` field `/get` never returns → ASSERT fails |
| **Status assertions** | `requests/status-ok.yaml`, `status-mismatch.yaml` | `200 == 200` passes; `200 ≠ 201` fails |
| **Secrets** | `environments/*.yaml`, `instances/basic-auth.yaml` → `basic-auth` | HTTP Basic auth from a masked `BASIC_AUTH` secret (`$env` → `$literal` fallback) |
| **Instances** | `instances/default-headers.yaml`, `basic-auth.yaml` | shared header sets injected with `$val` |
| **Streaming** | `requests/stream.yaml` | `/stream/1` read as a chunked stream (`response.streaming: true`) |
| **Every method** | `catalog` (GET) · `checkout` (POST) · `update` (PUT) · `patch` (PATCH) · `remove` (DELETE) | the full verb set, JSON bodies echoed back |
| **Utilities** | `gzip`, `deflate`, `encoding-utf8`, `headers`, `delay`, `time-object`, `status-ok` | compression, non-JSON, latency, deterministic time |
| **Interpolation** | `${TAX_RATE}`, `${SAMPLE_SIZE:int}`, `${DEFAULT_LOCALE}` | variables, and a whole-value `:int` cast into a JSON body |
| **Health** | `environments/*.yaml` | a `GET /get` readiness probe per environment |

The two `*-mismatch` requests are **reachable** (they answer `200`) but fail their assertions, so
`comparo run` lists them green (it reports reachability, status, and latency) while the TUI's
**ASSERT** column and the Report gate light them red — a request that is up yet wrong.

## Run it

```console
comparo validate --config examples/canary-project/comparo.yaml
comparo diff --config examples/canary-project/comparo.yaml --pair stable-vs-canary
comparo run --config examples/canary-project/comparo.yaml --env stable
comparo tui examples/canary-project      # Diff tab → x to run → ↑↓ through drift → i to triage
```

`validate` reports every object valid; `diff` fails its gate on the intended `taxRate` drift; `run`
reaches all 24 cells. The public `postman`/`password` Basic-auth creds are baked in as a `$literal`
fallback, so `basic-auth` works out of the box — set `COMPARO_BASIC_AUTH` to override.
