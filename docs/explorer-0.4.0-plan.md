# Explorer 0.4.0 implementation contract

Explorer explains resources. Exploring a Value or Matrix does not require selecting an executable
consumer, constructing a Compare plan, or navigating trials. The active Environment supplies its
own variables and sources where relevant; public values remain readable and secrets remain masked.

## Resource views

| Resource | Behavior |
| --- | --- |
| Value | Authored expression and standalone resolved result; public nested fields and `$use` references remain readable; secret-derived fields are masked |
| Matrix | Axes, explicit/include/exclude declarations, generated data cases and selected-case values; no consumer/binding/occurrence controls |
| Request Body | Numbered, indented JSON; keyboard-selected path, effective value, directive, declaration source and origin order; masked-copy action |
| Schema | Root, properties, item schemas, definitions and combinators; selected constraints and JSON Pointer; local references expand with cycle stops |
| Relationships | Typed links and Back restore the previous resource, view, filter and position |
| Other resources | Existing Environment/profile/Scenario inspectors retain their semantic groups and capability states |

Use `value.masked` for protected data (violet) and `value.boolean` for booleans (blue), with
explicit textual masks retained in no-color mode. Use the shared ActionBar for compact actions. Native Textual Buttons must not be imported by resource
inspectors. An architecture test enforces this rule. Definition and reference navigation use the
shared TreeMenu and Table components, including keyboard focus and responsive stacked panes.

## Resolution boundary

`application.value_inspection` resolves Values and Matrix case expressions directly from compiled
Project/Environment inputs. `ResolutionInputs` supplies the normal resolver with typed bindings and
policy independently of PreparedRun. Existing execution behavior retains the PreparedRun adapter.
No fake Request, Compare preparation or execution occurrence is introduced for these views.

Case compilation uses the core product/zip/union/include/exclude implementation. Lists use 50-row
pages, expansion is capped at 10,000 cases and value previews at 64 nodes. Missing inputs are local
unavailable fields. Secrets and secret-derived expressions are irreversibly masked before reaching
the frontend. Workers reject late output after selection, revision or Environment changes.

Request resolution retains its existing execution-aware backend. Body leaves are inspected
independently so a secret cannot conceal public siblings. The path pane reports compiled declaration
origins; it does not invent override chains or schema validation results. Body documents are capped
at 512 lines; the existing resolved batch covers at most 16 field targets with 64 nodes per target.
Uninspected leaves remain marked as not resolved; masked-copy copies the bounded preview.

## Schema navigation

Schema nodes retain separate display and source coordinates. Required flags belong to the immediate
object schema, including objects inside arrays. `$defs` and combinators remain separate branches.
Project-local URI/JSON Pointer references expand into their exact targets; recursive branches stop.
Reference rows open their declaration in the same tree. Projection is bounded to 512 nodes/depth 32.

Remote documents are explicitly shown as not fetched. Sample validation, remote fetch/cache controls
and arbitrary example payload disclosure remain outside this correction. No valid/verified status
is fabricated for them.

## Reproduction and checks

The permanent [inspection fixture](../examples/explorer-inspection/README.md) contains a standalone
Value, unused Matrix, deeply nested body with a secret, cross-schema references and a recursive schema.

Run the relevant application, core resolution, TUI and architecture tests, then Ruff, mypy and
import contracts. Use Textual Pilot captures at 160×52, 120×40 and 80×30 to check JSON selection,
references and Matrix paging. See [design QA](../design-qa.md) for the actual reviewed states.

This contract covers the Explorer correction; it is not a whole-product 0.4.0 release sign-off.
