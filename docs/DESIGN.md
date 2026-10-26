# talos-bar design

## Goals

1. One bar that runs on Linux (Wayland and X11), Windows 10/11 and macOS.
2. Fast to appear and smooth to draw: GPU rendering, no webview.
3. Easy for an agent to change: Python + QML, edit and relaunch, no compile.
4. Licensing that permits closed or commercial distribution.

Non-goals: being a full desktop shell (task manager, launcher dock, pager).
Those stay with the host desktop.

## Stack decision

| Choice | Decision | Why |
|---|---|---|
| Toolkit | **PySide6** (Qt 6) | Official Qt for Python, **LGPLv3**: closed apps are fine with dynamic linking. PyQt6 is GPL/commercial, so it is rejected. |
| UI language | **QML / Qt Quick** | GPU scene graph (Vulkan/Metal/D3D11/OpenGL); Python only drives state. Reuses the existing plasmoid QML. |
| Rust native core | Not in v1 | `kernal-api`'s Python package is only a compatibility stub today. Bindings (PyO3) come later, only if needed. |
| Packaging | `uv` for dev; Nuitka (or PyInstaller) per-OS for release | Nuitka also improves cold start. |

## Architecture

```
talos_bar/
  __main__.py          # entry: python -m talos_bar
  app.py               # QGuiApplication, QQmlApplicationEngine, per-screen bars
  config.py            # TOML config (~/.config/talos-bar/config.toml)
  platform/
    __init__.py        # dock(window, edge, height) -> picks backend
    wayland.py         # LayerShellQt: anchor, exclusive zone, layer=top
    x11.py             # _NET_WM_STRUT_PARTIAL + _NET_WM_WINDOW_TYPE_DOCK
    windows.py         # ctypes SHAppBarMessage (ABM_NEW/ABM_SETPOS), Mica/Acrylic via DWM
    macos.py           # pyobjc: NSPanel, NSStatusWindowLevel, NSVisualEffectView
  widgets/             # one Python "model" per widget, exposed to QML
    clock.py           # multi-time-zone clock
    agenda.py          # wraps calendar-py
    tasks.py           # wraps tasks_cli (GitHub-synced checklist)
  qml/
    Bar.qml            # the strip: blur/opacity background, widget row
    Clock.qml
    Agenda.qml         # popup list
    Tasks.qml          # popup checklist
```

### Data flow

- Each widget is a `QObject` with `Property`/`Signal`s registered to QML.
- Slow work (calendar sync, GitHub calls) runs in a worker thread or
  `asyncio` via `qasync`. It never runs on the GUI thread.
- QML binds to properties only. No business logic in QML, so the backends can
  be tested headless with pytest.

### Glue layer (the main extension point)

The current panel is held together by small scripts: plasmoid
`executable` DataSources, qdbus calls, KWin scripts and Python helpers.
talos-bar makes that kind of glue a supported feature. **Glue is written in
Python**, and an agent adds it by dropping in one file. The agent does not
edit the bar or write QML.

```
talos_bar/glue/          # glue that ships with the repo
~/.config/talos-bar/glue/  # personal or machine-local glue, same API
```

A glue module is ordinary Python and uses a small decorator API:

```python
from talos_bar.glue import item, every, on_click, run, notify

@item("standup", label="Standup")       # adds a text/icon slot to the bar
@every(seconds=60)                      # how often refresh runs (off the GUI thread)
def refresh(ctx):
    nxt = ctx.backends.agenda.next_event()
    return f"{nxt.title} in {nxt.minutes_until}m" if nxt else ""

@on_click("standup")
def open_meeting(ctx):
    run(["xdg-open", ctx.backends.agenda.next_event().url])
```

The API covers:

- `item` / `popup`: a text/icon slot or a list popup, rendered by built-in QML.
  Glue can't break the layout.
- `every`, `on_event` (screen change, wake, fullscreen), `on_click`, `on_scroll`.
- `run` (subprocess, with a timeout), `notify`, `ctx.config`, `ctx.cache`,
  and `ctx.platform` (open a URL, focus an app, OS name), all cross-platform.
- `ctx.backends.*`: the agenda and tasks backends, so glue can reuse them.

Rules that keep the glue safe to change:

- **Hot reload:** a file watcher reloads a module when it changes. No restart.
- **Isolation:** each glue module runs in a worker thread with a timeout. An
  exception shows a red dot on that item and goes to the log. The bar keeps running.
- **OS-specific glue:** use `@item(..., platforms={"linux"})`, or branch on
  `ctx.platform.os`. Glue that needs qdbus/KWin stays Linux-only. Everything
  else should work on every OS.
- **Testing:** `talos-bar glue test <file>` runs a module headless against
  fake backends, so agents can check their changes without a display.

### Platform docking contract

```python
def dock(window: QQuickWindow, screen: QScreen, edge: Edge, thickness: int) -> Docking
```

`Docking` exposes `reserve(bool)` (take screen space or float) and `close()`.
Behaviour per OS:

| OS | Reserves space | Notes |
|---|---|---|
| Wayland (KWin, Hyprland, Sway) | yes, exclusive zone | Needs the compositor to support `wlr-layer-shell`. GNOME does not, so GNOME falls back to a floating window. |
| X11 | yes, strut | |
| Windows | yes, AppBar | Handle `ABN_FULLSCREENAPP` to hide under fullscreen apps. |
| macOS | **no** | The system menu bar owns the top edge. Float an NSPanel just below it, or use a bottom edge. |

### Look

- Background: 70% opacity plus shadow, matching the current panel colorizer setting.
- Blur: KWin blur protocol on Linux, `DWMWA_SYSTEMBACKDROP_TYPE` on Windows,
  `NSVisualEffectView` on macOS. Fall back to plain translucency.
- Height 36 logical px, centered, sized to contents (matches today's panel).
- Follows each screen's DPI; one bar per screen, configurable.

## Performance budget

| Metric | Target |
|---|---|
| Cold start to visible bar | under 800 ms (Nuitka build), under 1.5 s (`uv run`) |
| Idle CPU | about 0% (clock ticks once per second, or per minute without seconds) |
| Resident memory | under 150 MB |

Measure these early. If cold start misses its target, use Nuitka and
precompiled QML (`qmlcachegen`) before changing the architecture.

## Open questions

- macOS: a bar below the menu bar, or `NSStatusItem` menu extras instead?
- Should talos-bar also absorb the bottom dock's custom launchers later?
- When should `kernal-api` get PyO3 bindings, and which modules first (process, http, tray)?
