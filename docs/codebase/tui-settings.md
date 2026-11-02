# TUI Settings

Open **Settings** with **4**. The workspace uses the existing Panel, Menu, SelectField, ValueInput,
Caption and ActionBar components. General, Appearance, Interaction and Keyboard shortcuts are navigable
sections; edits remain a draft until **Save & apply**. Discard restores saved choices; Reset loads
defaults into the draft without writing them.

**General** is the first section. It shows the installed version, author, repository, source link,
license and full build SHA. Builds stamp the source revision into both the wheel and source
distribution; rebuilding a wheel from that source distribution preserves it. Development installs
read the Comparo checkout, never the inspected project's Git repository. Modified source is labelled
explicitly, and unavailable revision metadata reads **Not recorded** rather than guessing a SHA.
These fields are read-only and do not participate in preference saves.

Preferences live in `$XDG_CONFIG_HOME/comparo/tui.json`, defaulting to
`~/.config/comparo/tui.json`. Startup only reads a bounded file. An invalid file produces a fixed
notice and defaults without overwriting the original. Explicit saves atomically replace that file;
project configuration, policy, run controls and active executions are unaffected.

| Preference | Applied |
| --- | --- |
| Comparo dark / light | Next launch; semantic method, entity and status colors follow the theme |
| Comfortable / compact spacing | Immediately on save |
| Startup workspace | Next launch |
| Mouse reporting | Next launch |
| Main workspace, quit, Launch and Analyze shortcuts | Immediately on save |

Choose a command, enter its default key, F1-F24 or Ctrl+Alt+letter, then **Assign key** and **Save &
apply**. Collisions are rejected, including commands on nested Launch/Analyze views. Function keys
may need Fn on macOS. Default Analyze shortcuts remain Option+1-5 with the terminal's Option-as-Meta
setting, or brackets to cycle views. Navigation labels and Analyze/Launch hints follow remapped keys.

Theme and mouse changes deliberately wait until restart; changing preferences never rebuilds a
running execution or discards its result selection. Compact mode reduces section and field spacing;
fixed record-table rows retain their normal readable height.

The header displays the actual selected execution Environment (`namespace/id`) and installed Comparo
package version. It says no context when none is selected. At compact widths the helper context is
hidden to preserve workspace navigation; Explore still exposes the selected Environment.

Coverage: `tests/tui/test_settings.py`, `tests/tui/test_bootstrap.py`, plus the shared workspace
regressions. Layouts are exercised at 80×30, 120×40 and 160×52, including light-theme startup.
