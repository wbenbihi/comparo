# Comparing environments

`run` checks assertions in selected environments. `diff` compares a baseline with
one or more candidates and evaluates attached assertions. The starter only compares
status codes. To compare a JSON body as well, add this resource:

```yaml
apiVersion: comparo/v1alpha1
kind: DiffProfile
metadata:
  id: matching-json
  name: Matching JSON body
spec:
  rules:
    - id: body-matches
      selector:
        source: response.body
        view: json
        path: ""
      comparator:
        name: exact
      severity: blocking
      completeness: complete
```

Save it under `.comparo/profiles/` and add `{$use: matching-json}` to
`spec.defaults.diffs` in the Project. Keep `{$use: stable-status}` if you also want
status comparison. For volatile fields, select stable contract fields rather than
assuming timestamps or generated IDs should match.

```console
comparo validate
comparo plan --baseline baseline --candidate candidate
comparo diff --baseline baseline --candidate candidate
comparo exec compare-health
```

The starter's `compare-health` ExecutionProfile supplies the subject and comparison
topology. `comparo exec PROFILE --help` lists invocation overrides.

| Exit code | Meaning |
| --- | --- |
| 0 | Command succeeded; execution gate passed |
| 1 | Execution completed and the gate failed |
| 2 | Invalid invocation, configuration, planning, or policy |
| 3 | Execution did not produce a complete gateable result |
| 4 | Unexpected internal failure |
| 130 | Interrupted |

Use `--format json` for structured command results. Saved bundles can be inspected
with `comparo artifact inspect` and rendered with `comparo report`; consult their
`--help` output for required arguments. See `comparo docs assertions` and
`comparo docs limitations`.
