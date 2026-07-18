# Auth project — first-class Basic & Bearer auth

A small, **runnable** comparo project that shows the `auth` block in every position it can
appear, with every credential sourced from a **masked secret** — never a literal in the
request. It targets [postman-echo](https://postman-echo.com), whose `/basic-auth` endpoint
validates HTTP Basic and whose `/get` endpoint echoes request headers back, so you can prove
an `Authorization` header actually arrived.

## What it demonstrates

| File | Feature |
| --- | --- |
| `environments/echo.yaml` → `spec.auth` | **environment-level default auth** — every request inherits `Authorization: Bearer ${API_TOKEN}` unless it sets its own |
| `requests/basic-auth.yaml` | **first-class Basic auth** (`auth.basic`) built from a masked password secret; comparo does the base64 |
| `requests/bearer-default.yaml` | **inherits** the environment default Bearer (no `auth` of its own) |
| `requests/bearer-admin.yaml` | **per-request Bearer override** (`auth.bearer`) with a different masked token |
| `environments/echo.yaml` → `spec.secrets` | credentials as **secret refs** — `$env` first, `$literal` demo fallback — so nothing is hardcoded |
| `requests/basic-auth.yaml` → `response.assert` | a **composing list** of two AssertionProfile `$ref`s |
| `assertions/*.yaml` | three composable **AssertionProfiles** (HTTP contract, authenticated body, Bearer present) |
| `executions/auth-smoke.yaml` | an **assert-only ExecutionProfile** — a baseline, no candidate, `check.diff: false` |

## The three auth positions

```
environment default   auth.bearer: ${API_TOKEN}      →  applies to every request…
  bearer-default      (no auth)                       →  …so it inherits the default token
  bearer-admin        auth.bearer: ${ADMIN_TOKEN}     →  overrides with a different token
  basic-auth          auth.basic: { username, ${…} }  →  overrides with Basic instead of Bearer
```

Each token/password is a declared **secret** (`spec.secrets`). In every display sink — a
rendered request, a report, a snapshot — the value is masked; only the execute sink puts the
real bytes on the wire. Point the secrets at real values with environment variables:

```console
export COMPARO_BASIC_PASSWORD=…   # else falls back to postman-echo's public "password"
export COMPARO_API_TOKEN=…        # else falls back to a demo token
export COMPARO_ADMIN_TOKEN=…
```

## Run it

```console
# Validate the project (offline).
comparo validate --config examples/auth-project/comparo.yaml

# See a request fully resolved, with the secret-tainted auth masked in the trail.
comparo render request.basic-auth --config examples/auth-project/comparo.yaml

# Run the assert-only gate against the live host (exits 0 only if every assertion holds).
comparo exec --config examples/auth-project/comparo.yaml execution.auth-smoke
```
