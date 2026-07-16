# Sample project — httpbin showcase

A small, **runnable** comparo project that exercises every object kind against
[httpbin](https://httpbin.org), a public HTTP echo service. It shows how comparo replays
requests across two environments and diffs the responses.

## What it demonstrates

| File | Feature |
| --- | --- |
| `environments/{local,prod}.yaml` | two targets for the same API; secrets via `$env` and `$literal` |
| `requests/get-json.yaml` | schema validation + **strict** diff on a static endpoint |
| `requests/get-uuid.yaml` | **volatile fields** — the response changes every call, and the diff ignores it |
| `requests/echo-anything.yaml` | **matrix expansion**, variable interpolation, and a **secret** bearer token |
| `requests/health-status.yaml` | a status-only assertion |
| `matrices/locales.yaml` | run a request once per locale, injected into the query |
| `schemas/*.yaml` | structural validation of responses |
| `diff/{lenient,strict}.yaml` | per-path comparison profiles |
| `comparo.yaml` | the manifest — the `local ⇄ prod` diff pair, concurrency, reporting |

## Run it

Start a local httpbin to act as the `local` environment:

```console
docker run -d -p 8080:80 kennethreitz/httpbin
```

Optionally provide the demo token used by `echo-anything`:

```console
export COMPARO_DEMO_TOKEN=demo-123
```

Then diff `local` against `prod`:

```console
comparo diff --config examples/sample-project/comparo.yaml --pair local-vs-prod
```

Because both environments front the same httpbin image, the controlled fields (`$.args`,
`$.json`) match while volatile fields (`$.uuid`, `$.origin`, `$.headers`) are ignored — so a
clean run reports no regressions. Point `prod` at a modified service to see comparo catch a
difference.
