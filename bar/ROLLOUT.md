# Rolling out talos-bar-rs on the NixOS box (A/B against the Qt bar)

This is a plan only. Nothing here has been applied to `~/dev/nixos` or to this
repo's `flake.nix`. The Qt bar (`talos-bar.service`, from
`~/dev/nixos/system/talos-bar.nix`) is still the bar that runs.

## What runs today

| Unit (systemd --user) | ExecStart | Notes |
| --- | --- | --- |
| `talos-bar.service` | `${talos-bar}/bin/talos-bar` (Python/Qt, layer-shell-qt) | `Restart=always`, `TALOS_BAR_ALIGN=center`, `TALOS_BAR_SCREEN=HDMI-A-1`, `path = /run/current-system/sw` so the glue can run git/gh/kioclient |
| `talos-searchd.service` | `${talos-searchd}/bin/talos-searchd --hotkey Meta+Space --browser brave-browser.desktop` | Shared by both bars; nothing changes here |

Both units are `wantedBy`/`partOf graphical-session.target`. Only one bar may
own the top edge at a time: each reserves a 36 px exclusive zone, and two
would stack to 72 px.

## 1. Package it next to the Qt bar (flake output, later)

Add a `talos-bar-rs` package to this repo's flake, built from `bar/` like
`talos-searchd` is built from `searchd/` (a `rustPlatform.buildRustPackage`
with `cargoLock.lockFile = ./bar/Cargo.lock` and the kernal-api git hash
pinned). It needs at runtime:

- the Wayland and Vulkan loaders wgpu opens at runtime (`wayland`,
  `vulkan-loader`, the system driver via `/run/opengl-driver`: wrap with
  `addDriverRunpath` or `LD_LIBRARY_PATH=/run/opengl-driver/lib`). The dock
  and its panels need no keymap library: panels map evdev codes themselves;
- the scripts it shells out to, copied into the package
  (`$out/share/talos-bar/scripts/{tasks_cli,calendar_agenda,calendar_ical}.py`)
  and a Python with their two dependencies:
  `python3.withPackages (ps: [ ps.icalendar ps.markdown-it-py ])`, the same
  set the Qt package already wraps in.

Wrap the binary so the bar finds both without uv:

```nix
wrapProgram $out/bin/talos-bar-rs \
  --set TALOS_BAR_SCRIPTS $out/share/talos-bar/scripts \
  --set TALOS_BAR_PYTHON "${pythonWithDeps}/bin/python3"
```

(Without `TALOS_BAR_PYTHON` the bar runs
`uv run --no-project --with icalendar --with markdown-it-py python`, which is
fine for a dev checkout but would reach the network on a cold uv cache.)
`TALOS_BAR_TASKS_CMD` / `TALOS_BAR_AGENDA_CMD` override the whole command,
the same variables the Qt glue honours.

Keep `talos-bar` in the package set: both bars stay installable side by
side, and only the unit decides which one runs.

## 2. The switch: one unit, two ExecStarts

In `system/talos-bar.nix`, add an option instead of a second always-on unit,
so exactly one bar can ever own the edge:

```nix
{ config, lib, pkgs, inputs, ... }:
let
  pkgsFor = inputs.talos-bar.packages.${pkgs.stdenv.hostPlatform.system};
  bar = config.talos.bar.implementation;   # "qt" | "rust"
in {
  options.talos.bar.implementation = lib.mkOption {
    type = lib.types.enum [ "qt" "rust" ];
    default = "qt";
  };
  config = {
    environment.systemPackages = [ pkgsFor.talos-bar pkgsFor.talos-bar-rs ];
    systemd.user.services.talos-bar = {
      description = "talos-bar desktop bar (${bar})";
      wantedBy = [ "graphical-session.target" ];
      partOf = [ "graphical-session.target" ];
      after = [ "graphical-session.target" "talos-searchd.service" ];
      path = [ "/run/current-system/sw" ];        # git + gh for tasks_cli
      environment = lib.mkIf (bar == "qt") {
        TALOS_BAR_ALIGN = "center";
        TALOS_BAR_SCREEN = "HDMI-A-1";
      };
      serviceConfig = {
        ExecStart = if bar == "rust"
          then "${pkgsFor.talos-bar-rs}/bin/talos-bar-rs"
          else "${pkgsFor.talos-bar}/bin/talos-bar";
        Restart = "always";
        RestartSec = 3;
      };
    };
    # talos-searchd.service unchanged.
  };
}
```

Then `talos.bar.implementation = "rust";` in the host config and
`nixos-rebuild switch`. The unit name stays `talos-bar.service`, so
`systemctl --user restart talos-bar` and the journal stay where they are.

**Trying it without a rebuild** (on a running session, from a dev build):

```console
systemctl --user stop talos-bar            # frees the top edge
./bar/target/debug/talos-bar-rs            # Ctrl-C to quit
systemctl --user start talos-bar           # Qt bar back
```

(`systemctl --user stop` only lasts until the next login or restart; nothing
is changed on disk.)

## 3. Rollback

- From the Rust bar to Qt: set `talos.bar.implementation = "qt"` (or remove
  the line) and `nixos-rebuild switch`, or `nixos-rebuild switch --rollback`
  to return to the previous generation. Both packages stay installed, so the
  rollback needs no build.
- Emergency, without a rebuild: `systemctl --user stop talos-bar` and run
  `talos-bar` (the Qt binary is still on `PATH`) by hand for the session.
