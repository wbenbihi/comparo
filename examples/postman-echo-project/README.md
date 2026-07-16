# Postman Echo project — a 1:1 reference for the whole API

A **runnable** project that maps **every endpoint Postman Echo offers** onto a comparo
`Request` — and several requests per endpoint where that shows a distinct HTTP or comparo
case. Where [`canary-project`](../canary-project) is a curated **diff** showcase built around
one surgical regression, this project is about **exhaustive coverage**: every method, every
status class, compression, streaming, the full auth and cookie flows, and all twelve `/time/*`
helpers, run against the live host.

It targets [postman-echo](https://postman-echo.com) — a public request-echo service whose
response **bodies are deterministic** (they echo what you send), so the only volatility lives
in the HTTP **headers** and a handful of inherently live values (`/ip`, `/time/now`, the
Cloudflare cookie jar).

> postman-echo can rate-limit rapid bursts with a transient 503 — this project keeps
> `run.concurrency` at 3 with retry/backoff, and if you do hit a 503, space the calls a few
> seconds apart. A 503 is the host throttling, not a problem with the config.

## What it demonstrates

| Area | Where | Feature |
| --- | --- | --- |
| **Every method** | `methods/` | `GET` · `POST` · `PUT` · `PATCH` · `DELETE` |
| **POST body cases** | `post-json` · `post-raw-text` · `post-args-json` | an object body (→ `json`+`data`), a scalar body (→ raw `data`, `json: null`), and query args + body together (→ `args`+`json`) |
| **Single matrix** | `matrices/params.yaml` → `get-matrix` | one GET /get run once per param case (3 cells) |
| **Multi-matrix** | `matrices/{locales,regions}.yaml` → `get-multi-matrix` | cartesian `3 × 2 = 6` cells |
| **Schemas** | `schemas/*` → `get`, `post-json`, `time-object`, `basic-auth` | JSON-Schema validation of `/get`, the write shape, `/time/object`, and `{authenticated: true}` |
| **Status assertions** | `status/` | a spread of codes asserted deliberately — `200, 201, 301, 400, 404, 418, 500` |
| **Non-200 as intent** | `brotli` (404) · `digest-auth`/`oauth1`/`hawk` (401) · `cookies-set`/`cookies-delete` (302) | reachable requests whose expected status is *not* 200 — and the assertion passes |
| **Auth + secrets** | `environments/*.yaml`, `instances/basic-auth.yaml` → `basic-auth` | HTTP Basic auth from a masked `BASIC_AUTH` secret (`$env` → `$literal` fallback) |
| **Instances** | `instances/{default-headers,basic-auth}.yaml` | shared header sets injected with `$val` |
| **Interpolation** | `${API_LABEL}`, `${FIXED_TS}`, `${QUANTITY:int}` | plain variables into query/body, and a whole-value `:int` cast into a JSON number |
| **Compression** | `gzip` · `deflate` | transparently decoded and compared |
| **Non-JSON** | `encoding-utf8` | a static HTML page → raw-byte comparison |
| **Streaming** | `stream` | `/stream/1` read as a chunked stream (`response.streaming: true`) |
| **Diff: `shape`** | `diff/envelope.yaml` | the project default — keys + types match, values ignored |
| **Diff: `exact`** | `diff/echo-args.yaml`, `diff/strict.yaml` | `$.args` round-trips exactly; every `/time/*` compared byte-for-byte |
| **Diff: `tolerance`** | `diff/echo-body.yaml` | `$.json.unitPrice` within **±0.01** stays SAME (fractional tolerance) |
| **Diff: `ignore`** | `diff/{envelope,cookies,volatile}.yaml` | the volatile envelope, the CF cookie jar, and `/ip` are skipped, not compared |
| **Diff pair** | `comparo.yaml` → `echo-vs-mirror` | replay the whole API against two identical fronts — a self-consistency / flake check |
| **Health** | `environments/*.yaml` | a `GET /get` readiness probe per environment |

## Endpoint coverage

Every endpoint in the Postman Echo surface, and the request(s) that cover it.

| Endpoint | Request id(s) | What it shows |
| --- | --- | --- |
| `GET /get` | `get`, `get-matrix`, `get-multi-matrix` | query echo; single + multi matrix; `$.args` exact |
| `POST /post` | `post-json`, `post-raw-text`, `post-args-json` | object body, raw scalar body, args + body |
| `PUT /put` | `put` | replace verb, body echoed under `json` |
| `PATCH /patch` | `patch` | partial-update verb |
| `DELETE /delete` | `delete` | remove verb with a body |
| `GET /headers` | `headers` | header echo — the whole body is skipped as envelope |
| `GET /ip` | `ip` | egress IP — the volatile body is skipped |
| `GET /response-headers` | `response-headers` | params reflected as headers + body |
| `GET /basic-auth` | `basic-auth` | **200** — masked Basic auth from a secret |
| `GET /digest-auth` | `digest-auth` | **401** — the digest challenge (comparo sends a single shot) |
| `GET /oauth1` | `oauth1` | **401** — unsigned OAuth 1.0 rejected |
| `GET /auth/hawk` | `hawk` | **401** — unsigned Hawk rejected |
| `GET /cookies/set` | `cookies-set` | **302** — Set-Cookie + redirect (not followed) |
| `GET /cookies` | `cookies` | cookie jar; CF cookies skipped |
| `GET /cookies/delete` | `cookies-delete` | **302** — clear cookie + redirect |
| `GET /time/now` | `time-now` | volatile clock — asserts 200 only |
| `GET /time/valid` | `time-valid` | `{valid: true}` |
| `GET /time/format` | `time-format` | formatted token |
| `GET /time/unit` | `time-unit` | single unit extract |
| `GET /time/add` | `time-add` | interval addition |
| `GET /time/subtract` | `time-subtract` | interval subtraction |
| `GET /time/start` | `time-start` | start-of-unit snap |
| `GET /time/object` | `time-object` | decomposed date + schema |
| `GET /time/before` | `time-before` | `{before: true}` |
| `GET /time/after` | `time-after` | `{after: false}` |
| `GET /time/between` | `time-between` | `{between: true}` |
| `GET /time/leap` | `time-leap` | `{leap: true}` |
| `GET /gzip` | `gzip` | gzip-compressed JSON |
| `GET /deflate` | `deflate` | deflate-compressed JSON |
| `GET /encoding/utf8` | `encoding-utf8` | static HTML → raw compare |
| `GET /brotli` | `brotli` | **404** — endpoint removed |
| `GET /status/{code}` | `status-200 … status-500` | 200, 201, 301, 400, 404, 418, 500 |
| `GET /delay/1` | `delay` | latency; body still deterministic |
| `GET /stream/1` | `stream` | chunked stream |

All `/time/*` requests pin their `timestamp` to `2016-10-10` (via `${FIXED_TS}`), so every
response is deterministic and compares byte-for-byte under `diff.strict`.

## Deliberately skipped, and why

- **`/transform/collection`** — a Postman-collection converter, not a general HTTP case; it
  belongs to Postman tooling rather than the request/response surface this project maps.
- **A true `application/x-www-form-urlencoded` POST** — comparo's request model sends bodies
  as JSON (the engine encodes `body` with `json=`), so a clean form-encoded body is not
  representable. The three `post-*` requests cover the object, raw-scalar, and args-plus-body
  cases that *are* expressible; Echo's `form` map stays empty by design.
- **`digest-auth` as a 200** — HTTP Digest is a two-step handshake (read the nonce from the
  401, then re-request signed). comparo sends one shot, so `digest-auth` asserts the **401
  challenge** instead of the authenticated 200.
- **`/stream/{n}` for n > 1** — Echo concatenates *n* JSON objects into a payload that is not
  valid JSON; `stream` uses `n = 1` so the response stays cleanly parseable.

## A note on `/time/now` in the diff pair

`/time/now` returns a bare RFC 1123 **string** (not JSON), so the differ compares it as raw
bytes and its `diff.volatile` profile — which works for JSON bodies like `/ip` — cannot carve
it out. The `echo-vs-mirror` pair fires both probes near-simultaneously, so in practice they
land in the same second and compare SAME; only if the two probes straddle a one-second
boundary would this single cell register as drift. It is the one place where the JSON-body
(profile-skippable) vs raw-body (compared whole) distinction shows through.

## Run it

```console
comparo validate --config examples/postman-echo-project/comparo.yaml
comparo run --config examples/postman-echo-project/comparo.yaml --env echo
comparo diff --config examples/postman-echo-project/comparo.yaml --pair echo-vs-mirror
comparo tui examples/postman-echo-project
```

`validate` reports every object valid; `run` reaches all 51 cells and the intended 401s, 404,
and 302s pass as declared; `diff` replays the whole API against the mirror and the gate PASSes
with the volatile envelope skipped. The public `postman`/`password` Basic-auth creds are baked
in as a `$literal` fallback, so `basic-auth` works out of the box — set `COMPARO_BASIC_AUTH`
to override.
