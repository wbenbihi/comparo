# Examples

Runnable comparo projects, each built on a public echo/inspection service
([httpbin](https://httpbin.org) or [postman-echo](https://postman-echo.com)) and focused on a
different feature. Every valid project passes
`comparo validate --config examples/<name>/comparo.yaml`; the broken one fails on purpose.

| Project | What it exercises |
| --- | --- |
| [`sample-project`](sample-project) | the full tour — every object kind, and diffing across two environments |
| [`matrix-project`](matrix-project) | **two matrices on one request** → cartesian expansion, plus **endpoint-path injection** (`/status/${code}`) |
| [`canary-project`](canary-project) | a stable ⇄ canary **diff showcase** with one surgical regression, an AssertionProfile, and a release-gate ExecutionProfile |
| [`postman-echo-project`](postman-echo-project) | an **exhaustive 1:1 reference** for the whole Postman Echo API — every method, status class, compression, streaming, cookies, `bodyType`, and all five diff modes |
| [`sse-project`](sse-project) | **Server-Sent Events** — a bundled local SSE server whose two feeds differ by one event, so the diff catches it event-by-event (run `serve.py` first) |
| [`auth-project`](auth-project) | **first-class Basic & Bearer auth** from masked secrets — environment-level default, per-request override, and an assert-only ExecutionProfile |
| [`checks-project`](checks-project) | **status & schema assertions** that pass and fail — the Run screen's ASSERT column |
| [`health-project`](health-project) | environments whose health resolves **green, orange, and red** |
| [`broken-project`](broken-project) | a **menu of load errors** — the error screen and `comparo validate` diagnostics |

## Quick tour

```console
# the happy path
comparo tui --config examples/sample-project/comparo.yaml

# two matrices → six cells; press m to scope them
comparo run --config examples/matrix-project/comparo.yaml --env prod

# some assertions pass, some fail
comparo run --config examples/checks-project/comparo.yaml --env prod

# first-class Basic/Bearer auth from masked secrets — an assert-only CI gate
comparo exec --config examples/auth-project/comparo.yaml execution.auth-smoke

# probe health: green / orange / red
comparo tui --config examples/health-project/comparo.yaml      # select an env, press h

# see every diagnostic at once
comparo validate --config examples/broken-project/comparo.yaml
```
