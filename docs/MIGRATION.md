# Migrating the NixOS top panel into talos-bar

## Starting point

The NixOS top panel is declared in `~/dev/nixos/system/configuration.nix`
(around line 3190): `location = "top"`, screen 0, centered, `lengthMode = "fit"`,
height 36, `hiding = "windowsgobelow"`. It holds:

| Current widget | Source | Moves to |
|---|---|---|
| `luisbocanegra.panel.colorizer` (70% opacity, shadow) | third-party plasmoid | `qml/Bar.qml` background, plus blur in `platform/*` |
| `digitalClock`, 9 zones (Local, New York, Chicago, Denver, Sydney, Luxembourg, Moscow, Tokyo, UTC), city format | stock Plasma | `widgets/clock.py` + `qml/Clock.qml`; zones in config.toml |
| `org.niteris.calendar-agenda` | `nixos/system/calendar-agenda/` (483-line QML) + `nixos/system/calendar-py/` | `widgets/agenda.py` imports calendar-py; QML ported |
| `org.niteris.tasks` | `nixos/system/tasks/` (applet + `tasks_cli.py`) | `widgets/tasks.py` imports tasks_cli; QML ported |

Out of scope: the bottom dock (launchers, pager, task manager) stays in Plasma.

## Phase 0: scaffold (about 1 day)

- `pyproject.toml` (uv), PySide6, pytest, ruff.
- `python -m talos_bar` opens an undecorated 36 px window with a clock.

## Phase 1: Linux parity, side by side with Plasma (about 3–5 days)

1. Wayland docking via LayerShellQt (in nixpkgs as `kdePackages.layer-shell-qt`).
2. Clock widget with the nine zones.
3. **Backends:** move `calendar-py` and `tasks_cli.py` into this repo as
   `talos_bar/backends/` (keep their tests). Until the move, depend on them
   by path. Drop the `Plasma5Support.DataSource { engine: "executable" }`
   subprocess bridge and call the Python functions directly.
4. **QML port:** replace `org.kde.plasma.*`, `Plasma5Support` and Kirigami
   imports with `QtQuick.Controls`. Replace `plasmoid.*` config with
   properties from `config.py`. Keep the layout and behaviour as they are.
5. Run talos-bar next to the Plasma panel on another screen or edge until it
   matches.

## Phase 1b: convert existing glue into Python glue modules (ongoing)

Go through every ad-hoc bridge that feeds the panel and rewrite it as a
`talos_bar/glue/*.py` module (see "Glue layer" in DESIGN.md):

- Plasmoid `executable` DataSources and the shell strings they run.
- `qdbus` / KWin-script snippets in `configuration.nix` that the panel uses
  (Overview button, desktop moves). These become Linux-only glue.
- Python helpers wired to the bar (e.g. `launch-origin/kitty_bridge.py`,
  `voxtype-popup.py`) if they surface state on the bar.

Do one conversion per commit, and record each in the table below. Once a
piece of glue is a module, an agent can change it without touching Nix.

| Old glue | New module | Platforms | Done |
|---|---|---|---|
| | | | |

## Phase 2: switch over on NixOS (about 1 day)

- Package talos-bar in `~/dev/nixos` (a `buildPythonApplication` in
  `system/pkgs/talos-bar/`) and start it with a systemd user service.
- Remove the top panel block from `configuration.nix`, and remove the
  agenda/tasks plasmoid wiring in `calendar.nix` and `tasks.nix` (keep the
  sync services those widgets rely on).
- Keep the old panel block in git history as the rollback.

## Phase 3: Windows and macOS (about 1.5–2 weeks)

- `platform/windows.py`: AppBar registration, work-area reservation,
  fullscreen hiding, Mica backdrop.
- `platform/macos.py`: NSPanel at status level below the menu bar,
  vibrancy, no Dock icon (`LSUIElement`).
- Check that the calendar and tasks backends have no Linux-only paths (XDG
  directories, `notify-send`, etc.). Use `platformdirs` for paths.

## Phase 4: packaging and polish (about 1 week)

- Nuitka builds per OS in CI (add workflows only when asked).
- Autostart: systemd user unit, Windows Startup shortcut / Run key, macOS LaunchAgent.
- Multi-monitor (one bar per screen, or primary only) and per-screen DPI.
- Measure against the performance budget in DESIGN.md.

## Risks

| Risk | Mitigation |
|---|---|
| GNOME has no layer-shell | Floating window fallback; document the limitation |
| Python + Qt cold start | Nuitka + `qmlcachegen`; it is a login-once process |
| calendar-py assumes Linux | Audit in Phase 3; `platformdirs` |
| macOS cannot reserve the top edge | Accept a floating bar, or go to `NSStatusItem` |
