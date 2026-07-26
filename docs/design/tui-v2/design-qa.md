# TUI v2 design QA

Date: 2026-07-26
Status: passed for the current v0.2 application projection
Acceptance viewport: 160 columns by 52 rows

## Review method

The implementation was exercised through the real Textual application with the
`examples/tui-showcase` project. Deterministic captures were produced at 170 by 52 cells so the
rendered image matched the reference aspect ratio, then every capture was placed beside its
corresponding approved screenshot and inspected as one image.

The paired review covered:

- Explorer Request, Environment, Matrix, and Schema states;
- Compare Target, admitted Plan, and active Run states;
- the shared Issues, Requests, Rules, Fields, and Timeline analysis lenses; and
- Report archive selection and read-only analysis entry.

The implementation preserves the approved terminal-native hierarchy: persistent workspace tabs,
decision-led page headings, contextual lifecycle navigation, a temporary index/detail split when
selection requires it, dense aligned facts, one visually dominant action, and a contextual native
Footer. It does not reproduce the rejected alpha TUI or the incomplete browser mockup.

## Interaction acceptance

Automated Textual Pilot journeys verify that:

- Explorer highlight changes the inspector immediately without requiring Enter;
- filters rebuild the native Tree without losing the selected resource;
- Tab moves through major regions while native widgets retain arrow-key navigation;
- mouse and key activation use the same semantic component messages;
- Compare follows Target to Plan to Run to Analyze and can safely return to edit;
- changing Compare mode replaces incompatible topology atomically;
- running work survives workspace navigation and remains cancellable;
- Compare and Report mount the same analysis component, with Report read-only;
- all five analysis lenses retain stable artifact references; and
- the Footer, palette, buttons, and help resolve commands from the shared command catalog and
  key map.

Compact, standard, and wide breakpoint classification is shared rather than implemented per
screen. Theme selection, density, mouse preference, glyph preference, reduced motion, and future
key-map overrides cross explicit TUI-only boundaries.

## Safety and capability boundary

Widgets receive sanitized application projections only. Explorer renders resource structure,
directive kinds, identity, provenance, and dependency relationships without receiving secret
values. Artifact analysis renders authenticated report projections and stable evidence references;
it does not open arbitrary paths or reconstruct runtime values. Terminal controls, data-bearing
markup, exception text, and secret canaries are covered by regression tests.

Some approved screenshots intentionally illustrate richer future projections, including detached
raw response or SSE evidence, expanded per-event timing, report filtering metadata, and resolved
request bodies. The current UI neither fabricates those facts nor bypasses the application boundary
to obtain them. Their component positions and navigation contracts are reserved, while delivery of
the underlying data remains a backend/application capability. Safe cURL copy remains explicitly
deferred for the same reason.

## Build evidence

- Ruff lint and formatting: passed.
- Strict mypy: passed for 683 source files.
- Import-linter: 11 contracts kept, none broken.
- Tests: 3,617 passed.
- Coverage: 100 percent statements and 100 percent branches across 43,279 statements and 15,022
  branches.
- Distribution audit: the `comparo-0.2.0` wheel and source distribution build and contain the exact
  expected package members.
