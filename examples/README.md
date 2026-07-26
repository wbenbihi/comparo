# Comparo examples

These projects are runnable demonstrations of Comparo's current `comparo/v1alpha1` subset. They
share a deterministic loopback service, so they do not require credentials, internet access, or a
third-party API.

## Start the demo service

From the repository root, keep this process running in one terminal:

```console
uv run --frozen python examples/support/demo_server.py
```

It listens only on `127.0.0.1:18080` and exposes three logical Environment prefixes:
`/baseline`, `/candidate`, and `/development`.

## Choose a project

| Project | Purpose | Expected comparison |
| --- | --- | --- |
| [`quickstart`](quickstart/README.md) | Smallest complete assertion and comparison project | Pass |
| [`environment-drift`](environment-drift/README.md) | Mixed pass/fail results and Environment-local values | Fail deliberately |
| [`tui-showcase`](tui-showcase/README.md) | Larger catalog with two Matrices, Values, attached profiles, Schema validation, and a future Scenario | One Matrix case drifts |

Open a project in a second terminal:

```console
uv run --frozen comparo tui --config examples/quickstart/comparo.yaml
```

Inside the TUI:

- `1` opens Explorer;
- `2` opens Compare, where `p` prepares and `x` executes the prepared run;
- `3` opens Report after a run has completed and been persisted;
- `4` opens terminal-only Settings;
- `?` shows the active commands and `q` quits.

The examples intentionally exercise only capabilities implemented by the current runtime. The
larger showcase also contains one unbound future Scenario so Explorer can represent that planned
resource kind without pretending stateful workflows execute today.

## Headless equivalents

Every project also works through the CLI:

```console
uv run --frozen comparo validate --config examples/quickstart/comparo.yaml
uv run --frozen comparo plan --config examples/quickstart/comparo.yaml
uv run --frozen comparo diff --config examples/quickstart/comparo.yaml
```

The loopback host and port are intentionally visible in each `Environment` resource. If port
`18080` is unavailable, start the server on another non-privileged port and update those base URLs.
