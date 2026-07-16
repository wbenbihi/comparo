# Examples

Runnable comparo projects, each built on [httpbin](https://httpbin.org) and focused on a
different feature. Every valid project passes `comparo validate examples/<name>`; the broken one
fails on purpose.

| Project | What it exercises |
| --- | --- |
| [`sample-project`](sample-project) | the full tour — every object kind, and diffing across two environments |
| [`matrix-project`](matrix-project) | **two matrices on one request** → cartesian expansion and the global matrix picker |
| [`checks-project`](checks-project) | **status & schema assertions** that pass and fail — the Run screen's ASSERT column |
| [`health-project`](health-project) | environments whose health resolves **green, orange, and red** |
| [`broken-project`](broken-project) | a **menu of load errors** — the error screen and `comparo validate` diagnostics |

## Quick tour

```console
# the happy path
comparo tui examples/sample-project

# two matrices → six cells; press m to scope them
comparo run examples/matrix-project --env prod

# some assertions pass, some fail
comparo run examples/checks-project --env prod

# probe health: green / orange / red
comparo tui examples/health-project      # select an env, press h

# see every diagnostic at once
comparo validate examples/broken-project
```
