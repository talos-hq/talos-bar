# Plugins: what the Qt bar's glue does, and what the Rust bar should do

Decision record for the last parity row ("glue plugin system"). No code goes with it.

## 1. What the Qt glue system actually is (read from the code, not the design doc)

`src/talos_bar/glue/__init__.py` (200 lines) plus the loader in `src/talos_bar/app.py`:

- **Discovery.** Every `*.py` in `src/talos_bar/glue/builtin/` and `~/.config/talos-bar/glue/` is
  imported into the bar's own Python process (`load_file`). A module registers items with
  decorators:
  - `@item(id, label, platforms, order, plain)` with `@every(seconds)`: a cell on the bar.
    `refresh(ctx)` returns a string or `{text, subtext, icon, dot, tooltip}`.
  - `@on_click(id)`: what a click on the cell does.
  - `@popup(id, hover)`: the dict a popup shows. Without a `component` key, the built-in
    `ListPopup.qml` draws it (`{title, subtitle, rows: [{text, subtext, icon, url}]}`).
    Otherwise `component` names a **QML file beside the module** that draws it.
  - `@action(id, name)`: a handler that the popup's QML calls with `bar.action(id, name, payload)`.
- **Running.** Refresh, popup and click calls run on a `QThreadPool`; an exception is printed
  and sets the item's `error` flag (a red dot). A `QFileSystemWatcher` reloads a module, its QML
  and `scripts/*.py` when they change (hot reload).
- **Helpers.** `run(cmd, timeout)` (a subprocess) and `run_script(name, *args)`, which imports a
  `scripts/` file **in-process** under a global lock and captures its `print()` output.

The design doc (`docs/DESIGN.md`, "Glue layer") promises more than was built: `on_event`,
`on_scroll`, `notify`, `ctx.backends`, `ctx.platform`, per-module timeouts and a
`talos-bar glue test` command do not exist. Python cannot interrupt a thread, so a glue call that
hangs keeps a pool thread forever.

**What uses it today:** the three built-in modules, `clock.py`, `agenda.py` and `tasks.py`
(with `Clock.qml`, `Agenda.qml`, `Tasks.qml`). The Rust bar already does all three natively,
through the same scripts (`tasks_cli.py`, `calendar_agenda.py`). On this machine
`~/.config/talos-bar/glue/` holds no modules, only a stale `__pycache__/zz_tabtest…pyc` left by a
test. **No third-party or personal glue is in use.**

## 2. Does the Rust bar need it?

The glue system does two jobs, and they are different sizes:

1. **Surfacing state on the bar** (a cell with text, a dot and a tooltip, refreshed on a timer)
   and **doing something on click** (run a command). This is the part that matters for "an agent
   adds an item by dropping in one file". It needs no Python in the bar: it is a command that
   prints a line, and another command to run on a click.
2. **Custom popups with their own UI** (`component: Foo.qml` plus `bar.action`). This exists
   because QML can load arbitrary UI at runtime. The Rust bar draws with `kernal_api::gfx`, a
   fixed set of widgets with no runtime-loaded UI language, so this half cannot be ported
   as-is under any option. Every popup that uses it today (agenda, tasks, world clock) is already
   native.

The Rust bar can run scripts directly; it already runs `tasks_cli.py` and `calendar_agenda.py`
off the UI thread with a timeout (`feeds.rs`). Nothing in it depends on a plugin host.

## 3. Options and their cost

| Option | What it means | Cost | Risk |
| --- | --- | --- | --- |
| **A. Port it** | Embed Python (PyO3) or keep a Python sidecar speaking the decorator API over IPC; draw `ListPopup`-style popups natively; drop QML components | Large: an embedded interpreter or a second resident process, a wire protocol for items, popups and actions, hot reload, and nix packaging of the Python env inside a Rust binary. About 1.5–3k lines plus packaging. Custom QML popups still cannot come across | Brings the hang and crash surface of in-process Python into the bar; no current user |
| **B. Replace it with a documented script contract** | `~/.config/talos-bar/items/*.toml` (or a `[[item]]` table in `config.toml`): `command` (prints one line of text, or JSON `{text, dot, tooltip}`), `every` seconds, `on_click` argv, optional `popup_command` returning `{title, rows:[{text, url}]}` drawn as a native list panel | Small to medium: about 300–500 lines in `bar/` (a config table, a `Feed` per item, which already exists, one more cell kind, a generic list panel, which `panel::Line` already covers). A watcher for hot reload is optional, since the bar already re-reads on start | Low: commands run out of process with the existing timeout, so a hung script cannot block the bar; any language works; testable with the existing `Spawn` and `Cli` seams |
| **C. Drop it** | Built-in cells only; new cells are Rust changes | Zero now; every new item is a code change and a rebuild | Loses "an agent drops in one file", which is the stated purpose of the extension point |

## 4. Recommendation

**B: replace the glue system with a documented script contract, and build it only when the
first real item asks for it.** Until then the Rust bar ships with **C** in effect: nothing uses
the glue system today, so dropping it costs nothing on the day of the switch.

Why not A: it would import the whole cost of a Python host to serve zero modules, and still
could not carry the part that is unique to Qt (runtime-loaded QML popups). Why B over C in the
long run: the purpose that matters, "add a cell by dropping in a file, no bar rebuild", survives
intact. It moves out of process, which also fixes the Qt glue's real weakness, an uninterruptible
hang inside the bar.

The contract, written so a script made today keeps working:

```toml
# ~/.config/talos-bar/config.toml
[[item]]
id = "standup"
command = ["~/bin/standup-status"]     # stdout: "Standup in 5m" or {"text": …, "dot": "#e5322d", "tooltip": …}
every = 60                              # seconds; the command is killed after min(every, 25) s
on_click = ["xdg-open", "https://…"]   # optional argv
popup = ["~/bin/standup-list"]          # optional: {"title": …, "rows": [{"text": …, "url": …}]}
```

Rules: commands run off the UI thread with a timeout. A non-zero exit or bad JSON shows `!` on
the cell and the error in its panel (the same treatment the Tasks cell gets now). Cells sit
between the Tasks cell and the clock, in file order, and are dropped before clock zones when the
bar runs out of room (logged, like a zone).

Until that exists, the Qt bar keeps the glue feature: anyone who needs a custom cell today can
run the Qt bar (`talos.bar.implementation = "qt"`, see ROLLOUT.md).
