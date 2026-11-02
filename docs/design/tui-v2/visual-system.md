# Visual and theming system

Reconciled **2026-10-31** with the canonical `comparo.tui` library. The retained screenshots define
layout and information-hierarchy targets; `theme.py`, the shared components and workspace guides
describe today's implementation. Historical neutral-method/text-only-kind proposals are superseded
by the established Explorer vocabulary, which Launch and Reports must reuse.

## Hierarchy

The screenshots define the target hierarchy:

1. Workspace and page title: bold foreground, not decorative color.
2. Primary outcome or primary action: one accent treatment.
3. Section title and selected entity: bold foreground.
4. Body and effective values: regular foreground.
5. Labels, identifiers, provenance, counts, and hints: muted foreground.

Use alignment, spacing, weight, and a divider before introducing another border. Avoid nested
rounded boxes, isolated dashboard cards, ornamental glyphs, gradients, and unrelated category
colors. Terminal typography remains monospaced and information-dense.

## Theme boundary

Comparo registers themes through Textual. Components use semantic TCSS variables, component
variants and `theme.style()` for Rich spans. Literal colors and theme-specific translation belong
in `src/comparo/tui/theme.py`, not in feature widgets.

The current dark theme uses these central tokens; the registered light theme translates both TCSS
and Rich text styles:

| Role | Dark value |
| --- | --- |
| background | `#0B0F14` |
| surface | `#121820` |
| panel | `#18202C` |
| foreground | `#D6E1EC` |
| muted | `#68809C` |
| border | `#243044` |
| primary / baseline / running | `#4FD6E3` |
| candidate | `#C89BF5` |
| success | `#7BD88F` |
| advisory | `#F0C060` |
| failure | `#F2637E` |
| operational error | `#FF9E64` |

Settings currently accepts `comparo-dark` and `comparo-light`; the saved theme applies next launch.
Automatic system-theme selection and third-party theme installation are not current controls.

Required theme-neutral component states are default, focused, hovered, cursor-highlighted,
committed selection, disabled, loading, empty, advisory, failed, and unavailable. Focus is a pane
or widget state; cursor and committed selection are different item states.

## Status grammar

Current status rendering uses the shared `components.status` grammar:

| State | Mark and label |
| --- | --- |
| passed | `✓ Passed` |
| advisory | `~ Advisory` |
| failed | `✗ Failed` |
| error | `! Error` |
| running | `◐ Running` |
| queued | `· Queued` |
| skipped | `⊘ Skipped` |
| cancelled | `⊘ Cancelled` |
| drifted | `Δ Drifted` |
| not evaluated | `— Not evaluated` |

Color never carries status alone. Preserve the producer's distinct completed, satisfied, equivalent,
violated, unavailable and partial states rather than reducing all outcomes to pass/fail.

Resource labels use the glyphs and semantic tokens in `theme.KIND_GLYPHS`: for example `⌂` Project,
`◉` Environment, `⇄` Request, `▦` Matrix, `§` Schema, `$` Value and `▶` ExecutionProfile.
`ResourceLabel` and the shared label fragments keep this vocabulary consistent across workspaces.
Methods retain their established colors: GET green, POST amber, PUT blue, PATCH teal, DELETE red,
HEAD/OPTIONS slate. Baseline and candidate roles retain distinct semantic colors and visible labels.
Glyph selection/ASCII fallback is not yet a Settings preference.

## Density and responsiveness

Comfortable density is the default and compact density is a persisted TUI preference. Saving density
updates the root class immediately and tightens supported component spacing; it does not currently
change every table's row height or fork component markup.

One breakpoint policy classifies terminal-cell width:

| Width | Class | Intent |
| ---: | --- | --- |
| `< 88` | compact | stack or replace secondary regions |
| `88-139` | standard | keep one index rail beside the active detail when useful |
| `>= 140` | wide | match the primary screenshot composition |

Use the shared breakpoint policy for workspace/result composition. Local adaptations also exist:
the header hides helper text below 110 columns, and Launch adapts its target/summary composition.
Evidence requiring side-by-side reading must remain readable through a wider evidence surface or
unified diff; the screenshot targets are not proof that every terminal size has passed visual QA.

## Component styling

- Global TCSS owns the shell, semantic variables, density modifiers, and responsive classes.
- Reusable widgets own their local structure and semantic state classes.
- Workspaces own composition only.
- Cross-workspace descendant selectors are forbidden.
- Raw hexadecimal, RGB, ANSI, or named colors outside the central theme module are forbidden.
- Textual variables describe semantic roles; feature names must not become palette variables.
