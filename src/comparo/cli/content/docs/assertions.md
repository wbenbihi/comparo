# Response assertions

Assertions check each environment's response against an expectation. A comparison
also evaluates its attached assertions: two identical HTTP 500 responses should not
pass an HTTP 200 expectation simply because they match each other.

Add this complete resource to `.comparo/profiles/assertions.yaml`:

```yaml
apiVersion: comparo/v1alpha1
kind: AssertionProfile
metadata:
  id: expected-status
  name: Expected status
spec:
  rules:
    - id: status-ok
      selector: response.status
      operator:
        name: equals
        expected: 200
      severity: blocking
      quantifier: one
      completeness: complete
```

Attach it by setting the Project's `spec.defaults.assertions` to
`[{$use: expected-status}]`, or attach it on the applicable Request or Environment.
Creating a profile alone does not activate it. Use the API contract to choose the
expected status; creation endpoints may expect 201, for example.

```console
comparo validate
comparo plan --env baseline
comparo run --env baseline
```

The selector chooses an observation; the operator checks it. Body selectors can
explicitly choose the JSON view and a JSON Pointer path. Consult `comparo schema config`
for selector and operator forms. Gate profiles determine how severities and incomplete
coverage affect the verdict. The starter uses the strict gate.

See `comparo docs comparisons` for cross-environment differences and exit codes.
