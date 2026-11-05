# Quickstart

Comparo validates HTTP responses and compares behavior across environments.
Start in your API repository:

```console
comparo init
```

This creates one `comparo.yaml` containing the Project and all starter resources.
It does not create `.comparo/`. For separate files from the start, use:

```console
comparo init --layout split
```

Choose one initialization command. Both refuse to overwrite an existing manifest;
the split layout also refuses an existing `.comparo/` directory.
An optional directory argument creates a project elsewhere:

```console
comparo init my-api --layout split
cd my-api
```

Edit the baseline and candidate `baseUrl` values and the Request's `/health`
endpoint to match your APIs. Defaults are `http://localhost:8000` and
`http://localhost:8001`; Comparo does not start servers for you.

```console
comparo validate
comparo plan
comparo diff
```

Validation and planning work offline. `diff` sends HTTP requests. The starter
requires HTTP 200 and compares status codes; it does not compare response bodies.
Use `comparo` to open the terminal interface in an interactive terminal.

For automation, use `comparo validate --format json` and `comparo plan --format json`.
Read `comparo docs configuration`, `comparo docs comparisons`, or `comparo docs agents`
next. Exact options are available through `comparo COMMAND --help`.