- Shared state is safe to switch back and forth: both bars read
  `~/.config/talos-bar/config.toml` and ignore each other's tables, and the
  tasks repo (`~/dev/tasks`) and calendar file
  (`~/.local/share/calendar-sync/google.ics`) are only read by the Rust bar.

## 4. Feature parity (honest)

| Feature | Qt bar | talos-bar-rs | Gap |
| --- | --- | --- | --- |
| Top dock with exclusive zone (KWin / wlroots, Wayland) | yes (layer-shell-qt) | yes (kernal-api layer shell) | — |
| Width / alignment | centered, fits content (`TALOS_BAR_ALIGN=center`) | `[bar] width = "full"\|"fit"`, `align = "left"\|"center"\|"right"`; fit sizes from `text_width`; 36 px reserved either way (nested KWin: MaximizeArea y=36 h=764 full and fit) | — |
| Screen choice | `TALOS_BAR_SCREEN`, right-click screen menu, remembered | `[bar] screen = "HDMI-A-1"`, else the Qt bar's saved choice (`$XDG_STATE_HOME/talos-bar/screen`), else `TALOS_BAR_SCREEN`, else primary; by connector name; an absent output falls back to primary with a WARNING; logs the output it landed on. Right-click screen menu lists outputs, ticks the current one, moves the running bar and saves to the Qt bar's state file in its format; goes back to the wanted output when it reappears. Proven on two virtual KWin outputs with injected clicks | — |
| Clock + 8 world zones inline | yes | yes, short labels (`NYC … UTC`, configurable); all 8 fit at 1280 px with the Tasks button in the widest case (tested); a dropped zone logs a WARNING naming it | — |
| World-clock table popup | yes (Clock.qml, on hover) | yes, on a click of the date (the time still opens the agenda): same rows, order, seconds and "N hours later" wording | click, not hover |
| Search button → popover via searchd | yes | yes (`{"op":"show"}`) | — |
| Agenda cell text ("Next 10:00 AM · in 2h 05m", red dot when live) | yes | `10:00 AM · Title` / `Now · Title`, cut with `…` to the width the zones leave (tested: never costs a zone at 1280 px) | no countdown, no red dot |
| Agenda panel: header, next-up block, Tomorrow/Today/Done sections | yes | yes, same script and wording, read-only | — |
| Agenda: Join Zoom button, details card, open calendar, New Zoom Meeting | yes | yes: same commands (`xdg-open` for URLs as `Qt.openUrlExternally`, `kioclient exec org.kde.korganizer.desktop`), same Join lead time and Zoom-app rule; the card opens below the list on click | card on click only (no hover), below the list rather than beside it |
| Tasks cell (open count, unsynced marker) | yes (amber dot) | yes ("Tasks N •") | — |
| Tasks panel content | **editable** TASKS.md editor (`save64`) | toggle, add (`add64`), rewrite and remove a top-level item (`read` + `save64`), all through the one `tasks_cli` seam, errors shown in the panel | — |
| Whole-file TASKS.md editing | Tasks.qml: the popup *is* a plain-text editor (`read`, then `save64` 1.5 s after typing stops and on close); "Open on GitHub" button (`Qt.openUrlExternally`) | "Edit file" opens a monospace text area (`Frame::text_area`) filled by `tasks_cli read`; Save, or closing the editor or panel with unsaved text, does `save64` and proves it with a second `read`. "Open on GitHub" runs `xdg-open https://github.com/zackees/tasks/blob/main/TASKS.md` through `launch::Spawn`. Proven under nested KWin with real clicks on a temp clone (stub `xdg-open`) | saves on Save/close, not 1.5 s after typing stops. Neither bar opens an external editor |
| Panel close: Esc / click away / click the cell again | yes | yes | — |
| Glue/plugin items (`talos_bar/glue/*.py`, `@item`/`@popup`/`@action`) | yes (only the 3 built-in modules use it; no personal glue installed) | no, cells are hard-coded; decision in `PLUGINS.md`: replace with a script contract (`[[item]] command/every/on_click/popup`) when a first item needs it | no plugin system (unused today) |
| System tray | no | yes, via `kernal_api::system_tray` (StatusNotifierItem): accent-coloured icon; left click / "Search…" opens the popover through searchd `show`; middle click / "Tasks panel" opens or closes the Tasks panel; "Quit bar" exits. `--no-tray` turns it off; with no status-area host it logs and the bar runs without it. Proven under nested KWin with kded6's watcher and Plasma's own tray (real clicks on the drawn icon and menu) | no "hide bar": a dock cannot be unmapped and still get frames to be told to come back |
| Theme from config | Theme.qml (Breeze Dark, hard-coded) | `[theme]` mode (dark/light/system), accent, background, foreground, panel | — |
| Translucent panel (`panelOpacity: 0.7`) | yes | `[bar] opacity = 0.7` (default 1.0) or a `#rrggbbaa` `[theme]` colour; bar and panels, text opaque; measured blend alpha 0.698-0.705 for 0.702 | — |
| Docks on GNOME (no layer-shell), X11 | no | no (`Unsupported`, exits 1 with the reason) | same |
| Docks on macOS / Windows | planned (MIGRATION phase 3) | `Unsupported` in kernal-api (not wired) | same |
| Idle CPU | — | ~0.7 % of a core under nested KWin on a load-50-80 host, equal to the pre-panel binary A/B'd on the same run, same with a panel open (see README) | — |

Headline gap before the Rust bar is a full replacement: the glue plugin system, which nothing
uses today (see `PLUGINS.md`). The tray and whole-file task editing are done.
For a read-mostly daily bar on the one 4K screen, the two panels that the
Qt bar exists for are covered.
