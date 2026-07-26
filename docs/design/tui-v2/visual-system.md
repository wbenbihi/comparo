# Visual and theming system

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

Comparo registers themes through Textual. A feature component uses semantic Textual variables and
component variants; it never imports a palette, branches on a theme ID, or contains a literal
color.

The initial dark theme is based on the approved screenshots:

| Role | Initial value |
| --- | --- |
| background | `#0D1117` |
| surface | `#161B22` |
| panel | `#21262D` |
| foreground | `#E6EDF3` |
| muted | `#8B949E` |
| border | `#30363D` |
| focus / primary / running | `#79A7FF` |
| success | `#56D364` |
| advisory | `#E3B341` |
| failure / error | `#FF7B72` |

Theme selection references an extensible registered theme ID. `system` is a selection mode, not a
theme implementation. New themes must be installable without changing feature widgets.

Required theme-neutral component states are default, focused, hovered, cursor-highlighted,
committed selection, disabled, loading, empty, advisory, failed, and unavailable. Focus is a pane
or widget state; cursor and committed selection are different item states.

## Status grammar

ASCII is the guaranteed baseline. A verified Unicode glyph set may be a user preference.

| State | Portable mark and label |
| --- | --- |
| success | `+ PASSED` |
| advisory | `~ ADVISORY` |
| failure | `x FAILED` |
| error | `! ERROR` |
| running | `> RUNNING` |
| queued | `. QUEUED` |
| skipped | `- SKIPPED` |
| cancelled | `# CANCELLED` |
| redacted | `* REDACTED` |
| neutral | `= NEUTRAL` |

Color never carries status alone. Resource kinds use compact textual labels such as `REQ`, `ENV`,
`SCHEMA`, `MATRIX`, `ASSERT`, `DIFF`, and `PROFILE`; methods and environments remain neutral.

## Density and responsiveness

Comfortable density is the default and compact density is a TUI preference. Density changes
spacing and row height through root classes or variables; it does not fork component markup.

One breakpoint policy classifies terminal-cell width:

| Width | Class | Intent |
| ---: | --- | --- |
| `< 88` | compact | stack or replace secondary regions |
| `88-139` | standard | keep one index rail beside the active detail when useful |
| `>= 140` | wide | match the primary screenshot composition |

Components cannot declare private breakpoints. Evidence requiring side-by-side reading receives a
wide replacement surface or drawer rather than two squeezed columns.

## Component styling

- Global TCSS owns the shell, semantic variables, density modifiers, and responsive classes.
- Reusable widgets own their local structure and semantic state classes.
- Workspaces own composition only.
- Cross-workspace descendant selectors are forbidden.
- Raw hexadecimal, RGB, ANSI, or named colors outside registered theme definitions are forbidden.
- Textual variables describe semantic roles; feature names must not become palette variables.
