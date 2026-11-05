# Comparo examples

The three execution projects demonstrate Comparo's current `comparo/v1alpha1` subset using a
deterministic loopback service. The Explorer inspection project is separate and needs no server.
None requires credentials, internet access or a third-party API.

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
| [`explorer-inspection`](explorer-inspection/README.md) | Standalone Values, unused Matrix, nested JSON body, cross-schema references and cycles; no server | Inspection only |
| [`quickstart`](quickstart/README.md) | Smallest complete assertion and comparison project | Pass |
| [`environment-drift`](environment-drift/README.md) | Mixed pass/fail results and Environment-local values | Fail deliberately |
| [`tui-showcase`](tui-showcase/README.md) | Dense TUI catalog with 17 Request shapes, directives, two Matrices, local/inline/remote Schemas, profiles, safe failures, and a future Scenario | Two locale cells drift for the eu/pro audience |

Open a project in a second terminal:

```console
uv run --frozen comparo tui --config examples/quickstart/comparo.yaml
```

Inside the TUI:

- `1` opens Explore; select a Request or ExecutionProfile and press `c` to send it to Launch;
- `2` opens Launch, where Ctrl+R prepares and Ctrl+X executes the reviewed plan;
- Ctrl+K cancels execution; a completed Run stays visible until you choose Analyze results;
- `3` opens Reports; saved results can be opened even after restarting the app;
- `4` opens persistent Settings and `5` opens the component Debug gallery;
- Ctrl+P opens the command palette and `q` quits outside text inputs.

These are default keys; Settings can remap the main commands. Alt+1–5 selects Analyze lenses,
`[` / `]` cycles them, and `i` opens paged raw/JSON/SSE captures and reviewed sanitized cURL.
See [TUI controls](../docs/tui.md), including the macOS Option/Meta setup. Manual Launch targets
can be saved as a new profile after a YAML preview. Example project writes are explicit.

Executable profiles exercise current runtime capabilities. The larger showcase additionally keeps
clearly tagged, unbound resources for schema-supported or future states—remote Schema acquisition,
Request-local policy attachments, an unavailable credential, and Scenario—so Explorer can render
them without pretending every declaration is executable today.

## Headless equivalents

The three execution projects also work through the CLI; `explorer-inspection` is intended for
inspection and deliberately points at a loopback endpoint with no server:

```console
uv run --frozen comparo validate --config examples/quickstart/comparo.yaml
uv run --frozen comparo plan --config examples/quickstart/comparo.yaml
uv run --frozen comparo diff --config examples/quickstart/comparo.yaml
```

The loopback host and port are intentionally visible in each `Environment` resource. If port
`18080` is unavailable, start the server on another non-privileged port and update those base URLs.
