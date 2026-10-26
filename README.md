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
