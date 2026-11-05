# talos-bar

A cross-platform desktop bar (Linux, Windows, macOS) for the Talos HQ setup.
It replaces the KDE Plasma top panel defined in `~/dev/nixos`, so the same
bar follows us onto any machine.

- UI: PySide6 (Qt 6, LGPLv3) + QML, GPU-rendered by Qt Quick
- Logic: plain Python, so agents can iterate without a compile step
- Glue: drop a Python file in `glue/` to add bar items, timers and click
  handlers, with hot reload. This is the main way agents extend the bar.
- Platform docking: one small module per OS (layer-shell, AppBar, NSPanel)

Status: design phase. See:

- [docs/DESIGN.md](docs/DESIGN.md) — architecture and decisions
- [docs/MIGRATION.md](docs/MIGRATION.md) — moving the NixOS panel widgets here

## Editing tasks and notes

The Tasks popup edits `TASKS.md` as plain text. Hover previews do not take the
keyboard; click the Tasks tab or inside its editor to start typing. Further
editor clicks keep it open. Edits save locally after 1.5 seconds without typing
and when the popup closes; the separate tasks-sync timer pushes commits.

A failed save keeps the draft while the bar is running, including after closing
and reopening the popup. Use **Retry save** to try again, or **Discard draft and
reload** to replace the draft with the current file. If another process changes
the file, saving stops with a conflict instead of overwriting that change.
Review or copy your draft before discarding it. Drafts are not a substitute for
a successful save and do not survive restarting the bar.

## Development

```sh
uv sync
uv run talos-bar            # run the bar
uv run ruff check . && uv run ruff format --check .
uv run basedpyright         # strict mode
uv run pytest
```

Glue lives in `src/talos_bar/glue/builtin/` and `~/.config/talos-bar/glue/`;
`docs/examples/hello.py` is a minimal example.
Edit a glue file while the bar runs and it reloads.

### NixOS

The PyPI PySide6 wheel does not run on NixOS. Use the flake, which builds
against nixpkgs' Qt and layer-shell-qt:

```sh
nix run .#talos-bar                         # packaged build
nix develop -c python -m talos_bar          # dev loop: edit src/, rerun
TALOS_BAR_ALIGN=center nix run .#talos-bar  # left | center | right (default)
TALOS_BAR_SCREEN=HDMI-A-1 nix run .#talos-bar  # pin to an output by name (default: primary)
```

On Wayland the bar is a layer-shell surface on the primary screen: it stays
above windows and reserves its height. It defaults to the top right so it can
run next to the centred Plasma top panel. The wrapper clears the session's
`QT_PLUGIN_PATH`/`QML2_IMPORT_PATH`, because Plasma exports paths for its own
Qt version and loading them aborts QtQuick.

## CI

CI is local. `bosn run --task ci` runs the Linux checks in Docker. Enable the gate once per clone with
`git config core.hooksPath .githooks`; it runs on every push and skips trees that already passed.
Windows and macOS have no automated coverage while `platforms.yml` is disabled.
