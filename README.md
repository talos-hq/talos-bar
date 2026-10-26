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

## Development

```sh
uv sync
uv run talos-bar            # run the bar
uv run ruff check . && uv run ruff format --check .
uv run basedpyright         # strict mode
uv run pytest
```

Glue lives in `src/talos_bar/glue/builtin/` and `~/.config/talos-bar/glue/`.
Edit a glue file while the bar runs and it reloads.

### NixOS

The PyPI PySide6 wheel does not run on NixOS. Use the flake, which builds
against nixpkgs' Qt and layer-shell-qt:

```sh
nix run .#talos-bar                         # packaged build
nix develop -c python -m talos_bar          # dev loop: edit src/, rerun
TALOS_BAR_ALIGN=center nix run .#talos-bar  # left | center | right (default)
```

On Wayland the bar is a layer-shell surface on the primary screen: it stays
above windows and reserves its height. It defaults to the top right so it can
run next to the centred Plasma top panel. The wrapper clears the session's
`QT_PLUGIN_PATH`/`QML2_IMPORT_PATH`, because Plasma exports paths for its own
Qt version and loading them aborts QtQuick.
