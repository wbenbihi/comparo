# Broken project — a menu of load errors

A project that **does not load, on purpose**. Every file carries one distinct diagnostic so the
TUI's error screen and `comparo validate` have a full spread to render — dangling references,
duplicate ids, a missing id, an unknown field, and a YAML syntax error. It is the fixture for
"what does comparo do when the project is wrong?"

## The intentional errors

| File | Diagnostic | Fix |
| --- | --- | --- |
| `matrices/locales.yaml` | `invalid YAML: expected ',' or ']'` (line 13) | close the `[ … ]` sequence |
| `requests/create-order.yaml` | `Object contains unknown field 'methdo'` | spell it `method` |
| `environments/staging.yaml` | `Environment is missing metadata.id` | add `id: environment.staging` |
| `requests/ping-again.yaml` | `duplicate id 'request.ping'` | rename one of the two |
| `requests/get-order.yaml` | `unknown $ref target 'schema.ordr'` → *did you mean 'schema.order'?* | fix the typo |
| `requests/reversed-ref.yaml` | `unknown $ref target 'order.schema'` → *same segments, different order* | reverse to `schema.order` |

`schemas/order.yaml` is the one valid object — it exists so the two dangling references have a
near-miss to suggest.

## See it

```console
comparo validate --config examples/broken-project/comparo.yaml    # prints all six, exits 1
comparo tui --config examples/broken-project/comparo.yaml          # renders the error screen
```

The loader collects **every** problem in one pass rather than stopping at the first, so a single
run shows the whole list — parse errors, envelope errors, id errors, and reference errors
together. Fix all six and the project loads clean.
